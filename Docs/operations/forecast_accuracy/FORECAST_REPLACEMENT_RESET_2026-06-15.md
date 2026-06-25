# Forecast Replacement Reset - 2026-06-15

This reset re-anchors the independent forecast work to the operational goal:
produce a `Product Info for BRG`-like planning input that can feed the existing
ingestion pipeline and create the AX Forward Demand CSV.

The goal is not a generic sales forecast leaderboard.  The goal is a forecast
that improves the replenishment and SlotTier decisions currently driven by the
weekly corporate workbook.

## Operational Contract

Current weekly source:

```text
Source/Product Info for BRG*.xlsx
```

Current AX-facing output:

```text
Output/Ingestion/FwdDemandCSV_YYYY-MM-DD.csv
```

The ingestion pipeline expects two forecast shapes from the workbook:

- `SKU Level 14 Day Forecast`: FD1-FD14 daily demand for advance
  replenishment.
- `Product Forecast Tool by Week`: weekly/13-week demand for velocity,
  SlotTier, RequiredSlots, and zone guidance.

An independent forecast candidate must be judged on whether it can replace or
challenge those workbook forecast inputs while preserving the 36-column AX CSV
contract.

## Reset Decisions

### 1. Target

Forecast replenishment-relevant forward pick demand.

Included:

- completed DirectPick demand from replenished pickface profiles;
- open/reserved sales demand only when it is relevant to replenished pickface
  execution;
- Product Info-style FD1-FD14 and weekly demand outputs.

Excluded from the model-facing target:

- `W001`, because those locations are not replenished by this process;
- Bulk/reserve/process-area reservation quantities, because they can represent
  carton or replenishment movement quantities rather than consumer demand;
- replenishment work itself;
- generic sales volume that cannot create AX replenishment demand.

### 2. Future SKU Universe

The model must decide which SKU/day and SKU/week rows belong in a future
forecast file using only information known before the forecast starts.

Candidate universe inputs should be explicitly separated:

- current or recent Product Info workbook SKUs;
- Product Attributes and AX hierarchy-resolved SKUs;
- recent fulfilled DirectPick demand;
- future promotion/PDL SKU signals;
- inbound/Cubiscan coverage signals already used by ingestion Guardrail 3;
- valid reservation signal:
  - blank-location reservations as unresolved open-order demand proxy;
  - pickface located reservations as replenishment-relevant allocation signal.

Do not use inventory-on-hand, Bulk reservations, W001 reservations, or warehouse
movement buckets as direct demand-universe expanders unless a specific guardrail
justifies them.

### 3. Evaluation

A candidate is not upload-ready until it passes a true future replacement
rehearsal:

1. Freeze an `as_of_date`.
2. Build only forecast rows knowable as of that date.
3. Produce a BRG-like forecast artifact:
   - FD1-FD14 daily demand;
   - weekly/13-week demand or the fields needed to derive it;
   - product hierarchy/SlotTier fields needed by ingestion.
4. Run the candidate through the ingestion contract or an equivalent AX-shaped
   validation harness.
5. Score:
   - WAPE and bias on replenishment-relevant actual demand;
   - actual-unit coverage;
   - over-generated zero-demand rows;
   - SlotTier/velocity churn versus corporate;
   - RequiredSlots and zone-capacity reasonableness.

Historical sparse-panel WAPE can remain a diagnostic, but it is no longer the
decision metric.

## Artifact Policy

Forecast facts that remain useful:

```text
Output/ForecastAccuracy/history/
Output/ForecastAccuracy/promotions/
Output/ForecastAccuracy/sales_orders/
Output/ForecastAccuracy/inventory/
Output/ForecastAccuracy/inbound/
Output/ForecastAccuracy/warehouse_supply/
Output/ForecastAccuracy/reservations/
```

Obsolete under this reset:

```text
Output/ForecastAccuracy/model/
```

That folder held sparse-panel experiments, challenger scoreboards, future-row
rehearsals, activity-gate tests, and the old monolithic/panel-part model files.
Those artifacts answered the wrong final question and should be regenerated only
under the reset contract above.

Model scripts should be kept only when they support the new BRG-like contract or
reusable fact extraction.  One-off challenger/activity-gate scripts from the old
path should not remain as active project tools.

Cleanup performed on 2026-06-15:

- deleted generated contents of `Output/ForecastAccuracy/model/`
  (`923.70 MB`, `397` files);
- removed the untracked old-approach scripts:
  - `scripts/python/forecast_model_activity_gate_rehearsal.py`
  - `scripts/python/forecast_model_booster_candidate_output.py`
  - `scripts/python/forecast_model_candidate_scoreboard.py`
  - `scripts/python/forecast_model_future_candidate_output.py`
  - `scripts/python/forecast_model_rolling_origin_boosters.py`
- removed the obsolete untracked candidate summary:
  - `Docs/operations/forecast_accuracy/FORECAST_MODEL_CANDIDATES_2026-06-14.md`

Reusable extraction and current-history scripts were retained.  The old tracked
model panel scripts were also retained for now because they may contain reusable
feature-building code, but they should be considered legacy until they are
adapted to this reset contract.

Generated replacement-contract candidate packages are local by default:

```text
Output/ForecastAccuracy/replacement_contract/
```

