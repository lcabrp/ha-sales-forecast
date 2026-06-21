from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from monitoring.inventory_zone_compliance_monitor import (  # noqa: E402
    DEFAULT_EXCLUDED_LOCATIONS,
    DEFAULT_EXCLUDED_ZONES,
    DEFAULT_PROFILES,
    prepare_detail,
    query_live_inventory,
)


OLD_FWD = (
    ROOT
    / "Output"
    / "Monitoring"
    / "forecast_snapshots"
    / "confirmed_raw"
    / "FwdDemandCSV_2026-06-01_e6fb384972ef.csv"
)
NEW_FWD = ROOT / "Output" / "Ingestion" / "FwdDemandCSV_2026-06-11.csv"
OUT_DIR = ROOT / "scratch" / "forecast_churn_20260611"


def velocity(value: object) -> str:
    text = "" if pd.isna(value) else str(value).strip().upper()
    if text.endswith("AA"):
        return "AA"
    if text:
        return text[-1]
    return ""


def read_forecast(path: Path, label: str) -> pd.DataFrame:
    cols = [
        "SKU",
        "ProductGroupCode",
        "SizeGroupCode",
        "Velocity",
        "SlotTier",
        "PutawayIndicator",
    ]
    df = pd.read_csv(path, usecols=cols, dtype={"SKU": str}).fillna("")
    rename = {col: f"{label}{col}" for col in cols if col != "SKU"}
    return df.rename(columns=rename)


