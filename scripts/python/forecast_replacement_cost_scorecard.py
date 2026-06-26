"""Expected-cost scorecard for forecast replacement candidates.

WHY THIS SCRIPT EXISTS
----------------------
The replacement backtests surfaced a real tension: the volume-braked hybrid wins
on WAPE but covers fewer sold units, while the recent no-ML baseline covers almost
everything but runs hot on WAPE. WAPE alone cannot resolve that tradeoff, because a
WAPE point and a missed (zero-forecast) sold unit do not cost the warehouse the
same thing. For an AX replenishment input, a sold SKU with no forecast row gets no
advance replenishment - a stockout/lost-sale risk - whereas an over-forecast unit
costs excess replenishment work and location pressure.

This script converts each candidate's error into an **expected operational cost** so
the coverage-vs-WAPE decision is made on cost, not on a single accuracy number. It
also runs a break-even sweep that reports the cost ratio at which the recommended
candidate flips, so the business can see how sensitive the choice is to its own cost
assumptions.

It consumes the same per-window candidate score CSVs used by
``forecast_replacement_policy_backtest.py`` and does not retrain anything.

COST DECOMPOSITION (exact from the window aggregates)
-----------------------------------------------------
For every (window, candidate) row with ForecastUnits F, SoldUnits S and absolute
error A (= sum of |forecast - actual| over the scored cells):

    over_units  = (A + (F - S)) / 2     # forecast exceeded actual (excess stock)
    under_units = (A - (F - S)) / 2     # actual exceeded forecast (shortfall)

``over_units - under_units == F - S`` and ``over_units + under_units == A`` hold
exactly at any grain, so the split needs no per-SKU data. ZeroForecastSoldUnits is a
subset of under_units (cells that got no forecast row at all) and is charged a higher
penalty than a partial shortfall on a SKU that did get a forecast row.

    expected_cost = c_over  * over_units
                  + c_under * (under_units - zero_forecast_sold_units)
                  + c_zero  * zero_forecast_sold_units

EXAMPLE
-------
    uv run python scripts/python/forecast_replacement_cost_scorecard.py \
        --score-file Output/ForecastAccuracy/replacement_ml_backtests/combined_replacement_window_scores.csv \
        --focus "hybrid_ml_hgb_absolute_log_raw_min_20p0_units_recent_w0p1" \
        --focus "recent_no_ml_no_promo_floor" \
        --c-over 1 --c-under 3 --c-zero 6
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402

FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_OUTPUT_DIR = FORECAST_ACCURACY_ROOT / "replacement_ml_backtests" / "cost_scorecard"
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
    """Parse command line arguments for the expected-cost scorecard.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Expected-cost scorecard for replacement candidates.")
    parser.add_argument("--score-file", action="append", type=Path, dest="score_files", required=False)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--candidate", action="append", dest="candidates", help="Limit to these candidates.")
    parser.add_argument("--focus", action="append", dest="focus", help="Two candidates to break-even compare.")
    parser.add_argument("--c-over", type=float, default=1.0, help="Cost per over-forecast unit (excess stock/labor).")
    parser.add_argument("--c-under", type=float, default=3.0, help="Cost per shortfall unit on a covered SKU.")
    parser.add_argument("--c-zero", type=float, default=6.0, help="Cost per sold unit on a SKU with no forecast row.")
    parser.add_argument(
        "--understock-sweep",
        nargs="+",
        type=float,
        default=[1, 2, 3, 4, 6, 8, 12],
        help="Sweep of the shortfall-to-excess cost ratio for break-even analysis.",
    )
    return parser.parse_args()


