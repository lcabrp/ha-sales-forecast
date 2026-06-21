"""Score the empty-location repaint plan with calibrated warehouse topology.

The previous repaint-fit proxy used sortcode and aisle distance. This script
keeps the same shadow plan but measures physical feet with the calibrated
router from the ha-cluster-monitoring project.

It is still a screen, not a full market-basket replay: it answers "are these
empty donor locations near the current neighborhood of the short tier?" before
we spend time building deployable layout variants.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLUSTER_PROJECT = PROJECT_ROOT.parent / "ha-cluster-monitoring"
ROUTER_SCRIPT = CLUSTER_PROJECT / "scripts" / "calculate_cluster_walking_distance.py"
SHADOW_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
REPAINT_PLAN = SHADOW_DIR / "velocity_policy_repaint_fit_plan.csv"
DEPLOYED_MAP = (
    PROJECT_ROOT
    / "Output"
    / "Monitoring"
    / "deployments"
    / "20260507_144000_EDT"
    / "AX_Proposed_Zone_Map.csv"
)
LOCATION_MASTER = PROJECT_ROOT / "Output" / "Layout" / "inputs" / "Data_Pick_Locations.csv"


@dataclass(frozen=True)
class ParsedLocation:
    location: str
    coord: object
    loc_profile: str
    sort_code: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repaint-plan", type=Path, default=REPAINT_PLAN)
    parser.add_argument("--deployed-map", type=Path, default=DEPLOYED_MAP)
    parser.add_argument("--location-master", type=Path, default=LOCATION_MASTER)
    parser.add_argument("--router-script", type=Path, default=ROUTER_SCRIPT)
    parser.add_argument("--output-dir", type=Path, default=SHADOW_DIR)
    parser.add_argument(
        "--anchor-neighborhood",
        type=int,
        default=40,
        help="Number of same-tier sortcode-nearest anchors to test with the physical router.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "detail": output_dir / "velocity_policy_repaint_fit_travel_detail.csv",
        "slot_summary": output_dir / "velocity_policy_repaint_fit_travel_slottier_summary.csv",
        "summary": output_dir / "velocity_policy_repaint_fit_travel_summary.csv",
        "metadata": output_dir / "velocity_policy_repaint_fit_travel_metadata.json",
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Travel artifacts already exist. Pass --overwrite to replace them: "
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


def normalize_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def is_picking_d_location(location: str, loc_profile: str) -> bool:
    loc = str(location).strip().upper()
    profile = str(loc_profile).strip().upper()
    if profile == "PICKING D":
        return True
    if "-" not in loc:
        return False
    aisle, slot = loc.split("-", 1)
    bin_letter = slot[-1:] if slot else ""
    return (aisle == "33" and bin_letter in {"W", "X", "Y", "Z"}) or (
        aisle == "34" and bin_letter in {"A", "B", "C", "D"}
    )


def load_router_module(router_script: Path):
    if not router_script.exists():
        raise FileNotFoundError(f"Router script not found: {router_script}")
    # The cluster-monitoring script imports pyodbc for live VoiceLink queries.
    # This shadow analysis only uses pure geometry functions, so a stub keeps
    # the import portable on machines without the ODBC package installed.
    sys.modules.setdefault(
        "pyodbc",
        types.SimpleNamespace(connect=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pyodbc stub"))),
    )
    spec = importlib.util.spec_from_file_location("cluster_walking_distance_shadow", router_script)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load router script: {router_script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_location(module, location: str, loc_profile: str, sort_code: float) -> ParsedLocation | None:
    coord = module.parse_location(location, is_picking_d=is_picking_d_location(location, loc_profile))
    if coord is None:
        return None
    return ParsedLocation(
        location=str(location).strip().upper(),
        coord=coord,
        loc_profile=str(loc_profile).strip(),
        sort_code=float(sort_code) if not pd.isna(sort_code) else np.nan,
    )


def build_anchor_locations(module, deployed_map: Path, location_master: Path) -> dict[str, list[ParsedLocation]]:
    zone_map = pd.read_csv(deployed_map).rename(
        columns={"WMSLOCATIONID": "Location", "ZONEID": "PaintedSlotTier"}
    )
    locations = pd.read_csv(location_master, usecols=["Location", "LocProfile", "SortCode"])
    painted = zone_map[["Location", "PaintedSlotTier"]].merge(
        locations, on="Location", how="left", validate="one_to_one"
    )
    painted["PaintedSlotTier"] = normalize_text(painted["PaintedSlotTier"])
    painted["Location"] = normalize_text(painted["Location"])
    painted["SortCode"] = pd.to_numeric(painted["SortCode"], errors="coerce")
    anchors: dict[str, list[ParsedLocation]] = {}
    for row in painted.itertuples(index=False):
        parsed = parse_location(module, row.Location, row.LocProfile, row.SortCode)
        if parsed is None:
            continue
        anchors.setdefault(row.PaintedSlotTier, []).append(parsed)
    return anchors


def choose_nearest_anchor(
    router,
    source: ParsedLocation,
    anchors: list[ParsedLocation],
    anchor_neighborhood: int,
    distance_cache: dict[tuple[str, str], float],
) -> tuple[str, float, float]:
    best_location = ""
    best_distance = float("inf")
    best_sort_distance = float("inf")
    candidate_anchors = sorted(
        anchors,
        key=lambda anchor: (
            abs(source.sort_code - anchor.sort_code)
            if pd.notna(source.sort_code) and pd.notna(anchor.sort_code)
            else float("inf"),
            anchor.location,
        ),
    )[:anchor_neighborhood]
    for anchor in candidate_anchors:
        cache_key = (source.location, anchor.location)
        if cache_key in distance_cache:
            distance = distance_cache[cache_key]
        else:
            distance = float(router.calculate_distance(source.coord, anchor.coord))
            distance_cache[cache_key] = distance
        if distance < best_distance or (
            distance == best_distance
            and abs(source.sort_code - anchor.sort_code) < best_sort_distance
        ):
            best_location = anchor.location
            best_distance = float(distance)
            best_sort_distance = float(abs(source.sort_code - anchor.sort_code))
    return best_location, best_distance, best_sort_distance


def score_plan(
    plan: pd.DataFrame,
    module,
    anchors: dict[str, list[ParsedLocation]],
    anchor_neighborhood: int,
) -> pd.DataFrame:
    router = module.Router()
    distance_cache: dict[tuple[str, str], float] = {}
    rows: list[dict[str, object]] = []
    for record in plan.itertuples(index=False):
        source = parse_location(module, record.Location, record.LocProfile, record.SortCode)
        short_tier = str(record.CandidatePaintedSlotTier).strip().upper()
        tier_anchors = anchors.get(short_tier, [])
        nearest_location = ""
        distance_ft = np.nan
        sort_distance = np.nan
        parse_status = "ok"
        if source is None:
            parse_status = "selected_location_unparsed"
        elif not tier_anchors:
            parse_status = "no_existing_short_tier_anchor"
        else:
            nearest_location, distance_ft, sort_distance = choose_nearest_anchor(
                router,
                source,
                tier_anchors,
                anchor_neighborhood=anchor_neighborhood,
                distance_cache=distance_cache,
            )
        rows.append(
            {
                **record._asdict(),
                "NearestExistingShortTierLocation": nearest_location,
                "PhysicalDistanceToNearestShortTierFt": distance_ft,
                "SortCodeDistanceToNearestShortTier": sort_distance,
                "TravelParseStatus": parse_status,
                "AnchorNeighborhoodTested": min(anchor_neighborhood, len(tier_anchors)),
                "Within25Ft": bool(pd.notna(distance_ft) and distance_ft <= 25),
                "Within50Ft": bool(pd.notna(distance_ft) and distance_ft <= 50),
                "Within100Ft": bool(pd.notna(distance_ft) and distance_ft <= 100),
            }
        )
    return pd.DataFrame(rows)


def p90(series: pd.Series) -> float:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    return float(clean.quantile(0.90)) if not clean.empty else np.nan


def summarize(detail: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    valid = detail["PhysicalDistanceToNearestShortTierFt"].notna()
    slot_summary = (
        detail.groupby("CandidatePaintedSlotTier", as_index=False)
        .agg(
            RepaintLocations=("Location", "size"),
            ParsedTravelLocations=("PhysicalDistanceToNearestShortTierFt", "count"),
            MeanNearestShortTierFt=("PhysicalDistanceToNearestShortTierFt", "mean"),
            P90NearestShortTierFt=("PhysicalDistanceToNearestShortTierFt", p90),
            MaxNearestShortTierFt=("PhysicalDistanceToNearestShortTierFt", "max"),
            Within25FtLocations=("Within25Ft", "sum"),
            Within50FtLocations=("Within50Ft", "sum"),
            Within100FtLocations=("Within100Ft", "sum"),
            SameClusterLocations=("SameCluster", "sum"),
            SameProductSizeLocations=("MatchClass", lambda values: int((values == "same_product_size").sum())),
            CrossVelocityPaintLocations=("RequiresCrossVelocityPaint", "sum"),
        )
        .sort_values(["MeanNearestShortTierFt", "RepaintLocations"], ascending=[False, False])
    )
    for column in ["Within25FtLocations", "Within50FtLocations", "Within100FtLocations"]:
        slot_summary[column.replace("Locations", "Pct")] = np.where(
            slot_summary["ParsedTravelLocations"].gt(0),
            slot_summary[column] / slot_summary["ParsedTravelLocations"] * 100,
            0,
        )
    summary = pd.DataFrame(
        [
            {
                "RepaintLocations": int(len(detail)),
                "ParsedTravelLocations": int(valid.sum()),
                "NoAnchorOrParseLocations": int((~valid).sum()),
                "MeanNearestShortTierFt": float(detail.loc[valid, "PhysicalDistanceToNearestShortTierFt"].mean()),
                "P90NearestShortTierFt": p90(detail["PhysicalDistanceToNearestShortTierFt"]),
                "MaxNearestShortTierFt": float(detail.loc[valid, "PhysicalDistanceToNearestShortTierFt"].max()),
                "Within25FtLocations": int(detail["Within25Ft"].sum()),
                "Within50FtLocations": int(detail["Within50Ft"].sum()),
                "Within100FtLocations": int(detail["Within100Ft"].sum()),
                "Within50FtPct": float(detail["Within50Ft"].sum() / max(valid.sum(), 1) * 100),
                "Within100FtPct": float(detail["Within100Ft"].sum() / max(valid.sum(), 1) * 100),
            }
        ]
    )
    return slot_summary, summary


def safe_write(outputs: dict[str, Path], temporary: dict[str, Path], frames: dict[str, pd.DataFrame], metadata: dict) -> None:
    frames["detail"].to_csv(temporary["detail"], index=False)
    frames["slot_summary"].to_csv(temporary["slot_summary"], index=False)
    frames["summary"].to_csv(temporary["summary"], index=False)
    with temporary["metadata"].open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
    for name, path in outputs.items():
        temporary[name].replace(path)


def main() -> None:
    args = parse_args()
    outputs, temporary = prepare_outputs(args.output_dir, args.overwrite)
    module = load_router_module(args.router_script)
    plan = pd.read_csv(args.repaint_plan)
    anchors = build_anchor_locations(module, args.deployed_map, args.location_master)
    detail = score_plan(plan, module, anchors, anchor_neighborhood=args.anchor_neighborhood)
    slot_summary, summary = summarize(detail)
    metadata = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only_no_production_changes",
        "router_source": str(args.router_script),
        "input_files": {
            "repaint_plan": relative(args.repaint_plan),
            "deployed_map": relative(args.deployed_map),
            "location_master": relative(args.location_master),
        },
        "input_hashes": {
            "repaint_plan": sha256(args.repaint_plan),
            "deployed_map": sha256(args.deployed_map),
            "location_master": sha256(args.location_master),
        },
        "notes": [
            "Physical feet use the calibrated ha-cluster-monitoring Router and parse_location functions.",
            "Distance is from the selected empty donor location to the nearest router-tested current location for the candidate short tier.",
            "Router candidates are limited to the same-tier sortcode neighborhood for performance, then scored in physical feet.",
            "This screens map fit/adjoining neighborhood risk; it is not a full order-sequence or market-basket travel replay.",
        ],
        "parameters": {"anchor_neighborhood": int(args.anchor_neighborhood)},
        "row_counts": {
            "repaint_plan_rows": int(len(plan)),
            "detail_rows": int(len(detail)),
            "short_tiers_scored": int(detail["CandidatePaintedSlotTier"].nunique()),
            "anchor_tiers": int(len(anchors)),
        },
    }
    safe_write(
        outputs,
        temporary,
        {"detail": detail, "slot_summary": slot_summary, "summary": summary},
        metadata,
    )
    print(json.dumps({"outputs": {k: relative(v) for k, v in outputs.items()}}, indent=2))


if __name__ == "__main__":
    main()
