"""Create a forward statistical shadow using corporate daily totals.

This is the transparent volume/allocation split used by the July closeout:

* corporate supplies the 14 daily total anchors, preserving its promotion and
  sales-calendar knowledge;
* recent monitoring-scope DirectPick history supplies the SKU allocation shape;
* largest-remainder rounding preserves every daily corporate total exactly.

The output is a shadow forecast for later evaluation, not an AX upload file.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from forecast_replacement_backtest import recent_daily_forecast
from forecast_schema import FD_COLUMNS, normalize_sku_series
from forecast_window_compare import add_anchored_candidates, query_live_ax_actuals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corporate-fwd", required=True, type=Path)
    parser.add_argument("--actuals", type=Path, help="Monitoring-scope SKU/day lookback Parquet.")
    parser.add_argument(
        "--live-ax",
        action="store_true",
        help="Use the monitoring-scope read-only AX fallback for the lookback.",
    )
    parser.add_argument("--lookback-days", type=int, default=56)
    parser.add_argument("--server", default="prodaxsql2")
    parser.add_argument("--database", default="DAX_PROD")
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def load_corporate_daily(path: Path) -> tuple[pd.DataFrame, pd.Timestamp, dict[str, Any]]:
    frame = pd.read_csv(path.resolve(), low_memory=False)
    missing = {"SKU", "ForecastStartDate", *FD_COLUMNS}.difference(frame.columns)
    if missing:
        raise ValueError(f"Corporate FwdDemand is missing columns: {sorted(missing)}")
    start = pd.Timestamp(pd.to_datetime(frame["ForecastStartDate"].iloc[0])).normalize()
    frame["SKU"] = normalize_sku_series(frame["SKU"])
    for column in FD_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).clip(lower=0)
    wide = frame.groupby("SKU", as_index=False)[FD_COLUMNS].sum()
    daily_parts: list[pd.DataFrame] = []
    for offset, column in enumerate(FD_COLUMNS):
        part = wide.loc[wide[column].gt(0), ["SKU", column]].rename(
            columns={column: "ForecastUnits"}
        )
        part["ForecastDate"] = start + pd.Timedelta(days=offset)
        part["Candidate"] = "corporate_raw"
        daily_parts.append(part[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]])
    daily = pd.concat(daily_parts, ignore_index=True)
    metadata = {
        "path": str(path.resolve()),
        "rows": int(len(frame)),
        "distinct_skus": int(frame["SKU"].nunique()),
        "forecast_start": start.date().isoformat(),
        "forecast_end": (start + pd.Timedelta(days=13)).date().isoformat(),
        "forecast_units": float(wide[FD_COLUMNS].sum().sum()),
        "daily_units": {column: float(wide[column].sum()) for column in FD_COLUMNS},
    }
    return daily, start, metadata


def load_actuals(path: Path, start: pd.Timestamp, lookback_days: int) -> pd.DataFrame:
    frame = pd.read_parquet(path.resolve())
    date_column = next(
        (column for column in ("ActualDate", "PickDate", "Date") if column in frame.columns),
        None,
    )
    unit_column = next(
        (column for column in ("SoldUnits", "PickUnits", "Units") if column in frame.columns),
        None,
    )
    if date_column is None or unit_column is None or "SKU" not in frame.columns:
        raise ValueError(f"Lookback actuals lack SKU/date/unit columns: {path}")
    actual = frame[[date_column, "SKU", unit_column]].rename(
        columns={date_column: "ActualDate", unit_column: "SoldUnits"}
    )
    actual["ActualDate"] = pd.to_datetime(actual["ActualDate"]).dt.normalize()
    actual["SKU"] = normalize_sku_series(actual["SKU"])
    actual["SoldUnits"] = pd.to_numeric(actual["SoldUnits"], errors="coerce").fillna(0)
    window_start = start - pd.Timedelta(days=lookback_days)
    actual = actual.loc[actual["ActualDate"].between(window_start, start - pd.Timedelta(days=1))]
    return actual.groupby(["ActualDate", "SKU"], as_index=False)["SoldUnits"].sum()


def daily_to_wide(daily: pd.DataFrame, candidate: str, start: pd.Timestamp) -> pd.DataFrame:
    frame = daily.loc[daily["Candidate"].eq(candidate)].copy()
    frame["ForecastDay"] = (frame["ForecastDate"] - start).dt.days + 1
    wide = (
        frame.pivot_table(
            index="SKU",
            columns="ForecastDay",
            values="ForecastUnits",
            aggfunc="sum",
            fill_value=0,
        )
        .rename(columns={day: f"FD{day}" for day in range(1, 15)})
        .reset_index()
    )
    for column in FD_COLUMNS:
        if column not in wide.columns:
            wide[column] = 0
        wide[column] = pd.to_numeric(wide[column], errors="coerce").fillna(0)
    return wide[["SKU", *FD_COLUMNS]].sort_values("SKU", kind="mergesort")


def main() -> int:
    args = parse_args()
    if args.lookback_days <= 0:
        raise ValueError("--lookback-days must be positive")
    corporate_daily, forecast_start, corporate_metadata = load_corporate_daily(
        args.corporate_fwd
    )
    lookback_start = (forecast_start - pd.Timedelta(days=args.lookback_days)).date()
    lookback_end = (forecast_start - pd.Timedelta(days=1)).date()
    if args.actuals:
        actuals = load_actuals(args.actuals, forecast_start, args.lookback_days)
        actual_source = {"kind": "portable_sku_day", "path": str(args.actuals.resolve())}
    elif args.live_ax:
        actuals = query_live_ax_actuals(
            lookback_start,
            lookback_end,
            args.server,
            args.database,
        )
        actual_source = {
            "kind": "live_ax_monitoring_scope_fallback",
            "server": args.server,
            "database": args.database,
        }
    else:
        raise ValueError("Provide --actuals or explicitly enable --live-ax.")

    recent, recent_metadata = recent_daily_forecast(
        actuals,
        forecast_start,
        args.lookback_days,
    )
    recent["Candidate"] = "independent_recent_shape"
    recent = recent[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]]
    combined = pd.concat([corporate_daily, recent], ignore_index=True)
    combined, generated_metadata = add_anchored_candidates(
        combined,
        ["corporate_total_recent_shape=independent_recent_shape:corporate_raw"],
    )
    combined = combined.sort_values(
        ["Candidate", "ForecastDate", "SKU"],
        kind="mergesort",
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(output_dir / "forward_daily_forecasts.parquet", index=False)
    for candidate in combined["Candidate"].unique():
        daily_to_wide(combined, str(candidate), forecast_start).to_csv(
            output_dir / f"{candidate}_fd14.csv",
            index=False,
        )
    actuals.to_parquet(output_dir / "lookback_actual_sku_day.parquet", index=False)

    totals = (
        combined.loc[combined["ForecastUnits"].gt(0)]
        .groupby("Candidate", as_index=False)
        .agg(
            ForecastUnits=("ForecastUnits", "sum"),
            ForecastedSKUs=("SKU", "nunique"),
            ForecastDays=("ForecastDate", "nunique"),
        )
        .sort_values("Candidate", kind="mergesort")
    )
    totals.to_csv(output_dir / "forward_candidate_totals.csv", index=False)
    metadata = {
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": "forward_shadow_not_yet_evaluable",
        "corporate": corporate_metadata,
        "lookback": {
            "start_date": lookback_start.isoformat(),
            "through_date": lookback_end.isoformat(),
            "days": args.lookback_days,
            "rows": int(len(actuals)),
            "distinct_skus": int(actuals["SKU"].nunique()),
            "sold_units": float(actuals["SoldUnits"].sum()),
            "source": actual_source,
            "recent_shape_metadata": recent_metadata,
        },
        "generated_candidates": generated_metadata,
        "notes": [
            "Corporate daily totals are preserved exactly.",
            "SKU allocation uses only DirectPick history before the forecast start.",
            "This statistical shadow is not the independent ML hybrid and must not be scored until the horizon closes.",
        ],
    }
    (output_dir / "forward_metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str),
        encoding="utf-8",
    )
    print(totals.to_string(index=False))
    print(f"Wrote {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
