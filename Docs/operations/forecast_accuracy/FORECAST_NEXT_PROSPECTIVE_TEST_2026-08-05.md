# Next Prospective Corporate-Anchored Test

Prepared 2026-08-05, before the next corporate forecast origin. This is a
pre-registered execution protocol, not a forecast artifact or an AX upload.
Read `FORECAST_CURRENT_STATE.md` first.

## Current Waiting State

The corporate file uploaded on 2026-08-03 has `ForecastStartDate` 2026-08-04
and covers 2026-08-04 through 2026-08-17. A later weekly corporate upload may
overlay part of that window in AX; preserve both uploads as distinct vintages.
No category-pool artifact was generated before that horizon began, so a
category-pool output built now would be a late-origin diagnostic only. Do not
generate or compare one as a prospective August 4 contestant.

On or after August 18, close the original-vintage Aug 4-17 corporate baseline
using the normal source audit and `forecast_window_compare.py`. Separately,
score the operational weekly-vintage forecast by using the Aug 3/4 upload for
the dates it was in force and the later overlay for its active dates. The
available later artifact is dated 2026-08-12 with `ForecastStartDate`
2026-08-11; determine its operational cutoff from the actual AX upload time,
not merely its planned start date. Keep the overlay-impact score separate from
the original frozen baseline: it cannot establish a winner over a challenger
that was not frozen before August 4. See
`FORECAST_OVERLAY_COMPARISON_2026-08-03_TO_2026-08-12.md` for the saved
forecast-to-forecast comparison.

## Objective Of The Next Clean-Origin Test

The first fair challenger test compares the corporate raw SKU allocation with
the already implemented corporate-anchored category-pool allocation. It keeps
the exact corporate daily totals and tests only the category/SKU allocation.
It therefore cannot repair a corporate daily-total miss; that must be assessed
separately in the closeout.

This single future horizon is the first valid prospective result for the
category-pool candidate. It is evidence, not authority to replace the
corporate forecast in AX by itself. Require at least two valid prospective
corporate-anchored horizons, with the same precision/coverage scorecard, before
promoting an operational replacement.

## Frozen Candidate Specification

The next pack must contain these named series:

| Status | Candidate | Locked definition |
|---|---|---|
| Baseline | `corporate_raw` | Raw corporate SKU/day forecast from the source `FwdDemandCSV`, preserved without redistribution. |
| Primary challenger | `catpool_corporate_anchor_activation` | `forecast_model_category_pool.py` with the corporate raw daily totals as the anchor, `--lookback-days 56`, `--seasonal-years 3`, `--activation`, default gated activation, and default run-rate de-spiking. |
| Secondary diagnostic | `corporate_total_recent_shape` | Existing 56-day global recent-shape allocation. Keep it in the pack for frontier context; do not let it substitute for the primary challenger decision. |

The locked implementation is
`scripts/python/forecast_model_category_pool.py` Git blob
`58ca4c2b79ab606cf7be98e57533e867aed3caa0`. Do not alter that script, its
defaults, the candidate labels, or the source inputs after the forecast start
date. A later revision requires a separate named candidate and a fresh test.

## Freeze Procedure At The Next Origin

Let `T` be the sole `ForecastStartDate` in the next corporate
`FwdDemandCSV_YYYY-MM-DD_velocity_frozen.csv`; the test horizon is `T` through
`T + 13 days`. Perform every step below after the corporate file is available
and **before the local start of `T`**. If that timing is missed, label the pack
late-origin and wait for the following clean origin instead of presenting it as
a contestant.

1. Copy the corporate source file unchanged into the dated forecast-test pack.
   Record its source path, file hash, file size, creation/modification time,
   one `ForecastStartDate`, 14-day total, and count of positive SKUs.
2. Refresh the portable `modified` DirectPick history only through `T - 1` and
   record its manifest/hash. The current 2026 shard ends 2026-07-22 and is not
   sufficient for a later origin. Never refresh it through an in-horizon date
   before building the candidate.
3. Snapshot the category crosswalk, the required DirectPick-history shards,
   inventory, and inbound inputs used by the build; record their hashes and
   maximum as-of dates. The current crosswalk is a July 22 snapshot (113,824
   SKUs), so refresh or explicitly retain it before `T` and save the exact
   version beside the dated pack. The candidate supports explicit snapshot
   paths; do not rely on rolling files for a reproducible frozen pack.
4. Run the existing corporate/recent-shape builder to create the raw baseline
   and secondary diagnostic. Then run the category-pool builder with the
   locked settings below. Preserve both generated metadata files and record the
   Git revision in the pack metadata.
5. Verify all 14 daily corporate totals are identical between `corporate_raw`
   and `catpool_corporate_anchor_activation`, then commit the compact pack and
   its inputs before the horizon starts. Do not upload the challenger to AX.

The execution shape is:

```powershell
$origin = '<T: YYYY-MM-DD>'
$pack = "Output/ForecastAccuracy/forward_tests/${origin}_corporate_<file-date>"

uv run python scripts/python/forecast_direct_pick_history.py `
  --start-date 2026-01-01 `
  --end-date $origin `
  --date-basis modified `
  --overwrite

uv run python scripts/python/forecast_forward_recent_shape.py `
  --corporate-fwd "$pack/input/FwdDemandCSV_<file-date>_velocity_frozen.csv" `
  --live-ax `
  --lookback-days 56 `
  --output-dir "$pack/recent_shape_shadow"

uv run python scripts/python/forecast_model_category_pool.py `
  --origin $origin `
  --ledger-db "$pack/input/sku_category_crosswalk.parquet" `
  --corporate-daily "$pack/recent_shape_shadow/forward_daily_forecasts.parquet" `
  --lookback-days 56 `
  --seasonal-years 3 `
  --activation `
  --direct-pick-dir "$pack/input/direct_pick_history/parquet" `
  --inventory-path "$pack/input/pickface_inventory_sku_day.parquet" `
  --inbound-path "$pack/input/ax_open_inbound_sku_day.parquet" `
  --output-dir "$pack/category_pool_shadow"
```

The DirectPick export uses read-only AX data with the documented warehouse 4010
and DirectPick filters. It replaces the rolling 2026 shard, so run it only at
the clean origin, inspect its date range, and preserve the dated input snapshot
in the test pack before later refreshes occur.

## Closeout Contract

On or after `T + 14`, first run `forecast_actuals_source_audit.py` for the 14
dates. Prefer a complete canonical monitoring-scope SKU/day fact; use the
read-only live-AX fallback only if the audit requires it, and reconcile the
saved SKU/day actual to monitoring. Score the raw corporate, recent-shape, and
category-pool packs on the same actuals. Report daily totals, 14-day bias, SKU
WAPE, sold-unit coverage, forecast-positive SKU use rate, zero-forecast sold
units, and forecast units assigned to zero-demand SKUs.

If a weekly corporate upload overlays this horizon, retain its dated source
file and score it separately as the operational-vintage forecast for the dates
it controlled. Do not overwrite the original frozen corporate pack; also
report the overlay's change in accuracy over only the overlapping dates.

Retain the actual, score tables, category scorecard, source metadata, candidate
metadata, input hashes, row/SKU/unit counts, and individual file sizes. Compact
artifacts under 90 MB belong in Git; do not retain credentials or a production
AX upload as research evidence.
