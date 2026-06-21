"""Catalog the Azure SQL Forecast database without reading business rows.

This is a one-off investigation helper. It uses Microsoft Entra authentication
through the SQL Server ODBC driver, then writes schema metadata to scratch.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pyodbc


DEFAULT_SERVER = "azprodfcast01.572f3811ca67.database.windows.net"
DEFAULT_DATABASE = "Forecast"
DEFAULT_USER = "labreu@hannaandersson.com"
DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"


TABLES_SQL = """
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
),
fk_out AS (
    SELECT parent_object_id AS object_id, COUNT(*) AS outbound_fk_count
    FROM sys.foreign_keys
    GROUP BY parent_object_id
),
fk_in AS (
    SELECT referenced_object_id AS object_id, COUNT(*) AS inbound_fk_count
    FROM sys.foreign_keys
    GROUP BY referenced_object_id
)
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    COALESCE(rc.row_count, 0) AS row_count,
    COALESCE(cc.column_count, 0) AS column_count,
    COALESCE(fko.outbound_fk_count, 0) AS outbound_fk_count,
    COALESCE(fki.inbound_fk_count, 0) AS inbound_fk_count,
    t.create_date,
    t.modify_date,
    CAST(ep.value AS nvarchar(4000)) AS table_description
FROM sys.tables AS t
INNER JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
LEFT JOIN row_counts AS rc
    ON rc.object_id = t.object_id
LEFT JOIN column_counts AS cc
    ON cc.object_id = t.object_id
LEFT JOIN fk_out AS fko
    ON fko.object_id = t.object_id
LEFT JOIN fk_in AS fki
    ON fki.object_id = t.object_id
LEFT JOIN sys.extended_properties AS ep
    ON ep.major_id = t.object_id
   AND ep.minor_id = 0
   AND ep.name = 'MS_Description'
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name;
"""


COLUMNS_SQL = """
WITH pk_columns AS (
    SELECT
        ic.object_id,
        ic.column_id,
        STRING_AGG(i.name, '; ') AS primary_key_names
    FROM sys.indexes AS i
    INNER JOIN sys.index_columns AS ic
        ON ic.object_id = i.object_id
       AND ic.index_id = i.index_id
    WHERE i.is_primary_key = 1
    GROUP BY ic.object_id, ic.column_id
),
fk_columns AS (
    SELECT
        fkc.parent_object_id AS object_id,
        fkc.parent_column_id AS column_id,
        STRING_AGG(
            CONCAT(
                OBJECT_SCHEMA_NAME(fkc.referenced_object_id), '.',
                OBJECT_NAME(fkc.referenced_object_id), '.',
                COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)
            ),
            '; '
        ) AS references_columns
    FROM sys.foreign_key_columns AS fkc
    GROUP BY fkc.parent_object_id, fkc.parent_column_id
)
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    c.column_id,
    c.name AS column_name,
    CASE
        WHEN ty.name IN ('varchar', 'char', 'varbinary', 'binary') THEN
            CONCAT(ty.name, '(', CASE WHEN c.max_length = -1 THEN 'max' ELSE CONVERT(varchar(10), c.max_length) END, ')')
        WHEN ty.name IN ('nvarchar', 'nchar') THEN
            CONCAT(ty.name, '(', CASE WHEN c.max_length = -1 THEN 'max' ELSE CONVERT(varchar(10), c.max_length / 2) END, ')')
        WHEN ty.name IN ('decimal', 'numeric') THEN
            CONCAT(ty.name, '(', CONVERT(varchar(10), c.precision), ',', CONVERT(varchar(10), c.scale), ')')
        WHEN ty.name IN ('datetime2', 'datetimeoffset', 'time') THEN
            CONCAT(ty.name, '(', CONVERT(varchar(10), c.scale), ')')
        ELSE ty.name
    END AS data_type,
    c.is_nullable,
    c.is_identity,
    c.is_computed,
    dc.definition AS default_definition,
    CAST(ep.value AS nvarchar(4000)) AS column_description,
    CASE WHEN pk.primary_key_names IS NULL THEN 0 ELSE 1 END AS is_primary_key,
    pk.primary_key_names,
    fk.references_columns
FROM sys.tables AS t
INNER JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
INNER JOIN sys.columns AS c
    ON c.object_id = t.object_id
INNER JOIN sys.types AS ty
    ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints AS dc
    ON dc.parent_object_id = c.object_id
   AND dc.parent_column_id = c.column_id
