"""Honest frozen backtest of the category-pool candidates.

Freezes every input at a chosen origin (default 2026-07-07), builds the new
category-pool candidates using only pre-origin DirectPick history / inventory /
inbound facts, and scores them against the saved closeout actuals with the same
SKU-allocation metrics used in ``FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md``.

It also re-scores the previously frozen candidates (corporate raw, corporate
total + recent shape, independent recent shape) that are saved for the same
origin, so the new candidates sit on one comparable leaderboard.

Runs entirely on portable Parquet/SQLite facts. No live-AX access.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_category_pool import (  # noqa: E402
    HORIZON_DAYS,
    ModelConfig,
    build_candidates,
    load_corporate_daily,
    load_crosswalk,
)
from forecast_schema import normalize_sku_series  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402

FA_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_ACTUALS = FA_ROOT / "handoff_eval" / "forward_2026-07-07_closeout" / "actual_sku_day.parquet"
DEFAULT_FROZEN = FA_ROOT / "handoff_eval" / "forward_2026-07-07_challenger" / "forward_daily_forecasts.parquet"
DEFAULT_LEDGER = FA_ROOT / "forward_tests" / "2026-07-10_corporate_2026-07-06" / \
    "replacement_contract_cold_start" / "raw_hybrid_cap085" / "ingestion_output" / "sku_ledger.db"
DEFAULT_OUTPUT = FA_ROOT / "handoff_eval" / "category_pool_backtest_2026-07-07"


def score_candidate(forecast_sku: pd.DataFrame, actual_sku: pd.DataFrame, name: str) -> dict[str, Any]:
    """Compute the closeout SKU-allocation metrics for one candidate."""
    compare = forecast_sku.merge(actual_sku, on="SKU", how="outer")
    compare["ForecastUnits"] = compare["ForecastUnits"].fillna(0).astype(float)
    compare["SoldUnits"] = compare["SoldUnits"].fillna(0).astype(float)
    abs_err = (compare["ForecastUnits"] - compare["SoldUnits"]).abs()
    sold = float(compare["SoldUnits"].sum())
    fcst = float(compare["ForecastUnits"].sum())
    fpos = compare["ForecastUnits"].gt(0)
    used = fpos & compare["SoldUnits"].gt(0)
    coverage_units = float(compare.loc[used, "SoldUnits"].sum())
    zero_demand_units = float(compare.loc[fpos & compare["SoldUnits"].eq(0), "ForecastUnits"].sum())
    return {
        "Candidate": name,
        "Units": fcst,
        "BiasPct": (fcst - sold) / sold if sold else np.nan,
        "SKU_WAPE": float(abs_err.sum() / sold) if sold else np.nan,
        "ForecastPositiveSKUs": int(fpos.sum()),
        "SKUUseRatePct": float(used.sum() / fpos.sum()) if fpos.sum() else np.nan,
        "SoldUnitCoveragePct": coverage_units / sold if sold else np.nan,
        "ZeroDemandUnitPct": zero_demand_units / fcst if fcst else np.nan,
    }


def category_table(
    forecast_sku: pd.DataFrame, actual_sku: pd.DataFrame, crosswalk: pd.DataFrame,
    name: str, cells: list[str],
) -> pd.DataFrame:
    """Forecast vs actual per named category cell (e.g. GIRM, BOYM)."""
    merged = forecast_sku.merge(actual_sku, on="SKU", how="outer").fillna(0)
    merged = merged.merge(crosswalk, on="SKU", how="left")
    merged["Category"] = merged["Category"].fillna("UNKNOWN")
    merged["AbsError"] = (merged["ForecastUnits"] - merged["SoldUnits"]).abs()
    rows = []
    for cell in cells:
        sub = merged.loc[merged["Category"].eq(cell)]
        sold = float(sub["SoldUnits"].sum())
        rows.append({
            "Candidate": name, "Cell": cell,
            "Forecast": float(sub["ForecastUnits"].sum()),
            "Actual": sold,
            "Bias": float(sub["ForecastUnits"].sum() - sold),
            "SKU_WAPE": float(sub["AbsError"].sum() / sold) if sold else np.nan,
        })
    return pd.DataFrame(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="2026-07-07", type=lambda s: pd.Timestamp(s).normalize())
    parser.add_argument("--actuals", type=Path, default=DEFAULT_ACTUALS)
    parser.add_argument("--frozen-candidates", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--ledger-db", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--lookback-days", type=int, default=56)
    parser.add_argument("--seasonal-years", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    origin = args.origin
    through = origin + pd.Timedelta(days=HORIZON_DAYS - 1)

    actual = pd.read_parquet(args.actuals)
    actual["SKU"] = normalize_sku_series(actual["SKU"])
    actual["SoldUnits"] = pd.to_numeric(actual["SoldUnits"], errors="coerce").fillna(0)
    actual_sku = actual.groupby("SKU", as_index=False)["SoldUnits"].sum()

    crosswalk = load_crosswalk(args.ledger_db)

    # Existing frozen candidates saved for this origin.
    frozen = pd.read_parquet(args.frozen_candidates)
    frozen["SKU"] = normalize_sku_series(frozen["SKU"])
    frozen["ForecastDate"] = pd.to_datetime(frozen["ForecastDate"]).dt.normalize()
    frozen = frozen.loc[frozen["ForecastDate"].between(origin, through)]

    # Corporate daily feed for the anchor path.
    corporate = load_corporate_daily(args.frozen_candidates, origin)

    # Build new candidates (base + activation).
    new_frames = []
    for activation in (False, True):
        config = ModelConfig(origin=origin, lookback_days=args.lookback_days,
                             seasonal_years=args.seasonal_years, use_activation=activation)
        combined, meta = build_candidates(config, crosswalk, corporate)
        new_frames.append(combined)
        tag = "activation" if activation else "base"
        (args.output_dir).mkdir(parents=True, exist_ok=True)
        (args.output_dir / f"metadata_{tag}.json").write_text(json.dumps(meta, indent=2, default=str))
    new_daily = pd.concat(new_frames, ignore_index=True).drop_duplicates(
        ["Candidate", "SKU", "ForecastDate"]
    )

    all_daily = pd.concat([frozen[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]], new_daily],
                          ignore_index=True)

    score_rows, cat_frames = [], []
    for name, grp in all_daily.groupby("Candidate"):
        fsku = grp.groupby("SKU", as_index=False)["ForecastUnits"].sum()
        score_rows.append(score_candidate(fsku, actual_sku, str(name)))
        cat_frames.append(category_table(fsku, actual_sku, crosswalk, str(name), ["GIRM", "BOYM"]))

    scores = pd.DataFrame(score_rows).sort_values("SKU_WAPE").reset_index(drop=True)
    categories = pd.concat(cat_frames, ignore_index=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    scores.to_csv(args.output_dir / "leaderboard.csv", index=False)
    categories.to_csv(args.output_dir / "category_scorecard.csv", index=False)
    new_daily.to_parquet(args.output_dir / "category_pool_candidates.parquet", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.2f}")
    print(f"\nOrigin {origin.date()} -> {through.date()}  |  actual sold units: {actual_sku['SoldUnits'].sum():,.0f}\n")
    print(scores.to_string(index=False))
    print("\nCategory scorecard (GIRM/BOYM):")
    print(categories.to_string(index=False))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
