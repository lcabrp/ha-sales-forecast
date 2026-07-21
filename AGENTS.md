# Agent Instructions

This is the Hanna Andersson KYDC sales forecast repo. It was extracted from `ha-zoning-slotting` to keep forecast research and replacement forecast tooling independent from ingestion, layout generation, and monitoring.

`ha-zoning-slotting` is now a legacy reference source. Continue active development in this repo and the other split repos.

## Start Here

- Read `README.md` for repo scope.
- Read `Docs/operations/forecast_accuracy/FORECAST_CURRENT_STATE.md` before changing forecast behavior.
- Read `Docs/operations/forecast_accuracy/FORECAST_CLOSEOUT_2026-07-07_TO_2026-07-20.md` before comparing or promoting a candidate.
- Read `Docs/operations/forecast_accuracy/FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md` before moving or regenerating forecast artifacts.
- Prefer existing helpers: `scripts/python/output_paths.py` for paths and `scripts/python/sql_utils.py` for AX SQL connections.

## Coding Discipline

- State assumptions when business meaning is unclear. Forecast, SlotTier, velocity, direct pick, reservation, inbound, and replenishment terms carry operational meaning.
- Make the smallest change that solves the request. Avoid broad rewrites while this repo is being extracted.
- Keep one-off investigations in `scratch/` unless they become repeatable forecast reports or datasets.
- Do not describe a smoke test, partial horizon, leaky backtest, or post-close rebuild as the current champion. `FORECAST_CURRENT_STATE.md` is the decision authority.
- When editing AX SQL, always join AX tables on `DATAAREAID` and `[PARTITION]`; warehouse `4010`, company `ha`, and partition `5637144576` are the normal defaults.
- For production analysis queries, use read-only patterns already present in the repo, usually `READ UNCOMMITTED` or `WITH (NOLOCK)`.
- **Online-repo / multi-PC artifact rule (2026-07-12):** when updating the remote repo so work continues on another PC, commit **all relevant forecast evidence and tooling** unless a file is over the practical **~90 MB** ceiling (GitHub hard limit 100 MB). Prefer **several smaller Parquet/CSV/JSON/SQLite files** over one large blob. Split panels beat monoliths. Do not leave long-run score tables, challenger forecasts, or compact ledgers only on one machine. Still never commit secrets (`.env`, credentials). Known exceptions that stay local are listed in `Docs/operations/forecast_accuracy/FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md` (e.g. monolithic `model_sku_day_panel.parquet`, `promotions.db` when oversized/regenerable).
- Ignore regenerable noise (`*.log`, `__pycache__`, `.venv`) even when small.

## Verification

- For code edits, run a targeted `ruff check` and `py_compile` on changed files when practical.
- For forecast artifact changes, report the query/data window, row counts, and whether results came from live AX, corporate forecast DB snapshots, planner workbooks, Product Info for BRG, cached Parquet, or local SQLite.
