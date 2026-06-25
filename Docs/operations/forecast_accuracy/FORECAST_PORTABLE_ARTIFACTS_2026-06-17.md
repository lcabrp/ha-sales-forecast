# Forecast Portable Artifacts - 2026-06-17

This note records the current portability contract for the forecast-replacement
artifacts in `ha-sales-forecast`. The goal is to keep as much useful forecast
data as possible in Git while avoiding GitHub's `100 MB` single-file limit and
keeping regenerable bulky artifacts local.

Extraction update, 2026-06-25: monitoring, market-basket, zone-map generation,
and daily layout-compliance artifacts belong to sibling repos unless they are
copied here as compact forecast facts with a documented forecast use.

## Size Audit

Checked on 2026-06-17:

```powershell
# Tracked working-tree files over 100 MB
$limit=100MB
git ls-files | ForEach-Object {
  if (Test-Path -LiteralPath $_ -PathType Leaf) {
    $item=Get-Item -LiteralPath $_
    if ($item.Length -ge $limit) {
      [pscustomobject]@{SizeMB=[math]::Round($item.Length/1MB,2); Path=$_}
    }
  }
}

# Untracked, not-ignored files over 100 MB
$files=git ls-files -o --exclude-standard
foreach ($f in $files) {
  if (Test-Path -LiteralPath $f -PathType Leaf) {
    $item=Get-Item -LiteralPath $f
    if ($item.Length -ge $limit) {
      [pscustomobject]@{SizeMB=[math]::Round($item.Length/1MB,2); Path=$f}
    }
  }
}

# Tracked Git blob objects over 100 MB
git ls-files -s |
  ForEach-Object { ($_ -split '\s+')[1] } |
  git cat-file --batch-check='%(objectname) %(objectsize)'
```

Result:

- no tracked working-tree file over `100 MB`;
- no untracked/not-ignored file over `100 MB`;
- no tracked Git blob over `100 MB`.

Largest tracked portable artifacts at the time of the audit:

| Artifact | Approx size | Keep tracked? | Reason |
| --- | ---: | --- | --- |
| `Output/ForecastAccuracy/sales_orders/sales_order_sku_day.parquet` | `68.4 MB` | Yes | Core SKU/day demand fact, expensive to rebuild |
| `Output/ForecastAccuracy/history/parquet/forecast_sku_day.parquet` | `28.3 MB` | Yes | Historical corporate forecast fact |
| `Output/ForecastAccuracy/inventory/inventory_sku_day.parquet` | `27.2 MB` | Yes | Forecast inventory feature fact |

Largest untracked/not-ignored candidates at the time of the audit:

| Artifact | Approx size | Recommendation |
| --- | ---: | --- |
| `Output/ForecastAccuracy/inventory/ax_inventory_history_sku_day.parquet` | `27.3 MB` | Safe to track; useful model input |
| `Output/Ingestion/FwdDemandCSV_2026-06-16.csv` | `5.5 MB` | Safe to track; confirmed AX upload |
| `Source/Planner/2024 Planner.xlsx` through `2026 Planner.xlsx` | `1.9-2.5 MB` | Safe to track if the team is comfortable storing Planner workbooks |

## Deliberately Local Or Ignored

These files are over `100 MB` locally and should stay ignored:

| Artifact | Approx size | Why local |
| --- | ---: | --- |
| `Output/ForecastAccuracy/model/model_sku_day_panel.parquet` | `221 MB` | Monolithic model panel; split monthly parts are the portable form |
| `tmp/forecast_history_smoke_20260601_v2.db` | `183 MB` | Temporary smoke-test database |
| `.venv/Lib/site-packages/xgboost/lib/xgboost.dll` | `137 MB` | Environment dependency, rebuilt by `uv` |

## Portable Data Contract

Track these when under `100 MB`:

- confirmed AX forward-demand CSV snapshots only when promoted under this repo's
  forecast-artifact folders;
- compact sales, promotion, inbound, inventory, warehouse-supply, reservation,
  and Planner Parquet/CSV/JSON facts under `Output/ForecastAccuracy/`;
- Planner extracted totals and snapshots under
  `Output/ForecastAccuracy/planner/`;
- source and script files that rebuild or explain the artifacts.

Keep local by default:

- raw source Excel workbooks in `Source/*.xlsx`;
- raw promotion workbooks in `Source/Promotions/*.xlsx`; keep
  `Source/Promotions/.gitkeep` tracked so the local drop folder exists after
  clone;
- the monolithic model panel `model_sku_day_panel.parquet`;
- raw replacement candidate package folders under
  `Output/ForecastAccuracy/replacement_contract/`;
- local SQLite convenience DBs;
- raw allocation-link facts with sales-order identifiers.

Replacement candidate package folders are intentionally ignored for now even
though the current files are below `100 MB`. They contain generated workbook
clones and ingestion round-trip folders that can grow quickly. The safer
portable contract is to track the scripts, source facts, Planner totals,
confirmed AX snapshots, and candidate comparison summaries, then regenerate a
candidate package when needed.

## Rebuild Commands

Recreate the monolithic model panel from tracked monthly parts:

```powershell
uv run python scripts/python/forecast_model_split_panel.py --combine
```

Rebuild the panel from source facts if monthly parts are missing:

```powershell
uv run python scripts/python/forecast_model_panel.py --workers 8
uv run python scripts/python/forecast_model_split_panel.py
```

Refresh AX saved inventory history:

```powershell
uv run python scripts/python/forecast_inventory_history.py
```

Extract Planner daily totals and preserve a timestamped 2026 snapshot:

```powershell
uv run python scripts/python/forecast_planner_extract.py --year 2024
uv run python scripts/python/forecast_planner_extract.py --year 2025
uv run python scripts/python/forecast_planner_extract.py --year 2026 --snapshot
```

Merge newly downloaded PDL/coupon workbooks into the portable promotion parquet
store:

```powershell
uv run python scripts/python/extract_promotions.py
uv run python scripts/python/forecast_promo_sku_features.py
```

`extract_promotions.py` preserves existing extracted promotion history by
default and replaces rows only for matching workbook filenames found in
`Source/Promotions/`. Use `--replace-existing` only for an intentional full
rebuild from a complete source workbook folder.

Rebuild a Planner-scaled corporate candidate from the confirmed June 16 upload:

```powershell
uv run python scripts/python/forecast_planner_scale_forward_demand.py `
  --input-csv Output/Ingestion/FwdDemandCSV_2026-06-16.csv `
  --candidate-id planner_scaled_corporate_100_2026-06-16_v2 `
  --planner-scale 1.0
```

Rebuild the current velocity-policy shadow panel after confirmed forecast
uploads:

```powershell
uv run python scripts/python/forecast_direct_pick_history.py --overwrite
uv run python scratch/backtest_velocity_stability_controls.py --overwrite
```

## Current Caveat

At the time of this audit, several previously tracked model candidate outputs
and monthly model-panel parts are marked deleted in the working tree. They are
all below the `100 MB` limit and are useful for avoiding rebuilds. Do not
commit those deletions unless the team intentionally decides that model output
artifacts should be local-only. If they are needed on this machine again, either
restore them from Git or rebuild with the commands above.
