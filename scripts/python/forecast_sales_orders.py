"""Extract sales-order price and discount features from AX for forecast modeling.

This complements the warehouse DirectPick actuals. DirectPick tells us what
was fulfilled; SALESLINE/SALESTABLE tells us what was ordered and at what price.
The price/discount fields are useful for validating PDL promotion windows and
for learning realized promotion lift.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
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


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "sales_orders"
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "sales_orders.db"
DEFAULT_EXCLUDED_ITEMS = ("30991", "3333", "9999")
DEFAULT_ORIGINS = ("WEB", "CALLCENTER")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the sales orders extractor.

    Returns:
        argparse.Namespace: Checked command line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Extract SALESLINE/SALESTABLE price-discount features for forecasting."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--server", default="prodaxsql2")
    probe.add_argument("--database", default="DAX_PROD")
    probe.add_argument("--schema", default="DAX_PROD.dbo")

    collect = subparsers.add_parser("collect")
    collect.add_argument("--start-date", required=True, help="Inclusive order date, YYYY-MM-DD.")
    collect.add_argument("--end-date", required=True, help="Inclusive order date, YYYY-MM-DD.")
    collect.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    collect.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    collect.add_argument("--server", default="prodaxsql2")
    collect.add_argument("--database", default="DAX_PROD")
    collect.add_argument("--schema", default="DAX_PROD.dbo")
    collect.add_argument("--chunk-days", type=int, default=31)
    collect.add_argument(
        "--origin",
        action="append",
        dest="origins",
        help="Sales origin to include. Defaults to WEB and CALLCENTER. Repeat for more.",
    )
    collect.add_argument(
        "--include-all-origins",
        action="store_true",
        help="Do not filter SALESTABLE.SALESORIGINID.",
    )
    collect.add_argument(
        "--exclude-item",
        action="append",
        dest="excluded_items",
        help="AX ITEMID to exclude from demand features. Defaults exclude virtual/gift-card items.",
    )
    collect.add_argument(
        "--keep-parts",
        action="store_true",
        help="Keep chunk-level sales_order_lines_part_*.parquet files after summaries are built.",
    )
    collect.add_argument(
        "--no-sqlite",
        action="store_true",
        help="Skip the convenience SQLite database.",
    )
    collect.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip CSV mirrors and write Parquet plus optional SQLite only. Recommended for large pulls.",
    )

    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    summarize.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    summarize.add_argument("--no-sqlite", action="store_true")
    summarize.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip CSV mirrors and write Parquet plus optional SQLite only.",
    )

    return parser.parse_args()


def normalize_text(series: pd.Series) -> pd.Series:
    """Safely fill missing values with empty strings, cast to string, and strip white space.

    Args:
        series: Target pandas Series.

    Returns:
        pd.Series: Normalized Series.
    """
    return series.fillna("").astype(str).str.strip()


def date_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    """Split a broad date range into smaller sub-intervals for chunked SQL extraction.

    Args:
        start: Inclusive start date.
        end: Inclusive end date.
        chunk_days: Maximum size of date chunks in days.

    Returns:
        list[tuple[date, date]]: List of start/end chunk tuples.
    """
    chunks = []
    current = start
    while current <= end:
        chunk_end = min(end, current + timedelta(days=chunk_days - 1))
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def sql_list(values: tuple[str, ...] | list[str]) -> str:
    """Escapes single quotes and maps string values to comma-separated single-quoted SQL list fragment.

    Args:
        values: Strings to format.

    Returns:
        str: Comma-separated SQL list fragment.
    """
    return ", ".join(f"'{value.replace("'", "''")}'" for value in values)


def probe_query(schema: str) -> sa.TextClause:
    """Query database schema definition to inspect SALESTABLE/SALESLINE column types.

    Args:
        schema: Target database schema name (e.g. 'dbo').

    Returns:
        sa.TextClause: Executable SQL query.
    """
    return sa.text(
        """
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            'SALESTABLE' AS TableName,
            COLUMN_NAME AS ColumnName,
            DATA_TYPE AS DataType
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = PARSENAME(:schema_name, 1)
          AND TABLE_NAME = 'SALESTABLE'
          AND COLUMN_NAME IN (
              'SALESID', 'HADWSALESID', 'HADWSALESIDONLY', 'SALESORIGINID',
              'CUSTACCOUNT', 'SALESSTATUS', 'SALESTYPE', 'HAORDERDATETIME',
              'CREATEDDATETIME', 'MODIFIEDDATETIME', 'DATAAREAID', 'PARTITION'
          )
        UNION ALL
        SELECT
            'SALESLINE' AS TableName,
            COLUMN_NAME AS ColumnName,
            DATA_TYPE AS DataType
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = PARSENAME(:schema_name, 1)
          AND TABLE_NAME = 'SALESLINE'
          AND COLUMN_NAME IN (
              'SALESID', 'LINENUM', 'ITEMID', 'INVENTDIMID', 'INVENTTRANSID',
              'QTYORDERED', 'SALESQTY', 'SALESPRICE', 'HAMSRP', 'HACOSTAMOUNT',
              'LINEDISC', 'LINEPERCENT', 'MULTILNDISC', 'MULTILNPERCENT',
              'LINEAMOUNT', 'SALESSTATUS', 'SALESTYPE', 'CUSTGROUP',
              'HAMERCHPLANNINGCHANNEL', 'HARETURNREASONCODE',
              'DATAAREAID', 'PARTITION'
          )
        ORDER BY TableName, ColumnName;
        """
    )


def sales_line_query(
    schema: str,
    origins: tuple[str, ...],
    excluded_items: tuple[str, ...],
    include_all_origins: bool,
) -> sa.TextClause:
    """Build query targeting SALESTABLE and SALESLINE to retrieve order lines and details.

    Applies company, partition, order types, exclusions, and origin filters.

    Args:
        schema: Schema namespace.
        origins: Target sales origin codes (e.g. WEB).
        excluded_items: ItemIDs to discard.
        include_all_origins: If True, skips the origin filter.

    Returns:
        sa.TextClause: Query string.
    """
    origin_filter = ""
    if not include_all_origins:
        origin_filter = f"AND st.SALESORIGINID IN ({sql_list(origins)})"
    excluded_filter = ""
    if excluded_items:
        excluded_filter = f"AND sl.ITEMID NOT IN ({sql_list(excluded_items)})"

    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        WITH FilteredSalesTable AS (
            SELECT
                st.SALESID,
                st.HADWSALESID,
                st.SALESORIGINID,
                st.SALESSTATUS,
                st.SALESTYPE,
                st.HAORDERDATETIME,
                st.CREATEDDATETIME,
                st.MODIFIEDDATETIME,
                st.DATAAREAID,
                st.[PARTITION]
            FROM {schema}.SALESTABLE st WITH (NOLOCK)
            WHERE st.DATAAREAID = 'ha'
              AND st.[PARTITION] = 5637144576
              AND st.SALESTYPE = 3
              AND st.HAORDERDATETIME >= :start_dt
              AND st.HAORDERDATETIME < :end_dt
              {origin_filter}
        )
        SELECT
            CAST(st.HAORDERDATETIME AS DATE) AS OrderDateUTC,
            st.SALESID AS SalesID,
            st.HADWSALESID AS SFCCOrderID,
            st.SALESORIGINID AS SalesOrigin,
            st.SALESSTATUS AS HeaderSalesStatus,
            st.SALESTYPE AS HeaderSalesType,
            st.HAORDERDATETIME AS OrderDateTimeUTC,
            st.CREATEDDATETIME AS HeaderCreatedDateTimeUTC,
            st.MODIFIEDDATETIME AS HeaderModifiedDateTimeUTC,
            sl.RECID AS SalesLineRecID,
            CAST(sl.LINENUM AS FLOAT) AS LineNum,
            sl.ITEMID AS ItemID,
            idim.INVENTCOLORID AS ColorID,
            idim.INVENTSIZEID AS SizeID,
            CASE
                WHEN ISNULL(idim.INVENTCOLORID, '') = '' THEN sl.ITEMID
                WHEN ISNULL(idim.INVENTSIZEID, '') = '' THEN sl.ITEMID + '-' + idim.INVENTCOLORID
                ELSE sl.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID
            END AS SKU,
            sl.INVENTDIMID AS InventDimID,
            sl.INVENTTRANSID AS InventTransID,
            sl.HAMERCHPLANNINGCHANNEL AS MerchPlanningChannel,
            sl.HARETURNREASONCODE AS ReturnReasonCode,
            sl.CUSTGROUP AS CustGroup,
            sl.SALESSTATUS AS LineSalesStatus,
            sl.SALESTYPE AS LineSalesType,
            CAST(sl.QTYORDERED AS FLOAT) AS QtyOrdered,
            CAST(sl.SALESQTY AS FLOAT) AS SalesQty,
            CAST(sl.HAMSRP AS FLOAT) AS MSRP,
            CAST(sl.SALESPRICE AS FLOAT) AS SalesPrice,
            CAST(sl.HACOSTAMOUNT AS FLOAT) AS CostAmount,
            CAST(sl.LINEDISC AS FLOAT) AS LineDiscAmount,
            CAST(sl.LINEPERCENT AS FLOAT) AS LineDiscPercent,
            CAST(sl.MULTILNDISC AS FLOAT) AS MultiLineDiscAmount,
            CAST(sl.MULTILNPERCENT AS FLOAT) AS MultiLineDiscPercent,
            CAST(sl.LINEAMOUNT AS FLOAT) AS LineAmount,
            sl.MODIFIEDDATETIME AS LineModifiedDateTimeUTC
        FROM FilteredSalesTable st
        JOIN {schema}.SALESLINE sl WITH (NOLOCK)
            ON st.SALESID = sl.SALESID
           AND st.DATAAREAID = sl.DATAAREAID
           AND st.[PARTITION] = sl.[PARTITION]
        LEFT JOIN {schema}.INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = sl.INVENTDIMID
           AND idim.DATAAREAID = sl.DATAAREAID
           AND idim.[PARTITION] = sl.[PARTITION]
        WHERE sl.DATAAREAID = 'ha'
          AND sl.[PARTITION] = 5637144576
          AND sl.SALESTYPE = 3
          AND sl.QTYORDERED <> 0
          {excluded_filter}
        ORDER BY st.SALESID, sl.LINENUM;
        """
    )


