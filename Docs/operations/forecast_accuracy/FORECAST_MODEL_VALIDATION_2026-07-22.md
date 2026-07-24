# Forecast Model Validation & Findings — 2026-07-22

Companion to `FORECAST_MODEL_PROPOSALS_2026-07-22.md`. This document records the
tests that were actually run (offline, no live-AX) and — importantly for the
next maintainer — **why each test was chosen and what it does and does not
prove**. If a different LLM/engineer picks this up, start here to avoid
re-deriving the reasoning.

Reproduce everything with:

```bash
uv run python scripts/python/forecast_validate_category_pool.py      # guardrails + multi-window
uv run python scripts/python/forecast_backtest_category_pool.py       # July-7 origin-safe post-close diagnostic + ablation
```

## Why these tests (and not "the usual" ones)

There is **no live-AX / corporate-DB access** in this environment and **no saved
per-origin corporate feed** for historical windows. Two consequences:

1. We cannot re-run the *corporate-anchored* candidate on many historical
   origins offline (no historical corporate totals to anchor on).
2. The single window where we *do* have a saved corporate feed **and** saved
   monitoring-scope actuals is the July 7-20 closeout.

So the validation is split to match what the data can honestly support:

- **July 7-20 origin-safe post-close diagnostic (1 window, real corporate +
  real actuals):**
  measures the full stack, including the corporate anchor and the activation
  layer. This is the flagship research evidence but it is one window and was
  built after the close; it cannot retroactively win the historical contest.
- **Oracle-total allocation backtest (11 windows, offline):** neutralizes
  volume by giving every method the *actual* 14-day total, so it isolates
  **allocation shape quality** only. This is what tells us whether the
  allocation mechanism generalizes beyond one window.
- **Guardrail assertions:** leakage, exact total-preservation, and determinism —
  cheap invariants that any future edit must keep green.

## 1. Guardrail assertions — ALL PASS

| Check | Result | Why it matters |
|---|---|---|
| `no_leakage_load_history` | PASS (max date 2026-05-14 < origin 2026-05-15) | A frozen 14-day forecast must never read a row dated on/after the origin. |
| `hamilton_sum_exact` / `nonnegative` / `zero_total` | PASS | Largest-remainder rounding must preserve category & daily totals exactly. |
| `deterministic_output` | PASS | Same inputs must give byte-identical candidates (reproducibility across PCs/LLMs). |
| `corporate_daily_total_preserved` | PASS (corp 165,008 == anchor 165,008) | The corporate anchor must not change corporate's per-day volume. |

## 2. July 7-20 post-close diagnostic — layer ablation (real corporate + real actuals)

Volume is identical across all anchored rows (corporate total, bias +0.65%), so
only allocation differs.

| Candidate | SKU WAPE | SKU use rate | Sold-unit coverage | Zero-demand unit % |
|---|---:|---:|---:|---:|
| corporate_total_recent_shape (repaired diagnostic baseline) | 1.051 | **0.860** | 0.669 | **0.088** |
| catpool_corporate_anchor (+ de-spiked lift-mix category reconciliation, **no** activation) | 1.065 | 0.860 | 0.670 | 0.089 |
| **catpool_corporate_anchor_activation (+ gated activation)** | **0.890** | 0.830 | **0.773** | 0.091 |
| corporate_raw | 1.548 | 0.881 | 0.351 | 0.101 |

**What this proves:** the large July allocation gain is driven by the
**activation layer**, not the category step by itself. With the implemented
run-rate de-spiking, lift-mix category reconciliation without activation is
close to the global recent-shape baseline (WAPE 1.065 versus 1.051). Adding the
gated activation layer using origin-safe July-6 inventory/inbound improves WAPE
to **0.890** and coverage to **0.773**, but it reduces SKU-use precision to
**0.830** and raises zero-demand unit share slightly to **0.091**. This is a
stronger precision/coverage tradeoff, not a win on every axis. The activation
snapshot added 14,610 brand-new SKUs, boosted 17,218 low-history active SKUs,
and down-weighted 6,401 unsupported ending-season SKUs.

## 3. Oracle-total allocation backtest — 11 windows (2025-06 → 2026-06)

Every method receives the **actual** 14-day total, so this measures allocation
shape only (volume neutralized). This experiment isolates the *category-mix*
lever (activation is tested separately in §3b).

| Method | Mean WAPE | Mean coverage | WAPE wins (of 11) |
|---|---:|---:|---:|
| **catpool_liftmix** (event-lift category mix → recent within-category) | **0.7990** | 0.9716 | **7** |
| global_recent (repaired recent-shape baseline) | 0.8024 | 0.9714 | 2 |
| catpool_recentmix (recent category mix → recent within-category) | 0.8030 | 0.9714 | 2 |

