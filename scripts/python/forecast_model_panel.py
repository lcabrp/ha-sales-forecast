"""Build the first model-ready SKU/day forecast panel.

This script joins the ingredients we have now:

- fulfilled DirectPick demand from forecast-accuracy actuals
- AX sales-order demand plus realized price/discount signals
- corporate SKU/day forecast snapshots
- daily promotion calendar features
- latest SKU hierarchy/slotting attributes from forecast snapshots

The panel is intentionally sparse.  A SKU/date row exists when that SKU has
actual fulfilled demand, ordered demand, or corporate forecast demand.  That is
the right first contract for model experiments because a full SKU x date grid
would be much larger and would need explicit lifecycle/assortment rules before
zero rows have clear business meaning.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


FORECAST_ACCURACY_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy"
HISTORY_PARQUET_DIR = FORECAST_ACCURACY_DIR / "history" / "parquet"
PROMOTIONS_DIR = FORECAST_ACCURACY_DIR / "promotions"
SALES_ORDERS_DIR = FORECAST_ACCURACY_DIR / "sales_orders"
DEFAULT_OUTPUT_DIR = FORECAST_ACCURACY_DIR / "model"

ACTUALS_PATH = HISTORY_PARQUET_DIR / "actual_sku_day_modified.parquet"
FORECAST_DAY_PATH = HISTORY_PARQUET_DIR / "forecast_sku_day.parquet"
FORECAST_SNAPSHOT_PATH = HISTORY_PARQUET_DIR / "forecast_sku_snapshot.parquet"
SALES_ORDER_SKU_DAY_PATH = SALES_ORDERS_DIR / "sales_order_sku_day.parquet"
PROMO_DAILY_PATH = PROMOTIONS_DIR / "combined_daily_promo_features.parquet"
PDL_SKU_FEATURES_PATH = PROMOTIONS_DIR / "pdl_sku_day_features.parquet"
INVENTORY_SKU_DAY_PATH = FORECAST_ACCURACY_DIR / "inventory" / "ax_inventory_history_sku_day.parquet"
INBOUND_SNAPSHOT_PATH = FORECAST_ACCURACY_DIR / "inbound" / "product_info_inbound_snapshots.parquet"
WAREHOUSE_SUPPLY_SKU_DAY_PATH = (
    FORECAST_ACCURACY_DIR / "warehouse_supply" / "warehouse_supply_sku_day.parquet"
)
MAX_INBOUND_SNAPSHOT_AGE_DAYS = 35

NUMERIC_ZERO_COLUMNS = [
    "SoldUnits",
    "PickLines",
    "ActualDistinctOrders",
    "OrderedUnits",
    "AbsOrderedUnits",
    "SalesLineCount",
    "SalesDistinctOrders",
    "GrossMSRPAmount",
    "GrossSalesPriceAmount",
    "LineAmount",
    "DiscountAmountVsMSRP",
    "DiscountAmountVsSalesPrice",
    "DiscountedLineCount",
    "ReturnOrNegativeLineCount",
    "CorporateForecastQty",
    "pdl_active_events",
    "pdl_offer_cc_count",
    "pdl_style_count",
    "pdl_total_avail_inv",
    "coupon_active_rows",
    "coupon_max_discount_percent",
    "pdl_sku_offer_rows",
    "pdl_sku_active_events",
    "pdl_sku_distinct_offer_colors",
    "pdl_sku_max_discount_pct",
    "pdl_sku_avg_discount_pct",
    "pdl_sku_min_promo_price",
    "pdl_sku_total_avail_inv",
    "pdl_sku_total_avail_plus_oo",
    "pdl_sku_lw_unit_sales",
    "InventoryAvailPhysicalLag1",
    "InventoryOrderedInTotalLag1",
    "InventoryPhysicalReservedLag1",
    "InventoryNetAvailablePhysicalLag1",
    "InventoryAvgUnitPriceLag1",
    "InventoryAvgLandedCostLag1",
    "InboundSnapshotAgeDays",
    "InboundPastDueUnits",
    "InboundNext7Units",
    "InboundNext8To14Units",
    "InboundNext15To30Units",
    "InboundNext31To60Units",
    "InboundNext61To90Units",
    "InboundLaterUnits",
    "InboundOpenPOLines",
    "InboundDistinctPOs",
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
]
PROMO_SCOPE_COLUMNS = ["pdl_scopes", "coupon_scopes"]
PDL_SKU_TEXT_COLUMNS = [
    "pdl_sku_primary_sheet_type",
    "pdl_sku_primary_scope",
    "pdl_sku_primary_event_name",
]
INVENTORY_TEXT_COLUMNS = [
    "InventoryCatalogLag1",
    "InventoryDirectCatalogLag1",
    "InventoryOfferLag1",
    "InventoryRetailFloorSetLag1",
    "InventorySeasonCodesLag1",
    "InventorySubSeasonCodeLag1",
]
LAG_SOURCE_COLUMNS = ["SoldUnits", "OrderedUnits", "CorporateForecastQty"]
LAG_FEATURE_COLUMNS = [
    "SoldUnitsLag1",
    "SoldUnitsLag7",
    "SoldUnitsLag14",
    "SoldUnitsRolling7",
    "SoldUnitsRolling28",
    "OrderedUnitsLag1",
    "OrderedUnitsLag7",
    "OrderedUnitsRolling7",
    "OrderedUnitsRolling28",
    "CorporateForecastQtyLag1",
    "CorporateForecastQtyRolling7",
]
ITEM_COLOR_LAG_FEATURE_COLUMNS = [
    "ItemColorSoldUnitsLag1",
    "ItemColorSoldUnitsRolling7",
    "ItemColorSoldUnitsRolling28",
    "ItemColorOrderedUnitsLag1",
    "ItemColorOrderedUnitsRolling7",
    "ItemColorOrderedUnitsRolling28",
]
CATEGORY_SIZE_LAG_FEATURE_COLUMNS = [
    "CategorySizeSoldUnitsLag1",
    "CategorySizeSoldUnitsRolling7",
    "CategorySizeSoldUnitsRolling28",
    "CategorySizeOrderedUnitsLag1",
    "CategorySizeOrderedUnitsRolling7",
    "CategorySizeOrderedUnitsRolling28",
]
PRODUCT_COLUMNS = [
    "SKU",
    "Item",
    "Color",
    "Size",
    "Division",
    "Department",
    "Class",
    "KeyCategoryView",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "PutawayIndicator",
    "ReplenishmentThreshold",
    "LatestProductSnapshotDate",
    "LatestProductForecastStartDate",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a model-ready SKU/day panel and transparent baseline backtest."
    )
    parser.add_argument(
        "--start-date",
        default="2025-01-01",
        help="Inclusive panel start date. Defaults to the current sales-order extract start.",
    )
    parser.add_argument(
        "--end-date",
        help="Inclusive panel end date. Defaults to the latest date with fulfilled actuals.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Local worker processes for lag feature calculation. Use 1 for lowest memory; "
            "try 2-4 on larger refreshes if the machine has headroom."
        ),
    )
    parser.add_argument(
        "--holdout-days",
        type=int,
        default=28,
        help="Trailing days to evaluate with baselines.",
    )
    parser.add_argument(
        "--skip-backtest",
        action="store_true",
        help="Only build the model panel.",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=5000,
        help="Rows to write to model_sku_day_panel_sample.csv.",
    )
    return parser.parse_args()


def parse_date_arg(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    return pd.Timestamp(date.fromisoformat(value))


def normalize_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def read_required_parquet(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required input not found: {path}")
    return pd.read_parquet(path, columns=columns)


def date_range_for(path: Path, column: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    df = read_required_parquet(path, [column])
    values = normalize_date(df[column]).dropna()
    if values.empty:
        raise ValueError(f"{path} has no usable dates in {column}")
    return values.min(), values.max()


def choose_panel_window(start_arg: str, end_arg: str | None) -> tuple[pd.Timestamp, pd.Timestamp, dict[str, Any]]:
    actual_min, actual_max = date_range_for(ACTUALS_PATH, "ActualDate")
    order_min, order_max = date_range_for(SALES_ORDER_SKU_DAY_PATH, "OrderDateUTC")
    forecast_min, forecast_max = date_range_for(FORECAST_DAY_PATH, "ForecastDate")

    start = parse_date_arg(start_arg)
    if start is None:
        start = max(actual_min, order_min, forecast_min)
    end = parse_date_arg(end_arg)
    if end is None:
        # Actuals are the target, so the automatic panel stops where target data exists.
        end = min(actual_max, order_max, forecast_max)

    if start > end:
        raise ValueError(f"Panel start {start.date()} is after end {end.date()}")

    ranges = {
        "actuals_date_range": [str(actual_min.date()), str(actual_max.date())],
        "sales_order_date_range": [str(order_min.date()), str(order_max.date())],
        "corporate_forecast_date_range": [str(forecast_min.date()), str(forecast_max.date())],
        "selected_panel_date_range": [str(start.date()), str(end.date())],
    }
    return start, end, ranges


def filter_date_window(df: pd.DataFrame, date_col: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    dates = normalize_date(df[date_col])
    out = df.loc[dates.between(start, end)].copy()
    out["Date"] = dates.loc[out.index]
    return out.drop(columns=[date_col])


def load_actuals(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = read_required_parquet(
        ACTUALS_PATH,
        ["ActualDate", "SKU", "SoldUnits", "PickLines", "DistinctOrders"],
    )
    df = filter_date_window(df, "ActualDate", start, end)
    df = df.rename(columns={"DistinctOrders": "ActualDistinctOrders"})
    return df.groupby(["SKU", "Date"], as_index=False).sum(numeric_only=True)


def load_sales_orders(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = read_required_parquet(
        SALES_ORDER_SKU_DAY_PATH,
        [
            "OrderDateUTC",
            "SKU",
            "OrderedUnits",
            "AbsOrderedUnits",
            "SalesLineCount",
            "DistinctOrders",
            "GrossMSRPAmount",
            "GrossSalesPriceAmount",
            "LineAmount",
            "DiscountAmountVsMSRP",
            "DiscountAmountVsSalesPrice",
            "MaxObservedDiscountPct",
            "DiscountedLineCount",
            "ReturnOrNegativeLineCount",
            "WeightedDiscountPctVsMSRP",
            "WeightedDiscountPctVsSalesPrice",
            "HasObservedDiscount",
        ],
    )
    df = filter_date_window(df, "OrderDateUTC", start, end)
    df = df.rename(columns={"DistinctOrders": "SalesDistinctOrders"})
    return df.groupby(["SKU", "Date"], as_index=False).agg(
        {
            "OrderedUnits": "sum",
            "AbsOrderedUnits": "sum",
            "SalesLineCount": "sum",
            "SalesDistinctOrders": "sum",
            "GrossMSRPAmount": "sum",
            "GrossSalesPriceAmount": "sum",
            "LineAmount": "sum",
            "DiscountAmountVsMSRP": "sum",
            "DiscountAmountVsSalesPrice": "sum",
            "MaxObservedDiscountPct": "max",
            "DiscountedLineCount": "sum",
            "ReturnOrNegativeLineCount": "sum",
            "WeightedDiscountPctVsMSRP": "max",
            "WeightedDiscountPctVsSalesPrice": "max",
            "HasObservedDiscount": "max",
        }
    )


def load_corporate_forecast(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = read_required_parquet(
        FORECAST_DAY_PATH,
        ["SKU", "InferredFileDate", "ForecastDate", "ForecastQty"],
    )
    df["Date"] = normalize_date(df["ForecastDate"])
    df["InferredFileDate"] = normalize_date(df["InferredFileDate"])
    df = df.loc[
        df["Date"].between(start, end)
        & df["InferredFileDate"].notna()
        & (df["InferredFileDate"] <= df["Date"])
    ].copy()
    if df.empty:
        return pd.DataFrame(columns=["SKU", "Date", "CorporateForecastQty", "CorporateSnapshotDate"])

    df = df.sort_values(["SKU", "Date", "InferredFileDate"])
    df = df.drop_duplicates(["SKU", "Date"], keep="last")
    return df.rename(
        columns={
            "ForecastQty": "CorporateForecastQty",
            "InferredFileDate": "CorporateSnapshotDate",
        }
    )[["SKU", "Date", "CorporateForecastQty", "CorporateSnapshotDate"]]


def load_product_attributes(end: pd.Timestamp) -> pd.DataFrame:
    df = read_required_parquet(
        FORECAST_SNAPSHOT_PATH,
        [
            "SKU",
            "InferredFileDate",
            "ForecastStartDate",
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
            "PutawayIndicator",
            "ReplenishmentThreshold",
        ],
    )
    df["InferredFileDate"] = normalize_date(df["InferredFileDate"])
    df["ForecastStartDate"] = normalize_date(df["ForecastStartDate"])
    df = df.loc[df["InferredFileDate"].notna() & (df["InferredFileDate"] <= end)].copy()
    if df.empty:
        return pd.DataFrame(columns=PRODUCT_COLUMNS)
    df = df.sort_values(["SKU", "InferredFileDate", "ForecastStartDate"])
    df = df.drop_duplicates("SKU", keep="last")
    return df.rename(
        columns={
            "InferredFileDate": "LatestProductSnapshotDate",
            "ForecastStartDate": "LatestProductForecastStartDate",
        }
    )[PRODUCT_COLUMNS]


def load_promotions(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = read_required_parquet(
        PROMO_DAILY_PATH,
        [
            "date",
            "pdl_active_events",
            "pdl_offer_cc_count",
            "pdl_style_count",
            "pdl_total_avail_inv",
            "pdl_scopes",
            "coupon_active_rows",
            "coupon_max_discount_percent",
            "coupon_scopes",
        ],
    )
    df = filter_date_window(df, "date", start, end)
    df["HasPDLPromotion"] = df["pdl_active_events"].fillna(0).gt(0)
    df["HasCouponPromotion"] = df["coupon_active_rows"].fillna(0).gt(0)
    df["HasAnyPromotion"] = df["HasPDLPromotion"] | df["HasCouponPromotion"]
    return df


def load_pdl_sku_features(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Load SKU-specific PDL features when that optional feature table exists."""
    if not PDL_SKU_FEATURES_PATH.exists():
        print("  SKU-specific PDL features: not found; run forecast_promo_sku_features.py")
        return pd.DataFrame(columns=["SKU", "Date"])

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
    df = pd.read_parquet(PDL_SKU_FEATURES_PATH, columns=columns)
    df["Date"] = normalize_date(df["Date"])
    return df.loc[df["Date"].between(start, end)].copy()


