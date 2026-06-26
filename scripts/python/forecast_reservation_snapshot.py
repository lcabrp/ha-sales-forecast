"""Capture live AX WMS reservation snapshots for forecast-model features.

Reservations are a current-state signal, so this script writes dated snapshots
that can be reused offline later. It uses WHSINVENTRESERVE as the primary WMS
reservation source and keeps blank-location reservations separate from
location-assigned reservations to preserve pre-work demand signal.
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


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "reservations"
DEFAULT_EXCLUDED_ITEMS = ("30991", "3333", "9999", "9997")
PICKFACE_PROFILES = {"Picking", "Picking A", "Picking D", "PalletPicking"}


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for reservations snapshot extraction script.

    Returns:
        argparse.Namespace: Checked command line arguments.
    """
    parser = argparse.ArgumentParser(description="Extract live AX reservation snapshots for forecasting.")
    parser.add_argument("--snapshot-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--server", default="prodaxsql2")
    parser.add_argument("--database", default="DAX_PROD")
    parser.add_argument("--warehouse", default="4010")
    parser.add_argument("--site", default="HA USA")
    parser.add_argument("--data-area", default="ha")
    parser.add_argument("--partition-id", type=int, default=5637144576)
    parser.add_argument(
        "--exclude-item",
        action="append",
        dest="excluded_items",
        help="AX ITEMID to exclude. Defaults exclude virtual/gift-card items.",
    )
    return parser.parse_args()


