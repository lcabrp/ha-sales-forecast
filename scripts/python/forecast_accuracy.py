"""Build a local forecast-accuracy SQLite workspace from forecast CSV snapshots.

This is intentionally data-first.  The initial import preserves weekly forecast
snapshots and unpivots nonzero FD1-FD14 values into SKU-day rows.  Actual
sales/pick imports can then join to forecast_sku_snapshot and forecast_sku_day
without rereading the large CSVs.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import INGESTION_OUTPUT_DIR, PROJECT_ROOT  # noqa: E402
from sql_utils import get_ax_engine  # noqa: E402


FORECAST_FILE_RE = re.compile(r"^FwdDemandCSV_(\d{4}-\d{2}-\d{2})(?:_[0-9a-f]+)?\.csv$")
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "Forecast_Accuracy.db"
FD_COLUMNS = [f"FD{i}" for i in range(1, 15)]
SKU_COLUMNS = [
    "SKU",
    "Item",
    "Color",
    "Size",
    "Division",
    "Department",
    "Class",
    "KeyCategoryView",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "PutawayIndicator",
    "ReplenishmentThreshold",
    "ForecastStartDate",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import FwdDemandCSV snapshots into a forecast accuracy SQLite database."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-forecasts")
    import_parser.add_argument(
        "--input-dir",
        type=Path,
        default=INGESTION_OUTPUT_DIR,
        help="Directory containing FwdDemandCSV_YYYY-MM-DD.csv or hashed confirmed snapshot files.",
    )
    import_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path to create/update.",
    )
    import_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace already-imported snapshots with the current file contents.",
    )
    import_parser.add_argument(
        "--include-zero-days",
        action="store_true",
        help="Also persist zero-quantity SKU/day forecast rows. This is usually unnecessary and large.",
    )

    actuals_parser = subparsers.add_parser("import-actuals")
    actuals_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    actuals_parser.add_argument(
        "--start-date",
        required=True,
        help="Inclusive actuals start date in YYYY-MM-DD format.",
    )
    actuals_parser.add_argument(
        "--end-date",
        required=True,
        help="Inclusive actuals end date in YYYY-MM-DD format.",
    )
    actuals_parser.add_argument("--server", default="prodaxsql2")
    actuals_parser.add_argument("--database", default="DAX_PROD")
    actuals_parser.add_argument(
        "--date-field",
        choices=("created", "modified"),
        default="created",
        help="Use work header created date or pick line modified date as the actuals date basis.",
    )

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)

    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_file_date(path: Path) -> date:
    match = FORECAST_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Expected FwdDemandCSV_YYYY-MM-DD.csv, got {path.name}")
    return date.fromisoformat(match.group(1))


def snapshot_id(path: Path, source_sha256: str) -> str:
    value = f"{path.name.lower()}|{source_sha256}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS forecast_snapshot_files (
            SnapshotId TEXT PRIMARY KEY,
            SourceFile TEXT NOT NULL,
            SourcePath TEXT NOT NULL,
            SourceSha256 TEXT NOT NULL,
            FileDate TEXT NOT NULL,
            ImportedAtUTC TEXT NOT NULL,
            RowsImported INTEGER NOT NULL,
            DistinctSKUs INTEGER NOT NULL,
            ForecastStartDateMin TEXT NOT NULL,
            ForecastStartDateMax TEXT NOT NULL,
            TotalForecastUnits REAL NOT NULL,
            ForecastedSKUs INTEGER NOT NULL,
            ZeroForecastSKUs INTEGER NOT NULL,
            ActiveSKUCount INTEGER NOT NULL,
            ReserveSKUCount INTEGER NOT NULL,
            OffsiteSKUCount INTEGER NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS ux_forecast_snapshot_files_source
            ON forecast_snapshot_files (SourceFile, SourceSha256);

        CREATE TABLE IF NOT EXISTS forecast_sku_snapshot (
            SnapshotId TEXT NOT NULL,
            FileDate TEXT NOT NULL,
            SKU TEXT NOT NULL,
            Item TEXT NOT NULL,
            Color TEXT NOT NULL,
            Size TEXT NOT NULL,
            Division TEXT NOT NULL,
            Department TEXT NOT NULL,
            Class TEXT NOT NULL,
            KeyCategoryView TEXT NOT NULL,
            ProductGroupCode TEXT NOT NULL,
            SizeGroupCode TEXT NOT NULL,
            Velocity TEXT NOT NULL,
            SlotTier TEXT NOT NULL,
            PutawayIndicator INTEGER,
            ReplenishmentThreshold REAL,
            ForecastStartDate TEXT NOT NULL,
            ForecastTotal14Day REAL NOT NULL,
            HasForecastDemand INTEGER NOT NULL CHECK (HasForecastDemand IN (0, 1)),
            PRIMARY KEY (SnapshotId, SKU),
            FOREIGN KEY (SnapshotId) REFERENCES forecast_snapshot_files (SnapshotId)
        );

        CREATE INDEX IF NOT EXISTS ix_forecast_sku_snapshot_sku
            ON forecast_sku_snapshot (SKU, FileDate);

        CREATE INDEX IF NOT EXISTS ix_forecast_sku_snapshot_slottier
            ON forecast_sku_snapshot (SlotTier, FileDate);

        CREATE TABLE IF NOT EXISTS forecast_sku_day (
            SnapshotId TEXT NOT NULL,
            FileDate TEXT NOT NULL,
            SKU TEXT NOT NULL,
            ForecastStartDate TEXT NOT NULL,
            ForecastDayOffset INTEGER NOT NULL,
            ForecastDate TEXT NOT NULL,
            ForecastQty REAL NOT NULL,
            PRIMARY KEY (SnapshotId, SKU, ForecastDayOffset),
            FOREIGN KEY (SnapshotId, SKU)
                REFERENCES forecast_sku_snapshot (SnapshotId, SKU)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS ix_forecast_sku_day_date_sku
            ON forecast_sku_day (ForecastDate, SKU);

        CREATE INDEX IF NOT EXISTS ix_forecast_sku_day_sku_date
            ON forecast_sku_day (SKU, ForecastDate);

        CREATE VIEW IF NOT EXISTS vw_forecast_snapshot_summary AS
        SELECT
            FileDate,
            SourceFile,
            RowsImported,
            DistinctSKUs,
            ForecastedSKUs,
            ZeroForecastSKUs,
            TotalForecastUnits,
            ActiveSKUCount,
            ReserveSKUCount,
            OffsiteSKUCount,
            ForecastStartDateMin,
            ForecastStartDateMax
        FROM forecast_snapshot_files;

        CREATE VIEW IF NOT EXISTS vw_forecast_daily_totals AS
        SELECT
            FileDate,
            ForecastDate,
            COUNT(*) AS SKUsWithForecastQty,
            SUM(ForecastQty) AS ForecastUnits
        FROM forecast_sku_day
        WHERE ForecastQty <> 0
        GROUP BY FileDate, ForecastDate;

        CREATE TABLE IF NOT EXISTS actual_sku_day (
            ActualDate TEXT NOT NULL,
            DateBasis TEXT NOT NULL,
            SKU TEXT NOT NULL,
            SoldUnits REAL NOT NULL,
            PickLines INTEGER NOT NULL,
            DistinctOrders INTEGER NOT NULL,
            ImportedAtUTC TEXT NOT NULL,
            PRIMARY KEY (ActualDate, DateBasis, SKU)
        );

        CREATE INDEX IF NOT EXISTS ix_actual_sku_day_sku_date
            ON actual_sku_day (SKU, ActualDate);

        CREATE VIEW IF NOT EXISTS vw_forecast_actual_14day AS
        SELECT
            sf.SnapshotId,
            'created' AS DateBasis,
            sf.FileDate,
            sf.SourceFile,
            fs.SKU,
            fs.ProductGroupCode,
            fs.SizeGroupCode,
            fs.Velocity,
            fs.SlotTier,
            fs.ForecastStartDate,
            fs.ForecastTotal14Day,
            COALESCE(SUM(a.SoldUnits), 0) AS SoldUnits14Day,
            COALESCE(SUM(a.PickLines), 0) AS PickLines14Day,
            COUNT(DISTINCT a.ActualDate) AS ActualDaysWithSales,
            COALESCE(SUM(a.SoldUnits), 0) - fs.ForecastTotal14Day AS UnitVariance,
            ABS(COALESCE(SUM(a.SoldUnits), 0) - fs.ForecastTotal14Day) AS AbsUnitVariance
        FROM forecast_sku_snapshot fs
        JOIN forecast_snapshot_files sf
            ON sf.SnapshotId = fs.SnapshotId
        LEFT JOIN actual_sku_day a
            ON a.SKU = fs.SKU
           AND a.DateBasis = 'created'
           AND a.ActualDate >= fs.ForecastStartDate
           AND a.ActualDate < date(fs.ForecastStartDate, '+14 day')
        GROUP BY
            sf.SnapshotId,
            sf.FileDate,
            sf.SourceFile,
            fs.SKU,
            fs.ProductGroupCode,
            fs.SizeGroupCode,
            fs.Velocity,
            fs.SlotTier,
            fs.ForecastStartDate,
            fs.ForecastTotal14Day;
        """
    )


