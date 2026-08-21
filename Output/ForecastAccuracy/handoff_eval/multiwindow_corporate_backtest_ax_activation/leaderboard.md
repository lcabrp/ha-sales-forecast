# Multi-Window Corporate-Anchored Backtest — Results (contract-repaired)

**Exploratory retrospective evidence, not a champion decision.**

- Windows scored: **9** (2026-04-07 -> 2026-06-02).
- As-of category attributes per corporate vintage (snapshot-specific).
- Origin-safe inclusion coverage >= 90% (corporate-forecast side; NOT actuals).
- Activation inventory: `C:\Users\labreu\Documents\Projects\ha-sales-forecast\Output\ForecastAccuracy\inventory\ax_inventory_history_sku_day.parquet`.
- Activation inbound: `C:\Users\labreu\Documents\Projects\ha-sales-forecast\Output\ForecastAccuracy\inbound\product_info_inbound_snapshots.parquet`.
- Activation is scored only on origins with a pre-origin evidence snapshot.
- Metric: SKU WAPE (lower better).

## Overall leaderboard (mean over all scored windows)

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 9 | 1.492 | 1.537 | 28.5% | -6.5% | 0 | 0.0% |
| corporate_total_recent_shape | 9 | 0.704 | 0.675 | 90.1% | -6.5% | 9 | 100.0% |
| catpool_corporate_anchor | 9 | 0.727 | 0.699 | 89.4% | -6.5% | 9 | 100.0% |
| catpool_corporate_anchor_activation | 9 | 0.788 | 0.766 | 91.6% | -6.5% | 9 | 100.0% |

## By corporate-file freeze class (availability vs origin)

| FreezeClass | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| clean_frozen | catpool_corporate_anchor | 4 | 0.712 | 93.4 | 100.0 |
| clean_frozen | catpool_corporate_anchor_activation | 4 | 0.784 | 94.8 | 100.0 |
| clean_frozen | corporate_raw | 4 | 1.591 | 28.3 | 0.0 |
| clean_frozen | corporate_total_recent_shape | 4 | 0.685 | 93.7 | 100.0 |
| late | catpool_corporate_anchor | 3 | 0.674 | 88.8 | 100.0 |
| late | catpool_corporate_anchor_activation | 3 | 0.709 | 91.7 | 100.0 |
| late | corporate_raw | 3 | 1.185 | 34.9 | 0.0 |
| late | corporate_total_recent_shape | 3 | 0.648 | 90.0 | 100.0 |
| same_day | catpool_corporate_anchor | 2 | 0.838 | 82.0 | 100.0 |
| same_day | catpool_corporate_anchor_activation | 2 | 0.917 | 84.9 | 100.0 |
| same_day | corporate_raw | 2 | 1.754 | 19.4 | 0.0 |
| same_day | corporate_total_recent_shape | 2 | 0.827 | 82.9 | 100.0 |

## By year

| Year | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| 2026 | catpool_corporate_anchor | 9 | 0.727 | 89.4 | 100.0 |
| 2026 | catpool_corporate_anchor_activation | 9 | 0.788 | 91.6 | 100.0 |
| 2026 | corporate_raw | 9 | 1.492 | 28.5 | 0.0 |
| 2026 | corporate_total_recent_shape | 9 | 0.704 | 90.1 | 100.0 |

## By hindsight regime (DIAGNOSTIC ONLY — uses realized coverage)

| Regime(hindsight) | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| degraded(cov<75%) | catpool_corporate_anchor | 9 | 0.727 | 89.4 | 100.0 |
| degraded(cov<75%) | catpool_corporate_anchor_activation | 9 | 0.788 | 91.6 | 100.0 |
| degraded(cov<75%) | corporate_raw | 9 | 1.492 | 28.5 | 0.0 |
| degraded(cov<75%) | corporate_total_recent_shape | 9 | 0.704 | 90.1 | 100.0 |

## Origin-safe gate (DEPLOYABLE policy: catpool when trailing-28d proxy < threshold)

| ProxyThreshold | WindowsTriggered | GatedMeanWAPE | CorporateRawMeanWAPE | AlwaysCatpoolMeanWAPE | WindowsImprovedVsRaw | WindowsWorsenedVsRaw |
| --- | --- | --- | --- | --- | --- | --- |
| 0.55 | 8.0 | 0.7804 | 1.4922 | 0.7272 | 8.0 | 0.0 |
| 0.6 | 9.0 | 0.7272 | 1.4922 | 0.7272 | 9.0 | 0.0 |
| 0.65 | 9.0 | 0.7272 | 1.4922 | 0.7272 | 9.0 | 0.0 |
| 0.7 | 9.0 | 0.7272 | 1.4922 | 0.7272 | 9.0 | 0.0 |
| 0.75 | 9.0 | 0.7272 | 1.4922 | 0.7272 | 9.0 | 0.0 |
| 0.8 | 9.0 | 0.7272 | 1.4922 | 0.7272 | 9.0 | 0.0 |

If the best row barely beats `CorporateRawMeanWAPE`, the deployable gate is not yet effective — the hindsight regime split overstates the opportunity.

## Non-overlapping origins (>=14 days apart) — independence check

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 5 | 1.4687655990393462 | 1.5366391892207458 | 0.267184540590665 | -0.10270145337655041 | 0 | 0.0 |
| corporate_total_recent_shape | 5 | 0.6969106073290211 | 0.6753872302215951 | 0.8771109229130063 | -0.10270145337655041 | 5 | 1.0 |
| catpool_corporate_anchor | 5 | 0.7158523634792088 | 0.6987461116634064 | 0.8701073685414202 | -0.10270145337655041 | 5 | 1.0 |
| catpool_corporate_anchor_activation | 5 | 0.7713808909625219 | 0.7664951949319351 | 0.8962438399972494 | -0.10270145337655041 | 5 | 1.0 |

## Honest limitations
- The hindsight regime split near-tautologically favors reallocation and must not drive promotion.
- Even non-overlapping origins are not i.i.d.; add block-bootstrap CIs before any significance claim.
- `late`/`same_day` corporate files are operational-vintage, not clean prospective forecasts.
- Warehouse inventory and pick-face inventory are different evidence; compare them as separate sensitivities.
- Activation rows cover only origins with pre-origin evidence and must not be compared as if they covered every archive window.
