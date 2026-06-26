"""Extract daily Planner forecast/actual totals for forecast calibration.

The Planner workbooks are operational spreadsheets with date columns across the
Outbound tab. This extractor keeps the row contract explicit and writes a tidy
daily table that can be joined to forecast-package totals without reopening the
workbook each time.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


DEFAULT_SOURCE_DIR = PROJECT_ROOT / "Source" / "Planner"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "planner"
OUTBOUND_SHEET = "Outbound"
KPI_SHEET = "KPI DC Forecast link file"
DATE_ROW_INDEX = 2

# Mapper for raw row labels in the outbound sheet to cleaned database field names
METRIC_LABELS = {
    "Remaining Volume (Units)": "remaining_volume_units",
    "Units Shipped Goal": "units_shipped_goal",
    "Shipped Units (PowerBi)": "shipped_units_powerbi",
    "Forecasted Demand (Units)": "forecasted_demand_units",
    "Forecasted Demand (Units) (-10%, -5%)": "forecasted_demand_units",
    "OPS (8/18 and earlier) or IMF Plan Forecasted (Units)": "ops_imf_plan_forecasted_units",
    "Actual Demand (units)": "actual_demand_units",
    "Percentage demand vs. forecast": "percentage_demand_vs_forecast",
}

REQUIRED_OUTPUT_COLUMNS = [
    "remaining_volume_units",
    "units_shipped_goal",
    "shipped_units_powerbi",
    "forecasted_demand_units",
    "ops_imf_plan_forecasted_units",
    "actual_demand_units",
    "percentage_demand_vs_forecast",
]


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the Planner extractor.

    Returns:
        argparse.Namespace: Checked command line arguments.
    """
    parser = argparse.ArgumentParser(description="Extract daily Planner totals from the Planner workbook.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Also write a timestamped copy so future Planner edits do not erase the historical forecast view.",
    )
    return parser.parse_args()


