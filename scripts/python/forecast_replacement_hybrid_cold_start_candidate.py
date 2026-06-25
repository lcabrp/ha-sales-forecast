"""Build a BRG-like candidate package from the cold-start quantile hybrid ML forecast using DB attributes.

This script implements Phase 1 candidate generation:
1. Loads product attributes from the Forecast DB snapshot.
2. Coalesces original_start_date and start_date to form a GoLiveDate.
3. Enriches the model panel and future dataframes with these attributes.
4. Trains a Quantile regression model with these attributes.
5. Predicts on the SKU universe, applies thresholds and recent fallbacks to generate a hybrid forecast.
6. Optionally scales/blends SKU forecasts to match corporate category totals.
7. Round-trips the candidate workbook through the ingestion pipeline to produce AXForwardDemand CSV.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))


import forecast_model_train as fmt  # noqa: E402
import forecast_replacement_ml_backtest as frmb  # noqa: E402
import forecast_replacement_ml_quantile_backtest as frmqb  # noqa: E402
import ingestion_pipeline as ingestion  # noqa: E402
from forecast_model_compare_sklearn import require_sklearn  # noqa: E402
from forecast_model_panel import PROMO_DAILY_PATH  # noqa: E402
from forecast_model_train import (  # noqa: E402
    DEFAULT_PANEL_PATH,
    configure_threads,
    load_panel,
)
from forecast_replacement_backtest import load_actuals  # noqa: E402
from forecast_replacement_contract import (  # noqa: E402
    ACTUALS_PATH,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_OUTPUT_ROOT,
    FD_COLUMNS,
    PDL_SKU_FEATURES_PATH,
    choose_source,
    normalize_optional_date,
    normalize_sku_series,
    run_ingestion_roundtrip,
    write_brg_workbook,
)
from forecast_replacement_ml_backtest import (  # noqa: E402
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
from forecast_replacement_ml_cold_start import (  # noqa: E402
    DEFAULT_SNAPSHOT_DIR,
    load_forecast_db_attributes,
    build_future_rows_cold_start,
)

# Set global db_attrs reference for build_future_rows_cold_start override
import forecast_replacement_ml_cold_start as cold_start_mod  # noqa: E402

DEFAULT_CANDIDATE_TYPE = "cold_start_hybrid_ml"
DEFAULT_MODEL = "hgb_cold_start_quantile"
DEFAULT_THRESHOLD = 20.0
DEFAULT_RECENT_FALLBACK_WEIGHT = 0.10
DEFAULT_WEEKLY_TAIL_SCALE = 0.50
DEFAULT_OUTPUT_DIR_NAME = "replacement_contract_cold_start"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a cold-start quantile hybrid ML candidate package.")
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
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


# Override build_future_rows in-place in candidate builder dependencies
frmb.build_future_rows = build_future_rows_cold_start
frmqb.build_future_rows = build_future_rows_cold_start


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

    # Scale SKU prediction:
    can_scale = df_blended["CorpCatUnits"].gt(0) & df_blended["ModelCatUnits"].gt(0)
    df_blended["ScaleFactor"] = 1.0
    df_blended.loc[can_scale, "ScaleFactor"] = df_blended.loc[can_scale, "CorpCatUnits"] / df_blended.loc[can_scale, "ModelCatUnits"]

    for col in FD_COLUMNS:
        df_blended[col] = (df_blended[col] * df_blended["ScaleFactor"]).round().clip(lower=0)
    
    return df_blended.drop(columns=[*group_cols, "CorpCatUnits", "ModelCatUnits", "ScaleFactor"]).copy()


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)

    # 1. Load Forecast DB attributes
    cold_start_mod.db_attrs = load_forecast_db_attributes(args.snapshot_dir)

    # Register categorical features on the fly
    fmt.CATEGORICAL_FEATURES = list(fmt.CATEGORICAL_FEATURES) + [
        "gender",
        "material_group",
        "fabric_group",
        "season",
        "theme",
        "collection",
        "NewnessBucket",
    ]

    source_path = choose_source(args.source_file)
    print(f"Reading source workbook: {source_path.name}")
    df_hier, df_status = ingestion.read_product_attributes(source_path)
    df_load = ingestion.read_load_data(source_path)
    df_on_hand_location = ingestion.read_on_hand_location_block(source_path)
    source_weekly, _, _ = ingestion.read_weekly_forecast(source_path)
    source_14day, source_header = ingestion.read_14day_forecast(source_path)

    # Determine window dates
    forecast_start = pd.Timestamp(
        normalize_optional_date(args.forecast_start_date)
        or source_header.get("ForecastStartDate")
    ).normalize()

    universe = source_universe(source_weekly, source_14day, df_hier)
    attrs = source_snapshot_attributes(df_hier, universe)

    # Load panel
    print("Loading model panel...", flush=True)
    panel = load_panel(args.panel, args.start_date)

    # Pre-warm promotions and actuals
    pdl_horizon = load_pdl_features(
        args.pdl_sku_features_path, forecast_start, forecast_start + pd.Timedelta(days=13)
    )
    daily_promo = load_daily_promotions(
        PROMO_DAILY_PATH, forecast_start, forecast_start + pd.Timedelta(days=13)
    )
    actuals = load_actuals(args.actuals_path)

    print("Splitting panel for training...", flush=True)
    train, calibration = train_window(panel, args, forecast_start)
    from forecast_replacement_ml_cold_start import enrich_with_attributes
    train = enrich_with_attributes(train, cold_start_mod.db_attrs)
    calibration = enrich_with_attributes(calibration, cold_start_mod.db_attrs)

    # Censoring
    if not args.disable_censoring:
        if "InventoryAvailPhysicalLag1" in train.columns:
            if train["InventoryAvailPhysicalLag1"].gt(0.0).any():
                is_stockout = train["InventoryAvailPhysicalLag1"].eq(0.0) & train["InventoryAvailPhysicalLag1"].notna()
                print(f"Censoring: dropping {is_stockout.sum():,} training rows due to stockouts (InventoryAvailPhysicalLag1 == 0).", flush=True)
                train = train.loc[~is_stockout].copy()
            else:
                print("Censoring: skipped (no positive InventoryAvailPhysicalLag1 in training split).", flush=True)
        elif "HasAvailableInventoryLag1" in train.columns:
            if train["HasAvailableInventoryLag1"].any():
                is_stockout = train["HasAvailableInventoryLag1"].eq(False) & train["HasAvailableInventoryLag1"].notna()
                print(f"Censoring: dropping {is_stockout.sum():,} training rows due to stockouts (HasAvailableInventoryLag1 == False).", flush=True)
                train = train.loc[~is_stockout].copy()
            else:
                print("Censoring: skipped (no True HasAvailableInventoryLag1 in training split).", flush=True)

    future, future_meta = build_future_rows_cold_start(
        panel=panel,
        actuals=actuals,
        pdl_horizon=pdl_horizon,
        daily_promo=daily_promo,
        snapshot_attrs=attrs,
        snapshot_id="current_source_workbook",
        start=forecast_start,
        lookback_days=args.lookback_days,
    )

    print("Training and scoring Cold-Start Quantile ML forecast...", flush=True)
    ml = require_sklearn()
    
    args.model = "ml_quantile"
    scored, calibration_factors = run_quantile_stage(ml, train, calibration, future, args)
    
    raw_col = "ml_quantileForecastQty"
    ml_daily, ml_totals = selected_ml_daily(scored, raw_col, args.ml_threshold_units)
    recent_daily, recent_meta = recent_daily_forecast(actuals, forecast_start, args.lookback_days)
    
    ml_daily["ForecastDate"] = pd.to_datetime(ml_daily["ForecastDate"])
    recent_daily["ForecastDate"] = pd.to_datetime(recent_daily["ForecastDate"])
    
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

    # Blending
    if not args.disable_blending:
        print("Blending: scaling SKU daily forecasts to corporate category totals...", flush=True)
        df_14day = blend_df_14day_with_corporate(df_14day, source_14day, df_hier)

    df_weekly, week_dates, weekly_meta = build_weekly_from_daily(
        df_14day,
        recent_daily,
        forecast_start,
        args.weekly_tail_scale,
    )
    build_signal_summary(
        df_14day,
        ml_totals,
        recent_daily,
        args.ml_threshold_units,
        args.recent_fallback_weight,
    )

    # Setup directories
    candidate_id = args.candidate_id or f"cold_start_hybrid_cap_{str(args.recent_volume_cap or 1.0).replace('.', 'p')}"
    candidate_dir = args.output_root / DEFAULT_OUTPUT_DIR_NAME / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = candidate_dir / f"Product Info for BRG_{forecast_start.date().isoformat()}_{candidate_id}.xlsx"

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
    print(f"Wrote candidate workbook to {workbook_path.name}")

    # Ingestion roundtrip to produce AXForwardDemand CSV
    run_ingestion_roundtrip(candidate_dir, workbook_path)
    print(f"Successfully generated AXForwardDemand CSV under {candidate_dir / 'ingestion_output'}")

    # Write summary metadata
    meta = {
        "candidate_id": candidate_id,
        "forecast_start_date": forecast_start.date().isoformat(),
        "recent_volume_cap": args.recent_volume_cap,
        "quantile": args.quantile,
        "features": fmt.CATEGORICAL_FEATURES,
    }
    with (candidate_dir / "candidate_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
