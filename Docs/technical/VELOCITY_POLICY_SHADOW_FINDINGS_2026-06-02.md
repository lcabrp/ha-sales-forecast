# Velocity Policy Shadow Findings - 2026-06-02

## Scope

This is an experiment-only profile. It does not change ingestion logic, AX
payloads, location directives, or approved maps.

Inputs:

- five confirmed AX-effective forecast uploads from May 4 through June 1, 2026;
- the ignored local three-year physical replenishment Parquet;
- the frozen May 7 cutover map for directional capacity reference.

The builder is:

```powershell
uv run python scratch/build_velocity_policy_shadow_panel.py --overwrite
```

## Saved Compact Artifacts

Tracked shadow outputs live under:

```text
Output/Monitoring/shadow_velocity_policy/
```

| Artifact | Rows | Approximate size |
| --- | ---: | ---: |
| SKU-snapshot Parquet | 173,624 | 1.1 MB |
| Changed-tier event Parquet | 14,741 | 0.37 MB |

The row-level outputs exclude sales-order identifiers. Detailed allocation
links remain ignored and regenerable locally.

## Interval Churn

| Confirmed interval | Shared SKUs | Velocity changes | Rate | Promotions | Demotions | Multi-tier jumps | `C -> AA` | `AA -> C` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| May 4 -> May 12 | 33,648 | 2,770 | 8.23% | 1,553 | 1,217 | 119 | 18 | 1 |
| May 12 -> May 18 | 34,102 | 3,149 | 9.23% | 1,989 | 1,160 | 128 | 1 | 28 |
| May 18 -> May 28 | 33,667 | 4,669 | 13.87% | 2,995 | 1,674 | 213 | 18 | 16 |
| May 28 -> June 1 | 32,220 | 4,153 | 12.89% | 2,489 | 1,664 | 167 | 0 | 29 |

Across adjacent confirmed intervals, there were `74` direct `AA -> C`
demotions and `37` direct `C -> AA` promotions. These are interval event counts,
not the May 4 -> June 1 endpoint comparison.

## Reversals

Of the changed-tier events:

| Window | Earlier events followed by an opposite-direction change |
| --- | ---: |
| Within 14 days | 370 |
| Within 28 days | 496 |
| Within 56 days | 496 |

These are lower bounds. The June 1 snapshot has no later confirmed upload yet,
so recent changes cannot show whether they reverse.

## Stability-Control Triage

The separate shadow routing replay treats the confirmed forecast tier as a
signal and applies experimental controls before changing the operational
routing tier. The immediate baseline includes `14,741` adjacent shared-SKU
changes plus `50` changes for SKUs that disappeared from an intermediate
snapshot and later returned. Returning-SKU events are labeled separately in the
saved output.

| Shadow routing control | Applied changes | Direct `AA -> C` | Reversed within 14 days | Final routing differences versus June 1 target |
| --- | ---: | ---: | ---: | ---: |
| Legacy immediate | 14,791 | 76 | 370 | 0 |
| Two confirmations for all changes | 7,406 | 60 | 0 | 4,105 |
| Three confirmations for all changes | 2,326 | 34 | 0 | 7,359 |
| Immediate promotions, two-confirmation demotions | 12,055 | 60 | 58 | 1,685 |
| Immediate promotions, two-confirmation staged demotions | 12,147 | 0 | 56 | 1,858 |
| Two-confirmation promotions, three-confirmation staged demotions | 5,691 | 0 | 0 | 5,467 |

The most useful first candidate for continued observation is immediate
promotions with two-confirmation staged demotions. Relative to the immediate
baseline, it removes direct operational `AA -> C` jumps, reduces 14-day
reversals from `370` to `56`, and still applies promotions immediately. Its
cost is visible rather than hidden: `1,858` June 1 forecast-target differences
remain deferred.

This is triage, not a selected production policy. Five snapshots are too few to
separate avoidable oscillation from legitimate seasonal movement or to measure
the payback from physical relocation.

## Initial Expensive-Demotion Triage

The first shadow output includes `TransitionPriorityPoints`. This is a review
queue, not a measured dollar or labor cost:

```text
tier steps
* direction weight (demotions count double)
* (1 + log(1 + prior 56-day physical touches))
```

The highest-priority rows are direct `AA -> C` demotions for SKUs that had
material replenishment activity before the change. This supports testing a
demotion grace period and staged demotions before allowing the routing tier to
fall three levels in one weekly upload.

Do not interpret the proxy as true relocation burden yet. Historical floor
occupancy and inventory by location still need to be joined.

## Capacity Reference

The frozen May 7 deployed map contains:

| Velocity suffix | Locations |
| --- | ---: |
| `AA` | 3,830 |
| `A` | 3,555 |
| `B` | 2,945 |
| `C` | 12,403 |
| Other / overflow | 462 |

This is a directional reference only. SKU counts cannot be treated as a direct
fit test because required slots vary by SKU and SlotTier.

## Known Limitation

The confirmed AX upload CSV does not carry the ingestion pipeline's calculated
planning `CaseQty`. The first panel preserves `FD1..FD14`,
`ReplenishmentThreshold`, actual last-put quantities, and observed work
features. Add historical ingestion-calculated `CaseQty` later from source
workbooks where a defensible snapshot mapping exists. Do not replace it with
observed last-put quantity; they answer different questions.

## Next Shadow Work

1. Join historical floor occupancy or inventory snapshots where available.
2. Add a true relocation-burden estimate for large demotions.
3. Add ingestion-calculated planning `CaseQty` from mapped source workbooks.
4. Keep collecting confirmed weekly AX-effective snapshots and rerun the
   stability controls as reversal windows mature.
5. Add capacity gates and lifecycle overrides before recommending a policy.
