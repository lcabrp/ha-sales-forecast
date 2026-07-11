# Volume vs Allocation Findings - 2026-07-11

Offline session on the non-AX PC. Continues
`HANDOFF_OFFLINE_MODEL_EVALUATION_2026-07-11.md`.

## Verdict

We were optimizing the wrong problem. The productive path is:

1. Treat **corporate aggregate volume as an informed soft reference** (good lately,
   not perfect).
2. Compete on **SKU allocation** at a shared total.
3. Keep free-total independent forecasts as diagnostics, not as the default
   production bet until they beat corporate volume *and* allocation.

Current lead challenger: **`corporate_total_recent_shape`**  
(corporate 14-day / daily totals × future-safe recent DirectPick SKU shares).

Do **not** resume July quantile cold-start / cap-grid / recency-brake tuning.

## Why The Prior Path Was Wrong

| Wrong path | What happened | Lesson |
| --- | --- | --- |
| End-to-end independent ML “replace corporate” | Mixed volume error with allocation error; headline WAPE wins were not production-ready | Separate volume vs allocation |
| July 10 quantile cold-start pipeline | ML nearly dead (~44k units / 26 windows); hybrid only added 10% recent fallback → ~938k vs 6.2M sold; caps identical because caps are **upper bounds only** | Collapse was contract/scale failure, not “need a harder brake” |
| Recency brake on identical under-forecast caps | Selection among duplicate rows; floor candidate `recent_no_ml_no_promo_floor` missing from cold-start scores | Do not trust brake outputs from collapsed runs |
| Promo / YoY overlays as volume rescue | Can fix aggregate bias while wrecking SKU WAPE | Overlays are diagnostics for volume, not allocation winners |
| More model families / knobs | Same hybrid stack with new suffixes week after week | Freeze one evaluation contract first |

Detailed cold-start autopsy:
`scratch/AUDIT_JULY10_COLD_START_COLLAPSE_2026-07-11.md`.

## What We Measured Today

### Corporate total accuracy (offline, as-of books)

Actuals through **2026-07-09**. Recent corporate books from confirmed
`FwdDemandCSV_*` + Product Info `2026-07-06`.

| Window | Actual | Corporate | Bias |
| --- | ---: | ---: | ---: |
| Last 7d | 187k | 193k | **+3.3%** |
| Last 14d | 428k | 440k | **+2.9%** |
| Last 30d | 746k | 805k | **+7.9%** |

Daily total WAPE still ~34–37% (timing). July 4 heavily over-forecast; July 7–9
under-forecast. Script: `scratch/corporate_vs_actual_recent_windows.py`.

### 26-window volume-vs-allocation contract

Windows `2025-12-09` .. `2026-06-02`, **6,174,617** sold units.
Outputs: `Output/ForecastAccuracy/handoff_eval/volume_vs_allocation_model_shape_2026-07-11/`.

| Candidate | WAPE | Bias | Coverage |
| --- | ---: | ---: | ---: |
| `corporate_raw` | 130% | +4% | 41% |
| `corporate_total_recent_shape` | **88%** | +4% | **96%** |
| `corporate_total_model_shape` (`hgb_absolute_log` shape) | 96% | +4% | 59% |
| `independent_total_model_shape` | 94% | −1% | 59% |
| `recent_no_ml_no_promo_floor` | 116% | **+46%** | 97% |

Holding corporate total and reallocating by **recent** shape beats corporate SKU
allocation and ML-shape allocation on this contract. Free recent volume overshoots.

### Hanna Sale holdout (origin ~2026-06-18, 14d through 2026-07-01)

No exact corporate start `2026-06-18` in available books; used Product Info
`2026-06-15` (start **2026-06-16**). Document that limitation.

| Candidate | WAPE | Bias | Coverage |
| --- | ---: | ---: | ---: |
| `corporate_raw` | 136% | +3% | 67% |
| `corporate_total_recent_shape` | 79% | +3% | 99% |
| `independent_recent_shape` | **75%** | **−19%** | 99% |

