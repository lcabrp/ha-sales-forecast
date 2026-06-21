"""Measure physical map debt created by velocity-tier churn.

Forecast and routing tiers can change immediately, but occupied forward-pick
locations clear gradually. This shadow-only analysis measures that backlog and
compares routing-stability controls without changing ingestion, AX, or maps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLAY_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
SHADOW_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
INVENTORY_PATH = REPLAY_DIR / "sku_location_inventory_snapshots.parquet"
PANEL_PATH = SHADOW_DIR / "velocity_policy_sku_snapshot_panel.parquet"
STABILITY_EVENTS_PATH = SHADOW_DIR / "velocity_policy_stability_events.parquet"
OUTPUT_DIR = SHADOW_DIR
TIER_RANK = {"C": 0, "B": 1, "A": 2, "AA": 3}
PREMIUM_TIERS = {"AA", "A"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    parser.add_argument("--panel", type=Path, default=PANEL_PATH)
    parser.add_argument("--stability-events", type=Path, default=STABILITY_EVENTS_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
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
        "location_snapshot": output_dir / "velocity_policy_map_debt_location_snapshot.parquet",
        "snapshot_summary": output_dir / "velocity_policy_map_debt_snapshot_summary.csv",
        "turnover_detail": output_dir / "velocity_policy_map_debt_turnover_detail.parquet",
        "turnover_summary": output_dir / "velocity_policy_map_debt_turnover_summary.csv",
        "latest_age_summary": output_dir / "velocity_policy_map_debt_latest_age_summary.csv",
        "policy_summary": output_dir / "velocity_policy_stability_map_debt_summary.csv",
        "policy_tradeoff": output_dir / "velocity_policy_stability_tradeoff_summary.csv",
        "metadata": output_dir / "velocity_policy_map_debt_metadata.json",
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Map-debt artifacts already exist. Pass --overwrite to replace them: "
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


def enrich_debt(
    frame: pd.DataFrame,
    target_tier: pd.Series,
    target_name: str,
) -> pd.DataFrame:
    output = frame.copy()
    output[target_name] = target_tier.reindex(output.index)
    output["CurrentLocationVelocity"] = tier_suffix(output["CurrentZoneId"])
    current_rank = output["CurrentLocationVelocity"].map(TIER_RANK)
    target_rank = output[target_name].map(TIER_RANK)
    known = current_rank.notna() & target_rank.notna()
    output["VelocityDebt"] = known & current_rank.ne(target_rank)
    output["VelocityDebtTierSteps"] = (current_rank - target_rank).abs().where(known, 0)
    output["VelocityDebtDirection"] = np.select(
        [
            known & current_rank.gt(target_rank),
            known & current_rank.lt(target_rank),
        ],
        [
            "DemotionDebt",
            "PromotionDebt",
        ],
        default="MatchedOrUnknown",
    )
    output["PremiumLocation"] = output["CurrentLocationVelocity"].isin(PREMIUM_TIERS)
    output["DemotionPremiumLocationDebt"] = (
        output["VelocityDebtDirection"].eq("DemotionDebt") & output["PremiumLocation"]
    )
    output["DemotionPremiumLocationStepProxy"] = np.where(
        output["DemotionPremiumLocationDebt"],
        output["VelocityDebtTierSteps"],
        0,
    )
    output["VelocityDebtPhysicalQty"] = np.where(
        output["VelocityDebt"],
        output["PhysicalQty"],
        0,
    )
    return output


def load_inventory(path: Path) -> pd.DataFrame:
    inventory = pd.read_parquet(path)
    required = {"SnapshotDate", "Location", "SKU", "CurrentZoneId", "PhysicalQty", "ForecastSlotTier"}
    missing = sorted(required - set(inventory.columns))
    if missing:
        raise ValueError(f"Inventory fact is missing columns: {', '.join(missing)}")
    if inventory.duplicated(["SnapshotDate", "Location", "SKU"]).any():
        raise ValueError("Inventory fact contains duplicate snapshot/location/SKU keys.")
    inventory["SnapshotDate"] = pd.to_datetime(inventory["SnapshotDate"])
    inventory["PhysicalQty"] = pd.to_numeric(inventory["PhysicalQty"], errors="coerce").fillna(0)
    inventory["ForecastTargetVelocity"] = tier_suffix(inventory["ForecastSlotTier"])
    return inventory


def summarize_location_debt(debt: pd.DataFrame) -> pd.DataFrame:
    return (
        debt.groupby("SnapshotDate", as_index=False)
        .agg(
            OccupiedLocations=("Location", "nunique"),
            DistinctSKUs=("SKU", "nunique"),
            PhysicalQty=("PhysicalQty", "sum"),
            VelocityDebtLocations=("VelocityDebt", "sum"),
            DemotionDebtLocations=(
                "VelocityDebtDirection",
                lambda values: values.eq("DemotionDebt").sum(),
            ),
            PromotionDebtLocations=(
                "VelocityDebtDirection",
                lambda values: values.eq("PromotionDebt").sum(),
            ),
            DemotionPremiumLocationDebtLocations=("DemotionPremiumLocationDebt", "sum"),
            VelocityDebtTierSteps=("VelocityDebtTierSteps", "sum"),
            DemotionPremiumLocationStepProxy=("DemotionPremiumLocationStepProxy", "sum"),
            VelocityDebtPhysicalQty=("VelocityDebtPhysicalQty", "sum"),
            DebtLocationsObservedAtLeast14d=(
                "ObservedDebtStreakDays",
                lambda values: values.ge(14).sum(),
            ),
        )
        .sort_values("SnapshotDate")
    )


def annotate_observed_debt_streaks(debt: pd.DataFrame) -> pd.DataFrame:
    output = debt.copy()
    output["ObservedDebtSinceSnapshotDate"] = pd.NaT
    output["ObservedDebtSnapshotCount"] = 0
    tracker: dict[tuple[str, str], tuple[pd.Timestamp, int]] = {}
    for snapshot_date in sorted(output["SnapshotDate"].drop_duplicates()):
        indexes = output.index[output["SnapshotDate"].eq(snapshot_date) & output["VelocityDebt"]]
        next_tracker: dict[tuple[str, str], tuple[pd.Timestamp, int]] = {}
        for index in indexes:
            key = (str(output.at[index, "Location"]), str(output.at[index, "SKU"]))
            first_date, count = tracker.get(key, (snapshot_date, 0))
            next_tracker[key] = (first_date, count + 1)
            output.at[index, "ObservedDebtSinceSnapshotDate"] = first_date
            output.at[index, "ObservedDebtSnapshotCount"] = count + 1
        tracker = next_tracker
    output["ObservedDebtStreakDays"] = (
        output["SnapshotDate"] - output["ObservedDebtSinceSnapshotDate"]
    ).dt.days.fillna(0)
    return output


def build_latest_age_summary(debt: pd.DataFrame) -> pd.DataFrame:
    latest = debt[debt["SnapshotDate"].eq(debt["SnapshotDate"].max()) & debt["VelocityDebt"]].copy()
    latest["ObservedDebtAgeBucket"] = pd.cut(
        latest["ObservedDebtStreakDays"],
        bins=[-1, 6, 13, float("inf")],
        labels=["Observed < 7 days", "Observed 7-13 days", "Observed >= 14 days"],
    )
    return (
        latest.groupby(["VelocityDebtDirection", "ObservedDebtAgeBucket"], observed=True, as_index=False)
        .agg(
            DebtLocations=("Location", "size"),
            PhysicalQty=("PhysicalQty", "sum"),
            DemotionPremiumLocationStepProxy=("DemotionPremiumLocationStepProxy", "sum"),
        )
        .sort_values(["VelocityDebtDirection", "ObservedDebtAgeBucket"])
    )


def build_turnover(debt: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted(debt["SnapshotDate"].drop_duplicates())
    frames: list[pd.DataFrame] = []
    for old_date, new_date in zip(dates[:-1], dates[1:], strict=True):
        old = debt[debt["SnapshotDate"].eq(old_date) & debt["VelocityDebt"]].copy()
        new = debt[debt["SnapshotDate"].eq(new_date)].copy()
        next_key = new.set_index(["Location", "SKU"])["VelocityDebt"]
        next_skus = set(new["SKU"])
        key = pd.MultiIndex.from_frame(old[["Location", "SKU"]])
        old["NextSnapshotDate"] = new_date
        old["DaysToNextSnapshot"] = (new_date - old_date).days
        old["SameSKULocationObservedNext"] = key.isin(next_key.index)
        old["SameSKULocationDebtNext"] = (
            next_key.reindex(key).fillna(False).astype(bool).to_numpy()
        )
        old["SKUObservedAnywhereNext"] = old["SKU"].isin(next_skus)
        old["TurnoverOutcome"] = np.select(
            [
                old["SameSKULocationDebtNext"],
                old["SameSKULocationObservedNext"],
                old["SKUObservedAnywhereNext"],
            ],
            [
                "Same SKU-location still in velocity debt",
                "Same SKU-location observed but debt resolved",
                "SKU observed elsewhere; original location turned over",
            ],
            default="SKU absent from scoped floor; original location turned over",
        )
        frames.append(old)
    detail = pd.concat(frames, ignore_index=True)
    summary = (
        detail.groupby(["SnapshotDate", "NextSnapshotDate", "TurnoverOutcome"], as_index=False)
        .agg(
            DebtLocations=("Location", "size"),
            PhysicalQtyAtStart=("PhysicalQty", "sum"),
            PremiumLocationStepProxyAtStart=("DemotionPremiumLocationStepProxy", "sum"),
        )
        .sort_values(["SnapshotDate", "TurnoverOutcome"])
    )
    return detail, summary


def policy_states(
    panel: pd.DataFrame,
    events: pd.DataFrame,
    inventory_dates: list[pd.Timestamp],
) -> dict[tuple[str, pd.Timestamp], dict[str, str]]:
    panel = panel.copy()
    panel["SnapshotEffectiveEST"] = pd.to_datetime(panel["SnapshotEffectiveEST"], utc=True)
    events = events.copy()
    events["SnapshotEffectiveEST"] = pd.to_datetime(events["SnapshotEffectiveEST"], utc=True)
    policies = sorted(events["Policy"].unique())
    forecast_frames = [
        (effective, scoped.set_index("SKU")["Velocity"])
        for effective, scoped in panel.groupby("SnapshotEffectiveEST", sort=True)
    ]
    result: dict[tuple[str, pd.Timestamp], dict[str, str]] = {}

    for policy in policies:
        state: dict[str, str] = {}
        cursor = 0
        policy_events = events[events["Policy"].eq(policy)]
        for inventory_date in inventory_dates:
            asof_utc = (
                inventory_date.tz_localize("America/New_York")
                + pd.Timedelta(days=1)
            ).tz_convert("UTC")
            while cursor < len(forecast_frames) and forecast_frames[cursor][0] <= asof_utc:
                effective, forecast = forecast_frames[cursor]
                for sku, tier in forecast.items():
                    state.setdefault(sku, tier)
                scoped_events = policy_events[policy_events["SnapshotEffectiveEST"].eq(effective)]
                for row in scoped_events.itertuples():
                    state[row.SKU] = row.NewRoutingTier
                cursor += 1
            result[(policy, inventory_date)] = state.copy()
    return result


def build_policy_summary(
    inventory: pd.DataFrame,
    panel: pd.DataFrame,
    stability_events: pd.DataFrame,
) -> pd.DataFrame:
    inventory_dates = sorted(inventory["SnapshotDate"].drop_duplicates())
    states = policy_states(panel, stability_events, inventory_dates)
    rows: list[dict[str, object]] = []
    for (policy, snapshot_date), state in states.items():
        scoped = inventory[inventory["SnapshotDate"].eq(snapshot_date)].copy()
        target = scoped["SKU"].map(state)
        debt = enrich_debt(scoped, target, "PolicyRoutingVelocity")
        rows.append(
            {
                "Policy": policy,
                "SnapshotDate": snapshot_date,
                "TargetCoverageLocations": int(debt["PolicyRoutingVelocity"].notna().sum()),
                "OccupiedLocations": int(debt["Location"].nunique()),
                "VelocityDebtLocations": int(debt["VelocityDebt"].sum()),
                "DemotionDebtLocations": int(debt["VelocityDebtDirection"].eq("DemotionDebt").sum()),
                "PromotionDebtLocations": int(debt["VelocityDebtDirection"].eq("PromotionDebt").sum()),
                "DemotionPremiumLocationDebtLocations": int(
                    debt["DemotionPremiumLocationDebt"].sum()
                ),
                "VelocityDebtTierSteps": float(debt["VelocityDebtTierSteps"].sum()),
                "DemotionPremiumLocationStepProxy": float(
                    debt["DemotionPremiumLocationStepProxy"].sum()
                ),
                "VelocityDebtPhysicalQty": float(debt["VelocityDebtPhysicalQty"].sum()),
            }
        )
    return pd.DataFrame(rows).sort_values(["SnapshotDate", "Policy"])


def build_policy_tradeoff(
    panel: pd.DataFrame,
    stability_events: pd.DataFrame,
    policy_map_debt: pd.DataFrame,
) -> pd.DataFrame:
    panel = panel.copy()
    panel["SnapshotEffectiveEST"] = pd.to_datetime(panel["SnapshotEffectiveEST"], utc=True)
    stability_events = stability_events.copy()
    stability_events["SnapshotEffectiveEST"] = pd.to_datetime(
        stability_events["SnapshotEffectiveEST"],
        utc=True,
    )
    latest_date = policy_map_debt["SnapshotDate"].max()
    latest_debt = policy_map_debt[policy_map_debt["SnapshotDate"].eq(latest_date)].set_index(
        "Policy"
    )
    rows: list[dict[str, object]] = []

    for policy in sorted(stability_events["Policy"].unique()):
        state: dict[str, str] = {}
        total_demand_touches = 0.0
        premium_demand_touches = 0.0
        aa_demand_touches = 0.0
        policy_events = stability_events[stability_events["Policy"].eq(policy)]
        for effective, snapshot in panel.groupby("SnapshotEffectiveEST", sort=True):
            for row in snapshot.itertuples():
                state.setdefault(row.SKU, row.Velocity)
            scoped_events = policy_events[policy_events["SnapshotEffectiveEST"].eq(effective)]
            for row in scoped_events.itertuples():
                state[row.SKU] = row.NewRoutingTier

            evaluable = snapshot[snapshot["OutcomeIntervalDays"].notna()].copy()
            evaluable["PolicyRoutingVelocity"] = evaluable["SKU"].map(state)
            outcome = evaluable["OutcomeToNextSnapshotDemandPhysicalTouches"].fillna(0)
            total_demand_touches += float(outcome.sum())
            premium_demand_touches += float(
                outcome[evaluable["PolicyRoutingVelocity"].isin(PREMIUM_TIERS)].sum()
            )
            aa_demand_touches += float(
                outcome[evaluable["PolicyRoutingVelocity"].eq("AA")].sum()
            )

        latest = latest_debt.loc[policy]
        rows.append(
            {
                "Policy": policy,
                "AppliedRoutingChanges": len(policy_events),
                "UniqueSKUsChanged": int(policy_events["SKU"].nunique()),
                "AAorAOutcomeDemandTouchCapturePct": round(
                    100 * premium_demand_touches / total_demand_touches,
                    2,
                )
                if total_demand_touches
                else 0,
                "AAOutcomeDemandTouchCapturePct": round(
                    100 * aa_demand_touches / total_demand_touches,
                    2,
                )
                if total_demand_touches
                else 0,
                "LatestMapDebtSnapshotDate": latest_date,
                "LatestVelocityDebtLocations": int(latest["VelocityDebtLocations"]),
                "LatestDemotionDebtLocations": int(latest["DemotionDebtLocations"]),
                "LatestPromotionDebtLocations": int(latest["PromotionDebtLocations"]),
                "LatestDemotionPremiumLocationStepProxy": float(
                    latest["DemotionPremiumLocationStepProxy"]
                ),
                "LatestVelocityDebtPhysicalQty": float(latest["VelocityDebtPhysicalQty"]),
            }
        )
    return pd.DataFrame(rows)


def write_outputs(
    args: argparse.Namespace,
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    location_snapshot: pd.DataFrame,
    snapshot_summary: pd.DataFrame,
    turnover_detail: pd.DataFrame,
    turnover_summary: pd.DataFrame,
    latest_age_summary: pd.DataFrame,
    policy_summary: pd.DataFrame,
    policy_tradeoff: pd.DataFrame,
) -> None:
    payload: dict[str, object] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "inputs": {
            "inventory": {"path": relative(args.inventory), "sha256": sha256(args.inventory)},
            "panel": {"path": relative(args.panel), "sha256": sha256(args.panel)},
            "stability_events": {
                "path": relative(args.stability_events),
                "sha256": sha256(args.stability_events),
            },
        },
        "outputs": {},
        "rows": {
            "location_snapshot": len(location_snapshot),
            "turnover_detail": len(turnover_detail),
            "policy_summary": len(policy_summary),
            "policy_tradeoff": len(policy_tradeoff),
        },
        "definitions": {
            "velocity_debt": "Occupied location velocity suffix differs from the target routing velocity.",
            "demotion_debt": "The occupied location is faster than the target routing velocity.",
            "promotion_debt": "The occupied location is slower than the target routing velocity.",
            "demotion_premium_location_step_proxy": "Occupied premium locations multiplied by downward velocity tier steps.",
        },
        "limitations": [
            "Inventory snapshots observe live state only; disappearance does not prove whether stock depleted through picks, moved manually, or left scope another way.",
            "Inventory snapshots are date-grain captures, so policy as-of joins use end-of-day Eastern time.",
            "Velocity debt measures suffix mismatch, not full SlotTier category/size mismatch.",
            "Only six inventory snapshots are currently available.",
        ],
    }
    try:
        location_snapshot.to_parquet(temporary["location_snapshot"], index=False, compression="zstd")
        snapshot_summary.to_csv(temporary["snapshot_summary"], index=False)
        turnover_detail.to_parquet(temporary["turnover_detail"], index=False, compression="zstd")
        turnover_summary.to_csv(temporary["turnover_summary"], index=False)
        latest_age_summary.to_csv(temporary["latest_age_summary"], index=False)
        policy_summary.to_csv(temporary["policy_summary"], index=False)
        policy_tradeoff.to_csv(temporary["policy_tradeoff"], index=False)
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
    inventory = load_inventory(args.inventory)
    location_snapshot = enrich_debt(
        inventory,
        inventory["ForecastTargetVelocity"],
        "ForecastTargetVelocity",
    )
    location_snapshot = annotate_observed_debt_streaks(location_snapshot)
    snapshot_summary = summarize_location_debt(location_snapshot)
    turnover_detail, turnover_summary = build_turnover(location_snapshot)
    latest_age_summary = build_latest_age_summary(location_snapshot)
    panel = pd.read_parquet(args.panel)
    stability_events = pd.read_parquet(args.stability_events)
    policy_summary = build_policy_summary(inventory, panel, stability_events)
    policy_tradeoff = build_policy_tradeoff(panel, stability_events, policy_summary)
    write_outputs(
        args,
        outputs,
        temporary,
        location_snapshot,
        snapshot_summary,
        turnover_detail,
        turnover_summary,
        latest_age_summary,
        policy_summary,
        policy_tradeoff,
    )
    print("Forecast-target map debt:")
    print(snapshot_summary.to_string(index=False))
    print("\nLatest stability-policy map debt:")
    latest = policy_summary["SnapshotDate"].max()
    print(policy_summary[policy_summary["SnapshotDate"].eq(latest)].to_string(index=False))
    print("\nStability-policy physical trade-off:")
    print(policy_tradeoff.to_string(index=False))
    print(f"\nMap-debt outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
