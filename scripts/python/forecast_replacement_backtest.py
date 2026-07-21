"""Backtest BRG replacement candidates against historical DirectPick actuals.

This is the decision gate before adding ML back into the forecast work.  It
compares corporate forecasts to deterministic no-ML baselines on historical
forecast-start dates using only demand history before each start date.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_schema import (  # noqa: E402
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_SEASONAL_RECENT_WEIGHT,
    DEFAULT_SEASONAL_WINDOW_DAYS,
    DEFAULT_SEASONAL_YEARS,
    PROMO_DEFAULT_MULTIPLIER,
    PROMO_DISCOUNT_UPLIFT_FACTOR,
    PROMO_MAX_MULTIPLIER,
    normalize_sku_series,
    same_month_day,
)
from output_paths import PROJECT_ROOT  # noqa: E402


FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
HISTORY_PARQUET_DIR = FORECAST_ACCURACY_ROOT / "history" / "parquet"
DEFAULT_OUTPUT_DIR = FORECAST_ACCURACY_ROOT / "replacement_backtests"
FORECAST_DAY_PATH = HISTORY_PARQUET_DIR / "forecast_sku_day.parquet"
FORECAST_SNAPSHOT_PATH = HISTORY_PARQUET_DIR / "forecast_sku_snapshot.parquet"
SNAPSHOT_SUMMARY_PATH = HISTORY_PARQUET_DIR / "forecast_accuracy_snapshot_summary.parquet"
ACTUALS_PATH = HISTORY_PARQUET_DIR / "actual_sku_day_modified.parquet"
PDL_SKU_FEATURES_PATH = FORECAST_ACCURACY_ROOT / "promotions" / "pdl_sku_day_features.parquet"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the replacement forecast backtest pipeline.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Backtest corporate, recent no-ML, and seasonal no-ML forecast candidates."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--forecast-day-path", type=Path, default=FORECAST_DAY_PATH)
    parser.add_argument("--forecast-snapshot-path", type=Path, default=FORECAST_SNAPSHOT_PATH)
    parser.add_argument("--snapshot-summary-path", type=Path, default=SNAPSHOT_SUMMARY_PATH)
    parser.add_argument("--actuals-path", type=Path, default=ACTUALS_PATH)
    parser.add_argument("--pdl-sku-features-path", type=Path, default=PDL_SKU_FEATURES_PATH)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date")
    parser.add_argument("--max-windows", type=int, default=26)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--seasonal-years", type=int, default=DEFAULT_SEASONAL_YEARS)
    parser.add_argument("--seasonal-window-days", type=int, default=DEFAULT_SEASONAL_WINDOW_DAYS)
    parser.add_argument("--seasonal-recent-weight", type=float, default=DEFAULT_SEASONAL_RECENT_WEIGHT)
    parser.add_argument("--threads", type=int, default=8)
    return parser.parse_args()


def normalize_date(series: pd.Series) -> pd.Series:
    """Normalize a pandas series containing dates.

    Args:
        series: Pandas series containing dates.

    Returns:
        pd.Series: Normalized datetime series.
    """
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def load_actuals(path: Path) -> pd.DataFrame:
    """Load, type, and filter historical DirectPick actual demand series.

    Args:
        path: Path to actuals Parquet file.

    Returns:
        pd.DataFrame: Cleaned actual demand dataset.
    """
    actuals = pd.read_parquet(path, columns=["ActualDate", "SKU", "SoldUnits"])
    actuals["ActualDate"] = normalize_date(actuals["ActualDate"])
    actuals["SKU"] = normalize_sku_series(actuals["SKU"])
    actuals["SoldUnits"] = pd.to_numeric(actuals["SoldUnits"], errors="coerce").fillna(0).clip(lower=0)
    return actuals.loc[
        actuals["ActualDate"].notna() & actuals["SKU"].ne("") & actuals["SoldUnits"].gt(0)
    ].copy()


def load_promo(path: Path) -> pd.DataFrame:
    """Load daily SKU promotion features.

    Args:
        path: Path to the PDL SKU promotion Parquet file.

    Returns:
        pd.DataFrame: Promotion feature records.
    """
    columns = [
        "Date",
        "SKU",
        "pdl_sku_max_discount_pct",
        "pdl_sku_lw_unit_sales",
        "HasSkuPDLPromotion",
    ]
    if not path.exists():
        return pd.DataFrame(columns=columns)
    promo = pd.read_parquet(path, columns=columns)
    promo["Date"] = normalize_date(promo["Date"])
    promo["SKU"] = normalize_sku_series(promo["SKU"])
    promo["HasSkuPDLPromotion"] = promo["HasSkuPDLPromotion"].fillna(False).astype(bool)
    return promo.loc[
        promo["Date"].notna() & promo["SKU"].ne("") & promo["HasSkuPDLPromotion"]
    ].copy()


def load_promo_for_window(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Filter SKU promotion features to a specific date interval window.

    Args:
        path: Path to promotions Parquet.
        start: Start date.
        end: End date.

    Returns:
        pd.DataFrame: Filtered daily promotions.
    """
    promo = load_promo(path)
    if promo.empty:
        return promo
    return promo.loc[promo["Date"].between(start, end)].copy()


