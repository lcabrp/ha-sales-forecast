"""Replay prototype velocity policies against confirmed AX forecast snapshots.

Investigation only. This script does not modify the ingestion pipeline, AX
files, monitoring SQLite, or any approved map. It emits scratch CSV summaries.

The score is deliberately simple and explainable. It is a starting point for
what-if analysis, not the final production policy.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from sql_utils import get_ax_engine  # noqa: E402


MONITORING_DB = PROJECT_ROOT / "Output" / "Monitoring" / "Monitoring_History.db"
REPLEN_SQL = PROJECT_ROOT / "scratch" / "velocity_policy_sales_order_replen_extract.sql"
OUTPUT_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
FD_COLUMNS = [f"FD{i}" for i in range(1, 15)]
TIER_ORDER = ("AA", "A", "B", "C")
TIER_RANK = {tier: rank for rank, tier in enumerate(TIER_ORDER)}
CUTOVER_EST = pd.Timestamp("2026-05-07 14:40:00", tz="America/New_York")


@dataclass(frozen=True)
class Policy:
    name: str
    forecast_weight: float
    recent_weight: float
    seasonal_weight: float
    confirmations_required: int


POLICIES = (
    Policy("forecast_cartons_only", 1.0, 0.0, 0.0, 1),
    Policy("all_consumed_recent_only", 0.0, 1.0, 0.0, 1),
    Policy("hybrid_immediate", 1.0, 1.0, 0.75, 1),
    Policy("hybrid_two_confirmation", 1.0, 1.0, 0.75, 2),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=MONITORING_DB)
    parser.add_argument("--sql", type=Path, default=REPLEN_SQL)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--chunk-size", type=int, default=100_000)
    return parser.parse_args()


def load_confirmed_snapshots(db_path: Path) -> list[tuple[pd.Timestamp, Path]]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT EffectiveFromEST, SourceFile, ArchivedSourcePath, SourcePath
            FROM forecast_snapshot_versions
            WHERE IsConfirmedAXUpload = 1
            ORDER BY EffectiveFromEST
            """
        ).fetchall()

    snapshots: list[tuple[pd.Timestamp, Path]] = []
    for effective_from, source_file, archived_path, source_path in rows:
        candidates = [
            Path(archived_path) if archived_path else None,
            Path(source_path) if source_path else None,
            PROJECT_ROOT / "Output" / "Ingestion" / source_file,
        ]
        resolved = [
            candidate if candidate.is_absolute() else PROJECT_ROOT / candidate
            for candidate in candidates
            if candidate is not None
        ]
        path = next((candidate for candidate in resolved if candidate.exists()), None)
        if path is None:
            raise FileNotFoundError(
                f"Confirmed snapshot does not exist. Checked: {', '.join(str(p) for p in resolved)}"
            )
        snapshots.append((pd.Timestamp(effective_from), path))
    return snapshots


def stream_replenishment_touches(sql_path: Path, chunk_size: int) -> pd.DataFrame:
    query = sql_path.read_text(encoding="utf-8")
    seen: set[str] = set()
    frames: list[pd.DataFrame] = []
    engine = get_ax_engine()
    with engine.connect() as conn:
        for chunk in pd.read_sql_query(query, conn, chunksize=chunk_size):
            chunk["TouchKey"] = chunk["TouchKey"].astype(str)
            unique = chunk.drop_duplicates("TouchKey", keep="first")
            unique = unique.loc[~unique["TouchKey"].isin(seen)].copy()
            seen.update(unique["TouchKey"].tolist())
            frames.append(unique)

    touches = pd.concat(frames, ignore_index=True)
    touches["SKU"] = touches["SKU"].astype(str).str.strip().str.upper()
    touches["ReplenCreatedDateTimeUtc"] = pd.to_datetime(
        touches["ReplenCreatedDateTimeUtc"], utc=True
    )
    touches["ReplenTouchInventQty"] = pd.to_numeric(
        touches["ReplenTouchInventQty"], errors="coerce"
    )
    return touches


