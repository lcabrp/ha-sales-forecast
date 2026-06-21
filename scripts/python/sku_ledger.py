"""
SKU Ledger — Persistent, deduplicated registry of SKU → Category mappings.

Maintains a SQLite database that accumulates every SKU ever seen across
all FwdDemand CSV files, storing their ProductGroupCode and SizeGroupCode
(without Velocity, which changes). This solves the "Cluster 999 orphan"
problem by ensuring that even SKUs that drop off the current forecast
retain their category assignment for ML clustering.

Usage:
    # Ingest all CSVs from a directory (e.g. Z:\\ForwardReplen\\Error)
    python sku_ledger.py ingest Z:\\ForwardReplen\\Error

    # Ingest a single CSV
    python sku_ledger.py ingest Output\\FwdDemandCSV_2026-03-31.csv

    # Ingest from the local Output/Ingestion folder (default)
    python sku_ledger.py ingest

    # Query stats
    python sku_ledger.py stats

    # Lookup a specific SKU
    python sku_ledger.py lookup 81563-25S-120

    # Export the ledger to CSV (for inspection or ML pipeline consumption)
    python sku_ledger.py export Output\\Ingestion\\sku_ledger_export.csv
"""
import argparse
import csv
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from output_paths import INGESTION_OUTPUT_DIR

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DB_PATH = INGESTION_OUTPUT_DIR / "sku_ledger.db"
DEFAULT_CSV_DIR = INGESTION_OUTPUT_DIR

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sku_ledger (
    sku             TEXT    PRIMARY KEY,
    product_group   TEXT    NOT NULL,
    size_group      TEXT    NOT NULL,
    division        TEXT,
    department      TEXT,
    class           TEXT,
    first_seen      TEXT    NOT NULL,
    last_seen       TEXT    NOT NULL,
    source_file     TEXT
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_pgc_sgc ON sku_ledger(product_group, size_group);
"""

# Upsert: insert if new, update last_seen + hierarchy if already exists
# We update hierarchy fields on conflict because the latest file has the
# most current Division/Department/Class assignments.
UPSERT_SQL = """
INSERT INTO sku_ledger (sku, product_group, size_group, division, department, class, first_seen, last_seen, source_file)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(sku) DO UPDATE SET
    last_seen    = excluded.last_seen,
    division     = COALESCE(excluded.division,    sku_ledger.division),
    department   = COALESCE(excluded.department,   sku_ledger.department),
    class        = COALESCE(excluded.class,        sku_ledger.class),
    product_group = COALESCE(excluded.product_group, sku_ledger.product_group),
    size_group    = COALESCE(excluded.size_group,    sku_ledger.size_group),
    source_file  = excluded.source_file;
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the ledger database and ensure schema exists."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")       # better concurrent perf
    conn.execute("PRAGMA synchronous=NORMAL;")      # safe + fast
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_INDEX_SQL)
    conn.commit()
    return conn


