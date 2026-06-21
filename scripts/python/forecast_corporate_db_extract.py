"""Snapshot selected Azure SQL Forecast DB tables to local Parquet datasets.

The goal is to pay the Microsoft Entra / Azure SQL connection cost once, then
do exploratory forecast work from local Parquet files.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyodbc

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


PYODBC_PANDAS_WARNING = (
    "pandas only supports SQLAlchemy connectable "
    "(engine/connection) or database string URI or sqlite3 DBAPI2 connection."
)


DEFAULT_SERVER = "azprodfcast01.572f3811ca67.database.windows.net"
DEFAULT_DATABASE = "Forecast"
DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"
DEFAULT_AUTH = "ActiveDirectoryInteractive"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "corporate_forecast"

CORE_TABLES = [
    "dbo.Channel_Offer_SKU_Forecast",
    "dbo.Channel_Offer_Forecast",
    "dbo.Offer_SKU_Inventory_Forecast",
    "dbo.Offer_Inventory_Forecast",
    "dbo.Channel_Offer_Demand_History",
    "dbo.Channel_SKU_SIZE_Weekly_Demand_History",
    "dbo.Product_Dimensions_Hierarchy_Attributes",
    "dbo.Offer_Control_Table",
    "dbo.Offers",
    "dbo.Current_SKU_Available_DC_Inventory",
    "dbo.Current_Offer_Inventory",
    "dbo.On_Order",
    "dbo.Forecast_Job_Log",
]

ARCHIVE_TABLES = [
    "dbo.Channel_Offer_SKU_Forecast_Archive",
    "dbo.Channel_Offer_Forecast_Archive",
    "dbo.Channel_Offer_Forecast_Frozen",
    "dbo.Offer_SKU_Inventory_Forecast_backup",
    "dbo.Offer_Inventory_Forecast_backup_20250324",
    "dbo.Offer_Inventory_Forecast_Frozen",
]

SUPPORT_TABLES = [
    "dbo.Allocation_Minimums",
    "dbo.Allocation_Parameter_Control_Table",
    "dbo.Allocation_Progression_Adjustment_Control_Table",
    "dbo.Allocation_System_Parameters",
    "dbo.AutoLoad_Size_Distribution_Library",
    "dbo.AutoLoad_Store_Groups",
    "dbo.Calendar_Lookup",
    "dbo.Channel_Offer_SKU_Control",
    "dbo.Channel_Offer_SKU_Inventory_History",
    "dbo.Inventory_History",
    "dbo.Kubix_Attributes",
    "dbo.Monthly_On_Order",
    "dbo.OTB_Month_Plans",
    "dbo.OTB_Plan_Parameters",
    "dbo.Promo_Model_Detail",
    "dbo.Promo_Model_Headers",
    "dbo.Promo_Rank_Calculations",
    "dbo.Receipt_History",
    "dbo.SalesDatabyMonth",
    "dbo.Seasonal_Profile_Index_Library",
    "dbo.Seasonal_Profile_Library",
    "dbo.Seasonal_Profile_Sales",
    "dbo.Size_Distribution_Library",
    "dbo.Size_Distribution_Sales",
    "dbo.Size_Range_Groups",
    "dbo.Store_Distribution_LIbrary",
    "dbo.Store_Distribution_Sales",
    "dbo.Store_Master_Control",
    "dbo.Store_Master_Groups_Control",
    "dbo.sku_hist",
]

TABLE_GROUPS = {
    "core": CORE_TABLES,
    "archive": ARCHIVE_TABLES,
    "support": SUPPORT_TABLES,
}

DATE_FILTER_COLUMNS = {
    "Channel_Offer_SKU_Forecast": "CalendarDate",
    "Channel_Offer_SKU_Forecast_Archive": "CalendarDate",
    "Channel_Offer_Forecast": "CalendarDate",
    "Channel_Offer_Forecast_Archive": "CalendarDate",
    "Channel_Offer_Forecast_Frozen": "CalendarDate",
    "Offer_SKU_Inventory_Forecast": "CalendarDate",
    "Offer_SKU_Inventory_Forecast_backup": "CalendarDate",
    "Offer_Inventory_Forecast": "CalendarDate",
    "Offer_Inventory_Forecast_backup_20250324": "CalendarDate",
    "Offer_Inventory_Forecast_Frozen": "CalendarDate",
    "Channel_Offer_Demand_History": "CalendarDate",
    "Channel_SKU_SIZE_Weekly_Demand_History": "FISCALWEEKSTARTDATE",
    "On_Order": "CalendarDate",
    "sku_hist": "calendar_date",
}

ORDER_COLUMNS = {
    "Channel_Offer_SKU_Forecast": ["Channel", "OfferID", "SKU", "CalendarDate"],
    "Channel_Offer_Forecast": ["Channel", "OfferID", "CalendarDate"],
    "Offer_SKU_Inventory_Forecast": ["OfferID", "SKU", "CalendarDate"],
    "Offer_Inventory_Forecast": ["OfferID", "CalendarDate"],
    "Channel_Offer_Demand_History": ["Channel", "sku", "offer", "CalendarDate"],
    "Channel_SKU_SIZE_Weekly_Demand_History": ["CHANNEL", "ITEMID", "SKU", "FISCALWEEKSTARTDATE"],
    "Product_Dimensions_Hierarchy_Attributes": ["sku"],
    "Offer_Control_Table": ["Channel", "OfferID"],
    "Offers": ["OfferID"],
    "Current_SKU_Available_DC_Inventory": ["SKU"],
    "Current_Offer_Inventory": ["OfferID"],
    "On_Order": ["SKU", "Offer", "CalendarDate"],
    "Forecast_Job_Log": ["Process_Date"],
}

CATALOG_SQL = """
WITH row_counts AS (
    SELECT
        object_id,
        SUM(CASE WHEN index_id IN (0, 1) THEN rows ELSE 0 END) AS row_count
    FROM sys.partitions
    GROUP BY object_id
),
column_counts AS (
    SELECT object_id, COUNT(*) AS column_count
    FROM sys.columns
    GROUP BY object_id
)
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    COALESCE(rc.row_count, 0) AS row_count,
    COALESCE(cc.column_count, 0) AS column_count,
    t.create_date,
    t.modify_date
