# Forecast Current State

Authoritative as of 2026-08-19. This is the first document to read for forecast
work. It replaces the June reset diary, model-input lab notebook, intermediate
handoffs, and smoke-test champion narratives as active guidance. Git history
retains those records when provenance is needed.

## Current Decision

Do not reset the project again. Narrow the architecture:

1. Preserve the corporate weekly forecast as the current operational anchor,
   but score total-volume accuracy separately. The August 4-17 original vintage
   underforecast by 36.86%, and its actual weekly overlay underforecast by even
   more. Corporate retains commercial/promotion knowledge that the warehouse
   model does not yet reproduce, but it is not an unqualified total-volume
   champion.
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

The primary research challenger is now
`catpool_corporate_anchor_activation`. Across the July 7, July 21, and August 4
completed corporate-vintage reconstructions it has the lowest SKU WAPE in all
three windows (0.890, 0.981, and 0.939). This is strong multi-vintage
retrospective evidence, not a prospective production promotion. Corporate
remains the AX baseline until the same locked challenger is frozen before a
clean origin and survives that closeout.

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
  outputs, creation and upload of the weekly corporate forecast to AX, and the
  current SKU/category ledger.
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
- Distinguish an honestly frozen candidate from any method defined or generated
  after the origin, even when it uses only pre-origin rows. Late-origin and
  post-close diagnostics may explain a method but cannot win that prospective
  contest.
- Preserve daily totals exactly when redistributing them across SKUs. Use
  deterministic largest-remainder rounding, not independent SKU rounding.

### Weekly corporate forecast vintages

Corporate forecasts are uploaded from `ha-ingestion-pipeline` weekly and their
14-day windows can overlap. Preserve every uploaded version as a dated,
immutable forecast vintage; never let a later upload overwrite the source used
by an earlier closeout. When an overlay occurs, report both:

1. the original-vintage 14-day score, which measures the forecast frozen at
   the initial origin; and
2. the operational-vintage score, which uses the forecast actually in force on
   each date (for example, the first weekly upload through its overlay date,
   then the replacement weekly upload).

Also report the overlay's change in accuracy on its overlapping dates. An
overlay may improve operations' active forecast, but it cannot retroactively
replace the original frozen baseline or become a prospective challenger.

## Active August 18-31 Pack

The August 19 ingestion output contains one `ForecastStartDate`, August 18,
and 282,204 corporate units across 14 dates. The corporate source was reported
available August 17, but the local ingestion pipeline and candidate build ran
August 19 before the AX upload. The resulting four-arm pack is therefore
classified as a one-day-late, pre-AX, as-of reconstruction—not a clean
prospective contestant.

All DirectPick inputs end August 17 and all four corporate-anchored arms retain
the exact daily corporate totals. The pack preserves `corporate_raw` (8,434
positive SKUs), `corporate_total_recent_shape` (12,990),
`catpool_corporate_anchor` (12,915), and
`catpool_corporate_anchor_activation` (16,537). Inventory and inbound use the
latest eligible July 22 snapshot and are 26 days stale relative to the
pre-origin cutoff.

Read `FORECAST_FREEZE_2026-08-18_TO_2026-08-31.md` and the pack manifest under
`Output/ForecastAccuracy/forward_tests/2026-08-19_corporate_2026-08-18/`.
Close it after all dates through August 31 are complete. Separately, freeze the
next corporate vintage before its own start rather than waiting for this
horizon to finish.

### Retrospective three-vintage allocation result

The same four corporate-total arms were compared over the completed July 7,
July 21, and August 4 starts. Mean SKU WAPE is 1.344 for corporate raw, 1.093
for global recent shape, 1.095 for category reconciliation without activation,
and 0.937 for category reconciliation with activation. The activation arm wins
all three windows and raises mean sold-unit coverage from 47.56% to 74.62%, at
the cost of lower forecast-SKU use and slightly more zero-demand forecast
units. Category reconstruction status and the current-value crosswalk
limitation remain explicit; this selects the primary challenger but does not
retroactively make it prospective.

## Completed Evidence