def enrich_lines(lines: pd.DataFrame) -> pd.DataFrame:
    """Enrich raw sales line records with calculated metrics and discount rates.

    Args:
        lines: Raw order lines DataFrame.

    Returns:
        pd.DataFrame: DataFrame populated with gross amounts, discount percentages, and indicators.
    """
    if lines.empty:
        return lines
    text_cols = [
        "SalesID",
        "SFCCOrderID",
        "SalesOrigin",
        "ItemID",
        "ColorID",
        "SizeID",
        "SKU",
        "InventDimID",
        "InventTransID",
        "MerchPlanningChannel",
        "ReturnReasonCode",
        "CustGroup",
    ]
    for col in text_cols:
        if col in lines:
            lines[col] = normalize_text(lines[col])

    for col in (
        "QtyOrdered",
        "SalesQty",
        "MSRP",
        "SalesPrice",
        "CostAmount",
        "LineDiscAmount",
        "LineDiscPercent",
        "MultiLineDiscAmount",
        "MultiLineDiscPercent",
        "LineAmount",
    ):
        lines[col] = pd.to_numeric(lines[col], errors="coerce").fillna(0.0)

    qty = lines["QtyOrdered"].where(lines["QtyOrdered"].ne(0), lines["SalesQty"])
    lines["AbsQtyOrdered"] = qty.abs()
    lines["EffectiveUnitPrice"] = lines["LineAmount"] / qty.replace(0, pd.NA)
    lines["GrossMSRPAmount"] = lines["MSRP"] * qty
    lines["GrossSalesPriceAmount"] = lines["SalesPrice"] * qty
    lines["DiscountAmountVsMSRP"] = lines["GrossMSRPAmount"] - lines["LineAmount"]
    lines["DiscountAmountVsSalesPrice"] = lines["GrossSalesPriceAmount"] - lines["LineAmount"]
    lines["DiscountPctVsMSRP"] = 1 - (lines["LineAmount"] / lines["GrossMSRPAmount"].replace(0, pd.NA))
    lines["DiscountPctVsSalesPrice"] = 1 - (
        lines["LineAmount"] / lines["GrossSalesPriceAmount"].replace(0, pd.NA)
    )
    lines["ObservedDiscountPct"] = lines[["DiscountPctVsMSRP", "DiscountPctVsSalesPrice"]].max(axis=1)
    lines["ObservedDiscountPct"] = lines["ObservedDiscountPct"].clip(lower=0, upper=1)
    lines["HasObservedDiscount"] = (
        (lines["ObservedDiscountPct"].fillna(0) >= 0.05)
        | (lines["LineDiscAmount"].abs() > 0)
        | (lines["LineDiscPercent"].abs() > 0)
        | (lines["MultiLineDiscAmount"].abs() > 0)
        | (lines["MultiLineDiscPercent"].abs() > 0)
    )
    lines["IsReturnOrNegativeQty"] = qty < 0
    return lines


