"""Apply routing-stability controls to an enriched shadow velocity candidate.

The selected candidate remains dynamic, but its operational routing tier
changes only when the experimental control allows it. Nothing writes to AX,
ingestion, approved maps, or location directives.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHADOW_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
REPLAY_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
DETAIL_PATH = SHADOW_DIR / "velocity_policy_enriched_candidate_sku_snapshot.parquet"
INVENTORY_PATH = REPLAY_DIR / "sku_location_inventory_snapshots.parquet"
OUTPUT_DIR = SHADOW_DIR
TIER_RANK = {"C": 0, "B": 1, "A": 2, "AA": 3}
RANK_TIER = {rank: tier for tier, rank in TIER_RANK.items()}
PREMIUM_TIERS = {"AA", "A"}


@dataclass(frozen=True)
class Control:
    name: str
    promotion_confirmations: int
    demotion_confirmations: int
    stage_demotions: bool


CONTROLS = (
    Control("enriched_immediate", 1, 1, False),
    Control("enriched_promo1_demo2", 1, 2, False),
    Control("enriched_promo1_demo2_staged", 1, 2, True),
    Control("enriched_promo1_demo3_staged", 1, 3, True),
    Control("enriched_promo2_demo3_staged", 2, 3, True),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detail", type=Path, default=DETAIL_PATH)
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--candidate", default="demand_and_pick")
    parser.add_argument("--capacity-envelope", default="legacy_sku_population_proxy")
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
        "events": output_dir / "velocity_policy_enriched_stability_events.parquet",
        "state": output_dir / "velocity_policy_enriched_stability_sku_snapshot.parquet",
        "summary": output_dir / "velocity_policy_enriched_stability_summary.csv",
        "metadata": output_dir / "velocity_policy_enriched_stability_metadata.json",
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Enriched stability artifacts already exist. Pass --overwrite to replace them: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}
    for path in temporary.values():
        if path.exists():
            path.unlink()
    return outputs, temporary


def direction(old_tier: str, new_tier: str) -> str:
    delta = TIER_RANK[new_tier] - TIER_RANK[old_tier]
    if delta > 0:
        return "Promotion"
    if delta < 0:
        return "Demotion"
    return "Unchanged"


def required_confirmations(control: Control, old_tier: str, target_tier: str) -> int:
    return (
        control.promotion_confirmations
        if direction(old_tier, target_tier) == "Promotion"
        else control.demotion_confirmations
    )


def routing_tier(control: Control, old_tier: str, target_tier: str) -> str:
    if not control.stage_demotions or direction(old_tier, target_tier) != "Demotion":
        return target_tier
    return RANK_TIER[max(TIER_RANK[old_tier] - 1, TIER_RANK[target_tier])]


def replay(
    control: Control,
    snapshots: list[tuple[pd.Timestamp, pd.DataFrame]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    state: dict[str, str] = {}
    pending_target: dict[str, str] = {}
    pending_count: dict[str, int] = {}
    event_rows: list[dict[str, object]] = []
    state_frames: list[pd.DataFrame] = []

    for effective, snapshot in snapshots:
        target = snapshot.set_index("SKU")["CandidateVelocity"]
        for sku, target_tier in target.items():
            if sku not in state:
                state[sku] = target_tier
                continue
            old_tier = state[sku]
            if target_tier == old_tier:
                pending_target.pop(sku, None)
                pending_count.pop(sku, None)
                continue
            if pending_target.get(sku) == target_tier:
                pending_count[sku] = pending_count.get(sku, 0) + 1
            else:
                pending_target[sku] = target_tier
                pending_count[sku] = 1
            if pending_count[sku] < required_confirmations(control, old_tier, target_tier):
                continue
            new_tier = routing_tier(control, old_tier, target_tier)
            state[sku] = new_tier
            event_rows.append(
                {
                    "Policy": control.name,
                    "SnapshotEffectiveEST": effective,
                    "SKU": sku,
                    "OldRoutingVelocity": old_tier,
                    "CandidateTargetVelocity": target_tier,
                    "NewRoutingVelocity": new_tier,
                    "Direction": direction(old_tier, new_tier),
                    "TierSteps": abs(TIER_RANK[new_tier] - TIER_RANK[old_tier]),
                    "IsAAtoC": old_tier == "AA" and new_tier == "C",
                    "IsCtoAA": old_tier == "C" and new_tier == "AA",
                }
            )
            if new_tier == target_tier:
                pending_target.pop(sku, None)
                pending_count.pop(sku, None)

        frame = snapshot[
            [
                "SnapshotEffectiveEST",
                "SKU",
                "CandidateVelocity",
                "OutcomeIntervalDays",
                "OutcomeToNextSnapshotDemandPhysicalTouches",
            ]
        ].copy()
        frame["Policy"] = control.name
        frame["RoutingVelocity"] = frame["SKU"].map(state)
        state_frames.append(frame)

    return pd.DataFrame(event_rows), pd.concat(state_frames, ignore_index=True)


def tier_suffix(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper().str.extract(
        r"(AA|A|B|C)$",
        expand=False,
    )


def latest_map_debt(state: pd.DataFrame, inventory: pd.DataFrame) -> dict[str, float]:
    latest_inventory = inventory[inventory["SnapshotDate"].eq(inventory["SnapshotDate"].max())].copy()
    latest_state = state[state["SnapshotEffectiveEST"].eq(state["SnapshotEffectiveEST"].max())]
    routing = latest_state.set_index("SKU")["RoutingVelocity"]
    latest_inventory["RoutingVelocity"] = latest_inventory["SKU"].map(routing)
    latest_inventory["CurrentLocationVelocity"] = tier_suffix(latest_inventory["CurrentZoneId"])
    current_rank = latest_inventory["CurrentLocationVelocity"].map(TIER_RANK)
    routing_rank = latest_inventory["RoutingVelocity"].map(TIER_RANK)
    known = current_rank.notna() & routing_rank.notna()
    debt = known & current_rank.ne(routing_rank)
    demotion = known & current_rank.gt(routing_rank)
    promotion = known & current_rank.lt(routing_rank)
    premium_demotion = demotion & latest_inventory["CurrentLocationVelocity"].isin(PREMIUM_TIERS)
    return {
        "LatestVelocityDebtLocations": int(debt.sum()),
        "LatestDemotionDebtLocations": int(demotion.sum()),
        "LatestPromotionDebtLocations": int(promotion.sum()),
        "LatestDemotionPremiumLocationStepProxy": float(
            (current_rank - routing_rank).where(premium_demotion, 0).sum()
        ),
        "LatestVelocityDebtPhysicalQty": float(latest_inventory["PhysicalQty"].where(debt, 0).sum()),
    }


def summarize(
    controls: tuple[Control, ...],
    events: pd.DataFrame,
    state: pd.DataFrame,
    inventory: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for control in controls:
        scoped_events = events[events["Policy"].eq(control.name)]
        scoped_state = state[state["Policy"].eq(control.name)]
        evaluable = scoped_state[scoped_state["OutcomeIntervalDays"].notna()].copy()
        outcome = evaluable["OutcomeToNextSnapshotDemandPhysicalTouches"].fillna(0)
        total = float(outcome.sum())
        premium = evaluable["RoutingVelocity"].isin(PREMIUM_TIERS)
        aa = evaluable["RoutingVelocity"].eq("AA")
        latest = scoped_state[scoped_state["SnapshotEffectiveEST"].eq(
            scoped_state["SnapshotEffectiveEST"].max()
        )]
        rows.append(
            {
                "Policy": control.name,
                "PromotionConfirmations": control.promotion_confirmations,
                "DemotionConfirmations": control.demotion_confirmations,
                "StageDemotions": control.stage_demotions,
                "AppliedRoutingChanges": len(scoped_events),
                "UniqueSKUsChanged": int(scoped_events["SKU"].nunique()),
                "AppliedMultiTierJumps": int(scoped_events["TierSteps"].gt(1).sum()),
                "AppliedAAtoC": int(scoped_events["IsAAtoC"].sum()),
                "AAorAOutcomeDemandTouchCapturePct": round(100 * outcome[premium].sum() / total, 2)
                if total
                else 0,
                "AAOutcomeDemandTouchCapturePct": round(100 * outcome[aa].sum() / total, 2)
                if total
                else 0,
                "FinalTargetDifferences": int(
                    (latest["RoutingVelocity"] != latest["CandidateVelocity"]).sum()
                ),
                **latest_map_debt(scoped_state, inventory),
            }
        )
    return pd.DataFrame(rows)


def write_outputs(
    args: argparse.Namespace,
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    events: pd.DataFrame,
    state: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    payload: dict[str, object] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "candidate": args.candidate,
        "capacity_envelope": args.capacity_envelope,
        "inputs": {
            "detail": {"path": relative(args.detail), "sha256": sha256(args.detail)},
            "inventory": {"path": relative(args.inventory), "sha256": sha256(args.inventory)},
        },
        "outputs": {},
        "rows": {"events": len(events), "state": len(state), "summary": len(summary)},
        "limitations": [
            "Only five confirmed forecast snapshots and six inventory snapshots are available.",
            "The selected capacity envelope is a ranking proxy, not a deployable SlotTier fit proof.",
            "Confirmation controls can delay legitimate changes as well as avoidable churn.",
            "Score-margin hysteresis still needs calibration after score boundaries are selected.",
        ],
    }
    try:
        events.to_parquet(temporary["events"], index=False, compression="zstd")
        state.to_parquet(temporary["state"], index=False, compression="zstd")
        summary.to_csv(temporary["summary"], index=False)
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
    detail = pd.read_parquet(args.detail)
    detail["SnapshotEffectiveEST"] = pd.to_datetime(detail["SnapshotEffectiveEST"], utc=True)
    selected = detail[
        detail["Candidate"].eq(args.candidate)
        & detail["CapacityEnvelope"].eq(args.capacity_envelope)
    ].copy()
    if selected.empty:
        raise ValueError("Selected candidate and capacity envelope returned no rows.")
    snapshots = list(selected.groupby("SnapshotEffectiveEST", sort=True))
    inventory = pd.read_parquet(args.inventory)
    inventory["SnapshotDate"] = pd.to_datetime(inventory["SnapshotDate"])
    inventory["PhysicalQty"] = pd.to_numeric(inventory["PhysicalQty"], errors="coerce").fillna(0)

    event_frames: list[pd.DataFrame] = []
    state_frames: list[pd.DataFrame] = []
    for control in CONTROLS:
        events, state = replay(control, snapshots)
        event_frames.append(events)
        state_frames.append(state)
    events = pd.concat(event_frames, ignore_index=True)
    state = pd.concat(state_frames, ignore_index=True)
    summary = summarize(CONTROLS, events, state, inventory)
    write_outputs(args, outputs, temporary, events, state, summary)
    print(summary.to_string(index=False))
    print(f"\nEnriched stability outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
