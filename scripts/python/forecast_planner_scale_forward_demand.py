"""Scale an AX Forward Demand CSV to Planner daily total units.

This is a low-risk volume candidate: it keeps the source CSV's SKU universe,
SlotTier, and PutawayIndicator decisions intact, then adjusts FD1-FD14 totals
by day to match a Planner total-volume anchor.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Add parent directory to path to locate corporate settings and contract tables
PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_replacement_contract import AX_FORWARD_DEMAND_COLUMNS, FD_COLUMNS  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402

# Default target input and output locations for the scaled candidates
DEFAULT_INPUT_CSV = PROJECT_ROOT / "Output" / "Ingestion" / "FwdDemandCSV_2026-06-16.csv"
DEFAULT_PLANNER_DAILY_PATH = (
    PROJECT_ROOT / "Output" / "ForecastAccuracy" / "planner" / "planner_daily_totals_2026.parquet"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "replacement_contract"
PLANNER_COLUMN = "ops_imf_plan_forecasted_units"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the demand scaling runner.

    Returns:
        argparse.Namespace: Parsed arguments namespaces.
    """
    parser = argparse.ArgumentParser(description="Scale FD1-FD14 daily totals to Planner OPS/IMF units.")
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=DEFAULT_INPUT_CSV,
        help="Path to the baseline AX Forward Demand CSV file.",
    )
    parser.add_argument(
        "--planner-daily-path",
        type=Path,
        default=DEFAULT_PLANNER_DAILY_PATH,
        help="Path to the Planner daily totals dataset (CSV or Parquet).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to save the scaled output candidate folder.",
    )
    parser.add_argument(
        "--candidate-id",
        help="Unique candidate folder identifier. Generates one dynamically if omitted.",
    )
    parser.add_argument(
        "--planner-scale",
        type=float,
        default=1.0,
        help="Scaling factor to apply to target Planner units.",
    )
    parser.add_argument(
        "--planner-column",
        default=PLANNER_COLUMN,
        help="Column in the planner dataset containing anchor daily target units.",
    )
    return parser.parse_args()


def load_planner(path: Path, column: str, start: pd.Timestamp) -> pd.DataFrame:
    """Load daily planner volumes for the 14-day horizon from the target file.

    Args:
        path (Path): Path to the planner CSV or Parquet file.
        column (str): Column name containing daily planner units.
        start (pd.Timestamp): Start date of the 14-day forecast window.

    Returns:
        pd.DataFrame: Sliced planner totals dataframe containing ForecastDate and PlannerUnits.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    planner = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    if "Date" not in planner.columns or column not in planner.columns:
        raise ValueError(f"Planner file must contain Date and {column}: {path}")
    planner = planner[["Date", column]].copy()
    planner["ForecastDate"] = pd.to_datetime(planner["Date"], errors="coerce").dt.normalize()
    planner["PlannerUnits"] = pd.to_numeric(planner[column], errors="coerce").fillna(0).clip(lower=0)
    end = start + pd.Timedelta(days=13)
    return planner.loc[planner["ForecastDate"].between(start, end), ["ForecastDate", "PlannerUnits"]].copy()


def integerize_day(group: pd.DataFrame) -> pd.DataFrame:
    """Distribute scaled fractional forecasts into whole integer units per SKU.

    Uses the Largest Remainder Method (Hamilton method) to adjust rounding errors
    so that the sum of the integerized forecasts exactly equals the rounded sum of
    the fractional forecasts.

    Args:
        group (pd.DataFrame): Dataframe of records for a single forecast day.

    Returns:
        pd.DataFrame: Modified dataframe with a 'ScaledUnits' integer column.
    """
    work = group.copy()
    raw = pd.to_numeric(work["ScaledRaw"], errors="coerce").fillna(0).clip(lower=0)
    floors = raw.apply(np.floor).astype(int)
    target = int(round(float(raw.sum())))
    target = max(target, int(floors.sum()))
    extra = target - int(floors.sum())
    work["ScaledUnits"] = floors
    if extra > 0:
        # Distribute remaining units to SKUs with the largest fractional remainders,
        # using SKU text code as a deterministic tie-breaker.
        order = (
            pd.DataFrame({"index": work.index, "Remainder": raw - floors, "SKU": work["SKU"].to_numpy()})
            .sort_values(["Remainder", "SKU"], ascending=[False, True])
            .head(extra)
        )
        work.loc[order["index"], "ScaledUnits"] += 1
    return work


def scale_forward_demand(
    df: pd.DataFrame,
    planner: pd.DataFrame,
    start: pd.Timestamp,
    scale: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rescale 14-day daily totals from AX Forward Demand to match Planner volume.

    Args:
        df (pd.DataFrame): Input AX forward demand records.
        planner (pd.DataFrame): Target daily planner totals.
        start (pd.Timestamp): Start date of the 14-day window.
        scale (float): Adjustment factor applied to the target planner total.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: The scaled forward demand records, and daily audit summary.
    """
    work = df.copy()
    for col in FD_COLUMNS:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0).clip(lower=0)

    # Melt to long format for SKU-day calculations
    daily = work[["SKU", *FD_COLUMNS]].melt(
        id_vars=["SKU"],
        value_vars=FD_COLUMNS,
        var_name="FD",
        value_name="OriginalUnits",
    )
    daily["ForecastDay"] = daily["FD"].str.replace("FD", "", regex=False).astype(int)
    daily["ForecastDate"] = start + pd.to_timedelta(daily["ForecastDay"] - 1, unit="D")
    current = daily.groupby("ForecastDate", as_index=False).agg(CurrentUnits=("OriginalUnits", "sum"))
    daily = daily.merge(current, on="ForecastDate", how="left").merge(planner, on="ForecastDate", how="left")
    daily["TargetUnits"] = daily["PlannerUnits"] * max(0.0, float(scale))
    daily["ScaleFactor"] = 1.0
    can_scale = daily["TargetUnits"].notna() & daily["TargetUnits"].gt(0) & daily["CurrentUnits"].gt(0)
    daily.loc[can_scale, "ScaleFactor"] = daily.loc[can_scale, "TargetUnits"] / daily.loc[can_scale, "CurrentUnits"]
    daily["ScaledRaw"] = daily["OriginalUnits"] * daily["ScaleFactor"]

    # Re-allocate fractions to integers day-by-day
    daily = pd.concat(
        [integerize_day(group) for _, group in daily.groupby("ForecastDay", sort=False)],
        ignore_index=True,
    )

    # Pivot back to wide format FD1-FD14
    scaled = (
        daily.pivot_table(index="SKU", columns="ForecastDay", values="ScaledUnits", aggfunc="sum", fill_value=0)
        .rename(columns={idx: f"FD{idx}" for idx in range(1, 15)})
        .reset_index()
    )
    for col in FD_COLUMNS:
        if col not in scaled.columns:
            scaled[col] = 0
        scaled[col] = pd.to_numeric(scaled[col], errors="coerce").fillna(0).astype(int)
    work = work.drop(columns=FD_COLUMNS).merge(scaled[["SKU", *FD_COLUMNS]], on="SKU", how="left")
    for col in FD_COLUMNS:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0).astype(int)

    daily_summary = (
        daily.groupby("ForecastDate", as_index=False)
        .agg(
            CurrentUnits=("OriginalUnits", "sum"),
            PlannerUnits=("PlannerUnits", "first"),
            TargetUnits=("TargetUnits", "first"),
            ScaledUnits=("ScaledUnits", "sum"),
        )
        .sort_values("ForecastDate", kind="mergesort")
    )
    return work[df.columns], daily_summary


