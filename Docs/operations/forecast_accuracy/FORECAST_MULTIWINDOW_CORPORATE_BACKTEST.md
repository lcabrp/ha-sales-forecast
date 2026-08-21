# Multi-Window Historical Corporate-Anchored Backtest

Added as a **direction review deliverable**. Read `FORECAST_CURRENT_STATE.md`
first; this document is additive and does not change any frozen forecast pack.

## Why this exists (the core finding of the direction review)

The project's evaluation was throttled to **one live window every two weeks**.
Each promotion decision waited for a fresh corporate upload, produced a single
noisy score, and could not reach significance — the "spinning in place" loop.

The `FORECAST_MODEL_VALIDATION_2026-07-22.md` note states, in writing, that we
*"cannot re-run the corporate-anchored candidate on many historical origins
offline (no historical corporate totals to anchor on)"* and that the July 7-20
window was *"the single window where we do have a saved corporate feed and saved
actuals."*

**That is not accurate.** The repo already stores a deep archive of the
historical corporate uploads themselves:

- `Output/ForecastAccuracy/history/parquet/forecast_sku_day.parquet`
  — **~157 corporate snapshots across ~152 distinct `ForecastStartDate`
  origins, per-SKU, per-day, 2022-08 → 2026-06** (`SnapshotId`,
  `InferredFileDate`, `SKU`, `ForecastStartDate`, `ForecastDayOffset`,
  `ForecastDate`, `ForecastQty`).
- `Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_<year>.parquet`
  — matching SKU/day DirectPick **actuals for every one of those windows**
  (2022 → 2026-07-22).

So the frozen corporate forecast **can** be replayed at every historical origin
and scored against real actuals in a single run. This harness does exactly that,
turning ~130 clean windows (2023–2026) into one leaderboard instead of one live
datapoint every fortnight.

## What it runs

Script: `scripts/python/forecast_multiwindow_corporate_backtest.py`.
It **reuses the production model code** (`forecast_model_category_pool.build_candidates`,
`load_history`, `hamilton_round`) and the closeout metric (`score_candidate`) —
it is a harness, not a re-implementation.

For each frozen corporate origin `T` (horizon `T … T+13`) it scores four
candidates, all anchored to the **exact** frozen corporate daily totals:

| Candidate | What it is |
|---|---|
| `corporate_raw` | The frozen corporate SKU/day upload, unchanged (baseline). |
| `corporate_total_recent_shape` | Corporate daily total re-split across SKUs by 56-day global recent DirectPick share. |
| `catpool_corporate_anchor` | Corporate daily total reconciled **by category**, then split within category by recent share. |
| `catpool_corporate_anchor_activation` | Same + season-transition activation layer. |

Metric: **SKU WAPE** (lower better), plus sold-unit coverage, SKU-use rate,
bias, zero-demand unit share, and win-rate vs `corporate_raw`.

## Frozen-origin discipline (how honesty is preserved)

- **Frozen corporate vintage:** for each `ForecastStartDate` we use the
  **earliest-uploaded** snapshot (`min InferredFileDate`) — the forecast a
  planner would have had at the origin, before any weekly overlay.
- **Origin-safe history:** the candidate build reads DirectPick strictly before
  `T` (`load_history` is origin-safe by construction).
- **Total preservation:** every anchored candidate preserves the corporate daily
  totals exactly (Hamilton rounding), so within a window they differ **only** in
  SKU allocation. `BiasPct` is therefore identical across anchored candidates
  and equals the corporate total-volume miss.
- **Category coverage gate:** windows where < 90% of sold units map to a
  category are skipped (this removes 2022, where the crosswalk is only ~66%
  SKU / ~84% units mapped). Override with `--min-category-coverage`.

## Important limitation — activation is only testable recently

The activation layer needs origin-safe **inventory/inbound** snapshots, which
only exist from ~2026-04 (`ax_inventory_history`, Apr–Jun) and ~2026-06-19
(pickface/inbound). For any earlier origin the activation layer has no evidence,
the turnover gate collapses to 0, and
`catpool_corporate_anchor_activation` is **identical** to
`catpool_corporate_anchor` (reported as-is, not hidden). Multi-window activation
evidence therefore remains limited to recent windows until historical inventory
is added (see next steps). The **allocation-shape** question
(`corporate_raw` vs `recent_shape` vs `catpool_corporate_anchor`) is fully
answerable across all 2023–2026 windows today — and that is the question that
was stuck on one window.

