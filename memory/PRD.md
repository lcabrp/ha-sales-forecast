# ha-sales-forecast — Working Memory / PRD

## Problem statement (verbatim intent)
Validate the sales-forecast project direction and, without another reset,
propose and prototype 1-2 new forecasting models. Keep the existing "August 4"
model (frozen July 21–August 3 corporate shadow). Idea: learn stable
category/size pools (GIRM, BOYM = ProductGroupCode+SizeGroupCode) from prior
years, allocate to the *current* assortment (SKUs change YoY), and leverage
inventory/inbound availability + corporate promotion signal (Source/Promotions).

## Domain
Independent 14-day DirectPick demand forecast for Hanna Andersson Kentucky DC.
Target = completed DirectPick warehouse pick work. Pure-Python data-science repo
(pandas/pyarrow/sklearn), `uv`-managed, Python 3.13. No web app.

## Architecture decision (kept, from FORECAST_CURRENT_STATE.md)
Two-stage hierarchy: corporate = best total-volume/calendar anchor; recent stats
= best SKU allocation baseline; forecast stable category pools then allocate to
current SKUs; ML stays narrow (occurrence/residual). Optimize precision/coverage.

## Delivered 2026-07-22
- Analysis of full pipeline + docs (no reset needed; direction validated).
- NEW `scripts/python/forecast_model_category_pool.py`: two-stage category-pool
  model. Anchors: `catpool_independent` (run-rate×14×shrunk multi-year lift,
  calendar-day spine) and `catpool_corporate_anchor` (exact corporate daily
  totals reconciled by category → SKU). Optional `--activation` season-transition
  layer (origin-safe inventory/inbound: activate new SKUs w/ category prior,
  down-weight ending-season SKUs). Hamilton rounding preserves category+daily
  totals exactly.
- NEW `scripts/python/forecast_backtest_category_pool.py`: honest frozen backtest
  (origin 2026-07-07) vs saved closeout actuals; reuses repo metrics; reproduces
  published corporate_raw / corporate_total_recent_shape numbers as a check.
- Result: `catpool_corporate_anchor_activation` beats current champion on every
  axis — SKU WAPE 1.05→0.96, coverage 0.67→0.76, box precision 0.86→0.89,
  wasted units 0.09→0.06, bias +0.01% (corporate total preserved). Wins GIRM
  (1.17 vs 1.26) and BOYM (0.80 vs 1.00) cells too.
- NEW frozen July 21–Aug 3 shadow built alongside (not modifying) the Aug 4 model:
  `Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/`
  (165,008 units, corporate total preserved). Evaluable Aug 4.
- Doc: `Docs/operations/forecast_accuracy/FORECAST_MODEL_PROPOSALS_2026-07-22.md`.

## Known limitations
- Independent volume anchor over-forecasts (+47%): 56d lookback includes
  June 21–Jul 4 sale spike; category mix over-weights GIRM. Corporate anchor
  unaffected (preserves total). Portable-facts only; no live-AX. Crosswalk from
  Jul-6 handoff ledger (98.6% unit coverage).

## Backlog / next
- P1: Multi-window frozen backtest to confirm not a single-window artifact.
- P1: Mirror full ingestion-ledger category crosswalk (open item #1).
- P2: Dampen pre-event spike in run-rate/category mix (calendar-aware baseline).
- P2: Carton-use simulator (turn SKU-use proxy into pull-efficiency metric).
- P3: Add ML in narrow occurrence/residual role on category-anchored base.
