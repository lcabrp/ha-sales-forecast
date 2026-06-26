"""Build and validate BRG-like forecast replacement candidate packages.

The first supported candidate is a corporate-workbook clone.  It proves the
candidate contract can round-trip through the existing ingestion pipeline before
we train or compare any model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import xlsxwriter

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import ingestion_pipeline as ingestion  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "replacement_contract"
FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
ACTUALS_PATH = FORECAST_ACCURACY_ROOT / "history" / "parquet" / "actual_sku_day_modified.parquet"
PDL_SKU_FEATURES_PATH = FORECAST_ACCURACY_ROOT / "promotions" / "pdl_sku_day_features.parquet"
INBOUND_SKU_DAY_PATH = FORECAST_ACCURACY_ROOT / "inbound" / "ax_open_inbound_sku_day.parquet"
RESERVATION_SKU_DAY_PATH = FORECAST_ACCURACY_ROOT / "reservations" / "ax_reservation_sku_day.parquet"
FD_COLUMNS = [f"FD{i}" for i in range(1, 15)]
DEFAULT_LOOKBACK_DAYS = 56
WEEKLY_FORECAST_WEEKS = 26
DEFAULT_SEASONAL_YEARS = 3
DEFAULT_SEASONAL_WINDOW_DAYS = 7
DEFAULT_SEASONAL_RECENT_WEIGHT = 0.65
RESERVATION_DAILY_WEIGHTS = {1: 0.60, 2: 0.40}
PROMO_DEFAULT_MULTIPLIER = 1.25
PROMO_DISCOUNT_UPLIFT_FACTOR = 0.75
PROMO_MAX_MULTIPLIER = 2.00
AX_FORWARD_DEMAND_COLUMNS = [
    "Division",
    "Department",
    "Class",
    "Subclass",
    "KeyCategoryView",
    "SKU",
    "Item",
    "Color",
    "Size",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "ReplenishmentThreshold",
    "PutawayIndicator",
    "ProductStatus",
    "ProductStatusDate",
    "ProductStage",
    "ReturnAction",
    "ReturnActionDate",
    "NVARExpectedQty",
    "ForecastStartDate",
    *FD_COLUMNS,
]


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the replacement contract generator.

    Returns:
        argparse.Namespace: Populated argument namespaces.
    """
    parser = argparse.ArgumentParser(
        description="Create a BRG-like forecast candidate and run the ingestion round-trip."
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        help="Corporate Product Info workbook to clone. Defaults to ingestion pipeline latest-source logic.",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--candidate-type",
        choices=["corporate_clone", "no_ml_baseline", "seasonal_no_ml_baseline"],
        default="corporate_clone",
        help="Candidate package to build before running the ingestion round-trip.",
    )
    parser.add_argument(
        "--candidate-id",
        help="Stable candidate folder/name. Defaults to corporate_clone_YYYY-MM-DD_HHMMSS.",
    )
    parser.add_argument(
        "--forecast-start-date",
        help="First FD1 forecast date for no_ml_baseline. Defaults to tomorrow.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="Recent DirectPick history window for no_ml_baseline.",
    )
    parser.add_argument("--actuals-path", type=Path, default=ACTUALS_PATH)
    parser.add_argument("--pdl-sku-features-path", type=Path, default=PDL_SKU_FEATURES_PATH)
    parser.add_argument("--inbound-path", type=Path, default=INBOUND_SKU_DAY_PATH)
    parser.add_argument("--reservations-path", type=Path, default=RESERVATION_SKU_DAY_PATH)
    parser.add_argument("--seasonal-years", type=int, default=DEFAULT_SEASONAL_YEARS)
    parser.add_argument("--seasonal-window-days", type=int, default=DEFAULT_SEASONAL_WINDOW_DAYS)
    parser.add_argument(
        "--seasonal-recent-weight",
        type=float,
        default=DEFAULT_SEASONAL_RECENT_WEIGHT,
        help="Blend weight for recent demand when seasonal history also exists.",
    )
    parser.add_argument("--sample-rows", type=int, default=1000)
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    """Compute the SHA-256 hash checksum of a file.

    Args:
        path (Path): Path to the file.

    Returns:
        str: Hex digest checksum.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_source(source_file: Path | None) -> Path:
    """Select the corporate workbook source.

    Returns the user-specified source_file if available, otherwise queries the ingestion
    pipeline for the latest available file in the repository source directory.

    Args:
        source_file (Path | None): Optional user specified workbook path.

    Returns:
        Path: Path to chosen workbook.
    """
    if source_file is not None:
        if not source_file.exists():
            raise FileNotFoundError(f"Source workbook not found: {source_file}")
        return source_file
    return ingestion.get_latest_source_file()


def normalize_forecast_start(value: str) -> pd.Timestamp:
    """Parse and normalize a forecast start date string into a Timestamp at midnight.

    Args:
        value (str): Raw string date.

    Returns:
        pd.Timestamp: Normalized midnight Timestamp.
    """
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Could not parse forecast start date: {value!r}")
    return pd.Timestamp(parsed).normalize()


def default_forecast_start_date() -> pd.Timestamp:
    """Calculate the default forecast start date (tomorrow).

    Returns:
        pd.Timestamp: Tomorrow's date at midnight.
    """
    return pd.Timestamp(datetime.now().date()) + pd.Timedelta(days=1)


def normalize_optional_date(value: str | None) -> pd.Timestamp:
    """Parse a forecast start date, falling back to tomorrow if empty.

    Args:
        value (str | None): Optional date string.

    Returns:
        pd.Timestamp: Normalized midnight Timestamp.
    """
    if value is None:
        return default_forecast_start_date()
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Could not parse forecast start date: {value!r}")
    return pd.Timestamp(parsed).normalize()


def previous_sunday(value: pd.Timestamp) -> pd.Timestamp:
    """Determine the Sunday immediately preceding (or equal to) the given Timestamp.

    Args:
        value (pd.Timestamp): Target date.

    Returns:
        pd.Timestamp: Midnight Timestamp of the previous Sunday.
    """
    return value - pd.Timedelta(days=(value.dayofweek + 1) % 7)


def forecast_week_dates(forecast_start: pd.Timestamp, weeks: int = WEEKLY_FORECAST_WEEKS) -> list[pd.Timestamp]:
    """Generate a series of weekly start dates commencing from the previous Sunday.

    Args:
        forecast_start (pd.Timestamp): Target start date.
        weeks (int, optional): Number of weeks to generate. Defaults to WEEKLY_FORECAST_WEEKS.

    Returns:
        list[pd.Timestamp]: List of midnight timestamps.
    """
    first_week = previous_sunday(forecast_start)
    return [first_week + pd.Timedelta(days=7 * idx) for idx in range(weeks)]


def normalize_sku_series(series: pd.Series) -> pd.Series:
    """Apply standard uppercase cleaning and stripping rules to SKU values.

    Args:
        series (pd.Series): Raw SKU pandas Series.

    Returns:
        pd.Series: Normalized SKU string Series.
    """
    return series.fillna("").map(ingestion.normalize_sku)


def build_daily_contract(
    df_14day: pd.DataFrame,
    forecast_start: pd.Timestamp,
    candidate_id: str,
    source_workbook: str,
) -> pd.DataFrame:
    """Melt daily wide forecasts (FD1-FD14) into long database contract records.

    Args:
        df_14day (pd.DataFrame): Wide daily forecast table.
        forecast_start (pd.Timestamp): Midnight start timestamp.
        candidate_id (str): Unique candidate identifier.
        source_workbook (str): Source workbook filename.

    Returns:
        pd.DataFrame: Long format daily forecast contract dataframe.
    """
    daily = df_14day.melt(
        id_vars=["SKU"],
        value_vars=[col for col in FD_COLUMNS if col in df_14day.columns],
        var_name="ForecastDayName",
        value_name="ForecastUnits",
    )
    daily["ForecastDay"] = daily["ForecastDayName"].str.replace("FD", "", regex=False).astype("int16")
    daily["ForecastDate"] = forecast_start + pd.to_timedelta(daily["ForecastDay"] - 1, unit="D")
    daily["ForecastUnits"] = pd.to_numeric(daily["ForecastUnits"], errors="coerce").fillna(0.0)
    daily.insert(0, "CandidateID", candidate_id)
    daily.insert(1, "SourceWorkbook", source_workbook)
    daily.insert(2, "ForecastStartDate", forecast_start)
    return daily[
        [
            "CandidateID",
            "SourceWorkbook",
            "ForecastStartDate",
            "SKU",
            "ForecastDay",
            "ForecastDate",
            "ForecastUnits",
        ]
    ].sort_values(["SKU", "ForecastDay"], kind="mergesort")


def build_weekly_contract(
    df_weekly: pd.DataFrame,
    week_dates: list[pd.Timestamp],
    week_columns: list[Any],
    candidate_id: str,
    source_workbook: str,
) -> pd.DataFrame:
    """Melt weekly wide forecasts into long database contract records.

    Args:
        df_weekly (pd.DataFrame): Wide weekly forecast table.
        week_dates (list[pd.Timestamp]): Sunday start date timestamps.
        week_columns (list[Any]): Target weekly column names/keys.
        candidate_id (str): Unique candidate identifier.
        source_workbook (str): Source workbook filename.

    Returns:
        pd.DataFrame: Long format weekly forecast contract dataframe.
    """
    weekly = df_weekly.melt(
        id_vars=["SKU"],
        value_vars=week_columns,
        var_name="WeekColumn",
        value_name="ForecastUnits",
    )
    week_index = {column: idx + 1 for idx, column in enumerate(week_columns)}
    week_date = {column: pd.Timestamp(week_dates[idx]).normalize() for idx, column in enumerate(week_columns)}
    weekly["WeekIndex"] = weekly["WeekColumn"].map(week_index).astype("int16")
    weekly["WeekStartDate"] = weekly["WeekColumn"].map(week_date)
    weekly["ForecastUnits"] = pd.to_numeric(weekly["ForecastUnits"], errors="coerce").fillna(0.0)
    weekly["IsFirst13Weeks"] = weekly["WeekIndex"].le(13)
    weekly.insert(0, "CandidateID", candidate_id)
    weekly.insert(1, "SourceWorkbook", source_workbook)
    return weekly[
        [
            "CandidateID",
            "SourceWorkbook",
            "SKU",
            "WeekIndex",
            "WeekStartDate",
            "ForecastUnits",
            "IsFirst13Weeks",
        ]
    ].sort_values(["SKU", "WeekIndex"], kind="mergesort")


def write_frame_with_sample(df: pd.DataFrame, path: Path, sample_rows: int) -> dict[str, Any]:
    """Write contract dataframe to Parquet with a subset sample saved as CSV.

    Args:
        df (pd.DataFrame): Target dataframe.
        path (Path): Destination parquet filepath.
        sample_rows (int): Max rows in the diagnostic CSV.

    Returns:
        dict[str, Any]: Saved files location metadata.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="zstd")
    sample_path = path.with_name(f"{path.stem}_sample.csv")
    df.head(sample_rows).to_csv(sample_path, index=False)
    return {
        "path": str(path),
        "sample_path": str(sample_path),
        "rows": int(len(df)),
        "columns": list(df.columns),
    }


