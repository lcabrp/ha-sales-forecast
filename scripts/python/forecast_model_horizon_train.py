"""Horizon-consistent forward forecast: train AND evaluate honestly.

WHY THIS SCRIPT EXISTS
----------------------
The frozen-origin harness (``forecast_model_frozen_origin_eval.py``) showed that
the current champion is trained on in-window demand lags and therefore over-relies
on ``SoldUnitsLag1`` - information a real 14-day-ahead forecast does not have. When
those lags are frozen at the origin, the champion extrapolates a single recent
value flat across the horizon and degrades past corporate by FD8..FD14.

The fix is to make TRAINING match how the model is actually used: predict demand at
horizon ``h`` (FD1..FD14) given only the SKU's state AS OF the forecast origin, with
the horizon itself supplied as a feature. This is a *direct multi-horizon* model.

Construction (identical for train and inference):
  * Origin-state features  - own-demand lags, family lags, inventory, supply, and
    inbound features taken at the origin date (the panel row dated ``O`` already
    holds these as lagged, leak-free values). Held constant across the horizon.
  * Target-date features    - calendar and promotion-calendar values for the target
    date ``O + h`` (legitimately known ahead of time).
  * Horizon feature         - the integer ``h`` (1..14).
  * Target                  - actual ``SoldUnits`` on ``O + h`` (0 if no sale).

Because the model now learns demand decay over the horizon directly, inference is a
single batched scoring of FD1..FD14 - no leakage, no recursion needed.

This script trains the horizon-consistent model, and for a fair side-by-side it
also trains the plain champion the old way. BOTH are then scored at the same honest
14-day-ahead horizon (via the frozen-origin construction) against corporate and the
recent-demand baselines. The end goal is a model that genuinely beats the corporate
forecast at the horizon corporate is actually delivered on.

It does NOT modify any existing script. It reuses helpers read-only, including the
frozen-origin harness.
"""

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

# Read-only reuse of existing, unmodified modules.
from forecast_model_compare_sklearn import build_candidate, predict_candidate, require_sklearn  # noqa: E402
from forecast_model_frozen_origin_eval import (  # noqa: E402
    apply_frozen_features,
    coverage_row,
    freeze_columns,
    origin_snapshot,
    parse_window,
)
from forecast_model_train import (  # noqa: E402
    BASELINE_COLUMNS,
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    configure_threads,
    evaluate_predictions,
    load_panel,
    prepare_xy,
    sample_training_rows,
)


DEFAULT_OUTPUT_DIR = MODEL_DIR / "horizon_consistent"
CHAMPION_MODEL = "hgb_absolute_log"
SKU_COLUMN = "SKU"
HORIZON_COLUMN = "Horizon"
MAX_HORIZON = 14

CALENDAR_COLUMNS = ["DayOfWeek", "WeekOfYear", "Month", "IsWeekend"]
PROMO_PREFIXES = ("pdl_", "coupon_")
PROMO_EXTRA = {"HasPDLPromotion", "HasCouponPromotion", "HasAnyPromotion", "HasSkuPDLPromotion"}


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for training and evaluating a horizon-consistent model.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Train and honestly evaluate a horizon-consistent forecast.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument(
        "--window",
        action="append",
        dest="windows",
        help="Forecast window FD_START:FD_END:LABEL (origin = FD_START minus one day). Repeatable.",
    )
    parser.add_argument(
        "--origin-stride",
        type=int,
        default=14,
        help="Spacing (days) between training origins. Lower = more origins, larger training set.",
    )
    parser.add_argument(
        "--keep-zero-frac",
        type=float,
        default=0.3,
        help="Fraction of zero-demand (SKU, origin, horizon) training rows to keep. Positives always kept.",
    )
    parser.add_argument("--max-train-rows", type=int, default=500_000)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=180)
    parser.add_argument("--learning-rate", type=float, default=0.06)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument(
        "--exclude-corporate-features",
        action="store_true",
        help="Keep corporate forecast columns as baselines but exclude them from model features.",
    )
    parser.add_argument(
        "--include-product-identity-features",
        action="store_true",
        help="Add Item/Color/Size/ItemColor categorical features (experimental).",
    )
    parser.add_argument(
        "--also-train-old-champion",
        action="store_true",
        default=True,
        help="Also train the plain champion on raw panel rows for an honest side-by-side.",
    )
    parser.add_argument(
        "--save-forecast",
        type=Path,
        default=None,
        help="Optional parquet path to persist per-SKU/day forecasts + actuals + "
        "attributes (consumed by the reconciliation and slotting scorecard scripts).",
    )
    return parser.parse_args()


