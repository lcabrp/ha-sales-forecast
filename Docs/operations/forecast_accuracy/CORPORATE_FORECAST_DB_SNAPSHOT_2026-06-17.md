# Corporate Forecast DB Snapshot

Historical provenance only. This is a point-in-time 2026-06-17 extract, not the
current corporate forecast or a model input for the July 21 shadow. The large
tracked snapshot should be moved to durable artifact storage before being
untracked; retain its manifest and extract summary in this repo.

Captured: 2026-06-17

## Source

- Server: `azprodfcast01.572f3811ca67.database.windows.net`
- Database: `Forecast`
- Auth used for the original snapshot: Microsoft Entra interactive auth
- Current default: cached Azure CLI token via `scripts/python/forecast_db_auth.py`
- Local extractor: `scripts/python/forecast_corporate_db_extract.py`

The Azure SQL server also exposes `Forecast_DEV`. The production-looking
database is `Forecast`; there is no database literally named `Forecast DB`.

## Local Snapshot

Core tables were copied to partitioned Parquet datasets here:

```text
Output/ForecastAccuracy/corporate_forecast/snapshots/20260617_173252/
```

Snapshot summary:

| Table | Rows | Parquet MB |
| --- | ---: | ---: |
| `dbo.Channel_Offer_SKU_Forecast` | 11,538,283 | 417.27 |
| `dbo.Offer_Inventory_Forecast` | 5,163,971 | 88.73 |
| `dbo.Offer_SKU_Inventory_Forecast` | 4,186,131 | 98.30 |
| `dbo.Channel_Offer_Forecast` | 1,568,932 | 31.47 |
| `dbo.Channel_SKU_SIZE_Weekly_Demand_History` | 1,367,399 | 2.87 |
| `dbo.Channel_Offer_Demand_History` | 798,916 | 17.88 |
| `dbo.Product_Dimensions_Hierarchy_Attributes` | 477,206 | 4.34 |
| `dbo.Forecast_Job_Log` | 221,038 | 1.48 |
| `dbo.Offers` | 93,435 | 1.67 |
| `dbo.Offer_Control_Table` | 41,531 | 1.64 |
| `dbo.Current_SKU_Available_DC_Inventory` | 34,388 | 0.29 |
| `dbo.On_Order` | 23,413 | 0.34 |
| `dbo.Current_Offer_Inventory` | 7,669 | 0.10 |

Total extracted: 25,522,312 rows, 666.38 MB of Parquet data.

Manifest and row summary:

```text
Output/ForecastAccuracy/corporate_forecast/snapshots/20260617_173252/manifest.json
Output/ForecastAccuracy/corporate_forecast/snapshots/20260617_173252/extract_summary.csv
```

## Reading Locally

Each table is a Parquet dataset directory. Example:

```python
from pathlib import Path
import pandas as pd

snapshot = Path("Output/ForecastAccuracy/corporate_forecast/snapshots/20260617_173252")
sku_forecast = pd.read_parquet(
    snapshot / "tables" / "dbo__Channel_Offer_SKU_Forecast",
    columns=["Channel", "OfferID", "SKU", "CalendarDate", "Net_Sales_Unit_Forecast"],
)
```

Using these local files avoids the Azure SQL connection for normal exploration.
A refresh requires a valid cached Azure CLI tenant login. See
`FORECAST_DB_AUTHENTICATION.md`.

## Refresh Command

Create or refresh the cached Hanna tenant login when needed:

```powershell
az login `
  --tenant d977da7e-372a-4369-b692-487f0d0adbe2 `
  --allow-no-subscriptions
```

Then refresh the core snapshot:

```powershell
uv run python scripts/python/forecast_corporate_db_extract.py
```

The extractor obtains an Azure SQL access token from the CLI cache. It does not
store the token or user credentials in this repository.

Useful variants:

```powershell
# Preview selected core tables without writing rows.
uv run python scripts/python/forecast_corporate_db_extract.py --dry-run

# Smoke test, 1,000 rows per table.
uv run python scripts/python/forecast_corporate_db_extract.py --max-rows-per-table 1000

# Add support tables such as allocation, promo, seasonal, store, and size libraries.
uv run python scripts/python/forecast_corporate_db_extract.py --group core --group support

# Archive/frozen/backups are intentionally opt-in because they are large.
uv run python scripts/python/forecast_corporate_db_extract.py --group archive
```

## Notes

### Compact inventory refresh (2026-08-21)

The 666 MB June core snapshot was not regenerated. Instead, the two relevant
support tables were extracted live into the tracked compact family under
`Output/ForecastAccuracy/inventory/forecast_db/`:

- 451,358 clean channel/SKU weekly rows (2.06 MB Parquet);
- 394,860 DIRECT-only rows (1.68 MB Parquet) for activation sensitivities;
- 22,161 aggregate channel/category/season inventory rows (89 KB Parquet);
- 78 billion-plus pseudo-SKU rows quarantined in a separate audit Parquet;
- compact snapshot summaries and SHA-256 provenance in `metadata.json`.

Rebuild this family with:

```powershell
uv run python scripts/python/forecast_corporate_inventory_history.py
```

- The schema has no declared foreign keys. Relationships appear to be by
  business keys such as `Channel`, `OfferID`, `SKU`, and `CalendarDate`.
- Archive, frozen, backup, and test tables were not included in the core local
  snapshot.
- Monolithic generated database snapshots are local artifacts. Compact,
  forecast-owned facts and their provenance remain trackable under the portable
  artifact contract.