def main() -> None:
    """Orchestrate forward demand CSV loading, scaling, saving, and metadata writing."""
    args = parse_args()
    if not args.input_csv.exists():
        raise FileNotFoundError(args.input_csv)
    df = pd.read_csv(args.input_csv, dtype=str).fillna("")
    missing = [col for col in AX_FORWARD_DEMAND_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Input CSV is missing AX columns: {missing}")
    forecast_dates = pd.to_datetime(df["ForecastStartDate"].replace("", pd.NA).dropna(), errors="coerce")
    if forecast_dates.empty:
        raise ValueError("Could not find ForecastStartDate in input CSV")
    start = pd.Timestamp(forecast_dates.iloc[0]).normalize()
    planner = load_planner(args.planner_daily_path, args.planner_column, start)
    candidate_id = (
        args.candidate_id
        or f"planner_scaled_forward_demand_{args.planner_scale:g}_{datetime.now():%Y%m%d_%H%M%S}"
    )
    candidate_dir = args.output_dir / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=False)

    scaled, daily_summary = scale_forward_demand(df, planner, start, args.planner_scale)
    output_csv = candidate_dir / f"FwdDemandCSV_{start.date().isoformat()}.csv"
    daily_path = candidate_dir / "daily_total_summary.csv"
    metadata_path = candidate_dir / "candidate_metadata.json"
    scaled.to_csv(output_csv, index=False)
    daily_summary.to_csv(daily_path, index=False)

    fd_total = float(pd.to_numeric(scaled[FD_COLUMNS].stack(), errors="coerce").fillna(0).sum())
    metadata: dict[str, Any] = {
        "candidate_id": candidate_id,
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "method": "planner_scaled_forward_demand",
        "input_csv": str(args.input_csv),
        "planner_daily_path": str(args.planner_daily_path),
        "planner_column": args.planner_column,
        "planner_scale": float(args.planner_scale),
        "forecast_start_date": start.date().isoformat(),
        "output_csv": str(output_csv),
        "daily_summary": str(daily_path),
        "rows": int(len(scaled)),
        "fd1_to_fd14_units": fd_total,
        "putaway_indicator_counts": scaled["PutawayIndicator"].value_counts().to_dict()
        if "PutawayIndicator" in scaled.columns
        else {},
        "duplicate_item_color_size_keys": int(
            scaled.duplicated(["Item", "Color", "Size"], keep=False).sum()
            if {"Item", "Color", "Size"}.issubset(scaled.columns)
            else 0
        ),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()

