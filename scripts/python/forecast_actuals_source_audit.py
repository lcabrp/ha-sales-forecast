"""Audit forecast-evaluation actuals before querying live AX.

The monitoring repo is the preferred producer for daily operational facts.  At
present it persists aggregate daily Pick metrics, inventory/inbound snapshots,
and an older portable DirectPick SKU-day export.  Forecast allocation scoring,
however, requires a current SKU-day actuals fact.  This audit reports what each
repo can support and recommends live AX only when no current portable SKU-day
artifact covers the requested evaluation window.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from output_paths import PROJECT_ROOT


DEFAULT_MONITORING_REPO = PROJECT_ROOT.parent / "ha-kydc-monitoring"
DEFAULT_MONITORING_DB = DEFAULT_MONITORING_REPO / "Output" / "Monitoring" / "Monitoring_History.db"
DEFAULT_LOCAL_ACTUALS = (
    PROJECT_ROOT
    / "Output"
    / "ForecastAccuracy"
    / "history"
    / "parquet"
    / "actual_sku_day_modified.parquet"
)
MONITORING_SKU_DAY_CANDIDATES = (
    Path("Output/ForecastAccuracy/direct_pick/actual_sku_day_modified.parquet"),
    Path("Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet"),
)
MONITORING_DIAGNOSTIC_EXPORT = Path(
    "scratch/velocity_policy_replay/direct_pick_sku_day_15mo.parquet"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check monitoring/local DirectPick actuals before using live AX."
    )
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--through-date", required=True, type=date.fromisoformat)
    parser.add_argument("--monitoring-repo", type=Path, default=DEFAULT_MONITORING_REPO)
    parser.add_argument("--monitoring-db", type=Path, default=DEFAULT_MONITORING_DB)
    parser.add_argument("--local-actuals", type=Path, default=DEFAULT_LOCAL_ACTUALS)
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def parquet_summary(path: Path, source_role: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": str(path.resolve()),
        "source_role": source_role,
        "exists": path.exists(),
    }
    if not path.exists():
        return summary

    parquet_file = pq.ParquetFile(path)
    columns = parquet_file.schema.names
    date_column = next((name for name in ("ActualDate", "PickDate", "Date") if name in columns), None)
    unit_column = next(
        (name for name in ("SoldUnits", "PickUnits", "Units") if name in columns), None
    )
    summary.update(
        {
            "bytes": path.stat().st_size,
            "columns": columns,
            "date_column": date_column,
            "unit_column": unit_column,
            "sku_day_capable": "SKU" in columns and date_column is not None and unit_column is not None,
        }
    )
    if date_column is None:
        return summary

    dates = pd.to_datetime(pd.read_parquet(path, columns=[date_column])[date_column], errors="coerce")
    summary["min_date"] = None if dates.dropna().empty else dates.min().date().isoformat()
    summary["max_date"] = None if dates.dropna().empty else dates.max().date().isoformat()
    summary["rows"] = int(parquet_file.metadata.num_rows)
    return summary


def monitoring_daily_summary(db_path: Path, start_date: date, through_date: date) -> dict[str, Any]:
    summary: dict[str, Any] = {"path": str(db_path.resolve()), "exists": db_path.exists()}
    if not db_path.exists():
        return summary

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            """
            SELECT
                MIN(MetricDate),
                MAX(MetricDate),
                COUNT(DISTINCT MetricDate),
                COALESCE(SUM(Units), 0)
            FROM daily_activity_metrics
            WHERE ActivityType = 'Pick'
              AND MetricDate >= ?
              AND MetricDate <= ?
            """,
            (start_date.isoformat(), through_date.isoformat()),
        ).fetchone()
        all_range = connection.execute(
            """
            SELECT MIN(MetricDate), MAX(MetricDate), COUNT(DISTINCT MetricDate)
            FROM daily_activity_metrics
            WHERE ActivityType = 'Pick'
            """
        ).fetchone()

    expected_days = (through_date - start_date).days + 1
    window_days = int(row[2] or 0)
    summary.update(
        {
            "grain": "operational_day_total",
            "date_basis": "Eastern monitoring window",
            "target_notes": "Pickface/profile-filtered completed DirectPick from the layout monitor",
            "available_min_date": all_range[0],
            "available_max_date": all_range[1],
            "available_days": int(all_range[2] or 0),
            "window_min_date": row[0],
            "window_max_date": row[1],
            "window_days": window_days,
            "expected_window_days": expected_days,
            "window_pick_units": float(row[3] or 0),
            "complete_window": window_days == expected_days,
            "sku_day_capable": False,
        }
    )
    return summary


def covers_through(summary: dict[str, Any], through_date: date) -> bool:
    max_date = summary.get("max_date")
    return bool(summary.get("sku_day_capable") and max_date and date.fromisoformat(max_date) >= through_date)


def main() -> int:
    args = parse_args()
    if args.through_date < args.start_date:
        raise ValueError("--through-date must be on or after --start-date")

    monitoring_repo = args.monitoring_repo.resolve()
    monitoring_candidates = [
        parquet_summary(monitoring_repo / relative, "monitoring_canonical_candidate")
        for relative in MONITORING_SKU_DAY_CANDIDATES
    ]
    monitoring_diagnostic = parquet_summary(
        monitoring_repo / MONITORING_DIAGNOSTIC_EXPORT,
        "monitoring_diagnostic_scratch_export",
    )
    local_actuals = parquet_summary(args.local_actuals.resolve(), "forecast_repo_portable_mirror")
    monitoring_daily = monitoring_daily_summary(
        args.monitoring_db.resolve(), args.start_date, args.through_date
    )

    selected = next(
        (item for item in monitoring_candidates if covers_through(item, args.through_date)),
        None,
    )
    if selected is None and covers_through(local_actuals, args.through_date):
        selected = local_actuals

    if selected is not None:
        recommendation = "portable_sku_day_actuals"
        allocation_ready = True
        selected_path = selected["path"]
    else:
        recommendation = "live_ax_fallback_required_for_sku_day_refresh"
        allocation_ready = False
        selected_path = None

    report = {
        "requested_window": {
            "start_date": args.start_date.isoformat(),
            "through_date": args.through_date.isoformat(),
        },
        "monitoring_daily_pick_totals": monitoring_daily,
        "monitoring_sku_day_candidates": monitoring_candidates,
        "monitoring_diagnostic_export": monitoring_diagnostic,
        "forecast_repo_local_actuals": local_actuals,
        "decision": {
            "aggregate_volume_ready_from_monitoring": bool(
                monitoring_daily.get("complete_window")
            ),
            "allocation_score_ready": allocation_ready,
            "recommended_source": recommendation,
            "selected_sku_day_path": selected_path,
            "reason": (
                "Monitoring daily Pick totals can validate aggregate volume, but SKU allocation "
                "metrics require a current SKU-day fact. Live AX is only the fallback when no "
                "canonical monitoring artifact or forecast-repo mirror covers the window."
            ),
        },
    }

    print(json.dumps(report, indent=2))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
