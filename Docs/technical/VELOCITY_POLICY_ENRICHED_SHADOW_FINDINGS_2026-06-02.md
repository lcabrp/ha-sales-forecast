# Velocity Policy Enriched Shadow Findings - 2026-06-02

## Scope

This is shadow analysis only. It does not change ingestion logic, AX files,
approved maps, location directives, or replenishment setup.

The enriched replay adds:

- ingestion-calculated planning `CaseQty` from eight Product Info workbooks;
- six SKU-location inventory snapshots through June 2, 2026;
- portable SKU-day direct-pick history through June 2, 2026;
- separate Demand, MinMax, and Reset replenishment features;
- two capacity-ranked stress-test envelopes;
- an inventory-burden proxy for observed tier changes.

## Portable Facts

The tracked Parquet artifacts are intentionally compact enough for normal Git:

| Artifact | Rows | Approximate size |
| --- | ---: | ---: |
| Planning `CaseQty` history | 294,447 | 0.92 MiB |
| SKU-location inventory snapshots | 91,987 | 0.67 MiB |
| SKU-day direct-pick history | 2,929,501 | 9.11 MiB |
| Enriched candidate SKU-snapshot detail | 1,388,992 | 14.93 MiB |
| Observed transition inventory burden | 14,741 | 0.36 MiB |

The local-only sales-order allocation-link Parquet remains excluded because it
contains order-level attribution. None of the portable enrichment facts contain
sales-order IDs, demand-work IDs, or replenishment work IDs.

## MinMax Pause Boundary

Operations clarified that MinMax was paused after the new map upload because it
was creating too much noise.

The current daily monitoring evidence shows `Forward Replen` activity on May
13, 2026 and no MinMax rows afterward through May 31, 2026. The enriched replay
therefore records **May 14, 2026 at 00:00 EDT** as an inferred analysis
boundary. This is not a claim that the operator command occurred at that exact
timestamp. Replace the assumption if a more precise operational timestamp is
available.

Keep MinMax work separate. Do not use the post-pause period as though it were a
normal MinMax production sample.

## Category-Weight Replay

The first enriched replay compares ranked burden scores under two directional
capacity envelopes. The table below uses the conservative
`legacy_sku_population_proxy`, which preserves the initial confirmed snapshot's
SKU counts by velocity tier.

| Candidate | Adjacent tier changes | `AA/A` capture of later Demand touches | `AA` capture of later Demand touches | `AA/A` capture of recent pick lines |
| --- | ---: | ---: | ---: | ---: |
| Forecast cartons only | 18,078 | 23.18% | 17.68% | 35.53% |
| Demand touches and direct picks | 14,107 | 69.71% | 52.38% | 79.56% |
| Demand, MinMax, and Reset equally weighted | 14,077 | 69.78% | 52.31% | 79.67% |
| MinMax diagnostic downweighted | 14,089 | 69.76% | 52.38% | 79.60% |

The useful conclusion is not that one weight set has won. The evidence says
that forecast cartons alone are a weak physical-routing policy and that actual
Demand labor plus direct-pick history must participate in candidate scoring.

The MinMax variants are too similar to distinguish responsibly with this short,
interrupted MinMax sample. Keep them in the replay while more evidence
accumulates.

## Capacity-Ranked Stress Test

The `one_slot_location_upper_bound` stress test increases `AA/A` capture of
later Demand touches from `69.71%` to `71.68%` for the Demand-and-pick
candidate. It also changes `13,213` June 1 SKU tiers versus the inherited
legacy assignment.

This is directional evidence only. It assumes at least one location per
premium SKU and does not perform a deployable SlotTier-level fit. Required
slots, location profiles, cube, PalletPicking exceptions, and relocation
payback still need to gate a real map proposal.

## Observed Demotion Burden

Inventory snapshots begin on May 13, so the first two transition intervals do
not yet have a prior inventory observation. For the later intervals:

| Confirmed interval | Demotions | Direct `AA -> C` | Premium-location tier-step proxy | Physical quantity present before demotion |
| --- | ---: | ---: | ---: | ---: |
| May 18 -> May 28 | 1,674 | 16 | 965 | 73,364 |
| May 28 -> June 1 | 1,664 | 29 | 1,104 | 70,199 |