def choose_source(source_dir: Path, year: int, source_file: Path | None) -> Path:
    """Choose the best input file from available planner workbooks.

    If a specific source_file is provided, verifies that it exists.
    Otherwise, scans source_dir for workbooks matching the year and picks the newest by mtime.

    Args:
        source_dir: Directory containing planner files.
        year: The year to search for in filenames.
        source_file: An optional specific file path.

    Returns:
        Path: Path to the selected planner workbook.

    Raises:
        FileNotFoundError: If no candidate workbooks are found.
    """
    if source_file:
        if not source_file.exists():
            raise FileNotFoundError(source_file)
        return source_file
    candidates = sorted(source_dir.glob(f"{year} Planner*.xlsx"))
    if not candidates:
        raise FileNotFoundError(f"No {year} Planner*.xlsx found in {source_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _normalize_label(value: object) -> str:
    """Normalize row label text by removing extra spaces.

    Args:
        value: Input label object/string.

    Returns:
        str: Cleaned, space-normalized string.
    """
    return " ".join(str(value).strip().split())


def extract_outbound(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Extract daily outbound metric totals from the 'Outbound' sheet of the workbook.

    Finds the row containing dates, aligns column indices, extracts row metrics
    matching METRIC_LABELS, and formats into a tidy pandas DataFrame.

    Args:
        path: Path to the Excel workbook.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Tidy daily metrics DataFrame and run metadata.

    Raises:
        ValueError: If 'Actual Demand (units)' row cannot be found.
    """
    raw = pd.read_excel(path, sheet_name=OUTBOUND_SHEET, header=None, engine="calamine")
    dates = pd.to_datetime(raw.iloc[DATE_ROW_INDEX, 1:], errors="coerce")
    date_columns = [idx + 1 for idx, value in enumerate(dates) if pd.notna(value)]
    date_values = [pd.Timestamp(value).normalize() for value in dates if pd.notna(value)]

    rows_by_label = {_normalize_label(raw.iloc[idx, 0]): idx for idx in range(len(raw))}
    output = pd.DataFrame({"Date": date_values})
    metric_rows_excel: dict[str, int] = {}
    for label, column_name in METRIC_LABELS.items():
        if label not in rows_by_label:
            continue
        row_idx = rows_by_label[label]
        output[column_name] = pd.to_numeric(raw.iloc[row_idx, date_columns], errors="coerce").to_numpy()
        metric_rows_excel[label] = int(row_idx + 1)

    missing_outputs = [column for column in REQUIRED_OUTPUT_COLUMNS if column not in output.columns]
    for column in missing_outputs:
        output[column] = pd.NA
    if "actual_demand_units" in missing_outputs:
        raise ValueError("Missing required Outbound row label: Actual Demand (units)")

    # Calculate helper ratios
    output["plan_vs_actual_demand_pct"] = (
        output["ops_imf_plan_forecasted_units"] / output["actual_demand_units"]
    )
    output["powerbi_vs_actual_demand_pct"] = (
        output["shipped_units_powerbi"] / output["actual_demand_units"]
    )
    
    metadata = {
        "outbound_sheet": OUTBOUND_SHEET,
        "date_row_excel": DATE_ROW_INDEX + 1,
        "date_min": output["Date"].min().date().isoformat(),
        "date_max": output["Date"].max().date().isoformat(),
        "row_count": int(len(output)),
        "metric_rows_excel": metric_rows_excel,
        "missing_output_columns": missing_outputs,
    }
    return output, metadata


def extract_kpi(path: Path) -> pd.DataFrame:
    """Extract daily KPI DC Forecasted demand units from the link sheet.

    If the tab does not exist, returns an empty DataFrame with the correct schema.

    Args:
        path: Path to the Excel workbook.

    Returns:
        pd.DataFrame: A DataFrame with columns ['Date', 'kpi_forecasted_demand_units'].
    """
    try:
        kpi = pd.read_excel(path, sheet_name=KPI_SHEET, header=0, engine="calamine")
    except ValueError:
        # Sheet does not exist or cannot be read
        return pd.DataFrame(columns=["Date", "kpi_forecasted_demand_units"])
        
    required = {"Date", "Forecasted Demand Units"}
    if not required.issubset(kpi.columns):
        return pd.DataFrame(columns=["Date", "kpi_forecasted_demand_units"])
        
    out = kpi[["Date", "Forecasted Demand Units"]].copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    out["kpi_forecasted_demand_units"] = pd.to_numeric(
        out["Forecasted Demand Units"],
        errors="coerce",
    )
    out = out.loc[out["Date"].notna()].drop(columns=["Forecasted Demand Units"])
    return out.drop_duplicates("Date", keep="last")


def main() -> None:
    """Main CLI entry point for planner workbook daily forecast totals extraction."""
    args = parse_args()
    source = choose_source(args.source_dir, args.year, args.source_file)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    outbound, metadata = extract_outbound(source)
    kpi = extract_kpi(source)
    if not kpi.empty:
        outbound = outbound.merge(kpi, on="Date", how="left")
        outbound["kpi_minus_actual_demand_units"] = (
            outbound["kpi_forecasted_demand_units"] - outbound["actual_demand_units"]
        )

    stem = f"planner_daily_totals_{args.year}"
    parquet_path = args.output_dir / f"{stem}.parquet"
    csv_path = args.output_dir / f"{stem}.csv"
    metadata_path = args.output_dir / f"{stem}_metadata.json"
    outbound.to_parquet(parquet_path, index=False)
    outbound.to_csv(csv_path, index=False)

    snapshot_paths: dict[str, str] = {}
    if args.snapshot:
        snapshot_dir = args.output_dir / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_parquet = snapshot_dir / f"{stem}_snapshot_{timestamp}.parquet"
        snapshot_csv = snapshot_dir / f"{stem}_snapshot_{timestamp}.csv"
        outbound.to_parquet(snapshot_parquet, index=False)
        outbound.to_csv(snapshot_csv, index=False)
        snapshot_paths = {
            "snapshot_parquet": str(snapshot_parquet),
            "snapshot_csv": str(snapshot_csv),
        }

    metadata.update(
        {
            "generated_at": datetime.now().replace(microsecond=0).isoformat(),
            "source_file": str(source),
            "source_mtime": datetime.fromtimestamp(source.stat().st_mtime).replace(microsecond=0).isoformat(),
            "output_parquet": str(parquet_path),
            "output_csv": str(csv_path),
            **snapshot_paths,
            "kpi_sheet": KPI_SHEET,
            "kpi_rows": int(len(kpi)),
        }
    )
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"Source: {source}")
    print(f"Rows: {len(outbound):,}; dates: {metadata['date_min']}..{metadata['date_max']}")
    print(f"Wrote: {parquet_path}")
    print(f"Wrote: {csv_path}")
    for snapshot_path in snapshot_paths.values():
        print(f"Wrote: {snapshot_path}")
    print(f"Wrote: {metadata_path}")


if __name__ == "__main__":
    main()
