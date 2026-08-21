"""Extract compact corporate Forecast DB inventory history to portable Parquet.

The corporate SKU table is a positive-presence weekly fact. Missing rows are
not explicit zeroes, so this extractor preserves only observed rows and records
the complete snapshot-date spine separately. DIRECT and RETAIL remain separate.
Known billion-plus pseudo-SKU balances are quarantined instead of discarded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_db_auth import (  # noqa: E402
    DEFAULT_AUTH,
    DEFAULT_DATABASE,
    DEFAULT_DRIVER,
    DEFAULT_SERVER,
    DEFAULT_TENANT_ID,
    connect_forecast_db,
)
from forecast_schema import normalize_sku_series  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402


DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "Output" / "ForecastAccuracy" / "inventory" / "forecast_db"
)
SENTINEL_THRESHOLD = 1_000_000_000.0

SKU_SQL = """
SELECT
    CAST(CalendarDate AS date) AS SnapshotDate,
    UPPER(LTRIM(RTRIM(CHANNEL))) AS Channel,
    LTRIM(RTRIM(CONVERT(varchar(100), OFFERID))) AS OfferID,
    LTRIM(RTRIM(CONVERT(varchar(100), SKU))) AS SKU,
    CONVERT(float, Avail_OH) AS AvailableOH
FROM dbo.Channel_Offer_SKU_Inventory_History WITH (NOLOCK)
ORDER BY CalendarDate, CHANNEL, SKU;
"""

MACRO_SQL = """
SELECT
    CAST(AsOfDate AS date) AS SnapshotDate,
    UPPER(LTRIM(RTRIM(CHANNEL))) AS Channel,
    LTRIM(RTRIM(CONVERT(varchar(100), Division))) AS Division,
    LTRIM(RTRIM(CONVERT(varchar(100), DEPARTMENT))) AS Department,
    LTRIM(RTRIM(CONVERT(varchar(100), SEASONPARENTCODE))) AS SeasonParentCode,
    CONVERT(float, Avail_OH) AS AvailableOH,
    CONVERT(float, Avail_Cost_OH) AS AvailableCostOH
