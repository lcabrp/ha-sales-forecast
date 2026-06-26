"""Export sharded SKU/day DirectPick history for forecast training.

The persisted facts intentionally stay at SKU/day grain. Work IDs, sales-order
IDs, operator/user fields, and raw work-line details are not saved here; this
dataset is for demand modeling, event lift, and forecast scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

from output_paths import OUTPUT_DIR
from sql_utils import get_ax_engine


DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "ForecastAccuracy" / "direct_pick_history"
DEFAULT_START_DATE = date(2022, 1, 1)
PARTITION_ID = 5637144576
DATA_AREA_ID = "ha"
WAREHOUSE_ID = "4010"
INCLUDED_LOCATION_PROFILES = ("Picking", "Picking A", "PalletPicking", "Picking D", "invalid")
EXCLUDED_LOCATION_PROFILES = ("W001", "No LP Track")
EXCLUDED_PICK_LOCATIONS = ("Bander", "AutoBagger")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for direct pick history exporter.

    Returns:
        argparse.Namespace: Checked command arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="prodaxsql2")
    parser.add_argument("--database", default="DAX_PROD")
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today() + timedelta(days=1))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--date-basis",
        choices=("modified", "created"),
        default="modified",
        help="Date used to bucket picks. modified matches the current model actuals.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    """Compute the SHA-256 hash of a file's content in a chunked, memory-efficient manner.

    Args:
        path: Path to the target file.

    Returns:
        str: Hexadecimal SHA-256 hash.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def detect_archive_boundary(engine: sa.Engine) -> date:
    """Detect boundary date separating active production and historical archive datasets.

    Args:
        engine: Database engine.

    Returns:
        date: Max date in the archive.
    """
    query = sa.text(
        """
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT CAST(MAX(CREATEDDATETIME) AS DATE) AS MaxArchiveDate
        FROM DAX_Archive.arc.WHSWORKTABLE WITH (NOLOCK)
        WHERE DATAAREAID = :data_area_id
          AND [PARTITION] = :partition_id
          AND WORKSTATUS = 4
        """
    )
    with engine.connect() as conn:
        row = conn.execute(query, {"data_area_id": DATA_AREA_ID, "partition_id": PARTITION_ID}).fetchone()
    if not row or row[0] is None:
        raise RuntimeError("Cannot determine DAX_Archive boundary; refusing to risk PROD/archive overlap.")
    return pd.to_datetime(row[0]).date()


def source_segments(start: date, end_exclusive: date, archive_boundary: date) -> list[tuple[str, str, date, date]]:
    """Determine schema mapping routes and split date intervals for extracting pick data.

    Decides when to route queries to DAX_Archive versus DAX_PROD databases.

    Args:
        start: Inclusive start date.
        end_exclusive: Exclusive end date.
        archive_boundary: Date boundary dividing production and archives.

    Returns:
        list[tuple[str, str, date, date]]: List of table schemas, location schemas, and start/end dates.
    """
    segments: list[tuple[str, str, date, date]] = []
    archive_end = min(end_exclusive, archive_boundary)
    prod_start = max(start, archive_boundary)
    if start < archive_end:
        segments.append(("DAX_Archive.arc", "DAX_PROD.dbo", start, archive_end))
    if prod_start < end_exclusive:
        segments.append(("DAX_PROD.dbo", "DAX_PROD.dbo", prod_start, end_exclusive))
    return segments


def year_windows(start: date, end_exclusive: date) -> list[tuple[int, date, date]]:
    """Slice a date range into chronological annual window intervals.

    Used to produce annual sharded Parquet files on disk.

    Args:
        start: Inclusive start date.
        end_exclusive: Exclusive end date.

    Returns:
        list[tuple[int, date, date]]: List of year-stamped start/end intervals.
    """
    windows: list[tuple[int, date, date]] = []
    current = start
    while current < end_exclusive:
        next_year = date(current.year + 1, 1, 1)
        window_end = min(next_year, end_exclusive)
        windows.append((current.year, current, window_end))
        current = window_end
    return windows


