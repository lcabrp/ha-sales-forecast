"""Build a compact shadow-only velocity-policy analysis panel.

This script reads local evidence and writes analysis artifacts only. It does not
change ingestion, AX payloads, location directives, or approved layout maps.

The detailed sales-order allocation extract remains local and ignored. The
GitHub-friendly outputs deliberately exclude sales-order identifiers and retain
only SKU-snapshot features, changed-tier events, and compact summaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MONITORING_DB = PROJECT_ROOT / "Output" / "Monitoring" / "Monitoring_History.db"
TOUCH_FACT = (
    PROJECT_ROOT
    / "scratch"
    / "velocity_policy_replay"
    / "physical_replen_touches_3y.parquet"
)
CUTOVER_MAP = (
    PROJECT_ROOT
    / "Output"
    / "Monitoring"
    / "deployments"
    / "20260507_144000_EDT"
    / "AX_Proposed_Zone_Map.csv"
)
OUTPUT_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
FD_COLUMNS = [f"FD{i}" for i in range(1, 15)]
TIER_RANK = {"C": 0, "B": 1, "A": 2, "AA": 3}
TIER_ORDER = ("AA", "A", "B", "C")
CATEGORY_LABELS = {
    "Demand": "Demand",
    "MinMaxUsedBySalesOrder": "MinMax",
    "ResetUsedBySalesOrder": "Reset",
}

PANEL_NAME = "velocity_policy_sku_snapshot_panel.parquet"
TRANSITIONS_NAME = "velocity_policy_transition_events.parquet"
SNAPSHOT_SUMMARY_NAME = "velocity_policy_snapshot_tier_summary.csv"
TRANSITION_SUMMARY_NAME = "velocity_policy_transition_summary.csv"
CAPACITY_NAME = "velocity_policy_capacity_reference.csv"
METADATA_NAME = "velocity_policy_shadow_metadata.json"


@dataclass(frozen=True)
class Snapshot:
    effective_est: pd.Timestamp
    source_file: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=MONITORING_DB)
    parser.add_argument("--touches", type=Path, default=TOUCH_FACT)
    parser.add_argument("--cutover-map", type=Path, default=CUTOVER_MAP)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing shadow artifacts intentionally.",
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
        "panel": output_dir / PANEL_NAME,
        "transitions": output_dir / TRANSITIONS_NAME,
        "snapshot_summary": output_dir / SNAPSHOT_SUMMARY_NAME,
        "transition_summary": output_dir / TRANSITION_SUMMARY_NAME,
        "capacity": output_dir / CAPACITY_NAME,
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
            "Shadow artifacts already exist. Pass --overwrite to replace them intentionally: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = temporary_paths(outputs)
    remove_existing(list(temporary.values()))
    return outputs, temporary


def normalize_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("America/New_York")
    return timestamp.tz_convert("America/New_York")


def resolve_candidate_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_confirmed_snapshots(db_path: Path) -> list[Snapshot]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT EffectiveFromEST, SourceFile, ArchivedSourcePath, SourcePath
            FROM forecast_snapshot_versions
            WHERE IsConfirmedAXUpload = 1
            ORDER BY EffectiveFromEST
            """
        ).fetchall()

    snapshots: list[Snapshot] = []
    for effective_from, source_file, archived_path, source_path in rows:
        candidates = [
            resolve_candidate_path(archived_path),
            resolve_candidate_path(source_path),
            PROJECT_ROOT / "Output" / "Ingestion" / source_file,
        ]
        path = next((candidate for candidate in candidates if candidate and candidate.exists()), None)
        if path is None:
            checked = ", ".join(str(candidate) for candidate in candidates if candidate)
            raise FileNotFoundError(f"Confirmed snapshot is missing. Checked: {checked}")
        snapshots.append(
            Snapshot(
                effective_est=normalize_timestamp(str(effective_from)),
                source_file=str(source_file),
                path=path,
            )
        )

    if len(snapshots) < 2:
        raise ValueError("At least two confirmed AX snapshots are required.")
    return snapshots


