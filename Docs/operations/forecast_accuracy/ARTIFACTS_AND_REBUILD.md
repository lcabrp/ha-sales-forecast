# Artifact Rebuild and Restore

This repo should be usable on a fresh PC after `uv sync` and a repo download. The goal is that tracked scripts, notes, and compact portable artifacts are enough to continue work without manual reconstruction.

## Durable Artifacts

These are the main generated artifacts this repo may keep around:

- `Output/ForecastAccuracy/Forecast_Accuracy.db`
- `Output/ForecastAccuracy/model/model_sku_day_panel_parts/`
- `Output/ForecastAccuracy/promotions/` derived files
- compact forecast snapshots and planner extracts that remain below the practical GitHub size limit

## Rebuild Commands

If the forecast database is missing or needs to be recreated:

```powershell
uv run python scripts/python/forecast_accuracy.py import-forecasts
uv run python scripts/python/forecast_accuracy.py summary
```

Confirmed AX uploads now need to be copied or promoted into a forecast-owned
artifact folder before this repo imports them. `Output\Monitoring\...` is a
sibling-repo path after the extraction.

If you need the imported actuals for a date range, rerun the actuals import with the correct window documented in the forecast-accuracy runbook:

```powershell
uv run python scripts/python/forecast_accuracy.py import-actuals --start-date YYYY-MM-DD --end-date YYYY-MM-DD --date-field modified
```

If a model artifact is missing, use the scripts that produced it and the portable-partifacts notes in this folder to rebuild from tracked snapshots and Parquet parts.

## Local-Only / Ignored Items

These are expected to stay local unless a future contract explicitly says otherwise:

- large source workbooks above the practical size limit
- `Output/ForecastAccuracy/promotions.db`
- very large single-file model outputs that have a split-part replacement
- `.venv/`, caches, and transient logs

## Notes

- When a generated artifact is not tracked, the rebuild recipe should live next to the contract that produces it.
- If a file is both large and expensive to rebuild, prefer a tracked compact replacement plus a rebuild command that is documented here.
- For date-specific datasets, record the exact source window and command in the related runbook so the LLM can recover the same state later.
