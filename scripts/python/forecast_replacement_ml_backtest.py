"""Backtest the sklearn champion as a future-safe BRG replacement candidate.

The older model scoreboard scores rows that already exist in the sparse model
panel.  That is useful for model development, but it is too generous for a true
replacement forecast because actual demand can create holdout rows.  This runner
uses the same historical corporate forecast-start windows as the replacement
baseline, but builds SKU/day forecast rows from information that should be known
before each forecast starts.
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

from forecast_model_compare_sklearn import require_sklearn, run_single_stage, run_two_stage  # noqa: E402
from forecast_model_panel import (  # noqa: E402
    CATEGORY_SIZE_LAG_FEATURE_COLUMNS,
    ITEM_COLOR_LAG_FEATURE_COLUMNS,
    LAG_FEATURE_COLUMNS,
    PDL_SKU_FEATURES_PATH,
    PROMO_DAILY_PATH,
)
from forecast_model_train import (  # noqa: E402
    BASELINE_COLUMNS,
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    NUMERIC_FEATURES,
    PRODUCT_IDENTITY_FEATURES,
    TARGET_COLUMN,
    configure_threads,
    load_panel,
    sample_training_rows,
)
from forecast_replacement_backtest import (  # noqa: E402
    ACTUALS_PATH,
    FORECAST_SNAPSHOT_PATH,
    SNAPSHOT_SUMMARY_PATH,
    actual_window,
    choose_windows,
    direct_pick_signal,
    load_actuals,
    load_promo_for_window,
    no_ml_forecast,
    normalize_date,
    score_forecast,
    summarize_by_candidate,
)
from forecast_replacement_contract import normalize_sku_series  # noqa: E402


DEFAULT_OUTPUT_DIR = MODEL_DIR.parent / "replacement_ml_backtests"
DEFAULT_MODELS = ["hgb_absolute_log"]
SUPPORTED_MODELS = ["hgb_absolute_log", "two_stage_hgb_log"]
SNAPSHOT_ATTRIBUTE_COLUMNS = [
    "SnapshotId",
    "SKU",
    "Division",
    "Department",
    "Class",
    "KeyCategoryView",
    "Item",
    "Color",
    "Size",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
]
FROZEN_FEATURE_COLUMNS = [
    *LAG_FEATURE_COLUMNS,
    *ITEM_COLOR_LAG_FEATURE_COLUMNS,
    *CATEGORY_SIZE_LAG_FEATURE_COLUMNS,
    "InventoryAvailPhysicalLag1",
    "InventoryOrderedInTotalLag1",
    "InventoryPhysicalReservedLag1",
    "InventoryNetAvailablePhysicalLag1",
    "InventoryAvgUnitPriceLag1",
    "InventoryAvgLandedCostLag1",
    "SupplyWorkUnitsLag1",
    "ReplenishmentUnitsLag1",
    "ReceivingPutawayUnitsLag1",
    "ReturnSellableFloorUnitsLag1",
    "ReturnNonSellableUnitsLag1",
    "TransferUnitsLag1",
    "ReplenishmentToFloorUnitsLag1",
    "ReserveOrBulkSupplyUnitsLag1",
    "StagingMovementUnitsLag1",
    "SellableFloorSupplyUnitsLag1",
    "NonSellableSupplyUnitsLag1",
    "HasAvailableInventoryLag1",
    "HasNetAvailableInventoryLag1",
    "HasOrderedInventoryLag1",
]
SEASONAL_FEATURE_COLUMNS = [
    "SeasonalSkuSoldUnitsAvg",
    "SeasonalItemColorSoldUnitsAvg",
    "SeasonalProductGroupSoldUnitsAvg",
    "SeasonalCategorySizeSoldUnitsAvg",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest the sklearn champion with future-safe SKU/day rows."
    )
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--snapshot-summary-path", type=Path, default=SNAPSHOT_SUMMARY_PATH)
    parser.add_argument("--forecast-snapshot-path", type=Path, default=FORECAST_SNAPSHOT_PATH)
    parser.add_argument("--actuals-path", type=Path, default=ACTUALS_PATH)
    parser.add_argument("--pdl-sku-features-path", type=Path, default=PDL_SKU_FEATURES_PATH)
    parser.add_argument("--promo-daily-path", type=Path, default=PROMO_DAILY_PATH)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date")
    parser.add_argument(
        "--panel-start-date",
        help=(
            "Optional earlier panel history start. Use this when scoring recent "
            "windows but seasonal features need prior-year history."
        ),
    )
    parser.add_argument("--max-windows", type=int, default=26)
    parser.add_argument("--lookback-days", type=int, default=56)
    parser.add_argument("--models", nargs="+", choices=SUPPORTED_MODELS, default=DEFAULT_MODELS)
    parser.add_argument(
        "--sku-total-thresholds",
        nargs="+",
        type=float,
        default=[0.0, 1.0, 3.0, 5.0],
        help="Score static minimum 14-day SKU forecast thresholds without using future actuals.",
    )
    parser.add_argument(
        "--hybrid-recent-fallback-weights",
        nargs="+",
        type=float,
        default=[],
        help=(
            "Optional fallback weights for recent no-ML demand on SKUs below each ML threshold. "
            "Example: 0.25 adds 25%% of recent no-ML demand only for SKUs not selected by ML."
        ),
    )
    parser.add_argument(
        "--hybrid-recent-volume-caps",
        nargs="+",
        type=float,
        default=[],
        help=(
            "Optional total-volume caps for hybrid forecasts, expressed as a multiple of "
            "the recent no-ML forecast total. Example: 1.10 caps hybrid units at 110%% "
            "of recent no-ML units for that window."
        ),
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
        help=(
            "Add prior-year same-calendar-window demand features by SKU, item-color, "
            "product group, and product-group/size. These are future-safe because "
            "they only use earlier years."
        ),
    )
    parser.add_argument("--seasonal-years", type=int, default=3)
    parser.add_argument("--seasonal-window-days", type=int, default=7)
    return parser.parse_args()


def load_snapshot_attributes(path: Path, snapshot_ids: list[str]) -> pd.DataFrame:
    attrs = pd.read_parquet(
        path,
        columns=SNAPSHOT_ATTRIBUTE_COLUMNS,
        filters=[("SnapshotId", "in", snapshot_ids)],
    )
    attrs["SKU"] = normalize_sku_series(attrs["SKU"])
    attrs = attrs.loc[attrs["SKU"].ne("")].copy()
    return attrs.drop_duplicates(["SnapshotId", "SKU"], keep="last")


def load_daily_promotions(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    columns = [
        "date",
        "pdl_active_events",
        "pdl_offer_cc_count",
        "pdl_style_count",
        "pdl_total_avail_inv",
        "coupon_active_rows",
        "coupon_max_discount_percent",
    ]
    if not path.exists():
        return pd.DataFrame(columns=["Date"])
    promo = pd.read_parquet(path, columns=columns)
    promo["Date"] = normalize_date(promo["date"])
    promo = promo.loc[promo["Date"].between(start, end)].drop(columns=["date"]).copy()
    promo["HasPDLPromotion"] = pd.to_numeric(
        promo.get("pdl_active_events", 0), errors="coerce"
    ).fillna(0).gt(0)
    promo["HasCouponPromotion"] = pd.to_numeric(
        promo.get("coupon_active_rows", 0), errors="coerce"
    ).fillna(0).gt(0)
    promo["HasAnyPromotion"] = promo["HasPDLPromotion"] | promo["HasCouponPromotion"]
    return promo


def load_pdl_features(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    columns = [
        "Date",
        "SKU",
        "pdl_sku_offer_rows",
        "pdl_sku_active_events",
        "pdl_sku_distinct_offer_colors",
        "pdl_sku_max_discount_pct",
        "pdl_sku_avg_discount_pct",
        "pdl_sku_min_promo_price",
        "pdl_sku_total_avail_inv",
        "pdl_sku_total_avail_plus_oo",
        "pdl_sku_lw_unit_sales",
        "pdl_sku_has_markdown",
        "pdl_sku_has_final_sale",
        "pdl_sku_has_tier1_recommendation",
        "pdl_sku_primary_sheet_type",
        "pdl_sku_primary_scope",
        "pdl_sku_primary_event_name",
        "HasSkuPDLPromotion",
    ]
    if not path.exists():
        return pd.DataFrame(columns=["Date", "SKU"])
    pdl = pd.read_parquet(path, columns=columns)
    pdl["Date"] = normalize_date(pdl["Date"])
    pdl["SKU"] = normalize_sku_series(pdl["SKU"])
    return pdl.loc[pdl["Date"].between(start, end) & pdl["SKU"].ne("")].copy()


def latest_prestart_features(panel: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    columns = ["SKU", *[col for col in FROZEN_FEATURE_COLUMNS if col in panel.columns]]
    if len(columns) == 1:
        return pd.DataFrame(columns=["SKU"])
    history = panel.loc[panel[DATE_COLUMN].lt(start), [DATE_COLUMN, *columns]].copy()
    if history.empty:
        return pd.DataFrame(columns=columns)
    return (
        history.sort_values(["SKU", DATE_COLUMN], kind="mergesort")
        .drop_duplicates("SKU", keep="last")
        .drop(columns=[DATE_COLUMN])
    )


def add_same_season_features(
    frame: pd.DataFrame,
    history: pd.DataFrame,
    *,
    years: int,
    window_days: int,
) -> pd.DataFrame:
    """Add prior-year same-calendar demand features without resurrecting old SKUs."""
    if frame.empty:
        return frame
    required = {
        DATE_COLUMN,
        "SKU",
        "Item",
        "Color",
        "ProductGroupCode",
        "SizeGroupCode",
        TARGET_COLUMN,
    }
    if not required.issubset(history.columns):
        result = frame.copy()
        for column in SEASONAL_FEATURE_COLUMNS:
            result[column] = 0.0
        return result

    years = max(1, int(years))
    window_days = max(0, int(window_days))
    denom = float(years * ((window_days * 2) + 1))

    result = frame.copy()
    result[DATE_COLUMN] = normalize_date(result[DATE_COLUMN])
    result["_SeasonalRowId"] = np.arange(len(result), dtype="int64")
    result["SKU"] = normalize_sku_series(result["SKU"])
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
    hist[DATE_COLUMN] = normalize_date(hist[DATE_COLUMN])
    hist["SKU"] = normalize_sku_series(hist["SKU"])
    for col in ["Item", "Color", "ProductGroupCode", "SizeGroupCode"]:
        hist[col] = hist[col].fillna("").astype(str).str.strip()
    hist[TARGET_COLUMN] = pd.to_numeric(hist[TARGET_COLUMN], errors="coerce").fillna(0).clip(lower=0)
    hist = hist.loc[hist[TARGET_COLUMN].gt(0) & hist["SKU"].ne("")].copy()
    hist["_ItemColorKey"] = hist["Item"] + "-" + hist["Color"]
    hist["_CategorySizeKey"] = hist["ProductGroupCode"] + "-" + hist["SizeGroupCode"]

    requested_dates = pd.DataFrame({DATE_COLUMN: sorted(result[DATE_COLUMN].dropna().unique())})
    date_links = []
    for date_value in requested_dates[DATE_COLUMN]:
        target = pd.Timestamp(date_value).normalize()
        for year_back in range(1, years + 1):
            try:
                anchor = target.replace(year=target.year - year_back)
            except ValueError:
                anchor = target.replace(year=target.year - year_back, day=28)
            start = anchor - pd.Timedelta(days=window_days)
            end = anchor + pd.Timedelta(days=window_days)
            date_links.append((target, start, end))
    if not date_links or hist.empty:
        for column in SEASONAL_FEATURE_COLUMNS:
            result[column] = 0.0
        return result.drop(columns=["_SeasonalRowId", "_ItemColorKey", "_CategorySizeKey"])

    feature_frames = []
    for target, start, end in date_links:
        sample = hist.loc[hist[DATE_COLUMN].between(start, end)]
        if sample.empty:
            continue
        feature_frames.append(
            sample.groupby("SKU", as_index=False)
            .agg(SeasonalSkuSoldUnitsAvg=(TARGET_COLUMN, "sum"))
            .assign(**{DATE_COLUMN: target})
        )
        feature_frames.append(
            sample.groupby("_ItemColorKey", as_index=False)
            .agg(SeasonalItemColorSoldUnitsAvg=(TARGET_COLUMN, "sum"))
            .assign(**{DATE_COLUMN: target})
        )
        feature_frames.append(
            sample.groupby("ProductGroupCode", as_index=False)
            .agg(SeasonalProductGroupSoldUnitsAvg=(TARGET_COLUMN, "sum"))
            .assign(**{DATE_COLUMN: target})
        )
        feature_frames.append(
            sample.groupby("_CategorySizeKey", as_index=False)
            .agg(SeasonalCategorySizeSoldUnitsAvg=(TARGET_COLUMN, "sum"))
            .assign(**{DATE_COLUMN: target})
        )

    for feature_name, key_cols in [
        ("SeasonalSkuSoldUnitsAvg", ["SKU"]),
        ("SeasonalItemColorSoldUnitsAvg", ["_ItemColorKey"]),
        ("SeasonalProductGroupSoldUnitsAvg", ["ProductGroupCode"]),
        ("SeasonalCategorySizeSoldUnitsAvg", ["_CategorySizeKey"]),
    ]:
        matching = [df for df in feature_frames if feature_name in df.columns]
        if not matching:
            result[feature_name] = 0.0
            continue
        features = (
            pd.concat(matching, ignore_index=True)
            .groupby([DATE_COLUMN, *key_cols], as_index=False)
            .agg(**{feature_name: (feature_name, "sum")})
        )
        features[feature_name] = pd.to_numeric(features[feature_name], errors="coerce").fillna(0) / denom
        result = result.merge(features, on=[DATE_COLUMN, *key_cols], how="left")
        result[feature_name] = pd.to_numeric(result[feature_name], errors="coerce").fillna(0.0)

    return result.drop(columns=["_SeasonalRowId", "_ItemColorKey", "_CategorySizeKey"])


def build_future_rows(
    *,
    panel: pd.DataFrame,
    actuals: pd.DataFrame,
    pdl_horizon: pd.DataFrame,
    daily_promo: pd.DataFrame,
    snapshot_attrs: pd.DataFrame,
    snapshot_id: str,
    start: pd.Timestamp,
    lookback_days: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    end = start + pd.Timedelta(days=13)
    attrs = snapshot_attrs.loc[snapshot_attrs["SnapshotId"].eq(snapshot_id)].copy()
    direct, _factors, direct_meta = direct_pick_signal(actuals, start, lookback_days)
    promo_skus = pdl_horizon.loc[pdl_horizon["Date"].between(start, end), "SKU"].dropna().unique()
    universe = set(attrs["SKU"].dropna().unique())
    universe.update(direct.loc[direct["DirectPickLookbackUnits"].gt(0), "SKU"])
    universe.update(promo_skus)
    universe.discard("")

    dates = pd.date_range(start, end, freq="D")
    future = pd.MultiIndex.from_product(
        [sorted(universe), dates],
        names=["SKU", DATE_COLUMN],
    ).to_frame(index=False)
    future[TARGET_COLUMN] = 0.0
    for col in BASELINE_COLUMNS:
        future[col] = 0.0
    future = future.merge(
        attrs.drop(columns=["SnapshotId"]).drop_duplicates("SKU", keep="last"),
        on="SKU",
        how="left",
    )
    future = future.merge(daily_promo, on=DATE_COLUMN, how="left")
    future = future.merge(pdl_horizon, on=["SKU", DATE_COLUMN], how="left")
    future = future.merge(latest_prestart_features(panel, start), on="SKU", how="left")
    if getattr(build_future_rows, "include_seasonal_features", False):
        future = add_same_season_features(
            future,
            panel,
            years=getattr(build_future_rows, "seasonal_years", 3),
            window_days=getattr(build_future_rows, "seasonal_window_days", 7),
        )

    future["DayOfWeek"] = future[DATE_COLUMN].dt.dayofweek.astype("int8")
    future["WeekOfYear"] = future[DATE_COLUMN].dt.isocalendar().week.astype("int16")
    future["Month"] = future[DATE_COLUMN].dt.month.astype("int8")
    future["IsWeekend"] = future["DayOfWeek"].isin([5, 6])
    future["HasCorporateForecast"] = False
    future["CorporateForecastQty"] = 0.0
    future["CorporateForecastQtyLag1"] = 0.0
    future["CorporateForecastQtyRolling7"] = 0.0

    for col in [*NUMERIC_FEATURES, *BASELINE_COLUMNS]:
        if col not in future.columns:
            future[col] = 0.0
        future[col] = pd.to_numeric(future[col], errors="coerce").fillna(0.0)
    for col in [*CATEGORICAL_FEATURES, *PRODUCT_IDENTITY_FEATURES]:
        if col not in future.columns:
            future[col] = ""
        future[col] = future[col].fillna("").astype(str)
    for col in BOOLEAN_FEATURES:
        if col not in future.columns:
            future[col] = False
        future[col] = future[col].fillna(False).astype(bool)

    metadata = {
        "candidate_universe_skus": int(len(universe)),
        "snapshot_attribute_skus": int(attrs["SKU"].nunique()),
        "direct_pick_signal": direct_meta,
        "promo_horizon_skus": int(len(set(promo_skus))),
        "future_rows": int(len(future)),
        "feature_policy": (
            "Rows are generated from snapshot, prior DirectPick, and known PDL SKU universe. "
            "Demand/inventory/supply lag features are frozen at latest prestart values; "
            "future actual/order rows are not used to create holdout rows."
        ),
    }
    return future, metadata


def train_window(
    panel: pd.DataFrame,
    args: argparse.Namespace,
    start: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    calibration_start = start - pd.Timedelta(days=max(args.calibration_days, 0))
    train = panel.loc[panel[DATE_COLUMN].lt(calibration_start)].copy()
    calibration = panel.loc[
        panel[DATE_COLUMN].between(calibration_start, start - pd.Timedelta(days=1))
    ].copy()
    if train.empty:
        train = panel.loc[panel[DATE_COLUMN].lt(start)].copy()
        calibration = panel.iloc[0:0].copy()
    train = sample_training_rows(train, args.max_train_rows, args.random_state)
    if getattr(args, "include_seasonal_features", False):
        train = add_same_season_features(
            train,
            panel,
            years=getattr(args, "seasonal_years", 3),
            window_days=getattr(args, "seasonal_window_days", 7),
        )
        if not calibration.empty:
            calibration = add_same_season_features(
                calibration,
                panel,
                years=getattr(args, "seasonal_years", 3),
                window_days=getattr(args, "seasonal_window_days", 7),
            )
    return train, calibration


def threshold_label(value: float) -> str:
    if value == 0:
        return "all_positive"
    return f"min_{str(value).replace('.', 'p')}_units"


def aggregate_forecast(scored: pd.DataFrame, forecast_col: str, min_sku_forecast_units: float) -> pd.DataFrame:
    forecast = (
        scored.groupby("SKU", as_index=False)
        .agg(ForecastUnits=(forecast_col, "sum"))
        .sort_values("SKU", kind="mergesort")
    )
    forecast["ForecastUnits"] = pd.to_numeric(
        forecast["ForecastUnits"], errors="coerce"
    ).fillna(0).clip(lower=0)
    return forecast.loc[forecast["ForecastUnits"].ge(min_sku_forecast_units)].copy()


def combine_with_recent_fallback(
    ml_forecast: pd.DataFrame,
    recent_forecast: pd.DataFrame,
    fallback_weight: float,
) -> pd.DataFrame:
    fallback_weight = max(0.0, float(fallback_weight))
    if fallback_weight <= 0 or recent_forecast.empty:
        return ml_forecast.copy()

    selected_skus = set(ml_forecast["SKU"].dropna().astype(str))
    fallback = recent_forecast.loc[~recent_forecast["SKU"].astype(str).isin(selected_skus)].copy()
    if fallback.empty:
        return ml_forecast.copy()
    fallback["ForecastUnits"] = (
        pd.to_numeric(fallback["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
        * fallback_weight
    )
    fallback = fallback.loc[fallback["ForecastUnits"].gt(0), ["SKU", "ForecastUnits"]]
    if fallback.empty:
        return ml_forecast.copy()
    return (
        pd.concat([ml_forecast[["SKU", "ForecastUnits"]], fallback], ignore_index=True)
        .groupby("SKU", as_index=False)
        .agg(ForecastUnits=("ForecastUnits", "sum"))
        .sort_values("SKU", kind="mergesort")
    )


def apply_recent_volume_cap(
    forecast: pd.DataFrame,
    recent_forecast: pd.DataFrame,
    cap_multiple: float,
) -> pd.DataFrame:
    cap_multiple = max(0.0, float(cap_multiple))
    if cap_multiple <= 0 or forecast.empty or recent_forecast.empty:
        return forecast.copy()
    forecast_units = float(pd.to_numeric(forecast["ForecastUnits"], errors="coerce").fillna(0).sum())
    recent_units = float(pd.to_numeric(recent_forecast["ForecastUnits"], errors="coerce").fillna(0).sum())
    cap_units = recent_units * cap_multiple
    if forecast_units <= cap_units or forecast_units <= 0:
        return forecast.copy()
    capped = forecast.copy()
    capped["ForecastUnits"] = (
        pd.to_numeric(capped["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
        * (cap_units / forecast_units)
    )
    return capped.loc[capped["ForecastUnits"].gt(0)].copy()


def main() -> None:
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
    print(f"  panel rows: {len(panel):,}", flush=True)
    print("Loading actuals and future-safe input tables...", flush=True)
    actuals = load_actuals(args.actuals_path)
    snapshot_attrs = load_snapshot_attributes(args.forecast_snapshot_path, snapshot_ids)
    daily_promo = load_daily_promotions(args.promo_daily_path, horizon_start, horizon_end)
    pdl_horizon = load_pdl_features(args.pdl_sku_features_path, horizon_start, horizon_end)
    # Keep this load for comparability with the no-ML run and for input metadata.
    promo = load_promo_for_window(args.pdl_sku_features_path, horizon_start, horizon_end)
    print(
        f"  actual rows: {len(actuals):,}; snapshot attrs: {len(snapshot_attrs):,}; "
        f"daily promo rows: {len(daily_promo):,}; PDL SKU rows: {len(pdl_horizon):,}; "
        f"active promo rows: {len(promo):,}",
        flush=True,
    )
    ml = require_sklearn()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    score_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    for idx, snapshot in windows.iterrows():
        start = pd.Timestamp(snapshot["ForecastStartDate"]).normalize()
        print(f"[{idx + 1}/{len(windows)}] ML guardrail backtest {start.date()}...", flush=True)
        train, calibration = train_window(panel, args, start)
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
        for model_name in args.models:
            print(f"  running {model_name}", flush=True)
            if model_name == "two_stage_hgb_log":
                scored, factors = run_two_stage(ml, train, calibration, future, args)
            else:
                scored, factors = run_single_stage(model_name, ml, train, calibration, future, args)
            raw_col = f"{model_name}ForecastQty"
            calibrated_col = f"{model_name}CalibratedForecastQty"
            for source_name, forecast_col in [("raw", raw_col), ("calibrated", calibrated_col)]:
                for threshold in args.sku_total_thresholds:
                    threshold = max(0.0, float(threshold))
                    candidate = (
                        f"ml_{model_name}_{source_name}_future_guardrail_"
                        f"{threshold_label(threshold)}"
                    )
                    forecast = aggregate_forecast(scored, forecast_col, threshold)
                    score_rows.append(score_forecast(forecast, actual, candidate, snapshot))
                    metadata_rows.append(
                        {
                            "Candidate": candidate,
                            "Model": model_name,
                            "ForecastSource": source_name,
                            "MinSkuForecastUnits": threshold,
                            "SnapshotId": snapshot["SnapshotId"],
                            "ForecastStartDate": start.date().isoformat(),
                            "TrainRows": int(len(train)),
                            "CalibrationRows": int(len(calibration)),
                            "FutureRows": int(len(future)),
                            "CalibrationFactors": json.dumps(
                                factors.head(50).replace({np.nan: None}).to_dict(orient="records"),
                                sort_keys=True,
                            ),
                            "FutureMetadata": json.dumps(future_meta, sort_keys=True),
                        }
                    )
                    if threshold <= 0:
                        continue
                    for fallback_weight in args.hybrid_recent_fallback_weights:
                        fallback_weight = max(0.0, float(fallback_weight))
                        if fallback_weight <= 0:
                            continue
                        hybrid_candidate = (
                            f"hybrid_ml_{model_name}_{source_name}_{threshold_label(threshold)}_"
                            f"recent_w{str(fallback_weight).replace('.', 'p')}"
                        )
                        hybrid_forecast = combine_with_recent_fallback(
                            forecast,
                            recent_forecast,
                            fallback_weight,
                        )
                        score_rows.append(score_forecast(hybrid_forecast, actual, hybrid_candidate, snapshot))
                        metadata_rows.append(
                            {
                                "Candidate": hybrid_candidate,
                                "Model": model_name,
                                "ForecastSource": source_name,
                                "MinSkuForecastUnits": threshold,
                                "RecentFallbackWeight": fallback_weight,
                                "SnapshotId": snapshot["SnapshotId"],
                                "ForecastStartDate": start.date().isoformat(),
                                "TrainRows": int(len(train)),
                                "CalibrationRows": int(len(calibration)),
                                "FutureRows": int(len(future)),
                                "RecentFallbackMetadata": json.dumps(recent_meta, sort_keys=True),
                                "CalibrationFactors": json.dumps(
                                    factors.head(50).replace({np.nan: None}).to_dict(orient="records"),
                                    sort_keys=True,
                                ),
                                "FutureMetadata": json.dumps(future_meta, sort_keys=True),
                            }
                        )
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
                            metadata_rows.append(
                                {
                                    "Candidate": capped_candidate,
                                    "Model": model_name,
                                    "ForecastSource": source_name,
                                    "MinSkuForecastUnits": threshold,
                                    "RecentFallbackWeight": fallback_weight,
                                    "RecentVolumeCapMultiple": cap_multiple,
                                    "SnapshotId": snapshot["SnapshotId"],
                                    "ForecastStartDate": start.date().isoformat(),
                                    "TrainRows": int(len(train)),
                                    "CalibrationRows": int(len(calibration)),
                                    "FutureRows": int(len(future)),
                                    "RecentFallbackMetadata": json.dumps(recent_meta, sort_keys=True),
                                    "CalibrationFactors": json.dumps(
                                        factors.head(50).replace({np.nan: None}).to_dict(orient="records"),
                                        sort_keys=True,
                                    ),
                                    "FutureMetadata": json.dumps(future_meta, sort_keys=True),
                                }
                            )

    scores = pd.DataFrame(score_rows)
    summary = summarize_by_candidate(scores)
    winners = (
        scores.sort_values(["ForecastStartDate", "WAPE"], kind="mergesort")
        .groupby("ForecastStartDate", as_index=False)
        .first()[["ForecastStartDate", "Candidate", "WAPE", "BiasPctForecastMinusActual"]]
        .rename(columns={"Candidate": "LowestWAPECandidate"})
    )
    metadata = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "threads_requested": args.threads,
        "windows": int(len(windows)),
        "window_start_min": str(windows["ForecastStartDate"].min().date()),
        "window_start_max": str(windows["ForecastStartDate"].max().date()),
        "panel_start_date": panel_start_date,
        "models": args.models,
        "sku_total_thresholds": args.sku_total_thresholds,
        "hybrid_recent_fallback_weights": args.hybrid_recent_fallback_weights,
        "hybrid_recent_volume_caps": args.hybrid_recent_volume_caps,
        "max_train_rows": args.max_train_rows,
        "max_iter": args.max_iter,
        "calibration_mode": args.calibration_mode,
        "exclude_corporate_features": bool(args.exclude_corporate_features),
        "include_product_identity_features": bool(args.include_product_identity_features),
        "seasonal_features": {
            "included": bool(args.include_seasonal_features),
            "years": int(args.seasonal_years),
            "window_days": int(args.seasonal_window_days),
            "columns": SEASONAL_FEATURE_COLUMNS if args.include_seasonal_features else [],
        },
        "inputs": {
            "panel": str(args.panel),
            "snapshot_summary": str(args.snapshot_summary_path),
            "forecast_snapshot": str(args.forecast_snapshot_path),
            "actuals": str(args.actuals_path),
            "pdl_sku_features": str(args.pdl_sku_features_path),
            "promo_daily": str(args.promo_daily_path),
        },
        "caveats": [
            "This is stricter than the old sparse-panel ML score: future rows are generated without future actual demand.",
            "Target/order/inventory/supply lag features are frozen at latest prestart values across the 14-day horizon.",
            "Future PDL promotion features are included because planned promos should be knowable before the forecast starts.",
            "Optional same-season features use prior-year same-calendar windows only and do not expand the SKU universe.",
            "Training still uses the current model panel contract; point-in-time product attributes are improved only for generated holdout rows.",
        ],
    }

    scores.to_parquet(args.output_dir / "replacement_ml_backtest_window_scores.parquet", index=False, compression="zstd")
    scores.to_csv(args.output_dir / "replacement_ml_backtest_window_scores.csv", index=False)
    summary.to_csv(args.output_dir / "replacement_ml_backtest_candidate_summary.csv", index=False)
    winners.to_csv(args.output_dir / "replacement_ml_backtest_window_winners.csv", index=False)
    pd.DataFrame(metadata_rows).to_parquet(
        args.output_dir / "replacement_ml_backtest_candidate_metadata.parquet",
        index=False,
        compression="zstd",
    )
    (args.output_dir / "replacement_ml_backtest_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print("\nML guardrail candidate summary:")
    print(summary.to_string(index=False))
    print(f"Wrote ML guardrail outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