def normalize_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def load_forecast_csv(path: Path) -> pd.DataFrame:
    usecols = [col for col in [*SKU_COLUMNS, *FD_COLUMNS] if col]
    df = pd.read_csv(path, usecols=lambda col: col in usecols)
    missing_cols = sorted(set([*SKU_COLUMNS, *FD_COLUMNS]) - set(df.columns))
    if missing_cols:
        raise ValueError(f"{path.name} missing required columns: {missing_cols}")

    for col in SKU_COLUMNS:
        if col in {"PutawayIndicator", "ReplenishmentThreshold"}:
            continue
        df[col] = normalize_text(df[col])
    for col in FD_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["PutawayIndicator"] = pd.to_numeric(df["PutawayIndicator"], errors="coerce").astype("Int64")
    df["ReplenishmentThreshold"] = pd.to_numeric(
        df["ReplenishmentThreshold"],
        errors="coerce",
    )
    df["ForecastStartDate"] = pd.to_datetime(df["ForecastStartDate"], errors="coerce").dt.date
    if df["ForecastStartDate"].isna().any():
        bad = df.loc[df["ForecastStartDate"].isna(), "SKU"].head(10).tolist()
        raise ValueError(f"{path.name} has invalid ForecastStartDate rows, sample SKUs: {bad}")

    duplicate_skus = df["SKU"].duplicated(keep=False)
    if duplicate_skus.any():
        sample = df.loc[duplicate_skus, "SKU"].head(10).tolist()
        raise ValueError(f"{path.name} has duplicate SKUs, sample: {sample}")

    return df


