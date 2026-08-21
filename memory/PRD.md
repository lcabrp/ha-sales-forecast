# ha-sales-forecast — Working Memory / PRD

## Problem statement (verbatim intent)
Validate the sales-forecast project direction and, without another reset,
propose and prototype 1-2 new forecasting models. Keep the existing "August 4"
model (frozen July 21–August 3 corporate shadow). Idea: learn stable
category/size pools (GIRM, BOYM = ProductGroupCode+SizeGroupCode) from prior
years, allocate to the *current* assortment (SKUs change YoY), and leverage
inventory/inbound availability + corporate promotion signal (Source/Promotions).

## Domain
Independent 14-day DirectPick demand forecast for Hanna Andersson Kentucky DC.
Target = completed DirectPick warehouse pick work. Pure-Python data-science repo
(pandas/pyarrow/sklearn), `uv`-managed, Python 3.13. No web app.

## Architecture decision (kept, from FORECAST_CURRENT_STATE.md)
Two-stage hierarchy: corporate = best total-volume/calendar anchor; recent stats
= best SKU allocation baseline; forecast stable category pools then allocate to
current SKUs; ML stays narrow (occurrence/residual). Optimize precision/coverage.

## Delivered 2026-07-22
- Analysis of full pipeline + docs (no reset needed; direction validated).
- NEW `scripts/python/forecast_model_category_pool.py`: two-stage category-pool
  model. Anchors: `catpool_independent` (run-rate×14×shrunk multi-year lift,
  calendar-day spine) and `catpool_corporate_anchor` (exact corporate daily
  totals reconciled by category → SKU). Optional `--activation` season-transition
  layer (origin-safe inventory/inbound: activate new SKUs w/ category prior,
  down-weight ending-season SKUs). Hamilton rounding preserves category+daily
  totals exactly.
- NEW `scripts/python/forecast_backtest_category_pool.py`: honest frozen backtest
  (origin 2026-07-07) vs saved closeout actuals; reuses repo metrics; reproduces
  published corporate_raw / corporate_total_recent_shape numbers as a check.
- NEW `scripts/python/forecast_validate_category_pool.py`: guardrail assertions
  (leakage / exact total-preservation / determinism — all PASS) + 11-window
  oracle-total allocation backtest (volume neutralized to isolate allocation).
- Result (July-7, real corporate+actuals): `catpool_corporate_anchor_activation`
  beats champion on every axis — SKU WAPE 1.05→0.96, coverage 0.67→0.76, box
  precision 0.86→0.89, wasted units 0.09→0.06, bias +0.01% (total preserved).
- Layer ablation (KEY finding): category reconciliation ALONE is a no-op /
  slightly hurts post-sale (1.05→1.18); ACTIVATION is the driver at the July
  season reset (→0.96) BUT is season-conditional — the Apr–Jun offline
  activation backtest shows blanket activation HURTS mid-season (WAPE 0.64→0.71,
  use 0.81→0.48). Must be gated on assortment turnover. 11-window oracle
  backtest: event-lift mix small consistent edge (7/11 wins); recentmix≈global
  confirms the identity control.
- NEW `scripts/python/forecast_validate_category_pool.py` extended: also runs a
  7-window offline ACTIVATION backtest using tracked `ax_inventory_history`
  (Apr01–Jun14) + `product_info_inbound` — no AX needed.
- Handoff written: `Docs/operations/forecast_accuracy/FORECAST_HANDOFF_2026-07-22.md`
  (entry point: what's built, data available offline now, data needed for more
  testing, big untracked-file guidance, next steps). Wired into
  FORECAST_CURRENT_STATE.md Reading Order and TOOL_MANIFEST.md.
- NEW frozen July 21–Aug 3 shadow built alongside (not modifying) the Aug 4 model:
  `Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/`
  (165,008 units, corporate total preserved). Evaluable Aug 4.
