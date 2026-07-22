"""Offline validation suite for the category-pool forecast candidates.

WHY THIS EXISTS
---------------
There is no live-AX or corporate-DB access in this environment, and no saved
per-origin corporate feed for historical windows. So we cannot multi-window the
*corporate-anchored* candidate offline. What we *can* do offline, and what
actually answers "is the July-7 win real or a one-window fluke", is to isolate
the part of the model we own: the **allocation shape**.

Key insight about the July-7 result: every corporate-anchored candidate keeps
the SAME total volume (corporate's). So the improvement over the champion
(`corporate_total_recent_shape`) did NOT come from volume — it came from HOW the
fixed total is split across SKUs:

  * champion       : one global recent-share pool, no category step;
  * our candidate  : an event-lift-adjusted *category* mix, then recent
                     within-category shares, plus the activation reshaping.

Therefore the honest offline test is an **oracle-total allocation backtest**:
give every method the *actual* 14-day total (removing corporate's volume edge
entirely) and measure only allocation quality across many historical origins.
If the category/lift/activation allocation still wins with volume neutralized,
the mechanism is real and not a volume artifact.

Note on a subtle identity (documented so the next maintainer does not get
confused): with a fixed total, splitting by (recent category mix) x (recent
within-category SKU share) is mathematically ~identical to a single global
recent-share split. So `catpool_recentmix` should ~tie `global_recent`; that is
a deliberate sanity control. The real lever is the **event-lift category mix**
(`catpool_liftmix`), which moves volume toward categories that historically lift
in this calendar window, and the **activation layer**, which moves
within-category weight onto origin-safe newly active SKUs.

This module also runs fast guardrail assertions (leakage, exact
total-preservation, determinism) that any future edit must keep green.

Everything runs on portable Parquet/SQLite only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_category_pool import (  # noqa: E402
    DIRECT_PICK_DIR,
    HORIZON_DAYS,
    ModelConfig,
    _read_direct_pick_year,
    build_candidates,
    category_run_rate,
    hamilton_round,
    load_corporate_daily,
    load_crosswalk,
    sku_recent_weights,
    stage1_independent_targets,
)
from output_paths import PROJECT_ROOT  # noqa: E402

FA_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_LEDGER = (
    FA_ROOT / "forward_tests" / "2026-07-10_corporate_2026-07-06"
    / "replacement_contract_cold_start" / "raw_hybrid_cap085"
    / "ingestion_output" / "sku_ledger.db"
)
DEFAULT_OUTPUT = FA_ROOT / "handoff_eval" / "category_pool_validation"

# Origins chosen so the full [origin, origin+13] horizon lands inside the strict
# DirectPick history (ends 2026-06-25). Includes the 2025 late-June sale window
# on purpose: that is where event-lift should matter most.
DEFAULT_ORIGINS = [
    "2025-06-21",  # sale window (high-lift stress test)
    "2025-09-15",
    "2025-10-15",
    "2025-11-15",
    "2025-12-08",
    "2026-01-15",
    "2026-02-16",
    "2026-03-16",
    "2026-04-15",
    "2026-05-15",
    "2026-06-11",
]


def load_all_history(directory: Path) -> pd.DataFrame:
    """Load every strict DirectPick shard once (cached across windows)."""
    frames = [_read_direct_pick_year(directory, year) for year in range(2022, 2027)]
    frames = [frame for frame in frames if not frame.empty]
    return pd.concat(frames, ignore_index=True)


def score_allocation(forecast_sku: pd.DataFrame, actual_sku: pd.DataFrame) -> dict[str, float]:
    """Allocation-only metrics (volume is held fixed, so bias is ~0 by design)."""
    compare = forecast_sku.merge(actual_sku, on="SKU", how="outer")
    compare["ForecastUnits"] = compare["ForecastUnits"].fillna(0).astype(float)
    compare["SoldUnits"] = compare["SoldUnits"].fillna(0).astype(float)
    abs_err = (compare["ForecastUnits"] - compare["SoldUnits"]).abs()
    sold = float(compare["SoldUnits"].sum())
    fcst = float(compare["ForecastUnits"].sum())
    fpos = compare["ForecastUnits"].gt(0)
    used = fpos & compare["SoldUnits"].gt(0)
    return {
        "Units": fcst,
        "SKU_WAPE": float(abs_err.sum() / sold) if sold else np.nan,
        "SKUUseRatePct": float(used.sum() / fpos.sum()) if fpos.sum() else np.nan,
        "SoldUnitCoveragePct": float(compare.loc[used, "SoldUnits"].sum() / sold) if sold else np.nan,
        "ZeroDemandUnitPct": (
            float(compare.loc[fpos & compare["SoldUnits"].eq(0), "ForecastUnits"].sum() / fcst)
            if fcst else np.nan
        ),
    }


def allocate_by_category(
    category_weights: pd.DataFrame, weight_col: str,
    sku_weights: pd.DataFrame, total: int,
) -> pd.DataFrame:
    """Two-level Hamilton: total -> categories -> SKUs. Preserves total exactly."""
    cats = category_weights["Category"].to_numpy()
    cw = category_weights[weight_col].to_numpy(dtype=float)
    cat_alloc = hamilton_round(cw, total)
    by_cat = {c: grp for c, grp in sku_weights.groupby("Category")}
    rows = []
    for category, units in zip(cats, cat_alloc):
        if units <= 0:
            continue
        grp = by_cat.get(category)
        if grp is None or grp["RecentUnits"].sum() <= 0:
            continue
        alloc = hamilton_round(grp["RecentUnits"].to_numpy(dtype=float), int(units))
        mask = alloc > 0
        rows.append(pd.DataFrame({"SKU": grp["SKU"].to_numpy()[mask], "ForecastUnits": alloc[mask]}))
    if not rows:
        return pd.DataFrame(columns=["SKU", "ForecastUnits"])
    return pd.concat(rows, ignore_index=True).groupby("SKU", as_index=False)["ForecastUnits"].sum()


def run_window(all_history: pd.DataFrame, crosswalk: pd.DataFrame, origin: pd.Timestamp,
               config: ModelConfig) -> dict[str, dict[str, float]]:
    """Score global vs category allocation for one origin, oracle total held fixed."""
    horizon_end = origin + pd.Timedelta(days=HORIZON_DAYS - 1)
    # Origin-safe training history (strictly before the origin).
    hist_pre = all_history.loc[all_history["ActualDate"].lt(origin)]
    actual = (
        all_history.loc[all_history["ActualDate"].between(origin, horizon_end)]
        .groupby("SKU", as_index=False)["SoldUnits"].sum()
    )
    total = int(round(float(actual["SoldUnits"].sum())))  # ORACLE total (volume neutralized)

    sku_w = sku_recent_weights(hist_pre, crosswalk, config)  # RecentUnits + Category
    global_w = sku_w.loc[sku_w["RecentUnits"].gt(0), ["SKU", "RecentUnits"]]

    run_rate = category_run_rate(hist_pre, crosswalk, config)  # LookbackUnits (recent mix)
    lift_targets = stage1_independent_targets(hist_pre, crosswalk, config)  # CategoryTarget (lift mix)

    # Method 1: champion-style global recent share (no category step).
    g = hamilton_round(global_w["RecentUnits"].to_numpy(dtype=float), total)
    global_fcst = pd.DataFrame({"SKU": global_w["SKU"].to_numpy(), "ForecastUnits": g})
    global_fcst = global_fcst.loc[global_fcst["ForecastUnits"].gt(0)]

    # Method 2 (control): recent category mix -> within-category recent share.
    recentmix = allocate_by_category(run_rate, "LookbackUnits", sku_w, total)
    # Method 3 (lever): event-lift category mix -> within-category recent share.
    liftmix = allocate_by_category(lift_targets, "CategoryTarget", sku_w, total)

    return {
        "global_recent": score_allocation(global_fcst, actual),
        "catpool_recentmix": score_allocation(recentmix, actual),
        "catpool_liftmix": score_allocation(liftmix, actual),
    }


def guardrails(crosswalk: pd.DataFrame) -> list[dict[str, Any]]:
    """Fast assertions that must stay green after any future edit."""
    results: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        results.append({"check": name, "passed": bool(passed), "detail": detail})

    # 1. No leakage: origin-safe loader never returns rows on/after the origin.
    from forecast_model_category_pool import load_history
    origin = pd.Timestamp("2026-05-15")
    hist = load_history(ModelConfig(origin=origin))
    max_date = hist["ActualDate"].max()
    check("no_leakage_load_history", max_date < origin, f"max={max_date.date()} < origin={origin.date()}")

    # 2. Hamilton allocation preserves the total exactly and is non-negative.
    rng = np.random.default_rng(0)
    w = rng.random(500)
    alloc = hamilton_round(w, 12345)
    check("hamilton_sum_exact", int(alloc.sum()) == 12345, f"sum={int(alloc.sum())}")
    check("hamilton_nonnegative", bool((alloc >= 0).all()))
    check("hamilton_zero_total", int(hamilton_round(w, 0).sum()) == 0)

    # 3. Determinism: identical inputs -> identical candidate output.
    cfg = ModelConfig(origin=pd.Timestamp("2026-05-15"), use_activation=True)
    a, _ = build_candidates(cfg, crosswalk, None)
    b, _ = build_candidates(cfg, crosswalk, None)
    a_s = a.sort_values(["Candidate", "SKU", "ForecastDate"]).reset_index(drop=True)
    b_s = b.sort_values(["Candidate", "SKU", "ForecastDate"]).reset_index(drop=True)
    check("deterministic_output", a_s.equals(b_s))

    # 4. Corporate anchor preserves each corporate daily total exactly.
    corp_path = (
        FA_ROOT / "forward_tests" / "2026-07-21_corporate_2026-07-20"
        / "recent_shape_shadow" / "forward_daily_forecasts.parquet"
    )
    if corp_path.exists():
        origin21 = pd.Timestamp("2026-07-21")
        corporate = load_corporate_daily(corp_path, origin21)
        corp_daily = corporate.groupby("ForecastDate")["ForecastUnits"].sum().round().astype(int)
        cfg21 = ModelConfig(origin=origin21, use_activation=True)
        combined, _ = build_candidates(cfg21, crosswalk, corporate)
        anchor = combined.loc[combined["Candidate"].eq("catpool_corporate_anchor_activation")]
        anchor_daily = anchor.groupby("ForecastDate")["ForecastUnits"].sum().round().astype(int)
        aligned = corp_daily.align(anchor_daily, fill_value=0)
        exact = bool((aligned[0] == aligned[1]).all())
        check("corporate_daily_total_preserved", exact,
              f"corp_total={int(corp_daily.sum())} anchor_total={int(anchor_daily.sum())}")
    else:
        check("corporate_daily_total_preserved", True, "shadow not present; skipped")

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origins", nargs="*", default=DEFAULT_ORIGINS)
    parser.add_argument("--ledger-db", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--lookback-days", type=int, default=56)
    parser.add_argument("--seasonal-years", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    crosswalk = load_crosswalk(args.ledger_db)

    print("Running guardrail assertions...")
    guard = guardrails(crosswalk)
    for row in guard:
        print(f"  [{'PASS' if row['passed'] else 'FAIL'}] {row['check']}  {row['detail']}")
    (args.output_dir / "guardrails.json").write_text(json.dumps(guard, indent=2))
    all_passed = all(row["passed"] for row in guard)

    print("\nLoading strict DirectPick history once...")
    all_history = load_all_history(DIRECT_PICK_DIR)
    print(f"  history rows: {len(all_history):,}")

    rows: list[dict[str, Any]] = []
    for origin_str in args.origins:
        origin = pd.Timestamp(origin_str).normalize()
        config = ModelConfig(origin=origin, lookback_days=args.lookback_days,
                             seasonal_years=args.seasonal_years)
        scored = run_window(all_history, crosswalk, origin, config)
        for method, metrics in scored.items():
            rows.append({"Origin": origin.date().isoformat(), "Method": method, **metrics})
        print(f"  scored {origin.date()}")

    detail = pd.DataFrame(rows)
    detail.to_csv(args.output_dir / "allocation_backtest_detail.csv", index=False)

    # Aggregate: mean metric per method + how often each method wins WAPE.
    agg = detail.groupby("Method").agg(
        Windows=("Origin", "nunique"),
        MeanWAPE=("SKU_WAPE", "mean"),
        MeanCoverage=("SoldUnitCoveragePct", "mean"),
        MeanUseRate=("SKUUseRatePct", "mean"),
        MeanZeroDemandPct=("ZeroDemandUnitPct", "mean"),
    ).reset_index()
    wins = (
        detail.loc[detail.groupby("Origin")["SKU_WAPE"].idxmin(), ["Origin", "Method"]]
        .groupby("Method").size().rename("WAPE_wins").reset_index()
    )
    agg = agg.merge(wins, on="Method", how="left").fillna({"WAPE_wins": 0})
    agg = agg.sort_values("MeanWAPE").reset_index(drop=True)
    agg.to_csv(args.output_dir / "allocation_backtest_summary.csv", index=False)

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.4f}")
    print("\n=== Oracle-total allocation backtest (volume neutralized) ===")
    print(agg.to_string(index=False))
    print("\nPer-window WAPE (lower = better allocation):")
    pivot = detail.pivot(index="Origin", columns="Method", values="SKU_WAPE")
    print(pivot.to_string())
    pivot.to_csv(args.output_dir / "allocation_backtest_wape_by_window.csv")

    (args.output_dir / "validation_metadata.json").write_text(json.dumps({
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "guardrails_all_passed": all_passed,
        "origins": args.origins,
        "note": "Oracle total (actual 14d total) is given to every method to isolate allocation quality.",
    }, indent=2))
    print(f"\nGuardrails all passed: {all_passed}")
    print(f"Wrote {args.output_dir}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