def delete_snapshot(conn: sqlite3.Connection, snap_id: str) -> None:
    conn.execute("DELETE FROM forecast_sku_day WHERE SnapshotId = ?", (snap_id,))
    conn.execute("DELETE FROM forecast_sku_snapshot WHERE SnapshotId = ?", (snap_id,))
    conn.execute("DELETE FROM forecast_snapshot_files WHERE SnapshotId = ?", (snap_id,))


def import_forecast_file(
    conn: sqlite3.Connection,
    path: Path,
    *,
    overwrite: bool = False,
    include_zero_days: bool = False,
) -> tuple[str, str]:
    source_sha256 = sha256(path)
    snap_id = snapshot_id(path, source_sha256)
    existing = conn.execute(
        "SELECT SnapshotId FROM forecast_snapshot_files WHERE SnapshotId = ?",
        (snap_id,),
    ).fetchone()
    if existing and not overwrite:
        return "skipped", snap_id
    if existing and overwrite:
        delete_snapshot(conn, snap_id)
    same_content = conn.execute(
        """
        SELECT SnapshotId
        FROM forecast_snapshot_files
        WHERE SourceSha256 = ?
        LIMIT 1
        """,
        (source_sha256,),
    ).fetchone()
    if same_content and not overwrite:
        return "skipped_hash", same_content[0]

    file_date = snapshot_file_date(path).isoformat()
    df = load_forecast_csv(path)
    df["ForecastTotal14Day"] = df[FD_COLUMNS].sum(axis=1)
    df["HasForecastDemand"] = (df["ForecastTotal14Day"] > 0).astype(int)

    putaway = df["PutawayIndicator"].value_counts(dropna=False).to_dict()
    metadata = pd.DataFrame(
        [
            {
                "SnapshotId": snap_id,
                "SourceFile": path.name,
                "SourcePath": str(path.resolve()),
                "SourceSha256": source_sha256,
                "FileDate": file_date,
                "ImportedAtUTC": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "RowsImported": int(len(df)),
                "DistinctSKUs": int(df["SKU"].nunique()),
                "ForecastStartDateMin": min(df["ForecastStartDate"]).isoformat(),
                "ForecastStartDateMax": max(df["ForecastStartDate"]).isoformat(),
                "TotalForecastUnits": float(df["ForecastTotal14Day"].sum()),
                "ForecastedSKUs": int(df["HasForecastDemand"].sum()),
                "ZeroForecastSKUs": int((df["ForecastTotal14Day"] == 0).sum()),
                "ActiveSKUCount": int(putaway.get(1, 0)),
                "ReserveSKUCount": int(putaway.get(0, 0)),
                "OffsiteSKUCount": int(putaway.get(2, 0)),
            }
        ]
    )
    metadata.to_sql("forecast_snapshot_files", conn, if_exists="append", index=False)

    sku_df = df[SKU_COLUMNS + ["ForecastTotal14Day", "HasForecastDemand"]].copy()
    sku_df.insert(0, "FileDate", file_date)
    sku_df.insert(0, "SnapshotId", snap_id)
    sku_df["ForecastStartDate"] = sku_df["ForecastStartDate"].map(lambda value: value.isoformat())
    sku_df.to_sql("forecast_sku_snapshot", conn, if_exists="append", index=False)

    day_frames = []
    base_cols = ["SnapshotId", "FileDate", "SKU", "ForecastStartDate"]
    day_source = sku_df[base_cols].copy()
    day_source["ForecastStartDate"] = pd.to_datetime(day_source["ForecastStartDate"]).dt.date
    for offset, fd_col in enumerate(FD_COLUMNS, start=1):
        day_df = day_source.copy()
        day_df["ForecastDayOffset"] = offset
        day_df["ForecastDate"] = day_df["ForecastStartDate"].map(
            lambda value, offset=offset: (value + timedelta(days=offset - 1)).isoformat()
        )
        day_df["ForecastQty"] = df[fd_col].astype(float).to_numpy()
        if not include_zero_days:
            day_df = day_df[day_df["ForecastQty"] != 0].copy()
        day_df["ForecastStartDate"] = day_df["ForecastStartDate"].map(lambda value: value.isoformat())
        day_frames.append(day_df)
    if day_frames:
        pd.concat(day_frames, ignore_index=True).to_sql(
            "forecast_sku_day",
            conn,
            if_exists="append",
            index=False,
            chunksize=100_000,
        )
    return "imported", snap_id


