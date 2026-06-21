# Velocity Policy Shadow Findings - 2026-06-16 Refresh

## Scope

This refresh adds the confirmed June 16, 2026 AX-effective forecast upload to
the shadow-only velocity policy panel. It does not change production ingestion
logic, the 36-column AX payload, location directives, or the approved
location-zone map.

Confirmed upload:

- Source: `Output/Ingestion/FwdDemandCSV_2026-06-16.csv`
- AX-effective timestamp: `2026-06-16 16:00 EDT`
- Registration hash prefix: `99541d05594b`
- AX staging count: `31,720` `Forecast replenishment` records
- Notes: Forecast replenishment DIXF batch completed at 4:00 PM EDT.

The refreshed builder commands were:

```powershell
uv run python scripts/python/monitoring/forecast_slottier_history.py import `
  --file Output/Ingestion/FwdDemandCSV_2026-06-16.csv `
  --confirm-upload `
  --effective-from-est "2026-06-16 16:00" `
  --notes "Confirmed AX Forecast replenishment DIXF upload; batch job finished 2026-06-16 16:00 EDT; 31,720 Forecast replenishment records inserted in staging"

uv run python scratch/build_velocity_policy_shadow_panel.py --overwrite
uv run python scratch/backtest_velocity_stability_controls.py --overwrite
```

## Refreshed Artifacts

Tracked shadow outputs remain under:

```text
Output/Monitoring/shadow_velocity_policy/
```

| Artifact | Rows |
| --- | ---: |
| SKU-snapshot panel | 237,482 |
| Changed-tier events | 18,711 |

The confirmed SCD history was also refreshed in
`Output/Monitoring/Monitoring_History.db` and the Power BI CSV exports under
`Output/Monitoring/exports/`.

## Interval Churn

| Confirmed interval | Shared SKUs | Velocity changes | Rate | Promotions | Demotions | Multi-tier jumps | `C -> AA` | `AA -> C` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| May 4 -> May 12 | 33,648 | 2,770 | 8.23% | 1,553 | 1,217 | 119 | 18 | 1 |
| May 12 -> May 18 | 34,102 | 3,149 | 9.23% | 1,989 | 1,160 | 128 | 1 | 28 |
| May 18 -> May 28 | 33,667 | 4,669 | 13.87% | 2,995 | 1,674 | 213 | 18 | 16 |
| May 28 -> June 1 | 32,220 | 4,153 | 12.89% | 2,489 | 1,664 | 167 | 0 | 29 |
| June 1 -> June 11 | 31,491 | 2,227 | 7.07% | 1,202 | 1,025 | 39 | 1 | 10 |
| June 11 -> June 16 | 31,056 | 1,743 | 5.61% | 705 | 1,038 | 63 | 11 | 0 |

The June 11 -> June 16 interval is now the lowest adjacent-snapshot churn rate
in the confirmed panel. This does not prove the inherited thresholds are good;
it means the latest upload does not worsen the churn pattern that triggered
the velocity-policy concern.

## June 16 Tier Counts

| Velocity | SKUs | FD14 units | Recent 56d physical touches |
| --- | ---: | ---: | ---: |
| AA | 3,957 | 184,677 | 2,839 |
| A | 4,432 | 77,477 | 2,124 |
| B | 2,494 | 34,231 | 1,074 |
| C | 20,837 | 136,478 | 3,020 |

The sale-period upload materially increases FD14 units, especially in `AA` and
`C`, while the count of `Active` putaway SKUs remains low in the AX upload
contract. Treat this as a confirmed production baseline for later comparison,
not as evidence that the velocity criteria are calibrated.

## Stability-Control Refresh

| Shadow routing control | Applied changes | Direct `AA -> C` | Reversed within 14 days | Final differences versus June 16 target |
| --- | ---: | ---: | ---: | ---: |
| Legacy immediate | 18,841 | 88 | 715 | 0 |
| Two confirmations for all changes | 12,993 | 174 | 13 | 1,710 |
| Three confirmations for all changes | 8,065 | 156 | 0 | 3,735 |
| Immediate promotions, two-confirmation demotions | 16,334 | 174 | 142 | 1,054 |
| Immediate promotions, two-confirmation staged demotions | 16,807 | 0 | 143 | 1,256 |
| Two-confirmation promotions, three-confirmation staged demotions | 11,501 | 0 | 6 | 2,936 |

The same candidate remains worth watching: immediate promotions with
two-confirmation staged demotions. It removes direct operational `AA -> C`
jumps while keeping promotions immediate. After adding the June 16 snapshot,
its visible cost is `1,256` final routing differences versus the June 16
forecast target.

This is still triage, not a selected production policy. Seven confirmed
snapshots are better than six, but they are still too few to finalize a new
velocity rule. The next decision should wait for more confirmed uploads, the
sale-period actuals, and comparison against the independent forecast-model
candidate signals.