- Docs: `Docs/operations/forecast_accuracy/FORECAST_MODEL_PROPOSALS_2026-07-22.md`
  and `FORECAST_MODEL_VALIDATION_2026-07-22.md` (explains the why for next LLM).

## Direction review + multi-window backtest (added, direction-review session)
NOTE: the v1 claims in this block (regime-rescue headline, "143/146") were
CORRECTED by the peer-review + contract-repair (see "peer review + contract
repair (v2)" below). Read v2 as authoritative.
- KEY UNLOCK: the "wait 2 weeks -> 1 noisy window -> no conclusion" loop was NOT
  a data gap. The repo already stores the historical corporate uploads
  (`Output/ForecastAccuracy/history/parquet/forecast_sku_day.parquet`: ~157
  snapshots / ~152 ForecastStartDate origins, per-SKU per-day, 2022-08->2026-06)
  AND matching SKU/day actuals (`direct_pick_history/parquet/*`). The corporate
  anchor can be replayed offline at every historical origin. The July-22 note's
  claim of "only one window" is superseded.
- NEW `scripts/python/forecast_multiwindow_corporate_backtest.py`: replays the
  frozen corporate vintage at every origin (earliest upload per start date),
  reuses production `build_candidates`/`load_history`/`score_candidate`
  (harness, not a re-impl), scores corporate_raw / recent_shape /
  catpool_corporate_anchor(+activation) on real actuals. Emits summary.csv,
  by_year.csv, by_regime.csv, per_window.csv, leaderboard.md.
- FIRST FULL RUN (146 windows, 2023-2026), see
  `Output/ForecastAccuracy/handoff_eval/multiwindow_corporate_backtest/RESULTS_SUMMARY.md`:
  the category-pool re-allocation is a REGIME-SPECIFIC RESCUE, not a universal
  champion. Degraded regime (corp coverage <75%, 31 windows, ~all 2026):
  catpool WAPE 1.358->0.986, coverage 40.8%->79.8%, beats raw 71% of windows.
  Healthy regime (115 windows, 2023-2025): raw is better (0.699 vs 0.992),
  catpool wins only ~7%. The single July-7 win was a degraded-regime window.
- IMPLICATION: do NOT pre-register catpool as an unconditional corporate
  replacement (would hurt ~79% of windows). Direction = a REGIME-GATED policy:
  apply catpool only when a coverage-collapse signal is present.
- Caveat: regime label uses realized (hindsight) corporate coverage; need an
  ORIGIN-SAFE collapse proxy (e.g. trailing-28d sold-unit share on SKUs
  absent/zero in the new corporate upload). Activation still only testable from
  ~2026-04 (no historical inventory) so activation==anchor on 143/146 windows.
- Docs: `Docs/operations/forecast_accuracy/FORECAST_MULTIWINDOW_CORPORATE_BACKTEST.md`.


## Working memory: peer review + contract repair (v2)
- A peer review correctly flagged the first cut as over-claimed. Fixes applied
  in `forecast_multiwindow_corporate_backtest.py`: as-of category mapping
  (snapshot-specific PGC+SGC), origin-safe window inclusion (never actuals),
  corporate-file freeze classification (only 14/146 are clean_frozen; 78
  same_day; 54 late), activation arm removed (no origin-safe inventory covers
  the archive), plus a non-overlapping subset (70) and an origin-safe gate grid.
- Corrected conclusion: aggregate unchanged (corp_raw ~0.84 best overall);
  catpool only helps in the 2026 coverage-collapse regime; as-of mapping did
  NOT move the aggregate (the old headline was a hindsight-regime artifact).
  Origin-safe deployable gate: best IN-SAMPLE threshold ~6% aggregate gain
  (0.839->0.786, 19 improved / 9 worsened) — modest, unvalidated. => exploratory
  evidence only; does NOT change the champion. catpool stays a research
  challenger per FORECAST_CURRENT_STATE.md.
- Next (repair-the-contract, not more model): time-separated gate tuning/test +
  block-bootstrap CIs on non-overlapping origins; clean-frozen-only prospective
  track; wire as-of historical inventory before ANY activation claim; add source
  hashes to per-window provenance.
- Note: platform auto-commit added .gitconfig + env/cron metadata unrelated to
  forecasting; the user should keep those out of the forecast repo.

## Out-of-time gate validation (v3, advance-the-work session)
- NEW `scripts/python/forecast_gate_validation.py`: validates the origin-safe
  regime gate (use catpool when trailing-28d demand-share-on-corporate-positive
  proxy < tau, else corporate_raw) with time-separated tuning — single time
  split, expanding leakage-free walk-forward, moving-block bootstrap CI, and a
  clean_frozen slice. Reads per_window.csv (no model rebuild). Metric =
  unit-weighted pooled SKU WAPE.
- RESULT (validated OUT OF TIME): walk-forward (106 win) pooled WAPE
  0.792 -> 0.723 (~9%), 12 improved / 0 worsened, fires on only 12/106 windows;
  single split test (46 win) 0.904 -> 0.728 (~19%); clean_frozen (14) 1.100 ->
  0.839. Moving-block bootstrap 95% CI on (gated - raw) = [-0.158, -0.006]
  (excludes 0), P(better)=0.98. Near the oracle ceiling (0.723 vs 0.693).
- KEY: 0 windows worsened in every cut (conservative + correct when it fires).
  This UPGRADES the gate from exploratory to "pre-register for a prospective
  clean-origin trial." STILL not an unconditional champion: the gain is
  concentrated in the 2026 collapse episode (one regime shift); corporate stays
  the AX baseline; the gate is a collapse-regime rescue with safe no-op elsewhere.
- Artifacts: `multiwindow_corporate_backtest/gate_validation/` (GATE_VALIDATION.md,
  walk_forward_*.csv, single_split_test.csv, bootstrap_ci.json, metadata.json).

## Known limitations
- Independent volume anchor over-forecasts (+47%): 56d run-rate is
  sale-spike-contaminated (June 21–Jul 4 inside lookback) → over-weights GIRM.
  Corporate anchor sidesteps this (preserves total). Activation validated on ONE
  origin only (inventory/inbound snapshots start 2026-06-19). Portable-facts
  only; no live-AX. Crosswalk from Jul-6 handoff ledger (98.6% unit coverage).

## Backlog / next (updated 2026-07-22)
- P0: GATE activation on a season-transition/assortment-turnover signal (blanket
  activation hurts mid-season). Fully doable offline with tracked data.
- P1: De-spike the 56d run-rate (calendar-aware baseline excl. prior in-window
  sale days) so independent Stage-1 total / lift-mix stop over-weighting GIRM.
- P1: Multi-window CORPORATE-ANCHORED backtest (needs per-origin corporate feeds)
  before promoting any champion.
- P1: Mirror full ingestion-ledger category crosswalk (open item #1).
- P2: Carton-use simulator (turn SKU-use proxy into pull-efficiency metric).
- P3: ML occurrence/residual layer on model_sku_day_panel_parts (ends 2026-06-08;
  rebuild/extend first).

## Data notes for continuation
- Offline-available now unlocks a lot: strict DirectPick (→2026-06-25),
  ax_inventory_history (daily Apr01–Jun14) for Apr–Jun activation tests,
  model_sku_day_panel_parts (→2026-06-08) for ML. See FORECAST_HANDOFF_2026-07-22.md §B.
- Big local-only gitignored files (won't push): monolithic model_sku_day_panel.parquet
  (== tracked parts, no new info), pdl_offer_rows.csv, promotions.db, raw promo
  workbooks. If a DIFFERENT big file extends inventory/actuals past 2026-06-25 it
  IS valuable — split to <90MB parts, don't push the monolith. See handoff §B.
- To extend testing forward: run sync_monitoring_forecast_artifacts.bat + save
  per-closeout actuals on the AX-connected PC. See handoff §C.
