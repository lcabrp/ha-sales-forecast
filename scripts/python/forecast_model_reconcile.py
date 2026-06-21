"""Hierarchical reconciliation for SKU/day forecasts.

WHY THIS SCRIPT EXISTS
----------------------
The forecast-model notes observe that corporate's *total* demand is often
directionally reliable while its *SKU allocation* is weak, and that the
independent model is strong at SKU shape but can drift in aggregate. That is the
textbook case for hierarchical reconciliation: keep the SKU-level shape from the
model, but rescale it so it agrees with a more trustworthy aggregate.

This script takes the per-SKU/day forecast parquet written by
``forecast_model_horizon_train.py --save-forecast`` and produces reconciled
variants of a chosen base forecast, then re-scores them (WAPE / bias / coverage)
against actuals and corporate on the same grid. It does NOT modify any existing
script.

Reconciliation variants
------------------------
For each (window, date) the base SKU forecast is rescaled so that a chosen
aggregate matches a more reliable target:

  * ``bottom_up``  - the base forecast unchanged (reference).
  * ``middle_out`` - rescale SKUs *within each category* (Division/Department/Class)
    so the category/day sum matches a category-level target. Preserves the model's
    in-category SKU shape while fixing category totals.
  * ``top_down``   - rescale all SKUs so the grand total/day matches a total target.
    Preserves SKU shares but fixes only the overall level.

The aggregate target can be the corporate aggregate, a recent-demand aggregate, or
a blend (``--target`` and ``--blend-alpha``). Rescaling factors are clipped to
``--max-factor`` to avoid runaway corrections from tiny denominators.

EXAMPLE
-------
    uv run python scripts/python/forecast_model_reconcile.py \
        --forecast Output/ForecastAccuracy/model/horizon_consistent/forecast_sku_day.parquet \
        --base HorizonConsistentMLForecastQty --target blend --blend-alpha 0.5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_train import MODEL_DIR  # noqa: E402


DEFAULT_FORECAST_PATH = MODEL_DIR / "horizon_consistent" / "forecast_sku_day.parquet"
DEFAULT_OUTPUT_DIR = MODEL_DIR / "reconciled"
ACTUAL_COLUMN = "SoldUnits"
SKU_COLUMN = "SKU"
DATE_COLUMN = "Date"
CATEGORY_COLUMNS = ["Division", "Department", "Class"]

TARGET_SOURCES = {
    "corporate": "CorporateForecastQty",
    "recent7": "Recent7BaselineQty",
    "recent28": "Recent28BaselineQty",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hierarchical reconciliation of SKU/day forecasts.")
    parser.add_argument("--forecast", type=Path, default=DEFAULT_FORECAST_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--base", default="HorizonConsistentMLForecastQty", help="Base SKU forecast to reconcile.")
    parser.add_argument(
        "--target",
        choices=["corporate", "recent7", "recent28", "blend"],
        default="blend",
        help="Aggregate target the reconciled forecast should match.",
    )
    parser.add_argument(
        "--blend-alpha",
        type=float,
        default=0.5,
        help="For --target blend: weight on the corporate aggregate vs the base model aggregate (0..1).",
    )
    parser.add_argument("--max-factor", type=float, default=4.0, help="Clip rescaling factors to [1/max, max].")
    parser.add_argument(
        "--save-forecast",
        type=Path,
        default=None,
        help="Optional parquet path to persist reconciled forecasts + actuals + attributes.",
    )
    return parser.parse_args()


def safe_factor(target: pd.Series, base: pd.Series, max_factor: float) -> pd.Series:
    factor = np.where(base.to_numpy() > 0, target.to_numpy() / np.where(base.to_numpy() > 0, base.to_numpy(), 1.0), 1.0)
    return pd.Series(np.clip(factor, 1.0 / max_factor, max_factor), index=base.index)


def aggregate_target(group_base: pd.Series, group_corp: pd.Series, target: str, alpha: float) -> pd.Series:
    if target == "blend":
        return alpha * group_corp + (1.0 - alpha) * group_base
    return group_corp  # corporate/recent column already selected upstream


def reconcile_grouped(
    df: pd.DataFrame,
    base_col: str,
    target_col: str,
    group_cols: list[str],
    target_mode: str,
    alpha: float,
    max_factor: float,
) -> pd.Series:
    """Rescale base forecast within each group so the group sum hits the target."""
    base_sum = df.groupby(group_cols)[base_col].transform("sum")
    target_raw_sum = df.groupby(group_cols)[target_col].transform("sum")
    if target_mode == "blend":
        target_sum = alpha * target_raw_sum + (1.0 - alpha) * base_sum
    else:
        target_sum = target_raw_sum
    factor = safe_factor(target_sum, base_sum, max_factor)
    return (df[base_col] * factor).clip(lower=0)


def wape_bias(df: pd.DataFrame, col: str) -> dict[str, float]:
    actual = float(df[ACTUAL_COLUMN].sum())
    forecast = float(df[col].sum())
    abs_err = float((df[col] - df[ACTUAL_COLUMN]).abs().sum())
    return {
        "ActualUnits": actual,
        "ForecastUnits": forecast,
        "BiasPct": (forecast - actual) / actual if actual else 0.0,
        "WAPE": abs_err / actual if actual else 0.0,
    }


def evaluate(df: pd.DataFrame, forecast_cols: list[str]) -> pd.DataFrame:
    rows = []
    for window, group in df.groupby("WindowLabel"):
        for col in forecast_cols:
            rows.append({"WindowLabel": window, "ForecastName": col, **wape_bias(group, col)})
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    if not args.forecast.exists():
        raise SystemExit(
            f"Forecast file not found: {args.forecast}\n"
            "Generate it first with: forecast_model_horizon_train.py --save-forecast <path>"
        )
    df = pd.read_parquet(args.forecast)
    if args.base not in df.columns:
        raise SystemExit(f"Base forecast column {args.base!r} not in {args.forecast}.")

    target_mode = args.target
    if target_mode == "blend":
        target_col = TARGET_SOURCES["corporate"]
    else:
        target_col = TARGET_SOURCES[target_mode]
    if target_col not in df.columns:
        raise SystemExit(f"Target column {target_col!r} not available in the forecast file.")

    group_present = [c for c in CATEGORY_COLUMNS if c in df.columns]

    df["BottomUp"] = df[args.base].clip(lower=0)
    df["TopDown"] = reconcile_grouped(
        df, args.base, target_col, ["WindowLabel", DATE_COLUMN], target_mode, args.blend_alpha, args.max_factor
    )
    if group_present:
        df["MiddleOut"] = reconcile_grouped(
            df,
            args.base,
            target_col,
            ["WindowLabel", DATE_COLUMN, *group_present],
            target_mode,
            args.blend_alpha,
            args.max_factor,
        )

    forecast_cols = ["BottomUp", "TopDown"]
    if "MiddleOut" in df.columns:
        forecast_cols.append("MiddleOut")
    for ref in (args.base, "CorporateForecastQty", "CorporateBaselineQty"):
        if ref in df.columns and ref not in forecast_cols:
            forecast_cols.append(ref)

    per_window = evaluate(df, forecast_cols)
    scoreboard = (
        per_window.groupby("ForecastName", as_index=False)
        .agg(
            Windows=("WindowLabel", "nunique"),
            TotalActualUnits=("ActualUnits", "sum"),
            TotalForecastUnits=("ForecastUnits", "sum"),
            MeanWAPE=("WAPE", "mean"),
            MeanBiasPct=("BiasPct", "mean"),
        )
        .sort_values("MeanWAPE")
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.save_forecast is not None:
        args.save_forecast.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(args.save_forecast, index=False)
        print(f"Saved reconciled forecasts to {args.save_forecast}")
    per_window.to_csv(args.output_dir / "reconcile_per_window.csv", index=False)
    scoreboard.to_csv(args.output_dir / "reconcile_scoreboard.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "forecast_input": str(args.forecast),
        "base_forecast": args.base,
        "target_mode": target_mode,
        "target_column": target_col,
        "blend_alpha": args.blend_alpha,
        "max_factor": args.max_factor,
        "category_levels": group_present,
        "notes": [
            "BottomUp is the base model forecast unchanged.",
            "TopDown rescales all SKUs per day so the grand total matches the target.",
            "MiddleOut rescales SKUs within each category per day to match category totals, "
            "preserving the model's in-category SKU shape.",
            "target=blend mixes the corporate aggregate with the base model aggregate by blend-alpha; "
            "use it when corporate totals are directionally trustworthy but not fully.",
        ],
    }
    with (args.output_dir / "reconcile_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print("Reconciliation scoreboard (mean across windows, lower WAPE is better):")
    print(scoreboard.to_string(index=False))
    print(f"\nWrote reconciliation outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
