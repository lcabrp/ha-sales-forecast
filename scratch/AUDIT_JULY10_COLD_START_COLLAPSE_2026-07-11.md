# July 10 cold-start collapse — audit findings (2026-07-11)

Offline audit on the non-AX PC. Script: `scratch/audit_july10_cold_start_collapse.py`.
Source: `Output/ForecastAccuracy/pipeline_runs/2026-07-10_corporate_2026-07-06/backtest/`.

## Verdict

Not a mysterious training regression in the sense of “volume brake broke.”
The July 10 cold-start pipeline produced a **near-dead quantile ML volume signal**, then
a **10% recent fallback** that cannot recover scale, then **upper-bound caps** that
cannot fix under-forecasting. Corporate blending restores volume and proves the
shape/volume split in the handoff.

## Numbers

| Candidate | Forecast units | Sold units | Bias | Coverage | Avg SKUs |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pure ML `min_20` (q=0.35) | 44,281 | 6,174,617 | −99.3% | 0.7% | 12 |
| Hybrid + recent_w0.1 | 938,333 | 6,174,617 | −84.8% | 96.6% | 16,471 |
| Same + any cap (0.85–1.25) | 938,333 | identical | identical | identical | identical |
| Same + corporate blend | 6,509,383 | 6,174,617 | +5.4% | 92.5% | 13,904 |

Contrast — June absolute-log hybrid (healthy independent volume):

| Candidate | Forecast units | Sold units | Bias | Coverage |
| --- | ---: | ---: | ---: | ---: |
| ML `hgb_absolute_log` min_20 | 6,916,530 | 7,334,496 | −5.7% | 59.7% |
| Hybrid + recent_w0.25 | 8,191,349 | 7,334,496 | +11.7% | 96.6% |

Per-window: pure ML median forecast = **0**; hybrid median ≈ 28.7k vs sold ≈ 200k+;
median ML share of hybrid volume ≈ **0**.

## Root causes (ordered)

1. **Quantile cold-start ML collapsed to near-zero volume**
   - Default quantile `0.35` + cold-start future rows → ~12 SKUs / window above the
     min-20 guardrail; many windows forecast 0 ML units.
   - This is not the June absolute-log model. Different family, different scale.

2. **Hybrid fallback weight is a fraction of recent, not a full recent baseline**
   - `combine_with_recent_fallback` multiplies non-ML SKUs by `fallback_weight`
     (0.05 / 0.10). With ML ≈ 0, hybrid ≈ 10% of recent → ~15% of sold.
   - High sold-unit coverage is real (SKU presence), but magnitude is wrong.

3. **Caps are upper bounds only → identical “variants”**
   - `apply_recent_volume_cap` scales down only when
     `forecast_units > recent_units * cap_multiple`.
   - Under-forecast never engages the brake. All cap labels are duplicates.

4. **Recency brake cannot help and is mis-wired for this score file**
   - Brake chooses among identical cap rows; selection is meaningless.
   - Pipeline asks for floor candidate `recent_no_ml_no_promo_floor`, which is
     **absent** from the cold-start window scores (only produced by the older
     no-ML / absolute-log backtest). That is a lookup/contract bug in
     `forecast_pipeline_runner.py` step 2.
   - No `recency_brake/` outputs under the dated July 10 run folder.

5. **`_blended` is corporate-total rescue, not an independent win**
   - `blend_with_corporate` restores aggregate volume (+5.4% bias).
   - That supports the handoff’s volume-vs-allocation split; it does not make the
     raw cold-start hybrid upload-ready.

## Forward candidate

`forward_tests/.../raw_hybrid_cap085` used the collapsed raw hybrid with an inert
0.85 cap. Do not upload or promote.

## Offline artifact check (this PC)

Present: actuals parquet, model panel parts manifest, corporate snapshot
`20260617_173252`, DirectPick parquet, planner 2026 totals, July 10 backtest,
Product Info `2026-07-06.xlsx`, confirmed `FwdDemandCSV_2026-07-11`, monitoring
data contract.

CPU: use **8 threads** (half of 16 logical) for any later ML work.

## Implications for next work

- Do **not** retrain cold-start / retune caps / re-run the July pipeline as-is.
- Treat independent total (`independent_total_model_shape`) as diagnostic until a
  model with plausible volume exists again (June absolute-log scale, or recent
  baseline, or corporate total).
- Highest-leverage comparison: hold total fixed, vary SKU allocation:
  1. `corporate_raw`
  2. `corporate_total_recent_shape`
  3. `corporate_total_model_shape` (shape from a credible model, total from corporate)
  4. `independent_total_model_shape` (diagnostic)
- Prefer the June absolute-log / recent-no-ML artifacts or a cheap allocator over
  the July 10 quantile cold-start outputs.

## Follow-up: same-contract 3-candidate table (2026-07-11)

Script: `scratch/handoff_volume_vs_allocation_comparison.py`  
Outputs: `Output/ForecastAccuracy/handoff_eval/volume_vs_allocation_2026-07-11/`  
Contract: 26 windows `2025-12-09` .. `2026-06-02`, 6,174,617 sold units.
`corporate_total_recent_shape` = corporate 14-day total × recent SKU shares (strict).

| Candidate | Forecast | WAPE | Bias | Coverage | Zero-forecast sold |
| --- | ---: | ---: | ---: | ---: | ---: |
| `corporate_raw` | 6.40M | 130.3% | +3.7% | 41.0% | 56.6% |
| `corporate_total_recent_shape` | 6.41M | **87.5%** | +3.9% | **95.9%** | **3.3%** |
| `recent_no_ml_no_promo_floor` | 9.00M | 115.8% | +45.8% | 96.6% | 2.8% |

Holding corporate total and reallocating by recent shape beats corporate SKU
allocation on WAPE and coverage without changing aggregate bias much. Independent
recent total overshoots (+46% bias). `corporate_total_model_shape` still pending
(needs June ML SKU shapes).