The tier-step proxy is:

```text
occupied premium locations before change * number of downward tier steps
```

It is not a labor standard or dollar cost. It creates a review queue for
demotions likely to obsolete meaningful forward-pick placement.

## Current Recommendation

Continue the unchanged legacy AX upload cadence and run shadow analysis in
parallel. Keep the earlier stability candidate under observation: immediate
promotions, two-confirmation demotions, and staged downward moves.

Do not select replacement thresholds yet. Add weekly forecast uploads,
Product Info workbooks, direct-pick refreshes, replenishment refreshes, and
SKU-location inventory snapshots so reversal and relocation windows mature.

## Physical Map Debt

The June 2 extension measures the operational lag between a target routing tier
and the occupied floor locations that still carry stock. This lag is called
**physical map debt**. It is not an error by itself: a dynamic map cannot move
stock instantly. It is a cost and capacity constraint that must gate routing
changes.

| Inventory snapshot | Occupied forward locations | Locations with physical velocity debt | Demotion debt | Promotion debt | Debt locations observed for at least 14 days |
| --- | ---: | ---: | ---: | ---: | ---: |
| May 13 | 15,854 | 9,106 | 4,929 | 4,177 | 0 |
| May 27 | 15,082 | 7,253 | 4,140 | 3,113 | 6,613 |
| June 2 | 14,666 | 7,487 | 4,889 | 2,598 | 5,824 |

On June 2, `51.05%` of occupied forward locations had a physical velocity
suffix different from the forecast target. The observed debt is not clearing
quickly between snapshots:

| Cohort start | Days to next snapshot | Debt locations | Turned over before next snapshot | Still present at next snapshot |
| --- | ---: | ---: | ---: | ---: |
| May 13 | 5 | 9,106 | 562 (`6.17%`) | 8,544 (`93.83%`) |
| May 19 | 8 | 8,588 | 1,590 (`18.51%`) | 6,998 (`81.49%`) |
| June 1 | 1 | 7,115 | 137 (`1.93%`) | 6,703 (`94.21%`) |

Inventory disappearance is only a turnover proxy. It can reflect picking,
manual moves, or another inventory adjustment. It does not prove natural
depletion. Even with that limitation, the persistence is sufficient to reject
weekly wholesale remapping.

## Stable Dynamic Routing

Keep two tiers for each SKU:

1. `SignalTier`: a weekly analytical recommendation based on forecast pressure,
   actual Demand touches, recent direct picks, capacity, and exceptions.
2. `RoutingTier`: the operational tier allowed to affect the next map.

The routing tier should follow the signal through controlled transitions:

- keep promotions responsive when premium capacity exists;
- require at least two confirmed snapshots before a demotion;
- stage downward moves one tier at a time, so `AA -> C` is not applied directly;
- apply a physical-debt budget and prefer locations that are empty or naturally
  clearing before activating new moves;
- review high-burden exceptions separately when manual movement has a justified
  payback.

The existing forecast-tier stability replay supports this design:

| Forecast-target routing control | Applied changes | Direct operational `AA -> C` | June 2 debt locations | June 2 premium demotion-step proxy |
| --- | ---: | ---: | ---: | ---: |
| Legacy immediate | 14,791 | 76 | 7,492 | 8,068 |
| Immediate promotion, two-confirmation staged demotion | 12,147 | 0 | 7,088 | 6,894 |
| Two-confirmation promotion, three-confirmation staged demotion | 5,691 | 0 | 6,767 | 6,292 |

The more conservative rule reduces debt but can postpone legitimate changes.
The balanced shadow candidate remains immediate promotion with
two-confirmation staged demotion. It is not approved for production yet.

## Enriched Score Stability Replay

The Demand-and-pick score was also replayed through the same stateful routing
controls. It improves downstream Demand-touch capture, but a wholesale
replacement would still create a broad remap:

| Enriched routing control | Applied changes | Direct operational `AA -> C` | `AA/A` capture of later Demand touches | June 2 debt locations | June 2 premium demotion-step proxy |
| --- | ---: | ---: | ---: | ---: | ---: |
| Immediate | 14,321 | 149 | 69.71% | 9,297 | 6,960 |
| Immediate promotion, two-confirmation staged demotion | 8,884 | 0 | 73.33% | 9,818 | 6,195 |
| Immediate promotion, three-confirmation staged demotion | 6,710 | 0 | 74.26% | 10,020 | 5,725 |

This is research evidence, not a deployable map proposal. The enriched score
is a better burden signal, while the stateful routing layer reduces expensive
demotions. A later rollout must be incremental and debt-budgeted rather than a
single mass remap.

## Incremental Activation Replay

The next shadow extension converts the design into an auditable activation
replay. It uses:

- the Demand-and-pick score;
- immediate-promotion, two-confirmation staged-demotion routing state;
- June 1 planning `RequiredSlots`;
- the May 7 deployed zone paint;
- June 2 occupied-floor inventory.

The replay changes no AX directive or production map. It tests how many shadow
routing changes could pass exact-SlotTier capacity, empty-location reservation,
premium-demotion review, and gross added-debt gates.

### Exact SlotTier Fit

The enriched routing shape improves the aggregate fit but does not complete it:

| Exact-SlotTier planning proxy | Legacy routing | Full enriched routing candidate |
| --- | ---: | ---: |
| Total required-slot proxy | 18,995.9 | 18,995.9 |
| Tiers with shortfall | 100 | 78 |
| Total shortfall proxy | 2,883.4 | 1,205.4 |

`18` candidate-demand SlotTiers still have no painted locations. Several large
shortfalls remain, including `GIRMC` (`214.0`), `GIRSC` (`135.5`), `KNISC`
(`97.6`), and `KNIMC` (`88.8`). A better velocity score cannot be deployed by
blindly replacing suffixes. Exact-tier paint and profile capacity must be
solved before a map proposal.

### Activation Budgets

The replay ranks `13,519` changed-SKU candidates and excludes premium-location
demotions from automatic activation:

| Gross added-debt budget | Accepted shadow routing changes | Promotions | Demotions | Added debt locations | Resolved debt locations | Net debt delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 3,238 | 560 | 2,678 | 0 | 527 | -527 |
| 100 | 3,334 | 650 | 2,684 | 100 | 549 | -449 |
| 250 | 3,452 | 768 | 2,684 | 250 | 550 | -300 |
| 500 | 3,666 | 982 | 2,684 | 500 | 551 | -51 |
| Unbounded | 3,923 | 1,233 | 2,690 | 782 | 557 | +225 |

The `500` row is a useful research envelope, not a recommended upload. It
shows that a meaningful incremental batch can remain slightly
debt-reducing overall while exact-tier and empty-location guards block unsafe
changes. The zero-added-debt row is the conservative starting benchmark.

### Premium Demotion Review

`617` demotion SKUs are quarantined because they occupy at least one premium
`AA/A` floor location. Together they represent:

| Review measure | Value |
| --- | ---: |
| Premium occupied locations | 704 |
| Physical quantity in occupied floor locations | 41,337 |
| Premium demotion tier-step proxy | 1,285 |

These rows are a manual-review queue, not an eviction list. Physical quantity,
cube, natural depletion rate, and operational payback still need to determine
whether any move is justified.

### Diagnostic Cohorts

The replay also labels new-or-returning SKUs, clearance/final-sale rows,
prior-year seasonal-only proxies, forecast zero crossings, and observed
forecast-velocity changes. These are diagnostic cohorts, not final lifecycle
rules. They make it possible to compare future snapshots without treating
every tier transition as the same kind of operational event.

## Remaining Gates Replay

The June 3 shadow extension completes the offline checks that do not require a
new weekly forecast snapshot.

### Exact-Tier Paint Transfer Options

The exact-tier paint diagnostic produced `390` donor options for the `78`
short candidate tiers. `143` options are same product/size prefix transfers,
meaning the candidate shortfall could theoretically be relieved by repainting
between velocity suffixes inside the same product-size family.

