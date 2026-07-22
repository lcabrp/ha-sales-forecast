# Forecast Data Landscape and Cross-Repo Contract - 2026-07-20

Updated 2026-07-21 after the July 7-20 closeout. Read
`FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md` for the final corporate versus
independent scorecard and the frozen July 21-August 3 shadow.

`FORECAST_CURRENT_STATE.md` is the authoritative current decision and reading
entry point. This document is the detailed owner/path/grain reference.

This is the local first-stop answer to: **what forecast data do we have, where
did it land after the repo split, and how should prior sale periods inform a
current SKU forecast?**

Read this before reopening broad data-source research. The current replacement
objective lives in `FORECAST_CURRENT_STATE.md`; artifact movement rules live in
`FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md`.

## Executive Finding

The needed pieces mostly exist, but the contracts between them are incomplete.

- `ha-sales-forecast` has strict SKU/day `DirectPick` history from 2022 through
  2026, promotion facts, sales orders, model panels, corporate history, and the
  forecast experiments.
- `ha-kydc-monitoring` is the live daily producer for completed-day operational
  totals, pick-face inventory snapshots, inbound snapshots, and forecast
  `SlotTier` history.
- `ha-ingestion-pipeline` owns the current Product Info parsing, production AX
  upload behavior, and the persistent SKU/category ledger.
- The stable historical category crosswalk did **not** become a formal
  forecast-owned artifact. The current multi-year sale overlay instead maps
  history through a model panel that begins in 2025. That map covers only
  `62.9%` of 2024 DirectPick units, while the active ingestion ledger covers
  `99.7%`.
- The almost-daily monitoring inventory fact is pick-face-only and contains
  positive physical rows. It is useful for replenishment state, but absence
  from it is not proof of a retail stockout.
- BigQuery is not required to build or evaluate the first category-total /
  current-assortment challenger. It becomes high value when we want to
  reconstruct 2024/2025 stockouts and estimate unconstrained customer demand.

The correct direction is therefore a hierarchy-aware hybrid:

1. forecast the event/category volume at a stable family grain;
2. build the eligible current assortment as of the forecast origin;
3. allocate selectively using recent demand, promotion eligibility, inventory,
   lifecycle, and probability of actual use;
4. keep ML as an occurrence/ranking/residual layer rather than asking one model
   to discover the total event regime and every SKU allocation simultaneously.

### 2026-07-21 closeout correction

The 14-day evidence now supports that direction directly:

- monitoring has all 14 Eastern days and 203,347 Pick units;
- the matching AX SKU/day fallback reconciles within 20 units;
- corporate raw was nearly exact on total volume (+0.65%) but covered only
  35.15% of sold units at SKU level;
- the frozen independent ML hybrid underforecast 22.58% and put 29.91% of its
  forecast units on SKUs with zero demand;
- the frozen corporate-total/recent-shape challenger had the best current
  allocation balance, but also exposed an 11,089-unit independent-rounding bug;
- repaired largest-remainder allocation preserves the corporate total exactly.

This is not evidence to abandon ML. It is evidence that statistics/category
reconciliation must own the base and that ML needs the narrower occurrence,
promotion/newness, and residual-ranking jobs.

## Repository Ownership After the Split

| Repository | Forecast-relevant ownership | Contract into this repo |
|---|---|---|
| `ha-sales-forecast` | DirectPick history; forecast/actual facts; corporate DB snapshots; promotion extraction; sales-order, inventory, inbound, reservation, and warehouse-supply research facts; backtests and candidates | This repo is the consumer and research owner. |
| `ha-kydc-monitoring` | Daily completed operational timeline, `Monitoring_History.db`, pick-face inventory, open inbound, confirmed forecast `SlotTier` SCD | Inventory/inbound mirror exists through `scripts/python/sync_monitoring_forecast_artifacts.py`. A canonical current SKU/day DirectPick feed is still missing. |
| `ha-ingestion-pipeline` | Product Info parsing, production AX `FwdDemandCSV`, `RequiredSlots`, current SKU/category ledger, production guardrails | No formal category-crosswalk mirror exists. Forecast code currently relies on model-panel attributes, source workbooks, or candidate-local ledgers. |
| `ha-warehouse-layout` | Physical canvas, zoning, market-basket layout inputs, maps and layout QA | Consumes `FwdDemandCSV`/`RequiredSlots`; it does not own the demand target. |
| `ha-zoning-slotting` | Frozen monorepo-era reference | Compare/recover only. Do not treat its copied outputs as current producers. |
| `ha-brg-legacy-reference` | Legacy BRG/Ankura workbooks and reverse-engineering evidence | Reference only. |