def load_scores(paths: list[Path], candidates: list[str] | None) -> pd.DataFrame:
    """Load and validate window-score CSV files.

    Args:
        paths: List of score file paths to load.
        candidates: Optional list of candidate names to filter for.

    Returns:
        pd.DataFrame: The validated scores DataFrame.
    """
    frames = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Score file not found: {path}")
        frame = pd.read_csv(path)
        missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"{path} is missing required columns: {missing}")
        frames.append(frame[REQUIRED_COLUMNS])
    scores = pd.concat(frames, ignore_index=True)
    scores = scores.drop_duplicates(["ForecastStartDate", "Candidate"], keep="last")
    if candidates:
        scores = scores.loc[scores["Candidate"].isin(candidates)].copy()
    scores["ForecastStartDate"] = pd.to_datetime(scores["ForecastStartDate"]).dt.normalize()
    return scores


def decompose(scores: pd.DataFrame) -> pd.DataFrame:
    """Decompose absolute error into over-forecast and under-forecast units.

    Also isolates zero-forecast misses to allow specific penalty rates.

    Args:
        scores: Full candidate scores DataFrame.

    Returns:
        pd.DataFrame: A copy of scores with decomposed error columns added.
    """
    df = scores.copy()
    bias = df["ForecastUnits"] - df["SoldUnits"]
    df["OverUnits"] = (df["AbsErrorUnits"] + bias).clip(lower=0) / 2.0
    df["UnderUnits"] = (df["AbsErrorUnits"] - bias).clip(lower=0) / 2.0
    df["ZeroForecastSoldUnits"] = df["ZeroForecastSoldUnits"].clip(lower=0)
    # Keep the zero-forecast misses within total under-forecast units.
    df["ZeroForecastSoldUnits"] = np.minimum(df["ZeroForecastSoldUnits"], df["UnderUnits"])
    df["CoveredUnderUnits"] = (df["UnderUnits"] - df["ZeroForecastSoldUnits"]).clip(lower=0)
    return df


def cost_summary(df: pd.DataFrame, c_over: float, c_under: float, c_zero: float) -> pd.DataFrame:
    """Summarize decomposed scores across all windows and calculate expected costs.

    Args:
        df: Decomposed scores DataFrame.
        c_over: Unit cost of over-forecasting.
        c_under: Unit cost of shortfall (under-forecasting) on covered SKUs.
        c_zero: Unit cost of shortfall on uncovered/zero-forecast SKUs.

    Returns:
        pd.DataFrame: A summary DataFrame sorted by expected cost descending.
    """
    grouped = df.groupby("Candidate", as_index=False).agg(
        Windows=("ForecastStartDate", "nunique"),
        SoldUnits=("SoldUnits", "sum"),
        ForecastUnits=("ForecastUnits", "sum"),
        AbsErrorUnits=("AbsErrorUnits", "sum"),
        OverUnits=("OverUnits", "sum"),
        CoveredUnderUnits=("CoveredUnderUnits", "sum"),
        ZeroForecastSoldUnits=("ZeroForecastSoldUnits", "sum"),
        AvgCoverage=("SoldUnitForecastCoveragePct", "mean"),
    )
    grouped["WAPE"] = grouped["AbsErrorUnits"] / grouped["SoldUnits"]
    grouped["BiasPct"] = (grouped["ForecastUnits"] - grouped["SoldUnits"]) / grouped["SoldUnits"]
    grouped["ExpectedCost"] = (
        c_over * grouped["OverUnits"]
        + c_under * grouped["CoveredUnderUnits"]
        + c_zero * grouped["ZeroForecastSoldUnits"]
    )
    grouped["CostPerSoldUnit"] = grouped["ExpectedCost"] / grouped["SoldUnits"]
    return grouped.sort_values("ExpectedCost")