This folder may contain cloned corporate workbooks, AX-shaped CSV outputs,
contract Parquet files, samples, and logs.  Those artifacts should not be
committed unless a specific package is intentionally promoted as a lightweight
review artifact.

## Contract Proof

Completed on 2026-06-15:

- added `scripts/python/forecast_replacement_contract.py`, which defines the
  BRG-like candidate package and validates the AX-facing ingestion output;
- extended `scripts/python/ingestion_pipeline.py` with `--source-file` and
  `--output-dir` so a candidate workbook can be round-tripped without
  overwriting normal ingestion outputs;
- built a corporate-workbook clone candidate from
  `Source/Product Info for BRG_2026-06-01.xlsx`;
- round-tripped that clone through ingestion and produced a passing
  `FwdDemandCSV_2026-06-15.csv` under the candidate output folder.

Passing clone summary:

```text
Candidate: corporate_clone_2026-06-15_v2
Output root: Output/ForecastAccuracy/replacement_contract/corporate_clone_2026-06-15_v2
Forward Demand rows: 33,963
Forward Demand columns: 36
Missing/extra Forward Demand columns: 0 / 0
Duplicate Item/Color/Size keys: 0
FD1-FD14 units: 161,188
Required slot tiers: 206
Required slots total: 18,995.9
```

This proves the replacement harness can accept a BRG-like candidate workbook,
run the same ingestion code path, and validate the AX CSV contract before any
model candidate is trained or compared.

## No-ML Baseline Proof

Completed on 2026-06-15:

- extended `scripts/python/forecast_replacement_contract.py` with
  `--candidate-type no_ml_baseline`;
- generated a minimal BRG-compatible workbook using the corporate workbook for
  product attributes, load data, and on-hand layout inputs;
- generated forecast rows from:
  - recent fulfilled DirectPick demand as the base rate;
  - PDL SKU/day promotion rows as a simple uplift and last-week-sales floor;
  - valid reservations as a near-term demand floor;
  - inbound as a coverage/universe signal only, not as synthetic demand;
- round-tripped the candidate workbook through the same ingestion path and
  validated the 36-column AX Forward Demand CSV contract.

Passing no-ML baseline summary:

```text
Candidate: no_ml_baseline_2026-06-15_v2
Forecast start date: 2026-06-16
Output root: Output/ForecastAccuracy/replacement_contract/no_ml_baseline_2026-06-15_v2
Forward Demand rows: 36,908
Forward Demand columns: 36
Missing/extra Forward Demand columns: 0 / 0
Duplicate Item/Color/Size keys: 0
FD1-FD14 units after ingestion filters: 446,554
Required slot tiers: 226
Required slots total: 20,237.2
```

Signal attribution:

```text
DirectPick lookback: 2026-04-21 through 2026-06-15
DirectPick lookback units: 1,030,297 across 16,220 SKUs
Future PDL promotion SKUs: 17,789
Inbound coverage SKUs: 16,751
Valid reservation SKUs: 7,982
Valid reservation units: 54,632
  blank-location open-order proxy: 31,689
  pickface allocated reservations: 22,943
Excluded reservation diagnostics:
  W001: 11
  reserve/bulk: 3,483
  operational located: 6,213
```

The higher FD1-FD14 total versus the corporate clone is mostly driven by recent
DirectPick volume and future PDL promotion rows.  Inbound is intentionally used
for coverage only.  This candidate is now the first deterministic replacement
baseline; it is not yet evidence that the no-ML method is better than corporate.

The result package is intentionally retained locally:

```text
Output/ForecastAccuracy/replacement_contract/no_ml_baseline_2026-06-15_v2
```

## Seasonal No-ML Baseline Proof

Completed on 2026-06-15:

- extended `scripts/python/forecast_replacement_contract.py` with
  `--candidate-type seasonal_no_ml_baseline`;
- used the same recent DirectPick, PDL, inbound, and reservation signals as the
  recent-only no-ML baseline;
- added prior-year same-season DirectPick history:
  - historical years: 2025, 2024, 2023;
  - same-calendar-date window: +/- 7 days;
  - blend when both recent and seasonal history exist: 65% recent / 35%
    seasonal;
- kept seasonal history out of the SKU-universe expansion.  Seasonal history can
  shape SKUs that are already present from Product Info, recent demand, future
  PDL, inbound, or valid reservations, but it cannot resurrect old SKUs by
  itself.

Passing seasonal no-ML baseline summary:

```text
Candidate: seasonal_no_ml_baseline_2026-06-15_v2
Forecast start date: 2026-06-16
Output root: Output/ForecastAccuracy/replacement_contract/seasonal_no_ml_baseline_2026-06-15_v2
Forward Demand rows: 43,481
Forward Demand columns: 36
Missing/extra Forward Demand columns: 0 / 0
Duplicate Item/Color/Size keys: 0
FD1-FD14 units after ingestion filters: 471,847
Required slot tiers: 232
Required slots total: 26,091.1
```

Signal attribution:

```text
Candidate universe SKUs: 45,687
Product Info universe SKUs in candidate: 32,787
DirectPick lookback units: 1,030,297 across 16,220 SKUs
Seasonal-history SKUs in candidate: 12,020
Seasonal 14-day contribution before rounding/filtering: 48,494
Seasonal first-13-week contribution before rounding/filtering: 449,540.9
Future PDL promotion SKUs: 17,789
Inbound coverage SKUs: 16,751
Valid reservation SKUs: 7,982
Valid reservation units: 54,632
```

