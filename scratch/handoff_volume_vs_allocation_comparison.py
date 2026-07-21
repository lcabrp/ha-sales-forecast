"""Build handoff volume-vs-allocation comparison without ML training.

Candidates:
  corporate_raw                 - exact snapshot corporate 14-day SKU totals
  corporate_total_recent_shape  - recent_no_ml_no_promo_floor shape scaled to
                                  corporate category totals via blend_with_corporate
  recent_no_ml_no_promo_floor   - independent recent total+shape (reference)
  independent_total_model_shape - reused June absolute-log ML scores (diagnostic)

No AX. No model training. Threads unused except for pandas.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_replacement_backtest import (  # noqa: E402
    ACTUALS_PATH,
    FORECAST_DAY_PATH,
    FORECAST_SNAPSHOT_PATH,
    PDL_SKU_FEATURES_PATH,
    SNAPSHOT_SUMMARY_PATH,
    actual_window,
    choose_windows,
    corporate_forecast,
    load_actuals,
    load_promo_for_window,
    no_ml_forecast,
    normalize_sku_series,
    score_forecast,
    snapshot_universe,
    summarize_by_candidate,
)
from forecast_replacement_hybrid_candidate import integerize_by_forecast_day  # noqa: E402
from forecast_replacement_contract import DEFAULT_LOOKBACK_DAYS  # noqa: E402

OUT_DIR = ROOT / "Output" / "ForecastAccuracy" / "handoff_eval" / "volume_vs_allocation_2026-07-11"
COMBINED_SUMMARY = (
    ROOT
    / "Output"
    / "ForecastAccuracy"
    / "replacement_ml_backtests"
    / "combined_replacement_candidate_summary.csv"
)
INDEPENDENT_MODEL = "ml_hgb_absolute_log_raw_future_guardrail_min_20p0_units"


def allocate_total_by_shape(shape: pd.DataFrame, total_units: float) -> pd.DataFrame:
    """Hold total fixed; redistribute by the SKU shape's relative shares."""
    frame = shape.copy()
    frame["ForecastUnits"] = (
        pd.to_numeric(frame["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
    )
    frame = frame.loc[frame["ForecastUnits"].gt(0), ["SKU", "ForecastUnits"]]
    shape_total = float(frame["ForecastUnits"].sum())
    total_units = float(total_units)
    if frame.empty or shape_total <= 0 or total_units <= 0:
        return frame.iloc[0:0].copy()
    frame["ForecastUnitsRaw"] = frame["ForecastUnits"] / shape_total * total_units
    frame["ForecastDay"] = 1
    frame = integerize_by_forecast_day(frame)
    return frame.loc[frame["ForecastUnits"].gt(0), ["SKU", "ForecastUnits"]].copy()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    windows = choose_windows(SNAPSHOT_SUMMARY_PATH, "2025-01-01", None, 26)
    if windows.empty:
        raise RuntimeError("No complete historical forecast windows found.")

    print(f"Windows: {len(windows)} "
          f"{windows['ForecastStartDate'].min().date()} .. "
          f"{windows['ForecastStartDate'].max().date()}", flush=True)

    actuals = load_actuals(ACTUALS_PATH)
    forecast_day = pd.read_parquet(
        FORECAST_DAY_PATH,
        columns=["SnapshotId", "SKU", "ForecastDate", "ForecastQty"],
    )
    forecast_day["ForecastQty"] = pd.to_numeric(forecast_day["ForecastQty"], errors="coerce").fillna(0)
    forecast_day["SKU"] = normalize_sku_series(forecast_day["SKU"])
    snapshot_sku = pd.read_parquet(FORECAST_SNAPSHOT_PATH)

    promo = load_promo_for_window(
        PDL_SKU_FEATURES_PATH,
        pd.Timestamp(windows["ForecastStartDate"].min()).normalize(),
        pd.Timestamp(windows["ForecastEndDate"].max()).normalize(),
    )

    score_rows: list[dict] = []
    for _, snapshot in windows.iterrows():
        start = pd.Timestamp(snapshot["ForecastStartDate"]).normalize()
        source_universe = snapshot_universe(snapshot_sku, str(snapshot["SnapshotId"]))
        actual = actual_window(actuals, start)

        corp = corporate_forecast(forecast_day, str(snapshot["SnapshotId"]))
        recent, _meta = no_ml_forecast(
            actuals=actuals,
            promo=promo,
            source_universe=source_universe,
            start=start,
            lookback_days=DEFAULT_LOOKBACK_DAYS,
            include_seasonal=False,
            include_promo_floor=False,
            seasonal_years=3,
            seasonal_window_days=7,
            seasonal_recent_weight=0.65,
        )
        corp_total = float(pd.to_numeric(corp["ForecastUnits"], errors="coerce").fillna(0).sum())
        corp_total_recent = allocate_total_by_shape(recent, corp_total)

        for name, forecast in [
            ("corporate_raw", corp),
            ("recent_no_ml_no_promo_floor", recent),
            ("corporate_total_recent_shape", corp_total_recent),
        ]:
            score_rows.append(score_forecast(forecast, actual, name, snapshot))
        print(
            f"  scored {start.date()} "
            f"corp={corp_total:,.0f} "
            f"recent={recent['ForecastUnits'].sum():,.0f} "
            f"corp_total_recent={corp_total_recent['ForecastUnits'].sum():,.0f}",
            flush=True,
        )

    scores = pd.DataFrame(score_rows)
    summary = summarize_by_candidate(scores)
    order = [
        "corporate_raw",
        "corporate_total_recent_shape",
        "recent_no_ml_no_promo_floor",
    ]
    summary["_ord"] = summary["Candidate"].map({c: i for i, c in enumerate(order)})
    summary = summary.sort_values(["_ord", "WAPE"], kind="mergesort").drop(columns="_ord")

    scores.to_csv(OUT_DIR / "handoff_candidate_window_scores.csv", index=False)
    summary.to_csv(OUT_DIR / "handoff_candidate_summary.csv", index=False)

    # Separate diagnostic: June independent ML was scored on a different window
    # set (7.33M sold). Do not merge into the same decision table.
    diagnostic = None
    if COMBINED_SUMMARY.exists():
        prior = pd.read_csv(COMBINED_SUMMARY)
        diagnostic = prior.loc[prior["Candidate"].eq(INDEPENDENT_MODEL)].copy()
        diagnostic["HandoffLabel"] = "independent_total_model_shape"
        diagnostic["SameWindowContractAsAbove"] = False
        diagnostic.to_csv(OUT_DIR / "handoff_independent_model_diagnostic.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "windows": int(len(windows)),
        "window_start_min": str(windows["ForecastStartDate"].min().date()),
        "window_start_max": str(windows["ForecastStartDate"].max().date()),
        "lookback_days": DEFAULT_LOOKBACK_DAYS,
        "candidates": order,
        "notes": [
            "Same 26-window contract for corporate_raw, corporate_total_recent_shape, recent_no_ml_no_promo_floor.",
            "corporate_total_recent_shape = corporate 14-day total allocated by recent_no_ml_no_promo_floor SKU shares (strict total match).",
            "independent_total_model_shape is reported separately from June combined scores (different sold-units window); not decision-comparable here.",
            "corporate_total_model_shape not yet scored; needs June ML SKU shapes + allocate_total_by_shape.",
            "July 10 quantile cold-start shapes intentionally excluded (unit collapse audit).",
        ],
        "inputs": {
            "forecast_day": str(FORECAST_DAY_PATH),
            "actuals": str(ACTUALS_PATH),
            "combined_summary": str(COMBINED_SUMMARY),
        },
    }
    (OUT_DIR / "handoff_candidate_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )
    print("\nSame-contract handoff summary:")
    print(summary.to_string(index=False))
    if diagnostic is not None and not diagnostic.empty:
        print("\nDiagnostic only (different window set — not comparable):")
        print(diagnostic.to_string(index=False))
    print(f"\nWrote: {OUT_DIR}")


if __name__ == "__main__":
    main()
