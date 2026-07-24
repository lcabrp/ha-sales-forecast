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

The authoritative August 4 contest (the prospectively frozen July 21–August 3
`corporate_raw` and `corporate_total_recent_shape` shadows) is **untouched**.
The category-pool candidate was built on July 22 after the July 21 origin. It is
saved in the same folder family so it can be scored against the same actuals
for learning, but it is a **late-origin diagnostic**, not a third contestant.

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

## 3. Prototype — origin-safe post-close diagnostic (origin 2026-07-07, horizon 07-07→07-20)

Built from pre-origin facts only and scored against the **saved closeout
actuals** (`actual_sku_day.parquet`, 203,327 units) with the same metrics as
`FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md`. The harness re-scores the
existing frozen candidates as a correctness check and reproduces their
published numbers.

| Candidate | Units | Bias | SKU WAPE | Fcst+ SKUs | SKU use rate | Sold-unit coverage | Zero-demand unit % |
|---|---:|---:|---:|---:|---:|---:|---:|
| **catpool_corporate_anchor_activation (NEW)** | 204,654 | +0.65% | **0.890** | 11,290 | 0.830 | **0.773** | 0.091 |
| corporate_total_recent_shape (current repaired diagnostic) | 204,654 | +0.65% | 1.051 | 8,530 | **0.860** | 0.669 | **0.088** |
| catpool_corporate_anchor (no activation, NEW) | 204,654 | +0.65% | 1.065 | 8,672 | 0.860 | 0.670 | 0.089 |
| independent_recent_shape (free total) | 305,454 | +50.23% | 1.334 | 9,863 | 0.849 | 0.711 | 0.088 |
| corporate_raw | 204,654 | +0.65% | 1.548 | 3,366 | 0.881 | 0.351 | 0.101 |
| catpool_activation (independent volume, NEW) | 450,827 | +121.73% | 1.705 | 26,748 | 0.551 | 0.970 | 0.187 |
| catpool_independent (no activation, NEW) | 450,827 | +121.73% | 1.871 | 14,836 | 0.796 | 0.877 | 0.094 |

Category cells (SKU WAPE, lower is better):

| Candidate | GIRM | BOYM |
|---|---:|---:|
| **catpool_corporate_anchor_activation (NEW)** | **1.11** | **0.84** |
| corporate_total_recent_shape | 1.26 | 1.00 |
| corporate_raw | 1.41 | 1.23 |

### What the result means

- The new **corporate-anchored, category-reconciled, gated-activation**
  diagnostic keeps the exact corporate total, improves SKU WAPE **1.051 →
  0.890**, and raises sold-unit coverage **0.669 → 0.773**. That gain has a
  modest cost: SKU-use precision falls **0.860 → 0.830**, and zero-demand unit
  share rises **0.088 → 0.091**. It is a stronger precision/coverage tradeoff,
  not a universal win. It also improves GIRM WAPE **1.26 → 1.11** and BOYM
  **1.00 → 0.84**.
- The **activation layer is the difference-maker** in the July diagnostic:
  without it the same corporate anchor scores WAPE 1.065 / coverage 0.670; with
  it WAPE 0.890 / coverage 0.773. The implemented run-rate de-spiking also
  brings the no-activation category anchor close to the global recent-shape
  baseline. **Important nuance:** activation remains season-conditional. Even
  with the current turnover gate, it is a large positive lever at the
  early-July reset but a net negative in the Apr-Jun oracle backtest (WAPE
  0.630→0.925, use rate 0.809→0.310). The gate therefore remains too permissive
  for promotion. See `FORECAST_MODEL_VALIDATION_2026-07-22.md` §2 and §3b. The
  July 6 snapshot activated 14,610 brand-new SKUs, boosted 17,218 low-history
  active SKUs, and down-weighted 6,401 unsupported ending-season SKUs.
- The **independent (no-corporate) volume anchor over-forecasts +121.7%** because
  the 56-day lookback contains the June 21–July 4 sale spike. This confirms the
  project's standing finding that corporate is the better *total-volume* anchor;
  the independent path is a diagnostic, not a contender, until the pre-event
  spike is dampened or a proper event-regime model is added.
- Open tuning lever: the independent **category mix** still over-weights GIRM
  (61.7k vs 18.9k actual) for the same spike reason. Blending the category mix
  toward corporate's category mix, or excluding prior in-window sale days from
  the mix, should tighten category bias further.

## 4. Reproduce

```bash
# Origin-safe post-close diagnostic + leaderboard for the 2026-07-07 origin
uv run python scripts/python/forecast_backtest_category_pool.py

# Rebuild the July 22 late-origin diagnostic (sits beside the Aug 4 contestants)
uv run python scripts/python/forecast_model_category_pool.py \
  --origin 2026-07-21 \
  --ledger-db Output/ForecastAccuracy/handoff_eval/independent_hybrid_absolute_log_2026-07-07/ingestion_output/sku_ledger.db \
  --corporate-daily Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/forward_daily_forecasts.parquet \
  --activation \
  --output-dir Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow
```

New scripts (research candidates, not yet a promoted champion):

- `scripts/python/forecast_model_category_pool.py` — model + CLI.
- `scripts/python/forecast_backtest_category_pool.py` — origin-safe post-close
  diagnostic/scorer.
- `scripts/python/forecast_validate_category_pool.py` — guardrails + multi-window
  oracle-total allocation backtest. See `FORECAST_MODEL_VALIDATION_2026-07-22.md`.

## 5. Limitations and honest caveats

- Runs on portable Parquet/SQLite only; no live-AX / corporate-DB dependency.
  The July 7 backtest scores against the **saved** closeout actuals.
- Backtest and validation defaults use the tracked canonical crosswalk
  (`product_attributes/sku_category_crosswalk.parquet`, 113,824 SKUs). The
  saved July 22 forward diagnostic was built with the immutable July-6 handoff
  ledger named in the reproduction command.
- The category-pool July 21-dated artifact was generated on July 22. Do not
  score it until the horizon closes on August 3, and then report it only as a
  **late-origin diagnostic**. It preserves the corporate 165,008-unit total
  exactly and does not modify the legitimate prospective shadows.
- This is one origin. Confirm the improvement across several completed windows
  before promoting a champion (see next steps).

## 6. Recommended next steps

1. Refine the implemented assortment-turnover gate until the activation layer
   is neutral-or-positive in stable mid-season windows.
2. Run origin-safe diagnostics across multiple completed origins to confirm the
   improvement is not a single-window artifact.
3. Validate and tighten the implemented pre-event de-spiking in the independent
   run-rate/category mix.
4. Add the carton-use simulator so the SKU-use proxy becomes a real
   pull-efficiency metric.
5. Only then add ML in the narrow occurrence/residual-ranking role on top of the
   category-anchored base.