Outstanding:

```text
MissingProductAttributes rows: 79
Source: Weekly + 14-Day
Demand on those rows: 0 FD1-FD14 units and 0 13-week units
Missing fields: Division, Department, Class, KeyCategoryView, SizeGroup, GoLiveDate
```

Those 79 rows do not drive forecast volume, but they should be resolved or
excluded before any candidate is considered upload-ready.

## Replacement Backtest Gate

Completed on 2026-06-15:

- added `scripts/python/forecast_replacement_backtest.py`;
- scored historical 14-day replacement windows against fulfilled DirectPick
  actuals;
- used the recovered corporate Forward Demand snapshots as the corporate
  baseline;
- compared deterministic no-ML variants before running any more ML:
  - recent no-ML with the original PDL last-week-sales floor;
  - seasonal no-ML with the original PDL last-week-sales floor;
  - recent no-ML without the PDL unit floor;
  - seasonal no-ML without the PDL unit floor.

The original no-ML variants were not competitive because the PDL
last-week-sales floor badly over-generated demand.  Removing that floor created
a useful transparent baseline.

Latest 26 complete windows:

```text
Window range: 2025-11-25 through 2026-05-19
Actual units: 7,334,496
Threads requested: 8
Output: Output/ForecastAccuracy/replacement_backtests/
```

Summary:

```text
Candidate                         WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
recent_no_ml_no_promo_floor       111.4%  +39.4%  96.6%                2.5%
corporate                         113.3%   +5.5%  43.9%               46.3%
seasonal_no_ml_no_promo_floor     127.5%  +56.0%  96.7%                2.5%
recent_no_ml                      408.8% +375.8%  97.0%                2.2%
seasonal_no_ml                    425.0% +392.2%  97.0%                2.2%
```

Window wins by lowest WAPE:

```text
recent_no_ml_no_promo_floor: 13
corporate: 12
recent_no_ml: 1
```

Interpretation:

- The calibrated recent no-ML baseline is a real benchmark because it slightly
  beats corporate WAPE across the 26-window aggregate and wins half the
  windows.
- It is not upload-ready as-is because it over-forecasts by `39.4%`.
- Corporate is better calibrated on total units but misses too many units/SKUs
  (`46.3%` zero-forecast sold-unit rate).
- Seasonal no-ML currently adds too much volume; same-season history should be
  treated as an ML feature or a calibrated adjustment, not a direct additive
  baseline.
- The old scikit-learn champion results around `48%` WAPE remain materially
  better than both corporate and deterministic baselines, but they must be
  re-tested under this replacement scoreboard before being treated as the
  production candidate.

Next ML gate:

```text
Train/evaluate the independent scikit-learn champion under the replacement
backtest harness, excluding corporate forecast features, and compare it against:
1. corporate
2. recent_no_ml_no_promo_floor
3. seasonal_no_ml_no_promo_floor
```

Do not add CatBoost/XGBoost/LightGBM or tune broad model grids until the current
scikit-learn champion is fairly scored against this replacement backtest.

## Scikit-Learn Guardrail Gate

Completed on 2026-06-15:

- rebuilt the local model panel from cached forecast facts using 8 workers;
- reran the old six-window scikit-learn champion health check:
  - model: raw `hgb_absolute_log`;
  - corporate forecast features excluded;
  - product identity/category-size/item-color features included;
  - result: `48.2%` mean WAPE versus corporate `158.7%`.
- added `scripts/python/forecast_replacement_ml_backtest.py` to test the same
  model under the reset contract:
  - train only on rows before each forecast start;
  - generate SKU/day forecast rows from known-before-start inputs;
  - freeze demand/inventory/supply lag features at latest prestart values;
  - allow future PDL promotion features because those are planned inputs;
  - score the forecast against the same 26 historical 14-day DirectPick windows
    used by the no-ML replacement gate.

The old `48.2%` sparse-panel WAPE is still a useful model-health signal, but it
is not the replacement decision metric because sparse holdout rows can be
created by actual future demand.

Latest ML guardrail run:

```text
Output: Output/ForecastAccuracy/replacement_ml_backtests/hgb_absolute_log_26_windows/
Window range: 2025-11-25 through 2026-05-19
Actual units: 7,334,496
Threads requested: 8
Model: hgb_absolute_log
Max train rows per window: 500,000
Max iterations: 180
```

Top aggregate candidates across corporate, no-ML, and ML:

```text
Candidate                                  WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
ML raw, min 15 units per SKU/14 days        85.4%   +1.5%  66.3%               28.3%
ML raw, min 20 units per SKU/14 days        85.9%   -5.7%  59.7%               34.6%
recent_no_ml_no_promo_floor                111.4%  +39.4%  96.6%                2.5%
corporate                                  113.3%   +5.5%  43.9%               46.3%
seasonal_no_ml_no_promo_floor              127.5%  +56.0%  96.7%                2.5%
```

Window wins by lowest WAPE across the combined replacement scoreboard:

```text
recent_no_ml_no_promo_floor: 10
corporate: 8
ML raw, min 15 units: 7
ML raw, min 20 units: 1
```

