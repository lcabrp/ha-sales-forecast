# Offline Forecast Model Evaluation Handoff - 2026-07-11

## Purpose

Continue forecast-model evaluation on a more powerful PC without requiring live
AX access. The immediate objective is not to create more model families. It is
to establish one honest evaluation contract, reduce the candidate set, and test
whether an independent SKU allocator improves on the corporate SKU allocation
while respecting a credible total-demand reference.

`ha-zoning-slotting` is legacy reference only. Active code and data ownership
belong to the split repositories described below. Do not run active workflows
from `ha-zoning-slotting` merely because an old path or document still points to
it.

## Portability Verdict

The work can continue offline after cloning the active sibling repositories and
copying the current local-only artifacts listed below. A clone by itself is not
yet sufficient because several July inputs are untracked working-tree files.

Live AX is not required for historical training, retrospective scoring, or an
already-frozen forward shadow. Live AX is required only to refresh actuals or
point-in-time operational facts beyond the last copied snapshot.

## Active Repository Ownership

| Repository | Forecast-evaluation role |
| --- | --- |
| `ha-sales-forecast` | Owns model research, historical backtests, promotion features, corporate comparison, candidate generation, and evaluation results. This is the working repository. |
| `ha-ingestion-pipeline` | Owns Product Info for BRG parsing and production AX-shaped forward-demand/RequiredSlots output. Consume its source workbooks; do not move model research there. |
| `ha-kydc-monitoring` | Owns confirmed production forecast-upload snapshots and daily point-in-time inventory/inbound captures. Consume these as immutable read-only facts. |
| `ha-zoning-slotting` | Legacy reference only. Use it to understand provenance or recover an omitted behavior, then implement or document that behavior in the owning split repo. |

## Data Available Without AX

### In `ha-sales-forecast`

- Tracked model panel parts and manifest under
  `Output/ForecastAccuracy/model/model_sku_day_panel_parts/`.
- Tracked DirectPick SKU/day shards for 2022-2026 under
  `Output/ForecastAccuracy/direct_pick_history/parquet/`.
- Forecast actuals through 2026-07-09 at
  `Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet`:
  1,614,433 rows, 30,224 SKUs, and 5,759,792 units for 2025-11-01 through
  2026-07-09.
- Tracked corporate Forecast DB snapshot from 2026-06-17, including base,
  promotion, markdown, inventory-adjusted, and manual-override forecast fields.
- Promotion extracts through 2026-07-20, including the rolling Hanna Sale
  workbook and July 7/July 14 events.
- Planner daily totals for 2024-2026 under `Output/ForecastAccuracy/planner/`,
  including the 2026 timestamped snapshot. The source Planner workbooks are
  local inputs under `Source/Planner/` and are not assumed to arrive from Git.
- Historical corporate forecast snapshots, replacement backtests, shadow
  forecasts, and prior scorecards.

### In `ha-kydc-monitoring`

- Confirmed production forward-demand uploads, including the 2026-07-11 upload.
- Narrow content-addressed forecast snapshots and the upload-version ledger.
- Point-in-time pickface inventory and open-inbound daily snapshots through
  2026-07-11.
- A tracked 15-month DirectPick SKU/day artifact for supplemental validation.
- Confirmed and observed SlotTier timelines for operational evaluation.

### In `ha-ingestion-pipeline`

- Historical Product Info for BRG workbooks through 2026-06-29 are tracked.
- The current `Product Info for BRG_2026-07-06.xlsx` workbook contains the
  corporate July 7-20 SKU/day forecast, product attributes, weekly forecast,
  on-hand, and inbound context.

## Local-Only Files That Must Be Transferred

Before disconnecting from the AX-connected PC, copy or intentionally commit the
following current artifacts. Preserve their repository-relative paths.

1. From `ha-ingestion-pipeline`:
   - `Source/Product Info for BRG_2026-07-06.xlsx`
   - Any newer Product Info workbook downloaded after this note.
   - The corresponding dated `Output/Ingestion/FwdDemandCSV_*.csv` that was
     actually uploaded, when it is needed to prove production provenance.
