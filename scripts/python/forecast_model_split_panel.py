"""Split the model SKU/day panel into GitHub-sized monthly Parquet parts.

Large forecasting dataset panels can exceed GitHub's single-file limits. Splitting them
by Year-Month allows tracking them in version control. This script supports both splitting
a single large panel into monthly parts and recombining monthly parts into a single panel.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DEFAULT_PANEL = Path("Output/ForecastAccuracy/model/model_sku_day_panel.parquet")
DEFAULT_OUTPUT_DIR = Path("Output/ForecastAccuracy/model/model_sku_day_panel_parts")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for splitting or combining the model panel dataset.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Split model panel into monthly Parquet files.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--compression", default="zstd")
    parser.add_argument(
        "--combine",
        action="store_true",
        help="Recreate --panel from monthly parts in --output-dir instead of splitting.",
    )
    return parser.parse_args()


def split_panel(panel_path: Path, output_dir: Path, compression: str) -> None:
    """Split a single large Parquet panel file into monthly Parquet partitions.

    Also generates a JSON manifest listing all partitions and their summary metrics.

    Args:
        panel_path: Path to the input large Parquet file.
        output_dir: Target directory to save the monthly partitions.
        compression: The compression codec to use (e.g. 'zstd').

    Raises:
        FileNotFoundError: If the input panel file does not exist.
    """
    if not panel_path.exists():
        raise FileNotFoundError(f"Model panel not found: {panel_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(panel_path)
    panel["Date"] = pd.to_datetime(panel["Date"], errors="coerce").dt.normalize()
    panel["YearMonth"] = panel["Date"].dt.to_period("M").astype(str)

    parts: list[dict[str, object]] = []
    # Group by year and month to produce monthly shards
    for year_month, group in panel.groupby("YearMonth", sort=True):
        output_path = output_dir / f"model_sku_day_panel_{year_month}.parquet"
        group = group.drop(columns=["YearMonth"])
        group.to_parquet(output_path, index=False, compression=compression)
        parts.append(
            {
                "year_month": year_month,
                "path": str(output_path),
                "rows": int(len(group)),
                "distinct_skus": int(group["SKU"].nunique()),
                "sold_units": float(group["SoldUnits"].sum()),
                "file_size_bytes": int(output_path.stat().st_size),
            }
        )

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_panel": str(panel_path),
        "output_dir": str(output_dir),
        "compression": compression,
        "row_count": int(len(panel)),
        "date_range": [
            str(panel["Date"].min().date()),
            str(panel["Date"].max().date()),
        ],
        "parts": parts,
    }
    manifest_path = output_dir / "model_sku_day_panel_parts_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)

    max_size = max(part["file_size_bytes"] for part in parts) if parts else 0
    print(f"Wrote {len(parts)} monthly panel parts to {output_dir}")
    print(f"Max part size: {max_size / 1024 / 1024:.2f} MB")
    print(f"Manifest: {manifest_path}")


def combine_panel(parts_dir: Path, panel_path: Path, compression: str) -> None:
    """Recombine monthly Parquet partitions into a single large Parquet panel file.

    Sorts the output rows consistently by SKU and Date.

    Args:
        parts_dir: Directory containing the monthly Parquet partitions.
        panel_path: Target path to write the combined Parquet file.
        compression: The compression codec to use (e.g. 'zstd').

    Raises:
        FileNotFoundError: If no monthly partitions are found in the directory.
    """
    part_paths = sorted(parts_dir.glob("model_sku_day_panel_????-??.parquet"))
    if not part_paths:
        raise FileNotFoundError(f"No monthly panel parts found in {parts_dir}")

    frames = [pd.read_parquet(path) for path in part_paths]
    panel = pd.concat(frames, ignore_index=True).sort_values(["SKU", "Date"], kind="mergesort")
    panel_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(panel_path, index=False, compression=compression)
    print(f"Combined {len(part_paths)} monthly parts into {panel_path}")
    print(f"Rows: {len(panel):,}; SKUs: {panel['SKU'].nunique():,}; size: {panel_path.stat().st_size / 1024 / 1024:.2f} MB")


def main() -> None:
    """Execute the command line entry point for splitting or combining the model panel."""
    args = parse_args()
    if args.combine:
        combine_panel(args.output_dir, args.panel, args.compression)
    else:
        split_panel(args.panel, args.output_dir, args.compression)


if __name__ == "__main__":
    main()
