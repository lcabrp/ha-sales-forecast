"""Export portable SKU-day direct-pick history for shadow velocity analysis.

The persisted fact excludes work IDs and order IDs. SQL performs the distinct
order aggregation before the data reaches the portable Parquet artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyodbc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
PARQUET_NAME = "direct_pick_sku_day_15mo.parquet"
SUMMARY_NAME = "direct_pick_sku_day_15mo_summary.csv"
METADATA_NAME = "direct_pick_sku_day_15mo_metadata.json"
DEFAULT_SERVER = "prodaxsql2"
DEFAULT_DATABASE = "DAX_PROD"
PARTITION_ID = 5637144576
DATA_AREA = "ha"
DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date.today() - timedelta(days=455))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today() + timedelta(days=1))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def connect(server: str, database: str) -> pyodbc.Connection:
    errors: list[str] = []
    for driver in DRIVERS:
        connection_string = (
            f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            "Trusted_Connection=yes;TrustServerCertificate=yes;"
        )
        try:
            connection = pyodbc.connect(connection_string)
            print(f"Connected with {driver}")
            return connection
        except pyodbc.Error as exc:
            errors.append(f"{driver}: {exc}")
    raise RuntimeError("Could not connect to AX SQL Server:\n" + "\n".join(errors))


def archive_boundary(connection: pyodbc.Connection) -> date:
    query = """
    SELECT CAST(MAX(CREATEDDATETIME) AS DATE)
    FROM DAX_Archive.arc.WHSWORKTABLE
    WHERE DATAAREAID = ? AND PARTITION = ? AND WORKSTATUS = 4
    """
    value = connection.cursor().execute(query, DATA_AREA, PARTITION_ID).fetchone()[0]
    if value is None:
        raise RuntimeError("Cannot determine DAX_Archive boundary date.")
    return value


def aggregate_query(database: str, schema: str) -> str:
    return f"""
    SELECT
        CAST(wt.CREATEDDATETIME AS DATE) AS PickDate,
        CASE
            WHEN ISNULL(dim.INVENTCOLORID, '') = '' THEN wl.ITEMID
            WHEN ISNULL(dim.INVENTSIZEID, '') = '' THEN wl.ITEMID + '-' + dim.INVENTCOLORID
            ELSE wl.ITEMID + '-' + dim.INVENTCOLORID + '-' + dim.INVENTSIZEID
        END AS SKU,
        COUNT_BIG(*) AS PickLines,
        COUNT(DISTINCT wt.ORDERNUM) AS DistinctOrders,
        SUM(CAST(wl.QTYWORK AS DECIMAL(18, 4))) AS PickUnits
    FROM {database}.{schema}.WHSWORKTABLE wt
    INNER JOIN {database}.{schema}.WHSWORKLINE wl
        ON wt.PARTITION = wl.PARTITION
       AND wt.DATAAREAID = wl.DATAAREAID
       AND wt.WORKID = wl.WORKID
    INNER JOIN {database}.{schema}.INVENTDIM dim
        ON wl.PARTITION = dim.PARTITION
       AND wl.DATAAREAID = dim.DATAAREAID
       AND wl.INVENTDIMID = dim.INVENTDIMID
    WHERE wt.DATAAREAID = ?
      AND wt.PARTITION = ?
      AND wt.WORKSTATUS = 4
      AND wl.WORKSTATUS = 4
      AND wl.WORKTYPE = 1
      AND wl.WORKCLASSID = 'DirectPick'
      AND wt.CREATEDDATETIME >= ?
      AND wt.CREATEDDATETIME < ?
    GROUP BY
        CAST(wt.CREATEDDATETIME AS DATE),
        CASE
            WHEN ISNULL(dim.INVENTCOLORID, '') = '' THEN wl.ITEMID
            WHEN ISNULL(dim.INVENTSIZEID, '') = '' THEN wl.ITEMID + '-' + dim.INVENTCOLORID
            ELSE wl.ITEMID + '-' + dim.INVENTCOLORID + '-' + dim.INVENTSIZEID
        END
    """


def read_period(
    connection: pyodbc.Connection,
    database: str,
    schema: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    if start_date >= end_date:
        return pd.DataFrame(columns=["PickDate", "SKU", "PickLines", "DistinctOrders", "PickUnits"])
    return pd.read_sql_query(
        aggregate_query(database, schema),
        connection,
        params=[DATA_AREA, PARTITION_ID, start_date, end_date],
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    parquet = args.output_dir / PARQUET_NAME
    summary_path = args.output_dir / SUMMARY_NAME
    metadata_path = args.output_dir / METADATA_NAME
    outputs = [parquet, summary_path, metadata_path]
    existing = [path for path in outputs if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError("Pass --overwrite to replace: " + ", ".join(str(path) for path in existing))

    with connect(args.server, args.database) as connection:
        boundary = archive_boundary(connection)
        archive_end = min(boundary, args.end_date)
        prod_start = max(boundary, args.start_date)
        print(f"Archive period: {args.start_date} to {archive_end}")
        print(f"PROD period:    {prod_start} to {args.end_date}")
        archive = read_period(connection, "DAX_Archive", "arc", args.start_date, archive_end)
        prod = read_period(connection, "DAX_PROD", "dbo", prod_start, args.end_date)

    frame = pd.concat([archive, prod], ignore_index=True)
    frame["PickDate"] = pd.to_datetime(frame["PickDate"]).dt.date
    frame["SKU"] = frame["SKU"].fillna("").astype(str).str.strip().str.upper()
    for column in ("PickLines", "DistinctOrders", "PickUnits"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame = (
        frame.groupby(["PickDate", "SKU"], as_index=False)
        .agg(PickLines=("PickLines", "sum"), DistinctOrders=("DistinctOrders", "sum"), PickUnits=("PickUnits", "sum"))
        .sort_values(["PickDate", "SKU"])
    )
    summary = pd.DataFrame(
        [
            {
                "StartDate": frame["PickDate"].min(),
                "EndDate": frame["PickDate"].max(),
                "SKUDays": len(frame),
                "DistinctSKUs": frame["SKU"].nunique(),
                "PickLines": frame["PickLines"].sum(),
                "DistinctOrdersSummedBySKUDay": frame["DistinctOrders"].sum(),
                "PickUnits": frame["PickUnits"].sum(),
            }
        ]
    )

    parquet_tmp = parquet.with_name(f"{parquet.name}.tmp")
    summary_tmp = summary_path.with_name(f"{summary_path.name}.tmp")
    metadata_tmp = metadata_path.with_name(f"{metadata_path.name}.tmp")
    frame.to_parquet(parquet_tmp, index=False, compression="zstd")
    summary.to_csv(summary_tmp, index=False)
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "window": {"start_date": str(args.start_date), "end_date_exclusive": str(args.end_date)},
        "archive_boundary": str(boundary),
        "rows": {"sku_days": len(frame), "distinct_skus": int(frame["SKU"].nunique())},
        "outputs": {
            "parquet": {"path": str(parquet.relative_to(PROJECT_ROOT)), "bytes": parquet_tmp.stat().st_size, "sha256": sha256(parquet_tmp)},
            "summary": {"path": str(summary_path.relative_to(PROJECT_ROOT)), "bytes": summary_tmp.stat().st_size, "sha256": sha256(summary_tmp)},
        },
        "notes": [
            "Persisted grain is SKU-day.",
            "No work IDs, sales-order IDs, or demand-work IDs are persisted.",
            "DistinctOrders is distinct within SKU-day and is not additive across SKUs.",
        ],
    }
    metadata_tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    parquet_tmp.replace(parquet)
    summary_tmp.replace(summary_path)
    metadata_tmp.replace(metadata_path)
    print(summary.to_string(index=False))
    print(f"Portable direct-pick fact: {parquet}")


if __name__ == "__main__":
    main()