def direct_pick_query(schema: str, location_schema: str, date_expr: str) -> sa.TextClause:
    """Build SQL statement to extract direct picking metrics from warehouse records.

    Filters for completed DirectPick actions and active picking zones.

    Args:
        schema: Target schema name.
        location_schema: WMS Location table schema prefix.
        date_expr: Database field representing transaction date (created or modified).

    Returns:
        sa.TextClause: Executable SQL query.
    """
    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            CAST({date_expr} AS DATE) AS PickDate,
            CASE
                WHEN ISNULL(dim.INVENTCOLORID, '') = '' THEN wl.ITEMID
                WHEN ISNULL(dim.INVENTSIZEID, '') = '' THEN wl.ITEMID + '-' + dim.INVENTCOLORID
                ELSE wl.ITEMID + '-' + dim.INVENTCOLORID + '-' + dim.INVENTSIZEID
            END AS SKU,
            COUNT_BIG(*) AS PickLines,
            COUNT(DISTINCT wt.ORDERNUM) AS DistinctOrders,
            SUM(CAST(wl.QTYWORK AS DECIMAL(18, 4))) AS PickUnits
        FROM {schema}.WHSWORKTABLE wt WITH (NOLOCK)
        INNER JOIN {schema}.WHSWORKLINE wl WITH (NOLOCK)
            ON wt.[PARTITION] = wl.[PARTITION]
           AND wt.DATAAREAID = wl.DATAAREAID
           AND wt.WORKID = wl.WORKID
        INNER JOIN {schema}.INVENTDIM dim WITH (NOLOCK)
            ON wl.[PARTITION] = dim.[PARTITION]
           AND wl.DATAAREAID = dim.DATAAREAID
           AND wl.INVENTDIMID = dim.INVENTDIMID
        INNER JOIN {location_schema}.WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = wl.WMSLOCATIONID
           AND loc.INVENTLOCATIONID = wt.INVENTLOCATIONID
           AND loc.DATAAREAID = wl.DATAAREAID
           AND loc.[PARTITION] = wl.[PARTITION]
        WHERE wt.DATAAREAID = :data_area_id
          AND wt.[PARTITION] = :partition_id
          AND wt.INVENTLOCATIONID = :warehouse_id
          AND wt.WORKSTATUS = 4
          AND wt.WORKTRANSTYPE = 2
          AND wl.WORKSTATUS = 4
          AND wl.WORKTYPE = 1
          AND wl.WORKCLASSID = 'DirectPick'
          AND loc.LOCPROFILEID IN ('Picking', 'Picking A', 'PalletPicking', 'Picking D', 'invalid')
          AND loc.LOCPROFILEID NOT IN ('W001', 'No LP Track')
          AND wl.WMSLOCATIONID NOT IN ('Bander', 'AutoBagger')
          AND {date_expr} >= :start_dt
          AND {date_expr} < :end_dt
        GROUP BY
            CAST({date_expr} AS DATE),
            CASE
                WHEN ISNULL(dim.INVENTCOLORID, '') = '' THEN wl.ITEMID
                WHEN ISNULL(dim.INVENTSIZEID, '') = '' THEN wl.ITEMID + '-' + dim.INVENTCOLORID
                ELSE wl.ITEMID + '-' + dim.INVENTCOLORID + '-' + dim.INVENTSIZEID
            END
        """
    )


def read_segment(
    conn: sa.Connection,
    schema: str,
    location_schema: str,
    start: date,
    end_exclusive: date,
    date_basis: str,
) -> pd.DataFrame:
    """Execute SQL query to pull picking records for a specific schema segment.

    Args:
        conn: Open database connection.
        schema: Target WMS schema name.
        location_schema: Location schema prefix.
        start: Segment start date.
        end_exclusive: Segment end date.
        date_basis: Field representing date (created or modified).

    Returns:
        pd.DataFrame: Query results DataFrame.
    """
    date_expr = "wl.MODIFIEDDATETIME" if date_basis == "modified" else "wt.CREATEDDATETIME"
    return pd.read_sql_query(
        direct_pick_query(schema, location_schema, date_expr),
        conn,
        params={
            "data_area_id": DATA_AREA_ID,
            "partition_id": PARTITION_ID,
            "warehouse_id": WAREHOUSE_ID,
            "start_dt": start.isoformat(),
            "end_dt": end_exclusive.isoformat(),
        },
    )


def normalize_frame(frame: pd.DataFrame, date_basis: str) -> pd.DataFrame:
    """Normalize and clean columns of the direct pick DataFrame, aggregate duplicates.

    Args:
        frame: Input raw pick DataFrame.
        date_basis: Extraction date basis.

    Returns:
        pd.DataFrame: Cleaned daily SKU pick statistics.
    """
    if frame.empty:
        return pd.DataFrame(columns=["PickDate", "DateBasis", "SKU", "PickLines", "DistinctOrders", "PickUnits"])

    normalized = frame.copy()
    normalized["PickDate"] = pd.to_datetime(normalized["PickDate"]).dt.date.map(lambda value: value.isoformat())
    normalized["DateBasis"] = date_basis
    normalized["SKU"] = normalized["SKU"].fillna("").astype(str).str.strip().str.upper()
    for column in ("PickLines", "DistinctOrders", "PickUnits"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0)
    grouped = (
        normalized.groupby(["PickDate", "DateBasis", "SKU"], as_index=False)
        .agg(PickLines=("PickLines", "sum"), DistinctOrders=("DistinctOrders", "sum"), PickUnits=("PickUnits", "sum"))
        .sort_values(["PickDate", "SKU"])
    )
    return grouped


def write_shard(frame: pd.DataFrame, path: Path, overwrite: bool) -> dict[str, object]:
    """Write DataFrame to a sharded zstd Parquet file, replacing existing records if allowed.

    Args:
        frame: Shard records DataFrame.
        path: Path to Parquet file.
        overwrite: Overwrite files if present.

    Returns:
        dict[str, object]: Shard stats summary dictionary.
    """
    if path.exists() and not overwrite:
        raise FileExistsError(f"Pass --overwrite to replace {path}")
    tmp_path = path.with_name(f"{path.name}.tmp")
    frame.to_parquet(tmp_path, index=False, compression="zstd")
    tmp_path.replace(path)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "rows": int(len(frame)),
        "distinct_skus": int(frame["SKU"].nunique()) if not frame.empty else 0,
        "pick_units": float(frame["PickUnits"].sum()) if not frame.empty else 0.0,
        "pick_lines": int(frame["PickLines"].sum()) if not frame.empty else 0,
        "start_date": frame["PickDate"].min() if not frame.empty else None,
        "end_date": frame["PickDate"].max() if not frame.empty else None,
    }


def main() -> None:
    """Main CLI entry point for direct pick history exporter."""
    args = parse_args()
    if args.start_date < DEFAULT_START_DATE:
        raise ValueError("Refusing to collect before 2022-01-01 by default.")
    if args.start_date >= args.end_date:
        raise ValueError("--start-date must be before --end-date.")

    parquet_dir = args.output_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "direct_pick_history_manifest.json"
    summary_path = args.output_dir / "direct_pick_history_year_summary.csv"
    if (manifest_path.exists() or summary_path.exists()) and not args.overwrite:
        raise FileExistsError("Pass --overwrite to replace the existing manifest/summary.")

    engine = get_ax_engine(server=args.server, database=args.database, verbose=True)
    archive_boundary = detect_archive_boundary(engine)
    print(f"Archive boundary: {archive_boundary}")

    shard_records: list[dict[str, object]] = []
    with engine.connect() as conn:
        for year, year_start, year_end in year_windows(args.start_date, args.end_date):
            frames: list[pd.DataFrame] = []
            for schema, location_schema, seg_start, seg_end in source_segments(year_start, year_end, archive_boundary):
                print(f"Pull {schema} {seg_start} to {seg_end} ({args.date_basis})")
                segment = read_segment(conn, schema, location_schema, seg_start, seg_end, args.date_basis)
                if not segment.empty:
                    segment["SourceSchema"] = schema
                    frames.append(segment)
                    print(f"  rows {len(segment):,} units {segment['PickUnits'].sum():,.0f}")

            year_frame = normalize_frame(pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), args.date_basis)
            shard_path = parquet_dir / f"direct_pick_sku_day_{args.date_basis}_{year}.parquet"
            record = write_shard(year_frame, shard_path, args.overwrite)
            record["year"] = year
            shard_records.append(record)
            print(
                f"Wrote {shard_path} rows={record['rows']:,} "
                f"units={record['pick_units']:,.0f} skus={record['distinct_skus']:,}"
            )

    summary = pd.DataFrame(shard_records)
    summary.to_csv(summary_path, index=False)
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "forecast_training_direct_pick_history",
        "source": "DAX_Archive.arc/DAX_PROD.dbo WHSWORKTABLE/WHSWORKLINE/INVENTDIM",
        "date_basis": args.date_basis,
        "window": {"start_date": str(args.start_date), "end_date_exclusive": str(args.end_date)},
        "archive_boundary": str(archive_boundary),
        "filters": {
            "DATAAREAID": DATA_AREA_ID,
            "PARTITION": PARTITION_ID,
            "INVENTLOCATIONID": WAREHOUSE_ID,
            "WHSWORKTABLE.WORKTRANSTYPE": 2,
            "WHSWORKTABLE.WORKSTATUS": 4,
            "WHSWORKLINE.WORKSTATUS": 4,
            "WHSWORKLINE.WORKTYPE": 1,
            "WHSWORKLINE.WORKCLASSID": "DirectPick",
            "Included.WMSLOCATION.LOCPROFILEID": list(INCLUDED_LOCATION_PROFILES),
            "Excluded.WMSLOCATION.LOCPROFILEID": list(EXCLUDED_LOCATION_PROFILES),
            "Excluded.WHSWORKLINE.WMSLOCATIONID": list(EXCLUDED_PICK_LOCATIONS),
        },
        "location_profile_source": "DAX_PROD.dbo.WMSLOCATION",
        "location_profile_assumption": "Location profiles are treated as stable enough to classify archived picks.",
        "grain": "SKU/day",
        "privacy_scope": [
            "No work IDs persisted.",
            "No sales-order IDs persisted.",
            "No operator/user fields persisted.",
            "DistinctOrders is distinct within SKU/day and is not additive across SKUs.",
        ],
        "shards": shard_records,
        "totals": {
            "rows": int(summary["rows"].sum()),
            "pick_units": float(summary["pick_units"].sum()),
            "pick_lines": int(summary["pick_lines"].sum()),
        },
        "outputs": {"manifest": str(manifest_path), "summary": str(summary_path), "parquet_dir": str(parquet_dir)},
    }
    tmp_manifest = manifest_path.with_name(f"{manifest_path.name}.tmp")
    tmp_manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_manifest.replace(manifest_path)
    print(summary.to_string(index=False))
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
