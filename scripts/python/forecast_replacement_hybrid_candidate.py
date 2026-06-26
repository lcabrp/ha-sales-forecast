"""Build a BRG-like candidate package from the conservative hybrid ML forecast.

This is the first bridge from the replacement scoreboard to an ingestion-ready
workbook.  The 14-day forecast uses the future-safe ML guardrail policy:

- train raw hgb_absolute_log without corporate forecast features;
- keep ML SKU rows only when the 14-day SKU total is at least a threshold;
- add a small recent-demand fallback for SKUs below that threshold.

The weekly tab is a conservative continuation used for ingestion rehearsal and
slotting impact review.  It should be revisited before production handoff.
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

# Add parent directory to system path to import modules
PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import ingestion_pipeline as ingestion  # noqa: E402
from forecast_model_compare_sklearn import require_sklearn, run_single_stage  # noqa: E402
from forecast_model_train import (  # noqa: E402
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    configure_threads,
    load_panel,
)
from forecast_replacement_backtest import direct_pick_signal, load_actuals  # noqa: E402
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
    forecast_week_dates,
    integerize_daily_forecast,
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

# Standard parameter defaults for candidate builder options
DEFAULT_CANDIDATE_TYPE = "hybrid_ml_baseline"
DEFAULT_MODEL = "hgb_absolute_log"
DEFAULT_THRESHOLD = 20.0
DEFAULT_RECENT_FALLBACK_WEIGHT = 0.10
DEFAULT_WEEKLY_TAIL_SCALE = 0.50
DEFAULT_OUTPUT_DIR_NAME = "replacement_contract"
DEFAULT_PLANNER_DAILY_PATH = DEFAULT_OUTPUT_ROOT.parent / "planner" / "planner_daily_totals_2026.parquet"
PLANNER_ANCHOR_COLUMN = "ops_imf_plan_forecasted_units"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the hybrid candidate generator.

    Returns:
        argparse.Namespace: Populated argument namespaces.
    """
    parser = argparse.ArgumentParser(description="Build a hybrid ML BRG-like candidate package.")
    parser.add_argument(
        "--source-file",
        type=Path,
        help="Path to the source planning workbook Excel file.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory where candidates are exported.",
    )
    parser.add_argument(
        "--candidate-id",
        help="Unique candidate directory identifier.",
    )
    parser.add_argument(
        "--forecast-start-date",
        help="Start date of the forecast horizon. Defaults to workbook date if omitted.",
    )
    parser.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PANEL_PATH,
        help="Path to panel data parts.",
    )
    parser.add_argument(
        "--actuals-path",
        type=Path,
        default=ACTUALS_PATH,
        help="Path to historical actuals parquet.",
    )
    parser.add_argument(
        "--pdl-sku-features-path",
        type=Path,
        default=PDL_SKU_FEATURES_PATH,
        help="Path to PDL/promotional SKU features.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="Days lookback to calculate baseline actuals velocity.",
    )
    parser.add_argument(
        "--model",
        choices=[DEFAULT_MODEL],
        default=DEFAULT_MODEL,
        help="ML model architecture name.",
    )
    parser.add_argument(
        "--ml-threshold-units",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="Unit threshold below which the model is bypassed in favor of fallback.",
    )
    parser.add_argument(
        "--recent-fallback-weight",
        type=float,
        default=DEFAULT_RECENT_FALLBACK_WEIGHT,
        help="Fraction weight applied to fallbacks for low-volume SKUs.",
    )
    parser.add_argument(
        "--recent-volume-cap",
        type=float,
        help="Optional cap for FD1-FD14 total units as a multiple of recent no-ML forecast units.",
    )
    parser.add_argument(
        "--planner-daily-path",
        type=Path,
        default=DEFAULT_PLANNER_DAILY_PATH,
        help="Path to daily planner anchor units file.",
    )
    parser.add_argument(
        "--planner-total-anchor",
        choices=["none", "ops_imf"],
        default="none",
        help="Optionally scale each FD day to a Planner daily total before writing the BRG-like workbook.",
    )
    parser.add_argument(
        "--planner-total-scale",
        type=float,
        default=1.0,
        help="Multiplier applied to Planner daily totals, e.g. 0.95 for a conservative volume posture.",
    )
    parser.add_argument(
        "--weekly-tail-scale",
        type=float,
        default=DEFAULT_WEEKLY_TAIL_SCALE,
        help="Weekly tail scaling multiplier for outer weeks (W3-W13).",
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
    parser.add_argument("--sample-rows", type=int, default=1000)
    return parser.parse_args()


def parse_size_from_sku(series: pd.Series) -> pd.Series:
    """Extract sizing suffix codes from a standard SKU string series.

    Args:
        series (pd.Series): Series of SKU string identifiers.

    Returns:
        pd.Series: Suffix size code series.
    """
    parts = series.fillna("").astype(str).str.rsplit("-", n=1, expand=True)
    if isinstance(parts, pd.DataFrame) and parts.shape[1] > 1:
        return parts[1].fillna("").astype(str)
    return pd.Series("", index=series.index)


def source_universe(
    source_weekly: pd.DataFrame,
    source_14day: pd.DataFrame,
    df_hier: pd.DataFrame,
) -> pd.DataFrame:
    """Consolidate the union of all active SKUs across source sheets.

    Args:
        source_weekly (pd.DataFrame): Weekly forecast sheet.
        source_14day (pd.DataFrame): 14-day daily forecast sheet.
        df_hier (pd.DataFrame): Product hierarchy attributes sheet.

    Returns:
        pd.DataFrame: Distinct set of normalized SKU values.
    """
    frames = [
        source_weekly[["SKU"]],
        source_14day[["SKU"]],
        df_hier[["SKU"]],
    ]
    universe = pd.concat(frames, ignore_index=True)
    universe["SKU"] = normalize_sku_series(universe["SKU"])
    return universe.loc[universe["SKU"].ne("")].drop_duplicates("SKU")


def source_snapshot_attributes(df_hier: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    """Rebuild product attributes and slotting tiers for the consolidated SKU universe.

    Args:
        df_hier (pd.DataFrame): Reference hierarchy attributes.
        universe (pd.DataFrame): Targeted SKU universe.

    Returns:
        pd.DataFrame: Standardized product attributes dataframe.
    """
    attrs = universe.copy()
    hier = df_hier.copy()
    hier["SKU"] = normalize_sku_series(hier["SKU"])
    hier = hier.drop_duplicates("SKU", keep="last")
    attrs = attrs.merge(hier, on="SKU", how="left")
    attrs["SnapshotId"] = "current_source_workbook"
    for col in ["Division", "Department", "Class", "KeyCategoryView", "Item", "Color"]:
        if col not in attrs.columns:
            attrs[col] = ""
        attrs[col] = attrs[col].fillna("").astype(str)
    attrs["Size"] = parse_size_from_sku(attrs["SKU"])
    if "ProductGroupCode" not in attrs.columns:
        attrs["ProductGroupCode"] = attrs["Item"]
    if "SizeGroupCode" not in attrs.columns:
        attrs["SizeGroupCode"] = attrs["SizeGroup"] if "SizeGroup" in attrs.columns else ""
    if "Velocity" not in attrs.columns:
        attrs["Velocity"] = ""
    if "SlotTier" not in attrs.columns:
        attrs["SlotTier"] = ""
    for col in ["ProductGroupCode", "SizeGroupCode", "Velocity", "SlotTier"]:
        attrs[col] = attrs[col].fillna("").astype(str)
    return attrs[
        [
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
    ].drop_duplicates(["SnapshotId", "SKU"], keep="last")


def recent_daily_forecast(
    actuals: pd.DataFrame,
    forecast_start: pd.Timestamp,
    lookback_days: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Determine recent base demand and apply day-of-week scaling indexes.

    Args:
        actuals (pd.DataFrame): Historical transaction records.
        forecast_start (pd.Timestamp): Start date of the 14-day horizon.
        lookback_days (int): Baseline history lookup window.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: A long SKU-day forecast and metadata stats.
    """
    direct, factors, metadata = direct_pick_signal(actuals, forecast_start, lookback_days)
    if direct.empty:
        return pd.DataFrame(columns=["SKU", "ForecastDay", "ForecastDate", "ForecastUnits"]), metadata
    frames = []
    for day_idx in range(1, 15):
        forecast_date = forecast_start + pd.Timedelta(days=day_idx - 1)
        frame = direct[["SKU", "DailyBaseUnits"]].copy()
        frame["ForecastDay"] = day_idx
        frame["ForecastDate"] = forecast_date
        frame["ForecastUnits"] = (
            frame["DailyBaseUnits"] * factors.get(int(forecast_date.dayofweek), 1.0)
        )
        frames.append(frame[["SKU", "ForecastDay", "ForecastDate", "ForecastUnits"]])
    return pd.concat(frames, ignore_index=True), metadata


def selected_ml_daily(
    scored: pd.DataFrame,
    forecast_col: str,
    threshold: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filter out ML predictions below an aggregate 14-day threshold.

    SKUs with forecast volumes below this limit are pushed to fallback.

    Args:
        scored (pd.DataFrame): Raw ML predictions.
        forecast_col (str): Target prediction column name.
        threshold (float): Cumulative unit requirement over the 14-day horizon.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: Sliced daily records, and total forecast per SKU.
    """
    daily = scored[["SKU", DATE_COLUMN, forecast_col]].copy()
    daily = daily.rename(columns={DATE_COLUMN: "ForecastDate", forecast_col: "ForecastUnitsRaw"})
    daily["ForecastUnitsRaw"] = pd.to_numeric(
        daily["ForecastUnitsRaw"], errors="coerce"
    ).fillna(0).clip(lower=0)
    totals = (
        daily.groupby("SKU", as_index=False)
        .agg(ML14DayForecastUnits=("ForecastUnitsRaw", "sum"))
    )
    keep_skus = set(totals.loc[totals["ML14DayForecastUnits"].ge(threshold), "SKU"])
    daily = daily.loc[daily["SKU"].isin(keep_skus)].copy()
    return daily, totals


def combine_daily_forecasts(
    ml_daily: pd.DataFrame,
    recent_daily: pd.DataFrame,
    forecast_start: pd.Timestamp,
    fallback_weight: float,
) -> pd.DataFrame:
    """Blend high-volume ML predictions with scaled fallback forecasts for low-volume SKUs.

    Args:
        ml_daily (pd.DataFrame): Active ML daily forecasts.
        recent_daily (pd.DataFrame): Baseline recent daily actuals.
        forecast_start (pd.Timestamp): Start date of the window.
        fallback_weight (float): Scalar applied to low-volume fallback units.

    Returns:
        pd.DataFrame: Pivot-table wide dataframe (FD1 to FD14).
    """
    selected_skus = set(ml_daily["SKU"].dropna().astype(str))
    fallback = recent_daily.loc[~recent_daily["SKU"].astype(str).isin(selected_skus)].copy()
    fallback["ForecastUnitsRaw"] = (
        pd.to_numeric(fallback["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
        * fallback_weight
    )
    fallback = fallback[["SKU", "ForecastDate", "ForecastUnitsRaw"]]
    combined = pd.concat(
        [ml_daily[["SKU", "ForecastDate", "ForecastUnitsRaw"]], fallback],
        ignore_index=True,
    )
    combined["ForecastDay"] = (combined["ForecastDate"] - forecast_start).dt.days + 1
    combined = combined.loc[combined["ForecastDay"].between(1, 14)].copy()
    combined = integerize_daily_forecast(combined)
    daily = (
        combined.pivot_table(
            index="SKU",
            columns="ForecastDay",
            values="ForecastUnits",
            aggfunc="sum",
            fill_value=0,
        )
        .rename(columns={idx: f"FD{idx}" for idx in range(1, 15)})
        .reset_index()
    )
    for col in FD_COLUMNS:
        if col not in daily.columns:
            daily[col] = 0
        daily[col] = pd.to_numeric(daily[col], errors="coerce").fillna(0).astype(int)
    return daily[["SKU", *FD_COLUMNS]].sort_values("SKU", kind="mergesort")


def cap_14day_to_recent(
    df_14day: pd.DataFrame,
    recent_daily: pd.DataFrame,
    forecast_start: pd.Timestamp,
    cap_multiple: float | None,
) -> pd.DataFrame:
    """Scale down the forecast if it exceeds recent baseline demand by a given threshold.

    This prevents anomalous spikes in the ML forecasts from driving excessive inventory allocations.

    Args:
        df_14day (pd.DataFrame): Wide format daily forecasts (FD1-FD14).
        recent_daily (pd.DataFrame): Long format recent daily baseline forecasts.
        forecast_start (pd.Timestamp): Start date of the forecast window.
        cap_multiple (float | None): Maximum allowed scale factor.

    Returns:
        pd.DataFrame: Scaled (or untouched) daily forecast wide-format records.
    """
    if cap_multiple is None or cap_multiple <= 0:
        return df_14day
    forecast_units = float(df_14day[FD_COLUMNS].sum().sum())
    recent_units = float(pd.to_numeric(recent_daily["ForecastUnits"], errors="coerce").fillna(0).sum())
    cap_units = recent_units * float(cap_multiple)
    if forecast_units <= 0 or forecast_units <= cap_units:
        return df_14day
    scale = cap_units / forecast_units
    daily_long = df_14day.melt(
        id_vars=["SKU"],
        value_vars=FD_COLUMNS,
        var_name="FD",
        value_name="ForecastUnitsRaw",
    )
    daily_long["ForecastDay"] = daily_long["FD"].str.replace("FD", "", regex=False).astype(int)
    daily_long["ForecastDate"] = forecast_start + pd.to_timedelta(daily_long["ForecastDay"] - 1, unit="D")
    daily_long["ForecastUnitsRaw"] = (
        pd.to_numeric(daily_long["ForecastUnitsRaw"], errors="coerce").fillna(0).clip(lower=0)
        * scale
    )
    daily_long = integerize_daily_forecast(daily_long)
    capped = (
        daily_long.pivot_table(
            index="SKU",
            columns="ForecastDay",
            values="ForecastUnits",
            aggfunc="sum",
            fill_value=0,
        )
        .rename(columns={idx: f"FD{idx}" for idx in range(1, 15)})
        .reset_index()
    )
    for col in FD_COLUMNS:
        if col not in capped.columns:
            capped[col] = 0
        capped[col] = pd.to_numeric(capped[col], errors="coerce").fillna(0).astype(int)
    return capped[["SKU", *FD_COLUMNS]].sort_values("SKU", kind="mergesort")


def integerize_by_forecast_day(daily: pd.DataFrame) -> pd.DataFrame:
    """Perform deterministic largest remainder allocation day-by-day.

    Args:
        daily (pd.DataFrame): Long format forecast records containing ForecastDay and ForecastUnitsRaw.

    Returns:
        pd.DataFrame: Enriched long format dataframe with integerized ForecastUnits.
    """
    pieces = []
    for _, group in daily.sort_values(["ForecastDay", "SKU"], kind="mergesort").groupby("ForecastDay", sort=False):
        work = group.copy()
        raw = pd.to_numeric(work["ForecastUnitsRaw"], errors="coerce").fillna(0).clip(lower=0)
        floors = raw.apply(np.floor).astype(int)
        target_total = int(round(float(raw.sum())))
        target_total = max(target_total, int(floors.sum()))
        extra = target_total - int(floors.sum())
        work["ForecastUnits"] = floors
        if extra > 0:
            remainder_order = (
                pd.DataFrame(
                    {
                        "index": work.index,
                        "Remainder": raw - floors,
                        "SKU": work["SKU"].to_numpy(),
                    }
                )
                .sort_values(["Remainder", "SKU"], ascending=[False, True])
                .head(extra)
            )
            work.loc[remainder_order["index"], "ForecastUnits"] += 1
        pieces.append(work)
    if not pieces:
        daily["ForecastUnits"] = pd.Series(dtype="int64")
        return daily
    return pd.concat(pieces, ignore_index=True)


def apply_planner_daily_anchor(
    df_14day: pd.DataFrame,
    planner_path: Path,
    forecast_start: pd.Timestamp,
    total_scale: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Rescale each day's cumulative unit volume to match target Planner daily anchors.

    Args:
        df_14day (pd.DataFrame): Forecast wide records.
        planner_path (Path): Path to daily planner values dataset.
        forecast_start (pd.Timestamp): Start date of forecast.
        total_scale (float): Scale multiplier.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Rescaled daily forecasts and execution metadata.
    """
    if not planner_path.exists():
        raise FileNotFoundError(f"Planner daily totals not found: {planner_path}")
    planner = pd.read_parquet(planner_path) if planner_path.suffix.lower() == ".parquet" else pd.read_csv(planner_path)
    if "Date" not in planner.columns or PLANNER_ANCHOR_COLUMN not in planner.columns:
        raise ValueError(f"Planner file must contain Date and {PLANNER_ANCHOR_COLUMN}: {planner_path}")

    planner = planner[["Date", PLANNER_ANCHOR_COLUMN]].copy()
    planner["ForecastDate"] = pd.to_datetime(planner["Date"], errors="coerce").dt.normalize()
    planner["PlannerDailyTargetUnits"] = (
        pd.to_numeric(planner[PLANNER_ANCHOR_COLUMN], errors="coerce").fillna(0).clip(lower=0)
        * max(0.0, float(total_scale))
    )
    horizon_end = forecast_start + pd.Timedelta(days=13)
    planner = planner.loc[planner["ForecastDate"].between(forecast_start, horizon_end)].copy()

    daily = df_14day.melt(
        id_vars=["SKU"],
        value_vars=FD_COLUMNS,
        var_name="FD",
        value_name="ForecastUnitsRaw",
    )
    daily["ForecastDay"] = daily["FD"].str.replace("FD", "", regex=False).astype(int)
    daily["ForecastDate"] = forecast_start + pd.to_timedelta(daily["ForecastDay"] - 1, unit="D")
    daily["ForecastUnitsRaw"] = pd.to_numeric(daily["ForecastUnitsRaw"], errors="coerce").fillna(0).clip(lower=0)
    current = daily.groupby("ForecastDate", as_index=False).agg(CurrentDailyUnits=("ForecastUnitsRaw", "sum"))
    daily = daily.merge(planner[["ForecastDate", "PlannerDailyTargetUnits"]], on="ForecastDate", how="left")
    daily = daily.merge(current, on="ForecastDate", how="left")

    daily["PlannerScaleFactor"] = 1.0
    can_scale = (
        daily["PlannerDailyTargetUnits"].notna()
        & daily["PlannerDailyTargetUnits"].gt(0)
        & daily["CurrentDailyUnits"].gt(0)
    )
    daily.loc[can_scale, "PlannerScaleFactor"] = (
        daily.loc[can_scale, "PlannerDailyTargetUnits"]
        / daily.loc[can_scale, "CurrentDailyUnits"]
    )
    daily["ForecastUnitsRaw"] = daily["ForecastUnitsRaw"] * daily["PlannerScaleFactor"]
    daily = integerize_by_forecast_day(daily)

    anchored = (
        daily.pivot_table(
            index="SKU",
            columns="ForecastDay",
            values="ForecastUnits",
            aggfunc="sum",
            fill_value=0,
        )
        .rename(columns={idx: f"FD{idx}" for idx in range(1, 15)})
        .reset_index()
    )
    for col in FD_COLUMNS:
        if col not in anchored.columns:
            anchored[col] = 0
        anchored[col] = pd.to_numeric(anchored[col], errors="coerce").fillna(0).astype(int)

    after_daily = anchored.melt(
        id_vars=["SKU"],
        value_vars=FD_COLUMNS,
        var_name="FD",
        value_name="ForecastUnits",
    )
    after_daily["ForecastDay"] = after_daily["FD"].str.replace("FD", "", regex=False).astype(int)
    after_daily["ForecastDate"] = forecast_start + pd.to_timedelta(after_daily["ForecastDay"] - 1, unit="D")
    after_totals = after_daily.groupby("ForecastDate", as_index=False).agg(
        AnchoredDailyUnits=("ForecastUnits", "sum")
    )
    meta_daily = (
        current.merge(planner[["ForecastDate", "PlannerDailyTargetUnits"]], on="ForecastDate", how="outer")
        .merge(after_totals, on="ForecastDate", how="outer")
        .sort_values("ForecastDate", kind="mergesort")
    )
    metadata = {
        "planner_path": str(planner_path),
        "planner_column": PLANNER_ANCHOR_COLUMN,
        "planner_total_scale": float(total_scale),
        "pre_anchor_units": float(meta_daily["CurrentDailyUnits"].fillna(0).sum()),
        "planner_target_units": float(meta_daily["PlannerDailyTargetUnits"].fillna(0).sum()),
        "post_anchor_units": float(meta_daily["AnchoredDailyUnits"].fillna(0).sum()),
        "daily_totals": [
            {
                "ForecastDate": pd.Timestamp(row.ForecastDate).date().isoformat(),
                "CurrentDailyUnits": float(row.CurrentDailyUnits) if pd.notna(row.CurrentDailyUnits) else None,
                "PlannerDailyTargetUnits": float(row.PlannerDailyTargetUnits)
                if pd.notna(row.PlannerDailyTargetUnits)
                else None,
                "AnchoredDailyUnits": float(row.AnchoredDailyUnits) if pd.notna(row.AnchoredDailyUnits) else None,
            }
            for row in meta_daily.itertuples(index=False)
        ],
        "missing_planner_target_dates": [
            pd.Timestamp(value).date().isoformat()
            for value in meta_daily.loc[meta_daily["PlannerDailyTargetUnits"].isna(), "ForecastDate"]
        ],
        "zero_current_with_planner_target_dates": [
            pd.Timestamp(value).date().isoformat()
            for value in meta_daily.loc[
                meta_daily["PlannerDailyTargetUnits"].fillna(0).gt(0)
                & meta_daily["CurrentDailyUnits"].fillna(0).le(0),
                "ForecastDate",
            ]
        ],
    }
    return anchored[["SKU", *FD_COLUMNS]].sort_values("SKU", kind="mergesort"), metadata


def build_weekly_from_daily(
    df_14day: pd.DataFrame,
    recent_daily: pd.DataFrame,
    forecast_start: pd.Timestamp,
    weekly_tail_scale: float,
) -> tuple[pd.DataFrame, list[pd.Timestamp], dict[str, Any]]:
    """Convert daily forecast records into standard weekly buckets.

    Extrapolates outer weeks W3 to W13 using a conservative minimum of recent demand
    and candidate W1-W2 average, scaled down by weekly_tail_scale.

    Args:
        df_14day (pd.DataFrame): Daily forecast records (FD1-FD14).
        recent_daily (pd.DataFrame): Baseline recent daily actuals.
        forecast_start (pd.Timestamp): Start date of forecast.
        weekly_tail_scale (float): Scalar applied to W3-W13 forecasts.

    Returns:
        tuple[pd.DataFrame, list[pd.Timestamp], dict[str, Any]]: Weekly dataframe, week dates, and metadata.
    """
    week_dates = forecast_week_dates(forecast_start)
    daily_long = df_14day.melt(
        id_vars=["SKU"],
        value_vars=FD_COLUMNS,
        var_name="FD",
        value_name="ForecastUnits",
    )
    daily_long["ForecastDay"] = daily_long["FD"].str.replace("FD", "", regex=False).astype(int)
    daily_long["ForecastDate"] = forecast_start + pd.to_timedelta(daily_long["ForecastDay"] - 1, unit="D")
    daily_long["WeekStartDate"] = daily_long["ForecastDate"].apply(
        lambda value: max([week for week in week_dates if week <= value], default=week_dates[0])
    )
    weekly = (
        daily_long.groupby(["SKU", "WeekStartDate"], as_index=False)
        .agg(ForecastUnits=("ForecastUnits", "sum"))
        .pivot_table(index="SKU", columns="WeekStartDate", values="ForecastUnits", fill_value=0, aggfunc="sum")
        .reset_index()
    )

    recent_weekly = (
        recent_daily.groupby("SKU", as_index=False)
        .agg(Recent14DayUnits=("ForecastUnits", "sum"))
    )
    recent_weekly["RecentWeeklyBase"] = recent_weekly["Recent14DayUnits"] / 2.0
    first14 = df_14day[["SKU", *FD_COLUMNS]].copy()
    first14["Candidate14DayUnits"] = first14[FD_COLUMNS].sum(axis=1)
    tail = first14.merge(recent_weekly[["SKU", "RecentWeeklyBase"]], on="SKU", how="left")
    tail["RecentWeeklyBase"] = tail["RecentWeeklyBase"].fillna(0)
    tail["CandidateWeeklyBase"] = tail["Candidate14DayUnits"] / 2.0
    tail["TailWeeklyUnits"] = (
        tail[["RecentWeeklyBase", "CandidateWeeklyBase"]].min(axis=1).clip(lower=0)
        * max(0.0, weekly_tail_scale)
    )

    for week_date in week_dates:
        if week_date not in weekly.columns:
            weekly[week_date] = 0.0
    fd_end = forecast_start + pd.Timedelta(days=13)
    tail_week_dates = [week for week in week_dates if week > fd_end]
    for week_date in tail_week_dates:
        weekly = weekly.drop(columns=[week_date], errors="ignore").merge(
            tail[["SKU", "TailWeeklyUnits"]].rename(columns={"TailWeeklyUnits": week_date}),
            on="SKU",
            how="right",
        )
    for week_date in week_dates:
        if week_date not in weekly.columns:
            weekly[week_date] = 0.0
        weekly[week_date] = pd.to_numeric(weekly[week_date], errors="coerce").fillna(0)

    weekly = weekly[["SKU", *week_dates]].sort_values("SKU", kind="mergesort")
    metadata = {
        "weekly_tail_scale": weekly_tail_scale,
        "tail_week_count": len(tail_week_dates),
        "first_13_week_units": float(weekly[week_dates[:13]].sum().sum()),
        "notes": [
            "Weeks overlapping FD1-FD14 are built from the hybrid daily forecast.",
            "Weeks after the 14-day horizon use a conservative scaled continuation from recent demand and candidate first-14-day demand.",
        ],
    }
    return weekly, week_dates, metadata


def build_signal_summary(
    df_14day: pd.DataFrame,
    ml_totals: pd.DataFrame,
    recent_daily: pd.DataFrame,
    threshold: float,
    fallback_weight: float,
) -> pd.DataFrame:
    """Generate diagnostic comparison metrics between ML and fallback sources.

    Args:
        df_14day (pd.DataFrame): Final hybrid daily units.
        ml_totals (pd.DataFrame): Raw ML cumulative predictions.
        recent_daily (pd.DataFrame): Fallback daily predictions.
        threshold (float): Active ML unit limit.
        fallback_weight (float): Fallback weighting scalar.

    Returns:
        pd.DataFrame: Summary diagnostics table.
    """
    signal = df_14day[["SKU"]].copy()
    signal["FD1ToFD14Units"] = df_14day[FD_COLUMNS].sum(axis=1)
    signal = signal.merge(ml_totals, on="SKU", how="left")
    recent_totals = (
        recent_daily.groupby("SKU", as_index=False)
        .agg(Recent14DayUnits=("ForecastUnits", "sum"))
    )
    signal = signal.merge(recent_totals, on="SKU", how="left")
    signal["ML14DayForecastUnits"] = signal["ML14DayForecastUnits"].fillna(0)
    signal["Recent14DayUnits"] = signal["Recent14DayUnits"].fillna(0)
    signal["SelectedByMLThreshold"] = signal["ML14DayForecastUnits"].ge(threshold)
    signal["RecentFallbackWeight"] = np.where(signal["SelectedByMLThreshold"], 0.0, fallback_weight)
    return signal.sort_values(["SelectedByMLThreshold", "FD1ToFD14Units"], ascending=[False, False])


def main() -> None:
    """Train ML models, blend with historical fallbacks, write workbook, and save contract logs."""
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
        horizon_end,
    )
    pdl_horizon = load_pdl_features(args.pdl_sku_features_path, forecast_start, horizon_end)
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

    print("Training and scoring hybrid ML forecast...", flush=True)
    ml = require_sklearn()
    scored, calibration_factors = run_single_stage(args.model, ml, train, calibration, future, args)
    raw_col = f"{args.model}ForecastQty"
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
    planner_anchor_meta: dict[str, Any] | None = None
    if args.planner_total_anchor == "ops_imf":
        df_14day, planner_anchor_meta = apply_planner_daily_anchor(
            df_14day,
            args.planner_daily_path,
            forecast_start,
            args.planner_total_scale,
        )
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
        "daily_forecast": write_frame_with_sample(
            daily_contract, contract_dir / "daily_forecast.parquet", args.sample_rows
        ),
        "weekly_forecast": write_frame_with_sample(
            weekly_contract, contract_dir / "weekly_forecast.parquet", args.sample_rows
        ),
        "product_hierarchy": write_frame_with_sample(
            df_hier, contract_dir / "product_hierarchy.parquet", args.sample_rows
        ),
        "product_status": write_frame_with_sample(
            df_status, contract_dir / "product_status.parquet", args.sample_rows
        ),
        "signal_sku_summary": write_frame_with_sample(
            signal, contract_dir / "signal_sku_summary.parquet", args.sample_rows
        ),
    }
    method_meta = {
        "method": DEFAULT_CANDIDATE_TYPE,
        "forecast_start_date": str(forecast_start.date()),
        "model": args.model,
        "ml_threshold_units": args.ml_threshold_units,
        "recent_fallback_weight": args.recent_fallback_weight,
        "recent_volume_cap": args.recent_volume_cap,
        "weekly_tail_scale": args.weekly_tail_scale,
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "future_rows": int(len(future)),
        "future_inputs": future_meta,
        "recent_direct_pick": recent_meta,
        "planner_total_anchor": planner_anchor_meta,
        "weekly_policy": weekly_meta,
        "calibration_factors_sample": calibration_factors.head(50)
        .replace({np.nan: None})
        .to_dict(orient="records"),
        "forecast_totals": {
            "fd1_to_fd14_units": float(df_14day[FD_COLUMNS].sum().sum()),
            "first_13_week_units": float(df_weekly[week_dates[:13]].sum().sum()),
            "selected_ml_skus": int(signal["SelectedByMLThreshold"].sum()),
            "forecast_skus": int(df_14day["SKU"].nunique()),
        },
        "risk_posture": [
            "Configured to prefer under-forecast risk over over-forecast risk for replenishment slotting.",
            "Recent fallback is deliberately small to avoid keeping low-demand SKUs in pick locations.",
            "Weekly tail is conservative and should be reviewed before production use.",
        ],
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
        "notes": [
            "FD1-FD14 comes from the conservative future-safe hybrid ML candidate.",
            "Weekly forecast is a conservative continuation for ingestion and slotting impact review.",
            "The generated workbook is local/generated output and should not be committed.",
        ],
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