### Important split boundaries

- Ingestion, SKU-ledger, SharePoint, and live SlotTier compatibility copies were
  removed from this repo on 2026-07-21. Product Info parsing and AX-shaped output
  generation must use `ha-ingestion-pipeline`.
- `output_paths.py`, `sql_utils.py`, and `forecast_schema.py` are forecast-owned
  helpers. They contain no Product Info workbook parser or ingestion pipeline.
- Production parity and every upload-facing roundtrip must be validated by the
  active ingestion repo.
- The monitoring-to-forecast inventory/inbound mirror in this repo was refreshed
  on `2026-07-21` and now matches the producer through that capture.

## Current Data Inventory

Counts below were checked locally on 2026-07-21.

| Data | Owner / path | Grain and current coverage | What it can answer | Limitation |
|---|---|---|---|---|
| Strict fulfilled demand | `ha-sales-forecast/Output/ForecastAccuracy/direct_pick_history/parquet/` | `9,960,972` SKU/day rows, `38,754,405` units, 2022-01-02 through 2026-06-25 | Historical event/category demand and model training | Fulfilled warehouse work, not unconstrained orders; annual facts do not embed category columns |
| Recent actual mirror | `ha-sales-forecast/Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet` | `1,614,433` SKU/day rows, `30,224` SKUs, 2025-11-01 through 2026-07-09 | SKU allocation scoring through its max date | Stale after July 9; query scope is broader than the strict operational DirectPick contract |
| Monitoring daily picks | `ha-kydc-monitoring/Output/Monitoring/Monitoring_History.db` | Aggregate Pick rows are current through 2026-07-20; July 7-20 totals are `203,347` units over 14/14 days | Completed-day coverage and aggregate-volume check | Activity/date aggregate only; cannot score individual SKU allocation |
| Monitoring pick-face inventory | Producer and mirror: `Output/ForecastAccuracy/inventory/pickface_inventory_sku_day.parquet` | `322,422` SKU/day rows, `17,354` SKUs, 28 snapshot days from 2026-06-19 through 2026-07-21 | Whether a positive-quantity SKU was present in replenishment-relevant pick profiles | Positive pick-face rows only; not total warehouse or ecommerce availability |
| Open inbound snapshots | Producer and mirror: `Output/ForecastAccuracy/inbound/ax_open_inbound_sku_day.parquet` | `427,014` SKU/day rows, `17,250` SKUs, 28 snapshots from 2026-06-19 through 2026-07-21 | Current/future supply context | Point-in-time state; never join a later snapshot into an earlier holdout |
| AX saved warehouse inventory history | `ha-sales-forecast/Output/ForecastAccuracy/inventory/ax_inventory_history_sku_day.parquet` | `3,541,676` SKU/day rows, `51,435` SKUs, 75 days from 2026-04-01 through 2026-06-14 | Warehouse-level available, reserved, ordered, and net-available state | Too short for 2024/2025 sale-event stockout correction |
| Active category ledger | `ha-ingestion-pipeline/Output/Ingestion/sku_ledger.db` | `113,887` raw rows, `113,744` normalized unique SKUs, 83 `ProductGroupCode + SizeGroupCode` cells | Map retired and current SKUs to stable operational families | Current-value ledger, not an as-of/SCD category history; `KeyCategoryView` is not stored |
| Model panel | `ha-sales-forecast/Output/ForecastAccuracy/model/model_sku_day_panel_parts/` | `5,457,830` rows, `47,445` raw SKUs, 2025-01-01 through 2026-06-08 | Existing model features/backtests | Stale for July, sparse, and too recent to be the historical category crosswalk |
| Sales-order demand/discount | `ha-sales-forecast/Output/ForecastAccuracy/sales_orders/sales_order_sku_day.parquet` | `3,317,066` SKU/day rows, 45,400 SKUs, 2025-01-01 through 2026-06-08 | Ordered demand and realized price/discount signal | Not refreshed for July; cancellations/returns need separate treatment |
| Promotion event rows | `ha-sales-forecast/Output/ForecastAccuracy/promotions/pdl_offer_rows.parquet` | 243,641 rows, 358 events, 88 workbook records, event dates through 2026-07-21 | Promotion calendar and offer/style-color eligibility | Only six raw workbooks are local; 82 older raw files survive only through extracted tables |
| Promotion SKU/day features | `ha-sales-forecast/Output/ForecastAccuracy/promotions/pdl_sku_day_features.parquet` | `8,059,505` rows, `80,485` SKUs, through 2026-07-21 | Model-ready SKU promotion flags | July 21 PDL has no end date, so its SKU feature is one day only; do not infer a week |
| Warehouse supply work | `ha-sales-forecast/Output/ForecastAccuracy/warehouse_supply/warehouse_supply_sku_day.parquet` | 67,901 SKU/day rows, 14,611 SKUs, 2026-03-30 through 2026-06-12 | Replenishment/sellable-floor supply events | Short recent window |
| Reservations | `ha-sales-forecast/Output/ForecastAccuracy/reservations/ax_reservation_sku_day.parquet` | One snapshot on 2026-06-15, 8,339 SKUs | Point-in-time open-order reservation proxy | Not a historical series |

