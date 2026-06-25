# Tool Manifest

Purpose: token-efficient registry of executable tools in this repository. For future LLM work, read this file first to choose the right command before opening individual scripts.

Repository scope: Forecast replacement research, sales/inventory/history extracts, backtests, model training, forecast policy candidates, and scorecards.

Repo boundaries:
- Production Forward Demand ingestion belongs in `ha-ingestion-pipeline`.
- Warehouse layout/map work belongs in `ha-warehouse-layout`.
- Operational monitoring and SlotTier SCD history belong in `ha-kydc-monitoring`.

Rules of thumb:
- Prefer active scripts over `scratch/` unless revisiting an old investigation.
- Treat SQL scripts as read/review first; confirm database, company, warehouse, and date filters before execution.
- For Python scripts, run with `uv run python <path>` unless the repo docs say otherwise.

## Forecast Scripts

- **Script Name:** `extract_promotions.py`
  - **Path:** `scripts/python/extract_promotions.py`
  - **Goal:** Extract promotion planning workbook features for forecast modeling.
  - **Params:** `--source-dir`, `--output-dir`, `--db`, `--replace-existing`, `--no-sqlite`.
  - **Trigger:** Run when extracting or inspecting source/reference data.

- **Script Name:** `forecast_accuracy.py`
  - **Path:** `scripts/python/forecast_accuracy.py`
  - **Goal:** Build a local forecast-accuracy SQLite workspace from forecast CSV snapshots.
  - **Params:** `--input-dir`, `--db`, `--overwrite`, `--include-zero-days`, `--start-date`, `--end-date`, `--server`, `--database`, `--date-field`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_corporate_db_extract.py`
  - **Path:** `scripts/python/forecast_corporate_db_extract.py`
  - **Goal:** Snapshot selected Azure SQL Forecast DB tables to local Parquet datasets.
  - **Params:** `--server`, `--database`, `--driver`, `--auth`, `--user`, `--timeout`, `--chunk-rows`, `--output-dir`, `--group`, `--table`, `--exclude-table`, `--calendar-start`, `--calendar-end`, `--max-rows-per-table`, `--ordered`, `--dry-run`, `--list-groups`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_direct_pick_history.py`
  - **Path:** `scripts/python/forecast_direct_pick_history.py`
  - **Goal:** Export sharded SKU/day DirectPick history for forecast training.
  - **Params:** `--server`, `--database`, `--start-date`, `--end-date`, `--output-dir`, `--date-basis`, `--overwrite`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_history_dataset.py`
  - **Path:** `scripts/python/forecast_history_dataset.py`
  - **Goal:** Build multi-year forecast accuracy datasets from AX forecast files and picks.
  - **Params:** `--output-dir`, `--since`, `--folder`, `--start-date`, `--end-date`, `--date-field`, `--server`, `--database`, `--date-basis`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_inventory_history.py`
  - **Path:** `scripts/python/forecast_inventory_history.py`
  - **Goal:** Extract AX inventory history snapshots for forecast-model features.
  - **Params:** `--start-date`, `--end-date`, `--output-dir`, `--server`, `--database`, `--warehouse`, `--site`, `--data-area`, `--partition-id`, `--exclude-sku`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_candidate_output.py`
  - **Path:** `scripts/python/forecast_model_candidate_output.py`
  - **Goal:** Build a shadow AX-style forecast candidate from the champion sklearn model.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--forecast-start`, `--forecast-end`, `--forecast-days`, `--model`, `--forecast-source`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--min-fd14-units`, `--allow-missing-ax-attributes`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_compare_sklearn.py`
  - **Path:** `scripts/python/forecast_model_compare_sklearn.py`
  - **Goal:** Compare scikit-learn forecast model candidates on the model panel.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--holdout-start`, `--holdout-end`, `--holdout-days`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--models`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_error_review.py`
  - **Path:** `scripts/python/forecast_model_error_review.py`
  - **Goal:** Review independent champion forecast errors against corporate forecast.
  - **Params:** `--input`, `--output-dir`, `--top-n`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_frozen_origin_eval.py`
  - **Path:** `scripts/python/forecast_model_frozen_origin_eval.py`
  - **Goal:** Frozen-origin / recursive forward-forecast evaluation harness.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--window`, `--modes`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_horizon_train.py`
  - **Path:** `scripts/python/forecast_model_horizon_train.py`
  - **Goal:** Horizon-consistent forward forecast: train AND evaluate honestly.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--window`, `--origin-stride`, `--keep-zero-frac`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--also-train-old-champion`, `--save-forecast`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_overlay_rules.py`
  - **Path:** `scripts/python/forecast_model_overlay_rules.py`
  - **Goal:** Backtest transparent overlay rules on top of the champion sklearn forecast.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--window`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_panel.py`
  - **Path:** `scripts/python/forecast_model_panel.py`
  - **Goal:** Build the first model-ready SKU/day forecast panel.
  - **Params:** `--start-date`, `--end-date`, `--output-dir`, `--workers`, `--holdout-days`, `--skip-backtest`, `--sample-rows`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_promo_newness_audit.py`
  - **Path:** `scripts/python/forecast_model_promo_newness_audit.py`
  - **Goal:** Audit promo and newness coverage for independent forecast misses.
  - **Params:** `--forecast`, `--panel`, `--product-info-dir`, `--output-dir`, `--top-n`, `--skip-product-info`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_reconcile.py`
  - **Path:** `scripts/python/forecast_model_reconcile.py`
  - **Goal:** Hierarchical reconciliation for SKU/day forecasts.
  - **Params:** `--forecast`, `--output-dir`, `--base`, `--target`, `--blend-alpha`, `--max-factor`, `--save-forecast`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_rolling_origin_sklearn.py`
  - **Path:** `scripts/python/forecast_model_rolling_origin_sklearn.py`
  - **Goal:** Run rolling-origin scikit-learn forecast comparisons.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--models`, `--window`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_slotting_scorecard.py`
  - **Path:** `scripts/python/forecast_model_slotting_scorecard.py`
  - **Goal:** Velocity-tier / slotting scorecard for forecast candidates.
  - **Params:** `--forecast`, `--output-dir`, `--thirteen-week-factor`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_split_panel.py`
  - **Path:** `scripts/python/forecast_model_split_panel.py`
  - **Goal:** Split the model SKU/day panel into GitHub-sized monthly Parquet parts.
  - **Params:** `--panel`, `--output-dir`, `--compression`, `--combine`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_train.py`
  - **Path:** `scripts/python/forecast_model_train.py`
  - **Goal:** Train the first SKU/day forecast model from the model panel.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--holdout-start`, `--holdout-end`, `--holdout-days`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--check-deps`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_model_train_quantile.py`
  - **Path:** `scripts/python/forecast_model_train_quantile.py`
  - **Goal:** Train a SKU/day forecast model from the model panel using Quantile Regression and Demand Censoring.
  - **Params:** `--panel`, `--output-dir`, `--start-date`, `--holdout-start`, `--holdout-end`, `--holdout-days`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--quantile`, `--disable-censoring`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--check-deps`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_pipeline_runner.py`
  - **Path:** `scripts/python/forecast_pipeline_runner.py`
  - **Goal:** One-command pipeline runner orchestrator for warehouse zoning & slotting forecasts.
  - **Params:** `--source-file`, `--forecast-start-date`, `--max-windows`, `--lookback`, `--threads`, `--output-dir`, `--c-over`, `--c-under`, `--c-zero`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_planner_extract.py`
  - **Path:** `scripts/python/forecast_planner_extract.py`
  - **Goal:** Extract daily Planner forecast/actual totals for forecast calibration.
  - **Params:** `--year`, `--source-dir`, `--source-file`, `--output-dir`, `--snapshot`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_planner_scale_forward_demand.py`
  - **Path:** `scripts/python/forecast_planner_scale_forward_demand.py`
  - **Goal:** Scale an AX Forward Demand CSV to Planner daily total units.
  - **Params:** `--input-csv`, `--planner-daily-path`, `--output-dir`, `--candidate-id`, `--planner-scale`, `--planner-column`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_product_info_inbound.py`
  - **Path:** `scripts/python/forecast_product_info_inbound.py`
  - **Goal:** Extract Product Info workbook inbound snapshots for model features.
  - **Params:** `--source-dir`, `--output-dir`, `--pattern`, `--max-files`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_promo_sku_features.py`
  - **Path:** `scripts/python/forecast_promo_sku_features.py`
  - **Goal:** Build SKU/day promotion features from PDL offer rows.
  - **Params:** `--pdl-offer-rows`, `--forecast-snapshot`, `--output-dir`, `--start-date`, `--end-date`, `--max-event-days`, `--sample-rows`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_backtest.py`
  - **Path:** `scripts/python/forecast_replacement_backtest.py`
  - **Goal:** Backtest BRG replacement candidates against historical DirectPick actuals.
  - **Params:** `--output-dir`, `--forecast-day-path`, `--forecast-snapshot-path`, `--snapshot-summary-path`, `--actuals-path`, `--pdl-sku-features-path`, `--start-date`, `--end-date`, `--max-windows`, `--lookback-days`, `--seasonal-years`, `--seasonal-window-days`, `--seasonal-recent-weight`, `--threads`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_contract.py`
  - **Path:** `scripts/python/forecast_replacement_contract.py`
  - **Goal:** Build and validate BRG-like forecast replacement candidate packages.
  - **Params:** `--source-file`, `--output-root`, `--candidate-type`, `--candidate-id`, `--forecast-start-date`, `--lookback-days`, `--actuals-path`, `--pdl-sku-features-path`, `--inbound-path`, `--reservations-path`, `--seasonal-years`, `--seasonal-window-days`, `--seasonal-recent-weight`, `--sample-rows`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_cost_scorecard.py`
  - **Path:** `scripts/python/forecast_replacement_cost_scorecard.py`
  - **Goal:** Expected-cost scorecard for forecast replacement candidates.
  - **Params:** `--score-file`, `--output-dir`, `--candidate`, `--focus`, `--c-over`, `--c-under`, `--c-zero`, `--understock-sweep`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_hybrid_candidate.py`
  - **Path:** `scripts/python/forecast_replacement_hybrid_candidate.py`
  - **Goal:** Build a BRG-like candidate package from the conservative hybrid ML forecast.
  - **Params:** `--source-file`, `--output-root`, `--candidate-id`, `--forecast-start-date`, `--panel`, `--actuals-path`, `--pdl-sku-features-path`, `--lookback-days`, `--model`, `--ml-threshold-units`, `--recent-fallback-weight`, `--recent-volume-cap`, `--planner-daily-path`, `--planner-total-anchor`, `--planner-total-scale`, `--weekly-tail-scale`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--start-date`, `--exclude-corporate-features`, `--include-product-identity-features`, `--sample-rows`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_hybrid_cold_start_candidate.py`
  - **Path:** `scripts/python/forecast_replacement_hybrid_cold_start_candidate.py`
  - **Goal:** Build a BRG-like candidate package from the cold-start quantile hybrid ML forecast using DB attributes.
  - **Params:** `--source-file`, `--snapshot-dir`, `--output-root`, `--candidate-id`, `--forecast-start-date`, `--panel`, `--actuals-path`, `--pdl-sku-features-path`, `--lookback-days`, `--quantile`, `--disable-censoring`, `--disable-blending`, `--ml-threshold-units`, `--recent-fallback-weight`, `--recent-volume-cap`, `--weekly-tail-scale`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--start-date`, `--exclude-corporate-features`, `--include-product-identity-features`, `--sample-rows`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_hybrid_quantile_candidate.py`
  - **Path:** `scripts/python/forecast_replacement_hybrid_quantile_candidate.py`
  - **Goal:** Build a BRG-like candidate package from the conservative quantile hybrid ML forecast.
  - **Params:** `--source-file`, `--output-root`, `--candidate-id`, `--forecast-start-date`, `--panel`, `--actuals-path`, `--pdl-sku-features-path`, `--lookback-days`, `--quantile`, `--disable-censoring`, `--disable-blending`, `--ml-threshold-units`, `--recent-fallback-weight`, `--recent-volume-cap`, `--weekly-tail-scale`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--start-date`, `--exclude-corporate-features`, `--include-product-identity-features`, `--sample-rows`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_ml_backtest.py`
  - **Path:** `scripts/python/forecast_replacement_ml_backtest.py`
  - **Goal:** Backtest the sklearn champion as a future-safe BRG replacement candidate.
  - **Params:** `--panel`, `--output-dir`, `--snapshot-summary-path`, `--forecast-snapshot-path`, `--actuals-path`, `--pdl-sku-features-path`, `--promo-daily-path`, `--start-date`, `--end-date`, `--panel-start-date`, `--max-windows`, `--lookback-days`, `--models`, `--sku-total-thresholds`, `--hybrid-recent-fallback-weights`, `--hybrid-recent-volume-caps`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--include-seasonal-features`, `--seasonal-years`, `--seasonal-window-days`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_ml_cold_start.py`
  - **Path:** `scripts/python/forecast_replacement_ml_cold_start.py`
  - **Goal:** Backtest the Cold-Start SKU/day forecast model using Forecast DB product attributes.
  - **Params:** `--panel`, `--snapshot-dir`, `--output-dir`, `--snapshot-summary-path`, `--forecast-snapshot-path`, `--forecast-day-path`, `--actuals-path`, `--pdl-sku-features-path`, `--promo-daily-path`, `--start-date`, `--end-date`, `--panel-start-date`, `--max-windows`, `--lookback-days`, `--sku-total-thresholds`, `--hybrid-recent-fallback-weights`, `--hybrid-recent-volume-caps`, `--quantile`, `--disable-censoring`, `--disable-blending`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--include-seasonal-features`, `--seasonal-years`, `--seasonal-window-days`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_ml_quantile_backtest.py`
  - **Path:** `scripts/python/forecast_replacement_ml_quantile_backtest.py`
  - **Goal:** Backtest the Quantile SKU/day forecast model with Demand Censoring and Corporate Blending.
  - **Params:** `--panel`, `--output-dir`, `--snapshot-summary-path`, `--forecast-snapshot-path`, `--forecast-day-path`, `--actuals-path`, `--pdl-sku-features-path`, `--promo-daily-path`, `--start-date`, `--end-date`, `--panel-start-date`, `--max-windows`, `--lookback-days`, `--sku-total-thresholds`, `--hybrid-recent-fallback-weights`, `--hybrid-recent-volume-caps`, `--quantile`, `--disable-censoring`, `--disable-blending`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--exclude-corporate-features`, `--include-product-identity-features`, `--include-seasonal-features`, `--seasonal-years`, `--seasonal-window-days`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_policy_backtest.py`
  - **Path:** `scripts/python/forecast_replacement_policy_backtest.py`
  - **Goal:** Compare fixed and prior-window forecast replacement policies.
  - **Params:** `--score-file`, `--output-dir`, `--candidate`, `--default-candidate`, `--lookback-windows`, `--expanding-warmup-windows`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_recency_brake.py`
  - **Path:** `scripts/python/forecast_replacement_recency_brake.py`
  - **Goal:** Self-calibrating recency / regime brake for forecast replacement.
  - **Params:** `--score-file`, `--output-dir`, `--reference`, `--cap-variant`, `--floor-candidate`, `--compare`, `--lookback`, `--min-cap`, `--min-coverage`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_replacement_shadow_window.py`
  - **Path:** `scripts/python/forecast_replacement_shadow_window.py`
  - **Goal:** Score replacement forecast candidates on an arbitrary recent shadow window.
  - **Params:** `--source-file`, `--panel`, `--actuals-path`, `--pdl-sku-features-path`, `--forecast-day-path`, `--snapshot-summary-path`, `--output-dir`, `--forecast-start-date`, `--forecast-days`, `--lookback-days`, `--model`, `--ml-threshold-units`, `--recent-fallback-weights`, `--recent-volume-caps`, `--base-frozen-forecast-path`, `--include-yoy-sale-lift-overlay`, `--yoy-direct-pick-history-path`, `--yoy-analog-sale-start`, `--yoy-analog-sale-end`, `--yoy-analog-baseline-start`, `--yoy-analog-baseline-end`, `--yoy-current-baseline-days`, `--yoy-lift-floor`, `--yoy-lift-cap`, `--yoy-total-cap-mode`, `--yoy-total-cap-units`, `--yoy-shrink-units`, `--yoy-overlay-shape-candidate`, `--yoy-overlay-candidate-name`, `--max-train-rows`, `--random-state`, `--max-iter`, `--learning-rate`, `--max-leaf-nodes`, `--calibration-days`, `--calibration-mode`, `--calibration-min-rows`, `--calibration-min-actual-units`, `--threads`, `--start-date`, `--exclude-corporate-features`, `--include-product-identity-features`, `--overwrite-frozen-forecast`, `--allow-partial-actuals`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_reservation_snapshot.py`
  - **Path:** `scripts/python/forecast_reservation_snapshot.py`
  - **Goal:** Capture live AX WMS reservation snapshots for forecast-model features.
  - **Params:** `--snapshot-date`, `--output-dir`, `--server`, `--database`, `--warehouse`, `--site`, `--data-area`, `--partition-id`, `--exclude-item`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_sales_orders.py`
  - **Path:** `scripts/python/forecast_sales_orders.py`
  - **Goal:** Extract sales-order price and discount features from AX for forecast modeling.
  - **Params:** `--server`, `--database`, `--schema`, `--start-date`, `--end-date`, `--output-dir`, `--db`, `--chunk-days`, `--origin`, `--include-all-origins`, `--exclude-item`, `--keep-parts`, `--no-sqlite`, `--no-csv`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_warehouse_supply_history.py`
  - **Path:** `scripts/python/forecast_warehouse_supply_history.py`
  - **Goal:** Extract warehouse supply work history for forecast-model diagnostics.
  - **Params:** `--start-date`, `--end-date`, `--output-dir`, `--server`, `--database`, `--warehouse`, `--data-area`, `--partition-id`, `--chunk-days`, `--keep-detail`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

## SQL Helpers

- **Script Name:** `Create_Forecast_SlotTier_SCD_PowerBI.sql`
  - **Path:** `scripts/sql/Create_Forecast_SlotTier_SCD_PowerBI.sql`
  - **Goal:** Optional shared SQL Server contract for Power BI.
  - **Params:** none detected.
  - **Trigger:** Run only after reviewing database, company, warehouse, and date filters.

## Shared Or Imported Helpers

- **Script Name:** `settings.py`
  - **Path:** `scripts/python/config/settings.py`
  - **Goal:** Centralized operational settings for ha-sales-forecast scripts.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `ingestion_pipeline.py`
  - **Path:** `scripts/python/ingestion_pipeline.py`
  - **Goal:** Ingestion Pipeline v3 — Zoning & Slotting Modernization ======================================================== This script replaces the legacy Excel-based workflow that involved two sequential workbooks (Case Quantity
  - **Params:** `--operator-mode/--quiet`, `--prompt-copy-to-ax-share`, `--copy-to-ax-share`, `--source-file`, `--output-dir`.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `live_inventory_classifier.py`
  - **Path:** `scripts/python/live_inventory_classifier.py`
  - **Goal:** Live floor SKU classification for layout fit checks.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `output_paths.py`
  - **Path:** `scripts/python/output_paths.py`
  - **Goal:** Shared output folder contract for forecast tooling.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `sharepoint_source.py`
  - **Path:** `scripts/python/sharepoint_source.py`
  - **Goal:** sharepoint_source.py — SharePoint Online Source File Downloader ================================================================ Downloads 'Product Info for BRG.xlsx' directly from SharePoint Online, replacing the manual
  - **Params:** `--force`, `--clear-creds`.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `sku_ledger.py`
  - **Path:** `scripts/python/sku_ledger.py`
  - **Goal:** SKU Ledger — Persistent, deduplicated registry of SKU → Category mappings.
  - **Params:** `--db`, `path`, `sku`, `output`.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `sql_utils.py`
  - **Path:** `scripts/python/sql_utils.py`
  - **Goal:** Common SQL Server connection utilities for the Zoning & Slotting project.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

## Other Executables

- **Script Name:** `repo_health_check.py`
  - **Path:** `scripts/python/repo_health_check.py`
  - **Goal:** repo_health_check.py - Lightweight pre-flight checks for the repo.
  - **Params:** `--syntax-only`, `--skip-ruff`.
  - **Trigger:** Run after code changes or before handing the repo to another machine/operator.

## Scratch And One-Off Investigations

- **Script Name:** `analyze_20260611_forecast_churn.py`
  - **Path:** `scratch/analyze_20260611_forecast_churn.py`
  - **Goal:** No module docstring; inspect before running.
  - **Params:** none detected.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `analyze_20260611_live_inventory_impact.py`
  - **Path:** `scratch/analyze_20260611_live_inventory_impact.py`
  - **Goal:** No module docstring; inspect before running.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `analyze_20260611_load_data_forecast_gap.py`
  - **Path:** `scratch/analyze_20260611_load_data_forecast_gap.py`
  - **Goal:** No module docstring; inspect before running.
  - **Params:** none detected.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `analyze_cubiscan_forecast_gap.py`
  - **Path:** `scratch/analyze_cubiscan_forecast_gap.py`
  - **Goal:** No module docstring; inspect before running.
  - **Params:** none detected.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `audit_direct_pick_excluded_detail.py`
  - **Path:** `scratch/audit_direct_pick_excluded_detail.py`
  - **Goal:** Profile excluded DirectPick rows by location and SKU.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `audit_direct_pick_sales_pickable_scope.py`
  - **Path:** `scratch/audit_direct_pick_sales_pickable_scope.py`
  - **Goal:** Audit DirectPick history scope for forecast-training demand facts.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `audit_forecast_coverage.py`
  - **Path:** `scratch/audit_forecast_coverage.py`
  - **Goal:** Audit whether current AX forecast output covers the pipeline SKU universe.
  - **Params:** none detected.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `forecast_overlay_rule_grid.py`
  - **Path:** `scratch/forecast_overlay_rule_grid.py`
  - **Goal:** Scratch grid for transparent champion forecast overlay rules.
  - **Params:** none detected.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `inspect_forecast_db_catalog.py`
  - **Path:** `scratch/inspect_forecast_db_catalog.py`
  - **Goal:** Catalog the Azure SQL Forecast database without reading business rows.
  - **Params:** `--server`, `--database`, `--user`, `--driver`, `--auth`, `--timeout`, `--output-dir`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

- **Script Name:** `july_sale_direct_pick_lift_analysis.py`
  - **Path:** `scratch/july_sale_direct_pick_lift_analysis.py`
  - **Goal:** DirectPick-based July sale lift investigation.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `july_sale_yoy_lift_analysis.py`
  - **Path:** `scratch/july_sale_yoy_lift_analysis.py`
  - **Goal:** Investigate prior July-sale lift as an anchor for the 2026 forward shadow.
  - **Params:** none detected.
  - **Trigger:** Use when this specific helper matches the task; inspect docstring/constants first.

- **Script Name:** `profile_forecast_db_dates.py`
  - **Path:** `scratch/profile_forecast_db_dates.py`
  - **Goal:** Profile date ranges in the Azure SQL Forecast database.
  - **Params:** `--server`, `--database`, `--user`, `--driver`, `--auth`, `--timeout`, `--output-dir`.
  - **Trigger:** Run for forecast extract, model, backtest, candidate, or scorecard work.

