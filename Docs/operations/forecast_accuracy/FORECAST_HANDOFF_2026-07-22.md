# Forecast Model Handoff — 2026-07-22

Single entry point for continuing the category-pool forecasting work (including
by a different LLM / engineer). Read this, then the two companion docs:

- `FORECAST_MODEL_PROPOSALS_2026-07-22.md` — the models and the July-7 result.
- `FORECAST_MODEL_VALIDATION_2026-07-22.md` — the tests, the "why", and the
  honest negative results.

Authority order unchanged: `FORECAST_CURRENT_STATE.md` remains the decision
authority; this handoff is additive research.

---

## A. What was built (2026-07-22)

Three new scripts under `scripts/python/` (research candidates — NOT a promoted
champion):

| Script | Purpose |
|---|---|
| `forecast_model_category_pool.py` | Two-stage category-pool model + CLI. Stage 1 category volume (independent lift or corporate anchor); Stage 2 largest-remainder allocation to current SKUs; optional `--activation` season-transition layer. |
| `forecast_backtest_category_pool.py` | Origin-safe post-close diagnostic at the 2026-07-07 origin vs the saved closeout actuals; reproduces published corporate numbers as a correctness check. |
| `forecast_validate_category_pool.py` | Guardrail assertions + 11-window oracle-total allocation backtest + 7-window offline activation backtest. |

Key results (full detail in the validation doc):

- **Flagship diagnostic (July 7-20, real corporate + real actuals):**
  `catpool_corporate_anchor_activation` improves the allocation tradeoff:
  **SKU WAPE 1.05→0.89** (sub-0.90 achieved for the first time) and coverage
  **0.67→0.77**, while SKU-use precision declines **0.86→0.83** and zero-demand
  unit share rises slightly **0.088→0.091**. Corporate volume is preserved
  exactly at 204,654 units. This is strong post-close research evidence, not a
  retroactive frozen contestant.
- **Guardrails all pass:** no-leakage, exact Hamilton total-preservation,
  determinism, corporate daily-total preserved.
- **Completed today with live AX SQL (`prodaxsql2`):**
  1. Extended `direct_pick_sku_day_modified_2026.parquet` from 2026-06-25 through **2026-07-22** (1,192,152 rows, 3.49M units).
  2. Mirrored canonical `sku_category_crosswalk.parquet` (113,824 SKUs, 83 category-size cells) into `Output/ForecastAccuracy/product_attributes/`.
  3. Implemented assortment-turnover gating for activation layer and promotional run-rate spike clipping.
- **New July 21-Aug 3 late-origin diagnostic** built on July 22 beside (not
  replacing) the two legitimate July 21 forward contestants:
  `Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/`.
  It may be scored on August 4 for learning, but it cannot win the prospective
  July 21 contest.

---

## B. Data available NOW (offline, already tracked) — use this first

On a PC without live AX, these tracked facts already support substantial
offline work and are the reason the multi-window tests are reproducible:

| Fact | Path | Coverage | Enables |
|---|---|---|---|
| Strict DirectPick SKU/day | `Output/ForecastAccuracy/direct_pick_history/parquet/` | 2022-01 → **2026-07-22** | training history and offline diagnostics; operational closeouts still follow the actual-source precedence in `FORECAST_CURRENT_STATE.md` |
| Saved July 7-20 closeout actuals | `Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_closeout/actual_sku_day.parquet` | 14 days | the one real corporate-anchored frozen comparison |
| Saved July-7 corporate feed | `Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_challenger/forward_daily_forecasts.parquet` | July 7 origin | corporate anchor for that window |
| **AX inventory history (daily)** | `Output/ForecastAccuracy/inventory/ax_inventory_history_sku_day.parquet` | **2026-04-01 → 06-14, 75 days** | origin-safe activation evidence for **Apr–Jun origins** (this is how §3b was run) |
| Pick-face inventory (daily) | `Output/ForecastAccuracy/inventory/pickface_inventory_sku_day.parquet` | 2026-06-19 → 07-21 | activation evidence for July origins |
| Open inbound (daily) | `Output/ForecastAccuracy/inbound/ax_open_inbound_sku_day.parquet` | 2026-06-19 → 07-21 | activation inbound signal (July) |
| Product Info inbound (sparse) | `Output/ForecastAccuracy/inbound/product_info_inbound_snapshots.parquet` | 2024-03 → 2026-06-01 | activation inbound signal (Apr–Jun) |
| Model feature panel (parts) | `Output/ForecastAccuracy/model/model_sku_day_panel_parts/` | 2025-01 → **2026-06-08** | ready-made ML feature matrix (target, corporate qty, category, inventory/inbound lags, promo, rolling baselines) for the future ML occurrence/residual layer |
| Promotion SKU/day features | `Output/ForecastAccuracy/promotions/pdl_sku_day_features.parquet` | → 2026-07-21 | promotion eligibility/discount features |
| Canonical category crosswalk | `Output/ForecastAccuracy/product_attributes/sku_category_crosswalk.parquet` | mirrored 2026-07-22 | 113,824 SKUs → 83 ProductGroup+SizeGroup cells |
| Historical handoff ledgers | `Output/ForecastAccuracy/**/ingestion_output/sku_ledger.db` | Jul-6 / Jul-11 snapshots | immutable crosswalk snapshots retained for exact historical reproduction |