## How to run

```bash
# Full 2023-2026 sweep (default)
uv run python scripts/python/forecast_multiwindow_corporate_backtest.py

# Faster smoke test
uv run python scripts/python/forecast_multiwindow_corporate_backtest.py --limit 5 --min-category-coverage 0.0

# Include 2022 (weak crosswalk) or change knobs
uv run python scripts/python/forecast_multiwindow_corporate_backtest.py \
  --min-start 2022-01-01 --min-category-coverage 0.60 --lookback-days 56 --seasonal-years 3
```

Outputs (under `Output/ForecastAccuracy/handoff_eval/multiwindow_corporate_backtest/`):

- `leaderboard.md` — human-readable summary (**start here**)
- `summary.csv` — per-candidate aggregates + win-rate vs corporate_raw
- `per_window.csv` — every window × candidate row (the raw evidence)
- `skipped_windows.csv`, `run_metadata.json`

## Results

See `Output/ForecastAccuracy/handoff_eval/multiwindow_corporate_backtest/RESULTS_SUMMARY.md`
(**contract-repaired v2**) and the CSVs (`summary.csv`, `by_freeze_class.csv`,
`by_year.csv`, `origin_safe_gate.csv`, `non_overlapping_summary.csv`,
`per_window.csv`).

Honest headline after a peer review and contract repair: the aggregate is
unchanged (corporate_raw best overall, ~0.84 WAPE); the category-pool
re-allocation only helps in the 2026 coverage-collapse regime and hurts in
2023-2025; only **14 of 146** windows are genuinely `clean_frozen`; and an
**origin-safe** gate gives at best a modest, **in-sample**, unvalidated ~6%
aggregate improvement. This is **exploratory retrospective evidence — it does
NOT change the champion decision.** The category-pool candidate remains a
research challenger per `FORECAST_CURRENT_STATE.md`.

### Contract-repair notes (v2)

The harness now: (a) maps categories **as-of** each corporate vintage using
`forecast_sku_snapshot.parquet` (no look-ahead); (b) includes windows on an
**origin-safe** corporate-side mapping coverage, never horizon actuals;
(c) **classifies** each window `clean_frozen` / `same_day` / `late` by
corporate-file availability and records `SnapshotId`; (d) **excludes** the
activation arm (no origin-safe inventory covers the archive); (e) reports a
**non-overlapping** subset and an **origin-safe gate** grid. Verified: as-of
mapping did NOT move the aggregate, confirming the earlier headline was driven
by a hindsight regime split, not by category identity.

## Next steps (in priority order)

1. **Read the leaderboard, then split it by regime.** Add a per-year / by-
   corporate-WAPE-bucket breakdown to `summarize()` so the "helps when corporate
   coverage is bad, hurts when it's good" hypothesis is quantified, not eyeballed.
   This directly informs whether the candidate should be applied *selectively*.
2. **Unlock historical activation.** The biggest lever (activation) can only be
   tested from ~2026-04 today. If BigQuery or an AX inventory-history export can
   supply as-of pick-face/inventory snapshots for 2024–2025 season resets, split
   them into `<90MB` origin-safe Parquet parts under the inventory contract and
   the harness will score activation across many real transitions — which is
   what the current "gate is too permissive" problem needs.
3. **Add the operational-vintage variant.** This harness uses the earliest
   frozen vintage. Add an option to also anchor on the vintage *in force on each
   date* (later weekly overlay), matching the closeout docs' dual reporting.
4. **Promote on evidence, not cadence.** Once (1) shows a candidate that wins
   consistently in the regime it targets, pre-register it per
   `FORECAST_NEXT_PROSPECTIVE_TEST_*.md` and require only the final prospective
   confirmation — instead of waiting fortnights to accumulate the whole case.
5. **Then, and only then, add ML** in the narrow occurrence/residual role on top
   of the winning category-anchored base (unchanged from current guidance).
