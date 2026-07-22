# Forecast Closeout: 2026-07-07 through 2026-07-20

This is the final 14-day evaluation of the forecasts frozen for the July 7
origin. It separates total volume, daily timing, individual-SKU allocation,
and the Operations preference to avoid pulling low-probability boxes.

## Decision

Do not reset the project again. Narrow the architecture.

- Corporate is currently the better total-volume and sale-calendar signal.
- Recent statistics are the better SKU allocation baseline.
- The independent ML hybrid is not the champion: it missed total volume and
  assigned too much volume to SKUs that were never picked.
- The next candidate should forecast stable category-size pools such as
  `GIRM`/`BOYM`, then rank/select current SKUs and estimate quantity conditional
  on use. ML should help occurrence, promotion/newness, and residual ranking;
  it should not own the whole total-to-SKU problem.
- Optimize a precision/coverage frontier, not WAPE alone. A forecast-positive
  SKU is only a box-use proxy until case quantity and replenishment simulation
  are added.

## Evaluation Contract

| Item | Value |
|---|---|
| Forecast origin / horizon | 2026-07-07 through 2026-07-20, 14 calendar days |
| Monitoring completeness source | `ha-kydc-monitoring/Output/Monitoring/Monitoring_History.db` |
| Monitoring Pick total | 203,347 units, 14/14 Eastern days |
| SKU/day actual source | Read-only live AX fallback using the monitoring DirectPick filters and Eastern window |
| SKU/day actual | 77,892 rows, 15,828 SKUs, 203,327 units |
| Reconciliation | AX detail minus monitoring aggregate = -20 units (-0.01%) |
| Category crosswalk | Active ingestion `sku_ledger.db`, 113,824 normalized latest-SKU rows loaded |

The 20-unit difference is immaterial and is consistent with query/run timing or
late AX changes. The closeout scorer did not use the broader legacy
`actual_sku_day_modified.parquet`, which ends July 9 and differs from the
monitoring target.

Evidence is under:

```text
Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_closeout/
```

## Frozen Forecast Results

Percentages below are horizon-level SKU allocation metrics. WAPE above 100% is
possible because overforecast on some SKUs and missed demand on other SKUs both
contribute absolute error.

| Frozen candidate | Units | Bias | SKU WAPE | Forecast-positive SKUs | Forecast SKU use rate | Sold-unit coverage | Forecast units on zero-demand SKUs |
|---|---:|---:|---:|---:|---:|---:|---:|
| Corporate raw | 204,654 | +0.65% | 154.77% | 3,366 | 88.09% | 35.15% | 10.14% |
| Independent ML hybrid (`absolute_log`) | 157,409 | -22.58% | 131.48% | 11,056 | 76.39% | 71.24% | 29.91% |
| Corporate total + recent shape, frozen before rounding repair | 193,565 | -4.80% | 103.71% | 8,191 | 86.47% | 65.63% | 8.29% |
| Independent recent shape, free total | 305,454 | +50.23% | 133.42% | 9,863 | 84.87% | 71.08% | 8.83% |

The corporate-total/recent-shape artifact lost 11,089 units during independent
SKU rounding. Its 193,565-unit result is the honest frozen score, not a true
fixed-total result. The bug is now repaired with deterministic
largest-remainder rounding.

### Post-closeout engineering diagnostics

These are useful method diagnostics but were rebuilt after the horizon and are
not promoted as frozen contestants:

| Diagnostic | Units | Bias | SKU WAPE | Forecast-positive SKUs | Forecast SKU use rate | Sold-unit coverage | Zero-demand forecast units |
|---|---:|---:|---:|---:|---:|---:|---:|
| Repaired corporate-total/recent-shape | 204,654 | +0.65% | 105.13% | 8,530 | 86.01% | 66.86% | 8.83% |
| Exact anchor over integer recent shape | 204,654 | +0.65% | 103.90% | 9,602 | 85.15% | 70.39% | 8.96% |

## What The Result Means

Corporate total volume was excellent, but its SKU distribution was not.
Corporate forecast only 3,366 positive SKUs and 88.1% of those SKUs were used,
which matches the preference for fewer useful boxes. The cost was severe: those
SKUs covered only 35.1% of picked units and missed 100,992 units on SKUs that
sold at least 10 units.

The statistical corporate-total/recent-shape candidate was the best current
balance. It kept roughly 86% box/SKU precision, more than doubled sold-unit
coverage versus corporate, and put less than 9% of forecast units on SKUs that
never moved. This is evidence for a statistical allocation foundation, not
proof that a global recent-share allocation is sufficient.

The ML hybrid improved coverage to 71.2%, but failed the operational objective:
it forecast 22.6% too few total units, only 76.4% of its forecast-positive SKUs
were used, and 29.9% of its forecast units landed on zero-demand SKUs. ML is
currently broadening the SKU set without enough occurrence precision.

## Total Volume Can Hide A Daily Miss

Corporate's 14-day total bias was only +0.65%, but its daily WAPE was 28.45%.
It underforecast July 7-9 by 33.98%, then overforecast July 10-20 by 19.78%.
The total was right partly because early underage and later overage cancelled.

