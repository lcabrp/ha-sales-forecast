"""Backtest the Quantile SKU/day forecast model with Demand Censoring and Corporate Blending.

This script implements Phase 1 backtesting:
1. Loads model panel and training/holdout sets for 26 historical windows.
2. Applies demand censoring to training sets (drops stockout rows).
3. Trains a Quantile regression model (default: 0.35 quantile).
4. Generates predictions, and optionally blends/scales SKU predictions back to corporate category totals.
5. Scores and compares the resulting forecasts against corporate and no-ML baselines.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_panel import (  # noqa: E402
    PDL_SKU_FEATURES_PATH,
    PROMO_DAILY_PATH,
)
from forecast_model_train import (  # noqa: E402
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    apply_calibration,
    configure_threads,
    load_panel,
    prepare_xy,
    require_sklearn,
)
from forecast_replacement_backtest import (  # noqa: E402
    ACTUALS_PATH,
    FORECAST_SNAPSHOT_PATH,
    SNAPSHOT_SUMMARY_PATH,
    FORECAST_DAY_PATH,
    actual_window,
    choose_windows,
    load_actuals,
    load_promo_for_window,
    no_ml_forecast,
    score_forecast,
    summarize_by_candidate,
)
from forecast_replacement_ml_backtest import (  # noqa: E402
    load_snapshot_attributes,
    load_daily_promotions,
    load_pdl_features,
    build_future_rows,
    train_window,
    threshold_label,
    aggregate_forecast,
    combine_with_recent_fallback,
    apply_recent_volume_cap,
)

DEFAULT_OUTPUT_DIR = MODEL_DIR.parent / "replacement_ml_quantile_backtests"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the quantile ML forecast backtest pipeline.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Backtest corporate, recent no-ML, and quantile ML forecasts."
    )
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
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


def run_quantile_stage(
    ml: dict[str, Any],
    train: pd.DataFrame,
    calibration: pd.DataFrame,
    holdout: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train a quantile regression model on train, calibrate it, and predict on holdout.

    Args:
        ml: Dictionary containing scikit-learn module/class references.
        train: Training feature panel dataframe.
        calibration: Calibration panel dataframe.
        holdout: Holdout/future dataset to predict on.
        args: Command-line configuration arguments.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: The scored holdout dataframe and calibration factors.
    """
    x_train, y_train, numeric, categorical, boolean = prepare_xy(
        train,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    x_calibration, _, _, _, _ = (
        prepare_xy(
            calibration,
            args.exclude_corporate_features,
            args.include_product_identity_features,
        )
        if not calibration.empty
        else (None, None, None, None, None)
    )
    x_holdout, _, _, _, _ = prepare_xy(
        holdout,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )

    # Build Pipeline
    transformers = []
    if numeric:
        transformers.append(
            (
                "num",
                ml["Pipeline"]([("imputer", ml["SimpleImputer"](strategy="constant", fill_value=0.0))]),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "cat",
                ml["OrdinalEncoder"](
                    handle_unknown="use_encoded_value",
                    unknown_value=-1,
                    encoded_missing_value=-1,
                ),
                categorical,
            )
        )
    if boolean:
        transformers.append(("bool", "passthrough", boolean))

    preprocessor = ml["ColumnTransformer"](transformers=transformers, remainder="drop")
    regressor = ml["HistGradientBoostingRegressor"](
        loss="quantile",
        quantile=args.quantile,
        learning_rate=args.learning_rate,
        max_iter=args.max_iter,
        max_leaf_nodes=args.max_leaf_nodes,
        l2_regularization=0.05,
        random_state=args.random_state,
    )
    model = ml["Pipeline"]([("prep", preprocessor), ("model", regressor)])

    model.fit(x_train, y_train)

    scored = holdout.copy()
    raw_col = "ml_quantileForecastQty"
    cal_col = "ml_quantileCalibratedForecastQty"
    
    scored[raw_col] = np.clip(np.expm1(model.predict(x_holdout)), 0, None)

    calibration_scored = calibration.copy()
    if x_calibration is not None:
        calibration_scored[raw_col] = np.clip(np.expm1(model.predict(x_calibration)), 0, None)
    
    scored, factors = apply_calibration(scored, calibration_scored, raw_col, args)
    scored = scored.rename(columns={"MLCalibratedForecastQty": cal_col})
    return scored, factors


def blend_with_corporate(
    forecast: pd.DataFrame,
    corporate_sku_day: pd.DataFrame,
    snapshot_attrs: pd.DataFrame,
    snapshot_id: str,
    group_cols: list[str] = ["Division", "Department", "Class"],
) -> pd.DataFrame:
    """Scale SKU forecasts so their category-level sum matches corporate category totals.

    This ensures that while SKU-level distribution is guided by ML, the overall category
    volume remains aligned with high-level corporate forecasts (Phase 1 blending).

    Args:
        forecast: Aggregate model forecast.
        corporate_sku_day: Daily corporate forecast records.
        snapshot_attrs: Attribute snapshot data.
        snapshot_id: ID of the snapshot to filter corporate forecast.
        group_cols: List of column names representing category hierarchy.

    Returns:
        pd.DataFrame: A scaled forecast DataFrame with SKU and scaled ForecastUnits.
    """
    if forecast.empty or corporate_sku_day.empty:
        return forecast.copy()

    attrs = snapshot_attrs.loc[snapshot_attrs["SnapshotId"].eq(snapshot_id)].copy()
    if attrs.empty:
        return forecast.copy()

    # Get corporate 14-day total at SKU level, then merge attributes
    corp_sku = (
        corporate_sku_day.loc[corporate_sku_day["SnapshotId"].eq(snapshot_id)]
        .groupby("SKU", as_index=False)
        .agg(CorpUnits=("ForecastQty", "sum"))
    )
    corp_sku = corp_sku.merge(attrs, on="SKU", how="left")
    
    # Aggregate corporate forecast units to category
    for col in group_cols:
        if col not in corp_sku.columns:
            corp_sku[col] = ""
    corp_cat = corp_sku.groupby(group_cols, as_index=False, dropna=False).agg(CorpCatUnits=("CorpUnits", "sum"))

    # Aggregate model units to category
    model_sku = forecast.copy()
    model_sku = model_sku.merge(attrs, on="SKU", how="left")
    for col in group_cols:
        if col not in model_sku.columns:
            model_sku[col] = ""
    model_cat = model_sku.groupby(group_cols, as_index=False, dropna=False).agg(ModelCatUnits=("ForecastUnits", "sum"))

    # Merge aggregates back to model SKU predictions
    model_sku = model_sku.merge(corp_cat, on=group_cols, how="left")
    model_sku = model_sku.merge(model_cat, on=group_cols, how="left")

    model_sku["CorpCatUnits"] = model_sku["CorpCatUnits"].fillna(0.0)
    model_sku["ModelCatUnits"] = model_sku["ModelCatUnits"].fillna(0.0)

    # Scale SKU prediction:
    # If corporate category units > 0 and model category units > 0, scale.
    # Otherwise keep model forecast as-is (scale factor 1.0) to preserve coverage.
    can_scale = model_sku["CorpCatUnits"].gt(0) & model_sku["ModelCatUnits"].gt(0)
    model_sku["ScaleFactor"] = 1.0
    model_sku.loc[can_scale, "ScaleFactor"] = model_sku.loc[can_scale, "CorpCatUnits"] / model_sku.loc[can_scale, "ModelCatUnits"]

    model_sku["ForecastUnits"] = (model_sku["ForecastUnits"] * model_sku["ScaleFactor"]).round().clip(lower=0)
    
    return model_sku.loc[model_sku["ForecastUnits"].gt(0), ["SKU", "ForecastUnits"]].copy()


def main() -> None:
    """Execute the ML quantile backtest runner across historical snapshot windows."""
    args = parse_args()
    configure_threads(args.threads)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, str(args.threads))

    windows = choose_windows(args.snapshot_summary_path, args.start_date, args.end_date, args.max_windows)
    if windows.empty:
        raise RuntimeError("No complete historical forecast windows matched the requested filters.")
    snapshot_ids = windows["SnapshotId"].dropna().astype(str).tolist()
    horizon_start = pd.Timestamp(windows["ForecastStartDate"].min()).normalize()
    horizon_end = pd.Timestamp(windows["ForecastEndDate"].max()).normalize()

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
        build_future_rows.include_seasonal_features = True
        build_future_rows.seasonal_years = args.seasonal_years
        build_future_rows.seasonal_window_days = args.seasonal_window_days
    else:
        build_future_rows.include_seasonal_features = False

    print("Loading corporate forecast rows for selected windows...", flush=True)
    forecast_day = pd.read_parquet(
        args.forecast_day_path,
        columns=["SnapshotId", "SKU", "ForecastQty"],
        filters=[("SnapshotId", "in", snapshot_ids)],
    )
    print("Loading snapshot SKU universes...", flush=True)
    pd.read_parquet(
        args.forecast_snapshot_path,
        columns=["SnapshotId", "SKU"],
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
        print(f"[{idx + 1}/{len(windows)}] Quantile backtest {start.date()}...", flush=True)
        
        train, calibration = train_window(panel, args, start)

        # Demand Censoring (Phase 1)
        if not args.disable_censoring:
            if "InventoryAvailPhysicalLag1" in train.columns:
                is_stockout = train["InventoryAvailPhysicalLag1"].eq(0.0) & train["InventoryAvailPhysicalLag1"].notna()
                train = train.loc[~is_stockout].copy()
            elif "HasAvailableInventoryLag1" in train.columns:
                is_stockout = train["HasAvailableInventoryLag1"].eq(False) & train["HasAvailableInventoryLag1"].notna()
                train = train.loc[~is_stockout].copy()

        future, future_meta = build_future_rows(
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

        # Train & calibrate Quantile model
        scored, factors = run_quantile_stage(ml, train, calibration, future, args)
        
        raw_col = "ml_quantileForecastQty"
        calibrated_col = "ml_quantileCalibratedForecastQty"
        
        for source_name, forecast_col in [("raw", raw_col), ("calibrated", calibrated_col)]:
            for threshold in args.sku_total_thresholds:
                threshold = max(0.0, float(threshold))
                base_candidate = f"ml_quantile_{source_name}_future_guardrail_{threshold_label(threshold)}"
                
                # Standard Quantile forecast
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

                # Quantile + Blended Corporate Forecast (Phase 1)
                if not args.disable_blending:
                    blended_candidate = f"ml_quantile_{source_name}_blended_guardrail_{threshold_label(threshold)}"
                    blended_forecast = blend_with_corporate(
                        forecast,
                        forecast_day,
                        snapshot_attrs,
                        str(snapshot["SnapshotId"]),
                    )
                    score_rows.append(score_forecast(blended_forecast, actual, blended_candidate, snapshot))
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

                # Quantile + Recent Fallback Hybrid
                for fallback_weight in args.hybrid_recent_fallback_weights:
                    fallback_weight = max(0.0, float(fallback_weight))
                    if fallback_weight <= 0:
                        continue
                        
                    hybrid_candidate = (
                        f"hybrid_ml_quantile_{source_name}_{threshold_label(threshold)}_"
                        f"recent_w{str(fallback_weight).replace('.', 'p')}"
                    )
                    hybrid_forecast = combine_with_recent_fallback(
                        forecast,
                        recent_forecast,
                        fallback_weight,
                    )
                    score_rows.append(score_forecast(hybrid_forecast, actual, hybrid_candidate, snapshot))
                    
                    # Hybrid + Blended Corporate Forecast (Phase 1)
                    if not args.disable_blending:
                        blended_hybrid_candidate = f"{hybrid_candidate}_blended"
                        blended_hybrid_forecast = blend_with_corporate(
                            hybrid_forecast,
                            forecast_day,
                            snapshot_attrs,
                            str(snapshot["SnapshotId"]),
                        )
                        score_rows.append(score_forecast(blended_hybrid_forecast, actual, blended_hybrid_candidate, snapshot))

                    # Hybrid + Volume Cap
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

                        # Capped Hybrid + Blended Corporate
                        if not args.disable_blending:
                            blended_capped_candidate = f"{capped_candidate}_blended"
                            blended_capped_forecast = blend_with_corporate(
                                capped_forecast,
                                forecast_day,
                                snapshot_attrs,
                                str(snapshot["SnapshotId"]),
                            )
                            score_rows.append(score_forecast(blended_capped_forecast, actual, blended_capped_candidate, snapshot))

    scores = pd.DataFrame(score_rows)
    summary = summarize_by_candidate(scores)
    
    scores.to_csv(args.output_dir / "replacement_quantile_backtest_scores.csv", index=False)
    summary.to_csv(args.output_dir / "replacement_quantile_backtest_summary.csv", index=False)
    
    metadata = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "windows": int(len(windows)),
        "quantile": args.quantile,
        "demand_censoring": not args.disable_censoring,
        "corporate_blending": not args.disable_blending,
        "sku_total_thresholds": args.sku_total_thresholds,
        "hybrid_recent_fallback_weights": args.hybrid_recent_fallback_weights,
        "hybrid_recent_volume_caps": args.hybrid_recent_volume_caps,
    }
    with (args.output_dir / "replacement_quantile_backtest_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2)

    print("\nQuantile Backtest Summary:")
    print(summary.head(20).to_string(index=False))
    print(f"\nWrote backtest outputs to: {args.output_dir}")


if __name__ == "__main__":
    main()
