# Historical Inventory Review and Origin-Safe Activation Backtest

Date: 2026-08-21

## Decision

Historical inventory is **high-value evidence for assortment activation,
stockout censoring, and supply-state diagnostics**, but it is not required for
the corporate-vs-reallocation coverage gate itself. The newly preserved history
also rejects the current activation rule: in both warehouse and pick-face
sensitivities, activation increased sold-unit coverage but worsened SKU WAPE.

Do not promote activation or change the current champion from this work. The
next activation design must distinguish warehouse availability, pick-face
presence, and inbound state instead of treating any supply signal as a single
active-assortment set.

The later Forecast DB isolation adds a narrow positive result without changing
that decision: fresh DIRECT inventory alone improves the category-pool anchor
slightly and consistently, but combining it with stale inbound reverses the
gain. This supports a source-aware, age-bounded redesign rather than the current
unioned activation rule.

## Portable Evidence Preserved

| Fact | Business meaning | Window | Snapshot days | Rows | Distinct SKUs | Provenance |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `ax_inventory_history_sku_day.parquet` | Broad warehouse SKU availability | 2026-04-01 to 2026-08-21 | 143 | 6,739,501 | 57,529 | Live read-only AX `DAX_PROD.dbo.HAINVENTDETAILREPORTBATCHTMPHISTORY`, company `ha`, partition `5637144576`, site `HA USA`, warehouse `4010`; merged with the prior local April-June archive |
| `pickface_inventory_sku_day.parquet` | Occupied pick-face/location inventory | 2026-05-13 to 2026-08-21 | 55 | 669,049 | 20,941 | Immutable `ky-dc-monitoring` inventory-zone detail reports; underlying live source `INVENTSUM`/`INVENTDIM`/`WMSLOCATION` |
| `pickface_inventory_snapshot_detail.parquet` | Location-level pick-face detail | 2026-05-13 to 2026-08-21 | 55 | 753,746 | 20,941 | Same monitoring reports; no missing calendar dates were synthesized |
| `ax_open_inbound_sku_day.parquet` | Open purchase-order/inbound state | 2026-06-19 to 2026-08-21 | 40 | 607,871 | 22,577 | Monitoring producer; live AX `PURCHLINE`/`PURCHTABLE`/`INVENTDIM` |
| `product_info_inbound_snapshots.parquet` | Sparse historical inbound snapshots | 2024-03-19 to 2026-06-01 | 10 | 210,057 | 36,222 | Saved Product Info snapshots |
| `forecast_db/channel_sku_inventory_weekly.parquet` | Corporate positive inventory presence by channel/SKU | 2026-02-15 to 2026-08-16 | 26 | 451,358 | 29,235 | Live read-only Forecast DB `dbo.Channel_Offer_SKU_Inventory_History`; 78 pseudo-SKU rows quarantined |
| `forecast_db/direct_sku_inventory_weekly.parquet` | DIRECT-only view for KYDC activation tests | 2026-02-15 to 2026-08-16 | 26 | 394,860 | 27,361 | Derived from the same live extract; no channel collapse |
| `forecast_db/inventory_macro_history.parquet` | Corporate channel/category/season inventory regime | 2019-09-07 to 2026-08-01 | 26 | 22,161 | n/a | Live read-only Forecast DB `dbo.Inventory_History`; sparse macro history |

The AX table is a rolling-retention source. On 2026-08-21 the live table exposed
2026-06-08 through 2026-08-21; the append-safe merge retained the locally saved
April-June dates and replaced 334,542 overlapping `SnapshotDate + SKU` keys.
The resulting 52.45 MB Parquet remains below the repository's practical 90 MB
portable-artifact ceiling.

Pick-face and warehouse facts are intentionally separate. A warehouse-positive
SKU is not necessarily in a direct-pick location, and absence from the pick face
is not equivalent to zero warehouse inventory.

## Origin-Safe Multi-Window Results

All windows use snapshot-specific corporate category attributes, pre-origin
DirectPick history, the saved corporate vintage, and the latest evidence
snapshot no later than origin minus one day. The metric is 14-day SKU WAPE.

### AX warehouse availability + Product Info inbound