def choose_windows(
    summary_path: Path,
    start_date: str,
    end_date: str | None,
    max_windows: int,
) -> pd.DataFrame:
    """Select complete historical forecast windows within the desired time bounds.

    Args:
        summary_path: Path to snapshot summary manifest.
        start_date: Start date boundary.
        end_date: Optional end date boundary.
        max_windows: Maximum windows limit.

    Returns:
        pd.DataFrame: Sorted windows table.
    """
    summary = pd.read_parquet(summary_path)
    summary["ForecastStartDate"] = normalize_date(summary["ForecastStartDate"])
    summary["ForecastEndDate"] = normalize_date(summary["ForecastEndDate"])
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize() if end_date else pd.Timestamp.max.normalize()
    windows = summary.loc[
        summary["CompleteActualWindow"].astype(bool)
        & summary["ForecastStartDate"].ge(start)
        & summary["ForecastStartDate"].le(end)
    ].copy()
    windows = windows.sort_values("ForecastStartDate", kind="mergesort")
    if max_windows and len(windows) > max_windows:
        windows = windows.tail(max_windows).copy()
    return windows.reset_index(drop=True)


def corporate_forecast(forecast_day: pd.DataFrame, snapshot_id: str) -> pd.DataFrame:
    """Extract corporate forecast quantities for a single snapshot window.

    Args:
        forecast_day: Forecast day records.
        snapshot_id: Target snapshot UUID.

    Returns:
        pd.DataFrame: Summed corporate forecast units by SKU.
    """
    corp = forecast_day.loc[forecast_day["SnapshotId"].eq(snapshot_id)].copy()
    corp = (
        corp.groupby("SKU", as_index=False)
        .agg(ForecastUnits=("ForecastQty", "sum"))
        .sort_values("SKU", kind="mergesort")
    )
    return corp


def snapshot_universe(snapshot_sku: pd.DataFrame, snapshot_id: str) -> pd.DataFrame:
    """Fetch unique SKU values present in the snapshot metadata state.

    Args:
        snapshot_sku: Snapshot SKU metadata rows.
        snapshot_id: Target snapshot ID.

    Returns:
        pd.DataFrame: Unique SKU identifiers list.
    """
    universe = snapshot_sku.loc[snapshot_sku["SnapshotId"].eq(snapshot_id), ["SKU"]].copy()
    universe["SKU"] = normalize_sku_series(universe["SKU"])
    return universe.loc[universe["SKU"].ne("")].drop_duplicates("SKU")


def dow_factors(window_actuals: pd.DataFrame, lookback_days: int, window_start: pd.Timestamp) -> dict[int, float]:
    """Calculate day-of-week scale adjustments based on historical demand ratios.

    Args:
        window_actuals: Historical actuals window.
        lookback_days: Length of history lookback.
        window_start: Target start timestamp.

    Returns:
        dict[int, float]: Day-of-week scaling multipliers mapped 0 (Monday) to 6 (Sunday).
    """
    if window_actuals.empty:
        return {idx: 1.0 for idx in range(7)}
    calendar = pd.DataFrame({"Date": pd.date_range(window_start, periods=lookback_days)})
    day_counts = calendar["Date"].dt.dayofweek.value_counts().to_dict()
    overall_daily_units = window_actuals["SoldUnits"].sum() / float(lookback_days)
    units = window_actuals.groupby(window_actuals["ActualDate"].dt.dayofweek)["SoldUnits"].sum()
    factors = {}
    for day_idx in range(7):
        mean_for_day = units.get(day_idx, 0.0) / float(day_counts.get(day_idx, 1))
        factor = mean_for_day / overall_daily_units if overall_daily_units > 0 else 1.0
        factors[day_idx] = float(min(max(factor, 0.65), 1.35))
    return factors


