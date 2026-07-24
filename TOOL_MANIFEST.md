# Supported Forecast Tooling

Current as of 2026-07-23. This is a routing index, not an inventory of every
Python file. Read `Docs/operations/forecast_accuracy/FORECAST_CURRENT_STATE.md`
before selecting a tool. Use `<script> --help` for exact parameters.

Scripts absent from the supported sections below are retained research or
compatibility code. Their existence does not make their outputs current, and
they must not be used to claim a champion without a new frozen evaluation.

## Current Closeout And Forward Comparison

| Script | Use |
|---|---|
| `scripts/python/forecast_actuals_source_audit.py` | Check monitoring completeness and portable SKU/day coverage before querying AX. |
| `scripts/python/forecast_window_compare.py` | Score saved daily candidates on one completed monitoring-scope window; save exact actual, score, category, daily, and SKU evidence. |
| `scripts/python/forecast_forward_recent_shape.py` | Freeze a transparent corporate-daily-total plus recent-SKU-shape shadow with exact total-preserving rounding. |
| `scripts/python/sync_monitoring_forecast_artifacts.py` | Mirror monitoring inventory/inbound artifacts into the forecast consumer. The current sync is broader than the intended compact-only future contract. |

For a completed comparison, prefer `forecast_window_compare.py` over the older
broad actual importers. Use `--live-ax` only when the source audit proves a
current monitoring-scope SKU/day fact is unavailable.

## Canonical And Historical Facts

| Script | Use | Current limitation |
|---|---|---|
| `scripts/python/forecast_direct_pick_history.py` | Build annual strict DirectPick SKU/day history and its manifest. | Saved shards currently end 2026-07-22. |
| `scripts/python/extract_category_crosswalk.py` | Mirror the ingestion-owned SKU ledger into `sku_category_crosswalk.parquet` and `sku_category_crosswalk_manifest.json`, including source/output hashes. | Current-value mirror, not an as-of/SCD category history. |
| `scripts/python/forecast_history_dataset.py` | Build selected historical corporate forecast facts and legacy actual mirrors. | Its actual contract is broader than the current monitoring closeout target. |
| `scripts/python/forecast_accuracy.py` | Rebuild/query the legacy forecast-accuracy SQLite workflow. | Not the current closeout scorer. |
| `scripts/python/forecast_sales_orders.py` | Extract order/price/discount research facts. | Orders are not fulfilled DirectPick demand. |
| `scripts/python/forecast_inventory_history.py` | Extract limited AX warehouse inventory history. | Only roughly April-June 2026; not historical stockout coverage. |
| `scripts/python/forecast_product_info_inbound.py` | Extract inbound context from Product Info workbooks. | Workbook snapshots are point-in-time inputs. |
| `scripts/python/forecast_reservation_snapshot.py` | Extract reservation research facts. | Freeze by origin; reservations are not demand. |
| `scripts/python/forecast_warehouse_supply_history.py` | Extract warehouse supply/return-work research facts. | Freeze by origin. |
| `scripts/python/forecast_corporate_db_extract.py` | Refresh an authenticated corporate Forecast DB snapshot. | The saved June 17 snapshot is historical, not current. |
| `scripts/python/forecast_planner_extract.py` | Extract planner daily/category totals from saved workbooks. | Planner vintages must be known at origin. |

## Promotions

| Script | Use |
|---|---|
| `scripts/python/extract_promotions.py` | Extract workbook/sheet, PDL, offer, coupon, and daily promotion tables. Supports `.xlsx` and `.xlsm`. |
| `scripts/python/forecast_promo_sku_features.py` | Map PDL offers to SKU/day features. Use bounded dates and `--merge-existing` for tail refreshes. |

Never infer an event end date that is absent from a source workbook. A refreshed
workbook may not be used for a historical origin unless its availability at that
origin is proven.

## Model Research — Not A Current Champion

These are supported only for an explicitly designed future-safe experiment:

| Script | Use |
|---|---|
| `scripts/python/forecast_model_panel.py` | Build the legacy feature panel. The current saved panel ends 2026-06-08 and must be rebuilt/redesigned before a new July-era ML claim. |
| `scripts/python/forecast_model_split_panel.py` | Split or recombine the legacy panel for portability. |
| `scripts/python/forecast_model_frozen_origin_eval.py` | Diagnose leakage by scoring one fixed-origin horizon. Treat old smoke outputs as historical only. |
| `scripts/python/forecast_replacement_backtest.py` | Shared normalization, actual loading, and scoring helpers used by current evaluation tooling. |

The remaining `forecast_model_*` and `forecast_replacement_*` scripts are June
experiments, quantile/cold-start variants, old reconciliation/overlay tests, or
operational scorecards. They are not part of the current execution path. Use Git
history and the script itself only when a specific provenance question requires
one; do not browse the whole family by default.

### Category-pool research candidate (2026-07-22)

New two-stage category-pool candidate. Research only; not a promoted champion.
See `Docs/operations/forecast_accuracy/FORECAST_HANDOFF_2026-07-22.md`.
The tracked July 21-August 3 output under `category_pool_shadow/` was generated
on July 22 and is late-origin diagnostic evidence, not a frozen July 21
contestant.

| Script | Use |
|---|---|
| `scripts/python/forecast_model_category_pool.py` | Build category-pool candidates (independent lift or corporate anchor; optional `--activation`). Preserves category/daily totals with largest-remainder rounding. |
| `scripts/python/forecast_backtest_category_pool.py` | Origin-safe post-close 2026-07-07 diagnostic vs saved closeout actuals; reproduces published corporate numbers as a check. |
| `scripts/python/forecast_validate_category_pool.py` | Guardrail assertions + multi-window oracle-total allocation and activation backtests (offline, no AX). |

## Shared Helpers

| Script | Status |
|---|---|
| `scripts/python/output_paths.py` | Current shared local path helper. |
| `scripts/python/sql_utils.py` | Current AX connection helper. |
| `scripts/python/forecast_schema.py` | Forecast-owned Forward Demand column names and consumed-SKU normalization; contains no workbook parsing or ingestion behavior. |

Product Info parsing, current SKU-ledger production, SharePoint acquisition,
SlotTier classification, and AX output generation belong exclusively to
`ha-ingestion-pipeline`. Forecast tooling consumes its artifacts and invokes its
CLI for upload-facing validation; there is no local ingestion compatibility
copy.

## Scratch

Everything under `scratch/` is one-off or historical investigation code. Scratch
outputs are not authoritative unless a current document explicitly promotes a
compact result. The July 7-20 closeout supersedes the June sale-lift, cold-start,
partial-score, and old champion conclusions.

## Verification

For changed Python files:

```powershell
uv run ruff check scripts/python/<changed_file>.py
uv run python -m py_compile scripts/python/<changed_file>.py
```

For documentation and artifact routing changes, verify references with `rg` and
run `git diff --check`. Do not run broad rebuilds merely to validate a document.