### August 4-17, closed August 19

The August 3 corporate vintage produced 144,405 forecast units against 228,715
monitoring-scope DirectPick units (85,638 SKU/day rows and 14,085 SKUs), a
36.86% total underforecast. Its SKU WAPE was 119.37%, forecast-SKU use rate
85.87%, sold-unit coverage 54.04%, and zero-demand forecast-unit share 12.34%.

AX received an August 12 overlay for an August 11 start; AX modification times
show it arrived at 15:35-15:41 Eastern on August 12. Applying the overlay from
August 13, its first full operational day, improves WAPE to 105.25%, SKU use to
88.10%, coverage to 62.31%, and zero-demand units to 8.47%, while worsening
total bias to -38.05%. The overlay's scheduled August 11-17 comparison also
improves allocation but lowers planned volume. Neither series is a replacement
model candidate. The closeout reconciles exactly to monitoring and is recorded
in `FORECAST_CLOSEOUT_2026-08-04_TO_2026-08-17.md`.

No category-pool challenger was frozen before either August forecast start.
The later origin-safe reconstruction is retained for multi-vintage learning,
but it cannot win the August 4 prospective contest. Freeze the already
pre-registered challenger only at the next clean corporate origin.

### July 21-August 3, closed August 5

The July 21-August 3 forward closeout contains 187,644 monitoring-scope
DirectPick units across 77,522 SKU/day rows and reconciles within three units of
the 187,647-unit monitoring aggregate. It shows that the two prospectively
frozen corporate-volume contestants are a precision/coverage tradeoff:

| Candidate | Bias | SKU WAPE | SKU use rate | Sold-unit coverage | Zero-demand forecast units |
|---|---:|---:|---:|---:|---:|
| Corporate raw | -12.06% | 129.07% | 82.72% | 53.48% | 6.25% |
| Corporate total + recent shape | -12.06% | 115.45% | 79.21% | 59.90% | 15.47% |

Neither is promoted as an unqualified champion. The July 22
corporate-anchored category-pool method is a late-origin diagnostic, but its
98.09% WAPE, 84.40% SKU use, and 72.23% coverage motivate freezing that
architecture at the next clean origin. The independent category-pool method
has 91.44% WAPE and 96.73% coverage but underforecasts total demand by 19.60%
and remains a volume diagnostic.

Read `FORECAST_CLOSEOUT_2026-07-21_TO_2026-08-03.md` for the status-separated
scorecard and durable evidence paths.

### July 7-20, prior evidence

The prior completed comparison is July 7-20, 2026:

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

## July 21-August 3 Forward Pack and Closeout Status

The July 21 pack was frozen before the origin and evaluated on August 5:

- corporate raw: 165,008 units across 5,792 positive SKUs;
- corporate total plus 56-day recent statistical shape: 165,008 units across
  8,389 positive SKUs.

The same July 21 pack also preserves an unconstrained independent recent-shape
volume diagnostic (280,572.414 units across 19,013 positive SKUs). It is useful
for diagnosing the volume anchor, not a champion candidate.

No hindsight ML candidate was created for this origin. The existing model panel
ends on 2026-06-08, and the July 21 promotion workbook provides only a one-day
effective date.

Two category-pool artifacts were generated on July 22 for the same dates:
`catpool_activation` (150,869 units) and
`catpool_corporate_anchor_activation` (165,008 units). They use origin-safe
rows and are useful August 4 diagnostics, but their model definition and
artifacts were created after the July 21 origin. They are not frozen July 21
contestants and cannot win that prospective comparison.

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

### July 21-August 3 closeout (completed 2026-08-05)

Do not rebuild or modify either saved forecast Parquet. The scoreable artifacts
are preserved at Git commit `b0a252a`, and the completed closeout is under
`Output/ForecastAccuracy/handoff_eval/forward_2026-07-21_closeout/`. The
commands below are retained for reproduction only. The completed audit found all
14 Eastern days and required the read-only live-AX fallback because no canonical
SKU/day fact covered the window:

```powershell
uv run python scripts/python/forecast_actuals_source_audit.py `
  --start-date 2026-07-21 `
  --through-date 2026-08-03
```

If the audit shows no current portable monitoring-scope SKU/day fact, use the
read-only AX fallback below. It loads both saved packs in one comparison and
uses a tracked SQLite ledger for portable category scoring:

```powershell
uv run python scripts/python/forecast_window_compare.py `
  --start-date 2026-07-21 `
  --through-date 2026-08-03 `
  --daily-forecast Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/forward_daily_forecasts.parquet `
  --daily-forecast Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/category_pool_daily_forecasts.parquet `
  --ledger-db Output/ForecastAccuracy/handoff_eval/independent_hybrid_absolute_log_2026-07-07/ingestion_output/sku_ledger.db `
  --live-ax `
  --output-dir Output/ForecastAccuracy/handoff_eval/forward_2026-07-21_closeout
```

When the audit identifies a canonical saved actual, replace `--live-ax` with
`--actuals <path>`. For a repeated analysis, use the saved
`actual_sku_day.parquet`, scorecards, and metadata in the completed closeout
directory. Results remain separated into three status groups:

1. prospective contestants: `corporate_raw`,
   `corporate_total_recent_shape`;
2. origin-frozen volume diagnostic: `independent_recent_shape`;
3. July 22 late-origin diagnostics: `catpool_activation`,
   `catpool_corporate_anchor_activation`.

## Open Work, In Order

1. Freeze the locked category-pool architecture before the next clean corporate
   origin. The August 18 pack was built one day late and is an as-of operational
   shadow, not the needed prospective confirmation. Follow
   `FORECAST_NEXT_PROSPECTIVE_TEST_2026-08-05.md` immediately when the source
   arrives; do not wait for the prior 14-day horizon to close.
2. Integrate the tracked canonical
   `product_attributes/sku_category_crosswalk.parquet` into the closeout scorer;
   extraction and provenance mirroring are complete, but the scorer currently
   accepts SQLite ledgers only.
3. Add an explicit occurrence/selection threshold and precision/coverage
   scorecard.
4. Add the carton-use simulator.
5. Rebuild a future-safe model panel before testing ML occurrence or residual
   challengers.
6. Use BigQuery inventory history only after its schema and as-of coverage are
   confirmed; monitoring remains the current operational source.

## Reading Order

1. This file.
2. `FORECAST_FREEZE_2026-08-18_TO_2026-08-31.md` for the active pack and
   three-vintage challenger comparison.
3. `FORECAST_CLOSEOUT_2026-08-04_TO_2026-08-17.md` for the latest closeout.
4. `FORECAST_CLOSEOUT_2026-07-21_TO_2026-08-03.md` for prior evidence.
5. `FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md` for earlier evidence.
6. `FORECAST_DATA_LANDSCAPE_2026-07-20.md` for detailed ownership and data
   contracts.
7. `FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md` only when moving, rebuilding, or
   committing artifacts.

### Active research candidate (2026-07-22, not yet champion)

A runnable two-stage category-pool candidate now implements the direction below.
For continuation start with `FORECAST_HANDOFF_2026-07-22.md`, then
`FORECAST_MODEL_PROPOSALS_2026-07-22.md` (models + July-7 result) and
`FORECAST_MODEL_VALIDATION_2026-07-22.md` (tests, the "why", and honest negative
results). Scripts: `forecast_model_category_pool.py`,
`forecast_backtest_category_pool.py`, `forecast_validate_category_pool.py`.
Headline: on the rebuilt July 7 diagnostic, corporate-anchored +
category-reconciled + gated activation improves SKU WAPE
`1.0513 → 0.8896` and sold-unit coverage `0.6686 → 0.7731`. The tradeoff is a
lower forecast-SKU use rate (`0.8601 → 0.8296`) and a slightly higher share of
forecast units on zero-demand SKUs (`0.0883 → 0.0912`). Activation remains
season-conditional; treat the method as research until it has a genuinely
prospective, multi-window corporate comparison.

Older conclusions are not active guidance. Recover them from Git history only
when a provenance question specifically requires them.
