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

This repo does not own:

- AX forward-demand ingestion production runs
- warehouse zone-map painting/allocation
- daily layout monitoring after map cutover
- monitoring history and SlotTier SCD maintenance, which live in `ha-kydc-monitoring`

## Layout

```text
scripts/python/                 Forecast scripts and shared local helpers
scripts/sql/                    Forecast-related SQL deployment/reference scripts
Docs/operations/forecast_accuracy/  Current contract, data map, runbooks, and evidence
Source/Planner/                 Planner source workbooks retained when portable
Source/Promotions/              Promotion sources; oversized workbooks stay local
Output/ForecastAccuracy/        Forecast facts and evaluation outputs governed by the portable-artifact contract
scratch/                        One-off forecast investigations
```

Start with the current forecast contract and decision state:
[Docs/operations/forecast_accuracy/FORECAST_CURRENT_STATE.md](Docs/operations/forecast_accuracy/FORECAST_CURRENT_STATE.md).

Latest completed 14-day evaluation:
[Docs/operations/forecast_accuracy/FORECAST_CLOSEOUT_2026-08-04_TO_2026-08-17.md](Docs/operations/forecast_accuracy/FORECAST_CLOSEOUT_2026-08-04_TO_2026-08-17.md).
It records the original corporate-vintage closeout, the actual AX weekly
overlay and its full-day operational cutoff, and the live-AX/monitoring
reconciliation. The [July 21-August 3 closeout](Docs/operations/forecast_accuracy/FORECAST_CLOSEOUT_2026-07-21_TO_2026-08-03.md)
remains the last prospective corporate-versus-recent-shape contest.

Cross-repo forecast data map:
[Docs/operations/forecast_accuracy/FORECAST_DATA_LANDSCAPE_2026-07-20.md](Docs/operations/forecast_accuracy/FORECAST_DATA_LANDSCAPE_2026-07-20.md).
Use it to locate DirectPick, category, promotion, inventory, inbound, and
monitoring facts after the split, and to understand the category-total ->
current-SKU allocation direction.

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

Product Info parsing, SKU-ledger maintenance, SharePoint download, SlotTier
classification, and AX-shaped output generation live only in
`ha-ingestion-pipeline`. This repo consumes its dated outputs and ledgers; it
does not carry or import a second ingestion implementation. Upload-facing
validation must run in the ingestion repo.

## Tracked Artifact Policy

This repo should track useful forecast artifacts when reasonable so future work does not have to rebuild every dataset — especially when switching PCs. The practical per-file ceiling is **90 MB** (GitHub hard limit 100 MB). Prefer several smaller files over one large file.

Current large-file handling:

- `Output/ForecastAccuracy/model/model_sku_day_panel.parquet` is local-only because it is about 221 MB.
- The default portable replacement is `Output/ForecastAccuracy/model/model_sku_day_panel_parts/`, split into Parquet parts with `manifest.json`; model scripts read this directory by default.
- `Output/ForecastAccuracy/promotions/pdl_offer_rows.csv` is local-only because it is about 122 MB; the smaller Parquet/sample promotion artifacts are tracked instead.
- `Output/ForecastAccuracy/promotions/promotions.db` stays local when oversized/regenerable; other compact SQLite under `Output/ForecastAccuracy/` (e.g. handoff `sku_ledger.db`) may be tracked when under 90 MB.
- Agents pushing for multi-PC continuity: see `AGENTS.md` and `Docs/operations/forecast_accuracy/FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md`.