### Actual-target scope warning

There are three related but non-identical `DirectPick` scopes:

1. The annual training shards filter warehouse 4010, completed sales-order
   `DirectPick`, replenishment-relevant source profiles, and documented
   exclusions. They include historical rows whose current source profile is
   `invalid` and cast `MODIFIEDDATETIME` directly to a SQL date.
2. `history/parquet/actual_sku_day_modified.parquet` is broader: it does not
   apply the same warehouse/profile/location restrictions and uses the raw AX
   modified-date cast.
3. Monitoring uses completed Eastern-day UTC windows and its operational
   warehouse/profile/location filters.

For July 7-19, the broader actual scope produced 196,655 units versus 191,930 in
monitoring, about a 2.5% difference. The final July 7-20 closeout instead used a
narrow live-AX fallback with the monitoring filters/Eastern window: 203,327
SKU/day units versus 203,347 in monitoring (-20, or -0.01%). Use
`forecast_actuals_source_audit.py` before every closeout and
`forecast_window_compare.py --live-ax` only when monitoring still lacks a
current canonical SKU/day fact.

## Category and SlotTier Terminology

The short operational codes should not all be treated as the same modeling
grain.

- `GIR` or `BOY`: `ProductGroupCode`.
- `GIRM` or `BOYM`: `ProductGroupCode + SizeGroupCode`. This is a useful stable
  category-size allocation cell across assortment turnover.
- `GIRMA`: `ProductGroupCode + SizeGroupCode + Velocity`. The trailing `A` is a
  current velocity/slotting policy outcome, not a stable merchandise category.
- `Division / Department / Class / KeyCategoryView`: descriptive product
  hierarchy from Product Info/corporate snapshots.

For prior-sale learning, anchor at `GIRM`/`BOYM` or an agreed descriptive
category. Do not make `GIRMA` the historical anchor unless an as-of velocity
history is available. Forecast the family demand first; calculate current
velocity/`SlotTier` after current-SKU allocation.

## Historical Category Coverage

The annual DirectPick facts intentionally store only demand columns. Category
must be joined through a separate crosswalk.

Coverage of DirectPick history using the active ingestion ledger:

| Year | DirectPick SKUs mapped | DirectPick units mapped |
|---:|---:|---:|
| 2022 | 66.5% | 83.6% |
| 2023 | 96.7% | 98.6% |
| 2024 | 98.5% | 99.7% |
| 2025 | 99.8% | 99.9% |
| 2026 through June 25 | 99.8% | 99.9% |

The weaker 2022 coverage is consistent with the ledger beginning on
2022-09-08. It should be reported or backfilled before treating 2022 as a fully
categorized event year.

By contrast, the latest-category map built from the existing model panel has
47,444 normalized SKUs and covers:

| Year | DirectPick SKUs mapped | DirectPick units mapped |
|---:|---:|---:|
| 2022 | 5.2% | 16.9% |
| 2023 | 9.4% | 24.5% |
| 2024 | 43.3% | 62.9% |
| 2025 | 100.0% | 100.0% |
| 2026 through June 25 | 98.6% | 100.0% |

This exposed the most important error in the retired sale overlay: it mapped
both 2024 and 2025 history through the 2025-2026 panel. Roughly `37.1%` of 2024
units therefore collapsed into the `Unknown` hierarchy instead of their real
family. The faulty overlay implementation has been removed.

