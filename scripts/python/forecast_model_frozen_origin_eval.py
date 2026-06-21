"""Frozen-origin / recursive forward-forecast evaluation harness.

WHY THIS SCRIPT EXISTS
----------------------
The existing rolling-origin scoreboard scores *every day* of a multi-day holdout
window using the panel's pre-computed demand-lag features (``SoldUnitsLag1``,
``SoldUnitsRolling7``, ``ItemColorSoldUnitsLag1`` ...).  Those lag columns are
derived from the panel's own actuals, so when the model scores day ``D`` inside
the holdout it can "see" actual sold units from days ``D-1, D-7, ...`` that also
fall *inside* the holdout window.  That is information a genuine forward forecast
cannot have: a real 14-day Forward Replenishment forecast is frozen at
``ForecastStartDate`` and must predict FD2..FD14 without ever seeing FD1..FD13
actuals.

The result is that the model is effectively scored as a ~1-day-ahead nowcast,
while the corporate forecast it is compared against is a true 14-day-ahead
forecast.  That is apples-to-oranges and inflates the model's apparent win.

This harness produces an honest, horizon-matched comparison by re-deriving the
model's inputs so they only use information available at the forecast origin:

  * ``frozen``     - every leak-prone feature (own-demand lags, family lags,
                     inventory, warehouse-supply, inbound) is taken at the origin
                     date and held constant across the whole FD1..FD14 horizon.
                     Calendar features advance per day; promotion-calendar
                     features keep their (legitimately known-ahead) future
                     values from the panel.
  * ``recursive``  - same as ``frozen`` but the target's own autoregressive
                     features (``SoldUnitsLag1/Lag7/Lag14/Rolling7/Rolling28``)
                     are rebuilt each day from a per-SKU buffer seeded with
                     actuals up to the origin and then extended with the model's
                     OWN predictions.  This emulates true multi-step forecasting.
  * ``leaky``      - (diagnostic) scores exactly the way the existing scoreboard
                     does, using the holdout rows' in-window lag features.  It is
                     included ONLY so you can see the size of the leakage gap.

It does NOT modify any existing script.  It reuses their helpers read-only.

It trains the current champion (``hgb_absolute_log``) on data strictly before the
forecast window, then for each window reports, per FD-day and aggregated:
WAPE, bias, sold-unit coverage and zero-forecast sold %, for the model(s) and the
existing baselines (corporate, recent-7, recent-28, hybrid).

EXAMPLE
-------
    uv run python scripts/python/forecast_model_frozen_origin_eval.py \
        --threads 8 --max-train-rows 500000 --max-iter 180 \
        --exclude-corporate-features \
        --modes frozen recursive leaky \
        --window 2026-05-12:2026-05-25:y2026_fd_a \
        --window 2026-05-26:2026-06-08:y2026_fd_b
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import copy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

# Read-only reuse of the existing, unmodified modules.
from forecast_model_compare_sklearn import build_candidate, predict_candidate, require_sklearn  # noqa: E402
from forecast_model_train import (  # noqa: E402
    BASELINE_COLUMNS,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    TARGET_COLUMN,
    configure_threads,
    evaluate_predictions,
    load_panel,
    prepare_xy,
    sample_training_rows,
)


DEFAULT_OUTPUT_DIR = MODEL_DIR / "frozen_origin_eval"
CHAMPION_MODEL = "hgb_absolute_log"
SKU_COLUMN = "SKU"

# Autoregressive features that ``recursive`` mode rebuilds from the model's own
# forward predictions. Everything else stays frozen at the origin snapshot.
RECURSIVE_AR_COLUMNS = [
    "SoldUnitsLag1",
    "SoldUnitsLag7",
    "SoldUnitsLag14",
    "SoldUnitsRolling7",
    "SoldUnitsRolling28",
]
# Longest lookback the recursion needs to seed lags/rolling means at the origin.
RECURSIVE_SEED_DAYS = 28


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Frozen-origin / recursive forward forecast evaluation.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2025-01-01", help="Earliest panel date to load.")
    parser.add_argument(
        "--window",
        action="append",
        dest="windows",
        help="Forecast window as FD_START:FD_END:LABEL (origin is FD_START minus one day). Repeatable.",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=["frozen", "recursive", "leaky"],
        default=["frozen", "recursive", "leaky"],
        help="Which forecast construction modes to score.",
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
    return parser.parse_args()


DEFAULT_WINDOWS = [
    "2026-05-12:2026-05-25:y2026_fd_a",
    "2026-05-26:2026-06-08:y2026_fd_b",
]


def parse_window(value: str) -> dict[str, Any]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Window must be FD_START:FD_END:LABEL, got {value!r}")
    start, end, label = parts
    start_ts = pd.Timestamp(date.fromisoformat(start))
    end_ts = pd.Timestamp(date.fromisoformat(end))
    if start_ts > end_ts:
        raise ValueError(f"Window start {start} is after end {end}")
    return {
        "label": label,
        "start": start_ts,
        "end": end_ts,
        "origin": start_ts - pd.Timedelta(days=1),
    }


def freeze_columns(model_feature_columns: list[str]) -> list[str]:
    """Features that are only known up to the origin in a real forward forecast.

    Anything with a lag/rolling suffix (own-demand, family, inventory, supply,
    corporate) plus inbound future-supply buckets. Promotion-calendar, calendar,
    and static product-attribute features are intentionally NOT frozen because a
    real forecast legitimately knows them for future dates.
    """
    frozen: list[str] = []
    for col in model_feature_columns:
        if "Lag" in col or "Rolling" in col:
            frozen.append(col)
        elif col.startswith("Inbound") or col.startswith("HasInbound"):
            frozen.append(col)
    return frozen


def origin_snapshot(panel: pd.DataFrame, origin: pd.Timestamp, freeze_cols: list[str]) -> pd.DataFrame:
    """Latest per-SKU value of each freeze column on or before the origin date."""
    history = panel.loc[panel[DATE_COLUMN].le(origin), [SKU_COLUMN, DATE_COLUMN, *freeze_cols]]
    history = history.sort_values(DATE_COLUMN)
    snapshot = history.groupby(SKU_COLUMN, as_index=False).last()
    return snapshot.drop(columns=[DATE_COLUMN])


def apply_frozen_features(holdout: pd.DataFrame, snapshot: pd.DataFrame, freeze_cols: list[str]) -> pd.DataFrame:
    """Overwrite leak-prone holdout columns with origin-snapshot values.

    SKUs with no history before the origin (true cold-start) keep NaN, which the
    model pipeline imputes to zero - the honest "no recent demand known" signal.
    """
    work = holdout.drop(columns=[col for col in freeze_cols if col in holdout.columns])
    work = work.merge(snapshot[[SKU_COLUMN, *freeze_cols]], on=SKU_COLUMN, how="left")
    return work


def seed_demand_buffer(panel: pd.DataFrame, origin: pd.Timestamp) -> dict[Any, dict[pd.Timestamp, float]]:
    """Per-SKU {date: actual SoldUnits} for the days just before the origin."""
    seed_start = origin - pd.Timedelta(days=RECURSIVE_SEED_DAYS)
    window = panel.loc[
        panel[DATE_COLUMN].between(seed_start, origin),
        [SKU_COLUMN, DATE_COLUMN, TARGET_COLUMN],
    ]
    buffer: dict[Any, dict[pd.Timestamp, float]] = {}
    for sku, dt, units in zip(window[SKU_COLUMN], window[DATE_COLUMN], window[TARGET_COLUMN], strict=False):
        buffer.setdefault(sku, {})[pd.Timestamp(dt)] = float(units)
    return buffer


def _buffer_lag(day_map: dict[pd.Timestamp, float], target: pd.Timestamp, offset_days: int) -> float:
    return float(day_map.get(target - pd.Timedelta(days=offset_days), 0.0))


def _buffer_rolling(day_map: dict[pd.Timestamp, float], target: pd.Timestamp, window_days: int) -> float:
    # Mean of the (shift-1) trailing window: dates in [target-window, target-1].
    total = 0.0
    for k in range(1, window_days + 1):
        total += float(day_map.get(target - pd.Timedelta(days=k), 0.0))
    return total / float(window_days)


def recursive_ar_row(day_map: dict[pd.Timestamp, float], target: pd.Timestamp) -> dict[str, float]:
    return {
        "SoldUnitsLag1": _buffer_lag(day_map, target, 1),
        "SoldUnitsLag7": _buffer_lag(day_map, target, 7),
        "SoldUnitsLag14": _buffer_lag(day_map, target, 14),
        "SoldUnitsRolling7": _buffer_rolling(day_map, target, 7),
        "SoldUnitsRolling28": _buffer_rolling(day_map, target, 28),
    }


def train_champion(
    ml: dict[str, Any],
    panel: pd.DataFrame,
    window: dict[str, Any],
    args: argparse.Namespace,
):
    """Train hgb_absolute_log strictly on data before the forecast window."""
    train = panel.loc[panel[DATE_COLUMN].lt(window["start"])].copy()
    if train.empty:
        raise ValueError(f"No training rows before {window['start'].date()} for window {window['label']}.")
    train = sample_training_rows(train, args.max_train_rows, args.random_state)
    x_train, y_train, numeric, categorical, boolean = prepare_xy(
        train,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    model, mode = build_candidate(CHAMPION_MODEL, ml, numeric, categorical, boolean, args)
    model.fit(x_train, y_train)
    feature_columns = [*numeric, *categorical, *boolean]
    return model, mode, feature_columns, len(train)


def predict_frame(model: Any, mode: str, frame: pd.DataFrame, args: argparse.Namespace) -> np.ndarray:
    x, _, _, _, _ = prepare_xy(
        frame,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    return predict_candidate(model, mode, x)


def score_frozen(model, mode, holdout, snapshot, freeze_cols, args) -> np.ndarray:
    frame = apply_frozen_features(holdout, snapshot, freeze_cols)
    return predict_frame(model, mode, frame, args)


def score_leaky(model, mode, holdout, args) -> np.ndarray:
    # Use holdout rows exactly as-is (the existing scoreboard behaviour).
    return predict_frame(model, mode, holdout, args)


def score_recursive(model, mode, holdout, snapshot, freeze_cols, panel, window, args) -> np.ndarray:
    """Day-by-day forecast that feeds the model's own predictions back as lags."""
    base = apply_frozen_features(holdout, snapshot, freeze_cols).copy()
    base["_row_id"] = np.arange(len(base))
    buffer = seed_demand_buffer(panel, window["origin"])

    predictions = np.zeros(len(base), dtype=float)
    forecast_dates = sorted(base[DATE_COLUMN].unique())
    for target in forecast_dates:
        target_ts = pd.Timestamp(target)
        day_rows = base.loc[base[DATE_COLUMN].eq(target_ts)].copy()
        if day_rows.empty:
            continue
        # Rebuild the autoregressive columns for this day from the buffer.
        ar_values = {col: [] for col in RECURSIVE_AR_COLUMNS}
        for sku in day_rows[SKU_COLUMN]:
            row = recursive_ar_row(buffer.get(sku, {}), target_ts)
            for col in RECURSIVE_AR_COLUMNS:
                ar_values[col].append(row[col])
        for col in RECURSIVE_AR_COLUMNS:
            if col in day_rows.columns:
                day_rows[col] = ar_values[col]
        day_pred = predict_frame(model, mode, day_rows, args)
        predictions[day_rows["_row_id"].to_numpy()] = day_pred
        # Extend the buffer with the model's own prediction for the next steps.
        for sku, pred in zip(day_rows[SKU_COLUMN], day_pred, strict=False):
            buffer.setdefault(sku, {})[target_ts] = float(pred)
    return predictions


