"""Score replacement forecast candidates on an arbitrary recent shadow window.

Use this when Operations asks: "If we had generated the forecast then, how close
would it have been to what actually happened?"  Unlike the recovered corporate
snapshot backtest, this can score a start date that does not align with a saved
corporate file.  Corporate is included only when an exact snapshot start exists.
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

import ingestion_pipeline as ingestion  # noqa: E402
from forecast_model_compare_sklearn import require_sklearn, run_single_stage  # noqa: E402
from forecast_model_train import DEFAULT_PANEL_PATH, configure_threads, load_panel  # noqa: E402
from forecast_replacement_backtest import (  # noqa: E402
    ACTUALS_PATH,
    FORECAST_DAY_PATH,
    SNAPSHOT_SUMMARY_PATH,
    load_actuals,
)
from forecast_replacement_contract import (  # noqa: E402
    DEFAULT_LOOKBACK_DAYS,
    FD_COLUMNS,
    PDL_SKU_FEATURES_PATH,
    choose_source,
    normalize_sku_series,
)
from forecast_replacement_hybrid_candidate import (  # noqa: E402
    build_signal_summary,
    combine_daily_forecasts,
    recent_daily_forecast,
    selected_ml_daily,
    source_snapshot_attributes,
    source_universe,
)
from forecast_replacement_ml_backtest import (  # noqa: E402
    apply_recent_volume_cap,
    build_future_rows,
    load_daily_promotions,
    load_pdl_features,
    train_window,
)


FORECAST_ACCURACY_ROOT = Path(__file__).resolve().parents[2] / "Output" / "ForecastAccuracy"
DEFAULT_OUTPUT_DIR = FORECAST_ACCURACY_ROOT / "replacement_shadow"
DEFAULT_MODEL = "hgb_absolute_log"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score a recent shadow forecast window.")
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--actuals-path", type=Path, default=ACTUALS_PATH)
    parser.add_argument("--pdl-sku-features-path", type=Path, default=PDL_SKU_FEATURES_PATH)
    parser.add_argument("--forecast-day-path", type=Path, default=FORECAST_DAY_PATH)
    parser.add_argument("--snapshot-summary-path", type=Path, default=SNAPSHOT_SUMMARY_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--forecast-start-date", required=True)
    parser.add_argument("--forecast-days", type=int, default=14)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--model", choices=[DEFAULT_MODEL], default=DEFAULT_MODEL)
    parser.add_argument("--ml-threshold-units", type=float, default=20.0)
    parser.add_argument("--recent-fallback-weights", nargs="+", type=float, default=[0.05, 0.10])
    parser.add_argument("--recent-volume-caps", nargs="+", type=float, default=[])
    parser.add_argument(
        "--base-frozen-forecast-path",
        type=Path,
        help=(
            "Load an existing shadow_daily_forecasts.parquet as the base forecast "
            "instead of training/rebuilding candidates. Useful for non-destructive "
            "overlay experiments in a separate output directory."
        ),
    )
    parser.add_argument(
        "--include-yoy-sale-lift-overlay",
        action="store_true",
        help="Add a shadow-only July sale YoY DirectPick category-lift overlay candidate.",
    )
    parser.add_argument(
        "--yoy-direct-pick-history-path",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "Output"
        / "ForecastAccuracy"
        / "direct_pick_history"
        / "parquet"
        / "direct_pick_sku_day_modified_2025.parquet",
    )
    parser.add_argument("--yoy-analog-sale-start", default="2025-06-21")
    parser.add_argument("--yoy-analog-sale-end", default="2025-07-04")
    parser.add_argument("--yoy-analog-baseline-start", default="2025-05-24")
    parser.add_argument("--yoy-analog-baseline-end", default="2025-06-20")
    parser.add_argument(
        "--yoy-direct-pick-history-path-2024",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "Output"
        / "ForecastAccuracy"
        / "direct_pick_history"
        / "parquet"
        / "direct_pick_sku_day_modified_2024.parquet",
    )
    parser.add_argument("--yoy-analog-sale-start-2024", default="2024-06-18")
    parser.add_argument("--yoy-analog-sale-end-2024", default="2024-07-06")
    parser.add_argument("--yoy-analog-baseline-start-2024", default="2024-05-21")
    parser.add_argument("--yoy-analog-baseline-end-2024", default="2024-06-17")
    parser.add_argument("--yoy-weight-2025", type=float, default=0.6)
    parser.add_argument("--yoy-weight-2024", type=float, default=0.4)
    parser.add_argument("--yoy-current-baseline-days", type=int, default=56)
    parser.add_argument("--yoy-lift-floor", type=float, default=0.75)
    parser.add_argument("--yoy-lift-cap", type=float, default=3.0)
    parser.add_argument(
        "--yoy-total-cap-mode",
        choices=["overall-lift", "none"],
        default="overall-lift",
        help=(
            "overall-lift caps total overlay units at prior-sale overall lift times "
            "the current DirectPick baseline. none leaves category targets uncapped."
        ),
    )
    parser.add_argument(
        "--yoy-total-cap-units",
        type=float,
        help="Optional explicit absolute cap for the YoY overlay total units.",
    )
    parser.add_argument(
        "--yoy-shrink-units",
        type=float,
        default=500.0,
        help=(
            "Empirical Bayes shrinkage strength in baseline-expected category units. "
            "Higher values pull noisy category lifts toward the overall sale lift."
        ),
    )
    parser.add_argument(
        "--yoy-overlay-shape-candidate",
        default="hybrid_ml_raw_min20_recent_w0p1_cap_recent_x0p85",
        help="Preferred existing candidate used as the SKU/day allocation shape.",
    )
    parser.add_argument(
        "--yoy-overlay-candidate-name",
        default="july_sale_yoy_lift_overlay",
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
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--exclude-corporate-features", action="store_true", default=True)
    parser.add_argument("--include-product-identity-features", action="store_true", default=True)
    parser.add_argument(
        "--overwrite-frozen-forecast",
        action="store_true",
        default=False,
        help=(
            "Rebuild and overwrite shadow_daily_forecasts.parquet when it already exists. "
            "By default, an existing frozen forecast is reused and only score/metadata "
            "outputs are refreshed."
        ),
    )
    parser.add_argument(
        "--allow-partial-actuals",
        action="store_true",
        default=False,
        help=(
            "Allow the window to extend beyond available actuals. "
            "Forecasts are saved immediately; scoring is done on the available "
            "actual days only and WAPE/coverage will be partial until the window "
            "completes. Re-run with the same --forecast-start-date once the full "
            "window has actuals to obtain the final score."
        ),
    )
    return parser.parse_args()


def wide_to_daily(df_14day: pd.DataFrame, forecast_start: pd.Timestamp, candidate: str) -> pd.DataFrame:
    daily = df_14day.melt(
        id_vars=["SKU"],
        value_vars=[col for col in FD_COLUMNS if col in df_14day.columns],
        var_name="FD",
        value_name="ForecastUnits",
    )
    daily["ForecastDay"] = daily["FD"].str.replace("FD", "", regex=False).astype(int)
    daily["ForecastDate"] = forecast_start + pd.to_timedelta(daily["ForecastDay"] - 1, unit="D")
    daily["ForecastUnits"] = pd.to_numeric(
        daily["ForecastUnits"], errors="coerce"
    ).fillna(0).clip(lower=0)
    daily["Candidate"] = candidate
    return daily.loc[daily["ForecastUnits"].gt(0), ["Candidate", "SKU", "ForecastDate", "ForecastUnits"]]


def cap_daily_to_recent(
    daily: pd.DataFrame,
    recent_daily: pd.DataFrame,
    cap_multiple: float,
    candidate: str,
) -> pd.DataFrame:
    forecast = (
        daily.groupby("SKU", as_index=False)
        .agg(ForecastUnits=("ForecastUnits", "sum"))
        .sort_values("SKU", kind="mergesort")
    )
    recent = (
        recent_daily.groupby("SKU", as_index=False)
        .agg(ForecastUnits=("ForecastUnits", "sum"))
        .sort_values("SKU", kind="mergesort")
    )
    capped_sku = apply_recent_volume_cap(forecast, recent, cap_multiple)
    if capped_sku.empty:
        return daily.iloc[0:0].copy()
    scale = capped_sku.rename(columns={"ForecastUnits": "CappedSkuUnits"}).merge(
        forecast.rename(columns={"ForecastUnits": "OriginalSkuUnits"}),
        on="SKU",
        how="left",
    )
    scale["ScaleFactor"] = scale["CappedSkuUnits"] / scale["OriginalSkuUnits"].where(
        scale["OriginalSkuUnits"].ne(0),
        pd.NA,
    )
    capped = daily.merge(scale[["SKU", "ScaleFactor"]], on="SKU", how="inner")
    capped["ForecastUnits"] = capped["ForecastUnits"] * capped["ScaleFactor"].fillna(0)
    capped["Candidate"] = candidate
    return capped.loc[capped["ForecastUnits"].gt(0), ["Candidate", "SKU", "ForecastDate", "ForecastUnits"]]


def recent_to_daily(recent_daily: pd.DataFrame, candidate: str) -> pd.DataFrame:
    daily = recent_daily.rename(columns={"ForecastUnits": "ForecastUnits"}).copy()
    daily["ForecastUnits"] = pd.to_numeric(
        daily["ForecastUnits"], errors="coerce"
    ).fillna(0).clip(lower=0)
    daily["Candidate"] = candidate
    return daily.loc[daily["ForecastUnits"].gt(0), ["Candidate", "SKU", "ForecastDate", "ForecastUnits"]]


def load_latest_category_map(panel_path: Path) -> pd.DataFrame:
    columns = ["SKU", "Date", "Division", "Department", "Class", "KeyCategoryView"]
    if panel_path.is_dir():
        parquet_files = sorted(panel_path.glob("*.parquet"))
        panel = pd.concat([pd.read_parquet(f, columns=columns) for f in parquet_files], ignore_index=True)
    else:
        panel = pd.read_parquet(panel_path, columns=columns)
    panel["SKU"] = normalize_sku_series(panel["SKU"])
    panel["Date"] = pd.to_datetime(panel["Date"], errors="coerce")
    category_map = (
        panel.loc[panel["SKU"].ne("")]
        .sort_values(["SKU", "Date"], kind="mergesort")
        .drop_duplicates("SKU", keep="last")
        .drop(columns=["Date"])
    )
    for column in ["Division", "Department", "Class", "KeyCategoryView"]:
        category_map[column] = category_map[column].fillna("Unknown").astype(str)
    return category_map


def attach_category_map(frame: pd.DataFrame, category_map: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["SKU"] = normalize_sku_series(output["SKU"])
    output = output.merge(category_map, on="SKU", how="left")
    for column in ["Division", "Department", "Class", "KeyCategoryView"]:
        output[column] = output[column].fillna("Unknown").astype(str)
    return output


def category_columns() -> list[str]:
    return ["Division", "Department", "Class", "KeyCategoryView"]


def load_yoy_direct_pick(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing YoY DirectPick history: {path}")
    direct = pd.read_parquet(path)
    if {"PickDate", "PickUnits"}.issubset(direct.columns):
        direct = direct.rename(columns={"PickDate": "Date", "PickUnits": "Units"})
    elif {"ActualDate", "SoldUnits"}.issubset(direct.columns):
        direct = direct.rename(columns={"ActualDate": "Date", "SoldUnits": "Units"})
    else:
        raise ValueError(
            "YoY DirectPick history must contain PickDate/PickUnits or ActualDate/SoldUnits."
        )
    direct["Date"] = pd.to_datetime(direct["Date"], errors="coerce").dt.normalize()
    direct["SKU"] = normalize_sku_series(direct["SKU"])
    direct["Units"] = pd.to_numeric(direct["Units"], errors="coerce").fillna(0).clip(lower=0)
    return direct.loc[direct["Date"].notna() & direct["SKU"].ne(""), ["Date", "SKU", "Units"]]


def category_lift_table(
    direct: pd.DataFrame,
    category_map: pd.DataFrame,
    *,
    sale_start: pd.Timestamp,
    sale_end: pd.Timestamp,
    baseline_start: pd.Timestamp,
    baseline_end: pd.Timestamp,
    lift_floor: float,
    lift_cap: float,
    shrink_units: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    direct = attach_category_map(direct, category_map)
    sale = direct.loc[direct["Date"].between(sale_start, sale_end)].copy()
    baseline = direct.loc[direct["Date"].between(baseline_start, baseline_end)].copy()
    sale_days = max(int(sale["Date"].nunique()), 1)
    baseline_days = max(int(baseline["Date"].nunique()), 1)
    sale_units = float(sale["Units"].sum())
    baseline_expected_units = float(baseline["Units"].sum() / baseline_days * sale_days)
    overall_lift = sale_units / baseline_expected_units if baseline_expected_units else 1.0

    keys = category_columns()
    sale_cat = sale.groupby(keys, dropna=False)["Units"].sum().rename("AnalogSaleUnits")
    base_cat = baseline.groupby(keys, dropna=False)["Units"].sum().rename("AnalogBaselineUnits")
    lift = pd.concat([sale_cat, base_cat], axis=1).fillna(0).reset_index()
    lift["AnalogBaselineDailyUnits"] = lift["AnalogBaselineUnits"] / baseline_days
    lift["AnalogBaselineExpectedUnits"] = lift["AnalogBaselineDailyUnits"] * sale_days
    lift["RawCategoryLift"] = np.where(
        lift["AnalogBaselineExpectedUnits"].gt(0),
        lift["AnalogSaleUnits"] / lift["AnalogBaselineExpectedUnits"],
        overall_lift,
    )
    shrink_units = max(0.0, float(shrink_units))
    denominator = lift["AnalogBaselineExpectedUnits"] + shrink_units
    lift["ShrunkCategoryLift"] = np.where(
        denominator.gt(0),
        (
            lift["RawCategoryLift"] * lift["AnalogBaselineExpectedUnits"]
            + overall_lift * shrink_units
        )
        / denominator,
        overall_lift,
    )
    lift["AppliedCategoryLift"] = lift["ShrunkCategoryLift"].clip(
        lower=float(lift_floor),
        upper=float(lift_cap),
    )
    metadata = {
        "analog_sale_start": str(sale_start.date()),
        "analog_sale_end": str(sale_end.date()),
        "analog_sale_days": sale_days,
        "analog_sale_units": sale_units,
        "analog_baseline_start": str(baseline_start.date()),
        "analog_baseline_end": str(baseline_end.date()),
        "analog_baseline_days": baseline_days,
        "analog_baseline_expected_units": baseline_expected_units,
        "overall_lift": overall_lift,
        "lift_floor": float(lift_floor),
        "lift_cap": float(lift_cap),
        "shrink_units": shrink_units,
    }
    return lift[keys + ["AppliedCategoryLift", "RawCategoryLift", "ShrunkCategoryLift"]], metadata


def build_pdl_shape(
    pdl_path: Path,
    category_map: pd.DataFrame,
    forecast_start: pd.Timestamp,
    forecast_end: pd.Timestamp,
) -> pd.DataFrame:
    columns = ["Date", "SKU", "pdl_sku_lw_unit_sales", "pdl_sku_total_avail_inv"]
    if not pdl_path.exists():
        return pd.DataFrame(columns=["SKU", "ForecastDate", "ShapeUnits", *category_columns()])
    pdl = pd.read_parquet(pdl_path, columns=columns)
    pdl["ForecastDate"] = pd.to_datetime(pdl["Date"], errors="coerce").dt.normalize()
    pdl["SKU"] = normalize_sku_series(pdl["SKU"])
    pdl = pdl.loc[
        pdl["ForecastDate"].between(forecast_start, forecast_end) & pdl["SKU"].ne("")
    ].copy()
    if pdl.empty:
        return pd.DataFrame(columns=["SKU", "ForecastDate", "ShapeUnits", *category_columns()])
    pdl["ShapeUnits"] = (
        pd.to_numeric(pdl["pdl_sku_lw_unit_sales"], errors="coerce").fillna(0).clip(lower=0)
        / 7.0
    )
    # Keep promoted SKUs in the allocation universe even when last-week sales are zero.
    pdl["ShapeUnits"] = pdl["ShapeUnits"].where(pdl["ShapeUnits"].gt(0), 0.01)
    pdl = attach_category_map(pdl[["SKU", "ForecastDate", "ShapeUnits"]], category_map)
    return pdl


def add_yoy_sale_lift_overlay(
    forecasts: pd.DataFrame,
    actuals: pd.DataFrame,
    args: argparse.Namespace,
    forecast_start: pd.Timestamp,
    forecast_end: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    category_map = load_latest_category_map(args.panel)
    
    # 2025 Category Lift
    direct_2025 = load_yoy_direct_pick(args.yoy_direct_pick_history_path)
    lift_2025, lift_meta_2025 = category_lift_table(
        direct_2025,
        category_map,
        sale_start=pd.Timestamp(args.yoy_analog_sale_start).normalize(),
        sale_end=pd.Timestamp(args.yoy_analog_sale_end).normalize(),
        baseline_start=pd.Timestamp(args.yoy_analog_baseline_start).normalize(),
        baseline_end=pd.Timestamp(args.yoy_analog_baseline_end).normalize(),
        lift_floor=args.yoy_lift_floor,
        lift_cap=args.yoy_lift_cap,
        shrink_units=args.yoy_shrink_units,
    )
    
    # 2024 Category Lift
    direct_2024 = load_yoy_direct_pick(args.yoy_direct_pick_history_path_2024)
    lift_2024, lift_meta_2024 = category_lift_table(
        direct_2024,
        category_map,
        sale_start=pd.Timestamp(args.yoy_analog_sale_start_2024).normalize(),
        sale_end=pd.Timestamp(args.yoy_analog_sale_end_2024).normalize(),
        baseline_start=pd.Timestamp(args.yoy_analog_baseline_start_2024).normalize(),
        baseline_end=pd.Timestamp(args.yoy_analog_baseline_end_2024).normalize(),
        lift_floor=args.yoy_lift_floor,
        lift_cap=args.yoy_lift_cap,
        shrink_units=args.yoy_shrink_units,
    )
    
    # Blend Lifts
    keys = category_columns()
    blended = lift_2025[[*keys, "AppliedCategoryLift"]].rename(columns={"AppliedCategoryLift": "Lift2025"}).merge(
        lift_2024[[*keys, "AppliedCategoryLift"]].rename(columns={"AppliedCategoryLift": "Lift2024"}),
        on=keys,
        how="outer"
    )
    blended["Lift2025"] = blended["Lift2025"].fillna(lift_meta_2025["overall_lift"])
    blended["Lift2024"] = blended["Lift2024"].fillna(lift_meta_2024["overall_lift"])
    
    w_sum = args.yoy_weight_2025 + args.yoy_weight_2024
    w_2025 = args.yoy_weight_2025 / w_sum if w_sum else 0.5
    w_2024 = args.yoy_weight_2024 / w_sum if w_sum else 0.5
    
    blended["AppliedCategoryLift"] = blended["Lift2025"] * w_2025 + blended["Lift2024"] * w_2024
    overall_lift_blended = lift_meta_2025["overall_lift"] * w_2025 + lift_meta_2024["overall_lift"] * w_2024
    
    lift = blended[[*keys, "AppliedCategoryLift"]]
    lift_meta = {
        "overall_lift": overall_lift_blended,
        "weight_2025": w_2025,
        "weight_2024": w_2024,
        "lift_2025": lift_meta_2025,
        "lift_2024": lift_meta_2024,
    }

    baseline_start = forecast_start - pd.Timedelta(days=max(args.yoy_current_baseline_days, 1))
    current = actuals.loc[
        actuals["ActualDate"].between(baseline_start, forecast_start - pd.Timedelta(days=1))
    ].copy()
    current = current.rename(columns={"ActualDate": "Date", "SoldUnits": "Units"})
    current["Units"] = pd.to_numeric(current["Units"], errors="coerce").fillna(0).clip(lower=0)
    current = attach_category_map(current[["Date", "SKU", "Units"]], category_map)
    baseline_days = max(int(current["Date"].nunique()), 1)
    current_cat = (
        current.groupby(category_columns(), dropna=False)["Units"]
        .sum()
        .rename("CurrentBaselineUnits")
        .reset_index()
    )
    current_baseline_units = float(current["Units"].sum())
    current_cat["CurrentBaselineDailyUnits"] = current_cat["CurrentBaselineUnits"] / baseline_days
    current_cat = current_cat.merge(lift, on=category_columns(), how="left")
    current_cat["AppliedCategoryLift"] = current_cat["AppliedCategoryLift"].fillna(
        lift_meta["overall_lift"]
    )
    current_cat["TargetUnits"] = (
        current_cat["CurrentBaselineDailyUnits"]
        * args.forecast_days
        * current_cat["AppliedCategoryLift"]
    )

    base_candidates = [args.yoy_overlay_shape_candidate, "recent_no_ml_no_promo_floor"]
    shape_frames = []
    for candidate in base_candidates:
        shape = forecasts.loc[
            forecasts["Candidate"].eq(candidate),
            ["SKU", "ForecastDate", "ForecastUnits"],
        ].copy()
        if shape.empty:
            continue
        shape = shape.rename(columns={"ForecastUnits": "ShapeUnits"})
        shape_frames.append(shape)
    shape_frames.append(build_pdl_shape(args.pdl_sku_features_path, category_map, forecast_start, forecast_end))
    shape = pd.concat(shape_frames, ignore_index=True)
    shape["ShapeUnits"] = pd.to_numeric(shape["ShapeUnits"], errors="coerce").fillna(0).clip(lower=0)
    shape = shape.loc[shape["ShapeUnits"].gt(0)].copy()
    shape = attach_category_map(shape[["SKU", "ForecastDate", "ShapeUnits"]], category_map)
    shape = (
        shape.groupby(["SKU", "ForecastDate", *category_columns()], as_index=False)
        .agg(ShapeUnits=("ShapeUnits", "max"))
    )

    pdl_categories = build_pdl_shape(
        args.pdl_sku_features_path,
        category_map,
        forecast_start,
        forecast_end,
    )[category_columns()].drop_duplicates()
    target = current_cat.merge(pdl_categories, on=category_columns(), how="inner")
    target = target.loc[target["TargetUnits"].gt(0)].copy()

    base_total = (
        shape.groupby(category_columns(), as_index=False)["ShapeUnits"]
        .sum()
        .rename(columns={"ShapeUnits": "ShapeCategoryUnits"})
    )
    target = target.merge(base_total, on=category_columns(), how="left")
    target["ShapeCategoryUnits"] = target["ShapeCategoryUnits"].fillna(0)
    target["OverlayCategoryUnits"] = target[["TargetUnits", "ShapeCategoryUnits"]].max(axis=1)

    shape = shape.merge(
        target[category_columns() + ["OverlayCategoryUnits", "ShapeCategoryUnits"]],
        on=category_columns(),
        how="inner",
    )
    shape["ScaleFactor"] = np.where(
        shape["ShapeCategoryUnits"].gt(0),
        shape["OverlayCategoryUnits"] / shape["ShapeCategoryUnits"],
        0,
    )
    overlay = shape.copy()
    overlay["ForecastUnits"] = overlay["ShapeUnits"] * overlay["ScaleFactor"]
    overlay["Candidate"] = args.yoy_overlay_candidate_name
    overlay = overlay.loc[
        overlay["ForecastUnits"].gt(0),
        ["Candidate", "SKU", "ForecastDate", "ForecastUnits"],
    ]
    uncapped_overlay_units = float(overlay["ForecastUnits"].sum())
    overall_lift_cap_units = (
        current_baseline_units / baseline_days * args.forecast_days * lift_meta["overall_lift"]
        if baseline_days
        else np.nan
    )
    total_cap_units = args.yoy_total_cap_units
    if total_cap_units is None and args.yoy_total_cap_mode == "overall-lift":
        total_cap_units = overall_lift_cap_units
    total_cap_scale = 1.0
    if total_cap_units and total_cap_units > 0 and uncapped_overlay_units > total_cap_units:
        total_cap_scale = float(total_cap_units / uncapped_overlay_units)
        overlay["ForecastUnits"] = overlay["ForecastUnits"] * total_cap_scale
    metadata = {
        **lift_meta,
        "candidate": args.yoy_overlay_candidate_name,
        "shape_candidate": args.yoy_overlay_shape_candidate,
        "current_baseline_start": str(baseline_start.date()),
        "current_baseline_end": str((forecast_start - pd.Timedelta(days=1)).date()),
        "current_baseline_days": baseline_days,
        "current_baseline_units": current_baseline_units,
        "overall_lift_cap_units": float(overall_lift_cap_units)
        if pd.notna(overall_lift_cap_units)
        else None,
        "total_cap_mode": args.yoy_total_cap_mode,
        "total_cap_units": float(total_cap_units) if total_cap_units else None,
        "total_cap_scale": total_cap_scale,
        "target_categories": int(len(target)),
        "overlay_skus": int(overlay["SKU"].nunique()),
        "overlay_sku_days": int(len(overlay)),
        "overlay_units": float(overlay["ForecastUnits"].sum()),
        "uncapped_overlay_units": uncapped_overlay_units,
        "notes": (
            "Shadow-only sale-regime overlay. Category targets use prior-year "
            "DirectPick sale lift, capped/shrunk, then allocate onto current "
            "hybrid/recent/PDL SKU-day shape."
        ),
    }
    output = pd.concat(
        [forecasts.loc[~forecasts["Candidate"].eq(args.yoy_overlay_candidate_name)], overlay],
        ignore_index=True,
    )
    return output, metadata


def load_corporate_exact(
    summary_path: Path,
    forecast_day_path: Path,
    forecast_start: pd.Timestamp,
    forecast_end: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    summary = pd.read_parquet(summary_path)
    summary["ForecastStartDate"] = pd.to_datetime(summary["ForecastStartDate"], errors="coerce").dt.normalize()
    exact = summary.loc[summary["ForecastStartDate"].eq(forecast_start)].copy()
    if exact.empty:
        return pd.DataFrame(columns=["Candidate", "SKU", "ForecastDate", "ForecastUnits"]), {
            "status": "missing_exact_start",
        }
    snapshot = exact.sort_values("ForecastEndDate").iloc[-1]
    forecast = pd.read_parquet(
        forecast_day_path,
        columns=["SnapshotId", "SKU", "ForecastDate", "ForecastQty"],
        filters=[("SnapshotId", "=", snapshot["SnapshotId"])],
    )
    forecast["SKU"] = normalize_sku_series(forecast["SKU"])
    forecast["ForecastDate"] = pd.to_datetime(forecast["ForecastDate"], errors="coerce").dt.normalize()
    forecast["ForecastUnits"] = pd.to_numeric(
        forecast["ForecastQty"], errors="coerce"
    ).fillna(0).clip(lower=0)
    forecast = forecast.loc[
        forecast["SKU"].ne("")
        & forecast["ForecastDate"].between(forecast_start, forecast_end)
        & forecast["ForecastUnits"].gt(0)
    ].copy()
    forecast["Candidate"] = "corporate_exact_snapshot"
    metadata = {
        "status": "loaded",
        "snapshot_id": snapshot["SnapshotId"],
        "source_file": snapshot.get("SourceFile", ""),
        "complete_actual_window": bool(snapshot.get("CompleteActualWindow", False)),
    }
    return forecast[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]], metadata


def actual_daily(actuals: pd.DataFrame, forecast_start: pd.Timestamp, forecast_end: pd.Timestamp) -> pd.DataFrame:
    actual = actuals.loc[actuals["ActualDate"].between(forecast_start, forecast_end)].copy()
    actual = (
        actual.groupby(["SKU", "ActualDate"], as_index=False)
        .agg(SoldUnits=("SoldUnits", "sum"))
        .rename(columns={"ActualDate": "ForecastDate"})
    )
    return actual


def score_daily(forecast: pd.DataFrame, actual: pd.DataFrame, candidate: str) -> dict[str, Any]:
    candidate_forecast = forecast.loc[forecast["Candidate"].eq(candidate), ["SKU", "ForecastDate", "ForecastUnits"]]
    compare = candidate_forecast.merge(actual, on=["SKU", "ForecastDate"], how="outer")
    compare["ForecastUnits"] = pd.to_numeric(compare["ForecastUnits"], errors="coerce").fillna(0)
    compare["SoldUnits"] = pd.to_numeric(compare["SoldUnits"], errors="coerce").fillna(0)
    compare["AbsError"] = (compare["ForecastUnits"] - compare["SoldUnits"]).abs()
    sold_units = float(compare["SoldUnits"].sum())
    forecast_units = float(compare["ForecastUnits"].sum())
    zero_forecast_sold_units = float(
        compare.loc[compare["SoldUnits"].gt(0) & compare["ForecastUnits"].eq(0), "SoldUnits"].sum()
    )
    sold_units_with_forecast = float(
        compare.loc[compare["SoldUnits"].gt(0) & compare["ForecastUnits"].gt(0), "SoldUnits"].sum()
    )
    return {
        "Candidate": candidate,
        "ForecastRows": int(candidate_forecast["SKU"].nunique()),
        "ForecastSkuDays": int(len(candidate_forecast)),
        "SoldSkuDays": int(compare["SoldUnits"].gt(0).sum()),
        "ForecastUnits": forecast_units,
        "SoldUnits": sold_units,
        "AbsErrorUnits": float(compare["AbsError"].sum()),
        "WAPE": float(compare["AbsError"].sum() / sold_units) if sold_units else pd.NA,
        "BiasPct": (forecast_units - sold_units) / sold_units if sold_units else pd.NA,
        "SoldUnitCoveragePct": sold_units_with_forecast / sold_units if sold_units else pd.NA,
        "ZeroForecastSoldUnitPct": zero_forecast_sold_units / sold_units if sold_units else pd.NA,
        "ZeroForecastSoldUnits": zero_forecast_sold_units,
    }


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, str(args.threads))

    forecast_start = pd.Timestamp(args.forecast_start_date).normalize()
    forecast_end = forecast_start + pd.Timedelta(days=args.forecast_days - 1)
    source_file = choose_source(args.source_file)
    output_dir = args.output_dir / f"shadow_{forecast_start:%Y-%m-%d}_{forecast_end:%Y-%m-%d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    forecast_path = output_dir / "shadow_daily_forecasts.parquet"
    metadata_path = output_dir / "shadow_metadata.json"

    print(f"Shadow window: {forecast_start.date()} through {forecast_end.date()}", flush=True)
    actuals = load_actuals(args.actuals_path)
    available_actual_max = actuals["ActualDate"].max()
    if available_actual_max < forecast_end:
        if not args.allow_partial_actuals:
            raise RuntimeError(
                f"Actuals are only available through {available_actual_max.date()}, "
                f"but requested shadow window ends {forecast_end.date()}. "
                f"Pass --allow-partial-actuals to generate frozen forecasts now "
                f"and score against available actuals only."
            )
        print(
            f"WARNING: Actuals available through {available_actual_max.date()} only "
            f"(window ends {forecast_end.date()}). Scoring will be partial; "
            f"re-run after {forecast_end.date()} for the final score.",
            flush=True,
        )

    loaded_external_frozen_forecast = bool(args.base_frozen_forecast_path)
    if loaded_external_frozen_forecast and not args.base_frozen_forecast_path.exists():
        raise FileNotFoundError(f"Missing base frozen forecast: {args.base_frozen_forecast_path}")
    reused_frozen_forecast = (
        forecast_path.exists() and not args.overwrite_frozen_forecast and not loaded_external_frozen_forecast
    )
    prior_metadata: dict[str, Any] = {}
    if reused_frozen_forecast and metadata_path.exists():
        prior_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if loaded_external_frozen_forecast:
        print(
            f"Loading external frozen forecast from {args.base_frozen_forecast_path}; "
            f"writing refreshed outputs to {output_dir}.",
            flush=True,
        )
        forecasts = pd.read_parquet(args.base_frozen_forecast_path)
        train = calibration = future = pd.DataFrame()
        future_meta = {"status": "loaded_external_frozen_forecast"}
        recent_meta = {"status": "loaded_external_frozen_forecast"}
        corporate_meta = {"status": "loaded_external_frozen_forecast"}
    elif reused_frozen_forecast:
        print(
            f"Reusing frozen forecast at {forecast_path}; refreshing score/metadata only. "
            "Pass --overwrite-frozen-forecast to rebuild it.",
            flush=True,
        )
        forecasts = pd.read_parquet(forecast_path)
        train = calibration = future = pd.DataFrame()
        future_meta: dict[str, Any] = prior_metadata.get(
            "future_inputs",
            {"status": "reused_frozen_forecast"},
        )
        recent_meta: dict[str, Any] = prior_metadata.get(
            "recent_direct_pick",
            {"status": "reused_frozen_forecast"},
        )
        corporate_meta: dict[str, Any] = prior_metadata.get(
            "corporate",
            {"status": "reused_frozen_forecast"},
        )
    else:
        df_hier, _ = ingestion.read_product_attributes(source_file)
        source_weekly, _, _ = ingestion.read_weekly_forecast(source_file)
        source_14day, _ = ingestion.read_14day_forecast(source_file)
        universe = source_universe(source_weekly, source_14day, df_hier)
        attrs = source_snapshot_attributes(df_hier, universe)

        panel = load_panel(args.panel, args.start_date)
        daily_promo = load_daily_promotions(
            args.pdl_sku_features_path.parent / "combined_daily_promo_features.parquet",
            forecast_start,
            forecast_end,
        )
        pdl_horizon = load_pdl_features(args.pdl_sku_features_path, forecast_start, forecast_end)
        train, calibration = train_window(panel, args, forecast_start)
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
        ml = require_sklearn()
        scored, _factors = run_single_stage(args.model, ml, train, calibration, future, args)
        raw_col = f"{args.model}ForecastQty"
        ml_daily, ml_totals = selected_ml_daily(scored, raw_col, args.ml_threshold_units)
        recent_daily, recent_meta = recent_daily_forecast(actuals, forecast_start, args.lookback_days)

        forecast_frames = [recent_to_daily(recent_daily, "recent_no_ml_no_promo_floor")]
        for weight in args.recent_fallback_weights:
            df_14day = combine_daily_forecasts(ml_daily, recent_daily, forecast_start, weight)
            candidate = f"hybrid_ml_raw_min20_recent_w{str(weight).replace('.', 'p')}"
            hybrid_daily = wide_to_daily(df_14day, forecast_start, candidate)
            forecast_frames.append(hybrid_daily)
            for cap_multiple in args.recent_volume_caps:
                cap_candidate = (
                    f"{candidate}_cap_recent_x{str(float(cap_multiple)).replace('.', 'p')}"
                )
                forecast_frames.append(
                    cap_daily_to_recent(hybrid_daily, recent_daily, cap_multiple, cap_candidate)
                )
            signal = build_signal_summary(df_14day, ml_totals, recent_daily, args.ml_threshold_units, weight)
            signal.to_csv(output_dir / f"{candidate}_signal_sku_summary.csv", index=False)
        corporate, corporate_meta = load_corporate_exact(
            args.snapshot_summary_path,
            args.forecast_day_path,
            forecast_start,
            forecast_end,
        )
        if not corporate.empty:
            forecast_frames.append(corporate)

        forecasts = pd.concat(forecast_frames, ignore_index=True)

    yoy_overlay_meta: dict[str, Any] | None = None
    if args.include_yoy_sale_lift_overlay:
        forecasts, yoy_overlay_meta = add_yoy_sale_lift_overlay(
            forecasts,
            actuals,
            args,
            forecast_start,
            forecast_end,
        )

    if (
        not reused_frozen_forecast
        or loaded_external_frozen_forecast
        or args.include_yoy_sale_lift_overlay
    ):
        forecasts.to_parquet(forecast_path, index=False, compression="zstd")

    actual = actual_daily(actuals, forecast_start, forecast_end)
    scores = pd.DataFrame(
        [score_daily(forecasts, actual, candidate) for candidate in sorted(forecasts["Candidate"].unique())]
    ).sort_values(["WAPE", "BiasPct"])
    scores.to_csv(output_dir / "shadow_score_summary.csv", index=False)
    metadata = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "forecast_start": str(forecast_start.date()),
        "forecast_end": str(forecast_end.date()),
        "partial_actuals": available_actual_max < forecast_end,
        "actuals_available_through": str(available_actual_max.date()),
        "reused_frozen_forecast": reused_frozen_forecast,
        "loaded_external_frozen_forecast": loaded_external_frozen_forecast,
        "base_frozen_forecast_path": str(args.base_frozen_forecast_path)
        if args.base_frozen_forecast_path
        else "",
        "forecast_path": str(forecast_path),
        "frozen_forecast_generated_at": prior_metadata.get("generated_at") if reused_frozen_forecast else None,
        "threads_requested": args.threads,
        "model": args.model,
        "ml_threshold_units": args.ml_threshold_units,
        "recent_fallback_weights": args.recent_fallback_weights,
        "recent_volume_caps": args.recent_volume_caps,
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "future_rows": int(len(future)),
        "future_inputs": future_meta,
        "recent_direct_pick": recent_meta,
        "corporate": corporate_meta,
        "yoy_sale_lift_overlay": yoy_overlay_meta,
        "score_grain": "SKU/day",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(scores.to_string(index=False))
    print(f"Wrote shadow outputs to {output_dir}")


if __name__ == "__main__":
    main()
