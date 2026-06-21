# July Sale DirectPick YoY Lift Scratch Findings

## Total DirectPick Lift
| Source | SaleStart | SaleEnd | SaleDays | SaleUnits | BaselineStart | BaselineEnd | BaselineObservedDays | BaselineDailyUnits | BaselineExpectedUnits | LiftVsBaseline |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2025 analog DirectPick | 2025-06-21 | 2025-07-04 | 14 | 473431.00 | 2025-05-24 | 2025-06-20 | 27 | 15844.48 | 221822.74 | 2.13 |
| 2026 current pre-sale DirectPick | 2026-06-18 | 2026-07-01 | 14 |  | 2026-05-21 | 2026-06-17 | 28 | 18616.68 | 260633.50 |  |

## Current Shadow Context
- Window: 2026-06-18 through 2026-07-01
- Promo-horizon SKUs: 48,738
- DirectPick lift projection across current promoted categories: 539,227
- Current hybrid 10% / 0.85x across current promoted categories: 150,603
- Recent no-ML floor across current promoted categories: 276,907

## Largest Category Gaps vs Hybrid
| Division | Department | Class | KeyCategoryView | Sale2025Units | CategoryLift2025 | CurrentPromoSKUs | DirectPickLiftProjection | hybrid_ml_raw_min20_recent_w0p1_cap_recent_x0p85 | GapVsHybrid10Cap085 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Girls | G dress | G SL dress | Dress | 41008.00 | 2.11 | 965.00 | 58947.41 | 11801.00 | 47146.41 |
| Kids Unisex Sleep | S KU SJ | S KU SJ print | Sleep | 39257.00 | 1.44 | 1242.00 | 41605.77 | 17210.00 | 24395.77 |
| Girls | G dress | G SS dress | Dress | 22804.00 | 2.93 | 1044.00 | 25414.21 | 3736.00 | 21678.21 |
| Kids Unisex | KU top | KU SS top | Top | 12330.00 | 2.38 | 698.00 | 26547.98 | 6611.00 | 19936.98 |
| Girls | G short | G k short | Short | 25335.00 | 2.11 | 432.00 | 22179.20 | 4696.00 | 17483.20 |
| Boys | B top | B SS top | Top | 24445.00 | 2.89 | 839.00 | 19466.65 | 3007.00 | 16459.65 |
| Baby Sleepwear | BS footless sleeper | BS footless print | Sleep | 18989.00 | 1.71 | 1299.00 | 25052.46 | 8818.00 | 16234.46 |
| Girls | G sets | G SL set | Set | 5135.00 | 5.25 | 179.00 | 13344.16 | 993.00 | 12351.16 |
| Baby Apparel | Bby sets | Bby SS set | Set | 12503.00 | 2.70 | 435.00 | 14725.94 | 3291.00 | 11434.94 |
| Kids Unisex Sleep | S KU LJ | S KU LJ print | Sleep | 11382.00 | 1.66 | 1706.00 | 13276.77 | 4254.00 | 9022.77 |
| Collab Sleepwear | CS Kids Unisex sleep | CS KU LJ | Sleep | 9178.00 | 3.94 | 1005.00 | 11030.73 | 2186.00 | 8844.73 |
| Girls | G pant | G k pant | Pant | 14438.00 | 2.94 | 597.00 | 9864.55 | 1963.00 | 7901.55 |
| Boys | B swimwear | B swim rash guard | Swim | 10753.00 | 1.93 | 247.00 | 11902.82 | 4006.00 | 7896.82 |
| Girls | G sweater | G swtr cardigan | Sweater | 15.00 | 28.93 | 78.00 | 6856.07 | 165.00 | 6691.07 |
| Girls | G top | G SL top | Top | 5972.00 | 2.50 | 135.00 | 7403.88 | 1348.00 | 6055.88 |
| Baby Apparel | Bby dress | Bby SL dress | Dress | 6612.00 | 2.25 | 321.00 | 7460.29 | 1496.00 | 5964.29 |
| Girls | G skirt | G k skirt | Skirt | 7473.00 | 3.98 | 130.00 | 6603.44 | 697.00 | 5906.44 |
| Kids Unisex Sleep | S KU non cotton | S KU non cttn PJ | Sleep | 16409.00 | 6.22 | 866.00 | 7208.38 | 1335.00 | 5873.38 |
| Boys | B swimwear | B swim trunk | Swim | 7613.00 | 1.66 | 318.00 | 10052.01 | 4241.00 | 5811.01 |
| Girls | G dress | G LS dress | Dress | 3590.00 | 2.93 | 1311.00 | 7199.99 | 1458.00 | 5741.99 |

## Read
- This rerun uses WHSWorkLine-derived DirectPick actuals, not Planner.
- The 2025 July sale lifted DirectPick units materially versus its pre-sale baseline.
- Category-level prior-sale lift still explains a large share of the current ML under-forecast.