Origins: 9, from 2026-04-07 through 2026-06-02.

| Candidate | Mean WAPE | Mean sold-unit coverage |
| --- | ---: | ---: |
| Corporate raw | 1.4922 | 28.53% |
| Corporate total / recent SKU shape | **0.7043** | 90.07% |
| Category-pool corporate anchor | 0.7272 | 89.36% |
| Category-pool anchor + activation | 0.7884 | 91.56% |

Against the same category-pool anchor, activation worsened mean WAPE by 0.0612
(8.4%) while increasing coverage by 2.20 percentage points. The current
turnover gate was fully open (`gate_factor = 1.0`) on all nine origins.

### Pick-face inventory + Product Info inbound

Origins: 3, from 2026-05-19 through 2026-06-02. This smaller sensitivity is
bounded by the first preserved pick-face snapshot on 2026-05-13.

| Candidate | Mean WAPE | Mean sold-unit coverage |
| --- | ---: | ---: |
| Corporate raw | 1.3611 | 45.80% |
| Corporate total / recent SKU shape | **0.6130** | 92.19% |
| Category-pool corporate anchor | 0.6412 | 92.03% |
| Category-pool anchor + activation | 0.7124 | 93.73% |

Activation worsened mean WAPE by 0.0712 (11.1%) while increasing coverage by
1.70 percentage points. The gate was again fully open on every covered origin.
Base-arm WAPE values matched the AX run exactly on the three shared origins,
which verifies the paired shared-precomputation path.

## Interpretation

The inventory history is important precisely because a single July transition
window made activation look promising. The broader origin-safe evidence shows
that the current turnover ratio is not a reliable season gate: normal
mid-season supply sets still appear to have high turnover relative to SKUs with
recent picks, so the rule injects too many low-probability SKUs. Coverage rises,
but precision falls enough to worsen WAPE.

The next version should retain separate features rather than unioning supply
sets:

1. `HasPickFaceInventory`, pick-face quantity, occupied-location count, and
   days since last pick-face presence.
2. `HasWarehouseInventory`, net available quantity, and days since warehouse
   availability changed.
3. Open-inbound units, delivery horizon, and days since the inbound snapshot.
4. A dated assortment/first-seen signal independent of supply presence.
5. An occurrence threshold calibrated on precision/coverage before any unit
   allocation boost.

## Corporate Forecast DB Inventory History

The 2026-06-17 catalog and a live 2026-08-21 date profile confirm two additional
tables:

- `dbo.Channel_Offer_SKU_Inventory_History`: 451,436 cataloged rows on 2026-08-21 at
  `CalendarDate + CHANNEL + OFFERID + SKU`, with `Avail_OH`; live business-date
  range **2026-02-15 through 2026-08-16**.
- `dbo.Inventory_History`: 22,161 cataloged rows on 2026-08-21 at `AsOfDate`, with
  channel, division, department, season, available units, and available cost;
  live business-date range **2019-09-07 through 2026-08-01**.

The live profile used the cached Azure CLI access-token connector documented in
`FORECAST_DB_AUTHENTICATION.md`. The SKU-level table is the next extraction
priority because it begins before the locally retained AX warehouse history.
The aggregate table can support macro availability/regime features but cannot
resolve SKU activation.

### Live grain and quality profile

The SKU-level history contains 26 Sunday snapshots and 29,238 distinct SKUs.
The dates run from 2026-02-15 through 2026-08-16, with 2026-03-01 absent. It is
a positive-presence table: all 451,436 rows have `Avail_OH > 0`; there are no
explicit zero or negative rows. Therefore a missing SKU/channel/date can only
be treated as zero after confirming that the snapshot date itself exists and
the channel is in scope.

The natural key `CalendarDate + CHANNEL + OFFERID + SKU` has no duplicate rows.
There are 5,265 SKU/date keys present in both channels, and 5,071 of those have
different quantities. There are no multiple-offer rows within a
`CalendarDate + CHANNEL + SKU` key. The safe analytical grain is therefore
`CalendarDate + CHANNEL + SKU`: preserve DIRECT and RETAIL separately rather
than summing or otherwise collapsing them. DIRECT contributes 394,938 rows and
27,364 distinct SKUs; RETAIL contributes 56,498 rows and 2,173 distinct SKUs.