Recent shape wins allocation; free recent under-ships sale volume.

### Forward shadow origin 2026-07-07 → 2026-07-20

Frozen competitors under `Output/ForecastAccuracy/handoff_eval/`:

| Artifact | Role | ~14d units |
| --- | --- | ---: |
| `forward_2026-07-07_challenger/corporate_raw_fd14.csv` | Corporate comparator | 204,654 |
| `forward_2026-07-07_challenger/corporate_total_recent_shape_fd14.csv` | **Lead challenger** | 193,565 |
| `forward_2026-07-07_challenger/independent_recent_shape_fd14.csv` | Free-total diagnostic | 305,454 |
| `independent_hybrid_absolute_log_2026-07-07/` | Independent ML+hybrid BRG package (ingestion roundtrip **pass**) | ~157,409 |

**Partial score Jul 7–9 only** (actuals end 2026-07-09): all under-forecast that
slice; `corporate_total_recent_shape` best WAPE; hybrid weakest (−58% bias).
Full decision needs actuals through **2026-07-20**.

## Candidate Definitions (Frozen)

```text
corporate_raw
  Exact corporate SKU FD1-FD14 from Product Info / confirmed FwdDemandCSV.

corporate_total_recent_shape
  For each FD day (or 14d total in retrospective tables):
  hold corporate day/total units fixed; allocate by recent DirectPick SKU shares.
  Lookback = DEFAULT_LOOKBACK_DAYS (56).

corporate_total_model_shape
  Same total lock; allocate by hgb_absolute_log raw min-20 SKU shares.
  Diagnostic / second allocator — lost to recent shape on the 26-window contract.

independent_recent_shape / independent_total_model_shape / independent hybrid
  Free total. Useful diagnostics. Not the default production challenger until
  volume stays plausible on sale + normal windows.
```

## Do / Don't Next Session

### Do first

1. Refresh actuals through **2026-07-20** (needs AX-connected PC or copied parquet).
2. Re-score the frozen Jul 7–20 forward package:
   - `corporate_raw`
   - `corporate_total_recent_shape`
   - `independent_recent_shape`
   - `independent_hybrid_absolute_log_2026-07-07`
3. If `corporate_total_recent_shape` still wins allocation at similar volume, freeze
   it as the shadow challenger and produce a BRG workbook via contract helpers
   (do not upload until production gate in the offline handoff is met).
4. Optionally re-score sale with an exact-start corporate book if one appears.

### Do not

- Re-run July quantile cold-start / `forecast_pipeline_runner` as-is.
- Add new ML families before the Jul 7–20 full-window score.
- Treat partial Jul 7–9 scores as a winner decision.
- Use `ha-zoning-slotting` as the active execution root.
- Select cost-scorecard winners without approved weights.

## Scripts To Reuse

| Script | Purpose |
| --- | --- |
| `scratch/audit_july10_cold_start_collapse.py` | Prove cold-start unit collapse / identical caps |
| `scratch/corporate_vs_actual_recent_windows.py` | Corporate daily/window total bias |
| `scratch/handoff_volume_vs_allocation_comparison.py` | No-ML allocate_total_by_shape backtest |
| `scratch/handoff_corporate_total_model_shape.py` | 26-window ML shape + allocate (use `--threads 8`) |
| `scratch/handoff_advance_challenger.py` | Sale holdout + Jul 7 challenger FD14 CSVs |
| `scratch/score_hybrid_forward_partial.py` | Partial score hybrid vs challengers |
| `scripts/python/forecast_replacement_hybrid_candidate.py` | Absolute-log hybrid BRG package (**not** cold-start) |

## Threading

This PC: 16 logical CPUs → use **`--threads 8`** for ML.

## Production Gate (unchanged)

From the offline handoff: no leakage; plausible volume; better allocation than
`corporate_raw` at a common total on historical + sale + forward shadow; no unit
collapse; one untouched forward shadow completes with actuals.

Practical finish line: best credible total + demonstrably better SKU allocation
than the current corporate SKU forecast — not “replace every part of corporate.”
