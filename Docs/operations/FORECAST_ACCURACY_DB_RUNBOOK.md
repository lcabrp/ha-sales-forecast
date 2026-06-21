# Forecast Accuracy Database Runbook

This runbook rebuilds the local forecast accuracy SQLite database from forecast
CSV snapshots and AX pick history. Use it when working from a different PC or
when the generated database is missing.

Current saved historical findings are tracked here:

```text
Docs/operations/forecast_accuracy/historical_forecast_accuracy_results.md
Docs/operations/forecast_accuracy/historical_forecast_accuracy_year_summary.csv
Docs/operations/forecast_accuracy/historical_forecast_accuracy_inflection_months.csv
Docs/operations/forecast_accuracy/historical_forecast_accuracy_ba_comparison.csv
```

The database is a generated local artifact:

```text
Output/ForecastAccuracy/Forecast_Accuracy.db
```

Do not commit this database to GitHub. It is large and can be rebuilt from the
tracked scripts, forecast snapshots, and AX.

## Prerequisites

1. Clone or pull the latest `ha-sales-forecast` repo.
2. Confirm the PC can run the project with `uv`.
3. Confirm the PC can connect to AX SQL Server with Windows authentication.
4. Confirm the forecast CSV snapshots are present in one or both locations:

```text
Output/Ingestion/FwdDemandCSV_*.csv
Output/Monitoring/forecast_snapshots/confirmed_raw/FwdDemandCSV_*.csv
```

The confirmed raw folder can contain hash-suffixed files such as:

```text
FwdDemandCSV_2026-05-04_31c1516662f7.csv
```

## Build The Database

From the repo root:

```powershell
uv run python scripts\python\forecast_accuracy.py import-forecasts
```

Then import the confirmed raw forecast snapshots, if present:

```powershell
uv run python scripts\python\forecast_accuracy.py import-forecasts --input-dir Output\Monitoring\forecast_snapshots\confirmed_raw
```

The importer skips snapshots that are already present by exact source file and
hash. It also skips exact duplicate file content when the same CSV appears under
two filenames. Use `--overwrite` only when intentionally replacing a previously
imported snapshot.

## Duplicate Snapshot Notes

There may be several close-dated forecast CSVs because the ingestion pipeline can
rerun against the same uploaded forecast after map/classification changes. For
forecast accuracy, do not treat every close-dated file as a separate weekly
upload.

Use this rule of thumb:

1. If the same content hash appears twice, keep only one copy.
2. If multiple files have the same `ForecastStartDate`, forecasted SKU count, and
   total forecast units, treat them as the same weekly forecast quantity.
3. Prefer `Output/Monitoring/forecast_snapshots/confirmed_raw` when available,
   because those are the preserved confirmed uploads.
4. Use `Output/Ingestion` files as fallback or for pipeline/debug comparisons.

Known examples from June 2026 project data:

```text
2026-05-18 Ingestion and confirmed_raw are exact duplicates.
2026-06-01 Ingestion and confirmed_raw are exact duplicates.
2026-05-04 confirmed_raw and 2026-05-06 Ingestion have the same forecast quantities.
2026-05-12 confirmed_raw, 2026-05-12 Ingestion, and 2026-05-13 Ingestion have the same forecast quantities.
2026-05-28 confirmed_raw and Ingestion have the same forecast quantities.
```

## Historical AX Processed Files

AX/DIXF records the `Forward Replenishment` import group with these file paths:

```text
Pickup/root:
\\tk-ax-report\Documents\ForwardReplen

Processing:
\\tk-ax-report\Documents\ForwardReplen\Processing

Complete:
\\tk-ax-report\Documents\ForwardReplen\Complete

Error:
\\tk-ax-report\Documents\ForwardReplen\Error
```

Despite the folder name, many successfully processed historical forecast CSVs
are stored in the `Error` folder. Do not assume files in `Error` failed. Use AX
DMF execution history (`DMFDEFINITIONGROUPEXECUTION`) to confirm the import
status, row counts, and execution timestamp.

As of the June 2026 review:

```text
\\tk-ax-report\Documents\ForwardReplen\Error
```

contains many weekly forecast CSVs from 2023 through 2026, including the May
2024 files referenced in the forecast-accuracy discussion:

```text
Fwd Demand CSV 5724.csv
Fwd Demand CSV 51424.csv
Fwd Demand CSV 52124.csv
Fwd Demand CSV 52924.csv
```

The `Complete` folder was browseable but mostly contained older 2022 files. The
`Processing` folder also contained some older 2023 files and one 2026 working
file. For multi-year forecast accuracy, start with the `Error` folder, then use
`Complete` and `Processing` as secondary recovery locations.

AX also references internal DIXF copies such as:

```text
\\TkProdFile01\AX_DIXF\{GUID}.csv
```

