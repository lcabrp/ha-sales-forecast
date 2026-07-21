"""Advance the handoff challenger: sale holdout + July 7 forward forecasts.

Produces and scores (offline, no AX):
  1) Sale holdout origin 2026-06-18 / 14 days (through 2026-07-01)
  2) Forward origin 2026-07-07 / 14 days with partial actuals through 2026-07-09

Candidates:
  corporate_raw
  corporate_total_recent_shape   <- current lead challenger
  independent_recent_shape       <- free-total recent diagnostic
  (optional) independent hybrid via separate hybrid_candidate run

Corporate totals come from Product Info / confirmed FwdDemandCSV (history parquet
does not cover these origins).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_replacement_backtest import (  # noqa: E402
    ACTUALS_PATH,
    PDL_SKU_FEATURES_PATH,
    load_actuals,
    load_promo_for_window,
    no_ml_forecast,
    normalize_sku_series,
    score_forecast,
)
from forecast_replacement_contract import DEFAULT_LOOKBACK_DAYS, FD_COLUMNS  # noqa: E402
from forecast_replacement_hybrid_candidate import (  # noqa: E402
    integerize_by_forecast_day,
    recent_daily_forecast,
)
from ingestion_pipeline import read_14day_forecast  # noqa: E402

INGESTION_SOURCE = ROOT.parent / "ha-ingestion-pipeline" / "Source"
CONFIRMED_DIR = (
    ROOT.parent
    / "ha-kydc-monitoring"
    / "Output"
    / "Monitoring"
    / "forecast_snapshots"
    / "confirmed_raw"
)
OUT_ROOT = ROOT / "Output" / "ForecastAccuracy" / "handoff_eval"


def allocate_total_by_shape(shape: pd.DataFrame, total_units: float) -> pd.DataFrame:
    frame = shape.copy()
    frame["ForecastUnits"] = (
        pd.to_numeric(frame["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
    )
    frame = frame.loc[frame["ForecastUnits"].gt(0), ["SKU", "ForecastUnits"]]
    shape_total = float(frame["ForecastUnits"].sum())
    total_units = float(total_units)
    if frame.empty or shape_total <= 0 or total_units <= 0:
        return frame.iloc[0:0].copy()
    frame["ForecastUnitsRaw"] = frame["ForecastUnits"] / shape_total * total_units
    frame["ForecastDay"] = 1
    frame = integerize_by_forecast_day(frame)
    return frame.loc[frame["ForecastUnits"].gt(0), ["SKU", "ForecastUnits"]].copy()


def wide_to_sku_total(wide: pd.DataFrame) -> pd.DataFrame:
    fd = [c for c in FD_COLUMNS if c in wide.columns]
    out = wide[["SKU"]].copy()
    out["ForecastUnits"] = wide[fd].sum(axis=1)
    out["SKU"] = normalize_sku_series(out["SKU"])
    return (
        out.groupby("SKU", as_index=False)["ForecastUnits"]
        .sum()
        .loc[lambda d: d["ForecastUnits"].gt(0)]
    )


def load_corporate_wide_from_product_info(path: Path) -> tuple[pd.DataFrame, pd.Timestamp]:
    wide, start_text = read_14day_forecast(path)
    start = pd.Timestamp(pd.to_datetime(start_text)).normalize()
    wide["SKU"] = normalize_sku_series(wide["SKU"])
    return wide, start


def load_corporate_wide_from_fwddemand(path: Path) -> tuple[pd.DataFrame, pd.Timestamp]:
    df = pd.read_csv(path, low_memory=False)
    start = pd.Timestamp(pd.to_datetime(df["ForecastStartDate"].iloc[0])).normalize()
    keep = ["SKU"] + [c for c in FD_COLUMNS if c in df.columns]
    wide = df[keep].copy()
    wide["SKU"] = normalize_sku_series(wide["SKU"])
    for c in FD_COLUMNS:
        if c in wide.columns:
            wide[c] = pd.to_numeric(wide[c], errors="coerce").fillna(0)
    wide = wide.groupby("SKU", as_index=False)[[c for c in FD_COLUMNS if c in wide.columns]].sum()
    return wide, start


def pick_corporate_source(target_start: pd.Timestamp) -> tuple[pd.DataFrame, pd.Timestamp, str]:
    """Prefer Product Info / FwdDemand whose ForecastStartDate equals target_start."""
    candidates: list[tuple[Path, str]] = []
    for path in sorted(INGESTION_SOURCE.glob("Product Info for BRG_*.xlsx")):
        candidates.append((path, "product_info"))
    for path in sorted(CONFIRMED_DIR.glob("FwdDemandCSV_*.csv")):
        candidates.append((path, "fwddemand"))

    exact: list[tuple[pd.DataFrame, pd.Timestamp, str]] = []
    near: list[tuple[pd.DataFrame, pd.Timestamp, str, int]] = []
    for path, kind in candidates:
        try:
            if kind == "product_info":
                wide, start = load_corporate_wide_from_product_info(path)
            else:
                wide, start = load_corporate_wide_from_fwddemand(path)
        except Exception as exc:  # noqa: BLE001 — skip unreadable sources
            print(f"  skip {path.name}: {exc}", flush=True)
            continue
        label = f"{kind}:{path.name}"
        delta = abs((start - target_start).days)
        if delta == 0:
            exact.append((wide, start, label))
        else:
            near.append((wide, start, label, delta))

    if exact:
        # Prefer Product Info over CSV when both exact
        exact.sort(key=lambda x: (0 if x[2].startswith("product_info") else 1, x[2]))
        wide, start, label = exact[0]
        return wide, start, label

    if not near:
        raise FileNotFoundError(f"No corporate source near {target_start.date()}")
    near.sort(key=lambda x: (x[3], 0 if x[2].startswith("product_info") else 1))
    wide, start, label, delta = near[0]
    print(f"  WARNING: no exact corporate start {target_start.date()}; using {label} start={start.date()} (d{delta}d)")
    return wide, start, label


def actual_window_units(actuals: pd.DataFrame, start: pd.Timestamp, days: int = 14) -> pd.DataFrame:
    end = start + pd.Timedelta(days=days - 1)
    frame = actuals.loc[actuals["ActualDate"].between(start, end)].copy()
    return (
        frame.groupby("SKU", as_index=False)
        .agg(SoldUnits=("SoldUnits", "sum"))
        .loc[lambda d: d["SoldUnits"].gt(0)]
    )


def score_pair(forecast: pd.DataFrame, actual: pd.DataFrame, candidate: str, start: pd.Timestamp) -> dict:
    snapshot = {
        "SnapshotId": f"manual_{start.date()}",
        "SourceFile": "handoff_challenger",
        "ForecastStartDate": start,
        "ForecastEndDate": start + pd.Timedelta(days=13),
    }
    return score_forecast(forecast, actual, candidate, snapshot)


def build_recent_sku_total(
    actuals: pd.DataFrame,
    start: pd.Timestamp,
    source_universe: pd.DataFrame,
) -> pd.DataFrame:
    promo = load_promo_for_window(
        PDL_SKU_FEATURES_PATH,
        start,
        start + pd.Timedelta(days=13),
    )
    recent, _ = no_ml_forecast(
        actuals=actuals,
        promo=promo,
        source_universe=source_universe,
        start=start,
        lookback_days=DEFAULT_LOOKBACK_DAYS,
        include_seasonal=False,
        include_promo_floor=False,
        seasonal_years=3,
        seasonal_window_days=7,
        seasonal_recent_weight=0.65,
    )
    return recent


def allocate_daily_by_recent_shape(
    corporate_wide: pd.DataFrame,
    recent_daily: pd.DataFrame,
    forecast_start: pd.Timestamp,
) -> pd.DataFrame:
    """For each FD day, hold corporate day total and allocate by that day's recent SKU shares."""
    rows: list[pd.DataFrame] = []
    recent_daily = recent_daily.copy()
    recent_daily["SKU"] = normalize_sku_series(recent_daily["SKU"])
    recent_daily["ForecastDate"] = pd.to_datetime(recent_daily["ForecastDate"]).dt.normalize()
    recent_daily["ForecastUnits"] = pd.to_numeric(
        recent_daily["ForecastUnits"], errors="coerce"
    ).fillna(0).clip(lower=0)

    for offset, fd in enumerate(FD_COLUMNS):
        if fd not in corporate_wide.columns:
            continue
        day = (forecast_start + pd.Timedelta(days=offset)).normalize()
        corp_total = float(pd.to_numeric(corporate_wide[fd], errors="coerce").fillna(0).sum())
        shape = recent_daily.loc[
            recent_daily["ForecastDate"].eq(day),
            ["SKU", "ForecastUnits"],
        ]
        if shape.empty or shape["ForecastUnits"].sum() <= 0:
            # Fall back to 14-day recent shape shares for this day.
            shape = (
                recent_daily.groupby("SKU", as_index=False)["ForecastUnits"]
                .sum()
            )
        allocated = allocate_total_by_shape(shape, corp_total)
        if allocated.empty:
            continue
        allocated = allocated.rename(columns={"ForecastUnits": fd})
        rows.append(allocated)

    if not rows:
        return pd.DataFrame(columns=["SKU", *FD_COLUMNS])

    out = rows[0]
    for part in rows[1:]:
        out = out.merge(part, on="SKU", how="outer")
    for fd in FD_COLUMNS:
        if fd not in out.columns:
            out[fd] = 0
        out[fd] = pd.to_numeric(out[fd], errors="coerce").fillna(0).round().clip(lower=0).astype(int)
    return out[["SKU", *FD_COLUMNS]]


