# Multi-Window Corporate-Anchored Backtest — Results (contract-repaired v2)

## UPDATE — origin-safe regime gate PASSES out-of-time validation

Run `scripts/python/forecast_gate_validation.py`; artifacts under
`gate_validation/` (`GATE_VALIDATION.md`, `walk_forward_windows.csv`,
`walk_forward_summary.csv`, `single_split_test.csv`, `bootstrap_ci.json`).

The gate = "use `catpool_corporate_anchor` when the pre-origin proxy
(trailing-28d demand share on corporate-positive SKUs) < tau, else
`corporate_raw`." Tuned **only on the past**, evaluated on the **future**.
Metric = unit-weighted pooled SKU WAPE (lower better).

| Evaluation | corporate_raw | gated | oracle ceiling | improved / worsened |
|---|---:|---:|---:|---:|
| Single split (train ≤2025-06 → test 46 win) | 0.904 | **0.728** (~19% better) | 0.703 | 14 / **0** |
| Expanding walk-forward (leakage-free, 106 win) | 0.792 | **0.723** (~9% better) | 0.693 | 12 / **0** |
| clean_frozen slice (14 truly-prospective) | 1.100 | **0.839** | — | 4 / **0** |

Moving-block bootstrap (block=4, 2000 resamples) 95% CI on (gated − raw) pooled
WAPE = **[−0.158, −0.006], excludes 0**; P(gated better) = 0.98.

**Verdict:** this is a real, statistically-supported, and *safe* improvement —
**0 windows worsened** in every cut, because the gate is conservative (fires on
only ~12/106 windows) and, when it fires, it is right. It nearly reaches the
oracle ceiling. This UPGRADES the gate from "in-sample / negligible" to
"validated enough to pre-register for a prospective clean-origin trial."

**Honest limitations (still not an unconditional champion):**
- The aggregate gain is concentrated in the **2026 coverage-collapse episode**;
  the test period is dominated by that single regime shift. It is validated as
  *future-relative-to-training*, but durability rests largely on one episode.
- `same_day`/`late` corporate vintages are operational, not strictly
  prospective; the 14-window `clean_frozen` slice mitigates but is small.
- Gap to oracle (0.723 vs 0.693) => a better origin-safe signal could add more.

**Recommendation:** graduate from exploratory to a **pre-registered prospective
trial** — freeze the proxy + tau (from expanding walk-forward) before the next
clean corporate origin per `FORECAST_NEXT_PROSPECTIVE_TEST_*.md`, apply catpool
only when the gate fires, and score on the clean-frozen closeout. Keep corporate
as the AX baseline; the gate is a collapse-regime rescue with a safe no-op
elsewhere.

---

**Exploratory retrospective evidence. NOT grounds to change the champion.**
This supersedes the first cut after a peer review flagged over-claiming. The
harness now uses as-of category attributes, origin-safe window inclusion,
corporate-file freeze classification, drops the (unevaluable) activation arm,
and adds an origin-safe gate + non-overlapping check.

146 origins, 2023-05-30 → 2026-06-02. Metric = SKU WAPE (lower better).

## What the review changed, and what it did not

| Fix applied | Effect on the conclusion |
|---|---|
| **As-of category mapping** (snapshot-specific PGC+SGC, not the 2026 crosswalk) | Aggregate essentially **unchanged** (corp_raw 0.839, catpool 0.993). The look-ahead in category identity was a real flaw but **not** what drove the headline. |
| **Origin-safe inclusion** (corporate-side mapping coverage, never horizon actuals) | Same window set qualifies; removed the hindsight inclusion filter. |
| **Freeze classification** | Only **14 / 146** windows are genuinely `clean_frozen` (file < origin). 78 `same_day`, 54 `late`. "146 frozen origins" was wrong. |
| **Activation arm dropped** | It evaluated nothing (pick-face inventory starts 2026-06-19, after the last origin 2026-06-02). Removed, not reported. |
| **Non-overlapping subset (70)** | Mirrors the full set → overlap was **not** inflating the aggregate. |
| **Hindsight regime split** | Retained as DIAGNOSTIC ONLY; no longer the headline. |

