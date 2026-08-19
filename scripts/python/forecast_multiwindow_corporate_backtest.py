"""Multi-window historical corporate-anchored backtest harness.

WHY THIS EXISTS
---------------
Until now, the corporate-anchored category-pool candidate could only be scored
on the single July 7-20 live window, because the project treated the corporate
anchor as a *live* feed that arrives once every two weeks. That forced every
promotion decision onto a "wait two weeks -> one noisy datapoint -> no
conclusion -> repeat" loop.

But the repo already stores a deep archive of historical corporate forecast
uploads:

  Output/ForecastAccuracy/history/parquet/forecast_sku_day.parquet
    -> ~157 corporate snapshots / ~152 distinct ForecastStartDate origins,
       per-SKU, per-day, 2022-08 -> 2026-06  (columns: SnapshotId,
       InferredFileDate, SKU, ForecastStartDate, ForecastDayOffset,
       ForecastDate, ForecastQty)

and matching SKU/day actuals for every one of those windows:

  Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_<year>.parquet

So we can replay the *frozen* corporate forecast at every historical origin,
run the corporate-anchored category-pool candidate on origin-safe history only,
and score all of them against real DirectPick actuals -- in a single run,
across ~130 clean windows instead of one.

WHAT IT SCORES  (all anchored to the same frozen corporate daily totals)
------------------------------------------------------------------------
* corporate_raw                         - the frozen corporate SKU/day upload, as-is (baseline).
* corporate_total_recent_shape          - corporate daily total re-split across SKUs by
                                          56-day global recent DirectPick share (Hamilton).
* catpool_corporate_anchor              - corporate daily total reconciled by category, then
                                          split within category by recent share (no activation).
* catpool_corporate_anchor_activation   - same + season-transition activation layer. NOTE:
                                          activation needs origin-safe inventory/inbound
                                          snapshots, which only exist from ~2026-04 onward. For
                                          earlier origins the activation layer has no evidence,
                                          the turnover gate collapses to 0, and this candidate is
                                          identical to catpool_corporate_anchor (reported as such).

FROZEN-ORIGIN DISCIPLINE
------------------------
* For each ForecastStartDate we use the EARLIEST-uploaded snapshot (min
  InferredFileDate) as the frozen vintage -- the forecast a planner would have
  had at the origin, before any later weekly overlay. (An operational-vintage
  variant is listed as a next step in the runbook.)
* The candidate build reads DirectPick history strictly before the origin
  (load_history is origin-safe by construction).
* Corporate daily totals are preserved exactly by every anchored candidate.

This harness only READS tracked portable facts. No live-AX, no writes to any
frozen forecast pack.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

import forecast_model_category_pool as cp  # noqa: E402
from forecast_model_category_pool import (  # noqa: E402
    HORIZON_DAYS,
    ModelConfig,
    build_candidates,
    hamilton_round,
    load_crosswalk,
    load_history,
    sku_recent_weights,
)
from forecast_backtest_category_pool import score_candidate  # noqa: E402
from forecast_schema import normalize_sku_series  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402

FA_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
CORPORATE_ARCHIVE = FA_ROOT / "history" / "parquet" / "forecast_sku_day.parquet"
DIRECT_PICK_DIR = FA_ROOT / "direct_pick_history" / "parquet"
DEFAULT_CROSSWALK = FA_ROOT / "product_attributes" / "sku_category_crosswalk.parquet"
DEFAULT_OUTPUT = FA_ROOT / "handoff_eval" / "multiwindow_corporate_backtest"

ANCHORED_CANDIDATES = [
    "corporate_raw",
    "corporate_total_recent_shape",
    "catpool_corporate_anchor",
    "catpool_corporate_anchor_activation",
]


# --------------------------------------------------------------------------- #
# Speed: cache the per-year DirectPick reads so 100+ origins do not re-read the
# same year Parquet files. We wrap the model module's own reader so the exact
# same origin-safe loading logic is used -- only cached.
# --------------------------------------------------------------------------- #
_ORIGINAL_READ_YEAR = cp._read_direct_pick_year


@functools.lru_cache(maxsize=16)
def _cached_read_year(directory_str: str, year: int) -> pd.DataFrame:
    return _ORIGINAL_READ_YEAR(Path(directory_str), year)


def _patched_read_year(directory: Path, year: int) -> pd.DataFrame:
    return _cached_read_year(str(directory), year).copy()


cp._read_direct_pick_year = _patched_read_year


# --------------------------------------------------------------------------- #
# Actuals
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=8)
def _actual_year(year: int) -> pd.DataFrame:
    path = DIRECT_PICK_DIR / f"direct_pick_sku_day_modified_{year}.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["PickDate", "SKU", "PickUnits"])
    frame = pd.read_parquet(path, columns=["PickDate", "SKU", "PickUnits"])
    frame["PickDate"] = pd.to_datetime(frame["PickDate"], errors="coerce").dt.normalize()
    frame["SKU"] = normalize_sku_series(frame["SKU"])
    frame["PickUnits"] = pd.to_numeric(frame["PickUnits"], errors="coerce").fillna(0.0)
    return frame


def actual_sku_for_window(origin: pd.Timestamp, through: pd.Timestamp) -> pd.DataFrame:
    frames = [_actual_year(y) for y in range(origin.year, through.year + 1)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["SKU", "SoldUnits"])
    allrows = pd.concat(frames, ignore_index=True)
    win = allrows.loc[allrows["PickDate"].between(origin, through)]
    out = win.groupby("SKU", as_index=False)["PickUnits"].sum().rename(columns={"PickUnits": "SoldUnits"})
    return out.loc[out["SoldUnits"].gt(0)].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Corporate archive
# --------------------------------------------------------------------------- #
def load_frozen_corporate_map() -> pd.DataFrame:
    """One frozen corporate vintage per ForecastStartDate (earliest upload)."""
    arc = pd.read_parquet(
        CORPORATE_ARCHIVE,
        columns=["SnapshotId", "InferredFileDate", "SKU", "ForecastStartDate", "ForecastDate", "ForecastQty"],
    )
    arc["ForecastStartDate"] = pd.to_datetime(arc["ForecastStartDate"], errors="coerce").dt.normalize()
    arc["ForecastDate"] = pd.to_datetime(arc["ForecastDate"], errors="coerce").dt.normalize()
    arc["InferredFileDate"] = pd.to_datetime(arc["InferredFileDate"], errors="coerce").dt.normalize()
    arc = arc.dropna(subset=["ForecastStartDate", "ForecastDate"])
    # earliest uploaded snapshot per start date
    order = (
        arc[["ForecastStartDate", "SnapshotId", "InferredFileDate"]]
        .drop_duplicates()
        .sort_values(["ForecastStartDate", "InferredFileDate", "SnapshotId"])
    )
    frozen = order.drop_duplicates("ForecastStartDate", keep="first")[["ForecastStartDate", "SnapshotId"]]
    arc = arc.merge(frozen, on=["ForecastStartDate", "SnapshotId"], how="inner")
    arc["SKU"] = normalize_sku_series(arc["SKU"])
    arc["ForecastQty"] = pd.to_numeric(arc["ForecastQty"], errors="coerce").fillna(0.0)
    return arc


def corporate_daily_for_origin(arc: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    through = origin + pd.Timedelta(days=HORIZON_DAYS - 1)
    sub = arc.loc[
        arc["ForecastStartDate"].eq(origin) & arc["ForecastDate"].between(origin, through),
        ["SKU", "ForecastDate", "ForecastQty"],
    ].rename(columns={"ForecastQty": "ForecastUnits"})
    sub = sub.loc[sub["SKU"].ne("") & sub["ForecastUnits"].gt(0)]
    return sub.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Baseline: corporate total re-split by global recent share
# --------------------------------------------------------------------------- #
def recent_shape_candidate(
    corporate_daily: pd.DataFrame, history: pd.DataFrame, config: ModelConfig
) -> pd.DataFrame:
    """Allocate each corporate daily total across SKUs by 56d global recent share."""
    weights = sku_recent_weights(history, pd.DataFrame({"SKU": [], "Category": []}), config)
    weights = weights.loc[weights["RecentUnits"].gt(0)]
    if weights.empty:
        return pd.DataFrame(columns=["SKU", "ForecastUnits"])
    skus = weights["SKU"].to_numpy()
    w = weights["RecentUnits"].to_numpy(dtype=float)
    daily = corporate_daily.groupby("ForecastDate", as_index=False)["ForecastUnits"].sum()
    acc = np.zeros(len(skus), dtype=np.int64)
    for _, row in daily.iterrows():
        acc += hamilton_round(w, int(round(float(row["ForecastUnits"]))))
    out = pd.DataFrame({"SKU": skus, "ForecastUnits": acc})
    return out.loc[out["ForecastUnits"].gt(0)].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# One window
# --------------------------------------------------------------------------- #
def run_window(arc: pd.DataFrame, crosswalk: pd.DataFrame, origin: pd.Timestamp,
               lookback_days: int, seasonal_years: int) -> list[dict[str, Any]]:
    through = origin + pd.Timedelta(days=HORIZON_DAYS - 1)
    corporate = corporate_daily_for_origin(arc, origin)
    if corporate.empty:
        return []
    actual = actual_sku_for_window(origin, through)
    if actual.empty or actual["SoldUnits"].sum() <= 0:
        return []

    cw_skus = set(crosswalk["SKU"])
    mapped_units = float(actual.loc[actual["SKU"].isin(cw_skus), "SoldUnits"].sum())
    cat_coverage = mapped_units / float(actual["SoldUnits"].sum())

    cfg_base = ModelConfig(origin=origin, lookback_days=lookback_days,
                           seasonal_years=seasonal_years, use_activation=False)
    history = load_history(cfg_base)

    per_candidate: dict[str, pd.DataFrame] = {}

    # corporate_raw
    per_candidate["corporate_raw"] = (
        corporate.groupby("SKU", as_index=False)["ForecastUnits"].sum()
    )
    # recent shape
    per_candidate["corporate_total_recent_shape"] = recent_shape_candidate(corporate, history, cfg_base)

    # category-pool anchored (base + activation)
    for activation in (False, True):
        cfg = ModelConfig(origin=origin, lookback_days=lookback_days,
                          seasonal_years=seasonal_years, use_activation=activation)
        combined, _meta = build_candidates(cfg, crosswalk, corporate)
        name = "catpool_corporate_anchor_activation" if activation else "catpool_corporate_anchor"
        grp = combined.loc[combined["Candidate"].eq(name)]
        per_candidate[name] = grp.groupby("SKU", as_index=False)["ForecastUnits"].sum()

    rows = []
    for name in ANCHORED_CANDIDATES:
        fsku = per_candidate.get(name)
        if fsku is None or fsku.empty:
            continue
        s = score_candidate(fsku, actual, name)
        s.update({
            "Origin": origin.date().isoformat(),
            "Through": through.date().isoformat(),
            "SoldUnits": float(actual["SoldUnits"].sum()),
            "CorporateUnits": float(corporate["ForecastUnits"].sum()),
            "CategoryCoveragePct": cat_coverage,
        })
        rows.append(s)
    return rows


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def summarize(per_window: pd.DataFrame) -> pd.DataFrame:
    ref = per_window.loc[per_window["Candidate"].eq("corporate_raw"),
                         ["Origin", "SKU_WAPE"]].rename(columns={"SKU_WAPE": "RawWAPE"})
    joined = per_window.merge(ref, on="Origin", how="left")
    joined["BeatsCorporateRaw"] = joined["SKU_WAPE"] < joined["RawWAPE"]
    out = []
    for name, grp in joined.groupby("Candidate"):
        out.append({
            "Candidate": name,
            "Windows": int(len(grp)),
            "MeanWAPE": float(grp["SKU_WAPE"].mean()),
            "MedianWAPE": float(grp["SKU_WAPE"].median()),
            "MeanCoveragePct": float(grp["SoldUnitCoveragePct"].mean()),
            "MeanSKUUsePct": float(grp["SKUUseRatePct"].mean()),
            "MeanBiasPct": float(grp["BiasPct"].mean()),
            "MeanZeroDemandPct": float(grp["ZeroDemandUnitPct"].mean()),
            "WinsVsCorporateRaw": int(grp["BeatsCorporateRaw"].sum()),
            "WinRateVsCorporateRaw": float(grp["BeatsCorporateRaw"].mean()),
        })
    order = {n: i for i, n in enumerate(ANCHORED_CANDIDATES)}
    return pd.DataFrame(out).sort_values("Candidate", key=lambda s: s.map(order)).reset_index(drop=True)


def write_markdown(summary: pd.DataFrame, per_window: pd.DataFrame, args, out_dir: Path) -> None:
    origins = sorted(per_window["Origin"].unique())
    lines = []
    lines.append("# Multi-Window Historical Corporate-Anchored Backtest — Results\n")
    lines.append(f"Generated by `scripts/python/forecast_multiwindow_corporate_backtest.py`.\n")
    lines.append(
        f"- Windows scored: **{len(origins)}** frozen corporate origins "
        f"({origins[0]} → {origins[-1]}).\n"
        f"- Frozen vintage per origin: earliest-uploaded snapshot.\n"
        f"- Lookback {args.lookback_days}d, seasonal years {args.seasonal_years}, "
        f"min ForecastStartDate {args.min_start}, min category coverage "
        f"{args.min_category_coverage:.0%}.\n"
        f"- Horizon: {HORIZON_DAYS} days. Metric: SKU WAPE (lower is better) vs DirectPick actuals.\n"
    )
    lines.append("\n## Leaderboard (mean across all scored windows)\n")
    disp = summary.copy()
    for c in ["MeanWAPE", "MedianWAPE"]:
        disp[c] = disp[c].map(lambda v: f"{v:.3f}")
    for c in ["MeanCoveragePct", "MeanSKUUsePct", "MeanBiasPct", "MeanZeroDemandPct", "WinRateVsCorporateRaw"]:
        disp[c] = disp[c].map(lambda v: f"{v*100:.1f}%")
    cols = list(disp.columns)
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join("---" for _ in cols) + " |\n")
    for _, r in disp.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |\n")
    lines.append("\n\n## Notes\n")
    lines.append(
        "- `catpool_corporate_anchor_activation` equals `catpool_corporate_anchor` on any "
        "origin without an origin-safe inventory/inbound snapshot (pre-~2026-04). The "
        "activation delta is only meaningful on recent windows; see per_window.csv.\n"
        "- All anchored candidates preserve the corporate daily totals exactly, so "
        "`BiasPct` is identical across them within a window; they differ only in SKU allocation.\n"
        "- `corporate_raw` bias is the corporate total-volume miss and is the same figure the "
        "closeout docs track separately from allocation.\n"
    )
    (out_dir / "leaderboard.md").write_text("".join(lines), encoding="utf-8")


def regime_breakdowns(per_window: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """By-year and by-corporate-coverage-regime breakdowns.

    NOTE: the regime label uses each window's *realized* corporate coverage,
    which is hindsight. It is an analysis lens, not an origin-safe deployment
    gate. Building an origin-safe collapse proxy is a documented next step.
    """
    pw = per_window.copy()
    pw["Year"] = pw["Origin"].str[:4]
    raw = pw.loc[pw["Candidate"].eq("corporate_raw"),
                 ["Origin", "SoldUnitCoveragePct", "SKU_WAPE"]].rename(
        columns={"SoldUnitCoveragePct": "RawCov", "SKU_WAPE": "RawWAPE"})
    pw = pw.merge(raw, on="Origin", how="left")
    pw["Regime"] = np.where(pw["RawCov"] >= 0.75, "healthy_corp(cov>=75%)", "degraded_corp(cov<75%)")
    pw["BeatsRaw"] = pw["SKU_WAPE"] < pw["RawWAPE"]

    def agg(df: pd.DataFrame, key: str) -> pd.DataFrame:
        g = df.groupby([key, "Candidate"]).agg(
            Windows=("Origin", "nunique"),
            MeanWAPE=("SKU_WAPE", "mean"),
            MeanCoveragePct=("SoldUnitCoveragePct", "mean"),
            WinRateVsRaw=("BeatsRaw", "mean"),
        ).reset_index()
        g["MeanWAPE"] = g["MeanWAPE"].round(3)
        g["MeanCoveragePct"] = (g["MeanCoveragePct"] * 100).round(1)
        g["WinRateVsRaw"] = (g["WinRateVsRaw"] * 100).round(1)
        return g

    return agg(pw, "Year"), agg(pw, "Regime")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--crosswalk", type=Path, default=DEFAULT_CROSSWALK)
    p.add_argument("--lookback-days", type=int, default=56)
    p.add_argument("--seasonal-years", type=int, default=3)
    p.add_argument("--min-start", default="2023-01-01",
                   help="Ignore corporate origins before this date (2022 crosswalk coverage is weak).")
    p.add_argument("--max-start", default=None, help="Optional upper bound on ForecastStartDate.")
    p.add_argument("--min-category-coverage", type=float, default=0.90,
                   help="Skip windows where < this fraction of sold units map to a category.")
    p.add_argument("--limit", type=int, default=0, help="Smoke test: only run the first N windows.")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    crosswalk = load_crosswalk(args.crosswalk)

    arc = load_frozen_corporate_map()
    starts = sorted(pd.Timestamp(d) for d in arc["ForecastStartDate"].unique())
    min_start = pd.Timestamp(args.min_start)
    starts = [d for d in starts if d >= min_start]
    if args.max_start:
        starts = [d for d in starts if d <= pd.Timestamp(args.max_start)]
    if args.limit:
        starts = starts[: args.limit]

    print(f"Scoring {len(starts)} corporate origins ({starts[0].date()} .. {starts[-1].date()})")
    all_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for i, origin in enumerate(starts, 1):
        rows = run_window(arc, crosswalk, origin, args.lookback_days, args.seasonal_years)
        if not rows:
            skipped.append({"Origin": origin.date().isoformat(), "reason": "no corporate/actuals"})
            continue
        cov = rows[0]["CategoryCoveragePct"]
        if cov < args.min_category_coverage:
            skipped.append({"Origin": origin.date().isoformat(),
                            "reason": f"category coverage {cov:.2%} < {args.min_category_coverage:.0%}"})
            continue
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(starts):
            print(f"  [{i}/{len(starts)}] {origin.date()}  scored")

    per_window = pd.DataFrame(all_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if per_window.empty:
        print("No windows scored.")
        return 1

    summary = summarize(per_window)
    by_year, by_regime = regime_breakdowns(per_window)

    per_window.to_csv(args.output_dir / "per_window.csv", index=False)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    by_year.to_csv(args.output_dir / "by_year.csv", index=False)
    by_regime.to_csv(args.output_dir / "by_regime.csv", index=False)
    pd.DataFrame(skipped).to_csv(args.output_dir / "skipped_windows.csv", index=False)
    (args.output_dir / "run_metadata.json").write_text(json.dumps({
        "windows_scored": int(per_window["Origin"].nunique()),
        "windows_skipped": len(skipped),
        "lookback_days": args.lookback_days,
        "seasonal_years": args.seasonal_years,
        "min_start": args.min_start,
        "max_start": args.max_start,
        "min_category_coverage": args.min_category_coverage,
        "frozen_vintage": "earliest_upload_per_forecast_start_date",
        "corporate_archive": str(CORPORATE_ARCHIVE.relative_to(PROJECT_ROOT)),
        "candidates": ANCHORED_CANDIDATES,
    }, indent=2), encoding="utf-8")
    write_markdown(summary, per_window, args, args.output_dir)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    print(f"\nScored {per_window['Origin'].nunique()} windows; skipped {len(skipped)}.\n")
    print(summary.to_string(index=False))
    print("\nBy corporate-coverage regime:")
    print(by_regime.to_string(index=False))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
