"""Compare fixed and prior-window forecast replacement policies.

This script consumes candidate window-score CSVs and evaluates simple selection
policies without rerunning the underlying forecasts.  It is meant to catch
post-hoc overfitting: if a candidate only looks good because we selected it
after seeing every holdout window, a prior-window selector should expose that.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_OUTPUT_DIR = FORECAST_ACCURACY_ROOT / "replacement_ml_backtests"
DEFAULT_SCORE_FILES = [
    FORECAST_ACCURACY_ROOT / "replacement_backtests" / "replacement_backtest_window_scores.csv",
    FORECAST_ACCURACY_ROOT
    / "replacement_ml_backtests"
    / "hgb_absolute_log_hybrid_weight_grid_26_windows"
    / "replacement_ml_backtest_window_scores.csv",
    FORECAST_ACCURACY_ROOT
    / "replacement_ml_backtests"
    / "hgb_absolute_log_26_windows"
    / "replacement_ml_backtest_window_scores.csv",
]
DEFAULT_CANDIDATES = [
    "corporate",
    "recent_no_ml_no_promo_floor",
    "hybrid_ml_hgb_absolute_log_raw_min_20p0_units_recent_w0p05",
    "hybrid_ml_hgb_absolute_log_raw_min_20p0_units_recent_w0p1",
    "hybrid_ml_hgb_absolute_log_raw_min_20p0_units_recent_w0p15",
]
DEFAULT_POLICY_CANDIDATE = "hybrid_ml_hgb_absolute_log_raw_min_20p0_units_recent_w0p1"
REQUIRED_COLUMNS = [
    "ForecastStartDate",
    "Candidate",
    "ForecastUnits",
    "SoldUnits",
    "AbsErrorUnits",
    "ZeroForecastSoldUnits",
    "SoldUnitForecastCoveragePct",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest replacement candidate selection policies.")
    parser.add_argument("--score-file", action="append", type=Path, dest="score_files")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--candidate", action="append", dest="candidates")
    parser.add_argument("--default-candidate", default=DEFAULT_POLICY_CANDIDATE)
    parser.add_argument("--lookback-windows", nargs="+", type=int, default=[4, 8, 12])
    parser.add_argument("--expanding-warmup-windows", type=int, default=4)
    return parser.parse_args()


def load_scores(paths: list[Path], candidates: list[str]) -> pd.DataFrame:
    frames = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Score file not found: {path}")
        frame = pd.read_csv(path)
        missing = [col for col in REQUIRED_COLUMNS if col not in frame.columns]
        if missing:
            raise ValueError(f"{path} is missing required columns: {missing}")
        frames.append(frame)
    scores = pd.concat(frames, ignore_index=True)
    scores = scores.drop_duplicates(["ForecastStartDate", "Candidate"], keep="last")
    scores = scores.loc[scores["Candidate"].isin(candidates)].copy()
    scores["ForecastStartDate"] = pd.to_datetime(scores["ForecastStartDate"]).dt.normalize()
    missing_candidates = sorted(set(candidates) - set(scores["Candidate"].unique()))
    if missing_candidates:
        raise ValueError(f"No rows found for candidates: {missing_candidates}")
    return scores.sort_values(["ForecastStartDate", "Candidate"], kind="mergesort")


def summarize(rows: list[dict[str, Any]], policy: str) -> tuple[dict[str, Any], pd.DataFrame]:
    selected = pd.DataFrame(rows)
    sold_units = float(selected["SoldUnits"].sum())
    forecast_units = float(selected["ForecastUnits"].sum())
    abs_error = float(selected["AbsErrorUnits"].sum())
    summary = {
        "Policy": policy,
        "Windows": int(selected["ForecastStartDate"].nunique()),
        "ForecastUnits": forecast_units,
        "SoldUnits": sold_units,
        "AbsErrorUnits": abs_error,
        "WAPE": abs_error / sold_units if sold_units else pd.NA,
        "BiasPct": (forecast_units - sold_units) / sold_units if sold_units else pd.NA,
        "ZeroForecastSoldUnitPct": float(selected["ZeroForecastSoldUnits"].sum()) / sold_units
        if sold_units
        else pd.NA,
        "AvgCoverage": float(selected["SoldUnitForecastCoveragePct"].mean()),
    }
    return summary, selected.assign(Policy=policy)


def best_prior_candidate(scores: pd.DataFrame, prior_dates: list[pd.Timestamp]) -> str:
    prior = scores.loc[scores["ForecastStartDate"].isin(prior_dates)].copy()
    grouped = prior.groupby("Candidate").agg(
        AbsErrorUnits=("AbsErrorUnits", "sum"),
        SoldUnits=("SoldUnits", "sum"),
    )
    wape = grouped["AbsErrorUnits"] / grouped["SoldUnits"]
    return str(wape.sort_values().index[0])


def fixed_policy(scores: pd.DataFrame, starts: list[pd.Timestamp], candidate: str) -> tuple[dict[str, Any], pd.DataFrame]:
    rows = [
        scores.loc[
            scores["ForecastStartDate"].eq(start) & scores["Candidate"].eq(candidate)
        ].iloc[0].to_dict()
        for start in starts
    ]
    return summarize(rows, f"fixed__{candidate}")


def rolling_policy(
    scores: pd.DataFrame,
    starts: list[pd.Timestamp],
    lookback_windows: int,
    default_candidate: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    rows = []
    for idx, start in enumerate(starts):
        if idx < lookback_windows:
            candidate = default_candidate
        else:
            candidate = best_prior_candidate(scores, starts[idx - lookback_windows : idx])
        row = scores.loc[
            scores["ForecastStartDate"].eq(start) & scores["Candidate"].eq(candidate)
        ].iloc[0].to_dict()
        row["ChosenCandidate"] = candidate
        rows.append(row)
    return summarize(rows, f"rolling_{lookback_windows}_prior_wape_default__{default_candidate}")


def expanding_policy(
    scores: pd.DataFrame,
    starts: list[pd.Timestamp],
    warmup_windows: int,
    default_candidate: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    rows = []
    for idx, start in enumerate(starts):
        if idx < warmup_windows:
            candidate = default_candidate
        else:
            candidate = best_prior_candidate(scores, starts[:idx])
        row = scores.loc[
            scores["ForecastStartDate"].eq(start) & scores["Candidate"].eq(candidate)
        ].iloc[0].to_dict()
        row["ChosenCandidate"] = candidate
        rows.append(row)
    return summarize(rows, f"expanding_prior_wape_default__{default_candidate}_after_{warmup_windows}")


def main() -> None:
    args = parse_args()
    score_files = args.score_files or DEFAULT_SCORE_FILES
    candidates = args.candidates or DEFAULT_CANDIDATES
    scores = load_scores(score_files, candidates)
    starts = sorted(scores["ForecastStartDate"].unique())
    if not starts:
        raise RuntimeError("No score rows matched the requested candidates.")

    summaries = []
    choices = []
    for candidate in candidates:
        summary, selected = fixed_policy(scores, starts, candidate)
        summaries.append(summary)
        choices.append(selected)
    for lookback in args.lookback_windows:
        summary, selected = rolling_policy(scores, starts, lookback, args.default_candidate)
        summaries.append(summary)
        choices.append(selected)
    summary, selected = expanding_policy(
        scores,
        starts,
        args.expanding_warmup_windows,
        args.default_candidate,
    )
    summaries.append(summary)
    choices.append(selected)

    summary_df = pd.DataFrame(summaries).sort_values(["WAPE", "ZeroForecastSoldUnitPct"])
    choices_df = pd.concat(choices, ignore_index=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(args.output_dir / "candidate_policy_backtest_summary.csv", index=False)
    choices_df.to_csv(args.output_dir / "candidate_policy_backtest_window_choices.csv", index=False)
    print(summary_df.to_string(index=False))
    print(f"Wrote policy backtest outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
