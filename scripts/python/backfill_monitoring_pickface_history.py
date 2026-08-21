"""Build portable pick-face history from KYDC monitoring report snapshots.

The monitoring forecast contract began on 2026-06-19, but immutable inventory
zone-compliance detail snapshots exist earlier. This consumer-side backfill
normalizes those reports into the same detail and SKU/day shapes used by the
forecast activation layer. Existing contract rows on dates not present in the
report folder are retained.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


DEFAULT_MONITORING_REPO = PROJECT_ROOT.parent / "ha-kydc-monitoring"
DEFAULT_REPORTS_DIR = DEFAULT_MONITORING_REPO / "Output" / "Monitoring" / "reports"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "inventory"
REPORT_PATTERN = re.compile(
    r"^inventory_zone_compliance_(\d{4}-\d{2}-\d{2})_detail\.parquet$"
)
DETAIL_FILENAME = "pickface_inventory_snapshot_detail.parquet"
SKU_DAY_FILENAME = "pickface_inventory_sku_day.parquet"
METADATA_FILENAME = "pickface_inventory_history_metadata.json"

DETAIL_COLUMNS = [
    "InventorySource",
    "SnapshotDate",
    "SKU",
    "Item",
    "Color",
    "Size_",
    "Location",
    "LocProfile",
    "CurrentZoneId",
    "CurrentCategoryCode",
    "CurrentCategoryName",
    "AisleId",
    "PhysicalQty",
    "ForecastSlotTier",
    "ForecastCategoryCode",
    "ForecastCategoryName",
    "HasForecast",
    "ExactZoneMatch",
    "CategoryMatch",
    "OutOfExactZone",
    "OutOfCategory",
    "NoForecast",
    "ForecastStartDate",
    "ForecastModifiedDateTime",
]
DETAIL_TEXT_COLUMNS = [
    "InventorySource",
    "SnapshotDate",
    "SKU",
    "Item",
    "Color",
    "Size_",
    "Location",
    "LocProfile",
    "CurrentZoneId",
    "CurrentCategoryCode",
    "CurrentCategoryName",
    "ForecastSlotTier",
    "ForecastCategoryCode",
    "ForecastCategoryName",
    "ForecastStartDate",
    "ForecastModifiedDateTime",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def _write_parquet_atomically(frame: pd.DataFrame, path: Path) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temp_path, index=False, compression="zstd")
    temp_path.replace(path)


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    for root in (PROJECT_ROOT.resolve(), PROJECT_ROOT.parent.resolve()):
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(resolved)


def _merge_fact(
    new_rows: pd.DataFrame,
    path: Path,
    keys: list[str],
) -> tuple[pd.DataFrame, int]:
    previous_rows = 0
    if path.exists():
        existing = pd.read_parquet(path)
        previous_rows = len(existing)
        if "Location" in keys:
            existing = _normalize_detail_types(existing)
            new_rows = _normalize_detail_types(new_rows)
        new_rows = pd.concat([existing, new_rows], ignore_index=True)
    merged = (
        new_rows.drop_duplicates(keys, keep="last")
        .sort_values(keys, kind="mergesort")
        .reset_index(drop=True)
    )
    return merged, previous_rows


def _normalize_detail_types(detail: pd.DataFrame) -> pd.DataFrame:
    output = detail.copy()
    for column in DETAIL_TEXT_COLUMNS:
        if column in output:
            output[column] = output[column].fillna("").astype(str).str.strip()
    if "AisleId" in output:
        output["AisleId"] = pd.to_numeric(output["AisleId"], errors="coerce").astype("Int64")
    if "PhysicalQty" in output:
        output["PhysicalQty"] = pd.to_numeric(
            output["PhysicalQty"], errors="coerce"
        ).fillna(0.0)
    return output


def _joined_text_fact(detail: pd.DataFrame, column: str, output_name: str) -> pd.DataFrame:
    keys = ["InventorySource", "SnapshotDate", "SKU"]
    text_rows = detail.loc[:, [*keys, column]].dropna(subset=[column]).copy()
    text_rows[column] = text_rows[column].astype(str)
    text_rows = text_rows.drop_duplicates([*keys, column]).sort_values([*keys, column])
    return (
        text_rows.groupby(keys, dropna=False, sort=False)[column]
        .agg(list)
        .str.join("|")
        .rename(output_name)
        .reset_index()
    )


def normalize_snapshot(path: Path, snapshot_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail = pd.read_parquet(path)
    detail.insert(0, "SnapshotDate", snapshot_date)
    detail.insert(0, "InventorySource", "monitoring_report_snapshot")
    detail = _normalize_detail_types(detail)
    detail = detail.loc[detail["SKU"].ne("")]
    detail = detail.loc[:, [column for column in DETAIL_COLUMNS if column in detail.columns]]

    keys = ["InventorySource", "SnapshotDate", "SKU"]
    sku_day = (
        detail.groupby(keys, dropna=False)
        .agg(
            PhysicalQty=("PhysicalQty", "sum"),
            OccupiedLocations=("Location", "nunique"),
            HasForecast=("HasForecast", "max"),
            AnyExactZoneMatch=("ExactZoneMatch", "max"),
            AnyCategoryMatch=("CategoryMatch", "max"),
            AnyOutOfExactZone=("OutOfExactZone", "max"),
            AnyOutOfCategory=("OutOfCategory", "max"),
        )
        .reset_index()
    )
    sku_day["HasPickableInventory"] = sku_day["PhysicalQty"].gt(0)
    sku_day = sku_day.merge(
        _joined_text_fact(detail, "LocProfile", "LocProfiles"), on=keys, how="left"
    ).merge(
        _joined_text_fact(detail, "CurrentZoneId", "CurrentZones"), on=keys, how="left"
    )
    sku_day = sku_day[
        [
            *keys,
            "PhysicalQty",
            "OccupiedLocations",
            "LocProfiles",
            "CurrentZones",
            "HasPickableInventory",
            "HasForecast",
            "AnyExactZoneMatch",
            "AnyCategoryMatch",
            "AnyOutOfExactZone",
            "AnyOutOfCategory",
        ]
    ]
    return detail, sku_day


def main() -> int:
    args = parse_args()
    reports_dir = args.reports_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshots: list[tuple[str, Path]] = []
    for path in reports_dir.glob("inventory_zone_compliance_*_detail.parquet"):
        match = REPORT_PATTERN.match(path.name)
        if match:
            snapshots.append((match.group(1), path))
    snapshots.sort()
    if not snapshots:
        raise FileNotFoundError(f"No immutable inventory detail reports found in {reports_dir}")

    details: list[pd.DataFrame] = []
    sku_days: list[pd.DataFrame] = []
    dated_files_created = 0
    dated_files_preserved = 0
    for snapshot_date, path in snapshots:
        detail, sku_day = normalize_snapshot(path, snapshot_date)
        details.append(detail)
        sku_days.append(sku_day)
        dated_path = output_dir / f"pickface_inventory_snapshot_detail_{snapshot_date}.parquet"
        if dated_path.exists():
            dated_files_preserved += 1
        else:
            _write_parquet_atomically(detail, dated_path)
            dated_files_created += 1

    detail_path = output_dir / DETAIL_FILENAME
    sku_day_path = output_dir / SKU_DAY_FILENAME
    detail_fact, previous_detail_rows = _merge_fact(
        pd.concat(details, ignore_index=True),
        detail_path,
        ["SnapshotDate", "SKU", "Location"],
    )
    sku_day_fact, previous_sku_day_rows = _merge_fact(
        pd.concat(sku_days, ignore_index=True),
        sku_day_path,
        ["SnapshotDate", "SKU"],
    )
    _write_parquet_atomically(detail_fact, detail_path)
    _write_parquet_atomically(sku_day_fact, sku_day_path)

    snapshot_dates = pd.to_datetime(sku_day_fact["SnapshotDate"], errors="coerce")
    metadata = {
        "updated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": (
            "KYDC monitoring immutable inventory_zone_compliance detail reports; "
            "live source DAX_PROD.dbo.INVENTSUM/INVENTDIM/WMSLOCATION"
        ),
        "reports_directory": _portable_path(reports_dir),
        "report_snapshots_read": len(snapshots),
        "report_start_date": snapshots[0][0],
        "report_end_date": snapshots[-1][0],
        "dated_files_created": dated_files_created,
        "dated_files_preserved": dated_files_preserved,
        "combined_start_date": snapshot_dates.min().date().isoformat(),
        "combined_end_date": snapshot_dates.max().date().isoformat(),
        "combined_snapshot_days": int(snapshot_dates.nunique()),
        "detail_rows": int(len(detail_fact)),
        "sku_day_rows": int(len(sku_day_fact)),
        "distinct_skus": int(sku_day_fact["SKU"].nunique()),
        "retention_merge": {
            "previous_detail_rows": int(previous_detail_rows),
            "previous_sku_day_rows": int(previous_sku_day_rows),
            "keys": {
                "detail": ["SnapshotDate", "SKU", "Location"],
                "sku_day": ["SnapshotDate", "SKU"],
            },
        },
        "outputs": {
            "detail_fact": _portable_path(detail_path),
            "sku_day": _portable_path(sku_day_path),
        },
        "notes": [
            "Snapshot dates are capture dates; missing calendar dates were not synthesized.",
            "This is pick-face/location inventory and remains distinct from broad AX warehouse availability history.",
        ],
    }
    (output_dir / METADATA_FILENAME).write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
