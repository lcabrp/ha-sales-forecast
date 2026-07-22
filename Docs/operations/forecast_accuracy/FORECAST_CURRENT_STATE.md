# Forecast Current State

Authoritative as of 2026-07-21. This is the first document to read for forecast
work. It replaces the June reset diary, model-input lab notebook, intermediate
handoffs, and smoke-test champion narratives as active guidance. Git history
retains those records when provenance is needed.

## Current Decision

Do not reset the project again. Narrow the architecture:

1. Use the best future-known daily and 14-day volume signal. The completed July
   closeout supports using the corporate total as the current anchor because it
   contains promotion and commercial knowledge that the warehouse model does
   not yet reproduce reliably.
2. Forecast stable category-size pools, such as `GIRM` and `BOYM`, before
   allocating to current SKUs. Do not use a velocity suffix such as the `A` in
   `GIRMA` as a stable year-over-year category identity.
3. Allocate category totals selectively across current SKUs. Recent fulfilled
   demand is the statistical baseline. ML may improve occurrence probability,
   promotion/newness response, and residual ranking; it is not the owner of the
   entire total-to-SKU problem.
4. Optimize a precision/coverage frontier. Fewer forecast-positive SKUs are
   desirable only when they still cover enough sold units. A forecast-positive
   SKU is a box-use proxy, not a physical carton measurement.

The independent ML hybrid is not the current champion. No model should be called
a champion from a smoke test, a leaky rolling-origin score, a partial horizon,
or a rebuilt-after-close diagnostic.

## Operational Target

- Warehouse/company/partition: `4010` / `ha` / `5637144576`.
- Demand target: completed `DirectPick` work at pick-line modified time, using
  the monitoring location-profile and exclusion scope.
- Forecast horizon: 14 calendar days, frozen at the origin.
- Primary comparison levels: daily total, 14-day total, category total, and
  individual SKU allocation.
- Required allocation metrics: SKU WAPE, bias, sold-unit coverage,
  forecast-positive SKU use rate, zero-forecast sold units, and forecast units
  assigned to zero-demand SKUs.
- Physical carton/pull efficiency is not yet measured. Add a carton simulator
  before treating the SKU-use proxy as an operational cost result.

## Source Precedence

Use the narrowest portable source that satisfies the requested window. Record
the source path, date window, row count, and provenance in every evaluation.

1. `ha-kydc-monitoring` proves completed-day availability and aggregate Pick
   totals through `Output/Monitoring/Monitoring_History.db`.
2. Use a canonical monitoring-scope SKU/day fact when one exists and covers the
   horizon.
3. Use a saved evaluation-local monitoring-scope SKU/day fact for a repeated
   closeout.
4. Use the read-only live AX fallback in `forecast_window_compare.py` only when
   current SKU/day detail is unavailable. Reconcile it to monitoring totals.

The legacy `history/parquet/actual_sku_day_modified.parquet` is broader than the
strict monitoring target and ends on 2026-07-09. It is historical/model input,
not the default current closeout source.

Other ownership boundaries:

- `ha-ingestion-pipeline` owns Product Info parsing, production AX-shaped
  outputs, and the current SKU/category ledger.
- `ha-kydc-monitoring` owns daily monitoring, pick-face inventory, open inbound,
  and confirmed operational forecast timelines.
- `ha-sales-forecast` owns forecast research, portable research facts,
  candidates, and evaluation evidence.
- `ha-zoning-slotting` is provenance only.

Compatibility copies of ingestion modules in this repo are not production
sources of truth. They were removed on 2026-07-21. Validate any upload-facing
candidate with the active ingestion repo; do not restore copied parsers or
roundtrip code here.

## Frozen Evaluation Rules

- Freeze every candidate and business input at the same origin.
- Score the same 14 dates and the same actual-demand contract.
- Never use actuals, inventory, inbound, promotion revisions, or product status
  first observed after the origin.
- In-window actual-demand lags are leakage for a frozen 14-day forecast.
- Separate total-volume accuracy from SKU allocation accuracy.
- Distinguish an honestly frozen candidate from a method rebuilt after the
  horizon. Post-close diagnostics may explain a method but cannot win the
  historical contest.
- Preserve daily totals exactly when redistributing them across SKUs. Use
  deterministic largest-remainder rounding, not independent SKU rounding.

## Latest Completed Evidence

The authoritative completed comparison is July 7-20, 2026:

| Candidate | Units | Bias | SKU WAPE | SKU use rate | Sold-unit coverage | Units on zero-demand SKUs |
|---|---:|---:|---:|---:|---:|---:|
| Corporate raw | 204,654 | +0.65% | 154.77% | 88.09% | 35.15% | 10.14% |
| Independent ML hybrid | 157,409 | -22.58% | 131.48% | 76.39% | 71.24% | 29.91% |
| Corporate total + recent shape, repaired diagnostic | 204,654 | +0.65% | 105.13% | 86.01% | 66.86% | 8.83% |