def coverage_row(df: pd.DataFrame, forecast_col: str) -> dict[str, Any]:
    actual = pd.to_numeric(df[TARGET_COLUMN], errors="coerce").fillna(0.0)
    forecast = pd.to_numeric(df[forecast_col], errors="coerce").fillna(0.0)
    total_sold = float(actual.sum())
    covered_mask = forecast.gt(0)
    covered_sold = float(actual.loc[covered_mask].sum())
    sold_sku_days = int(actual.gt(0).sum())
    covered_sku_days = int((actual.gt(0) & covered_mask).sum())
    return {
        "ForecastName": forecast_col,
        "SoldUnits": total_sold,
        "SoldUnitCoverage": covered_sold / total_sold if total_sold else 0.0,
        "ZeroForecastSoldPct": 1.0 - (covered_sold / total_sold) if total_sold else 0.0,
        "SoldSkuDayCoverage": covered_sku_days / sold_sku_days if sold_sku_days else 0.0,
    }


def evaluate_window(scored: pd.DataFrame, forecast_cols: list[str], window: dict[str, Any]) -> dict[str, pd.DataFrame]:
    scored = scored.copy()
    scored["FDDay"] = (scored[DATE_COLUMN] - window["origin"]).dt.days

    aggregate = evaluate_predictions(scored, forecast_cols)
    aggregate.insert(0, "WindowLabel", window["label"])

    per_day = evaluate_predictions(scored, forecast_cols, ["FDDay"])
    per_day.insert(0, "WindowLabel", window["label"])

    coverage = pd.DataFrame([coverage_row(scored, col) for col in forecast_cols])
    coverage.insert(0, "WindowLabel", window["label"])
    return {"aggregate": aggregate, "per_day": per_day, "coverage": coverage}