FROM sys.tables AS t
INNER JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
LEFT JOIN row_counts AS rc
    ON rc.object_id = t.object_id
LEFT JOIN column_counts AS cc
    ON cc.object_id = t.object_id
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract selected Azure SQL Forecast DB tables to local Parquet.",
    )
    parser.add_argument("--server", default=os.getenv("ZS_FORECAST_DB_SERVER", DEFAULT_SERVER))
    parser.add_argument("--database", default=os.getenv("ZS_FORECAST_DB_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--driver", default=os.getenv("ZS_FORECAST_DB_DRIVER", DEFAULT_DRIVER))
    parser.add_argument("--auth", default=os.getenv("ZS_FORECAST_DB_AUTH", DEFAULT_AUTH))
    parser.add_argument(
        "--user",
        default=os.getenv("ZS_FORECAST_DB_USER"),
        help="Entra user principal name. Can also be set as ZS_FORECAST_DB_USER.",
    )
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--chunk-rows", type=int, default=200_000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--group",
        action="append",
        choices=sorted(TABLE_GROUPS),
        default=None,
        help="Table group to extract. Repeatable. Defaults to core.",
    )
    parser.add_argument("--table", action="append", default=[], help="Extra schema.table to extract.")
    parser.add_argument("--exclude-table", action="append", default=[])
    parser.add_argument(
        "--calendar-start",
        help="Optional inclusive date filter for tables with a known calendar column.",
    )
    parser.add_argument(
        "--calendar-end",
        help="Optional exclusive date filter for tables with a known calendar column.",
    )
    parser.add_argument(
        "--max-rows-per-table",
        type=int,
        help="Smoke-test limit. Applies TOP N to every table.",
    )
    parser.add_argument(
        "--ordered",
        action="store_true",
        help="Order extracted rows by known business keys. Slower on large tables.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show selected tables without extracting.")
    parser.add_argument("--list-groups", action="store_true", help="Print configured table groups and exit.")
    return parser.parse_args()


def normalized_table_name(name: str) -> str:
    parts = name.split(".")
    if len(parts) == 1:
        return f"dbo.{parts[0]}"
    if len(parts) == 2:
        return f"{parts[0]}.{parts[1]}"
    raise ValueError(f"Expected schema.table or table name, got: {name}")


def table_key(name: str) -> str:
    return normalized_table_name(name).lower()


def table_basename(name: str) -> str:
    return normalized_table_name(name).split(".", 1)[1]