def wide_to_daily_long(wide: pd.DataFrame, forecast_start: pd.Timestamp, candidate: str) -> pd.DataFrame:
    rows = []
    for offset, fd in enumerate(FD_COLUMNS):
        if fd not in wide.columns:
            continue
        day = forecast_start + pd.Timedelta(days=offset)
        part = wide.loc[wide[fd].gt(0), ["SKU", fd]].copy()
        part = part.rename(columns={fd: "ForecastUnits"})
        part["ForecastDate"] = day
        part["Candidate"] = candidate
        rows.append(part)
    if not rows:
        return pd.DataFrame(columns=["SKU", "ForecastDate", "ForecastUnits", "Candidate"])
    return pd.concat(rows, ignore_index=True)


def score_partial_daily(
    daily: pd.DataFrame,
    actuals: pd.DataFrame,
    candidate: str,
    start: pd.Timestamp,
    through: pd.Timestamp,
) -> dict:
    frame = daily.loc[
        daily["Candidate"].eq(candidate)
        & daily["ForecastDate"].between(start, through)
    ].copy()
    actual = (
        actuals.loc[actuals["ActualDate"].between(start, through)]
        .groupby("SKU", as_index=False)
        .agg(SoldUnits=("SoldUnits", "sum"))
    )
    forecast = (
        frame.groupby("SKU", as_index=False)
        .agg(ForecastUnits=("ForecastUnits", "sum"))
    )
    snapshot = {
        "SnapshotId": f"partial_{start.date()}_{through.date()}",
        "SourceFile": "handoff_challenger",
        "ForecastStartDate": start,
        "ForecastEndDate": through,
    }
    row = score_forecast(forecast, actual, candidate, snapshot)
    row["PartialThrough"] = through.date().isoformat()
    row["PartialDays"] = int((through - start).days + 1)
    return row