def load_physical_touches(path: Path) -> pd.DataFrame:
    touches = pd.read_parquet(path)
    required = {
        "TouchKey",
        "SKU",
        "ReplenCategory",
        "ReplenCreatedDateTimeUtc",
        "FinalPutInventQty",
    }
    missing = sorted(required - set(touches.columns))
    if missing:
        raise ValueError(f"Physical-touch fact is missing columns: {', '.join(missing)}")
    if touches["TouchKey"].duplicated().any():
        raise ValueError("Physical-touch fact contains duplicate TouchKey rows.")

    touches = touches.loc[:, sorted(required)].copy()
    touches["SKU"] = touches["SKU"].fillna("").astype(str).str.strip().str.upper()
    touches["ReplenCreatedDateTimeUtc"] = pd.to_datetime(
        touches["ReplenCreatedDateTimeUtc"],
        utc=True,
    )
    touches["FinalPutInventQty"] = pd.to_numeric(touches["FinalPutInventQty"], errors="coerce")
    if touches["FinalPutInventQty"].isna().any():
        raise ValueError("Physical-touch fact contains missing final put quantities.")
    return touches


def read_snapshot(snapshot: Snapshot) -> pd.DataFrame:
    required = {
        "SKU",
        "SlotTier",
        "Velocity",
        "ProductGroupCode",
        "SizeGroupCode",
        "ReplenishmentThreshold",
        *FD_COLUMNS,
    }
    frame = pd.read_csv(snapshot.path, usecols=lambda column: column in required)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{snapshot.path.name} is missing columns: {', '.join(missing)}")

    frame["SKU"] = frame["SKU"].fillna("").astype(str).str.strip().str.upper()
    for column in ("SlotTier", "Velocity", "ProductGroupCode", "SizeGroupCode"):
        frame[column] = frame[column].fillna("").astype(str).str.strip().str.upper()
    for column in ("ReplenishmentThreshold", *FD_COLUMNS):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)

    frame["ForecastFD14Units"] = frame[FD_COLUMNS].sum(axis=1)
    frame["ForecastUnitsPerDay"] = frame["ForecastFD14Units"] / 14.0
    frame["SnapshotEffectiveEST"] = snapshot.effective_est.isoformat()
    frame["SnapshotSourceFile"] = snapshot.source_file
    columns = [
        "SnapshotEffectiveEST",
        "SnapshotSourceFile",
        "SKU",
        "SlotTier",
        "Velocity",
        "ProductGroupCode",
        "SizeGroupCode",
        "ReplenishmentThreshold",
        "ForecastFD14Units",
        "ForecastUnitsPerDay",
    ]
    return frame.loc[:, columns].drop_duplicates("SKU", keep="last")


def touch_aggregate(
    touches: pd.DataFrame,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    prefix: str,
    include_categories: bool = False,
) -> pd.DataFrame:
    scoped = touches[
        (touches["ReplenCreatedDateTimeUtc"] >= start_utc)
        & (touches["ReplenCreatedDateTimeUtc"] < end_utc)
    ]
    summary = (
        scoped.groupby("SKU", as_index=False)
        .agg(
            **{
                f"{prefix}PhysicalTouches": ("TouchKey", "nunique"),
                f"{prefix}ActualLastPutQty": ("FinalPutInventQty", "sum"),
            }
        )
    )
    if not include_categories:
        return summary

    for category, label in CATEGORY_LABELS.items():
        category_summary = (
            scoped[scoped["ReplenCategory"].eq(category)]
            .groupby("SKU", as_index=False)
            .agg(
                **{
                    f"{prefix}{label}PhysicalTouches": ("TouchKey", "nunique"),
                    f"{prefix}{label}ActualLastPutQty": ("FinalPutInventQty", "sum"),
                }
            )
        )
        summary = summary.merge(category_summary, on="SKU", how="outer")
    return summary


def trailing_median_last_put_qty(
    touches: pd.DataFrame,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
) -> pd.DataFrame:
    return (
        touches[
            (touches["ReplenCreatedDateTimeUtc"] >= start_utc)
            & (touches["ReplenCreatedDateTimeUtc"] < end_utc)
        ]
        .groupby("SKU", as_index=False)["FinalPutInventQty"]
        .median()
        .rename(columns={"FinalPutInventQty": "Trailing365dMedianActualLastPutQty"})
    )


