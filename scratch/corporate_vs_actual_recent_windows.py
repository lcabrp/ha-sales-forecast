"""Offline corporate forecast vs actual sold units for recent calendar windows.

No AX. Uses local actuals parquet, confirmed FwdDemandCSV snapshots, and the
July 7-20 Product Info workbook for the latest corporate horizon.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))

from ingestion_pipeline import read_14day_forecast, read_last_refreshed_timestamp  # noqa: E402

ACTUAL_PATH = ROOT / "Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet"
SUMMARY_PATH = ROOT / "Output/ForecastAccuracy/history/parquet/forecast_accuracy_snapshot_summary.parquet"
HISTORY_DAY_PATH = ROOT / "Output/ForecastAccuracy/history/parquet/forecast_sku_day.parquet"
PLANNER_PATH = ROOT / "Output/ForecastAccuracy/planner/planner_daily_totals_2026.csv"
CONFIRMED_DIR = (
    ROOT.parent
    / "ha-kydc-monitoring/Output/Monitoring/forecast_snapshots/confirmed_raw"
)
PRODUCT_INFO = (
    ROOT.parent / "ha-ingestion-pipeline/Source/Product Info for BRG_2026-07-06.xlsx"
)

WINDOWS = (7, 14, 30)


def _fd_daily_totals(df: pd.DataFrame, forecast_start: pd.Timestamp, source: str) -> pd.DataFrame:
    rows = []
    for offset in range(1, 15):
        col = f"FD{offset}"
        if col not in df.columns:
            continue
        qty = float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())
        rows.append(
            {
                "ForecastDate": (forecast_start + pd.Timedelta(days=offset - 1)).normalize(),
                "CorporateUnits": qty,
                "Source": source,
                "ForecastStartDate": forecast_start.normalize(),
            }
        )
    return pd.DataFrame(rows)


def load_confirmed_snapshots() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(CONFIRMED_DIR.glob("FwdDemandCSV_*.csv")):
        df = pd.read_csv(path, low_memory=False)
        if "ForecastStartDate" not in df.columns:
            continue
        start = pd.to_datetime(df["ForecastStartDate"].iloc[0], errors="coerce")
        if pd.isna(start):
            continue
        # File date from name when present: FwdDemandCSV_YYYY-MM-DD_*.csv
        parts = path.stem.split("_")
        file_date = pd.to_datetime(parts[1], errors="coerce") if len(parts) >= 2 else pd.NaT
        if pd.isna(file_date):
            file_date = start
        day = _fd_daily_totals(df, start.normalize(), path.name)
        day["InferredFileDate"] = pd.Timestamp(file_date).normalize()
        day["SnapshotKey"] = path.name
        frames.append(day)
    if not frames:
        raise FileNotFoundError(f"No confirmed FwdDemandCSV snapshots in {CONFIRMED_DIR}")
    return pd.concat(frames, ignore_index=True)


def load_product_info_july() -> pd.DataFrame:
    if not PRODUCT_INFO.exists():
        return pd.DataFrame()
    day14, start_text = read_14day_forecast(PRODUCT_INFO)
    start = pd.to_datetime(start_text)
    refresh = read_last_refreshed_timestamp(PRODUCT_INFO)
    file_date = pd.Timestamp(refresh).normalize() if pd.notna(refresh) else pd.Timestamp("2026-07-06")
    day = _fd_daily_totals(day14, start.normalize(), PRODUCT_INFO.name)
    day["InferredFileDate"] = file_date
    day["SnapshotKey"] = PRODUCT_INFO.name
    return day


def load_history_parquet_recent(min_date: pd.Timestamp) -> pd.DataFrame:
    """Optional supplement from historical parquet (ends mid-June 2026)."""
    if not HISTORY_DAY_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(
        HISTORY_DAY_PATH,
        columns=[
            "SnapshotId",
            "InferredFileDate",
            "ForecastStartDate",
            "ForecastDate",
            "ForecastQty",
        ],
    )
    df["ForecastDate"] = pd.to_datetime(df["ForecastDate"]).dt.normalize()
    df["InferredFileDate"] = pd.to_datetime(df["InferredFileDate"]).dt.normalize()
    df["ForecastStartDate"] = pd.to_datetime(df["ForecastStartDate"]).dt.normalize()
    df = df.loc[df["ForecastDate"] >= min_date].copy()
    if df.empty:
        return pd.DataFrame()
    daily = (
        df.groupby(
            ["SnapshotId", "InferredFileDate", "ForecastStartDate", "ForecastDate"],
            as_index=False,
        )
        .agg(CorporateUnits=("ForecastQty", "sum"))
        .rename(columns={"SnapshotId": "SnapshotKey"})
    )
    daily["Source"] = "forecast_sku_day.parquet"
    return daily


def build_corporate_catalog() -> pd.DataFrame:
    frames = [load_confirmed_snapshots()]
    pi = load_product_info_july()
    if not pi.empty:
        frames.append(pi)
    # Prefer confirmed/Product Info over stale parquet for overlapping dates:
    # only add parquet rows for dates not already covered by a newer external source.
    covered_min = min(f["ForecastDate"].min() for f in frames)
    hist = load_history_parquet_recent(covered_min - pd.Timedelta(days=45))
    if not hist.empty:
        frames.append(hist)
    cat = pd.concat(frames, ignore_index=True)
    cat["ForecastDate"] = pd.to_datetime(cat["ForecastDate"]).dt.normalize()
    cat["InferredFileDate"] = pd.to_datetime(cat["InferredFileDate"]).dt.normalize()
    cat["ForecastStartDate"] = pd.to_datetime(cat["ForecastStartDate"]).dt.normalize()
    # Deduplicate identical snapshot+date rows (Product Info vs confirmed same origin)
    cat = cat.sort_values(
        ["ForecastDate", "InferredFileDate", "ForecastStartDate", "SnapshotKey"]
    )
    return cat.reset_index(drop=True)


def as_of_corporate_daily(catalog: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """For each day D, use most recent snapshot with ForecastStartDate <= D covering D.

    Tie-break: latest InferredFileDate, then SnapshotKey. Prefer confirmed CSVs /
    Product Info over parquet when same start/file date by keeping last after sort
    that ranks non-parquet higher via SnapshotKey lexical is weak — instead
    explicitly prefer Source not ending in .parquet.
    """
    rows = []
    for day in dates:
        day = pd.Timestamp(day).normalize()
        cand = catalog.loc[
            (catalog["ForecastDate"] == day)
            & (catalog["ForecastStartDate"] <= day)
            & (catalog["InferredFileDate"] <= day)
        ].copy()
        if cand.empty:
            # Allow file date == next calendar day for overnight uploads still
            # issued before the demand day (e.g. July 6 Product Info for July 7).
            cand = catalog.loc[
                (catalog["ForecastDate"] == day) & (catalog["ForecastStartDate"] <= day)
            ].copy()
        if cand.empty:
            rows.append(
                {
                    "Date": day,
                    "CorporateUnits": float("nan"),
                    "SnapshotKey": None,
                    "ForecastStartDate": pd.NaT,
                    "InferredFileDate": pd.NaT,
                    "Source": None,
                }
            )
            continue
        cand["PreferExternal"] = ~cand["Source"].astype(str).str.endswith(".parquet")
        cand = cand.sort_values(
            ["PreferExternal", "InferredFileDate", "ForecastStartDate", "SnapshotKey"]
        )
        pick = cand.iloc[-1]
        rows.append(
            {
                "Date": day,
                "CorporateUnits": float(pick["CorporateUnits"]),
                "SnapshotKey": pick["SnapshotKey"],
                "ForecastStartDate": pick["ForecastStartDate"],
                "InferredFileDate": pick["InferredFileDate"],
                "Source": pick["Source"],
            }
        )
    return pd.DataFrame(rows)


def single_snapshot_covering(
    catalog: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.DataFrame, dict]:
    """Latest snapshot whose FD horizon fully covers [start, end]."""
    snaps = (
        catalog.groupby(["SnapshotKey", "Source", "ForecastStartDate", "InferredFileDate"], as_index=False)
        .agg(HorizonStart=("ForecastDate", "min"), HorizonEnd=("ForecastDate", "max"))
    )
    full = snaps.loc[
        (snaps["HorizonStart"] <= start) & (snaps["HorizonEnd"] >= end)
    ].copy()
    meta = {
        "mode": "full_cover",
        "snapshot": None,
        "note": "",
    }
    if full.empty:
        # Fall back to latest snapshot with max overlap of the window.
        overlap = []
        for _, s in snaps.iterrows():
            ov_start = max(start, s["HorizonStart"])
            ov_end = min(end, s["HorizonEnd"])
            days = int((ov_end - ov_start).days) + 1 if ov_end >= ov_start else 0
            overlap.append({**s.to_dict(), "OverlapDays": days})
        ov = pd.DataFrame(overlap)
        ov = ov.loc[ov["OverlapDays"] > 0].sort_values(
            ["OverlapDays", "InferredFileDate", "ForecastStartDate"]
        )
        if ov.empty:
            meta["mode"] = "none"
            meta["note"] = "No corporate snapshot overlaps window"
            return pd.DataFrame(columns=["Date", "CorporateUnits"]), meta
        pick = ov.iloc[-1]
        meta["mode"] = "partial_cover"
        meta["snapshot"] = pick["SnapshotKey"]
        meta["note"] = (
            f"No snapshot fully covers window; using {pick['SnapshotKey']} "
            f"({pick['HorizonStart'].date()}..{pick['HorizonEnd'].date()}, "
            f"overlap {int(pick['OverlapDays'])}d)"
        )
        key = pick["SnapshotKey"]
        start_use, end_use = pick["HorizonStart"], pick["HorizonEnd"]
    else:
        full["PreferExternal"] = ~full["Source"].astype(str).str.endswith(".parquet")
        full = full.sort_values(
            ["PreferExternal", "InferredFileDate", "ForecastStartDate", "SnapshotKey"]
        )
        pick = full.iloc[-1]
        meta["snapshot"] = pick["SnapshotKey"]
        meta["note"] = (
            f"Fully covered by {pick['SnapshotKey']} "
            f"(start {pick['ForecastStartDate'].date()}, "
            f"file {pick['InferredFileDate'].date()}, "
            f"horizon {pick['HorizonStart'].date()}..{pick['HorizonEnd'].date()})"
        )
        key = pick["SnapshotKey"]
        start_use, end_use = start, end

    day = catalog.loc[
        (catalog["SnapshotKey"] == key)
        & (catalog["ForecastDate"] >= start)
        & (catalog["ForecastDate"] <= end)
        & (catalog["ForecastDate"] >= start_use)
        & (catalog["ForecastDate"] <= end_use)
    ][["ForecastDate", "CorporateUnits"]].copy()
    day = day.rename(columns={"ForecastDate": "Date"})
    day = day.groupby("Date", as_index=False)["CorporateUnits"].sum()
    return day, meta


def wape_daily(actual: pd.Series, forecast: pd.Series) -> float:
    a = actual.fillna(0.0)
    f = forecast.fillna(0.0)
    denom = float(a.abs().sum())
    if denom <= 0:
        return float("nan")
    return float((f - a).abs().sum() / denom)


def fmt(x: float | None, digits: int = 1) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x:,.{digits}f}"


def pct(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{100.0 * x:,.1f}%"


def main() -> int:
    print("=== Corporate vs actual (offline) ===")
    print(f"Today (assumed): 2026-07-11")
    print(f"Actuals: {ACTUAL_PATH}")
    print(f"Confirmed snapshots: {CONFIRMED_DIR}")
    print(f"Product Info: {PRODUCT_INFO}")
    print()

    actual = pd.read_parquet(ACTUAL_PATH, columns=["ActualDate", "SoldUnits"])
    actual["ActualDate"] = pd.to_datetime(actual["ActualDate"]).dt.normalize()
    daily_actual = (
        actual.groupby("ActualDate", as_index=False)
        .agg(ActualUnits=("SoldUnits", "sum"))
        .sort_values("ActualDate")
    )
    first_actual = daily_actual["ActualDate"].iloc[0]
    last_actual = daily_actual["ActualDate"].iloc[-1]
    print(f"1) Actuals date range: {first_actual.date()} .. {last_actual.date()}")
    print(f"   Last actual date:   {last_actual.date()}")
    print(f"   Actual day count:   {len(daily_actual):,}")
    print(f"   Total sold units:   {daily_actual['ActualUnits'].sum():,.0f}")
    print()

    if SUMMARY_PATH.exists():
        summary = pd.read_parquet(SUMMARY_PATH)
        summary["ForecastEndDate"] = pd.to_datetime(summary["ForecastEndDate"])
        print(
            "History parquet snapshot summary max ForecastEndDate:",
            summary["ForecastEndDate"].max().date(),
            "(confirmed CSVs + Product Info used for later days)",
        )
        print()

    catalog = build_corporate_catalog()
    print("Corporate catalog snapshot keys (recent):")
    recent_keys = (
        catalog.groupby(["SnapshotKey", "InferredFileDate", "ForecastStartDate"], as_index=False)
        .agg(HorizonEnd=("ForecastDate", "max"), Units=("CorporateUnits", "sum"))
        .sort_values("InferredFileDate")
        .tail(10)
    )
    for _, r in recent_keys.iterrows():
        print(
            f"  {r['InferredFileDate'].date()} start={r['ForecastStartDate'].date()} "
            f"end={r['HorizonEnd'].date()} units={r['Units']:,.0f}  {r['SnapshotKey']}"
        )
    print()

    # As-of series across last 30 days (covers all windows)
    win30_start = last_actual - pd.Timedelta(days=29)
    dates_30 = pd.date_range(win30_start, last_actual, freq="D")
    asof = as_of_corporate_daily(catalog, dates_30)
    asof = asof.merge(
        daily_actual.rename(columns={"ActualDate": "Date"}),
        on="Date",
        how="left",
    )
    asof["ActualUnits"] = asof["ActualUnits"].fillna(0.0)
    asof["Bias"] = asof["CorporateUnits"] - asof["ActualUnits"]

    print("2) Window scorecard (as-of: per day, latest snapshot with ForecastStartDate<=D covering D)")
    print(
        f"{'Window':<8} {'Actual':>12} {'Corporate':>12} {'Bias':>12} {'Bias%':>8} {'WAPE':>8}  notes"
    )
    window_rows = []
    for n in WINDOWS:
        start = last_actual - pd.Timedelta(days=n - 1)
        w = asof.loc[asof["Date"].between(start, last_actual)].copy()
        actual_total = float(w["ActualUnits"].sum())
        corp_total = float(w["CorporateUnits"].sum(skipna=True))
        bias = corp_total - actual_total
        bias_pct = bias / actual_total if actual_total else float("nan")
        wape = wape_daily(w["ActualUnits"], w["CorporateUnits"])
        missing = int(w["CorporateUnits"].isna().sum())
        sources = (
            w.dropna(subset=["SnapshotKey"])
            .groupby("SnapshotKey")
            .size()
            .sort_values(ascending=False)
        )
        src_note = ", ".join(f"{k}({v}d)" for k, v in sources.items()) or "none"
        if missing:
            src_note += f"; missing_corp={missing}d"
        print(
            f"{n:>2}d      {fmt(actual_total,0):>12} {fmt(corp_total,0):>12} "
            f"{fmt(bias,0):>12} {pct(bias_pct):>8} {pct(wape):>8}  {src_note}"
        )
        window_rows.append(
            {
                "WindowDays": n,
                "Start": start.date().isoformat(),
                "End": last_actual.date().isoformat(),
                "ActualUnits": actual_total,
                "CorporateUnits": corp_total,
                "Bias": bias,
                "BiasPct": bias_pct,
                "WAPE": wape,
                "Sources": src_note,
            }
        )
    print()

    print("2b) Simpler single-snapshot view (latest snapshot that covers the window)")
    print(
        f"{'Window':<8} {'Actual':>12} {'Corporate':>12} {'Bias':>12} {'Bias%':>8} {'WAPE':>8}"
    )
    for n in WINDOWS:
        start = last_actual - pd.Timedelta(days=n - 1)
        w_act = daily_actual.loc[
            daily_actual["ActualDate"].between(start, last_actual),
            ["ActualDate", "ActualUnits"],
        ].rename(columns={"ActualDate": "Date"})
        corp_day, meta = single_snapshot_covering(catalog, start, last_actual)
        merged = w_act.merge(corp_day, on="Date", how="left")
        actual_total = float(merged["ActualUnits"].sum())
        # Only score days with corporate coverage in this simpler view
        scored = merged.dropna(subset=["CorporateUnits"])
        corp_total = float(scored["CorporateUnits"].sum()) if not scored.empty else float("nan")
        bias = corp_total - float(scored["ActualUnits"].sum()) if not scored.empty else float("nan")
        bias_pct = (
            bias / float(scored["ActualUnits"].sum())
            if not scored.empty and scored["ActualUnits"].sum()
            else float("nan")
        )
        wape = (
            wape_daily(scored["ActualUnits"], scored["CorporateUnits"])
            if not scored.empty
            else float("nan")
        )
        print(
            f"{n:>2}d      {fmt(actual_total,0):>12} {fmt(corp_total,0):>12} "
            f"{fmt(bias,0):>12} {pct(bias_pct):>8} {pct(wape):>8}"
        )
        print(f"         {meta['note']}")
    print()

    print("3) Daily series — last 14 days (as-of corporate)")
    print(
        f"{'Date':<12} {'Actual':>10} {'Corporate':>10} {'Bias':>10}  snapshot"
    )
    last14 = asof.loc[asof["Date"] >= last_actual - pd.Timedelta(days=13)].copy()
    for _, r in last14.iterrows():
        snap = r["SnapshotKey"] or "MISSING"
        print(
            f"{r['Date'].date()}  {fmt(r['ActualUnits'],0):>10} "
            f"{fmt(r['CorporateUnits'],0):>10} {fmt(r['Bias'],0):>10}  {snap}"
        )
    print()

    # Planner comparison if available
    if PLANNER_PATH.exists():
        planner = pd.read_csv(PLANNER_PATH)
        planner["Date"] = pd.to_datetime(planner["Date"]).dt.normalize()
        p14 = planner.loc[
            planner["Date"].between(last_actual - pd.Timedelta(days=13), last_actual)
        ]
        if not p14.empty and "actual_demand_units" in p14.columns:
            print("4) Planner daily totals (last 14d) — optional cross-check")
            print(
                f"{'Date':<12} {'PlannerActual':>14} {'OpsIMF':>10} {'ForecastedDemand':>16}"
            )
            for _, r in p14.iterrows():
                ops = r.get("ops_imf_plan_forecasted_units", float("nan"))
                fd = r.get("forecasted_demand_units", float("nan"))
                print(
                    f"{r['Date'].date()}  {fmt(r['actual_demand_units'],0):>14} "
                    f"{fmt(ops,0):>10} {fmt(fd,0):>16}"
                )
            print()

    # July 7-9 sensitivity: Product Info vs still-live June 30 confirmed
    j79 = asof.loc[asof["Date"].between("2026-07-07", "2026-07-09")]
    june30 = catalog.loc[
        (catalog["SnapshotKey"] == "FwdDemandCSV_2026-06-30_6ef7d945effb.csv")
        & (catalog["ForecastDate"].between("2026-07-07", "2026-07-09"))
    ][["ForecastDate", "CorporateUnits"]].rename(columns={"ForecastDate": "Date", "CorporateUnits": "June30Units"})
    j79m = j79[["Date", "ActualUnits", "CorporateUnits"]].merge(june30, on="Date", how="left")
    print("4b) July 7-9 sensitivity (Product Info as-of vs June 30 confirmed still in horizon)")
    print(
        f"  Product Info corp={j79m['CorporateUnits'].sum():,.0f}  "
        f"June30 corp={j79m['June30Units'].sum():,.0f}  "
        f"actual={j79m['ActualUnits'].sum():,.0f}"
    )
    print(
        "  Note: confirmed AX upload of the July 7-20 book is dated 2026-07-11, "
        "so AX-live for 7/7-7/9 may still have been the June 30 snapshot; "
        "day totals are nearly the same either way."
    )
    print()

    print("5) Sources & caveats")
    print(
        "- History parquet forecast_sku_day ends ForecastDate 2026-06-15; "
        "recent corporate day totals come from confirmed_raw FwdDemandCSV_* "
        "plus Product Info for BRG_2026-07-06.xlsx."
    )
    print(
        "- July 7-20: Product Info LAST REFRESHED 2026-07-06 (start 7/7); "
        "confirmed upload FwdDemandCSV_2026-07-11_b0518891ae8c.csv matches "
        "the same 204,654-unit 14-day total / start date."
    )
    print(
        "- As-of rule: for day D, latest snapshot with ForecastStartDate <= D "
        "and InferredFileDate <= D covering D (Product Info file date 7/6 wins "
        "for 7/7-7/9 over the 7/11 confirmed file date)."
    )
    print(
        "- Single-snapshot view: full horizon cover when possible; else max overlap "
        "(14d/30d cannot be covered by one 14-day book)."
    )
    print(
        "- Bias = corporate - actual (positive => over-forecast). "
        "WAPE on daily totals after aggregating SKU->day."
    )
    print(
        f"- Actuals: DateBasis=modified through {last_actual.date()}."
    )
    print()
    print("Exit OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