def run_sale_holdout(actuals: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    start = pd.Timestamp("2026-06-18")
    print(f"\n=== Sale holdout origin {start.date()} (14d through 2026-07-01) ===", flush=True)
    corp_wide, corp_start, corp_label = pick_corporate_source(start)
    if corp_start != start:
        # If workbook starts earlier/later, align by slicing FD columns to the sale window dates.
        # Prefer exact; otherwise shift mapping by date overlap.
        print(f"  corporate source {corp_label} start={corp_start.date()}", flush=True)

    corp_sku = wide_to_sku_total(corp_wide)
    source_universe = corp_wide[["SKU"]].drop_duplicates()
    recent = build_recent_sku_total(actuals, start, source_universe)
    corp_total = float(corp_sku["ForecastUnits"].sum())
    corp_recent = allocate_total_by_shape(recent, corp_total)
    actual = actual_window_units(actuals, start, 14)

    scores = []
    for name, forecast in [
        ("corporate_raw", corp_sku),
        ("corporate_total_recent_shape", corp_recent),
        ("independent_recent_shape", recent),
    ]:
        scores.append(score_pair(forecast, actual, name, start))
        print(
            f"  {name}: forecast={forecast['ForecastUnits'].sum():,.0f} "
            f"actual={actual['SoldUnits'].sum():,.0f}",
            flush=True,
        )

    score_df = pd.DataFrame(scores)
    out_dir.mkdir(parents=True, exist_ok=True)
    score_df.to_csv(out_dir / "sale_holdout_scores.csv", index=False)
    corp_recent.to_csv(out_dir / "corporate_total_recent_shape_sku.csv", index=False)
    recent.to_csv(out_dir / "independent_recent_shape_sku.csv", index=False)
    corp_sku.to_csv(out_dir / "corporate_raw_sku.csv", index=False)
    meta = {
        "origin": start.date().isoformat(),
        "horizon_end": (start + pd.Timedelta(days=13)).date().isoformat(),
        "corporate_source": corp_label,
        "corporate_source_start": corp_start.date().isoformat(),
        "corporate_total": corp_total,
        "sold_units": float(actual["SoldUnits"].sum()),
    }
    (out_dir / "sale_holdout_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(score_df[
        ["Candidate", "ForecastUnits", "SoldUnits", "WAPE", "BiasPctForecastMinusActual",
         "SoldUnitForecastCoveragePct", "ZeroForecastSoldUnitPct"]
    ].to_string(index=False), flush=True)
    return score_df


