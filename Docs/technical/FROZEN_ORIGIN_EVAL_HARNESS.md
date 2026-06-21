# Frozen-Origin / Recursive Forecast Evaluation Harness

**Script:** `scripts/python/forecast_model_frozen_origin_eval.py`
**Status:** new, standalone. Does **not** modify any existing script; it imports
`forecast_model_train.py` and `forecast_model_compare_sklearn.py` read-only.

## Why it exists

The existing rolling-origin scoreboard scores every day of a holdout window using
the panel's pre-computed demand-lag features (`SoldUnitsLag1`, `SoldUnitsRolling7`,
`ItemColorSoldUnitsLag1`, ...). Those lags are derived from the panel's own
actuals, so when the model scores day *D* inside the holdout it can read actual
sold units from days *D-1, D-7, ...* that also fall **inside** the holdout window.

A real 14-day Forward Replenishment forecast is frozen at `ForecastStartDate` and
must predict FD2..FD14 **without** ever seeing FD1..FD13 actuals. So the current
scoreboard scores the model as a ~1-day-ahead nowcast while comparing it against a
true 14-day-ahead corporate forecast. That is the apples-to-oranges gap that makes
"we beat corporate" look bigger than it is.

This harness removes the leak so the model and corporate are scored at the **same
14-day-ahead horizon**.

## Modes

| Mode | What it does | Use |
|---|---|---|
| `frozen` | Every leak-prone feature (own-demand lags, family lags, inventory, warehouse-supply, inbound) is taken at the **origin** date and held constant across FD1..FD14. Calendar advances per day; promo-calendar keeps its legitimately-known future values. | The honest headline comparison. |
| `recursive` | Same as `frozen`, but `SoldUnits` autoregressive features are rebuilt each day from a per-SKU buffer seeded with actuals up to the origin, then extended with the **model's own predictions**. | Closer emulation of true multi-step forecasting. |
| `leaky` | Scores exactly like the existing scoreboard (in-window lags). | Diagnostic only — shows the size of the leakage gap. |

Promotion-calendar, calendar, and static product-attribute features are **not**
frozen, because a genuine forecast legitimately knows them for future dates.

## What it reports (per window)

- `frozen_origin_scoreboard.csv` — mean WAPE / bias per forecast across windows.
- `frozen_origin_window_aggregate.csv` — WAPE / bias / units per forecast per window.
- `frozen_origin_fd_day_detail.csv` — WAPE / bias broken out by **FD-day (1..14)**.
- `frozen_origin_coverage.csv` — sold-unit coverage and zero-forecast sold %.
- `frozen_origin_metadata.json` — run configuration and method notes.

Forecasts compared: the model mode(s) plus the existing baselines
(`CorporateBaselineQty`, `Recent7BaselineQty`, `Recent28BaselineQty`, `HybridBaselineQty`).

## Run it

```powershell
uv run python scripts/python/forecast_model_frozen_origin_eval.py `
  --threads 8 --max-train-rows 500000 --max-iter 180 `
  --exclude-corporate-features `
  --modes frozen recursive leaky `
  --window 2026-05-12:2026-05-25:y2026_fd_a `
  --window 2026-05-26:2026-06-08:y2026_fd_b
```

Window format is `FD_START:FD_END:LABEL`; the origin is `FD_START` minus one day.
The model is trained strictly on panel rows **before** `FD_START`.

## How to read the output

1. **Compare `leaky` vs `frozen`/`recursive` aggregate WAPE.** The difference is the
   leakage premium your current scoreboard is crediting to the model.
2. **Read `frozen_origin_fd_day_detail.csv` by FD-day.** A `leaky` forecast stays
   flat across FD1..FD14 (it cheats every day). A `frozen` forecast should be best
   at FD1 and decay toward FD14 — that decay is the real forward-forecast signature.
3. **Then compare `frozen` against `CorporateBaselineQty` at equal horizon.** This
   is the only fair "did we beat corporate?" number.
4. **Keep coverage separate from WAPE.** Corporate's biggest weakness is
   `ZeroForecastSoldPct` (SKUs that sold but had no forecast), which is an
   upload/coverage failure, not a unit-accuracy comparison.

## Smoke-test observation (small training slice — directional, not definitive)

On a single 14-day window (`2026-05-26..2026-06-08`), training only on
`2026-04-15` onward with reduced settings, the leakage gap was large and clear:

| Forecast | Aggregate WAPE |
|---|---:|
| Leaky ML (in-window lags) | ~0.53 |
| Recent-7 baseline | ~0.79 |
| Frozen-origin ML | ~1.26 |
| Recursive ML | ~1.24 |
| Corporate baseline | ~1.28 |

FD-day detail showed the frozen model winning FD1 (~0.35 vs corporate ~1.04) but
**degrading past corporate by FD8..FD14**, while the leaky model stayed flat at
~0.4-0.7 across all days.

> **Important caveat & deeper finding.** These absolute numbers are from a
> deliberately tiny training slice for a fast end-to-end validation; run on the
> full panel for real figures. But the *direction* is robust and surfaces a second
> issue beyond evaluation: the champion was **trained** on leaky in-window lags, so
> it over-relies on `Lag1` and extrapolates a single recent value flat across the
> horizon when that lag is frozen. An honest evaluation is step one; the natural
> follow-up is **horizon-consistent training** (build training features at the same
> forward horizon you score at) so the model stops depending on information it will
> not have in production.