def import_forecasts(
    input_dir: Path,
    db_path: Path,
    overwrite: bool,
    include_zero_days: bool,
) -> None:
    paths = sorted(
        path
        for path in input_dir.glob("FwdDemandCSV_*.csv")
        if FORECAST_FILE_RE.match(path.name)
    )
    if not paths:
        raise FileNotFoundError(f"No FwdDemandCSV forecast files found in {input_dir}")

    with connect(db_path) as conn:
        initialize_schema(conn)
        imported = 0
        skipped = 0
        for path in paths:
            status, _snap_id = import_forecast_file(
                conn,
                path,
                overwrite=overwrite,
                include_zero_days=include_zero_days,
            )
            if status == "imported":
                imported += 1
            else:
                skipped += 1
            print(f"{status:12} {path.name}")
        conn.commit()

    print(f"\nDatabase: {db_path}")
    print(f"Imported: {imported}")
    print(f"Skipped:  {skipped}")


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD date, got {value}") from exc


def detect_archive_boundary(engine: sa.Engine) -> date | None:
    query = sa.text(
        """
        SELECT CAST(MAX(CREATEDDATETIME) AS DATE) AS MaxArchiveDate
        FROM DAX_Archive.arc.WHSWORKTABLE WITH (NOLOCK)
        WHERE DATAAREAID = 'ha'
          AND [PARTITION] = 5637144576
          AND WORKSTATUS = 4
        """
    )
    try:
        with engine.connect() as ax_conn:
            row = ax_conn.execute(query).fetchone()
    except Exception as exc:
        print(f"[!] Could not detect archive boundary; using PROD only. Detail: {exc}")
        return None
    if not row or row[0] is None:
        return None
    return pd.to_datetime(row[0]).date()


