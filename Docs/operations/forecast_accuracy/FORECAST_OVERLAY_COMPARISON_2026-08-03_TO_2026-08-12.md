# Corporate Forecast Overlay Comparison: August 3 vs August 12, 2026

Prepared 2026-08-17 from the immutable corporate-upload CSVs owned by
`ha-ingestion-pipeline`. This is a forecast-to-forecast comparison only; it
does **not** score either version against actual DirectPick demand.

## Sources

| Vintage | Source | Forecast start | File modification time |
|---|---|---|---|
| Original | `ha-ingestion-pipeline/Output/Ingestion/FwdDemandCSV_2026-08-03.csv` | 2026-08-04 | 2026-08-03 15:18 ET |
| Overlay | `ha-ingestion-pipeline/Output/Ingestion/FwdDemandCSV_2026-08-12.csv` | 2026-08-11 | 2026-08-12 15:23 ET |

The overlay's `ForecastStartDate` is August 11, but the actual AX upload time
must determine the first date it was operationally in force. The table below
compares the scheduled common window, August 11-17.

## Scheduled overlap comparison

| Date | August 3 vintage | August 12 overlay | Change | Change % |
|---|---:|---:|---:|---:|
| 2026-08-11 | 13,136 | 13,097 | -39 | -0.30% |
| 2026-08-12 | 13,492 | 13,097 | -395 | -2.93% |
| 2026-08-13 | 12,810 | 13,097 | +287 | +2.24% |
| 2026-08-14 | 12,810 | 13,097 | +287 | +2.24% |
| 2026-08-15 | 16,777 | 14,494 | -2,283 | -13.61% |
| 2026-08-16 | 16,777 | 15,994 | -783 | -4.67% |
| 2026-08-17 | 13,486 | 13,271 | -215 | -1.59% |
| **Total** | **99,288** | **96,147** | **-3,141** | **-3.16%** |

The overlay lowers the planned overlap total, especially on August 15-16, but
widens its SKU footprint:

| Measure, August 11-17 | August 3 vintage | August 12 overlay |
|---|---:|---:|
| Positive SKU/day rows | 21,298 | 34,780 |
| Distinct positive SKUs | 4,469 | 5,192 |
| Newly positive distinct SKUs | — | 3,241 |
| Removed distinct SKUs | — | 975 |

This is evidence of a materially revised allocation, not evidence that the
overlay was better. On or after August 18, score both versions against the same
actuals: preserve the original-vintage 14-day score, score the active forecast
by actual AX-vintage cutoff, and report the overlay's accuracy change only on
the dates controlled by it.
