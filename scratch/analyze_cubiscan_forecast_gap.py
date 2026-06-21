from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from sql_utils import get_ax_engine  # noqa: E402


OUT_DIR = ROOT / "scratch" / "forecast_churn_20260611"


CUBE_LOCATIONS_QUERY = sa.text(
    """
    SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

    SELECT
        loc.INVENTLOCATIONID,
        loc.WMSLOCATIONID,
        loc.AISLEID,
        loc.LOCPROFILEID,
        loc.ZONEID
    FROM WMSLOCATION loc WITH (NOLOCK)
    WHERE loc.DATAAREAID = :data_area
      AND loc.[PARTITION] = :partition_id
      AND loc.INVENTLOCATIONID = :warehouse
      AND (
          UPPER(loc.WMSLOCATIONID) LIKE '%CUBE%'
          OR UPPER(loc.ZONEID) LIKE '%CUBE%'
          OR UPPER(loc.LOCPROFILEID) LIKE '%CUBE%'
      )
    ORDER BY loc.WMSLOCATIONID;
    """
)


CUBE_INVENTORY_QUERY = sa.text(
    """
    SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

    WITH CubeInventory AS (
        SELECT
            loc.INVENTLOCATIONID AS Warehouse,
            loc.WMSLOCATIONID AS Location,
            loc.LOCPROFILEID AS LocProfile,
            loc.ZONEID AS ZoneId,
            isum.ITEMID AS Item,
            UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTCOLORID, '')))) AS Color,
            UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTSIZEID, '')))) AS Size_,
            isum.ITEMID + '-' + UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTCOLORID, ''))))
                + '-' + UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTSIZEID, '')))) AS SKU,
            SUM(CONVERT(decimal(18, 4), isum.PHYSICALINVENT)) AS PhysicalQty,
            SUM(CONVERT(decimal(18, 4), isum.RESERVPHYSICAL)) AS ReservedPhysicalQty
        FROM INVENTSUM isum WITH (NOLOCK)
        INNER JOIN INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = isum.INVENTDIMID
           AND idim.DATAAREAID = isum.DATAAREAID
           AND idim.[PARTITION] = isum.[PARTITION]
        INNER JOIN WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = idim.WMSLOCATIONID
           AND loc.DATAAREAID = idim.DATAAREAID
           AND loc.[PARTITION] = idim.[PARTITION]
           AND loc.INVENTLOCATIONID = idim.INVENTLOCATIONID
        WHERE isum.DATAAREAID = :data_area
          AND isum.[PARTITION] = :partition_id
          AND idim.INVENTLOCATIONID = :warehouse
          AND isum.PHYSICALINVENT > 0
          AND (
              UPPER(loc.WMSLOCATIONID) LIKE '%CUBE%'
              OR UPPER(loc.ZONEID) LIKE '%CUBE%'
              OR UPPER(loc.LOCPROFILEID) LIKE '%CUBE%'
          )
        GROUP BY
            loc.INVENTLOCATIONID,
            loc.WMSLOCATIONID,
            loc.LOCPROFILEID,
            loc.ZONEID,
            isum.ITEMID,
            idim.INVENTCOLORID,
            idim.INVENTSIZEID
    ),
    ForecastRows AS (
        SELECT
            fc.ITEM AS Item,
            UPPER(LTRIM(RTRIM(ISNULL(fc.COLOR, '')))) AS Color,
            UPPER(LTRIM(RTRIM(ISNULL(fc.SIZE_, '')))) AS Size_,
            fc.SLOTTIERVALUE AS ForecastSlotTier,
            fc.HAPUTAWAYPICKZONE AS PutawayPickZone,
            fc.HACUBISCANTOACTIVE AS CubiscanToActive,
            fc.MODIFIEDDATETIME AS ForecastModifiedDateTime,
            ROW_NUMBER() OVER (
                PARTITION BY
                    fc.ITEM,
                    UPPER(LTRIM(RTRIM(ISNULL(fc.COLOR, '')))),
                    UPPER(LTRIM(RTRIM(ISNULL(fc.SIZE_, ''))))
                ORDER BY fc.MODIFIEDDATETIME DESC
            ) AS rn
        FROM HAFORECASTREPLENISHMENTTABLE fc WITH (NOLOCK)
        WHERE fc.DATAAREAID = :data_area
          AND fc.[PARTITION] = :partition_id
    )
    SELECT
        cube.*,
        forecast.ForecastSlotTier,
        forecast.PutawayPickZone,
        forecast.CubiscanToActive,
        forecast.ForecastModifiedDateTime
    FROM CubeInventory cube
    LEFT JOIN ForecastRows forecast
        ON forecast.rn = 1
       AND forecast.Item = cube.Item
       AND forecast.Color = cube.Color
       AND forecast.Size_ = cube.Size_
    ORDER BY cube.Location, cube.SKU;
    """
)