def actuals_query(source_table_schema: str, source_dim_schema: str, date_expr: str) -> sa.TextClause:
    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            CAST({date_expr} AS DATE) AS ActualDate,
            CASE
                WHEN ISNULL(idim.INVENTCOLORID, '') = '' THEN wkln.ITEMID
                WHEN ISNULL(idim.INVENTSIZEID, '') = '' THEN wkln.ITEMID + '-' + idim.INVENTCOLORID
                ELSE wkln.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID
            END AS SKU,
            SUM(CAST(wkln.QTYWORK AS FLOAT)) AS SoldUnits,
            COUNT(*) AS PickLines,
            COUNT(DISTINCT wktbl.ORDERNUM) AS DistinctOrders
        FROM {source_table_schema}.WHSWORKTABLE wktbl WITH (NOLOCK)
        INNER JOIN {source_table_schema}.WHSWORKLINE wkln WITH (NOLOCK)
            ON wktbl.WORKID = wkln.WORKID
           AND wktbl.DATAAREAID = wkln.DATAAREAID
           AND wktbl.[PARTITION] = wkln.[PARTITION]
        INNER JOIN {source_dim_schema}.INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = wkln.INVENTDIMID
           AND idim.DATAAREAID = wkln.DATAAREAID
           AND idim.[PARTITION] = wkln.[PARTITION]
        WHERE wktbl.DATAAREAID = 'ha'
          AND wktbl.[PARTITION] = 5637144576
          AND wktbl.WORKSTATUS = 4
          AND wkln.WORKSTATUS = 4
          AND wkln.WORKTYPE = 1
          AND wkln.WORKCLASSID = 'DirectPick'
          AND {date_expr} >= :start_dt
          AND {date_expr} < :end_dt
        GROUP BY
            CAST({date_expr} AS DATE),
            CASE
                WHEN ISNULL(idim.INVENTCOLORID, '') = '' THEN wkln.ITEMID
                WHEN ISNULL(idim.INVENTSIZEID, '') = '' THEN wkln.ITEMID + '-' + idim.INVENTCOLORID
                ELSE wkln.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID
            END
        """
    )


def fetch_actuals(
    *,
    server: str,
    database: str,
    start_date: date,
    end_date: date,
    date_basis: str,
) -> pd.DataFrame:
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")

    engine = get_ax_engine(server=server, database=database, verbose=True)
    archive_boundary = detect_archive_boundary(engine)
    end_exclusive = end_date + timedelta(days=1)
    date_expr = "wktbl.CREATEDDATETIME" if date_basis == "created" else "wkln.MODIFIEDDATETIME"

    pulls: list[tuple[str, str, str, date, date]] = []
    if archive_boundary is None:
        pulls.append(("DAX_PROD.dbo", "DAX_PROD.dbo", "PROD", start_date, end_exclusive))
    else:
        if start_date < archive_boundary:
            archive_end = min(end_exclusive, archive_boundary)
            if archive_end > start_date:
                pulls.append(("DAX_Archive.arc", "DAX_Archive.arc", "ARCHIVE", start_date, archive_end))
        if end_exclusive > archive_boundary:
            prod_start = max(start_date, archive_boundary)
            if end_exclusive > prod_start:
                pulls.append(("DAX_PROD.dbo", "DAX_PROD.dbo", "PROD", prod_start, end_exclusive))

    frames = []
    with engine.connect() as ax_conn:
        for table_schema, dim_schema, source_name, pull_start, pull_end in pulls:
            print(f"[*] Pulling {source_name}: {pull_start} to {pull_end - timedelta(days=1)}")
            query = actuals_query(table_schema, dim_schema, date_expr)
            frame = pd.read_sql_query(
                query,
                ax_conn,
                params={"start_dt": pull_start.isoformat(), "end_dt": pull_end.isoformat()},
            )
            print(f"    rows: {len(frame):,}")
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["ActualDate", "SKU", "SoldUnits", "PickLines", "DistinctOrders"])

    actuals = pd.concat(frames, ignore_index=True)
    if actuals.empty:
        return actuals
    actuals["ActualDate"] = pd.to_datetime(actuals["ActualDate"]).dt.date.map(lambda value: value.isoformat())
    actuals["SKU"] = normalize_text(actuals["SKU"])
    grouped = (
        actuals.groupby(["ActualDate", "SKU"], as_index=False)
        .agg(
            SoldUnits=("SoldUnits", "sum"),
            PickLines=("PickLines", "sum"),
            DistinctOrders=("DistinctOrders", "sum"),
        )
        .sort_values(["ActualDate", "SKU"])
    )
    return grouped


def import_actuals(
    *,
    db_path: Path,
    start_date_text: str,
    end_date_text: str,
    server: str,
    database: str,
    date_basis: str,
) -> None:
    start_date = parse_iso_date(start_date_text)
    end_date = parse_iso_date(end_date_text)
    actuals = fetch_actuals(
        server=server,
        database=database,
        start_date=start_date,
        end_date=end_date,
        date_basis=date_basis,
    )
    actuals.insert(1, "DateBasis", date_basis)
    actuals["ImportedAtUTC"] = datetime.now(UTC).replace(microsecond=0).isoformat()

    with connect(db_path) as conn:
        initialize_schema(conn)
        conn.execute(
            """
            DELETE FROM actual_sku_day
            WHERE DateBasis = ?
              AND ActualDate >= ?
              AND ActualDate <= ?
            """,
            (date_basis, start_date.isoformat(), end_date.isoformat()),
        )
        actuals.to_sql("actual_sku_day", conn, if_exists="append", index=False, chunksize=100_000)
        conn.commit()

    print(f"\nDatabase: {db_path}")
    print(f"Date basis: {date_basis}")
    print(f"Actual rows imported: {len(actuals):,}")
    print(f"Distinct SKUs: {actuals['SKU'].nunique():,}" if not actuals.empty else "Distinct SKUs: 0")
    print(f"Sold units: {actuals['SoldUnits'].sum():,.0f}" if not actuals.empty else "Sold units: 0")


def print_summary(db_path: Path) -> None:
    with connect(db_path) as conn:
        initialize_schema(conn)
        rows = conn.execute(
            """
            SELECT
                FileDate,
                RowsImported,
                ForecastedSKUs,
                ZeroForecastSKUs,
                TotalForecastUnits,
                ActiveSKUCount,
                ReserveSKUCount,
                OffsiteSKUCount
            FROM vw_forecast_snapshot_summary
            ORDER BY FileDate
            """
        ).fetchall()

    if not rows:
        print(f"No forecast snapshots imported yet: {db_path}")
        return

    print("FileDate    Rows   FcstSKUs  ZeroFcst  FcstUnits  Active  Reserve  Offsite")
    for row in rows:
        print(
            f"{row[0]}  {row[1]:6,d}  {row[2]:8,d}  {row[3]:8,d}  "
            f"{row[4]:9,.0f}  {row[5]:6,d}  {row[6]:7,d}  {row[7]:7,d}"
        )


def main() -> None:
    args = parse_args()
    if args.command == "import-forecasts":
        import_forecasts(args.input_dir, args.db, args.overwrite, args.include_zero_days)
    elif args.command == "import-actuals":
        import_actuals(
            db_path=args.db,
            start_date_text=args.start_date,
            end_date_text=args.end_date,
            server=args.server,
            database=args.database,
            date_basis=args.date_field,
        )
    elif args.command == "summary":
        print_summary(args.db)


if __name__ == "__main__":
    main()