def merge_features(panel: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    return panel.merge(features, on="SKU", how="left")


def fill_numeric_features(panel: pd.DataFrame) -> pd.DataFrame:
    protected = {
        "ReplenishmentThreshold",
        "ForecastFD14Units",
        "ForecastUnitsPerDay",
        "OutcomeIntervalDays",
    }
    numeric = [
        column
        for column in panel.columns
        if column not in protected
        and (
            column.endswith("PhysicalTouches")
            or column.endswith("ActualLastPutQty")
            or column == "Trailing365dMedianActualLastPutQty"
        )
    ]
    panel[numeric] = panel[numeric].fillna(0)
    return panel


def build_panel(snapshots: list[Snapshot], touches: pd.DataFrame) -> pd.DataFrame:
    panels: list[pd.DataFrame] = []
    for index, snapshot in enumerate(snapshots):
        effective_utc = snapshot.effective_est.tz_convert("UTC")
        panel = read_snapshot(snapshot)
        windows = (
            (28, "Recent28d", False),
            (56, "Recent56d", True),
            (91, "Recent91d", False),
        )
        for days, prefix, include_categories in windows:
            panel = merge_features(
                panel,
                touch_aggregate(
                    touches,
                    effective_utc - pd.Timedelta(days=days),
                    effective_utc,
                    prefix,
                    include_categories=include_categories,
                ),
            )

        panel = merge_features(
            panel,
            touch_aggregate(
                touches,
                effective_utc - pd.Timedelta(days=393),
                effective_utc - pd.Timedelta(days=337),
                "SeasonalPriorYear56d",
            ),
        )
        panel = merge_features(
            panel,
            trailing_median_last_put_qty(
                touches,
                effective_utc - pd.Timedelta(days=365),
                effective_utc,
            ),
        )

        next_snapshot = snapshots[index + 1] if index + 1 < len(snapshots) else None
        if next_snapshot is None:
            panel["OutcomeIntervalDays"] = np.nan
        else:
            next_utc = next_snapshot.effective_est.tz_convert("UTC")
            interval_days = (next_utc - effective_utc).total_seconds() / 86400.0
            panel["OutcomeIntervalDays"] = interval_days
            panel = merge_features(
                panel,
                touch_aggregate(
                    touches,
                    effective_utc,
                    next_utc,
                    "OutcomeToNextSnapshot",
                    include_categories=True,
                ),
            )
            panel["OutcomePhysicalTouchesPerDay"] = (
                panel["OutcomeToNextSnapshotPhysicalTouches"] / interval_days
            )
            panel["OutcomeActualLastPutQtyPerDay"] = (
                panel["OutcomeToNextSnapshotActualLastPutQty"] / interval_days
            )

        panels.append(fill_numeric_features(panel))
    return pd.concat(panels, ignore_index=True)


def load_capacity_reference(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, usecols=["ZONEID"])
    frame["Velocity"] = frame["ZONEID"].str.extract(r"(AA|A|B|C)$", expand=False).fillna("OTHER")
    return (
        frame.groupby("Velocity", as_index=False)
        .size()
        .rename(columns={"size": "FrozenCutoverLocationCount"})
    )


def transition_direction(delta: pd.Series) -> pd.Series:
    return np.select([delta.gt(0), delta.lt(0)], ["Promotion", "Demotion"], default="Unchanged")


def build_transitions(panel: pd.DataFrame, snapshots: list[Snapshot]) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_events: list[pd.DataFrame] = []
    summary_rows: list[dict[str, object]] = []
    for old_snapshot, new_snapshot in zip(snapshots[:-1], snapshots[1:], strict=True):
        old = panel[panel["SnapshotEffectiveEST"].eq(old_snapshot.effective_est.isoformat())].copy()
        new = panel[panel["SnapshotEffectiveEST"].eq(new_snapshot.effective_est.isoformat())].copy()
        old = old.add_prefix("Old")
        new = new.add_prefix("New")
        shared = old.merge(new, left_on="OldSKU", right_on="NewSKU", how="inner")

        shared["OldTierRank"] = shared["OldVelocity"].map(TIER_RANK)
        shared["NewTierRank"] = shared["NewVelocity"].map(TIER_RANK)
        shared["TierRankDelta"] = shared["NewTierRank"] - shared["OldTierRank"]
        shared["TierSteps"] = shared["TierRankDelta"].abs()
        shared["Direction"] = transition_direction(shared["TierRankDelta"])
        shared["VelocityChanged"] = shared["TierSteps"].gt(0)
        shared["IsCtoAA"] = shared["OldVelocity"].eq("C") & shared["NewVelocity"].eq("AA")
        shared["IsAAtoC"] = shared["OldVelocity"].eq("AA") & shared["NewVelocity"].eq("C")
        shared["ForecastFD14UnitsDelta"] = (
            shared["NewForecastFD14Units"] - shared["OldForecastFD14Units"]
        )

        # This is a triage proxy, not measured relocation labor. Demotions get
        # extra weight because they can obsolete premium placement. Actual cost
        # still needs historical floor occupancy and movement evidence.
        direction_weight = np.where(shared["Direction"].eq("Demotion"), 2.0, 1.0)
        shared["TransitionPriorityPoints"] = (
            shared["TierSteps"]
            * direction_weight
            * (1.0 + np.log1p(shared["OldRecent56dPhysicalTouches"]))
        )

        events = shared[shared["VelocityChanged"]].copy()
        events["OldSnapshotEffectiveEST"] = old_snapshot.effective_est.isoformat()
        events["NewSnapshotEffectiveEST"] = new_snapshot.effective_est.isoformat()
        events["DaysBetweenSnapshots"] = (
            new_snapshot.effective_est - old_snapshot.effective_est
        ).total_seconds() / 86400.0
        all_events.append(events)

        summary_rows.append(
            {
                "OldSnapshotEffectiveEST": old_snapshot.effective_est.isoformat(),
                "NewSnapshotEffectiveEST": new_snapshot.effective_est.isoformat(),
                "SharedSKUs": len(shared),
                "VelocityChanges": int(shared["VelocityChanged"].sum()),
                "VelocityChangeRate": float(shared["VelocityChanged"].mean()),
                "Promotions": int(shared["Direction"].eq("Promotion").sum()),
                "Demotions": int(shared["Direction"].eq("Demotion").sum()),
                "MultiTierJumps": int(shared["TierSteps"].gt(1).sum()),
                "CtoAA": int(shared["IsCtoAA"].sum()),
                "AAtoC": int(shared["IsAAtoC"].sum()),
            }
        )

    events = pd.concat(all_events, ignore_index=True)
    return decorate_reversals(events), pd.DataFrame(summary_rows)


def decorate_reversals(events: pd.DataFrame) -> pd.DataFrame:
    events = events.sort_values(["NewSKU", "NewSnapshotEffectiveEST"]).reset_index(drop=True)
    effective = pd.to_datetime(events["NewSnapshotEffectiveEST"], utc=True)
    events["FirstOppositeDirectionDays"] = np.nan

    for _, indexes in events.groupby("NewSKU").groups.items():
        ordered = list(indexes)
        for position, row_index in enumerate(ordered):
            direction = events.at[row_index, "Direction"]
            for later_index in ordered[position + 1 :]:
                later_direction = events.at[later_index, "Direction"]
                if direction == later_direction:
                    continue
                delta_days = (effective.at[later_index] - effective.at[row_index]).total_seconds() / 86400
                events.at[row_index, "FirstOppositeDirectionDays"] = delta_days
                break

    for days in (14, 28, 56):
        events[f"ReversedWithin{days}d"] = events["FirstOppositeDirectionDays"].le(days)
    return events


def build_transition_summary(events: pd.DataFrame, base_summary: pd.DataFrame) -> pd.DataFrame:
    reversal = (
        events.groupby(["OldSnapshotEffectiveEST", "NewSnapshotEffectiveEST"], as_index=False)
        .agg(
            ReversedWithin14d=("ReversedWithin14d", "sum"),
            ReversedWithin28d=("ReversedWithin28d", "sum"),
            ReversedWithin56d=("ReversedWithin56d", "sum"),
            TransitionPriorityPoints=("TransitionPriorityPoints", "sum"),
        )
    )
    return base_summary.merge(
        reversal,
        on=["OldSnapshotEffectiveEST", "NewSnapshotEffectiveEST"],
        how="left",
    )


def build_snapshot_summary(
    panel: pd.DataFrame,
    capacity: pd.DataFrame,
) -> pd.DataFrame:
    summary = (
        panel.groupby(["SnapshotEffectiveEST", "Velocity"], as_index=False)
        .agg(
            SKUs=("SKU", "nunique"),
            ForecastFD14Units=("ForecastFD14Units", "sum"),
            Recent56dPhysicalTouches=("Recent56dPhysicalTouches", "sum"),
            Recent56dActualLastPutQty=("Recent56dActualLastPutQty", "sum"),
            Recent56dDemandPhysicalTouches=("Recent56dDemandPhysicalTouches", "sum"),
            Recent56dMinMaxPhysicalTouches=("Recent56dMinMaxPhysicalTouches", "sum"),
            Recent56dResetPhysicalTouches=("Recent56dResetPhysicalTouches", "sum"),
        )
    )
    return summary.merge(capacity, on="Velocity", how="left")


def metadata(
    args: argparse.Namespace,
    snapshots: list[Snapshot],
    panel: pd.DataFrame,
    transitions: pd.DataFrame,
    outputs: dict[str, Path],
) -> dict[str, object]:
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only",
        "production_logic_changed": False,
        "inputs": {
            "monitoring_db": str(args.db.relative_to(PROJECT_ROOT)),
            "physical_touch_fact": str(args.touches.relative_to(PROJECT_ROOT)),
            "physical_touch_fact_sha256": sha256(args.touches),
            "cutover_map": str(args.cutover_map.relative_to(PROJECT_ROOT)),
            "cutover_map_sha256": sha256(args.cutover_map),
            "confirmed_snapshots": [
                {
                    "effective_est": snapshot.effective_est.isoformat(),
                    "source_file": snapshot.source_file,
                    "path": str(snapshot.path.relative_to(PROJECT_ROOT)),
                    "sha256": sha256(snapshot.path),
                }
                for snapshot in snapshots
            ],
        },
        "outputs": {
            name: {
                "path": str(path.relative_to(PROJECT_ROOT)),
            }
            for name, path in outputs.items()
            if name != "metadata"
        },
        "rows": {
            "sku_snapshot_panel": len(panel),
            "transition_events": len(transitions),
        },
        "limitations": [
            "Confirmed AX upload CSVs do not carry the ingestion pipeline's calculated CaseQty.",
            "Observed final-put quantities are historical operational truth, not a replacement forecast.",
            "TransitionPriorityPoints is a triage proxy, not measured relocation labor.",
            "Historical floor occupancy is not yet joined, so physical relocation cost remains incomplete.",
        ],
    }


