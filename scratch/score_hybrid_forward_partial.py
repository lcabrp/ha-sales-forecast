"""Score independent_hybrid_absolute_log partial actuals Jul 7-9 vs challengers."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))

from forecast_replacement_backtest import ACTUALS_PATH, load_actuals, normalize_sku_series, score_forecast  # noqa: E402

HYBRID_DAILY = ROOT / "Output/ForecastAccuracy/handoff_eval/independent_hybrid_absolute_log_2026-07-07/contract/daily_forecast.parquet"
PARTIAL_SCORES = ROOT / "Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_challenger/forward_partial_scores_2026-07-07_to_09.csv"
OUT_CSV = ROOT / "Output/ForecastAccuracy/handoff_eval/forward_2026-07-07_challenger/forward_partial_scores_with_hybrid.csv"
CANDIDATE = "independent_hybrid_absolute_log"
START = pd.Timestamp("2026-07-07")
THROUGH = pd.Timestamp("2026-07-09")


def score_partial_daily(daily: pd.DataFrame, actuals: pd.DataFrame, candidate: str) -> dict:
    frame = daily.loc[daily["ForecastDate"].between(START, THROUGH)].copy()
    actual = (
        actuals.loc[actuals["ActualDate"].between(START, THROUGH)]
        .groupby("SKU", as_index=False)
        .agg(SoldUnits=("SoldUnits", "sum"))
    )
    forecast = frame.groupby("SKU", as_index=False).agg(ForecastUnits=("ForecastUnits", "sum"))
    snapshot = {
        "SnapshotId": f"partial_{START.date()}_{THROUGH.date()}",
        "SourceFile": "handoff_challenger",
        "ForecastStartDate": START,
        "ForecastEndDate": THROUGH,
    }
    row = score_forecast(forecast, actual, candidate, snapshot)
    row["PartialThrough"] = THROUGH.date().isoformat()
    row["PartialDays"] = int((THROUGH - START).days + 1)
    return row


def main() -> int:
    daily = pd.read_parquet(HYBRID_DAILY)
    daily["SKU"] = normalize_sku_series(daily["SKU"])
    daily["ForecastDate"] = pd.to_datetime(daily["ForecastDate"]).dt.normalize()
    daily["ForecastUnits"] = pd.to_numeric(daily["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)

    hybrid_14d = float(daily["ForecastUnits"].sum())
    # Prefer FD1-FD14 if present as ForecastDay 1..14
    if "ForecastDay" in daily.columns:
        hybrid_14d = float(daily.loc[daily["ForecastDay"].between(1, 14), "ForecastUnits"].sum())

    actuals = load_actuals(ACTUALS_PATH)
    hybrid_row = score_partial_daily(daily, actuals, CANDIDATE)

    existing = pd.read_csv(PARTIAL_SCORES)
    existing = existing.loc[~existing["Candidate"].eq(CANDIDATE)].copy()
    out = pd.concat([existing, pd.DataFrame([hybrid_row])], ignore_index=True)
    # Stable candidate order
    order = [
        "corporate_raw",
        "corporate_total_recent_shape",
        "independent_recent_shape",
        CANDIDATE,
    ]
    out["_ord"] = out["Candidate"].map({c: i for i, c in enumerate(order)}).fillna(99)
    out = out.sort_values("_ord").drop(columns="_ord")
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)

    cols = [
        "Candidate",
        "ForecastUnits",
        "SoldUnits",
        "WAPE",
        "BiasPctForecastMinusActual",
        "SoldUnitForecastCoveragePct",
        "ZeroForecastSoldUnitPct",
        "PartialDays",
    ]
    print(out[cols].to_string(index=False))
    print(f"\nhybrid_14d_total={hybrid_14d:.0f}")
    print(f"wrote {OUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