**Takeaway:** you do NOT need new data to (a) keep tuning the allocation, (b)
multi-window the *category-mix* lever, (c) multi-window the *activation* lever
for Apr–Jun, or (d) prototype the ML occurrence/residual layer on the panel.
Run `forecast_validate_category_pool.py` (takes a few minutes).

### The large local-only / untracked files (gitignored)

These will **not** reach GitHub on push (they are in `.gitignore`). If one is the
"big untracked file", here is what each is and whether it helps:

| Gitignored path | ~Size | Helps the model work? |
|---|---|---|
| `Output/ForecastAccuracy/model/model_sku_day_panel.parquet` | ~221 MB | Same content as the already-tracked `model_sku_day_panel_parts/` (ends 2026-06-08). **No new info** unless you rebuilt it more recently — if so, re-split it with `forecast_model_split_panel.py` and commit the parts. |
| `Output/ForecastAccuracy/promotions/pdl_offer_rows.csv` | ~122 MB | Raw PDL offer rows. The compact `pdl_offer_rows.parquet` + `pdl_sku_day_features.parquet` are tracked and are what the model consumes. Only needed for raw re-extraction. |
| `Output/ForecastAccuracy/promotions/promotions.db` | large | Regenerable SQLite; not required by the model. |
| `Source/Promotions/6.18.26 Hanna Sale PDL.xlsx`, `7.21.26 …xlsm` | large | Raw promo workbooks; extracted tables already tracked. |

If the big untracked file is something **else** (e.g. a fresh multi-year
inventory export, a BigQuery dump, or a rebuilt panel that extends past
2026-07-22), that WOULD be valuable — see §C. To check what it is, run:
`python -c "import pandas as pd; df=pd.read_parquet(PATH); print(df.shape); print(list(df.columns)); print(df.filter(regex='(?i)date').head())"`
and share the columns + date range. **Because it exceeds GitHub's 90 MB ceiling,
do not push it**; instead split it into <90 MB Parquet parts (per the tracked-
artifact policy) or share it out-of-band.

---

## C. Data NEEDED to finish the open questions (requires local PC / AX)

Ordered by value. Each item lists WHY and HOW.

1. **More post-2026-06-19 pick-face inventory + open-inbound snapshots, and the
   matching closed-window actuals — to properly test *gated* activation across
   real season transitions.**
   - WHY: activation's July-7 win is one window; §3b shows it hurts mid-season.
     We need several *transition* windows to design and prove the season gate.
   - HOW (on the PC with the monitoring repo + AX/VPN):
     - `sync_monitoring_forecast_artifacts.bat` (daily) — refreshes the two
       snapshot files from `ha-kydc-monitoring`.
     - At each 14-day closeout, save the actuals: either extend the strict shards
       with `forecast_direct_pick_history.py`, or run
       `forecast_window_compare.py --live-ax --output-dir …` to save an
       `actual_sku_day.parquet` for that window.
   - Then push the refreshed parquet + saved actuals; the offline scripts here
     can re-run with zero AX.

2. **Per-origin frozen corporate FwdDemand feeds — to multi-window the
   *corporate-anchored* candidate (the flagship).**
   - WHY: the corporate anchor is only tested on July-7 offline; we cannot claim
     a champion from one window.
   - HOW: for each historical origin, the frozen `FwdDemandCSV_*.csv` /
     `forward_daily_forecasts.parquet` that was valid at that origin. Feed via
     `forecast_model_category_pool.py --corporate-daily …`.

3. **BigQuery inventory history** — only when its schema/coverage is confirmed
   (see the data-landscape doc). Needed to mark stockout-censored SKU/days in
   the 2024/2025 analog sale windows and separate weak demand from no inventory.

---

## D. Recommended next steps (in order)

