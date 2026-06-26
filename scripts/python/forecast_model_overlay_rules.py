"""Backtest transparent overlay rules on top of the champion sklearn forecast."""

from __future__ import annotations

import argparse
import json
import sys
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_compare_sklearn import require_sklearn, run_single_stage, split_panel  # noqa: E402
from forecast_model_rolling_origin_sklearn import DEFAULT_WINDOWS, parse_window  # noqa: E402
from forecast_model_train import (  # noqa: E402
    BASELINE_COLUMNS,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    TARGET_COLUMN,
    configure_threads,
    evaluate_predictions,
    load_panel,
)


DEFAULT_OUTPUT_DIR = MODEL_DIR / "champion_overlay_rules"
CHAMPION_MODEL = "hgb_absolute_log"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for backtesting forecast overlay rules.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Backtest overlay rules for the champion forecast.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--max-train-rows", type=int, default=500_000)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=180)
    parser.add_argument("--learning-rate", type=float, default=0.06)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument("--calibration-days", type=int, default=28)
    parser.add_argument(
        "--calibration-mode",
        choices=[
            "global",
            "sku-promo",
            "category",
            "category-velocity",
            "category-velocity-promo",
            "none",
        ],
        default="global",
    )
    parser.add_argument("--calibration-min-rows", type=int, default=500)
    parser.add_argument("--calibration-min-actual-units", type=float, default=50.0)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--exclude-corporate-features",
        action="store_true",
        help="Exclude corporate forecast fields from model features; keep them as comparison baselines.",
    )
    parser.add_argument(
        "--include-product-identity-features",
        action="store_true",
        help="Add Item, Color, Size, and ItemColor categorical features for experimental family-aware models.",
    )
    parser.add_argument(
        "--window",
        action="append",
        dest="windows",
        help="Holdout window as START:END:LABEL. Repeat for multiple windows.",
    )
    return parser.parse_args()


def args_for_window(args: argparse.Namespace, window: dict[str, str]) -> argparse.Namespace:
    """Create a copy of arguments updated with dates for the target window.

    Args:
        args: Base command-line arguments namespace.
        window: Dict containing 'start' and 'end' date strings.

    Returns:
        argparse.Namespace: Updated arguments namespace copy.
    """
    window_args = copy(args)
    window_args.holdout_start = window["start"]
    window_args.holdout_end = window["end"]
    window_args.holdout_days = 0
    return window_args


def add_overlay_columns(scored: pd.DataFrame) -> pd.DataFrame:
    """Apply experimental overlay blending and flooring rules to model predictions.

    Args:
        scored: DataFrame of model predictions.

    Returns:
        pd.DataFrame: DataFrame with additional columns for each overlay rule.
    """
    scored = scored.copy()
    raw = scored[f"{CHAMPION_MODEL}ForecastQty"]
    recent7 = scored["Recent7BaselineQty"].fillna(0)
    promo = scored["HasSkuPDLPromotion"].fillna(False)

    rules = {
        "OverlayPromoAAABBlendR7_015": (["AA", "A", "B"], 0.15),
        "OverlayPromoAAABBlendR7_025": (["AA", "A", "B"], 0.25),
        "OverlayPromoAABlendR7_025": (["AA", "A"], 0.25),
        "OverlayPromoAABlendR7_035": (["AA", "A"], 0.35),
        "OverlayPromoAAABFloorR7_035": (["AA", "A", "B"], 0.35),
    }
    for name, (velocities, weight_or_factor) in rules.items():
        mask = scored["Velocity"].isin(velocities) & promo
        pred = raw.copy()
        if "Blend" in name:
            pred.loc[mask] = raw.loc[mask] * (1 - weight_or_factor) + recent7.loc[mask] * weight_or_factor
        else:
            pred.loc[mask] = np.maximum(raw.loc[mask], recent7.loc[mask] * weight_or_factor)
        scored[name] = pred.clip(lower=0)
    return scored


