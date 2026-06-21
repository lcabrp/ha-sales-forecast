# Operational Forecast Evaluation: Reconciliation + Slotting Scorecard

Two new, standalone scripts that consume the per-SKU/day forecast written by
`forecast_model_horizon_train.py --save-forecast`. Neither modifies an existing
script. They answer two *different* questions, so both are worth keeping:

| Script | Question it answers |
|---|---|
| `forecast_model_slotting_scorecard.py` | Does the forecast slot SKUs into the **right velocity tier**, and is the zone map **stable**? (the operational truth, beyond WAPE) |
| `forecast_model_reconcile.py` | Can **hierarchical reconciliation** (rescale SKU shape to a more reliable category/total) improve accuracy? |

## Producing the shared input

```powershell
uv run python scripts/python/forecast_model_horizon_train.py `
  --threads 8 --max-train-rows 500000 --max-iter 180 --exclude-corporate-features `
  --origin-stride 14 --keep-zero-frac 0.3 `
  --window 2026-05-12:2026-05-25:y2026_fd_a `
  --window 2026-05-26:2026-06-08:y2026_fd_b `
  --save-forecast Output/ForecastAccuracy/model/horizon_consistent/forecast_sku_day.parquet
```

Use **two or more windows** so the scorecard can measure tier churn between them.

## 1. Slotting / velocity-tier scorecard

```powershell
uv run python scripts/python/forecast_model_slotting_scorecard.py `
  --forecast Output/ForecastAccuracy/model/horizon_consistent/forecast_sku_day.parquet
```

Each forecast's 14-day total is extrapolated to 13-week-equivalent units (x6.5) and
bucketed with the inherited cutoffs (C<=20, B 21-40, A 41-100, AA>100). Outputs:

- `tier_accuracy_summary.csv` - exact & within-one-tier accuracy vs the tier implied
  by **actual** demand, plus units-weighted misallocation.
- `tier_confusion_<forecast>.csv` - full actual-vs-predicted tier matrix.
- `tier_stability.csv` - tier-change % and 3-rank (C<->AA) jump % between windows.

### Smoke result (small 2026 slice — directional)

| Forecast | Exact tier acc. | Units mis-tiered | Tier churn | 3-rank jumps |
|---|---:|---:|---:|---:|
| Recent-7 baseline | **80.4%** | **8.1%** | 30.5% | 0.6% |
| Recent-28 baseline | 74.0% | 10.8% | 28.8% | 0.6% |
| Horizon-consistent ML | 67.9% | 12.7% | 33.9% | 0.9% |
| Hybrid baseline | 62.4% | 12.0% | 42.7% | 1.2% |
| Frozen champion (old) | 54.8% | 59.6% | 64.7% | 6.4% |
| **Corporate** | **25.9%** | **79.4%** | **42.3%** | **9.9%** |
| *(actual demand churn, reference)* | - | - | *34.2%* | *2.2%* |

**Two decision-relevant findings:**
1. **Corporate is dramatically the worst at the thing slotting actually uses** -
   it mis-tiers ~3 of every 4 units and churns the zone map *more than real demand
   does* (42% vs 34%), with ~10% violent C<->AA jumps. This is a stronger argument
   for replacing it than the WAPE numbers alone.
2. **The objective changes the winner.** On raw WAPE the horizon-consistent model
   leads; on *tiering* a simple recent-demand baseline leads and is calmer. So the
   production answer may be model-by-purpose: the ML model for unit-level
   replenishment planning, a recent-demand-with-stability-controls signal for the
   velocity letter that drives zoning. The scorecard is how you make that call with
   data instead of opinion.

## 2. Hierarchical reconciliation

```powershell
uv run python scripts/python/forecast_model_reconcile.py `
  --forecast Output/ForecastAccuracy/model/horizon_consistent/forecast_sku_day.parquet `
  --base HorizonConsistentMLForecastQty --target blend --blend-alpha 0.5
```

Variants (re-scored on the same grid): `BottomUp` (unchanged), `TopDown` (match a
total target per day), `MiddleOut` (match category totals per day, preserving the
model's in-category SKU shape). Target can be `corporate`, `recent7`, `recent28`,
or a `blend`.

### Smoke result (directional)

`BottomUp` (the raw horizon model, aggregate bias only -1.8%) was best; reconciling
toward the corporate or recent aggregate **did not help** here. That is the correct,
honest outcome: reconciliation only helps when the aggregate target is genuinely
more reliable than the model's own aggregate, and on this slice corporate's total is
biased ~+14% high. The value of the script is letting you **test** that on the full
panel rather than assume it - if a fuller run shows corporate (or a category model)
is the better aggregate, `MiddleOut` will show the gain.

## How these fit the end goal

The goal is a forecast that beats corporate **for the warehouse**, not just on a
metric. Together with the frozen-origin harness and horizon-consistent trainer, the
recommended pipeline is:

1. Train the horizon-consistent model (`forecast_model_horizon_train.py`).
2. Confirm the honest WAPE win vs corporate (`forecast_model_frozen_origin_eval.py`).
3. Confirm the **operational** win - tier accuracy & stability
   (`forecast_model_slotting_scorecard.py`).
4. Optionally tighten aggregates (`forecast_model_reconcile.py`).
5. Run forward in shadow alongside corporate before any AX change.
