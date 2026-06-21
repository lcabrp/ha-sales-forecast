# July Sale YoY Lift Scratch Findings

## Planner / Total-Unit Lift
| Year | Metric | SaleStart | SaleEnd | SaleDays | SaleUnits | BaselineStart | BaselineEnd | BaselineDailyUnits | BaselineExpectedUnits | LiftVsBaseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2024 | ops_imf_plan_forecasted_units | 2024-06-18 | 2024-07-06 | 19 | 562469.00 | 2024-05-21 | 2024-06-17 | 18936.68 | 359796.89 | 1.56 |
| 2025 | actual_demand_units | 2025-06-21 | 2025-07-04 | 14 | 481288.00 | 2025-05-24 | 2025-06-20 | 14643.79 | 205013.00 | 2.35 |
| 2026 | ops_imf_plan_forecasted_units | 2026-06-18 | 2026-07-01 | 14 | 456096.37 | 2026-05-21 | 2026-06-17 | 16418.14 | 229854.00 | 1.98 |

## Shadow Metadata
- Forecast window: 2026-06-18 through 2026-07-01
- Promo-horizon SKUs: 48,738
- Future rows: 3,387,986

## Largest Category Gaps vs Current Hybrid 10% / 0.85x Cap
| Division | Department | Class | KeyCategoryView | Sale2025Units | CategoryLift2025 | CurrentPromoSKUs | CurrentExpectedBy2025Lift | hybrid_ml_raw_min20_recent_w0p1_cap_recent_x0p85 | GapVsHybrid10Cap085 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Girls | G dress | G SL dress | Dress | 41310.00 | 2.25 | 965.00 | 67750.40 | 11801.00 | 55949.40 |
| Kids Unisex Sleep | S KU SJ | S KU SJ print | Sleep | 39023.00 | 1.52 | 1242.00 | 42302.91 | 17210.00 | 25092.91 |
| Girls | G dress | G SS dress | Dress | 23048.00 | 3.13 | 1044.00 | 28380.02 | 3736.00 | 24644.02 |
| Kids Unisex | KU top | KU SS top | Top | 12355.00 | 2.53 | 698.00 | 28712.69 | 6611.00 | 22101.69 |
| Girls | G short | G k short | Short | 25295.00 | 2.24 | 432.00 | 24089.14 | 4696.00 | 19393.14 |
| Boys | B top | B SS top | Top | 24556.00 | 3.09 | 839.00 | 22226.29 | 3007.00 | 19219.29 |
| Baby Sleepwear | BS footless sleeper | BS footless print | Sleep | 19000.00 | 1.81 | 1299.00 | 25600.38 | 8818.00 | 16782.38 |
| Girls | G sets | G SL set | Set | 5131.00 | 5.44 | 179.00 | 14782.34 | 993.00 | 13789.34 |
| Baby Apparel | Bby sets | Bby SS set | Set | 12509.00 | 2.85 | 435.00 | 16232.11 | 3291.00 | 12941.11 |
| Kids Unisex Sleep | S KU LJ | S KU LJ print | Sleep | 11418.00 | 1.78 | 1706.00 | 15237.21 | 4254.00 | 10983.21 |
| Collab Sleepwear | CS Kids Unisex sleep | CS KU LJ | Sleep | 9246.00 | 4.22 | 1005.00 | 12357.02 | 2186.00 | 10171.02 |
| Girls | G pant | G k pant | Pant | 14531.00 | 3.18 | 597.00 | 11690.22 | 1963.00 | 9727.22 |
| Girls | G sweater | G swtr cardigan | Sweater | 15.00 | 30.00 | 78.00 | 8355.79 | 165.00 | 8190.79 |
| Boys | B swimwear | B swim rash guard | Swim | 10747.00 | 2.03 | 247.00 | 11769.37 | 4006.00 | 7763.37 |
| Girls | G top | G SL top | Top | 5987.00 | 2.67 | 135.00 | 8530.22 | 1348.00 | 7182.22 |

## Read
- Prior-year July sale behavior creates a materially higher category-level volume
  expectation than the current ML shadow in several promoted categories.
- Treat this as directional: 2025 category lift is based on retail sales-order
  SKU/day rows, while the replacement model is scored against DC DirectPick demand.
- The result supports adding a sale-event category lift / total-volume anchor
  rather than relying on PDL presence as a binary feature.