def count_metrics(df: pd.DataFrame, prefix: str) -> dict[str, float]:
    return {
        f"{prefix}_inventory_rows": float(len(df)),
        f"{prefix}_occupied_locations": float(df["Location"].nunique()),
        f"{prefix}_unique_skus": float(df["SKU"].nunique()),
        f"{prefix}_physical_qty": float(df["PhysicalQty"].sum()),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(
        data_area="ha",
        partition_id=5637144576,
        warehouse="4010",
        profiles=list(DEFAULT_PROFILES),
        include_zone_d=False,
        include_overflow_zone=False,
        include_aisle34=False,
    )
    raw = query_live_inventory(args, server="prodaxsql2", database="DAX_PROD")
    live = prepare_detail(raw)
    live = live.drop(columns=["ForecastSlotTier", "ForecastCategoryCode"], errors="ignore")

    old = read_forecast(OLD_FWD, "Old")
    new = read_forecast(NEW_FWD, "New")
    impact = live.merge(old, on="SKU", how="left").merge(new, on="SKU", how="left")

    for col in [
        "OldSlotTier",
        "NewSlotTier",
        "OldProductGroupCode",
        "NewProductGroupCode",
        "OldSizeGroupCode",
        "NewSizeGroupCode",
        "OldVelocity",
        "NewVelocity",
    ]:
        impact[col] = impact[col].fillna("").astype(str).str.strip().str.upper()

    impact["HasOldForecast"] = impact["OldSlotTier"].ne("")
    impact["HasNewForecast"] = impact["NewSlotTier"].ne("")
    impact["HasBothForecasts"] = impact["HasOldForecast"] & impact["HasNewForecast"]
    impact["SlotTierChanged"] = (
        impact["HasBothForecasts"] & impact["OldSlotTier"].ne(impact["NewSlotTier"])
    )
    impact["ProductGroupChanged"] = (
        impact["HasBothForecasts"]
        & impact["OldProductGroupCode"].ne(impact["NewProductGroupCode"])
    )
    impact["SizeGroupChanged"] = (
        impact["HasBothForecasts"] & impact["OldSizeGroupCode"].ne(impact["NewSizeGroupCode"])
    )
    impact["OldExactMatch"] = impact["HasOldForecast"] & impact["CurrentZoneId"].eq(
        impact["OldSlotTier"]
    )
    impact["NewExactMatch"] = impact["HasNewForecast"] & impact["CurrentZoneId"].eq(
        impact["NewSlotTier"]
    )
    impact["WouldLoseExactMatch"] = impact["OldExactMatch"] & ~impact["NewExactMatch"]
    impact["WouldGainExactMatch"] = ~impact["OldExactMatch"] & impact["NewExactMatch"]
    impact["OldVelocityFromZone"] = impact["CurrentZoneId"].map(velocity)
    impact["NewVelocityFromForecast"] = impact["NewSlotTier"].map(velocity)
    impact["VelocityZoneMatchAfter"] = impact["OldVelocityFromZone"].eq(
        impact["NewVelocityFromForecast"]
    )

    rows: list[dict[str, float | str]] = []
    for name, frame in [
        ("live_scope", impact),
        ("has_both_forecasts", impact[impact["HasBothForecasts"]]),
        ("new_forecast_changed_slottier", impact[impact["SlotTierChanged"]]),
        ("would_lose_exact_match", impact[impact["WouldLoseExactMatch"]]),
        ("would_gain_exact_match", impact[impact["WouldGainExactMatch"]]),
        ("old_exact_match", impact[impact["OldExactMatch"]]),
        ("new_exact_match", impact[impact["NewExactMatch"]]),
        ("no_new_forecast", impact[~impact["HasNewForecast"]]),
        ("new_only_forecast_on_floor", impact[~impact["HasOldForecast"] & impact["HasNewForecast"]]),
    ]:
        row: dict[str, float | str] = {"cohort": name}
        row.update(count_metrics(frame, "count"))
        rows.append(row)
    summary = pd.DataFrame(rows)

    by_transition = (
        impact[impact["SlotTierChanged"]]
        .groupby(["OldVelocity", "NewVelocity"], dropna=False)
        .agg(
            InventoryRows=("SKU", "size"),
            OccupiedLocations=("Location", "nunique"),
            UniqueSKUs=("SKU", "nunique"),
            PhysicalQty=("PhysicalQty", "sum"),
            WouldLoseExactRows=("WouldLoseExactMatch", "sum"),
            WouldGainExactRows=("WouldGainExactMatch", "sum"),
        )
        .reset_index()
        .sort_values(["InventoryRows", "OldVelocity", "NewVelocity"], ascending=[False, True, True])
    )

    by_current_zone = (
        impact[impact["SlotTierChanged"]]
        .groupby(["CurrentZoneId", "OldSlotTier", "NewSlotTier"], dropna=False)
        .agg(
            InventoryRows=("SKU", "size"),
            OccupiedLocations=("Location", "nunique"),
            UniqueSKUs=("SKU", "nunique"),
            PhysicalQty=("PhysicalQty", "sum"),
            WouldLoseExactRows=("WouldLoseExactMatch", "sum"),
            WouldGainExactRows=("WouldGainExactMatch", "sum"),
        )
        .reset_index()
        .sort_values(["InventoryRows", "PhysicalQty"], ascending=[False, False])
    )

    impact.to_csv(OUT_DIR / "live_inventory_june11_forecast_impact_detail.csv", index=False)
    summary.to_csv(OUT_DIR / "live_inventory_june11_forecast_impact_summary.csv", index=False)
    by_transition.to_csv(
        OUT_DIR / "live_inventory_june11_forecast_impact_by_transition.csv", index=False
    )
    by_current_zone.to_csv(
        OUT_DIR / "live_inventory_june11_forecast_impact_by_zone_tier.csv", index=False
    )

    print("\nLIVE INVENTORY IMPACT SUMMARY")
    print(summary.to_string(index=False))
    print("\nCHANGED FLOOR INVENTORY BY VELOCITY TRANSITION")
    print(by_transition.to_string(index=False))
    print(f"\nExcluded zones: {DEFAULT_EXCLUDED_ZONES}")
    print(f"Excluded locations: {DEFAULT_EXCLUDED_LOCATIONS}")
    print(f"Wrote detail files to {OUT_DIR}")


if __name__ == "__main__":
    main()