LEFT JOIN sys.extended_properties AS ep
    ON ep.major_id = c.object_id
   AND ep.minor_id = c.column_id
   AND ep.name = 'MS_Description'
LEFT JOIN pk_columns AS pk
    ON pk.object_id = c.object_id
   AND pk.column_id = c.column_id
LEFT JOIN fk_columns AS fk
    ON fk.object_id = c.object_id
   AND fk.column_id = c.column_id
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name, c.column_id;
"""


INDEXES_SQL = """
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    i.name AS index_name,
    i.type_desc,
    i.is_primary_key,
    i.is_unique,
    i.has_filter,
    i.filter_definition,
    STRING_AGG(
        CASE WHEN ic.is_included_column = 0 THEN
            CONCAT(c.name, CASE WHEN ic.is_descending_key = 1 THEN ' DESC' ELSE ' ASC' END)
        END,
        ', '
    ) WITHIN GROUP (ORDER BY ic.key_ordinal) AS key_columns,
    STRING_AGG(
        CASE WHEN ic.is_included_column = 1 THEN c.name END,
        ', '
    ) AS included_columns
FROM sys.tables AS t
INNER JOIN sys.schemas AS s
    ON s.schema_id = t.schema_id
INNER JOIN sys.indexes AS i
    ON i.object_id = t.object_id
INNER JOIN sys.index_columns AS ic
    ON ic.object_id = i.object_id
   AND ic.index_id = i.index_id
INNER JOIN sys.columns AS c
    ON c.object_id = ic.object_id
   AND c.column_id = ic.column_id
WHERE t.is_ms_shipped = 0
  AND i.index_id > 0
GROUP BY
    s.name,
    t.name,
    i.name,
    i.type_desc,
    i.is_primary_key,
    i.is_unique,
    i.has_filter,
    i.filter_definition
ORDER BY s.name, t.name, i.is_primary_key DESC, i.name;
"""


FOREIGN_KEYS_SQL = """
SELECT
    fk.name AS foreign_key_name,
    OBJECT_SCHEMA_NAME(fkc.parent_object_id) AS parent_schema,
    OBJECT_NAME(fkc.parent_object_id) AS parent_table,
    COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS parent_column,
    OBJECT_SCHEMA_NAME(fkc.referenced_object_id) AS referenced_schema,
    OBJECT_NAME(fkc.referenced_object_id) AS referenced_table,
    COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS referenced_column,
    fk.delete_referential_action_desc,
    fk.update_referential_action_desc,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_key_columns AS fkc
INNER JOIN sys.foreign_keys AS fk
    ON fk.object_id = fkc.constraint_object_id
