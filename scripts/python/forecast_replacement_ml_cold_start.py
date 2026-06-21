"""Backtest the Cold-Start SKU/day forecast model using Forecast DB product attributes.

This script extends the Quantile ML backtest by:
1. Loading product dimensions/attributes from the Forecast DB snapshot.
2. Coalescing original_start_date and start_date to form a launch date (GoLiveDate).
3. Adding gender, material_group, fabric_group, season, theme, collection, and NewnessBucket as categorical features.
4. Backtesting model accuracy across the 26 historical windows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402

import forecast_model_train as fmt
import forecast_replacement_ml_backtest as frmb
import forecast_replacement_ml_quantile_backtest as frmqb
from forecast_model_panel import (
    PDL_SKU_FEATURES_PATH,
    PROMO_DAILY_PATH,
)
from forecast_model_train import (
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    TARGET_COLUMN,
    apply_calibration,
    configure_threads,
    load_panel,
    prepare_xy,
    require_sklearn,
)
from forecast_replacement_backtest import (
    ACTUALS_PATH,
    FORECAST_DAY_PATH,
    FORECAST_SNAPSHOT_PATH,
    SNAPSHOT_SUMMARY_PATH,
    actual_window,
    choose_windows,
    load_actuals,
    load_promo_for_window,
    no_ml_forecast,
    score_forecast,
    summarize_by_candidate,
)
from forecast_replacement_ml_backtest import (
    aggregate_forecast,
    combine_with_recent_fallback,
    load_daily_promotions,
    load_pdl_features,
    load_snapshot_attributes,
    threshold_label,
)

DEFAULT_SNAPSHOT_DIR = (
    PROJECT_ROOT
    / "Output"
    / "ForecastAccuracy"
    / "corporate_forecast"
    / "snapshots"
    / "20260617_173252"
)
DEFAULT_OUTPUT_DIR = MODEL_DIR.parent / "replacement_ml_quantile_backtests" / "cold_start"

# Global placeholder for loaded product attributes
db_attrs: pd.DataFrame = pd.DataFrame()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest cold-start ML forecast model with DB attributes."
    )
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--snapshot-summary-path", type=Path, default=SNAPSHOT_SUMMARY_PATH)
    parser.add_argument("--forecast-snapshot-path", type=Path, default=FORECAST_SNAPSHOT_PATH)
    parser.add_argument("--forecast-day-path", type=Path, default=FORECAST_DAY_PATH)
    parser.add_argument("--actuals-path", type=Path, default=ACTUALS_PATH)
    parser.add_argument("--pdl-sku-features-path", type=Path, default=PDL_SKU_FEATURES_PATH)
    parser.add_argument("--promo-daily-path", type=Path, default=PROMO_DAILY_PATH)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date")
    parser.add_argument("--panel-start-date")
    parser.add_argument("--max-windows", type=int, default=26)
    parser.add_argument("--lookback-days", type=int, default=56)
    parser.add_argument(
        "--sku-total-thresholds",
        nargs="+",
        type=float,
        default=[0.0, 1.0, 5.0, 10.0, 20.0],
        help="Score static minimum 14-day SKU forecast thresholds.",
    )
    parser.add_argument(
        "--hybrid-recent-fallback-weights",
        nargs="+",
        type=float,
        default=[0.0, 0.05, 0.10],
        help="Optional fallback weights for recent no-ML demand on SKUs below each ML threshold.",
    )
    parser.add_argument(
        "--hybrid-recent-volume-caps",
        nargs="+",
        type=float,
        default=[0.85, 1.00],
        help="Optional total-volume caps for hybrid forecasts.",
    )
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
    parser.add_argument("--max-train-rows", type=int, default=500000)
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
    parser.add_argument("--exclude-corporate-features", action="store_true", default=True)
    parser.add_argument("--include-product-identity-features", action="store_true", default=True)
    parser.add_argument(
        "--include-seasonal-features",
        action="store_true",
        default=True,
    )
    parser.add_argument("--seasonal-years", type=int, default=3)
    parser.add_argument("--seasonal-window-days", type=int, default=7)
    return parser.parse_args()


def load_forecast_db_attributes(snapshot_dir: Path) -> pd.DataFrame:
    path = snapshot_dir / "tables" / "dbo__Product_Dimensions_Hierarchy_Attributes"
    if not path.exists():
        raise FileNotFoundError(f"Forecast DB product attributes table not found: {path}")
    print(f"Loading Forecast DB product attributes from {path.name}...")
    df = pd.read_parquet(
        path,
        columns=[
            "sku",
            "gender",
            "material_group",
            "fabric_group",
            "season",
            "theme",
            "collection",
            "original_start_date",
            "start_date",
        ],
    )
    df = df.rename(columns={"sku": "SKU"})
    # Normalize dates
    df["original_start_date"] = pd.to_datetime(
        df["original_start_date"], errors="coerce"
    ).dt.normalize()
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce").dt.normalize()
    # Coalesce start dates to GoLiveDate
    df["GoLiveDate"] = df["original_start_date"].combine_first(df["start_date"])
    df = df.drop_duplicates("SKU", keep="last")
    return df


def classify_newness(days_since_go_live: float | None) -> str:
    if days_since_go_live is None or pd.isna(days_since_go_live):
        return "UnknownGoLive"
    if days_since_go_live < 0:
        return "PreGoLive"
    if days_since_go_live <= 30:
        return "New0To30"
    if days_since_go_live <= 90:
        return "New31To90"
    return "Established"


def enrich_with_attributes(df: pd.DataFrame, db_attrs: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    df = df.merge(
        db_attrs[
            [
                "SKU",
                "gender",
                "material_group",
                "fabric_group",
                "season",
                "theme",
                "collection",
                "GoLiveDate",
            ]
        ],
        on="SKU",
        how="left",
    )
    df["DaysSinceGoLive"] = (df[DATE_COLUMN] - df["GoLiveDate"]).dt.days
    df["NewnessBucket"] = df["DaysSinceGoLive"].map(classify_newness)
    for col in [
        "gender",
        "material_group",
        "fabric_group",
        "season",
        "theme",
        "collection",
        "NewnessBucket",
    ]:
        df[col] = df[col].fillna("").astype(str)
    return df


# Save a reference to original build_future_rows
original_build_future_rows = frmb.build_future_rows


def build_future_rows_cold_start(*args, **kwargs) -> tuple[pd.DataFrame, dict[str, Any]]:
    # Call original build_future_rows
    future, meta = original_build_future_rows(*args, **kwargs)

    # Merge DB product attributes
    global db_attrs
    future = future.merge(
        db_attrs[
            [
                "SKU",
                "gender",
                "material_group",
                "fabric_group",
                "season",
                "theme",
                "collection",
                "GoLiveDate",
            ]
        ],
        on="SKU",
        how="left",
    )

    # Recompute DaysSinceGoLive and NewnessBucket date-dependently
    future["DaysSinceGoLive"] = (future["Date"] - future["GoLiveDate"]).dt.days
    future["NewnessBucket"] = future["DaysSinceGoLive"].map(classify_newness)

    # Ensure they are preprocessed correctly as categories
    for col in [
        "gender",
        "material_group",
        "fabric_group",
        "season",
        "theme",
        "collection",
        "NewnessBucket",
    ]:
        future[col] = future[col].fillna("").astype(str)

    return future, meta


# Override build_future_rows in-place in both modules
frmb.build_future_rows = build_future_rows_cold_start
frmqb.build_future_rows = build_future_rows_cold_start


def shift_date_years(series: pd.Series, y: int) -> pd.Series:
    unique_dates = series.dropna().unique()
    mapping = {}
    for d in unique_dates:
        ts = pd.Timestamp(d)
        try:
            mapping[d] = ts.replace(year=ts.year + y)
        except ValueError:
            mapping[d] = ts.replace(year=ts.year + y, day=28)
    return series.map(mapping)


def fast_add_same_season_features(
    frame: pd.DataFrame,
    history: pd.DataFrame,
    *,
    years: int,
    window_days: int,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    
    result = frame.copy()
    result[DATE_COLUMN] = pd.to_datetime(result[DATE_COLUMN])
    
    # Prepare keys
    for col in ["Item", "Color", "ProductGroupCode", "SizeGroupCode"]:
        if col not in result.columns:
            result[col] = ""
        result[col] = result[col].fillna("").astype(str).str.strip()
    result["_ItemColorKey"] = result["Item"] + "-" + result["Color"]
    result["_CategorySizeKey"] = result["ProductGroupCode"] + "-" + result["SizeGroupCode"]
    
    hist_cols = [
        DATE_COLUMN,
        "SKU",
        "Item",
        "Color",
        "ProductGroupCode",
        "SizeGroupCode",
        TARGET_COLUMN,
    ]
    hist = history.loc[:, hist_cols].copy()
    hist[DATE_COLUMN] = pd.to_datetime(hist[DATE_COLUMN])
    for col in ["Item", "Color", "ProductGroupCode", "SizeGroupCode"]:
        hist[col] = hist[col].fillna("").astype(str).str.strip()
    hist[TARGET_COLUMN] = pd.to_numeric(hist[TARGET_COLUMN], errors="coerce").fillna(0).clip(lower=0)
    hist = hist.loc[hist[TARGET_COLUMN].gt(0) & hist["SKU"].ne("")].copy()
    hist["_ItemColorKey"] = hist["Item"] + "-" + hist["Color"]
    hist["_CategorySizeKey"] = hist["ProductGroupCode"] + "-" + hist["SizeGroupCode"]
    
    target_dates = sorted(result[DATE_COLUMN].dropna().unique())
    
    # Date expansion mapping: target -> HistoryDate
    date_expansion_list = []
    for target in target_dates:
        target_ts = pd.Timestamp(target)
        for offset in range(-window_days, window_days + 1):
            date_expansion_list.append({
                DATE_COLUMN: target_ts,
                "HistoryDate": target_ts + pd.Timedelta(days=offset)
            })
    date_expansion = pd.DataFrame(date_expansion_list)
    date_expansion[DATE_COLUMN] = pd.to_datetime(date_expansion[DATE_COLUMN])
    date_expansion["HistoryDate"] = pd.to_datetime(date_expansion["HistoryDate"])
    
    denom = float(years * ((window_days * 2) + 1))
    
    for feature_name, key_cols in [
        ("SeasonalSkuSoldUnitsAvg", ["SKU"]),
        ("SeasonalItemColorSoldUnitsAvg", ["_ItemColorKey"]),
        ("SeasonalProductGroupSoldUnitsAvg", ["ProductGroupCode"]),
        ("SeasonalCategorySizeSoldUnitsAvg", ["_CategorySizeKey"]),
    ]:
        key_col = key_cols[0]
        # Shift history dates forward by y years
        shifted_list = []
        for y in range(1, years + 1):
            hist_y = hist[[DATE_COLUMN, key_col, TARGET_COLUMN]].copy()
            hist_y[DATE_COLUMN] = shift_date_years(hist_y[DATE_COLUMN], y)
            shifted_list.append(hist_y)
        
        hist_all = pd.concat(shifted_list, ignore_index=True)
        # Aggregate daily shifted demand
        hist_daily = hist_all.groupby([DATE_COLUMN, key_col], as_index=False)[TARGET_COLUMN].sum()
        hist_daily = hist_daily.rename(columns={DATE_COLUMN: "HistoryDate"})
        hist_daily["HistoryDate"] = pd.to_datetime(hist_daily["HistoryDate"])
        
        # Merge with date expansion
        merged = date_expansion.merge(hist_daily, on="HistoryDate", how="inner")
        # Sum within window
        features = merged.groupby([DATE_COLUMN, key_col], as_index=False)[TARGET_COLUMN].sum()
        features[feature_name] = features[TARGET_COLUMN] / denom
        features = features.drop(columns=[TARGET_COLUMN])
        
        # Merge back to result
        result = result.merge(features, on=[DATE_COLUMN, key_col], how="left")
        result[feature_name] = result[feature_name].fillna(0.0)
        
    return result.drop(columns=["_ItemColorKey", "_CategorySizeKey"])


# Override add_same_season_features in-place in both modules
frmb.add_same_season_features = fast_add_same_season_features
frmqb.add_same_season_features = fast_add_same_season_features


def main() -> None:
    global db_attrs
    args = parse_args()
    configure_threads(args.threads)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, str(args.threads))

    windows = choose_windows(
        args.snapshot_summary_path, args.start_date, args.end_date, args.max_windows
    )
    if windows.empty:
        raise RuntimeError("No complete historical forecast windows matched the requested filters.")
    snapshot_ids = windows["SnapshotId"].dropna().astype(str).tolist()
    horizon_start = pd.Timestamp(windows["ForecastStartDate"].min()).normalize()
    horizon_end = pd.Timestamp(windows["ForecastEndDate"].max()).normalize()

    # 1. Load Forecast DB attributes
    db_attrs = load_forecast_db_attributes(args.snapshot_dir)

    # Register new categorical attributes as features on the fly
    fmt.CATEGORICAL_FEATURES = list(fmt.CATEGORICAL_FEATURES) + [
        "gender",
        "material_group",
        "fabric_group",
        "season",
        "theme",
        "collection",
        "NewnessBucket",
    ]

    print(f"Selected complete windows: {len(windows):,}", flush=True)
    print("Loading model panel...", flush=True)
    panel_start_date = args.panel_start_date or args.start_date
    panel = load_panel(args.panel, panel_start_date)

    if args.include_seasonal_features:
        print(
            "Same-season features enabled "
            f"({args.seasonal_years} years, +/- {args.seasonal_window_days} days).",
            flush=True,
        )
        build_future_rows_cold_start.include_seasonal_features = True
        build_future_rows_cold_start.seasonal_years = args.seasonal_years
        build_future_rows_cold_start.seasonal_window_days = args.seasonal_window_days
    else:
        build_future_rows_cold_start.include_seasonal_features = False

    print("Loading corporate forecast rows for selected windows...", flush=True)
    forecast_day = pd.read_parquet(
        args.forecast_day_path,
        columns=["SnapshotId", "SKU", "ForecastQty"],
        filters=[("SnapshotId", "in", snapshot_ids)],
    )
    actuals = load_actuals(args.actuals_path)
    snapshot_attrs = load_snapshot_attributes(args.forecast_snapshot_path, snapshot_ids)
    daily_promo = load_daily_promotions(args.promo_daily_path, horizon_start, horizon_end)
    pdl_horizon = load_pdl_features(args.pdl_sku_features_path, horizon_start, horizon_end)
    # Keep this load for comparability with the no-ML run and for input metadata.
    promo = load_promo_for_window(args.pdl_sku_features_path, horizon_start, horizon_end)

    ml = require_sklearn()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    score_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []

    for idx, snapshot in windows.iterrows():
        start = pd.Timestamp(snapshot["ForecastStartDate"]).normalize()
        print(f"[{idx + 1}/{len(windows)}] Cold-Start backtest {start.date()}...", flush=True)

        train, calibration = frmb.train_window(panel, args, start)
        train = enrich_with_attributes(train, db_attrs)
        calibration = enrich_with_attributes(calibration, db_attrs)

        # Demand Censoring (Phase 1)
        if not args.disable_censoring:
            if "InventoryAvailPhysicalLag1" in train.columns:
                if train["InventoryAvailPhysicalLag1"].gt(0.0).any():
                    is_stockout = (
                        train["InventoryAvailPhysicalLag1"].eq(0.0)
                        & train["InventoryAvailPhysicalLag1"].notna()
                    )
                    train = train.loc[~is_stockout].copy()
            elif "HasAvailableInventoryLag1" in train.columns:
                if train["HasAvailableInventoryLag1"].any():
                    is_stockout = (
                        train["HasAvailableInventoryLag1"].eq(False)
                        & train["HasAvailableInventoryLag1"].notna()
                    )
                    train = train.loc[~is_stockout].copy()

        future, future_meta = build_future_rows_cold_start(
            panel=panel,
            actuals=actuals,
            pdl_horizon=pdl_horizon,
            daily_promo=daily_promo,
            snapshot_attrs=snapshot_attrs,
            snapshot_id=str(snapshot["SnapshotId"]),
            start=start,
            lookback_days=args.lookback_days,
        )
        actual = actual_window(actuals, start)
        source_universe = snapshot_attrs.loc[
            snapshot_attrs["SnapshotId"].eq(str(snapshot["SnapshotId"])),
            ["SKU"],
        ].drop_duplicates()

        # Build baseline recent no-ML forecast
        recent_forecast, recent_meta = no_ml_forecast(
            actuals=actuals,
            promo=promo,
            source_universe=source_universe,
            start=start,
            lookback_days=args.lookback_days,
            include_seasonal=False,
            include_promo_floor=False,
            seasonal_years=3,
            seasonal_window_days=7,
            seasonal_recent_weight=0.65,
        )

        # Train & calibrate Quantile model (with DB attributes)
        scored, factors = frmqb.run_quantile_stage(ml, train, calibration, future, args)

        raw_col = "ml_quantileForecastQty"
        calibrated_col = "ml_quantileCalibratedForecastQty"

        for source_name, forecast_col in [("raw", raw_col), ("calibrated", calibrated_col)]:
            for threshold in args.sku_total_thresholds:
                threshold = max(0.0, float(threshold))
                base_candidate = (
                    f"ml_cold_start_{source_name}_future_guardrail_{threshold_label(threshold)}"
                )

                # Standard Cold-Start forecast
                forecast = aggregate_forecast(scored, forecast_col, threshold)
                score_rows.append(score_forecast(forecast, actual, base_candidate, snapshot))
                metadata_rows.append(
                    {
                        "Candidate": base_candidate,
                        "SnapshotId": snapshot["SnapshotId"],
                        "ForecastStartDate": start.date().isoformat(),
                        "Quantile": args.quantile,
                        "Censoring": not args.disable_censoring,
                        "Blending": False,
                    }
                )

                # Cold-Start + Blended Corporate Forecast
                if not args.disable_blending:
                    blended_candidate = (
                        f"ml_cold_start_{source_name}_blended_guardrail_{threshold_label(threshold)}"
                    )
                    blended_forecast = frmqb.blend_with_corporate(
                        forecast,
                        forecast_day,
                        snapshot_attrs,
                        str(snapshot["SnapshotId"]),
                    )
                    score_rows.append(
                        score_forecast(blended_forecast, actual, blended_candidate, snapshot)
                    )
                    metadata_rows.append(
                        {
                            "Candidate": blended_candidate,
                            "SnapshotId": snapshot["SnapshotId"],
                            "ForecastStartDate": start.date().isoformat(),
                            "Quantile": args.quantile,
                            "Censoring": not args.disable_censoring,
                            "Blending": True,
                        }
                    )

                if threshold <= 0:
                    continue

                # Cold-Start + Recent Fallback Hybrid
                for fallback_weight in args.hybrid_recent_fallback_weights:
                    fallback_weight = max(0.0, float(fallback_weight))
                    if fallback_weight <= 0:
                        continue

                    hybrid_candidate = (
                        f"hybrid_ml_cold_start_{source_name}_{threshold_label(threshold)}_"
                        f"recent_w{str(fallback_weight).replace('.', 'p')}"
                    )
                    hybrid_forecast = combine_with_recent_fallback(
                        forecast,
                        recent_forecast,
                        fallback_weight,
                    )
                    score_rows.append(
                        score_forecast(hybrid_forecast, actual, hybrid_candidate, snapshot)
                    )
                    metadata_rows.append(
                        {
                            "Candidate": hybrid_candidate,
                            "SnapshotId": snapshot["SnapshotId"],
                            "ForecastStartDate": start.date().isoformat(),
                            "Quantile": args.quantile,
                            "Censoring": not args.disable_censoring,
                            "Blending": False,
                        }
                    )

                    # Hybrid + Blended Corporate Forecast
                    if not args.disable_blending:
                        blended_hybrid_candidate = f"{hybrid_candidate}_blended"
                        blended_hybrid_forecast = frmqb.blend_with_corporate(
                            hybrid_forecast,
                            forecast_day,
                            snapshot_attrs,
                            str(snapshot["SnapshotId"]),
                        )
                        score_rows.append(score_forecast(blended_hybrid_forecast, actual, blended_hybrid_candidate, snapshot))
                        metadata_rows.append({
                            "Candidate": blended_hybrid_candidate,
                            "SnapshotId": snapshot["SnapshotId"],
                            "ForecastStartDate": start.date().isoformat(),
                            "Quantile": args.quantile,
                            "Censoring": not args.disable_censoring,
                            "Blending": True,
                        })

                    # Hybrid + Volume Cap
                    from forecast_replacement_ml_backtest import apply_recent_volume_cap
                    for cap_multiple in args.hybrid_recent_volume_caps:
                        cap_multiple = max(0.0, float(cap_multiple))
                        if cap_multiple <= 0:
                            continue
                        
                        capped_candidate = (
                            f"{hybrid_candidate}_cap_recent_x"
                            f"{str(cap_multiple).replace('.', 'p')}"
                        )
                        capped_forecast = apply_recent_volume_cap(
                            hybrid_forecast,
                            recent_forecast,
                            cap_multiple,
                        )
                        score_rows.append(score_forecast(capped_forecast, actual, capped_candidate, snapshot))
                        metadata_rows.append({
                            "Candidate": capped_candidate,
                            "SnapshotId": snapshot["SnapshotId"],
                            "ForecastStartDate": start.date().isoformat(),
                            "Quantile": args.quantile,
                            "Censoring": not args.disable_censoring,
                            "Blending": False,
                        })

                        # Capped Hybrid + Blended Corporate
                        if not args.disable_blending:
                            blended_capped_candidate = f"{capped_candidate}_blended"
                            blended_capped_forecast = frmqb.blend_with_corporate(
                                capped_forecast,
                                forecast_day,
                                snapshot_attrs,
                                str(snapshot["SnapshotId"]),
                            )
                            score_rows.append(score_forecast(blended_capped_forecast, actual, blended_capped_candidate, snapshot))
                            metadata_rows.append({
                                "Candidate": blended_capped_candidate,
                                "SnapshotId": snapshot["SnapshotId"],
                                "ForecastStartDate": start.date().isoformat(),
                                "Quantile": args.quantile,
                                "Censoring": not args.disable_censoring,
                                "Blending": True,
                            })

    scores = pd.DataFrame(score_rows)
    scores.to_csv(args.output_dir / "replacement_ml_backtest_window_scores.csv", index=False)

    summary = summarize_by_candidate(scores)
    summary.to_csv(args.output_dir / "replacement_ml_backtest_candidate_summary.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "snapshot_dir": str(args.snapshot_dir),
        "windows": len(windows),
        "quantile": args.quantile,
        "censoring": not args.disable_censoring,
        "features": fmt.CATEGORICAL_FEATURES,
    }
    with (args.output_dir / "replacement_ml_backtest_metadata.json").open(
        "w", encoding="utf-8"
    ) as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print("\nCold-Start Backtest Summary (top 15 candidates by WAPE):")
    print(summary.sort_values("WAPE").head(15).to_string(index=False))
    print(f"\nSaved cold-start results to {args.output_dir}")


if __name__ == "__main__":
    main()
