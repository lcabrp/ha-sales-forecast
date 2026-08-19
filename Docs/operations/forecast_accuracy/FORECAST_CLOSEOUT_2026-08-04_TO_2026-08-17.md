# Forecast Closeout: 2026-08-04 through 2026-08-17

Completed 2026-08-19. This closeout preserves the original frozen corporate
vintage, the weekly update that was actually imported into AX during the
window, and the direct scheduled-overlap comparison. No replacement-model
candidate was frozen before either forecast start, so this is not a
corporate-versus-category-pool contest.

## Decision

- The August 3 corporate vintage is the authoritative original 14-day
  baseline. It underforecast volume by 84,310 units (36.86%) and has 119.37%
  SKU WAPE; it does not establish corporate as a reliably accurate total-volume
  owner for this event window.
- The August 12 AX overlay improves allocation materially when used from its
  first full operating day, August 13: SKU WAPE falls to 105.25%, sold-unit
  coverage rises to 62.31%, forecast-SKU use rises to 88.10%, and zero-demand
  forecast units fall to 8.47%. Its volume underforecast is worse, however:
  87,017 units (38.05%). It is an operational vintage update, not a new model
  or a replacement-model promotion.
- Over its scheduled August 11-17 overlap, the overlay improves allocation
  versus the original while lowering planned volume by 3,141 units. It cuts
  WAPE from 147.80% to 120.86%, but worsens volume bias from -16.69% to
  -19.32%.
- Do not replace the corporate production process or claim a category-pool
  winner. Freeze the pre-registered corporate-anchored category-pool challenger
  before the next clean corporate forecast start, then compare it against the
  same frozen corporate vintage.

## Evaluation Contract

| Item | Value |
|---|---|
| Original forecast vintage | `FwdDemandCSV_2026-08-03_velocity_frozen.csv`, 26,291 rows, 144,405 units, forecast dates 2026-08-04 through 2026-08-17 |
| Original source hash | `a4d0ed79b91e7b5ce1c15d873c33ddcc7650012eef1295cfed51fb5330d92340` |
| Weekly overlay vintage | `FwdDemandCSV_2026-08-12_velocity_frozen.csv`, 28,016 rows, 189,569 units, forecast dates 2026-08-11 through 2026-08-24 |
| Overlay source hash | `f1c7c46ea3284b1c6cfed2f0e5c54f7d7a1b0277a12a45bbd6d146767c693b60` |
| Original-vintage score window | 2026-08-04 through 2026-08-17, 14 Eastern days |
| Monitoring completeness | `ha-kydc-monitoring/Output/Monitoring/Monitoring_History.db`, 228,715 Pick units, 14/14 days |
| SKU/day actual source | Read-only `prodaxsql2` / `DAX_PROD` fallback, with the monitoring DirectPick filters and Eastern dates |
| SKU/day actual | 85,638 rows, 14,085 SKUs, 228,715 units |
| Reconciliation | AX SKU/day actual minus monitoring aggregate = 0 units |
| Category crosswalk | Tracked July 7 handoff `sku_ledger.db`, 20,449 rows; category scores are diagnostic only |

The normal and velocity-frozen CSVs have identical FD1-FD14 demand values for
both August vintages. The frozen versions above are therefore the correct
upload-shaped inputs and do not alter the demand being scored.

### Operational-vintage cutoff

The current AX `HAFORECASTREPLENISHMENTTABLE` shows the August 11-start overlay
was modified by `latmpadm` from 2026-08-12 19:35:31 through 19:41:30 UTC
(15:35:31 through 15:41:30 Eastern). At daily grain, August 12 cannot be
cleanly split. The operational-vintage score therefore retains the original
through August 12 and uses the overlay from August 13, the first complete
operating day. The manifest records this rule and the source hashes.

## 14-Day Results

| Status | Candidate | Units | Bias | SKU WAPE | Daily WAPE | Forecast-positive SKUs | SKU use rate | Sold-unit coverage | Zero-demand forecast units |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Frozen original baseline | August 3 corporate vintage | 144,405 | -36.86% | 119.37% | 57.04% | 5,825 | 85.87% | 54.04% | 12.34% |
| Operational-vintage score | Original Aug 4-12 + overlay Aug 13-17 | 141,698 | -38.05% | 105.25% | 55.79% | 6,796 | 88.10% | 62.31% | 8.47% |

The operational update improves SKU allocation even though it forecasts 2,707
fewer units. It covers 18,921 more sold units than the original forecast
(142,520 versus 123,599), but misses 2,707 additional total units. The largest
daily overage remains August 16, when 15,994 planned units met 817 actual
units; both forecast timing and volume need attention, not just SKU allocation.

## August 11-17 Overlay Impact

This comparison holds the actuals and scheduled seven dates fixed. It measures
the overlay's forecast revision, not a model promotion. The original daily
vintage planned 99,288 units; the overlay planned 96,147, down 3,141 units
(3.16%).

| Candidate on scheduled overlap | Units | Bias | SKU WAPE | SKU use rate | Sold-unit coverage | Zero-demand forecast units |
|---|---:|---:|---:|---:|---:|---:|
| August 3 original | 99,288 | -16.69% | 147.80% | 81.96% | 45.40% | 18.58% |
| August 12 overlay | 96,147 | -19.32% | 120.86% | 88.81% | 55.77% | 6.20% |

The overlay improves WAPE by 26.93 points, SKU use by 6.85 points, coverage by
10.37 points, and reduces zero-demand forecast units by 12.37 points. The
tradeoff is an additional 3,141-unit underforecast over those seven dates.

## Durable Evidence

The complete pack is under:

```text
Output/ForecastAccuracy/handoff_eval/forward_2026-08-04_closeout/
```

| Directory or file | Purpose |
|---|---|
| `input_vintages/corporate_vintage_manifest.json` | Source paths/hashes, FD totals, AX cutover evidence, and the first-full-day rule |
| `input_vintages/*.parquet` | Immutable long-form original, operational-vintage, and scheduled-overlap forecast inputs |
| `original_and_operational/` | 14-day actual, scorecards, daily totals, category diagnostics, and SKU comparison |
| `overlay_scheduled_overlap/` | August 11-17 overlay score against the same saved actuals |
| `scheduled_overlap_baselines/` | August 11-17 original/operational baselines for direct overlay impact |

The pack has 21 files totaling approximately 1.35 MB; the largest individual
file is 279 KB. All files are compact portable evidence and belong in Git.
