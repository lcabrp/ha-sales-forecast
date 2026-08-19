# Forecast Portable Artifact Contract

Current as of 2026-08-19. Read `FORECAST_CURRENT_STATE.md` first. This document
only governs what forecast evidence should travel with the repository and what
must remain producer-owned or local.

## Rule

Work moves between PCs. Commit relevant forecast evidence when each file is
under the practical `~90 MB` ceiling and the artifact is not a secret,
regenerable duplicate, or producer-owned operational detail. GitHub rejects
single files at 100 MB.

Prefer compact Parquet/CSV/JSON/SQLite facts, manifests, hashes, and score tables
over copied workbooks, repeated roundtrip packages, or monolithic databases.
Split facts by stable partitions when a portable dataset is genuinely needed.

## Track

Keep these current, compact contracts in Git:

- `Output/ForecastAccuracy/direct_pick_history/`: annual strict DirectPick
  SKU/day shards, manifest, and summaries;
- `Output/ForecastAccuracy/product_attributes/sku_category_crosswalk.parquet`
  and `sku_category_crosswalk_manifest.json`: the compact forecast mirror of
  the ingestion-owned category ledger, including source and Parquet hashes;
- `Output/ForecastAccuracy/history/parquet/`: selected historical corporate
  forecast facts and documented historical actual mirrors;
- `Output/ForecastAccuracy/promotions/`: compact extracted promotion events,
  offer Parquet, SKU/day features, and extraction summaries;
- compact planner, sales-order, inventory, inbound, reservation, and warehouse
  supply facts that are forecast-owned and expensive to recreate;
- `Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_closeout/`;
- `Output/ForecastAccuracy/handoff_eval/forward_2026-07-21_closeout/`;
- `Output/ForecastAccuracy/handoff_eval/forward_2026-08-04_closeout/`;
- `Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/`;
- `Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/`
  as immutable late-origin diagnostic evidence, not as a frozen July 21
  contestant;
- `Output/ForecastAccuracy/forward_tests/2026-08-19_corporate_2026-08-18/`
  as the immutable one-day-late, pre-AX four-arm pack, including its exact
  origin-safe inputs and manifest;
- `Output/ForecastAccuracy/handoff_eval/multivintage_corporate_anchored_2026-08-19/`
  as the compact three-window retrospective comparison;
- score tables, candidate metadata, manifests, and source hashes required to
  reproduce a documented decision.

The July 7 closeout pack contains the exact 14-day scorecard,
monitoring-scope evaluation actual, category/SKU diagnostics, and frozen
pre-rounding-fix evidence. The August 5 closeout pack contains the July 21-
August 3 live-AX actual, monitoring reconciliation, all five scorecards, and
category/SKU evidence. These are current decision evidence and belong in Git.
The August 4-17 closeout completed on August 19 contains the original and
operational corporate vintages, their source hashes and AX cutoff evidence,
the exact live-AX actual, and the scheduled-overlap scorecards. It is current
decision evidence and belongs in Git.
The category-pool pack was generated on July 22 for the same dates. Keep it
portable so it can be scored for learning, while retaining its late-origin
diagnostic status.

## Keep Local Or Producer-Owned

Do not add these to Git:

- secrets, credentials, `.env`, authentication caches, or database tokens;
- `.venv`, caches, logs, temporary databases, and database sidecars;
- `Output/ForecastAccuracy/model/model_sku_day_panel.parquet`; use the tracked
  split parts while that stale panel is still required;
- `Output/ForecastAccuracy/promotions/pdl_offer_rows.csv`; the compact Parquet
  is the portable form;
- `Output/ForecastAccuracy/promotions/promotions.db`;
- raw source workbooks above the practical ceiling;
- generated candidate packages that duplicate an input workbook, ingestion
  roundtrip, hierarchy, and full forecast when compact metadata/scores suffice;
- monitoring-owned dated detail when the producer repo and a compact consumer
  fact already preserve the contract.

Known local-only promotion sources:

- `Source/Promotions/6.18.26 Hanna Sale PDL.xlsx` (`~95.7 MB`);
- `Source/Promotions/7.21.26 BTS & Sleep Up to 30% Off + New Markdowns.xlsm`
  (`~59.7 MB`; retained locally as a raw-source workbook);
- `Output/ForecastAccuracy/promotions/pdl_offer_rows.csv` (`~142.1 MB`).

Their compact derived promotion tables must remain portable. The July 21
workbook contains only a July 21 effective date; portability does not authorize
inventing later campaign dates.

## Current Large Legacy Exceptions

Two large tracked families require deliberate cleanup, not silent deletion:

1. `corporate_forecast/snapshots/20260617_173252/` is a unique 666 MB database
   snapshot. It is not a current forecast input and should be moved to durable
   artifact storage before being untracked. Keep its manifest and extract
   summary in this repo.
2. `model/model_sku_day_panel_parts/` is a 293 MB portable split of the current
   model panel. It ends on 2026-06-08 and is not current July evidence. Keep it
   until a future-safe replacement panel is built and verified, then retire the
   old parts as one unit.

Removing these paths from a future commit will not shrink existing Git history.
History rewriting is a separate high-risk operation and is not part of normal
artifact cleanup.

## Cross-Repo Ownership

- `ha-kydc-monitoring` owns daily monitoring, pick-face inventory detail, open
  inbound detail, confirmed forecast timelines, and `Monitoring_History.db`.
- `ha-ingestion-pipeline` owns Product Info parsing, production AX-shaped
  outputs, and the current SKU/category ledger.
- `ha-sales-forecast` may mirror compact read-only facts needed for forecast
  research; a mirror does not transfer producer ownership.

The current monitoring sync script mirrors compact and detailed inventory/inbound
files. Forecast code should consume the compact SKU/day facts and metadata. The
detailed consumer copies are cleanup candidates once the sync contract is
narrowed; do not treat them as a second producer.

## Rebuild And Refresh

Recombine the currently tracked model parts only when an old-model investigation
requires the monolith:

```powershell
uv run python scripts/python/forecast_model_split_panel.py --combine
```

Refresh monitoring mirrors:

```powershell
uv run python scripts/python/sync_monitoring_forecast_artifacts.py
```

Refresh only a bounded promotion tail and merge it into the portable feature
store:

```powershell
uv run python scripts/python/extract_promotions.py --no-sqlite
uv run python scripts/python/forecast_promo_sku_features.py `
  --start-date YYYY-MM-DD --merge-existing
```

The `.xlsm` reader uses `openpyxl`; `.xlsx` extraction uses `python-calamine`.
Do not infer a missing promotion end date.

## Before Committing Artifacts

For every new or refreshed fact, report:

- producer/source and exact path;
- query or event window;
- row and distinct-SKU counts;
- whether the result came from monitoring, live AX, corporate DB, Product Info,
  planner workbooks, cached Parquet, or SQLite;
- whether the file is immutable evidence, a rolling mirror, or regenerable
  output;
- individual file sizes and any local-only exception.

Do not preserve an obsolete experiment merely because it is under 90 MB. The
size ceiling is a safety limit, not a retention requirement.