def extract_date_from_filename(filepath: Path) -> str:
    """
    Try to extract a date from the CSV filename for the 'seen' timestamp.
    Handles patterns like:
        FwdDemandCSV_2026-03-31.csv      -> 2026-03-31
        Fwd Demand CSV 33126.csv         -> 2026-03-31 (MDDYY)
        FwdDemandCSV_03242026.csv        -> 2026-03-24 (MMDDYYYY)
        Fwd Demand CSV 32426.csv         -> 2026-03-24 (MDDYY)
        FwdDemandCSV9.6.csv              -> (fallback to file mtime)
    Falls back to file modification time, then today's date.
    """
    stem = filepath.stem

    # ISO date: 2026-03-31
    m = re.search(r'(\d{4}-\d{2}-\d{2})', stem)
    if m:
        return m.group(1)

    # MMDDYYYY: 03242026
    m = re.search(r'(\d{8})$', stem.replace(' ', ''))
    if m:
        try:
            dt = datetime.strptime(m.group(1), '%m%d%Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

    # MDDYY or MMDDYY: 33126 = 3/31/26, 32426 = 3/24/26, 22626 = 2/26/26
    m = re.search(r'(\d{4,6})\s*$', stem.replace(' ', '').rstrip('.'))
    if m:
        digits = m.group(1)
        # Try M/DD/YY (5 digits)
        if len(digits) == 5:
            try:
                dt = datetime.strptime(digits, '%m%d%y')
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                pass
            # Try as MDD+YY
            try:
                dt = datetime(2000 + int(digits[3:5]), int(digits[0]), int(digits[1:3]))
                return dt.strftime('%Y-%m-%d')
            except (ValueError, IndexError):
                pass
        # Try MMDDYY (6 digits)
        if len(digits) == 6:
            try:
                dt = datetime.strptime(digits, '%m%d%y')
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                pass

    # Fallback: use file modification time
    try:
        mtime = filepath.stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
    except OSError:
        return datetime.now().strftime('%Y-%m-%d')


def ingest_csv(conn: sqlite3.Connection, csv_path: Path, file_date: str | None = None):
    """
    Read a FwdDemand CSV and upsert all SKUs into the ledger.
    Only reads the columns we need; skips the 14-day forecast columns.
    """
    if file_date is None:
        file_date = extract_date_from_filename(csv_path)

    source_name = csv_path.name
    rows_read = 0
    rows_new = 0

    # Count existing SKUs before this file
    (count_before,) = conn.execute("SELECT COUNT(*) FROM sku_ledger").fetchone()

    try:
        with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)

            # Verify required columns exist
            if not reader.fieldnames:
                print(f"    ⚠ {source_name}: empty or unreadable, skipping")
                return 0, 0

            available = set(reader.fieldnames)
            missing = {'SKU', 'ProductGroupCode', 'SizeGroupCode'} - available
            if missing:
                print(f"    ⚠ {source_name}: missing columns {missing}, skipping")
                return 0, 0

            batch = []
            for row in reader:
                sku = (row.get('SKU') or '').strip()
                pgc = (row.get('ProductGroupCode') or '').strip()
                sgc = (row.get('SizeGroupCode') or '').strip()

                # Skip rows with empty key fields
                if not sku or not pgc or not sgc:
                    continue

                division = (row.get('Division') or '').strip() or None
                department = (row.get('Department') or '').strip() or None
                cls = (row.get('Class') or '').strip() or None

                batch.append((sku, pgc, sgc, division, department, cls,
                              file_date, file_date, source_name))
                rows_read += 1

                # Batch insert every 5000 rows
                if len(batch) >= 5000:
                    conn.executemany(UPSERT_SQL, batch)
                    batch.clear()

            # Flush remaining
            if batch:
                conn.executemany(UPSERT_SQL, batch)
            conn.commit()

    except Exception as e:
        print(f"    ✗ {source_name}: error reading — {e}")
        return 0, 0

    (count_after,) = conn.execute("SELECT COUNT(*) FROM sku_ledger").fetchone()
    rows_new = count_after - count_before

    return rows_read, rows_new


def ingest_path(db_path: Path, target: Path):
    """Ingest a single CSV or all CSVs in a directory."""
    conn = init_db(db_path)

    if target.is_file():
        csv_files = [target]
    elif target.is_dir():
        # Find all CSVs matching FwdDemand / Fwd Demand patterns
        patterns = ['FwdDemand*.csv', 'Fwd Demand*.csv', 'FWDDEMAND*.csv',
                    'FWD Demand*.csv']
        csv_files = []
        for pat in patterns:
            csv_files.extend(target.glob(pat))
        # Also check subdirectories (Complete, Processing, Error)
        for subdir in target.iterdir():
            if subdir.is_dir():
                for pat in patterns:
                    csv_files.extend(subdir.glob(pat))

        # Deduplicate and sort by modification time
        csv_files = sorted(set(csv_files), key=lambda p: p.stat().st_mtime)
    else:
        print(f"✗ Path not found: {target}")
        conn.close()
        return

    if not csv_files:
        print(f"No FwdDemand CSV files found in {target}")
        conn.close()
        return

    print(f"[*] SKU Ledger: {db_path}")
    print(f"[*] Ingesting {len(csv_files)} CSV file(s)...\n")

    total_read = 0
    total_new = 0

    for i, f in enumerate(csv_files, 1):
        file_date = extract_date_from_filename(f)
        rows_read, rows_new = ingest_csv(conn, f, file_date)
        status = f"  +{rows_new} new" if rows_new > 0 else "  (no new)"
        print(f"  [{i:3d}/{len(csv_files)}] {f.name:<45s} date={file_date}  "
              f"rows={rows_read:>6,}{status}")
        total_read += rows_read
        total_new += rows_new

    (total_skus,) = conn.execute("SELECT COUNT(*) FROM sku_ledger").fetchone()
    print(f"\n{'='*70}")
    print(f"  Total rows processed: {total_read:,}")
    print(f"  New SKUs added:       {total_new:,}")
    print(f"  Total SKUs in ledger: {total_skus:,}")
    print(f"  Database size:        {db_path.stat().st_size / 1024:.0f} KB")

    conn.close()


