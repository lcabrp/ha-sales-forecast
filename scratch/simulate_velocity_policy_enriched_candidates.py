"""Backtest portable category-weight and capacity-ranked velocity candidates.

This experiment never changes ingestion, AX files, approved maps, or location
directives. Capacity envelopes are directional ranking tests, not deployable
slot-fit maps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHADOW_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
REPLAY_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
PANEL = SHADOW_DIR / "velocity_policy_sku_snapshot_panel.parquet"
TRANSITIONS = SHADOW_DIR / "velocity_policy_transition_events.parquet"
CASE_QTY = REPLAY_DIR / "planning_case_qty_history.parquet"
INVENTORY = REPLAY_DIR / "sku_location_inventory_snapshots.parquet"
DIRECT_PICK = REPLAY_DIR / "direct_pick_sku_day_15mo.parquet"
CAPACITY = SHADOW_DIR / "velocity_policy_capacity_reference.csv"
OUTPUT_DIR = SHADOW_DIR
MINMAX_PAUSE_ASSUMED_EST = pd.Timestamp("2026-05-14 00:00:00", tz="America/New_York")
TIER_ORDER = ("AA", "A", "B", "C")


@dataclass(frozen=True)
class Candidate:
    name: str
    forecast: float
    direct_pick: float
    demand: float
    minmax: float
    reset: float


CANDIDATES = (
    Candidate("forecast_cartons_only", 1.0, 0.0, 0.0, 0.0, 0.0),
    Candidate("demand_and_pick", 0.75, 0.75, 1.0, 0.0, 0.0),
    Candidate("all_categories_equal", 0.75, 0.75, 1.0, 1.0, 1.0),
    Candidate("minmax_diagnostic_downweighted", 0.75, 0.75, 1.0, 0.25, 0.50),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=PANEL)
    parser.add_argument("--transitions", type=Path, default=TRANSITIONS)
    parser.add_argument("--case-qty", type=Path, default=CASE_QTY)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--direct-pick", type=Path, default=DIRECT_PICK)
    parser.add_argument("--capacity", type=Path, default=CAPACITY)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(series: pd.Series) -> pd.Series:
    return series.fillna(0).rank(method="average", pct=True)


def latest_asof(frame: pd.DataFrame, date_column: str, effective: pd.Timestamp) -> pd.DataFrame:
    dates = pd.to_datetime(frame[date_column])
    eligible = dates[dates <= effective.tz_localize(None)]
    if eligible.empty:
        return frame.iloc[0:0].copy()
    latest = eligible.max()
    return frame.loc[dates.eq(latest)].copy()


def aggregate_direct_pick(picks: pd.DataFrame, effective_utc: pd.Timestamp) -> pd.DataFrame:
    start = effective_utc.tz_localize(None) - pd.Timedelta(days=56)
    end = effective_utc.tz_localize(None)
    scoped = picks[(picks["PickDate"] >= start) & (picks["PickDate"] < end)]
    return (
        scoped.groupby("SKU", as_index=False)
        .agg(Recent56dPickLines=("PickLines", "sum"), Recent56dPickUnits=("PickUnits", "sum"), Recent56dActivePickDays=("PickDate", "nunique"))
    )


def aggregate_inventory(inventory: pd.DataFrame, effective_utc: pd.Timestamp) -> pd.DataFrame:
    scoped = latest_asof(inventory, "SnapshotDate", effective_utc)
    if scoped.empty:
        return pd.DataFrame(columns=["SKU", "InventorySnapshotDate", "OccupiedLocations", "InventoryPhysicalQty", "PremiumOccupiedLocations"])
    scoped["IsPremiumLocation"] = scoped["CurrentZoneId"].str.endswith(("AA", "A"))
    output = (
        scoped.groupby("SKU", as_index=False)
        .agg(
            InventorySnapshotDate=("SnapshotDate", "first"),
            OccupiedLocations=("Location", "nunique"),
            InventoryPhysicalQty=("PhysicalQty", "sum"),
            PremiumOccupiedLocations=("IsPremiumLocation", "sum"),
        )
    )
    return output


def asof_case_qty(case_qty: pd.DataFrame, effective_utc: pd.Timestamp) -> pd.DataFrame:
    scoped = latest_asof(case_qty, "WorkbookSnapshotDate", effective_utc)
    return scoped[["SKU", "WorkbookSnapshotDate", "CaseQty", "CaseQtySource"]].copy()


def envelope_quotas(panel: pd.DataFrame, capacity: pd.DataFrame) -> dict[str, dict[str, int]]:
    first_date = panel["SnapshotEffectiveEST"].min()
    first = panel[panel["SnapshotEffectiveEST"].eq(first_date)]
    legacy = first["Velocity"].value_counts().reindex(TIER_ORDER, fill_value=0).astype(int).to_dict()
    location = capacity.set_index("Velocity")["FrozenCutoverLocationCount"].to_dict()
    return {
        "legacy_sku_population_proxy": {tier: int(legacy.get(tier, 0)) for tier in TIER_ORDER},
        "one_slot_location_upper_bound": {
            "AA": int(location.get("AA", 0)),
            "A": int(location.get("A", 0)),
            "B": int(location.get("B", 0)),
            "C": 10**9,
        },
    }


def assign_ranked_tiers(frame: pd.DataFrame, score: str, quotas: dict[str, int]) -> pd.Series:
    ordered = frame.sort_values([score, "SKU"], ascending=[False, True])
    values = np.full(len(ordered), "C", dtype=object)
    cursor = 0
    for tier in ("AA", "A", "B"):
        end = min(cursor + quotas[tier], len(values))
        values[cursor:end] = tier
        cursor = end
    return pd.Series(values, index=ordered.index).reindex(frame.index)


def candidate_frame(
    snapshot: pd.DataFrame,
    case_qty: pd.DataFrame,
    inventory: pd.DataFrame,
    picks: pd.DataFrame,
    candidate: Candidate,
    envelope: str,
    quotas: dict[str, int],
) -> pd.DataFrame:
    effective = pd.Timestamp(snapshot["SnapshotEffectiveEST"].iloc[0])
    effective_utc = effective.tz_convert("UTC")
    frame = snapshot.copy()
    frame = frame.merge(asof_case_qty(case_qty, effective_utc), on="SKU", how="left")
    frame = frame.merge(aggregate_inventory(inventory, effective_utc), on="SKU", how="left")
    frame = frame.merge(aggregate_direct_pick(picks, effective_utc), on="SKU", how="left")
    numeric = [
        "CaseQty",
        "OccupiedLocations",
        "InventoryPhysicalQty",
        "PremiumOccupiedLocations",
        "Recent56dPickLines",
        "Recent56dPickUnits",
        "Recent56dActivePickDays",
        "Recent56dDemandPhysicalTouches",
        "Recent56dMinMaxPhysicalTouches",
        "Recent56dResetPhysicalTouches",
    ]
    frame[numeric] = frame[numeric].fillna(0)
    frame["CaseQty"] = frame["CaseQty"].replace(0, np.nan).fillna(36)
    frame["PlanningForecastCartonsPerDay"] = frame["ForecastFD14Units"] / frame["CaseQty"] / 14.0
    frame["ForecastRank"] = percentile(frame["PlanningForecastCartonsPerDay"])
    frame["DirectPickRank"] = percentile(frame["Recent56dPickLines"])
    frame["DemandTouchRank"] = percentile(frame["Recent56dDemandPhysicalTouches"])
    frame["MinMaxTouchRank"] = percentile(frame["Recent56dMinMaxPhysicalTouches"])
    frame["ResetTouchRank"] = percentile(frame["Recent56dResetPhysicalTouches"])
    frame["BurdenScore"] = (
        candidate.forecast * frame["ForecastRank"]
        + candidate.direct_pick * frame["DirectPickRank"]
        + candidate.demand * frame["DemandTouchRank"]
        + candidate.minmax * frame["MinMaxTouchRank"]
        + candidate.reset * frame["ResetTouchRank"]
    )
    frame["CandidateVelocity"] = assign_ranked_tiers(frame, "BurdenScore", quotas)
    frame["Candidate"] = candidate.name
    frame["CapacityEnvelope"] = envelope
    frame["DecisionAfterAssumedMinMaxPause"] = effective >= MINMAX_PAUSE_ASSUMED_EST
    return frame


def summarize(detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (candidate, envelope), scoped in detail.groupby(["Candidate", "CapacityEnvelope"]):
        changes = 0
        previous: pd.Series | None = None
        for _effective, snapshot in scoped.groupby("SnapshotEffectiveEST", sort=True):
            current = snapshot.set_index("SKU")["CandidateVelocity"]
            if previous is not None:
                shared = previous.index.intersection(current.index)
                changes += int((previous.reindex(shared) != current.reindex(shared)).sum())
            previous = current
        evaluable = scoped[scoped["OutcomeIntervalDays"].notna()].copy()
        premium = evaluable[evaluable["CandidateVelocity"].isin(["AA", "A"])]
        aa = evaluable[evaluable["CandidateVelocity"].eq("AA")]
        total_demand = evaluable["OutcomeToNextSnapshotDemandPhysicalTouches"].sum()
        rows.append(
            {
                "Candidate": candidate,
                "CapacityEnvelope": envelope,
                "AdjacentCandidateTierChanges": changes,
                "AAorAOutcomeDemandTouchCapturePct": round(100 * premium["OutcomeToNextSnapshotDemandPhysicalTouches"].sum() / total_demand, 2) if total_demand else 0,
                "AAOutcomeDemandTouchCapturePct": round(100 * aa["OutcomeToNextSnapshotDemandPhysicalTouches"].sum() / total_demand, 2) if total_demand else 0,
                "AAorARecentPickLineCapturePct": round(100 * premium["Recent56dPickLines"].sum() / evaluable["Recent56dPickLines"].sum(), 2) if evaluable["Recent56dPickLines"].sum() else 0,
                "AAorAPremiumOccupiedLocations": int(premium["PremiumOccupiedLocations"].sum()),
                "FinalSnapshotDifferencesVsLegacy": int(
                    (scoped[scoped["SnapshotEffectiveEST"].eq(scoped["SnapshotEffectiveEST"].max())]["CandidateVelocity"]
                     != scoped[scoped["SnapshotEffectiveEST"].eq(scoped["SnapshotEffectiveEST"].max())]["Velocity"]).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def build_observed_transition_inventory_burden(
    transitions: pd.DataFrame,
    inventory: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    for old_effective, scoped in transitions.groupby("OldSnapshotEffectiveEST", sort=True):
        effective_utc = pd.Timestamp(old_effective).tz_convert("UTC")
        occupied = aggregate_inventory(inventory, effective_utc).rename(columns={"SKU": "OldSKU"})
        frames.append(scoped.merge(occupied, on="OldSKU", how="left"))
    detail = pd.concat(frames, ignore_index=True)
    numeric = ["OccupiedLocations", "InventoryPhysicalQty", "PremiumOccupiedLocations"]
    detail[numeric] = detail[numeric].fillna(0)
    demotion = detail["Direction"].eq("Demotion")
    detail["DemotionPremiumLocationStepProxy"] = np.where(
        demotion,
        detail["PremiumOccupiedLocations"] * detail["TierSteps"],
        0,
    )
    detail["DemotionPhysicalQtyProxy"] = np.where(demotion, detail["InventoryPhysicalQty"], 0)
    summary = (
        detail.groupby(["OldSnapshotEffectiveEST", "NewSnapshotEffectiveEST"], as_index=False)
        .agg(
            ObservedVelocityChanges=("OldSKU", "size"),
            Demotions=("Direction", lambda values: values.eq("Demotion").sum()),
            DirectAAtoC=("IsAAtoC", "sum"),
            DemotionPremiumLocationStepProxy=("DemotionPremiumLocationStepProxy", "sum"),
            DemotionPhysicalQtyProxy=("DemotionPhysicalQtyProxy", "sum"),
        )
    )
    return detail, summary


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "detail": args.output_dir / "velocity_policy_enriched_candidate_sku_snapshot.parquet",
        "summary": args.output_dir / "velocity_policy_enriched_candidate_summary.csv",
        "transition_burden": args.output_dir / "velocity_policy_observed_transition_inventory_burden.parquet",
        "transition_burden_summary": args.output_dir / "velocity_policy_observed_transition_inventory_burden_summary.csv",
        "metadata": args.output_dir / "velocity_policy_enriched_candidate_metadata.json",
    }
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError("Pass --overwrite to replace: " + ", ".join(str(path) for path in existing))

    panel = pd.read_parquet(args.panel)
    panel["SnapshotEffectiveEST"] = pd.to_datetime(panel["SnapshotEffectiveEST"], utc=True)
    transitions = pd.read_parquet(args.transitions)
    case_qty = pd.read_parquet(args.case_qty)
    inventory = pd.read_parquet(args.inventory)
    picks = pd.read_parquet(args.direct_pick)
    picks["PickDate"] = pd.to_datetime(picks["PickDate"])
    capacity = pd.read_csv(args.capacity)
    quotas = envelope_quotas(panel, capacity)
    frames: list[pd.DataFrame] = []
    for _effective, snapshot in panel.groupby("SnapshotEffectiveEST", sort=True):
        for candidate in CANDIDATES:
            for envelope, envelope_quota in quotas.items():
                frames.append(candidate_frame(snapshot, case_qty, inventory, picks, candidate, envelope, envelope_quota))
    detail = pd.concat(frames, ignore_index=True)
    summary = summarize(detail)
    transition_burden, transition_burden_summary = build_observed_transition_inventory_burden(
        transitions,
        inventory,
    )

    detail_tmp = outputs["detail"].with_name(f"{outputs['detail'].name}.tmp")
    summary_tmp = outputs["summary"].with_name(f"{outputs['summary'].name}.tmp")
    transition_burden_tmp = outputs["transition_burden"].with_name(
        f"{outputs['transition_burden'].name}.tmp"
    )
    transition_burden_summary_tmp = outputs["transition_burden_summary"].with_name(
        f"{outputs['transition_burden_summary'].name}.tmp"
    )
    metadata_tmp = outputs["metadata"].with_name(f"{outputs['metadata'].name}.tmp")
    detail.to_parquet(detail_tmp, index=False, compression="zstd")
    summary.to_csv(summary_tmp, index=False)
    transition_burden.to_parquet(transition_burden_tmp, index=False, compression="zstd")
    transition_burden_summary.to_csv(transition_burden_summary_tmp, index=False)
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "assumptions": {
            "minmax_pause_assumed_est": MINMAX_PAUSE_ASSUMED_EST.isoformat(),
            "minmax_pause_basis": "Inferred: daily monitor shows Forward Replen on 2026-05-13 and none afterward through 2026-05-31. This is not the exact operator command timestamp.",
            "capacity_envelopes": {
                "legacy_sku_population_proxy": "Preserves first confirmed snapshot SKU tier populations.",
                "one_slot_location_upper_bound": "Directional stress test only: assumes at least one location per premium SKU. It is not a deployable fit proof.",
            },
        },
        "inputs": {
            name: {"path": relative(path), "sha256": sha256(path)}
            for name, path in {
                "panel": args.panel,
                "transitions": args.transitions,
                "case_qty": args.case_qty,
                "inventory": args.inventory,
                "direct_pick": args.direct_pick,
                "capacity": args.capacity,
            }.items()
        },
        "outputs": {
            "detail": {"path": relative(outputs["detail"]), "bytes": detail_tmp.stat().st_size, "sha256": sha256(detail_tmp)},
            "summary": {"path": relative(outputs["summary"]), "bytes": summary_tmp.stat().st_size, "sha256": sha256(summary_tmp)},
            "transition_burden": {"path": relative(outputs["transition_burden"]), "bytes": transition_burden_tmp.stat().st_size, "sha256": sha256(transition_burden_tmp)},
            "transition_burden_summary": {"path": relative(outputs["transition_burden_summary"]), "bytes": transition_burden_summary_tmp.stat().st_size, "sha256": sha256(transition_burden_summary_tmp)},
        },
        "rows": {
            "detail": len(detail),
            "summary": len(summary),
            "observed_transition_inventory_burden": len(transition_burden),
        },
        "limitations": [
            "Only five confirmed AX-effective forecast snapshots are available.",
            "MinMax was paused after cutover; MinMax history is diagnostic and must not be treated as a uniform production outcome.",
            "Capacity envelopes rank candidates but do not perform a SlotTier-level physical fit allocation.",
            "Lifecycle exception flags are not yet available as a structured source.",
        ],
    }
    metadata_tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    detail_tmp.replace(outputs["detail"])
    summary_tmp.replace(outputs["summary"])
    transition_burden_tmp.replace(outputs["transition_burden"])
    transition_burden_summary_tmp.replace(outputs["transition_burden_summary"])
    metadata_tmp.replace(outputs["metadata"])
    print(summary.to_string(index=False))
    print(f"Enriched shadow candidate outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
