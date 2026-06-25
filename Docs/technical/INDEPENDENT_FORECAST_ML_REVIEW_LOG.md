# Independent Forecast - ML Review Log

A running, dated log of independent ML-expert review of the forecast-replacement
work. Each entry is a checkpoint so progress can be measured over time. Newest
entry first.

## 2026-06-18 - July sale DirectPick YoY lift overlay decision

### Context reviewed
- `Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2025.parquet`
- `scratch/july_sale_direct_pick_lift_analysis.py`
- `Output/ForecastAccuracy/replacement_shadow_pdl_sku_refreshed/shadow_2026-06-18_2026-07-01/`
- `Docs/operations/forecast_accuracy/FORECAST_REPLACEMENT_RESET_2026-06-15.md`

### Summary of verification
1. The prior-sale signal should come from warehouse `DirectPick` actuals, not
   Planner totals.  The usable local DirectPick SKU/day fact covers 2025-03-04
   through 2026-06-02, which includes the 2025 July-sale analog.  The separate
   three-year artifact is physical replenishment touches, not DirectPick demand.
2. The 2025 July-sale analog (`2025-06-21` through `2025-07-04`) had `473,431`
   DirectPick units versus `221,823` baseline-expected units, a `2.13x` lift.
3. Applying 2025 category lift to current promoted 2026 categories projects
   roughly `539K` units across promoted categories, versus the PDL-refreshed
   hybrid shadow at roughly `151K` and the recent no-ML floor at roughly `277K`.
4. This confirms the current ML family is seeing PDL presence but not learning
   major-sale magnitude.  The correct next candidate is a shadow-only
   `july_sale_yoy_lift_overlay`: apply capped/shrunk 2025 DirectPick category
   lift to the current PDL/current-assortment shape, then score it as actuals
   land.

### Recommendation

Do not hard-code the raw `539K` diagnostic projection.  Bake the signal as a
sale-regime overlay:

- category lift by `Division/Department/Class/KeyCategoryView`;
- cap or shrink unstable lifts from tiny baselines;
- apply only to current PDL categories;
- allocate onto current SKUs using the hybrid/recent/PDL shape;
- keep current hybrid, recent no-ML, and corporate as comparison candidates.

---

## 2026-06-18 - Delivery of memory-optimized Cold-Start Model & Orchestrated Pipeline

### Context reviewed
- `scripts/python/forecast_replacement_ml_cold_start.py` (memory-optimized, on-the-fly attributes merge)
- `scripts/python/forecast_replacement_hybrid_cold_start_candidate.py` (stable workbook and CSV Candidate generator)
- `scripts/python/forecast_pipeline_runner.py` (one-command runner)

### Summary of verification
1. **Memory Optimization**: Removing the global merge of the massive 4.9M row panel with Forecast DB snapshot categoricals successfully resolved the `ArrayMemoryError` allocation crash. The localized merge now safely executes training and predictions inside the sandbox.
2. **Pipeline Execution**: The orchestrated runner successfully runs backtesting, determines the optimal volume cap via the self-calibrating recency brake, generates AX-ready workbook packages, and outputs the expected operational cost scorecard in a single unified command.
3. **Smoke Run Verification**: A 3-window evaluation confirmed that the recency brake automatically adjusted the volume cap to `0.85` on `2026-06-02` window to mitigate collapse in demand, significantly reducing the expected operational cost compared to fixed caps.

---

## 2026-06-17 - Review of the replacement-reset Prod tests + two new decision tools

### Context reviewed

- `Docs/operations/forecast_accuracy/FORECAST_REPLACEMENT_RESET_2026-06-15.md`
- `Docs/operations/forecast_accuracy/DIRECT_PICK_HISTORY_DATASET.md`
- `Docs/operations/forecast_accuracy/CORPORATE_FORECAST_DB_SNAPSHOT_2026-06-17.md`
- `Docs/operations/forecast_accuracy/FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md`
- replacement backtest / ml-backtest / hybrid / volume-brake / policy scripts and
  the `champion_candidate_independent_category_size_shadow` review outputs.

### Headline verdict

Major step in the right direction. The famous `48%` sparse-panel WAPE is now
correctly demoted to a diagnostic, and the operational decision metric (BRG ->
ingestion -> AX contract, scored on replenishment-relevant DirectPick demand)
shows a **smaller but genuine win over corporate**. The work independently adopted
three levers from the prior review: demand censoring, quantile regression, and
hierarchical reconciliation.

### What is right (and the real win)

1. The decision metric was reframed to the replacement contract + coverage, not
   sparse WAPE. Correct.
2. Future-safe 26-window backtest: best candidate (volume-braked hybrid) ~ `80.6%`
   WAPE / `-6.9%` bias / `96.6%` coverage vs corporate `113.3%` / `+5.5%` / `43.9%`.
3. **The real win is coverage**: corporate leaves `46.3%` of sold units with zero
   forecast; the hybrid sits at `2.5%`. For a replenishment input that matters more
   than the WAPE gap.
4. The quantile script implements demand censoring (drops stockout rows), a
   conservative `0.35` quantile, and category-total reconciliation - the exact
   recommended levers.
5. Good empirical catch on the latest-period over-forecast (volume brake), and the
   corporate Forecast DB extract (25.5M rows, multi-year weekly demand history)
   unlocks a true comparison and real seasonal/elasticity features.

