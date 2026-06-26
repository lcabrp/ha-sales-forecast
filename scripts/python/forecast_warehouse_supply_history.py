"""Extract warehouse supply work history for forecast-model diagnostics.

This captures what physically arrived or moved into inventory positions by
SKU/day from WHS work history. It is intentionally separate from sales-order
history: the goal is to describe supply events that can affect forward
replenishment pressure, not demand itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402
from sql_utils import get_ax_engine  # noqa: E402


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "warehouse_supply"
SELLABLE_FLOOR_PROFILES = {"Picking", "Picking A", "PalletPicking"}
NON_SELLABLE_LOCATIONS = {"Washed", "Rags", "Quality"}


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for warehouse supply extractor.

    Returns:
        argparse.Namespace: Checked command line arguments.
    """
    parser = argparse.ArgumentParser(description="Extract SKU/day warehouse supply work history.")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-06-12")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--server", default="prodaxsql2")
    parser.add_argument("--database", default="DAX_PROD")
    parser.add_argument("--warehouse", default="4010")
    parser.add_argument("--data-area", default="ha")
    parser.add_argument("--partition-id", type=int, default=5637144576)
    parser.add_argument("--chunk-days", type=int, default=31)
    parser.add_argument("--keep-detail", action="store_true")
    return parser.parse_args()


def date_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    """Split date interval into chunk windows for batch queries.

    Args:
        start: Inclusive start date.
        end: Inclusive end date.
        chunk_days: Maximum size of chunks in days.

    Returns:
        list[tuple[date, date]]: List of split date ranges.
    """
    chunks = []
    current = start
    while current <= end:
        chunk_end = min(end, current + timedelta(days=chunk_days - 1))
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


# Query to pull completed supply movement details (WORKSTATUS = 4, WORKTYPE = 2 PUT)
# Filters out dummy items/color keys. Joins WHSWORKTABLE, WHSWORKLINE, INVENTDIM, and WMSLOCATION.
SUPPLY_WORK_QUERY = sa.text(
    """
    SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

    SELECT
        CAST(wl.MODIFIEDDATETIME AS date) AS EventDate,
        wt.WORKTRANSTYPE AS WorkTransType,
        wt.WORKTEMPLATECODE AS WorkTemplateCode,
        wt.WORKPOOLID AS WorkPoolID,
        wl.WORKCLASSID AS WorkClassID,
        wl.WMSLOCATIONID AS TargetLocation,
        loc.LOCPROFILEID AS TargetLocProfile,
        loc.ZONEID AS TargetZone,
        wl.ITEMID AS Item,
        idim.INVENTCOLORID AS Color,
        idim.INVENTSIZEID AS Size_,
        CASE
            WHEN ISNULL(idim.INVENTCOLORID, '') = '' THEN wl.ITEMID
            WHEN ISNULL(idim.INVENTSIZEID, '') = '' THEN wl.ITEMID + '-' + idim.INVENTCOLORID
            ELSE wl.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID
        END AS SKU,
        COUNT_BIG(*) AS WorkLineRows,
        COUNT(DISTINCT wl.WORKID) AS DistinctWorks,
        SUM(CONVERT(float, wl.QTYWORK)) AS Units
    FROM WHSWORKTABLE wt WITH (NOLOCK)
    INNER JOIN WHSWORKLINE wl WITH (NOLOCK)
        ON wl.WORKID = wt.WORKID
       AND wl.DATAAREAID = wt.DATAAREAID
       AND wl.[PARTITION] = wt.[PARTITION]
    INNER JOIN INVENTDIM idim WITH (NOLOCK)
        ON idim.INVENTDIMID = wl.INVENTDIMID
       AND idim.DATAAREAID = wl.DATAAREAID
       AND idim.[PARTITION] = wl.[PARTITION]
    LEFT JOIN WMSLOCATION loc WITH (NOLOCK)
        ON loc.WMSLOCATIONID = wl.WMSLOCATIONID
       AND loc.INVENTLOCATIONID = wt.INVENTLOCATIONID
       AND loc.DATAAREAID = wl.DATAAREAID
       AND loc.[PARTITION] = wl.[PARTITION]
    WHERE wt.DATAAREAID = :data_area
      AND wt.[PARTITION] = :partition_id
      AND wt.INVENTLOCATIONID = :warehouse
      AND wl.WORKTYPE = 2
      AND wl.WORKSTATUS = 4
      AND wl.MODIFIEDDATETIME >= :start_dt
      AND wl.MODIFIEDDATETIME < :end_dt
      AND wt.WORKTRANSTYPE IN (1, 7, 11, 12)
      AND wl.ITEMID NOT IN ('9999', '30991', '3333')
    GROUP BY
        CAST(wl.MODIFIEDDATETIME AS date),
        wt.WORKTRANSTYPE,
        wt.WORKTEMPLATECODE,
        wt.WORKPOOLID,
        wl.WORKCLASSID,
        wl.WMSLOCATIONID,
        loc.LOCPROFILEID,
        loc.ZONEID,
        wl.ITEMID,
        idim.INVENTCOLORID,
        idim.INVENTSIZEID
    ORDER BY EventDate, SKU, WorkTransType;
    """
)


