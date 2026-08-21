"""Out-of-time validation of the origin-safe regime gate.

CONTEXT
-------
The multi-window backtest showed that re-allocating the corporate total
(``catpool_corporate_anchor``) helps ONLY when the corporate SKU forecast has
under-covered demand (mainly 2026), and hurts otherwise. A deployable policy
must therefore DECIDE, before demand is known, when to re-allocate. The naive
"best in-sample threshold" is not evidence it generalizes.

This script answers the honest question: **does an origin-safe gate, tuned only
on the past, improve accuracy on the future?**

POLICY
------
Origin-safe signal per window: ``ProxyCorpPositiveShare`` =
share of trailing-28d DirectPick demand that fell on SKUs the new corporate
upload forecasts positive (all pre-origin). Low proxy => corporate is about to
under-cover => use ``catpool_corporate_anchor``; else keep ``corporate_raw``.

  gated_wape(window) = catpool_wape if proxy < tau else corporate_raw_wape

EVALUATIONS
-----------
1. Single time split: tune tau on an early block, test on a held-out later block.
2. Expanding walk-forward: for each test origin, tune tau ONLY on strictly
   earlier origins (no leakage), apply to that origin. This is the primary,
   fully-honest generalization test and uses all data.
3. Block (moving-block) bootstrap CI on the walk-forward
   (gated_pooled_WAPE - corporate_raw_pooled_WAPE) to account for ~weekly
   overlapping 14-day windows. If the 95% CI crosses 0, it is not significant.
4. clean_frozen-only slice: the genuinely prospective corporate vintages.

Aggregate metric = UNIT-WEIGHTED pooled WAPE across windows
(sum_w wape_w * sold_w / sum_w sold_w), reconstructed from per-window WAPE and
sold units. Mean-of-window WAPE is also reported.

Reads only ``per_window.csv`` from the backtest output plus the corporate
archive provenance (for freeze class). No model rebuild.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from output_paths import PROJECT_ROOT

FA_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
CORPORATE_ARCHIVE = FA_ROOT / "history" / "parquet" / "forecast_sku_day.parquet"
DEFAULT_BACKTEST = FA_ROOT / "handoff_eval" / "multiwindow_corporate_backtest"

TAU_GRID = np.round(np.arange(0.30, 0.86, 0.01), 2)


def load_origin_table(backtest_dir: Path) -> pd.DataFrame:
    pw = pd.read_csv(backtest_dir / "per_window.csv")
    wide = pw.pivot_table(index="Origin", columns="Candidate", values="SKU_WAPE", aggfunc="first")
    sold = pw.drop_duplicates("Origin").set_index("Origin")["SoldUnits"]
    proxy = pw.drop_duplicates("Origin").set_index("Origin")["ProxyCorpPositiveShare"]
    df = pd.DataFrame({
        "wape_raw": wide["corporate_raw"],
        "wape_catpool": wide["catpool_corporate_anchor"],
        "sold": sold,
        "proxy": proxy,
    }).dropna(subset=["wape_raw", "wape_catpool", "proxy"]).reset_index()
    df["Origin"] = pd.to_datetime(df["Origin"])
    return df.sort_values("Origin").reset_index(drop=True)


def add_freeze_class(df: pd.DataFrame) -> pd.DataFrame:
    arc = pd.read_parquet(CORPORATE_ARCHIVE, columns=["SnapshotId", "InferredFileDate", "ForecastStartDate"]).drop_duplicates()
    arc["ForecastStartDate"] = pd.to_datetime(arc["ForecastStartDate"]).dt.normalize()
    arc["InferredFileDate"] = pd.to_datetime(arc["InferredFileDate"]).dt.normalize()
    prov = (arc.sort_values(["ForecastStartDate", "InferredFileDate", "SnapshotId"])
            .drop_duplicates("ForecastStartDate", keep="first"))
    days = (prov["InferredFileDate"] - prov["ForecastStartDate"]).dt.days
    prov = prov.assign(FreezeClass=np.select([days < 0, days == 0, days > 0],
                                              ["clean_frozen", "same_day", "late"], default="unknown"))
    prov = prov.rename(columns={"ForecastStartDate": "Origin"})[["Origin", "FreezeClass"]]
    return df.merge(prov, on="Origin", how="left")


def pooled_wape(wape: np.ndarray, sold: np.ndarray) -> float:
    s = sold.sum()
    return float((wape * sold).sum() / s) if s else float("nan")


def gated_wape(df: pd.DataFrame, tau: float) -> np.ndarray:
    return np.where(df["proxy"].to_numpy() < tau, df["wape_catpool"].to_numpy(), df["wape_raw"].to_numpy())


def best_tau(train: pd.DataFrame) -> float:
    sold = train["sold"].to_numpy()
    best, best_val = TAU_GRID[0], float("inf")
    for tau in TAU_GRID:
        val = pooled_wape(gated_wape(train, tau), sold)
        if val < best_val - 1e-12:
            best_val, best = val, tau
    return float(best)


def policy_row(name: str, wape: np.ndarray, df: pd.DataFrame) -> dict:
    sold = df["sold"].to_numpy()
    raw = df["wape_raw"].to_numpy()
    return {
        "Policy": name,
        "Windows": int(len(df)),
        "PooledWAPE": round(pooled_wape(wape, sold), 4),
        "MeanWAPE": round(float(np.mean(wape)), 4),
        "ImprovedVsRaw": int(np.sum(wape < raw - 1e-9)),
        "WorsenedVsRaw": int(np.sum(wape > raw + 1e-9)),
    }


def evaluate_set(df: pd.DataFrame, tau: float | None) -> pd.DataFrame:
    raw = df["wape_raw"].to_numpy()
    cat = df["wape_catpool"].to_numpy()
    oracle = np.minimum(raw, cat)
    rows = [
        policy_row("always_corporate_raw", raw, df),
        policy_row("always_catpool", cat, df),
        policy_row("oracle_perfect_gate(ceiling)", oracle, df),
    ]
    if tau is not None:
        rows.append(policy_row(f"gated(tau={tau:.2f})", gated_wape(df, tau), df))
    return pd.DataFrame(rows)


def walk_forward(df: pd.DataFrame, min_train: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """For each origin i >= min_train, tune tau on origins < i, apply to i."""
    recs = []
    for i in range(min_train, len(df)):
        train = df.iloc[:i]
        tau = best_tau(train)
        row = df.iloc[i]
        used_catpool = bool(row["proxy"] < tau)
        recs.append({
            "Origin": row["Origin"].date().isoformat(),
            "tau": tau,
            "proxy": round(float(row["proxy"]), 4),
            "used_catpool": used_catpool,
            "wape_gated": float(row["wape_catpool"] if used_catpool else row["wape_raw"]),
            "wape_raw": float(row["wape_raw"]),
            "wape_catpool": float(row["wape_catpool"]),
            "sold": float(row["sold"]),
            "FreezeClass": row.get("FreezeClass", "unknown"),
        })
    wf = pd.DataFrame(recs)
    summary = pd.DataFrame([
        policy_row("always_corporate_raw", wf["wape_raw"].to_numpy(),
                   wf.rename(columns={"wape_raw": "wape_raw"})),
        policy_row("always_catpool", wf["wape_catpool"].to_numpy(),
                   wf.assign(wape_raw=wf["wape_raw"])),
        {**policy_row("walkforward_gated", wf["wape_gated"].to_numpy(),
                      wf.assign(wape_raw=wf["wape_raw"])),
         "TriggeredCatpool": int(wf["used_catpool"].sum())},
        policy_row("oracle_perfect_gate(ceiling)",
                   np.minimum(wf["wape_raw"], wf["wape_catpool"]).to_numpy(),
                   wf.assign(wape_raw=wf["wape_raw"])),
    ])
    return wf, summary


def moving_block_bootstrap(wf: pd.DataFrame, block: int, n_boot: int, seed: int) -> dict:
    """95% CI on pooled(gated) - pooled(raw) over walk-forward test origins."""
    rng = np.random.default_rng(seed)
    g = wf["wape_gated"].to_numpy()
    r = wf["wape_raw"].to_numpy()
    s = wf["sold"].to_numpy()
    n = len(wf)
    if n < block:
        return {"n": n, "note": "too few windows for bootstrap"}
    n_blocks = int(np.ceil(n / block))
    starts_max = n - block
    diffs = []
    for _ in range(n_boot):
        idx = []
        for _b in range(n_blocks):
            st = rng.integers(0, starts_max + 1)
            idx.extend(range(st, st + block))
        idx = np.array(idx[:n])
        diffs.append(pooled_wape(g[idx], s[idx]) - pooled_wape(r[idx], s[idx]))
    diffs = np.array(diffs)
    point = pooled_wape(g, s) - pooled_wape(r, s)
    return {
        "n": n, "block": block, "n_boot": n_boot,
        "point_diff_pooled_wape": round(float(point), 4),
        "ci95_low": round(float(np.percentile(diffs, 2.5)), 4),
        "ci95_high": round(float(np.percentile(diffs, 97.5)), 4),
        "prob_gated_better": round(float(np.mean(diffs < 0)), 3),
    }


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("" if pd.isna(r[c]) else str(r[c]) for c in cols) + " |")
    return "\n".join(out) + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backtest-dir", type=Path, default=DEFAULT_BACKTEST)
    p.add_argument("--split-date", default="2025-07-01", help="Train < split-date <= Test.")
    p.add_argument("--min-train", type=int, default=40, help="Min train windows before walk-forward starts.")
    p.add_argument("--block", type=int, default=4, help="Moving-block bootstrap block length (origins).")
    p.add_argument("--n-boot", type=int, default=2000)
    p.add_argument("--seed", type=int, default=20260619)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_BACKTEST / "gate_validation")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    df = add_freeze_class(load_origin_table(args.backtest_dir))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    split = pd.Timestamp(args.split_date)
    train = df.loc[df["Origin"] < split]
    test = df.loc[df["Origin"] >= split]
    tau_star = best_tau(train)

    split_eval = evaluate_set(test, tau_star)
    train_eval = evaluate_set(train, tau_star)

    wf, wf_summary = walk_forward(df, args.min_train)
    boot = moving_block_bootstrap(wf, args.block, args.n_boot, args.seed)

    # clean_frozen slice (genuinely prospective) on walk-forward origins
    cf = wf.loc[wf["FreezeClass"].eq("clean_frozen")]
    cf_summary = pd.DataFrame([
        policy_row("always_corporate_raw", cf["wape_raw"].to_numpy(), cf.assign(wape_raw=cf["wape_raw"])),
        policy_row("walkforward_gated", cf["wape_gated"].to_numpy(), cf.assign(wape_raw=cf["wape_raw"])),
    ]) if not cf.empty else pd.DataFrame()

    # persist
    wf.to_csv(args.output_dir / "walk_forward_windows.csv", index=False)
    wf_summary.to_csv(args.output_dir / "walk_forward_summary.csv", index=False)
    split_eval.to_csv(args.output_dir / "single_split_test.csv", index=False)
    (args.output_dir / "bootstrap_ci.json").write_text(json.dumps(boot, indent=2), encoding="utf-8")
    (args.output_dir / "metadata.json").write_text(json.dumps({
        "split_date": args.split_date,
        "train_windows": int(len(train)), "test_windows": int(len(test)),
        "tau_star_on_train": tau_star,
        "min_train": args.min_train, "walk_forward_test_windows": int(len(wf)),
        "tau_grid": [float(t) for t in TAU_GRID],
    }, indent=2), encoding="utf-8")

    md = []
    md.append("# Origin-Safe Regime Gate — Out-of-Time Validation\n\n")
    md.append("**Question:** does a gate tuned only on the past improve the future? "
              "Metric = unit-weighted pooled SKU WAPE (lower better).\n\n")
    md.append(f"## 1. Single time split (train < {args.split_date} <= test)\n\n")
    md.append(f"Train windows: {len(train)}, test windows: {len(test)}, "
              f"tau* chosen on train = **{tau_star:.2f}**.\n\n")
    md.append("Test-set performance:\n\n")
    md.append(md_table(split_eval))
    md.append("\n(Train-set, for reference:)\n\n")
    md.append(md_table(train_eval))
    md.append("\n## 2. Expanding walk-forward (tau tuned only on strictly-earlier origins)\n\n")
    md.append(f"Test origins: {len(wf)} (from origin #{args.min_train + 1} onward).\n\n")
    md.append(md_table(wf_summary))
    md.append("\n## 3. Moving-block bootstrap CI on walk-forward (gated - corporate_raw)\n\n")
    md.append("```\n" + json.dumps(boot, indent=2) + "\n```\n")
    md.append("A 95% CI that includes 0 means the gate's aggregate gain is not statistically distinguishable from noise.\n")
    if not cf_summary.empty:
        md.append(f"\n## 4. clean_frozen slice within walk-forward ({len(cf)} genuinely-prospective windows)\n\n")
        md.append(md_table(cf_summary))
    md.append("\n## Read this honestly\n"
              "- If walk-forward `walkforward_gated` PooledWAPE ~= `always_corporate_raw`, the gate does "
              "not yet generalize and must NOT be promoted.\n"
              "- `oracle_perfect_gate(ceiling)` shows the maximum achievable if regime detection were perfect; "
              "the gap between the gate and the oracle is the room a better origin-safe signal could capture.\n"
              "- Overlapping 14-day windows are not independent; the block bootstrap is the honest uncertainty.\n")
    (args.output_dir / "GATE_VALIDATION.md").write_text("".join(md), encoding="utf-8")

    pd.set_option("display.width", 200)
    print(f"tau* (train) = {tau_star:.2f}")
    print("\nSINGLE-SPLIT TEST:"); print(split_eval.to_string(index=False))
    print("\nWALK-FORWARD:"); print(wf_summary.to_string(index=False))
    print("\nBOOTSTRAP CI:"); print(json.dumps(boot, indent=2))
    if not cf_summary.empty:
        print("\nCLEAN_FROZEN (walk-forward):"); print(cf_summary.to_string(index=False))
    print(f"\nWrote {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
