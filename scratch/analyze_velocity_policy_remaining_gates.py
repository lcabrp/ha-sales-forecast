"""Analyze remaining velocity-policy gates that do not need another forecast.

This shadow-only package focuses on three deployability questions left after
the incremental activation replay:

1. Which exact SlotTiers could donate painted locations to short tiers?
2. Which SlotTiers show PalletPicking/profile pressure while cube data is absent?
3. How sensitive is the enriched signal to score-boundary hysteresis?

The script reads existing portable shadow artifacts and writes compact derived
outputs only. It does not touch ingestion, AX uploads, approved maps, or the
production allocator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHADOW_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
CAPACITY_FIT = SHADOW_DIR / "velocity_policy_slottier_capacity_fit.csv"
ACTIVATION_CANDIDATES = SHADOW_DIR / "velocity_policy_incremental_activation_candidates.parquet"
ENRICHED_DETAIL = SHADOW_DIR / "velocity_policy_enriched_candidate_sku_snapshot.parquet"
OUTPUT_DIR = SHADOW_DIR
SELECTED_CANDIDATE = "demand_and_pick"
SELECTED_ENVELOPE = "legacy_sku_population_proxy"
TIER_RANK = {"C": 0, "B": 1, "A": 2, "AA": 3}
PREMIUM_TIERS = {"AA", "A"}
MARGIN_BUFFERS = (0.0, 0.0025, 0.005, 0.01, 0.02, 0.05)


@dataclass(frozen=True)
class SlotParts:
    product_group: str
    size_group: str
    velocity: str
    prefix: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity-fit", type=Path, default=CAPACITY_FIT)
    parser.add_argument("--activation-candidates", type=Path, default=ACTIVATION_CANDIDATES)
    parser.add_argument("--enriched-detail", type=Path, default=ENRICHED_DETAIL)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--candidate", default=SELECTED_CANDIDATE)
    parser.add_argument("--capacity-envelope", default=SELECTED_ENVELOPE)
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
        "paint_transfer_options": output_dir / "velocity_policy_exact_tier_paint_transfer_options.csv",
        "pallet_pressure": output_dir / "velocity_policy_palletpicking_profile_pressure.csv",
        "score_hysteresis_state": output_dir / "velocity_policy_score_margin_hysteresis_state.parquet",
        "score_hysteresis_summary": output_dir / "velocity_policy_score_margin_hysteresis_summary.csv",
        "metadata": output_dir / "velocity_policy_remaining_gates_metadata.json",
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Remaining-gate artifacts already exist. Pass --overwrite to replace them: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}
    for path in temporary.values():
        if path.exists():
            path.unlink()
    return outputs, temporary


def split_slot_tier(value: object) -> SlotParts:
    slot_tier = str(value).strip().upper()
    match = re.search(r"(AA|A|B|C)$", slot_tier)
    velocity = match.group(1) if match else ""
    prefix = slot_tier[: -len(velocity)] if velocity else slot_tier
    product_group = prefix[:3]
    size_group = prefix[3:]
    return SlotParts(product_group, size_group, velocity, prefix)


def add_slot_parts(frame: pd.DataFrame, column: str, prefix: str) -> pd.DataFrame:
    parts = frame[column].map(split_slot_tier)
    output = frame.copy()
    output[f"{prefix}ProductGroupCode"] = parts.map(lambda part: part.product_group)
    output[f"{prefix}SizeGroupCode"] = parts.map(lambda part: part.size_group)
    output[f"{prefix}Velocity"] = parts.map(lambda part: part.velocity)
    output[f"{prefix}Prefix"] = parts.map(lambda part: part.prefix)
    return output


def build_paint_transfer_options(capacity_fit: pd.DataFrame) -> pd.DataFrame:
    fit = add_slot_parts(capacity_fit, "SlotTier", "")
    fit["ShortfallSlotsCeil"] = np.ceil(fit["CandidatePlanningShortfall"].clip(lower=0)).astype(int)
    fit["SurplusSlotsFloor"] = np.floor(fit["CandidatePlanningHeadroom"].clip(lower=0)).astype(int)
    shortages = fit[fit["ShortfallSlotsCeil"].gt(0)].copy()
    donors = fit[fit["SurplusSlotsFloor"].gt(0)].copy()
    rows: list[dict[str, object]] = []
    for shortage in shortages.itertuples(index=False):
        scoped = donors[donors["SlotTier"].ne(shortage.SlotTier)].copy()
        scoped["MatchClass"] = np.select(
            [
                scoped["Prefix"].eq(shortage.Prefix),
                scoped["ProductGroupCode"].eq(shortage.ProductGroupCode),
            ],
            ["same_product_size", "same_product_group"],
            default="cross_product_group",
        )
        scoped["MatchRank"] = scoped["MatchClass"].map(
            {"same_product_size": 0, "same_product_group": 1, "cross_product_group": 2}
        )
        scoped["TransferableSlotsProxy"] = np.minimum(
            int(shortage.ShortfallSlotsCeil),
            scoped["SurplusSlotsFloor"].astype(int),
        )
        scoped = scoped[scoped["TransferableSlotsProxy"].gt(0)]
        scoped = scoped.sort_values(
            [
                "MatchRank",
                "PaintedEmptyLocations",
                "CandidatePlanningHeadroom",
                "PaintedOccupiedLocations",
                "SlotTier",
            ],
            ascending=[True, False, False, True, True],
        ).head(5)
        for donor in scoped.itertuples(index=False):
            rows.append(
                {
                    "ShortSlotTier": shortage.SlotTier,
                    "ShortProductGroupCode": shortage.ProductGroupCode,
                    "ShortSizeGroupCode": shortage.SizeGroupCode,
                    "ShortVelocity": shortage.Velocity,
                    "ShortfallSlotsCeil": int(shortage.ShortfallSlotsCeil),
                    "ShortCandidatePlanningShortfall": round(
                        float(shortage.CandidatePlanningShortfall),
                        3,
                    ),
                    "DonorSlotTier": donor.SlotTier,
                    "DonorProductGroupCode": donor.ProductGroupCode,
                    "DonorSizeGroupCode": donor.SizeGroupCode,
                    "DonorVelocity": donor.Velocity,
                    "DonorSurplusSlotsFloor": int(donor.SurplusSlotsFloor),
                    "DonorPaintedEmptyLocations": int(donor.PaintedEmptyLocations),
                    "DonorPaintedOccupiedLocations": int(donor.PaintedOccupiedLocations),
                    "TransferableSlotsProxy": int(donor.TransferableSlotsProxy),
                    "MatchClass": donor.MatchClass,
                    "RequiresCrossVelocityPaint": donor.Prefix == shortage.Prefix
                    and donor.Velocity != shortage.Velocity,
                }
            )
    return pd.DataFrame(rows)


def build_pallet_pressure(capacity_fit: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    tier_pressure = (
        candidates.groupby("RoutingSlotTier", as_index=False)
        .agg(
            ChangedSKUs=("SKU", "nunique"),
            Promotions=("Direction", lambda values: values.eq("Promotion").sum()),
            Demotions=("Direction", lambda values: values.eq("Demotion").sum()),
            Recent56dDemandPhysicalTouches=("Recent56dDemandPhysicalTouches", "sum"),
            Recent56dPickLines=("Recent56dPickLines", "sum"),
            FloorPhysicalQty=("FloorPhysicalQty", "sum"),
            FloorOccupiedLocations=("FloorOccupiedLocations", "sum"),
            CandidatePlanningSlotReservations=("ActivationSlotReservation", "sum"),
            PremiumDemotionReviewSKUs=("RequiresPremiumDemotionReview", "sum"),
        )
        .rename(columns={"RoutingSlotTier": "SlotTier"})
    )
    fit = capacity_fit.merge(tier_pressure, on="SlotTier", how="left", validate="one_to_one")
    numeric = [
        "ChangedSKUs",
        "Promotions",
        "Demotions",
        "Recent56dDemandPhysicalTouches",
        "Recent56dPickLines",
        "FloorPhysicalQty",
        "FloorOccupiedLocations",
        "CandidatePlanningSlotReservations",
        "PremiumDemotionReviewSKUs",
        "PaintedProfile_PalletPicking",
        "PaintedProfile_Picking",
        "PaintedProfile_Picking_A",
    ]
    for column in numeric:
        if column not in fit.columns:
            fit[column] = 0
    fit[numeric] = fit[numeric].fillna(0)
    fit["HasPalletPickingPaint"] = fit["PaintedProfile_PalletPicking"].gt(0)
    fit["PalletPressureScore"] = (
        fit["CandidatePlanningShortfall"].clip(lower=0).rank(pct=True)
        + fit["Recent56dPickLines"].rank(pct=True)
        + fit["Recent56dDemandPhysicalTouches"].rank(pct=True)
        + fit["FloorPhysicalQty"].rank(pct=True)
    )
    fit["CubeDataStatus"] = "not_available_in_tracked_shadow_inputs"
    columns = [
        "SlotTier",
        "PaintedLocations",
        "PaintedEmptyLocations",
        "PaintedOccupiedLocations",
        "PaintedProfile_PalletPicking",
        "PaintedProfile_Picking",
        "PaintedProfile_Picking_A",
        "HasPalletPickingPaint",
        "CandidatePlanningRequiredSlots",
        "CandidatePlanningShortfall",
        "ChangedSKUs",
        "Promotions",
        "Demotions",
        "Recent56dDemandPhysicalTouches",
        "Recent56dPickLines",
        "FloorPhysicalQty",
        "FloorOccupiedLocations",
        "CandidatePlanningSlotReservations",
        "PremiumDemotionReviewSKUs",
        "PalletPressureScore",
        "CubeDataStatus",
    ]
    return fit.sort_values(
        ["PalletPressureScore", "CandidatePlanningShortfall", "SlotTier"],
        ascending=[False, False, True],
    )[columns]


def add_score_boundary_distance(snapshot: pd.DataFrame) -> pd.DataFrame:
    ordered = snapshot.sort_values(["BurdenScore", "SKU"], ascending=[False, True]).copy()
    ordered["ScoreRankPosition"] = np.arange(1, len(ordered) + 1)
    counts = ordered["CandidateVelocity"].value_counts()
    boundaries = [
        int(counts.get("AA", 0)),
        int(counts.get("AA", 0) + counts.get("A", 0)),
        int(counts.get("AA", 0) + counts.get("A", 0) + counts.get("B", 0)),
    ]
    usable_boundaries = [boundary for boundary in boundaries if 0 < boundary < len(ordered)]
    if usable_boundaries:
        distance = np.min(
            [np.abs(ordered["ScoreRankPosition"] - boundary) for boundary in usable_boundaries],
            axis=0,
        )
        ordered["BoundaryDistancePct"] = distance / len(ordered)
    else:
        ordered["BoundaryDistancePct"] = 1.0
    return ordered.sort_index()


def apply_margin_hysteresis(detail: pd.DataFrame, buffer_pct: float) -> pd.DataFrame:
    state: dict[str, str] = {}
    frames: list[pd.DataFrame] = []
    for effective, snapshot in detail.groupby("SnapshotEffectiveEST", sort=True):
        scoped = add_score_boundary_distance(snapshot).copy()
        routing: list[str] = []
        blocked: list[bool] = []
        for row in scoped.itertuples(index=False):
            previous = state.get(row.SKU)
            candidate = row.CandidateVelocity
            near_boundary = float(row.BoundaryDistancePct) < buffer_pct
            if previous is not None and candidate != previous and near_boundary:
                tier = previous
                is_blocked = True
            else:
                tier = candidate
                is_blocked = False
            state[row.SKU] = tier
            routing.append(tier)
            blocked.append(is_blocked)
        scoped["MarginBufferPct"] = buffer_pct
        scoped["HysteresisVelocity"] = routing
        scoped["BoundaryChangeBlocked"] = blocked
        frames.append(
            scoped[
                [
                    "SnapshotEffectiveEST",
                    "SKU",
                    "Velocity",
                    "CandidateVelocity",
                    "BurdenScore",
                    "ScoreRankPosition",
                    "BoundaryDistancePct",
                    "OutcomeIntervalDays",
                    "OutcomeToNextSnapshotDemandPhysicalTouches",
                    "Recent56dPickLines",
                    "MarginBufferPct",
                    "HysteresisVelocity",
                    "BoundaryChangeBlocked",
                ]
            ].copy()
        )
    return pd.concat(frames, ignore_index=True)


def summarize_hysteresis(frames: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for buffer, scoped in frames.groupby("MarginBufferPct"):
        churn = 0
        previous: pd.Series | None = None
        for _effective, snapshot in scoped.groupby("SnapshotEffectiveEST", sort=True):
            current = snapshot.set_index("SKU")["HysteresisVelocity"]
            if previous is not None:
                shared = previous.index.intersection(current.index)
                churn += int((previous.reindex(shared) != current.reindex(shared)).sum())
            previous = current
        evaluable = scoped[scoped["OutcomeIntervalDays"].notna()].copy()
        total_demand = float(evaluable["OutcomeToNextSnapshotDemandPhysicalTouches"].fillna(0).sum())
        premium = evaluable["HysteresisVelocity"].isin(PREMIUM_TIERS)
        aa = evaluable["HysteresisVelocity"].eq("AA")
        latest = scoped[scoped["SnapshotEffectiveEST"].eq(scoped["SnapshotEffectiveEST"].max())]
        rows.append(
            {
                "MarginBufferPct": buffer,
                "AdjacentSignalTierChanges": churn,
                "BoundaryChangeBlocks": int(scoped["BoundaryChangeBlocked"].sum()),
                "FinalDifferencesVsCandidate": int(
                    latest["HysteresisVelocity"].ne(latest["CandidateVelocity"]).sum()
                ),
                "FinalDifferencesVsLegacy": int(
                    latest["HysteresisVelocity"].ne(latest["Velocity"]).sum()
                ),
                "AAorAOutcomeDemandTouchCapturePct": round(
                    100
                    * evaluable.loc[
                        premium,
                        "OutcomeToNextSnapshotDemandPhysicalTouches",
                    ].sum()
                    / total_demand,
                    2,
                )
                if total_demand
                else 0,
                "AAOutcomeDemandTouchCapturePct": round(
                    100
                    * evaluable.loc[aa, "OutcomeToNextSnapshotDemandPhysicalTouches"].sum()
                    / total_demand,
                    2,
                )
                if total_demand
                else 0,
                "AAorARecentPickLineCapturePct": round(
                    100
                    * evaluable.loc[premium, "Recent56dPickLines"].sum()
                    / evaluable["Recent56dPickLines"].sum(),
                    2,
                )
                if evaluable["Recent56dPickLines"].sum()
                else 0,
            }
        )
    return pd.DataFrame(rows).sort_values("MarginBufferPct")


def build_score_margin_hysteresis(
    detail_path: Path,
    candidate: str,
    capacity_envelope: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail = pd.read_parquet(detail_path)
    detail["SnapshotEffectiveEST"] = pd.to_datetime(detail["SnapshotEffectiveEST"], utc=True)
    selected = detail[
        detail["Candidate"].eq(candidate)
        & detail["CapacityEnvelope"].eq(capacity_envelope)
    ].copy()
    if selected.empty:
        raise ValueError("Selected enriched candidate returned no rows.")
    frames = [apply_margin_hysteresis(selected, buffer) for buffer in MARGIN_BUFFERS]
    state = pd.concat(frames, ignore_index=True)
    summary = summarize_hysteresis(state)
    return state, summary


def write_outputs(
    args: argparse.Namespace,
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    paint_transfer: pd.DataFrame,
    pallet_pressure: pd.DataFrame,
    hysteresis_state: pd.DataFrame,
    hysteresis_summary: pd.DataFrame,
) -> None:
    payload: dict[str, object] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "candidate": args.candidate,
        "capacity_envelope": args.capacity_envelope,
        "inputs": {
            "capacity_fit": {
                "path": relative(args.capacity_fit),
                "sha256": sha256(args.capacity_fit),
            },
            "activation_candidates": {
                "path": relative(args.activation_candidates),
                "sha256": sha256(args.activation_candidates),
            },
            "enriched_detail": {
                "path": relative(args.enriched_detail),
                "sha256": sha256(args.enriched_detail),
            },
        },
        "outputs": {},
        "rows": {
            "paint_transfer_options": len(paint_transfer),
            "pallet_pressure": len(pallet_pressure),
            "score_hysteresis_state": len(hysteresis_state),
            "score_hysteresis_summary": len(hysteresis_summary),
        },
        "definitions": {
            "paint_transfer_options": "Ranked donor SlotTiers with positive candidate headroom for exact SlotTiers with candidate shortfall.",
            "pallet_pressure": "Profile-pressure screen using required slots, occupied floor, picks, and Demand touches. It does not include cube.",
            "margin_buffer_pct": "Signal-tier changes within this fraction of a ranking boundary retain the prior signal tier in the stress test.",
        },
        "limitations": [
            "Paint transfer rows are diagnostics only; they do not preserve aisle adjacency, travel, cluster fit, or category-room constraints.",
            "Cube is not available in tracked shadow inputs. Prior cube work used AX WHSPHYSDIMUOM and should be refreshed before cube-based activation.",
            "PalletPicking pressure is a non-cube proxy and must not become an automatic assignment rule.",
            "Score-margin hysteresis has only five confirmed snapshots, so use it to choose stress ranges, not final thresholds.",
        ],
    }
    try:
        paint_transfer.to_csv(temporary["paint_transfer_options"], index=False)
        pallet_pressure.to_csv(temporary["pallet_pressure"], index=False)
        hysteresis_state.to_parquet(
            temporary["score_hysteresis_state"],
            index=False,
            compression="zstd",
        )
        hysteresis_summary.to_csv(temporary["score_hysteresis_summary"], index=False)
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
    capacity_fit = pd.read_csv(args.capacity_fit)
    candidates = pd.read_parquet(args.activation_candidates)
    paint_transfer = build_paint_transfer_options(capacity_fit)
    pallet_pressure = build_pallet_pressure(capacity_fit, candidates)
    hysteresis_state, hysteresis_summary = build_score_margin_hysteresis(
        args.enriched_detail,
        args.candidate,
        args.capacity_envelope,
    )
    write_outputs(
        args,
        outputs,
        temporary,
        paint_transfer,
        pallet_pressure,
        hysteresis_state,
        hysteresis_summary,
    )
    print("Paint transfer options:")
    print(paint_transfer.head(20).to_string(index=False))
    print("\nPalletPicking/profile pressure:")
    print(pallet_pressure.head(20).to_string(index=False))
    print("\nScore-margin hysteresis:")
    print(hysteresis_summary.to_string(index=False))
    print(f"\nRemaining-gate outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