Interpretation:

- The scikit-learn champion is not a mirage: when forced through a future-safe
  row generator, it beats corporate and the no-ML baseline on aggregate WAPE.
- The plain all-positive ML output is not usable because it spreads small
  forecasts across nearly the whole known SKU universe and over-generates
  zero-demand rows.
- A simple SKU-level threshold fixes much of that over-generation and gives
  almost neutral aggregate bias, but it misses too much sold demand to be an
  upload-ready champion.
- The no-ML recent baseline remains important because it wins the most recent
  spring windows and has much better sold-unit coverage.
- The next useful ML work is not CatBoost yet.  It is a better occurrence/rank
  layer or threshold policy that chooses which SKUs deserve forecast rows,
  while keeping the regression model for volume.

## Hybrid Coverage Gate

Completed on 2026-06-15:

- extended `scripts/python/forecast_replacement_ml_backtest.py` to test a
  small recent-history fallback for SKUs below the ML selection threshold;
- kept the same future-safe 26-window setup:
  - no future actual rows used to create the forecast universe;
  - ML volume from raw `hgb_absolute_log`;
  - corporate features excluded from model training;
  - fallback from recent no-ML without the PDL last-week-sales floor.

The tested hybrid is:

```text
High-confidence ML rows:
  raw hgb_absolute_log SKU 14-day forecast >= 20 units

Fallback rows:
  for SKUs not selected by ML,
  add a fraction of recent_no_ml_no_promo_floor demand
```

Best fine-grid result:

```text
Output: Output/ForecastAccuracy/replacement_ml_backtests/hgb_absolute_log_hybrid_weight_grid_26_windows/
Combined summary: Output/ForecastAccuracy/replacement_ml_backtests/combined_replacement_candidate_summary.csv
Combined winners: Output/ForecastAccuracy/replacement_ml_backtests/combined_replacement_window_winners.csv
Candidate: hybrid_ml_hgb_absolute_log_raw_min_20p0_units_recent_w0p1
```

Updated top aggregate candidates:

```text
Candidate                                  WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
ML raw min 20 + 10% recent fallback         84.3%   +1.3%  96.6%                2.5%
ML raw min 20 + 5% recent fallback          84.4%   -2.2%  96.6%                2.5%
ML raw min 20 + 15% recent fallback         84.7%   +4.7%  96.6%                2.5%
ML raw, min 15 units per SKU/14 days        85.4%   +1.5%  66.3%               28.3%
recent_no_ml_no_promo_floor                111.4%  +39.4%  96.6%                2.5%
corporate                                  113.3%   +5.5%  43.9%               46.3%
```

Combined window wins by lowest WAPE:

```text
corporate: 8
recent_no_ml_no_promo_floor: 7
hybrid ML min20 + 30% recent fallback: 4
hybrid ML min20 + 10% recent fallback: 3
ML raw min15: 3
hybrid ML min20 + 5% recent fallback: 1
```

Interpretation:

- The hybrid is the first candidate that beats corporate on aggregate WAPE
  while keeping no-ML-like sold-unit coverage.
- The 10% fallback is the best aggregate balance so far.  It improves WAPE
  versus pure ML and avoids the large zero-forecast sold-unit problem.
- The recent no-ML baseline still wins the latest May windows.  That suggests
  the final candidate may need a regime/calibration switch instead of one
  static formula.
- This is now a credible challenger to corporate, but it still needs an
  operational rehearsal as a BRG-like workbook and ingestion output before it
  should be called upload-ready.

## Policy Backtest Gate

Completed on 2026-06-15:

- added `scripts/python/forecast_replacement_policy_backtest.py`;
- consumed existing candidate window-score CSVs rather than rerunning models;
- compared fixed candidates against simple prior-window selection policies:
  - rolling 4-window WAPE selector;
  - rolling 8-window WAPE selector;
  - rolling 12-window WAPE selector;
  - expanding prior-window WAPE selector after a 4-window warmup.

Outputs:

```text
Output/ForecastAccuracy/replacement_ml_backtests/candidate_policy_backtest_summary.csv
Output/ForecastAccuracy/replacement_ml_backtests/candidate_policy_backtest_window_choices.csv
```

Policy summary:

```text
Policy                                    WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
rolling 8-window prior WAPE selector       82.4%   +1.6%  88.7%                6.8%
rolling 4-window prior WAPE selector       82.8%   +5.4%  86.7%                8.8%
fixed hybrid ML min20 + 10% recent         84.3%   +1.3%  96.6%                2.5%
fixed hybrid ML min20 + 5% recent          84.4%   -2.2%  96.6%                2.5%
recent_no_ml_no_promo_floor               111.4%  +39.4%  96.6%                2.5%
corporate                                 113.3%   +5.5%  43.9%               46.3%
```

Interpretation:

- Prior-window policy selection can improve WAPE further, but it gives up
  coverage because it sometimes selects corporate during periods where
  corporate looked good recently.
- For an AX replenishment input, the fixed hybrid is currently the safer
  challenger because it preserves coverage while still beating corporate by
  about 29 WAPE points.
- The policy selector is useful as a benchmark, but not yet the preferred
  production rule.

## Hybrid BRG Package Rehearsal

Completed on 2026-06-15:

- added `scripts/python/forecast_replacement_hybrid_candidate.py`;
- generated BRG-like candidate workbooks from the conservative hybrid ML
  forecast;
- round-tripped each workbook through the same ingestion path used by the
  corporate clone and no-ML packages;
- saved the candidate comparison table:

```text
Output/ForecastAccuracy/replacement_contract/candidate_roundtrip_comparison_2026-06-15.csv
```

Candidate policy:

```text
Model: raw hgb_absolute_log
Corporate forecast features: excluded
ML SKU threshold: 20 units across FD1-FD14
Fallback source: recent no-ML demand, no PDL last-week-sales floor
Weekly tail: conservative continuation from hybrid daily forecast and recent demand
Weekly tail scale: 50%
```

Round-trip comparison:

```text
Candidate                                  FD rows  FD1-FD14 units  Required slots  Status
corporate_clone_2026-06-15_v2              33,963         161,188        18,995.9   pass
no_ml_baseline_2026-06-15_v2               36,908         446,554        20,237.2   pass
seasonal_no_ml_baseline_2026-06-15_v2      43,481         471,847        26,091.1   pass
hybrid_ml_conservative_2026-06-15_v1       18,620         148,392        11,935.6   pass
hybrid_ml_more_conservative_2026-06-15_v1  18,620         140,360         9,076.7   pass
```

Interpretation:

- Both hybrid workbooks pass the 36-column AX Forward Demand contract with no
  duplicate Item/Color/Size keys.
- The hybrid packages are intentionally conservative and align with the
  operational preference that under-forecasting is safer than over-forecasting
  while location cleanup is still manual/incomplete.
- The `5%` fallback package is the safer review candidate if the business wants
  to avoid adding too much forward replenishment pressure.
- The `10%` fallback package is the balanced review candidate from the
  backtest: nearly neutral bias with the best fixed-candidate WAPE.
- RequiredSlots are much lower than the corporate clone, especially in the
  `5%` fallback package.  That is directionally good for avoiding location
  hijacking, but it needs an Operations sanity review by category/zone before
  any upload discussion.
- Current reservations are not yet added as a direct hybrid floor.  They should
  be reviewed separately because they may help high-volume periods, but they
  could also recreate the over-forecast/replenishment-pressure problem if used
  too aggressively.

Review files:

```text
Output/ForecastAccuracy/replacement_contract/hybrid_vs_corporate_forward_demand_by_category_2026-06-15.csv
Output/ForecastAccuracy/replacement_contract/hybrid_vs_corporate_required_slots_by_segment_2026-06-15.csv
```

Early category scan:

- largest hybrid reductions versus corporate are in groups such as kids
  underwear bottoms, girls shorts/pants/dresses, boys pants, and some collab
  sleepwear;
- largest hybrid increases versus corporate are concentrated in more
  seasonally plausible summer/sleep/swim groups such as swimwear, SS tops,
  footless sleepers, and KU short johns;
- this direction is plausible for mid-June and the upcoming July sale, but the
  review should focus on whether the hybrid is cutting any still-active
  replenishment needs too aggressively.

## Next Build Sequence

1. [x] Create a `forecast_replacement_contract` script or module that defines
   the required candidate output columns before any model is trained.
2. [x] Build a baseline BRG-clone candidate from the latest corporate workbook
   so the harness proves it can round-trip through ingestion.
3. [x] Build a no-ML baseline from recent DirectPick, promotions, inbound, and
   valid reservation signals.
4. [x] Build a seasonal no-ML baseline using multi-year same-season history
   without letting old seasonal-only SKUs expand the future universe.
5. [x] Build the replacement backtest gate and calibrated no-ML benchmark.
6. [x] Train/evaluate the independent scikit-learn champion on the reset target
   and score it against the same future replacement harness.
7. Build the next occurrence/ranking guardrail:
   - [x] compare static thresholds against a recent-history fallback;
   - [x] compare the fixed hybrid against a threshold/fallback selected from prior
     calibration windows;
   - evaluate a two-stage occurrence model only if it improves recent-window
     coverage/WAPE without creating a broad model grid;
   - keep corporate and `recent_no_ml_no_promo_floor` as the two mandatory
     benchmarks.
8. Generate a BRG-like candidate package from the current best hybrid and run
   it through the ingestion harness before any production handoff discussion.
   - [x] generated `hybrid_ml_conservative_2026-06-15_v1`
   - [x] generated `hybrid_ml_more_conservative_2026-06-15_v1`
9. Review hybrid package slotting impact by category/zone and compare the
   SKUs/slots it removes versus the corporate clone before treating it as an
   upload candidate.

## Recent Shadow Window Check

Completed on 2026-06-15:

- added `scripts/python/forecast_replacement_shadow_window.py`;
- scored arbitrary recent forecast windows at SKU/day grain;
- used this to answer the Operations question: "If we had produced this
  forecast for the latest available period, how close would it have been?"

Data availability:

```text
Latest fulfilled DirectPick actual date available: 2026-06-08
Exact "past two weeks" through 2026-06-15 is not scoreable yet.
Latest complete two-week actual window: 2026-05-26 through 2026-06-08
```

Shadow result, latest complete two-week window:

```text
Window: 2026-05-26 through 2026-06-08
Score grain: SKU/day

Candidate                         WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
recent_no_ml_no_promo_floor       102.7%   +8.9%  98.0%                2.0%
hybrid ML min20 + 5% recent       171.5% +125.7%  84.7%               15.3%
hybrid ML min20 + 10% recent      172.2% +127.2%  85.3%               14.7%
```

At SKU-total grain for the same window:

```text
Candidate                         WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
recent_no_ml_no_promo_floor        54.4%   +8.9%  98.0%                2.0%
hybrid ML min20 + 10% recent      156.8% +127.2%  94.2%                5.8%
hybrid ML min20 + 5% recent       157.2% +125.7%  89.6%               10.4%
```

Corporate-comparable shadow window:

```text
Window: 2026-05-27 through 2026-06-08
Reason: exact corporate snapshot starts 2026-05-27, actuals complete through 2026-06-08
Score grain: SKU/day

Candidate                         WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
recent_no_ml_no_promo_floor       121.6%  +34.1%  97.8%                2.2%
corporate_exact_snapshot          122.5%  -20.5%  56.3%               43.7%
hybrid ML min20 + 5% recent       226.0% +187.1%  85.7%               14.3%
hybrid ML min20 + 10% recent      226.9% +189.0%  86.3%               13.7%
```

Interpretation:

- The latest shadow period is a warning against treating the fixed hybrid as
  production-ready.
- The hybrid improved aggregate 26-window WAPE, but it badly over-forecasted
  this most recent period.
- Recent no-ML is the best latest-period candidate, even though it is too high
  on the broader 26-window aggregate.
- Corporate was close to no-ML on daily WAPE for the 13-day comparable window,
  but only by under-forecasting and missing many sold units.
- Next model work should add a recent-volume/calibration brake or regime switch:
  if the last 1-2 weeks show the ML hybrid over-running actual demand, fall back
  toward the conservative recent-history forecast.

## Volume-Brake Hybrid Test

Completed on 2026-06-15:

- extended `scripts/python/forecast_replacement_ml_backtest.py` with
  `--hybrid-recent-volume-caps`;
- extended `scripts/python/forecast_replacement_shadow_window.py` with
  `--recent-volume-caps`;
- tested capped hybrid candidates over the same 26 historical replacement
  windows and the latest available shadow windows.

The volume brake caps total hybrid forecast units at a multiple of the recent
no-ML forecast total for the same 14-day window.  It does not change the SKU
selection directly; it scales selected hybrid rows down when ML is trying to
run too hot.

26-window aggregate output:

```text
Output/ForecastAccuracy/replacement_ml_backtests/hgb_absolute_log_hybrid_volume_brake_26_windows/
Output/ForecastAccuracy/replacement_ml_backtests/combined_replacement_candidate_summary_with_volume_brake.csv
```

Top aggregate candidates:

```text
Candidate                                      WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
Hybrid 10% fallback, cap at 0.85x recent       80.6%   -6.9%  96.6%                2.5%
Hybrid 5% fallback, cap at 0.85x recent        80.8%   -9.7%  96.6%                2.5%
Hybrid 10% fallback, cap at 1.00x recent       82.1%   -2.0%  96.6%                2.5%
Hybrid 5% fallback, cap at 1.00x recent        82.4%   -5.1%  96.6%                2.5%
Fixed hybrid 10% fallback, uncapped            84.3%   +1.3%  96.6%                2.5%
recent_no_ml_no_promo_floor                   111.4%  +39.4%  96.6%                2.5%
corporate                                     113.3%   +5.5%  43.9%               46.3%
```

Recent shadow-window output:

```text
Output/ForecastAccuracy/replacement_shadow/latest_shadow_volume_brake_summary.csv
```

Latest complete two-week window:

```text
Window: 2026-05-26 through 2026-06-08
Score grain: SKU/day

Candidate                                      WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
Hybrid 5% fallback, cap at 0.85x recent        93.2%   -7.4%  84.7%               15.3%
Hybrid 10% fallback, cap at 0.85x recent       93.4%   -7.4%  85.3%               14.7%
recent_no_ml_no_promo_floor                   102.7%   +8.9%  98.0%                2.0%
Uncapped hybrid 5% fallback                   171.5% +125.7%  84.7%               15.3%
Uncapped hybrid 10% fallback                  172.2% +127.2%  85.3%               14.7%
```

Corporate-comparable recent window:

```text
Window: 2026-05-27 through 2026-06-08
Reason: exact corporate snapshot starts 2026-05-27, actuals complete through 2026-06-08
Score grain: SKU/day

Candidate                                      WAPE    Bias    Sold-unit coverage   Zero-forecast sold units
Hybrid 5% fallback, cap at 0.85x recent       104.8%  +14.0%  85.7%               14.3%
Hybrid 10% fallback, cap at 0.85x recent      104.8%  +14.0%  86.3%               13.7%
recent_no_ml_no_promo_floor                   121.6%  +34.1%  97.8%                2.2%
corporate_exact_snapshot                      122.5%  -20.5%  56.3%               43.7%
Uncapped hybrid 5% fallback                   226.0% +187.1%  85.7%               14.3%
Uncapped hybrid 10% fallback                  226.9% +189.0%  86.3%               13.7%
```

Interpretation:

- The volume brake directly addresses the latest-period failure mode.
- A cap at `0.85x` recent no-ML volume is the best current family: it improves
  26-window WAPE, aligns with the Operations preference to avoid over-filling
  locations, and prevents the late-May hybrid volume blow-up.