## Overall (146 windows)

| Candidate | Mean WAPE | Median | Mean Coverage | Win-rate vs raw |
|---|---:|---:|---:|---:|
| corporate_raw | **0.839** | 0.706 | 80.2% | — |
| catpool_corporate_anchor | 0.993 | 0.938 | 87.6% | 19.9% |
| corporate_total_recent_shape | 0.999 | 0.915 | 87.5% | 20.5% |

Non-overlapping (70 windows): corp_raw 0.842 vs catpool 0.977 — same story.

## By corporate-file freeze class (the honest cut)

| Freeze class | corp_raw WAPE | catpool WAPE | corp_raw cov | catpool cov | catpool win-rate |
|---|---:|---:|---:|---:|---:|
| **clean_frozen (14)** | 1.164 | **0.910** | 57.2% | 87.1% | 50% |
| same_day (78) | **0.868** | 1.004 | 80.8% | 88.3% | 19% |
| late (54) | **0.713** | 0.999 | 85.3% | 86.7% | 13% |

The only genuinely prospective subset (14 clean-frozen windows) happens to be
skewed toward low-coverage periods, where catpool ties/wins half. Suggestive,
but **14 windows cannot promote a champion.**

## By year (allocation is conditional)

| Candidate | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|
| corporate_raw | **0.635** | **0.767** | **0.782** | 1.413 |
| catpool_corporate_anchor | 1.025 | 1.033 | 0.967 | **0.916** |

catpool only helps in **2026** (corporate coverage collapsed 90%→33%; catpool
restores it to 81% and wins 82% of 2026 windows). It hurts in 2023-2025.

## Origin-safe deployable gate (trailing-28d demand share on corporate-positive SKUs)

Policy: use catpool when the proxy < threshold, else corporate_raw.

| Proxy threshold | Windows triggered | Gated mean WAPE | corp_raw mean WAPE | Improved / Worsened |
|---:|---:|---:|---:|---:|
| 0.55 | 28 | **0.786** | 0.839 | 19 / 9 |
| 0.60 | 34 | 0.797 | 0.839 | 20 / 14 |
| 0.70 | 41 | 0.809 | 0.839 | 21 / 20 |
| 0.75 | 57 | 0.839 | 0.839 | 23 / 34 |

At the best **in-sample** threshold (0.55) the gate cuts aggregate WAPE ~6%
(0.839 → 0.786) with a favorable 19:9 improved:worsened ratio. That is more
encouraging than "negligible" — **but it is in-sample threshold selection on
overlapping windows**, so it is not validated and must not drive promotion.
(A prior review using a different proxy found ~zero aggregate gain; both are
exploratory and proxy-dependent.)

## Bottom line

- **Keep the architecture and the harness. Do not change the champion.**
- The corrected evidence still says the category-pool re-allocation is a
  **coverage-collapse rescue**, not a general replacement — but the *promotable*
  version (an origin-safe gate) is only a modest, unvalidated aggregate gain.
- Real value was the **data/evaluation unlock**: this is producible offline in
  ~12 minutes across 146 windows, not one live window per fortnight.

## Next work (repair-the-contract order, not another model layer)

1. **Time-separated gate validation.** Tune the proxy threshold on an early time
   block, test on a held-out later block; add block-bootstrap CIs on
   non-overlapping origins. Only then consider a prospective freeze.
2. **Clean-frozen-only track.** Report and pre-register on `clean_frozen`
   windows; treat `same_day`/`late` as operational-vintage analysis only.
3. **Historical inventory/inbound** (as-of) for 2024-2025 season resets, so the
   activation layer can finally be evaluated. Until then it stays out.
4. **Record full provenance per window** (SnapshotId, availability, source hash,
   mapping coverage) — SnapshotId + freeze class are now emitted; add hashes.
