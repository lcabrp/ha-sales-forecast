# Multi-Window Corporate-Anchored Backtest — Results (contract-repaired)

**Exploratory retrospective evidence, not a champion decision.**

- Windows scored: **146** (2023-05-30 -> 2026-06-02).
- As-of category attributes per corporate vintage (snapshot-specific).
- Origin-safe inclusion coverage >= 90% (corporate-forecast side; NOT actuals).
- Activation arm excluded (no origin-safe inventory covers the archive).
- Metric: SKU WAPE (lower better).

## Overall leaderboard (mean over all scored windows)

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 146 | 0.839 | 0.706 | 80.2% | 13.9% | 0 | 0.0% |
| corporate_total_recent_shape | 146 | 0.999 | 0.915 | 87.5% | 13.9% | 30 | 20.5% |
| catpool_corporate_anchor | 146 | 0.993 | 0.938 | 87.6% | 13.9% | 29 | 19.9% |

## By corporate-file freeze class (availability vs origin)

| FreezeClass | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| clean_frozen | catpool_corporate_anchor | 14 | 0.91 | 87.1 | 50.0 |
| clean_frozen | corporate_raw | 14 | 1.164 | 57.2 | 0.0 |
| clean_frozen | corporate_total_recent_shape | 14 | 0.912 | 87.0 | 50.0 |
| late | catpool_corporate_anchor | 54 | 0.999 | 86.7 | 13.0 |
| late | corporate_raw | 54 | 0.713 | 85.3 | 0.0 |
| late | corporate_total_recent_shape | 54 | 1.009 | 86.6 | 13.0 |
| same_day | catpool_corporate_anchor | 78 | 1.004 | 88.3 | 19.2 |
| same_day | corporate_raw | 78 | 0.868 | 80.8 | 0.0 |
| same_day | corporate_total_recent_shape | 78 | 1.008 | 88.1 | 20.5 |

## By year

| Year | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| 2023 | catpool_corporate_anchor | 33 | 1.025 | 89.8 | 0.0 |
| 2023 | corporate_raw | 33 | 0.635 | 92.7 | 0.0 |
| 2023 | corporate_total_recent_shape | 33 | 1.039 | 89.8 | 0.0 |
| 2024 | catpool_corporate_anchor | 45 | 1.033 | 87.4 | 6.7 |
| 2024 | corporate_raw | 45 | 0.767 | 88.2 | 0.0 |
| 2024 | corporate_total_recent_shape | 45 | 1.036 | 87.4 | 8.9 |
| 2025 | catpool_corporate_anchor | 46 | 0.967 | 89.2 | 17.4 |
| 2025 | corporate_raw | 46 | 0.782 | 85.9 | 0.0 |
| 2025 | corporate_total_recent_shape | 46 | 0.977 | 88.8 | 17.4 |
| 2026 | catpool_corporate_anchor | 22 | 0.916 | 81.4 | 81.8 |
| 2026 | corporate_raw | 22 | 1.413 | 33.2 | 0.0 |
| 2026 | corporate_total_recent_shape | 22 | 0.911 | 81.5 | 81.8 |

## By hindsight regime (DIAGNOSTIC ONLY — uses realized coverage)

| Regime(hindsight) | Candidate | Windows | MeanWAPE | MeanCoveragePct | WinRateVsRaw |
| --- | --- | --- | --- | --- | --- |
| degraded(cov<75%) | catpool_corporate_anchor | 31 | 0.996 | 79.6 | 67.7 |
| degraded(cov<75%) | corporate_raw | 31 | 1.358 | 40.8 | 0.0 |
| degraded(cov<75%) | corporate_total_recent_shape | 31 | 0.994 | 79.5 | 67.7 |
| healthy(cov>=75%) | catpool_corporate_anchor | 115 | 0.992 | 89.7 | 7.0 |
| healthy(cov>=75%) | corporate_raw | 115 | 0.699 | 90.8 | 0.0 |
| healthy(cov>=75%) | corporate_total_recent_shape | 115 | 1.001 | 89.6 | 7.8 |

## Origin-safe gate (DEPLOYABLE policy: catpool when trailing-28d proxy < threshold)

| ProxyThreshold | WindowsTriggered | GatedMeanWAPE | CorporateRawMeanWAPE | AlwaysCatpoolMeanWAPE | WindowsImprovedVsRaw | WindowsWorsenedVsRaw |
| --- | --- | --- | --- | --- | --- | --- |
| 0.55 | 28.0 | 0.7859 | 0.8392 | 0.9929 | 19.0 | 9.0 |
| 0.6 | 34.0 | 0.7969 | 0.8392 | 0.9929 | 20.0 | 14.0 |
| 0.65 | 36.0 | 0.8037 | 0.8392 | 0.9929 | 20.0 | 16.0 |
| 0.7 | 41.0 | 0.8091 | 0.8392 | 0.9929 | 21.0 | 20.0 |
| 0.75 | 57.0 | 0.8389 | 0.8392 | 0.9929 | 23.0 | 34.0 |
| 0.8 | 80.0 | 0.874 | 0.8392 | 0.9929 | 25.0 | 55.0 |

If the best row barely beats `CorporateRawMeanWAPE`, the deployable gate is not yet effective — the hindsight regime split overstates the opportunity.

## Non-overlapping origins (>=14 days apart) — independence check

| Candidate | Windows | MeanWAPE | MedianWAPE | MeanCoveragePct | MeanBiasPct | WinsVsCorporateRaw | WinRateVsCorporateRaw |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 70 | 0.8421970496666277 | 0.7198378992945171 | 0.791778968928674 | 0.12352123222085452 | 0 | 0.0 |
| corporate_total_recent_shape | 70 | 0.9833708355313214 | 0.896892227818578 | 0.8709949937880163 | 0.12352123222085452 | 14 | 0.2 |
| catpool_corporate_anchor | 70 | 0.9768049372138017 | 0.9066678275883986 | 0.8716507745859458 | 0.12352123222085452 | 14 | 0.2 |

## Honest limitations
- The hindsight regime split near-tautologically favors reallocation and must not drive promotion.
- Even non-overlapping origins are not i.i.d.; add block-bootstrap CIs before any significance claim.
- `late`/`same_day` corporate files are operational-vintage, not clean prospective forecasts.
- Activation is unevaluated here; wire origin-safe inventory history separately before any activation claim.
