"""Simulate debt-budgeted activation of an enriched shadow velocity policy.

The analytical score may change weekly, but floor routing cannot be remapped
wholesale while occupied locations still carry stock. This shadow-only replay
fits exact SlotTier capacity, ranks incremental routing changes, quarantines
premium-location demotions for review, and measures several activation budgets.

Nothing writes to AX, ingestion, approved maps, or production allocator logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHADOW_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
REPLAY_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
DETAIL_PATH = SHADOW_DIR / "velocity_policy_enriched_candidate_sku_snapshot.parquet"
STATE_PATH = SHADOW_DIR / "velocity_policy_enriched_stability_sku_snapshot.parquet"
INVENTORY_PATH = REPLAY_DIR / "sku_location_inventory_snapshots.parquet"
REQUIRED_SLOTS_PATH = PROJECT_ROOT / "Output" / "Ingestion" / "RequiredSlots_2026-06-01.csv"
FWD_DEMAND_PATH = PROJECT_ROOT / "Output" / "Ingestion" / "FwdDemandCSV_2026-06-01.csv"
DEPLOYED_MAP_PATH = (
    PROJECT_ROOT
    / "Output"
    / "Monitoring"
    / "deployments"
    / "20260507_144000_EDT"
    / "AX_Proposed_Zone_Map.csv"
)
LOCATION_MASTER_PATH = PROJECT_ROOT / "Output" / "Layout" / "inputs" / "Data_Pick_Locations.csv"
OUTPUT_DIR = SHADOW_DIR
TIER_RANK = {"C": 0, "B": 1, "A": 2, "AA": 3}
PREMIUM_TIERS = {"AA", "A"}
DEFAULT_BUDGETS = (0, 50, 100, 250, 500, 1000)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", type=Path, default=DETAIL_PATH)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    parser.add_argument("--required-slots", type=Path, default=REQUIRED_SLOTS_PATH)
    parser.add_argument("--fwd-demand", type=Path, default=FWD_DEMAND_PATH)
    parser.add_argument("--deployed-map", type=Path, default=DEPLOYED_MAP_PATH)
    parser.add_argument("--location-master", type=Path, default=LOCATION_MASTER_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--candidate", default="demand_and_pick")
    parser.add_argument("--capacity-envelope", default="legacy_sku_population_proxy")
    parser.add_argument("--policy", default="enriched_promo1_demo2_staged")
    parser.add_argument("--budgets", type=int, nargs="*", default=list(DEFAULT_BUDGETS))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "candidates": output_dir / "velocity_policy_incremental_activation_candidates.parquet",
        "budget_decisions": output_dir / "velocity_policy_incremental_activation_budget_decisions.parquet",
        "budget_summary": output_dir / "velocity_policy_incremental_activation_budget_summary.csv",
        "capacity_fit": output_dir / "velocity_policy_slottier_capacity_fit.csv",
        "cohort_summary": output_dir / "velocity_policy_exception_cohort_summary.csv",
        "premium_review": output_dir / "velocity_policy_premium_demotion_review_queue.csv",
        "metadata": output_dir / "velocity_policy_incremental_activation_metadata.json",
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Incremental-activation artifacts already exist. Pass --overwrite to replace them: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}
    for path in temporary.values():
        if path.exists():
            path.unlink()
    return outputs, temporary


def tier_suffix(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper().str.extract(
        r"(AA|A|B|C)$",
        expand=False,
    )


def replace_tier_suffix(slot_tier: pd.Series, velocity: pd.Series) -> pd.Series:
    prefixes = slot_tier.fillna("").astype(str).str.strip().str.upper().str.replace(
        r"(AA|A|B|C)$",
        "",
        regex=True,
    )
    return prefixes + velocity.fillna("").astype(str).str.strip().str.upper()


def count_transitions(values: pd.Series) -> int:
    ordered = values.dropna().astype(str)
    return int(ordered.ne(ordered.shift()).sum() - (not ordered.empty))


def load_selected_detail(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail = pd.read_parquet(args.detail)
    detail["SnapshotEffectiveEST"] = pd.to_datetime(detail["SnapshotEffectiveEST"], utc=True)
    selected = detail[
        detail["Candidate"].eq(args.candidate)
        & detail["CapacityEnvelope"].eq(args.capacity_envelope)
    ].copy()
    if selected.empty:
        raise ValueError("Selected candidate and capacity envelope returned no enriched rows.")
    selected = selected.sort_values(["SKU", "SnapshotEffectiveEST"])
    history = (
        selected.groupby("SKU", as_index=False)
        .agg(
            ObservedForecastSnapshots=("SnapshotEffectiveEST", "nunique"),
            FirstObservedForecastSnapshot=("SnapshotEffectiveEST", "min"),
            LastObservedForecastSnapshot=("SnapshotEffectiveEST", "max"),
            LegacyVelocityTransitions=("Velocity", count_transitions),
            LegacyVelocityDistinct=("Velocity", "nunique"),
            ForecastFD14Min=("ForecastFD14Units", "min"),
            ForecastFD14Max=("ForecastFD14Units", "max"),
            SeasonalPriorYear56dPhysicalTouchesMax=("SeasonalPriorYear56dPhysicalTouches", "max"),
        )
    )
    latest_date = selected["SnapshotEffectiveEST"].max()
    latest = selected[selected["SnapshotEffectiveEST"].eq(latest_date)].copy()
    if latest.duplicated("SKU").any():
        raise ValueError("Latest enriched candidate contains duplicate SKU rows.")
    return latest, history


def load_latest_state(args: argparse.Namespace) -> pd.DataFrame:
    state = pd.read_parquet(args.state)
    state["SnapshotEffectiveEST"] = pd.to_datetime(state["SnapshotEffectiveEST"], utc=True)
    selected = state[state["Policy"].eq(args.policy)].copy()
    if selected.empty:
        raise ValueError(f"Selected stability policy returned no rows: {args.policy}")
    latest = selected[selected["SnapshotEffectiveEST"].eq(selected["SnapshotEffectiveEST"].max())]
    if latest.duplicated("SKU").any():
        raise ValueError("Latest stability state contains duplicate SKU rows.")
    return latest[["SKU", "RoutingVelocity"]]


def load_inventory(path: Path) -> pd.DataFrame:
    inventory = pd.read_parquet(path)
    inventory["SnapshotDate"] = pd.to_datetime(inventory["SnapshotDate"])
    inventory = inventory[inventory["SnapshotDate"].eq(inventory["SnapshotDate"].max())].copy()
    inventory["PhysicalQty"] = pd.to_numeric(inventory["PhysicalQty"], errors="coerce").fillna(0)
    inventory["CurrentLocationVelocity"] = tier_suffix(inventory["CurrentZoneId"])
    if inventory.duplicated(["Location", "SKU"]).any():
        raise ValueError("Latest inventory contains duplicate location/SKU rows.")
    return inventory


def build_painted_capacity(args: argparse.Namespace, inventory: pd.DataFrame) -> pd.DataFrame:
    zone_map = pd.read_csv(args.deployed_map)
    zone_map = zone_map.rename(columns={"WMSLOCATIONID": "Location", "ZONEID": "PaintedSlotTier"})
    location_master = pd.read_csv(args.location_master, usecols=["Location", "LocProfile"])
    painted = zone_map[["Location", "PaintedSlotTier"]].merge(
        location_master,
        on="Location",
        how="left",
        validate="one_to_one",
    )
    occupied_locations = set(inventory["Location"])
    painted["IsOccupied"] = painted["Location"].isin(occupied_locations)
    profile_counts = (
        painted.pivot_table(
            index="PaintedSlotTier",
            columns="LocProfile",
            values="Location",
            aggfunc="size",
            fill_value=0,
        )
        .rename(columns=lambda value: f"PaintedProfile_{re.sub(r'[^A-Za-z0-9]+', '_', str(value))}")
        .reset_index()
    )
    capacity = (
        painted.groupby("PaintedSlotTier", as_index=False)
        .agg(
            PaintedLocations=("Location", "size"),
            PaintedEmptyLocations=("IsOccupied", lambda values: (~values).sum()),
            PaintedOccupiedLocations=("IsOccupied", "sum"),
        )
        .merge(profile_counts, on="PaintedSlotTier", how="left", validate="one_to_one")
        .rename(columns={"PaintedSlotTier": "SlotTier"})
    )
    return capacity


def add_floor_burden(candidates: pd.DataFrame, inventory: pd.DataFrame) -> pd.DataFrame:
    floor = inventory.merge(
        candidates[["SKU", "Velocity", "RoutingVelocity"]],
        on="SKU",
        how="inner",
        validate="many_to_one",
    )
    current_rank = floor["CurrentLocationVelocity"].map(TIER_RANK)
    old_rank = floor["Velocity"].map(TIER_RANK)
    new_rank = floor["RoutingVelocity"].map(TIER_RANK)
    known = current_rank.notna() & old_rank.notna() & new_rank.notna()
    floor["BeforeDebt"] = known & current_rank.ne(old_rank)
    floor["AfterDebt"] = known & current_rank.ne(new_rank)
    floor["AddedDebt"] = floor["AfterDebt"] & ~floor["BeforeDebt"]
    floor["ResolvedDebt"] = floor["BeforeDebt"] & ~floor["AfterDebt"]
    floor["AtRoutingVelocity"] = known & current_rank.eq(new_rank)
    floor["PremiumLocation"] = floor["CurrentLocationVelocity"].isin(PREMIUM_TIERS)
    floor["AfterPremiumDemotionStepProxy"] = np.where(
        known & current_rank.gt(new_rank) & floor["PremiumLocation"],
        current_rank - new_rank,
        0,
    )
    summary = (
        floor.groupby("SKU", as_index=False)
        .agg(
            FloorOccupiedLocations=("Location", "nunique"),
            FloorPhysicalQty=("PhysicalQty", "sum"),
            FloorPremiumOccupiedLocations=("PremiumLocation", "sum"),
            FloorLocationsAlreadyAtRoutingVelocity=("AtRoutingVelocity", "sum"),
            AddedDebtLocationsIfActivated=("AddedDebt", "sum"),
            ResolvedDebtLocationsIfActivated=("ResolvedDebt", "sum"),
            PremiumDemotionStepProxyIfActivated=("AfterPremiumDemotionStepProxy", "sum"),
        )
    )
    output = candidates.merge(summary, on="SKU", how="left", validate="one_to_one")
    burden_columns = [
        "FloorOccupiedLocations",
        "FloorPhysicalQty",
        "FloorPremiumOccupiedLocations",
        "FloorLocationsAlreadyAtRoutingVelocity",
        "AddedDebtLocationsIfActivated",
        "ResolvedDebtLocationsIfActivated",
        "PremiumDemotionStepProxyIfActivated",
    ]
    output[burden_columns] = output[burden_columns].fillna(0)
    output["NetDebtLocationDeltaIfActivated"] = (
        output["AddedDebtLocationsIfActivated"] - output["ResolvedDebtLocationsIfActivated"]
    )
    return output


def assign_exception_cohort(frame: pd.DataFrame, total_snapshots: int) -> pd.Series:
    status = frame["ProductStatus"].fillna("").astype(str).str.upper()
    return pd.Series(
        np.select(
            [
                frame["ObservedForecastSnapshots"].lt(total_snapshots),
                status.str.contains(r"CLEARANCE|FINAL SALE", regex=True),
                frame["SeasonalPriorYear56dPhysicalTouchesMax"].gt(0)
                & frame["Recent56dDemandPhysicalTouches"].eq(0),
                frame["ForecastFD14Min"].eq(0) & frame["ForecastFD14Max"].gt(0),
                frame["LegacyVelocityTransitions"].gt(0),
            ],
            [
                "NewOrReturningInObservedWindow",
                "ClearanceOrFinalSale",
                "PriorYearSeasonalOnlyProxy",
                "ForecastZeroCrossing",
                "ForecastVelocityChanged",
            ],
            default="StableObservedWindow",
        ),
        index=frame.index,
    )


def build_candidates(
    args: argparse.Namespace,
    latest: pd.DataFrame,
    history: pd.DataFrame,
    state: pd.DataFrame,
    inventory: pd.DataFrame,
    capacity: pd.DataFrame,
) -> pd.DataFrame:
    fwd = pd.read_csv(args.fwd_demand, usecols=["SKU", "ProductStatus", "ProductStage", "ReturnAction"])
    required = pd.read_csv(args.required_slots)
    required["PlanningRequiredSlotShare"] = (
        pd.to_numeric(required["TotalRequiredSlots"], errors="coerce").fillna(0)
        / pd.to_numeric(required["SKU_Count"], errors="coerce").replace(0, np.nan)
    ).fillna(0)
    frame = (
        latest.merge(state, on="SKU", how="left", validate="one_to_one")
        .merge(history, on="SKU", how="left", validate="one_to_one")
        .merge(fwd, on="SKU", how="left", validate="one_to_one")
        .merge(
            required[["SlotTier", "PlanningRequiredSlotShare"]],
            on="SlotTier",
            how="left",
            validate="many_to_one",
        )
    )
    frame["RoutingVelocity"] = frame["RoutingVelocity"].fillna(frame["Velocity"])
    frame["RoutingSlotTier"] = replace_tier_suffix(frame["SlotTier"], frame["RoutingVelocity"])
    frame["PlanningRequiredSlotShare"] = frame["PlanningRequiredSlotShare"].fillna(0)
    frame = add_floor_burden(frame, inventory)
    frame["Direction"] = np.select(
        [
            frame["RoutingVelocity"].map(TIER_RANK).gt(frame["Velocity"].map(TIER_RANK)),
            frame["RoutingVelocity"].map(TIER_RANK).lt(frame["Velocity"].map(TIER_RANK)),
        ],
        ["Promotion", "Demotion"],
        default="Unchanged",
    )
    frame["VelocityTierSteps"] = (
        frame["RoutingVelocity"].map(TIER_RANK) - frame["Velocity"].map(TIER_RANK)
    ).abs()
    frame["RequiresPremiumDemotionReview"] = (
        frame["Direction"].eq("Demotion") & frame["FloorPremiumOccupiedLocations"].gt(0)
    )
    frame["ActivationSlotReservation"] = np.where(
        frame["FloorLocationsAlreadyAtRoutingVelocity"].gt(0),
        0,
        np.maximum(1, np.ceil(frame["PlanningRequiredSlotShare"])),
    ).astype(int)
    frame["ExceptionCohort"] = assign_exception_cohort(
        frame,
        int(history["ObservedForecastSnapshots"].max()),
    )
    target_capacity = capacity.rename(
        columns={
            "SlotTier": "RoutingSlotTier",
            "PaintedLocations": "RoutingPaintedLocations",
            "PaintedEmptyLocations": "RoutingPaintedEmptyLocations",
            "PaintedOccupiedLocations": "RoutingPaintedOccupiedLocations",
        }
    )
    frame = frame.merge(
        target_capacity[
            [
                "RoutingSlotTier",
                "RoutingPaintedLocations",
                "RoutingPaintedEmptyLocations",
                "RoutingPaintedOccupiedLocations",
            ]
        ],
        on="RoutingSlotTier",
        how="left",
        validate="many_to_one",
    )
    for column in [
        "RoutingPaintedLocations",
        "RoutingPaintedEmptyLocations",
        "RoutingPaintedOccupiedLocations",
    ]:
        frame[column] = frame[column].fillna(0)
    changed = frame[frame["Direction"].ne("Unchanged")].copy()
    changed["AutoActivationEligible"] = (
        ~changed["RequiresPremiumDemotionReview"] & changed["RoutingPaintedLocations"].gt(0)
    )
    changed["AutoActivationBlockReason"] = np.select(
        [
            changed["RequiresPremiumDemotionReview"],
            changed["RoutingPaintedLocations"].eq(0),
        ],
        [
            "Premium demotion with occupied floor stock requires review",
            "Routing SlotTier has no painted locations",
        ],
        default="Eligible for debt-budget replay",
    )
    changed["PriorityDebtClass"] = np.where(
        changed["NetDebtLocationDeltaIfActivated"].le(0),
        0,
        1,
    )
    changed["PriorityDirection"] = np.where(changed["Direction"].eq("Promotion"), 0, 1)
    changed = changed.sort_values(
        [
            "PriorityDebtClass",
            "PriorityDirection",
            "BurdenScore",
            "AddedDebtLocationsIfActivated",
            "SKU",
        ],
        ascending=[True, True, False, True, True],
    ).reset_index(drop=True)
    changed["ActivationPriorityRank"] = changed.index + 1
    return changed


def build_capacity_fit(
    latest: pd.DataFrame,
    state: pd.DataFrame,
    required_slots_path: Path,
    inventory: pd.DataFrame,
    capacity: pd.DataFrame,
) -> pd.DataFrame:
    required = pd.read_csv(required_slots_path)
    legacy = required.groupby("SlotTier", as_index=False).agg(
        LegacyPlanningRequiredSlots=("TotalRequiredSlots", "sum"),
        LegacyPlanningSKUs=("SKU_Count", "sum"),
    )
    required["PlanningRequiredSlotShare"] = (
        pd.to_numeric(required["TotalRequiredSlots"], errors="coerce").fillna(0)
        / pd.to_numeric(required["SKU_Count"], errors="coerce").replace(0, np.nan)
    ).fillna(0)
    sku = (
        latest[["SKU", "SlotTier", "Velocity"]]
        .merge(state, on="SKU", how="left", validate="one_to_one")
        .merge(
            required[["SlotTier", "PlanningRequiredSlotShare"]],
            on="SlotTier",
            how="left",
            validate="many_to_one",
        )
    )
    sku["RoutingVelocity"] = sku["RoutingVelocity"].fillna(sku["Velocity"])
    sku["RoutingSlotTier"] = replace_tier_suffix(sku["SlotTier"], sku["RoutingVelocity"])
    routing = (
        sku.groupby("RoutingSlotTier", as_index=False)
        .agg(
            CandidatePlanningRequiredSlots=("PlanningRequiredSlotShare", "sum"),
            CandidatePlanningSKUs=("SKU", "nunique"),
        )
        .rename(columns={"RoutingSlotTier": "SlotTier"})
    )
    occupied = (
        inventory.groupby("CurrentZoneId", as_index=False)
        .agg(
            CurrentOccupiedLocations=("Location", "nunique"),
            CurrentPhysicalQty=("PhysicalQty", "sum"),
        )
        .rename(columns={"CurrentZoneId": "SlotTier"})
    )
    fit = (
        capacity.merge(legacy, on="SlotTier", how="outer", validate="one_to_one")
        .merge(routing, on="SlotTier", how="outer", validate="one_to_one")
        .merge(occupied, on="SlotTier", how="outer", validate="one_to_one")
    )
    numeric = [
        "PaintedLocations",
        "PaintedEmptyLocations",
        "PaintedOccupiedLocations",
        "LegacyPlanningRequiredSlots",
        "LegacyPlanningSKUs",
        "CandidatePlanningRequiredSlots",
        "CandidatePlanningSKUs",
        "CurrentOccupiedLocations",
        "CurrentPhysicalQty",
    ]
    fit[numeric] = fit[numeric].fillna(0)
    fit["LegacyPlanningHeadroom"] = fit["PaintedLocations"] - fit["LegacyPlanningRequiredSlots"]
    fit["CandidatePlanningHeadroom"] = fit["PaintedLocations"] - fit["CandidatePlanningRequiredSlots"]
    fit["CandidateRequiredSlotDelta"] = (
        fit["CandidatePlanningRequiredSlots"] - fit["LegacyPlanningRequiredSlots"]
    )
    fit["CandidatePlanningShortfall"] = (-fit["CandidatePlanningHeadroom"]).clip(lower=0)
    return fit.sort_values(
        ["CandidatePlanningShortfall", "CandidateRequiredSlotDelta", "SlotTier"],
        ascending=[False, False, True],
    )


def simulate_budget(
    candidates: pd.DataFrame,
    capacity_fit: pd.DataFrame,
    debt_budget: int | None,
) -> tuple[dict[str, object], pd.DataFrame]:
    planned = capacity_fit.set_index("SlotTier")["LegacyPlanningRequiredSlots"].to_dict()
    painted = capacity_fit.set_index("SlotTier")["PaintedLocations"].to_dict()
    empty = capacity_fit.set_index("SlotTier")["PaintedEmptyLocations"].to_dict()
    reserved: dict[str, int] = {}
    added_debt = 0
    resolved_debt = 0
    accepted: list[pd.Series] = []
    blocked_review = 0
    blocked_missing_paint = 0
    blocked_capacity = 0
    blocked_empty = 0
    blocked_budget = 0
    decisions: list[dict[str, object]] = []
    budget_label = "unbounded" if debt_budget is None else str(debt_budget)

    def record(row: pd.Series, decision: str) -> None:
        decisions.append(
            {
                "AddedDebtLocationBudget": budget_label,
                "SKU": row["SKU"],
                "SlotTier": row["SlotTier"],
                "RoutingSlotTier": row["RoutingSlotTier"],
                "Direction": row["Direction"],
                "ExceptionCohort": row["ExceptionCohort"],
                "BurdenScore": row["BurdenScore"],
                "AddedDebtLocationsIfActivated": row["AddedDebtLocationsIfActivated"],
                "ResolvedDebtLocationsIfActivated": row["ResolvedDebtLocationsIfActivated"],
                "FloorPhysicalQty": row["FloorPhysicalQty"],
                "Decision": decision,
            }
        )

    for _, row in candidates.iterrows():
        if row["RequiresPremiumDemotionReview"]:
            blocked_review += 1
            record(row, "Blocked: premium demotion review")
            continue
        if row["RoutingPaintedLocations"] <= 0:
            blocked_missing_paint += 1
            record(row, "Blocked: Routing SlotTier has no painted locations")
            continue
        source = row["SlotTier"]
        target = row["RoutingSlotTier"]
        slot_share = float(row["PlanningRequiredSlotShare"])
        target_before = float(planned.get(target, 0))
        target_after = target_before + slot_share
        target_capacity = float(painted.get(target, 0))
        before_shortfall = max(target_before - target_capacity, 0)
        after_shortfall = max(target_after - target_capacity, 0)
        if after_shortfall > before_shortfall + 1e-9:
            blocked_capacity += 1
            record(row, "Blocked: exact SlotTier planning capacity")
            continue
        reservation = int(row["ActivationSlotReservation"])
        available_empty = int(empty.get(target, 0)) - int(reserved.get(target, 0))
        if reservation > available_empty:
            blocked_empty += 1
            record(row, "Blocked: empty-location reservation")
            continue
        candidate_added = int(row["AddedDebtLocationsIfActivated"])
        if debt_budget is not None and added_debt + candidate_added > debt_budget:
            blocked_budget += 1
            record(row, "Blocked: added-debt budget")
            continue
        planned[source] = float(planned.get(source, 0)) - slot_share
        planned[target] = target_after
        reserved[target] = int(reserved.get(target, 0)) + reservation
        added_debt += candidate_added
        resolved_debt += int(row["ResolvedDebtLocationsIfActivated"])
        accepted.append(row)
        record(row, "Accepted")

    accepted_frame = pd.DataFrame(accepted)
    if accepted_frame.empty:
        promotions = demotions = changed_skus = demand_touches = pick_lines = 0
    else:
        promotions = int(accepted_frame["Direction"].eq("Promotion").sum())
        demotions = int(accepted_frame["Direction"].eq("Demotion").sum())
        changed_skus = int(accepted_frame["SKU"].nunique())
        demand_touches = float(accepted_frame["Recent56dDemandPhysicalTouches"].sum())
        pick_lines = float(accepted_frame["Recent56dPickLines"].sum())
    return {
        "AddedDebtLocationBudget": budget_label,
        "AcceptedRoutingChanges": len(accepted),
        "AcceptedDistinctSKUs": changed_skus,
        "AcceptedPromotions": promotions,
        "AcceptedDemotions": demotions,
        "AddedDebtLocations": added_debt,
        "ResolvedDebtLocations": resolved_debt,
        "NetDebtLocationDelta": added_debt - resolved_debt,
        "Recent56dDemandTouchesCovered": demand_touches,
        "Recent56dPickLinesCovered": pick_lines,
        "BlockedPremiumDemotionReview": blocked_review,
        "BlockedMissingPaintedSlotTier": blocked_missing_paint,
        "BlockedExactSlotTierPlanningCapacity": blocked_capacity,
        "BlockedEmptyLocationReservation": blocked_empty,
        "BlockedDebtBudget": blocked_budget,
    }, pd.DataFrame(decisions)


def build_budget_summary(
    candidates: pd.DataFrame,
    capacity_fit: pd.DataFrame,
    budgets: list[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    results = [simulate_budget(candidates, capacity_fit, budget) for budget in sorted(set(budgets))]
    results.append(simulate_budget(candidates, capacity_fit, None))
    rows, decisions = zip(*results, strict=True)
    return pd.DataFrame(rows), pd.concat(decisions, ignore_index=True)


def build_cohort_summary(candidates: pd.DataFrame) -> pd.DataFrame:
    return (
        candidates.groupby(["ExceptionCohort", "Direction"], as_index=False)
        .agg(
            CandidateRoutingChanges=("SKU", "nunique"),
            PremiumDemotionReviewSKUs=("RequiresPremiumDemotionReview", "sum"),
            FloorOccupiedLocations=("FloorOccupiedLocations", "sum"),
            FloorPhysicalQty=("FloorPhysicalQty", "sum"),
            AddedDebtLocationsIfActivated=("AddedDebtLocationsIfActivated", "sum"),
            ResolvedDebtLocationsIfActivated=("ResolvedDebtLocationsIfActivated", "sum"),
            Recent56dDemandPhysicalTouches=("Recent56dDemandPhysicalTouches", "sum"),
            Recent56dPickLines=("Recent56dPickLines", "sum"),
        )
        .sort_values(["CandidateRoutingChanges", "ExceptionCohort"], ascending=[False, True])
    )


def build_premium_review(candidates: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "SKU",
        "SlotTier",
        "RoutingSlotTier",
        "Velocity",
        "RoutingVelocity",
        "VelocityTierSteps",
        "ExceptionCohort",
        "BurdenScore",
        "FloorPremiumOccupiedLocations",
        "FloorOccupiedLocations",
        "FloorPhysicalQty",
        "PremiumDemotionStepProxyIfActivated",
        "AddedDebtLocationsIfActivated",
        "ResolvedDebtLocationsIfActivated",
        "Recent56dDemandPhysicalTouches",
        "Recent56dPickLines",
        "ForecastFD14Units",
        "ForecastFD14Min",
        "ForecastFD14Max",
        "ProductStatus",
        "ProductStage",
        "ReturnAction",
    ]
    return (
        candidates[candidates["RequiresPremiumDemotionReview"]]
        .sort_values(
            [
                "PremiumDemotionStepProxyIfActivated",
                "FloorPhysicalQty",
                "FloorPremiumOccupiedLocations",
                "BurdenScore",
            ],
            ascending=[False, False, False, False],
        )[columns]
        .reset_index(drop=True)
    )


def write_outputs(
    args: argparse.Namespace,
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    candidates: pd.DataFrame,
    budget_decisions: pd.DataFrame,
    budget_summary: pd.DataFrame,
    capacity_fit: pd.DataFrame,
    cohort_summary: pd.DataFrame,
    premium_review: pd.DataFrame,
) -> None:
    payload: dict[str, object] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "candidate": args.candidate,
        "capacity_envelope": args.capacity_envelope,
        "policy": args.policy,
        "inputs": {
            name: {"path": relative(path), "sha256": sha256(path)}
            for name, path in {
                "detail": args.detail,
                "state": args.state,
                "inventory": args.inventory,
                "required_slots": args.required_slots,
                "fwd_demand": args.fwd_demand,
                "deployed_map": args.deployed_map,
                "location_master": args.location_master,
            }.items()
        },
        "outputs": {},
        "rows": {
            "candidates": len(candidates),
            "budget_decisions": len(budget_decisions),
            "budget_summary": len(budget_summary),
            "capacity_fit": len(capacity_fit),
            "cohort_summary": len(cohort_summary),
            "premium_review": len(premium_review),
        },
        "definitions": {
            "signal_tier": "The enriched weekly analytical recommendation.",
            "routing_tier": "The stateful operational shadow tier after confirmation and staged-demotion controls.",
            "planning_required_slot_share": "June 1 RequiredSlots total divided equally across SKUs in the inherited SlotTier. This is a proxy because SKU-level planning slots are not exported.",
            "added_debt_location_budget": "Maximum occupied locations that match the inherited velocity but would become velocity debt if the routing change activated.",
            "premium_demotion_review": "A demotion candidate with stock occupying at least one AA or A floor location. It is excluded from automatic activation.",
        },
        "limitations": [
            "This is an offline ranking and capacity diagnostic, not an AX upload or a deployable map.",
            "RequiredSlots is exported at SlotTier level, so per-SKU planning pressure is an equal-share proxy within each inherited SlotTier.",
            "One empty painted location is conservatively reserved for a changed SKU unless stock is already observed at its routing velocity.",
            "Inventory disappearance and empty-location availability do not prove natural picking depletion.",
            "Lifecycle and seasonality cohorts are diagnostic proxies from the observed five-snapshot window and June 1 product fields.",
            "Cube, PalletPicking manual exceptions, labor standards, and score-margin hysteresis still require separate calibration before deployment.",
        ],
    }
    try:
        candidates.to_parquet(temporary["candidates"], index=False, compression="zstd")
        budget_decisions.to_parquet(
            temporary["budget_decisions"],
            index=False,
            compression="zstd",
        )
        budget_summary.to_csv(temporary["budget_summary"], index=False)
        capacity_fit.to_csv(temporary["capacity_fit"], index=False)
        cohort_summary.to_csv(temporary["cohort_summary"], index=False)
        premium_review.to_csv(temporary["premium_review"], index=False)
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
    latest, history = load_selected_detail(args)
    state = load_latest_state(args)
    inventory = load_inventory(args.inventory)
    capacity = build_painted_capacity(args, inventory)
    candidates = build_candidates(args, latest, history, state, inventory, capacity)
    capacity_fit = build_capacity_fit(latest, state, args.required_slots, inventory, capacity)
    budget_summary, budget_decisions = build_budget_summary(candidates, capacity_fit, args.budgets)
    cohort_summary = build_cohort_summary(candidates)
    premium_review = build_premium_review(candidates)
    write_outputs(
        args,
        outputs,
        temporary,
        candidates,
        budget_decisions,
        budget_summary,
        capacity_fit,
        cohort_summary,
        premium_review,
    )
    print("Incremental activation budget replay:")
    print(budget_summary.to_string(index=False))
    print("\nExact SlotTier capacity shortfalls:")
    shortfall = capacity_fit[capacity_fit["CandidatePlanningShortfall"].gt(0)]
    print(shortfall.head(20).to_string(index=False))
    print(f"\nPremium demotion review SKUs: {len(premium_review):,}")
    print(f"Incremental activation outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