# Columns persisted when --save-forecast is set, so downstream operational scripts
# (reconciliation, velocity-tier / slotting scorecard) can run without retraining.
SAVE_FORECAST_COLUMNS = [
    "WindowLabel",
    SKU_COLUMN,
    DATE_COLUMN,
    "FDDay",
    TARGET_COLUMN,
    "HorizonConsistentMLForecastQty",
    "FrozenChampionMLForecastQty",
    "CorporateForecastQty",
    "Division",
    "Department",
    "Class",
    "KeyCategoryView",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    *BASELINE_COLUMNS,
]

DEFAULT_WINDOWS = [
    "2026-05-12:2026-05-25:y2026_fd_a",
    "2026-05-26:2026-06-08:y2026_fd_b",
]


def model_feature_columns(panel_columns: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Determine available model features split by data type.

    Args:
        panel_columns: Set of all columns present in the input panel.

    Returns:
        tuple[list[str], list[str], list[str]]: Numeric, categorical, and boolean feature lists.
    """
    numeric = [c for c in NUMERIC_FEATURES if c in panel_columns]
    categorical = [c for c in CATEGORICAL_FEATURES if c in panel_columns]
    boolean = [c for c in BOOLEAN_FEATURES if c in panel_columns]
    return numeric, categorical, boolean


def promo_columns(panel_columns: list[str]) -> list[str]:
    """Identify promotion-related columns in the panel dataset.

    Args:
        panel_columns: List of columns to inspect.

    Returns:
        list[str]: Promotion column names.
    """
    cols = []
    for col in panel_columns:
        if col.startswith(PROMO_PREFIXES) or col in PROMO_EXTRA:
            cols.append(col)
    return cols


def calendar_frame(target: pd.Timestamp, n_rows: int) -> dict[str, Any]:
    """Generate calendar features dictionary for a target date.

    Args:
        target: Target date timestamp.
        n_rows: Number of rows in target dataframe.

    Returns:
        dict[str, Any]: Calendar features dictionary.
    """
    iso_week = int(target.isocalendar().week)
    weekend = target.weekday() >= 5
    return {
        "DayOfWeek": int(target.weekday()),
        "WeekOfYear": iso_week,
        "Month": int(target.month),
        "IsWeekend": bool(weekend),
    }


def build_lookup_by_date(
    panel: pd.DataFrame,
    value_columns: list[str],
    dates: set[pd.Timestamp],
) -> dict[pd.Timestamp, pd.DataFrame]:
    """Construct a date-indexed lookup dictionary containing values for specific dates.

    Args:
        panel: Panel dataset.
        value_columns: Columns to extract.
        dates: Set of dates to filter.

    Returns:
        dict[pd.Timestamp, pd.DataFrame]: Date-indexed lookup mapping.
    """
    needed = panel.loc[panel[DATE_COLUMN].isin(dates), [SKU_COLUMN, DATE_COLUMN, *value_columns]]
    return {pd.Timestamp(dt): grp.drop(columns=[DATE_COLUMN]) for dt, grp in needed.groupby(DATE_COLUMN)}


def horizon_training_frame(
    panel: pd.DataFrame,
    origins: list[pd.Timestamp],
    promo_cols: list[str],
    args: argparse.Namespace,
) -> pd.DataFrame:
    """Expand (SKU active at origin) x horizon into a leak-free training table.

    Groups data by historical origin dates, matches each active SKU against targets at
    horizons 1..14, incorporates target-date promotions/calendar features, and aggregates actuals.

    Args:
        panel: Fully populated model panel.
        origins: Selected training origin dates.
        promo_cols: Promotion-related columns.
        args: Pipeline options.

    Returns:
        pd.DataFrame: Expanded training dataset.
    """
    target_dates = {o + pd.Timedelta(days=h) for o in origins for h in range(1, MAX_HORIZON + 1)}
    sold_lookup = build_lookup_by_date(panel, [TARGET_COLUMN], target_dates)
    promo_lookup = build_lookup_by_date(panel, promo_cols, target_dates)

    # Budget SKUs per origin so the expanded table stays near max-train-rows.
    per_origin_cap = max(150, args.max_train_rows // max(1, len(origins) * MAX_HORIZON))

    pieces: list[pd.DataFrame] = []
    for origin in origins:
        base = panel.loc[panel[DATE_COLUMN].eq(origin)].copy()
        if base.empty:
            continue
        if len(base) > per_origin_cap:
            base = base.sample(n=per_origin_cap, random_state=args.random_state)
        base = base.drop(columns=[col for col in promo_cols if col in base.columns], errors="ignore")

        for h in range(1, MAX_HORIZON + 1):
            target = origin + pd.Timedelta(days=h)
            frame = base.copy()
            frame[HORIZON_COLUMN] = h

            cal = calendar_frame(target, len(frame))
            for col, value in cal.items():
                if col in NUMERIC_FEATURES or col in BOOLEAN_FEATURES:
                    frame[col] = value

            promo_today = promo_lookup.get(target)
            if promo_today is not None:
                frame = frame.merge(promo_today, on=SKU_COLUMN, how="left")
            for col in promo_cols:
                if col not in frame.columns:
                    frame[col] = np.nan

            sold_today = sold_lookup.get(target)
            if sold_today is not None:
                frame = frame.merge(sold_today.rename(columns={TARGET_COLUMN: "_target_sold"}), on=SKU_COLUMN, how="left")
                frame[TARGET_COLUMN] = pd.to_numeric(frame["_target_sold"], errors="coerce").fillna(0.0)
                frame = frame.drop(columns=["_target_sold"])
            else:
                frame[TARGET_COLUMN] = 0.0

            pieces.append(frame)

    if not pieces:
        raise ValueError("No training rows were built; check origins and panel coverage.")

    training = pd.concat(pieces, ignore_index=True)

    # Down-sample zero-demand rows so the model is not swamped by structural zeros.
    positive = training.loc[training[TARGET_COLUMN].gt(0)]
    zero = training.loc[training[TARGET_COLUMN].le(0)]
    if 0.0 <= args.keep_zero_frac < 1.0 and len(zero) > 0:
        keep_n = int(len(zero) * args.keep_zero_frac)
        zero = zero.sample(n=keep_n, random_state=args.random_state) if keep_n > 0 else zero.iloc[0:0]
    training = pd.concat([positive, zero], ignore_index=True)
    training = sample_training_rows(training, args.max_train_rows, args.random_state)
    return training.sample(frac=1.0, random_state=args.random_state).reset_index(drop=True)


def prepare_with_horizon(
    frame: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, np.ndarray, list[str], list[str], list[str]]:
    """Clean, slice, and return features and targets with Horizon appended to numeric features.

    Args:
        frame: Input dataset with a Horizon column.
        args: Pipeline options.

    Returns:
        tuple[pd.DataFrame, np.ndarray, list[str], list[str], list[str]]:
            Features dataframe, log target array, numeric feature columns,
            categorical feature columns, and boolean feature columns.
    """
    x, y, numeric, categorical, boolean = prepare_xy(
        frame,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    x = x.copy()
    x[HORIZON_COLUMN] = pd.to_numeric(frame[HORIZON_COLUMN], errors="coerce").fillna(0).to_numpy()
    numeric = [*numeric, HORIZON_COLUMN]
    return x, y, numeric, categorical, boolean


def fit_horizon_model(
    ml: dict[str, Any],
    training: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[Any, str]:
    """Fit a horizon-consistent model on the expanded multi-horizon training frame.

    Args:
        ml: Dictionary of scikit-learn components.
        training: Prepared training dataset.
        args: Command parameters.

    Returns:
        tuple[Any, str]: Fitted Pipeline and candidate model mode key.
    """
    x_train, y_train, numeric, categorical, boolean = prepare_with_horizon(training, args)
    model, mode = build_candidate(CHAMPION_MODEL, ml, numeric, categorical, boolean, args)
    model.fit(x_train, y_train)
    return model, mode


def fit_old_champion(
    ml: dict[str, Any],
    panel: pd.DataFrame,
    window_start: pd.Timestamp,
    args: argparse.Namespace,
) -> tuple[Any, str]:
    """Train the plain champion model on raw panel rows (non-horizon consistent) for backtest side-by-sides.

    Args:
        ml: Dictionary of scikit-learn components.
        panel: Fully merged panel dataset.
        window_start: Holdout window start timestamp.
        args: Command options.

    Returns:
        tuple[Any, str]: Fitted Pipeline and model mode.
    """
    train = panel.loc[panel[DATE_COLUMN].lt(window_start)].copy()
    train = sample_training_rows(train, args.max_train_rows, args.random_state)
    x_train, y_train, numeric, categorical, boolean = prepare_xy(
        train,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    model, mode = build_candidate(CHAMPION_MODEL, ml, numeric, categorical, boolean, args)
    model.fit(x_train, y_train)
    return model, mode


def score_horizon(
    model: Any,
    mode: str,
    holdout: pd.DataFrame,
    snapshot: pd.DataFrame,
    freeze_cols: list[str],
    origin: pd.Timestamp,
    args: argparse.Namespace,
) -> np.ndarray:
    """Generate predictions using the horizon-consistent model.

    Freezes origin-state features, derives target-date horizons, and generates predictions.

    Args:
        model: Fitted model Pipeline.
        mode: Model mode key.
        holdout: Holdout prediction template.
        snapshot: Snapshot of features frozen at origin.
        freeze_cols: Columns to freeze.
        origin: Forecast origin date.
        args: Command options.

    Returns:
        np.ndarray: Predicted demand units.
    """
    frame = apply_frozen_features(holdout, snapshot, freeze_cols).copy()
    frame[HORIZON_COLUMN] = (frame[DATE_COLUMN] - origin).dt.days
    x, _, _, _, _ = prepare_with_horizon(frame, args)
    return predict_candidate(model, mode, x)


def score_frozen_old(
    model: Any,
    mode: str,
    holdout: pd.DataFrame,
    snapshot: pd.DataFrame,
    freeze_cols: list[str],
    args: argparse.Namespace,
) -> np.ndarray:
    """Generate predictions using the plain champion model with origin-frozen features.

    Args:
        model: Fitted model pipeline.
        mode: Model mode.
        holdout: Prediction holdout frame.
        snapshot: Snapshot of features frozen at origin.
        freeze_cols: Columns to freeze.
        args: Command options.

    Returns:
        np.ndarray: Predicted demand units.
    """
    frame = apply_frozen_features(holdout, snapshot, freeze_cols)
    x, _, _, _, _ = prepare_xy(frame, args.exclude_corporate_features, args.include_product_identity_features)
    return predict_candidate(model, mode, x)


def evaluate_window(scored: pd.DataFrame, forecast_cols: list[str], window: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Calculate aggregated, daily, and coverage evaluation metrics for a holdout window.

    Args:
        scored: Scored predictions dataset.
        forecast_cols: Forecast columns to evaluate.
        window: Window metadata dictionary.

    Returns:
        dict[str, pd.DataFrame]: Compiled evaluation datasets.
    """
    scored = scored.copy()
    scored["FDDay"] = (scored[DATE_COLUMN] - window["origin"]).dt.days

    aggregate = evaluate_predictions(scored, forecast_cols)
    aggregate.insert(0, "WindowLabel", window["label"])
    per_day = evaluate_predictions(scored, forecast_cols, ["FDDay"])
    per_day.insert(0, "WindowLabel", window["label"])
    coverage = pd.DataFrame([coverage_row(scored, col) for col in forecast_cols])
    coverage.insert(0, "WindowLabel", window["label"])
    return {"aggregate": aggregate, "per_day": per_day, "coverage": coverage}


def training_origins(panel: pd.DataFrame, window_start: pd.Timestamp, stride: int) -> list[pd.Timestamp]:
    """Identify valid historical origin dates before the forecast window.

    Args:
        panel: Model panel dataset.
        window_start: Holdout window start date.
        stride: Distance in days between training origins.

    Returns:
        list[pd.Timestamp]: List of training origin dates.
    """
    available = panel.loc[panel[DATE_COLUMN].lt(window_start), DATE_COLUMN]
    if available.empty:
        return []
    first = available.min()
    last = available.max()
    origins = list(pd.date_range(first + pd.Timedelta(days=MAX_HORIZON), last, freq=f"{max(1, stride)}D"))
    return [pd.Timestamp(o) for o in origins]


def run_window(
    ml: dict[str, Any],
    panel: pd.DataFrame,
    window: dict[str, Any],
    promo_cols: list[str],
    args: argparse.Namespace,
) -> dict[str, pd.DataFrame]:
    """Execute training and frozen-origin prediction on a single holdout window.

    Args:
        ml: Dictionary of scikit-learn components.
        panel: Fully merged model panel.
        window: Window metadata.
        promo_cols: Promotion columns.
        args: Command parameters.

    Returns:
        dict[str, pd.DataFrame]: Scored dataset and evaluation metrics.
    """
    holdout = panel.loc[panel[DATE_COLUMN].between(window["start"], window["end"])].copy()
    if holdout.empty:
        raise ValueError(f"No panel rows in forecast window {window['label']}.")

    numeric, categorical, boolean = model_feature_columns(set(panel.columns))
    freeze_cols = [c for c in freeze_columns([*numeric, *categorical, *boolean]) if c in panel.columns]
    snapshot = origin_snapshot(panel, window["origin"], freeze_cols)

    origins = training_origins(panel, window["start"], args.origin_stride)
    if not origins:
        raise ValueError(f"No training origins available before {window['start'].date()}.")
    training = horizon_training_frame(panel, origins, promo_cols, args)

    print(
        f"Window {window['label']}: origin={window['origin'].date()} "
        f"fd={window['start'].date()}..{window['end'].date()} "
        f"train_origins={len(origins)} horizon_train_rows={len(training):,} "
        f"holdout_rows={len(holdout):,} frozen_features={len(freeze_cols)}"
    )

    hz_model, hz_mode = fit_horizon_model(ml, training, args)
    holdout["HorizonConsistentMLForecastQty"] = score_horizon(
        hz_model, hz_mode, holdout, snapshot, freeze_cols, window["origin"], args
    )
    forecast_cols = ["HorizonConsistentMLForecastQty"]

    if args.also_train_old_champion:
        old_model, old_mode = fit_old_champion(ml, panel, window["start"], args)
        holdout["FrozenChampionMLForecastQty"] = score_frozen_old(
            old_model, old_mode, holdout, snapshot, freeze_cols, args
        )
        forecast_cols.append("FrozenChampionMLForecastQty")

    forecast_cols.extend([c for c in BASELINE_COLUMNS if c in holdout.columns])
    metrics = evaluate_window(holdout, forecast_cols, window)
    scored = holdout.copy()
    scored["FDDay"] = (scored[DATE_COLUMN] - window["origin"]).dt.days
    scored["WindowLabel"] = window["label"]
    metrics["scored"] = scored.loc[:, [c for c in SAVE_FORECAST_COLUMNS if c in scored.columns]]
    return metrics


def main() -> None:
    """Execute the command line entry point to train a horizon-consistent model and run frozen evaluation."""
    args = parse_args()
    configure_threads(args.threads)
    ml = require_sklearn()

    windows = [parse_window(v) for v in (args.windows or DEFAULT_WINDOWS)]
    panel = load_panel(args.panel, args.start_date)
    promo_cols = promo_columns(list(panel.columns))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    aggregates, per_days, coverages, scored_frames = [], [], [], []
    for window in windows:
        result = run_window(ml, panel, copy(window), promo_cols, args)
        aggregates.append(result["aggregate"])
        per_days.append(result["per_day"])
        coverages.append(result["coverage"])
        scored_frames.append(result["scored"])

    aggregate = pd.concat(aggregates, ignore_index=True)
    per_day = pd.concat(per_days, ignore_index=True)
    coverage = pd.concat(coverages, ignore_index=True)

    if args.save_forecast is not None:
        args.save_forecast.parent.mkdir(parents=True, exist_ok=True)
        pd.concat(scored_frames, ignore_index=True).to_parquet(args.save_forecast, index=False)
        print(f"Saved per-SKU/day forecasts to {args.save_forecast}")

    scoreboard = (
        aggregate.groupby("ForecastName", as_index=False)
        .agg(
            Windows=("WindowLabel", "nunique"),
            TotalActualUnits=("ActualUnits", "sum"),
            TotalForecastUnits=("ForecastUnits", "sum"),
            MeanWAPE=("WAPE", "mean"),
            MeanBiasPct=("BiasPct", "mean"),
        )
        .sort_values("MeanWAPE")
    )

    aggregate.to_csv(args.output_dir / "horizon_window_aggregate.csv", index=False)
    per_day.to_csv(args.output_dir / "horizon_fd_day_detail.csv", index=False)
    coverage.to_csv(args.output_dir / "horizon_coverage.csv", index=False)
    scoreboard.to_csv(args.output_dir / "horizon_scoreboard.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "output_dir": str(args.output_dir),
        "champion_model": CHAMPION_MODEL,
        "max_horizon": MAX_HORIZON,
        "origin_stride": args.origin_stride,
        "keep_zero_frac": args.keep_zero_frac,
        "windows": [
            {"label": w["label"], "origin": str(w["origin"].date()),
             "fd_start": str(w["start"].date()), "fd_end": str(w["end"].date())}
            for w in windows
        ],
        "exclude_corporate_features": args.exclude_corporate_features,
        "max_train_rows": args.max_train_rows,
        "max_iter": args.max_iter,
        "learning_rate": args.learning_rate,
        "notes": [
            "HorizonConsistentMLForecastQty is trained on (SKU, origin) x horizon rows with "
            "origin-state lag features, target-date calendar/promo features, and Horizon as a "
            "feature, then scored leak-free over FD1..FD14.",
            "FrozenChampionMLForecastQty is the plain champion trained on raw panel rows but "
            "scored at the same honest horizon, for a like-for-like comparison.",
            "Both are compared against corporate and recent-demand baselines on an identical grid.",
        ],
    }
    with (args.output_dir / "horizon_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print("\nScoreboard (mean across windows):")
    print(scoreboard.to_string(index=False))
    print("\nCoverage by window/forecast:")
    print(coverage.to_string(index=False))
    print(f"\nWrote horizon-consistent outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
