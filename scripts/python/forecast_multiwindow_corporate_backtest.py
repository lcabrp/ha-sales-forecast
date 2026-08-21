"""Multi-window historical corporate-anchored backtest harness (contract-repaired).

WHY THIS EXISTS
---------------
The repo already stores the historical corporate uploads
(``Output/ForecastAccuracy/history/parquet/forecast_sku_day.parquet`` and the
attribute companion ``forecast_sku_snapshot.parquet``) plus cached SKU/day
DirectPick actuals. That is enough to replay historical corporate vintages
offline and score allocation candidates against real actuals in one run, instead
of waiting two weeks per observation.

WHAT THIS VERSION FIXES (relative to the first cut)
---------------------------------------------------
This is *exploratory retrospective evidence*, not a champion decision. To keep
it honest the harness now:

1. CLASSIFIES each window by corporate-file availability instead of calling
   everything "frozen": ``clean_frozen`` (file date < origin), ``same_day``
   (== origin), ``late`` (> origin). Records SnapshotId and availability date.
2. Uses AS-OF category attributes per corporate vintage from
   ``forecast_sku_snapshot.parquet`` (snapshot-specific ProductGroupCode +
   SizeGroupCode), NOT the current 2026 crosswalk -> removes look-ahead in
   category identity.
3. Uses an ORIGIN-SAFE inclusion coverage (fraction of the *corporate forecast*
   units that map to a category), never horizon actuals, for window inclusion.
   Realized (actuals-based) coverage is still recorded, but only as a diagnostic.
4. DROPS the activation arm: pick-face inventory starts 2026-06-19, after the
   last archive origin (2026-06-02), so activation evaluated nothing here. It
   needs a separate inventory-covered harness (see the doc).
5. Adds an ORIGIN-SAFE regime gate evaluation (trailing-28d demand share on
   corporate-positive SKUs) over a threshold grid, and a NON-OVERLAPPING origin
   subset, so aggregate claims are not driven by hindsight or by ~weekly
   overlapping 14-day windows.

Reuses the production model code (``build_candidates``/``load_history``/
``hamilton_round``) and the closeout metric (``score_candidate``); it is a
harness, not a re-implementation. Read-only on all tracked facts.
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
    load_history,
    sku_recent_weights,
)
from forecast_backtest_category_pool import score_candidate  # noqa: E402
from forecast_schema import normalize_sku_series  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402

FA_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
CORPORATE_ARCHIVE = FA_ROOT / "history" / "parquet" / "forecast_sku_day.parquet"
SNAPSHOT_ARCHIVE = FA_ROOT / "history" / "parquet" / "forecast_sku_snapshot.parquet"
DIRECT_PICK_DIR = FA_ROOT / "direct_pick_history" / "parquet"
DEFAULT_OUTPUT = FA_ROOT / "handoff_eval" / "multiwindow_corporate_backtest"

# Activation intentionally excluded: no origin-safe inventory covers the archive.
ANCHORED_CANDIDATES = [
    "corporate_raw",
    "corporate_total_recent_shape",
    "catpool_corporate_anchor",
]
GATE_THRESHOLDS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


# --------------------------------------------------------------------------- #
# Speed: cache per-year DirectPick reads (uses the model's own origin-safe reader)
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


def actual_sku_between(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames = [_actual_year(y) for y in range(start.year, end.year + 1)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["SKU", "SoldUnits"])
    allrows = pd.concat(frames, ignore_index=True)
    win = allrows.loc[allrows["PickDate"].between(start, end)]
    out = win.groupby("SKU", as_index=False)["PickUnits"].sum().rename(columns={"PickUnits": "SoldUnits"})
    return out.loc[out["SoldUnits"].gt(0)].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Corporate archive + as-of category attributes
# --------------------------------------------------------------------------- #
def load_frozen_corporate_map() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (daily archive rows, per-origin provenance).

    Frozen vintage per ForecastStartDate = earliest uploaded snapshot.
    Provenance records SnapshotId and InferredFileDate (availability date) so
    each window can be classified clean_frozen / same_day / late.
    """
    arc = pd.read_parquet(
        CORPORATE_ARCHIVE,
        columns=["SnapshotId", "InferredFileDate", "SKU", "ForecastStartDate", "ForecastDate", "ForecastQty"],
    )
    arc["ForecastStartDate"] = pd.to_datetime(arc["ForecastStartDate"], errors="coerce").dt.normalize()
    arc["ForecastDate"] = pd.to_datetime(arc["ForecastDate"], errors="coerce").dt.normalize()
    arc["InferredFileDate"] = pd.to_datetime(arc["InferredFileDate"], errors="coerce").dt.normalize()
    arc = arc.dropna(subset=["ForecastStartDate", "ForecastDate"])

    provenance = (
        arc[["ForecastStartDate", "SnapshotId", "InferredFileDate"]]
        .drop_duplicates()
        .sort_values(["ForecastStartDate", "InferredFileDate", "SnapshotId"])
        .drop_duplicates("ForecastStartDate", keep="first")
        .reset_index(drop=True)
    )
    arc = arc.merge(provenance[["ForecastStartDate", "SnapshotId"]], on=["ForecastStartDate", "SnapshotId"], how="inner")
    arc["SKU"] = normalize_sku_series(arc["SKU"])
    arc["ForecastQty"] = pd.to_numeric(arc["ForecastQty"], errors="coerce").fillna(0.0)

    days = (provenance["InferredFileDate"] - provenance["ForecastStartDate"]).dt.days
    provenance["AvailabilityDays"] = days
    provenance["FreezeClass"] = np.select(
        [days < 0, days == 0, days > 0], ["clean_frozen", "same_day", "late"], default="unknown"
    )
    return arc, provenance