def run_forward_july7(actuals: pd.DataFrame, out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    start = pd.Timestamp("2026-07-07")
    through = pd.Timestamp("2026-07-09")
    print(f"\n=== Forward origin {start.date()} (partial score through {through.date()}) ===", flush=True)

    product_info = INGESTION_SOURCE / "Product Info for BRG_2026-07-06.xlsx"
    corp_wide, corp_start = load_corporate_wide_from_product_info(product_info)
    if corp_start != start:
        print(f"  WARNING: Product Info start {corp_start.date()} != {start.date()}")

    corp_sku = wide_to_sku_total(corp_wide)
    source_universe = corp_wide[["SKU"]].drop_duplicates()
    recent = build_recent_sku_total(actuals, start, source_universe)
    corp_total = float(corp_sku["ForecastUnits"].sum())
    corp_recent_sku = allocate_total_by_shape(recent, corp_total)

    recent_daily, recent_meta = recent_daily_forecast(actuals, start, DEFAULT_LOOKBACK_DAYS)
    corp_recent_wide = allocate_daily_by_recent_shape(corp_wide, recent_daily, start)

    # Independent recent daily (free total)
    ind_recent_wide = recent_daily.pivot_table(
        index="SKU", columns="ForecastDate", values="ForecastUnits", aggfunc="sum", fill_value=0
    ).reset_index()
    date_cols = sorted([c for c in ind_recent_wide.columns if c != "SKU"])
    rename = {date_cols[i]: FD_COLUMNS[i] for i in range(min(14, len(date_cols)))}
    ind_recent_wide = ind_recent_wide.rename(columns=rename)
    for fd in FD_COLUMNS:
        if fd not in ind_recent_wide.columns:
            ind_recent_wide[fd] = 0
        ind_recent_wide[fd] = (
            pd.to_numeric(ind_recent_wide[fd], errors="coerce").fillna(0).round().clip(lower=0).astype(int)
        )
    ind_recent_wide = ind_recent_wide[["SKU", *FD_COLUMNS]]

    out_dir.mkdir(parents=True, exist_ok=True)
    daily_frames = []
    for name, wide in [
        ("corporate_raw", corp_wide),
        ("corporate_total_recent_shape", corp_recent_wide),
        ("independent_recent_shape", ind_recent_wide),
    ]:
        wide_path = out_dir / f"{name}_fd14.csv"
        wide.to_csv(wide_path, index=False)
        daily = wide_to_daily_long(wide, start, name)
        daily_frames.append(daily)
        print(
            f"  wrote {wide_path.name}: skus={len(wide):,} units={wide[[c for c in FD_COLUMNS if c in wide.columns]].sum().sum():,.0f}",
            flush=True,
        )

    daily_all = pd.concat(daily_frames, ignore_index=True)
    daily_all.to_parquet(out_dir / "forward_daily_forecasts.parquet", index=False)

    # Full 14d SKU scores (actuals only partial — still report full-horizon forecast vs partial actual for transparency)
    actual_partial = actual_window_units(actuals, start, days=3)  # Jul 7-9
    full_scores = []
    for name, forecast in [
        ("corporate_raw", corp_sku),
        ("corporate_total_recent_shape", corp_recent_sku),
        ("independent_recent_shape", recent),
    ]:
        # Compare 14d forecast to only 3d actuals is misleading for WAPE; score partial daily instead.
        full_scores.append(score_partial_daily(daily_all, actuals, name, start, through))

    score_df = pd.DataFrame(full_scores)
    score_df.to_csv(out_dir / "forward_partial_scores_2026-07-07_to_09.csv", index=False)
    meta = {
        "origin": start.date().isoformat(),
        "partial_through": through.date().isoformat(),
        "corporate_source": str(product_info),
        "corporate_start": corp_start.date().isoformat(),
        "corporate_14d_total": corp_total,
        "recent_daily_meta": recent_meta,
        "partial_sold_units": float(actual_partial["SoldUnits"].sum()),
        "notes": [
            "Partial scores use FD days 1-3 only (Jul 7-9) against modified actuals.",
            "corporate_total_recent_shape allocates each corporate FD-day total by that day's recent SKU shares.",
            "independent_recent_shape uses free recent total (diagnostic).",
        ],
    }
    (out_dir / "forward_metadata.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    print(score_df[
        ["Candidate", "ForecastUnits", "SoldUnits", "WAPE", "BiasPctForecastMinusActual",
         "SoldUnitForecastCoveragePct", "ZeroForecastSoldUnitPct", "PartialDays"]
    ].to_string(index=False), flush=True)
    return score_df, daily_all


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--skip-sale", action="store_true")
    p.add_argument("--skip-forward", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    print("Loading actuals...", flush=True)
    actuals = load_actuals(ACTUALS_PATH)
    print(
        f"  actuals {actuals['ActualDate'].min().date()} .. {actuals['ActualDate'].max().date()} "
        f"rows={len(actuals):,}",
        flush=True,
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = {"generated_at_utc": datetime.now(timezone.utc).isoformat()}

    if not args.skip_sale:
        sale_dir = OUT_ROOT / "sale_holdout_2026-06-18"
        sale_scores = run_sale_holdout(actuals, sale_dir)
        results["sale_holdout"] = str(sale_dir)
        results["sale_best"] = (
            sale_scores.sort_values("WAPE").iloc[0]["Candidate"] if not sale_scores.empty else None
        )

    if not args.skip_forward:
        fwd_dir = OUT_ROOT / "forward_2026-07-07_challenger"
        fwd_scores, _ = run_forward_july7(actuals, fwd_dir)
        results["forward"] = str(fwd_dir)
        results["forward_best_partial"] = (
            fwd_scores.sort_values("WAPE").iloc[0]["Candidate"] if not fwd_scores.empty else None
        )

    (OUT_ROOT / f"challenger_advance_{stamp}.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(f"\nDone. Outputs under {OUT_ROOT}", flush=True)


if __name__ == "__main__":
    main()
