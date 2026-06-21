# Historical Forecast Accuracy Results

Generated: 2026-06-08

This note preserves the multi-year forecast accuracy results so the slow
historical rebuild does not need to be rerun just to recover the current
business answer.

## Data Basis

- Forecast source: recovered weekly `Forward Replenishment` CSV uploads from
  `\\tk-ax-report\Documents\ForwardReplen\Error`, `Complete`, and `Processing`,
  deduped by file hash and weekly forecast signature.
- Actual units: completed `DirectPick` work from AX, using
  `WHSWORKLINE.MODIFIEDDATETIME` as the picked-date basis.
- Accuracy window: each forecast snapshot's own 14-day forecast window,
  starting from `ForecastStartDate`.
- Trend filters: only snapshots with `CompleteActualWindow = true` are included
  in the yearly and monthly comparisons below.
- Generated heavy artifacts remain local under `Output/ForecastAccuracy/`.

## Executive Read

The forecast did not mainly fail by total units. It failed by SKU coverage.
Starting in early 2026, a very large share of picked units were for SKUs with
zero forecast in the uploaded snapshot.

The degradation starts in January 2026, but the clear inflection point is
February 2026.

## Yearly Summary

| Year | Snapshots | Forecast Units | Sold Units | Net Error Units | Net Error Pct | WAPE | Sold Unit Coverage By Forecast | Zero-Forecast Sold Unit Pct |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2022 | 6 | 1,279,643 | 2,078,789 | 799,146 | 38.4% | 0.75 | 58.8% | 41.2% |
| 2023 | 36 | 17,497,944 | 16,861,051 | -636,893 | -3.8% | 0.58 | 93.8% | 6.2% |
| 2024 | 45 | 20,297,244 | 16,672,305 | -3,624,939 | -21.7% | 0.68 | 91.9% | 8.1% |
| 2025 | 48 | 18,550,392 | 15,609,865 | -2,940,527 | -18.8% | 0.73 | 88.7% | 11.3% |
| 2026 | 20 | 4,213,316 | 4,382,573 | 169,257 | 3.9% | 1.45 | 28.6% | 71.4% |

## Inflection Point

The month-level trend shows the operational break:

| Month | Snapshots | Forecast Units | Sold Units | WAPE | Sold Unit Coverage By Forecast | Zero-Forecast Sold Unit Pct |
|---|---:|---:|---:|---:|---:|---:|
| 2025-12 | 5 | 2,502,672 | 2,089,463 | 0.74 | 88.2% | 11.8% |
| 2026-01 | 4 | 601,998 | 719,760 | 0.76 | 65.2% | 34.8% |
| 2026-02 | 4 | 712,451 | 672,057 | 1.56 | 28.4% | 71.6% |
| 2026-03 | 5 | 1,031,387 | 970,498 | 1.65 | 20.8% | 79.2% |
| 2026-04 | 4 | 840,408 | 1,080,902 | 1.39 | 20.6% | 79.4% |
| 2026-05 | 3 | 1,027,072 | 939,356 | 1.74 | 17.8% | 82.2% |

Conclusion: January 2026 is the warning month. February 2026 is the inflection.
After February, 70% to 83% of picked units were on SKUs that had zero forecast
in the weekly snapshot.

## Match To BA Numbers

The May 2024 BA examples match very closely, which supports the historical data
pull and the DirectPick modified-date basis.

| Forecast | BA Forecast Units | Our Forecast Units | BA Sold Units | Our Sold Units | Notes |
|---|---:|---:|---:|---:|---|
| 2024-05-07 | 241,685 | 241,685 | 224,767 | 224,041 | Very close |
| 2024-05-21 | 428,942 | 428,942 | 374,084 | 373,186 | Very close |
| 2026-05-04 | 229,667 | 229,667 | 220,548 | 224,193 | Close using forecast-start window |
| 2026-05-18 | 355,414 | 355,414 | 404,479 | 377,181 | BA likely used a file-date-inclusive window |

For the 2026-05-18 forecast, the forecast file starts on 2026-05-19. Using that
forecast-start window gives 377,181 sold units. Using a file-date-inclusive
window of 2026-05-18 through 2026-06-01 gives 410,596 sold units, which is much
closer to the BA's 404,479.

## BA-Style Variance Buckets

| Forecast | Forecasted SKUs | Sold SKUs | Forecast Units | Sold Units | Variance 0 | Variance +/- 1-5 | Variance +/- 6-10 | Variance +/- 11-25 | Variance +/- 26-50 | Variance +/- 51-75 | Variance +/- 76-99 | Variance +/- 100+ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024-05-07 | 9,765 | 12,206 | 241,685 | 224,041 | 497 | 5,908 | 2,716 | 2,425 | 666 | 143 | 54 | 30 |
| 2024-05-21 | 9,395 | 12,041 | 428,942 | 373,186 | 303 | 4,627 | 2,269 | 2,816 | 1,244 | 452 | 203 | 300 |
| 2026-05-04 | 5,132 | 13,617 | 229,667 | 224,193 | 21 | 4,879 | 2,184 | 4,097 | 2,283 | 808 | 350 | 562 |
| 2026-05-18 | 4,794 | 13,403 | 355,414 | 377,181 | 17 | 2,920 | 1,464 | 4,216 | 3,094 | 1,356 | 565 | 1,193 |

## Saved Compact Tables

The compact tracked CSV tables are saved next to this note:

- `historical_forecast_accuracy_year_summary.csv`
- `historical_forecast_accuracy_inflection_months.csv`
- `historical_forecast_accuracy_ba_comparison.csv`

The full generated outputs are local-only and ignored by Git:

- `Output/ForecastAccuracy/history/forecast_accuracy_snapshot_summary.csv`
- `Output/ForecastAccuracy/history/forecast_accuracy_variance_buckets.csv`
- `Output/ForecastAccuracy/history/parquet/*.parquet`

To rebuild the heavy artifacts only when necessary, use
`Docs/operations/FORECAST_ACCURACY_DB_RUNBOOK.md`.
