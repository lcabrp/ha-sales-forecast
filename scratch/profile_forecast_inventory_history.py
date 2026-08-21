"""Profile the two corporate Forecast DB inventory-history tables read-only."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

PYTHON_DIR = Path(__file__).resolve().parents[1] / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_db_auth import connect_forecast_db  # noqa: E402


OUTPUT_PATH = Path("scratch/forecast_db_inventory_history_profile_20260821.json")


def fetch_one(cursor, sql: str) -> dict[str, Any]:
    cursor.execute(sql)
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, cursor.fetchone()))


def fetch_all(cursor, sql: str) -> list[dict[str, Any]]:
    cursor.execute(sql)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def main() -> None:
    with connect_forecast_db(timeout=60) as connection:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
        identity = fetch_one(
            cursor,
            "SELECT SUSER_SNAME() AS LoginName, DB_NAME() AS DatabaseName, "
            "CONVERT(varchar(33), SYSDATETIMEOFFSET(), 127) AS CapturedAt;",
        )
        sku_summary = fetch_one(
            cursor,
            """
            SELECT
                COUNT_BIG(*) AS Rows,
                COUNT(DISTINCT CalendarDate) AS SnapshotDates,
                COUNT(DISTINCT SKU) AS DistinctSKUs,
                COUNT(DISTINCT CHANNEL) AS DistinctChannels,
                COUNT(DISTINCT OFFERID) AS DistinctOffers,
                MIN(CalendarDate) AS FirstDate,
                MAX(CalendarDate) AS LastDate,
                SUM(CASE WHEN Avail_OH > 0 THEN 1 ELSE 0 END) AS PositiveRows,
                SUM(CASE WHEN Avail_OH = 0 THEN 1 ELSE 0 END) AS ZeroRows,
                SUM(CASE WHEN Avail_OH < 0 THEN 1 ELSE 0 END) AS NegativeRows,
                SUM(CONVERT(float, Avail_OH)) AS TotalAvailOH
            FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK);
            """,
        )
        sku_dates = fetch_all(
            cursor,
            """
            SELECT
                CalendarDate,
                COUNT_BIG(*) AS Rows,
                COUNT(DISTINCT SKU) AS DistinctSKUs,
                SUM(CASE WHEN Avail_OH > 0 THEN 1 ELSE 0 END) AS PositiveRows,
                SUM(CONVERT(float, Avail_OH)) AS TotalAvailOH
            FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
            GROUP BY CalendarDate
            ORDER BY CalendarDate;
            """,
        )
        sku_duplicates = fetch_one(
            cursor,
            """
            SELECT COALESCE(SUM(KeyRows - 1), 0) AS DuplicateNaturalKeyRows
            FROM (
                SELECT COUNT_BIG(*) AS KeyRows
                FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
                GROUP BY CalendarDate, CHANNEL, OFFERID, SKU
                HAVING COUNT_BIG(*) > 1
            ) duplicates;
            """,
        )
        sku_day_multiplicity = fetch_one(
            cursor,
            """
            SELECT
                COUNT_BIG(*) AS SKUDateKeys,
                SUM(CASE WHEN CombinationRows > 1 THEN 1 ELSE 0 END) AS MultiOfferChannelSKUDateKeys,
                MAX(CombinationRows) AS MaxRowsPerSKUDate
            FROM (
                SELECT CalendarDate, SKU, COUNT_BIG(*) AS CombinationRows
                FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
                GROUP BY CalendarDate, SKU
            ) sku_day;
            """,
        )
        sku_value_quality = fetch_one(
            cursor,
            """
            SELECT
                MIN(CONVERT(float, Avail_OH)) AS MinAvailOH,
                MAX(CONVERT(float, Avail_OH)) AS MaxAvailOH,
                SUM(CASE WHEN Avail_OH >= 1000000000 THEN 1 ELSE 0 END) AS BillionPlusRows,
                SUM(CASE WHEN Avail_OH < 1000000000 THEN CONVERT(float, Avail_OH) ELSE 0 END)
                    AS TotalAvailOHBelowBillion
            FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK);
            """,
        )
        sku_top_values = fetch_all(
            cursor,
            """
            SELECT TOP (20) CalendarDate, CHANNEL, OFFERID, SKU, Avail_OH
            FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
            ORDER BY Avail_OH DESC;
            """,
        )
        sku_sentinel_profile = fetch_all(
            cursor,
            """
            SELECT
                CHANNEL,
                OFFERID,
                SKU,
                COUNT_BIG(*) AS Rows,
                MIN(CalendarDate) AS FirstDate,
                MAX(CalendarDate) AS LastDate,
                MIN(Avail_OH) AS MinAvailOH,
                MAX(Avail_OH) AS MaxAvailOH
            FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
            WHERE Avail_OH >= 1000000000
            GROUP BY CHANNEL, OFFERID, SKU
            ORDER BY CHANNEL, OFFERID, SKU;
            """,
        )
        sku_channel_profile = fetch_all(
            cursor,
            """
            SELECT
                CHANNEL,
                COUNT_BIG(*) AS Rows,
                COUNT(DISTINCT SKU) AS DistinctSKUs,
                COUNT(DISTINCT OFFERID) AS DistinctOffers,
                MIN(CalendarDate) AS FirstDate,
                MAX(CalendarDate) AS LastDate
            FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
            GROUP BY CHANNEL
            ORDER BY CHANNEL;
            """,
        )
        sku_multi_value_consistency = fetch_one(
            cursor,
            """
            SELECT
                COUNT_BIG(*) AS MultiRowSKUDateKeys,
                SUM(CASE WHEN MinAvailOH = MaxAvailOH THEN 1 ELSE 0 END) AS SameValueKeys,
                SUM(CASE WHEN MinAvailOH <> MaxAvailOH THEN 1 ELSE 0 END) AS DifferentValueKeys
            FROM (
                SELECT
                    CalendarDate,
                    SKU,
                    COUNT_BIG(*) AS CombinationRows,
                    MIN(Avail_OH) AS MinAvailOH,
                    MAX(Avail_OH) AS MaxAvailOH
                FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
                GROUP BY CalendarDate, SKU
                HAVING COUNT_BIG(*) > 1
            ) sku_day;
            """,
        )
        sku_channel_day_multiplicity = fetch_one(
            cursor,
            """
            SELECT
                COUNT_BIG(*) AS SKUChannelDateKeys,
                SUM(CASE WHEN OfferRows > 1 THEN 1 ELSE 0 END) AS MultiOfferKeys,
                MAX(OfferRows) AS MaxOffersPerSKUChannelDate
            FROM (
                SELECT CalendarDate, CHANNEL, SKU, COUNT_BIG(*) AS OfferRows
                FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
                GROUP BY CalendarDate, CHANNEL, SKU
            ) sku_channel_day;
            """,
        )
        aggregate_summary = fetch_one(
            cursor,
            """
            SELECT
                COUNT_BIG(*) AS Rows,
                COUNT(DISTINCT CAST(AsOfDate AS date)) AS SnapshotDates,
                COUNT(DISTINCT CHANNEL) AS DistinctChannels,
                COUNT(DISTINCT Division) AS DistinctDivisions,
                COUNT(DISTINCT DEPARTMENT) AS DistinctDepartments,
                COUNT(DISTINCT SEASONPARENTCODE) AS DistinctSeasons,
                MIN(AsOfDate) AS FirstDate,
                MAX(AsOfDate) AS LastDate,
                SUM(CONVERT(float, Avail_OH)) AS TotalAvailOH,
                SUM(CONVERT(float, Avail_Cost_OH)) AS TotalAvailCostOH
            FROM dbo.Inventory_History WITH (NOLOCK);
            """,
        )
        aggregate_dates = fetch_all(
            cursor,
            """
            SELECT
                CAST(AsOfDate AS date) AS AsOfDate,
                COUNT_BIG(*) AS Rows,
                SUM(CONVERT(float, Avail_OH)) AS TotalAvailOH,
                SUM(CONVERT(float, Avail_Cost_OH)) AS TotalAvailCostOH
            FROM dbo.Inventory_History WITH (NOLOCK)
            GROUP BY CAST(AsOfDate AS date)
            ORDER BY AsOfDate;
            """,
        )

    output = {
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "identity": identity,
        "channel_offer_sku_inventory_history": {
            "summary": sku_summary,
            "natural_key_duplicates": sku_duplicates,
            "sku_day_multiplicity": sku_day_multiplicity,
            "value_quality": sku_value_quality,
            "top_values": sku_top_values,
            "sentinel_profile": sku_sentinel_profile,
            "channel_profile": sku_channel_profile,
            "multi_value_consistency": sku_multi_value_consistency,
            "sku_channel_day_multiplicity": sku_channel_day_multiplicity,
            "date_profile": sku_dates,
        },
        "inventory_history": {
            "summary": aggregate_summary,
            "date_profile": aggregate_dates,
        },
    }
    OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, default=json_default), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(OUTPUT_PATH),
        "sku_summary": sku_summary,
        "sku_duplicates": sku_duplicates,
        "sku_day_multiplicity": sku_day_multiplicity,
        "sku_value_quality": sku_value_quality,
        "sku_multi_value_consistency": sku_multi_value_consistency,
        "sku_channel_day_multiplicity": sku_channel_day_multiplicity,
        "sku_channel_profile": sku_channel_profile,
        "aggregate_summary": aggregate_summary,
    }, indent=2, default=json_default))


if __name__ == "__main__":
    main()