def write_outputs(
    outputs: dict[str, Path],
    temporary: dict[str, Path],
    panel: pd.DataFrame,
    transitions: pd.DataFrame,
    snapshot_summary: pd.DataFrame,
    transition_summary: pd.DataFrame,
    capacity: pd.DataFrame,
    metadata_payload: dict[str, object],
) -> None:
    try:
        panel.to_parquet(temporary["panel"], index=False, compression="zstd")
        transitions.to_parquet(temporary["transitions"], index=False, compression="zstd")
        snapshot_summary.to_csv(temporary["snapshot_summary"], index=False)
        transition_summary.to_csv(temporary["transition_summary"], index=False)
        capacity.to_csv(temporary["capacity"], index=False)
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
    snapshots = load_confirmed_snapshots(args.db)
    touches = load_physical_touches(args.touches)
    capacity = load_capacity_reference(args.cutover_map)
    panel = build_panel(snapshots, touches)
    transitions, base_transition_summary = build_transitions(panel, snapshots)
    transition_summary = build_transition_summary(transitions, base_transition_summary)
    snapshot_summary = build_snapshot_summary(panel, capacity)
    payload = metadata(args, snapshots, panel, transitions, outputs)
    write_outputs(
        outputs,
        temporary,
        panel,
        transitions,
        snapshot_summary,
        transition_summary,
        capacity,
        payload,
    )

    print(f"Confirmed snapshots: {len(snapshots):,}")
    print(f"SKU-snapshot rows:   {len(panel):,}")
    print(f"Transition events:   {len(transitions):,}")
    print("\nTransition summary:")
    print(transition_summary.to_string(index=False))
    print(f"\nShadow outputs: {args.output_dir}")


if __name__ == "__main__":
    main()