def latest_snapshot(
    df: pd.DataFrame,
    date_col: str,
    as_of_date: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Retrieve the most recent data snapshot available before or on the target date.

    Args:
        df (pd.DataFrame): Dataframe with historical snapshot records.
        date_col (str): Snapshot date column.
        as_of_date (pd.Timestamp): Maximum allowed snapshot date.

    Returns:
        tuple[pd.DataFrame, pd.Timestamp | None]: Eligible snapshot rows, and snapshot date used.
    """
    if df.empty or date_col not in df.columns:
        return df.iloc[0:0].copy(), None
    work = df.copy()
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce").dt.normalize()
    eligible_dates = sorted(date for date in work[date_col].dropna().unique() if date <= as_of_date)
    if not eligible_dates:
        eligible_dates = sorted(work[date_col].dropna().unique())
    if not eligible_dates:
        return work.iloc[0:0].copy(), None
    snapshot_date = pd.Timestamp(eligible_dates[-1]).normalize()
    return work.loc[work[date_col].eq(snapshot_date)].copy(), snapshot_date


def load_direct_pick_signal(
    actuals_path: Path,
    forecast_start: pd.Timestamp,
    lookback_days: int,
) -> tuple[pd.DataFrame, dict[int, float], dict[str, Any]]:
    """Load historical direct pick actuals and calculate daily average velocity.

    Also returns day-of-week demand scaling factors representing normal weekly curves.

    Args:
        actuals_path (Path): Path to actuals parquet.
        forecast_start (pd.Timestamp): Start date of forecast window.
        lookback_days (int): Days window size for history.

    Returns:
        tuple[pd.DataFrame, dict[int, float], dict[str, Any]]: Sku base demand, dow scaling indexes, and stats metadata.
    """
    columns = ["ActualDate", "SKU", "SoldUnits"]
    if not actuals_path.exists():
        return (
            pd.DataFrame(columns=["SKU", "DirectPickLookbackUnits", "DailyBaseUnits", "WeeklyBaseUnits"]),
            {idx: 1.0 for idx in range(7)},
            {"path": str(actuals_path), "status": "missing"},
        )

    window_start = forecast_start - pd.Timedelta(days=lookback_days)
    actuals = pd.read_parquet(actuals_path, columns=columns)
    actuals["ActualDate"] = pd.to_datetime(actuals["ActualDate"], errors="coerce").dt.normalize()
    actuals["SKU"] = normalize_sku_series(actuals["SKU"])
    actuals["SoldUnits"] = pd.to_numeric(actuals["SoldUnits"], errors="coerce").fillna(0).clip(lower=0)
    actuals = actuals.loc[
        actuals["ActualDate"].ge(window_start)
        & actuals["ActualDate"].lt(forecast_start)
        & actuals["SKU"].ne("")
        & actuals["SoldUnits"].gt(0)
    ].copy()
    if actuals.empty:
        return (
            pd.DataFrame(columns=["SKU", "DirectPickLookbackUnits", "DailyBaseUnits", "WeeklyBaseUnits"]),
            {idx: 1.0 for idx in range(7)},
            {
                "path": str(actuals_path),
                "status": "empty_window",
                "window_start": str(window_start.date()),
                "window_end_exclusive": str(forecast_start.date()),
            },
        )

    sku_signal = (
        actuals.groupby("SKU", as_index=False)
        .agg(
            DirectPickLookbackUnits=("SoldUnits", "sum"),
            DirectPickDemandDays=("ActualDate", "nunique"),
        )
        .sort_values("SKU", kind="mergesort")
    )
    sku_signal["DailyBaseUnits"] = sku_signal["DirectPickLookbackUnits"] / float(lookback_days)
    sku_signal["WeeklyBaseUnits"] = sku_signal["DailyBaseUnits"] * 7.0

    calendar = pd.DataFrame({"Date": pd.date_range(window_start, forecast_start - pd.Timedelta(days=1))})
    day_counts = calendar["Date"].dt.dayofweek.value_counts().to_dict()
    overall_daily_units = actuals["SoldUnits"].sum() / float(lookback_days)
    dow_units = actuals.groupby(actuals["ActualDate"].dt.dayofweek)["SoldUnits"].sum()
    dow_factors = {}
    for day_idx in range(7):
        mean_for_day = dow_units.get(day_idx, 0.0) / float(day_counts.get(day_idx, 1))
        factor = mean_for_day / overall_daily_units if overall_daily_units > 0 else 1.0
        dow_factors[day_idx] = float(min(max(factor, 0.65), 1.35))

    metadata = {
        "path": str(actuals_path),
        "status": "loaded",
        "window_start": str(window_start.date()),
        "window_end_exclusive": str(forecast_start.date()),
        "lookback_days": lookback_days,
        "rows_in_window": int(len(actuals)),
        "distinct_skus": int(sku_signal["SKU"].nunique()),
        "sold_units": float(actuals["SoldUnits"].sum()),
        "dow_factors": {str(key): value for key, value in dow_factors.items()},
    }
    return sku_signal, dow_factors, metadata


def same_month_day(year: int, date_value: pd.Timestamp) -> pd.Timestamp:
    """Map a given date to the same month and day in a different calendar year.

    Safely handles Leap Day by rolling back to February 28th if the target year
    does not support February 29th.

    Args:
        year (int): Target year.
        date_value (pd.Timestamp): Reference date.

    Returns:
        pd.Timestamp: Date in the target year.
    """
    day = date_value.day
    while day > 0:
        try:
            return pd.Timestamp(year=year, month=date_value.month, day=day)
        except ValueError:
            day -= 1
    raise ValueError(f"Could not map {date_value.date()} into year {year}")


def load_seasonal_direct_pick_signal(
    actuals_path: Path,
    forecast_start: pd.Timestamp,
    daily_dates: list[pd.Timestamp],
    week_dates: list[pd.Timestamp],
    seasonal_years: int,
    seasonal_window_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Compute historical seasonal actual demand over past years.

    Evaluates sales in matching calendar day windows across historical years to
    form seasonal baseline anchors.

    Args:
        actuals_path (Path): Path to actuals parquet.
        forecast_start (pd.Timestamp): Start date of forecast window.
        daily_dates (list[pd.Timestamp]): Target daily dates.
        week_dates (list[pd.Timestamp]): Target weekly start dates.
        seasonal_years (int): Number of prior years to evaluate.
        seasonal_window_days (int): Half-width of seasonal comparison window.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]: Seasonal daily, weekly records and stats.
    """
    daily_columns = ["SKU", "ForecastDate", "SeasonalDailyUnits", "SeasonalDailyWindowDays"]
    weekly_columns = ["SKU", "WeekStartDate", "SeasonalWeeklyUnits", "SeasonalWeeklyWindowDays"]
    empty_daily = pd.DataFrame(columns=daily_columns)
    empty_weekly = pd.DataFrame(columns=weekly_columns)
    if not actuals_path.exists():
        return empty_daily, empty_weekly, {"path": str(actuals_path), "status": "missing"}

    columns = ["ActualDate", "SKU", "SoldUnits"]
    actuals = pd.read_parquet(actuals_path, columns=columns)
    actuals["ActualDate"] = pd.to_datetime(actuals["ActualDate"], errors="coerce").dt.normalize()
    actuals["SKU"] = normalize_sku_series(actuals["SKU"])
    actuals["SoldUnits"] = pd.to_numeric(actuals["SoldUnits"], errors="coerce").fillna(0).clip(lower=0)
    actuals = actuals.loc[
        actuals["ActualDate"].notna()
        & actuals["ActualDate"].lt(forecast_start)
        & actuals["SKU"].ne("")
        & actuals["SoldUnits"].gt(0)
    ].copy()
    if actuals.empty:
        return empty_daily, empty_weekly, {"path": str(actuals_path), "status": "empty"}

    historical_years = [forecast_start.year - offset for offset in range(1, seasonal_years + 1)]
    min_year = int(actuals["ActualDate"].dt.year.min())
    historical_years = [year for year in historical_years if year >= min_year]

    def aggregate_windows(target_dates: list[pd.Timestamp], output_date_col: str) -> pd.DataFrame:
        frames = []
        for target_date in target_dates:
            historical_ranges = []
            for year in historical_years:
                center = same_month_day(year, target_date)
                start = center - pd.Timedelta(days=seasonal_window_days)
                end = center + pd.Timedelta(days=seasonal_window_days)
                historical_ranges.append((start, end))
            if not historical_ranges:
                continue
            mask = pd.Series(False, index=actuals.index)
            window_days = 0
            for start, end in historical_ranges:
                range_mask = actuals["ActualDate"].between(start, end)
                if range_mask.any():
                    mask = mask | range_mask
                    window_days += int((end - start).days) + 1
            if window_days == 0 or not mask.any():
                continue
            grouped = (
                actuals.loc[mask]
                .groupby("SKU", as_index=False)
                .agg(SeasonalUnits=("SoldUnits", "sum"))
            )
            grouped[output_date_col] = target_date
            grouped["SeasonalWindowDays"] = window_days
            frames.append(grouped)
        if not frames:
            return pd.DataFrame(columns=["SKU", output_date_col, "SeasonalUnits", "SeasonalWindowDays"])
        return pd.concat(frames, ignore_index=True)

    daily = aggregate_windows(daily_dates, "ForecastDate")
    if not daily.empty:
        daily["SeasonalDailyUnits"] = daily["SeasonalUnits"] / daily["SeasonalWindowDays"]
        daily = daily.rename(columns={"SeasonalWindowDays": "SeasonalDailyWindowDays"})
        daily = daily[daily_columns]
    else:
        daily = empty_daily

    weekly = aggregate_windows(week_dates, "WeekStartDate")
    if not weekly.empty:
        weekly["SeasonalWeeklyUnits"] = weekly["SeasonalUnits"] / (
            weekly["SeasonalWindowDays"] / 7.0
        )
        weekly = weekly.rename(columns={"SeasonalWindowDays": "SeasonalWeeklyWindowDays"})
        weekly = weekly[weekly_columns]
    else:
        weekly = empty_weekly

    metadata = {
        "path": str(actuals_path),
        "status": "loaded",
        "historical_years": historical_years,
        "seasonal_window_days_each_side": seasonal_window_days,
        "actual_rows_available": int(len(actuals)),
        "actual_date_min": str(actuals["ActualDate"].min().date()),
        "actual_date_max": str(actuals["ActualDate"].max().date()),
        "daily_rows": int(len(daily)),
        "daily_skus": int(daily["SKU"].nunique()) if not daily.empty else 0,
        "weekly_rows": int(len(weekly)),
        "weekly_skus": int(weekly["SKU"].nunique()) if not weekly.empty else 0,
        "daily_seasonal_units_14d": float(
            pd.to_numeric(daily["SeasonalDailyUnits"], errors="coerce").fillna(0).sum()
        )
        if not daily.empty
        else 0.0,
        "weekly_seasonal_units_26w": float(
            pd.to_numeric(weekly["SeasonalWeeklyUnits"], errors="coerce").fillna(0).sum()
        )
        if not weekly.empty
        else 0.0,
    }
    return daily, weekly, metadata


def promo_multiplier(df: pd.DataFrame) -> pd.Series:
    """Calculate promotional uplift multiplier based on discount percentages.

    Args:
        df (pd.DataFrame): Dataframe containing max discount percentages and promotion flags.

    Returns:
        pd.Series: Numeric series of multiplier values.
    """
    discount = pd.to_numeric(df.get("pdl_sku_max_discount_pct", 0), errors="coerce").fillna(0)
    multiplier = 1.0 + discount.clip(lower=0, upper=1) * PROMO_DISCOUNT_UPLIFT_FACTOR
    has_promo = df.get("HasSkuPDLPromotion", False)
    if isinstance(has_promo, pd.Series):
        has_promo = has_promo.fillna(False).astype(bool)
    multiplier = multiplier.where(~has_promo | discount.gt(0), PROMO_DEFAULT_MULTIPLIER)
    return multiplier.clip(lower=1.0, upper=PROMO_MAX_MULTIPLIER)


def load_promo_signal(
    pdl_path: Path,
    forecast_start: pd.Timestamp,
    week_dates: list[pd.Timestamp],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Parse future promotional features and compile daily and weekly uplifts.

    Args:
        pdl_path (Path): Path to PDL features file.
        forecast_start (pd.Timestamp): Midnight start timestamp.
        week_dates (list[pd.Timestamp]): List of weekly start dates.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]: Daily, weekly promo signals and stats.
    """
    columns = [
        "Date",
        "SKU",
        "pdl_sku_max_discount_pct",
        "pdl_sku_lw_unit_sales",
        "pdl_sku_has_final_sale",
        "pdl_sku_has_tier1_recommendation",
        "pdl_sku_primary_event_name",
        "HasSkuPDLPromotion",
    ]
    daily_end = forecast_start + pd.Timedelta(days=13)
    weekly_end = week_dates[-1] + pd.Timedelta(days=6)
    if not pdl_path.exists():
        empty_daily = pd.DataFrame(columns=["SKU", "ForecastDate", "PromoMultiplier", "PromoDailyFloor"])
        empty_weekly = pd.DataFrame(columns=["SKU", "WeekStartDate", "PromoMultiplier", "PromoWeeklyFloor"])
        return empty_daily, empty_weekly, {"path": str(pdl_path), "status": "missing"}

    promo = pd.read_parquet(pdl_path, columns=columns)
    promo["Date"] = pd.to_datetime(promo["Date"], errors="coerce").dt.normalize()
    promo["SKU"] = normalize_sku_series(promo["SKU"])
    promo = promo.loc[
        promo["Date"].between(forecast_start, weekly_end)
        & promo["SKU"].ne("")
        & promo["HasSkuPDLPromotion"].fillna(False).astype(bool)
    ].copy()
    if promo.empty:
        empty_daily = pd.DataFrame(columns=["SKU", "ForecastDate", "PromoMultiplier", "PromoDailyFloor"])
        empty_weekly = pd.DataFrame(columns=["SKU", "WeekStartDate", "PromoMultiplier", "PromoWeeklyFloor"])
        return (
            empty_daily,
            empty_weekly,
            {
                "path": str(pdl_path),
                "status": "loaded_empty_horizon",
                "horizon_start": str(forecast_start.date()),
                "horizon_end": str(weekly_end.date()),
            },
        )

    promo["PromoMultiplier"] = promo_multiplier(promo)
    promo["PromoDailyFloor"] = (
        pd.to_numeric(promo["pdl_sku_lw_unit_sales"], errors="coerce").fillna(0).clip(lower=0) / 7.0
    )
    daily = (
        promo.loc[promo["Date"].le(daily_end)]
        .groupby(["SKU", "Date"], as_index=False)
        .agg(
            PromoMultiplier=("PromoMultiplier", "max"),
            PromoDailyFloor=("PromoDailyFloor", "max"),
        )
        .rename(columns={"Date": "ForecastDate"})
    )

    week_lookup = pd.DataFrame({"WeekStartDate": week_dates})
    promo["WeekStartDate"] = promo["Date"].apply(
        lambda value: max([week for week in week_dates if week <= value], default=week_dates[0])
    )
    weekly = (
        promo.merge(week_lookup, on="WeekStartDate", how="inner")
        .groupby(["SKU", "WeekStartDate"], as_index=False)
        .agg(
            PromoMultiplier=("PromoMultiplier", "max"),
            PromoWeeklyFloor=("pdl_sku_lw_unit_sales", "max"),
            PromoDays=("Date", "nunique"),
        )
    )
    weekly["PromoWeeklyFloor"] = pd.to_numeric(
        weekly["PromoWeeklyFloor"], errors="coerce"
    ).fillna(0).clip(lower=0)

    metadata = {
        "path": str(pdl_path),
        "status": "loaded",
        "horizon_start": str(forecast_start.date()),
        "horizon_end": str(weekly_end.date()),
        "rows_in_horizon": int(len(promo)),
        "daily_rows": int(len(daily)),
        "weekly_rows": int(len(weekly)),
        "distinct_skus": int(promo["SKU"].nunique()),
        "future_14_day_skus": int(daily["SKU"].nunique()) if not daily.empty else 0,
    }
    return daily, weekly, metadata


def load_inbound_signal(
    inbound_path: Path,
    forecast_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse corporate inbound shipments files.

    Provides coverage/universe attributes to ensure new and returning SKUs are slotted.

    Args:
        inbound_path (Path): Path to open inbound shipments file.
        forecast_start (pd.Timestamp): Midnight start timestamp.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Inbound dataframe, and stats metadata.
    """
    columns = [
        "SnapshotDate",
        "SKU",
        "InboundPastDueUnits",
        "InboundNext7Units",
        "InboundNext8To14Units",
        "InboundNext15To30Units",
        "InboundTotalUnits",
        "HasInboundNext14",
        "HasInboundNext30",
    ]
    if not inbound_path.exists():
        return pd.DataFrame(columns=["SKU"]), {"path": str(inbound_path), "status": "missing"}

    inbound = pd.read_parquet(inbound_path, columns=columns)
    inbound["SKU"] = normalize_sku_series(inbound["SKU"])
    inbound, snapshot_date = latest_snapshot(inbound, "SnapshotDate", forecast_start)
    if inbound.empty:
        return pd.DataFrame(columns=["SKU"]), {"path": str(inbound_path), "status": "empty"}

    numeric_cols = [col for col in columns if col.endswith("Units")]
    for col in numeric_cols:
        inbound[col] = pd.to_numeric(inbound[col], errors="coerce").fillna(0).clip(lower=0)
    inbound = inbound.loc[inbound["SKU"].ne("") & inbound["InboundTotalUnits"].gt(0)].copy()
    inbound["InboundNext14Units"] = inbound["InboundNext7Units"] + inbound["InboundNext8To14Units"]
    inbound["InboundNext30Units"] = inbound["InboundNext14Units"] + inbound["InboundNext15To30Units"]
    inbound = inbound.sort_values("SKU", kind="mergesort")
    metadata = {
        "path": str(inbound_path),
        "status": "loaded",
        "snapshot_date": str(snapshot_date.date()) if snapshot_date is not None else None,
        "distinct_skus": int(inbound["SKU"].nunique()),
        "inbound_total_units": float(inbound["InboundTotalUnits"].sum()),
        "inbound_next14_units": float(inbound["InboundNext14Units"].sum()),
    }
    return inbound, metadata


def load_reservation_signal(
    reservations_path: Path,
    forecast_start: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Parse corporate sales reservation files to extract open-order proxies.

    Restricts demand units only to valid reserve statuses (WMS blank location and
    pickface allocations) to prevent over-forecasting.

    Args:
        reservations_path (Path): Path to reservation parquet.
        forecast_start (pd.Timestamp): Midnight start timestamp.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Cleaned reservation records, and metrics metadata.
    """
    columns = [
        "SnapshotDate",
        "SKU",
        "ReservationPhysicalTotal",
        "ReservationSalesAllocatedPhysicalTotal",
        "ReservationW001",
        "ReservationReserveOrBulk",
        "ReservationStagingOrProcessing",
        "ReservationOperationalLocatedPhysicalTotal",
    ]
    if not reservations_path.exists():
        return pd.DataFrame(columns=["SKU"]), {"path": str(reservations_path), "status": "missing"}

    reservations = pd.read_parquet(reservations_path, columns=columns)
    reservations["SKU"] = normalize_sku_series(reservations["SKU"])
    reservations, snapshot_date = latest_snapshot(reservations, "SnapshotDate", forecast_start)
    if reservations.empty:
        return pd.DataFrame(columns=["SKU"]), {"path": str(reservations_path), "status": "empty"}

    numeric_cols = [col for col in columns if col.startswith("Reservation")]
    for col in numeric_cols:
        reservations[col] = pd.to_numeric(reservations[col], errors="coerce").fillna(0).clip(lower=0)
    reservations["ValidReservationUnits"] = (
        reservations["ReservationPhysicalTotal"]
        + reservations["ReservationSalesAllocatedPhysicalTotal"]
    )
    reservations = reservations.loc[
        reservations["SKU"].ne("") & reservations["ValidReservationUnits"].gt(0)
    ].copy()
    reservations = reservations.sort_values("SKU", kind="mergesort")
    metadata = {
        "path": str(reservations_path),
        "status": "loaded",
        "snapshot_date": str(snapshot_date.date()) if snapshot_date is not None else None,
        "distinct_skus": int(reservations["SKU"].nunique()),
        "valid_reservation_units": float(reservations["ValidReservationUnits"].sum()),
        "blank_location_units": float(reservations["ReservationPhysicalTotal"].sum()),
        "pickface_allocated_units": float(
            reservations["ReservationSalesAllocatedPhysicalTotal"].sum()
        ),
        "excluded_w001_units": float(reservations["ReservationW001"].sum()),
        "excluded_reserve_or_bulk_units": float(reservations["ReservationReserveOrBulk"].sum()),
        "excluded_operational_located_units": float(
            reservations["ReservationOperationalLocatedPhysicalTotal"].sum()
        ),
    }
    return reservations, metadata


def integerize_daily_forecast(daily: pd.DataFrame) -> pd.DataFrame:
    """Integerize daily fractional forecasts to integer units per SKU.

    Uses largest remainder rounding (Hamilton method) to ensure that the sum of
    daily integer forecasts exactly equals the rounded sum of daily fractional forecasts.

    Args:
        daily (pd.DataFrame): Long format daily forecast dataframe containing SKU, ForecastDay, and ForecastUnitsRaw.

    Returns:
        pd.DataFrame: Enriched daily long forecast with integer ForecastUnits.
    """
    pieces = []
    for _, group in daily.sort_values(["SKU", "ForecastDay"], kind="mergesort").groupby("SKU", sort=False):
        work = group.copy()
        raw = pd.to_numeric(work["ForecastUnitsRaw"], errors="coerce").fillna(0).clip(lower=0)
        floors = raw.apply(math.floor).astype(int)
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
                        "ForecastDay": work["ForecastDay"].to_numpy(),
                    }
                )
                .sort_values(["Remainder", "ForecastDay"], ascending=[False, True])
                .head(extra)
            )
            work.loc[remainder_order["index"], "ForecastUnits"] += 1
        pieces.append(work)
    if not pieces:
        daily["ForecastUnits"] = pd.Series(dtype="int64")
        return daily
    return pd.concat(pieces, ignore_index=True)


def build_no_ml_forecasts(
    actuals_path: Path,
    pdl_path: Path,
    inbound_path: Path,
    reservations_path: Path,
    forecast_start: pd.Timestamp,
    lookback_days: int,
    source_universe_skus: pd.DataFrame | None = None,
    include_seasonal_history: bool = False,
    seasonal_years: int = DEFAULT_SEASONAL_YEARS,
    seasonal_window_days: int = DEFAULT_SEASONAL_WINDOW_DAYS,
    seasonal_recent_weight: float = DEFAULT_SEASONAL_RECENT_WEIGHT,
) -> tuple[pd.DataFrame, pd.DataFrame, list[pd.Timestamp], pd.DataFrame, dict[str, Any]]:
    """Build a non-ML baseline forecast blending recent direct picks, reservations, and promotions.

    If enabled, historical seasonal actuals from calendar analogs are blended
    using a weighted average.

    Args:
        actuals_path (Path): Path to actuals parquet.
        pdl_path (Path): Path to PDL features parquet.
        inbound_path (Path): Path to inbound shipments parquet.
        reservations_path (Path): Path to reservations snapshot parquet.
        forecast_start (pd.Timestamp): Midnight start date of forecast.
        lookback_days (int): Prior days window for base actuals.
        source_universe_skus (pd.DataFrame | None, optional): SKUs list. Defaults to None.
        include_seasonal_history (bool, optional): If True, incorporates YoY calendar logic.
        seasonal_years (int, optional): Prior years lookback count.
        seasonal_window_days (int, optional): Half-width calendar window.
        seasonal_recent_weight (float, optional): Weight assigned to recent demand (0 to 1).

    Returns:
        tuple[pd.DataFrame, pd.DataFrame, list[pd.Timestamp], pd.DataFrame, dict[str, Any]]:
            Daily forecast, weekly forecast, week dates list, SKU signal summary, and execution metadata.
    """
    week_dates = forecast_week_dates(forecast_start)
    daily_dates = [forecast_start + pd.Timedelta(days=idx) for idx in range(14)]
    direct_pick, dow_factors, direct_pick_meta = load_direct_pick_signal(
        actuals_path, forecast_start, lookback_days
    )
    if include_seasonal_history:
        seasonal_daily, seasonal_weekly, seasonal_meta = load_seasonal_direct_pick_signal(
            actuals_path,
            forecast_start,
            daily_dates,
            week_dates,
            seasonal_years,
            seasonal_window_days,
        )
    else:
        seasonal_daily = pd.DataFrame(
            columns=["SKU", "ForecastDate", "SeasonalDailyUnits", "SeasonalDailyWindowDays"]
        )
        seasonal_weekly = pd.DataFrame(
            columns=["SKU", "WeekStartDate", "SeasonalWeeklyUnits", "SeasonalWeeklyWindowDays"]
        )
        seasonal_meta = {"status": "disabled"}
    promo_daily, promo_weekly, promo_meta = load_promo_signal(pdl_path, forecast_start, week_dates)
    inbound, inbound_meta = load_inbound_signal(inbound_path, forecast_start)
    reservations, reservations_meta = load_reservation_signal(reservations_path, forecast_start)

    if source_universe_skus is None:
        source_universe_skus = pd.DataFrame(columns=["SKU"])
    source_universe_skus = source_universe_skus[["SKU"]].copy()
    source_universe_skus["SKU"] = normalize_sku_series(source_universe_skus["SKU"])
    source_universe_skus = source_universe_skus.loc[source_universe_skus["SKU"].ne("")].drop_duplicates()

    universe = set(direct_pick.loc[direct_pick["DirectPickLookbackUnits"].gt(0), "SKU"])
    universe.update(source_universe_skus["SKU"].dropna().unique())
    universe.update(promo_daily["SKU"].dropna().unique())
    universe.update(promo_weekly["SKU"].dropna().unique())
    universe.update(inbound["SKU"].dropna().unique())
    universe.update(reservations["SKU"].dropna().unique())
    universe.discard("")
    sku_frame = pd.DataFrame({"SKU": sorted(universe)})

    signal = sku_frame.merge(direct_pick, on="SKU", how="left")
    signal = signal.merge(
        reservations[
            [
                "SKU",
                "ValidReservationUnits",
                "ReservationPhysicalTotal",
                "ReservationSalesAllocatedPhysicalTotal",
            ]
        ],
        on="SKU",
        how="left",
    )
    signal = signal.merge(
        inbound[["SKU", "InboundTotalUnits", "InboundNext14Units", "InboundNext30Units"]],
        on="SKU",
        how="left",
    )
    for col in [
        "DirectPickLookbackUnits",
        "DirectPickDemandDays",
        "DailyBaseUnits",
        "WeeklyBaseUnits",
        "ValidReservationUnits",
        "ReservationPhysicalTotal",
        "ReservationSalesAllocatedPhysicalTotal",
        "InboundTotalUnits",
        "InboundNext14Units",
        "InboundNext30Units",
    ]:
        if col in signal.columns:
            signal[col] = pd.to_numeric(signal[col], errors="coerce").fillna(0)
    signal["HasDirectPickHistory"] = signal["DirectPickLookbackUnits"].gt(0)
    signal["HasValidReservation"] = signal["ValidReservationUnits"].gt(0)
    signal["HasInbound"] = signal["InboundTotalUnits"].gt(0)
    signal["InSourceProductInfoUniverse"] = signal["SKU"].isin(set(source_universe_skus["SKU"]))

    daily_frames = []
    for day_idx in range(1, 15):
        forecast_date = forecast_start + pd.Timedelta(days=day_idx - 1)
        frame = signal[["SKU", "DailyBaseUnits", "ValidReservationUnits"]].copy()
        frame["ForecastDay"] = day_idx
        frame["ForecastDate"] = forecast_date
        frame["RecentDailyUnits"] = (
            frame["DailyBaseUnits"] * dow_factors.get(int(forecast_date.dayofweek), 1.0)
        )
        seasonal_for_day = seasonal_daily.loc[seasonal_daily["ForecastDate"].eq(forecast_date)]
        if not seasonal_for_day.empty:
            frame = frame.merge(seasonal_for_day, on=["SKU", "ForecastDate"], how="left")
            frame["SeasonalDailyUnits"] = pd.to_numeric(
                frame["SeasonalDailyUnits"], errors="coerce"
            ).fillna(0)
        else:
            frame["SeasonalDailyUnits"] = 0.0
        has_recent = frame["RecentDailyUnits"].gt(0)
        has_seasonal = frame["SeasonalDailyUnits"].gt(0)
        seasonal_weight = 1.0 - seasonal_recent_weight
        frame["ForecastUnitsRaw"] = frame["RecentDailyUnits"]
        both = has_recent & has_seasonal
        frame.loc[both, "ForecastUnitsRaw"] = (
            frame.loc[both, "RecentDailyUnits"] * seasonal_recent_weight
            + frame.loc[both, "SeasonalDailyUnits"] * seasonal_weight
        )
        seasonal_only = ~has_recent & has_seasonal
        frame.loc[seasonal_only, "ForecastUnitsRaw"] = frame.loc[
            seasonal_only, "SeasonalDailyUnits"
        ]
        promo_for_day = promo_daily.loc[promo_daily["ForecastDate"].eq(forecast_date)]
        if not promo_for_day.empty:
            frame = frame.merge(promo_for_day, on=["SKU", "ForecastDate"], how="left")
            frame["PromoMultiplier"] = frame["PromoMultiplier"].fillna(1.0)
            frame["PromoDailyFloor"] = frame["PromoDailyFloor"].fillna(0)
            frame["ForecastUnitsRaw"] = (
                frame["ForecastUnitsRaw"] * frame["PromoMultiplier"]
            ).where(
                frame["ForecastUnitsRaw"] * frame["PromoMultiplier"] >= frame["PromoDailyFloor"],
                frame["PromoDailyFloor"],
            )
        reservation_weight = RESERVATION_DAILY_WEIGHTS.get(day_idx, 0.0)
        if reservation_weight:
            reservation_floor = frame["ValidReservationUnits"] * reservation_weight
            frame["ForecastUnitsRaw"] = frame["ForecastUnitsRaw"].where(
                frame["ForecastUnitsRaw"] >= reservation_floor,
                reservation_floor,
            )
        daily_frames.append(frame[["SKU", "ForecastDay", "ForecastDate", "ForecastUnitsRaw"]])

    daily_long = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    daily_long = integerize_daily_forecast(daily_long)
    df_14day = (
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
        if col not in df_14day.columns:
            df_14day[col] = 0
        df_14day[col] = pd.to_numeric(df_14day[col], errors="coerce").fillna(0).astype(int)
    df_14day = df_14day[["SKU", *FD_COLUMNS]].sort_values("SKU", kind="mergesort")

    weekly = signal[["SKU", "WeeklyBaseUnits", "ValidReservationUnits"]].copy()
    for week_date in week_dates:
        recent_weekly = weekly["WeeklyBaseUnits"].copy()
        seasonal_for_week = seasonal_weekly.loc[seasonal_weekly["WeekStartDate"].eq(week_date)]
        if not seasonal_for_week.empty:
            weekly = weekly.merge(
                seasonal_for_week[["SKU", "SeasonalWeeklyUnits"]],
                on="SKU",
                how="left",
            )
            weekly["SeasonalWeeklyUnits"] = pd.to_numeric(
                weekly["SeasonalWeeklyUnits"], errors="coerce"
            ).fillna(0)
        else:
            weekly["SeasonalWeeklyUnits"] = 0.0
        has_recent = recent_weekly.gt(0)
        has_seasonal = weekly["SeasonalWeeklyUnits"].gt(0)
        weekly[week_date] = recent_weekly
        both = has_recent & has_seasonal
        weekly.loc[both, week_date] = (
            recent_weekly.loc[both] * seasonal_recent_weight
            + weekly.loc[both, "SeasonalWeeklyUnits"] * (1.0 - seasonal_recent_weight)
        )
        seasonal_only = ~has_recent & has_seasonal
        weekly.loc[seasonal_only, week_date] = weekly.loc[
            seasonal_only, "SeasonalWeeklyUnits"
        ]
        weekly = weekly.drop(columns=["SeasonalWeeklyUnits"])
    if not promo_weekly.empty:
        for week_date in week_dates:
            promo_for_week = promo_weekly.loc[promo_weekly["WeekStartDate"].eq(week_date)]
            if promo_for_week.empty:
                continue
            weekly = weekly.merge(
                promo_for_week[["SKU", "PromoMultiplier", "PromoWeeklyFloor"]],
                on="SKU",
                how="left",
            )
            base = weekly[week_date] * weekly["PromoMultiplier"].fillna(1.0)
            floor = weekly["PromoWeeklyFloor"].fillna(0)
            weekly[week_date] = base.where(base >= floor, floor)
            weekly = weekly.drop(columns=["PromoMultiplier", "PromoWeeklyFloor"])
    first_week = week_dates[0]
    weekly[first_week] = weekly[first_week].where(
        weekly[first_week] >= weekly["ValidReservationUnits"],
        weekly["ValidReservationUnits"],
    )
    df_weekly = weekly[["SKU", *week_dates]].sort_values("SKU", kind="mergesort")

    signal = signal.merge(
        promo_daily.groupby("SKU", as_index=False).agg(Future14DayPromoDays=("ForecastDate", "nunique")),
        on="SKU",
        how="left",
    )
    signal = signal.merge(
        promo_weekly.groupby("SKU", as_index=False).agg(FutureWeeklyPromoWeeks=("WeekStartDate", "nunique")),
        on="SKU",
        how="left",
    )
    signal["Future14DayPromoDays"] = signal["Future14DayPromoDays"].fillna(0).astype(int)
    signal["FutureWeeklyPromoWeeks"] = signal["FutureWeeklyPromoWeeks"].fillna(0).astype(int)
    signal["HasFuturePromo"] = signal["Future14DayPromoDays"].gt(0) | signal[
        "FutureWeeklyPromoWeeks"
    ].gt(0)
    seasonal_daily_summary = (
        seasonal_daily.groupby("SKU", as_index=False)
        .agg(Seasonal14DayUnits=("SeasonalDailyUnits", "sum"))
        if not seasonal_daily.empty
        else pd.DataFrame(columns=["SKU", "Seasonal14DayUnits"])
    )
    seasonal_weekly_summary = (
        seasonal_weekly.loc[seasonal_weekly["WeekStartDate"].isin(week_dates[:13])]
        .groupby("SKU", as_index=False)
        .agg(SeasonalFirst13WeekUnits=("SeasonalWeeklyUnits", "sum"))
        if not seasonal_weekly.empty
        else pd.DataFrame(columns=["SKU", "SeasonalFirst13WeekUnits"])
    )
    signal = signal.merge(seasonal_daily_summary, on="SKU", how="left")
    signal = signal.merge(seasonal_weekly_summary, on="SKU", how="left")
    signal["Seasonal14DayUnits"] = signal["Seasonal14DayUnits"].fillna(0)
    signal["SeasonalFirst13WeekUnits"] = signal["SeasonalFirst13WeekUnits"].fillna(0)
    signal["HasSeasonalHistory"] = signal["Seasonal14DayUnits"].gt(0) | signal[
        "SeasonalFirst13WeekUnits"
    ].gt(0)
    signal["FD1ToFD14Units"] = df_14day.set_index("SKU")[FD_COLUMNS].sum(axis=1).reindex(
        signal["SKU"]
    ).fillna(0).to_numpy()
    signal["First13WeekUnits"] = df_weekly.set_index("SKU")[week_dates[:13]].sum(axis=1).reindex(
        signal["SKU"]
    ).fillna(0).to_numpy()

    metadata = {
        "method": "seasonal_no_ml_baseline" if include_seasonal_history else "no_ml_baseline",
        "forecast_start_date": str(forecast_start.date()),
        "weekly_first_date": str(week_dates[0].date()),
        "weekly_weeks": len(week_dates),
        "lookback_days": lookback_days,
        "seasonal": seasonal_meta,
        "seasonal_blend": {
            "enabled": include_seasonal_history,
            "recent_weight_when_both_exist": seasonal_recent_weight,
            "seasonal_weight_when_both_exist": 1.0 - seasonal_recent_weight,
        },
        "direct_pick": direct_pick_meta,
        "promotions": promo_meta,
        "inbound": inbound_meta,
        "reservations": reservations_meta,
        "universe": {
            "distinct_skus": int(len(sku_frame)),
            "in_source_product_info_universe": int(signal["InSourceProductInfoUniverse"].sum()),
            "with_direct_pick_history": int(signal["HasDirectPickHistory"].sum()),
            "with_seasonal_history": int(signal["HasSeasonalHistory"].sum()),
            "with_future_promo": int(signal["HasFuturePromo"].sum()),
            "with_inbound": int(signal["HasInbound"].sum()),
            "with_valid_reservation": int(signal["HasValidReservation"].sum()),
        },
        "forecast_totals": {
            "fd1_to_fd14_units": float(df_14day[FD_COLUMNS].sum().sum()),
            "first_13_week_units": float(df_weekly[week_dates[:13]].sum().sum()),
        },
        "notes": [
            "DirectPick actuals are the base fulfilled-demand rate.",
            "Seasonal history blends same-calendar prior-year DirectPick demand when enabled.",
            "PDL promotion rows apply a simple discount-based uplift and last-week-sales floor.",
            "Valid reservation demand is limited to blank-location open-order proxy plus pickface allocation.",
            "W001, reserve/bulk, and process-area reservations are excluded from demand.",
            "Inbound is a coverage/universe signal only; it does not create demand units by itself.",
        ],
    }
    return df_14day, df_weekly, week_dates, signal, metadata


def excel_value(value: Any) -> Any:
    """Convert pandas/numpy missing values and timestamps to Excel-compatible formats.

    Args:
        value: Input scalar value to be formatted.

    Returns:
        The formatted value, converting Timestamp to datetime and na/NaN/NaT to None.
    """
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def write_rows(
    worksheet: xlsxwriter.worksheet.Worksheet,
    start_row: int,
    start_col: int,
    rows: pd.DataFrame,
) -> None:
    """Bulk write a pandas DataFrame to an xlsxwriter worksheet using row tuples.

    Args:
        worksheet: Target sheet to write to.
        start_row: Starting row index (0-indexed).
        start_col: Starting column index (0-indexed).
        rows: Dataframe containing rows to write.
    """
    for row_offset, values in enumerate(rows.itertuples(index=False, name=None)):
        worksheet.write_row(start_row + row_offset, start_col, [excel_value(value) for value in values])


def write_brg_workbook(
    workbook_path: Path,
    df_weekly: pd.DataFrame,
    week_dates: list[pd.Timestamp],
    df_14day: pd.DataFrame,
    forecast_start: pd.Timestamp,
    df_hier: pd.DataFrame,
    df_status: pd.DataFrame,
    df_load: pd.DataFrame,
    df_on_hand_location: pd.DataFrame,
) -> None:
    """Generate a corporate-style BRG forecast template workbook from raw forecast data.

    This function simulates the structured sheets that the legacy active storage
    and zoning-slotting pipelines expect. It formats datetime values and prints
    metadata mimicking the SharePoint master download output.

    Args:
        workbook_path: Destination path for the Excel workbook.
        df_weekly: Weekly forecast data by SKU.
        week_dates: Dates corresponding to the weekly columns.
        df_14day: Daily forecast data for the 14-day horizon.
        forecast_start: Start date of the daily forecast period.
        df_hier: SKU product hierarchy metadata.
        df_status: SKU workflow status records.
        df_load: Target load details and expected maximum inventory.
        df_on_hand_location: Location-level inventory counts.
    """
    workbook_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(
        str(workbook_path),
        # Use constant_memory to handle large SKU volumes without blowing up RAM footprint
        {"constant_memory": True, "strings_to_urls": False, "nan_inf_to_errors": True},
    )
    date_format = workbook.add_format({"num_format": "m/d/yyyy"})
    datetime_format = workbook.add_format({"num_format": "m/d/yyyy h:mm:ss AM/PM"})

    # Write "LAST REFRESHED" worksheet metadata
    ws = workbook.add_worksheet("LAST REFRESHED")
    ws.write(1, 2, "On Hand Location data current as of:")
    ws.write(3, 2, "Last Refreshed UTC")
    ws.write_datetime(4, 2, datetime.now(), datetime_format)

    # Write "Product Forecast Tool by Week" worksheet
    ws = workbook.add_worksheet("Product Forecast Tool by Week")
    ws.write(0, 0, "FiscalYear_Month")
    ws.write(0, 1, "All")
    ws.write(0, 2, "No-ML baseline forecast generated from local forecast replacement facts")
    ws.write(2, 0, "6 Months Fwd (Net Adj Inv Unit (SKU)")
    ws.write(2, 1, "Column Labels")
    ws.write(3, 0, "Row Labels")
    for idx, week_date in enumerate(week_dates, start=1):
        ws.write_datetime(3, idx, week_date.to_pydatetime(), date_format)
    ws.write(3, len(week_dates) + 1, "Grand Total")
    for row_idx, row in enumerate(df_weekly.itertuples(index=False, name=None), start=4):
        sku = row[0]
        values = list(row[1:])
        ws.write(row_idx, 0, sku)
        ws.write_row(row_idx, 1, values)
        ws.write(row_idx, len(week_dates) + 1, float(sum(values)))

    # Write "SKU Level 14 Day Forecast" worksheet
    ws = workbook.add_worksheet("SKU Level 14 Day Forecast")
    ws.write_datetime(1, 1, forecast_start.to_pydatetime(), date_format)
    for idx in range(2, 15):
        ws.write_datetime(1, idx, (forecast_start + pd.Timedelta(days=idx - 1)).to_pydatetime(), date_format)
    ws.write(2, 0, "SKU")
    for idx in range(1, 15):
        ws.write(2, idx, f"D{idx}")
    write_rows(ws, 3, 0, df_14day[["SKU", *FD_COLUMNS]])

    # Write "Product Attributes" worksheet
    ws = workbook.add_worksheet("Product Attributes")
    left_headers = [
        "Offer",
        "SKU",
        "Division",
        "Department",
        "Class",
        "Key Category View",
        "SizeGroup",
        "Go Live Date",
        "Offer Count",
    ]
    left_brackets = [
        "Product Detail[Offer]",
        "Product Detail[SKU]",
        "Product Hierarchy[Division]",
        "Product Hierarchy[Department]",
        "Product Hierarchy[Class]",
        "Product Categories[Key Category View]",
        "Product Detail[SizeGroup]",
        "Product Attributes[Go Live Date]",
        "[Offer_Count]",
    ]
    status_headers = ["SKU", "Type", "Date"]
    status_brackets = ["Status[SKU]", "Status[Type]", "Status[Date]"]
    ws.write_row(3, 0, left_headers)
    ws.write_row(4, 0, left_brackets)
    ws.write_row(3, 10, status_headers)
    ws.write_row(4, 10, status_brackets)
    hier_out = df_hier[
        [
            "Offer",
            "SKU",
            "Division",
            "Department",
            "Class",
            "KeyCategoryView",
            "SizeGroup",
            "GoLiveDate",
            "OfferCount",
        ]
    ].copy()
    status_out = df_status[["StatusSKU", "ProductStatus", "ProductStatusDate"]].copy()
    hier_rows = list(hier_out.itertuples(index=False, name=None))
    status_rows = list(status_out.itertuples(index=False, name=None))
    for row_offset in range(max(len(hier_rows), len(status_rows))):
        if row_offset < len(hier_rows):
            ws.write_row(5 + row_offset, 0, [excel_value(value) for value in hier_rows[row_offset]])
        if row_offset < len(status_rows):
            ws.write_row(5 + row_offset, 10, [excel_value(value) for value in status_rows[row_offset]])

    # Write "Load Data" worksheet
    ws = workbook.add_worksheet("Load Data")
    ws.write_row(1, 0, ["Load", "LoadShipDate", "LoadETA", "PurchId", "SKU", "LicensePlate", "LP Units"])
    load_out = pd.DataFrame(
        {
            "Load": "NO_ML_BASELINE",
            "LoadShipDate": "",
            "LoadETA": "",
            "PurchId": "",
            "SKU": df_load["SKU"],
            "LicensePlate": "",
            "LP Units": df_load["LoadMaxQty"],
        }
    )
    write_rows(ws, 2, 0, load_out)

    # Write "On Hand by Location" worksheet
    ws = workbook.add_worksheet("On Hand by Location")
    ws.write_row(
        2,
        11,
        [
            "WMSLOCATIONID",
            "SORTCODE",
            "AISLEID",
            "LOCPROFILEID",
            "ZONEID",
            "INVENTSTATUSID",
            "LICENSEPLATEID",
            "SKU",
            "Physical",
        ],
    )
    on_hand_out = pd.DataFrame(
        {
            "WMSLOCATIONID": df_on_hand_location["WMSLOCATIONID"],
            "SORTCODE": "",
            "AISLEID": "",
            "LOCPROFILEID": df_on_hand_location["LOCPROFILEID"],
            "ZONEID": df_on_hand_location["ZONEID"],
            "INVENTSTATUSID": "",
            "LICENSEPLATEID": "",
            "SKU": df_on_hand_location["SKU"],
            "Physical": df_on_hand_location["Physical"],
        }
    )
    write_rows(ws, 3, 11, on_hand_out)

    workbook.close()


def write_no_ml_baseline_contract(
    source_file: Path,
    candidate_dir: Path,
    candidate_id: str,
    candidate_type: str,
    sample_rows: int,
    forecast_start: pd.Timestamp,
    lookback_days: int,
    actuals_path: Path,
    pdl_path: Path,
    inbound_path: Path,
    reservations_path: Path,
    seasonal_years: int = DEFAULT_SEASONAL_YEARS,
    seasonal_window_days: int = DEFAULT_SEASONAL_WINDOW_DAYS,
    seasonal_recent_weight: float = DEFAULT_SEASONAL_RECENT_WEIGHT,
) -> dict[str, Any]:
    """Execute the no-ML baseline contract pipeline.

    Reads the reference product attributes, builds baseline no-ML forecasts, generates
    the target BRG Excel workbook structure, writes the contract parquets, and saves the
    candidate JSON metadata contract.

    Args:
        source_file: Path to corporate planner workbook.
        candidate_dir: Base directory to save candidate outputs.
        candidate_id: Unique candidate identification string.
        candidate_type: Type descriptor of the baseline candidate.
        sample_rows: Max number of preview rows to include in logs.
        forecast_start: Midnight timestamp marking the forecast origin.
        lookback_days: Length of baseline actuals demand lookback.
        actuals_path: Path to historical actuals parquet.
        pdl_path: Path to promotion demand ledger parquet.
        inbound_path: Path to inbound shipments parquet.
        reservations_path: Path to reservations snapshot parquet.
        seasonal_years: Number of years to look back for seasonal blending.
        seasonal_window_days: Day window offset width for YoY seasonal matching.
        seasonal_recent_weight: Blend weight (0 to 1) given to recent demand actuals.

    Returns:
        A dictionary containing candidate contract metadata details.
    """
    input_dir = candidate_dir / "input"
    contract_dir = candidate_dir / "contract"
    input_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = input_dir / f"{source_file.stem}__{candidate_id}.xlsx"

    df_hier, df_status = ingestion.read_product_attributes(source_file)
    df_load = ingestion.read_load_data(source_file)
    df_on_hand_location = ingestion.read_on_hand_location_block(source_file)
    source_weekly, _, _ = ingestion.read_weekly_forecast(source_file)
    source_14day, _ = ingestion.read_14day_forecast(source_file)
    source_universe_skus = (
        pd.concat([source_weekly[["SKU"]], source_14day[["SKU"]]], ignore_index=True)
        .drop_duplicates("SKU")
        .reset_index(drop=True)
    )
    include_seasonal_history = candidate_type == "seasonal_no_ml_baseline"
    df_14day, df_weekly, week_dates, signal, baseline_meta = build_no_ml_forecasts(
        actuals_path,
        pdl_path,
        inbound_path,
        reservations_path,
        forecast_start,
        lookback_days,
        source_universe_skus=source_universe_skus,
        include_seasonal_history=include_seasonal_history,
        seasonal_years=seasonal_years,
        seasonal_window_days=seasonal_window_days,
        seasonal_recent_weight=seasonal_recent_weight,
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

    daily = build_daily_contract(df_14day, forecast_start, candidate_id, workbook_path.name)
    weekly = build_weekly_contract(df_weekly, week_dates, week_dates, candidate_id, workbook_path.name)
    outputs = {
        "daily_forecast": write_frame_with_sample(
            daily,
            contract_dir / "daily_forecast.parquet",
            sample_rows,
        ),
        "weekly_forecast": write_frame_with_sample(
            weekly,
            contract_dir / "weekly_forecast.parquet",
            sample_rows,
        ),
        "product_hierarchy": write_frame_with_sample(
            df_hier,
            contract_dir / "product_hierarchy.parquet",
            sample_rows,
        ),
        "product_status": write_frame_with_sample(
            df_status,
            contract_dir / "product_status.parquet",
            sample_rows,
        ),
        "signal_sku_summary": write_frame_with_sample(
            signal,
            contract_dir / "signal_sku_summary.parquet",
            sample_rows,
        ),
    }
    contract = {
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_workbook": str(source_file),
        "source_workbook_name": source_file.name,
        "source_workbook_sha256": file_sha256(source_file),
        "clone_workbook": str(workbook_path),
        "clone_workbook_sha256": file_sha256(workbook_path),
        "forecast_start_date": str(forecast_start.date()),
        "required_candidate_tables": {
            "daily_forecast": [
                "CandidateID",
                "SourceWorkbook",
                "ForecastStartDate",
                "SKU",
                "ForecastDay",
                "ForecastDate",
                "ForecastUnits",
            ],
            "weekly_forecast": [
                "CandidateID",
                "SourceWorkbook",
                "SKU",
                "WeekIndex",
                "WeekStartDate",
                "ForecastUnits",
                "IsFirst13Weeks",
            ],
            "signal_sku_summary": list(signal.columns),
        },
        "ax_forward_demand_columns": AX_FORWARD_DEMAND_COLUMNS,
        "baseline_method": baseline_meta,
        "outputs": outputs,
        "notes": [
            "This package is a deterministic no-ML baseline for the reset replacement harness.",
            "It is not a model comparison artifact; it only proves a baseline candidate can produce the BRG-like contract.",
            "Seasonal history can shape current/recent/forward-signal SKUs but cannot add SKU rows by itself.",
            "The generated workbook is local/generated output and should not be committed.",
        ],
    }
    (contract_dir / "candidate_contract.json").write_text(
        json.dumps(contract, indent=2),
        encoding="utf-8",
    )
    return contract


def write_candidate_contract(
    source_file: Path,
    candidate_dir: Path,
    candidate_id: str,
    sample_rows: int,
) -> dict[str, Any]:
    """Clones a corporate forecast workbook and registers it as a candidate contract.

    Args:
        source_file: Path to corporate planner workbook.
        candidate_dir: Target output folder path for candidate registration.
        candidate_id: Unique candidate ID string.
        sample_rows: Maximum rows to preview in diagnostic tables.

    Returns:
        A dictionary containing contract metadata details.
    """
    input_dir = candidate_dir / "input"
    contract_dir = candidate_dir / "contract"
    input_dir.mkdir(parents=True, exist_ok=True)
    clone_path = input_dir / f"{source_file.stem}__{candidate_id}.xlsx"
    shutil.copy2(source_file, clone_path)

    df_weekly, week_dates, week_columns = ingestion.read_weekly_forecast(clone_path)
    df_14day, forecast_start_raw = ingestion.read_14day_forecast(clone_path)
    df_hier, df_status = ingestion.read_product_attributes(clone_path)
    forecast_start = normalize_forecast_start(forecast_start_raw)

    daily = build_daily_contract(df_14day, forecast_start, candidate_id, clone_path.name)
    weekly = build_weekly_contract(df_weekly, week_dates, week_columns, candidate_id, clone_path.name)

    outputs = {
        "daily_forecast": write_frame_with_sample(
            daily,
            contract_dir / "daily_forecast.parquet",
            sample_rows,
        ),
        "weekly_forecast": write_frame_with_sample(
            weekly,
            contract_dir / "weekly_forecast.parquet",
            sample_rows,
        ),
        "product_hierarchy": write_frame_with_sample(
            df_hier,
            contract_dir / "product_hierarchy.parquet",
            sample_rows,
        ),
        "product_status": write_frame_with_sample(
            df_status,
            contract_dir / "product_status.parquet",
            sample_rows,
        ),
    }

    contract = {
        "candidate_id": candidate_id,
        "candidate_type": "corporate_workbook_clone",
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_workbook": str(source_file),
        "source_workbook_name": source_file.name,
        "source_workbook_sha256": file_sha256(source_file),
        "clone_workbook": str(clone_path),
        "clone_workbook_sha256": file_sha256(clone_path),
        "forecast_start_date": str(forecast_start.date()),
        "required_candidate_tables": {
            "daily_forecast": [
                "CandidateID",
                "SourceWorkbook",
                "ForecastStartDate",
                "SKU",
                "ForecastDay",
                "ForecastDate",
                "ForecastUnits",
            ],
            "weekly_forecast": [
                "CandidateID",
                "SourceWorkbook",
                "SKU",
                "WeekIndex",
                "WeekStartDate",
                "ForecastUnits",
                "IsFirst13Weeks",
            ],
        },
        "ax_forward_demand_columns": AX_FORWARD_DEMAND_COLUMNS,
        "outputs": outputs,
        "notes": [
            "This package is a corporate workbook clone used to prove the replacement harness.",
            "Future model candidates should produce the daily and weekly forecast contract tables before ingestion scoring.",
            "The cloned workbook is local/generated output and should not be committed.",
        ],
    }
    (contract_dir / "candidate_contract.json").write_text(
        json.dumps(contract, indent=2),
        encoding="utf-8",
    )
    return contract


def run_ingestion_roundtrip(candidate_dir: Path, clone_workbook: Path) -> dict[str, Any]:
    """Execute ingestion pipeline sub-process on generated candidate workbook.

    Args:
        candidate_dir: Folder path where logs and outputs are placed.
        clone_workbook: Path to the generated Excel workbook candidate.

    Returns:
        A dictionary summarizing roundtrip evaluation parameters.
    """
    ingestion_output_dir = candidate_dir / "ingestion_output"
    log_dir = candidate_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        str(PYTHON_DIR / "ingestion_pipeline.py"),
        "--source-file",
        str(clone_workbook),
        "--output-dir",
        str(ingestion_output_dir),
    ]
    result = subprocess.run(  # noqa: S603 - command is fixed to local Python script plus paths.
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    (log_dir / "ingestion_stdout.log").write_text(result.stdout, encoding="utf-8")
    (log_dir / "ingestion_stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        tail = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-40:])
        raise RuntimeError(f"Ingestion round-trip failed with code {result.returncode}:\n{tail}")

    fwd_paths = sorted(ingestion_output_dir.glob("FwdDemandCSV_*.csv"))
    slots_paths = sorted(ingestion_output_dir.glob("RequiredSlots_*.csv"))
    if not fwd_paths:
        raise FileNotFoundError(f"No FwdDemandCSV output found in {ingestion_output_dir}")
    if not slots_paths:
        raise FileNotFoundError(f"No RequiredSlots output found in {ingestion_output_dir}")

    fwd_path = fwd_paths[-1]
    slots_path = slots_paths[-1]
    df_fwd = pd.read_csv(fwd_path, dtype=str, keep_default_na=False)
    df_slots = pd.read_csv(slots_path)

    missing_columns = [col for col in AX_FORWARD_DEMAND_COLUMNS if col not in df_fwd.columns]
    extra_columns = [col for col in df_fwd.columns if col not in AX_FORWARD_DEMAND_COLUMNS]
    duplicate_keys = int(
        df_fwd.assign(
            _key=df_fwd["Item"].astype(str).str.upper()
            + "|"
            + df_fwd["Color"].astype(str).str.upper()
            + "|"
            + df_fwd["Size"].astype(str).str.upper()
        )["_key"].duplicated().sum()
    )
    fd_units = float(df_fwd[FD_COLUMNS].apply(pd.to_numeric, errors="coerce").fillna(0).sum().sum())
    slot_total = float(pd.to_numeric(df_slots["TotalRequiredSlots"], errors="coerce").fillna(0).sum())

    summary = {
        "roundtrip_status": "pass",
        "command": command,
        "ingestion_output_dir": str(ingestion_output_dir),
        "forward_demand_csv": str(fwd_path),
        "required_slots_csv": str(slots_path),
        "forward_demand_rows": int(len(df_fwd)),
        "forward_demand_columns": int(len(df_fwd.columns)),
        "missing_forward_demand_columns": missing_columns,
        "extra_forward_demand_columns": extra_columns,
        "duplicate_item_color_size_keys": duplicate_keys,
        "fd1_to_fd14_units": fd_units,
        "required_slot_tiers": int(len(df_slots)),
        "required_slots_total": slot_total,
        "logs": {
            "stdout": str(log_dir / "ingestion_stdout.log"),
            "stderr": str(log_dir / "ingestion_stderr.log"),
        },
    }
    if missing_columns or extra_columns or duplicate_keys:
        summary["roundtrip_status"] = "fail"
        raise ValueError(f"Round-trip validation failed: {summary}")
    return summary


def main() -> None:
    """Execute forecast replacement candidate contract generation.

    Parses configuration flags, identifies the appropriate source workbook, resolves the
    output directory structure, generates the contract, and invokes the ingestion validation
    roundtrip steps.
    """
    args = parse_args()
    source_file = choose_source(args.source_file)
    default_prefix = args.candidate_type
    candidate_id = args.candidate_id or f"{default_prefix}_{datetime.now():%Y-%m-%d_%H%M%S}"
    candidate_dir = args.output_root / candidate_id
    if candidate_dir.exists():
        raise FileExistsError(f"Candidate output already exists: {candidate_dir}")
    candidate_dir.mkdir(parents=True)

    if args.candidate_type == "corporate_clone":
        contract = write_candidate_contract(
            source_file,
            candidate_dir,
            candidate_id,
            args.sample_rows,
        )
    else:
        contract = write_no_ml_baseline_contract(
            source_file,
            candidate_dir,
            candidate_id,
            args.candidate_type,
            args.sample_rows,
            normalize_optional_date(args.forecast_start_date),
            args.lookback_days,
            args.actuals_path,
            args.pdl_sku_features_path,
            args.inbound_path,
            args.reservations_path,
            args.seasonal_years,
            args.seasonal_window_days,
            args.seasonal_recent_weight,
        )
    roundtrip = run_ingestion_roundtrip(candidate_dir, Path(contract["clone_workbook"]))
    summary = {
        "candidate_id": candidate_id,
        "candidate_type": args.candidate_type,
        "candidate_dir": str(candidate_dir),
        "contract": str(candidate_dir / "contract" / "candidate_contract.json"),
        "roundtrip": roundtrip,
    }
    summary_path = candidate_dir / "roundtrip_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    pd.DataFrame([roundtrip]).to_csv(candidate_dir / "roundtrip_summary.csv", index=False)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