Exclude three explicit pseudo-SKU/offer records before using quantities:
`30991`, `3333`, and `9999`. Each occurs once per snapshot under DIRECT and has
an approximately 100-billion- or 1-trillion-unit placeholder balance. Together
they produce 78 invalid rows and dominate the unfiltered inventory total.

The aggregate `dbo.Inventory_History` table is not a continuous seven-year
series. Its 26 dates consist of two 2019 snapshots, a gap to 2024-09-07, and
then approximately monthly snapshots through 2026-08-01. It is useful as a
coarse channel/category/season inventory-regime series, not as SKU availability
evidence or a daily stockout signal.

## Forecast DB DIRECT Inventory Sensitivity

The compact DIRECT history extends the paired origin-safe replay to 16 origins,
from 2026-02-18 through 2026-06-02. Each origin uses the latest Sunday snapshot
strictly before the origin. Snapshot age is two or three days except for the
missing 2026-03-01 snapshot, which makes the 2026-03-03 evidence nine days old.

### Inventory only

| Candidate | Windows | Mean WAPE | Mean sold-unit coverage |
| --- | ---: | ---: | ---: |
| Corporate raw | 16 | 1.5381 | 26.16% |
| Corporate total / recent SKU shape | 16 | **0.8078** | 87.30% |
| Category-pool corporate anchor | 16 | 0.8268 | 86.69% |
| Category-pool anchor + DIRECT inventory activation | 16 | 0.8183 | 87.09% |

Against the paired category-pool anchor, inventory-only activation improved
mean WAPE by 0.0085 (1.0%) and coverage by 0.39 percentage points. It improved
WAPE on all 16 origins, but the effect is small and the simpler global recent
shape remains better. The eight non-overlapping origins tell the same story:
0.8053 anchor WAPE versus 0.7971 with inventory activation.

### DIRECT inventory plus historical Product Info inbound

Adding the historical Product Info inbound fact worsened mean WAPE to 0.8435
while raising coverage to 89.24%. For the first five origins, the latest inbound
snapshot was still 2024-06-20. Treating that stale observation as current open
inbound forced the activation gate fully open on every origin and erased the
small inventory-only gain.

This is the clearest source-separation result from the review: Forecast DB
DIRECT inventory is a modestly useful occurrence signal, while an unrestricted
union with stale inbound is harmful. Do not promote from this retrospective
sensitivity. A future candidate must enforce per-source age limits and retain
separate inventory, pick-face, and inbound features.

## Reproduction

```powershell
uv run python scripts/python/forecast_inventory_history.py `
  --start-date 2026-06-08 --end-date 2026-08-21 --merge-existing

uv run python scripts/python/sync_monitoring_forecast_artifacts.py `
  --families inventory inbound

uv run python scripts/python/backfill_monitoring_pickface_history.py

uv run python scripts/python/forecast_multiwindow_corporate_backtest.py `
  --min-start 2026-04-01 --max-start 2026-06-02 `
  --output-dir Output/ForecastAccuracy/handoff_eval/multiwindow_corporate_backtest_ax_activation

uv run python scripts/python/forecast_multiwindow_corporate_backtest.py `
  --min-start 2026-05-13 --max-start 2026-06-02 `
  --activation-inventory-path Output/ForecastAccuracy/inventory/pickface_inventory_sku_day.parquet `
  --activation-inbound-path Output/ForecastAccuracy/inbound/product_info_inbound_snapshots.parquet `
  --output-dir Output/ForecastAccuracy/handoff_eval/multiwindow_corporate_backtest_pickface_activation

uv run python scripts/python/forecast_corporate_inventory_history.py

uv run python scripts/python/forecast_multiwindow_corporate_backtest.py `
  --min-start 2026-02-15 --max-start 2026-06-02 `
  --activation-inventory-path Output/ForecastAccuracy/inventory/forecast_db/direct_sku_inventory_weekly.parquet `
  --no-activation-inbound `
  --output-dir Output/ForecastAccuracy/handoff_eval/multiwindow_corporate_backtest_forecast_db_inventory_only
```
