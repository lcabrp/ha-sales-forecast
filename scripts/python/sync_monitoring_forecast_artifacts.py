"""Mirror compact monitoring forecast artifacts into this repo.

The monitoring repo owns the daily AX/live capture. This script copies the
forecast-facing Parquet/CSV/JSON contract artifacts into ha-sales-forecast so
modeling work has a GitHub-trackable local copy with provenance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_SOURCE_REPO = PROJECT_ROOT.parent / "ha-kydc-monitoring"
DEFAULT_MANIFEST_PATH = FORECAST_ACCURACY_ROOT / "monitoring_artifact_mirror_manifest.json"
MAX_TRACKED_FILE_BYTES = 90 * 1024 * 1024

CONTRACT_FILES = {
    "inventory": {
        "metadata": "pickface_inventory_metadata.json",
        "rolling": (
            "pickface_inventory_snapshot_detail.parquet",
            "pickface_inventory_sku_day.parquet",
        ),
        "dated_pattern": re.compile(r"^pickface_inventory_snapshot_detail_\d{4}-\d{2}-\d{2}\.parquet$"),
    },
    "inbound": {
        "metadata": "ax_open_inbound_metadata.json",
        "rolling": (
            "ax_open_inbound_detail.parquet",
            "ax_open_inbound_sku_day.parquet",
            "ax_open_inbound_snapshot_summary.csv",
        ),
        "dated_pattern": re.compile(r"^ax_open_inbound_detail_\d{4}-\d{2}-\d{2}\.parquet$"),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy model-ready monitoring inventory/inbound artifacts into ha-sales-forecast."
    )
    parser.add_argument("--source-repo", type=Path, default=DEFAULT_SOURCE_REPO)
    parser.add_argument("--output-root", type=Path, default=FORECAST_ACCURACY_ROOT)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--families",
        nargs="+",
        choices=sorted(CONTRACT_FILES),
        default=sorted(CONTRACT_FILES),
        help="Artifact families to mirror.",
    )
    parser.add_argument(
        "--max-file-mb",
        type=float,
        default=90.0,
        help="Refuse to copy files larger than this size.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_count(path: Path) -> int | None:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return int(len(pd.read_parquet(path)))
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            return sum(1 for _row in reader)
    if suffix == ".json":
        return None
    return None


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def contract_paths(source_family_dir: Path, family: str) -> list[Path]:
    contract = CONTRACT_FILES[family]
    paths: list[Path] = []

    metadata = source_family_dir / str(contract["metadata"])
    if metadata.exists():
        paths.append(metadata)

    for name in contract["rolling"]:
        path = source_family_dir / str(name)
        if path.exists():
            paths.append(path)

    pattern: re.Pattern[str] = contract["dated_pattern"]  # type: ignore[assignment]
    dated = sorted(path for path in source_family_dir.iterdir() if path.is_file() and pattern.match(path.name))
    paths.extend(dated)

    return paths


def relative_to_repo(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def copy_one(
    source_path: Path,
    source_repo: Path,
    output_root: Path,
    source_root: Path,
    max_file_bytes: int,
    dry_run: bool,
) -> dict[str, Any]:
    size = source_path.stat().st_size
    if size > max_file_bytes:
        raise ValueError(
            f"Refusing to copy {source_path}: {size / 1024 / 1024:.1f} MB exceeds "
            f"{max_file_bytes / 1024 / 1024:.1f} MB limit."
        )

    relative = source_path.resolve().relative_to(source_root.resolve())
    destination_path = output_root / relative
    source_hash = sha256_file(source_path)
    previous_hash = sha256_file(destination_path) if destination_path.exists() else ""
    action = "unchanged" if previous_hash == source_hash else ("update" if destination_path.exists() else "create")

    if not dry_run and action != "unchanged":
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)

    destination_for_counts = destination_path if destination_path.exists() and not dry_run else source_path

    return {
        "family": relative.parts[0],
        "action": action,
        "source_path": relative_to_repo(source_path, source_repo),
        "destination_path": relative_to_repo(destination_path, PROJECT_ROOT),
        "bytes": size,
        "sha256": source_hash,
        "row_count": row_count(destination_for_counts),
    }


def main() -> None:
    args = parse_args()
    source_repo = args.source_repo.resolve()
    source_root = source_repo / "Output" / "ForecastAccuracy"
    output_root = args.output_root
    max_file_bytes = int(args.max_file_mb * 1024 * 1024)

    if not source_root.exists():
        raise FileNotFoundError(f"Monitoring forecast artifact root not found: {source_root}")

    copied: list[dict[str, Any]] = []
    source_metadata: dict[str, Any] = {}

    for family in args.families:
        source_family_dir = source_root / family
        if not source_family_dir.exists():
            raise FileNotFoundError(f"Monitoring {family} artifact folder not found: {source_family_dir}")

        metadata_path = source_family_dir / str(CONTRACT_FILES[family]["metadata"])
        source_metadata[family] = load_json(metadata_path)

        for source_path in contract_paths(source_family_dir, family):
            copied.append(
                copy_one(
                    source_path=source_path,
                    source_repo=source_repo,
                    output_root=output_root,
                    source_root=source_root,
                    max_file_bytes=max_file_bytes,
                    dry_run=args.dry_run,
                )
            )

    manifest = {
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "mode": "dry_run" if args.dry_run else "mirror",
        "producer_repo": str(source_repo),
        "producer_contract_root": relative_to_repo(source_root, source_repo),
        "consumer_repo": str(PROJECT_ROOT.resolve()),
        "consumer_output_root": relative_to_repo(output_root, PROJECT_ROOT),
        "max_file_mb": args.max_file_mb,
        "families": args.families,
        "source_metadata": source_metadata,
        "files": copied,
        "summary": {
            "files_seen": len(copied),
            "created": sum(1 for item in copied if item["action"] == "create"),
            "updated": sum(1 for item in copied if item["action"] == "update"),
            "unchanged": sum(1 for item in copied if item["action"] == "unchanged"),
            "bytes_seen": sum(int(item["bytes"]) for item in copied),
        },
        "notes": [
            "ha-kydc-monitoring remains the daily producer for these contract artifacts.",
            "ha-sales-forecast keeps this mirrored copy for forecast modeling and GitHub/cloud LLM review.",
            "Dated Parquet files are immutable as-of snapshots; rolling facts should not be used for older holdouts without checking SnapshotDate.",
        ],
    }

    if not args.dry_run:
        args.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps(manifest["summary"], indent=2))
    if args.dry_run:
        print("Dry run only; no files copied and manifest not written.")
    else:
        print(f"Manifest written: {args.manifest_path}")


if __name__ == "__main__":
    main()