def sql_list(values: tuple[str, ...]) -> str:
    """Safely format string values to a comma-separated single-quoted SQL list fragment.

    Args:
        values: String identifiers to list.

    Returns:
        str: Comma-separated escaped string literal.
    """
    return ", ".join(f"'{value.replace("'", "''")}'" for value in values)


def reservation_snapshot_query(excluded_items: tuple[str, ...]) -> sa.TextClause:
    """Generate SQL query to extract active WMS reservation statistics from Microsoft Dynamics AX tables.

    Specifically joins WHSINVENTRESERVE, INVENTDIM, and WMSLOCATION tables,
    filtering for the default company, warehouse partition, and site.
    Also categorizes reservations into reservation buckets (e.g. BlankLocation, PickFace, bulk).

    Args:
        excluded_items: ItemIDs to exclude (e.g. virtual items, gift cards).

    Returns:
        sa.TextClause: Executable SQL query.
    """
    excluded_filter = ""
    if excluded_items:
        excluded_filter = f"AND wir.ITEMID NOT IN ({sql_list(excluded_items)})"

    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            CAST(:snapshot_date AS date) AS SnapshotDate,
            wir.HIERARCHYLEVEL AS HierarchyLevel,
            wir.ITEMID AS Item,
            UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTCOLORID, '')))) AS Color,
            UPPER(LTRIM(RTRIM(ISNULL(idim.INVENTSIZEID, '')))) AS Size_,
            LTRIM(RTRIM(ISNULL(idim.INVENTSTATUSID, ''))) AS InventStatusId,
            LTRIM(RTRIM(ISNULL(idim.INVENTSITEID, ''))) AS Site,
            LTRIM(RTRIM(ISNULL(idim.INVENTLOCATIONID, ''))) AS Warehouse,
            LTRIM(RTRIM(ISNULL(idim.WMSLOCATIONID, ''))) AS Location,
            LTRIM(RTRIM(ISNULL(loc.LOCPROFILEID, ''))) AS LocProfile,
            LTRIM(RTRIM(ISNULL(loc.ZONEID, ''))) AS ZoneId,
            CASE
                WHEN ISNULL(idim.WMSLOCATIONID, '') = '' THEN 'BlankLocation'
                WHEN loc.LOCPROFILEID IN ('Picking', 'Picking A', 'Picking D', 'PalletPicking') THEN 'PickFace'
                WHEN loc.LOCPROFILEID = 'W001' THEN 'W001'
                WHEN loc.LOCPROFILEID IN ('Bulk', 'Overflow') THEN 'ReserveOrBulk'
                WHEN loc.LOCPROFILEID IN ('RSTAGE', 'Induction', 'Direct Packing') THEN 'StagingOrProcessing'
                ELSE 'OtherLocated'
            END AS ReservationBucket,
            SUM(CONVERT(float, wir.RESERVPHYSICAL)) AS ReservedPhysical,
            SUM(CONVERT(float, wir.RESERVORDERED)) AS ReservedOrdered,
            SUM(CONVERT(float, wir.AVAILPHYSICAL)) AS AvailPhysical,
            SUM(CONVERT(float, wir.AVAILORDERED)) AS AvailOrdered,
            COUNT_BIG(*) AS SourceRows,
            MAX(wir.MODIFIEDDATETIME) AS MaxReservationModifiedDateTimeUTC
        FROM WHSINVENTRESERVE wir WITH (NOLOCK)
        INNER JOIN INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = wir.INVENTDIMID
           AND idim.DATAAREAID = wir.DATAAREAID
           AND idim.[PARTITION] = wir.[PARTITION]
        LEFT JOIN WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = idim.WMSLOCATIONID
           AND loc.INVENTLOCATIONID = idim.INVENTLOCATIONID
           AND loc.DATAAREAID = idim.DATAAREAID
           AND loc.[PARTITION] = idim.[PARTITION]
        WHERE wir.DATAAREAID = :data_area
          AND wir.[PARTITION] = :partition_id
          AND idim.INVENTLOCATIONID = :warehouse
          AND idim.INVENTSITEID = :site
          AND (wir.RESERVPHYSICAL <> 0 OR wir.RESERVORDERED <> 0)
          AND (
                (wir.HIERARCHYLEVEL = 3 AND ISNULL(idim.WMSLOCATIONID, '') = '')
             OR (wir.HIERARCHYLEVEL = 4 AND ISNULL(idim.WMSLOCATIONID, '') <> '')
          )
          {excluded_filter}
        GROUP BY
            wir.HIERARCHYLEVEL,
            wir.ITEMID,
            idim.INVENTCOLORID,
            idim.INVENTSIZEID,
            idim.INVENTSTATUSID,
            idim.INVENTSITEID,
            idim.INVENTLOCATIONID,
            idim.WMSLOCATIONID,
            loc.LOCPROFILEID,
            loc.ZONEID
        ORDER BY Item, Color, Size_, ReservationBucket, Location;
        """
    )


def make_sku(df: pd.DataFrame) -> pd.Series:
    """Concatenate Item, Color, and Size fields to construct standard dash-separated SKUs.

    Args:
        df: Input DataFrame containing Item, Color, and Size_ columns.

    Returns:
        pd.Series: Generated SKU string Series.
    """
    item = df["Item"].fillna("").astype(str).str.strip()
    color = df["Color"].fillna("").astype(str).str.strip()
    size = df["Size_"].fillna("").astype(str).str.strip()
    return item.where(color.eq(""), item + "-" + color).where(size.eq(""), item + "-" + color + "-" + size)


def clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Perform data cleaning on raw reservation snapshot records.

    Args:
        df: Raw database query results DataFrame.

    Returns:
        pd.DataFrame: Normalized and sorted reservations DataFrame.
    """
    output = df.copy()
    output["SnapshotDate"] = pd.to_datetime(output["SnapshotDate"], errors="coerce").dt.normalize()
    output["MaxReservationModifiedDateTimeUTC"] = pd.to_datetime(
        output["MaxReservationModifiedDateTimeUTC"],
        errors="coerce",
    )
    for col in (
        "Item",
        "Color",
        "Size_",
        "InventStatusId",
        "Site",
        "Warehouse",
        "Location",
        "LocProfile",
        "ZoneId",
        "ReservationBucket",
    ):
        output[col] = output[col].fillna("").astype(str).str.strip()
    output["SKU"] = make_sku(output)
    for col in ("ReservedPhysical", "ReservedOrdered", "AvailPhysical", "AvailOrdered"):
        output[col] = pd.to_numeric(output[col], errors="coerce").fillna(0.0)
    output["HierarchyLevel"] = pd.to_numeric(output["HierarchyLevel"], errors="coerce").fillna(0).astype("int16")
    output["SourceRows"] = pd.to_numeric(output["SourceRows"], errors="coerce").fillna(0).astype("int64")
    output = output.loc[
        output["SnapshotDate"].notna()
        & output["SKU"].ne("")
        & (output["ReservedPhysical"].ne(0) | output["ReservedOrdered"].ne(0))
    ].copy()
    return output.sort_values(
        ["SnapshotDate", "SKU", "ReservationBucket", "Location"],
        kind="mergesort",
    ).reset_index(drop=True)