def load_inventory_features(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Load one-day-lagged inventory features when the optional table exists."""
    if not INVENTORY_SKU_DAY_PATH.exists():
        print("  inventory history features: not found; run forecast_inventory_history.py")
        return pd.DataFrame(columns=["SKU", "Date"])

    columns = [
        "SnapshotDate",
        "SKU",
        "Catalog",
        "DirectCatalog",
        "Offer",
        "RetailFloorSet",
        "SeasonCodes",
        "SubSeasonCode",
        "SeasonYear",
        "AvailPhysical",
        "OrderedInTotal",
        "PhysicalReserved",
        "AvgUnitPrice",
        "AvgLandedCost",
        "NetAvailablePhysical",
        "HasAvailableInventory",
        "HasNetAvailableInventory",
        "HasOrderedInventory",
    ]
    df = pd.read_parquet(INVENTORY_SKU_DAY_PATH, columns=columns)
    df["SnapshotDate"] = normalize_date(df["SnapshotDate"])
    df["Date"] = df["SnapshotDate"] + pd.Timedelta(days=1)
    df = df.loc[df["Date"].between(start, end)].copy()
    return df.rename(
        columns={
            "SnapshotDate": "InventorySnapshotDateLag1",
            "Catalog": "InventoryCatalogLag1",
            "DirectCatalog": "InventoryDirectCatalogLag1",
            "Offer": "InventoryOfferLag1",
            "RetailFloorSet": "InventoryRetailFloorSetLag1",
            "SeasonCodes": "InventorySeasonCodesLag1",
            "SubSeasonCode": "InventorySubSeasonCodeLag1",
            "SeasonYear": "InventorySeasonYearLag1",
            "AvailPhysical": "InventoryAvailPhysicalLag1",
            "OrderedInTotal": "InventoryOrderedInTotalLag1",
            "PhysicalReserved": "InventoryPhysicalReservedLag1",
            "AvgUnitPrice": "InventoryAvgUnitPriceLag1",
            "AvgLandedCost": "InventoryAvgLandedCostLag1",
            "NetAvailablePhysical": "InventoryNetAvailablePhysicalLag1",
            "HasAvailableInventory": "HasAvailableInventoryLag1",
            "HasNetAvailableInventory": "HasNetAvailableInventoryLag1",
            "HasOrderedInventory": "HasOrderedInventoryLag1",
        }
    )


def _inbound_bucket_frame(
    snapshot: pd.DataFrame,
    panel_date: pd.Timestamp,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    work = snapshot.copy()
    days_until = (work["ExpectedInDCDate"] - panel_date).dt.days
    units = pd.to_numeric(work["InboundRemainderUnits"], errors="coerce").fillna(0.0)
    work["InboundPastDueUnits"] = units.where(days_until < 0, 0.0)
    work["InboundNext7Units"] = units.where(days_until.between(0, 7), 0.0)
    work["InboundNext8To14Units"] = units.where(days_until.between(8, 14), 0.0)
    work["InboundNext15To30Units"] = units.where(days_until.between(15, 30), 0.0)
    work["InboundNext31To60Units"] = units.where(days_until.between(31, 60), 0.0)
    work["InboundNext61To90Units"] = units.where(days_until.between(61, 90), 0.0)
    work["InboundLaterUnits"] = units.where(days_until > 90, 0.0)

    grouped = (
        work.groupby("SKU", as_index=False)
        .agg(
            InboundPastDueUnits=("InboundPastDueUnits", "sum"),
            InboundNext7Units=("InboundNext7Units", "sum"),
            InboundNext8To14Units=("InboundNext8To14Units", "sum"),
            InboundNext15To30Units=("InboundNext15To30Units", "sum"),
            InboundNext31To60Units=("InboundNext31To60Units", "sum"),
            InboundNext61To90Units=("InboundNext61To90Units", "sum"),
            InboundLaterUnits=("InboundLaterUnits", "sum"),
            InboundOpenPOLines=("PurchID", "size"),
            InboundDistinctPOs=("PurchID", "nunique"),
        )
    )
    grouped["Date"] = panel_date
    grouped["InboundSnapshotDate"] = snapshot_date
    grouped["InboundSnapshotAgeDays"] = int((panel_date - snapshot_date).days)
    grouped["HasInboundPastDue"] = grouped["InboundPastDueUnits"].gt(0)
    grouped["HasInboundNext14"] = (
        grouped["InboundNext7Units"] + grouped["InboundNext8To14Units"]
    ).gt(0)
    grouped["HasInboundNext30"] = (
        grouped["InboundNext7Units"]
        + grouped["InboundNext8To14Units"]
        + grouped["InboundNext15To30Units"]
    ).gt(0)
    grouped["HasInboundNext90"] = (
        grouped["InboundNext7Units"]
        + grouped["InboundNext8To14Units"]
        + grouped["InboundNext15To30Units"]
        + grouped["InboundNext31To60Units"]
        + grouped["InboundNext61To90Units"]
    ).gt(0)
    return grouped


def load_inbound_features(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Load Product Info inbound snapshots as daily forecast-safe features."""
    if not INBOUND_SNAPSHOT_PATH.exists():
        print("  inbound Product Info features: not found; run forecast_product_info_inbound.py")
        return pd.DataFrame(columns=["SKU", "Date"])

    df = pd.read_parquet(
        INBOUND_SNAPSHOT_PATH,
        columns=[
            "SnapshotDate",
            "SKU",
            "ExpectedInDCDate",
            "PurchID",
            "InboundRemainderUnits",
        ],
    )
    df["SnapshotDate"] = normalize_date(df["SnapshotDate"])
    df["ExpectedInDCDate"] = normalize_date(df["ExpectedInDCDate"])
    df["SKU"] = df["SKU"].fillna("").astype(str).str.strip()
    df["PurchID"] = df["PurchID"].fillna("").astype(str).str.strip()
    df = df.loc[
        df["SnapshotDate"].notna()
        & df["ExpectedInDCDate"].notna()
        & df["SKU"].ne("")
        & pd.to_numeric(df["InboundRemainderUnits"], errors="coerce").fillna(0).gt(0)
    ].copy()
    if df.empty:
        return pd.DataFrame(columns=["SKU", "Date"])

    snapshots = {
        snapshot_date: group.copy()
        for snapshot_date, group in df.groupby("SnapshotDate", sort=True)
    }
    snapshot_dates = sorted(snapshots)
    frames = []
    for panel_date in pd.date_range(start, end, freq="D"):
        eligible = [snapshot_date for snapshot_date in snapshot_dates if snapshot_date <= panel_date]
        if not eligible:
            continue
        snapshot_date = eligible[-1]
        if (panel_date - snapshot_date).days > MAX_INBOUND_SNAPSHOT_AGE_DAYS:
            continue
        frames.append(_inbound_bucket_frame(snapshots[snapshot_date], panel_date, snapshot_date))
    if not frames:
        return pd.DataFrame(columns=["SKU", "Date"])
    return pd.concat(frames, ignore_index=True)


def load_warehouse_supply_features(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Load one-day-lagged warehouse supply work features when available."""
    if not WAREHOUSE_SUPPLY_SKU_DAY_PATH.exists():
        print("  warehouse supply work features: not found; run forecast_warehouse_supply_history.py")
        return pd.DataFrame(columns=["SKU", "Date"])

    columns = [
        "EventDate",
        "SKU",
        "SupplyWorkUnits",
        "ReplenishmentUnits",
        "ReceivingPutawayUnits",
        "ReturnSellableFloorUnits",
        "ReturnNonSellableUnits",
        "TransferUnits",
        "ReplenishmentToFloorUnits",
        "ReserveOrBulkSupplyUnits",
        "StagingMovementUnits",
        "SellableFloorSupplyUnits",
        "NonSellableSupplyUnits",
    ]
    df = pd.read_parquet(WAREHOUSE_SUPPLY_SKU_DAY_PATH, columns=columns)
    df["EventDate"] = normalize_date(df["EventDate"])
    df["Date"] = df["EventDate"] + pd.Timedelta(days=1)
    df = df.loc[df["Date"].between(start, end)].copy()
    return df.rename(
        columns={
            "EventDate": "WarehouseSupplyEventDateLag1",
            "SupplyWorkUnits": "SupplyWorkUnitsLag1",
            "ReplenishmentUnits": "ReplenishmentUnitsLag1",
            "ReceivingPutawayUnits": "ReceivingPutawayUnitsLag1",
            "ReturnSellableFloorUnits": "ReturnSellableFloorUnitsLag1",
            "ReturnNonSellableUnits": "ReturnNonSellableUnitsLag1",
            "TransferUnits": "TransferUnitsLag1",
            "ReplenishmentToFloorUnits": "ReplenishmentToFloorUnitsLag1",
            "ReserveOrBulkSupplyUnits": "ReserveOrBulkSupplyUnitsLag1",
            "StagingMovementUnits": "StagingMovementUnitsLag1",
            "SellableFloorSupplyUnits": "SellableFloorSupplyUnitsLag1",
            "NonSellableSupplyUnits": "NonSellableSupplyUnitsLag1",
        }
    )


def merge_panel(
    actuals: pd.DataFrame,
    orders: pd.DataFrame,
    corporate_forecast: pd.DataFrame,
    product_attributes: pd.DataFrame,
    promotions: pd.DataFrame,
    pdl_sku_features: pd.DataFrame,
    inventory_features: pd.DataFrame,
    inbound_features: pd.DataFrame,
    warehouse_supply_features: pd.DataFrame,
) -> pd.DataFrame:
    panel = actuals.merge(orders, on=["SKU", "Date"], how="outer")
    panel = panel.merge(corporate_forecast, on=["SKU", "Date"], how="outer")
    panel = panel.merge(product_attributes, on="SKU", how="left")
    panel = panel.merge(promotions, on="Date", how="left")
    if not pdl_sku_features.empty:
        panel = panel.merge(pdl_sku_features, on=["SKU", "Date"], how="left")
    if not inventory_features.empty:
        panel = panel.merge(inventory_features, on=["SKU", "Date"], how="left")
    if not inbound_features.empty:
        panel = panel.merge(inbound_features, on=["SKU", "Date"], how="left")
    if not warehouse_supply_features.empty:
        panel = panel.merge(warehouse_supply_features, on=["SKU", "Date"], how="left")

    for column in NUMERIC_ZERO_COLUMNS:
        if column in panel.columns:
            panel[column] = panel[column].fillna(0)
    for column in PROMO_SCOPE_COLUMNS:
        if column in panel.columns:
            panel[column] = panel[column].fillna("")
    for column in PDL_SKU_TEXT_COLUMNS:
        if column in panel.columns:
            panel[column] = panel[column].fillna("")
    for column in INVENTORY_TEXT_COLUMNS:
        if column in panel.columns:
            panel[column] = panel[column].fillna("")
    for column in (
        "HasObservedDiscount",
        "HasPDLPromotion",
        "HasCouponPromotion",
        "HasAnyPromotion",
        "HasSkuPDLPromotion",
        "pdl_sku_has_markdown",
        "pdl_sku_has_final_sale",
        "pdl_sku_has_tier1_recommendation",
        "HasAvailableInventoryLag1",
        "HasNetAvailableInventoryLag1",
        "HasOrderedInventoryLag1",
        "HasInboundPastDue",
        "HasInboundNext14",
        "HasInboundNext30",
        "HasInboundNext90",
    ):
        if column in panel.columns:
            panel[column] = panel[column].fillna(False).astype(bool)

    panel["Date"] = normalize_date(panel["Date"])
    panel["DayOfWeek"] = panel["Date"].dt.dayofweek.astype("int8")
    panel["WeekOfYear"] = panel["Date"].dt.isocalendar().week.astype("int16")
    panel["Month"] = panel["Date"].dt.month.astype("int8")
    panel["IsWeekend"] = panel["DayOfWeek"].isin([5, 6])
    panel["HasActualDemand"] = panel["SoldUnits"].gt(0)
    panel["HasOrderedDemand"] = panel["OrderedUnits"].ne(0)
    panel["HasCorporateForecast"] = panel["CorporateForecastQty"].gt(0)
    panel["ActualMinusCorporateForecast"] = panel["SoldUnits"] - panel["CorporateForecastQty"]
    return panel.sort_values(["SKU", "Date"], kind="mergesort").reset_index(drop=True)


def _add_lags_for_partition(partition: pd.DataFrame) -> pd.DataFrame:
    partition = partition.sort_values(["SKU", "Date"], kind="mergesort").copy()
    grouped = partition.groupby("SKU", sort=False)

    # Rolling features are shifted one day before the window is calculated.
    # That keeps baseline features usable for forecasting and avoids same-day leakage.
    for source in LAG_SOURCE_COLUMNS:
        if source not in partition.columns:
            continue
        shifted = grouped[source].shift(1)
        if source in ("SoldUnits", "OrderedUnits"):
            partition[f"{source}Lag1"] = shifted
            partition[f"{source}Lag7"] = grouped[source].shift(7)
            if source == "SoldUnits":
                partition[f"{source}Lag14"] = grouped[source].shift(14)
            partition[f"{source}Rolling7"] = shifted.groupby(partition["SKU"]).rolling(7, min_periods=1).mean().to_numpy()
            partition[f"{source}Rolling28"] = shifted.groupby(partition["SKU"]).rolling(28, min_periods=1).mean().to_numpy()
        else:
            partition[f"{source}Lag1"] = shifted
            partition[f"{source}Rolling7"] = shifted.groupby(partition["SKU"]).rolling(7, min_periods=1).mean().to_numpy()

    for column in LAG_FEATURE_COLUMNS:
        if column in partition.columns:
            partition[column] = partition[column].fillna(0)
    return partition


def add_lag_features(panel: pd.DataFrame, workers: int) -> pd.DataFrame:
    workers = max(1, int(workers))
    if workers == 1 or panel["SKU"].nunique() < 1000:
        return _add_lags_for_partition(panel)

    worker_count = min(workers, os.cpu_count() or 1)
    buckets = pd.util.hash_pandas_object(panel["SKU"], index=False) % worker_count
    partitions = [panel.loc[buckets == bucket].copy() for bucket in range(worker_count)]
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            completed = list(executor.map(_add_lags_for_partition, partitions))
    except (OSError, PermissionError) as exc:
        print(f"  worker fallback: {exc}; calculating lag features in one process")
        return _add_lags_for_partition(panel)
    return pd.concat(completed, ignore_index=True).sort_values(["SKU", "Date"], kind="mergesort")


def add_item_color_lag_features(panel: pd.DataFrame) -> pd.DataFrame:
    required = {"Item", "Color", "Date", "SoldUnits", "OrderedUnits"}
    if not required.issubset(panel.columns):
        for column in ITEM_COLOR_LAG_FEATURE_COLUMNS:
            panel[column] = 0.0
        return panel

    panel = panel.copy()
    panel["ItemColorKey"] = (
        panel["Item"].fillna("").astype(str).str.strip()
        + "-"
        + panel["Color"].fillna("").astype(str).str.strip()
    )
    family_day = (
        panel.groupby(["ItemColorKey", "Date"], as_index=False)
        .agg(
            ItemColorSoldUnits=("SoldUnits", "sum"),
            ItemColorOrderedUnits=("OrderedUnits", "sum"),
        )
        .sort_values(["ItemColorKey", "Date"], kind="mergesort")
    )
    grouped = family_day.groupby("ItemColorKey", sort=False)
    for source in ["ItemColorSoldUnits", "ItemColorOrderedUnits"]:
        shifted = grouped[source].shift(1)
        family_day[f"{source}Lag1"] = shifted
        family_day[f"{source}Rolling7"] = shifted.groupby(family_day["ItemColorKey"]).rolling(
            7,
            min_periods=1,
        ).mean().to_numpy()
        family_day[f"{source}Rolling28"] = shifted.groupby(family_day["ItemColorKey"]).rolling(
            28,
            min_periods=1,
        ).mean().to_numpy()

    panel = panel.merge(
        family_day[["ItemColorKey", "Date", *ITEM_COLOR_LAG_FEATURE_COLUMNS]],
        on=["ItemColorKey", "Date"],
        how="left",
    )
    for column in ITEM_COLOR_LAG_FEATURE_COLUMNS:
        panel[column] = panel[column].fillna(0)
    return panel.drop(columns=["ItemColorKey"])


def add_category_size_lag_features(panel: pd.DataFrame) -> pd.DataFrame:
    required = {"ProductGroupCode", "SizeGroupCode", "Date", "SoldUnits", "OrderedUnits"}
    if not required.issubset(panel.columns):
        for column in CATEGORY_SIZE_LAG_FEATURE_COLUMNS:
            panel[column] = 0.0
        return panel

    panel = panel.copy()
    panel["CategorySizeKey"] = (
        panel["ProductGroupCode"].fillna("").astype(str).str.strip()
        + "-"
        + panel["SizeGroupCode"].fillna("").astype(str).str.strip()
    )
    category_size_day = (
        panel.groupby(["CategorySizeKey", "Date"], as_index=False)
        .agg(
            CategorySizeSoldUnits=("SoldUnits", "sum"),
            CategorySizeOrderedUnits=("OrderedUnits", "sum"),
        )
        .sort_values(["CategorySizeKey", "Date"], kind="mergesort")
    )
    grouped = category_size_day.groupby("CategorySizeKey", sort=False)
    for source in ["CategorySizeSoldUnits", "CategorySizeOrderedUnits"]:
        shifted = grouped[source].shift(1)
        category_size_day[f"{source}Lag1"] = shifted
        category_size_day[f"{source}Rolling7"] = shifted.groupby(
            category_size_day["CategorySizeKey"]
        ).rolling(7, min_periods=1).mean().to_numpy()
        category_size_day[f"{source}Rolling28"] = shifted.groupby(
            category_size_day["CategorySizeKey"]
        ).rolling(28, min_periods=1).mean().to_numpy()

    panel = panel.merge(
        category_size_day[["CategorySizeKey", "Date", *CATEGORY_SIZE_LAG_FEATURE_COLUMNS]],
        on=["CategorySizeKey", "Date"],
        how="left",
    )
    for column in CATEGORY_SIZE_LAG_FEATURE_COLUMNS:
        panel[column] = panel[column].fillna(0)
    return panel.drop(columns=["CategorySizeKey"])


def add_baseline_forecasts(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["Recent28BaselineQty"] = panel["SoldUnitsRolling28"].fillna(0)
    panel["Recent7BaselineQty"] = panel["SoldUnitsRolling7"].fillna(0)
    panel["CorporateBaselineQty"] = panel["CorporateForecastQty"].fillna(0)

    # A deliberately transparent starter benchmark.  The first holdout checks
    # showed corporate SKU allocation can be badly biased, so this uses the
    # corporate forecast as a modest prior rather than letting it dominate.
    promo_weight = panel["HasAnyPromotion"].map({True: 0.25, False: 0.10}).astype(float)
    recent_weight = 1.0 - promo_weight
    panel["HybridBaselineQty"] = (
        promo_weight * panel["CorporateBaselineQty"]
        + recent_weight * panel[["Recent28BaselineQty", "Recent7BaselineQty"]].max(axis=1)
    ).clip(lower=0)
    return panel


def safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0 or math.isnan(denominator):
        return 0.0
    return numerator / denominator


def evaluate_group(df: pd.DataFrame, group_cols: list[str], forecast_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if group_cols:
        groups = df.groupby(group_cols, dropna=False)
    else:
        groups = [((), df)]

    for key, group in groups:
        key_values = key if isinstance(key, tuple) else (key,)
        base: dict[str, Any] = dict(zip(group_cols, key_values, strict=False))
        actual = float(group["SoldUnits"].sum())
        for forecast_col in forecast_cols:
            forecast = float(group[forecast_col].sum())
            error = forecast - actual
            abs_error = float((group[forecast_col] - group["SoldUnits"]).abs().sum())
            zero_forecast_sold_units = float(
                group.loc[group[forecast_col].le(0) & group["SoldUnits"].gt(0), "SoldUnits"].sum()
            )
            rows.append(
                {
                    **base,
                    "ForecastName": forecast_col,
                    "Rows": int(len(group)),
                    "ActualUnits": actual,
                    "ForecastUnits": forecast,
                    "BiasUnits": error,
                    "BiasPct": safe_divide(error, actual),
                    "WAPE": safe_divide(abs_error, actual),
                    "ZeroForecastSoldUnits": zero_forecast_sold_units,
                    "ZeroForecastSoldUnitPct": safe_divide(zero_forecast_sold_units, actual),
                }
            )
    return pd.DataFrame(rows)


def run_backtest(panel: pd.DataFrame, holdout_days: int, output_dir: Path) -> dict[str, Any]:
    if panel.empty:
        raise ValueError("Cannot backtest an empty panel.")
    end = panel["Date"].max()
    start = end - pd.Timedelta(days=holdout_days - 1)
    holdout = panel.loc[panel["Date"].between(start, end)].copy()

    forecast_cols = [
        "CorporateBaselineQty",
        "Recent28BaselineQty",
        "Recent7BaselineQty",
        "HybridBaselineQty",
    ]
    summary = evaluate_group(holdout, [], forecast_cols)
    by_promo = evaluate_group(holdout, ["HasAnyPromotion"], forecast_cols)
    by_category = evaluate_group(holdout, ["Division", "Department", "Class"], forecast_cols)
    by_category = by_category.sort_values(["ForecastName", "ActualUnits"], ascending=[True, False])

    summary.to_csv(output_dir / "baseline_backtest_summary.csv", index=False)
    by_promo.to_csv(output_dir / "baseline_backtest_by_promo_flag.csv", index=False)
    by_category.head(1000).to_csv(output_dir / "baseline_backtest_by_category_top1000.csv", index=False)

    detail_cols = [
        "Date",
        "SKU",
        "SoldUnits",
        "CorporateBaselineQty",
        "Recent28BaselineQty",
        "Recent7BaselineQty",
        "HybridBaselineQty",
        "HasAnyPromotion",
        "Division",
        "Department",
        "Class",
        "KeyCategoryView",
    ]
    detail = holdout.loc[holdout["SoldUnits"].gt(0), detail_cols].copy()
    detail["HybridAbsError"] = (detail["HybridBaselineQty"] - detail["SoldUnits"]).abs()
    detail.sort_values("HybridAbsError", ascending=False).head(5000).to_csv(
        output_dir / "baseline_backtest_largest_hybrid_errors.csv",
        index=False,
    )

    return {
        "holdout_date_range": [str(start.date()), str(end.date())],
        "holdout_rows": int(len(holdout)),
        "holdout_sold_units": float(holdout["SoldUnits"].sum()),
        "summary_path": str(output_dir / "baseline_backtest_summary.csv"),
        "by_promo_path": str(output_dir / "baseline_backtest_by_promo_flag.csv"),
        "by_category_path": str(output_dir / "baseline_backtest_by_category_top1000.csv"),
    }


def write_outputs(
    panel: pd.DataFrame,
    output_dir: Path,
    sample_rows: int,
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = output_dir / "model_sku_day_panel.parquet"
    panel.to_parquet(panel_path, index=False, compression="zstd")

    if sample_rows > 0:
        panel.head(sample_rows).to_csv(output_dir / "model_sku_day_panel_sample.csv", index=False)

    summary["outputs"] = {
        "model_sku_day_panel": str(panel_path),
        "model_sku_day_panel_sample": str(output_dir / "model_sku_day_panel_sample.csv")
        if sample_rows > 0
        else None,
        "model_panel_summary": str(output_dir / "model_panel_summary.json"),
    }
    with (output_dir / "model_panel_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)


def build_panel(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    start, end, source_ranges = choose_panel_window(args.start_date, args.end_date)
    print(f"Building model panel for {start.date()} through {end.date()}")

    actuals = load_actuals(start, end)
    print(f"  actual SKU/day rows: {len(actuals):,}")
    orders = load_sales_orders(start, end)
    print(f"  sales-order SKU/day rows: {len(orders):,}")
    corporate_forecast = load_corporate_forecast(start, end)
    print(f"  corporate forecast SKU/day rows: {len(corporate_forecast):,}")
    product_attributes = load_product_attributes(end)
    print(f"  product attribute SKUs: {len(product_attributes):,}")
    promotions = load_promotions(start, end)
    print(f"  promotion calendar days: {len(promotions):,}")
    pdl_sku_features = load_pdl_sku_features(start, end)
    print(f"  SKU-specific PDL feature rows: {len(pdl_sku_features):,}")
    inventory_features = load_inventory_features(start, end)
    print(f"  inventory history feature rows: {len(inventory_features):,}")
    inbound_features = load_inbound_features(start, end)
    print(f"  inbound Product Info feature rows: {len(inbound_features):,}")
    warehouse_supply_features = load_warehouse_supply_features(start, end)
    print(f"  warehouse supply work feature rows: {len(warehouse_supply_features):,}")

    panel = merge_panel(
        actuals,
        orders,
        corporate_forecast,
        product_attributes,
        promotions,
        pdl_sku_features,
        inventory_features,
        inbound_features,
        warehouse_supply_features,
    )
    panel = add_lag_features(panel, args.workers)
    panel = add_item_color_lag_features(panel)
    panel = add_category_size_lag_features(panel)
    panel = add_baseline_forecasts(panel)

    summary: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "actuals": str(ACTUALS_PATH),
            "sales_orders": str(SALES_ORDER_SKU_DAY_PATH),
            "corporate_forecast_day": str(FORECAST_DAY_PATH),
            "corporate_forecast_snapshot": str(FORECAST_SNAPSHOT_PATH),
            "promotions": str(PROMO_DAILY_PATH),
            "pdl_sku_features": str(PDL_SKU_FEATURES_PATH),
            "inventory_features": str(INVENTORY_SKU_DAY_PATH),
            "inbound_features": str(INBOUND_SNAPSHOT_PATH),
            "warehouse_supply_features": str(WAREHOUSE_SUPPLY_SKU_DAY_PATH),
        },
        "source_date_ranges": source_ranges,
        "assumptions": [
            "Target SoldUnits is fulfilled DirectPick demand by modified actual date.",
            "Sales-order OrderDateUTC features are same-day diagnostics in the panel; backtest baselines use lagged demand to avoid leakage.",
            "Daily promotion features are calendar-level; SKU-specific PDL features use item-color offer codes joined to all known SKU sizes.",
            "Unparseable or correction-only PDL workbook rows stay out of SKU-specific promotion features.",
            "Product hierarchy/slotting attributes use the latest corporate forecast snapshot on or before the panel end date.",
            "Panel is sparse: it includes SKU/date rows with actual demand, ordered demand, or corporate forecast demand.",
        ],
        "row_count": int(len(panel)),
        "date_range": [str(panel["Date"].min().date()), str(panel["Date"].max().date())],
        "distinct_skus": int(panel["SKU"].nunique()),
        "sold_units": float(panel["SoldUnits"].sum()),
        "ordered_units": float(panel["OrderedUnits"].sum()),
        "corporate_forecast_units": float(panel["CorporateForecastQty"].sum()),
        "promo_days": int(promotions["HasAnyPromotion"].sum()) if not promotions.empty else 0,
        "pdl_sku_feature_rows_in_window": int(len(pdl_sku_features)),
        "pdl_sku_feature_skus_in_window": int(pdl_sku_features["SKU"].nunique())
        if not pdl_sku_features.empty
        else 0,
        "inventory_feature_rows_in_window": int(len(inventory_features)),
        "inventory_feature_skus_in_window": int(inventory_features["SKU"].nunique())
        if not inventory_features.empty
        else 0,
        "inbound_feature_rows_in_window": int(len(inbound_features)),
        "inbound_feature_skus_in_window": int(inbound_features["SKU"].nunique())
        if not inbound_features.empty
        else 0,
        "warehouse_supply_feature_rows_in_window": int(len(warehouse_supply_features)),
        "warehouse_supply_feature_skus_in_window": int(warehouse_supply_features["SKU"].nunique())
        if not warehouse_supply_features.empty
        else 0,
        "panel_rows_with_sku_pdl": int(panel["HasSkuPDLPromotion"].sum())
        if "HasSkuPDLPromotion" in panel.columns
        else 0,
        "panel_rows_with_inventory_lag1": int(panel["HasAvailableInventoryLag1"].sum())
        if "HasAvailableInventoryLag1" in panel.columns
        else 0,
        "panel_rows_with_inbound_next90": int(panel["HasInboundNext90"].sum())
        if "HasInboundNext90" in panel.columns
        else 0,
        "panel_rows_with_sellable_floor_supply_lag1": int(
            panel["SellableFloorSupplyUnitsLag1"].gt(0).sum()
        )
        if "SellableFloorSupplyUnitsLag1" in panel.columns
        else 0,
        "workers": int(args.workers),
    }
    return panel, summary


def main() -> None:
    args = parse_args()
    panel, summary = build_panel(args)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_backtest:
        backtest = run_backtest(panel, args.holdout_days, output_dir)
        summary["backtest"] = backtest

    write_outputs(panel, output_dir, args.sample_rows, summary)
    print(f"Wrote model panel: {output_dir / 'model_sku_day_panel.parquet'}")
    print(f"Rows: {len(panel):,}; SKUs: {panel['SKU'].nunique():,}; sold units: {panel['SoldUnits'].sum():,.0f}")


if __name__ == "__main__":
    main()
