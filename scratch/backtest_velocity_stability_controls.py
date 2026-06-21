"""Backtest shadow routing-tier stability controls against confirmed snapshots.

The confirmed forecast velocity remains the signal. Each experimental policy
maintains a separate shadow routing tier and decides when that operational tier
may change. Nothing in this script writes to ingestion, AX, or approved maps.
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
PANEL_PATH = SHADOW_DIR / "velocity_policy_sku_snapshot_panel.parquet"
OUTPUT_DIR = SHADOW_DIR
TIER_RANK = {"C": 0, "B": 1, "A": 2, "AA": 3}
RANK_TIER = {rank: tier for tier, rank in TIER_RANK.items()}
TIER_ORDER = ("AA", "A", "B", "C")

EVENTS_NAME = "velocity_policy_stability_events.parquet"
INTERVAL_NAME = "velocity_policy_stability_interval_summary.csv"
POLICY_NAME = "velocity_policy_stability_policy_summary.csv"
METADATA_NAME = "velocity_policy_stability_metadata.json"


@dataclass(frozen=True)
class Control:
    name: str
    promotion_confirmations: int
    demotion_confirmations: int
    stage_demotions: bool


CONTROLS = (
    Control("legacy_immediate", 1, 1, False),
    Control("two_confirmation", 2, 2, False),
    Control("three_confirmation", 3, 3, False),
    Control("asymmetric_promo1_demo2", 1, 2, False),
    Control("asymmetric_promo1_demo2_staged", 1, 2, True),
    Control("asymmetric_promo2_demo3_staged", 2, 3, True),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing shadow backtest artifacts intentionally.",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "events": output_dir / EVENTS_NAME,
        "interval_summary": output_dir / INTERVAL_NAME,
        "policy_summary": output_dir / POLICY_NAME,
        "metadata": output_dir / METADATA_NAME,
    }


def temporary_paths(outputs: dict[str, Path]) -> dict[str, Path]:
    return {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}


def remove_existing(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Stability artifacts already exist. Pass --overwrite to replace them intentionally: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = temporary_paths(outputs)
    remove_existing(list(temporary.values()))
    return outputs, temporary


def load_panel(path: Path) -> pd.DataFrame:
    panel = pd.read_parquet(path)
    required = {
        "SnapshotEffectiveEST",
        "SKU",
        "Velocity",
        "Recent56dPhysicalTouches",
        "Recent56dActualLastPutQty",
    }
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"SKU-snapshot panel is missing columns: {', '.join(missing)}")
    if panel.duplicated(["SnapshotEffectiveEST", "SKU"]).any():
        raise ValueError("SKU-snapshot panel contains duplicate snapshot/SKU keys.")
    panel["SnapshotEffectiveEST"] = pd.to_datetime(panel["SnapshotEffectiveEST"], utc=True)
    return panel


def target_snapshots(panel: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.DataFrame]]:
    snapshots: list[tuple[pd.Timestamp, pd.DataFrame]] = []
    for effective_utc, frame in panel.groupby("SnapshotEffectiveEST", sort=True):
        target = frame.set_index("SKU").copy()
        snapshots.append((pd.Timestamp(effective_utc), target))
    if len(snapshots) < 2:
        raise ValueError("At least two confirmed snapshots are required.")
    return snapshots


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


def staged_routing_tier(control: Control, old_tier: str, target_tier: str) -> str:
    if not control.stage_demotions or direction(old_tier, target_tier) != "Demotion":
        return target_tier
    return RANK_TIER[max(TIER_RANK[old_tier] - 1, TIER_RANK[target_tier])]


def tier_counts(state: dict[str, str], skus: pd.Index) -> dict[str, int]:
    values = pd.Series({sku: state[sku] for sku in skus})
    return {f"Routing{tier}": int(values.eq(tier).sum()) for tier in TIER_ORDER}


def replay_control(
    control: Control,
    snapshots: list[tuple[pd.Timestamp, pd.DataFrame]],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    first_effective, first = snapshots[0]
    state = first["Velocity"].to_dict()
    pending_target: dict[str, str] = {}
    pending_count: dict[str, int] = {}
    prior_target = first["Velocity"].copy()
    event_rows: list[dict[str, object]] = []
    interval_rows: list[dict[str, object]] = []

    interval_rows.append(
        {
            "Policy": control.name,
            "SnapshotEffectiveEST": first_effective.isoformat(),
            "TargetVelocityChangesFromPrior": 0,
            "AppliedRoutingChanges": 0,
            "AppliedReturningSKUChanges": 0,
            "DeferredTargetDifferences": 0,
            **tier_counts(state, first.index),
        }
    )

    for effective_utc, target_frame in snapshots[1:]:
        target = target_frame["Velocity"]
        shared = prior_target.index.intersection(target.index)
        target_changes = int((prior_target.reindex(shared) != target.reindex(shared)).sum())
        applied = 0
        applied_returning = 0

        for sku, target_tier in target.items():
            if sku not in state:
                state[sku] = target_tier
                pending_target.pop(sku, None)
                pending_count.pop(sku, None)
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

            confirmations = required_confirmations(control, old_tier, target_tier)
            if pending_count[sku] < confirmations:
                continue

            new_tier = staged_routing_tier(control, old_tier, target_tier)
            if new_tier == old_tier:
                continue

            applied += 1
            observed_in_prior_snapshot = sku in prior_target.index
            if not observed_in_prior_snapshot:
                applied_returning += 1
            state[sku] = new_tier
            tier_steps = abs(TIER_RANK[new_tier] - TIER_RANK[old_tier])
            event_rows.append(
                {
                    "Policy": control.name,
                    "SnapshotEffectiveEST": effective_utc.isoformat(),
                    "SKU": sku,
                    "OldRoutingTier": old_tier,
                    "ForecastTargetTier": target_tier,
                    "NewRoutingTier": new_tier,
                    "ObservedInPriorSnapshot": observed_in_prior_snapshot,
                    "Direction": direction(old_tier, new_tier),
                    "TierSteps": tier_steps,
                    "IsAAtoC": old_tier == "AA" and new_tier == "C",
                    "IsCtoAA": old_tier == "C" and new_tier == "AA",
                    "DecisionRecent56dPhysicalTouches": float(
                        target_frame.at[sku, "Recent56dPhysicalTouches"]
                    ),
                    "DecisionRecent56dActualLastPutQty": float(
                        target_frame.at[sku, "Recent56dActualLastPutQty"]
                    ),
                }
            )

            if new_tier == target_tier:
                pending_target.pop(sku, None)
                pending_count.pop(sku, None)

        active_state = pd.Series({sku: state[sku] for sku in target.index})
        deferred = int((active_state != target).sum())
        interval_rows.append(
            {
                "Policy": control.name,
                "SnapshotEffectiveEST": effective_utc.isoformat(),
                "TargetVelocityChangesFromPrior": target_changes,
                "AppliedRoutingChanges": applied,
                "AppliedReturningSKUChanges": applied_returning,
                "DeferredTargetDifferences": deferred,
                **tier_counts(state, target.index),
            }
        )
        prior_target = target.copy()

    final_target = snapshots[-1][1]["Velocity"]
    final_state = pd.Series({sku: state[sku] for sku in final_target.index})
    final_metrics = {
        "FinalDeferredTargetDifferences": int((final_state != final_target).sum()),
        "FinalRoutingAA": int(final_state.eq("AA").sum()),
        "FinalRoutingA": int(final_state.eq("A").sum()),
        "FinalRoutingB": int(final_state.eq("B").sum()),
        "FinalRoutingC": int(final_state.eq("C").sum()),
    }
    return pd.DataFrame(event_rows), pd.DataFrame(interval_rows), final_metrics


def decorate_reversals(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events
    events = events.sort_values(["Policy", "SKU", "SnapshotEffectiveEST"]).reset_index(drop=True)
    effective = pd.to_datetime(events["SnapshotEffectiveEST"], utc=True)
    events["FirstOppositeDirectionDays"] = np.nan

    for _, indexes in events.groupby(["Policy", "SKU"]).groups.items():
        ordered = list(indexes)
        for position, row_index in enumerate(ordered):
            event_direction = events.at[row_index, "Direction"]
            for later_index in ordered[position + 1 :]:
                if events.at[later_index, "Direction"] == event_direction:
                    continue
                delta_days = (effective.at[later_index] - effective.at[row_index]).total_seconds()
                events.at[row_index, "FirstOppositeDirectionDays"] = delta_days / 86400
                break

    for days in (14, 28, 56):
        events[f"ReversedWithin{days}d"] = events["FirstOppositeDirectionDays"].le(days)
    return events


def policy_summary(
    events: pd.DataFrame,
    intervals: pd.DataFrame,
    final_metrics: dict[str, dict[str, int]],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for control in CONTROLS:
        scoped = events[events["Policy"].eq(control.name)]
        sku_counts = scoped["SKU"].value_counts()
        row = {
            "Policy": control.name,
            "PromotionConfirmations": control.promotion_confirmations,
            "DemotionConfirmations": control.demotion_confirmations,
            "StageDemotions": control.stage_demotions,
            "AppliedRoutingChanges": len(scoped),
            "AppliedReturningSKUChanges": int((~scoped["ObservedInPriorSnapshot"]).sum()),
            "UniqueSKUsChanged": int(scoped["SKU"].nunique()),
            "SKUsChangedMoreThanOnce": int(sku_counts.gt(1).sum()),
            "AppliedMultiTierJumps": int(scoped["TierSteps"].gt(1).sum()),
            "AppliedCtoAA": int(scoped["IsCtoAA"].sum()),
            "AppliedAAtoC": int(scoped["IsAAtoC"].sum()),
            "ReversedWithin14d": int(scoped.get("ReversedWithin14d", pd.Series(dtype=bool)).sum()),
            "ReversedWithin28d": int(scoped.get("ReversedWithin28d", pd.Series(dtype=bool)).sum()),
            "ReversedWithin56d": int(scoped.get("ReversedWithin56d", pd.Series(dtype=bool)).sum()),
            "MaxDeferredTargetDifferences": int(
                intervals.loc[
                    intervals["Policy"].eq(control.name),
                    "DeferredTargetDifferences",
                ].max()
            ),
            **final_metrics[control.name],
        }
        rows.append(row)
    return pd.DataFrame(rows)


def metadata(
    args: argparse.Namespace,
    panel: pd.DataFrame,
    events: pd.DataFrame,
    outputs: dict[str, Path],
) -> dict[str, object]:
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "input": {
            "panel": str(args.panel.relative_to(PROJECT_ROOT)),
            "panel_sha256": sha256(args.panel),
            "panel_rows": len(panel),
        },
        "outputs": {
            name: {"path": str(path.relative_to(PROJECT_ROOT))}
            for name, path in outputs.items()
            if name != "metadata"
        },
        "rows": {"stability_events": len(events)},
        "limitations": [
            "Only five confirmed AX-effective snapshots are available.",
            "Confirmation controls can delay legitimate changes as well as avoidable churn.",
            "Recent changes cannot show future reversals until later snapshots arrive.",
            "No shadow result is authorized for AX upload.",
        ],
    }


def write_outputs(
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    events: pd.DataFrame,
    intervals: pd.DataFrame,
    policies: pd.DataFrame,
    metadata_payload: dict[str, object],
) -> None:
    try:
        events.to_parquet(temporary["events"], index=False, compression="zstd")
        intervals.to_csv(temporary["interval_summary"], index=False)
        policies.to_csv(temporary["policy_summary"], index=False)
        for name, artifact in metadata_payload["outputs"].items():
            artifact["bytes"] = temporary[name].stat().st_size
            artifact["sha256"] = sha256(temporary[name])
        temporary["metadata"].write_text(
            json.dumps(metadata_payload, indent=2) + "\n",
            encoding="utf-8",
        )
        for name, path in outputs.items():
            temporary[name].replace(path)
    except Exception:
        remove_existing(list(temporary.values()))
        raise


def main() -> None:
    args = parse_args()
    outputs, temporary = prepare_outputs(args.output_dir, args.overwrite)
    panel = load_panel(args.panel)
    snapshots = target_snapshots(panel)

    event_frames: list[pd.DataFrame] = []
    interval_frames: list[pd.DataFrame] = []
    final_metrics: dict[str, dict[str, int]] = {}
    for control in CONTROLS:
        events, intervals, final = replay_control(control, snapshots)
        event_frames.append(events)
        interval_frames.append(intervals)
        final_metrics[control.name] = final

    events = decorate_reversals(pd.concat(event_frames, ignore_index=True))
    intervals = pd.concat(interval_frames, ignore_index=True)
    policies = policy_summary(events, intervals, final_metrics)
    payload = metadata(args, panel, events, outputs)
    write_outputs(outputs, temporary, events, intervals, policies, payload)

    print(policies.to_string(index=False))
    print(f"\nShadow stability outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
