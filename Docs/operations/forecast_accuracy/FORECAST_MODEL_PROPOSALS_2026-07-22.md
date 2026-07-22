# Forecast Model Proposals — 2026-07-22

Purpose: answer the question "are we on the right path, and can we add one or
two better models?" without another project reset. This document analyzes the
current state, then proposes and **prototypes** two new candidates that
implement the direction the project already committed to but had not yet built
as runnable, scored code.

Read `FORECAST_CURRENT_STATE.md` first; this file is additive to it.

## 1. Where the project actually stands

The architecture decision in `FORECAST_CURRENT_STATE.md` is sound and should be
kept:

- Corporate supplies the best **total-volume / sale-calendar** signal.
- Recent statistics are the best **SKU allocation** baseline.
- The next win is a **two-stage hierarchy**: forecast stable category-size
  pools (`GIRM`, `BOYM` = `ProductGroupCode + SizeGroupCode`), then allocate to
  the *current* assortment; ML stays in the narrow occurrence/residual role.
- Optimize a precision/coverage frontier, not WAPE alone.

What was missing was not more theory — it was a runnable candidate that (a)
reconciles the corporate total **by category** before splitting to SKUs, and
(b) builds an **origin-safe active assortment** so new SKUs get a category prior
and ending-season SKUs are down-weighted. The July closeout explicitly said a
global recent-share split "still needs category reconciliation" and flagged the
season-transition failure mode as the top open risk. Those are the two models
below.

The August 4 model (the frozen July 21–August 3 `corporate_raw` and
`corporate_total_recent_shape` shadows) is **untouched**. The new candidate is
added as a *third* frozen shadow in the same folder family so it can be scored
on the same closeout without disturbing the existing ones.

## 2. Proposed models

### Model A — Two-stage Category-Pool allocation

Stage 1 (category volume), two interchangeable anchors:

- `catpool_independent`: `CategoryTarget = PreOriginRunRate(56d) × 14 ×
  ShrunkMultiYearEventLift`. Run-rate uses a **complete calendar-day spine**
  (fixes the retired overlay's `nunique()` denominator bug). Lift is pooled over
  the prior 3 same-calendar windows, weighted by baseline units, shrunk toward
  1.0, and clipped to [0.5, 3.0].
- `catpool_corporate_anchor`: keep the **exact corporate daily totals**, but
  route each day's volume through the independent category mix (Hamilton /
  largest-remainder), then split within category to SKUs. This is the missing
  category-reconciliation step.

Stage 2 (SKU allocation): within each category, allocate the category total
across current-assortment SKUs by recent within-category demand share, using
deterministic largest-remainder rounding so category **and** daily totals are
preserved exactly. Days are shaped by each category's day-of-week profile.

### Model B — Season-transition activation layer (`--activation`)

Reshapes the Stage-2 SKU weights using the latest **pre-origin** pick-face
inventory and open-inbound snapshots (origin-safe):

- **Activate new SKUs**: present in inventory/inbound but with little or no pick
  history → receive a category/size prior weight instead of zero.
- **Down-weight ending-season SKUs**: positive recent demand but no pickable
  inventory and no open inbound → weight × 0.35.

This directly targets the documented season-transition risk: ending-season SKUs
keep spurious weight from the trailing 56 days while newly activated SKUs would
otherwise be invisible.

## 3. Prototype — honest frozen backtest (origin 2026-07-07, horizon 07-07→07-20)

Built from pre-origin facts only and scored against the **saved closeout
actuals** (`actual_sku_day.parquet`, 203,327 units) with the same metrics as
`FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md`. The harness re-scores the
existing frozen candidates as a correctness check and reproduces their
published numbers.

| Candidate | Units | Bias | SKU WAPE | Fcst+ SKUs | SKU use rate | Sold-unit coverage | Zero-demand unit % |
|---|---:|---:|---:|---:|---:|---:|---:|
| **catpool_corporate_anchor_activation (NEW)** | 204,654 | +0.01% | **0.96** | 10,041 | **0.89** | **0.76** | **0.06** |
| corporate_total_recent_shape (current champion diagnostic) | 204,654 | +0.01% | 1.05 | 8,530 | 0.86 | 0.67 | 0.09 |
| catpool_activation (independent volume, NEW) | 299,901 | +47% | 1.17 | 17,783 | 0.78 | 0.95 | 0.08 |
| catpool_corporate_anchor (no activation, NEW) | 204,654 | +0.01% | 1.18 | 8,305 | 0.84 | 0.62 | 0.10 |
| independent_recent_shape (free total) | 305,454 | +50% | 1.33 | 9,863 | 0.85 | 0.71 | 0.09 |
| corporate_raw | 204,654 | +0.01% | 1.55 | 3,366 | 0.88 | 0.35 | 0.10 |

