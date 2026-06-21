"""Extract AX inventory history snapshots for forecast-model features.

AX keeps a limited SKU-level inventory snapshot history in
``HAINVENTDETAILREPORTBATCHTMPHISTORY``.  This is not a full multi-year
availability source, but it is useful for recent backtests and for testing
whether inventory availability helps distinguish true low demand from
stockout-censored demand.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402
from sql_utils import get_ax_engine  # noqa: E402


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "inventory"
AX_HISTORY_SKU_DAY_FILENAME = "ax_inventory_history_sku_day.parquet"
AX_HISTORY_SUMMARY_FILENAME = "ax_inventory_history_sku_day_summary.csv"
AX_HISTORY_METADATA_FILENAME = "ax_inventory_history_metadata.json"
DEFAULT_EXCLUDED_SKUS = ("9999", "30991", "3333")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract limited AX inventory history for forecast modeling."
    )
    parser.add_argument("--start-date", default="2026-03-30")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--server", default="prodaxsql2")
    parser.add_argument("--database", default="DAX_PROD")
    parser.add_argument("--warehouse", default="4010")
    parser.add_argument("--site", default="HA USA")
    parser.add_argument("--data-area", default="ha")
    parser.add_argument("--partition-id", type=int, default=5637144576)
    parser.add_argument(
        "--exclude-sku",
        action="append",
        dest="excluded_skus",
        help="SKU to exclude. Defaults exclude virtual/gift-card SKUs.",
    )
    return parser.parse_args()


def inventory_history_query(excluded_skus: tuple[str, ...]) -> sa.TextClause:
    excluded_filter = ""
    if excluded_skus:
        escaped = ", ".join(f"'{sku.replace("'", "''")}'" for sku in excluded_skus)
        excluded_filter = f"AND hist.SKU NOT IN ({escaped})"

    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            CAST(hist.ASOFDATE AS date) AS SnapshotDate,
            LTRIM(RTRIM(hist.SKU)) AS SKU,
            MAX(LTRIM(RTRIM(ISNULL(hist.CATALOG, '')))) AS Catalog,
            MAX(LTRIM(RTRIM(ISNULL(hist.DIRECTCATALOG, '')))) AS DirectCatalog,
            MAX(LTRIM(RTRIM(ISNULL(hist.OFFER, '')))) AS Offer,
            MAX(LTRIM(RTRIM(ISNULL(hist.RETAILFLOORSET, '')))) AS RetailFloorSet,
            MAX(LTRIM(RTRIM(ISNULL(hist.SEASONCODES, '')))) AS SeasonCodes,
            MAX(LTRIM(RTRIM(ISNULL(hist.SUBSEASONCODE, '')))) AS SubSeasonCode,
            MAX(CAST(hist.SEASONYEAR AS int)) AS SeasonYear,
            SUM(CONVERT(float, hist.AVAILPHYSICAL)) AS AvailPhysical,
            SUM(CONVERT(float, hist.ORDEREDINTOTAL)) AS OrderedInTotal,
            SUM(CONVERT(float, hist.PHYSICALRESERVED)) AS PhysicalReserved,
            AVG(CONVERT(float, hist.UNITPRICE)) AS AvgUnitPrice,
            AVG(CONVERT(float, hist.LANDEDCOST)) AS AvgLandedCost,
            COUNT_BIG(*) AS SourceRows
        FROM dbo.HAINVENTDETAILREPORTBATCHTMPHISTORY hist WITH (NOLOCK)
        WHERE hist.DATAAREAID = :data_area
          AND hist.[PARTITION] = :partition_id
          AND hist.INVENTSITEID = :site
          AND hist.INVENTLOCATIONID = :warehouse
          AND hist.ASOFDATE >= :start_date
          AND hist.ASOFDATE < DATEADD(day, 1, :end_date)
          AND LTRIM(RTRIM(ISNULL(hist.SKU, ''))) <> ''
          {excluded_filter}
        GROUP BY
            CAST(hist.ASOFDATE AS date),
            LTRIM(RTRIM(hist.SKU))
        ORDER BY SnapshotDate, SKU;
        """
    )


