"""Profile excluded DirectPick rows by location and SKU."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import sqlalchemy as sa


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from sql_utils import get_ax_engine  # noqa: E402


OUTPUT_DIR = PROJECT_ROOT / "scratch" / "direct_pick_scope_audit"
START_DATE = date(2022, 1, 1)
END_EXCLUSIVE = date(2026, 6, 19)
ARCHIVE_BOUNDARY = date(2026, 6, 13)


def source_segments() -> list[tuple[str, date, date]]:
    return [
        ("DAX_Archive.arc", START_DATE, ARCHIVE_BOUNDARY),
        ("DAX_PROD.dbo", ARCHIVE_BOUNDARY, END_EXCLUSIVE),
    ]


def excluded_query(schema: str) -> sa.TextClause:
    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            YEAR(CAST(wl.MODIFIEDDATETIME AS DATE)) AS PickYear,
            COALESCE(loc.LOCPROFILEID, '<missing WMSLOCATION>') AS LocProfileId,
            wl.WMSLOCATIONID AS SourceLocation,
            loc.ZONEID AS SourceZone,
            CASE
                WHEN ISNULL(dim.INVENTCOLORID, '') = '' THEN wl.ITEMID
                WHEN ISNULL(dim.INVENTSIZEID, '') = '' THEN wl.ITEMID + '-' + dim.INVENTCOLORID
                ELSE wl.ITEMID + '-' + dim.INVENTCOLORID + '-' + dim.INVENTSIZEID
            END AS SKU,
            COUNT_BIG(*) AS PickLines,
            COUNT(DISTINCT wt.ORDERNUM) AS DistinctOrders,
            SUM(CAST(wl.QTYWORK AS DECIMAL(18, 4))) AS PickUnits,
            MIN(CAST(wl.MODIFIEDDATETIME AS DATE)) AS FirstPickDate,
            MAX(CAST(wl.MODIFIEDDATETIME AS DATE)) AS LastPickDate
        FROM {schema}.WHSWORKTABLE wt WITH (NOLOCK)
        INNER JOIN {schema}.WHSWORKLINE wl WITH (NOLOCK)
            ON wt.[PARTITION] = wl.[PARTITION]
           AND wt.DATAAREAID = wl.DATAAREAID
           AND wt.WORKID = wl.WORKID
        INNER JOIN {schema}.INVENTDIM dim WITH (NOLOCK)
            ON wl.[PARTITION] = dim.[PARTITION]
           AND wl.DATAAREAID = dim.DATAAREAID
           AND wl.INVENTDIMID = dim.INVENTDIMID
        LEFT JOIN DAX_PROD.dbo.WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = wl.WMSLOCATIONID
           AND loc.INVENTLOCATIONID = wt.INVENTLOCATIONID
           AND loc.DATAAREAID = wl.DATAAREAID
           AND loc.[PARTITION] = wl.[PARTITION]
        WHERE wt.DATAAREAID = 'ha'
          AND wt.[PARTITION] = 5637144576
          AND wt.INVENTLOCATIONID = '4010'
          AND wt.WORKSTATUS = 4
          AND wt.WORKTRANSTYPE = 2
          AND wl.WORKSTATUS = 4
          AND wl.WORKTYPE = 1
          AND wl.WORKCLASSID = 'DirectPick'
          AND wl.MODIFIEDDATETIME >= :start_dt
          AND wl.MODIFIEDDATETIME < :end_dt
          AND (
              COALESCE(loc.LOCPROFILEID, '<missing WMSLOCATION>')
                  NOT IN ('Picking', 'Picking A', 'PalletPicking', 'Picking D')
              OR wl.WMSLOCATIONID IN ('Bander', 'AutoBagger')
          )
        GROUP BY
            YEAR(CAST(wl.MODIFIEDDATETIME AS DATE)),
            COALESCE(loc.LOCPROFILEID, '<missing WMSLOCATION>'),
            wl.WMSLOCATIONID,
            loc.ZONEID,
            CASE
                WHEN ISNULL(dim.INVENTCOLORID, '') = '' THEN wl.ITEMID
                WHEN ISNULL(dim.INVENTSIZEID, '') = '' THEN wl.ITEMID + '-' + dim.INVENTCOLORID
                ELSE wl.ITEMID + '-' + dim.INVENTCOLORID + '-' + dim.INVENTSIZEID
            END
        """
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    engine = get_ax_engine(verbose=True)
    with engine.connect() as conn:
        for schema, start, end in source_segments():
            print(f"Pull excluded {schema} {start} to {end}")
            frame = pd.read_sql_query(
                excluded_query(schema),
                conn,
                params={"start_dt": start.isoformat(), "end_dt": end.isoformat()},
            )
            if not frame.empty:
                frame["SourceSchema"] = schema
                frames.append(frame)
                print(f"  rows {len(frame):,} units {frame['PickUnits'].sum():,.0f}")

    detail = pd.concat(frames, ignore_index=True)
    for column in ("PickLines", "DistinctOrders", "PickUnits"):
        detail[column] = pd.to_numeric(detail[column], errors="coerce").fillna(0)

    location = (
        detail.groupby(["LocProfileId", "SourceLocation", "SourceZone"], dropna=False, as_index=False)
        .agg(
            PickUnits=("PickUnits", "sum"),
            PickLines=("PickLines", "sum"),
            DistinctSKUs=("SKU", "nunique"),
            FirstPickDate=("FirstPickDate", "min"),
            LastPickDate=("LastPickDate", "max"),
        )
        .sort_values("PickUnits", ascending=False)
    )
    sku = (
        detail.groupby(["LocProfileId", "SKU"], as_index=False)
        .agg(
            PickUnits=("PickUnits", "sum"),
            PickLines=("PickLines", "sum"),
            DistinctLocations=("SourceLocation", "nunique"),
            FirstPickDate=("FirstPickDate", "min"),
            LastPickDate=("LastPickDate", "max"),
        )
        .sort_values("PickUnits", ascending=False)
    )
    year_location = (
        detail.groupby(["PickYear", "LocProfileId", "SourceLocation"], as_index=False)
        .agg(PickUnits=("PickUnits", "sum"), PickLines=("PickLines", "sum"), DistinctSKUs=("SKU", "nunique"))
        .sort_values(["PickYear", "PickUnits"], ascending=[True, False])
    )

    detail.to_csv(OUTPUT_DIR / "direct_pick_excluded_sku_location_detail.csv", index=False)
    location.to_csv(OUTPUT_DIR / "direct_pick_excluded_by_location.csv", index=False)
    sku.to_csv(OUTPUT_DIR / "direct_pick_excluded_by_sku.csv", index=False)
    year_location.to_csv(OUTPUT_DIR / "direct_pick_excluded_by_year_location.csv", index=False)
    print(location.head(30).to_string(index=False))
    print(sku.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