CUBE_WORK_QUERY = sa.text(
    """
    SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

    WITH CubeWork AS (
        SELECT
            wt.WORKID AS WorkId,
            wt.WORKTRANSTYPE AS WorkTransType,
            wt.WORKSTATUS AS WorkStatus,
            wt.CREATEDDATETIME AS WorkCreatedDateTime,
            wl.LINENUM AS LineNum,
            wl.WORKTYPE AS WorkType,
            wl.WORKSTATUS AS LineStatus,
            wl.WMSLOCATIONID AS Location,
            wl.ITEMID AS Item,
            UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTCOLORID, '')))) AS Color,
            UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTSIZEID, '')))) AS Size_,
            wl.ITEMID + '-' + UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTCOLORID, ''))))
                + '-' + UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTSIZEID, '')))) AS SKU,
            CONVERT(decimal(18, 4), wl.QTYWORK) AS WorkQty
        FROM WHSWORKLINE wl WITH (NOLOCK)
        INNER JOIN WHSWORKTABLE wt WITH (NOLOCK)
            ON wt.WORKID = wl.WORKID
           AND wt.DATAAREAID = wl.DATAAREAID
           AND wt.[PARTITION] = wl.[PARTITION]
        LEFT JOIN INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = wl.INVENTDIMID
           AND idim.DATAAREAID = wl.DATAAREAID
           AND idim.[PARTITION] = wl.[PARTITION]
        WHERE wl.DATAAREAID = :data_area
          AND wl.[PARTITION] = :partition_id
          AND idim.INVENTLOCATIONID = :warehouse
          AND wt.CREATEDDATETIME >= DATEADD(day, -14, GETUTCDATE())
          AND UPPER(ISNULL(wl.WMSLOCATIONID, '')) LIKE '%CUBE%'
    ),
    ForecastRows AS (
        SELECT
            fc.ITEM AS Item,
            UPPER(LTRIM(RTRIM(ISNULL(fc.COLOR, '')))) AS Color,
            UPPER(LTRIM(RTRIM(ISNULL(fc.SIZE_, '')))) AS Size_,
            fc.SLOTTIERVALUE AS ForecastSlotTier,
            fc.HAPUTAWAYPICKZONE AS PutawayPickZone,
            fc.HACUBISCANTOACTIVE AS CubiscanToActive,
            fc.MODIFIEDDATETIME AS ForecastModifiedDateTime,
            ROW_NUMBER() OVER (
                PARTITION BY
                    fc.ITEM,
                    UPPER(LTRIM(RTRIM(ISNULL(fc.COLOR, '')))),
                    UPPER(LTRIM(RTRIM(ISNULL(fc.SIZE_, ''))))
                ORDER BY fc.MODIFIEDDATETIME DESC
            ) AS rn
        FROM HAFORECASTREPLENISHMENTTABLE fc WITH (NOLOCK)
        WHERE fc.DATAAREAID = :data_area
          AND fc.[PARTITION] = :partition_id
    )
    SELECT
        work.*,
        forecast.ForecastSlotTier,
        forecast.PutawayPickZone,
        forecast.CubiscanToActive,
        forecast.ForecastModifiedDateTime
    FROM CubeWork work
    LEFT JOIN ForecastRows forecast
        ON forecast.rn = 1
       AND forecast.Item = work.Item
       AND forecast.Color = work.Color
       AND forecast.Size_ = work.Size_
    ORDER BY work.WorkCreatedDateTime DESC, work.WorkId, work.LineNum;
    """
)


def summarize(name: str, df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            [{"Scope": name, "Rows": 0, "UniqueSKUs": 0, "NoForecastRows": 0, "NoForecastSKUs": 0}]
        )
    has_forecast = df["ForecastSlotTier"].fillna("").astype(str).str.strip().ne("")
    return pd.DataFrame(
        [
            {
                "Scope": name,
                "Rows": len(df),
                "UniqueSKUs": df["SKU"].nunique() if "SKU" in df.columns else 0,
                "NoForecastRows": int((~has_forecast).sum()),
                "NoForecastSKUs": df.loc[~has_forecast, "SKU"].nunique()
                if "SKU" in df.columns
                else 0,
            }
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    params = {
        "data_area": "ha",
        "partition_id": 5637144576,
        "warehouse": "4010",
    }
    engine = get_ax_engine(server="prodaxsql2", database="DAX_PROD", verbose=True)
    locations = pd.read_sql_query(CUBE_LOCATIONS_QUERY, engine, params=params)
    inventory = pd.read_sql_query(CUBE_INVENTORY_QUERY, engine, params=params)
    work = pd.read_sql_query(CUBE_WORK_QUERY, engine, params=params)

    locations.to_csv(OUT_DIR / "cubiscan_locations.csv", index=False)
    inventory.to_csv(OUT_DIR / "cubiscan_live_inventory_forecast_gap.csv", index=False)
    work.to_csv(OUT_DIR / "cubiscan_recent_work_forecast_gap.csv", index=False)

    summary = pd.concat(
        [
            pd.DataFrame(
                [
                    {
                        "Scope": "cube_locations",
                        "Rows": len(locations),
                        "UniqueSKUs": 0,
                        "NoForecastRows": 0,
                        "NoForecastSKUs": 0,
                    }
                ]
            ),
            summarize("cube_live_inventory", inventory),
            summarize("cube_recent_work_14d", work),
        ],
        ignore_index=True,
    )
    summary.to_csv(OUT_DIR / "cubiscan_forecast_gap_summary.csv", index=False)

    print("\nCUBISCAN FORECAST GAP SUMMARY")
    print(summary.to_string(index=False))
    print("\nCUBE LOCATIONS")
    print(locations.to_string(index=False))
    for name, df in [("LIVE INVENTORY WITHOUT FORECAST", inventory), ("RECENT WORK WITHOUT FORECAST", work)]:
        if df.empty or "ForecastSlotTier" not in df.columns:
            continue
        missing = df[df["ForecastSlotTier"].fillna("").astype(str).str.strip().eq("")]
        print(f"\n{name}")
        print(missing.head(30).to_string(index=False))
    print(f"\nWrote detail files to {OUT_DIR}")


if __name__ == "__main__":
    main()
