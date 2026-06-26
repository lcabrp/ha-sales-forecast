"""Velocity-tier / slotting scorecard for forecast candidates.

WHY THIS SCRIPT EXISTS
----------------------
WAPE is only a proxy for what slotting actually consumes. The warehouse acts on
the velocity letter inside each SlotTier (the "AA"/"A"/"B"/"C" in e.g. ``GIRMAA``),
and on how *stable* that letter is week to week. A forecast can have a good WAPE
yet still put SKUs in the wrong velocity tier or churn tiers so often that the
physical zone map becomes a moving target (the documented 8-14% weekly velocity
churn problem).

This scorecard scores a forecast on the things slotting cares about:

  1. **Tier accuracy** - does the forecast put each SKU in the same velocity tier
     that its *actual* demand would imply? (exact match and within-one-tier)
  2. **Units-weighted misallocation** - how many actual units are mis-tiered?
  3. **Tier stability / churn** - across consecutive forecast windows, how often
     does the forecast move a SKU between tiers (and how many 3-rank C<->AA jumps)?
     Lower churn = a calmer, cheaper-to-maintain zone map.

It consumes the per-SKU/day forecast parquet written by
``forecast_model_horizon_train.py --save-forecast`` and compares every forecast
column in that file (horizon-consistent model, old champion, corporate, recent
baselines) on the same grid. It does NOT modify any existing script.

Velocity thresholds replicate the inherited BRG/Ankura cutoffs on 13-week units
(C<=20, B 21-40, A 41-100, AA>100). A 14-day forecast total is extrapolated to a
13-week-equivalent via x6.5; this transform is applied identically to every
forecast so the comparison stays fair.

EXAMPLE
-------
    uv run python scripts/python/forecast_model_slotting_scorecard.py \
        --forecast Output/ForecastAccuracy/model/horizon_consistent/forecast_sku_day.parquet
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Add the parent directory to Python path to import sister utilities
PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_train import MODEL_DIR  # noqa: E402

# Default locations for incoming forecasts and target output scorecard reports
DEFAULT_FORECAST_PATH = MODEL_DIR / "horizon_consistent" / "forecast_sku_day.parquet"
DEFAULT_OUTPUT_DIR = MODEL_DIR / "slotting_scorecard"
ACTUAL_COLUMN = "SoldUnits"
SKU_COLUMN = "SKU"

# Inherited velocity cutoffs on 13-week units (see VELOCITY_THRESHOLD_ANALYSIS.md).
TIER_ORDER = ["C", "B", "A", "AA"]
TIER_RANK = {tier: i for i, tier in enumerate(TIER_ORDER)}
THIRTEEN_WEEK_FROM_14D = 91.0 / 14.0  # extrapolate a 14-day total to 13-week units

# Forecast candidate methods to score in the comparison grid
CANDIDATE_COLUMNS = [
    "HorizonConsistentMLForecastQty",
    "FrozenChampionMLForecastQty",
    "CorporateForecastQty",
    "CorporateBaselineQty",
    "Recent7BaselineQty",
    "Recent28BaselineQty",
    "HybridBaselineQty",
    "BottomUp",
    "TopDown",
    "MiddleOut",
]


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the slotting scorecard.

    Returns:
        argparse.Namespace: Populated argument Namespace with file paths and parameters.
    """
    parser = argparse.ArgumentParser(description="Velocity-tier / slotting scorecard for forecasts.")
    parser.add_argument(
        "--forecast",
        type=Path,
        default=DEFAULT_FORECAST_PATH,
        help="Path to the forecast Parquet file containing columns for actuals and predictions.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory to save the scorecard CSVs and JSON metadata.",
    )
    parser.add_argument(
        "--thirteen-week-factor",
        type=float,
        default=THIRTEEN_WEEK_FROM_14D,
        help="Multiplier converting a 14-day total to 13-week-equivalent units.",
    )
    return parser.parse_args()


