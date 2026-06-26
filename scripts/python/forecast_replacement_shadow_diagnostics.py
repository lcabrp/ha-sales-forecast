"""Build daily and SKU diagnostics for a replacement shadow forecast window."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_replacement_backtest import ACTUALS_PATH, load_actuals  # noqa: E402
from forecast_replacement_contract import normalize_sku_series  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402


FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DEFAULT_SHADOW_DIR = (
    FORECAST_ACCURACY_ROOT
    / "replacement_shadow_pdl_sku_refreshed"
    / "shadow_2026-06-18_2026-07-01"
)
DEFAULT_FORECAST_PATH = DEFAULT_SHADOW_DIR / "shadow_daily_forecasts.parquet"
DEFAULT_METADATA_PATH = DEFAULT_SHADOW_DIR / "shadow_metadata.json"
DEFAULT_OUTPUT_DIR = DEFAULT_SHADOW_DIR / "diagnostics"
DEFAULT_INVENTORY_PATH = FORECAST_ACCURACY_ROOT / "inventory" / "pickface_inventory_sku_day.parquet"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize daily and SKU-level behavior for a shadow forecast."
    )
    parser.add_argument("--forecast-path", type=Path, default=DEFAULT_FORECAST_PATH)
    parser.add_argument("--actuals-path", type=Path, default=ACTUALS_PATH)
    parser.add_argument("--metadata-path", type=Path, default=DEFAULT_METADATA_PATH)
    parser.add_argument("--inventory-path", type=Path, default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--forecast-start-date")
    parser.add_argument("--forecast-end-date")
    parser.add_argument(
        "--score-through-date",
        help=(
            "Inclusive actuals-through date for diagnostics. Defaults to "
            "metadata actuals_available_through, capped at forecast_end."
        ),
    )
    parser.add_argument(
        "--candidate",
        action="append",
        dest="candidates",
        help="Candidate to include. Repeat to include multiple; default includes all.",
    )
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument(
        "--skip-inventory",
        action="store_true",
        help="Do not annotate SKU summaries with mirrored pickface inventory snapshots.",
    )
    return parser.parse_args()


def normalize_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def read_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def date_arg(value: str | None, fallback: Any) -> pd.Timestamp:
    if value:
        return pd.Timestamp(value).normalize()
    if fallback:
        return pd.Timestamp(fallback).normalize()
    raise ValueError("Unable to infer date; pass explicit date argument.")


def load_forecasts(
    path: Path,
    candidates: list[str] | None,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    forecast = pd.read_parquet(path)
    required = {"Candidate", "SKU", "ForecastDate", "ForecastUnits"}
    missing = required.difference(forecast.columns)
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")

    forecast = forecast[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]].copy()
    forecast["Candidate"] = forecast["Candidate"].fillna("").astype(str).str.strip()
    forecast["SKU"] = normalize_sku_series(forecast["SKU"])
    forecast["ForecastDate"] = normalize_date(forecast["ForecastDate"])
    forecast["ForecastUnits"] = (
        pd.to_numeric(forecast["ForecastUnits"], errors="coerce").fillna(0).clip(lower=0)
    )
    forecast = forecast.loc[
        forecast["Candidate"].ne("")
        & forecast["SKU"].ne("")
        & forecast["ForecastDate"].notna()
        & forecast["ForecastDate"].between(start, end)
        & forecast["ForecastUnits"].gt(0)
    ].copy()
    if candidates:
        candidate_set = set(candidates)
        forecast = forecast.loc[forecast["Candidate"].isin(candidate_set)].copy()
        missing_candidates = sorted(candidate_set.difference(set(forecast["Candidate"].unique())))
        if missing_candidates:
            raise ValueError(f"Requested candidate(s) not found: {missing_candidates}")
    return forecast


def actual_daily(actuals_path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    actuals = load_actuals(actuals_path)
    actual = actuals.loc[actuals["ActualDate"].between(start, end)].copy()
    if actual.empty:
        return pd.DataFrame(columns=["SKU", "ForecastDate", "SoldUnits"])
    return (
        actual.groupby(["SKU", "ActualDate"], as_index=False)
        .agg(SoldUnits=("SoldUnits", "sum"))
        .rename(columns={"ActualDate": "ForecastDate"})
    )


def compare_candidate(forecast: pd.DataFrame, actual: pd.DataFrame, candidate: str) -> pd.DataFrame:
    candidate_forecast = (
        forecast.loc[forecast["Candidate"].eq(candidate)]
        .groupby(["SKU", "ForecastDate"], as_index=False)
        .agg(ForecastUnits=("ForecastUnits", "sum"))
    )
    compare = candidate_forecast.merge(actual, on=["SKU", "ForecastDate"], how="outer")
    compare["Candidate"] = candidate
    compare["ForecastUnits"] = pd.to_numeric(compare["ForecastUnits"], errors="coerce").fillna(0)
    compare["SoldUnits"] = pd.to_numeric(compare["SoldUnits"], errors="coerce").fillna(0)
    compare["ErrorUnits"] = compare["ForecastUnits"] - compare["SoldUnits"]
    compare["AbsErrorUnits"] = compare["ErrorUnits"].abs()
    compare["UnderForecastUnits"] = (compare["SoldUnits"] - compare["ForecastUnits"]).clip(lower=0)
    compare["OverForecastUnits"] = (compare["ForecastUnits"] - compare["SoldUnits"]).clip(lower=0)
    compare["ZeroForecastSoldUnits"] = compare["SoldUnits"].where(
        compare["ForecastUnits"].eq(0) & compare["SoldUnits"].gt(0),
        0,
    )
    compare["ForecastNoSaleUnits"] = compare["ForecastUnits"].where(
        compare["ForecastUnits"].gt(0) & compare["SoldUnits"].eq(0),
        0,
    )
    return compare[
        [
            "Candidate",
            "SKU",
            "ForecastDate",
            "ForecastUnits",
            "SoldUnits",
            "ErrorUnits",
            "AbsErrorUnits",
            "UnderForecastUnits",
            "OverForecastUnits",
            "ZeroForecastSoldUnits",
            "ForecastNoSaleUnits",
        ]
    ]


def build_detail(forecast: pd.DataFrame, actual: pd.DataFrame) -> pd.DataFrame:
    frames = [compare_candidate(forecast, actual, candidate) for candidate in sorted(forecast["Candidate"].unique())]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(
        ["Candidate", "ForecastDate", "SKU"], kind="mergesort"
    )


def summarize_daily(detail: pd.DataFrame) -> pd.DataFrame:
    daily = (
        detail.groupby(["Candidate", "ForecastDate"], as_index=False)
        .agg(
            ForecastSkuDays=("ForecastUnits", lambda values: int(values.gt(0).sum())),
            SoldSkuDays=("SoldUnits", lambda values: int(values.gt(0).sum())),
            ForecastUnits=("ForecastUnits", "sum"),
            SoldUnits=("SoldUnits", "sum"),
            AbsErrorUnits=("AbsErrorUnits", "sum"),
            UnderForecastUnits=("UnderForecastUnits", "sum"),
            OverForecastUnits=("OverForecastUnits", "sum"),
            ZeroForecastSoldUnits=("ZeroForecastSoldUnits", "sum"),
            ForecastNoSaleUnits=("ForecastNoSaleUnits", "sum"),
        )
        .sort_values(["Candidate", "ForecastDate"], kind="mergesort")
    )
    daily["BiasUnitsForecastMinusActual"] = daily["ForecastUnits"] - daily["SoldUnits"]
    daily["BiasPct"] = daily["BiasUnitsForecastMinusActual"] / daily["SoldUnits"].where(
        daily["SoldUnits"].ne(0),
        pd.NA,
    )
    daily["WAPE"] = daily["AbsErrorUnits"] / daily["SoldUnits"].where(daily["SoldUnits"].ne(0), pd.NA)
    daily["SoldUnitCoveragePct"] = (
        (daily["SoldUnits"] - daily["ZeroForecastSoldUnits"])
        / daily["SoldUnits"].where(daily["SoldUnits"].ne(0), pd.NA)
    )
    return daily


def summarize_sku(detail: pd.DataFrame) -> pd.DataFrame:
    sku = (
        detail.groupby(["Candidate", "SKU"], as_index=False)
        .agg(
            ForecastDays=("ForecastUnits", lambda values: int(values.gt(0).sum())),
            SoldDays=("SoldUnits", lambda values: int(values.gt(0).sum())),
            ForecastUnits=("ForecastUnits", "sum"),
            SoldUnits=("SoldUnits", "sum"),
            AbsErrorUnits=("AbsErrorUnits", "sum"),
            UnderForecastUnits=("UnderForecastUnits", "sum"),
            OverForecastUnits=("OverForecastUnits", "sum"),
            ZeroForecastSoldUnits=("ZeroForecastSoldUnits", "sum"),
            ForecastNoSaleUnits=("ForecastNoSaleUnits", "sum"),
        )
        .sort_values(["Candidate", "AbsErrorUnits"], ascending=[True, False], kind="mergesort")
    )
    sku["BiasUnitsForecastMinusActual"] = sku["ForecastUnits"] - sku["SoldUnits"]
    sku["BiasPct"] = sku["BiasUnitsForecastMinusActual"] / sku["SoldUnits"].where(sku["SoldUnits"].ne(0), pd.NA)
    sku["WAPE"] = sku["AbsErrorUnits"] / sku["SoldUnits"].where(sku["SoldUnits"].ne(0), pd.NA)
    sku["SoldUnitCoveragePct"] = (
        (sku["SoldUnits"] - sku["ZeroForecastSoldUnits"])
        / sku["SoldUnits"].where(sku["SoldUnits"].ne(0), pd.NA)
    )
    return sku


def inventory_summary(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["SKU"])
    inventory = pd.read_parquet(path)
    required = {"SnapshotDate", "SKU", "PhysicalQty", "HasPickableInventory"}
    if not required.issubset(inventory.columns):
        return pd.DataFrame(columns=["SKU"])
    inventory = inventory[["SnapshotDate", "SKU", "PhysicalQty", "HasPickableInventory"]].copy()
    inventory["SnapshotDate"] = normalize_date(inventory["SnapshotDate"])
    inventory["SKU"] = normalize_sku_series(inventory["SKU"])
    inventory["PhysicalQty"] = pd.to_numeric(inventory["PhysicalQty"], errors="coerce").fillna(0)
    inventory["HasPickableInventory"] = inventory["HasPickableInventory"].fillna(False).astype(bool)
    inventory = inventory.loc[
        inventory["SnapshotDate"].between(start, end)
        & inventory["SKU"].ne("")
        & inventory["SnapshotDate"].notna()
    ].copy()
    if inventory.empty:
        return pd.DataFrame(columns=["SKU"])
    return (
        inventory.groupby("SKU", as_index=False)
        .agg(
            InventorySnapshotDays=("SnapshotDate", "nunique"),
            DaysWithPickableInventory=("HasPickableInventory", "sum"),
            MinPickfacePhysicalQty=("PhysicalQty", "min"),
            MaxPickfacePhysicalQty=("PhysicalQty", "max"),
            AvgPickfacePhysicalQty=("PhysicalQty", "mean"),
        )
        .assign(
            AnyPickableInventory=lambda df: df["DaysWithPickableInventory"].gt(0),
            AllObservedDaysNoPickableInventory=lambda df: df["DaysWithPickableInventory"].eq(0),
        )
    )


def write_top_outputs(sku: pd.DataFrame, output_dir: Path, top_n: int) -> dict[str, str]:
    outputs = {}
    top_specs = {
        "shadow_top_underforecast_skus.csv": ("UnderForecastUnits", False),
        "shadow_top_overforecast_skus.csv": ("OverForecastUnits", False),
        "shadow_zero_forecast_sold_skus.csv": ("ZeroForecastSoldUnits", False),
        "shadow_forecast_no_sale_skus.csv": ("ForecastNoSaleUnits", False),
    }
    for filename, (column, ascending) in top_specs.items():
        frame = (
            sku.loc[sku[column].gt(0)]
            .sort_values(["Candidate", column], ascending=[True, ascending], kind="mergesort")
            .groupby("Candidate", group_keys=False)
            .head(top_n)
        )
        path = output_dir / filename
        frame.to_csv(path, index=False)
        outputs[filename.removesuffix(".csv")] = str(path)
    return outputs


def main() -> None:
    args = parse_args()
    metadata = read_metadata(args.metadata_path)
    forecast_start = date_arg(args.forecast_start_date, metadata.get("forecast_start"))
    full_forecast_end = date_arg(args.forecast_end_date, metadata.get("forecast_end"))
    score_through = date_arg(
        args.score_through_date,
        metadata.get("actuals_available_through") or metadata.get("forecast_end"),
    )
    forecast_end = min(full_forecast_end, score_through)

    forecast = load_forecasts(args.forecast_path, args.candidates, forecast_start, forecast_end)
    actual = actual_daily(args.actuals_path, forecast_start, forecast_end)
    detail = build_detail(forecast, actual)
    if detail.empty:
        raise ValueError("No forecast rows available for diagnostics.")

    daily = summarize_daily(detail)
    sku = summarize_sku(detail)
    inv = pd.DataFrame(columns=["SKU"])
    if not args.skip_inventory:
        inv = inventory_summary(args.inventory_path, forecast_start, forecast_end)
        if not inv.empty:
            sku = sku.merge(inv, on="SKU", how="left")
            for col in ("InventorySnapshotDays", "DaysWithPickableInventory"):
                sku[col] = sku[col].fillna(0).astype(int)
            for col in ("AnyPickableInventory", "AllObservedDaysNoPickableInventory"):
                sku[col] = sku[col].fillna(False).astype(bool)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_path = args.output_dir / "shadow_sku_day_detail.parquet"
    daily_path = args.output_dir / "shadow_daily_candidate_summary.csv"
    sku_path = args.output_dir / "shadow_sku_candidate_summary.csv"
    metadata_path = args.output_dir / "shadow_diagnostics_metadata.json"

    detail.to_parquet(detail_path, index=False, compression="zstd")
    daily.to_csv(daily_path, index=False)
    sku.to_csv(sku_path, index=False)
    top_outputs = write_top_outputs(sku, args.output_dir, args.top_n)

    diagnostic_metadata = {
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "forecast_path": str(args.forecast_path),
        "actuals_path": str(args.actuals_path),
        "metadata_path": str(args.metadata_path),
        "inventory_path": "" if args.skip_inventory else str(args.inventory_path),
        "forecast_start": str(forecast_start.date()),
        "full_forecast_end": str(full_forecast_end.date()),
        "scored_actual_end": str(forecast_end.date()),
        "candidates": sorted(forecast["Candidate"].unique()),
        "rows": {
            "forecast_rows": int(len(forecast)),
            "actual_sku_days": int(len(actual)),
            "detail_rows": int(len(detail)),
            "daily_summary_rows": int(len(daily)),
            "sku_summary_rows": int(len(sku)),
            "inventory_skus": int(inv["SKU"].nunique()) if not inv.empty else 0,
        },
        "outputs": {
            "detail": str(detail_path),
            "daily_summary": str(daily_path),
            "sku_summary": str(sku_path),
            **top_outputs,
        },
        "notes": [
            "Diagnostics score the frozen shadow forecast against available actuals.",
            "If actuals are partial, rerun after the window closes for final sale-window scores.",
            "Inventory annotations are based on mirrored pickface snapshots and may not cover every forecast date.",
        ],
    }
    metadata_path.write_text(json.dumps(diagnostic_metadata, indent=2), encoding="utf-8")
    print(json.dumps(diagnostic_metadata["rows"], indent=2))
    print(f"Daily summary: {daily_path}")
    print(f"SKU summary:   {sku_path}")


if __name__ == "__main__":
    main()
