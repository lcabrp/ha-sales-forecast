# Forecast SlotTier History For Monitoring And Power BI

Weekly forecast imports can change the velocity suffix of hundreds or thousands
of SKU `SlotTier` values. Historical replenishment accuracy must therefore use
the forecast assignment that was effective when AX created the work. Joining old
work directly to the latest `HAFORECASTREPLENISHMENTTABLE.SLOTTIERVALUE` rewrites
history and can make previously correct replenishments appear incorrect.

## Storage Model

The maintained script is:

```powershell
uv run python scripts/python/monitoring/forecast_slottier_history.py
```

It writes compact SQL tables in:

```text
Output/Monitoring/Monitoring_History.db
```

Tables:

- `forecast_snapshot_versions`: one row per registered full forecast CSV,
  including SHA256 hash, observed and confirmed effective timestamps, row count,
  local-output and confirmation flags, and putaway-indicator quality metadata.
- `forecast_slottier_scd`: materialized SCD Type 2 history with effective date
  ranges.

Compressed narrow per-snapshot Parquet files are retained under:

```text
Output/Monitoring/forecast_snapshots/narrow/
```

When a snapshot is explicitly confirmed as AX-effective, the exact submitted
36-column CSV is also archived under:

```text
Output/Monitoring/forecast_snapshots/confirmed_raw/
```

Both snapshot archive folders are tracked in Git. They are compact,
immutable evidence inputs for cross-PC replay and historical reporting.

The source AX upload CSV remains unchanged. The 36-column DIXF format must not
be modified for reporting needs.

The registry records `Active`, `Reserve`, `Offsite`, and other
`PutawayIndicator` counts. A snapshot with more than 500 `Active` SKUs is
flagged with `unusually_high_active_sku_count>500`. Keep the snapshot if it
reflects an AX state that operators actually used; the warning is context for
analysis, not a reason to silently rewrite history.

## Two Timelines

The script deliberately maintains two timelines:

- `observed_local_output`: every canonical local
  `FwdDemandCSV_YYYY-MM-DD.csv`. Use this for investigation and backfill review.
- `confirmed_ax_upload`: only snapshots explicitly confirmed as AX-effective.
  Use this for Power BI and historical replenishment scoring.

The version registry keeps `ObservedEffectiveFromEST` separate from
`EffectiveFromEST`. This prevents an exploratory local output from being
mistaken for the exact AX submission when their files differ or share a
calendar date.

Local generation time is not proof that AX activated a file. The AX pickup
share moves processed files into `Complete`, `Error`, or `Processing`, but the
destination folder is not authoritative: a file moved into `Error` can still
have updated `HAFORECASTREPLENISHMENTTABLE`. Do not promote a snapshot to the
confirmed timeline without an effective AX update time.

The canonical generated files under `Output/Ingestion/` are sufficient when the
exact submitted file is already retained locally. Copy from
`\\tk-ax-report\Documents\ForwardReplen` only when a submitted canonical file is
missing locally or when its hash must be reconciled.

## Initial Backfill

Register canonical local files as unconfirmed candidates:

```powershell
uv run python scripts/python/monitoring/forecast_slottier_history.py import `
  --all-canonical
```

List registered candidates:

```powershell
uv run python scripts/python/monitoring/forecast_slottier_history.py list
```

The initial confirmed backfill was completed on 2026-06-01 from exact files
retained under `\\tk-ax-report\Documents\ForwardReplen\Error`. The folder name
is not a success indicator; each promoted payload was matched to the successful
DIXF staging count:

| AX-effective timestamp | DIXF job | Staging rows | Archived source file |
| --- | --- | ---: | --- |
| 2026-05-04 15:54 EDT | `288` | 34,627 | `FwdDemandCSV_2026-05-04.csv` |
| 2026-05-12 18:15 EDT | `304` | 35,546 | `FwdDemandCSV_2026-05-12.csv` |
| 2026-05-18 16:13 EDT | `305` | 35,300 | `FwdDemandCSV_2026-05-18.csv` |
| 2026-05-28 13:47 EDT | `306` | 34,361 | `FwdDemandCSV_2026-05-28.csv` |
| 2026-06-01 17:09:02 EDT | not recorded | 33,790 | `FwdDemandCSV_2026-06-01.csv` |
| 2026-06-11 13:02 EDT | not recorded | 32,138 | `FwdDemandCSV_2026-06-11.csv` |
| 2026-06-16 16:00 EDT | not recorded | 31,720 | `FwdDemandCSV_2026-06-16.csv` |

These boundaries use the successful DIXF staging-job completion timestamps.
Continue capturing the completion timestamp for future uploads.

When DIXF history confirms that a registered file became effective, approve it
with the actual Eastern timestamp:

```powershell
uv run python scripts/python/monitoring/forecast_slottier_history.py approve `
  --snapshot-id <unique-id-prefix> `
  --effective-from-est "2026-05-12 18:13" `
  --notes "Confirmed from AX DIXF log"
```

## Weekly Use

After an AX-effective weekly forecast update, register the exact submitted file
and its effective Eastern timestamp:

```powershell
uv run python scripts/python/monitoring/forecast_slottier_history.py import `
  --file "\\tk-ax-report\Documents\ForwardReplen\FwdDemandCSV_YYYY-MM-DD.csv" `
  --confirm-upload `
  --effective-from-est "YYYY-MM-DD HH:MM" `
  --notes "Confirmed AX Forecast replenishment DIXF upload"
```

The importer hashes the source file, stores the narrow snapshot, rebuilds both
SCD timelines, and refreshes Power BI-friendly CSV exports.

## Power BI Inputs

SQLite views:

- `vw_forecast_snapshot_versions_powerbi`
- `vw_forecast_slottier_scd_confirmed`
- `vw_forecast_slottier_current_confirmed`
- `vw_forecast_slottier_scd_observed`
- `vw_forecast_slottier_current_observed`

Portable CSV exports:

```text
Output/Monitoring/exports/forecast_snapshot_versions.csv
Output/Monitoring/exports/forecast_slottier_scd_confirmed.csv
Output/Monitoring/exports/forecast_slottier_scd_observed.csv
```

Use only `forecast_slottier_scd_confirmed.csv` or
`vw_forecast_slottier_scd_confirmed` for a production Power BI historical
accuracy graph.

Use `vw_forecast_snapshot_versions_powerbi` or
`forecast_snapshot_versions.csv` to expose source lineage and quality warnings.

As-of join rule:

```sql
work.CreatedDateTimeEST >= scd.ValidFromEST
AND (
    scd.ValidToEST IS NULL
    OR work.CreatedDateTimeEST < scd.ValidToEST
)
```

`ValidToEST` is exclusive. A velocity change therefore becomes effective at the
next confirmed upload timestamp without altering prior work.

For a shared SQL Server deployment, use:

```text
scripts/sql/Create_Forecast_SlotTier_SCD_PowerBI.sql
```

The local confirmed CSV export can be loaded into that table without giving
Power BI the full 36-column AX payload.

## Reporting Contract

Keep these metrics separate:

| Metric | Forecast reference | Purpose |
| --- | --- | --- |
| Layout adherence | Frozen deployment forecast | Did the cutover layout improve the floor? |
| Replenishment routing accuracy | Confirmed forecast effective when work was created | Did AX route work correctly at that time? |
| Current re-slotting pressure | Latest live AX forecast | What should Operations fix now? |

Category and size match remain useful stable measures. Velocity match is
valuable, but it is expected to move after each weekly forecast refresh.
