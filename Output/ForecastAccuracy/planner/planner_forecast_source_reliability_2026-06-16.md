# Planner Forecast Source Reliability - 2026-06-16

Important interpretation: completed Planner files appear to contain rows that were backfilled or linked to actual demand. Very low WAPE and high exact-match rates are suspicious, not proof of excellent forecasting.

## Key Scores
- 2024 all rows with actual demand present / ops_imf_plan_forecasted_units: days 364, WAPE 8.7%, bias 0.4%, exact-or-placeholder days 0.8%.
- 2024 all rows with actual demand present / forecasted_demand_units: days 364, WAPE 3.2%, bias 1.0%, exact-or-placeholder days 2.5%.
- 2024 all rows with actual demand present / kpi_forecasted_demand_units: days 364, WAPE 0.0%, bias -0.0%, exact-or-placeholder days 1.1%.
- 2024 all rows with actual demand present / units_shipped_goal: days 364, WAPE 36.9%, bias -3.2%, exact-or-placeholder days 0.0%.
- 2024 all rows with actual demand present / shipped_units_powerbi: days 364, WAPE 37.7%, bias 0.6%, exact-or-placeholder days 0.0%.
- 2025 all rows with actual demand present / ops_imf_plan_forecasted_units: days 77, WAPE 1.5%, bias -1.5%, exact-or-placeholder days 13.0%.
- 2025 all rows with actual demand present / forecasted_demand_units: days 340, WAPE 1.8%, bias -1.1%, exact-or-placeholder days 1.2%.
- 2025 all rows with actual demand present / kpi_forecasted_demand_units: days 364, WAPE 0.1%, bias -0.1%, exact-or-placeholder days 1.6%.
- 2025 all rows with actual demand present / units_shipped_goal: days 372, WAPE 38.7%, bias -3.6%, exact-or-placeholder days 0.0%.
- 2025 all rows with actual demand present / shipped_units_powerbi: days 372, WAPE 38.7%, bias 0.6%, exact-or-placeholder days 0.0%.
- 2026 all rows with actual demand present / ops_imf_plan_forecasted_units: days 364, WAPE 3.1%, bias -0.2%, exact-or-placeholder days 63.2%.
- 2026 all rows with actual demand present / kpi_forecasted_demand_units: days 364, WAPE 0.3%, bias -0.2%, exact-or-placeholder days 62.4%.
- 2026 all rows with actual demand present / units_shipped_goal: days 364, WAPE 51.4%, bias -36.5%, exact-or-placeholder days 0.0%.
- 2026 all rows with actual demand present / shipped_units_powerbi: days 364, WAPE 51.5%, bias -34.4%, exact-or-placeholder days 0.0%.
- 2026 observed through 2026-06-14 / ops_imf_plan_forecasted_units: days 134, WAPE 12.4%, bias -0.6%, exact-or-placeholder days 0.0%.
- 2026 recent underforecast streak 2026-06-09..2026-06-14 / ops_imf_plan_forecasted_units: days 6, WAPE 23.7%, bias -23.7%, exact-or-placeholder days 0.0%.
- 2026 sale 14-day horizon 2026-06-16..2026-06-29 / ops_imf_plan_forecasted_units: days 14, WAPE 0.0%, bias 0.0%, exact-or-placeholder days 100.0%.
- 2024 sale analog 2024-06-18..2024-07-06 / ops_imf_plan_forecasted_units: days 19, WAPE 14.8%, bias 12.9%, exact-or-placeholder days 0.0%.

## Current Read
- Use the live 2026 OPS/IMF row as a total-unit planning signal, but preserve snapshots because the workbook can change and future rows may later become actuals.
- Do not trust the KPI tab or plain Forecasted Demand row as historical forecast accuracy evidence; they often match actual demand too closely after the fact.
- The current sale-window OPS/IMF total is a better volume anchor than our uncapped SKU model, but it is not SKU-level allocation.