def run_window(
    ml: dict[str, Any],
    panel: pd.DataFrame,
    args: argparse.Namespace,
    window: dict[str, str],
) -> pd.DataFrame:
    """Train the champion model and evaluate all overlay rules for a single window.

    Args:
        ml: Dictionary containing scikit-learn module/class references.
        panel: Full feature panel history DataFrame.
        args: Global command-line configuration arguments.
        window: Window descriptors (start, end, label).

    Returns:
        pd.DataFrame: Evaluation summary metrics for all candidates.
    """
    window_args = args_for_window(args, window)
    train, calibration, holdout, holdout_start, holdout_end = split_panel(panel, window_args)
    print(
        f"Window {window['label']}: {holdout_start.date()} to {holdout_end.date()} "
        f"train={len(train):,} calibration={len(calibration):,} holdout={len(holdout):,}"
    )
    scored, _ = run_single_stage(CHAMPION_MODEL, ml, train, calibration, holdout, window_args)
    scored = add_overlay_columns(scored)
    forecast_cols = [
        f"{CHAMPION_MODEL}ForecastQty",
        f"{CHAMPION_MODEL}CalibratedForecastQty",
        "OverlayPromoAAABBlendR7_015",
        "OverlayPromoAAABBlendR7_025",
        "OverlayPromoAABlendR7_025",
        "OverlayPromoAABlendR7_035",
        "OverlayPromoAAABFloorR7_035",
        *[col for col in BASELINE_COLUMNS if col in scored.columns],
    ]
    summary = evaluate_predictions(scored, forecast_cols)
    summary.insert(0, "WindowLabel", window["label"])
    summary.insert(1, "HoldoutStart", window["start"])
    summary.insert(2, "HoldoutEnd", window["end"])
    return summary


def aggregate(summary: pd.DataFrame) -> pd.DataFrame:
    """Aggregate window-level prediction metrics across all backtest windows.

    Args:
        summary: Combined window-level summary metrics.

    Returns:
        pd.DataFrame: Aggregated performance metrics sorted by mean WAPE.
    """
    summary = summary.copy()
    summary["AbsBiasPct"] = summary["BiasPct"].abs()
    winners = (
        summary.sort_values(["WindowLabel", "WAPE", "AbsBiasPct"])
        .groupby("WindowLabel", as_index=False)
        .head(1)
    )
    winner_counts = winners.groupby("ForecastName").size().rename("WindowWins")
    out = (
        summary.groupby("ForecastName", as_index=False)
        .agg(
            Windows=("WindowLabel", "nunique"),
            MeanBiasPct=("BiasPct", "mean"),
            MeanAbsBiasPct=("AbsBiasPct", "mean"),
            MeanWAPE=("WAPE", "mean"),
            MedianWAPE=("WAPE", "median"),
        )
        .merge(winner_counts, on="ForecastName", how="left")
        .fillna({"WindowWins": 0})
        .sort_values(["MeanWAPE", "MeanAbsBiasPct"])
    )
    out["WindowWins"] = out["WindowWins"].astype(int)
    return out


def main() -> None:
    """Execute the champion model overlay rules backtest pipeline."""
    args = parse_args()
    configure_threads(args.threads)
    ml = require_sklearn()
    panel = load_panel(args.panel, args.start_date)
    windows = [parse_window(value) for value in (args.windows or DEFAULT_WINDOWS)]

    summaries = [run_window(ml, panel, args, window) for window in windows]
    summary = pd.concat(summaries, ignore_index=True)
    agg = aggregate(summary)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / "overlay_rule_window_summary.csv", index=False)
    agg.to_csv(args.output_dir / "overlay_rule_aggregate.csv", index=False)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "windows": windows,
        "model": CHAMPION_MODEL,
        "threads": args.threads,
        "max_train_rows": args.max_train_rows,
        "max_iter": args.max_iter,
        "calibration_mode": args.calibration_mode,
        "exclude_corporate_features": args.exclude_corporate_features,
        "include_product_identity_features": args.include_product_identity_features,
        "target": TARGET_COLUMN,
        "date_column": DATE_COLUMN,
    }
    with (args.output_dir / "overlay_rule_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print(agg.head(20).to_string(index=False))
    print(f"Wrote overlay rule outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