The ML hybrid's daily WAPE was 38.25%. It underforecast July 7-9 by 55.91% and
was much closer on aggregate over the remaining 11 days (-4.17%). The event
start/regime change, not just SKU splitting, was a major failure mode.

## Category Evidence

Current-ledger category mapping confirms that a global recent-share allocation
still needs category reconciliation.

| Candidate | Cell | Forecast | Actual | Bias | SKU WAPE |
|---|---|---:|---:|---:|---:|
| Corporate raw | GIRM | 14,696 | 18,944 | -4,248 | 140.88% |
| Repaired corporate-total/recent-shape | GIRM | 26,589 | 18,944 | +7,645 | 125.82% |
| Independent ML hybrid | GIRM | 17,177 | 18,944 | -1,767 | 123.76% |
| Corporate raw | BOYM | 3,054 | 6,232 | -3,178 | 122.69% |
| Repaired corporate-total/recent-shape | BOYM | 8,238 | 6,232 | +2,006 | 99.90% |
| Independent ML hybrid | BOYM | 5,490 | 6,232 | -742 | 122.72% |

The ML totals were closer for these two cells, but individual-SKU errors were
still large. This supports category-total reconciliation followed by selective
current-SKU allocation; it does not support returning the whole problem to ML.

## Promotion Visibility At Closeout

The July 21 promotion workbook is now extracted. The portable promotion store
contains 88 workbook records, 86 PDL workbooks, 358 events, and 243,641 offer
rows. The SKU/day store contains 8,059,505 rows, 80,485 SKUs, and dates through
July 21.

Important limitation: the July 21 workbook's `Effective Date(s)` cell contains
only `2026-07-21`. It gives no campaign end date. The feature builder therefore
flags July 21 only and does not invent July 22-27 coverage. This is genuine
limited promotion visibility, not a parser omission.

The extraction ledger retains 88 workbooks, but only six raw workbooks are
currently in `Source/Promotions`; 82 older raw workbooks are absent locally.
Their extracted tables remain available, but exact raw-workbook replay requires
recovering the originals.

## Legacy Repo Audit

No active forecast-model script was left exclusively in `ha-zoning-slotting`.

- `snapshot_forecast.py` and `audit_live_forecast_zone_coverage.py` are legacy
  copies of ingestion/monitoring responsibilities and should not move here.
- The legacy-only velocity-policy simulations are slotting policy work, not
  sales-demand forecasting.
- The June 12 forecast-model handoff is useful historical evidence: it records
  the old independent category/size ML champion. Its 48% backtest result is now
  superseded by the frozen forward closeout above and should not be cited as
  proof of current production performance.
- The legacy raw corporate CSV archive remains useful rebuild provenance. The
  compact current history Parquets are the active consumer artifacts, so the
  duplicate raw CSVs were indexed rather than copied back into this repo.
- The old July lift output reporting 473,431 units / 2.13x remains superseded by
  the current strict DirectPick manifest and category audit.

## Next Forward Shadow

The new corporate/velocity-frozen source starts July 21 and ends August 3:

| Candidate | 14-day units | Forecast-positive SKUs | Status |
|---|---:|---:|---|
| Corporate raw | 165,008 | 5,792 | Frozen forward benchmark |
| Corporate-total/recent-shape | 165,008 | 8,389 | Frozen statistical shadow |
| Independent recent shape, unconstrained | 280,572 | 19,013 | Volume diagnostic only |

The statistical shadow uses 56 complete pre-origin days, May 26-July 20:
330,048 SKU/day rows, 19,013 SKUs, and 1,117,444 monitoring-scope DirectPick
units. It preserves each corporate daily total exactly. Evidence is under:

```text
Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/
```

No July 21 ML forecast was retroactively manufactured. The existing model panel
ends June 8 and the refreshed July 21 PDL supplies only a one-day effective
date. Rebuild and freeze a future ML occurrence/ranking challenger before a
later origin if it is to receive a fair score.

## Repeatable Commands

```powershell
uv run python scripts/python/sync_monitoring_forecast_artifacts.py

uv run python scripts/python/forecast_actuals_source_audit.py `
  --start-date 2026-07-07 `
  --through-date 2026-07-20

uv run python scripts/python/forecast_window_compare.py `
  --start-date 2026-07-07 `
  --through-date 2026-07-20 `
  --daily-forecast Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_challenger/forward_daily_forecasts.parquet `
  --named-daily independent_hybrid_absolute_log=Output/ForecastAccuracy/handoff_eval/independent_hybrid_absolute_log_2026-07-07/contract/daily_forecast.parquet `
  --live-ax `
  --output-dir Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_closeout

uv run python scripts/python/extract_promotions.py --no-sqlite
uv run python scripts/python/forecast_promo_sku_features.py `
  --start-date 2026-07-07 `
  --merge-existing

uv run python scripts/python/forecast_forward_recent_shape.py `
  --corporate-fwd ../ha-ingestion-pipeline/Output/Ingestion/velocity_freeze/FwdDemandCSV_2026-07-21_velocity_frozen.csv `
  --live-ax `
  --lookback-days 56 `
  --output-dir Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow
```
