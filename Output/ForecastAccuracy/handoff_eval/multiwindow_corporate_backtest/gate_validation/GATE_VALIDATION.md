# Origin-Safe Regime Gate — Out-of-Time Validation

**Question:** does a gate tuned only on the past improve the future? Metric = unit-weighted pooled SKU WAPE (lower better).

## 1. Single time split (train < 2025-07-01 <= test)

Train windows: 100, test windows: 46, tau* chosen on train = **0.30**.

Test-set performance:

| Policy | Windows | PooledWAPE | MeanWAPE | ImprovedVsRaw | WorsenedVsRaw |
| --- | --- | --- | --- | --- | --- |
| always_corporate_raw | 46 | 0.9041 | 1.0395 | 0 | 0 |
| always_catpool | 46 | 0.8915 | 0.9446 | 19 | 27 |
| oracle_perfect_gate(ceiling) | 46 | 0.7031 | 0.774 | 19 | 0 |
| gated(tau=0.30) | 46 | 0.7363 | 0.8315 | 12 | 0 |

(Train-set, for reference:)

| Policy | Windows | PooledWAPE | MeanWAPE | ImprovedVsRaw | WorsenedVsRaw |
| --- | --- | --- | --- | --- | --- |
| always_corporate_raw | 100 | 0.6705 | 0.7471 | 0 | 0 |
| always_catpool | 100 | 0.9545 | 1.0152 | 10 | 90 |
| oracle_perfect_gate(ceiling) | 100 | 0.6549 | 0.7186 | 10 | 0 |
| gated(tau=0.30) | 100 | 0.6705 | 0.7471 | 0 | 0 |

## 2. Expanding walk-forward (tau tuned only on strictly-earlier origins)

Test origins: 106 (from origin #41 onward).

| Policy | Windows | PooledWAPE | MeanWAPE | ImprovedVsRaw | WorsenedVsRaw | TriggeredCatpool |
| --- | --- | --- | --- | --- | --- | --- |
| always_corporate_raw | 106 | 0.7923 | 0.9029 | 0 | 0 |  |
| always_catpool | 106 | 0.9021 | 0.9524 | 29 | 77 |  |
| walkforward_gated | 106 | 0.7232 | 0.8126 | 12 | 0 | 12.0 |
| oracle_perfect_gate(ceiling) | 106 | 0.693 | 0.7607 | 29 | 0 |  |

## 3. Moving-block bootstrap CI on walk-forward (gated - corporate_raw)

```
{
  "n": 106,
  "block": 4,
  "n_boot": 2000,
  "point_diff_pooled_wape": -0.0691,
  "ci95_low": -0.1582,
  "ci95_high": -0.0056,
  "prob_gated_better": 0.982
}
```
A 95% CI that includes 0 means the gate's aggregate gain is not statistically distinguishable from noise.

## 4. clean_frozen slice within walk-forward (14 genuinely-prospective windows)

| Policy | Windows | PooledWAPE | MeanWAPE | ImprovedVsRaw | WorsenedVsRaw |
| --- | --- | --- | --- | --- | --- |
| always_corporate_raw | 14 | 1.0999 | 1.1642 | 0 | 0 |
| walkforward_gated | 14 | 0.8394 | 0.9012 | 4 | 0 |

## Read this honestly
- If walk-forward `walkforward_gated` PooledWAPE ~= `always_corporate_raw`, the gate does not yet generalize and must NOT be promoted.
- `oracle_perfect_gate(ceiling)` shows the maximum achievable if regime detection were perfect; the gap between the gate and the oracle is the room a better origin-safe signal could capture.
- Overlapping 14-day windows are not independent; the block bootstrap is the honest uncertainty.