Category cells (SKU WAPE, lower is better):

| Candidate | GIRM | BOYM |
|---|---:|---:|
| **catpool_corporate_anchor_activation (NEW)** | **1.17** | **0.80** |
| corporate_total_recent_shape | 1.26 | 1.00 |
| corporate_raw | 1.41 | 1.23 |

### What the result means

- The new **corporate-anchored, category-reconciled, activation** candidate
  **beats the current champion on every operational axis**: it keeps corporate's
  exact total, improves SKU WAPE 1.05 → 0.96, raises sold-unit coverage
  0.67 → 0.76, raises box/SKU precision 0.86 → 0.89, and cuts wasted units
  0.09 → 0.06. It wins at the GIRM and BOYM cell level too.
- The **activation layer is the difference-maker**: without it the same anchor
  scores WAPE 1.18 / coverage 0.62; with it WAPE 0.96 / coverage 0.76. The
  July 6 snapshot activated 16,092 brand-new SKUs, boosted 17,685 low-history
  active SKUs, and down-weighted 6,290 unsupported ending-season SKUs.
- The **independent (no-corporate) volume anchor over-forecasts ~+47%** because
  the 56-day lookback contains the June 21–July 4 sale spike. This confirms the
  project's standing finding that corporate is the better *total-volume* anchor;
  the independent path is a diagnostic, not a contender, until the pre-event
  spike is dampened or a proper event-regime model is added.
- Open tuning lever: the independent **category mix** still over-weights GIRM
  (30.7k vs 18.8k actual) for the same spike reason. Blending the category mix
  toward corporate's category mix, or excluding prior in-window sale days from
  the mix, should tighten category bias further.

## 4. Reproduce

```bash
# Frozen backtest + leaderboard for the 2026-07-07 origin
uv run python scripts/python/forecast_backtest_category_pool.py

# Build the new frozen July 21-August 3 shadow (sits beside the Aug 4 model)
uv run python scripts/python/forecast_model_category_pool.py \
  --origin 2026-07-21 \
  --ledger-db Output/ForecastAccuracy/handoff_eval/independent_hybrid_absolute_log_2026-07-07/ingestion_output/sku_ledger.db \
  --corporate-daily Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/forward_daily_forecasts.parquet \
  --activation \
  --output-dir Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow
```

New scripts (research candidates, not yet a promoted champion):

- `scripts/python/forecast_model_category_pool.py` — model + CLI.
- `scripts/python/forecast_backtest_category_pool.py` — frozen backtest/scorer.

## 5. Limitations and honest caveats

- Runs on portable Parquet/SQLite only; no live-AX / corporate-DB dependency.
  The July 7 backtest scores against the **saved** closeout actuals.
- Category crosswalk uses the July-6 handoff ledger (covers 98.6% of closeout
  units). Mirror the full ingestion ledger crosswalk (open item #1) before
  production use.
- The July 21 candidate is a **frozen forward shadow**: do not score it until
  the horizon closes on August 3. It preserves the corporate 165,008-unit total
  exactly and does not modify the existing shadows.
- This is one origin. Confirm the improvement across several completed windows
  before promoting a champion (see next steps).

## 6. Recommended next steps

1. Mirror the ingestion-ledger category crosswalk into this repo (open item #1).
2. Run the frozen backtest across multiple completed origins to confirm the
   improvement is not a single-window artifact.
3. Dampen the pre-event sale spike in the independent run-rate / category mix
   (calendar-aware baseline, or blend toward corporate's category mix).
4. Add the carton-use simulator so the SKU-use proxy becomes a real
   pull-efficiency metric.
5. Only then add ML in the narrow occurrence/residual-ranking role on top of the
   category-anchored base.