def summarize_parts(output_dir: Path) -> dict[str, pd.DataFrame]:
    """Read saved Parquet parts, consolidate records, and build SKU/Day and daily summary rollups.

    Args:
        output_dir: Output folder.

    Returns:
        dict[str, pd.DataFrame]: Summarized DataFrames map.
    """
    parts_dir = output_dir / "parts"
    part_paths = sorted(parts_dir.glob("sales_order_lines_part_*.parquet"))
    if not part_paths:
        raise FileNotFoundError(f"No sales order line part files found in {parts_dir}")

    frames = [pd.read_parquet(path) for path in part_paths]
    lines = pd.concat(frames, ignore_index=True)
    order_line_sample = lines.head(1000).copy()

    sku_day = (
        lines.groupby(["OrderDateUTC", "SKU"], as_index=False)
        .agg(
            OrderedUnits=("QtyOrdered", "sum"),
            AbsOrderedUnits=("AbsQtyOrdered", "sum"),
            SalesLineCount=("SalesLineRecID", "count"),
            DistinctOrders=("SalesID", "nunique"),
            GrossMSRPAmount=("GrossMSRPAmount", "sum"),
            GrossSalesPriceAmount=("GrossSalesPriceAmount", "sum"),
            LineAmount=("LineAmount", "sum"),
            DiscountAmountVsMSRP=("DiscountAmountVsMSRP", "sum"),
            DiscountAmountVsSalesPrice=("DiscountAmountVsSalesPrice", "sum"),
            MaxObservedDiscountPct=("ObservedDiscountPct", "max"),
            DiscountedLineCount=("HasObservedDiscount", lambda values: int(values.fillna(False).sum())),
            ReturnOrNegativeLineCount=(
                "IsReturnOrNegativeQty",
                lambda values: int(values.fillna(False).sum()),
            ),
        )
        .sort_values(["OrderDateUTC", "SKU"])
    )
    sku_day["WeightedDiscountPctVsMSRP"] = 1 - (
        sku_day["LineAmount"] / sku_day["GrossMSRPAmount"].replace(0, pd.NA)
    )
    sku_day["WeightedDiscountPctVsSalesPrice"] = 1 - (
        sku_day["LineAmount"] / sku_day["GrossSalesPriceAmount"].replace(0, pd.NA)
    )
    sku_day["HasObservedDiscount"] = sku_day["DiscountedLineCount"] > 0

    daily = (
        lines.groupby("OrderDateUTC", as_index=False)
        .agg(
            OrderedUnits=("QtyOrdered", "sum"),
            AbsOrderedUnits=("AbsQtyOrdered", "sum"),
            SalesLineCount=("SalesLineRecID", "count"),
            DistinctOrders=("SalesID", "nunique"),
            DistinctSKUs=("SKU", "nunique"),
            GrossMSRPAmount=("GrossMSRPAmount", "sum"),
            GrossSalesPriceAmount=("GrossSalesPriceAmount", "sum"),
            LineAmount=("LineAmount", "sum"),
            DiscountAmountVsMSRP=("DiscountAmountVsMSRP", "sum"),
            DiscountAmountVsSalesPrice=("DiscountAmountVsSalesPrice", "sum"),
            MaxObservedDiscountPct=("ObservedDiscountPct", "max"),
            DiscountedLineCount=("HasObservedDiscount", lambda values: int(values.fillna(False).sum())),
        )
        .sort_values("OrderDateUTC")
    )
    daily["WeightedDiscountPctVsMSRP"] = 1 - (
        daily["LineAmount"] / daily["GrossMSRPAmount"].replace(0, pd.NA)
    )
    daily["WeightedDiscountPctVsSalesPrice"] = 1 - (
        daily["LineAmount"] / daily["GrossSalesPriceAmount"].replace(0, pd.NA)
    )

    discount_bands = lines.copy()
    discount_bands["DiscountBand"] = pd.cut(
        discount_bands["ObservedDiscountPct"].fillna(0),
        bins=[-0.001, 0.0499, 0.1499, 0.2499, 0.3499, 0.4999, 0.6999, 1.0],
        labels=["<5%", "5-15%", "15-25%", "25-35%", "35-50%", "50-70%", "70%+"],
    )
    daily_discount_bands = (
        discount_bands.groupby(["OrderDateUTC", "DiscountBand"], observed=False, as_index=False)
        .agg(
            OrderedUnits=("QtyOrdered", "sum"),
            SalesLineCount=("SalesLineRecID", "count"),
            DistinctSKUs=("SKU", "nunique"),
            LineAmount=("LineAmount", "sum"),
        )
        .sort_values(["OrderDateUTC", "DiscountBand"])
    )

    return {
        "sales_order_lines_sample": order_line_sample,
        "sales_order_sku_day": sku_day,
        "sales_order_daily_summary": daily,
        "sales_order_daily_discount_bands": daily_discount_bands,
    }


