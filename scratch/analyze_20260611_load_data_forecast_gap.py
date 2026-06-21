from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from ingestion_pipeline import get_latest_source_file, read_load_data  # noqa: E402


FWD_PATH = ROOT / "Output" / "Ingestion" / "FwdDemandCSV_2026-06-11.csv"
OUT_DIR = ROOT / "scratch" / "forecast_churn_20260611"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    source = get_latest_source_file()
    load = read_load_data(source)
    fwd = pd.read_csv(
        FWD_PATH,
        usecols=["SKU", "SlotTier", "PutawayIndicator", "Velocity"],
        dtype={"SKU": str},
    ).fillna("")

    load["InFwdDemandCSV"] = load["SKU"].isin(set(fwd["SKU"]))
    missing = load[~load["InFwdDemandCSV"]].copy()
    present = load[load["InFwdDemandCSV"]].copy()

    detail = load.merge(fwd, on="SKU", how="left")
    detail.to_csv(OUT_DIR / "load_data_forecast_coverage_detail.csv", index=False)
    missing.to_csv(OUT_DIR / "load_data_missing_from_fwd_demand.csv", index=False)

    summary = pd.DataFrame(
        [
            {"Metric": "source_file", "Value": source.name},
            {"Metric": "load_data_unique_skus", "Value": len(load)},
            {"Metric": "load_data_skus_in_fwd_demand", "Value": len(present)},
            {"Metric": "load_data_skus_missing_fwd_demand", "Value": len(missing)},
            {
                "Metric": "missing_rate",
                "Value": len(missing) / len(load) if len(load) else 0,
            },
        ]
    )
    summary.to_csv(OUT_DIR / "load_data_forecast_coverage_summary.csv", index=False)

    print("\nLOAD DATA FORECAST COVERAGE")
    print(summary.to_string(index=False))
    print("\nTop missing LoadMaxQty rows")
    print(missing.sort_values("LoadMaxQty", ascending=False).head(25).to_string(index=False))
    print(f"\nWrote detail files to {OUT_DIR}")


if __name__ == "__main__":
    main()