def aggregate_sku_day(detail: pd.DataFrame) -> pd.DataFrame:
    """Pivots detailed location records into daily SKU summary metrics.

    WHS reservation hierarchy levels are not additive. Level 3 blank-location
    rows are the best current open-order demand proxy; level 4 located rows
    describe allocation/work progress and must stay separate. Only pickface
    located reservations are treated as replenishment-relevant sales allocation;
    W001, bulk, and process-area reservations are diagnostics/exclusions.

    Args:
        detail: Cleaned detailed snapshot DataFrame.

    Returns:
        pd.DataFrame: Aggregated daily SKU summary.
    """
    if detail.empty:
        return pd.DataFrame(columns=["SnapshotDate", "SKU"])

    bucket = (
        detail.pivot_table(
            index=["SnapshotDate", "SKU"],
            columns="ReservationBucket",
            values="ReservedPhysical",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )
    bucket_renames = {
        "BlankLocation": "ReservationBlankLocation",
        "PickFace": "ReservationPickFace",
        "W001": "ReservationW001",
        "ReserveOrBulk": "ReservationReserveOrBulk",
        "StagingOrProcessing": "ReservationStagingOrProcessing",
        "OtherLocated": "ReservationOtherLocated",
    }
    bucket = bucket.rename(columns=bucket_renames)
    output = bucket.copy()
    for col in bucket_renames.values():
        if col not in output.columns:
            output[col] = 0.0
        output[col] = pd.to_numeric(output[col], errors="coerce").fillna(0.0)

    ordered = (
        detail.groupby(["SnapshotDate", "SKU"], as_index=False)
        .agg(
            ReservationOrderedTotal=("ReservedOrdered", "sum"),
            ReservationAvailPhysicalAtDims=("AvailPhysical", "sum"),
            ReservationAvailOrderedAtDims=("AvailOrdered", "sum"),
            ReservationRows=("SourceRows", "sum"),
            ReservationDistinctLocations=(
                "Location",
                lambda values: int(values[values.astype(str).str.len() > 0].nunique()),
            ),
        )
        .sort_values(["SnapshotDate", "SKU"])
    )
    output = output.merge(ordered, on=["SnapshotDate", "SKU"], how="left")
    located_cols = [
        "ReservationPickFace",
        "ReservationW001",
        "ReservationReserveOrBulk",
        "ReservationStagingOrProcessing",
        "ReservationOtherLocated",
    ]
    output["ReservationLocatedPhysicalTotal"] = output[located_cols].sum(axis=1)
    output["ReservationSalesAllocatedPhysicalTotal"] = output["ReservationPickFace"]
    output["ReservationOperationalLocatedPhysicalTotal"] = output[
        [
            "ReservationReserveOrBulk",
            "ReservationStagingOrProcessing",
            "ReservationOtherLocated",
        ]
    ].sum(axis=1)
    
    # Establish total physical reservation based on hierarchy level logic
    output["ReservationPhysicalTotal"] = output["ReservationBlankLocation"]
    output["HasReservation"] = output["ReservationPhysicalTotal"].ne(0) | output[
        "ReservationSalesAllocatedPhysicalTotal"
    ].ne(0)
    return output


def append_or_replace_snapshot(existing_path: Path, snapshot: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """Concatenate fresh snapshot records into the historical file, replacing conflicting dates.

    Args:
        existing_path: Target parquet file holding history.
        snapshot: New snapshot to merge.
        date_col: Timestamp column name.

    Returns:
        pd.DataFrame: Combined sorted history.
    """
    if existing_path.exists():
        existing = pd.read_parquet(existing_path)
        existing[date_col] = pd.to_datetime(existing[date_col], errors="coerce").dt.normalize()
        dates = set(snapshot[date_col].dropna().unique())
        # Strip conflicting dates to prevent duplicate rows
        existing = existing.loc[~existing[date_col].isin(dates)].copy()
        snapshot = pd.concat([existing, snapshot], ignore_index=True)
    return snapshot.sort_values([date_col, "SKU"], kind="mergesort").reset_index(drop=True)


def write_outputs(detail: pd.DataFrame, args: argparse.Namespace) -> None:
    """Write snapshots, historical aggregates, and manifest metadata to the output directory.

    Args:
        detail: Cleaned detailed DataFrame.
        args: Command parameters.
    """
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_date = pd.Timestamp(args.snapshot_date).date().isoformat()
    detail_path = args.output_dir / f"ax_reservation_snapshot_detail_{snapshot_date}.parquet"
    latest_detail_path = args.output_dir / "ax_reservation_snapshot_detail.parquet"
    sku_day_path = args.output_dir / "ax_reservation_sku_day.parquet"
    summary_path = args.output_dir / "ax_reservation_snapshot_summary.csv"
    metadata_path = args.output_dir / "ax_reservation_metadata.json"

    sku_day = aggregate_sku_day(detail)
    sku_day_history = append_or_replace_snapshot(sku_day_path, sku_day, "SnapshotDate")

    detail.to_parquet(detail_path, index=False, compression="zstd")
    detail.to_parquet(latest_detail_path, index=False, compression="zstd")
    sku_day_history.to_parquet(sku_day_path, index=False, compression="zstd")

    daily_summary = (
        sku_day.groupby("SnapshotDate", as_index=False)
        .agg(
            Rows=("SKU", "size"),
            DistinctSKUs=("SKU", "nunique"),
            ReservationPhysicalTotal=("ReservationPhysicalTotal", "sum"),
            ReservationLocatedPhysicalTotal=("ReservationLocatedPhysicalTotal", "sum"),
            ReservationSalesAllocatedPhysicalTotal=("ReservationSalesAllocatedPhysicalTotal", "sum"),
            ReservationOperationalLocatedPhysicalTotal=("ReservationOperationalLocatedPhysicalTotal", "sum"),
            ReservationBlankLocation=("ReservationBlankLocation", "sum"),
            ReservationPickFace=("ReservationPickFace", "sum"),
            ReservationW001=("ReservationW001", "sum"),
            ReservationReserveOrBulk=("ReservationReserveOrBulk", "sum"),
            ReservationStagingOrProcessing=("ReservationStagingOrProcessing", "sum"),
            ReservationOtherLocated=("ReservationOtherLocated", "sum"),
        )
        .sort_values("SnapshotDate")
    )
    if summary_path.exists():
        existing_summary = pd.read_csv(summary_path)
        existing_summary["SnapshotDate"] = pd.to_datetime(
            existing_summary["SnapshotDate"],
            errors="coerce",
        ).dt.normalize()
        existing_summary = existing_summary.loc[
            existing_summary["SnapshotDate"].dt.date.astype(str) != snapshot_date
        ]
        daily_summary = pd.concat([existing_summary, daily_summary], ignore_index=True)
    daily_summary = daily_summary.sort_values("SnapshotDate")
    daily_summary.to_csv(summary_path, index=False)

    metadata = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "server": args.server,
        "database": args.database,
        "source": "DAX_PROD.dbo.WHSINVENTRESERVE/INVENTDIM/WMSLOCATION",
        "snapshot_date": snapshot_date,
        "warehouse": args.warehouse,
        "site": args.site,
        "data_area": args.data_area,
        "partition_id": args.partition_id,
        "detail_rows": int(len(detail)),
        "sku_day_rows_for_snapshot": int(len(sku_day)),
        "sku_day_history_rows": int(len(sku_day_history)),
        "distinct_skus_for_snapshot": int(sku_day["SKU"].nunique()) if not sku_day.empty else 0,
        "outputs": {
            "dated_detail": str(detail_path),
            "latest_detail": str(latest_detail_path),
            "sku_day": str(sku_day_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Uses WHSINVENTRESERVE hierarchy level 3 for blank-location reservations.",
            "Uses WHSINVENTRESERVE hierarchy level 4 for located reservations.",
            "Hierarchy levels are not additive; do not sum all WHSINVENTRESERVE levels together.",
            "ReservationPhysicalTotal is the level-3 BlankLocation open-order demand proxy.",
            "ReservationSalesAllocatedPhysicalTotal is pickface located reservations only.",
            "W001 reservations are retained as an excluded diagnostic because W001 is not replenished.",
            "ReservationOperationalLocatedPhysicalTotal includes reserve/bulk and process-area reservations; do not use it as sales demand.",
            "ReservationLocatedPhysicalTotal is retained as a broad diagnostic total, not model demand.",
            "BlankLocation is preserved as its own bucket for pre-work reservation signal.",
        ],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


def main() -> None:
    """Main CLI entry point for reservations extractor."""
    args = parse_args()
    excluded_items = tuple(args.excluded_items or DEFAULT_EXCLUDED_ITEMS)
    engine = get_ax_engine(server=args.server, database=args.database, verbose=True)
    with engine.connect() as conn:
        detail = pd.read_sql_query(
            reservation_snapshot_query(excluded_items),
            conn,
            params={
                "snapshot_date": args.snapshot_date,
                "data_area": args.data_area,
                "partition_id": args.partition_id,
                "warehouse": args.warehouse,
                "site": args.site,
            },
        )
    detail = clean_frame(detail)
    write_outputs(detail, args)


if __name__ == "__main__":
    main()
