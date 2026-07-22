# DirectPick History Dataset

This dataset is the warehouse fulfilled-demand backbone for forecast training,
sale-event lift learning, and post-event scoring.

## Existing Local Files

Current useful pick artifacts found on disk:

| File | Grain | Date Span | Use |
|---|---|---:|---|
| `Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2022.parquet` through `..._2026.parquet` | SKU/day DirectPick totals by pick-line modified date | 2022-01-02 through 2026-06-25 | Canonical strict fulfilled-demand history for event/category learning and backtests. |
| `Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet` | SKU/day DirectPick totals by pick-line modified date | 2025-11-01 through 2026-07-09 | Recent model target/scorecard mirror; refresh when the requested closeout extends past its max date. |
| `../ha-kydc-monitoring/Output/Monitoring/Monitoring_History.db` | Date/activity aggregate | Current completed Pick day through 2026-07-19 at the 2026-07-20 audit | Primary completed-day/aggregate check, but not an SKU allocation fact. |
| `../ha-warehouse-layout/Output/MarketBasket/Market_Basket_Data_12mo.parquet` | Pick line/order/SKU | Historical layout-analysis window | Co-purchase/pathing analysis, not needed for forecast target training. |

The earlier gap in older DirectPick demand has been closed: strict yearly shards
now reach 2022 and include multiple July, October, and holiday analog periods.
The remaining history gap is deep inventory availability, not fulfilled pick
history.

## Dataset Contract

Use SKU/day totals, not raw pick lines, for forecast grounding:

- Source tables: `WHSWORKTABLE`, `WHSWORKLINE`, `INVENTDIM`,
  `WMSLOCATION`.
- Company: `DATAAREAID = 'ha'`.
- Partition: `[PARTITION] = 5637144576`.
- Warehouse: `INVENTLOCATIONID = '4010'`.
- Included work: closed sales-order `DirectPick` pick lines only.
- Filters:
  - work header `WORKSTATUS = 4`
  - work header `WORKTRANSTYPE = 2`
  - work line `WORKSTATUS = 4`
  - work line `WORKTYPE = 1`
  - work line `WORKCLASSID = 'DirectPick'`
  - source location profile in `Picking`, `Picking A`, `PalletPicking`,
    `Picking D`, or `invalid`
  - source location profile not in `W001`, `No LP Track`
  - source location not in `Bander`, `AutoBagger`
- Date basis: `WHSWORKLINE.MODIFIEDDATETIME`, matching the current model
  fulfilled-demand actuals.
- Persisted columns:
  - `PickDate`
  - `DateBasis`
  - `SKU`
  - `PickLines`
  - `DistinctOrders`
  - `PickUnits`

Do not persist `WORKID`, sales-order IDs, user/operator fields, or raw line
detail for forecast training. Raw line/order detail belongs in market-basket or
pick-path analysis only.

## Target Window

Collect from 2022-01-01 forward. Do not go earlier by default: pandemic-era
volume and operating patterns are likely different enough to add noise.

The preferred storage shape is yearly Parquet shards under:

```text
Output/ForecastAccuracy/direct_pick_history/parquet/
```

The extractor also writes:

```text
Output/ForecastAccuracy/direct_pick_history/direct_pick_history_manifest.json
Output/ForecastAccuracy/direct_pick_history/direct_pick_history_year_summary.csv
```

## Current Collection Command

```powershell
uv run python scripts/python/forecast_direct_pick_history.py `
  --start-date 2022-01-01 `
  --end-date 2026-06-26 `
  --overwrite
