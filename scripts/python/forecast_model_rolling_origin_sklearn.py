"""Run rolling-origin scikit-learn forecast comparisons.

This script is the repeatable scoreboard for the current no-new-dependency
model path.  It evaluates selected scikit-learn candidates over multiple
historical holdout windows using the same model panel and metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import copy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_compare_sklearn import (  # noqa: E402
    DEFAULT_OUTPUT_DIR as COMPARE_OUTPUT_DIR,
    require_sklearn,
    run_single_stage,
    run_two_stage,
    split_panel,
)
from forecast_model_train import (  # noqa: E402
    BASELINE_COLUMNS,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    TARGET_COLUMN,
    configure_threads,
    evaluate_predictions,
    load_panel,
)


DEFAULT_OUTPUT_DIR = COMPARE_OUTPUT_DIR.parent / "sklearn_rolling_origin"
DEFAULT_WINDOWS = [
    "2026-01-01:2026-01-31:y2026_m01",
    "2026-02-01:2026-02-28:y2026_m02",
    "2026-03-01:2026-03-31:y2026_m03",
    "2026-04-01:2026-04-30:y2026_m04",
    "2026-05-01:2026-05-31:y2026_m05",
    "2026-06-02:2026-06-08:y2026_w23",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rolling-origin sklearn model comparisons.")
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
    parser.add_argument("--threads", type=int, default=1)
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
        "--models",
        nargs="+",
        default=["hgb_squared_log", "hgb_absolute_log", "two_stage_hgb_log"],
    )
    parser.add_argument(
        "--window",
        action="append",
        dest="windows",
        help="Holdout window as START:END:LABEL. Repeat for multiple windows.",
    )
    return parser.parse_args()


def parse_window(value: str) -> dict[str, str]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Window must be START:END:LABEL, got {value!r}")
    start, end, label = parts
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end:
        raise ValueError(f"Window start {start} is after end {end}")
    return {"start": start, "end": end, "label": label}


def args_for_window(args: argparse.Namespace, window: dict[str, str]) -> argparse.Namespace:
    window_args = copy(args)
    window_args.holdout_start = window["start"]
    window_args.holdout_end = window["end"]
    window_args.holdout_days = 0
    return window_args


def summarize_baselines(holdout: pd.DataFrame, window: dict[str, str]) -> pd.DataFrame:
    forecast_cols = [col for col in BASELINE_COLUMNS if col in holdout.columns]
    summary = evaluate_predictions(holdout, forecast_cols)
    summary.insert(0, "WindowLabel", window["label"])
    summary.insert(1, "HoldoutStart", window["start"])
    summary.insert(2, "HoldoutEnd", window["end"])
    summary.insert(3, "ModelKey", "baseline")
    return summary


def run_window(
    ml: dict[str, Any],
    panel: pd.DataFrame,
    args: argparse.Namespace,
    window: dict[str, str],
) -> pd.DataFrame:
    window_args = args_for_window(args, window)
    train, calibration, holdout, holdout_start, holdout_end = split_panel(panel, window_args)
    rows = [summarize_baselines(holdout, window)]

    print(
        f"Window {window['label']}: {holdout_start.date()} to {holdout_end.date()} "
        f"train={len(train):,} calibration={len(calibration):,} holdout={len(holdout):,}"
    )
    for model_name in args.models:
        print(f"  running {model_name}")
        if model_name == "two_stage_hgb_log":
            scored, _ = run_two_stage(ml, train, calibration, holdout, window_args)
        else:
            scored, _ = run_single_stage(model_name, ml, train, calibration, holdout, window_args)

        forecast_cols = [
            f"{model_name}ForecastQty",
            f"{model_name}CalibratedForecastQty",
        ]
        summary = evaluate_predictions(scored, forecast_cols)
        summary.insert(0, "WindowLabel", window["label"])
        summary.insert(1, "HoldoutStart", window["start"])
        summary.insert(2, "HoldoutEnd", window["end"])
        summary.insert(3, "ModelKey", model_name)
        rows.append(summary)

    return pd.concat(rows, ignore_index=True)


def add_window_context(summary: pd.DataFrame) -> pd.DataFrame:
    summary = summary.copy()
    summary["AbsBiasPct"] = summary["BiasPct"].abs()
    summary["IsModelForecast"] = summary["ModelKey"].ne("baseline")
    return summary


def best_by_window(summary: pd.DataFrame) -> pd.DataFrame:
    ranked = summary.sort_values(["WindowLabel", "WAPE", "AbsBiasPct"], ascending=[True, True, True])
    return ranked.groupby("WindowLabel", as_index=False).head(1).reset_index(drop=True)


def aggregate_by_forecast(summary: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        summary.groupby(["ModelKey", "ForecastName"], as_index=False)
        .agg(
            Windows=("WindowLabel", "nunique"),
            TotalActualUnits=("ActualUnits", "sum"),
            TotalForecastUnits=("ForecastUnits", "sum"),
            MeanBiasPct=("BiasPct", "mean"),
            MeanAbsBiasPct=("AbsBiasPct", "mean"),
            MeanWAPE=("WAPE", "mean"),
            MedianWAPE=("WAPE", "median"),
            BestWindowCount=("WAPE", "size"),
        )
        .sort_values(["MeanWAPE", "MeanAbsBiasPct"])
    )
    winners = best_by_window(summary)
    winner_counts = winners.groupby(["ModelKey", "ForecastName"]).size().rename("WindowWins")
    grouped = grouped.merge(winner_counts, on=["ModelKey", "ForecastName"], how="left")
    grouped["WindowWins"] = grouped["WindowWins"].fillna(0).astype(int)
    return grouped


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)
    ml = require_sklearn()
    windows = [parse_window(value) for value in (args.windows or DEFAULT_WINDOWS)]
    panel = load_panel(args.panel, args.start_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    summaries = [run_window(ml, panel, args, window) for window in windows]
    summary = add_window_context(pd.concat(summaries, ignore_index=True))
    winners = best_by_window(summary)
    aggregate = aggregate_by_forecast(summary)

    summary.to_csv(args.output_dir / "rolling_origin_summary.csv", index=False)
    winners.to_csv(args.output_dir / "rolling_origin_window_winners.csv", index=False)
    aggregate.to_csv(args.output_dir / "rolling_origin_forecast_aggregate.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "output_dir": str(args.output_dir),
        "windows": windows,
        "models": args.models,
        "max_train_rows": args.max_train_rows,
        "max_iter": args.max_iter,
        "learning_rate": args.learning_rate,
        "calibration_mode": args.calibration_mode,
        "exclude_corporate_features": args.exclude_corporate_features,
        "include_product_identity_features": args.include_product_identity_features,
        "target": TARGET_COLUMN,
        "date_column": DATE_COLUMN,
    }
    with (args.output_dir / "rolling_origin_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print("Window winners:")
    print(
        winners[
            ["WindowLabel", "ForecastName", "ActualUnits", "ForecastUnits", "BiasPct", "WAPE"]
        ].to_string(index=False)
    )
    print("\nAggregate:")
    print(
        aggregate[
            [
                "ModelKey",
                "ForecastName",
                "Windows",
                "MeanBiasPct",
                "MeanAbsBiasPct",
                "MeanWAPE",
                "MedianWAPE",
                "WindowWins",
            ]
        ].head(12).to_string(index=False)
    )
    print(f"Wrote rolling-origin outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