### Risks / what to fix (priority order)

1. **Regime instability is the #1 blocker.** The hybrid wins the aggregate but
   over-runs in the latest sale window (`+125%` bias) without the brake, and the
   `0.85x` cap is hand-tuned. Window-win counts are near-tied (corporate 8 /
   recent 7-10 / hybrid 3-4): no candidate dominates window-by-window.
2. **Coverage vs WAPE must be decided on cost.** A zero-forecast sold unit = no
   replenishment = stockout risk, not equal to a WAPE point.
3. **Cold-start is the problem.** Review shows ~99% of demand rows are
   `UnknownGoLive`; the model beats corporate most exactly there (promo+new:
   `0.45` vs `1.09` WAPE). Attribute / like-item cold-start + promo elasticity is
   the highest-ROI accuracy lever now.
4. Deterministic baselines still train on supply-censored fulfilled demand; this
   is part of why a volume brake keeps being needed.
5. Pick the decision grain (SKU/day vs SKU/14-day-total differ hugely) and make it
   the headline.
6. Exploit the corporate DB for a grain/horizon-aligned corporate baseline (likely
   deflates corporate's inflated WAPE), proper seasonal features, and a separately
   scored ensemble candidate. Keep corporate OUT of training features.
7. Velocity/slotting: validate tiers against the new "recent 56-day physical
   touches" ground truth; keep the asymmetric hysteresis instinct (immediate
   promote, staged demote); finalize after sale actuals + more snapshots.

### New tooling delivered this entry

Two standalone scripts that consume the existing per-window candidate score CSVs
(same contract as `forecast_replacement_policy_backtest.py`:
`ForecastStartDate, Candidate, ForecastUnits, SoldUnits, AbsErrorUnits,
ZeroForecastSoldUnits, SoldUnitForecastCoveragePct`). Neither retrains anything.

#### `scripts/python/forecast_replacement_recency_brake.py`

Replaces the hand-tuned static volume cap with a **self-calibrating brake**: for
each window it reads the reference (uncapped) candidate's recently realized
`Sold / Forecast` ratio over the prior `--lookback` windows and selects the cap
variant that brakes at least as hard as the recent miss implies, with a coverage
guard that falls back toward the high-coverage recent baseline. It only uses prior
windows, so the evaluation is honest out-of-sample.

```powershell
uv run python scripts/python/forecast_replacement_recency_brake.py `
  --score-file Output/ForecastAccuracy/replacement_ml_backtests/combined_replacement_window_scores.csv `
  --reference <uncapped_hybrid_candidate> `
  --cap-variant 1.00:<hybrid_cap1p00> `
  --cap-variant 0.85:<hybrid_cap0p85> `
  --cap-variant 0.70:<hybrid_cap0p70> `
  --floor-candidate recent_no_ml_no_promo_floor `
  --compare corporate --compare recent_no_ml_no_promo_floor --lookback 2
```

Smoke check (synthetic 12-window fixture where demand collapses late): the brake
auto-tightened from cap `1.00` to `0.70` exactly when the recent ratio fell to
`0.53`, and beat the fixed uncapped candidate (`WAPE 0.19 vs 0.43`, bias `-13%`
vs `+32%`) with no hindsight. On the real sale-window blow-up the adaptive brake
should win clearly because it tightens precisely there.

> Action: run on the real `combined_replacement_window_scores.csv` with the actual
> capped-variant slugs, tune `--lookback` (1-3) and `--min-coverage`, and report it
> alongside the fixed hybrid and corporate.

#### `scripts/python/forecast_replacement_cost_scorecard.py`

Turns the coverage-vs-WAPE tradeoff into an **expected operational cost**. From the
window aggregates it exactly splits error into over-forecast, covered shortfall,
and zero-forecast misses, then charges each at its own unit cost:

```text
expected_cost = c_over * over_units
              + c_under * (under_units - zero_forecast_sold_units)
              + c_zero  * zero_forecast_sold_units
```

It prints a per-candidate cost ranking and a **break-even sweep** of the
shortfall/excess cost ratio, so the decision boundary is explicit.

```powershell
uv run python scripts/python/forecast_replacement_cost_scorecard.py `
  --score-file Output/ForecastAccuracy/replacement_ml_backtests/combined_replacement_window_scores.csv `
  --focus <hybrid_cap0p85> --focus recent_no_ml_no_promo_floor `
  --c-over 1 --c-under 3 --c-zero 6
```

Smoke check: with default costs the capped hybrid wins when a shortfall unit costs
under ~`2x` an excess unit, and the high-coverage recent baseline wins at `>=3x`.
For a fashion DC during a sale, stockout/lost-margin per unit is plausibly several
times the cost of an excess replenishment unit, which would favor protecting
coverage - the opposite of what raw WAPE alone suggests.

> Action: set `--c-over/--c-under/--c-zero` from real lost-margin and
> replenishment-labor estimates and read the break-even ratio to choose between the
> capped hybrid and the recent baseline.

### Recommended path to production

1. Make the brake self-calibrating (done as a backtest tool) and validate it
   rolling-OOS on the real scores.
2. Decide coverage vs WAPE with the cost scorecard using real unit costs.
3. Add cold-start + censoring improvements using the corporate DB.
4. Run a multi-week forward shadow across the July sale, never touching AX, before
   any upload.