def promo_multiplier(promo: pd.DataFrame) -> pd.Series:
    """Derive promotion volume lift multiplier based on discount percentage.

    Args:
        promo: Dataframe containing PDL discount rates.

    Returns:
        pd.Series: Lift factor series.
    """
    discount = pd.to_numeric(
        promo.get("pdl_sku_max_discount_pct", 0), errors="coerce"
    ).fillna(0)
    multiplier = 1.0 + discount.clip(lower=0, upper=1) * PROMO_DISCOUNT_UPLIFT_FACTOR
    multiplier = multiplier.where(discount.gt(0), PROMO_DEFAULT_MULTIPLIER)
    return multiplier.clip(lower=1.0, upper=PROMO_MAX_MULTIPLIER)


def promo_daily_signal(promo: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Build daily promotion uplift scaling factor and unit floors.

    Args:
        promo: Input promotion records.
        start: Window start date.
        end: Window end date.

    Returns:
        pd.DataFrame: Daily promotion multiplier and floor signals by SKU/Date.
    """
    if promo.empty:
        return pd.DataFrame(columns=["SKU", "ForecastDate", "PromoMultiplier", "PromoDailyFloor"])
    work = promo.loc[promo["Date"].between(start, end)].copy()
    if work.empty:
        return pd.DataFrame(columns=["SKU", "ForecastDate", "PromoMultiplier", "PromoDailyFloor"])
    work["PromoMultiplier"] = promo_multiplier(work)
    work["PromoDailyFloor"] = (
        pd.to_numeric(work["pdl_sku_lw_unit_sales"], errors="coerce").fillna(0).clip(lower=0) / 7.0
    )
    return (
        work.groupby(["SKU", "Date"], as_index=False)
        .agg(
            PromoMultiplier=("PromoMultiplier", "max"),
            PromoDailyFloor=("PromoDailyFloor", "max"),
        )
        .rename(columns={"Date": "ForecastDate"})
    )


def direct_pick_signal(
    actuals: pd.DataFrame,
    start: pd.Timestamp,
    lookback_days: int,
) -> tuple[pd.DataFrame, dict[int, float], dict[str, Any]]:
    """Compute recent DirectPick daily average base rates and seasonality profiles.

    Args:
        actuals: Demand actuals.
        start: Forecast origin start date.
        lookback_days: Length of lookback window.

    Returns:
        tuple[pd.DataFrame, dict[int, float], dict[str, Any]]: Average base rates table,
            day-of-week factors map, and metadata metrics.
    """
    window_start = start - pd.Timedelta(days=lookback_days)
    window = actuals.loc[actuals["ActualDate"].ge(window_start) & actuals["ActualDate"].lt(start)].copy()
    if window.empty:
        empty = pd.DataFrame(columns=["SKU", "DirectPickLookbackUnits", "DailyBaseUnits"])
        return empty, {idx: 1.0 for idx in range(7)}, {"rows": 0, "sold_units": 0.0}
    grouped = (
        window.groupby("SKU", as_index=False)
        .agg(DirectPickLookbackUnits=("SoldUnits", "sum"))
        .sort_values("SKU", kind="mergesort")
    )
    grouped["DailyBaseUnits"] = grouped["DirectPickLookbackUnits"] / float(lookback_days)
    metadata = {
        "window_start": str(window_start.date()),
        "window_end_exclusive": str(start.date()),
        "rows": int(len(window)),
        "distinct_skus": int(grouped["SKU"].nunique()),
        "sold_units": float(window["SoldUnits"].sum()),
    }
    return grouped, dow_factors(window, lookback_days, window_start), metadata


def recent_daily_forecast(
    actuals: pd.DataFrame,
    forecast_start: pd.Timestamp,
    lookback_days: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Allocate a recent DirectPick rate across the next 14 weekdays."""
    direct, factors, metadata = direct_pick_signal(actuals, forecast_start, lookback_days)
    if direct.empty:
        columns = ["SKU", "ForecastDay", "ForecastDate", "ForecastUnits"]
        return pd.DataFrame(columns=columns), metadata
    frames: list[pd.DataFrame] = []
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


def seasonal_daily_signal(
    actuals: pd.DataFrame,
    forecast_dates: list[pd.Timestamp],
    start: pd.Timestamp,
    seasonal_years: int,
    seasonal_window_days: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Compute prior-year same-season daily average demand rates.

    Args:
        actuals: Demand actuals.
        forecast_dates: Target forecast timeline.
        start: Forecast origin start date.
        seasonal_years: Prior years back to query.
        seasonal_window_days: Calendar window margin on each side of date center.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Seasonal base rate table and metadata dictionary.
    """
    historical_years = [start.year - offset for offset in range(1, seasonal_years + 1)]
    min_year = int(actuals["ActualDate"].dt.year.min())
    historical_years = [year for year in historical_years if year >= min_year]
    target_ranges: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    for forecast_date in forecast_dates:
        for year in historical_years:
            center = same_month_day(year, forecast_date)
            hist_start = center - pd.Timedelta(days=seasonal_window_days)
            hist_end = center + pd.Timedelta(days=seasonal_window_days)
            target_ranges.append((forecast_date, hist_start, hist_end))
    if not target_ranges:
        metadata = {
            "historical_years": historical_years,
            "seasonal_window_days_each_side": seasonal_window_days,
            "daily_rows": 0,
            "daily_skus": 0,
            "seasonal_units_14d": 0.0,
        }
        return pd.DataFrame(columns=["SKU", "ForecastDate", "SeasonalDailyUnits"]), metadata

    historical_dates = set()
    for _forecast_date, hist_start, hist_end in target_ranges:
        historical_dates.update(pd.date_range(hist_start, hist_end, freq="D"))
    scoped_actuals = actuals.loc[actuals["ActualDate"].isin(historical_dates)].copy()
    frames = []
    for forecast_date in forecast_dates:
        masks = []
        window_days = 0
        for year in historical_years:
            center = same_month_day(year, forecast_date)
            hist_start = center - pd.Timedelta(days=seasonal_window_days)
            hist_end = center + pd.Timedelta(days=seasonal_window_days)
            masks.append(scoped_actuals["ActualDate"].between(hist_start, hist_end))
            window_days += int((hist_end - hist_start).days) + 1
        if not masks or window_days == 0:
            continue
        mask = masks[0]
        for next_mask in masks[1:]:
            mask = mask | next_mask
        hist = scoped_actuals.loc[mask]
        if hist.empty:
            continue
        grouped = (
            hist.groupby("SKU", as_index=False)
            .agg(SeasonalDailyUnits=("SoldUnits", "sum"))
            .sort_values("SKU", kind="mergesort")
        )
        grouped["SeasonalDailyUnits"] = grouped["SeasonalDailyUnits"] / float(window_days)
        grouped["ForecastDate"] = forecast_date
        frames.append(grouped)
    if not frames:
        daily = pd.DataFrame(columns=["SKU", "ForecastDate", "SeasonalDailyUnits"])
    else:
        daily = pd.concat(frames, ignore_index=True)
    metadata = {
        "historical_years": historical_years,
        "seasonal_window_days_each_side": seasonal_window_days,
        "daily_rows": int(len(daily)),
        "daily_skus": int(daily["SKU"].nunique()) if not daily.empty else 0,
        "seasonal_units_14d": float(daily["SeasonalDailyUnits"].sum()) if not daily.empty else 0.0,
    }
    return daily, metadata


def no_ml_forecast(
    *,
    actuals: pd.DataFrame,
    promo: pd.DataFrame,
    source_universe: pd.DataFrame,
    start: pd.Timestamp,
    lookback_days: int,
    include_seasonal: bool,
    include_promo_floor: bool,
    seasonal_years: int,
    seasonal_window_days: int,
    seasonal_recent_weight: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Synthesize a deterministic no-ML forecast using recent volume and promotions logic.

    Args:
        actuals: Demand actuals.
        promo: Promotion records.
        source_universe: active SKU universe list.
        start: Forecast origin start date.
        lookback_days: Lookback window length.
        include_seasonal: Whether to blend same-season history.
        include_promo_floor: Whether to apply last-week-sales floor from PDL files.
        seasonal_years: Backtest years count.
        seasonal_window_days: Seasonal calendar margin size.
        seasonal_recent_weight: Weight applied to recent demand vs seasonal.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Reconciled SKU-level forecast and metadata dict.
    """
    end = start + pd.Timedelta(days=13)
    forecast_dates = [start + pd.Timedelta(days=idx) for idx in range(14)]
    direct, factors, direct_meta = direct_pick_signal(actuals, start, lookback_days)
    promo_daily = promo_daily_signal(promo, start, end)
    if include_seasonal:
        seasonal_daily, seasonal_meta = seasonal_daily_signal(
            actuals, forecast_dates, start, seasonal_years, seasonal_window_days
        )
    else:
        seasonal_daily = pd.DataFrame(columns=["SKU", "ForecastDate", "SeasonalDailyUnits"])
        seasonal_meta = {"status": "disabled"}

    universe = set(source_universe["SKU"].dropna().unique())
    universe.update(direct.loc[direct["DirectPickLookbackUnits"].gt(0), "SKU"])
    universe.update(promo_daily["SKU"].dropna().unique())
    universe.discard("")
    signal = pd.DataFrame({"SKU": sorted(universe)})
    signal = signal.merge(direct, on="SKU", how="left")
    signal["DirectPickLookbackUnits"] = signal["DirectPickLookbackUnits"].fillna(0)
    signal["DailyBaseUnits"] = signal["DailyBaseUnits"].fillna(0)

    frames = []
    seasonal_weight = 1.0 - seasonal_recent_weight
    for idx, forecast_date in enumerate(forecast_dates, start=1):
        frame = signal[["SKU", "DailyBaseUnits"]].copy()
        frame["ForecastDay"] = idx
        frame["ForecastDate"] = forecast_date
        frame["RecentDailyUnits"] = frame["DailyBaseUnits"] * factors.get(int(forecast_date.dayofweek), 1.0)
        seasonal_for_day = seasonal_daily.loc[seasonal_daily["ForecastDate"].eq(forecast_date)]
        if not seasonal_for_day.empty:
            frame = frame.merge(seasonal_for_day, on=["SKU", "ForecastDate"], how="left")
        else:
            frame["SeasonalDailyUnits"] = 0.0
        frame["SeasonalDailyUnits"] = frame["SeasonalDailyUnits"].fillna(0)
        has_recent = frame["RecentDailyUnits"].gt(0)
        has_seasonal = frame["SeasonalDailyUnits"].gt(0)
        frame["ForecastUnitsRaw"] = frame["RecentDailyUnits"]
        both = include_seasonal & has_recent & has_seasonal
        frame.loc[both, "ForecastUnitsRaw"] = (
            frame.loc[both, "RecentDailyUnits"] * seasonal_recent_weight
            + frame.loc[both, "SeasonalDailyUnits"] * seasonal_weight
        )
        seasonal_only = include_seasonal & ~has_recent & has_seasonal
        frame.loc[seasonal_only, "ForecastUnitsRaw"] = frame.loc[
            seasonal_only, "SeasonalDailyUnits"
        ]
        promo_for_day = promo_daily.loc[promo_daily["ForecastDate"].eq(forecast_date)]
        if not promo_for_day.empty:
            frame = frame.merge(promo_for_day, on=["SKU", "ForecastDate"], how="left")
            frame["PromoMultiplier"] = frame["PromoMultiplier"].fillna(1.0)
            frame["PromoDailyFloor"] = frame["PromoDailyFloor"].fillna(0)
            if not include_promo_floor:
                frame["PromoDailyFloor"] = 0.0
            lifted = frame["ForecastUnitsRaw"] * frame["PromoMultiplier"]
            frame["ForecastUnitsRaw"] = lifted.where(lifted >= frame["PromoDailyFloor"], frame["PromoDailyFloor"])
        frames.append(frame[["SKU", "ForecastDay", "ForecastDate", "ForecastUnitsRaw"]])

    daily = pd.concat(frames, ignore_index=True)
    forecast = (
        daily.groupby("SKU", as_index=False)
        .agg(ForecastUnits=("ForecastUnitsRaw", "sum"))
        .sort_values("SKU", kind="mergesort")
    )
    forecast["ForecastUnits"] = forecast["ForecastUnits"].round().clip(lower=0)
    forecast = forecast.loc[forecast["ForecastUnits"].gt(0)].copy()
    metadata = {
        "direct_pick": direct_meta,
        "seasonal": seasonal_meta,
        "promo_future_skus": int(promo_daily["SKU"].nunique()) if not promo_daily.empty else 0,
        "candidate_universe_skus": int(len(signal)),
        "forecasted_skus": int(len(forecast)),
        "forecast_units": float(forecast["ForecastUnits"].sum()),
        "include_promo_floor": include_promo_floor,
        "notes": [
            "Historical backtest excludes reservations and inbound floors because point-in-time history is not available.",
            "Seasonal history shapes the candidate universe but cannot add rows by itself.",
        ],
    }
    return forecast, metadata


def actual_window(actuals: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """Retrieve actual units sold for a 14-day window.

    Args:
        actuals: Historical actuals.
        start: Start timestamp.

    Returns:
        pd.DataFrame: Summed units sold by SKU.
    """
    end = start + pd.Timedelta(days=13)
    return (
        actuals.loc[actuals["ActualDate"].between(start, end)]
        .groupby("SKU", as_index=False)
        .agg(SoldUnits=("SoldUnits", "sum"))
    )


def score_forecast(
    forecast: pd.DataFrame,
    actual: pd.DataFrame,
    candidate_id: str,
    snapshot: pd.Series,
) -> dict[str, Any]:
    """Score forecast metrics (WAPE, Bias, coverage, zeros) against actuals.

    Args:
        forecast: Forecasted units dataframe.
        actual: Actual demand units dataframe.
        candidate_id: Forecast candidate label.
        snapshot: Snapshot metadata series.

    Returns:
        dict[str, Any]: Compiled evaluation metrics.
    """
    compare = forecast.merge(actual, on="SKU", how="outer")
    compare["ForecastUnits"] = compare["ForecastUnits"].fillna(0).astype(float)
    compare["SoldUnits"] = compare["SoldUnits"].fillna(0).astype(float)
    compare["AbsError"] = (compare["ForecastUnits"] - compare["SoldUnits"]).abs()
    sold_units = float(compare["SoldUnits"].sum())
    forecast_units = float(compare["ForecastUnits"].sum())
    sold_units_with_forecast = float(
        compare.loc[compare["SoldUnits"].gt(0) & compare["ForecastUnits"].gt(0), "SoldUnits"].sum()
    )
    zero_forecast_sold_units = float(
        compare.loc[compare["SoldUnits"].gt(0) & compare["ForecastUnits"].eq(0), "SoldUnits"].sum()
    )
    overgenerated = compare.loc[compare["ForecastUnits"].gt(0) & compare["SoldUnits"].eq(0)]
    missed_high_volume = compare.loc[compare["SoldUnits"].ge(10) & compare["ForecastUnits"].eq(0)]
    return {
        "Candidate": candidate_id,
        "SnapshotId": snapshot["SnapshotId"],
        "SourceFile": snapshot["SourceFile"],
        "ForecastStartDate": pd.Timestamp(snapshot["ForecastStartDate"]).date().isoformat(),
        "ForecastEndDate": pd.Timestamp(snapshot["ForecastEndDate"]).date().isoformat(),
        "ForecastedSKUs": int(compare["ForecastUnits"].gt(0).sum()),
        "SoldSKUs": int(compare["SoldUnits"].gt(0).sum()),
        "UnionSKUs": int((compare["ForecastUnits"].gt(0) | compare["SoldUnits"].gt(0)).sum()),
        "ForecastUnits": forecast_units,
        "SoldUnits": sold_units,
        "BiasUnitsForecastMinusActual": forecast_units - sold_units,
        "BiasPctForecastMinusActual": (forecast_units - sold_units) / sold_units if sold_units else pd.NA,
        "AbsErrorUnits": float(compare["AbsError"].sum()),
        "WAPE": float(compare["AbsError"].sum() / sold_units) if sold_units else pd.NA,
        "SoldUnitsWithForecast": sold_units_with_forecast,
        "SoldUnitForecastCoveragePct": sold_units_with_forecast / sold_units if sold_units else pd.NA,
        "ZeroForecastSoldSKUs": int((compare["SoldUnits"].gt(0) & compare["ForecastUnits"].eq(0)).sum()),
        "ZeroForecastSoldUnits": zero_forecast_sold_units,
        "ZeroForecastSoldUnitPct": zero_forecast_sold_units / sold_units if sold_units else pd.NA,
        "OvergeneratedZeroDemandSKUs": int(len(overgenerated)),
        "OvergeneratedZeroDemandUnits": float(overgenerated["ForecastUnits"].sum()),
        "MissedHighVolumeSKUsSoldGE10": int(len(missed_high_volume)),
        "MissedHighVolumeUnitsSoldGE10": float(missed_high_volume["SoldUnits"].sum()),
    }


def summarize_by_candidate(scores: pd.DataFrame) -> pd.DataFrame:
    """Aggregate window scorecard metrics grouped by candidate.

    Args:
        scores: Scored window logs.

    Returns:
        pd.DataFrame: Aggregated candidates leaderboard.
    """
    summary = (
        scores.groupby("Candidate", as_index=False)
        .agg(
            Windows=("ForecastStartDate", "nunique"),
            ForecastUnits=("ForecastUnits", "sum"),
            SoldUnits=("SoldUnits", "sum"),
            AbsErrorUnits=("AbsErrorUnits", "sum"),
            ZeroForecastSoldUnits=("ZeroForecastSoldUnits", "sum"),
            OvergeneratedZeroDemandUnits=("OvergeneratedZeroDemandUnits", "sum"),
            MissedHighVolumeUnitsSoldGE10=("MissedHighVolumeUnitsSoldGE10", "sum"),
            AvgForecastedSKUs=("ForecastedSKUs", "mean"),
            AvgSoldUnitForecastCoveragePct=("SoldUnitForecastCoveragePct", "mean"),
        )
        .sort_values("AbsErrorUnits", kind="mergesort")
    )
    summary["WAPE"] = summary["AbsErrorUnits"] / summary["SoldUnits"]
    summary["BiasUnitsForecastMinusActual"] = summary["ForecastUnits"] - summary["SoldUnits"]
    summary["BiasPctForecastMinusActual"] = summary["BiasUnitsForecastMinusActual"] / summary["SoldUnits"]
    summary["ZeroForecastSoldUnitPct"] = summary["ZeroForecastSoldUnits"] / summary["SoldUnits"]
    return summary


def main() -> None:
    """Execute the command line entry point to backtest no-ML candidates against corporate."""
    args = parse_args()
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, str(args.threads))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    windows = choose_windows(args.snapshot_summary_path, args.start_date, args.end_date, args.max_windows)
    if windows.empty:
        raise RuntimeError("No complete historical forecast windows matched the requested filters.")

    snapshot_ids = windows["SnapshotId"].dropna().astype(str).tolist()
    print(f"Selected complete windows: {len(windows):,}", flush=True)
    print("Loading corporate forecast rows for selected windows...", flush=True)
    forecast_day = pd.read_parquet(
        args.forecast_day_path,
        columns=["SnapshotId", "SKU", "ForecastQty"],
        filters=[("SnapshotId", "in", snapshot_ids)],
    )
    print(f"  forecast day rows: {len(forecast_day):,}", flush=True)
    print("Loading snapshot SKU universes for selected windows...", flush=True)
    snapshot_sku = pd.read_parquet(
        args.forecast_snapshot_path,
        columns=["SnapshotId", "SKU"],
        filters=[("SnapshotId", "in", snapshot_ids)],
    )
    print(f"  snapshot SKU rows: {len(snapshot_sku):,}", flush=True)
    print("Loading DirectPick actuals...", flush=True)
    actuals = load_actuals(args.actuals_path)
    print(f"  actual rows: {len(actuals):,}", flush=True)
    promo_start = pd.Timestamp(windows["ForecastStartDate"].min()).normalize()
    promo_end = pd.Timestamp(windows["ForecastEndDate"].max()).normalize()
    print("Loading PDL promo rows for selected horizons...", flush=True)
    promo = load_promo_for_window(args.pdl_sku_features_path, promo_start, promo_end)
    print(f"  promo rows: {len(promo):,}", flush=True)
    forecast_day["ForecastQty"] = pd.to_numeric(forecast_day["ForecastQty"], errors="coerce").fillna(0)
    forecast_day["SKU"] = normalize_sku_series(forecast_day["SKU"])
    snapshot_sku["SKU"] = normalize_sku_series(snapshot_sku["SKU"])

    score_rows = []
    metadata_rows = []
    for idx, snapshot in windows.iterrows():
        start = pd.Timestamp(snapshot["ForecastStartDate"]).normalize()
        print(f"[{idx + 1}/{len(windows)}] Backtesting {start.date()}...")
        actual = actual_window(actuals, start)
        source_universe = snapshot_universe(snapshot_sku, snapshot["SnapshotId"])
        candidates = {
            "corporate": (corporate_forecast(forecast_day, snapshot["SnapshotId"]), {}),
        }
        recent_forecast, recent_meta = no_ml_forecast(
            actuals=actuals,
            promo=promo,
            source_universe=source_universe,
            start=start,
            lookback_days=args.lookback_days,
            include_seasonal=False,
            include_promo_floor=True,
            seasonal_years=args.seasonal_years,
            seasonal_window_days=args.seasonal_window_days,
            seasonal_recent_weight=args.seasonal_recent_weight,
        )
        seasonal_forecast, seasonal_meta = no_ml_forecast(
            actuals=actuals,
            promo=promo,
            source_universe=source_universe,
            start=start,
            lookback_days=args.lookback_days,
            include_seasonal=True,
            include_promo_floor=True,
            seasonal_years=args.seasonal_years,
            seasonal_window_days=args.seasonal_window_days,
            seasonal_recent_weight=args.seasonal_recent_weight,
        )
        recent_no_floor_forecast, recent_no_floor_meta = no_ml_forecast(
            actuals=actuals,
            promo=promo,
            source_universe=source_universe,
            start=start,
            lookback_days=args.lookback_days,
            include_seasonal=False,
            include_promo_floor=False,
            seasonal_years=args.seasonal_years,
            seasonal_window_days=args.seasonal_window_days,
            seasonal_recent_weight=args.seasonal_recent_weight,
        )
        seasonal_no_floor_forecast, seasonal_no_floor_meta = no_ml_forecast(
            actuals=actuals,
            promo=promo,
            source_universe=source_universe,
            start=start,
            lookback_days=args.lookback_days,
            include_seasonal=True,
            include_promo_floor=False,
            seasonal_years=args.seasonal_years,
            seasonal_window_days=args.seasonal_window_days,
            seasonal_recent_weight=args.seasonal_recent_weight,
        )
        candidates["recent_no_ml"] = (recent_forecast, recent_meta)
        candidates["seasonal_no_ml"] = (seasonal_forecast, seasonal_meta)
        candidates["recent_no_ml_no_promo_floor"] = (
            recent_no_floor_forecast,
            recent_no_floor_meta,
        )
        candidates["seasonal_no_ml_no_promo_floor"] = (
            seasonal_no_floor_forecast,
            seasonal_no_floor_meta,
        )

        for candidate, (forecast, metadata) in candidates.items():
            score_rows.append(score_forecast(forecast, actual, candidate, snapshot))
            metadata_rows.append(
                {
                    "Candidate": candidate,
                    "SnapshotId": snapshot["SnapshotId"],
                    "ForecastStartDate": start.date().isoformat(),
                    "Metadata": json.dumps(metadata, sort_keys=True),
                }
            )

    scores = pd.DataFrame(score_rows)
    candidate_summary = summarize_by_candidate(scores)
    window_winners = (
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
        "lookback_days": args.lookback_days,
        "seasonal_years": args.seasonal_years,
        "seasonal_window_days_each_side": args.seasonal_window_days,
        "seasonal_recent_weight": args.seasonal_recent_weight,
        "inputs": {
            "forecast_day": str(args.forecast_day_path),
            "forecast_snapshot": str(args.forecast_snapshot_path),
            "snapshot_summary": str(args.snapshot_summary_path),
            "actuals": str(args.actuals_path),
            "pdl_sku_features": str(args.pdl_sku_features_path),
        },
        "caveats": [
            "Historical no-ML candidates exclude reservation and inbound floors because point-in-time history is not available for the selected windows.",
            "PDL future promotion features are used when rows exist for the forecast horizon; this assumes those promotion rows would have been knowable before the forecast start.",
            "This backtest scores 14-day SKU demand, not full ingestion SlotTier or RequiredSlots churn.",
        ],
    }

    scores.to_parquet(args.output_dir / "replacement_backtest_window_scores.parquet", index=False, compression="zstd")
    scores.to_csv(args.output_dir / "replacement_backtest_window_scores.csv", index=False)
    candidate_summary.to_csv(args.output_dir / "replacement_backtest_candidate_summary.csv", index=False)
    window_winners.to_csv(args.output_dir / "replacement_backtest_window_winners.csv", index=False)
    pd.DataFrame(metadata_rows).to_parquet(
        args.output_dir / "replacement_backtest_candidate_metadata.parquet",
        index=False,
        compression="zstd",
    )
    (args.output_dir / "replacement_backtest_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print("\nCandidate summary:")
    print(candidate_summary.to_string(index=False))
    print(f"\nWrote backtest outputs to: {args.output_dir}")


if __name__ == "__main__":
    main()
