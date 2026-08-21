# Multi-Window Corporate-Anchored Backtest — Results (contract-repaired)

**Exploratory retrospective evidence, not a champion decision.**

- Windows scored: **3** (2026-05-19 -> 2026-06-02).
- As-of category attributes per corporate vintage (snapshot-specific).
- Origin-safe inclusion coverage >= 90% (corporate-forecast side; NOT actuals).
- Activation inventory: `Output\ForecastAccuracy\inventory\pickface_inventory_sku_day.parquet`.
- Activation inbound: `Output\ForecastAccuracy\inbound\product_info_inbound_snapshots.parquet`.
- Activation is scored only on origins with a pre-origin evidence snapshot.
- Metric: SKU WAPE (lower better).

## Overall leaderboard (mean over all scored windows)

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 3 | 1.361 | 1.384 | 45.8% | -8.0% | 0 | 0.0% |
| corporate_total_recent_shape | 3 | 0.613 | 0.586 | 92.2% | -8.0% | 3 | 100.0% |
| catpool_corporate_anchor | 3 | 0.641 | 0.625 | 92.0% | -8.0% | 3 | 100.0% |
| catpool_corporate_anchor_activation | 3 | 0.712 | 0.719 | 93.7% | -8.0% | 3 | 100.0% |

## By corporate-file freeze class (availability vs origin)

| FreezeClass | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| clean_frozen | catpool_corporate_anchor | 2 | 0.649 | 93.7 | 100.0 |
| clean_frozen | catpool_corporate_anchor_activation | 2 | 0.733 | 95.1 | 100.0 |
| clean_frozen | corporate_raw | 2 | 1.489 | 36.6 | 0.0 |
| clean_frozen | corporate_total_recent_shape | 2 | 0.626 | 93.6 | 100.0 |
| late | catpool_corporate_anchor | 1 | 0.625 | 88.7 | 100.0 |
| late | catpool_corporate_anchor_activation | 1 | 0.671 | 90.9 | 100.0 |
| late | corporate_raw | 1 | 1.105 | 64.1 | 0.0 |
| late | corporate_total_recent_shape | 1 | 0.586 | 89.3 | 100.0 |

## By year

| Year | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| 2026 | catpool_corporate_anchor | 3 | 0.641 | 92.0 | 100.0 |
| 2026 | catpool_corporate_anchor_activation | 3 | 0.712 | 93.7 | 100.0 |
| 2026 | corporate_raw | 3 | 1.361 | 45.8 | 0.0 |
| 2026 | corporate_total_recent_shape | 3 | 0.613 | 92.2 | 100.0 |

## By hindsight regime (DIAGNOSTIC ONLY — uses realized coverage)

| Regime(hindsight) | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| degraded(cov<75%) | catpool_corporate_anchor | 3 | 0.641 | 92.0 | 100.0 |
| degraded(cov<75%) | catpool_corporate_anchor_activation | 3 | 0.712 | 93.7 | 100.0 |
| degraded(cov<75%) | corporate_raw | 3 | 1.361 | 45.8 | 0.0 |
| degraded(cov<75%) | corporate_total_recent_shape | 3 | 0.613 | 92.2 | 100.0 |

## Origin-safe gate (DEPLOYABLE policy: catpool when trailing-28d proxy < threshold)

| ProxyThreshold | WindowsTriggered | GatedMeanWAPE | CorporateRawMeanWAPE | AlwaysCatpoolMeanWAPE | WindowsImprovedVsRaw | WindowsWorsenedVsRaw |
| --- | --- | --- | --- | --- | --- | --- |
| 0.55 | 2.0 | 0.801 | 1.3611 | 0.6412 | 2.0 | 0.0 |
| 0.6 | 3.0 | 0.6412 | 1.3611 | 0.6412 | 3.0 | 0.0 |
| 0.65 | 3.0 | 0.6412 | 1.3611 | 0.6412 | 3.0 | 0.0 |
| 0.7 | 3.0 | 0.6412 | 1.3611 | 0.6412 | 3.0 | 0.0 |
| 0.75 | 3.0 | 0.6412 | 1.3611 | 0.6412 | 3.0 | 0.0 |
| 0.8 | 3.0 | 0.6412 | 1.3611 | 0.6412 | 3.0 | 0.0 |

If the best row barely beats `CorporateRawMeanWAPE`, the deployable gate is not yet effective — the hindsight regime split overstates the opportunity.

## Non-overlapping origins (>=14 days apart) — independence check

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 2 | 1.489308313591157 | 1.489308313591157 | 0.36640341471727045 | -0.011387224119989495 | 0 | 0.0 |
| corporate_total_recent_shape | 2 | 0.6264814898768798 | 0.6264814898768798 | 0.93625524896783 | -0.011387224119989495 | 2 | 1.0 |
| catpool_corporate_anchor | 2 | 0.6491049628751157 | 0.6491049628751157 | 0.9367994863530842 | -0.011387224119989495 | 2 | 1.0 |
| catpool_corporate_anchor_activation | 2 | 0.7332834516576574 | 0.7332834516576574 | 0.9514648081718508 | -0.011387224119989495 | 2 | 1.0 |

## Honest limitations
- The hindsight regime split near-tautologically favors reallocation and must not drive promotion.
- Even non-overlapping origins are not i.i.d.; add block-bootstrap CIs before any significance claim.
- `late`/`same_day` corporate files are operational-vintage, not clean prospective forecasts.
- Warehouse inventory and pick-face inventory are different evidence; compare them as separate sensitivities.
- Activation rows cover only origins with pre-origin evidence and must not be compared as if they covered every archive window.
