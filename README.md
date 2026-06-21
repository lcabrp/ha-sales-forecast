# ha-sales-forecast

Independent sales forecast, forecast accuracy, and slotting-forecast research tooling for Hanna Andersson's Kentucky DC.

This repo was extracted from `ha-zoning-slotting` on 2026-06-19 so forecast work can evolve separately from ingestion, warehouse layout generation, and post-cutover monitoring.

## Scope

This repo owns:

- historical forecast accuracy datasets and audits
- corporate forecast database snapshots/extracts
- sales-order, direct-pick, promotion, inventory, reservation, inbound, and warehouse-supply feature datasets
- replacement forecast backtests and shadow windows
- ML and hybrid forecast candidates
- planner-based forecast comparison and scaling experiments
- velocity/SlotTier policy research when the question is forecast stability, demand quality, or replenishment-pressure criteria

This repo does not own:

- AX forward-demand ingestion production runs
- warehouse zone-map painting/allocation
- daily layout monitoring after map cutover

## Layout

```text
scripts/python/                 Forecast scripts and shared local helpers
scripts/sql/                    Forecast-related SQL deployment/reference scripts
Docs/operations/forecast_accuracy/  Forecast runbooks, reset contracts, and portable artifact notes
Docs/technical/                 Forecast and velocity-policy technical notes
Source/Planner/                 Local planner workbooks, ignored by Git
Source/Promotions/              Local promotion workbooks, ignored by Git
Output/ForecastAccuracy/        Local/generated forecast artifacts, mostly ignored by Git
scratch/                        One-off forecast investigations
```

## Setup

```powershell
uv sync
```

## Verification

For narrow edits:

```powershell
uv run ruff check scripts/python/<changed_file>.py
uv run python -m py_compile scripts/python/<changed_file>.py
```

For broader edits:

```powershell
uv run python scripts/python/repo_health_check.py --skip-map-audit
```

## Notes

Large workbooks, local databases, and generated model artifacts should stay local unless a contract document explicitly promotes a compact artifact for Git tracking.

For rebuild and restore steps, see [Docs/operations/forecast_accuracy/ARTIFACTS_AND_REBUILD.md](Docs/operations/forecast_accuracy/ARTIFACTS_AND_REBUILD.md).

## Extraction Notes

Some replacement-forecast scripts still import `ingestion_pipeline.py` to generate AX-shaped roundtrip outputs. Those compatibility modules are copied here for now so the extracted repo can run. A later cleanup can narrow that dependency into a small forecast-to-ingestion contract helper.

## Tracked Artifact Policy

This repo should track useful forecast artifacts when reasonable so future work does not have to rebuild every dataset. The practical per-file ceiling is 90 MB to stay below GitHub's hard 100 MB push limit with margin.

Current large-file handling:

- `Output/ForecastAccuracy/model/model_sku_day_panel.parquet` is local-only because it is about 221 MB.
- The default portable replacement is `Output/ForecastAccuracy/model/model_sku_day_panel_parts/`, split into Parquet parts with `manifest.json`; model scripts read this directory by default.
- `Output/ForecastAccuracy/promotions/pdl_offer_rows.csv` is local-only because it is about 122 MB; the smaller Parquet/sample promotion artifacts are tracked instead.
- `Output/ForecastAccuracy/promotions/promotions.db` and source workbooks above the 90 MB practical limit are local-only; smaller source workbooks may be tracked when useful.