**What this proves (and the crucial identity):**

- `catpool_recentmix` ≈ `global_recent` almost exactly (0.8030 vs 0.8024). This
  is expected and is a **deliberate sanity control**: with a fixed total,
  splitting by (recent category mix) × (recent within-category share) is
  mathematically ~identical to a single global recent-share split. **Category
  reconciliation by itself is a no-op.** If a future change makes these two
  diverge, something is wrong.
- The real allocation lever is the **event-lift category mix**
  (`catpool_liftmix`): a **small but consistent** edge — lower mean WAPE and 7
  of 11 window wins. Per-window, it helps most at event-adjacent windows
  (2025-11-15: 0.507 vs 0.537; 2025-12-08: 0.789 vs 0.791) and slightly hurts at
  quiet windows (2026-04-15: 0.781 vs 0.771) where lift ≈ 1 adds noise.

## 3b. Activation backtest — 7 windows (Apr–Jun 2026, oracle total)

This is the multi-window generalization of the activation layer that was only
testable on one window before. It uses the tracked, origin-safe
`ax_inventory_history_sku_day.parquet` (daily 2026-04-01→06-14) plus
`product_info_inbound_snapshots.parquet` as activation evidence, holds the
category mix fixed to lift-mix, and holds the total to the oracle, so the ONLY
difference is base vs activation within-category SKU weighting.

| Method | Mean WAPE | Mean coverage | Mean use rate |
|---|---:|---:|---:|
| catpool_liftmix (base) | **0.6297** | 0.9895 | **0.8094** |
| catpool_liftmix_activation | 0.9246 | **0.9986** | 0.3096 |

**What this proves — activation is SEASON-CONDITIONAL, and the current gate is
still too permissive.** In the stable mid-season Apr–Jun windows, gated
activation **hurts** WAPE (0.630 → 0.925) and sharply reduces box precision (use
rate 0.809 → 0.310), buying only a small coverage gain. Contrast with the
July-7 season-onset window (§2), where activation helps substantially (anchored
WAPE 1.065 → 0.890, coverage 0.670 → 0.773). The reason is structural:
activation's value is proportional to how much sold volume lands on **newly
active SKUs with no recent history**. That share is large at a seasonal
assortment reset and near zero in a stable mid-season, where the current gate
still admits thousands of low-probability SKUs. Refine the gate before
promotion.

## 4. Honest synthesis for the next maintainer

- **Direction has promising evidence.** Corporate remains the volume anchor;
  the category-anchored allocation with gated activation materially improves
  WAPE/coverage on the July-7 season-onset window, and the category-mix
  mechanism has a small edge across 11 volume-neutralized windows. The July
  result is post-close research evidence, not a retroactive champion.
- **Activation is SEASON-CONDITIONAL, and the implemented turnover gate needs
  refinement.** It is the large lever at a seasonal reset (July-7: WAPE
  1.065→0.890) but remains a large net negative mid-season (Apr–Jun:
  0.630→0.925, use rate 0.809→0.310).
- **Category reconciliation alone is a no-op** (proven by the identity control);
  the **event-lift category mix** is a small consistent edge.
- **Known weakness — run-rate spike contamination.** The implemented de-spiking
  is not sufficient to make the independent Stage-1 total safe: the current
  July diagnostic over-forecasts by +121.7%. Continue tightening the
  calendar-aware baseline or shrink toward a non-event baseline. The corporate
  anchor sidesteps this and should remain the default volume source.
- **What is NOT yet proven:** multi-window *corporate-anchored* performance;
  a proper *gated* activation across several transition windows; physical
  carton/pull efficiency (still a SKU-use proxy). Do not call this a champion
  until a multi-window frozen corporate comparison and a validated season gate
  exist.

## 5. Output artifacts

```text
Output/ForecastAccuracy/handoff_eval/category_pool_backtest_2026-07-07/
  leaderboard.csv, category_scorecard.csv, category_pool_candidates.parquet, metadata_*.json
Output/ForecastAccuracy/handoff_eval/category_pool_validation/
  guardrails.json, allocation_backtest_detail.csv, allocation_backtest_summary.csv,
  allocation_backtest_wape_by_window.csv, validation_metadata.json
Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/
  (generated Jul 22 for the Jul 21-Aug 3 horizon; late-origin diagnostic only;
   evaluate for learning on/after Aug 4; corporate total 165,008 preserved)
```