def read_snapshot(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        usecols=["SKU", "Velocity", "ProductGroupCode", "SizeGroupCode", *FD_COLUMNS],
    )
    frame["SKU"] = frame["SKU"].astype(str).str.strip().str.upper()
    frame["Velocity"] = frame["Velocity"].astype(str).str.strip().str.upper()
    frame["FD14Units"] = frame[FD_COLUMNS].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    return frame[["SKU", "Velocity", "ProductGroupCode", "SizeGroupCode", "FD14Units"]].drop_duplicates("SKU")


def add_touch_features(
    snapshot: pd.DataFrame,
    touches: pd.DataFrame,
    effective_from: pd.Timestamp,
) -> pd.DataFrame:
    as_of_utc = effective_from.tz_convert("UTC")
    hist = touches[touches["ReplenCreatedDateTimeUtc"] < as_of_utc].copy()
    recent_start = as_of_utc - pd.Timedelta(days=56)
    seasonal_start = as_of_utc - pd.Timedelta(days=393)
    seasonal_end = as_of_utc - pd.Timedelta(days=337)

    recent = (
        hist[hist["ReplenCreatedDateTimeUtc"] >= recent_start]
        .groupby("SKU")
        .size()
        .rename("RecentAllConsumedTouches56d")
    )
    seasonal = (
        hist[
            (hist["ReplenCreatedDateTimeUtc"] >= seasonal_start)
            & (hist["ReplenCreatedDateTimeUtc"] < seasonal_end)
        ]
        .groupby("SKU")
        .size()
        .rename("SeasonalAllConsumedTouches56d")
    )
    # Prototype only: median source-line quantity is not the ingestion
    # CaseQty and not the authoritative historical last-put quantity. Keep the
    # proxy until the next replay version joins the separately persisted facts.
    case_qty = (
        hist.groupby("SKU")["ReplenTouchInventQty"]
        .median()
        .rename("CaseQtyProxy")
    )

    scored = snapshot.merge(recent, on="SKU", how="left")
    scored = scored.merge(seasonal, on="SKU", how="left")
    scored = scored.merge(case_qty, on="SKU", how="left")
    scored["CaseQtyProxy"] = scored["CaseQtyProxy"].fillna(
        scored.groupby(["ProductGroupCode", "SizeGroupCode"])["CaseQtyProxy"].transform("median")
    )
    scored["CaseQtyProxy"] = scored["CaseQtyProxy"].fillna(36).clip(lower=1)
    scored["ForecastCartonsPerDay"] = scored["FD14Units"] / scored["CaseQtyProxy"] / 14.0
    scored["RecentTouchesPerDay"] = scored["RecentAllConsumedTouches56d"].fillna(0) / 56.0
    scored["SeasonalTouchesPerDay"] = scored["SeasonalAllConsumedTouches56d"].fillna(0) / 56.0
    return scored


def assign_capacity_bands(frame: pd.DataFrame, score_column: str, quotas: dict[str, int]) -> pd.Series:
    ordered = frame.sort_values([score_column, "SKU"], ascending=[False, True]).copy()
    values = np.full(len(ordered), "C", dtype=object)
    cursor = 0
    for tier in ("AA", "A", "B"):
        end = min(cursor + quotas[tier], len(values))
        values[cursor:end] = tier
        cursor = end
    assigned = pd.Series(values, index=ordered.index)
    return assigned.reindex(frame.index)


def _changed(left: pd.Series, right: pd.Series) -> int:
    aligned = pd.concat([left.rename("left"), right.rename("right")], axis=1).dropna()
    return int((aligned["left"] != aligned["right"]).sum())