def break_even(
    df: pd.DataFrame,
    focus: list[str],
    sweep: list[float],
    c_over: float,
    zero_multiple: float,
) -> pd.DataFrame:
    """Sweep the shortfall/excess cost ratio and show which focus candidate wins.

    For each ratio in the sweep:
    c_under = ratio * c_over
    c_zero = zero_multiple * c_under

    Args:
        df: Decomposed scores DataFrame.
        focus: Two candidate identifiers to compare.
        sweep: List of shortfall-to-excess cost ratios to sweep.
        c_over: Unit cost of over-forecasting.
        zero_multiple: Multiplier for zero-forecast unit cost relative to c_under.

    Returns:
        pd.DataFrame: A DataFrame containing sweep ratios, cost comparison, and winner.
    """
    rows = []
    sub = df.loc[df["Candidate"].isin(focus)]
    agg = sub.groupby("Candidate").agg(
        OverUnits=("OverUnits", "sum"),
        CoveredUnderUnits=("CoveredUnderUnits", "sum"),
        ZeroForecastSoldUnits=("ZeroForecastSoldUnits", "sum"),
        SoldUnits=("SoldUnits", "sum"),
    )
    for ratio in sweep:
        c_under = ratio * c_over
        c_zero = zero_multiple * c_under
        costs = (
            c_over * agg["OverUnits"]
            + c_under * agg["CoveredUnderUnits"]
            + c_zero * agg["ZeroForecastSoldUnits"]
        )
        winner = str(costs.idxmin())
        row = {"ShortfallToExcessRatio": ratio, "Winner": winner}
        for cand in focus:
            row[f"cost__{cand}"] = float(costs.get(cand, np.nan))
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    """Execute the expected-cost scorecard and break-even sweep pipeline."""
    args = parse_args()
    score_files = args.score_files or [
        FORECAST_ACCURACY_ROOT / "replacement_ml_backtests" / "combined_replacement_window_scores.csv"
    ]
    scores = load_scores(score_files, args.candidates)
    if scores.empty:
        raise RuntimeError("No score rows loaded. Pass --score-file pointing at a window-scores CSV.")

    decomposed = decompose(scores)
    summary = cost_summary(decomposed, args.c_over, args.c_under, args.c_zero)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_dir / "cost_scorecard_summary.csv", index=False)

    focus = args.focus
    if not focus or len(focus) < 2:
        # Default to the lowest-WAPE and the highest-coverage candidates.
        by_wape = summary.sort_values("WAPE")["Candidate"].tolist()
        by_cov = summary.sort_values("AvgCoverage", ascending=False)["Candidate"].tolist()
        focus = list(dict.fromkeys([by_wape[0], by_cov[0]]))
        if len(focus) < 2 and len(by_wape) > 1:
            focus.append(by_wape[1])
    zero_multiple = args.c_zero / args.c_under if args.c_under else 2.0
    sweep = break_even(decomposed, focus, args.understock_sweep, args.c_over, zero_multiple)
    sweep.to_csv(args.output_dir / "cost_scorecard_break_even.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "score_files": [str(p) for p in score_files],
        "costs": {"c_over": args.c_over, "c_under": args.c_under, "c_zero": args.c_zero},
        "focus_candidates": focus,
        "zero_multiple_of_under": zero_multiple,
        "notes": [
            "ExpectedCost charges over-forecast, covered shortfall, and zero-forecast misses "
            "at separate unit costs so the coverage-vs-WAPE tradeoff is decided in cost terms.",
            "Break-even sweeps the shortfall/excess cost ratio; the ratio where Winner flips is "
            "the decision boundary between the low-WAPE and high-coverage candidates.",
        ],
    }
    with (args.output_dir / "cost_scorecard_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    show = ["Candidate", "Windows", "WAPE", "BiasPct", "AvgCoverage", "CostPerSoldUnit", "ExpectedCost"]
    print(f"Expected-cost scorecard (c_over={args.c_over}, c_under={args.c_under}, c_zero={args.c_zero}); lower cost is better:")
    print(summary[show].to_string(index=False))
    print(f"\nBreak-even between {focus} (shortfall/excess ratio -> winner):")
    print(sweep.to_string(index=False))
    print(f"\nWrote cost scorecard to {args.output_dir}")


if __name__ == "__main__":
    main()