def write_tables(
    tables: dict[str, pd.DataFrame],
    output_dir: Path,
    db_path: Path,
    no_sqlite: bool,
    no_csv: bool,
) -> None:
    """Save processed DataFrames in Parquet, CSV, and SQLite database storage.

    Args:
        tables: Maps table names to DataFrames.
        output_dir: Output folder.
        db_path: Target SQLite file.
        no_sqlite: If True, skips SQLite database.
        no_csv: If True, skips CSV mirroring.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df.to_parquet(output_dir / f"{name}.parquet", index=False, compression="zstd")
        if not no_csv:
            df.to_csv(output_dir / f"{name}.csv", index=False)
    if no_sqlite:
        return
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        for name, df in tables.items():
            sqlite_df = df.copy()
            for column in sqlite_df.columns:
                if pd.api.types.is_datetime64_any_dtype(sqlite_df[column]):
                    sqlite_df[column] = sqlite_df[column].dt.strftime("%Y-%m-%dT%H:%M:%S")
                elif sqlite_df[column].dtype == object:
                    sample = sqlite_df[column].dropna().head(100)
                    if any(isinstance(value, pd.Timestamp | datetime | date) for value in sample):
                        sqlite_df[column] = sqlite_df[column].map(
                            lambda value: value.isoformat()
                            if isinstance(value, pd.Timestamp | datetime | date)
                            else value
                        )
            sqlite_df.to_sql(name, conn, if_exists="replace", index=False)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS ix_sales_order_sku_day_date_sku
                ON sales_order_sku_day (OrderDateUTC, SKU);
            CREATE INDEX IF NOT EXISTS ix_sales_order_daily_summary_date
                ON sales_order_daily_summary (OrderDateUTC);
            """
        )