- The tradeoff is coverage.  Capped hybrid beats recent no-ML on WAPE in the
  latest shadow windows, but recent no-ML covers more sold units.  That is the
  operational decision to review next: lower WAPE and lower replenishment
  pressure versus fewer missed SKU/day units.
- The capped hybrid should be the next BRG package candidate, not the uncapped
  hybrid.

Until those steps exist, no model should be described as a replacement candidate.

## Cold-Start Diagnosis — 2026-06-18

Investigated why the cold-start ML run (`forecast_replacement_ml_cold_start.py`)
reports ~138–254% WAPE versus ~80.6% for the main hybrid.

Root cause: three-layer problem.

**Layer 1 — Temporal population mismatch.**
The cold-start backtest targets `UnknownGoLive` SKUs identified in the *current*
(June 2026) snapshot.  In 24 of 26 historical windows those SKUs did not exist
yet, so actuals = 0 and WAPE is undefined for those windows.  The alarming
headline WAPE is derived from only 2 valid windows.

**Layer 2 — True cold-start failure in those 2 windows.**
In the 2 windows where the new SKUs actually sold:

```text
Window       Sold     ML Forecast    WAPE    Coverage
2026-05-27  11,183     36,270       410%      7.2%
2026-06-02  86,075      3,439        98%      4.6%
```

The model guesses the wrong direction window-to-window — massively
over-forecasting in one, massively under-forecasting the other.  Coverage of
4–7% means the model predicted 0 for 93–96% of the units that actually sold.
Root cause: `UnknownGoLive` SKUs have no demand history → all lag/rolling
features are zero → model predicts near-zero → coverage collapses.  Attribute
features (gender, material, season) do not carry enough demand-magnitude signal.

**Layer 3 — Small impact.**
Cold-start SKUs account for only **1.3%** of total demand (97K of 7.3M sold
units across 26 windows).  The main hybrid already handles 98.7% of demand at
80.6% WAPE.  A like-item or category-median prior for new items is the correct
fix, but it is not on the critical path.

**Decision:** deprioritize cold-start ML.  Use `recent_no_ml_no_promo_floor`
as the cold-start fallback for now (no separate model needed).

## July Sale Forward Shadow — 2026-06-18

Added `--allow-partial-actuals` flag to `forecast_replacement_shadow_window.py`.

**Motivation.** The script previously raised a hard error if the requested
window extended beyond available actuals.  That blocked forward shadow tests —
the primary value of the shadow harness.  The new flag allows a frozen forecast
to be generated now and scored against partial actuals, with the full score
available after the window closes.  Behavior without the flag is unchanged (hard
error).  The metadata file records `"partial_actuals": true` when the flag is
used.

Froze the June 18 – July 1 forward shadow:

```text
Script:  scripts/python/forecast_replacement_shadow_window.py
Window:  2026-06-18 through 2026-07-01  (14 days)
Source:  Product Info for BRG_2026-06-15.xlsx
Output:  Output/ForecastAccuracy/replacement_shadow/shadow_2026-06-18_2026-07-01/
Status:  partial — actuals available through 2026-06-18 only
```

Frozen forecast volumes:

```text
Candidate                                      SKUs  Forecast units (14-day)
Hybrid 5% fallback, cap at 0.85x recent       8,440          142,282
Hybrid 10% fallback, cap at 0.85x recent     10,952          150,617
recent_no_ml_no_promo_floor                  16,336          277,260
```

**Critical early signal.**  The OPS/IMF plan expects ~456K units for June 18 –
July 1, derived from the planner daily totals.  The ML hybrid is forecasting
only 142K (31% of plan).  All volume-cap variants are identical — the ML is
already under the 0.85x recent floor, meaning the brake is not helping in a
sale ramp.  This is the regime-instability concern: the 56-day lookback window
is pre-sale, so the model has not yet seen sale-level demand and is
under-forecasting.  This is the most important open question before any
promotion to production candidate.

To score the final window after actuals are complete, re-run:

```powershell
uv run python scripts/python/forecast_replacement_shadow_window.py `
  --forecast-start-date 2026-06-18 `
  --allow-partial-actuals `
  --recent-volume-caps 0.85 1.0 1.1 `
  --threads 8
```

The frozen `shadow_daily_forecasts.parquet` will not be overwritten; the
script overwrites only the `shadow_score_summary.csv` and `shadow_metadata.json`
with the updated partial/final scores.  Pass `--overwrite-frozen-forecast` only
when intentionally rebuilding the frozen forecast from current inputs.

## July Sale PDL Feature Refresh — 2026-06-18

Follow-up on the sale-ramp miss found one stale-input issue, then ruled it out
as the primary cause.

The original June 18 shadow metadata reported `"promo_horizon_skus": 0` even
though `Source/Promotions/6.18.26 Hanna Sale PDL.xlsx` was present.  The issue
was a two-step refresh gap:

1. `extract_promotions.py` had not been rerun since June 10, so the June 18 PDL
   workbook was not in the extracted offer/event tables.
2. After refreshing the workbook extraction, `forecast_promo_sku_features.py`
   also had to be rerun to expand PDL offer rows into SKU/day features.

Commands run:

```powershell
uv run python scripts/python/extract_promotions.py
uv run python scripts/python/forecast_promo_sku_features.py
```

Refreshed PDL SKU/day feature summary:

```text
Output: Output/ForecastAccuracy/promotions/pdl_sku_day_features.parquet
Feature date range: 2026-06-09 through 2026-07-06
SKU/day feature rows: 570,521
Distinct SKUs with SKU-level PDL signal: 48,738
```

Then reran a separate, non-overwriting shadow into:

```text
Output/ForecastAccuracy/replacement_shadow_pdl_sku_refreshed/shadow_2026-06-18_2026-07-01/
```

The refreshed shadow metadata correctly reports:

```text
promo_horizon_skus: 48,738
future_rows: 3,387,986
```

However, forecast volume barely changed:

```text
Candidate                                 Original frozen   PDL-refreshed
Hybrid 5% fallback, 0.85x cap                 142,282          142,365
Hybrid 10% fallback, 0.85x cap                150,617          150,696
recent_no_ml_no_promo_floor                   277,260          277,260
OPS/IMF planner total                         456,096          456,096
```

Interpretation:

- The stale PDL SKU/day feature table was real, and it is now refreshed.
- It does **not** explain the sale-ramp under-forecast.  Even with 48,738
  promo-horizon SKUs in the future rows, the current model does not translate
  PDL presence into sale-level demand magnitude.
- Next useful work should build a separate sale-event promo-magnitude
  multiplier from prior DirectPick behavior.  More generic model tuning is
  unlikely to solve this gap by itself.

## July Sale DirectPick YoY Lift Overlay Decision — 2026-06-18

Follow-up analysis replaced the Planner-based volume read with the warehouse
actual that matters for this project: completed `DirectPick` work from
`WHSWORKLINE`.

Scratch analysis:

```text
Script: scratch/july_sale_direct_pick_lift_analysis.py
Output: scratch/july_sale_direct_pick_lift_outputs/
Prior DirectPick source: Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2025.parquet
Current actual source: Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet
Shadow source: Output/ForecastAccuracy/replacement_shadow_pdl_sku_refreshed/shadow_2026-06-18_2026-07-01/
```

Important source distinction:

- `Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2025.parquet` is the
  usable SKU/day `DirectPick` demand fact for the 2025 July-sale analog.
- `Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2024.parquet` is a
  three-year physical replenishment-touch fact, useful for velocity/routing
  policy, but it is not a three-year DirectPick demand fact.

DirectPick sale-lift result:

```text
2025 analog sale window:         2025-06-21 through 2025-07-04
2025 DirectPick sale units:      473,431
2025 pre-sale baseline expected: 221,823
2025 sale lift:                  2.13x baseline

2026 current pre-sale baseline:  260,634 expected units over 14 days
Current PDL-refreshed hybrid:    150,603 units across promoted categories
Recent no-ML floor:              276,907 units across promoted categories
2025 category-lift projection:   539,227 units across promoted categories
```

The exact 539K projection should not be hard-coded as the forecast.  It is an
uncapped diagnostic that proves the current ML shadow is missing sale-event
magnitude, not missing PDL rows.  The right production-safe shape is a new
shadow-only candidate:

```text
july_sale_yoy_lift_overlay
```

Candidate rule:

1. Use current 56-day DirectPick demand as the category baseline.
2. Apply 2025 July-sale category lift by `Division/Department/Class/KeyCategoryView`.
3. Cap or shrink noisy category lifts so tiny baselines cannot dominate.
4. Apply only to categories with current PDL promo SKUs in the forward window.
5. Allocate lifted category volume across current SKUs using the existing
   hybrid/recent/PDL shape instead of resurrecting prior-year SKUs.
6. Score as a forward shadow alongside:
   - current hybrid 5% and 10% fallback candidates;
   - recent no-ML floor;
   - corporate/Product Info when an exact-start comparison exists.

This overlay should be treated as a sale-regime correction layer, not a generic
replacement for the scikit-learn model.  It addresses the specific failure mode
where `hgb_absolute_log` sees PDL presence but does not translate it into
major-sale magnitude.

Implementation note:

- `forecast_replacement_shadow_window.py` now supports
  `--include-yoy-sale-lift-overlay` and `--base-frozen-forecast-path`.
- The first capped overlay shadow was generated without retraining by loading
  the PDL-refreshed frozen forecast and writing a separate output folder:

```text
Output/ForecastAccuracy/replacement_shadow_yoy_overlay_capped/shadow_2026-06-18_2026-07-01/
```

Overlay metadata:

```text
Candidate: july_sale_yoy_lift_overlay
Analog DirectPick sale units: 473,431
Analog baseline expected units: 221,823
Analog overall lift: 2.13x
Current DirectPick baseline window: 2026-04-23 through 2026-06-17
Current baseline units: 1,114,780
Uncapped overlay units: 943,397
Capped overlay units: 605,627
Total cap: current baseline daily units * 14 days * 2025 overall sale lift
Overlay SKUs: 49,270 total; about 16,407 with at least 1 unit and 6,389 with
at least 20 units over the horizon
```

The capped overlay is intentionally a shadow candidate only.  It is much closer
to a major-sale regime than the current ML hybrid (`~151K` units), but it is too
broad to upload without an operational thresholding/rounding review.
