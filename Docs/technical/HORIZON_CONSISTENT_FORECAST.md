# Horizon-Consistent Forecast: Train + Honest Evaluation

**Script:** `scripts/python/forecast_model_horizon_train.py`
**Status:** new, standalone. Imports `forecast_model_train.py`,
`forecast_model_compare_sklearn.py`, and `forecast_model_frozen_origin_eval.py`
read-only. Modifies nothing.

## The problem it solves

The frozen-origin harness proved the current champion is trained on in-window
demand lags and therefore over-relies on `SoldUnitsLag1`. Scored honestly (lags
frozen at the origin), it extrapolates a single recent value flat across the
horizon and degrades **past corporate** by FD8..FD14. Honest *evaluation* exposed
the issue; this script fixes the *training* so the model is good at the horizon it
is actually used on.

## How it works: direct multi-horizon training

Each training row is one `(SKU, origin O, horizon h)` triple, built so that
training and inference are structurally identical:

| Feature group | Value used | Rationale |
|---|---|---|
| Own-demand lags, family lags, inventory, supply, inbound | as of origin `O` (the panel row dated `O` already holds these as leak-free lagged values) | this is all a real forecast knows at origin |
| Calendar + promotion-calendar | for the **target** date `O + h` | legitimately known ahead of time |
| `Horizon` | the integer `h` (1..14) | lets one model represent demand decay over the horizon |
| Target | actual `SoldUnits` on `O + h` (0 if no sale) | the thing we want to predict |

Training origins are sampled every `--origin-stride` days (default 14, i.e. a
biweekly cadence). Structural-zero rows are down-sampled with `--keep-zero-frac`
(default 0.3) so the model is not swamped by zeros while still learning that most
SKU/day cells are empty. Inference then scores FD1..FD14 in a single batch with no
leakage and no recursion, because the horizon is an explicit input.

For a like-for-like check, the script also trains the **plain champion** on raw
panel rows and scores it at the same honest horizon
(`FrozenChampionMLForecastQty`). Both are compared to corporate and the
recent-demand baselines on an identical grid.

## Run it

```powershell
uv run python scripts/python/forecast_model_horizon_train.py `
  --threads 8 --max-train-rows 500000 --max-iter 180 `
  --exclude-corporate-features `
  --origin-stride 14 --keep-zero-frac 0.3 `
  --window 2026-05-12:2026-05-25:y2026_fd_a `
  --window 2026-05-26:2026-06-08:y2026_fd_b
```

Outputs (under `--output-dir`, default `Output/ForecastAccuracy/model/horizon_consistent/`):
`horizon_scoreboard.csv`, `horizon_window_aggregate.csv`,
`horizon_fd_day_detail.csv`, `horizon_coverage.csv`, `horizon_metadata.json`.

## Smoke-test result (small 2026-only slice — directional, not definitive)

Window `2026-05-26..2026-06-08`, trained on 2026 data only, 15 biweekly-ish
origins, ~73k training rows, reduced iterations:

| Forecast (honest 14-day horizon) | WAPE | Bias |
|---|---:|---:|
| **Horizon-consistent ML (new)** | **0.68** | **+0.14** |
| Recent-7 baseline | 0.79 | +0.33 |
| Recent-28 baseline | 0.83 | +0.22 |
| Hybrid baseline | 0.86 | +0.34 |
| Frozen champion (old training, honest scoring) | 1.27 | +0.88 |
| Corporate baseline | 1.28 | -0.20 |

Per-FD-day, the horizon-consistent model stays in ~0.44-1.29 across all 14 days
and beats corporate at essentially every horizon, while the old champion blows up
to 2-4x by FD8..FD14.

**Read this as direction, not a final number.** It was trained on a small
2026-only slice for a fast end-to-end check. The headline is that, at the same
horizon corporate is delivered on, a horizon-consistent model:

1. **beats corporate** (0.68 vs 1.28 WAPE here), and
2. **beats the recent-7 baseline** (the bar the model review said it must clear),
   and
3. **fixes the old champion's long-horizon collapse and over-forecast bias.**

## Recommended next steps toward a production challenger

1. Run on the **full panel** (all history) with the production settings above and
   multiple rolling windows for a stable estimate.
2. Tune `--origin-stride` (more origins = more training signal) and
   `--keep-zero-frac` against WAPE **and** bias.
3. Add **hierarchical reconciliation** (SKU -> category -> total) on top of these
   point forecasts; corporate's total is often directionally fine while its SKU
   allocation is weak.
4. Add a **velocity-tier confusion matrix** and a replenishment/zone-match
   simulation, since slotting cares about tier accuracy and stability, not only
   unit WAPE.
5. Promote to a forward **shadow** (FD1..FD14 alongside corporate, never touching
   AX) for several weeks before any production decision.
