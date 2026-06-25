# DirectPick History Dataset

This dataset is the warehouse fulfilled-demand backbone for forecast training,
sale-event lift learning, and post-event scoring.

## Existing Local Files

Current useful pick artifacts found on disk:

| File | Grain | Date Span | Use |
|---|---|---:|---|
| `Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2025.parquet` | SKU/day DirectPick totals by work-created date | 2025-01-01 through 2025-12-31 | Useful for July sale analogs and forecast work. |
| `Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet` | SKU/day DirectPick totals by pick-line modified date | 2025-11-01 through 2026-06-18 | Current model target and scorecard actuals. |
| `Output/MarketBasket/Market_Basket_Data_12mo.parquet` | pick line/order/SKU | 2025-04-01 through 2026-04-06 | Co-purchase/pathing analysis, not needed for forecast target training. |
| `Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2024.parquet` | replenishment touch | 2024-01-01 through 2024-12-31 | Supply/labor pressure, not demand. Do not use as sold-unit history. |

The gap for event learning is older fulfilled DirectPick demand. The local
DirectPick demand caches do not reach October 2024/2023, so they are not enough
to learn Friends and Family or earlier peak-season event behavior.

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
  --end-date 2026-06-19 `
  --overwrite
```

Use an end date that is exclusive. For example, `2026-06-19` includes picks
through `2026-06-18`.

## Collection Result - 2026-06-18

Scope audit:

- Broad closed `DirectPick` units, before source-profile restriction:
  `38,735,335`.
- Strict sales-order pickable-location units, excluding both `invalid` and
  `W001`: `38,075,573` (`98.30%`).
- Follow-up review: `invalid` source locations were valid at historical pick
  time and should be included. `W001` is Amazon/wholesale-related and remains
  excluded from replenished pick-face forecasting.
- Final regenerated training scope units: `38,574,387`.
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

Collection scope:

| Year | Date span | SKU/day rows | Distinct SKUs | Pick units |
|---:|---|---:|---:|---:|
| 2022 | 2022-01-02 through 2022-12-31 | 2,119,178 | 31,381 | 9,298,381 |
| 2023 | 2023-01-02 through 2023-12-31 | 2,154,543 | 32,348 | 9,021,926 |
| 2024 | 2024-01-02 through 2024-12-31 | 2,289,654 | 36,340 | 9,085,238 |
| 2025 | 2025-01-02 through 2025-12-31 | 2,366,162 | 36,963 | 8,440,037 |
| 2026 | 2026-01-02 through 2026-06-18 | 985,883 | 24,569 | 2,728,805 |
| **Total** | 2022-01-02 through 2026-06-18 | **9,915,420** |  | **38,574,387** |

AX archive boundary during the run was `2026-06-13`; rows before that came from
`DAX_Archive.arc`, and rows from `2026-06-13` forward came from `DAX_PROD.dbo`.

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

## Modeling Use

This dataset should feed a reusable sale-event layer, not one-off hard-coded
sale totals:

- event-type lift by category/product group;
- day-of-event curves;
- promoted-SKU allocation using PDLs plus recent velocity;
- post-event reversion/cooldown;
- leave-one-event-out backtests before trusting an event rule for peak season.
