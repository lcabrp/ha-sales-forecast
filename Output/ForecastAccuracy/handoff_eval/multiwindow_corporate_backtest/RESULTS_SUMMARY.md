# Multi-Window Corporate-Anchored Backtest — First Full Run

**146 frozen corporate origins, 2023-05-30 → 2026-06-02.** Frozen vintage =
earliest upload per `ForecastStartDate`. Origin-safe history. Windows with
< 90% category coverage skipped (removes 2022). Metric = SKU WAPE, lower better.
All anchored candidates preserve the corporate daily totals exactly.

## 1. Overall leaderboard (mean over all 146 windows)

| Candidate | Mean WAPE | Median WAPE | Mean Coverage | Mean SKU-use | Win-rate vs corporate_raw |
|---|---:|---:|---:|---:|---:|
| corporate_raw | **0.839** | 0.706 | 80.2% | 91.6% | — |
| catpool_corporate_anchor | 0.991 | 0.923 | 87.6% | 84.2% | 20.5% |
| corporate_total_recent_shape | 0.999 | 0.915 | 87.5% | 84.1% | 20.5% |
| catpool_corporate_anchor_activation | 0.991 | 0.923 | 87.6% | 84.2% | 20.5% |

On the raw average, **corporate_raw wins** and the re-allocation candidates look
*worse*. That average is misleading — see the regime split below.
(`activation == anchor` here because no origin-safe inventory snapshot exists
before ~2026-04, so the activation layer has no evidence on 143 of 146 windows.)

## 2. The finding — it is a regime-specific rescue, not a universal model

### By year (mean SKU WAPE)

| Candidate | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|
| corporate_raw | **0.635** | **0.767** | **0.782** | 1.413 |
| catpool_corporate_anchor | 1.027 | 1.028 | 0.969 | **0.906** |

### By year (mean sold-unit coverage)

| Candidate | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|
| corporate_raw | 92.7% | 88.2% | 85.9% | **33.2%** |
| catpool_corporate_anchor | 89.8% | 87.5% | 89.0% | **81.6%** |

### By corporate-coverage regime

| Regime | Candidate | Windows | Mean WAPE | Mean Coverage | Win-rate vs raw |
|---|---|---:|---:|---:|---:|
| degraded (corp cov < 75%) | corporate_raw | 31 | 1.358 | 40.8% | — |
| degraded (corp cov < 75%) | **catpool_corporate_anchor** | 31 | **0.986** | **79.8%** | **71.0%** |
| healthy (corp cov ≥ 75%) | corporate_raw | 115 | **0.699** | 90.8% | — |
| healthy (corp cov ≥ 75%) | catpool_corporate_anchor | 115 | 0.992 | 89.7% | 7.0% |

## 3. What this means (and why it matters for direction)

1. **The category-pool re-allocation is a rescue for the coverage-collapse
   regime, not a general-purpose replacement.** When the corporate SKU forecast
   has collapsed (2026 / coverage < 75%) it cuts WAPE **1.36 → 0.99** and nearly
   **doubles sold-unit coverage (40.8% → 79.8%)**, winning **71%** of those
   windows. When corporate is healthy (2023–2025) it *hurts* (WAPE 0.70 → 0.99)
   and wins only ~7% of the time.

2. **The single July-7 window that anointed `catpool_corporate_anchor_activation`
   the champion was a 2026 season-onset window — i.e. the degraded regime.** That
   is why it looked like a clean win. Across 146 windows it is revealed as
   **conditional**. Pre-registering it as an unconditional replacement for the
   corporate forecast would have *degraded* accuracy in ~79% of historical
   windows.

3. **The right direction is a regime-GATED policy**, not "catpool vs corporate":
   detect the coverage-collapse regime and apply category-pool re-allocation only
   then; otherwise trust `corporate_raw`. The 2026 collapse (corporate coverage
   ~90% → ~33%) is the real, persistent business problem, and this candidate
   demonstrably addresses exactly that regime.

4. **Honest caveat — the regime label here is hindsight.** It uses each window's
   *realized* corporate coverage, which is not known at the origin. To deploy the
   gate you need an **origin-safe proxy** for "corporate is about to under-cover",
   e.g. the share of trailing-28d sold units whose SKU is absent/zero in the new
   corporate upload, assortment-turnover, or forecast-positive-SKU count vs
   recent-active-SKU count. Building and validating that proxy across these same
   146 windows is the immediate next step.

5. **Activation is still only tested on recent windows** (no historical
   inventory). Adding as-of inventory/inbound history for 2024–2025 season resets
   is the highest-value data unlock left.

## 4. Bottom line for the direction review

- The architecture is sound and aimed at the correct problem. **Keep it.**
- Stop framing it as "beat corporate everywhere." Frame it as **"detect and
  rescue the coverage-collapse regime."** The evidence for that framing is now
  quantified across 146 windows instead of one.
- This entire result was produced offline, in one run, from data already in the
  repo — the two-week live loop was never necessary to get here.

Artifacts: `summary.csv`, `by_year.csv`, `by_regime.csv`, `per_window.csv`,
`skipped_windows.csv`, `run_metadata.json`, `leaderboard.md`.
