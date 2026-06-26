"""Extract Product Info workbook inbound snapshots for model features.

AX live open-PO tables are the most operationally authoritative source for
today, but saved Product Info workbooks are useful for historical backtests
because each workbook is an as-of planning snapshot. This extractor keeps that
snapshot date explicit so downstream model features can avoid future leakage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


DEFAULT_SOURCE_DIR = PROJECT_ROOT / "Source"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "inbound"
FILENAME_DATE_PATTERN = re.compile(r"(\d{4})[-_](\d{2})[-_](\d{2})")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for Product Info inbound snapshot extractor.

    Returns:
        argparse.Namespace: Checked command line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Extract Product Info workbook inbound snapshots."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pattern", default="Product Info for BRG*.xlsx")
    parser.add_argument("--max-files", type=int, default=0)
    return parser.parse_args()


def snapshot_date_from_name(path: Path) -> pd.Timestamp | None:
    """Parse a date timestamp from workbook name using pattern matching (e.g. YYYY-MM-DD).

    Args:
        path: Path to the Excel workbook.

    Returns:
        pd.Timestamp or None: Extracted date timestamp, or None if not matching.
    """
    match = FILENAME_DATE_PATTERN.search(path.name)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return pd.Timestamp(year=year, month=month, day=day)


def read_workbook_inbound(path: Path) -> pd.DataFrame:
    """Extract inbound details from a single Product Info workbook.

    Expects tab name 'Product Inbound' with columns: SKU, InDC(calc), PurchId,
    and PO Receive Remainder Units.

    Args:
        path: Path to the Excel workbook file.

    Returns:
        pd.DataFrame: Cleaned and normalized inbound records DataFrame.

    Raises:
        ValueError: If snapshot date is missing or required columns are absent.
    """
    snapshot_date = snapshot_date_from_name(path)
    if snapshot_date is None:
        raise ValueError(f"Could not infer snapshot date from {path.name}")

    df = pd.read_excel(path, sheet_name="Product Inbound", header=1, engine="calamine")
    expected = ["SKU", "InDC(calc)", "PurchId", "PO Receive Remainder Units"]
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(f"{path.name} missing Product Inbound columns: {missing}")

    output = df.loc[:, expected].copy()
    output = output.rename(
        columns={
            "InDC(calc)": "ExpectedInDCDate",
            "PurchId": "PurchID",
            "PO Receive Remainder Units": "InboundRemainderUnits",
        }
    )
    output["SnapshotDate"] = snapshot_date
    output["SourceWorkbook"] = path.name
    output["SKU"] = output["SKU"].fillna("").astype(str).str.strip()
    output["PurchID"] = output["PurchID"].fillna("").astype(str).str.strip()
    output["ExpectedInDCDate"] = pd.to_datetime(output["ExpectedInDCDate"], errors="coerce").dt.normalize()
    output["InboundRemainderUnits"] = pd.to_numeric(
        output["InboundRemainderUnits"],
        errors="coerce",
    ).fillna(0.0)
    output = output.loc[
        output["SKU"].ne("")
        & output["ExpectedInDCDate"].notna()
        & output["InboundRemainderUnits"].gt(0)
    ].copy()
    
    return output.loc[
        :,
        [
            "SnapshotDate",
            "SKU",
            "ExpectedInDCDate",
            "PurchID",
            "InboundRemainderUnits",
            "SourceWorkbook",
        ],
    ]


def write_outputs(df: pd.DataFrame, args: argparse.Namespace) -> None:
    """Save processed snapshots, daily summaries, and manifest metadata files.

    Args:
        df: Processed DataFrame.
        args: Command line parameters.
    """
    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = args.output_dir / "product_info_inbound_snapshots.parquet"
    summary_path = args.output_dir / "product_info_inbound_snapshot_summary.csv"
    metadata_path = args.output_dir / "product_info_inbound_metadata.json"

    df.to_parquet(detail_path, index=False, compression="zstd")
    summary = (
        df.groupby(["SnapshotDate", "SourceWorkbook"], as_index=False)
        .agg(
            Rows=("SKU", "size"),
            DistinctSKUs=("SKU", "nunique"),
            DistinctPOs=("PurchID", "nunique"),
            InboundRemainderUnits=("InboundRemainderUnits", "sum"),
            MinExpectedInDCDate=("ExpectedInDCDate", "min"),
            MaxExpectedInDCDate=("ExpectedInDCDate", "max"),
        )
        .sort_values("SnapshotDate")
    )
    summary.to_csv(summary_path, index=False)
    
    metadata = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_dir": str(args.source_dir),
        "rows": int(len(df)),
        "snapshot_count": int(df["SnapshotDate"].nunique()) if not df.empty else 0,
        "distinct_skus": int(df["SKU"].nunique()) if not df.empty else 0,
        "snapshot_date_range": [
            str(df["SnapshotDate"].min().date()) if not df.empty else "",
            str(df["SnapshotDate"].max().date()) if not df.empty else "",
        ],
        "outputs": {
            "snapshots": str(detail_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Workbook snapshots are historical planning snapshots, useful for backtesting.",
            "AX live open PO tables are still preferred for current operational state.",
        ],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


def main() -> None:
    """Main CLI entry point for Product Info inbound snapshots extractor."""
    args = parse_args()
    files = sorted(args.source_dir.glob(args.pattern))
    files = [path for path in files if snapshot_date_from_name(path) is not None]
    if args.max_files > 0:
        files = files[-args.max_files :]
    if not files:
        raise FileNotFoundError(f"No Product Info workbooks found in {args.source_dir}")

    frames = []
    failures = []
    for path in files:
        try:
            frame = read_workbook_inbound(path)
            frames.append(frame)
            print(f"{path.name}: {len(frame):,} inbound rows")
        except Exception as exc:  # noqa: BLE001 - keep extracting other snapshots.
            failures.append({"path": str(path), "error": str(exc)})
            print(f"WARNING: skipped {path.name}: {exc}")

    if not frames:
        raise RuntimeError(f"No inbound rows extracted. Failures: {failures}")
    output = pd.concat(frames, ignore_index=True).sort_values(
        ["SnapshotDate", "SKU", "ExpectedInDCDate", "PurchID"],
        kind="mergesort",
    )
    write_outputs(output, args)
    if failures:
        print(json.dumps({"failures": failures}, indent=2))


if __name__ == "__main__":
    main()