1. **Refine the implemented assortment-turnover gate.** The current gate keeps
   the July-7 gain but remains too permissive in the Apr-Jun oracle tests
   (activation mean WAPE 0.925 versus 0.630 base). Require neutral-or-positive
   mid-season behavior before promotion.
2. **Validate the implemented run-rate de-spiking across additional event and
   non-event origins.** The independent Stage-1 total remains unsafe as a
   production volume owner.
3. Once items in §C.1 land, **multi-window the corporate-anchored candidate** and
   only then consider promoting a champion (with a new frozen evaluation, per the
   Frozen Evaluation Rules).
4. Add the **carton-use simulator** so the SKU-use proxy becomes real
   pull-efficiency.
5. Add **ML in the narrow occurrence/residual role** on the category-anchored
   base, using `model_sku_day_panel_parts/` (rebuild/extend the panel first; it
   ends 2026-06-08).

---

## E. How to reproduce everything here (no AX)

```bash
uv sync
uv run python scripts/python/forecast_backtest_category_pool.py        # July-7 origin-safe post-close diagnostic + ablation
uv run python scripts/python/forecast_validate_category_pool.py        # guardrails + 11-window + activation (few min)
# Rebuild the July 22 late-origin diagnostic (score for learning on/after Aug 4):
uv run python scripts/python/forecast_model_category_pool.py \
  --origin 2026-07-21 \
  --ledger-db Output/ForecastAccuracy/handoff_eval/independent_hybrid_absolute_log_2026-07-07/ingestion_output/sku_ledger.db \
  --corporate-daily Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/forward_daily_forecasts.parquet \
  --activation \
  --output-dir Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow
```

Outputs land under `Output/ForecastAccuracy/handoff_eval/category_pool_backtest_2026-07-07/`
and `…/category_pool_validation/` (leaderboards, category scorecards, guardrail
results, per-window WAPE tables).

---

## F. August 5 closeout outcome

The authoritative prospective contest covered **2026-07-21 through 2026-08-03**
and was closed on 2026-08-05. Read
`FORECAST_CLOSEOUT_2026-07-21_TO_2026-08-03.md` for the scorecard, source
contract, and decision. Do **not** rebuild either saved forecast Parquet; their
scoreable state is preserved at Git commit `b0a252a` and the completed evidence
is under `Output/ForecastAccuracy/handoff_eval/forward_2026-07-21_closeout/`.

The command below is retained only to reproduce the saved closeout on a machine
with live AX access. Never feed post-origin inventory/inbound refreshes back
into the saved candidates.

```powershell
uv run python scripts/python/sync_monitoring_forecast_artifacts.py

uv run python scripts/python/forecast_actuals_source_audit.py `
  --start-date 2026-07-21 `
  --through-date 2026-08-03

uv run python scripts/python/forecast_window_compare.py `
  --start-date 2026-07-21 `
  --through-date 2026-08-03 `
  --daily-forecast Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/recent_shape_shadow/forward_daily_forecasts.parquet `
  --daily-forecast Output/ForecastAccuracy/forward_tests/2026-07-21_corporate_2026-07-20/category_pool_shadow/category_pool_daily_forecasts.parquet `
  --ledger-db Output/ForecastAccuracy/handoff_eval/independent_hybrid_absolute_log_2026-07-07/ingestion_output/sku_ledger.db `
  --live-ax `
  --output-dir Output/ForecastAccuracy/handoff_eval/forward_2026-07-21_closeout
```

Use `--live-ax` only when the source audit confirms that no complete canonical
monitoring-scope SKU/day actual is available. If one exists, replace
`--live-ax` with `--actuals <canonical-actuals.parquet>`. The tracked handoff
ledger above is intentional: `forecast_window_compare.py` currently reads a
SQLite ledger, while the new canonical crosswalk is Parquet.

The combined command scores five saved series, but the decision report must keep
their statuses separate:

- **Prospectively frozen contestants:** `corporate_raw` and
  `corporate_total_recent_shape`.
- **Pre-origin volume diagnostic only:** `independent_recent_shape`.
- **Built July 22 after the origin; late-origin diagnostics only:**
  `catpool_corporate_anchor_activation` (165,008 units; 9,692 positive SKUs)
  and `catpool_activation` (150,869 units; 25,039 positive SKUs).

At closeout, retain the exact actual SKU/day Parquet, row/SKU/unit counts,
monitoring reconciliation, query window and source provenance written under the
durable output directory. A strong category-pool score is useful evidence for
the next clean origin; it cannot become the winner of the July 21 contest.