| Short tier | Shortfall proxy | Same-prefix donor examples |
| --- | ---: | --- |
| `GIRMC` | 214 | `GIRMB`, `GIRMA`, `GIRMAA` |
| `GIRSC` | 136 | `GIRSB`, `GIRSA`, `GIRSAA` |
| `KNISC` | 98 | `KNISB`, `KNISA`, `KNISAA` |
| `KNIMC` | 89 | `KNIMA`, `KNIMB` |

These are paint diagnostics only. They do not preserve aisle adjacency,
category-room constraints, market-basket travel, or labor payback. They are a
shortlist for a future map-fitting experiment, not a map.

### PalletPicking/Profile Pressure

The PalletPicking pressure report ranks SlotTiers by current profile paint,
candidate shortfall, recent Demand touches, recent pick lines, and occupied
floor quantity. Cube was refreshed in the June 3 shadow package from AX
`WHSPHYSDIMUOM`. `UnitCube` is `DEPTH * WIDTH * HEIGHT`, which is a unit/piece
cube proxy rather than carton cube. It is suitable for pressure ranking and
exception review, not for automatic PalletPicking reassignment by itself.

Among the top 20 profile-pressure tiers, 18 currently have no PalletPicking
paint. This supports keeping PalletPicking as a manual exception lane rather
than hard-coding the current `BAPXAA` allocation or automatically assigning new
tiers without travel review.

### Score-Margin Hysteresis

The enriched Demand-and-pick signal was replayed with score-boundary buffers.
If a SKU's score is too close to a rank boundary, the stress test retains its
previous signal tier instead of changing immediately.

| Margin buffer | Signal-tier changes | Boundary blocks | `AA/A` Demand-touch capture |
| --- | ---: | ---: | ---: |
| 0.00% | 14,107 | 0 | 69.71% |
| 0.25% | 12,880 | 1,155 | 69.81% |
| 0.50% | 11,846 | 2,186 | 69.94% |
| 1.00% | 10,089 | 4,023 | 70.28% |
| 2.00% | 7,429 | 7,229 | 70.45% |
| 5.00% | 2,429 | 14,450 | 70.25% |

The useful research range is roughly `0.5%` to `2.0%`. A `1.0%` score-boundary
buffer cuts signal churn by about `28.5%` versus no buffer while slightly
improving observed Demand-touch capture in the short sample. A `5.0%` buffer
is probably too sticky because it leaves `4,713` final differences versus the
candidate signal and starts reducing `AA` capture.

Do not select the final buffer from five snapshots. Keep it as a stress range
until more weekly forecasts mature.

### Empty-Location Repaint Fit

The donor shortlist was converted into a location-level repaint-fit proxy using
only currently empty donor locations. This tests how much exact-tier shortfall
could be relieved without creating an immediate occupied-stock relocation.

| Repaint-fit measure | Value |
| --- | ---: |
| Candidate shortfall before repaint proxy | 1,205.4 |
| Candidate shortfall after empty-location repaint proxy | 180.1 |
| Shortfall proxy reduced | 1,025.3 |
| Empty locations selected for repaint | 1,063 |
| Short tiers receiving at least one repaint | 76 |
| Same product/size-prefix repaint locations | 992 |
| Same cluster repaint locations | 1,008 |
| Average aisle distance to short-tier median | 11.5 |

This is the strongest evidence so far that the enriched routing shape can be
made physically closer to feasible without a mass stock move. It is still not a
map. The average aisle distance and the lack of a full market-basket travel
simulation mean the next map-fitting step must preserve adjacency and pick-path
quality, not merely exact-tier capacity.

The June 3 calibrated-router screen materially weakens any broad repaint
interpretation. Of `1,063` selected empty repaint locations, `1,018` were
scored against existing short-tier anchors. Mean nearest-neighborhood distance
is `199.2 ft`, P90 is `319.0 ft`, only `117` are within `50 ft`, and only
`263` are within `100 ft`. Treat repaint fit as a capacity shortlist; only
physically close subsets should move forward to full basket travel replay.