@functools.lru_cache(maxsize=256)
def _asof_crosswalk(snapshot_id: str) -> pd.DataFrame:
    """SKU -> category (ProductGroupCode+SizeGroupCode) as recorded in THAT vintage."""
    df = pd.read_parquet(
        SNAPSHOT_ARCHIVE,
        columns=["SnapshotId", "SKU", "ProductGroupCode", "SizeGroupCode"],
        filters=[("SnapshotId", "==", snapshot_id)],
    )
    df["SKU"] = normalize_sku_series(df["SKU"])
    cat = (
        df["ProductGroupCode"].fillna("").astype(str).str.strip().str.upper()
        + df["SizeGroupCode"].fillna("").astype(str).str.strip().str.upper()
    )
    df["Category"] = cat.replace("", "UNKNOWN")
    return df.loc[df["SKU"].ne(""), ["SKU", "Category"]].drop_duplicates("SKU").reset_index(drop=True)


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
def recent_shape_candidate(corporate_daily: pd.DataFrame, history: pd.DataFrame, config: ModelConfig) -> pd.DataFrame:
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
def run_window(arc: pd.DataFrame, origin: pd.Timestamp, snapshot_id: str,
               lookback_days: int, seasonal_years: int, min_cov: float) -> list[dict[str, Any]]:
    through = origin + pd.Timedelta(days=HORIZON_DAYS - 1)
    corporate = corporate_daily_for_origin(arc, origin)
    if corporate.empty:
        return []

    crosswalk = _asof_crosswalk(snapshot_id)  # AS-OF category identity

    # ORIGIN-SAFE inclusion coverage: share of corporate forecast units that map
    # to a known category in this vintage. Never uses horizon actuals.
    corp_sku = corporate.groupby("SKU", as_index=False)["ForecastUnits"].sum().merge(
        crosswalk, on="SKU", how="left")
    corp_sku["Category"] = corp_sku["Category"].fillna("UNKNOWN")
    mapped = float(corp_sku.loc[corp_sku["Category"].ne("UNKNOWN"), "ForecastUnits"].sum())
    corp_total = float(corp_sku["ForecastUnits"].sum())
    origin_safe_cov = mapped / corp_total if corp_total else 0.0
    if origin_safe_cov < min_cov:
        return []

    actual = actual_sku_between(origin, through)
    if actual.empty or actual["SoldUnits"].sum() <= 0:
        return []

    # Diagnostic-only (hindsight): realized category coverage of actuals.
    cw_skus = set(crosswalk.loc[crosswalk["Category"].ne("UNKNOWN"), "SKU"])
    realized_cov = float(actual.loc[actual["SKU"].isin(cw_skus), "SoldUnits"].sum()) / float(actual["SoldUnits"].sum())

    # Origin-safe regime proxy: trailing-28d demand share on corporate-positive SKUs.
    recent28 = actual_sku_between(origin - pd.Timedelta(days=28), origin - pd.Timedelta(days=1))
    corp_pos = set(corp_sku.loc[corp_sku["ForecastUnits"].gt(0), "SKU"])
    r_total = float(recent28["SoldUnits"].sum())
    proxy = (float(recent28.loc[recent28["SKU"].isin(corp_pos), "SoldUnits"].sum()) / r_total) if r_total else np.nan

    cfg = ModelConfig(origin=origin, lookback_days=lookback_days, seasonal_years=seasonal_years, use_activation=False)
    history = load_history(cfg)

    per_candidate: dict[str, pd.DataFrame] = {
        "corporate_raw": corp_sku[["SKU", "ForecastUnits"]],
        "corporate_total_recent_shape": recent_shape_candidate(corporate, history, cfg),
    }
    combined, _meta = build_candidates(cfg, crosswalk, corporate)
    grp = combined.loc[combined["Candidate"].eq("catpool_corporate_anchor")]
    per_candidate["catpool_corporate_anchor"] = grp.groupby("SKU", as_index=False)["ForecastUnits"].sum()

    rows = []
    for name in ANCHORED_CANDIDATES:
        fsku = per_candidate.get(name)
        if fsku is None or fsku.empty:
            continue
        s = score_candidate(fsku, actual, name)
        s.update({
            "Origin": origin.date().isoformat(),
            "Through": through.date().isoformat(),
            "SnapshotId": snapshot_id,
            "SoldUnits": float(actual["SoldUnits"].sum()),
            "CorporateUnits": corp_total,
            "OriginSafeMappingCovPct": origin_safe_cov,
            "RealizedCategoryCovPct": realized_cov,
            "ProxyCorpPositiveShare": proxy,
        })
        rows.append(s)
    return rows