def replay_policy(
    policy: Policy,
    snapshots: list[tuple[pd.Timestamp, Path]],
    touches: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    first_effective, first_path = snapshots[0]
    initial = add_touch_features(read_snapshot(first_path), touches, first_effective)
    quotas = initial["Velocity"].value_counts().reindex(TIER_ORDER, fill_value=0).astype(int).to_dict()

    state = initial.set_index("SKU")["Velocity"].copy()
    pending = pd.Series(index=state.index, dtype=object)
    pending_count = pd.Series(0, index=state.index, dtype=int)
    prior_legacy = state.copy()
    rows: list[dict[str, object]] = []
    detail_frames: list[pd.DataFrame] = []

    for effective_from, path in snapshots:
        scored = add_touch_features(read_snapshot(path), touches, effective_from)
        scored["BurdenScore"] = (
            policy.forecast_weight * scored["ForecastCartonsPerDay"]
            + policy.recent_weight * scored["RecentTouchesPerDay"]
            + policy.seasonal_weight * scored["SeasonalTouchesPerDay"]
        )
        scored["TargetTier"] = assign_capacity_bands(scored, "BurdenScore", quotas)
        indexed = scored.set_index("SKU")

        universe = state.index.union(indexed.index)
        state = state.reindex(universe).fillna("C")
        pending = pending.reindex(universe)
        pending_count = pending_count.reindex(universe).fillna(0).astype(int)
        target = indexed["TargetTier"].reindex(universe).fillna("C")
        legacy = indexed["Velocity"].reindex(universe).fillna("C")

        same_pending = pending.eq(target)
        pending = target
        pending_count = np.where(same_pending, pending_count + 1, 1)
        pending_count = pd.Series(pending_count, index=universe)
        eligible = target.ne(state) & pending_count.ge(policy.confirmations_required)
        previous_state = state.copy()
        state.loc[eligible] = target.loc[eligible]

        after_cutover = effective_from >= CUTOVER_EST
        rows.append(
            {
                "Policy": policy.name,
                "EffectiveFromEST": effective_from.isoformat(),
                "AfterCutover": after_cutover,
                "SnapshotRows": len(indexed),
                "LegacyChangesFromPrior": _changed(prior_legacy, legacy),
                "TargetChangesFromPriorState": _changed(previous_state, target),
                "AppliedStableChanges": _changed(previous_state, state),
                "StableVsLegacyDifferences": _changed(state, legacy),
                "StableAA": int((state == "AA").sum()),
                "StableA": int((state == "A").sum()),
                "StableB": int((state == "B").sum()),
                "StableC": int((state == "C").sum()),
            }
        )
        prior_legacy = legacy

        detail = indexed[
            [
                "Velocity",
                "TargetTier",
                "BurdenScore",
                "ForecastCartonsPerDay",
                "RecentTouchesPerDay",
                "SeasonalTouchesPerDay",
                "CaseQtyProxy",
            ]
        ].copy()
        detail["StableTier"] = state.reindex(detail.index)
        detail["Policy"] = policy.name
        detail["EffectiveFromEST"] = effective_from.isoformat()
        detail_frames.append(detail.reset_index())

    return pd.DataFrame(rows), pd.concat(detail_frames, ignore_index=True)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshots = load_confirmed_snapshots(args.db)
    touches = stream_replenishment_touches(args.sql, args.chunk_size)

    summary_frames: list[pd.DataFrame] = []
    detail_frames: list[pd.DataFrame] = []
    for policy in POLICIES:
        summary, detail = replay_policy(policy, snapshots, touches)
        summary_frames.append(summary)
        detail_frames.append(detail)

    summary = pd.concat(summary_frames, ignore_index=True)
    detail = pd.concat(detail_frames, ignore_index=True)
    summary_path = args.output_dir / "velocity_policy_replay_summary.csv"
    detail_path = args.output_dir / "velocity_policy_replay_detail.csv"
    summary.to_csv(summary_path, index=False)
    detail.to_csv(detail_path, index=False)

    print(summary[summary["AfterCutover"]].to_string(index=False))
    print(f"\nSummary: {summary_path}")
    print(f"Detail:  {detail_path}")


if __name__ == "__main__":
    main()
