"""Self-calibrating recency / regime brake for forecast replacement.

WHY THIS SCRIPT EXISTS
----------------------
The replacement backtests showed the hybrid wins the 26-window aggregate but
over-runs badly in the most recent sale-period window, and the fix so far is a
hand-tuned static volume cap (``0.85x recent``). A static cap is brittle: it is
chosen with hindsight over the whole history and will be wrong when the regime
shifts again.

This script replaces the static cap with a **self-calibrating brake**: for each
forecast window it chooses how hard to brake using ONLY information from prior
windows - specifically the recently realized over/under-forecast of a reference
(uncapped) candidate - and then evaluates the choice on that window's true score.
Because it never looks at the current window before choosing, it is an honest
out-of-sample policy, the same discipline the policy backtest enforces.

How the brake is chosen for window t (after a warmup):
  1. Look back ``--lookback`` windows. Compute the reference candidate's realized
     coverage-of-forecast ratio ``r = sum(SoldUnits) / sum(ForecastUnits)`` over
     those windows. ``r < 1`` means the reference recently forecast too hot.
  2. Target brake = ``clip(r, min_cap, 1.0)`` - never inflate above 1.0 by default.
  3. Pick the available cap variant whose cap is the largest value <= the target
     (brake at least as hard as the recent miss implies); if the target is >= 1.0,
     use the uncapped hybrid.
  4. Coverage guard: if the chosen variant's recent sold-unit coverage is below
     ``--min-coverage``, step toward a higher-coverage variant, falling back to the
     ``--floor-candidate`` (recent no-ML) if needed - protecting against the
     corporate-style coverage collapse.

The variants are not retrained here; the script consumes precomputed per-window
candidate score CSVs (same contract as ``forecast_replacement_policy_backtest.py``)
and a user-supplied map of ``cap -> candidate name``. The selected sequence is then
scored with each window's real numbers and compared to the fixed candidates.

EXAMPLE
-------
    uv run python scripts/python/forecast_replacement_recency_brake.py \
        --score-file Output/.../combined_replacement_window_scores.csv \
        --reference hybrid_..._uncapped \
        --cap-variant 1.00:hybrid_..._cap1p00 \
        --cap-variant 0.85:hybrid_..._cap0p85 \
        --cap-variant 0.70:hybrid_..._cap0p70 \
        --floor-candidate recent_no_ml_no_promo_floor \
        --compare corporate --compare recent_no_ml_no_promo_floor
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402

FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_OUTPUT_DIR = FORECAST_ACCURACY_ROOT / "replacement_ml_backtests" / "recency_brake"
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
    parser = argparse.ArgumentParser(description="Self-calibrating recency/regime brake policy backtest.")
    parser.add_argument("--score-file", action="append", type=Path, dest="score_files", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--reference", required=True, help="Uncapped candidate used to read recent over/under-forecast.")
    parser.add_argument(
        "--cap-variant",
        action="append",
        dest="cap_variants",
        required=True,
        help="Map CAP:CANDIDATE, e.g. 0.85:hybrid_..._cap0p85. Repeatable. CAP>=1.0 is the uncapped tier.",
    )
    parser.add_argument("--floor-candidate", required=True, help="High-coverage fallback (e.g. recent no-ML).")
    parser.add_argument("--compare", action="append", dest="compare", default=None, help="Fixed candidates to report alongside.")
    parser.add_argument("--lookback", type=int, default=2, help="Prior windows used to estimate the recent regime.")
    parser.add_argument("--min-cap", type=float, default=0.6, help="Hardest brake the policy may request.")
    parser.add_argument("--min-coverage", type=float, default=0.8, help="Recent sold-unit coverage floor for a pick.")
    return parser.parse_args()


def load_scores(paths: list[Path]) -> pd.DataFrame:
    frames = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Score file not found: {path}")
        frame = pd.read_csv(path)
        missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"{path} is missing required columns: {missing}")
        frames.append(frame[REQUIRED_COLUMNS])
    scores = pd.concat(frames, ignore_index=True).drop_duplicates(["ForecastStartDate", "Candidate"], keep="last")
    scores["ForecastStartDate"] = pd.to_datetime(scores["ForecastStartDate"]).dt.normalize()
    return scores


def parse_cap_variants(raw: list[str]) -> list[tuple[float, str]]:
    variants = []
    for item in raw:
        cap_str, _, candidate = item.partition(":")
        if not candidate:
            raise ValueError(f"--cap-variant must be CAP:CANDIDATE, got {item!r}")
        variants.append((float(cap_str), candidate))
    # Highest cap (loosest brake) first.
    return sorted(variants, key=lambda kv: kv[0], reverse=True)


def normalize_coverage(value: float) -> float:
    return value / 100.0 if value > 1.0 else value


def recent_ratio(scores: pd.DataFrame, candidate: str, prior_dates: list[pd.Timestamp]) -> float | None:
    prior = scores.loc[scores["Candidate"].eq(candidate) & scores["ForecastStartDate"].isin(prior_dates)]
    forecast = float(prior["ForecastUnits"].sum())
    sold = float(prior["SoldUnits"].sum())
    if forecast <= 0:
        return None
    return sold / forecast


def recent_coverage(scores: pd.DataFrame, candidate: str, prior_dates: list[pd.Timestamp]) -> float:
    prior = scores.loc[scores["Candidate"].eq(candidate) & scores["ForecastStartDate"].isin(prior_dates)]
    if prior.empty:
        return 0.0
    return float(prior["SoldUnitForecastCoveragePct"].map(normalize_coverage).mean())


def choose_candidate(
    scores: pd.DataFrame,
    prior_dates: list[pd.Timestamp],
    reference: str,
    cap_variants: list[tuple[float, str]],
    floor_candidate: str,
    min_cap: float,
    min_coverage: float,
) -> tuple[str, float]:
    ratio = recent_ratio(scores, reference, prior_dates)
    # Loosest variant (highest cap) is the default when nothing says to brake.
    if ratio is None:
        return cap_variants[0][1], 1.0
    target_cap = max(min_cap, min(ratio, cap_variants[0][0]))
    # Pick the largest cap <= target (brake at least as hard as the recent miss).
    chosen = cap_variants[-1]
    for cap, candidate in cap_variants:
        if cap <= target_cap:
            chosen = (cap, candidate)
            break
    # Coverage guard: if the pick's recent coverage is too low, loosen toward
    # higher-coverage variants, then to the floor candidate.
    ordered = sorted(cap_variants, key=lambda kv: kv[0])  # hardest -> loosest
    if recent_coverage(scores, chosen[1], prior_dates) < min_coverage:
        for cap, candidate in ordered:
            if cap >= chosen[0] and recent_coverage(scores, candidate, prior_dates) >= min_coverage:
                chosen = (cap, candidate)
                break
        else:
            if recent_coverage(scores, floor_candidate, prior_dates) >= min_coverage:
                return floor_candidate, 0.0
    return chosen[1], chosen[0]


def score_row(scores: pd.DataFrame, start: pd.Timestamp, candidate: str) -> dict[str, Any] | None:
    match = scores.loc[scores["ForecastStartDate"].eq(start) & scores["Candidate"].eq(candidate)]
    if match.empty:
        return None
    return match.iloc[0].to_dict()


def summarize(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    df = pd.DataFrame(rows)
    sold = float(df["SoldUnits"].sum())
    forecast = float(df["ForecastUnits"].sum())
    abs_err = float(df["AbsErrorUnits"].sum())
    zero = float(df["ZeroForecastSoldUnits"].sum())
    return {
        "Policy": name,
        "Windows": int(df["ForecastStartDate"].nunique()),
        "WAPE": abs_err / sold if sold else float("nan"),
        "BiasPct": (forecast - sold) / sold if sold else float("nan"),
        "ZeroForecastSoldUnitPct": zero / sold if sold else float("nan"),
        "AvgCoverage": float(df["SoldUnitForecastCoveragePct"].map(normalize_coverage).mean()),
    }


def main() -> None:
    args = parse_args()
    scores = load_scores(args.score_files)
    cap_variants = parse_cap_variants(args.cap_variants)
    starts = sorted(scores["ForecastStartDate"].unique())
    if len(starts) <= args.lookback:
        raise RuntimeError("Not enough windows for the requested lookback.")

    selected_rows: list[dict[str, Any]] = []
    choices: list[dict[str, Any]] = []
    for idx, start in enumerate(starts):
        if idx < args.lookback:
            candidate, requested_cap = cap_variants[0][1], 1.0  # warmup: loosest
        else:
            prior = starts[idx - args.lookback : idx]
            candidate, requested_cap = choose_candidate(
                scores, prior, args.reference, cap_variants,
                args.floor_candidate, args.min_cap, args.min_coverage,
            )
        row = score_row(scores, start, candidate)
        if row is None:
            raise ValueError(f"No score row for chosen candidate {candidate!r} at {start.date()}.")
        ref_ratio = recent_ratio(scores, args.reference, starts[max(0, idx - args.lookback): idx])
        choices.append({
            "ForecastStartDate": start,
            "ChosenCandidate": candidate,
            "RequestedCap": requested_cap,
            "RecentReferenceRatio": ref_ratio,
        })
        selected_rows.append(row)

    summaries = [summarize(selected_rows, "self_calibrating_recency_brake")]
    for cand in (args.compare or []) + [v[1] for v in cap_variants] + [args.floor_candidate]:
        rows = [r for s in starts if (r := score_row(scores, s, cand)) is not None]
        if rows:
            summaries.append(summarize(rows, f"fixed__{cand}"))

    summary_df = pd.DataFrame(summaries).drop_duplicates("Policy").sort_values("WAPE")
    choices_df = pd.DataFrame(choices)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(args.output_dir / "recency_brake_summary.csv", index=False)
    choices_df.to_csv(args.output_dir / "recency_brake_window_choices.csv", index=False)

    # Save window-by-window scores of the self-calibrating brake policy
    selected_df = pd.DataFrame(selected_rows).copy()
    selected_df["Candidate"] = "self_calibrating_recency_brake"
    selected_df.to_csv(args.output_dir / "recency_brake_window_scores.csv", index=False)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "score_files": [str(p) for p in args.score_files],
        "reference": args.reference,
        "cap_variants": [{"cap": c, "candidate": n} for c, n in cap_variants],
        "floor_candidate": args.floor_candidate,
        "lookback": args.lookback,
        "min_cap": args.min_cap,
        "min_coverage": args.min_coverage,
        "notes": [
            "The brake is chosen from prior-window realized over/under-forecast only, so the "
            "evaluation is honest out-of-sample.",
            "A coverage guard prevents the brake from collapsing sold-unit coverage the way a "
            "corporate-style under-forecast does.",
        ],
    }
    with (args.output_dir / "recency_brake_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True, default=str)

    print("Self-calibrating recency brake vs fixed candidates (lower WAPE is better):")
    print(summary_df.to_string(index=False))
    print("\nPer-window brake choices:")
    print(choices_df.to_string(index=False))
    print(f"\nWrote recency-brake outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
