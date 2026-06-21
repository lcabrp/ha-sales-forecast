# Agent Instructions

This is the Hanna Andersson KYDC sales forecast repo. It was extracted from `ha-zoning-slotting` to keep forecast research and replacement forecast tooling independent from ingestion, layout generation, and monitoring.

`ha-zoning-slotting` is now a legacy reference source. Continue active development in this repo and the other split repos.

## Start Here

- Read `README.md` for repo scope.
- Read `Docs/operations/forecast_accuracy/FORECAST_REPLACEMENT_RESET_2026-06-15.md` before changing replacement forecast behavior.
- Read `Docs/operations/forecast_accuracy/FORECAST_PORTABLE_ARTIFACTS_2026-06-17.md` before moving or regenerating forecast artifacts.
- Prefer existing helpers: `scripts/python/output_paths.py` for paths and `scripts/python/sql_utils.py` for AX SQL connections.

## Coding Discipline

- State assumptions when business meaning is unclear. Forecast, SlotTier, velocity, direct pick, reservation, inbound, and replenishment terms carry operational meaning.
- Make the smallest change that solves the request. Avoid broad rewrites while this repo is being extracted.
- Keep one-off investigations in `scratch/` unless they become repeatable forecast reports or datasets.
- When editing AX SQL, always join AX tables on `DATAAREAID` and `[PARTITION]`; warehouse `4010`, company `ha`, and partition `5637144576` are the normal defaults.
- For production analysis queries, use read-only patterns already present in the repo, usually `READ UNCOMMITTED` or `WITH (NOLOCK)`.
- Do not commit large workbooks, local SQLite databases, or generated model artifacts unless a reset/portable-artifact contract explicitly says they are promoted.

## Verification

- For code edits, run a targeted `ruff check` and `py_compile` on changed files when practical.
- For forecast artifact changes, report the query/data window, row counts, and whether results came from live AX, corporate forecast DB snapshots, planner workbooks, Product Info for BRG, cached Parquet, or local SQLite.