def classify_supply(row: pd.Series) -> str:
    """Classify the type of supply activity from transaction metadata.

    Args:
        row: Pandas Series representing columns of a work record row.

    Returns:
        str: Activity classification (e.g. 'Replenishment', 'ReceivingPutaway').
    """
    trans_type = int(row["WorkTransType"]) if pd.notna(row["WorkTransType"]) else -1
    template = str(row.get("WorkTemplateCode", "") or "")
    location = str(row.get("TargetLocation", "") or "").strip()
    profile = str(row.get("TargetLocProfile", "") or "").strip()
    zone = str(row.get("TargetZone", "") or "").strip()

    if trans_type == 12:
        if location in NON_SELLABLE_LOCATIONS or zone == "Quarantine":
            return "ReturnNonSellable"
        if profile in SELLABLE_FLOOR_PROFILES:
            return "ReturnToSellableFloor"
        return "ReturnOther"
    if trans_type == 11:
        return "Replenishment"
    if trans_type == 7:
        return "Transfer"
    if trans_type == 1:
        if template == "Active Putaway":
            return "ActivePutaway"
        if "Cubiscan" in template:
            return "CubiscanPutaway"
        return "ReceivingPutaway"
    return "OtherSupplyWork"


def classify_destination(row: pd.Series) -> str:
    """Classify destination area of warehouse supply movement.

    Args:
        row: Record row data.

    Returns:
        str: Destination category (e.g. 'SellableFloor', 'ReserveOrBulk').
    """
    location = str(row.get("TargetLocation", "") or "").strip()
    profile = str(row.get("TargetLocProfile", "") or "").strip()
    zone = str(row.get("TargetZone", "") or "").strip()
    if location in NON_SELLABLE_LOCATIONS or zone == "Quarantine":
        return "NonSellable"
    if profile in SELLABLE_FLOOR_PROFILES:
        return "SellableFloor"
    if profile in {"Bulk", "Overflow"} or zone in {"Bulk", "OVFLO"}:
        return "ReserveOrBulk"
    if "STAGE" in profile.upper() or "STAGE" in zone.upper() or location.startswith("RSTAGE"):
        return "Staging"
    return "Other"


