# Planner Candidate Comparison - 2026-06-16

Planner OPS/IMF 14-day total for 2026-06-16..2026-06-29: 405,722 units.

| Candidate | Rows | PositiveFDSKUs | FDUnits | VsPlannerPct | Active | Reserve | Offsite |
| --- | --- | --- | --- | --- | --- | --- | --- |
| corporate_raw | 31,720 | 7,113 | 432,863 | 106.7% | 87 | 23,733 | 7,900 |
| planner_scaled_corp_100 | 31,720 | 7,108 | 405,722 | 100.0% | 87 | 23,733 | 7,900 |
| planner_scaled_corp_95 | 31,720 | 7,095 | 385,435 | 95.0% | 87 | 23,733 | 7,900 |
| planner95_hybrid | 18,634 | 10,846 | 385,426 | 95.0% | 3,135 | 15,499 | 0 |
| hybrid_capped | 18,620 | 8,247 | 140,360 | 34.6% | 2,469 | 16,151 | 0 |

## Interpretation
- Corporate raw is 6.7% above Planner OPS/IMF and keeps only 87 Active SKUs after guardrails.
- Planner-scaled corporate variants preserve corporate SKU/SlotTier/PutawayIndicator decisions while matching Planner daily totals.
- Planner-95 hybrid reaches the volume target but creates 3,135 Active SKUs, which is operationally risky.
- Current hybrid capped model is far below the sale volume signal.