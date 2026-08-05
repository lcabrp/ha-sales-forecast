# Forecast Closeout: 2026-07-21 through 2026-08-03

Completed 2026-08-05. This is the completed evaluation of the July 21 forward
packs. It preserves the distinction between forecasts that were frozen at the
origin and methods added after the origin.

## Decision

- The two legitimate prospective contestants form a precision/coverage tradeoff;
  neither is promoted as an unqualified champion.
- `corporate_total_recent_shape` improves SKU WAPE and sold-unit coverage over
  `corporate_raw`, but it forecasts more zero-demand units and has lower
  forecast-SKU use.
- The July 22 corporate-anchored category-pool diagnostic improves WAPE,
  coverage, and forecast-SKU use over both prospective contestants, but carries
  more zero-demand forecast units than corporate raw. It was created after the
  July 21 origin, so treat it as evidence for the next clean origin, not as the
  winner of this contest.
- Corporate daily volume underforecast the window by 22,636 units (-12.06%).
  The raw, recent-shape, and corporate-anchored category-pool forecasts share
  those exact daily totals, so allocation cannot repair this total-volume miss.

## Evaluation Contract

| Item | Value |
|---|---|
| Forecast origin / horizon | 2026-07-21 through 2026-08-03, 14 calendar days |
| Monitoring completeness | `ha-kydc-monitoring/Output/Monitoring/Monitoring_History.db` |
| Monitoring Pick total | 187,647 units, 14/14 Eastern days |
| SKU/day actual source | Read-only `prodaxsql2` / `DAX_PROD` fallback using monitoring DirectPick filters and Eastern dates |
| SKU/day actual | 77,522 rows, 14,900 SKUs, 187,644 units |
| Reconciliation | AX detail minus monitoring aggregate = -3 units (-0.002%) |
| Category crosswalk for scores | Tracked July 7 handoff `sku_ledger.db`, 20,449 rows |

The monitoring source supplied complete daily totals but no current canonical
SKU/day fact. The live-AX fallback was therefore required and is the correct
source under the documented precedence. The three-unit difference is
immaterial and consistent with query timing or late AX changes.

## Results

All percentages below are horizon-level SKU allocation metrics. WAPE above 100%
is possible because overforecast and missed demand both contribute to absolute
error.

| Status | Candidate | Units | Bias | SKU WAPE | Fcst+ SKUs | SKU use rate | Sold-unit coverage | Zero-demand forecast units |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Prospective contestant | Corporate raw | 165,008 | -12.06% | 129.07% | 5,792 | 82.72% | 53.48% | 6.25% |
| Prospective contestant | Corporate total + recent shape | 165,008 | -12.06% | 115.45% | 8,389 | 79.21% | 59.90% | 15.47% |
| Origin-frozen volume diagnostic | Independent recent shape | 280,572 | +49.52% | 147.29% | 19,013 | 71.59% | 95.46% | 16.08% |
| Late-origin diagnostic | Category pool, corporate anchor + activation | 165,008 | -12.06% | 98.09% | 9,692 | 84.40% | 72.23% | 9.58% |
| Late-origin diagnostic | Independent category pool + activation | 150,869 | -19.60% | 91.44% | 25,039 | 55.46% | 96.73% | 18.18% |

The corporate-anchored candidates have the same daily volume plan, producing a
shared daily WAPE of 21.31%. The free-total diagnostics miss timing and volume
more substantially (45.52% and 50.75% daily WAPE respectively).

### What the prospective contest says

The recent-shape allocation reduces SKU WAPE by 13.62 percentage points and
captures 6.42 more points of sold units than corporate raw. The cost is a 3.51
point decrease in forecast-SKU use and 9.22 more points of forecast units sent
to SKUs with no demand. That is a real frontier tradeoff, not proof that either
method is universally preferable without an explicit precision/coverage policy
or carton-use simulator.

### What the late-origin diagnostics say

The corporate-anchored category-pool diagnostic improves on the frozen
recent-shape allocation in SKU WAPE (98.09% versus 115.45%), coverage (72.23%
versus 59.90%), forecast-SKU use (84.40% versus 79.21%), and zero-demand
forecast units (9.58% versus 15.47%). It also improves category allocation in
the two review cells:

| Candidate | GIRM SKU WAPE | BOYM SKU WAPE |
|---|---:|---:|
| Corporate raw | 136.17% | 140.93% |
| Corporate total + recent shape | 146.78% | 120.99% |
| Category pool, corporate anchor + activation | 116.65% | 88.60% |

This is strong motivation to freeze the category-pool architecture before the
next origin. It is not a retroactive promotion: the implementation and output
were created on July 22, after the July 21 horizon began.

The independent category-pool diagnostic has the lowest WAPE and highest
coverage, but it underforecasts total demand by 19.60%, activates too many SKUs,
and places 18.18% of forecast units on zero-demand SKUs. The free-volume path
remains a diagnostic, not a total-volume owner.

## Durable Evidence

The complete closeout pack is under:

```text
Output/ForecastAccuracy/handoff_eval/forward_2026-07-21_closeout/
```

| File | Purpose |
|---|---|
| `actual_sku_day.parquet` | Exact AX SKU/day actuals used for this closeout |
| `forecast_window_scores.csv` | Horizon allocation scorecard |
| `forecast_daily_totals.csv` | Daily totals and timing errors |
| `forecast_category_scores.csv` | Category allocation diagnostics |
| `forecast_sku_comparison.parquet` | Per-SKU forecast-versus-actual evidence |
| `forecast_window_metadata.json` | Source paths, rows, totals, and monitoring reconciliation |

All six files are compact (the largest is approximately 0.53 MB) and should be
committed with this record under the portable-artifact contract.