2. From `ha-kydc-monitoring`:
   - `Output/Monitoring/forecast_snapshots/confirmed_raw/FwdDemandCSV_2026-07-11_b0518891ae8c.csv`
   - `Output/Monitoring/forecast_snapshots/narrow/b0518891ae8cda5477c301ca4babeb1cc7cacebe2b21372205e7f20c6952d2ca.parquet`
     if it is not present after cloning.
   - Dated inventory snapshots for 2026-07-02 through 2026-07-11.
   - Dated inbound snapshots for 2026-07-02 through 2026-07-11.
   - The current `Output/Monitoring/exports/forecast_snapshot_versions.csv`.
   - Copy confirmed forecast CSVs needed for evaluation into
     `ha-sales-forecast/Output/ForecastAccuracy/confirmed_forecasts/` before
     importing or comparing them. This is the forecast-owned offline handoff;
     do not make the model repo depend on a sibling `Output/Monitoring` path.
3. From `ha-sales-forecast`:
   - All current uncommitted July actual, promotion, inventory/inbound mirror,
     shadow, pipeline-run, and forward-test artifacts needed to reproduce the
     current state.
   - The local promotion source workbooks if promotion extraction must be
     rebuilt rather than consumed from the tracked Parquet features.

Do not assume `git clone` transfers untracked files. Verify each required file
on the offline PC before beginning model work.

## Modeling Decision To Adopt

The corporate forecast total is an informed reference, not a naive baseline.
The corporate process has access to planned promotions, campaigns, pricing,
markdowns, inventory adjustments, manual overrides, and annual planning context.
We should not assume an independent warehouse model can reliably replace that
aggregate signal from fulfilled DirectPick history alone.

The harder and more operationally valuable problem is allocating an informed
total to current SKUs. The evaluation should therefore separate two questions:

1. **Volume:** How accurate is the corporate daily and 14-day total? Can a simple,
   future-safe adjustment improve it without destroying event awareness?
2. **Allocation:** Holding the total fixed, which method distributes units to the
   SKUs that actually sell with the lowest error and highest sold-unit coverage?

This creates a clean candidate structure:

- `corporate_raw`: corporate total and corporate SKU allocation.
- `corporate_total_recent_shape`: corporate total allocated using a future-safe
  recent-demand SKU shape.
- `corporate_total_model_shape`: corporate total allocated using one independent
  model-derived SKU shape.
- `independent_total_model_shape`: independent total and independent shape,
  retained as a diagnostic rather than presumed production winner.

Promotion/event overlays may influence the SKU shape only when the promotion was
known at forecast origin. They must not use post-origin workbook revisions.

## Honest Evaluation Contract

Use one frozen-origin harness for every candidate.

- Same forecast origin and same 14 target dates.
- Same SKU universe and actual-demand source.
- Corporate forecast frozen to the version available at origin.
- Inventory, inbound, reservations, and warehouse-supply features frozen to
  snapshots available at origin; never join a later current snapshot.
- Independent candidates must exclude corporate SKU forecast fields as model
  features. A corporate-total-constrained allocator may consume only the
  aggregate corporate total by explicit design.
- Score both daily SKU demand and 14-day SKU totals.
- Report total WAPE and bias separately from allocation quality.
- Report sold-unit coverage, zero-forecast sold units, overgenerated zero-demand
  units, category errors, promotion segments, velocity segments, and cold-start
  segments.
- Include operational cost scoring, but do not select a winner until cost weights
  are approved or sensitivity results show the same winner over a reasonable
  range.

### Sale Holdout

The completed Hanna Sale must be re-evaluated over its actual event span. The
promotion workbook shows successive windows from 2026-06-18 through 2026-07-06.
The previous frozen shadow ended on 2026-07-01 and is not a complete July 4 sale
test.

