# Artifact Rebuild And Restore

Current as of 2026-07-21. Read `FORECAST_CURRENT_STATE.md` for the modeling and
evaluation contract and `FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md` for Git
retention rules.

## Current Durable Facts

- Annual strict DirectPick shards and manifest:
  `Output/ForecastAccuracy/direct_pick_history/`.
- Selected corporate forecast history and historical actual mirrors:
  `Output/ForecastAccuracy/history/parquet/`.
- Promotion event/offer/SKU-day features:
  `Output/ForecastAccuracy/promotions/`.
- Current monitoring consumer facts:
  `Output/ForecastAccuracy/inventory/pickface_inventory_sku_day.parquet` and
  `Output/ForecastAccuracy/inbound/ax_open_inbound_sku_day.parquet`.
- Current completed closeout:
  `Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_closeout/`.
- Current forward shadow:
  `Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/`.

The split legacy model panel remains under
`Output/ForecastAccuracy/model/model_sku_day_panel_parts/`, but it ends on
2026-06-08 and is not current forecast evidence.

## Evaluation Actuals

Audit source coverage before scoring:

```powershell
uv run python scripts/python/forecast_actuals_source_audit.py `
  --start-date YYYY-MM-DD --through-date YYYY-MM-DD
```

Source order:

1. monitoring for completed-day availability and aggregate Pick totals;
2. a current canonical monitoring-scope SKU/day fact;
3. a saved evaluation-local monitoring-scope SKU/day fact;
4. the narrow read-only live AX fallback.

The historical `actual_sku_day_modified.parquet` is broader than the strict
monitoring target and ends 2026-07-09. Do not silently substitute it for a
current operational closeout.

When AX detail is required:

```powershell
uv run python scripts/python/forecast_window_compare.py `
  --start-date YYYY-MM-DD --through-date YYYY-MM-DD `
  --daily-forecast <frozen-candidates.parquet> `
  --live-ax --output-dir <closeout-output-dir>
```

The scorer saves the exact SKU/day actual and reconciles it to monitoring.

## Refresh Commands

Monitoring consumer facts:

```powershell
uv run python scripts/python/sync_monitoring_forecast_artifacts.py
```

Promotion tail:

```powershell
uv run python scripts/python/extract_promotions.py --no-sqlite
uv run python scripts/python/forecast_promo_sku_features.py `
  --start-date YYYY-MM-DD --merge-existing
```

Historical DirectPick shards:

```powershell
uv run python scripts/python/forecast_direct_pick_history.py --overwrite
```

Historical corporate forecast facts and legacy actual mirror:

```powershell
uv run python scripts/python/forecast_history_dataset.py collect-forecasts `
  --since 2022-01-01
uv run python scripts/python/forecast_history_dataset.py collect-actuals `
  --start-date YYYY-MM-DD --end-date YYYY-MM-DD --date-field modified
uv run python scripts/python/forecast_history_dataset.py build-summaries
```

These historical actuals are useful for model research but still require an
explicit scope reconciliation before an operational closeout.

## Category Dependency

The active SKU/category ledger belongs to:

```text
../ha-ingestion-pipeline/Output/Ingestion/sku_ledger.db
```

Until a forecast-owned crosswalk is implemented, record the ledger path, hash,
and row count used by every category/event analysis. Do not use the stale
2025-starting model panel as the category map for 2022-2024 events.

The planned portable contract is:

```text
Output/ForecastAccuracy/product_attributes/sku_category_crosswalk.parquet
Output/ForecastAccuracy/product_attributes/sku_category_crosswalk_metadata.json
```

## Local-Only And Historical Items

- `Forecast_Accuracy.db` is a legacy/generated SQLite artifact, not the current
  closeout store.
- The monolithic model panel is local-only; reconstruct it from parts only for a
  specific old-model investigation.
- The June corporate Forecast DB snapshot is historical and should be offloaded
  before it is untracked.
- Old replacement packages, shadow directories, and scratch detail are not
  rebuild prerequisites once their compact score/metadata evidence is retained.

For every rebuild, report the exact source, date window, row/SKU counts, and
whether the result came from monitoring, live AX, corporate DB, Product Info,
planner workbooks, cached Parquet, or SQLite.
