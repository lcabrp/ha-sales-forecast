"""Build a shadow repaint-fit diagnostic from exact-tier donor options.

The remaining-gates report ranks SlotTier donor options. This script goes one
step deeper and selects currently empty painted locations that could cover
candidate exact-tier shortfalls with the lowest adjacency risk proxy.

It intentionally does not create an AX upload or modify approved maps. The
output is a repaint-review queue for future map-fitting work.
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
TRANSFER_OPTIONS = SHADOW_DIR / "velocity_policy_exact_tier_paint_transfer_options.csv"
CAPACITY_FIT = SHADOW_DIR / "velocity_policy_slottier_capacity_fit.csv"
INVENTORY = REPLAY_DIR / "sku_location_inventory_snapshots.parquet"
DEPLOYED_MAP = (
    PROJECT_ROOT
    / "Output"
    / "Monitoring"
    / "deployments"
    / "20260507_144000_EDT"
    / "AX_Proposed_Zone_Map.csv"
)
LOCATION_MASTER = PROJECT_ROOT / "Output" / "Layout" / "inputs" / "Data_Pick_Locations.csv"
CATEGORY_CLUSTERS = PROJECT_ROOT / "Output" / "MarketBasket" / "Category_Clusters_2026-04-06.csv"
OUTPUT_DIR = SHADOW_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transfer-options", type=Path, default=TRANSFER_OPTIONS)
    parser.add_argument("--capacity-fit", type=Path, default=CAPACITY_FIT)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--deployed-map", type=Path, default=DEPLOYED_MAP)
    parser.add_argument("--location-master", type=Path, default=LOCATION_MASTER)
    parser.add_argument("--category-clusters", type=Path, default=CATEGORY_CLUSTERS)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "repaint_plan": output_dir / "velocity_policy_repaint_fit_plan.csv",
        "slot_summary": output_dir / "velocity_policy_repaint_fit_slottier_summary.csv",
        "summary": output_dir / "velocity_policy_repaint_fit_summary.csv",
        "metadata": output_dir / "velocity_policy_repaint_fit_metadata.json",
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Repaint-fit artifacts already exist. Pass --overwrite to replace them: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}
    for path in temporary.values():
        if path.exists():
            path.unlink()
    return outputs, temporary


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def normalize_slot(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def tier_suffix(value: object) -> str:
    match = re.search(r"(AA|A|B|C)$", str(value).strip().upper())
    return match.group(1) if match else ""


def load_painted_locations(args: argparse.Namespace) -> pd.DataFrame:
    zone_map = pd.read_csv(args.deployed_map).rename(
        columns={"WMSLOCATIONID": "Location", "ZONEID": "PaintedSlotTier"}
    )
    location_master = pd.read_csv(
        args.location_master,
        usecols=["Location", "LocProfile", "CleanAisle", "SortCode"],
    )
    latest_inventory = pd.read_parquet(args.inventory)
    latest_inventory["SnapshotDate"] = pd.to_datetime(latest_inventory["SnapshotDate"])
    latest_inventory = latest_inventory[
        latest_inventory["SnapshotDate"].eq(latest_inventory["SnapshotDate"].max())
    ].copy()
    occupied = latest_inventory.groupby("Location", as_index=False).agg(
        OccupiedSKUs=("SKU", "nunique"),
        PhysicalQty=("PhysicalQty", "sum"),
    )
    painted = (
        zone_map[["Location", "PaintedSlotTier"]]
        .merge(location_master, on="Location", how="left", validate="one_to_one")
        .merge(occupied, on="Location", how="left", validate="one_to_one")
    )
    painted["PaintedSlotTier"] = normalize_slot(painted["PaintedSlotTier"])
    painted["OccupiedSKUs"] = painted["OccupiedSKUs"].fillna(0).astype(int)
    painted["PhysicalQty"] = pd.to_numeric(painted["PhysicalQty"], errors="coerce").fillna(0)
    painted["IsEmpty"] = painted["OccupiedSKUs"].eq(0)
    painted["CleanAisle"] = pd.to_numeric(painted["CleanAisle"], errors="coerce")
    painted["SortCode"] = pd.to_numeric(painted["SortCode"], errors="coerce")
    return painted


def load_clusters(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    clusters = pd.read_csv(path)
    clusters["Node"] = clusters["Node"].astype(str).str.upper().str.strip()
    return clusters.set_index("Node")["Cluster"].to_dict()


def add_transfer_risk(
    options: pd.DataFrame,
    painted: pd.DataFrame,
    clusters: dict[str, int],
) -> pd.DataFrame:
    output = options.copy()
    output["ShortNode"] = output["ShortProductGroupCode"] + output["ShortSizeGroupCode"]
    output["DonorNode"] = output["DonorProductGroupCode"] + output["DonorSizeGroupCode"]
    output["ShortCluster"] = output["ShortNode"].map(clusters)
    output["DonorCluster"] = output["DonorNode"].map(clusters)
    output["SameCluster"] = (
        output["ShortCluster"].notna()
        & output["DonorCluster"].notna()
        & output["ShortCluster"].eq(output["DonorCluster"])
    )
    locations = (
        painted.groupby("PaintedSlotTier", as_index=False)
        .agg(
            ZoneMedianSortCode=("SortCode", "median"),
            ZoneMedianAisle=("CleanAisle", "median"),
            ZoneLocationCount=("Location", "size"),
            ZoneEmptyLocations=("IsEmpty", "sum"),
        )
        .rename(columns={"PaintedSlotTier": "SlotTier"})
    )
    output = output.merge(
        locations.rename(
            columns={
                "SlotTier": "ShortSlotTier",
                "ZoneMedianSortCode": "ShortMedianSortCode",
                "ZoneMedianAisle": "ShortMedianAisle",
                "ZoneLocationCount": "ShortPaintedLocationCount",
                "ZoneEmptyLocations": "ShortEmptyLocations",
            }
        ),
        on="ShortSlotTier",
        how="left",
        validate="many_to_one",
    ).merge(
        locations.rename(
            columns={
                "SlotTier": "DonorSlotTier",
                "ZoneMedianSortCode": "DonorMedianSortCode",
                "ZoneMedianAisle": "DonorMedianAisle",
                "ZoneLocationCount": "DonorPaintedLocationCount",
                "ZoneEmptyLocations": "DonorEmptyLocations",
            }
        ),
        on="DonorSlotTier",
        how="left",
        validate="many_to_one",
    )
    output["MedianSortCodeDistance"] = (
        output["DonorMedianSortCode"] - output["ShortMedianSortCode"]
    ).abs()
    output["MedianAisleDistance"] = (
        output["DonorMedianAisle"] - output["ShortMedianAisle"]
    ).abs()
    output["MatchRank"] = output["MatchClass"].map(
        {"same_product_size": 0, "same_product_group": 1, "cross_product_group": 2}
    ).fillna(3)
    output["ClusterPenalty"] = np.where(output["SameCluster"], 0, 1)
    output["AdjacencyRiskScore"] = (
        output["MatchRank"] * 1000
        + output["ClusterPenalty"] * 250
        + output["MedianAisleDistance"].fillna(999) * 10
        + output["MedianSortCodeDistance"].fillna(999999) / 1000
    )
    return output


def build_repaint_plan(
    transfer_options: pd.DataFrame,
    painted: pd.DataFrame,
) -> pd.DataFrame:
    remaining_shortfall = (
        transfer_options.groupby("ShortSlotTier")["ShortfallSlotsCeil"].max().astype(int).to_dict()
    )
    remaining_donor_surplus = (
        transfer_options.groupby("DonorSlotTier")["DonorSurplusSlotsFloor"].max().astype(int).to_dict()
    )
    used_locations: set[str] = set()
    rows: list[dict[str, object]] = []
    sorted_options = transfer_options.sort_values(
        [
            "AdjacencyRiskScore",
            "MatchRank",
            "SameCluster",
            "DonorPaintedEmptyLocations",
            "TransferableSlotsProxy",
            "ShortSlotTier",
            "DonorSlotTier",
        ],
        ascending=[True, True, False, False, False, True, True],
    )
    for option in sorted_options.itertuples(index=False):
        short_remaining = remaining_shortfall.get(option.ShortSlotTier, 0)
        donor_remaining = remaining_donor_surplus.get(option.DonorSlotTier, 0)
        if short_remaining <= 0 or donor_remaining <= 0:
            continue
        donor_locations = painted[
            painted["PaintedSlotTier"].eq(option.DonorSlotTier)
            & painted["IsEmpty"]
            & ~painted["Location"].isin(used_locations)
        ].copy()
        if donor_locations.empty:
            continue
        donor_locations["DistanceToShortMedianSortCode"] = (
            donor_locations["SortCode"] - option.ShortMedianSortCode
        ).abs()
        donor_locations["DistanceToShortMedianAisle"] = (
            donor_locations["CleanAisle"] - option.ShortMedianAisle
        ).abs()
        take = min(
            short_remaining,
            donor_remaining,
            int(option.TransferableSlotsProxy),
            len(donor_locations),
        )
        if take <= 0:
            continue
        selected = donor_locations.sort_values(
            [
                "DistanceToShortMedianAisle",
                "DistanceToShortMedianSortCode",
                "SortCode",
                "Location",
            ]
        ).head(take)
        for location in selected.itertuples(index=False):
            rows.append(
                {
                    "Location": location.Location,
                    "SortCode": location.SortCode,
                    "CleanAisle": location.CleanAisle,
                    "LocProfile": location.LocProfile,
                    "OriginalPaintedSlotTier": option.DonorSlotTier,
                    "CandidatePaintedSlotTier": option.ShortSlotTier,
                    "ShortfallSlotsCeilAtStart": int(option.ShortfallSlotsCeil),
                    "MatchClass": option.MatchClass,
                    "SameCluster": bool(option.SameCluster),
                    "RequiresCrossVelocityPaint": bool(option.RequiresCrossVelocityPaint),
                    "DistanceToShortMedianSortCode": location.DistanceToShortMedianSortCode,
                    "DistanceToShortMedianAisle": location.DistanceToShortMedianAisle,
                    "AdjacencyRiskScore": option.AdjacencyRiskScore,
                }
            )
            used_locations.add(str(location.Location))
        remaining_shortfall[option.ShortSlotTier] = short_remaining - take
        remaining_donor_surplus[option.DonorSlotTier] = donor_remaining - take
    return pd.DataFrame(rows)


def build_slot_summary(capacity_fit: pd.DataFrame, repaint_plan: pd.DataFrame) -> pd.DataFrame:
    fit = capacity_fit.copy()
    incoming = repaint_plan.groupby("CandidatePaintedSlotTier").size().rename("IncomingRepaintSlots")
    outgoing = repaint_plan.groupby("OriginalPaintedSlotTier").size().rename("OutgoingRepaintSlots")
    fit = fit.merge(incoming, left_on="SlotTier", right_index=True, how="left")
    fit = fit.merge(outgoing, left_on="SlotTier", right_index=True, how="left")
    fit[["IncomingRepaintSlots", "OutgoingRepaintSlots"]] = fit[
        ["IncomingRepaintSlots", "OutgoingRepaintSlots"]
    ].fillna(0).astype(int)
    fit["PaintedLocationsAfterRepaintProxy"] = (
        fit["PaintedLocations"] + fit["IncomingRepaintSlots"] - fit["OutgoingRepaintSlots"]
    )
    fit["CandidatePlanningHeadroomAfterRepaintProxy"] = (
        fit["PaintedLocationsAfterRepaintProxy"] - fit["CandidatePlanningRequiredSlots"]
    )
    fit["CandidatePlanningShortfallAfterRepaintProxy"] = (
        -fit["CandidatePlanningHeadroomAfterRepaintProxy"]
    ).clip(lower=0)
    return fit.sort_values(
        ["CandidatePlanningShortfallAfterRepaintProxy", "SlotTier"],
        ascending=[False, True],
    )


def build_summary(
    capacity_fit: pd.DataFrame,
    repaint_plan: pd.DataFrame,
    slot_summary: pd.DataFrame,
) -> pd.DataFrame:
    before_shortfall = float(capacity_fit["CandidatePlanningShortfall"].clip(lower=0).sum())
    after_shortfall = float(slot_summary["CandidatePlanningShortfallAfterRepaintProxy"].sum())
    rows = [
        {
            "Metric": "Candidate shortfall before repaint proxy",
            "Value": round(before_shortfall, 3),
        },
        {
            "Metric": "Candidate shortfall after empty-location repaint proxy",
            "Value": round(after_shortfall, 3),
        },
        {
            "Metric": "Shortfall proxy reduced",
            "Value": round(before_shortfall - after_shortfall, 3),
        },
        {
            "Metric": "Empty locations selected for repaint",
            "Value": int(len(repaint_plan)),
        },
        {
            "Metric": "Short tiers receiving at least one repaint",
            "Value": int(repaint_plan["CandidatePaintedSlotTier"].nunique())
            if not repaint_plan.empty
            else 0,
        },
        {
            "Metric": "Donor tiers used",
            "Value": int(repaint_plan["OriginalPaintedSlotTier"].nunique())
            if not repaint_plan.empty
            else 0,
        },
        {
            "Metric": "Same product/size-prefix repaint locations",
            "Value": int(repaint_plan["MatchClass"].eq("same_product_size").sum())
            if not repaint_plan.empty
            else 0,
        },
        {
            "Metric": "Same cluster repaint locations",
            "Value": int(repaint_plan["SameCluster"].sum()) if not repaint_plan.empty else 0,
        },
        {
            "Metric": "Average aisle distance to short tier median",
            "Value": round(float(repaint_plan["DistanceToShortMedianAisle"].mean()), 3)
            if not repaint_plan.empty
            else 0,
        },
    ]
    return pd.DataFrame(rows)


def write_outputs(
    args: argparse.Namespace,
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    repaint_plan: pd.DataFrame,
    slot_summary: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    payload: dict[str, object] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "inputs": {
            "transfer_options": {
                "path": relative(args.transfer_options),
                "sha256": sha256(args.transfer_options),
            },
            "capacity_fit": {"path": relative(args.capacity_fit), "sha256": sha256(args.capacity_fit)},
            "inventory": {"path": relative(args.inventory), "sha256": sha256(args.inventory)},
            "deployed_map": {"path": relative(args.deployed_map), "sha256": sha256(args.deployed_map)},
            "location_master": {
                "path": relative(args.location_master),
                "sha256": sha256(args.location_master),
            },
            "category_clusters": {
                "path": relative(args.category_clusters),
                "sha256": sha256(args.category_clusters),
            },
        },
        "outputs": {},
        "rows": {
            "repaint_plan": len(repaint_plan),
            "slot_summary": len(slot_summary),
            "summary": len(summary),
        },
        "definitions": {
            "empty_location_repaint": "A diagnostic repaint of a currently unoccupied location from a donor SlotTier to a short candidate SlotTier.",
            "candidate_shortfall_after_repaint_proxy": "Candidate required-slot proxy minus painted-location count after the diagnostic repaint plan.",
            "adjacency_risk_score": "Heuristic ranking using match class, category-cluster match, aisle distance, and SortCode distance.",
        },
        "limitations": [
            "This is not an AX upload, not a location directive, and not a production map.",
            "Only empty donor locations are selected; occupied stock movement is intentionally excluded.",
            "The repaint plan does not run full market-basket travel simulation or allocator adjacency repair.",
            "RequiredSlots remains a SlotTier-level planning proxy rather than a SKU-level slot requirement.",
        ],
    }
    try:
        repaint_plan.to_csv(temporary["repaint_plan"], index=False)
        slot_summary.to_csv(temporary["slot_summary"], index=False)
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
    transfer_options = pd.read_csv(args.transfer_options)
    capacity_fit = pd.read_csv(args.capacity_fit)
    painted = load_painted_locations(args)
    clusters = load_clusters(args.category_clusters)
    transfer_options = add_transfer_risk(transfer_options, painted, clusters)
    repaint_plan = build_repaint_plan(transfer_options, painted)
    slot_summary = build_slot_summary(capacity_fit, repaint_plan)
    summary = build_summary(capacity_fit, repaint_plan, slot_summary)
    write_outputs(args, outputs, temporary, repaint_plan, slot_summary, summary)
    print("Repaint-fit summary:")
    print(summary.to_string(index=False))
    print("\nLargest remaining shortfalls after repaint proxy:")
    print(
        slot_summary[
            slot_summary["CandidatePlanningShortfallAfterRepaintProxy"].gt(0)
        ].head(20).to_string(index=False)
    )
    print(f"\nRepaint-fit outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
