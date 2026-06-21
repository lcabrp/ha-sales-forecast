# Velocity Policy Shadow Panel

## Purpose

This is an experiment-only dataset for evaluating velocity-tier churn and
future candidate policies. It does not change ingestion logic, AX files,
location directives, or approved layout maps.

Build it with:

```powershell
uv run python scratch/build_velocity_policy_shadow_panel.py --overwrite
```

Backtest routing-tier stability controls with:

```powershell
uv run python scratch/backtest_velocity_stability_controls.py --overwrite
```

Replay debt-budgeted incremental activation with:

```powershell
uv run python scratch/simulate_velocity_policy_incremental_activation.py --overwrite
```

Analyze remaining offline gates with:

```powershell
uv run python scratch/analyze_velocity_policy_remaining_gates.py --overwrite
```

Build an empty-location repaint-fit proxy with:

```powershell
uv run python scratch/simulate_velocity_policy_repaint_fit.py --overwrite
```

## Storage Contract

Detailed AX allocation links remain local and ignored:

```text
scratch/velocity_policy_replay/sales_order_replen_allocations_3y.parquet
```

The file carries sales-order attribution and demand-work identifiers. Keep it
out of repository history unless a later analysis specifically requires those
links across machines.

The deduplicated physical-touch fact is tracked because it is compact and
reusable across development machines:

```text
scratch/velocity_policy_replay/physical_replen_touches_3y.parquet
scratch/velocity_policy_replay/physical_replen_touches_3y_by_category.csv
scratch/velocity_policy_replay/physical_replen_touches_3y_by_final_put_line.csv
scratch/velocity_policy_replay/replenishment_history_3y_metadata.json
```

The Parquet file contains physical reserve-to-forward movements without
sales-order attribution. The small CSVs and metadata preserve validation
counts and extraction provenance.

Portable enrichment facts are also tracked:

```text
scratch/velocity_policy_replay/planning_case_qty_history.parquet
scratch/velocity_policy_replay/sku_location_inventory_snapshots.parquet
scratch/velocity_policy_replay/direct_pick_sku_day_15mo.parquet
```

These preserve calculated planning `CaseQty`, observed SKU-location inventory,
and SKU-day direct-pick pressure across development machines without carrying
order-level identifiers.

The compact derived outputs are stored under:

```text
Output/Monitoring/shadow_velocity_policy/
```

These are suitable for GitHub because they exclude sales-order identifiers and
use compressed Parquet for row-level analysis:

| Artifact | Purpose |
| --- | --- |
| `velocity_policy_sku_snapshot_panel.parquet` | One row per confirmed AX snapshot and SKU with forecast and observed-work features |
| `velocity_policy_transition_events.parquet` | One row per changed velocity tier with reversal flags and triage components |
| `velocity_policy_snapshot_tier_summary.csv` | Tier population, forecast, observed work, and frozen cutover capacity |
| `velocity_policy_transition_summary.csv` | Interval-level churn, direct jumps, reversals, and transition-priority totals |
| `velocity_policy_capacity_reference.csv` | Frozen May 7 cutover map location counts by velocity suffix |
| `velocity_policy_shadow_metadata.json` | Input hashes, output sizes, mode, and limitations |
| `velocity_policy_stability_events.parquet` | Shadow routing-tier changes applied by each stability policy |
| `velocity_policy_stability_interval_summary.csv` | Snapshot-by-snapshot deferred and applied routing changes |
| `velocity_policy_stability_policy_summary.csv` | Policy-level comparison of confirmations, staged demotions, reversals, and final differences |
| `velocity_policy_stability_metadata.json` | Stability replay hashes, mode, and limitations |
| `velocity_policy_enriched_candidate_sku_snapshot.parquet` | Candidate-scoring detail with planning `CaseQty`, direct picks, inventory occupancy, and separate category features |
| `velocity_policy_enriched_candidate_summary.csv` | Category-weight and capacity-envelope comparison |
| `velocity_policy_observed_transition_inventory_burden.parquet` | Observed tier changes joined to prior inventory occupancy for relocation triage |
| `velocity_policy_observed_transition_inventory_burden_summary.csv` | Interval-level demotion-burden proxy |
| `velocity_policy_enriched_candidate_metadata.json` | Enriched replay hashes, MinMax pause assumption, mode, and limitations |
| `velocity_policy_map_debt_location_snapshot.parquet` | Occupied location-level physical velocity debt by inventory snapshot |
| `velocity_policy_map_debt_snapshot_summary.csv` | Snapshot-level debt quantity, age, and premium-step proxies |
| `velocity_policy_map_debt_turnover_detail.parquet` | Debt-location turnover cohorts between observed inventory snapshots |
| `velocity_policy_map_debt_turnover_summary.csv` | Compact debt persistence summary |
| `velocity_policy_stability_tradeoff_summary.csv` | Forecast-target stability controls joined to physical map-debt outcomes |
| `velocity_policy_enriched_stability_sku_snapshot.parquet` | Stateful routing replay for the Demand-and-pick candidate score |
| `velocity_policy_enriched_stability_summary.csv` | Enriched-score routing controls compared by capture and physical debt |
| `velocity_policy_enriched_stability_metadata.json` | Enriched stability hashes, mode, and limitations |
| `velocity_policy_incremental_activation_candidates.parquet` | Changed-SKU queue with routing targets, floor burden, cohorts, and automatic-review gates |
| `velocity_policy_incremental_activation_budget_decisions.parquet` | Row-level accepted or blocked result for each SKU and debt budget |
| `velocity_policy_incremental_activation_budget_summary.csv` | Compact comparison of gross added-debt activation budgets |
| `velocity_policy_slottier_capacity_fit.csv` | Exact-SlotTier planning proxy compared with deployed paint and occupied locations |
| `velocity_policy_exception_cohort_summary.csv` | New, lifecycle, seasonality-proxy, and forecast-swing routing diagnostics |
| `velocity_policy_premium_demotion_review_queue.csv` | Occupied premium-location demotions held out of automatic activation |
| `velocity_policy_incremental_activation_metadata.json` | Activation replay hashes, definitions, mode, and limitations |
| `velocity_policy_exact_tier_paint_transfer_options.csv` | Candidate donor SlotTiers for exact-tier paint shortfalls |
| `velocity_policy_palletpicking_profile_pressure.csv` | PalletPicking/profile pressure screen without cube |
| `velocity_policy_score_margin_hysteresis_state.parquet` | Row-level score-boundary hysteresis stress state |
| `velocity_policy_score_margin_hysteresis_summary.csv` | Churn and capture trade-off by score-margin buffer |
| `velocity_policy_remaining_gates_metadata.json` | Remaining-gate hashes, definitions, mode, and limitations |
| `velocity_policy_repaint_fit_plan.csv` | Location-level empty donor repaint proxy for exact-tier shortfalls |
| `velocity_policy_repaint_fit_slottier_summary.csv` | Candidate shortfall after the repaint-fit proxy |
| `velocity_policy_repaint_fit_summary.csv` | Compact repaint-fit coverage and risk summary |
| `velocity_policy_repaint_fit_metadata.json` | Repaint-fit hashes, definitions, mode, and limitations |

## Important Semantics

- Historical movement quantity is `FinalPutInventQty` from the last put line.
- Physical labor uses distinct `TouchKey` rows, not raw allocation-link rows.
- Demand, MinMax later used by sales orders, and Reset later used by sales
  orders remain separate.
- `TransitionPriorityPoints` is a triage proxy. It is not measured relocation
  labor.
- Confirmed AX upload files do not contain the ingestion pipeline's calculated
  `CaseQty`. Add that feature later from historical source workbooks; do not
  substitute observed last-put quantity as though the two concepts were equal.
- Stability events label whether the SKU existed in the immediately prior
  confirmed snapshot. This keeps returning-SKU routing changes separate from
  adjacent shared-SKU churn.
- The MinMax pause boundary is retained as an explicit replay assumption.
  MinMax remains a separate diagnostic category and must not be treated as a
  uniform production sample after the pause.
- Physical map debt is an occupied location whose velocity suffix differs from
  its shadow target routing tier. It is a routing-capacity constraint, not a
  claim that the occupied location is incorrect.
- Inventory disappearance is a turnover proxy only. It does not distinguish
  natural picking depletion from manual moves or adjustments.
- Incremental activation budgets cap gross newly created occupied-location
  debt. Debt resolved by the same batch is reported separately.
- Premium-location demotions remain manual-review candidates. The queue is not
  an instruction to relocate or evict stock.
- Score-margin hysteresis is a stress test on the analytical signal. It is
  separate from routing-tier confirmation and staged-demotion controls.
- Cube now uses AX `WHSPHYSDIMUOM` in the shadow refresh. `UnitCube` is
  `DEPTH * WIDTH * HEIGHT`, which is a unit/piece cube proxy, not carton cube.
- Empty-location repaint-fit outputs are capacity diagnostics only. They do not
  run the allocator, preserve all adjacency rules, or create AX-ready maps.
- Physical-travel repaint outputs are an adjacency screen only. They use the
  calibrated `ha-cluster-monitoring` router to measure feet to nearby current
  tier anchors, but they still do not replay full market baskets.

## Next Enrichment

Continue collecting SKU-location inventory snapshots so physical-debt age and
turnover windows mature. Filter exact-tier paint changes to physically close
neighborhoods and run a full basket/order travel replay before proposing an
operational map.
