"""Offline audit of the 2026-07-10 cold-start unit collapse. No AX, no ML training."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "Output/ForecastAccuracy/pipeline_runs/2026-07-10_corporate_2026-07-06/backtest"
GOOD = (
    ROOT
    / "Output/ForecastAccuracy/replacement_ml_backtests"
    / "hgb_absolute_log_hybrid_26_windows/replacement_ml_backtest_candidate_summary.csv"
)
ARTIFACT_CHECKS = [
    ROOT / "Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet",
    ROOT / "Output/ForecastAccuracy/model/model_sku_day_panel_parts/manifest.json",
    ROOT / "Output/ForecastAccuracy/corporate_forecast/snapshots/20260617_173252",
    ROOT / "Output/ForecastAccuracy/direct_pick_history/parquet",
    ROOT / "Output/ForecastAccuracy/planner/planner_daily_totals_2026.csv",
    RUN / "replacement_ml_backtest_candidate_summary.csv",
    ROOT.parent / "ha-ingestion-pipeline/Source/Product Info for BRG_2026-07-06.xlsx",
    ROOT.parent
    / "ha-kydc-monitoring/Output/Monitoring/forecast_snapshots/confirmed_raw"
    / "FwdDemandCSV_2026-07-11_b0518891ae8c.csv",
    ROOT.parent / "ha-kydc-monitoring/Docs/operations/TST_MODEL_EVALUATION_DATA_CONTRACT.md",
]


def main() -> None:
    summary = pd.read_csv(RUN / "replacement_ml_backtest_candidate_summary.csv")
    windows = pd.read_csv(RUN / "replacement_ml_backtest_window_scores.csv")
    meta = json.loads((RUN / "replacement_ml_backtest_metadata.json").read_text(encoding="utf-8"))

    focus = [
        "ml_cold_start_raw_future_guardrail_min_20p0_units",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x0p85",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_blended",
    ]
    print("=== July 10 cold-start summary (selected candidates) ===")
    cols = [
        "Candidate",
        "ForecastUnits",
        "SoldUnits",
        "BiasPctForecastMinusActual",
        "WAPE",
        "AvgSoldUnitForecastCoveragePct",
        "AvgForecastedSKUs",
    ]
    print(summary.loc[summary["Candidate"].isin(focus), cols].to_string(index=False))

    raw = summary.loc[
        summary["Candidate"].eq("hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1")
    ].iloc[0]
    caps = summary.loc[
        summary["Candidate"].str.startswith(
            "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x"
        )
        & ~summary["Candidate"].str.endswith("_blended")
    ]
    print("\n=== Cap variant identity check (raw hybrid family) ===")
    print(f"unique ForecastUnits among caps: {caps['ForecastUnits'].nunique()}")
    print(f"all equal to uncapped? {(caps['ForecastUnits'] == raw['ForecastUnits']).all()}")

    pure_ml = windows.loc[
        windows["Candidate"].eq("ml_cold_start_raw_future_guardrail_min_20p0_units")
    ]
    hybrid = windows.loc[
        windows["Candidate"].eq("hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1")
    ]
    print("\n=== Per-window volume (first/last/median) ===")
    for label, frame in [("pure_ml", pure_ml), ("hybrid_w0p1", hybrid)]:
        fu = frame["ForecastUnits"]
        print(
            f"{label}: windows={len(frame)} "
            f"sum={fu.sum():,.0f} median={fu.median():,.0f} "
            f"min={fu.min():,.0f} max={fu.max():,.0f} "
            f"avg_skus={frame['ForecastedSKUs'].mean():.1f}"
        )

    # Implied recent contribution: hybrid - ml (approx; ML SKUs may overlap)
    merged = pure_ml[["ForecastStartDate", "ForecastUnits", "SoldUnits"]].merge(
        hybrid[["ForecastStartDate", "ForecastUnits"]],
        on="ForecastStartDate",
        suffixes=("_ml", "_hybrid"),
    )
    merged["approx_fallback_units"] = merged["ForecastUnits_hybrid"] - merged["ForecastUnits_ml"]
    merged["ml_share_of_hybrid"] = merged["ForecastUnits_ml"] / merged["ForecastUnits_hybrid"].clip(
        lower=1e-9
    )
    merged["hybrid_vs_sold"] = merged["ForecastUnits_hybrid"] / merged["SoldUnits"].clip(lower=1e-9)
    print("\n=== Approx ML share of hybrid volume ===")
    print(
        f"median ml_share={merged['ml_share_of_hybrid'].median():.3f} "
        f"median hybrid/sold={merged['hybrid_vs_sold'].median():.3f} "
        f"median fallback_units={merged['approx_fallback_units'].median():,.0f}"
    )

    if GOOD.exists():
        good = pd.read_csv(GOOD)
        print("\n=== Contrast: June absolute-log hybrid (known healthy volume) ===")
        gfocus = good.loc[
            good["Candidate"].isin(
                [
                    "ml_hgb_absolute_log_raw_future_guardrail_min_20p0_units",
                    "hybrid_ml_hgb_absolute_log_raw_min_20p0_units_recent_w0p25",
                ]
            ),
            cols,
        ]
        print(gfocus.to_string(index=False))

    # Recency-brake failure reproduction without writing outputs
    required = {
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x0p85",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x1p0",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x1p1",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x1p25",
        "recent_no_ml_no_promo_floor",
    }
    present = set(windows["Candidate"].unique())
    missing = sorted(required - present)
    print("\n=== Recency-brake name lookup ===")
    print(f"missing from cold-start window scores: {missing or 'none'}")
    print(f"metadata quantile={meta.get('quantile')} censoring={meta.get('censoring')}")
    print(f"panel path recorded: {meta.get('panel')}")

    print("\n=== Offline artifact presence ===")
    for path in ARTIFACT_CHECKS:
        status = "OK" if path.exists() else "MISSING"
        extra = ""
        if path.exists() and path.is_file():
            extra = f" ({path.stat().st_size / 1e6:.1f} MB)"
        print(f"{status:7} {path}{extra}")


if __name__ == "__main__":
    main()
