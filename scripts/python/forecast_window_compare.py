"""Score saved daily forecasts against the monitoring DirectPick target.

The monitoring repository is the preferred source for completed-day aggregate
Pick totals.  It does not currently persist current SKU/day picks, so this tool
can use a read-only AX query as a narrow fallback for the allocation detail.
The AX query intentionally matches the monitoring pick filters and Eastern-day
window instead of the broader legacy ``actual_sku_day_modified`` definition.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np
import sqlalchemy as sa

from forecast_replacement_backtest import normalize_sku_series, score_forecast
from output_paths import PROJECT_ROOT
from sql_utils import get_ax_engine


EASTERN = ZoneInfo("America/New_York")
DEFAULT_MONITORING_DB = (
    PROJECT_ROOT.parent
    / "ha-kydc-monitoring"
    / "Output"
    / "Monitoring"
    / "Monitoring_History.db"
)
DEFAULT_LEDGER_DB = (
    PROJECT_ROOT.parent
    / "ha-ingestion-pipeline"
    / "Output"
    / "Ingestion"
    / "sku_ledger.db"
)

MONITORING_PICK_QUERY = sa.text(
    """
    SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

    SELECT
        wl.MODIFIEDDATETIME AS EventDateTimeUTC,
        CASE
            WHEN ISNULL(d.INVENTCOLORID, '') = '' THEN wl.ITEMID
            WHEN ISNULL(d.INVENTSIZEID, '') = ''
                THEN wl.ITEMID + '-' + d.INVENTCOLORID
            ELSE wl.ITEMID + '-' + d.INVENTCOLORID + '-' + d.INVENTSIZEID
        END AS SKU,
        CAST(wl.QTYWORK AS DECIMAL(18, 4)) AS SoldUnits
    FROM DAX_PROD.dbo.WHSWORKLINE wl WITH (NOLOCK)
    INNER JOIN DAX_PROD.dbo.WHSWORKTABLE wt WITH (NOLOCK)
        ON wt.WORKID = wl.WORKID
       AND wt.DATAAREAID = wl.DATAAREAID
       AND wt.[PARTITION] = wl.[PARTITION]
    INNER JOIN DAX_PROD.dbo.INVENTDIM d WITH (NOLOCK)
        ON d.INVENTDIMID = wl.INVENTDIMID
       AND d.DATAAREAID = wl.DATAAREAID
       AND d.[PARTITION] = wl.[PARTITION]
    INNER JOIN DAX_PROD.dbo.WMSLOCATION loc WITH (NOLOCK)
        ON loc.WMSLOCATIONID = wl.WMSLOCATIONID
       AND loc.INVENTLOCATIONID = wt.INVENTLOCATIONID
       AND loc.DATAAREAID = wl.DATAAREAID
       AND loc.[PARTITION] = wl.[PARTITION]
    LEFT JOIN DAX_PROD.dbo.WHSWORKCLUSTERLINE cl WITH (NOLOCK)
        ON cl.WORKID = wl.WORKID
       AND cl.DATAAREAID = wl.DATAAREAID
       AND cl.[PARTITION] = wl.[PARTITION]
    LEFT JOIN DAX_PROD.dbo.WHSWORKCLUSTERTABLE ct WITH (NOLOCK)
        ON ct.CLUSTERID = cl.CLUSTERID
       AND ct.DATAAREAID = cl.DATAAREAID
       AND ct.[PARTITION] = cl.[PARTITION]
    WHERE wl.DATAAREAID = 'ha'
      AND wl.[PARTITION] = 5637144576
      AND wt.INVENTLOCATIONID = '4010'
      AND wl.WORKTYPE = 1
      AND wl.WORKCLASSID = 'DirectPick'
      AND wl.WORKSTATUS = 4
      AND wl.MODIFIEDDATETIME >= :start_utc
      AND wl.MODIFIEDDATETIME < :end_utc
      AND loc.LOCPROFILEID IN ('Picking', 'Picking A', 'Picking D', 'PalletPicking')
      AND wl.WMSLOCATIONID NOT IN ('Washed', 'Quality', 'Rags', 'Bander', 'AutoBagger')
    """
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--through-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--daily-forecast",
        action="append",
        type=Path,
        default=[],
        help="Daily Parquet/CSV with Candidate, SKU, ForecastDate, ForecastUnits.",
    )
    parser.add_argument(
        "--named-daily",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Daily Parquet/CSV without Candidate; assign it NAME.",
    )
    parser.add_argument(
        "--anchor-candidate",
        action="append",
        default=[],
        metavar="NAME=SHAPE:TOTAL",
        help=(
            "Create NAME by allocating each TOTAL candidate day across the SHAPE candidate "
            "SKUs with exact largest-remainder rounding."
        ),
    )
    parser.add_argument("--actuals", type=Path, help="Current SKU/day actuals, if available.")
    parser.add_argument(
        "--live-ax",
        action="store_true",
        help="Use read-only AX only when no current portable SKU/day actual is supplied.",
    )
    parser.add_argument("--monitoring-db", type=Path, default=DEFAULT_MONITORING_DB)
    parser.add_argument("--ledger-db", type=Path, default=DEFAULT_LEDGER_DB)
    parser.add_argument("--server", default="prodaxsql2")
    parser.add_argument("--database", default="DAX_PROD")
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, low_memory=False)
    raise ValueError(f"Unsupported tabular file: {path}")


def load_daily_forecasts(
    paths: list[Path],
    named_paths: list[str],
    start_date: date,
    through_date: date,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    sources: list[dict[str, Any]] = []
    assignments: list[tuple[str | None, Path]] = [(None, path) for path in paths]
    for value in named_paths:
        if "=" not in value:
            raise ValueError(f"--named-daily must be NAME=PATH, got: {value}")
        name, path_text = value.split("=", 1)
        assignments.append((name.strip(), Path(path_text.strip())))

    required = {"SKU", "ForecastDate", "ForecastUnits"}
    for assigned_name, path in assignments:
        resolved = path.resolve()
        frame = read_table(resolved)
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"{resolved} is missing columns: {sorted(missing)}")
        if assigned_name:
            frame["Candidate"] = assigned_name
        elif "Candidate" not in frame.columns:
            raise ValueError(f"{resolved} has no Candidate column; use --named-daily NAME=PATH")

        frame = frame[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]].copy()
        frame["SKU"] = normalize_sku_series(frame["SKU"])
        frame["ForecastDate"] = pd.to_datetime(frame["ForecastDate"]).dt.normalize()
        frame["ForecastUnits"] = (
            pd.to_numeric(frame["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
        )
        start = pd.Timestamp(start_date)
        through = pd.Timestamp(through_date)
        frame = frame.loc[frame["ForecastDate"].between(start, through)]
        frames.append(frame)
        sources.append(
            {
                "path": str(resolved),
                "assigned_candidate": assigned_name,
                "window_rows": int(len(frame)),
                "window_units": float(frame["ForecastUnits"].sum()),
            }
        )

    if not frames:
        raise ValueError("Provide at least one --daily-forecast or --named-daily input.")
    daily = pd.concat(frames, ignore_index=True)
    daily = (
        daily.groupby(["Candidate", "SKU", "ForecastDate"], as_index=False)["ForecastUnits"]
        .sum()
        .sort_values(["Candidate", "ForecastDate", "SKU"], kind="mergesort")
    )
    return daily, sources


def add_anchored_candidates(
    daily: pd.DataFrame,
    specifications: list[str],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    generated: list[pd.DataFrame] = []
    metadata: list[dict[str, Any]] = []
    available = set(daily["Candidate"].astype(str).unique())
    for value in specifications:
        if "=" not in value or ":" not in value.split("=", 1)[1]:
            raise ValueError(f"--anchor-candidate must be NAME=SHAPE:TOTAL, got: {value}")
        name, source_text = value.split("=", 1)
        shape_name, total_name = source_text.split(":", 1)
        name = name.strip()
        shape_name = shape_name.strip()
        total_name = total_name.strip()
        missing = {shape_name, total_name}.difference(available)
        if missing:
            raise ValueError(f"Anchor specification references missing candidates: {sorted(missing)}")

        shape = daily.loc[daily["Candidate"].eq(shape_name)].copy()
        totals = (
            daily.loc[daily["Candidate"].eq(total_name)]
            .groupby("ForecastDate", as_index=False)["ForecastUnits"]
            .sum()
            .rename(columns={"ForecastUnits": "TargetUnits"})
        )
        pieces: list[pd.DataFrame] = []
        for forecast_date, group in shape.groupby("ForecastDate", sort=True):
            target_row = totals.loc[totals["ForecastDate"].eq(forecast_date), "TargetUnits"]
            if target_row.empty:
                continue
            target_units = int(round(float(target_row.iloc[0])))
            work = group.loc[group["ForecastUnits"].gt(0), ["SKU", "ForecastDate", "ForecastUnits"]].copy()
            shape_total = float(work["ForecastUnits"].sum())
            if work.empty or shape_total <= 0 or target_units <= 0:
                continue
            raw = work["ForecastUnits"] / shape_total * target_units
            floors = np.floor(raw).astype(int)
            extra = target_units - int(floors.sum())
            work["ForecastUnits"] = floors
            if extra > 0:
                remainder_order = (
                    pd.DataFrame(
                        {
                            "index": work.index,
                            "Remainder": raw - floors,
                            "SKU": work["SKU"].to_numpy(),
                        }
                    )
                    .sort_values(["Remainder", "SKU"], ascending=[False, True])
                    .head(extra)
                )
                work.loc[remainder_order["index"], "ForecastUnits"] += 1
            work = work.loc[work["ForecastUnits"].gt(0)].copy()
            work["Candidate"] = name
            pieces.append(work[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]])
        if not pieces:
            raise ValueError(f"Anchor specification generated no rows: {value}")
        candidate = pd.concat(pieces, ignore_index=True)
        generated.append(candidate)
        available.add(name)
        metadata.append(
            {
                "kind": "generated_daily_anchor",
                "candidate": name,
                "shape_candidate": shape_name,
                "total_candidate": total_name,
                "window_rows": int(len(candidate)),
                "window_units": float(candidate["ForecastUnits"].sum()),
            }
        )
    if generated:
        daily = pd.concat([daily, *generated], ignore_index=True)
    return daily, metadata


def load_portable_actuals(path: Path, start_date: date, through_date: date) -> pd.DataFrame:
    frame = read_table(path.resolve())
    date_column = next(
        (column for column in ("ActualDate", "PickDate", "Date") if column in frame.columns),
        None,
    )
    unit_column = next(
        (column for column in ("SoldUnits", "PickUnits", "Units") if column in frame.columns),
        None,
    )
    if date_column is None or unit_column is None or "SKU" not in frame.columns:
        raise ValueError(f"Actuals file lacks SKU/date/unit columns: {path}")
    actual = frame[[date_column, "SKU", unit_column]].rename(
        columns={date_column: "ActualDate", unit_column: "SoldUnits"}
    )
    actual["ActualDate"] = pd.to_datetime(actual["ActualDate"]).dt.normalize()
    actual["SKU"] = normalize_sku_series(actual["SKU"])
    actual["SoldUnits"] = pd.to_numeric(actual["SoldUnits"], errors="coerce").fillna(0)
    actual = actual.loc[
        actual["ActualDate"].between(pd.Timestamp(start_date), pd.Timestamp(through_date))
    ]
    return (
        actual.groupby(["ActualDate", "SKU"], as_index=False)["SoldUnits"]
        .sum()
        .sort_values(["ActualDate", "SKU"], kind="mergesort")
    )


def query_live_ax_actuals(
    start_date: date,
    through_date: date,
    server: str,
    database: str,
) -> pd.DataFrame:
    start_est = datetime.combine(start_date, time.min, tzinfo=EASTERN)
    end_est = datetime.combine(through_date + timedelta(days=1), time.min, tzinfo=EASTERN)
    params = {
        "start_utc": start_est.astimezone(UTC).replace(tzinfo=None),
        "end_utc": end_est.astimezone(UTC).replace(tzinfo=None),
    }
    engine = get_ax_engine(server=server, database=database, verbose=True)
    with engine.connect() as connection:
        raw = pd.read_sql_query(MONITORING_PICK_QUERY, connection, params=params)
    if raw.empty:
        raise RuntimeError("The AX monitoring-scope query returned no DirectPick rows.")

    timestamps = pd.to_datetime(raw["EventDateTimeUTC"], errors="coerce", utc=True)
    raw["ActualDate"] = timestamps.dt.tz_convert(EASTERN).dt.tz_localize(None).dt.normalize()
    raw["SKU"] = normalize_sku_series(raw["SKU"])
    raw["SoldUnits"] = pd.to_numeric(raw["SoldUnits"], errors="coerce").fillna(0)
    raw = raw.loc[raw["ActualDate"].notna() & raw["SKU"].ne("")]
    actual = (
        raw.groupby(["ActualDate", "SKU"], as_index=False)
        .agg(SoldUnits=("SoldUnits", "sum"), PickLines=("SoldUnits", "size"))
        .sort_values(["ActualDate", "SKU"], kind="mergesort")
    )
    actual.insert(1, "DateBasis", "modified_eastern_monitoring_scope")
    return actual


def monitoring_window_summary(
    path: Path,
    start_date: date,
    through_date: date,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"path": str(path.resolve()), "exists": path.exists()}
    if not path.exists():
        return summary
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            """
            SELECT MIN(MetricDate), MAX(MetricDate), COUNT(DISTINCT MetricDate),
                   COALESCE(SUM(Units), 0)
            FROM daily_activity_metrics
            WHERE ActivityType = 'Pick'
              AND MetricDate >= ?
              AND MetricDate <= ?
            """,
            (start_date.isoformat(), through_date.isoformat()),
        ).fetchone()
    expected_days = (through_date - start_date).days + 1
    summary.update(
        {
            "start_date": row[0],
            "through_date": row[1],
            "days": int(row[2] or 0),
            "expected_days": expected_days,
            "pick_units": float(row[3] or 0),
            "complete": int(row[2] or 0) == expected_days,
        }
    )
    return summary


def load_category_crosswalk(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["SKU", "Category"])
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        ledger = pd.read_sql_query(
            """
            SELECT sku, product_group, size_group, last_seen, source_file
            FROM sku_ledger
            """,
            connection,
        )
    ledger["SKU"] = normalize_sku_series(ledger["sku"])
    ledger["last_seen"] = pd.to_datetime(ledger["last_seen"], errors="coerce")
    ledger = ledger.sort_values(["last_seen", "source_file"], kind="mergesort")
    ledger = ledger.drop_duplicates("SKU", keep="last")
    product_group = ledger["product_group"].fillna("").astype(str).str.strip().str.upper()
    size_group = ledger["size_group"].fillna("").astype(str).str.strip().str.upper()
    ledger["Category"] = (product_group + size_group).replace("", "UNKNOWN")
    return ledger[["SKU", "Category"]]


def score_candidates(
    daily_forecast: pd.DataFrame,
    actual_sku_day: pd.DataFrame,
    start_date: date,
    through_date: date,
    category_crosswalk: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    actual_sku = actual_sku_day.groupby("SKU", as_index=False).agg(SoldUnits=("SoldUnits", "sum"))
    forecast_sku = daily_forecast.groupby(["Candidate", "SKU"], as_index=False).agg(
        ForecastUnits=("ForecastUnits", "sum")
    )
    snapshot = pd.Series(
        {
            "SnapshotId": f"closeout_{start_date}_{through_date}",
            "SourceFile": "saved_daily_forecast",
            "ForecastStartDate": start_date,
            "ForecastEndDate": through_date,
        }
    )

    score_rows: list[dict[str, Any]] = []
    detail_frames: list[pd.DataFrame] = []
    for candidate, candidate_forecast in forecast_sku.groupby("Candidate", sort=True):
        candidate_forecast = candidate_forecast[["SKU", "ForecastUnits"]]
        row = score_forecast(candidate_forecast, actual_sku, str(candidate), snapshot)
        detail = candidate_forecast.merge(actual_sku, on="SKU", how="outer").fillna(0)
        detail["Candidate"] = candidate
        detail["AbsError"] = (detail["ForecastUnits"] - detail["SoldUnits"]).abs()
        detail["ForecastedAndUsed"] = detail["ForecastUnits"].gt(0) & detail["SoldUnits"].gt(0)
        detail["ForecastedButUnused"] = detail["ForecastUnits"].gt(0) & detail["SoldUnits"].eq(0)
        detail["SoldButUnforecast"] = detail["SoldUnits"].gt(0) & detail["ForecastUnits"].eq(0)

        used_forecast_skus = int(detail["ForecastedAndUsed"].sum())
        forecasted_skus = int(detail["ForecastUnits"].gt(0).sum())
        forecast_units = float(detail["ForecastUnits"].sum())
        forecast_units_on_used_skus = float(
            detail.loc[detail["ForecastedAndUsed"], "ForecastUnits"].sum()
        )
        row.update(
            {
                "UsedForecastSKUs": used_forecast_skus,
                "ForecastSKUUseRatePct": (
                    used_forecast_skus / forecasted_skus if forecasted_skus else pd.NA
                ),
                "ForecastUnitsOnUsedSKUs": forecast_units_on_used_skus,
                "ForecastUnitsOnUsedSKUPct": (
                    forecast_units_on_used_skus / forecast_units if forecast_units else pd.NA
                ),
                "ZeroDemandForecastUnitPct": (
                    row["OvergeneratedZeroDemandUnits"] / forecast_units
                    if forecast_units
                    else pd.NA
                ),
            }
        )
        score_rows.append(row)
        detail_frames.append(detail)

    scores = pd.DataFrame(score_rows).sort_values("WAPE", kind="mergesort")
    detail_all = pd.concat(detail_frames, ignore_index=True)
    detail_all = detail_all.merge(category_crosswalk, on="SKU", how="left")
    detail_all["Category"] = detail_all["Category"].fillna("UNKNOWN")
    category = (
        detail_all.groupby(["Candidate", "Category"], as_index=False)
        .agg(
            ForecastUnits=("ForecastUnits", "sum"),
            SoldUnits=("SoldUnits", "sum"),
            AbsErrorUnits=("AbsError", "sum"),
            ForecastedSKUs=("ForecastUnits", lambda values: int(values.gt(0).sum())),
            SoldSKUs=("SoldUnits", lambda values: int(values.gt(0).sum())),
            UsedForecastSKUs=("ForecastedAndUsed", "sum"),
        )
        .sort_values(["Candidate", "SoldUnits"], ascending=[True, False], kind="mergesort")
    )
    category["BiasUnitsForecastMinusActual"] = category["ForecastUnits"] - category["SoldUnits"]
    category["WAPE"] = category["AbsErrorUnits"] / category["SoldUnits"].replace(0, pd.NA)
    category["ForecastSKUUseRatePct"] = category["UsedForecastSKUs"] / category[
        "ForecastedSKUs"
    ].replace(0, pd.NA)
    return scores, detail_all, category


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def main() -> int:
    args = parse_args()
    if args.through_date < args.start_date:
        raise ValueError("--through-date must be on or after --start-date")
    daily_forecast, forecast_sources = load_daily_forecasts(
        args.daily_forecast,
        args.named_daily,
        args.start_date,
        args.through_date,
    )
    daily_forecast, generated_sources = add_anchored_candidates(
        daily_forecast,
        args.anchor_candidate,
    )
    forecast_sources.extend(generated_sources)

    if args.actuals:
        actual_sku_day = load_portable_actuals(
            args.actuals,
            args.start_date,
            args.through_date,
        )
        actual_source = {"kind": "portable_sku_day", "path": str(args.actuals.resolve())}
    elif args.live_ax:
        actual_sku_day = query_live_ax_actuals(
            args.start_date,
            args.through_date,
            args.server,
            args.database,
        )
        actual_source = {
            "kind": "live_ax_monitoring_scope_fallback",
            "server": args.server,
            "database": args.database,
        }
    else:
        raise ValueError("Provide --actuals or explicitly enable the narrow --live-ax fallback.")

    monitoring = monitoring_window_summary(
        args.monitoring_db,
        args.start_date,
        args.through_date,
    )
    actual_units = float(actual_sku_day["SoldUnits"].sum())
    monitoring_units = monitoring.get("pick_units")
    monitoring_delta = None if monitoring_units is None else actual_units - float(monitoring_units)

    crosswalk = load_category_crosswalk(args.ledger_db)
    scores, detail, category = score_candidates(
        daily_forecast,
        actual_sku_day,
        args.start_date,
        args.through_date,
        crosswalk,
    )
    actual_daily = actual_sku_day.groupby("ActualDate", as_index=False).agg(
        SoldUnits=("SoldUnits", "sum")
    )
    forecast_daily = daily_forecast.groupby(["Candidate", "ForecastDate"], as_index=False).agg(
        ForecastUnits=("ForecastUnits", "sum")
    )
    daily_score = forecast_daily.merge(
        actual_daily,
        left_on="ForecastDate",
        right_on="ActualDate",
        how="left",
    ).drop(columns="ActualDate")
    daily_score["ErrorUnitsForecastMinusActual"] = (
        daily_score["ForecastUnits"] - daily_score["SoldUnits"]
    )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    actual_sku_day.to_parquet(output_dir / "actual_sku_day.parquet", index=False, compression="zstd")
    scores.to_csv(output_dir / "forecast_window_scores.csv", index=False)
    daily_score.to_csv(output_dir / "forecast_daily_totals.csv", index=False)
    category.to_csv(output_dir / "forecast_category_scores.csv", index=False)
    detail.to_parquet(output_dir / "forecast_sku_comparison.parquet", index=False, compression="zstd")

    metadata = {
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "window": {
            "start_date": args.start_date.isoformat(),
            "through_date": args.through_date.isoformat(),
            "days": (args.through_date - args.start_date).days + 1,
        },
        "forecast_sources": forecast_sources,
        "actual_source": actual_source,
        "actuals": {
            "rows": int(len(actual_sku_day)),
            "distinct_skus": int(actual_sku_day["SKU"].nunique()),
            "sold_units": actual_units,
        },
        "monitoring_validation": {
            **monitoring,
            "ax_or_portable_minus_monitoring_units": monitoring_delta,
        },
        "category_crosswalk": {
            "path": str(args.ledger_db.resolve()),
            "rows": int(len(crosswalk)),
        },
        "outputs": {
            "actual_sku_day": "actual_sku_day.parquet",
            "scores": "forecast_window_scores.csv",
            "daily_totals": "forecast_daily_totals.csv",
            "category_scores": "forecast_category_scores.csv",
            "sku_comparison": "forecast_sku_comparison.parquet",
        },
        "metric_notes": {
            "ForecastSKUUseRatePct": (
                "Share of forecast-positive SKUs that had positive DirectPick demand; "
                "this is a SKU/box-avoidance proxy, not a physical carton measurement."
            ),
            "ZeroDemandForecastUnitPct": (
                "Share of forecast units assigned to SKUs with zero DirectPick demand in the window."
            ),
        },
    }
    (output_dir / "forecast_window_metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str),
        encoding="utf-8",
    )

    display_columns = [
        "Candidate",
        "ForecastUnits",
        "SoldUnits",
        "WAPE",
        "BiasPctForecastMinusActual",
        "SoldUnitForecastCoveragePct",
        "ForecastSKUUseRatePct",
        "ZeroDemandForecastUnitPct",
    ]
    print(scores[display_columns].to_string(index=False))
    print(f"Monitoring Pick units: {monitoring_units}")
    print(f"Scorer actual units:    {actual_units}")
    print(f"Difference:             {monitoring_delta}")
    print(f"Wrote {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
