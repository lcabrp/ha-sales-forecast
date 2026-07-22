"""Extract canonical SKU category crosswalk from ha-ingestion-pipeline.

Reads sku_ledger.db from ha-ingestion-pipeline and produces the canonical
sku_category_crosswalk.parquet and companion JSON manifest in ha-sales-forecast.
Fulfills Open Item #1 in FORECAST_CURRENT_STATE.md and FORECAST_DATA_LANDSCAPE_2026-07-20.md.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import OUTPUT_DIR, PROJECT_ROOT  # noqa: E402

INGESTION_REPO_ROOT = PROJECT_ROOT.parent / "ha-ingestion-pipeline"
DEFAULT_SOURCE_DB = INGESTION_REPO_ROOT / "Output" / "Ingestion" / "sku_ledger.db"
DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "ForecastAccuracy" / "product_attributes"


def sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_category_crosswalk(
    source_db: Path = DEFAULT_SOURCE_DB,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, Path]:
    """Extract category crosswalk from sku_ledger.db and save parquet + manifest."""
    if not source_db.exists():
        raise FileNotFoundError(f"Ingestion SKU ledger database not found at {source_db}")

    db_hash = sha256(source_db)

    with sqlite3.connect(source_db) as conn:
        df_raw = pd.read_sql_query("SELECT * FROM sku_ledger", conn)

    # Standardize column naming and compute CategorySizeCode (e.g., GIRM, BOYM)
    df = df_raw.rename(
        columns={
            "sku": "SKU",
            "product_group": "ProductGroupCode",
            "size_group": "SizeGroupCode",
            "division": "Division",
            "department": "Department",
            "class": "Class",
            "first_seen": "FirstSeen",
            "last_seen": "LastSeen",
            "source_file": "SourceFile",
        }
    ).copy()

    # Clean text columns
    df["SKU"] = df["SKU"].fillna("").astype(str).str.strip().str.upper()
    df["ProductGroupCode"] = df["ProductGroupCode"].fillna("").astype(str).str.strip().str.upper()
    df["SizeGroupCode"] = df["SizeGroupCode"].fillna("").astype(str).str.strip().str.upper()
    df["Division"] = df["Division"].fillna("").astype(str).str.strip()
    df["Department"] = df["Department"].fillna("").astype(str).str.strip()
    df["Class"] = df["Class"].fillna("").astype(str).str.strip()

    # Derived CategorySizeCode (e.g., GIRM, BOYM)
    df["CategorySizeCode"] = df.apply(
        lambda r: f"{r['ProductGroupCode']}{r['SizeGroupCode']}"
        if r["ProductGroupCode"] and r["SizeGroupCode"]
        else r["ProductGroupCode"] or "UNKNOWN",
        axis=1,
    )

    # Deduplicate on SKU if needed, prioritizing the record with valid product_group and latest last_seen
    df = df.sort_values(
        by=["SKU", "ProductGroupCode", "LastSeen"],
        ascending=[True, True, False],
    ).drop_duplicates(subset=["SKU"], keep="first")

    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / "sku_category_crosswalk.parquet"
    manifest_path = output_dir / "sku_category_crosswalk_manifest.json"

    df.to_parquet(parquet_path, index=False, compression="zstd")
    parquet_hash = sha256(parquet_path)

    manifest = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "source_repo": str(INGESTION_REPO_ROOT.resolve()),
        "source_db": str(source_db.resolve()),
        "source_db_sha256": db_hash,
        "rows": len(df),
        "distinct_skus": df["SKU"].nunique(),
        "distinct_product_groups": df["ProductGroupCode"].nunique(),
        "distinct_category_size_codes": df["CategorySizeCode"].nunique(),
        "parquet_path": str(parquet_path.resolve()),
        "parquet_sha256": parquet_hash,
        "columns": list(df.columns),
    }

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Extracted {len(df):,} SKUs across {df['CategorySizeCode'].nunique():,} category-size cells.")
    print(f"Parquet saved to: {parquet_path}")
    print(f"Manifest saved to: {manifest_path}")

    return parquet_path, manifest_path


if __name__ == "__main__":
    extract_category_crosswalk()
