# Velocity Policy Investigation Plan

## Status

This is an investigation plan only. It does not change the AX forecast file,
the ingestion pipeline, the monitoring scripts, or the current SlotTier policy.

The current velocity suffix is inherited from the BRG/Ankura process:

| Velocity | 13-week forecast units |
| --- | ---: |
| AA | > 100 |
| A | 41-100 |
| B | 21-40 |
| C | <= 20 |

Those cutoffs preserve legacy behavior. They are not yet validated against
Kentucky DC production work.

## Working Recommendation

Use a **controlled dynamic** policy:

1. Keep the demand signal variable so seasonality, launches, and changing sales
   patterns remain visible.
2. Calibrate the velocity tiers against warehouse work, especially
   replenishment touches and carton flow into the pick face.
3. Stabilize the routing tier with hysteresis, a minimum-duration rule, and
   explicit exception handling.
4. Revisit the thresholds periodically as capacity and operating patterns
   change. Do not freeze them permanently, but do not let the routing tier copy
   every weekly forecast movement directly.

The useful distinction is:

| Concept | Purpose | Expected behavior |
| --- | --- | --- |
| Demand signal tier | Show what the newest forecast or demand model implies | Can move weekly |
| Operational routing tier | Drive SlotTier placement and AX routing | Changes only after stability rules are satisfied |

This is a project-specific recommendation inferred from the evidence below. It
is not a claim that there is one universal industry formula.

## Why Investigate

The confirmed AX-effective SCD2 history shows that product group is stable and
size group is effectively stable, while velocity is not:

| Confirmed interval | Shared SKUs | Velocity changes | Product-group changes | Size-group changes |
| --- | ---: | ---: | ---: | ---: |
| May 4 -> May 12 | 33,648 | 2,770 (8.23%) | 0 | 10 (0.03%) |
| May 12 -> May 18 | 34,102 | 3,149 (9.23%) | 0 | 0 |
| May 18 -> May 28 | 33,667 | 4,669 (13.87%) | 0 | 0 |
| May 28 -> June 1 | 32,220 | 4,153 (12.89%) | 0 | 0 |
| May 4 -> June 1 | 29,742 | 8,251 (27.74%) | 0 | 9 (0.03%) |

The May 4 -> June 1 comparison includes `612` direct `C -> AA` movements and
`227` direct `AA -> C` movements. That is too much churn to treat the forecast
suffix as a durable physical-placement instruction without testing controls.

## What Mainstream WMS Products Do

Public product documentation shows a common pattern rather than a single
formula:

| Source | Publicly documented pattern | Implication for this project |
| --- | --- | --- |
| [Oracle WMS Cloud Reslotting Workbench](https://docs.oracle.com/en/cloud/saas/warehouse-management/25d/owmol/optimize-pick-locations-with-reslotting-workbench.html) | Realigns pick locations using item velocity rankings and considers seasonality, demand patterns, location capacity, volume, weight, and units. | Velocity is an input to slotting, not the only input. Capacity matters. |
| [SAP EWM Slotting](https://learning.sap.com/courses/processes-in-sap-s-4hana-ewm/performing-slotting) | Uses product, requirement, packaging, forecast, or historical data. ABC analysis can classify products from confirmed warehouse tasks. Results can be analyzed as planned values before activation. | Use actual work and packaging data; shadow-test before changing active routing. |
| [Dynamics 365 Replenishment](https://learn.microsoft.com/en-us/dynamics365/supply-chain/warehousing/replenishment) | Supports demand-triggered and min/max replenishment strategies. Replenishment units, location directives, and pick-face limits remain operational controls. | Separate classification from replenishment mechanics and location limits. |
| [Dynamics 365 Warehouse Slotting](https://learn.microsoft.com/en-us/dynamics365/supply-chain/warehousing/warehouse-slotting) | Applies demand to pick locations using quantity, unit of measure, physical dimensions, fixed locations, and on-hand inventory. | A units-only 13-week threshold is incomplete for physical slotting. |
| [Dynamic storage assignment case study](https://www.mdpi.com/2227-7080/10/6/129) | A real-world study reported that a dynamic assignment approach outperformed the site's manual ABC classification for its objective. | Treat static ABC as a baseline, not an endpoint; validate against this warehouse's costs. |

The documentation supports variable inputs and periodic reassessment. It does
not support changing physical routing blindly every time one input changes.

## Data Already Available

| Data | Current coverage | Use |
| --- | --- | --- |
| Confirmed AX-effective forecast SlotTier SCD2 | 7 snapshots: May 4, May 12, May 18, May 28, June 1, June 11, June 16 | Measure forecast-driven tier churn and perform as-of joins |
| Versioned forecast CSV archives | Canonical outputs from April 1 through June 1, plus confirmed raw archives | Extend exploratory snapshot comparisons |
| Direct-pick history Parquet | 8,106,154 rows, 38,871 SKUs, April 1, 2025 through April 6, 2026 | Compute units, order lines, orders, intermittency, and seasonality |
| Forecast workbooks | Historical source workbooks from 2024 and 2026 | Inspect forecast inputs and case-pack sources |
| Monitoring SQLite | Daily layout metrics for May 8 through May 31; inventory compliance snapshots through June 1 | Validate the post-cutover operating baseline |
| Existing scenario helpers | `FD1..FD14`, case-quantity logic, and carton-pressure calculations | Reuse proven analysis concepts without changing production scripts |
| Replenishment history Parquet | Three-year extract: 1,096,914 local-only allocation links and 189,273 tracked physical touches, June 2, 2023 through June 2, 2026 | Analyze observed last-put quantities, labor touches, and Demand/MinMax/Reset categories offline across development machines |

## Data To Add Or Enrich For The Investigation

The narrow row-level replenishment facts are now persisted under
`scratch/velocity_policy_replay/`. The deduplicated physical-touch Parquet,
validation summaries, and metadata are tracked for portable shadow analysis.
The detailed allocation-link Parquet remains local-only because it contains
sales-order attribution and demand-work identifiers. Do not move either fact
into a production monitor yet. The remaining work is to enrich the offline
panel with the other facts below.

| Fact area | Required fields | Reason |
| --- | --- | --- |
| Replenishment work | Event date/time, work ID, work-build ID, template, status, SKU, source and target locations, source and target profiles/zones, quantity, final put line | Measure actual reserve-to-pick touches and routing outcomes |
| Pick demand | SKU-day units, order lines, distinct orders, active days | Separate steady movers from intermittent or concentrated demand |
| Packaging | SKU, ingestion-calculated planning `CaseQty`, source, effective date, confidence flag | Preserve the planning input used by the ingestion pipeline without confusing it with observed AX movement quantity |
| Capacity | Location profile, zone, bin type, slot count, usable cube, item cube where reliable | Test whether proposed bands fit the physical pick face |
| Lifecycle and exceptions | New item, launch, seasonal peak, clearance, long-tail, manually managed exception | Avoid treating planned ramps and declines as noise |

The extraction must classify work carefully. A first broad query joined every
put line before isolating replenishment work and timed out. That was the wrong
query shape, not evidence that the history could not be extracted.

`WHSREPLENWORKLINK` directly maps a replenishment work line to the sales-order
demand work line that needed it. Filter replenishment headers to
`WORKTRANSTYPE = 11`, join the link table by replenishment work ID, and require
the linked demand header to have `WORKTRANSTYPE = 2` for sales orders. This
excludes unrelated returns, outbound puts, and active putaway without relying
only on template names.

Keep the raw allocation-link grain and derive a separate physical-touch fact:

| Fact | Grain | Use |
| --- | --- | --- |
| Sales-order replenishment allocation | `ReplenWorkId + ReplenLineNum + DemandWorkId + DemandLineNum` | Attribute replenishment demand to sales-order work |
| Physical replenishment touch | Distinct `ReplenWorkId + ReplenLineNum` | Measure reserve-pick labor without double-counting shared allocations |

The linked replenishment line is the reserve pick. The authoritative historical
movement quantity is the quantity on the **last put line** for the replenishment
work ID. Current work templates normally place that final put on line `5`
because a `Print` step was introduced at line `2`; older templates had four
lines. Select the last put semantically (`WORKTYPE = 2`, highest `LINENUM`)
instead of hard-coding line `5`.

Keep the ingestion-calculated planning `CaseQty` as a separate feature. It is
useful for understanding the ingestion model and MinMax setup, but it is not
carried in the 36-column AX upload CSV and it is not the source of truth for how
many units AX actually moved into the forward-pick face.

### Confirmed Index Paths

The extraction is designed around the AX indexes rather than a broad work-line
scan:

| Table | Index | Leading keys used |
| --- | --- | --- |
| PROD `WHSWORKTABLE` | `I_102778STATUSCLOSEDDATECREATEDDATEIDX` | `PARTITION`, `DATAAREAID`, `WORKCLOSEDUTCDATETIME`, `CREATEDDATETIME`, `WORKSTATUS` |
| PROD/archive `WHSREPLENWORKLINK` | `I_102706REPLENWORKLINEDEMANDWORKLINEIDX` | `PARTITION`, `DATAAREAID`, `REPLENWORKID`, `REPLENLINENUM`, `DEMANDWORKID`, `DEMANDLINENUM`, `WORKBUILDID` |
| PROD/archive `WHSWORKTABLE` | `I_102778WORKIDX` | `PARTITION`, `DATAAREAID`, `WORKID` |
| PROD/archive `WHSWORKLINE` | `I_102773WORKIDLINENUMIDX` | `PARTITION`, `DATAAREAID`, `WORKID`, `LINENUM` |

`DAX_Archive` retains the clustered work-ID paths but fewer secondary indexes.
Use the same clean archive/PROD boundary split already proven by
`extract_picking_history_12mo.py`.

### Year-Scale Validation

A read-only monthly profile using this indexed shape completed in `6.61`
seconds for June 1, 2025 through June 1, 2026:

| Metric | Count |
| --- | ---: |
| Sales-order-linked allocation rows | 410,048 |
| Physical replenishment source-line touches | 79,795 |
| Linked sales-order demand work IDs | 320,226 |
| Allocated inventory quantity | 425,694.29 |

The profile includes `Fwd Wave Demand`, `Fwd Rush Wave Demand`,
`Forward Replen`, and `Reset Replenishment` rows when they were linked to sales
orders. Preserve the replenishment category so the calibration can score normal
demand replenishment separately from min/max and reset behavior.

The reusable investigation query is
`scratch/velocity_policy_sales_order_replen_extract.sql`.

The query originally validated a trailing two-year window and now defaults to a
three-year calibration window. The earlier two-year read-only detail stream
completed in `38.71` seconds with:

| Two-year detail metric | Count |
| --- | ---: |
| Sales-order-linked allocation rows | 662,699 |
| Physical replenishment source-line touches | 130,701 |
| Missing final pick-face targets | 0 |

## Metrics To Calculate

Use several signals. No single metric is sufficient:

| Metric | Meaning |
| --- | --- |
| Replenishment touches per SKU-day | Labor pressure created by a SKU |
| Actual moved quantity per SKU-day | Sum of last-put quantities into the pick face |
| Physical replenishment touches per SKU-day | Distinct replenishment source-line touches; the primary labor count |
| Calculated forecast cartons per day | Forward-looking planning signal based on the ingestion pipeline's calculated `CaseQty` |
| Pick units, lines, and orders per SKU-day | Demand intensity and handling frequency |
| Active pick days and inter-pick gaps | Demand intermittency |
| Forecast cartons per day | Forward-looking planning pressure: forecast units divided by ingestion-calculated planning `CaseQty` and horizon |
| Forecast error by horizon | Whether forecast movement is a useful signal or mostly churn |
| Weekly tier transition rate | Operational stability cost |
| Tier occupancy versus usable slots | Physical feasibility |
| Promotions, demotions, and reversals | Whether stabilization rules prevent oscillation |

## Recommended History Horizon

Use more than one year, but do not average every historical year equally.

| Horizon | Recommendation | Reason |
| --- | --- | --- |
| Trailing 12 months | Minimum viable calibration window | Covers one promotional calendar and is enough to build the first SKU-week panel |
| Trailing 24 months | Preferred calibration window | Provides a prior-year seasonal comparison and helps distinguish recurring promotional ramps from one-off events |
| Trailing 36 months | Anomaly-review window | Useful for detecting unusual years and validating seasonal assumptions; it should not dominate the weekly score |

A read-only indexed monthly profile confirmed that the same calendar periods can
carry materially different workload levels:

| Complete June-May cycle | Normal demand-replenishment touches |
| --- | ---: |
| June 2023 - May 2024 | 50,756 |
| June 2024 - May 2025 | 42,918 |
| June 2025 - May 2026 | 70,442 |

The promotional calendar may repeat, but the magnitude does not. Use same-period
history as a seasonal feature scaled to the current run rate. Do not use a
three-year unweighted average as the operational forecast.

## Preliminary Profile Of The Current Bands

The current June 1 forecast tiers do not align cleanly with the prior year's
normal demand-replenishment workload:

| Current velocity | Current SKUs | SKUs with trailing demand touches | Share of trailing demand touches |
| --- | ---: | ---: | ---: |
| AA | 3,503 | 1,718 (49.04%) | 19.55% |
| A | 4,916 | 2,092 (42.55%) | 13.72% |
| B | 2,893 | 1,575 (54.44%) | 8.99% |
| C | 22,478 | 7,363 (32.76%) | 57.74% |

This is not a final scorecard for the tiers: it compares today's forward-looking
classification to a trailing year containing prior seasons. It does show why
the current `20/40/100` raw-unit bands are not sufficient as a physical-routing
policy.

## Proposed Explainable Policy To Backtest

Treat velocity as an **operational slotting priority**, not as a label for raw
forecast units.

### 1. Build SKU-week signals

For each SKU and weekly decision date, calculate:

| Signal | Definition | Purpose |
| --- | --- | --- |
| Near-term forecast carton pressure | `FD1..FD14 units / 14 / ingestion-calculated planning CaseQty` | Capture the ingestion model's expected immediate work |
| Forward peak carton pressure | Highest forecasted weekly cartons per day in the planning lead-time window | Promote seasonal or promotional items before the demand arrives |
| Recent pick pressure | Recent direct-pick lines and units per active day, using a recency-weighted 4-8 week window | Capture current handling demand |
| Seasonal baseline | Same-period prior-year pick pressure and replenishment touches, scaled to the current run rate | Respect repeatable promotion timing without freezing last year's magnitude |
| Historical normal-demand touches | Distinct `ReplenWorkId + ReplenLineNum` for `Fwd Wave Demand` and `Fwd Rush Wave Demand` | Calibration target for actual reserve-to-pick labor |
| Capacity fit | Usable slots, profile, bin type, item cube where reliable | Keep the result physically deployable |

Keep both quantity concepts. A unit-only rule is incomplete, but calculated
planning `CaseQty` and observed last-put movement quantity answer different
questions. The former is a planning feature; the latter is the historical
operational truth used to measure actual forward movement.

### 2. Predict operational burden

Use normal-demand replenishment touches as the primary outcome to predict, then
include direct-pick lines as a secondary handling signal:

```text
ExpectedRefillTouchesPerDay =
    model(forecast carton pressure,
          recent pick pressure,
          scaled seasonal baseline,
          case quantity,
          pick-face capacity where available)

OperationalBurdenScore =
    refill-labor component
    + pick-handling component
```

Actual touches are a calibration outcome, not the sole input. They are affected
by the existing location map, pick-face capacity, replenishment thresholds, and
warehouse process. Keep `MinMaxUsedBySalesOrder` and `ResetUsedBySalesOrder`
available for diagnostics, but train the normal-demand baseline separately.

Start with an explainable regression or scoring model and compare it with simple
rules. A more complex model is only worthwhile if it materially improves the
backtest.

### Replenishment Category Semantics

The monitoring reports intentionally use `Demand` as a narrow category:

| Category | AX templates | Policy treatment |
| --- | --- | --- |
| Demand | `Fwd Wave Demand`, `Fwd Rush Wave Demand` | Clean primary target for ordinary reserve-to-pick labor |
| MinMax used by sales orders | `Forward Replen` rows later linked to sales-order demand | Include as real burden and capacity use; retain separately because it can lead consumption |
| Reset used by sales orders | `Reset Replenishment` rows later linked to sales-order demand | Include as real burden; retain separately because it is reactive and may expose a process issue |

Do not silently merge the categories in reporting. Do not discard min/max or
reset work from the velocity analysis either.

A two-year read-only lag profile supports that treatment:

| Category | Physical touches | Median creation-to-consumption lag | 90th percentile lag | Consumed after 3 days |
| --- | ---: | ---: | ---: | ---: |
| Demand | 113,440 | 0.00 days | 0.01 days | 0.00% |
| MinMax used by sales orders | 16,323 | 0.87 days | 2.55 days | 5.65% |
| Reset used by sales orders | 932 | 0.10 days | 0.68 days | 0.21% |

The linked sample had no negative lags. Normal-demand work is effectively
immediate. Min/max work has a measurable but bounded lead time and was linked to
sales-order consumption within seven days in this sample.

### 3. Set capacity-constrained bands

Assign `AA/A/B/C` by burden score and physical capacity:

| Tier | Operational meaning |
| --- | --- |
| AA | Highest refill or pick-handling burden; premium slotting and PalletPicking candidates |
| A | High burden; should receive favorable active-pick capacity |
| B | Moderate burden |
| C | Long tail or low near-term burden |

Derive the score boundaries from the distribution of predicted burden and the
usable capacity of the intended zones. Do not start with replacement magic
numbers.

### 4. Stabilize the routing tier

Recompute the signals weekly, but control operational changes:

| Control | Backtest requirement |
| --- | --- |
| Promotion hysteresis | Require a stronger score to move into a faster tier than to remain there |
| Demotion grace period | Require sustained evidence before moving a SKU down |
| Minimum duration | Test two or more consecutive weekly signals before ordinary tier changes |
| Planned promotion override | Allow a forecast-backed promotion ahead of a known event |
| New-item rule | Use forecast and category analogs until sufficient actual history exists |
| Capacity gate | Prevent a tier from exceeding usable physical slots |

Thresholds should be recalibrated quarterly or after a meaningful facility or
process change. SKU signals can refresh weekly. Operational tiers should change
only when the stabilization rules are satisfied.

### First Shadow Stability-Control Triage

The companion shadow routing replay now compares immediate, two-confirmation,
three-confirmation, asymmetric, and staged-demotion controls against the five
confirmed snapshots. It does not alter AX or ingestion.

The most useful candidate for continued observation is immediate promotions
with two-confirmation staged demotions:

| Metric | Legacy immediate routing | Candidate shadow routing |
| --- | ---: | ---: |
| Applied routing changes | 14,791 | 12,147 |
| Direct operational `AA -> C` jumps | 76 | 0 |
| Reversed within 14 days | 370 | 56 |
| Final differences versus June 1 forecast target | 0 | 1,858 |

The immediate baseline includes `50` returning-SKU routing changes in addition
to `14,741` adjacent shared-SKU changes. The shadow event output labels those
rows explicitly. This first result supports continued observation of staged
demotions; it does not authorize a production policy change. Five snapshots
remain insufficient for threshold selection, capacity gating, or relocation
payback analysis.

## Deployment Sequence

Do not add an AX-producing `--velocity-policy` switch to the ingestion pipeline
yet. The new policy is not calibrated or approved, and an ingestion flag could
make an experimental tier assignment look production-ready.

Use this sequence:

1. Keep `ingestion_pipeline.py` on the legacy policy.
2. Run a separate shadow simulator that reads the weekly forecast output and
   produces companion analysis files only.
3. Backtest score definitions, category weights, capacity bands, and stability
   controls.
4. Review the shadow report for several weekly forecast cycles.
5. After approval, add an explicit versioned ingestion option such as
   `--velocity-policy legacy|v1`, with `legacy` as the default during rollout.

The first scratch replay harness is
`scratch/simulate_velocity_policy_replay.py`. It writes local-only CSVs under
`scratch/velocity_policy_replay/`.

## Prototype Cutover Replay

The first replay covers the confirmed AX-effective snapshots from May 4 through
June 1, 2026. The map cutover was May 7, 2026 at 14:40 EDT.

Prototype assumptions:

1. Keep May 4 tier counts as fixed capacity quotas.
2. Use `FD1..FD14 / CaseQtyProxy / 14` for an initial near-term planning proxy.
3. Use all sales-order-consumed touches, including min/max and reset, for recent
   and same-period prior-year burden.
4. Use median historical replenishment quantity as a temporary `CaseQtyProxy`.
5. Replay confirmed AX-effective snapshots only.

These are scaffolding assumptions, not the final policy. The next replay must
retain ingestion-calculated planning `CaseQty` and observed last-put movement
quantity as separate fields rather than replacing one with the other.

| Prototype | Applied change events after cutover | Unique SKUs changed | SKUs changed more than once |
| --- | ---: | ---: | ---: |
| Confirmed legacy forecast tiers | 14,741 | 10,029 | 4,103 |
| Forecast cartons only | 18,103 | 10,392 | 6,134 |
| Recent consumed touches only | 7,657 | 5,683 | 1,557 |
| Hybrid, immediate changes | 17,751 | 10,557 | 5,557 |
| Hybrid, two-confirmation rule | 15,027 | 11,376 | 3,651 |

The replay proves the harness works and that stability controls matter. It does
not select a winning policy. The first hybrid weights are deliberately simple,
and the short confirmed-snapshot window means a two-confirmation rule can delay
rather than eliminate broad reclassification. The objective is not merely fewer
changes; it is lower avoidable churn while preserving high-burden coverage.

## What-If Questions

Prioritize these simulations:

| Question | Why it matters |
| --- | --- |
| What changes when normal demand, min/max, and reset touches are weighted separately? | Prevent anticipatory or reactive work from dominating the clean labor target |
| What is the best history mix: recent 4/8/13 weeks, same period last year, or two-year seasonal scaling? | Balance responsiveness and promotion awareness |
| How do results change when ingestion-calculated planning `CaseQty` and observed last-put quantities replace the temporary proxy? | Separate planning assumptions from actual operational movement |
| Which band boundaries maximize captured touches per premium slot? | Tie velocity to scarce physical capacity |
| How much churn reduction comes from 2-week, 3-week, asymmetric promotion, and demotion rules? | Stabilize routing without missing ramps |
| How many `C -> AA` and `AA -> C` jumps remain under each policy? | Directly test the BA and GM concern |
| How many SKUs reverse direction within 2, 4, and 8 weeks? | Measure threshold oscillation |
| Which SKUs need new-item, launch, clearance, or promotion overrides? | Handle cases without reliable history |
| How would a same-product/size `AA -> A -> B -> C` AX cascade perform under each stabilized policy? | Re-evaluate the paused AX LOE after tier quality improves |
| What relocation burden and payback follow each policy? | Avoid a mathematically cleaner map that costs too much to operate |

## Candidate Policies To Backtest

| Candidate | Description | Value |
| --- | --- | --- |
| Legacy baseline | Current fixed raw-unit cutoffs | Required comparison point |
| Production-calibrated fixed bands | Set cutoffs from actual carton flow or replenishment touches | Tests whether better units materially improve separation |
| Capacity-ranked bands | Rank SKUs by activity and assign tiers to fit available slot capacity | Keeps the map physically meaningful |
| Controlled dynamic hybrid | Combine forecast pressure, actual work, capacity, and exceptions; apply hysteresis and minimum duration | Recommended candidate |

For the hybrid, test promotion and demotion separately. Promoting an item may
need a stronger or more sustained signal than retaining it. Demoting a seasonal
item may need a forecast-aware grace period. The backtest should determine the
rules; it should not assume them.

## Offline Experiment Design

1. Extract row-level replenishment work for a 36-month calibration window.
   Keep the archive/PROD split and indexed access paths already validated.
2. Join each SKU-day to the exact forecast snapshot that was effective at that
   time. Preserve the SCD2 as-of semantics.
3. Add case quantity, pick history, lifecycle flags, and location capacity.
4. Backtest each candidate policy without writing to AX.
5. Score tier separation, weekly churn, reversal rate, slot capacity, routing
   match, and replenishment labor pressure.
6. Run the selected policy in shadow mode for several weekly forecast cycles.
7. Evaluate the paused AX cascade separately after the tier policy is stable.

## Deferred Upstream Forecast Review

The current investigation intentionally treats the ingestion forecast as an
input rather than attempting to redesign it. Operations has observed cases
where the model appears to overforecast. That matters because forecast error
can cause both unnecessary MinMax creation and avoidable weekly velocity churn.

Do not mix forecast-model changes into the first velocity-policy backtest.
First measure the inherited threshold policy and routing stability against
observed AX work. Preserve enough weekly forecast history to support a later
forecast-quality study: forecast bias, error by horizon, overforecast frequency,
and the relationship between forecast changes, MinMax creation, actual picks,
and actual last-put quantities.

The current seven confirmed forecast snapshots are enough to prove that the
problem exists and to start the extraction. They are not enough to select final
thresholds. Collecting more weekly snapshots will improve confidence,
especially around seasonal transitions and promotion-heavy periods.

## Completed Portable Enrichment - June 2

The shadow workspace now includes tracked Parquet facts for:

| Fact | Rows | Coverage |
| --- | ---: | --- |
| Ingestion-calculated planning `CaseQty` | 294,447 | Eight Product Info workbooks, March 30 through May 26, 2026 |
| SKU-location inventory occupancy | 91,987 | Six live snapshots, May 13 through June 2, 2026 |
| Direct-pick SKU-day activity | 2,929,501 | March 4, 2025 through June 2, 2026 |

The enriched replay separates Demand, MinMax, and Reset signals and compares
capacity-ranked stress tests. Forecast cartons alone captured only `23.18%` of
later Demand touches in `AA/A` under the conservative routing envelope. Adding
actual Demand touches and direct-pick history increased capture to `69.71%`.

This materially narrows the design space: actual work must participate in the
candidate score. It does not select final weights or deployable capacity bands.

### MinMax Pause Assumption

Operations paused MinMax after the map upload because it was creating too much
noise. Daily monitoring shows `Forward Replen` rows on May 13, 2026 and none
afterward through May 31. The shadow replay records May 14 at 00:00 EDT as an
inferred boundary, not an exact operator command timestamp.

Keep MinMax separate and diagnostic. Do not train or approve a MinMax weight as
though the post-pause period were a normal production sample.

## Physical Map Debt - June 2

Floor occupancy now confirms that a velocity change is not physically realized
when the analytical tier changes. Stock already in the forward location must
deplete through picks, be relocated, or otherwise leave the location before
capacity becomes reusable.

The June 2 inventory snapshot contains `14,666` occupied forward locations.
`7,487` locations have a physical velocity suffix different from the current
forecast target, and `5,824` of those mismatches have been observed for at
least 14 days. Between observed snapshots, `81.49%` to `94.21%` of starting
debt locations remained present at the next snapshot.

Treat physical map debt as an activation constraint. Preserve a dynamic weekly
`SignalTier`, but use a slower stateful `RoutingTier` for operational changes.
The next shadow design should:

1. keep promotions responsive when usable premium capacity exists;
2. require at least two confirmed snapshots before demotion;
3. stage demotions one tier at a time;
4. prefer empty or naturally clearing locations;
5. cap each activation batch by physical-debt budget and expected payback.

The enriched Demand-and-pick score remains a candidate ranking signal. Do not
activate it as a wholesale replacement map: its June 2 physical mismatch is
too broad for that.

## Incremental Activation Replay - June 2

The shadow workspace now tests exact-SlotTier fit and debt-budgeted activation
without changing production. The replay uses June 1 `RequiredSlots`, the May 7
deployed map paint, and June 2 floor occupancy.

The full enriched routing shape reduces aggregate exact-tier planning
shortfall from `2,883.4` to `1,205.4` slot-proxy units, but `78` SlotTiers
remain short and `18` candidate-demand tiers have no painted locations. This
confirms that suffix-level scoring and deployable map painting are related but
separate problems.

The replay also proves that incremental batches are measurable:

| Gross newly-created physical-debt budget | Accepted shadow changes | Net debt delta |
| --- | ---: | ---: |
| 0 | 3,238 | -527 |
| 250 | 3,452 | -300 |
| 500 | 3,666 | -51 |
| Unbounded | 3,923 | +225 |

Treat `0` as the conservative benchmark and `500` as a research envelope. Do
not upload either batch. Before a production candidate, solve exact-tier paint
shortfalls, add cube and manual PalletPicking exceptions, calibrate score
margin hysteresis, and observe more weekly snapshots.

`617` occupied premium-location demotions are intentionally quarantined for
review. They represent `704` premium locations and `41,337` physical units.
The review queue is a payback-analysis input, not an eviction list.

## Remaining Offline Gates - June 3

The remaining-gates replay completes the current offline work that can be done
before another weekly forecast snapshot arrives.

Completed now:

1. Exact-tier paint donor diagnostics: `390` donor options for `78` short
   candidate SlotTiers, including `143` same product/size-prefix transfer
   options.
2. PalletPicking/profile pressure screen: 18 of the top 20 pressure tiers have
   no PalletPicking paint today. Cube remains unavailable in the tracked
   shadow inputs, so this is not a cube-aware rule.
3. Score-margin hysteresis stress test: a `1.0%` rank-boundary buffer reduces
   signal-tier changes from `14,107` to `10,089` while preserving or slightly
   improving short-sample `AA/A` Demand-touch capture.

Treat `0.5%` to `2.0%` as the score-margin stress range for the next observed
weeks. Do not approve a final buffer yet. The `5.0%` buffer is likely too
sticky because it leaves `4,713` final differences versus the enriched
candidate signal.

Remaining work that needs either new weekly data or a refreshed AX cube pull:

- test map-fitting alternatives that preserve adjacency/travel, not just
  exact-tier headroom;
- re-run all churn, reversal, debt, and hysteresis metrics after each new
  confirmed weekly forecast.

### Empty-Location Repaint Fit

An additional repaint-fit proxy selects only currently empty donor locations
from the exact-tier transfer shortlist. It reduces candidate shortfall from
`1,205.4` to `180.1` slot-proxy units using `1,063` empty locations. `992`
selected locations are same product/size-prefix moves and `1,008` are same
category-cluster moves.

This is promising but not deployable. The average selected location is `11.5`
aisles from the short tier's median aisle, and the proxy does not run the full
market-basket travel simulation. Treat it as the input to the next
travel/adjacency-aware map fit, not as a map candidate.

### Cube and Physical-Travel Screen

The June 3 shadow refresh pulled cube directly from AX `WHSPHYSDIMUOM`. The
model uses:

```text
UnitCube = DEPTH * WIDTH * HEIGHT
```

This is the correct AX source for item physical dimensions, but it is still
unit/piece cube rather than carton cube. Use it to rank physical pressure and
Gaylord/PalletPicking exception candidates; do not let it override adjacency,
case quantity, replenishment touches, or operational handling observations by
itself.

The refreshed portable cube artifact contains `392,765` unique SKU dimension
rows and is only about `5.1 MB`, so it is safe to track for multi-PC work. It
shows the strongest AA daily cube-per-required-slot pressure in Kids Unisex:
`KUNMAA`, `KUNSAA`, `KUNXAA`, and `KUNLAA`.

The travel-aware screen used the calibrated physical router from
`ha-cluster-monitoring` to score the empty-location repaint shortlist. The
script measures feet from each selected empty donor location to the nearest
currently painted location for that candidate short tier, using the warehouse
crossover graph instead of raw sortcode or aisle distance.

Result:

| Metric | Value |
| --- | ---: |
| Empty repaint locations selected by capacity proxy | `1,063` |
| Locations scored with physical-router anchors | `1,018` |
| Mean nearest short-tier distance | `199.2 ft` |
| P90 nearest short-tier distance | `319.0 ft` |
| Within `50 ft` | `117` |
| Within `100 ft` | `263` |

Conclusion: exact-tier headroom is real, but the broad repaint batch is not
travel-safe. The next map-fit experiment should filter to close physical
neighborhoods first, then run a full basket/order travel replay.

## Observation Cadence

Continue running the unchanged legacy ingestion pipeline and uploading the
normal production AX file on the established weekly cadence. Preserve each
confirmed AX-effective output as the production baseline. Do not upload a
shadow-policy file to AX.

Use these checkpoints after the June 1, 2026 confirmed snapshot. As of the
June 16 upload, two additional confirmed snapshots have been added to the
shadow panel:

| Checkpoint | Additional weekly uploads | Approximate date | Purpose |
| --- | ---: | --- | --- |
| Early review | 4 | June 29, 2026 | Mature the 28-day reversal window and inspect whether staged demotions are behaving sensibly |
| Candidate recommendation | 8 | July 27, 2026 | Compare stability controls across roughly three months of confirmed snapshots |
| Stronger seasonal review | 12 | August 24, 2026 | Reduce the risk of selecting a rule from a short-lived pattern |

These dates assume the current weekly upload cadence continues. Offline work on
planning `CaseQty`, floor occupancy, relocation burden, capacity gates, and
lifecycle exceptions should proceed in parallel rather than waiting for the
later checkpoints.

## Decision Gate

Do not replace the current `AA/A/B/C` thresholds until the backtest can answer:

1. Which metric best predicts replenishment labor and pick-face pressure?
2. What capacity can each tier actually support?
3. How much churn is legitimate seasonal movement versus threshold noise?
4. How much churn reduction comes from hysteresis and minimum duration?
5. Do new-item and seasonal exceptions need explicit rules?
6. Does a stabilized tier policy change the value of the proposed AX cascade?