```

Use an end date that is exclusive. For example, `2026-06-26` includes picks
through `2026-06-25`.

## Scope Decision - 2026-06-18

Scope audit:

- Broad closed `DirectPick` units, before source-profile restriction:
  `38,735,335`.
- Strict sales-order pickable-location units, excluding both `invalid` and
  `W001`: `38,075,573` (`98.30%`).
- Follow-up review: `invalid` source locations were valid at historical pick
  time and should be included. `W001` is Amazon/wholesale-related and remains
  excluded from replenished pick-face forecasting.
- Final regenerated training scope units through 2026-06-18: `38,574,387`.
- The remaining excluded units are `W001` / `No LP Track` profile rows, plus
  explicit `Bander` / `AutoBagger` source locations.
- The yearly shards below were regenerated with the final scope.
- `DAX_Archive.arc` does not contain `WMSLOCATION`; archived work is classified
  against `DAX_PROD.dbo.WMSLOCATION` under the assumption that these source
  location profiles are stable enough for historical demand classification.

Collected successfully from AX into yearly Parquet shards:

```text
Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2022.parquet
Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2023.parquet
Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2024.parquet
Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2025.parquet
Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2026.parquet
```

Manifest and summaries:

```text
Output/ForecastAccuracy/direct_pick_history/direct_pick_history_manifest.json
Output/ForecastAccuracy/direct_pick_history/direct_pick_history_year_summary.csv
Output/ForecastAccuracy/direct_pick_history/direct_pick_daily_totals.csv
Output/ForecastAccuracy/direct_pick_history/direct_pick_july_window_lift_summary.csv
Output/ForecastAccuracy/direct_pick_history/direct_pick_top_14d_windows_by_year.csv
Output/ForecastAccuracy/direct_pick_history/direct_pick_october_top_14d_windows.csv
```

## Current Collection Result - 2026-06-25

The current manifest was refreshed on 2026-06-25 with an exclusive end date of
2026-06-26. Collection scope:

| Year | Date span | SKU/day rows | Distinct SKUs | Pick units |
|---:|---|---:|---:|---:|
| 2022 | 2022-01-02 through 2022-12-31 | 2,119,178 | 31,381 | 9,298,381 |
| 2023 | 2023-01-02 through 2023-12-31 | 2,154,543 | 32,348 | 9,021,926 |
| 2024 | 2024-01-02 through 2024-12-31 | 2,289,654 | 36,340 | 9,085,238 |
| 2025 | 2025-01-02 through 2025-12-31 | 2,366,162 | 36,963 | 8,440,037 |
| 2026 | 2026-01-02 through 2026-06-25 | 1,031,435 | 24,794 | 2,908,823 |
| **Total** | 2022-01-02 through 2026-06-25 | **9,960,972** |  | **38,754,405** |

AX archive boundary during the current run was `2026-06-20`; rows before that
came from `DAX_Archive.arc`, and rows from `2026-06-20` forward came from
`DAX_PROD.dbo`.

Quick event checks:

| Year | July 21-Jun through 4-Jul units | Prior 28-day units | Lift vs prior 14-day run rate |
|---:|---:|---:|---:|
| 2022 | 305,796 | 576,820 | 1.06x |
| 2023 | 321,299 | 572,183 | 1.12x |
| 2024 | 344,944 | 509,392 | 1.35x |
| 2025 | 417,092 | 460,281 | 1.81x |

Top October 14-day windows are stronger and more stable:

| Year | Top October 14-day window | Units | Lift vs prior 14-day run rate |
|---:|---|---:|---:|
| 2022 | 2022-10-13 through 2022-10-26 | 814,141 | 2.56x |
| 2023 | 2023-10-12 through 2023-10-25 | 763,566 | 2.94x |
| 2024 | 2024-10-10 through 2024-10-23 | 762,379 | 2.80x |
| 2025 | 2025-10-12 through 2025-10-25 | 693,194 | 2.51x |

Interpretation: the DirectPick history is sufficient to train and backtest a
generic event-lift layer before Friends and Family / peak season. July should be
used as the current live shadow validation, but October and holiday do not need
to wait for a manual rescue.

## Category Crosswalk

The yearly facts do **not** embed `Division`, `Department`, `Class`,
`KeyCategoryView`, `ProductGroupCode`, `SizeGroupCode`, or historical velocity.
Category is a dimension and should be joined separately so a corrected mapping
does not require rewriting every demand shard.

The active crosswalk currently lives in:

```text
../ha-ingestion-pipeline/Output/Ingestion/sku_ledger.db
```

At the 2026-07-20 audit it mapped `99.7%` of 2024 DirectPick units and `99.9%`
of 2025 units. The current model-panel category map covers only `62.9%` of 2024
units because the panel starts on 2025-01-01. Do not use that panel map for the
2024 sale overlay.

Use `ProductGroupCode + SizeGroupCode` for stable operational cells such as
`GIRM` or `BOYM`. A code such as `GIRMA` adds the current velocity `A`; velocity
is an allocation/slotting result and is not a stable prior-year category without
an as-of velocity history.

See `FORECAST_DATA_LANDSCAPE_2026-07-20.md` for coverage counts, the required
portable category-crosswalk contract, the July analog examples, and the
confirmed sale-overlay misalignments.

## Modeling Use

This dataset should feed a reusable sale-event layer, not one-off hard-coded
sale totals:

- event-type lift by category/product group;
- day-of-event curves;
- promoted-SKU allocation using PDLs plus recent velocity;
- post-event reversion/cooldown;
- leave-one-event-out backtests before trusting an event rule for peak season.

Event calculations must build a complete calendar date spine. Do not infer the
number of baseline days from dates present in this sparse positive-activity fact:
a zero-pick day is intentionally absent and must remain a zero when forecasting
a calendar horizon.