# --------------------------------------------------------------------------- #
# Aggregation / reporting
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
            "MeanBiasPct": float(grp["BiasPct"].mean()),
            "WinsVsCorporateRaw": int(grp["BeatsCorporateRaw"].sum()),
            "WinRateVsCorporateRaw": float(grp["BeatsCorporateRaw"].mean()),
        })
    order = {n: i for i, n in enumerate(ANCHORED_CANDIDATES)}
    return pd.DataFrame(out).sort_values("Candidate", key=lambda s: s.map(order)).reset_index(drop=True)


def breakdowns(per_window: pd.DataFrame, provenance: pd.DataFrame) -> dict[str, pd.DataFrame]:
    pw = per_window.copy()
    pw["Year"] = pw["Origin"].str[:4]
    prov = provenance.copy()
    prov["Origin"] = prov["ForecastStartDate"].dt.date.astype(str)
    pw = pw.merge(prov[["Origin", "FreezeClass", "AvailabilityDays"]], on="Origin", how="left")
    raw = pw.loc[pw["Candidate"].eq("corporate_raw"),
                 ["Origin", "SoldUnitCoveragePct", "SKU_WAPE"]].rename(
        columns={"SoldUnitCoveragePct": "RawCov", "SKU_WAPE": "RawWAPE"})
    pw = pw.merge(raw, on="Origin", how="left")
    pw["Regime(hindsight)"] = np.where(pw["RawCov"] >= 0.75, "healthy(cov>=75%)", "degraded(cov<75%)")
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

    return {
        "by_year": agg(pw, "Year"),
        "by_regime_hindsight": agg(pw, "Regime(hindsight)"),
        "by_freeze_class": agg(pw, "FreezeClass"),
        "_augmented": pw,
    }