def show_stats(db_path: Path):
    """Print summary statistics about the ledger."""
    if not db_path.exists():
        print(f"✗ Ledger not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))

    (total,) = conn.execute("SELECT COUNT(*) FROM sku_ledger").fetchone()
    (distinct_pgc,) = conn.execute("SELECT COUNT(DISTINCT product_group) FROM sku_ledger").fetchone()
    (distinct_sgc,) = conn.execute("SELECT COUNT(DISTINCT size_group) FROM sku_ledger").fetchone()
    (distinct_nodes,) = conn.execute(
        "SELECT COUNT(DISTINCT product_group || size_group) FROM sku_ledger"
    ).fetchone()
    (oldest,) = conn.execute("SELECT MIN(first_seen) FROM sku_ledger").fetchone()
    (newest,) = conn.execute("SELECT MAX(last_seen) FROM sku_ledger").fetchone()

    print(f"SKU Ledger: {db_path}")
    print(f"  Database size:  {db_path.stat().st_size / 1024:.0f} KB")
    print(f"  Total SKUs:     {total:,}")
    print(f"  Unique PGCs:    {distinct_pgc}")
    print(f"  Unique SGCs:    {distinct_sgc}")
    print(f"  Unique Nodes:   {distinct_nodes} (PGC+SGC combos)")
    print(f"  Date range:     {oldest} → {newest}")

    # --- Category breakdown by Product Group ---
    print("\n  Product Groups (all):")
    rows = conn.execute(
        "SELECT product_group, COUNT(*) as cnt, "
        "MIN(division) as sample_div "
        "FROM sku_ledger "
        "GROUP BY product_group ORDER BY cnt DESC"
    ).fetchall()
    print(f"    {'PGC':<6s} {'SKUs':>8s}  {'% Total':>7s}  Division")
    print(f"    {'─'*6} {'─'*8}  {'─'*7}  {'─'*25}")
    for pgc, cnt, div in rows:
        pct = cnt / total * 100
        print(f"    {pgc:<6s} {cnt:>8,}  {pct:>6.1f}%  {div or '—'}")

    # --- Size Group breakdown ---
    print("\n  Size Groups (all):")
    rows = conn.execute(
        "SELECT size_group, COUNT(*) as cnt "
        "FROM sku_ledger "
        "GROUP BY size_group ORDER BY cnt DESC"
    ).fetchall()
    print(f"    {'SGC':<6s} {'SKUs':>8s}  {'% Total':>7s}")
    print(f"    {'─'*6} {'─'*8}  {'─'*7}")
    for sgc, cnt in rows:
        pct = cnt / total * 100
        print(f"    {sgc:<6s} {cnt:>8,}  {pct:>6.1f}%")

    # --- Category matrix (PGC × SGC) ---
    print("\n  Category Matrix (PGC × SGC — top 15 nodes by SKU count):")
    rows = conn.execute(
        "SELECT product_group || size_group as node, "
        "product_group, size_group, COUNT(*) as cnt "
        "FROM sku_ledger "
        "GROUP BY product_group, size_group "
        "ORDER BY cnt DESC LIMIT 15"
    ).fetchall()
    print(f"    {'Node':<8s} {'PGC':<5s} {'SGC':<5s} {'SKUs':>8s}  {'% Total':>7s}")
    print(f"    {'─'*8} {'─'*5} {'─'*5} {'─'*8}  {'─'*7}")
    for node, pgc, sgc, cnt in rows:
        pct = cnt / total * 100
        print(f"    {node:<8s} {pgc:<5s} {sgc:<5s} {cnt:>8,}  {pct:>6.1f}%")

    # --- SKU Activity / churn ---
    print("\n  SKU Activity:")
    (one_time,) = conn.execute(
        "SELECT COUNT(*) FROM sku_ledger WHERE first_seen = last_seen"
    ).fetchone()
    (still_active,) = conn.execute(
        "SELECT COUNT(*) FROM sku_ledger WHERE last_seen >= date('now', '-60 days')"
    ).fetchone()
    (dormant,) = conn.execute(
        "SELECT COUNT(*) FROM sku_ledger WHERE last_seen < date('now', '-180 days')"
    ).fetchone()
    print(f"    Seen in a single file only:   {one_time:>8,} ({one_time/total*100:.1f}%)")
    print(f"    Active (last 60 days):        {still_active:>8,} ({still_active/total*100:.1f}%)")
    print(f"    Dormant (>180 days ago):      {dormant:>8,} ({dormant/total*100:.1f}%)")

    conn.close()


def lookup_sku(db_path: Path, sku: str):
    """Look up a specific SKU in the ledger."""
    if not db_path.exists():
        print(f"✗ Ledger not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT * FROM sku_ledger WHERE sku = ?", (sku,)
    ).fetchone()

    if row:
        cols = [d[0] for d in conn.execute("SELECT * FROM sku_ledger LIMIT 0").description]
        for col, val in zip(cols, row):
            print(f"  {col:<16s} {val}")
    else:
        # Try partial match
        rows = conn.execute(
            "SELECT sku, product_group, size_group FROM sku_ledger WHERE sku LIKE ? LIMIT 10",
            (f"%{sku}%",)
        ).fetchall()
        if rows:
            print(f"  No exact match for '{sku}'. Partial matches:")
            for r in rows:
                print(f"    {r[0]:<25s} {r[1]}{r[2]}")
        else:
            print(f"  SKU '{sku}' not found in ledger.")

    conn.close()


def export_ledger(db_path: Path, export_path: Path):
    """Export the full ledger to CSV."""
    if not db_path.exists():
        print(f"✗ Ledger not found: {db_path}")
        return

    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT * FROM sku_ledger ORDER BY product_group, size_group, sku")
    cols = [d[0] for d in cursor.description]

    with open(export_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        count = 0
        for row in cursor:
            writer.writerow(row)
            count += 1

    print(f"Exported {count:,} SKUs to {export_path}")
    print(f"  File size: {export_path.stat().st_size / 1024:.0f} KB")
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="SKU Ledger — persistent, deduplicated SKU → Category registry"
    )
    parser.add_argument(
        '--db', type=Path, default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH})"
    )

    subparsers = parser.add_subparsers(dest='command')

    # ingest
    p_ingest = subparsers.add_parser('ingest', help='Ingest FwdDemand CSVs into the ledger')
    p_ingest.add_argument(
        'path', nargs='?', type=Path, default=DEFAULT_CSV_DIR,
        help='Path to a CSV file or directory containing CSVs'
    )

    # stats
    subparsers.add_parser('stats', help='Show ledger statistics')

    # lookup
    p_lookup = subparsers.add_parser('lookup', help='Look up a specific SKU')
    p_lookup.add_argument('sku', help='SKU to look up')

    # export
    p_export = subparsers.add_parser('export', help='Export ledger to CSV')
    p_export.add_argument(
        'output', type=Path,
        help='Path for the exported CSV'
    )

    args = parser.parse_args()

    if args.command == 'ingest':
        ingest_path(args.db, args.path)
    elif args.command == 'stats':
        show_stats(args.db)
    elif args.command == 'lookup':
        lookup_sku(args.db, args.sku)
    elif args.command == 'export':
        export_ledger(args.db, args.output)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