Actual detail contained 203,327 units across 77,892 SKU/day rows and reconciled
within 20 units of the monitoring aggregate. The originally frozen
corporate-total/recent-shape artifact lost 11,089 units through bad rounding;
its honest frozen result remains 193,565 units. The repaired result is an
engineering diagnostic, not a retroactive contestant.

Read `FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md` for the full scorecard and
category evidence.

## Current Forward Shadow

The frozen July 21-August 3 comparison contains:

- corporate raw: 165,008 units across 5,792 positive SKUs;
- corporate total plus 56-day recent statistical shape: 165,008 units across
  8,389 positive SKUs.

It cannot be evaluated until the horizon closes. No hindsight ML candidate was
created for this origin. The existing model panel ends on 2026-06-08, and the
July 21 promotion workbook provides only a one-day effective date.

### Season-transition limitation

The 56-day recent shape is a valid frozen baseline, but it assumes that the SKU
assortment is reasonably stable. A major seasonal reset can violate that
assumption: ending-season SKUs retain historical weight while newly activated
SKUs have little or no pick history. Do not modify the frozen July 21 candidate;
measure this failure mode at closeout and address it in the next pre-origin
challenger.

An operational investigation reported a July 15-20 collapse in exact
replenishment-zone placement concentrated in Velocity AA. It also reported a
large July 14-21 pick-face inflow and no empty locations in the examined AA
sub-zones. Treat these figures as operational context until reproduced from a
saved report in this repo. The important forecast interpretation is:

- inventory appearance, inventory growth, inbound, lifecycle status, and a
  replenishment request can be evidence that a SKU is entering the active
  assortment;
- the zone where replenishment actually landed is not a demand or season
  signal when intended zones are full;
- replenishment volume alone is not proof of customer demand;
- an ending-season SKU should not retain weight solely because it sold during
  the trailing 56 days.

The next prospectively frozen challenger should keep the best known corporate
daily totals, forecast stable category/size pools, build an origin-safe active
assortment, and give new SKUs category/size priors instead of requiring their
own 56-day history. Use promotion eligibility only for dates supported by the
source workbook; the July 21 PDL must not be extended through August 3 without
an explicit end date. Any candidate created after the July 21 origin is a
late-origin diagnostic, not a third frozen contestant.

## Active Workflow

```powershell
uv run python scripts/python/sync_monitoring_forecast_artifacts.py

uv run python scripts/python/forecast_actuals_source_audit.py `
  --start-date YYYY-MM-DD --through-date YYYY-MM-DD

uv run python scripts/python/forecast_window_compare.py `
  --start-date YYYY-MM-DD --through-date YYYY-MM-DD `
  --daily-forecast <frozen-candidates.parquet> `
  --live-ax --output-dir <closeout-directory>
```

For the transparent corporate-total/recent-shape forward shadow, use
`forecast_forward_recent_shape.py`. Promotion extraction uses
`extract_promotions.py` followed by a bounded
`forecast_promo_sku_features.py --start-date ... --merge-existing` refresh.

## Open Work, In Order

1. Mirror the current ingestion-ledger SKU/category crosswalk with provenance.
2. Implement and evaluate a season-transition-aware category-total to
   current-SKU allocation using origin-safe lifecycle, inventory, inbound,
   replenishment-activation, and promotion evidence.
3. Add an explicit occurrence/selection threshold and precision/coverage
   scorecard.
4. Add the carton-use simulator.
5. Rebuild a future-safe model panel before testing ML occurrence or residual
   challengers.
6. Use BigQuery inventory history only after its schema and as-of coverage are
   confirmed; monitoring remains the current operational source.

## Reading Order

1. This file.
2. `FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md` for current evidence.
3. `FORECAST_DATA_LANDSCAPE_2026-07-20.md` for detailed ownership and data
   contracts.
4. `FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md` only when moving, rebuilding, or
   committing artifacts.

### Active research candidate (2026-07-22, not yet champion)

A runnable two-stage category-pool candidate now implements the direction below.
For continuation start with `FORECAST_HANDOFF_2026-07-22.md`, then
`FORECAST_MODEL_PROPOSALS_2026-07-22.md` (models + July-7 result) and
`FORECAST_MODEL_VALIDATION_2026-07-22.md` (tests, the "why", and honest negative
results). Scripts: `forecast_model_category_pool.py`,
`forecast_backtest_category_pool.py`, `forecast_validate_category_pool.py`.
Headline: corporate-anchored + category-reconciled + activation beats the July
champion (SKU WAPE 1.05→0.96, coverage 0.67→0.76) but activation is
season-conditional and must be gated; treat as research until a multi-window
frozen corporate comparison exists.

Older conclusions are not active guidance. Recover them from Git history only
when a provenance question specifically requires them.