### Required category artifact contract

Do not add changing category text directly to every annual demand shard. Keep a
separate forecast-facing crosswalk produced from the active ingestion ledger:

```text
Output/ForecastAccuracy/product_attributes/sku_category_crosswalk.parquet
```

Minimum fields:

- `SKU`
- `ProductGroupCode`
- `SizeGroupCode`
- `CategorySizeCode`
- `Division`
- `Department`
- `Class`
- `FirstSeen`
- `LastSeen`
- source repo/path, source file hash, and extraction timestamp in companion JSON

If `KeyCategoryView` is required, enrich it from Product Info/AX and record the
fallback source. A future SCD version should preserve effective dates if
category reassignment proves material.

## What Prior July Sales Say

The user's proposed reasoning is valid: learn a stable category/event pool from
prior sale periods, then distribute that pool over the current assortment
instead of trying to find the same SKUs next year.

Using the current strict DirectPick facts and the active ingestion ledger:

| Analog | Cell | Sale units | Prior baseline units | Calendar-normalized expected units | Lift |
|---|---|---:|---:|---:|---:|
| 2025-06-21 through 2025-07-04 (14 days) | GIRM | 56,884 | 56,114 over prior 28 days | 28,057 | 2.03x |
| 2025-06-21 through 2025-07-04 (14 days) | BOYM | 19,463 | 19,225 over prior 28 days | 9,612.5 | 2.02x |
| 2024-06-18 through 2024-07-06 (19 days) | GIRM | 58,401 | 72,019 over prior 28 days | 48,870 | 1.20x |
| 2024-06-18 through 2024-07-06 (19 days) | BOYM | 21,106 | 25,960 over prior 28 days | 17,615.7 | 1.20x |

The event windows have different durations, so compare lift/daily curves rather
than raw totals. The very different 2024 and 2025 lifts also show why one prior
year should not be copied literally. Use a shrunk multi-year event estimate,
current run rate, current promotion depth, and the current assortment.

## Retired July Overlay: Reusable Lesson

The retired implementation attempted to:

- compute prior-event category lift;
- shrink/cap noisy categories;
- apply lift to the current pre-event baseline;
- limit the overlay to current promoted categories;
- allocate the category target onto current hybrid/recent/PDL SKU shape.

That structural idea remains worth testing, but the implementation was removed
because:

1. **Historical category mapping is incomplete.** The 2024 history uses the
   2025-starting model-panel map instead of the persistent ingestion ledger.
2. **Calendar-day denominators are wrong.** `category_lift_table()` and the
   current-baseline calculation count dates present in the sparse fact with
   `nunique()`. A zero-pick day is absent and is therefore dropped. For example,
   2025-06-15 is absent from the 28-day baseline. A 14-calendar-day forecast
   must use explicit calendar-day counts or a completed date spine.
3. **Saved overlay evidence drifted from the current training fact.** The old overlay
   narrative reports 473,431 2025 analog units and a 2.13x lift. The current
   strict annual shard contains 417,092 units in 2025-06-21 through 2025-07-04.
   Do not reuse the old 2.13x output without rebuilding it from the current
   manifest and category crosswalk.
4. **Promotion features were stale at origin.** Raw PDL extraction reached July
   20, but the model-ready SKU/day file stopped July 6 when the July 7 forecast
   was frozen. Refreshing it after closeout improves future inputs but cannot
   retroactively make the frozen test promotion-aware.
5. **Allocation objective is still wrong for Operations.** The current shape is
   driven by unit error and coverage, not probability that a pulled carton will
   actually be consumed.

Apply these corrections only to a future pre-origin challenger and compare it
honestly. Do not reconstruct the retired script from its generated outputs.

## Correct Event-to-SKU Forecast Contract

For each forecast origin and event:

1. **Freeze inputs as of origin.** Save the promotion files/features, current
   Product Info assortment, category crosswalk, DirectPick history cutoff,
   inventory/inbound snapshot, and candidate code/version.
2. **Define comparable event windows.** Record event name/type, start/end,
   discount/promotion depth, calendar days, and daily curve. Use a complete date
   spine so closed/zero-pick days remain zeros.
3. **Forecast stable category totals.** One reasonable starting form is:

   ```text
   CategoryTarget = CurrentPreEventRunRate
                    * TargetCalendarDays
                    * ShrunkMultiYearEventLift
   ```

   Blend or reconcile this with the best aggregate/corporate/category signal
   only after backtesting.
