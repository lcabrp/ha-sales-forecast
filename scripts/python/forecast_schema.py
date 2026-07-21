"""Forecast-owned schema constants and identifier normalization.

This module intentionally contains no Product Info workbook parsing, AX output
generation, slotting logic, or ingestion side effects. Those responsibilities
belong to ``ha-ingestion-pipeline``. Forecast research only needs the stable
shape of an already-produced Forward Demand CSV and a canonical SKU key.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


FD_COLUMNS = [f"FD{day}" for day in range(1, 15)]
DEFAULT_LOOKBACK_DAYS = 56
DEFAULT_SEASONAL_YEARS = 3
DEFAULT_SEASONAL_WINDOW_DAYS = 7
DEFAULT_SEASONAL_RECENT_WEIGHT = 0.65
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


def normalize_sku(value: Any) -> str:
    """Normalize a consumed SKU key without owning ingestion classification."""
    if pd.isna(value):
        return ""
    raw = str(value).strip()
    if "-" not in raw:
        return raw
    parts = raw.split("-")
    item = parts[0].strip()
    if not item:
        return ""
    color = parts[1].strip().upper() if len(parts) >= 2 else ""
    size = "-".join(parts[2:]).strip().upper() if len(parts) >= 3 else ""
    if not color:
        return f"{item}--{size}" if size else f"{item}--"
    if not size:
        return f"{item}-{color}"
    return f"{item}-{color}-{size}"


def normalize_sku_series(series: pd.Series) -> pd.Series:
    """Normalize a pandas SKU series to the forecast comparison key."""
    return series.fillna("").map(normalize_sku)


def same_month_day(year: int, date_value: pd.Timestamp) -> pd.Timestamp:
    """Map a date to another year, rolling leap day back when necessary."""
    day = date_value.day
    while day > 0:
        try:
            return pd.Timestamp(year=year, month=date_value.month, day=day)
        except ValueError:
            day -= 1
    raise ValueError(f"Could not map {date_value.date()} into year {year}")
