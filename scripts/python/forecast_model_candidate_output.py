"""Build a shadow AX-style forecast candidate from the champion sklearn model.

This script is intentionally a shadow/export tool, not an AX upload step.  It
trains the current champion model on the model panel, scores a historical
forecast window, and writes both SKU/day predictions and an FD1-FD14 shaped CSV
so Operations can compare the model against actuals and the existing corporate
forecast contract before any production handoff.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_compare_sklearn import require_sklearn, run_single_stage, split_panel  # noqa: E402
from forecast_model_train import (  # noqa: E402
    BASELINE_COLUMNS,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    TARGET_COLUMN,
    configure_threads,
    evaluate_predictions,
    load_panel,
)


DEFAULT_OUTPUT_DIR = MODEL_DIR / "champion_candidate"
DEFAULT_MODEL = "hgb_absolute_log"
AX_OUTPUT_COLUMNS = [
    "Division",
    "Department",
    "Class",
    "Subclass",
    "KeyCategoryView",
    "SKU",
    "Item",
    "Color",
    "Size",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "ReplenishmentThreshold",
    "PutawayIndicator",
    "ProductStatus",
    "ProductStatusDate",
    "ProductStage",
    "ReturnAction",
    "ReturnActionDate",
    "NVARExpectedQty",
    "ForecastStartDate",
    "FD1",
    "FD2",
    "FD3",
    "FD4",
    "FD5",
    "FD6",
    "FD7",
    "FD8",
    "FD9",
    "FD10",
    "FD11",
    "FD12",
    "FD13",
    "FD14",
]
ATTRIBUTE_COLUMNS = [
    "Division",
    "Department",
    "Class",
    "KeyCategoryView",
    "Item",
    "Color",
    "Size",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "ReplenishmentThreshold",
    "PutawayIndicator",
]
REQUIRED_AX_ATTRIBUTE_COLUMNS = [
    "Division",
    "Department",
    "Class",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "PutawayIndicator",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a shadow AX-style champion forecast.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--forecast-start", help="Inclusive forecast start. Defaults to panel max date minus 13 days.")
    parser.add_argument("--forecast-end", help="Inclusive forecast end. Defaults to forecast start plus --forecast-days.")
    parser.add_argument("--forecast-days", type=int, default=14)
    parser.add_argument("--model", choices=[DEFAULT_MODEL], default=DEFAULT_MODEL)
    parser.add_argument(
        "--forecast-source",
        choices=["raw", "calibrated"],
        default="raw",
        help="Which model output to place into FD1-FD14.",
    )
    parser.add_argument("--max-train-rows", type=int, default=500_000)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=180)
    parser.add_argument("--learning-rate", type=float, default=0.06)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument("--calibration-days", type=int, default=28)
    parser.add_argument(
        "--calibration-mode",
        choices=[
            "global",
            "sku-promo",
            "category",
            "category-velocity",
            "category-velocity-promo",
            "none",
        ],
        default="category-velocity-promo",
    )
    parser.add_argument("--calibration-min-rows", type=int, default=500)
    parser.add_argument("--calibration-min-actual-units", type=float, default=50.0)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--exclude-corporate-features",
        action="store_true",
        help="Exclude corporate forecast fields from model features; keep them as comparison baselines.",
    )
    parser.add_argument(
        "--include-product-identity-features",
        action="store_true",
        help="Add Item, Color, Size, and ItemColor categorical features for experimental family-aware models.",
    )
    parser.add_argument(
        "--min-fd14-units",
        type=float,
        default=0.01,
        help="Drop AX-shaped rows whose selected FD1-FD14 total is below this value.",
    )
    parser.add_argument(
        "--allow-missing-ax-attributes",
        action="store_true",
        help="Keep rows with missing AX hierarchy/slotting fields in the AX-shaped shadow CSV.",
    )
    return parser.parse_args()


def resolve_forecast_window(panel: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.Timestamp, pd.Timestamp]:
    if args.forecast_start:
        start = pd.Timestamp(args.forecast_start)
    else:
        start = panel[DATE_COLUMN].max() - pd.Timedelta(days=args.forecast_days - 1)
    if args.forecast_end:
        end = pd.Timestamp(args.forecast_end)
    else:
        end = start + pd.Timedelta(days=args.forecast_days - 1)
    if start > end:
        raise ValueError("Forecast start is after forecast end.")
    return start.normalize(), end.normalize()


def load_export_attributes(panel_path: Path) -> pd.DataFrame:
    schema_columns = set(pq.read_schema(panel_path).names)
    read_columns = ["SKU", *[col for col in ATTRIBUTE_COLUMNS if col in schema_columns]]
    attrs = pd.read_parquet(panel_path, columns=read_columns)
    agg_spec = {col: first_non_null for col in read_columns if col != "SKU"}
    return attrs.groupby("SKU", as_index=False).agg(agg_spec)


def add_export_attributes(panel: pd.DataFrame, panel_path: Path) -> pd.DataFrame:
    attrs = load_export_attributes(panel_path)
    missing_export_columns = [col for col in attrs.columns if col != "SKU" and col not in panel.columns]
    if not missing_export_columns:
        return panel
    return panel.merge(attrs[["SKU", *missing_export_columns]], on="SKU", how="left")


def add_window_args(args: argparse.Namespace, start: pd.Timestamp, end: pd.Timestamp) -> argparse.Namespace:
    args.holdout_start = str(start.date())
    args.holdout_end = str(end.date())
    args.holdout_days = 0
    return args


def first_non_null(series: pd.Series) -> Any:
    values = series.dropna()
    return values.iloc[0] if not values.empty else ""


def build_sku_day(scored: pd.DataFrame, model_name: str, forecast_source: str) -> pd.DataFrame:
    raw_col = f"{model_name}ForecastQty"
    cal_col = f"{model_name}CalibratedForecastQty"
    selected_col = raw_col if forecast_source == "raw" else cal_col
    keep_cols = [
        DATE_COLUMN,
        "SKU",
        TARGET_COLUMN,
        raw_col,
        cal_col,
        selected_col,
        "CorporateForecastQty",
        *[col for col in BASELINE_COLUMNS if col in scored.columns],
        *[col for col in ATTRIBUTE_COLUMNS if col in scored.columns],
        "HasSkuPDLPromotion",
        "HasAnyPromotion",
        "InventoryNetAvailablePhysicalLag1",
        "InboundNext30Units",
        "SellableFloorSupplyUnitsLag1",
    ]
    keep_cols = list(dict.fromkeys([col for col in keep_cols if col in scored.columns]))
    sku_day = scored.loc[:, keep_cols].copy()
    sku_day = sku_day.rename(columns={selected_col: "SelectedForecastQty"})
    sku_day["SelectedForecastSource"] = forecast_source
    if "ReplenishmentThreshold" in sku_day.columns:
        sku_day["ReplenishmentThreshold"] = pd.to_numeric(
            sku_day["ReplenishmentThreshold"],
            errors="coerce",
        ).fillna(0)
    for col in [col for col in ATTRIBUTE_COLUMNS if col in sku_day.columns and col != "ReplenishmentThreshold"]:
        sku_day[col] = sku_day[col].fillna("").astype(str)
    return sku_day


def build_ax_shape(
    sku_day: pd.DataFrame,
    start: pd.Timestamp,
    min_fd14_units: float,
    require_ax_attributes: bool,
) -> tuple[pd.DataFrame, int]:
    df = sku_day.copy()
    df["DayOffset"] = (df[DATE_COLUMN] - start).dt.days + 1
    df = df.loc[df["DayOffset"].between(1, 14)].copy()
    df["FDColumn"] = "FD" + df["DayOffset"].astype(int).astype(str)

    pivot = (
        df.pivot_table(index="SKU", columns="FDColumn", values="SelectedForecastQty", aggfunc="sum", fill_value=0.0)
        .reset_index()
        .rename_axis(None, axis=1)
    )
    for idx in range(1, 15):
        col = f"FD{idx}"
        if col not in pivot.columns:
            pivot[col] = 0.0

    attrs = (
        df.groupby("SKU", as_index=False)
        .agg({col: first_non_null for col in ATTRIBUTE_COLUMNS if col in df.columns})
    )
    ax = attrs.merge(pivot, on="SKU", how="right")
    ax["FD14Total"] = ax[[f"FD{idx}" for idx in range(1, 15)]].sum(axis=1)
    ax = ax.loc[ax["FD14Total"].ge(min_fd14_units)].copy()
    rows_before_attribute_filter = len(ax)
    if require_ax_attributes:
        for col in REQUIRED_AX_ATTRIBUTE_COLUMNS:
            if col not in ax.columns:
                ax[col] = ""
        required = ax[REQUIRED_AX_ATTRIBUTE_COLUMNS].fillna("").astype(str)
        ax = ax.loc[required.ne("").all(axis=1)].copy()
    dropped_missing_attributes = rows_before_attribute_filter - len(ax)

    constant_defaults: dict[str, Any] = {
        "Subclass": "",
        "ProductStatus": "",
        "ProductStatusDate": "",
        "ProductStage": "",
        "ReturnAction": "",
        "ReturnActionDate": "",
        "NVARExpectedQty": 0,
        "ForecastStartDate": str(start.date()),
    }
    for col, value in constant_defaults.items():
        if col not in ax.columns:
            ax[col] = value
    for col in AX_OUTPUT_COLUMNS:
        if col not in ax.columns:
            ax[col] = ""
    for idx in range(1, 15):
        ax[f"FD{idx}"] = pd.to_numeric(ax[f"FD{idx}"], errors="coerce").fillna(0).round(4)
    ax = ax.sort_values(["Division", "SKU"], kind="mergesort").reset_index(drop=True)
    return ax[AX_OUTPUT_COLUMNS], dropped_missing_attributes


def build_sku_summary(sku_day: pd.DataFrame) -> pd.DataFrame:
    agg_spec: dict[str, tuple[str, str]] = {
        "ActualUnits": (TARGET_COLUMN, "sum"),
        "SelectedForecastUnits": ("SelectedForecastQty", "sum"),
    }
    for col in ["CorporateForecastQty", "Recent7BaselineQty", "Recent28BaselineQty"]:
        if col in sku_day.columns:
            agg_spec[col.replace("Qty", "Units")] = (col, "sum")
    summary = sku_day.groupby("SKU", as_index=False).agg(**agg_spec)
    summary["ForecastMinusActual"] = summary["SelectedForecastUnits"] - summary["ActualUnits"]
    summary["AbsError"] = summary["ForecastMinusActual"].abs()
    summary["BiasPct"] = np.where(
        summary["ActualUnits"].gt(0),
        summary["ForecastMinusActual"] / summary["ActualUnits"],
        np.nan,
    )
    return summary.sort_values("AbsError", ascending=False)


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)
    ml = require_sklearn()

    panel = add_export_attributes(load_panel(args.panel, args.start_date), args.panel)
    forecast_start, forecast_end = resolve_forecast_window(panel, args)
    args = add_window_args(args, forecast_start, forecast_end)

    train, calibration, holdout, _, _ = split_panel(panel, args)
    scored, calibration_factors = run_single_stage(args.model, ml, train, calibration, holdout, args)
    sku_day = build_sku_day(scored, args.model, args.forecast_source)
    ax_shape, dropped_missing_attributes = build_ax_shape(
        sku_day,
        forecast_start,
        args.min_fd14_units,
        require_ax_attributes=not args.allow_missing_ax_attributes,
    )
    sku_summary = build_sku_summary(sku_day)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    forecast_cols = [
        f"{args.model}ForecastQty",
        f"{args.model}CalibratedForecastQty",
        *[col for col in BASELINE_COLUMNS if col in scored.columns],
    ]
    summary = evaluate_predictions(scored, forecast_cols)
    by_segment_cols = [col for col in ["Division", "Department", "Class", "Velocity", "HasSkuPDLPromotion"] if col in scored.columns]
    by_segment = evaluate_predictions(scored, forecast_cols, by_segment_cols)

    sku_day.to_parquet(args.output_dir / "champion_sku_day_forecast.parquet", index=False, compression="zstd")
    sku_day.to_csv(args.output_dir / "champion_sku_day_forecast_sample.csv", index=False)
    ax_shape.to_csv(args.output_dir / "champion_ax_forward_demand_shadow.csv", index=False)
    summary.to_csv(args.output_dir / "champion_backtest_summary.csv", index=False)
    by_segment.sort_values(["ForecastName", "ActualUnits"], ascending=[True, False]).head(2500).to_csv(
        args.output_dir / "champion_backtest_by_segment_top2500.csv",
        index=False,
    )
    calibration_factors.to_csv(args.output_dir / "champion_calibration_factors.csv", index=False)
    sku_summary.head(5000).to_csv(args.output_dir / "champion_largest_sku_errors.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "output_dir": str(args.output_dir),
        "model": args.model,
        "forecast_source": args.forecast_source,
        "forecast_window": [str(forecast_start.date()), str(forecast_end.date())],
        "threads": args.threads,
        "max_train_rows": args.max_train_rows,
        "max_iter": args.max_iter,
        "calibration_mode": args.calibration_mode,
        "exclude_corporate_features": args.exclude_corporate_features,
        "include_product_identity_features": args.include_product_identity_features,
        "calibration_rows": int(len(calibration)),
        "train_rows": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "shadow_ax_rows": int(len(ax_shape)),
        "shadow_ax_rows_dropped_missing_attributes": int(dropped_missing_attributes),
        "outputs": {
            "sku_day_parquet": str(args.output_dir / "champion_sku_day_forecast.parquet"),
            "sku_day_sample_csv": str(args.output_dir / "champion_sku_day_forecast_sample.csv"),
            "ax_shadow_csv": str(args.output_dir / "champion_ax_forward_demand_shadow.csv"),
            "summary": str(args.output_dir / "champion_backtest_summary.csv"),
            "segment_summary": str(args.output_dir / "champion_backtest_by_segment_top2500.csv"),
            "calibration_factors": str(args.output_dir / "champion_calibration_factors.csv"),
            "largest_sku_errors": str(args.output_dir / "champion_largest_sku_errors.csv"),
        },
    }
    with (args.output_dir / "champion_candidate_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print(summary.to_string(index=False))
    print(f"Wrote champion candidate outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