def quote_name(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def quote_table(name: str) -> str:
    schema_name, table_name = normalized_table_name(name).split(".", 1)
    return f"{quote_name(schema_name)}.{quote_name(table_name)}"


def safe_path_name(table: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", normalized_table_name(table).replace(".", "__"))


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connection_string(args: argparse.Namespace) -> str:
    server = args.server
    if not server.lower().startswith("tcp:"):
        server = f"tcp:{server},1433"

    parts = [
        f"DRIVER={{{args.driver}}}",
        f"SERVER={server}",
        f"DATABASE={args.database}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        f"Authentication={args.auth}",
        f"Connection Timeout={args.timeout}",
    ]
    if args.user:
        parts.append(f"UID={args.user}")
    return ";".join(parts)


def fetch_dicts(cursor: pyodbc.Cursor, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cursor.execute(sql, params or [])
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def selected_tables(args: argparse.Namespace) -> list[str]:
    groups = args.group or ["core"]
    selected: list[str] = []
    for group in groups:
        selected.extend(TABLE_GROUPS[group])
    selected.extend(args.table)

    excluded = {table_key(table) for table in args.exclude_table}
    output: list[str] = []
    seen: set[str] = set()
    for table in selected:
        normalized = normalized_table_name(table)
        key = table_key(normalized)
        if key in seen or key in excluded:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def catalog_by_table(cursor: pyodbc.Cursor) -> dict[str, dict[str, Any]]:
    rows = fetch_dicts(cursor, CATALOG_SQL)
    return {table_key(f"{row['schema_name']}.{row['table_name']}"): row for row in rows}


def build_query(table: str, args: argparse.Namespace) -> tuple[str, list[Any], dict[str, Any]]:
    base_table = table_basename(table)
    where_parts: list[str] = []
    params: list[Any] = []
    filters: dict[str, Any] = {}

    date_column = DATE_FILTER_COLUMNS.get(base_table)
    if date_column and args.calendar_start:
        where_parts.append(f"{quote_name(date_column)} >= ?")
        params.append(args.calendar_start)
        filters["calendar_start"] = args.calendar_start
    if date_column and args.calendar_end:
        where_parts.append(f"{quote_name(date_column)} < ?")
        params.append(args.calendar_end)
        filters["calendar_end"] = args.calendar_end

    top_clause = ""
    if args.max_rows_per_table is not None:
        if args.max_rows_per_table <= 0:
            raise ValueError("--max-rows-per-table must be positive.")
        top_clause = f"TOP ({args.max_rows_per_table}) "
        filters["max_rows_per_table"] = args.max_rows_per_table

    where_clause = ""
    if where_parts:
        where_clause = " WHERE " + " AND ".join(where_parts)

    order_clause = ""
    order_columns = ORDER_COLUMNS.get(base_table)
    if args.ordered and order_columns:
        order_clause = " ORDER BY " + ", ".join(quote_name(column) for column in order_columns)

    query = f"SELECT {top_clause}* FROM {quote_table(table)} WITH (NOLOCK){where_clause}{order_clause};"
    return query, params, filters


def remove_existing_parts(table_dir: Path) -> None:
    for part_path in table_dir.glob("part-*.parquet"):
        part_path.unlink()


def parquet_bytes(table_dir: Path) -> int:
    return sum(path.stat().st_size for path in table_dir.glob("part-*.parquet"))


def extract_table(
    conn: pyodbc.Connection,
    table: str,
    snapshot_dir: Path,
    args: argparse.Namespace,
    estimate: dict[str, Any] | None,
) -> dict[str, Any]:
    table_start = time.perf_counter()
    table_dir = snapshot_dir / "tables" / safe_path_name(table)
    table_dir.mkdir(parents=True, exist_ok=True)
    remove_existing_parts(table_dir)

    query, params, filters = build_query(table, args)
    rows_written = 0
    part_count = 0
    columns: list[str] = []

    print(f"Extracting {table}...")
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=f".*{re.escape(PYODBC_PANDAS_WARNING)}.*")
            chunk_iter = pd.read_sql_query(query, conn, params=params, chunksize=args.chunk_rows)
            for chunk in chunk_iter:
                if columns == []:
                    columns = list(chunk.columns)
                part_path = table_dir / f"part-{part_count:05d}.parquet"
                chunk.to_parquet(part_path, index=False, compression="zstd")
                rows_written += len(chunk)
                part_count += 1
                print(f"  {table}: {rows_written:,} rows")

        if part_count == 0:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=f".*{re.escape(PYODBC_PANDAS_WARNING)}.*")
                empty = pd.read_sql_query(
                    f"SELECT TOP (0) * FROM {quote_table(table)} WITH (NOLOCK);",
                    conn,
                )
            if columns == []:
                columns = list(empty.columns)
            empty.to_parquet(table_dir / "part-00000.parquet", index=False, compression="zstd")
            part_count = 1

        status = "ok"
        error = ""
    except Exception as exc:
        status = "error"
        error = str(exc)

    result = {
        "table": normalized_table_name(table),
        "status": status,
        "error": error,
        "estimated_rows": int(estimate["row_count"]) if estimate else None,
        "estimated_columns": int(estimate["column_count"]) if estimate else None,
        "rows_written": rows_written,
        "parts": part_count,
        "output_path": str(table_dir),
        "parquet_bytes": parquet_bytes(table_dir),
        "filters": filters,
        "columns": columns,
        "elapsed_seconds": round(time.perf_counter() - table_start, 2),
    }
    (table_dir / "_table_metadata.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def print_group_listing() -> None:
    for group, tables in TABLE_GROUPS.items():
        print(f"[{group}]")
        for table in tables:
            print(f"  {table}")


def main() -> None:
    args = parse_args()
    if args.list_groups:
        print_group_listing()
        return

    tables = selected_tables(args)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_dir = args.output_dir / "snapshots" / timestamp

    with pyodbc.connect(connection_string(args)) as conn:
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
        connection_info = fetch_dicts(
            cursor,
            "SELECT DB_NAME() AS database_name, @@SERVERNAME AS server_name, SUSER_SNAME() AS login_name, "
            "CONVERT(varchar(33), SYSDATETIMEOFFSET(), 127) AS captured_at;",
        )[0]
        catalog = catalog_by_table(cursor)

        selection_rows = []
        for table in tables:
            estimate = catalog.get(table_key(table))
            selection_rows.append(
                {
                    "table": table,
                    "estimated_rows": int(estimate["row_count"]) if estimate else None,
                    "estimated_columns": int(estimate["column_count"]) if estimate else None,
                    "exists": estimate is not None,
                }
            )

        print("Selected tables:")
        for row in selection_rows:
            print(f"  {row['table']}: rows={row['estimated_rows']} columns={row['estimated_columns']}")

        if args.dry_run:
            return

        snapshot_dir.mkdir(parents=True, exist_ok=False)
        write_csv(snapshot_dir / "selected_tables.csv", selection_rows)

        start = time.perf_counter()
        results = []
        for table in tables:
            result = extract_table(conn, table, snapshot_dir, args, catalog.get(table_key(table)))
            results.append(result)

    summary_rows = [
        {
            "table": result["table"],
            "status": result["status"],
            "estimated_rows": result["estimated_rows"],
            "rows_written": result["rows_written"],
            "parts": result["parts"],
            "parquet_mb": round(result["parquet_bytes"] / 1024 / 1024, 2),
            "elapsed_seconds": result["elapsed_seconds"],
            "output_path": result["output_path"],
            "error": result["error"],
        }
        for result in results
    ]
    write_csv(snapshot_dir / "extract_summary.csv", summary_rows)

    manifest = {
        "generated_at": now_utc(),
        "source": {
            "server": args.server,
            "database": args.database,
            "driver": args.driver,
            "auth": args.auth,
            "user": args.user,
            **connection_info,
        },
        "groups": args.group or ["core"],
        "calendar_start": args.calendar_start,
        "calendar_end": args.calendar_end,
        "max_rows_per_table": args.max_rows_per_table,
        "chunk_rows": args.chunk_rows,
        "snapshot_dir": str(snapshot_dir),
        "table_count": len(results),
        "rows_written": sum(result["rows_written"] for result in results),
        "parquet_bytes": sum(result["parquet_bytes"] for result in results),
        "elapsed_seconds": round(time.perf_counter() - start, 2),
        "tables": results,
    }
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Wrote snapshot: {snapshot_dir}")
    print(f"Wrote manifest: {manifest_path}")
    print(f"Rows written: {manifest['rows_written']:,}")
    print(f"Parquet size: {manifest['parquet_bytes'] / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
