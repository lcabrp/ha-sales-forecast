"""Score corporate_total_model_shape on the same 26-window handoff contract.

Trains only hgb_absolute_log (raw, min 20 SKU units), then:
  - independent_total_model_shape: ML total + ML shape
  - corporate_total_model_shape: corporate total × ML SKU shares
Also re-scores corporate_raw, corporate_total_recent_shape, and recent baseline.

No AX. Default threads=8 (half of 16 logical CPUs).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_compare_sklearn import require_sklearn, run_single_stage  # noqa: E402
from forecast_model_train import load_panel  # noqa: E402
from forecast_replacement_backtest import (  # noqa: E402
    ACTUALS_PATH,
    FORECAST_DAY_PATH,
    FORECAST_SNAPSHOT_PATH,
    PDL_SKU_FEATURES_PATH,
    SNAPSHOT_SUMMARY_PATH,
    actual_window,
    choose_windows,
    corporate_forecast,
    load_actuals,
    load_promo_for_window,
    no_ml_forecast,
    normalize_sku_series,
    score_forecast,
    summarize_by_candidate,
)
from forecast_replacement_contract import DEFAULT_LOOKBACK_DAYS  # noqa: E402
from forecast_replacement_ml_backtest import (  # noqa: E402
    DEFAULT_PANEL_PATH,
    PROMO_DAILY_PATH,
    aggregate_forecast,
    build_future_rows,
    configure_threads,
    load_daily_promotions,
    load_pdl_features,
    load_snapshot_attributes,
    train_window,
)

OUT_DIR = (
    ROOT
    / "Output"
    / "ForecastAccuracy"
    / "handoff_eval"
    / "volume_vs_allocation_model_shape_2026-07-11"
)


def allocate_total_by_shape(shape: pd.DataFrame, total_units: float) -> pd.DataFrame:
    frame = shape.copy()
    frame["ForecastUnits"] = (
        pd.to_numeric(frame["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
    )
    frame = frame.loc[frame["ForecastUnits"].gt(0), ["SKU", "ForecastUnits"]]
    shape_total = float(frame["ForecastUnits"].sum())
    total_units = float(total_units)
    if frame.empty or shape_total <= 0 or total_units <= 0:
        return frame.iloc[0:0].copy()
    frame["ForecastUnits"] = (
        frame["ForecastUnits"] / shape_total * total_units
    ).round().clip(lower=0)
    return frame.loc[frame["ForecastUnits"].gt(0)].copy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--max-windows", type=int, default=26)
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--panel-start-date", default="2025-01-01")
    parser.add_argument("--output-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--sku-min-units", type=float, default=20.0)
    parser.add_argument("--max-train-rows", type=int, default=500_000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(args.threads)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    windows = choose_windows(SNAPSHOT_SUMMARY_PATH, "2025-01-01", None, args.max_windows)
    if windows.empty:
        raise RuntimeError("No complete historical forecast windows found.")

    print(
        f"Windows: {len(windows)} "
        f"{windows['ForecastStartDate'].min().date()} .. "
        f"{windows['ForecastStartDate'].max().date()} | threads={args.threads}",
        flush=True,
    )

    horizon_start = pd.Timestamp(windows["ForecastStartDate"].min()).normalize()
    horizon_end = pd.Timestamp(windows["ForecastEndDate"].max()).normalize()
    snapshot_ids = windows["SnapshotId"].dropna().astype(str).tolist()

    print("Loading panel / actuals / promo tables...", flush=True)
    panel = load_panel(args.panel, args.panel_start_date)
    build_future_rows.include_seasonal_features = False
    actuals = load_actuals(ACTUALS_PATH)
    forecast_day = pd.read_parquet(
        FORECAST_DAY_PATH,
        columns=["SnapshotId", "SKU", "ForecastDate", "ForecastQty"],
    )
    forecast_day["ForecastQty"] = pd.to_numeric(forecast_day["ForecastQty"], errors="coerce").fillna(0)
    forecast_day["SKU"] = normalize_sku_series(forecast_day["SKU"])
    snapshot_attrs = load_snapshot_attributes(FORECAST_SNAPSHOT_PATH, snapshot_ids)
    daily_promo = load_daily_promotions(PROMO_DAILY_PATH, horizon_start, horizon_end)
    pdl_horizon = load_pdl_features(PDL_SKU_FEATURES_PATH, horizon_start, horizon_end)
    promo = load_promo_for_window(PDL_SKU_FEATURES_PATH, horizon_start, horizon_end)
    ml = require_sklearn()

    ml_args = SimpleNamespace(
        max_train_rows=args.max_train_rows,
        random_state=42,
        max_iter=180,
        learning_rate=0.06,
        max_leaf_nodes=31,
        calibration_days=28,
        calibration_mode="category",
        calibration_group_cols=["Division", "Department", "Class"],
        calibration_min_rows=500,
        calibration_min_actual_units=50.0,
        exclude_corporate_features=True,
        include_product_identity_features=True,
        lookback_days=DEFAULT_LOOKBACK_DAYS,
        include_seasonal_features=False,
        seasonal_years=3,
        seasonal_window_days=7,
        threads=args.threads,
    )

    score_rows: list[dict] = []
    for i, (_, snapshot) in enumerate(windows.iterrows(), start=1):
        start = pd.Timestamp(snapshot["ForecastStartDate"]).normalize()
        print(f"\n[{i}/{len(windows)}] {start.date()}", flush=True)

        train, calibration = train_window(panel, ml_args, start)
        future, future_meta = build_future_rows(
            panel=panel,
            actuals=actuals,
            pdl_horizon=pdl_horizon,
            daily_promo=daily_promo,
            snapshot_attrs=snapshot_attrs,
            snapshot_id=str(snapshot["SnapshotId"]),
            start=start,
            lookback_days=DEFAULT_LOOKBACK_DAYS,
        )
        actual = actual_window(actuals, start)
        source_universe = snapshot_attrs.loc[
            snapshot_attrs["SnapshotId"].eq(str(snapshot["SnapshotId"])),
            ["SKU"],
        ].drop_duplicates()

        corp = corporate_forecast(forecast_day, str(snapshot["SnapshotId"]))
        corp_total = float(pd.to_numeric(corp["ForecastUnits"], errors="coerce").fillna(0).sum())
        recent, _ = no_ml_forecast(
            actuals=actuals,
            promo=promo,
            source_universe=source_universe,
            start=start,
            lookback_days=DEFAULT_LOOKBACK_DAYS,
            include_seasonal=False,
            include_promo_floor=False,
            seasonal_years=3,
            seasonal_window_days=7,
            seasonal_recent_weight=0.65,
        )
        corp_total_recent = allocate_total_by_shape(recent, corp_total)

        print(f"  training hgb_absolute_log on {len(train):,} rows...", flush=True)
        scored, _factors = run_single_stage(
            "hgb_absolute_log", ml, train, calibration, future, ml_args
        )
        ml_forecast = aggregate_forecast(
            scored, "hgb_absolute_logForecastQty", args.sku_min_units
        )
        corp_total_model = allocate_total_by_shape(ml_forecast, corp_total)

        for name, forecast in [
            ("corporate_raw", corp),
            ("corporate_total_recent_shape", corp_total_recent),
            ("corporate_total_model_shape", corp_total_model),
            ("independent_total_model_shape", ml_forecast),
            ("recent_no_ml_no_promo_floor", recent),
        ]:
            score_rows.append(score_forecast(forecast, actual, name, snapshot))

        print(
            f"  corp={corp_total:,.0f} "
            f"ml={ml_forecast['ForecastUnits'].sum():,.0f} "
            f"corp_model={corp_total_model['ForecastUnits'].sum():,.0f} "
            f"future_rows={future_meta.get('future_rows', len(future))}",
            flush=True,
        )

        # Checkpoint after each window so a long run is recoverable.
        pd.DataFrame(score_rows).to_csv(
            args.output_dir / "handoff_candidate_window_scores.partial.csv",
            index=False,
        )

    scores = pd.DataFrame(score_rows)
    summary = summarize_by_candidate(scores)
    order = [
        "corporate_raw",
        "corporate_total_recent_shape",
        "corporate_total_model_shape",
        "independent_total_model_shape",
        "recent_no_ml_no_promo_floor",
    ]
    summary["_ord"] = summary["Candidate"].map({c: i for i, c in enumerate(order)})
    summary = summary.sort_values(["_ord", "WAPE"], kind="mergesort").drop(columns="_ord")

    scores.to_csv(args.output_dir / "handoff_candidate_window_scores.csv", index=False)
    summary.to_csv(args.output_dir / "handoff_candidate_summary.csv", index=False)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "windows": int(len(windows)),
        "window_start_min": str(windows["ForecastStartDate"].min().date()),
        "window_start_max": str(windows["ForecastStartDate"].max().date()),
        "threads": args.threads,
        "model": "hgb_absolute_log",
        "sku_min_units": args.sku_min_units,
        "lookback_days": DEFAULT_LOOKBACK_DAYS,
        "candidates": order,
        "notes": [
            "corporate_total_* candidates hold corporate 14-day total fixed and reallocate by shape.",
            "Corporate total is an informed soft reference, not a hard production constraint.",
            "independent_total_model_shape and recent_no_ml_no_promo_floor are free-total diagnostics.",
        ],
    }
    (args.output_dir / "handoff_candidate_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print("\nSame-contract handoff summary:")
    print(summary.to_string(index=False))
    print(f"\nWrote: {args.output_dir}")


if __name__ == "__main__":
    main()