Access to that share may be restricted. The DMF execution history can still
provide the GUID path, file name, row counts, staging/target status, and created
timestamp even when the share itself cannot be browsed.

## Import Actual Picked Units

For forecast accuracy, use completed pick timing:

```powershell
uv run python scripts\python\forecast_accuracy.py import-actuals --start-date 2026-05-04 --end-date 2026-06-01 --date-field modified
```

`--date-field modified` uses `WHSWORKLINE.MODIFIEDDATETIME`, which is the better
proxy for when the piece was picked. `--date-field created` uses
`WHSWORKTABLE.CREATEDDATETIME`, which reflects when AX created the work during
wave processing and can be several days earlier during high-volume periods.

The actuals logic sums completed DirectPick work:

```text
WHSWORKTABLE + WHSWORKLINE + INVENTDIM
WORKSTATUS = 4
WORKTYPE = 1
WORKCLASSID = 'DirectPick'
SoldUnits = SUM(WHSWORKLINE.QTYWORK)
```

The import automatically uses `DAX_Archive.arc` for archived dates and
`DAX_PROD.dbo` for live dates, based on the archive boundary.

## Check The Load

```powershell
uv run python scripts\python\forecast_accuracy.py summary
```

For a deeper row-count check:

```powershell
uv run python -c "import sqlite3; c=sqlite3.connect('Output/ForecastAccuracy/Forecast_Accuracy.db'); print(c.execute('select count(*) from forecast_snapshot_files').fetchone()); print(c.execute('select count(*) from forecast_sku_snapshot').fetchone()); print(c.execute('select count(*) from forecast_sku_day').fetchone()); print(c.execute('select DateBasis, min(ActualDate), max(ActualDate), sum(SoldUnits) from actual_sku_day group by DateBasis').fetchall())"
```

## Query Notes

Forecast files have a file date and a forecast start date. For example, the
confirmed 2026-05-04 file forecasts 2026-05-05 through 2026-05-18.

Use forecast start date for the 14-day accuracy window:

```sql
a.ActualDate >= fs.ForecastStartDate
AND a.ActualDate < date(fs.ForecastStartDate, '+14 day')
```

The SQLite view `vw_forecast_actual_14day` currently uses the `created` actuals
basis for backward compatibility. For forecast accuracy analysis, prefer custom
queries against `actual_sku_day` with `DateBasis = 'modified'`.

## Storage Notes

SQLite is convenient for local joins and repeatable analysis, but the generated
database can exceed 500 MB. Keep it local. If a portable extract is needed later,
write filtered fact tables to Parquet and keep those as generated artifacts too
unless they are small enough and intentionally approved for Git.

## Multi-Year Historical Dataset

For multi-year analysis, use Parquet as the primary storage format. It is much
smaller than SQLite for the long fact tables and easier to rebuild on different
PCs.

Build the historical forecast dataset from AX folders:

```powershell
uv run python scripts\python\forecast_history_dataset.py collect-forecasts --since 2022-01-01
```

This creates:

```text
Output/ForecastAccuracy/history/raw_forecasts/
Output/ForecastAccuracy/history/parquet/forecast_snapshot_files.parquet
Output/ForecastAccuracy/history/parquet/forecast_sku_snapshot.parquet
Output/ForecastAccuracy/history/parquet/forecast_sku_day.parquet
Output/ForecastAccuracy/history/forecast_snapshot_manifest.csv
```

The raw folder keeps one local copy per unique source-file hash. The Parquet
fact tables only include selected weekly snapshots so duplicate/reprocessed
uploads do not distort accuracy trends.

Pull completed-pick actuals:

```powershell
uv run python scripts\python\forecast_history_dataset.py collect-actuals --start-date 2022-08-01 --end-date 2026-06-15 --date-field modified
```

This creates:

```text
Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet
Output/ForecastAccuracy/history/actual_sku_day_modified_summary.csv
```

Use `modified` as the default basis because `WHSWORKLINE.MODIFIEDDATETIME`
tracks pick completion better than work creation. The script automatically
splits AX pulls between `DAX_Archive.arc` and `DAX_PROD.dbo` using the archive
boundary.

Ready-to-use accuracy summaries are written to:

```text
Output/ForecastAccuracy/history/parquet/forecast_accuracy_snapshot_summary.parquet
Output/ForecastAccuracy/history/parquet/forecast_accuracy_category_summary.parquet
Output/ForecastAccuracy/history/parquet/forecast_accuracy_variance_buckets.parquet
Output/ForecastAccuracy/history/forecast_accuracy_snapshot_summary.csv
Output/ForecastAccuracy/history/forecast_accuracy_variance_buckets.csv
```

Build or refresh those summaries after collecting forecasts and actuals:

```powershell
uv run python scripts\python\forecast_history_dataset.py build-summaries
```

Filter snapshot summaries to `CompleteActualWindow = true` before comparing
accuracy trends. The latest forecast windows may extend past the last available
pick date.