def origin_safe_gate_eval(pw_aug: pd.DataFrame) -> pd.DataFrame:
    """Deployable policy: use catpool when trailing-28d proxy < threshold, else corporate_raw.

    Reports aggregate WAPE and #windows-improved vs always-corporate_raw. This is
    the honest, origin-safe counterpart to the hindsight regime split.
    """
    wide = pw_aug.pivot_table(index="Origin", columns="Candidate", values="SKU_WAPE", aggfunc="first")
    proxy = pw_aug.drop_duplicates("Origin").set_index("Origin")["ProxyCorpPositiveShare"]
    base = wide["corporate_raw"]
    rows = []
    for thr in GATE_THRESHOLDS:
        use_catpool = proxy < thr
        gated = np.where(use_catpool, wide["catpool_corporate_anchor"], wide["corporate_raw"])
        gated = pd.Series(gated, index=wide.index)
        rows.append({
            "ProxyThreshold": thr,
            "WindowsTriggered": int(use_catpool.sum()),
            "GatedMeanWAPE": round(float(gated.mean()), 4),
            "CorporateRawMeanWAPE": round(float(base.mean()), 4),
            "AlwaysCatpoolMeanWAPE": round(float(wide["catpool_corporate_anchor"].mean()), 4),
            "WindowsImprovedVsRaw": int((gated < base - 1e-9).sum()),
            "WindowsWorsenedVsRaw": int((gated > base + 1e-9).sum()),
        })
    return pd.DataFrame(rows)


def non_overlapping_origins(origins: list[str]) -> list[str]:
    picked: list[str] = []
    last = None
    for o in sorted(origins):
        d = pd.Timestamp(o)
        if last is None or (d - last).days >= HORIZON_DAYS:
            picked.append(o)
            last = d
    return picked


