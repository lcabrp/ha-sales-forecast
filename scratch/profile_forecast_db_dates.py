"""Profile date ranges in the Azure SQL Forecast database.

This complements the schema catalog with low-detail recency checks. It reads
aggregate min/max dates and recent process-log rows only.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path
from typing import Any

import pyodbc

from inspect_forecast_db_catalog import (
    DEFAULT_DATABASE,
    DEFAULT_DRIVER,
    DEFAULT_SERVER,
    DEFAULT_USER,
    connection_string,
)


DATE_CHECKS = [
    ("dbo", "Channel_Offer_SKU_Forecast", "CalendarDate"),
    ("dbo", "Channel_Offer_SKU_Forecast_Archive", "CalendarDate"),
    ("dbo", "Channel_Offer_SKU_Forecast_Archive", "Archive_Date"),
    ("dbo", "Channel_Offer_Forecast", "CalendarDate"),
    ("dbo", "Channel_Offer_Forecast_Frozen", "CalendarDate"),
    ("dbo", "Channel_Offer_Forecast_Frozen", "Frozen_Date"),
    ("dbo", "Offer_SKU_Inventory_Forecast", "CalendarDate"),
    ("dbo", "Offer_SKU_Inventory_Forecast", "Last_Updated_Date"),
    ("dbo", "Offer_Inventory_Forecast", "CalendarDate"),
    ("dbo", "Offer_Inventory_Forecast_Frozen", "Frozen_Date"),
    ("dbo", "Channel_Offer_Demand_History", "CalendarDate"),
    ("dbo", "Channel_SKU_SIZE_Weekly_Demand_History", "FISCALWEEKSTARTDATE"),
    ("dbo", "Product_Dimensions_Hierarchy_Attributes", "dbt_loaded_at"),
    ("dbo", "Current_SKU_Available_DC_Inventory", "LastUpdatedDate"),
    ("dbo", "Current_Offer_Inventory", "LastUpdatedDate"),
    ("dbo", "On_Order", "CalendarDate"),
    ("dbo", "Forecast_Job_Log", "Process_Date"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile date ranges in Forecast DB.")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--driver", default=DEFAULT_DRIVER)
    parser.add_argument("--auth", default="ActiveDirectoryInteractive")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output-dir", type=Path, default=Path("scratch"))
    return parser.parse_args()


def quote_name(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def fetch_dicts(cursor: pyodbc.Cursor, sql: str) -> list[dict[str, Any]]:
    cursor.execute(sql)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def markdown_escape(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def table_markdown(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(markdown_escape(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    date_profiles: list[dict[str, Any]] = []
    with pyodbc.connect(connection_string(args)) as conn:
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")

        connection_info = fetch_dicts(
            cursor,
            "SELECT DB_NAME() AS database_name, @@SERVERNAME AS server_name, SUSER_SNAME() AS login_name, "
            "CONVERT(varchar(33), SYSDATETIMEOFFSET(), 127) AS captured_at;",
        )[0]

        for schema_name, table_name, column_name in DATE_CHECKS:
            sql = (
                "SELECT "
                f"MIN({quote_name(column_name)}) AS min_value, "
                f"MAX({quote_name(column_name)}) AS max_value "
                f"FROM {quote_name(schema_name)}.{quote_name(table_name)} WITH (NOLOCK);"
            )
            try:
                row = fetch_dicts(cursor, sql)[0]
                date_profiles.append(
                    {
                        "schema_name": schema_name,
                        "table_name": table_name,
                        "column_name": column_name,
                        "min_value": row["min_value"],
                        "max_value": row["max_value"],
                        "status": "ok",
                    }
                )
            except Exception as exc:  # keep profiling resilient for odd tables
                date_profiles.append(
                    {
                        "schema_name": schema_name,
                        "table_name": table_name,
                        "column_name": column_name,
                        "min_value": "",
                        "max_value": "",
                        "status": f"error: {exc}",
                    }
                )

        recent_jobs = fetch_dicts(
            cursor,
            """
            SELECT TOP (25)
                Process_Date,
                Process_Description
            FROM dbo.Forecast_Job_Log WITH (NOLOCK)
            ORDER BY Process_Date DESC;
            """,
        )

    profile_csv = args.output_dir / f"forecast_db_date_profile_{stamp}.csv"
    jobs_csv = args.output_dir / f"forecast_db_recent_jobs_{stamp}.csv"
    markdown_path = args.output_dir / f"forecast_db_date_profile_{stamp}.md"

    write_csv(profile_csv, date_profiles)
    write_csv(jobs_csv, recent_jobs)

    lines = [
        "# Forecast DB Date Profile",
        "",
        f"- Captured at: `{connection_info.get('captured_at')}`",
        f"- Server: `{connection_info.get('server_name')}`",
        f"- Database: `{connection_info.get('database_name')}`",
        f"- User context: `{connection_info.get('login_name')}`",
        "",
        "Date profiles are aggregate min/max checks only. Recent job rows come from the process log.",
        "",
        "## Date Ranges",
        "",
        table_markdown(
            ["Schema", "Table", "Column", "Min", "Max", "Status"],
            [
                [
                    row["schema_name"],
                    row["table_name"],
                    row["column_name"],
                    row["min_value"],
                    row["max_value"],
                    row["status"],
                ]
                for row in date_profiles
            ],
        ),
        "",
        "## Recent Forecast Job Log",
        "",
        table_markdown(
            ["Process Date", "Description"],
            [[row["Process_Date"], row["Process_Description"]] for row in recent_jobs],
        ),
        "",
    ]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"Markdown: {markdown_path}")
    print(f"date profile: {profile_csv}")
    print(f"recent jobs: {jobs_csv}")


if __name__ == "__main__":
    main()
