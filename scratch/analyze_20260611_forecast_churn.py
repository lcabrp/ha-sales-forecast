from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
OLD_FWD = (
    ROOT
    / "Output"
    / "Monitoring"
    / "forecast_snapshots"
    / "confirmed_raw"
    / "FwdDemandCSV_2026-06-01_e6fb384972ef.csv"
)
NEW_FWD = ROOT / "Output" / "Ingestion" / "FwdDemandCSV_2026-06-11.csv"
OLD_SLOTS = ROOT / "Output" / "Ingestion" / "RequiredSlots_2026-06-01.csv"
NEW_SLOTS = ROOT / "Output" / "Ingestion" / "RequiredSlots_2026-06-11.csv"
OUT_DIR = ROOT / "scratch" / "forecast_churn_20260611"

FD_COLS = [f"FD{i}" for i in range(1, 15)]
RANK = {"C": 1, "B": 2, "A": 3, "AA": 4}


def read_fwd(path: Path) -> pd.DataFrame:
    cols = [
        "SKU",
        "ProductGroupCode",
        "SizeGroupCode",
        "Velocity",
        "SlotTier",
        "PutawayIndicator",
        "ReplenishmentThreshold",
        "ForecastStartDate",
        *FD_COLS,
    ]
    df = pd.read_csv(path, usecols=cols, dtype={"SKU": str})
    for col in FD_COLS + ["PutawayIndicator", "ReplenishmentThreshold"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["FD14"] = df[FD_COLS].sum(axis=1)
    df["Has14DayDemand"] = df["FD14"] > 0
    return df


def snapshot_summary(label: str, df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        df.groupby("Velocity", dropna=False)
        .agg(
            SKUs=("SKU", "count"),
            FD14Units=("FD14", "sum"),
            ActiveSKUs=("PutawayIndicator", lambda s: int((s == 1).sum())),
            ReserveSKUs=("PutawayIndicator", lambda s: int((s == 0).sum())),
            OffsiteSKUs=("PutawayIndicator", lambda s: int((s == 2).sum())),
            SKUsWith14DayDemand=("Has14DayDemand", "sum"),
        )
        .reset_index()
    )
    summary.insert(0, "Snapshot", label)
    return summary


def transition_summary(old: pd.DataFrame, new: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    merged = old.merge(
        new,
        on="SKU",
        how="inner",
        suffixes=("_Old", "_New"),
    )
    merged["OldRank"] = merged["Velocity_Old"].map(RANK)
    merged["NewRank"] = merged["Velocity_New"].map(RANK)
    merged["RankDelta"] = merged["NewRank"] - merged["OldRank"]
    merged["VelocityChanged"] = merged["Velocity_Old"] != merged["Velocity_New"]
    merged["SlotTierChanged"] = merged["SlotTier_Old"] != merged["SlotTier_New"]
    merged["ProductGroupChanged"] = (
        merged["ProductGroupCode_Old"] != merged["ProductGroupCode_New"]
    )
    merged["SizeGroupChanged"] = merged["SizeGroupCode_Old"] != merged["SizeGroupCode_New"]
    merged["VelocityOnlySlotTierChange"] = (
        merged["SlotTierChanged"]
        & merged["VelocityChanged"]
        & ~merged["ProductGroupChanged"]
        & ~merged["SizeGroupChanged"]
    )
    merged["FD14Delta"] = merged["FD14_New"] - merged["FD14_Old"]
    merged["AbsFD14Delta"] = merged["FD14Delta"].abs()

    changed = merged[merged["VelocityChanged"]].copy()
    matrix = pd.crosstab(
        changed["Velocity_Old"],
        changed["Velocity_New"],
        rownames=["OldVelocity"],
        colnames=["NewVelocity"],
    ).reindex(index=["AA", "A", "B", "C"], columns=["AA", "A", "B", "C"], fill_value=0)

    metrics = {
        "old_rows": len(old),
        "new_rows": len(new),
        "shared_skus": len(merged),
        "old_only_skus": len(set(old["SKU"]) - set(new["SKU"])),
        "new_only_skus": len(set(new["SKU"]) - set(old["SKU"])),
        "velocity_changes": int(merged["VelocityChanged"].sum()),
        "velocity_change_rate": float(merged["VelocityChanged"].mean()),
        "slot_tier_changes": int(merged["SlotTierChanged"].sum()),
        "slot_tier_change_rate": float(merged["SlotTierChanged"].mean()),
        "product_group_changes": int(merged["ProductGroupChanged"].sum()),
        "size_group_changes": int(merged["SizeGroupChanged"].sum()),
        "velocity_only_slot_tier_changes": int(merged["VelocityOnlySlotTierChange"].sum()),
        "promotions": int((merged["RankDelta"] > 0).sum()),
        "demotions": int((merged["RankDelta"] < 0).sum()),
        "multi_tier_jumps": int((merged["RankDelta"].abs() > 1).sum()),
        "aa_to_c": int(((merged["Velocity_Old"] == "AA") & (merged["Velocity_New"] == "C")).sum()),
        "c_to_aa": int(((merged["Velocity_Old"] == "C") & (merged["Velocity_New"] == "AA")).sum()),
        "direct_high_to_c": int(
            (merged["Velocity_Old"].isin(["AA", "A"]) & (merged["Velocity_New"] == "C")).sum()
        ),
        "fd14_units_old": float(old["FD14"].sum()),
        "fd14_units_new": float(new["FD14"].sum()),
        "fd14_units_shared_old": float(merged["FD14_Old"].sum()),
        "fd14_units_shared_new": float(merged["FD14_New"].sum()),
    }
    metrics_df = pd.DataFrame(
        [{"Metric": key, "Value": value} for key, value in metrics.items()]
    )

    detail_cols = [
        "SKU",
        "Velocity_Old",
        "Velocity_New",
        "SlotTier_Old",
        "SlotTier_New",
        "ProductGroupCode_Old",
        "ProductGroupCode_New",
        "SizeGroupCode_Old",
        "SizeGroupCode_New",
        "FD14_Old",
        "FD14_New",
        "FD14Delta",
        "RankDelta",
        "PutawayIndicator_Old",
        "PutawayIndicator_New",
    ]
    changed.sort_values(
        ["RankDelta", "AbsFD14Delta", "SKU"],
        ascending=[True, False, True],
    )[detail_cols].to_csv(OUT_DIR / "velocity_change_detail.csv", index=False)
    matrix.to_csv(OUT_DIR / "velocity_transition_matrix.csv")
    return metrics_df, matrix


def required_slot_summary() -> tuple[pd.DataFrame, pd.DataFrame]:
    old = pd.read_csv(OLD_SLOTS)
    new = pd.read_csv(NEW_SLOTS)
    for df in (old, new):
        df["TotalRequiredSlots"] = pd.to_numeric(
            df["TotalRequiredSlots"], errors="coerce"
        ).fillna(0)
    old_total = old["TotalRequiredSlots"].sum()
    new_total = new["TotalRequiredSlots"].sum()

    by_velocity = (
        old.groupby("Velocity")["TotalRequiredSlots"].sum().rename("OldSlots").to_frame()
        .join(
            new.groupby("Velocity")["TotalRequiredSlots"].sum().rename("NewSlots"),
            how="outer",
        )
        .fillna(0)
        .reset_index()
    )
    by_velocity["DeltaSlots"] = by_velocity["NewSlots"] - by_velocity["OldSlots"]

    tier = old[["SlotTier", "TotalRequiredSlots"]].rename(
        columns={"TotalRequiredSlots": "OldSlots"}
    ).merge(
        new[["SlotTier", "TotalRequiredSlots"]].rename(
            columns={"TotalRequiredSlots": "NewSlots"}
        ),
        on="SlotTier",
        how="outer",
    )
    tier[["OldSlots", "NewSlots"]] = tier[["OldSlots", "NewSlots"]].fillna(0)
    tier["DeltaSlots"] = tier["NewSlots"] - tier["OldSlots"]
    tier["AbsDeltaSlots"] = tier["DeltaSlots"].abs()
    tier = tier.sort_values(["AbsDeltaSlots", "SlotTier"], ascending=[False, True])

    totals = pd.DataFrame(
        [
            {"Metric": "old_required_slots", "Value": old_total},
            {"Metric": "new_required_slots", "Value": new_total},
            {"Metric": "required_slot_delta", "Value": new_total - old_total},
            {"Metric": "old_slot_tiers", "Value": old["SlotTier"].nunique()},
            {"Metric": "new_slot_tiers", "Value": new["SlotTier"].nunique()},
            {
                "Metric": "slot_tiers_with_nonzero_delta",
                "Value": int((tier["DeltaSlots"] != 0).sum()),
            },
        ]
    )
    by_velocity.to_csv(OUT_DIR / "required_slots_by_velocity.csv", index=False)
    tier.to_csv(OUT_DIR / "required_slots_by_tier_delta.csv", index=False)
    return totals, by_velocity


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    old = read_fwd(OLD_FWD)
    new = read_fwd(NEW_FWD)

    snapshot = pd.concat(
        [
            snapshot_summary("2026-06-01 confirmed", old),
            snapshot_summary("2026-06-11 generated", new),
        ],
        ignore_index=True,
    )
    metrics, matrix = transition_summary(old, new)
    slot_totals, slot_velocity = required_slot_summary()

    snapshot.to_csv(OUT_DIR / "snapshot_velocity_summary.csv", index=False)
    metrics.to_csv(OUT_DIR / "forecast_churn_metrics.csv", index=False)
    slot_totals.to_csv(OUT_DIR / "required_slot_metrics.csv", index=False)

    print("\nFORECAST CHURN METRICS")
    print(metrics.to_string(index=False))
    print("\nVELOCITY TRANSITION MATRIX")
    print(matrix.to_string())
    print("\nSNAPSHOT VELOCITY SUMMARY")
    print(snapshot.to_string(index=False))
    print("\nREQUIRED SLOT METRICS")
    print(slot_totals.to_string(index=False))
    print("\nREQUIRED SLOTS BY VELOCITY")
    print(slot_velocity.to_string(index=False))
    print(f"\nWrote detail files to {OUT_DIR}")


if __name__ == "__main__":
    main()