def write_markdown(summary: pd.DataFrame, br: dict, gate: pd.DataFrame,
                   non_overlap_summary: pd.DataFrame, per_window: pd.DataFrame, args, out_dir: Path) -> None:
    origins = sorted(per_window["Origin"].unique())

    def table(df: pd.DataFrame) -> str:
        cols = list(df.columns)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
        for _, r in df.iterrows():
            lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
        return "\n".join(lines) + "\n"

    disp = summary.copy()
    for c in ["MeanWAPE", "MedianWAPE"]:
        disp[c] = disp[c].map(lambda v: f"{v:.3f}")
    for c in ["MeanCoveragePct", "MeanBiasPct", "WinRateVsCorporateRaw"]:
        disp[c] = disp[c].map(lambda v: f"{v * 100:.1f}%")

    md = []
    md.append("# Multi-Window Corporate-Anchored Backtest — Results (contract-repaired)\n\n")
    md.append("**Exploratory retrospective evidence, not a champion decision.**\n\n")
    md.append(
        f"- Windows scored: **{len(origins)}** ({origins[0]} -> {origins[-1]}).\n"
        "- As-of category attributes per corporate vintage (snapshot-specific).\n"
        f"- Origin-safe inclusion coverage >= {args.min_category_coverage:.0%} "
        "(corporate-forecast side; NOT actuals).\n"
        "- Activation arm excluded (no origin-safe inventory covers the archive).\n"
        "- Metric: SKU WAPE (lower better).\n\n"
    )
    md.append("## Overall leaderboard (mean over all scored windows)\n\n")
    md.append(table(disp))
    md.append("\n## By corporate-file freeze class (availability vs origin)\n\n")
    md.append(table(br["by_freeze_class"]))
    md.append("\n## By year\n\n")
    md.append(table(br["by_year"]))
    md.append("\n## By hindsight regime (DIAGNOSTIC ONLY — uses realized coverage)\n\n")
    md.append(table(br["by_regime_hindsight"]))
    md.append("\n## Origin-safe gate (DEPLOYABLE policy: catpool when trailing-28d proxy < threshold)\n\n")
    md.append(table(gate))
    md.append(
        "\nIf the best row barely beats `CorporateRawMeanWAPE`, the deployable gate is not yet "
        "effective — the hindsight regime split overstates the opportunity.\n"
    )
    md.append("\n## Non-overlapping origins (>=14 days apart) — independence check\n\n")
    md.append(table(non_overlap_summary))
    md.append(
        "\n## Honest limitations\n"
        "- The hindsight regime split near-tautologically favors reallocation and must not drive promotion.\n"
        "- Even non-overlapping origins are not i.i.d.; add block-bootstrap CIs before any significance claim.\n"
        "- `late`/`same_day` corporate files are operational-vintage, not clean prospective forecasts.\n"
        "- Activation is unevaluated here; wire origin-safe inventory history separately before any activation claim.\n"
    )
    (out_dir / "leaderboard.md").write_text("".join(md), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lookback-days", type=int, default=56)
    p.add_argument("--seasonal-years", type=int, default=3)
    p.add_argument("--min-start", default="2023-01-01")
    p.add_argument("--max-start", default=None)
    p.add_argument("--min-category-coverage", type=float, default=0.90,
                   help="Origin-safe (corporate-side) mapping coverage threshold for inclusion.")
    p.add_argument("--freeze-classes", default="clean_frozen,same_day,late",
                   help="Comma list of freeze classes to include.")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    keep_classes = {c.strip() for c in args.freeze_classes.split(",") if c.strip()}

    arc, provenance = load_frozen_corporate_map()
    prov = provenance.copy()
    prov = prov.loc[prov["ForecastStartDate"] >= pd.Timestamp(args.min_start)]
    if args.max_start:
        prov = prov.loc[prov["ForecastStartDate"] <= pd.Timestamp(args.max_start)]
    prov = prov.loc[prov["FreezeClass"].isin(keep_classes)].sort_values("ForecastStartDate")
    if args.limit:
        prov = prov.head(args.limit)

    print(f"Scoring {len(prov)} corporate origins "
          f"({prov['ForecastStartDate'].min().date()} .. {prov['ForecastStartDate'].max().date()})")
    all_rows: list[dict[str, Any]] = []
    for i, (_, prow) in enumerate(prov.iterrows(), 1):
        origin = prow["ForecastStartDate"]
        rows = run_window(arc, origin, prow["SnapshotId"], args.lookback_days,
                          args.seasonal_years, args.min_category_coverage)
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(prov):
            print(f"  [{i}/{len(prov)}] {origin.date()}")

    per_window = pd.DataFrame(all_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if per_window.empty:
        print("No windows scored.")
        return 1

    summary = summarize(per_window)
    br = breakdowns(per_window, provenance)
    gate = origin_safe_gate_eval(br["_augmented"])

    no_origins = non_overlapping_origins(list(per_window["Origin"].unique()))
    no_pw = per_window.loc[per_window["Origin"].isin(no_origins)]
    non_overlap_summary = summarize(no_pw)

    per_window.to_csv(args.output_dir / "per_window.csv", index=False)
    summary.to_csv(args.output_dir / "summary.csv", index=False)
    br["by_year"].to_csv(args.output_dir / "by_year.csv", index=False)
    br["by_regime_hindsight"].to_csv(args.output_dir / "by_regime_hindsight.csv", index=False)
    br["by_freeze_class"].to_csv(args.output_dir / "by_freeze_class.csv", index=False)
    gate.to_csv(args.output_dir / "origin_safe_gate.csv", index=False)
    non_overlap_summary.to_csv(args.output_dir / "non_overlapping_summary.csv", index=False)
    (args.output_dir / "run_metadata.json").write_text(json.dumps({
        "windows_scored": int(per_window["Origin"].nunique()),
        "non_overlapping_windows": len(no_origins),
        "lookback_days": args.lookback_days,
        "seasonal_years": args.seasonal_years,
        "min_start": args.min_start,
        "max_start": args.max_start,
        "min_category_coverage_origin_safe": args.min_category_coverage,
        "freeze_classes_included": sorted(keep_classes),
        "as_of_category_mapping": True,
        "activation_arm": "excluded (no origin-safe inventory covers archive)",
        "candidates": ANCHORED_CANDIDATES,
    }, indent=2), encoding="utf-8")
    write_markdown(summary, br, gate, non_overlap_summary, per_window, args, args.output_dir)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    print(f"\nScored {per_window['Origin'].nunique()} windows "
          f"({len(no_origins)} non-overlapping).\n")
    print(summary.to_string(index=False))
    print("\nBy freeze class:")
    print(br["by_freeze_class"].to_string(index=False))
    print("\nOrigin-safe deployable gate:")
    print(gate.to_string(index=False))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