def run_window(ml, panel, window, args) -> dict[str, pd.DataFrame]:
    holdout = panel.loc[panel[DATE_COLUMN].between(window["start"], window["end"])].copy()
    if holdout.empty:
        raise ValueError(f"No panel rows in forecast window {window['label']} "
                         f"({window['start'].date()}..{window['end'].date()}).")

    model, mode, feature_columns, train_rows = train_champion(ml, panel, window, args)
    freeze_cols = [col for col in freeze_columns(feature_columns) if col in panel.columns]
    snapshot = origin_snapshot(panel, window["origin"], freeze_cols)

    print(
        f"Window {window['label']}: origin={window['origin'].date()} "
        f"fd={window['start'].date()}..{window['end'].date()} "
        f"train_rows={train_rows:,} holdout_rows={len(holdout):,} frozen_features={len(freeze_cols)}"
    )

    forecast_cols: list[str] = []
    if "leaky" in args.modes:
        holdout["LeakyMLForecastQty"] = score_leaky(model, mode, holdout, args)
        forecast_cols.append("LeakyMLForecastQty")
    if "frozen" in args.modes:
        holdout["FrozenOriginMLForecastQty"] = score_frozen(model, mode, holdout, snapshot, freeze_cols, args)
        forecast_cols.append("FrozenOriginMLForecastQty")
    if "recursive" in args.modes:
        holdout["RecursiveMLForecastQty"] = score_recursive(
            model, mode, holdout, snapshot, freeze_cols, panel, window, args
        )
        forecast_cols.append("RecursiveMLForecastQty")

    forecast_cols.extend([col for col in BASELINE_COLUMNS if col in holdout.columns])
    return evaluate_window(holdout, forecast_cols, window)


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)
    ml = require_sklearn()

    windows = [parse_window(value) for value in (args.windows or DEFAULT_WINDOWS)]
    panel = load_panel(args.panel, args.start_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    aggregates, per_days, coverages = [], [], []
    for window in windows:
        result = run_window(ml, panel, copy(window), args)
        aggregates.append(result["aggregate"])
        per_days.append(result["per_day"])
        coverages.append(result["coverage"])

    aggregate = pd.concat(aggregates, ignore_index=True)
    per_day = pd.concat(per_days, ignore_index=True)
    coverage = pd.concat(coverages, ignore_index=True)

    # Mean WAPE / bias across windows per forecast, for a quick scoreboard.
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

    aggregate.to_csv(args.output_dir / "frozen_origin_window_aggregate.csv", index=False)
    per_day.to_csv(args.output_dir / "frozen_origin_fd_day_detail.csv", index=False)
    coverage.to_csv(args.output_dir / "frozen_origin_coverage.csv", index=False)
    scoreboard.to_csv(args.output_dir / "frozen_origin_scoreboard.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "output_dir": str(args.output_dir),
        "champion_model": CHAMPION_MODEL,
        "modes": args.modes,
        "windows": [
            {"label": w["label"], "origin": str(w["origin"].date()),
             "fd_start": str(w["start"].date()), "fd_end": str(w["end"].date())}
            for w in windows
        ],
        "exclude_corporate_features": args.exclude_corporate_features,
        "include_product_identity_features": args.include_product_identity_features,
        "max_train_rows": args.max_train_rows,
        "max_iter": args.max_iter,
        "learning_rate": args.learning_rate,
        "notes": [
            "frozen/recursive remove the in-window demand-lag leakage so the model is "
            "scored at the same 14-day-ahead horizon as the corporate forecast.",
            "leaky reproduces the existing scoreboard's nowcast behaviour for gap diagnosis.",
            "recursive rebuilds SoldUnits autoregressive features from the model's own "
            "predictions (calendar-day buffer, 0-fill for inactive days); family-level, "
            "inventory, supply and inbound features stay frozen at the origin snapshot.",
        ],
    }
    with (args.output_dir / "frozen_origin_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print("\nScoreboard (mean across windows):")
    print(scoreboard.to_string(index=False))
    print("\nCoverage by window/forecast:")
    print(coverage.to_string(index=False))
    print(f"\nWrote frozen-origin evaluation outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
