"""One-command pipeline runner orchestrator for warehouse zoning & slotting forecasts.

It automates the following sequence:
1. Performs backtesting to produce candidate scores.
2. Runs the self-calibrating recency brake to pick the optimal out-of-sample cap.
3. Automatically generates the final candidate workbook and AXForwardDemand CSV using the chosen cap.
4. Executes the expected operational cost scorecard and slotting stability scorecards.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Orchestrate the forecast training and candidate pipeline.")
    parser.add_argument(
        "--source-file",
        type=Path,
        required=True,
        help="Path to the corporate Product Info for BRG workbook.",
    )
    parser.add_argument(
        "--forecast-start-date",
        required=True,
        help="Forecast start date for candidate generation (YYYY-MM-DD).",
    )
    parser.add_argument("--max-windows", type=int, default=26, help="Maximum windows to evaluate in backtest.")
    parser.add_argument("--lookback", type=int, default=2, help="Prior windows lookback for recency brake.")
    parser.add_argument("--threads", type=int, default=8, help="Number of CPU threads to use.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "Output" / "ForecastAccuracy" / "pipeline_runs",
        help="Target output directory for pipeline run artifacts.",
    )
    parser.add_argument("--c-over", type=float, default=1.0, help="Unit cost of over-forecasting.")
    parser.add_argument("--c-under", type=float, default=3.0, help="Unit cost of under-forecasting.")
    parser.add_argument("--c-zero", type=float, default=6.0, help="Unit cost of zero-forecast missed units.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("======================================================================")
    print("STEP 1: Running Cold-Start Quantile Backtests...")
    print("======================================================================")
    backtest_dir = args.output_dir / "backtest"
    cmd_backtest = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "python" / "forecast_replacement_ml_cold_start.py"),
        "--max-windows",
        str(args.max_windows),
        "--threads",
        str(args.threads),
        "--output-dir",
        str(backtest_dir),
        "--hybrid-recent-volume-caps", "0.85", "1.00", "1.10", "1.25"
    ]
    print(f"Running command: {' '.join(cmd_backtest)}")
    subprocess.run(cmd_backtest, check=True)

    print("\n======================================================================")
    print("STEP 2: Determining Optimal Volume Cap via Recency Brake...")
    print("======================================================================")
    brake_dir = args.output_dir / "recency_brake"
    score_file = backtest_dir / "replacement_ml_backtest_window_scores.csv"
    
    # Check that scores file exists
    if not score_file.exists():
        raise FileNotFoundError(f"Backtest scores not found at {score_file}")
        
    cmd_brake = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "python" / "forecast_replacement_recency_brake.py"),
        "--score-file",
        str(score_file),
        "--reference",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1",
        "--cap-variant",
        "1.25:hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x1p25",
        "--cap-variant",
        "1.10:hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x1p1",
        "--cap-variant",
        "1.00:hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x1p0",
        "--cap-variant",
        "0.85:hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x0p85",
        "--floor-candidate",
        "recent_no_ml_no_promo_floor",
        "--lookback",
        str(args.lookback),
        "--output-dir",
        str(brake_dir),
    ]
    print(f"Running command: {' '.join(cmd_brake)}")
    subprocess.run(cmd_brake, check=True)

    # Load the choices CSV and read the last selected cap
    choices_file = brake_dir / "recency_brake_window_choices.csv"
    if not choices_file.exists():
        raise FileNotFoundError(f"Brake choices not found at {choices_file}")
    
    choices_df = pd.read_csv(choices_file)
    last_row = choices_df.iloc[-1]
    chosen_cap = float(last_row["RequestedCap"])
    chosen_candidate = str(last_row["ChosenCandidate"])
    print(f"\n>>> Recency Brake selected Cap variant: {chosen_cap} ({chosen_candidate})")

    print("\n======================================================================")
    print("STEP 3: Generating AX-Ready Candidate Workbook & Forward Demand CSV...")
    print("======================================================================")
    cmd_candidate = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "python" / "forecast_replacement_hybrid_cold_start_candidate.py"),
        "--source-file",
        str(args.source_file),
        "--forecast-start-date",
        str(args.forecast_start_date),
        "--recent-volume-cap",
        str(chosen_cap),
        "--threads",
        str(args.threads),
    ]
    print(f"Running command: {' '.join(cmd_candidate)}")
    subprocess.run(cmd_candidate, check=True)

    print("\n======================================================================")
    print("STEP 4: Scoring Operational Costs with Expected-Cost Scorecard...")
    print("======================================================================")
    # Combine backtest, brake, and baseline scores for the scorecard
    combined_scores_path = backtest_dir / "combined_scores.csv"
    backtest_scores = pd.read_csv(score_file)
    brake_scores = pd.read_csv(brake_dir / "recency_brake_window_scores.csv")
    
    dfs = [backtest_scores, brake_scores]
    baseline_path = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "replacement_backtests" / "replacement_backtest_window_scores.csv"
    if baseline_path.exists():
        print(f"Loading baseline scores from {baseline_path}...")
        dfs.append(pd.read_csv(baseline_path))
    else:
        print(f"WARNING: Baseline scores not found at {baseline_path}. Baseline candidates will be missing from cost scorecard.")
        
    # Merge and write
    combined_scores = pd.concat(dfs, ignore_index=True)
    combined_scores = combined_scores.drop_duplicates(["ForecastStartDate", "Candidate"], keep="last")
    combined_scores.to_csv(combined_scores_path, index=False)
    
    cmd_scorecard = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "python" / "forecast_replacement_cost_scorecard.py"),
        "--score-file",
        str(combined_scores_path),
        "--focus",
        "self_calibrating_recency_brake",
        "--focus",
        "hybrid_ml_cold_start_raw_min_20p0_units_recent_w0p1_cap_recent_x0p85",
        "--focus",
        "recent_no_ml_no_promo_floor",
        "--focus",
        "corporate",
        "--c-over",
        str(args.c_over),
        "--c-under",
        str(args.c_under),
        "--c-zero",
        str(args.c_zero),
        "--output-dir",
        str(args.output_dir / "cost_scorecard"),
    ]
    print(f"Running command: {' '.join(cmd_scorecard)}")
    subprocess.run(cmd_scorecard, check=True)

    print("\n======================================================================")
    print("PIPELINE RUN COMPLETE!")
    print("======================================================================")
    print(f"Check final outputs and summaries in: {args.output_dir}")


if __name__ == "__main__":
    main()
