"""Build a BRG-like candidate package from the conservative quantile hybrid ML forecast.

This script implements Phase 1 candidate generation:
1. Trains a Quantile regression model (default: 0.35 quantile) on historical data prior to forecast start.
2. Applies demand censoring to the training set (drops stockout rows).
3. Predicts on the SKU universe, applies thresholds and recent fallbacks to generate a hybrid forecast.
4. Optionally scales/blends the hybrid SKU forecasts to match the corporate category totals from the source workbook.
5. Round-trips the candidate workbook through the ingestion pipeline to produce AXForwardDemand CSV.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import ingestion_pipeline as ingestion  # noqa: E402
from forecast_model_compare_sklearn import require_sklearn  # noqa: E402
from forecast_model_train import (  # noqa: E402
    DEFAULT_PANEL_PATH,
    configure_threads,
    load_panel,
)
from forecast_replacement_backtest import load_actuals  # noqa: E402
from forecast_replacement_contract import (  # noqa: E402
    ACTUALS_PATH,
    AX_FORWARD_DEMAND_COLUMNS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_OUTPUT_ROOT,
    FD_COLUMNS,
    PDL_SKU_FEATURES_PATH,
    build_daily_contract,
    build_weekly_contract,
    choose_source,
    file_sha256,
    normalize_optional_date,
    normalize_sku_series,
    run_ingestion_roundtrip,
    write_brg_workbook,
    write_frame_with_sample,
)
from forecast_replacement_ml_backtest import (  # noqa: E402
    build_future_rows,
    load_daily_promotions,
    load_pdl_features,
    train_window,
)
from forecast_replacement_hybrid_candidate import (  # noqa: E402
    source_universe,
    source_snapshot_attributes,
    recent_daily_forecast,
    selected_ml_daily,
    combine_daily_forecasts,
    cap_14day_to_recent,
    build_weekly_from_daily,
    build_signal_summary,
)
from forecast_replacement_ml_quantile_backtest import run_quantile_stage  # noqa: E402

DEFAULT_CANDIDATE_TYPE = "quantile_hybrid_ml_baseline"
DEFAULT_MODEL = "hgb_quantile_log"
DEFAULT_THRESHOLD = 20.0
DEFAULT_RECENT_FALLBACK_WEIGHT = 0.10
DEFAULT_WEEKLY_TAIL_SCALE = 0.50
DEFAULT_OUTPUT_DIR_NAME = "replacement_contract_quantile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a quantile hybrid ML BRG-like candidate package.")
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--candidate-id")
    parser.add_argument("--forecast-start-date")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--actuals-path", type=Path, default=ACTUALS_PATH)
    parser.add_argument("--pdl-sku-features-path", type=Path, default=PDL_SKU_FEATURES_PATH)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--quantile", type=float, default=0.35, help="Quantile regression target (e.g. 0.35).")
    parser.add_argument(
        "--disable-censoring",
        action="store_true",
        help="Disable demand censoring of stockout periods during training.",
    )
    parser.add_argument(
        "--disable-blending",
        action="store_true",
        help="Disable category-level corporate forecast blending.",
    )
    parser.add_argument("--ml-threshold-units", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--recent-fallback-weight", type=float, default=DEFAULT_RECENT_FALLBACK_WEIGHT)
    parser.add_argument(
        "--recent-volume-cap",
        type=float,
        help="Optional cap for FD1-FD14 total units as a multiple of recent no-ML forecast units.",
    )
    parser.add_argument("--weekly-tail-scale", type=float, default=DEFAULT_WEEKLY_TAIL_SCALE)
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
        default="category-velocity-promo",
    )
    parser.add_argument("--calibration-min-rows", type=int, default=500)
    parser.add_argument("--calibration-min-actual-units", type=float, default=50.0)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--exclude-corporate-features", action="store_true", default=True)
    parser.add_argument("--include-product-identity-features", action="store_true", default=True)
    parser.add_argument("--sample-rows", type=int, default=1000)
    return parser.parse_args()


def blend_df_14day_with_corporate(
    df_14day: pd.DataFrame,
    source_14day: pd.DataFrame,
    df_hier: pd.DataFrame,
    group_cols: list[str] = ["Division", "Department", "Class"],
) -> pd.DataFrame:
    """Scale SKU 14-day daily forecasts to match the corporate category totals from the workbook."""
    if df_14day.empty or source_14day.empty:
        return df_14day

    # 1. Sum corporate daily forecast columns to SKU total
    source_cols = [col for col in source_14day.columns if col.startswith("FD")]
    corp_sku = source_14day[["SKU"]].copy()
    corp_sku["SKU"] = normalize_sku_series(corp_sku["SKU"])
    corp_sku["CorpUnits"] = source_14day[source_cols].sum(axis=1)
    corp_sku = corp_sku.groupby("SKU", as_index=False).agg(CorpUnits=("CorpUnits", "sum"))

    hier = df_hier.copy()
    hier["SKU"] = normalize_sku_series(hier["SKU"])
    corp_sku = corp_sku.merge(hier, on="SKU", how="left")

    # Aggregate corporate category units
    for col in group_cols:
        if col not in corp_sku.columns:
            corp_sku[col] = ""
    corp_cat = corp_sku.groupby(group_cols, as_index=False, dropna=False).agg(CorpCatUnits=("CorpUnits", "sum"))

    # 2. Sum model SKU daily forecasts
    model_sku = df_14day[["SKU"]].copy()
    model_sku["ModelUnits"] = df_14day[FD_COLUMNS].sum(axis=1)
    model_sku = model_sku.merge(hier, on="SKU", how="left")

    # Aggregate model category units
    for col in group_cols:
        if col not in model_sku.columns:
            model_sku[col] = ""
    model_cat = model_sku.groupby(group_cols, as_index=False, dropna=False).agg(ModelCatUnits=("ModelUnits", "sum"))

    # 3. Merge aggregates back to df_14day
    df_blended = df_14day.merge(hier[["SKU", *group_cols]], on="SKU", how="left")
    for col in group_cols:
        df_blended[col] = df_blended[col].fillna("")
    
    df_blended = df_blended.merge(corp_cat, on=group_cols, how="left")
    df_blended = df_blended.merge(model_cat, on=group_cols, how="left")

    df_blended["CorpCatUnits"] = df_blended["CorpCatUnits"].fillna(0.0)
    df_blended["ModelCatUnits"] = df_blended["ModelCatUnits"].fillna(0.0)

    # Scale factor
    can_scale = df_blended["CorpCatUnits"].gt(0) & df_blended["ModelCatUnits"].gt(0)
    df_blended["ScaleFactor"] = 1.0
    df_blended.loc[can_scale, "ScaleFactor"] = df_blended.loc[can_scale, "CorpCatUnits"] / df_blended.loc[can_scale, "ModelCatUnits"]

    # Proportions scale daily columns
    for col in FD_COLUMNS:
        df_blended[col] = (df_blended[col] * df_blended["ScaleFactor"]).round().astype(int)

    return df_blended[["SKU", *FD_COLUMNS]].copy()


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, str(args.threads))

    source_file = choose_source(args.source_file)
    forecast_start = normalize_optional_date(args.forecast_start_date)
    candidate_id = args.candidate_id or f"{DEFAULT_CANDIDATE_TYPE}_{datetime.now():%Y-%m-%d_%H%M%S}"
    candidate_dir = args.output_root / candidate_id
    if candidate_dir.exists():
        raise FileExistsError(f"Candidate output already exists: {candidate_dir}")
    input_dir = candidate_dir / "input"
    contract_dir = candidate_dir / "contract"
    input_dir.mkdir(parents=True)
    contract_dir.mkdir(parents=True)
    workbook_path = input_dir / f"{source_file.stem}__{candidate_id}.xlsx"

    print(f"Source workbook: {source_file}", flush=True)
    print(f"Forecast start: {forecast_start.date()}", flush=True)
    df_hier, df_status = ingestion.read_product_attributes(source_file)
    df_load = ingestion.read_load_data(source_file)
    df_on_hand_location = ingestion.read_on_hand_location_block(source_file)
    source_weekly, _, _ = ingestion.read_weekly_forecast(source_file)
    source_14day, _ = ingestion.read_14day_forecast(source_file)
    universe = source_universe(source_weekly, source_14day, df_hier)
    attrs = source_snapshot_attributes(df_hier, universe)

    print("Loading model panel and future-safe inputs...", flush=True)
    panel = load_panel(args.panel, args.start_date)
    actuals = load_actuals(args.actuals_path)
    horizon_end = forecast_start + pd.Timedelta(days=13)
    daily_promo = load_daily_promotions(
        args.pdl_sku_features_path.parent / "combined_daily_promo_features.parquet", 
        forecast_start, 
        horizon_end
    )
    pdl_horizon = load_pdl_features(args.pdl_sku_features_path, forecast_start, horizon_end)
    train, calibration = train_window(panel, args, forecast_start)

    # Demand Censoring (Phase 1)
    if not args.disable_censoring:
        if "InventoryAvailPhysicalLag1" in train.columns:
            is_stockout = train["InventoryAvailPhysicalLag1"].eq(0.0) & train["InventoryAvailPhysicalLag1"].notna()
            print(f"Censoring: dropping {is_stockout.sum():,} training rows due to stockouts (InventoryAvailPhysicalLag1 == 0).", flush=True)
            train = train.loc[~is_stockout].copy()
        elif "HasAvailableInventoryLag1" in train.columns:
            is_stockout = train["HasAvailableInventoryLag1"].eq(False) & train["HasAvailableInventoryLag1"].notna()
            print(f"Censoring: dropping {is_stockout.sum():,} training rows due to stockouts (HasAvailableInventoryLag1 == False).", flush=True)
            train = train.loc[~is_stockout].copy()

    future, future_meta = build_future_rows(
        panel=panel,
        actuals=actuals,
        pdl_horizon=pdl_horizon,
        daily_promo=daily_promo,
        snapshot_attrs=attrs,
        snapshot_id="current_source_workbook",
        start=forecast_start,
        lookback_days=args.lookback_days,
    )

    print("Training and scoring Quantile ML forecast...", flush=True)
    ml = require_sklearn()
    
    # Force quantile options
    args.model = "ml_quantile"
    scored, calibration_factors = run_quantile_stage(ml, train, calibration, future, args)
    
    raw_col = "ml_quantileForecastQty"
    ml_daily, ml_totals = selected_ml_daily(scored, raw_col, args.ml_threshold_units)
    recent_daily, recent_meta = recent_daily_forecast(actuals, forecast_start, args.lookback_days)
    df_14day = combine_daily_forecasts(
        ml_daily,
        recent_daily,
        forecast_start,
        args.recent_fallback_weight,
    )
    df_14day = cap_14day_to_recent(
        df_14day,
        recent_daily,
        forecast_start,
        args.recent_volume_cap,
    )

    # Corporate Forecast Blending (Phase 1)
    if not args.disable_blending:
        print("Blending: scaling SKU daily forecasts to corporate category totals...", flush=True)
        df_14day = blend_df_14day_with_corporate(df_14day, source_14day, df_hier)

    df_weekly, week_dates, weekly_meta = build_weekly_from_daily(
        df_14day,
        recent_daily,
        forecast_start,
        args.weekly_tail_scale,
    )
    signal = build_signal_summary(
        df_14day,
        ml_totals,
        recent_daily,
        args.ml_threshold_units,
        args.recent_fallback_weight,
    )

    write_brg_workbook(
        workbook_path,
        df_weekly,
        week_dates,
        df_14day,
        forecast_start,
        df_hier,
        df_status,
        df_load,
        df_on_hand_location,
    )

    daily_contract = build_daily_contract(df_14day, forecast_start, candidate_id, workbook_path.name)
    weekly_contract = build_weekly_contract(df_weekly, week_dates, week_dates, candidate_id, workbook_path.name)
    outputs = {
        "daily_forecast": write_frame_with_sample(daily_contract, contract_dir / "daily_forecast.parquet", args.sample_rows),
        "weekly_forecast": write_frame_with_sample(weekly_contract, contract_dir / "weekly_forecast.parquet", args.sample_rows),
        "product_hierarchy": write_frame_with_sample(df_hier, contract_dir / "product_hierarchy.parquet", args.sample_rows),
        "product_status": write_frame_with_sample(df_status, contract_dir / "product_status.parquet", args.sample_rows),
        "signal_sku_summary": write_frame_with_sample(signal, contract_dir / "signal_sku_summary.parquet", args.sample_rows),
    }
    
    method_meta = {
        "method": DEFAULT_CANDIDATE_TYPE,
        "forecast_start_date": str(forecast_start.date()),
        "quantile": args.quantile,
        "demand_censoring": not args.disable_censoring,
        "corporate_blending": not args.disable_blending,
        "ml_threshold_units": args.ml_threshold_units,
        "recent_fallback_weight": args.recent_fallback_weight,
        "recent_volume_cap": args.recent_volume_cap,
        "weekly_tail_scale": args.weekly_tail_scale,
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "future_rows": int(len(future)),
        "forecast_totals": {
            "fd1_to_fd14_units": float(df_14day[FD_COLUMNS].sum().sum()),
            "first_13_week_units": float(df_weekly[week_dates[:13]].sum().sum()),
            "selected_ml_skus": int(signal["SelectedByMLThreshold"].sum()),
            "forecast_skus": int(df_14day["SKU"].nunique()),
        },
    }
    contract = {
        "candidate_id": candidate_id,
        "candidate_type": DEFAULT_CANDIDATE_TYPE,
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_workbook": str(source_file),
        "source_workbook_name": source_file.name,
        "source_workbook_sha256": file_sha256(source_file),
        "clone_workbook": str(workbook_path),
        "clone_workbook_sha256": file_sha256(workbook_path),
        "forecast_start_date": str(forecast_start.date()),
        "ax_forward_demand_columns": AX_FORWARD_DEMAND_COLUMNS,
        "hybrid_method": method_meta,
        "outputs": outputs,
    }
    (contract_dir / "candidate_contract.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")

    print("Running ingestion round-trip...", flush=True)
    roundtrip = run_ingestion_roundtrip(candidate_dir, workbook_path)
    summary = {
        "candidate_id": candidate_id,
        "candidate_type": DEFAULT_CANDIDATE_TYPE,
        "candidate_dir": str(candidate_dir),
        "contract": str(contract_dir / "candidate_contract.json"),
        "roundtrip": roundtrip,
    }
    (candidate_dir / "roundtrip_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    pd.DataFrame([roundtrip]).to_csv(candidate_dir / "roundtrip_summary.csv", index=False)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
