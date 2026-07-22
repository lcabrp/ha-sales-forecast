# Forecast Model Validation & Findings — 2026-07-22

Companion to `FORECAST_MODEL_PROPOSALS_2026-07-22.md`. This document records the
tests that were actually run (offline, no live-AX) and — importantly for the
next maintainer — **why each test was chosen and what it does and does not
prove**. If a different LLM/engineer picks this up, start here to avoid
re-deriving the reasoning.

Reproduce everything with:

```bash
uv run python scripts/python/forecast_validate_category_pool.py      # guardrails + multi-window
uv run python scripts/python/forecast_backtest_category_pool.py       # July-7 frozen backtest + ablation
```

## Why these tests (and not "the usual" ones)

There is **no live-AX / corporate-DB access** in this environment and **no saved
per-origin corporate feed** for historical windows. Two consequences:

1. We cannot re-run the *corporate-anchored* candidate on many historical
   origins offline (no historical corporate totals to anchor on).
2. The single window where we *do* have a saved corporate feed **and** saved
   monitoring-scope actuals is the July 7-20 closeout.

So the validation is split to match what the data can honestly support:

- **July 7-20 frozen backtest (1 window, real corporate + real actuals):**
  measures the full stack, including the corporate anchor and the activation
  layer. This is the flagship evidence but it is one window.
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

## 2. July 7-20 frozen backtest — layer ablation (real corporate + real actuals)

Volume is identical across all anchored rows (corporate total, bias +0.01%), so
only allocation differs.

| Candidate | SKU WAPE | SKU use rate | Sold-unit coverage | Zero-demand unit % |
|---|---:|---:|---:|---:|
| corporate_total_recent_shape (champion) | 1.051 | 0.860 | 0.669 | 0.088 |
| catpool_corporate_anchor (+ lift-mix category reconciliation, **no** activation) | 1.182 | 0.842 | 0.620 | 0.104 |
| **catpool_corporate_anchor_activation (+ activation)** | **0.956** | **0.890** | **0.759** | **0.061** |
| corporate_raw | 1.548 | 0.881 | 0.351 | 0.101 |

**What this proves:** the win is driven by the **activation layer**, not by the
category step by itself. Adding lift-mix category reconciliation *without*
activation actually made WAPE worse (1.05 -> 1.18) at this origin, because the
56-day run-rate is contaminated by the June 21-July 4 sale and over-weights
GIRM. Adding the activation layer (origin-safe July-6 inventory/inbound:
+16,092 brand-new SKUs, +17,685 boosted low-history active SKUs, -6,290
down-weighted ending-season SKUs) more than recovers it and beats the champion
on every axis.

## 3. Oracle-total allocation backtest — 11 windows (2025-06 → 2026-06)

Every method receives the **actual** 14-day total, so this measures allocation
shape only (volume neutralized). Activation is **not** included here because
origin-safe inventory/inbound snapshots only exist from 2026-06-19; this
experiment therefore isolates the *category-mix* lever.

| Method | Mean WAPE | Mean coverage | WAPE wins (of 11) |
|---|---:|---:|---:|
| **catpool_liftmix** (event-lift category mix → recent within-category) | **0.7999** | 0.9716 | **7** |
| global_recent (champion-style single global pool) | 0.8024 | 0.9714 | 4 |
| catpool_recentmix (recent category mix → recent within-category) | 0.8027 | 0.9714 | 0 |

**What this proves (and the crucial identity):**

- `catpool_recentmix` ≈ `global_recent` almost exactly (0.8027 vs 0.8024). This
  is expected and is a **deliberate sanity control**: with a fixed total,
  splitting by (recent category mix) × (recent within-category share) is
  mathematically ~identical to a single global recent-share split. **Category
  reconciliation by itself is a no-op.** If a future change makes these two
  diverge, something is wrong.
- The real allocation lever is the **event-lift category mix**
  (`catpool_liftmix`): a **small but consistent** edge — lower mean WAPE and 7
  of 11 window wins. Per-window, it helps most at event-adjacent windows
  (2025-11-15: 0.510 vs 0.537; 2025-12-08: 0.766 vs 0.791) and slightly hurts at
  quiet windows (2026-04-15: 0.800 vs 0.771) where lift ≈ 1 adds noise.

## 4. Honest synthesis for the next maintainer

- **Direction is validated.** Corporate = volume anchor; the category-anchored
  allocation with activation is a real, measured improvement on the one full
  window we can test, and the allocation mechanism holds up (does not regress)
  across 11 volume-neutralized windows.
- **The activation layer is the money.** Category reconciliation alone is a
  no-op; event-lift mix is a small consistent edge; **activation is the large
  lever** — but it is currently validated on only one origin because inventory/
  inbound snapshots start 2026-06-19. Priority: accumulate more post-2026-06-19
  closeouts (or backfill snapshots) to multi-window the activation layer.
- **Known weakness — run-rate spike contamination.** The flat 56-day run-rate
  inflates categories that had a sale inside the lookback. This is why the
  independent volume anchor over-forecasts ~+47% and why lift-mix can hurt right
  after a sale. Fix path (backlog): calendar-aware baseline that excludes prior
  in-window sale days, or shrink toward a non-event baseline, before trusting
  the independent Stage-1 total. The corporate anchor sidesteps this and should
  remain the default volume source until this is fixed.
- **What is NOT yet proven:** multi-window corporate-anchored performance;
  activation across seasons; physical carton/pull efficiency (still a SKU-use
  proxy). Do not call this a champion until a multi-window frozen corporate
  comparison exists.

## 5. Output artifacts

```text
Output/ForecastAccuracy/handoff_eval/category_pool_backtest_2026-07-07/
  leaderboard.csv, category_scorecard.csv, category_pool_candidates.parquet, metadata_*.json
Output/ForecastAccuracy/handoff_eval/category_pool_validation/
  guardrails.json, allocation_backtest_detail.csv, allocation_backtest_summary.csv,
  allocation_backtest_wape_by_window.csv, validation_metadata.json
Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/
  (frozen Jul 21-Aug 3 shadow; evaluate on/after Aug 4; corporate total 165,008 preserved)
```