def write_summary(
    output_dir: Path,
    db_path: Path,
    tables: dict[str, pd.DataFrame],
    *,
    start: date | None = None,
    end: date | None = None,
    no_sqlite: bool = False,
) -> None:
    """Save extraction run metadata metrics.

    Args:
        output_dir: Target folder.
        db_path: SQLite DB path.
        tables: Data DataFrames.
        start: Extraction start.
        end: Extraction end.
        no_sqlite: True to skip SQLite.
    """
    sku_day = tables["sales_order_sku_day"]
    daily = tables["sales_order_daily_summary"]
    summary = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "output_dir": str(output_dir.resolve()),
        "sqlite_db": "" if no_sqlite else str(db_path.resolve()),
        "requested_start_date": start.isoformat() if start else "",
        "requested_end_date": end.isoformat() if end else "",
        "order_date_min": str(daily["OrderDateUTC"].min()) if not daily.empty else "",
        "order_date_max": str(daily["OrderDateUTC"].max()) if not daily.empty else "",
        "sku_day_rows": int(len(sku_day)),
        "daily_rows": int(len(daily)),
        "distinct_skus": int(sku_day["SKU"].nunique()) if not sku_day.empty else 0,
        "ordered_units": float(daily["OrderedUnits"].sum()) if not daily.empty else 0.0,
        "line_amount": float(daily["LineAmount"].sum()) if not daily.empty else 0.0,
        "tables": {name: int(len(df)) for name, df in tables.items()},
    }
    (output_dir / "sales_order_extraction_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))


def run_probe(args: argparse.Namespace) -> None:
    """Query schema structures of SALESTABLE and SALESLINE tables.

    Args:
        args: Command parameters.
    """
    engine = get_ax_engine(server=args.server, database=args.database, verbose=True)
    with engine.connect() as conn:
        columns = pd.read_sql_query(probe_query(args.schema), conn, params={"schema_name": args.schema})
    print(columns.to_string(index=False))


def run_collect(args: argparse.Namespace) -> None:
    """Query order line details sequentially in chunk intervals, cleaning, enriching, and saving them.

    Args:
        args: Command parameters.
    """
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise ValueError("--end-date must be on or after --start-date")
    if args.chunk_days < 1:
        raise ValueError("--chunk-days must be positive")

    output_dir = args.output_dir
    parts_dir = output_dir / "parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    origins = tuple(args.origins or DEFAULT_ORIGINS)
    excluded_items = tuple(args.excluded_items or DEFAULT_EXCLUDED_ITEMS)
    engine = get_ax_engine(server=args.server, database=args.database, verbose=True)
    query = sales_line_query(args.schema, origins, excluded_items, args.include_all_origins)

    part_count = 0
    with engine.connect() as conn:
        for chunk_start, chunk_end in date_chunks(start, end, args.chunk_days):
            end_exclusive = chunk_end + timedelta(days=1)
            print(f"pull {chunk_start} to {chunk_end}")
            lines = pd.read_sql_query(
                query,
                conn,
                params={
                    "start_dt": chunk_start.isoformat(),
                    "end_dt": end_exclusive.isoformat(),
                },
            )
            lines = enrich_lines(lines)
            part_path = (
                parts_dir
                / f"sales_order_lines_part_{chunk_start.isoformat()}_{chunk_end.isoformat()}.parquet"
            )
            lines.to_parquet(part_path, index=False, compression="zstd")
            part_count += 1
            print(f"  rows {len(lines):,} units {lines['QtyOrdered'].sum() if not lines.empty else 0:,.0f}")

    print(f"parts written: {part_count}")
    tables = summarize_parts(output_dir)
    write_tables(tables, output_dir, args.db, args.no_sqlite, args.no_csv)
    write_summary(output_dir, args.db, tables, start=start, end=end, no_sqlite=args.no_sqlite)
    if not args.keep_parts:
        shutil.rmtree(parts_dir)


def run_summarize(args: argparse.Namespace) -> None:
    """Generate daily summaries and matrices from previously downloaded parquet parts.

    Args:
        args: Command parameters.
    """
    tables = summarize_parts(args.output_dir)
    write_tables(tables, args.output_dir, args.db, args.no_sqlite, args.no_csv)
    write_summary(args.output_dir, args.db, tables, no_sqlite=args.no_sqlite)


def main() -> None:
    """Main CLI entry point for sales orders extractor."""
    args = parse_args()
    if args.command == "probe":
        run_probe(args)
    elif args.command == "collect":
        run_collect(args)
    elif args.command == "summarize":
        run_summarize(args)


if __name__ == "__main__":
    main()
