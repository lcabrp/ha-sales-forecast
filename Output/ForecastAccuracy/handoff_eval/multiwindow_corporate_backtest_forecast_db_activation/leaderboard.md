# Multi-Window Corporate-Anchored Backtest — Results (contract-repaired)

**Exploratory retrospective evidence, not a champion decision.**

- Windows scored: **16** (2026-02-18 -> 2026-06-02).
- As-of category attributes per corporate vintage (snapshot-specific).
- Origin-safe inclusion coverage >= 90% (corporate-forecast side; NOT actuals).
- Activation inventory: `Output\ForecastAccuracy\inventory\forecast_db\direct_sku_inventory_weekly.parquet`.
- Activation inbound: `Output\ForecastAccuracy\inbound\product_info_inbound_snapshots.parquet`.
- Activation is scored only on origins with a pre-origin evidence snapshot.
- Metric: SKU WAPE (lower better).

## Overall leaderboard (mean over all scored windows)

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 16 | 1.538 | 1.566 | 26.2% | -1.7% | 0 | 0.0% |
| corporate_total_recent_shape | 16 | 0.808 | 0.754 | 87.3% | -1.7% | 16 | 100.0% |
| catpool_corporate_anchor | 16 | 0.827 | 0.774 | 86.7% | -1.7% | 16 | 100.0% |
| catpool_corporate_anchor_activation | 16 | 0.844 | 0.846 | 89.2% | -1.7% | 16 | 100.0% |

## By corporate-file freeze class (availability vs origin)

| FreezeClass | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| clean_frozen | catpool_corporate_anchor | 6 | 0.807 | 90.3 | 100.0 |
| clean_frozen | catpool_corporate_anchor_activation | 6 | 0.833 | 92.5 | 100.0 |
| clean_frozen | corporate_raw | 6 | 1.581 | 27.2 | 0.0 |
| clean_frozen | corporate_total_recent_shape | 6 | 0.785 | 90.7 | 100.0 |
| late | catpool_corporate_anchor | 4 | 0.767 | 87.9 | 100.0 |
| late | catpool_corporate_anchor_activation | 4 | 0.77 | 90.8 | 100.0 |
| late | corporate_raw | 4 | 1.244 | 33.2 | 0.0 |
| late | corporate_total_recent_shape | 4 | 0.755 | 88.6 | 100.0 |
| same_day | catpool_corporate_anchor | 6 | 0.886 | 82.3 | 100.0 |
| same_day | catpool_corporate_anchor_activation | 6 | 0.903 | 84.9 | 100.0 |
| same_day | corporate_raw | 6 | 1.692 | 20.5 | 0.0 |
| same_day | corporate_total_recent_shape | 6 | 0.866 | 83.0 | 100.0 |

## By year

| Year | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| 2026 | catpool_corporate_anchor | 16 | 0.827 | 86.7 | 100.0 |
| 2026 | catpool_corporate_anchor_activation | 16 | 0.844 | 89.2 | 100.0 |
| 2026 | corporate_raw | 16 | 1.538 | 26.2 | 0.0 |
| 2026 | corporate_total_recent_shape | 16 | 0.808 | 87.3 | 100.0 |

## By hindsight regime (DIAGNOSTIC ONLY — uses realized coverage)

| Regime(hindsight) | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| degraded(cov<75%) | catpool_corporate_anchor | 16 | 0.827 | 86.7 | 100.0 |
| degraded(cov<75%) | catpool_corporate_anchor_activation | 16 | 0.844 | 89.2 | 100.0 |
| degraded(cov<75%) | corporate_raw | 16 | 1.538 | 26.2 | 0.0 |
| degraded(cov<75%) | corporate_total_recent_shape | 16 | 0.808 | 87.3 | 100.0 |

## Origin-safe gate (DEPLOYABLE policy: catpool when trailing-28d proxy < threshold)

| ProxyThreshold | WindowsTriggered | GatedMeanWAPE | CorporateRawMeanWAPE | AlwaysCatpoolMeanWAPE | WindowsImprovedVsRaw | WindowsWorsenedVsRaw |
| --- | --- | --- | --- | --- | --- | --- |
| 0.55 | 15.0 | 0.8568 | 1.5381 | 0.8268 | 15.0 | 0.0 |
| 0.6 | 16.0 | 0.8268 | 1.5381 | 0.8268 | 16.0 | 0.0 |
| 0.65 | 16.0 | 0.8268 | 1.5381 | 0.8268 | 16.0 | 0.0 |
| 0.7 | 16.0 | 0.8268 | 1.5381 | 0.8268 | 16.0 | 0.0 |
| 0.75 | 16.0 | 0.8268 | 1.5381 | 0.8268 | 16.0 | 0.0 |
| 0.8 | 16.0 | 0.8268 | 1.5381 | 0.8268 | 16.0 | 0.0 |

If the best row barely beats `CorporateRawMeanWAPE`, the deployable gate is not yet effective — the hindsight regime split overstates the opportunity.

## Non-overlapping origins (>=14 days apart) — independence check

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 8 | 1.5136504262622146 | 1.5349503269763263 | 0.25689941684823625 | -0.04507480653096241 | 0 | 0.0 |
| corporate_total_recent_shape | 8 | 0.7846817344629053 | 0.7210087307162842 | 0.8648292424004276 | -0.04507480653096241 | 8 | 1.0 |
| catpool_corporate_anchor | 8 | 0.8052776278714071 | 0.7468507229066956 | 0.857835602434267 | -0.04507480653096241 | 8 | 1.0 |
| catpool_corporate_anchor_activation | 8 | 0.8235708873861374 | 0.7508792161019711 | 0.8855796173828664 | -0.04507480653096241 | 8 | 1.0 |

## Honest limitations
- The hindsight regime split near-tautologically favors reallocation and must not drive promotion.
- Even non-overlapping origins are not i.i.d.; add block-bootstrap CIs before any significance claim.
- `late`/`same_day` corporate files are operational-vintage, not clean prospective forecasts.
- Warehouse inventory and pick-face inventory are different evidence; compare them as separate sensitivities.
- Activation rows cover only origins with pre-origin evidence and must not be compared as if they covered every archive window.