def clean_inventory(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    output.insert(0, "InventorySource", "ax_history")
    output["SnapshotDate"] = pd.to_datetime(output["SnapshotDate"], errors="coerce").dt.normalize()
    for col in (
        "SKU",
        "Catalog",
        "DirectCatalog",
        "Offer",
        "RetailFloorSet",
        "SeasonCodes",
        "SubSeasonCode",
    ):
        output[col] = output[col].fillna("").astype(str).str.strip()
    for col in (
        "AvailPhysical",
        "OrderedInTotal",
        "PhysicalReserved",
        "AvgUnitPrice",
        "AvgLandedCost",
    ):
        output[col] = pd.to_numeric(output[col], errors="coerce").fillna(0.0)
    output["NetAvailablePhysical"] = output["AvailPhysical"] - output["PhysicalReserved"]
    output["HasAvailableInventory"] = output["AvailPhysical"].gt(0)
    output["HasNetAvailableInventory"] = output["NetAvailablePhysical"].gt(0)
    output["HasOrderedInventory"] = output["OrderedInTotal"].gt(0)
    output["SourceRows"] = pd.to_numeric(output["SourceRows"], errors="coerce").fillna(0).astype("int64")
    return output.sort_values(["SnapshotDate", "SKU"], kind="mergesort").reset_index(drop=True)


def write_outputs(df: pd.DataFrame, args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = args.output_dir / AX_HISTORY_SKU_DAY_FILENAME
    summary_path = args.output_dir / AX_HISTORY_SUMMARY_FILENAME
    metadata_path = args.output_dir / AX_HISTORY_METADATA_FILENAME

    df.to_parquet(detail_path, index=False, compression="zstd")
    summary = (
        df.groupby("SnapshotDate", as_index=False)
        .agg(
            Rows=("SKU", "size"),
            DistinctSKUs=("SKU", "nunique"),
            AvailPhysical=("AvailPhysical", "sum"),
            OrderedInTotal=("OrderedInTotal", "sum"),
            PhysicalReserved=("PhysicalReserved", "sum"),
            NetAvailablePhysical=("NetAvailablePhysical", "sum"),
            SKUsWithAvailableInventory=("HasAvailableInventory", "sum"),
            SKUsWithNetAvailableInventory=("HasNetAvailableInventory", "sum"),
            SKUsWithOrderedInventory=("HasOrderedInventory", "sum"),
        )
        .sort_values("SnapshotDate")
    )
    summary.to_csv(summary_path, index=False)

    metadata = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "server": args.server,
        "database": args.database,
        "source_table": "dbo.HAINVENTDETAILREPORTBATCHTMPHISTORY",
        "requested_start_date": args.start_date,
        "requested_end_date": args.end_date,
        "actual_start_date": str(df["SnapshotDate"].min().date()) if not df.empty else "",
        "actual_end_date": str(df["SnapshotDate"].max().date()) if not df.empty else "",
        "rows": int(len(df)),
        "snapshot_days": int(df["SnapshotDate"].nunique()) if not df.empty else 0,
        "distinct_skus": int(df["SKU"].nunique()) if not df.empty else 0,
        "outputs": {
            "ax_inventory_history_sku_day": str(detail_path),
            "ax_inventory_history_sku_day_summary": str(summary_path),
        },
        "notes": [
            "Limited AX SKU-level snapshot history, not full multi-year BigQuery inventory history.",
            "Downstream model panel uses these rows as one-day-lagged features to avoid same-day target leakage.",
        ],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")

    excluded_skus = tuple(args.excluded_skus or DEFAULT_EXCLUDED_SKUS)
    engine = get_ax_engine(server=args.server, database=args.database, verbose=True)
    query = inventory_history_query(excluded_skus)
    df = pd.read_sql_query(
        query,
        engine,
        params={
            "data_area": args.data_area,
            "partition_id": args.partition_id,
            "site": args.site,
            "warehouse": args.warehouse,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    write_outputs(clean_inventory(df), args)


if __name__ == "__main__":
    main()