ORDER BY parent_schema, parent_table, foreign_key_name, fkc.constraint_column_id;
"""


KEYWORDS = {
    "forecast",
    "demand",
    "plan",
    "sku",
    "item",
    "style",
    "color",
    "size",
    "sales",
    "order",
    "season",
    "week",
    "inventory",
    "receipt",
    "allocation",
    "channel",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Catalog the Forecast Azure SQL database.")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--driver", default=DEFAULT_DRIVER)
    parser.add_argument("--auth", default="ActiveDirectoryInteractive")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output-dir", type=Path, default=Path("scratch"))
    return parser.parse_args()


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


def score_tables(
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    columns_by_table: dict[tuple[str, str], list[str]] = defaultdict(list)
    for column in columns:
        key = (column["schema_name"], column["table_name"])
        columns_by_table[key].append(column["column_name"])

    scored = []
    for table in tables:
        key = (table["schema_name"], table["table_name"])
        haystack = " ".join([table["table_name"], *columns_by_table[key]]).lower()
        matches = sorted(keyword for keyword in KEYWORDS if keyword in haystack)
        if matches:
            item = dict(table)
            item["keyword_score"] = len(matches)
            item["keyword_matches"] = ", ".join(matches)
            scored.append(item)

    return sorted(
        scored,
        key=lambda row: (row["keyword_score"], row["row_count"], row["schema_name"], row["table_name"]),
        reverse=True,
    )


def write_markdown(
    path: Path,
    connection_info: dict[str, Any],
    tables: list[dict[str, Any]],
    columns: list[dict[str, Any]],
    indexes: list[dict[str, Any]],
    foreign_keys: list[dict[str, Any]],
    output_files: dict[str, Path],
) -> None:
    scored = score_tables(tables, columns)
    total_rows = sum(int(table["row_count"] or 0) for table in tables)
    columns_by_table: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for column in columns:
        columns_by_table[(column["schema_name"], column["table_name"])].append(column)

    lines = [
        "# Forecast DB Catalog",
        "",
        f"- Captured at: `{connection_info.get('captured_at')}`",
        f"- Server: `{connection_info.get('server_name')}`",
        f"- Database: `{connection_info.get('database_name')}`",
        f"- User context: `{connection_info.get('login_name')}`",
        f"- User tables: `{len(tables):,}`",
        f"- User columns: `{len(columns):,}`",
        f"- Approximate user-table rows from metadata: `{total_rows:,}`",
        "",
        "This catalog uses SQL Server metadata only. Row counts come from",
        "`sys.partitions`, not `COUNT(*)`, and no business rows are exported.",
        "",
        "## Output Files",
        "",
    ]

    for label, output_path in output_files.items():
        lines.append(f"- {label}: `{output_path}`")

    lines.extend(
        [
            "",
            "## User Tables",
            "",
            table_markdown(
                [
                    "Schema",
                    "Table",
                    "Rows",
                    "Columns",
                    "Created",
                    "Modified",
                    "FK Out",
                    "FK In",
                ],
                [
                    [
                        table["schema_name"],
                        table["table_name"],
                        f"{int(table['row_count'] or 0):,}",
                        table["column_count"],
                        table["create_date"],
                        table["modify_date"],
                        table["outbound_fk_count"],
                        table["inbound_fk_count"],
                    ]
                    for table in sorted(tables, key=lambda row: int(row["row_count"] or 0), reverse=True)
                ],
            ),
            "",
            "## Forecast-Relevant Candidates",
            "",
            "Candidate ranking is a keyword scan across table and column names. It is a starting point for review, not proof of business meaning.",
            "",
        ]
    )

    candidate_rows = [
        [
            row["schema_name"],
            row["table_name"],
            f"{int(row['row_count'] or 0):,}",
            row["column_count"],
            row["keyword_matches"],
        ]
        for row in scored[:30]
    ]
    lines.append(table_markdown(["Schema", "Table", "Rows", "Columns", "Keyword Matches"], candidate_rows))

    lines.extend(["", "## Columns By Table", ""])
    for table in sorted(tables, key=lambda row: (row["schema_name"], row["table_name"])):
        key = (table["schema_name"], table["table_name"])
        lines.extend(
            [
                f"### {table['schema_name']}.{table['table_name']}",
                "",
                table_markdown(
                    ["#", "Column", "Type", "Nullable", "PK", "References"],
                    [
                        [
                            column["column_id"],
                            column["column_name"],
                            column["data_type"],
                            "Y" if column["is_nullable"] else "N",
                            "Y" if column["is_primary_key"] else "",
                            column["references_columns"] or "",
                        ]
                        for column in columns_by_table[key]
                    ],
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Relationship Summary",
            "",
            f"- Indexes cataloged: `{len(indexes):,}`",
            f"- Foreign-key column mappings cataloged: `{len(foreign_keys):,}`",
            "",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"forecast_db_catalog_{stamp}"

    with pyodbc.connect(connection_string(args)) as conn:
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
        connection_info = fetch_dicts(
            cursor,
            "SELECT DB_NAME() AS database_name, @@SERVERNAME AS server_name, SUSER_SNAME() AS login_name, "
            "CONVERT(varchar(33), SYSDATETIMEOFFSET(), 127) AS captured_at;",
        )[0]
        tables = fetch_dicts(cursor, TABLES_SQL)
        columns = fetch_dicts(cursor, COLUMNS_SQL)
        indexes = fetch_dicts(cursor, INDEXES_SQL)
        foreign_keys = fetch_dicts(cursor, FOREIGN_KEYS_SQL)

    output_files = {
        "tables": args.output_dir / f"{prefix}_tables.csv",
        "columns": args.output_dir / f"{prefix}_columns.csv",
        "indexes": args.output_dir / f"{prefix}_indexes.csv",
        "foreign keys": args.output_dir / f"{prefix}_foreign_keys.csv",
    }
    markdown_path = args.output_dir / f"{prefix}.md"

    write_csv(output_files["tables"], tables)
    write_csv(output_files["columns"], columns)
    write_csv(output_files["indexes"], indexes)
    write_csv(output_files["foreign keys"], foreign_keys)
    write_markdown(markdown_path, connection_info, tables, columns, indexes, foreign_keys, output_files)

    print(f"Markdown: {markdown_path}")
    for label, output_path in output_files.items():
        print(f"{label}: {output_path}")
    print(f"Tables: {len(tables):,}")
    print(f"Columns: {len(columns):,}")


if __name__ == "__main__":
    main()
