"""Build portable shadow-only CaseQty and SKU-location inventory facts.

This script imports read-only workbook parsing helpers from the production
ingestion module, but it never invokes the ingestion pipeline or writes AX
files. Outputs are compact Parquet facts intended for Git portability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from ingestion_pipeline import (  # noqa: E402
    compute_case_qty,
    read_14day_forecast,
    read_load_data,
    read_on_hand,
    read_product_attributes,
    read_weekly_forecast,
)


SOURCE_DIR = PROJECT_ROOT / "Source"
REPORT_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "reports"
OUTPUT_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
CASE_QTY_NAME = "planning_case_qty_history.parquet"
CASE_QTY_SUMMARY_NAME = "planning_case_qty_history_summary.csv"
INVENTORY_NAME = "sku_location_inventory_snapshots.parquet"
INVENTORY_SUMMARY_NAME = "sku_location_inventory_snapshots_summary.csv"
METADATA_NAME = "portable_enrichment_metadata.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "planning_case_qty": output_dir / CASE_QTY_NAME,
        "planning_case_qty_summary": output_dir / CASE_QTY_SUMMARY_NAME,
        "inventory_snapshots": output_dir / INVENTORY_NAME,
        "inventory_snapshots_summary": output_dir / INVENTORY_SUMMARY_NAME,
        "metadata": output_dir / METADATA_NAME,
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Portable enrichment artifacts already exist. Pass --overwrite to replace them: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}
    for path in temporary.values():
        if path.exists():
            path.unlink()
    return outputs, temporary


def workbook_snapshot_date(path: Path) -> str:
    match = re.search(r"_(\d{8})$", path.stem)
    if not match:
        raise ValueError(f"Cannot parse YYYYMMDD snapshot date from workbook: {path.name}")
    return pd.Timestamp(match.group(1)).date().isoformat()


def build_case_qty_history(source_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[Path]]:
    frames: list[pd.DataFrame] = []
    workbooks = sorted(source_dir.glob("Product Info for BRG_*.xlsx"))
    if not workbooks:
        raise FileNotFoundError(f"No Product Info workbooks found under {source_dir}")

    for workbook in workbooks:
        snapshot_date = workbook_snapshot_date(workbook)
        weekly, _week_dates, _week_columns = read_weekly_forecast(workbook)
        forecast_14d, _forecast_start = read_14day_forecast(workbook)
        hierarchy, _status = read_product_attributes(workbook)
        load_data = read_load_data(workbook)
        on_hand, on_hand_skus = read_on_hand(workbook)
        sku_universe = (
            pd.concat([weekly[["SKU"]], forecast_14d[["SKU"]], on_hand_skus])
            .drop_duplicates("SKU")
            .copy()
        )
        case_qty = compute_case_qty(load_data, on_hand, hierarchy, sku_universe)
        case_qty.insert(0, "WorkbookSnapshotDate", snapshot_date)
        case_qty.insert(1, "SourceWorkbook", workbook.name)
        frames.append(case_qty)

    history = pd.concat(frames, ignore_index=True)
    if history.duplicated(["WorkbookSnapshotDate", "SKU"]).any():
        raise ValueError("Planning CaseQty history contains duplicate workbook-date/SKU keys.")
    summary = (
        history.groupby(["WorkbookSnapshotDate", "CaseQtySource"], as_index=False)
        .agg(SKUs=("SKU", "nunique"), MedianCaseQty=("CaseQty", "median"))
    )
    return history, summary, workbooks


def inventory_snapshot_date(path: Path) -> str:
    match = re.search(r"inventory_zone_compliance_(\d{4}-\d{2}-\d{2})_detail$", path.stem)
    if not match:
        raise ValueError(f"Cannot parse snapshot date from inventory detail: {path.name}")
    return match.group(1)


def normalized_bool(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def build_inventory_history(report_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[Path]]:
    """Load inventory snapshots, preferring Parquet over CSV for efficiency.
    
    Strategy:
    1. Glob for parquet files first (newer, compressed format).
    2. For any missing parquet files, fall back to CSV equivalents.
    3. Track which files were loaded (for provenance metadata).
    
    This allows a gradual migration where the monitoring producer can write
    both CSV and Parquet while consumers transition to Parquet-first logic.
    """
    frames: list[pd.DataFrame] = []
    details: list[Path] = []

    # Find all snapshot dates from both CSV and Parquet
    csv_files = set(report_dir.glob("inventory_zone_compliance_*_detail.csv"))
    pq_files = set(report_dir.glob("inventory_zone_compliance_*_detail.parquet"))

    if not csv_files and not pq_files:
        raise FileNotFoundError(f"No inventory-zone detail snapshots found under {report_dir}")

    # Collect unique snapshot dates and prefer Parquet for each date
    snapshot_dates = set()
    for path in csv_files | pq_files:
        date_str = inventory_snapshot_date(path)
        snapshot_dates.add(date_str)

    columns = [
        "LocProfile",
        "CurrentZoneId",
        "CurrentCategoryCode",
        "Location",
        "AisleId",
        "SKU",
        "PhysicalQty",
        "ForecastSlotTier",
        "ForecastCategoryCode",
        "HasForecast",
        "ExactZoneMatch",
        "CategoryMatch",
        "OutOfExactZone",
        "OutOfCategory",
        "NoForecast",
    ]

    for date_str in sorted(snapshot_dates):
        pq_path = report_dir / f"inventory_zone_compliance_{date_str}_detail.parquet"
        csv_path = report_dir / f"inventory_zone_compliance_{date_str}_detail.csv"

        # Prefer Parquet if it exists
        if pq_path.exists():
            frame = pd.read_parquet(pq_path, columns=columns)
            details.append(pq_path)
        elif csv_path.exists():
            frame = pd.read_csv(csv_path, usecols=columns)
            details.append(csv_path)
        else:
            continue

        frame.insert(0, "SnapshotDate", date_str)
        text_columns = [column for column in columns if column not in {"PhysicalQty"}]
        for column in text_columns:
            frame[column] = frame[column].fillna("").astype(str).str.strip()
        frame["SKU"] = frame["SKU"].str.upper()
        frame["PhysicalQty"] = pd.to_numeric(frame["PhysicalQty"], errors="coerce").fillna(0)
        for column in (
            "HasForecast",
            "ExactZoneMatch",
            "CategoryMatch",
            "OutOfExactZone",
            "OutOfCategory",
            "NoForecast",
        ):
            frame[column] = normalized_bool(frame[column])
        frames.append(frame)

    history = pd.concat(frames, ignore_index=True)
    key = ["SnapshotDate", "Location", "SKU"]
    if history.duplicated(key).any():
        raise ValueError("SKU-location inventory history contains duplicate snapshot/location/SKU keys.")
    summary = (
        history.groupby("SnapshotDate", as_index=False)
        .agg(
            InventoryRows=("SKU", "size"),
            OccupiedLocations=("Location", "nunique"),
            DistinctSKUs=("SKU", "nunique"),
            PhysicalQty=("PhysicalQty", "sum"),
            OutOfExactZoneLocations=(
                "Location",
                lambda values: values[history.loc[values.index, "OutOfExactZone"]].nunique(),
            ),
        )
    )
    return history, summary, details


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def write_outputs(
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    case_qty: pd.DataFrame,
    case_qty_summary: pd.DataFrame,
    inventory: pd.DataFrame,
    inventory_summary: pd.DataFrame,
    workbooks: list[Path],
    inventory_details: list[Path],
) -> None:
    payload: dict[str, object] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "inputs": {
            "source_workbooks": [
                {"path": relative(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in workbooks
            ],
            "inventory_detail_csvs": [
                {"path": relative(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in inventory_details
            ],
        },
        "outputs": {},
        "rows": {
            "planning_case_qty": len(case_qty),
            "sku_location_inventory_snapshots": len(inventory),
        },
        "notes": [
            "Planning CaseQty is calculated from each workbook using ingestion helper semantics.",
            "Planning CaseQty is not the authoritative quantity physically moved into the pick face.",
            "Inventory snapshots are historical live-state observations, not work events.",
        ],
    }
    try:
        case_qty.to_parquet(temporary["planning_case_qty"], index=False, compression="zstd")
        case_qty_summary.to_csv(temporary["planning_case_qty_summary"], index=False)
        inventory.to_parquet(temporary["inventory_snapshots"], index=False, compression="zstd")
        inventory_summary.to_csv(temporary["inventory_snapshots_summary"], index=False)
        for name, path in outputs.items():
            if name == "metadata":
                continue
            temp = temporary[name]
            payload["outputs"][name] = {
                "path": relative(path),
                "bytes": temp.stat().st_size,
                "sha256": sha256(temp),
            }
        temporary["metadata"].write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        for name, path in outputs.items():
            temporary[name].replace(path)
    except Exception:
        for path in temporary.values():
            if path.exists():
                path.unlink()
        raise


def main() -> None:
    args = parse_args()
    outputs, temporary = prepare_outputs(args.output_dir, args.overwrite)
    case_qty, case_qty_summary, workbooks = build_case_qty_history(args.source_dir)
    inventory, inventory_summary, inventory_details = build_inventory_history(args.report_dir)
    write_outputs(
        outputs,
        temporary,
        case_qty,
        case_qty_summary,
        inventory,
        inventory_summary,
        workbooks,
        inventory_details,
    )
    print(f"Planning CaseQty rows:      {len(case_qty):,}")
    print(f"Inventory SKU-location rows: {len(inventory):,}")
    print(f"Portable enrichment outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