def velocity_tier(units_13wk: float) -> str:
    """Classify 13-week unit volume into standard velocity letter tiers.

    Args:
        units_13wk (float): Extrapolated or actual 13-week unit volume.

    Returns:
        str: Velocity tier code ('C', 'B', 'A', or 'AA').
    """
    if pd.isna(units_13wk) or units_13wk <= 20:
        return "C"
    if units_13wk > 100:
        return "AA"
    if units_13wk > 40:
        return "A"
    return "B"


def sku_window_totals(forecast: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """Sum each forecast candidate and actuals to a 14-day total per SKU.

    Args:
        forecast (pd.DataFrame): SKU-day forecast observations.
        value_cols (list[str]): Columns representing forecast predictions.

    Returns:
        pd.DataFrame: Grouped SKU-window dataframe.
    """
    agg = {col: "sum" for col in [ACTUAL_COLUMN, *value_cols]}
    totals = forecast.groupby(["WindowLabel", SKU_COLUMN], as_index=False).agg(agg)
    return totals


def assign_tiers(totals: pd.DataFrame, value_cols: list[str], factor: float) -> pd.DataFrame:
    """Map sales quantities to velocity tiers based on the thirteen-week factor.

    Args:
        totals (pd.DataFrame): Dataframe with summed forecast quantities per SKU/window.
        value_cols (list[str]): List of forecast columns.
        factor (float): Multiplier for 13-week extrapolation.

    Returns:
        pd.DataFrame: Dataframe with added velocity tier columns.
    """
    out = totals.copy()
    out["ActualTier"] = (out[ACTUAL_COLUMN] * factor).map(velocity_tier)
    for col in value_cols:
        out[f"{col}__tier"] = (out[col] * factor).map(velocity_tier)
    return out


def tier_accuracy(tiers: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """Calculate velocity tier accuracy metrics for each forecast candidate.

    Args:
        tiers (pd.DataFrame): SKU-window totals with mapped tiers.
        value_cols (list[str]): Columns containing candidate forecasts.

    Returns:
        pd.DataFrame: Comparison scorecard with exact match rates and unit-weighted miss percentages.
    """
    rows = []
    actual_rank = tiers["ActualTier"].map(TIER_RANK)
    actual_units = tiers[ACTUAL_COLUMN]
    total_units = float(actual_units.sum())
    for col in value_cols:
        pred_tier = tiers[f"{col}__tier"]
        pred_rank = pred_tier.map(TIER_RANK)
        exact = pred_tier.eq(tiers["ActualTier"])
        within_one = (pred_rank - actual_rank).abs().le(1)
        mis_units = float(actual_units.loc[~exact].sum())
        rows.append(
            {
                "ForecastName": col,
                "SKUs": int(len(tiers)),
                "ExactTierAccuracy": float(exact.mean()),
                "WithinOneTierAccuracy": float(within_one.mean()),
                "UnitsWeightedMisallocationPct": mis_units / total_units if total_units else 0.0,
                "MeanTierRankError": float((pred_rank - actual_rank).abs().mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("ExactTierAccuracy", ascending=False)


def confusion_for(tiers: pd.DataFrame, col: str) -> pd.DataFrame:
    """Generate a confusion matrix cross-tabulation for a single candidate.

    Args:
        tiers (pd.DataFrame): Assigned tier dataframe.
        col (str): The forecast candidate column name to tabulate.

    Returns:
        pd.DataFrame: Reindexed cross-tabulation table.
    """
    confusion = pd.crosstab(tiers["ActualTier"], tiers[f"{col}__tier"])
    confusion = confusion.reindex(index=TIER_ORDER, columns=TIER_ORDER, fill_value=0)
    confusion.index.name = "ActualTier"
    confusion.columns.name = f"{col}_PredictedTier"
    return confusion.reset_index()


def tier_stability(tiers: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """Evaluate velocity tier churn rate between consecutive forecast windows.

    Lower churn values are preferred because they indicate that physical SKU relocations
    will be minimized.

    Args:
        tiers (pd.DataFrame): Assigned tier dataframe.
        value_cols (list[str]): Columns representing forecast candidates.

    Returns:
        pd.DataFrame: Stability breakdown per window pair.
    """
    windows = sorted(tiers["WindowLabel"].unique())
    if len(windows) < 2:
        return pd.DataFrame(
            [{"Note": "Need >=2 windows (use multiple --window args when saving the forecast) to measure churn."}]
        )
    rows = []
    tier_cols = {"Actual": "ActualTier", **{col: f"{col}__tier" for col in value_cols}}
    for prev_w, next_w in zip(windows[:-1], windows[1:], strict=False):
        prev = tiers.loc[tiers["WindowLabel"].eq(prev_w)]
        nxt = tiers.loc[tiers["WindowLabel"].eq(next_w)]
        merged = prev.merge(nxt, on=SKU_COLUMN, suffixes=("_prev", "_next"))
        for name, tcol in tier_cols.items():
            prev_tier = merged[f"{tcol}_prev"]
            next_tier = merged[f"{tcol}_next"]
            changed = prev_tier.ne(next_tier)
            rank_jump = (next_tier.map(TIER_RANK) - prev_tier.map(TIER_RANK)).abs()
            rows.append(
                {
                    "FromWindow": prev_w,
                    "ToWindow": next_w,
                    "ForecastName": name,
                    "SharedSKUs": int(len(merged)),
                    "TierChangePct": float(changed.mean()),
                    "ThreeRankJumpPct": float(rank_jump.ge(3).mean()),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    """Load inputs, execute slotting accuracy/stability audits, and write reports."""
    args = parse_args()
    if not args.forecast.exists():
        raise SystemExit(
            f"Forecast file not found: {args.forecast}\n"
            "Generate it first with: forecast_model_horizon_train.py --save-forecast <path>"
        )
    forecast = pd.read_parquet(args.forecast)
    value_cols = [c for c in CANDIDATE_COLUMNS if c in forecast.columns]
    if not value_cols:
        raise SystemExit("No recognized forecast columns found in the input file.")

    totals = sku_window_totals(forecast, value_cols)
    tiers = assign_tiers(totals, value_cols, args.thirteen_week_factor)

    accuracy = tier_accuracy(tiers, value_cols)
    stability = tier_stability(tiers, value_cols)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    accuracy.to_csv(args.output_dir / "tier_accuracy_summary.csv", index=False)
    stability.to_csv(args.output_dir / "tier_stability.csv", index=False)
    for col in value_cols:
        confusion_for(tiers, col).to_csv(
            args.output_dir / f"tier_confusion_{col}.csv", index=False
        )

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "forecast_input": str(args.forecast),
        "output_dir": str(args.output_dir),
        "thirteen_week_factor": args.thirteen_week_factor,
        "tier_thresholds_13wk_units": {"C": "<=20", "B": "21-40", "A": "41-100", "AA": ">100"},
        "forecasts_scored": value_cols,
        "notes": [
            "Tier is derived from each forecast's 14-day total x thirteen_week_factor, "
            "bucketed with the inherited BRG/Ankura cutoffs.",
            "ExactTierAccuracy and UnitsWeightedMisallocationPct measure whether the "
            "forecast would slot SKUs into the correct velocity zone.",
            "TierChangePct / ThreeRankJumpPct measure zone-map stability across windows; "
            "a lower-churn forecast keeps the physical layout from thrashing.",
        ],
    }
    with (args.output_dir / "slotting_scorecard_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print("Velocity-tier accuracy (higher is better):")
    print(accuracy.to_string(index=False))
    print("\nTier stability across windows (lower churn is better):")
    print(stability.to_string(index=False))
    print(f"\nWrote slotting scorecard to {args.output_dir}")


if __name__ == "__main__":
    main()

