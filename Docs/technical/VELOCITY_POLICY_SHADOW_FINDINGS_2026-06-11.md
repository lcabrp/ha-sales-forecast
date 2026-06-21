# Velocity Policy Shadow Findings - 2026-06-11 Refresh

## Scope

This refresh adds the confirmed June 11, 2026 AX-effective forecast upload to
the shadow-only velocity policy panel. It does not change production ingestion
logic, AX payload structure, location directives, or the approved location-zone
map.

Confirmed upload:

- Source: `Output/Ingestion/FwdDemandCSV_2026-06-11.csv`
- AX-effective timestamp: `2026-06-11 13:02 EDT`
- Registration hash prefix: `3d58fbe5f71e`
- Notes: Forecast replenishment DIXF batch completed at 1:02 PM EDT.

The refreshed builder commands were:

```powershell
uv run python scripts/python/monitoring/forecast_slottier_history.py import `
  --file Output/Ingestion/FwdDemandCSV_2026-06-11.csv `
  --confirm-upload `
  --effective-from-est "2026-06-11 13:02" `
  --notes "Confirmed AX Forecast replenishment DIXF upload; batch job finished 2026-06-11 13:02 EDT"

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
| SKU-snapshot panel | 205,762 |
| Changed-tier events | 16,968 |

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

The June 1 -> June 11 interval is the lowest adjacent-snapshot churn rate in
the confirmed panel so far. It is still meaningful churn, but it is not the
worst case that drove the original concern.

## June 11 Tier Counts

| Velocity | SKUs | FD14 units | Frozen cutover locations |
| --- | ---: | ---: | ---: |
| AA | 3,830 | 129,237 | 3,830 |
| A | 4,650 | 52,904 | 3,555 |
| B | 2,609 | 24,838 | 2,945 |
| C | 21,049 | 83,322 | 12,403 |

The June 11 AA SKU count exactly equals the frozen cutover AA location count.
That is not a true capacity proof because required slots vary by SKU, but it is
a useful directional check: the new forecast does not push the AA SKU population
above the deployed AA location count.

## Stability-Control Refresh

| Shadow routing control | Applied changes | Direct `AA -> C` | Reversed within 14 days | Final routing differences versus June 11 target |
| --- | ---: | ---: | ---: | ---: |
| Legacy immediate | 17,058 | 87 | 601 | 0 |
| Two confirmations for all changes | 10,941 | 135 | 13 | 2,170 |
| Three confirmations for all changes | 4,905 | 58 | 0 | 5,662 |
| Immediate promotions, two-confirmation demotions | 14,723 | 135 | 123 | 1,045 |
| Immediate promotions, two-confirmation staged demotions | 14,935 | 0 | 122 | 1,344 |
| Two-confirmation promotions, three-confirmation staged demotions | 8,899 | 0 | 6 | 3,797 |

The same candidate remains worth watching: immediate promotions with
two-confirmation staged demotions. It removes direct operational `AA -> C`
jumps while keeping promotions immediate. After adding the June 11 snapshot, its
visible cost is `1,344` final routing differences versus the June 11 forecast
target.

This is still triage, not a selected production policy. Six snapshots are
better than five, but they are still too few to finalize a new velocity rule.
The next decision should wait for more confirmed uploads and for the custom
forecast model work to produce comparable candidate signals.