4. **Build the current eligible assortment.** Use current Product Info, PDL,
   recent DirectPick, go-live/status, positive or incoming supply, and valid
   reservation signals. Historical SKUs must not be resurrected merely because
   they sold in the analog year. During a seasonal assortment reset, use
   origin-safe inventory appearance/growth, inbound, lifecycle state, and the
   creation of replenishment demand as activation evidence. Give a new SKU a
   category/size prior rather than requiring 56 days of its own pick history.
   Conversely, downweight an ending-season SKU when declining supply, inactive
   lifecycle state, no inbound/promotion support, and absent recent demand agree.
   Do not interpret the SKU's actual landing zone as demand evidence: when AA
   sub-zones are full, AX fallback placement reflects capacity pressure rather
   than expected sales. Replenishment activity is an assortment/supply clue,
   not customer-demand proof.
5. **Estimate use probability and conditional quantity separately.** Rank by
   probability of consuming at least one replenishment carton in the horizon;
   estimate units conditional on use. Recent statistical demand should remain
   the baseline, with ML supplying promo/lifecycle/analog/residual corrections.
6. **Allocate selectively.** Apply current SKU shares within each category, but
   do not force aggregate planning volume into very low-probability SKU rows.
   Preserve any unallocated remainder as aggregate labor/volume planning signal
   unless service constraints require more SKU coverage.
7. **Apply case and inventory policy.** Convert units into forecast-triggered
   pulls using case quantity, starting pick-face quantity, reserve availability,
   replenishment threshold, and inbound timing.
8. **Round deterministically.** Use largest-remainder/Hamilton rounding when a
   selected category or daily total must be preserved. Do not independently
   round every SKU and silently change the total.

Primary operational metrics should include useful pulled cartons / predicted
pulled cartons, pulled-unit utilization, missed demand/reactive replenishments,
and sold-unit coverage. WAPE remains diagnostic, not the sole decision metric.

## Inventory and BigQuery Decision

### What local data is enough for

The local monitoring and AX facts are enough to:

- evaluate the July 2026 forecast against fulfilled DirectPick actuals;
- condition current allocation on pick-face and inbound state;
- simulate recent carton-use policies when an origin snapshot exists;
- build the category-total/current-assortment challenger.

### What local data cannot prove

It cannot reliably tell whether low 2024/2025 DirectPick demand meant low
customer demand or no sellable inventory. Monitoring started in 2026, and its
pick-face fact omits zero inventory rows and reserve/bulk inventory.

### When BigQuery becomes necessary

Use BigQuery when its snapshot history covers the prior event periods and can
provide an as-of warehouse/SKU state. It is most valuable for:

- marking stockout-censored SKU/days during 2024/2025 analog events;
- estimating lost/unconstrained demand;
- separating weak demand from unavailable inventory;
- validating launch, back-in-stock, and sell-through behavior.

Before extraction, confirm the BigQuery contract contains:

- snapshot/as-of timestamp and timezone;
- SKU, site, and warehouse;
- available physical, physical reserved, ordered/inbound, and ideally location
  profile or pick-face versus reserve/bulk quantities;
- explicit zero rows or a complete SKU/date universe so absence is interpretable;
- coverage dates and refresh cadence.

Write it in yearly/partitioned Parquet under an inventory-history contract and
lag it at least one day for forecast features. Do not replace the monitoring
producer: BigQuery is the deep-history source; monitoring remains the current
operational source.

## Priority Fix Order

Completed on 2026-07-21: the frozen July closeout, monitoring inventory/inbound
refresh, promotion extraction/SKU-day tail refresh, total-preserving rounding
repair, and the July 21-August 3 statistical shadow.

Remaining order:

1. Produce/mirror the ingestion-ledger category crosswalk into this repo.
2. Correct the sale overlay's category source and date spine; use the repaired
   total-preserving rounding and rebuild old overlay evidence instead of
   quoting 473,431 / 2.13x.
3. Add the carton-use operational simulator/scorecard and an explicit
   precision/coverage selection policy.
4. Evaluate the two-stage category-total -> current-SKU allocation challenger.
5. Pull BigQuery history only after its schema/coverage is confirmed, then rerun
   historical analog tests with stockout censoring.

This is a correction of the data contracts and objective, not another wholesale
forecast reset.