FROM dbo.Inventory_History WITH (NOLOCK)
ORDER BY AsOfDate, CHANNEL, Division, DEPARTMENT, SEASONPARENTCODE;
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--driver", default=DEFAULT_DRIVER)
    parser.add_argument("--auth", default=DEFAULT_AUTH)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    parser.add_argument("--user")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--chunk-rows", type=int, default=100_000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def read_query(connection, sql: str, chunk_rows: int) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pandas only supports SQLAlchemy connectable",
            category=UserWarning,
        )
        chunks = pd.read_sql_query(sql, connection, chunksize=chunk_rows)
        frames = list(chunks)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def write_parquet(frame: pd.DataFrame, path: Path) -> dict[str, Any]:
    frame.to_parquet(path, index=False, compression="zstd")
    return {
        "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "rows": int(len(frame)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def normalize_sku_history(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = raw.copy()
    frame["SnapshotDate"] = pd.to_datetime(
        frame["SnapshotDate"], errors="coerce"
    ).dt.normalize()
    frame["Channel"] = frame["Channel"].fillna("").astype(str).str.strip().str.upper()
    frame["OfferID"] = frame["OfferID"].fillna("").astype(str).str.strip()
    frame["SKU"] = normalize_sku_series(frame["SKU"])
    frame["AvailableOH"] = pd.to_numeric(frame["AvailableOH"], errors="coerce")
    frame = frame.dropna(subset=["SnapshotDate", "AvailableOH"])
    frame = frame.loc[frame["SKU"].ne("") & frame["Channel"].ne("")]

    sentinel_mask = frame["AvailableOH"].ge(SENTINEL_THRESHOLD)
    sentinels = frame.loc[sentinel_mask].copy()
    clean = frame.loc[~sentinel_mask].copy()
    clean["HasAvailableInventory"] = clean["AvailableOH"].gt(0)

    key = ["SnapshotDate", "Channel", "SKU"]
    duplicate_rows = int(clean.duplicated(key, keep=False).sum())
    if duplicate_rows:
        raise ValueError(
            f"Forecast DB inventory has {duplicate_rows:,} duplicate rows at {key}."
        )
    return (
        clean.sort_values(key, kind="mergesort").reset_index(drop=True),
        sentinels.sort_values(key, kind="mergesort").reset_index(drop=True),
    )


def normalize_macro_history(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    frame["SnapshotDate"] = pd.to_datetime(
        frame["SnapshotDate"], errors="coerce"
    ).dt.normalize()
    for column in ["Channel", "Division", "Department", "SeasonParentCode"]:
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    for column in ["AvailableOH", "AvailableCostOH"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["SnapshotDate"]).sort_values(
        ["SnapshotDate", "Channel", "Division", "Department", "SeasonParentCode"],
        kind="mergesort",
    ).reset_index(drop=True)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    with connect_forecast_db(
        server=args.server,
        database=args.database,
        driver=args.driver,
        auth=args.auth,
        tenant_id=args.tenant_id,
        user=args.user,
        timeout=args.timeout,
    ) as connection:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
        cursor.execute(
            "SELECT SUSER_SNAME(), DB_NAME(), "
            "CONVERT(varchar(33), SYSDATETIMEOFFSET(), 127);"
        )
        login_name, database_name, captured_at = cursor.fetchone()
        sku_raw = read_query(connection, SKU_SQL, args.chunk_rows)
        macro_raw = read_query(connection, MACRO_SQL, args.chunk_rows)

    sku_history, sentinel_rows = normalize_sku_history(sku_raw)
    macro_history = normalize_macro_history(macro_raw)
    direct_history = sku_history.loc[sku_history["Channel"].eq("DIRECT")].copy()

    snapshot_summary = (
        sku_history.groupby(["SnapshotDate", "Channel"], as_index=False)
        .agg(
            Rows=("SKU", "size"),
            DistinctSKUs=("SKU", "nunique"),
            AvailableOH=("AvailableOH", "sum"),
        )
        .sort_values(["SnapshotDate", "Channel"], kind="mergesort")
    )
    macro_summary = (
        macro_history.groupby(["SnapshotDate", "Channel"], as_index=False)
        .agg(
            Rows=("Channel", "size"),
            AvailableOH=("AvailableOH", "sum"),
            AvailableCostOH=("AvailableCostOH", "sum"),
        )
        .sort_values(["SnapshotDate", "Channel"], kind="mergesort")
    )

    outputs = {}
    outputs["channel_sku_weekly"] = write_parquet(
        sku_history, args.output_dir / "channel_sku_inventory_weekly.parquet"
    )
    outputs["direct_sku_weekly"] = write_parquet(
        direct_history, args.output_dir / "direct_sku_inventory_weekly.parquet"
    )
    outputs["sku_snapshot_summary"] = write_parquet(
        snapshot_summary, args.output_dir / "channel_sku_snapshot_summary.parquet"
    )
    outputs["macro_history"] = write_parquet(
        macro_history, args.output_dir / "inventory_macro_history.parquet"
    )
    outputs["macro_snapshot_summary"] = write_parquet(
        macro_summary, args.output_dir / "inventory_macro_snapshot_summary.parquet"
    )
    outputs["sentinel_audit"] = write_parquet(
        sentinel_rows, args.output_dir / "inventory_sentinel_rows.parquet"
    )

    snapshot_dates = sorted(sku_history["SnapshotDate"].dt.date.astype(str).unique())
    expected_sundays = pd.date_range(
        sku_history["SnapshotDate"].min(),
        sku_history["SnapshotDate"].max(),
        freq="W-SUN",
    ).date.astype(str).tolist()
    metadata = {
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": {
            "system": "Corporate Forecast Azure SQL",
            "server": args.server,
            "database": database_name,
            "login": login_name,
            "captured_at": captured_at,
            "authentication": args.auth,
            "isolation": "READ UNCOMMITTED / NOLOCK",
            "tables": [
                "dbo.Channel_Offer_SKU_Inventory_History",
                "dbo.Inventory_History",
            ],
        },
        "sku_history": {
            "raw_rows": int(len(sku_raw)),
            "clean_rows": int(len(sku_history)),
            "direct_rows": int(len(direct_history)),
            "distinct_skus": int(sku_history["SKU"].nunique()),
            "snapshot_dates": len(snapshot_dates),
            "first_snapshot": snapshot_dates[0],
            "last_snapshot": snapshot_dates[-1],
            "missing_expected_sundays": sorted(set(expected_sundays) - set(snapshot_dates)),
            "channels": {
                str(channel): int(rows)
                for channel, rows in sku_history.groupby("Channel").size().items()
            },
            "positive_presence_only": bool(sku_history["AvailableOH"].gt(0).all()),
            "natural_key": ["SnapshotDate", "Channel", "SKU"],
            "natural_key_duplicate_rows": int(
                sku_history.duplicated(["SnapshotDate", "Channel", "SKU"], keep=False).sum()
            ),
            "sentinel_threshold": SENTINEL_THRESHOLD,
            "sentinel_rows_quarantined": int(len(sentinel_rows)),
            "sentinel_skus": sorted(sentinel_rows["SKU"].unique().tolist()),
        },
        "macro_history": {
            "rows": int(len(macro_history)),
            "snapshot_dates": int(macro_history["SnapshotDate"].nunique()),
            "first_snapshot": str(macro_history["SnapshotDate"].min().date()),
            "last_snapshot": str(macro_history["SnapshotDate"].max().date()),
        },
        "outputs": outputs,
        "usage_notes": [
            "The SKU table records positive presence only; absence is not an explicit zero row.",
            "Preserve DIRECT and RETAIL separately.",
            "Use direct_sku_inventory_weekly.parquet for KYDC DirectPick activation tests.",
            "Join the latest snapshot strictly before the forecast origin and retain snapshot age.",
        ],
    }
    metadata_path = args.output_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, default=json_default) + "\n", encoding="utf-8"
    )

    print(json.dumps(metadata, indent=2, default=json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