def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and normalize columns, and calculate classifications.

    Args:
        df: Raw query results DataFrame.

    Returns:
        pd.DataFrame: Cleaned and labeled DataFrame.
    """
    output = df.copy()
    output["EventDate"] = pd.to_datetime(output["EventDate"], errors="coerce").dt.normalize()
    for col in (
        "WorkTemplateCode",
        "WorkPoolID",
        "WorkClassID",
        "TargetLocation",
        "TargetLocProfile",
        "TargetZone",
        "Item",
        "Color",
        "Size_",
        "SKU",
    ):
        output[col] = output[col].fillna("").astype(str).str.strip()
    output["WorkTransType"] = pd.to_numeric(output["WorkTransType"], errors="coerce").fillna(-1).astype("int16")
    output["Units"] = pd.to_numeric(output["Units"], errors="coerce").fillna(0.0)
    output["WorkLineRows"] = pd.to_numeric(output["WorkLineRows"], errors="coerce").fillna(0).astype("int64")
    output["DistinctWorks"] = pd.to_numeric(output["DistinctWorks"], errors="coerce").fillna(0).astype("int64")
    output = output.loc[output["SKU"].ne("") & output["Units"].gt(0)].copy()
    output["SupplyCategory"] = output.apply(classify_supply, axis=1)
    output["DestinationGroup"] = output.apply(classify_destination, axis=1)
    return output


def aggregate_sku_day(detail: pd.DataFrame) -> pd.DataFrame:
    """Pivot detailed supply actions into daily SKU summary counts.

    Args:
        detail: Labeled detail DataFrame.

    Returns:
        pd.DataFrame: Aggregated daily SKU table.
    """
    sku_day = (
        detail.groupby(["EventDate", "SKU"], as_index=False)
        .agg(
            SupplyWorkUnits=("Units", "sum"),
            SupplyWorkLines=("WorkLineRows", "sum"),
            SupplyDistinctWorks=("DistinctWorks", "sum"),
            ReplenishmentUnits=(
                "Units",
                lambda values: values[detail.loc[values.index, "SupplyCategory"].eq("Replenishment")].sum(),
            ),
            ReceivingPutawayUnits=(
                "Units",
                lambda values: values[
                    detail.loc[values.index, "SupplyCategory"].isin(
                        ["ReceivingPutaway", "ActivePutaway", "CubiscanPutaway"]
                    )
                ].sum(),
            ),
            ReturnSellableFloorUnits=(
                "Units",
                lambda values: values[
                    detail.loc[values.index, "SupplyCategory"].eq("ReturnToSellableFloor")
                ].sum(),
            ),
            ReturnNonSellableUnits=(
                "Units",
                lambda values: values[detail.loc[values.index, "SupplyCategory"].eq("ReturnNonSellable")].sum(),
            ),
            TransferUnits=(
                "Units",
                lambda values: values[detail.loc[values.index, "SupplyCategory"].eq("Transfer")].sum(),
            ),
            ReplenishmentToFloorUnits=(
                "Units",
                lambda values: values[
                    detail.loc[values.index, "SupplyCategory"].eq("Replenishment")
                    & detail.loc[values.index, "DestinationGroup"].eq("SellableFloor")
                ].sum(),
            ),
            ReserveOrBulkSupplyUnits=(
                "Units",
                lambda values: values[
                    detail.loc[values.index, "DestinationGroup"].eq("ReserveOrBulk")
                ].sum(),
            ),
            StagingMovementUnits=(
                "Units",
                lambda values: values[detail.loc[values.index, "DestinationGroup"].eq("Staging")].sum(),
            ),
            SellableFloorSupplyUnits=(
                "Units",
                lambda values: values[
                    detail.loc[values.index, "DestinationGroup"].eq("SellableFloor")
                ].sum(),
            ),
            NonSellableSupplyUnits=(
                "Units",
                lambda values: values[detail.loc[values.index, "DestinationGroup"].eq("NonSellable")].sum(),
            ),
        )
        .sort_values(["EventDate", "SKU"])
    )
    return sku_day


def write_outputs(detail: pd.DataFrame, args: argparse.Namespace) -> None:
    """Save detail snapshots, historical sku/day datasets, and run manifests.

    Args:
        detail: Labeled DataFrame.
        args: Command parameters.
    """
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sku_day = aggregate_sku_day(detail)
    detail_path = args.output_dir / "warehouse_supply_work_detail.parquet"
    sku_day_path = args.output_dir / "warehouse_supply_sku_day.parquet"
    summary_path = args.output_dir / "warehouse_supply_daily_summary.csv"
    metadata_path = args.output_dir / "warehouse_supply_metadata.json"

    if args.keep_detail:
        detail.to_parquet(detail_path, index=False, compression="zstd")
    sku_day.to_parquet(sku_day_path, index=False, compression="zstd")

    summary = (
        detail.groupby(["EventDate", "SupplyCategory", "DestinationGroup"], as_index=False)
        .agg(
            Rows=("WorkLineRows", "sum"),
            DistinctSKUs=("SKU", "nunique"),
            Units=("Units", "sum"),
        )
        .sort_values(["EventDate", "SupplyCategory", "DestinationGroup"])
    )
    summary.to_csv(summary_path, index=False)
    
    metadata = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "source": "DAX_PROD.dbo.WHSWORKTABLE/WHSWORKLINE",
        "date_range": [
            str(detail["EventDate"].min().date()) if not detail.empty else "",
            str(detail["EventDate"].max().date()) if not detail.empty else "",
        ],
        "detail_rows": int(len(detail)),
        "sku_day_rows": int(len(sku_day)),
        "distinct_skus": int(sku_day["SKU"].nunique()) if not sku_day.empty else 0,
        "outputs": {
            "detail": str(detail_path) if args.keep_detail else "",
            "sku_day": str(sku_day_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Washed, Rags, and Quarantine destinations are classified as non-sellable.",
            "Use sellable-floor supply separately from replenishment and non-sellable returns.",
        ],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


def main() -> None:
    """Main CLI entry point for warehouse supply extractor."""
    args = parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")
    if args.chunk_days < 1:
        raise ValueError("--chunk-days must be positive")

    engine = get_ax_engine(server=args.server, database=args.database, verbose=True)
    frames = []
    with engine.connect() as conn:
        for chunk_start, chunk_end in date_chunks(start, end, args.chunk_days):
            end_exclusive = chunk_end + timedelta(days=1)
            print(f"pull {chunk_start} to {chunk_end}")
            frame = pd.read_sql_query(
                SUPPLY_WORK_QUERY,
                conn,
                params={
                    "data_area": args.data_area,
                    "partition_id": args.partition_id,
                    "warehouse": args.warehouse,
                    "start_dt": chunk_start.isoformat(),
                    "end_dt": end_exclusive.isoformat(),
                },
            )
            frame = clean_frame(frame)
            print(f"  rows {len(frame):,} units {frame['Units'].sum() if not frame.empty else 0:,.0f}")
            frames.append(frame)
    if not frames:
        raise RuntimeError("No warehouse supply rows returned.")
    detail = pd.concat(frames, ignore_index=True)
    write_outputs(detail, args)


if __name__ == "__main__":
    main()