Build retrospective origins only from artifacts known at those origins. Do not
pretend the final rolling Hanna Sale workbook was fully known on 2026-06-18. If
the historical revision timing cannot be proven, score the sale at the earliest
origin supported by a preserved workbook/upload snapshot and document the
limitation.

## Current Evidence And Warnings

- Earlier 26-window results favored the raw hybrid family over corporate on WAPE
  and sold-unit coverage, but those results mixed a total-volume model with an
  allocation comparison.
- Corporate historically had low SKU coverage in the imported comparison facts,
  but its aggregate total may still contain useful business information.
- The sale-specific YoY overlay achieved near-zero aggregate bias and high
  coverage but poor SKU-level WAPE. This is evidence for separating volume from
  allocation, not evidence that the overlay is production-ready.
- The 2026-07-10 cold-start backtest produced only 938,333 forecast units against
  6,174,617 actual units and identical cap variants. Treat this as a pipeline or
  contract regression until explained.
- The pipeline runner failed in the recency-brake lookup after the backtest.
- The generated independent July 7-20 candidate was severely under-scaled and
  must not be uploaded or promoted.

## Start Here On The Offline PC

1. Clone `ha-sales-forecast`, `ha-ingestion-pipeline`, and
   `ha-kydc-monitoring`. Clone `ha-zoning-slotting` only if legacy provenance is
   useful; never use it as the active execution root.
2. Restore the local-only files above and verify their paths, sizes, dates, and
   provenance against this note and the producer metadata.
   Confirm that `Source/Planner/2024 Planner.xlsx`, `2025 Planner.xlsx`, and
   `2026 Planner.xlsx` are present if Planner total anchors are part of the
   experiment.
3. Read:
   - `README.md`
   - `Docs/operations/forecast_accuracy/FORECAST_REPLACEMENT_RESET_2026-06-15.md`
   - `Docs/operations/forecast_accuracy/FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md`
   - this handoff
   - `../ha-kydc-monitoring/Docs/operations/TST_MODEL_EVALUATION_DATA_CONTRACT.md`
4. Audit the cold-start unit collapse and identical cap variants before any new
   training. Confirm units, date windows, candidate construction, and score-file
   candidate names.
5. Build one comparison table for the four candidate structures above using the
   common frozen-origin contract. Do not add another model family during this
   phase.
6. Re-score the completed sale holdout and historical windows, then freeze one
   challenger for a new forward shadow against the confirmed corporate upload.

## Production Gate

Do not call a candidate the winner or send it to ingestion until it satisfies all
of the following:

- No leakage or as-of-date contract violations.
- Aggregate volume is plausible relative to corporate, recent demand, and known
  events.
- Better SKU allocation than `corporate_raw` at a common total, demonstrated on
  historical windows and the completed sale holdout.
- Sold-unit coverage does not regress materially.
- No unexplained unit collapse, cap no-op, or candidate-name failure.
- One untouched forward shadow completes with actuals and preserves the same
  candidate code/configuration used in retrospective evaluation.

The practical finish line is not “replace every part of corporate forecasting.”
It is “use the best credible total signal, and demonstrably allocate it to SKUs
better than the current corporate SKU forecast.”

## Session Results (same day, offline PC)

See
`Docs/operations/forecast_accuracy/HANDOFF_VOLUME_VS_ALLOCATION_FINDINGS_2026-07-11.md`
for the full write-up. Short version:

- July 10 cold-start “unit collapse” explained: near-dead quantile ML + 10%
  recent fallback + upper-bound-only caps (not a brake-tuning problem).
- Corporate totals recently ~+3% over 7–14 days; daily timing still noisy.
- Lead challenger: `corporate_total_recent_shape` on the 26-window contract and
  sale holdout (allocation), with free recent / absolute-log hybrid kept as
  independent diagnostics.
- Frozen forward package for **2026-07-07 → 2026-07-20** lives under
  `Output/ForecastAccuracy/handoff_eval/`. Full evaluation waits on actuals
  through 2026-07-20.
