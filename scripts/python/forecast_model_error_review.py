"""Review independent champion forecast errors against corporate forecast.

This report is for deciding where the independent model is better/worse than
corporate and where the remaining misses are concentrated.  It reads a
champion candidate SKU/day Parquet output and writes compact CSV review files.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_INPUT = Path(
    "Output/ForecastAccuracy/model/champion_candidate_independent_shadow/champion_sku_day_forecast.parquet"
)
DEFAULT_OUTPUT_DIR = Path("Output/ForecastAccuracy/model/champion_candidate_independent_shadow/review")
TARGET_COLUMN = "SoldUnits"
MODEL_COLUMN = "SelectedForecastQty"
CORPORATE_COLUMN = "CorporateForecastQty"
REVIEW_FORECAST_COLUMNS = [
    MODEL_COLUMN,
    CORPORATE_COLUMN,
    "Recent7BaselineQty",
    "Recent28BaselineQty",
]
SEGMENT_COLUMNS = ["Division", "Department", "Class", "Velocity", "HasSkuPDLPromotion"]
FAMILY_COLUMNS = ["Division", "Department", "Class", "Item", "Color"]
REQUIRED_AX_COLUMNS = [
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
    parser = argparse.ArgumentParser(description="Review champion forecast errors.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-n", type=int, default=250)
    return parser.parse_args()


def safe_divide(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def metric_row(df: pd.DataFrame, forecast_col: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    actual = float(df[TARGET_COLUMN].sum())
    forecast = float(df[forecast_col].sum())
    abs_error = float((df[forecast_col] - df[TARGET_COLUMN]).abs().sum())
    return {
        **(extra or {}),
        "ForecastName": forecast_col,
        "Rows": int(len(df)),
        "SKUs": int(df["SKU"].nunique()) if "SKU" in df.columns else 0,
        "ActualUnits": actual,
        "ForecastUnits": forecast,
        "BiasUnits": forecast - actual,
        "BiasPct": safe_divide(forecast - actual, actual),
        "WAPE": safe_divide(abs_error, actual),
    }


def overall_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows = [metric_row(df, col) for col in REVIEW_FORECAST_COLUMNS if col in df.columns]
    return pd.DataFrame(rows).sort_values(["WAPE", "BiasPct"])


def grouped_comparison(df: pd.DataFrame, group_cols: list[str], top_n: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, group in df.groupby(group_cols, dropna=False):
        key_values = key if isinstance(key, tuple) else (key,)
        base = dict(zip(group_cols, key_values, strict=False))
        model = metric_row(group, MODEL_COLUMN, base)
        corporate = metric_row(group, CORPORATE_COLUMN, base)
        rows.append(
            {
                **base,
                "Rows": model["Rows"],
                "SKUs": model["SKUs"],
                "ActualUnits": model["ActualUnits"],
                "ModelForecastUnits": model["ForecastUnits"],
                "ModelBiasUnits": model["BiasUnits"],
                "ModelBiasPct": model["BiasPct"],
                "ModelWAPE": model["WAPE"],
                "CorporateForecastUnits": corporate["ForecastUnits"],
                "CorporateBiasUnits": corporate["BiasUnits"],
                "CorporateBiasPct": corporate["BiasPct"],
                "CorporateWAPE": corporate["WAPE"],
                "ModelWAPEAdvantage": corporate["WAPE"] - model["WAPE"],
                "ModelAbsErrorAdvantageUnits": abs(corporate["BiasUnits"]) - abs(model["BiasUnits"]),
            }
        )
    out = pd.DataFrame(rows)
    out["AbsModelBiasUnits"] = out["ModelBiasUnits"].abs()
    out["AbsWAPEAdvantage"] = out["ModelWAPEAdvantage"].abs()
    return out.sort_values(["ActualUnits", "AbsWAPEAdvantage"], ascending=[False, False]).head(top_n)


def sku_comparison(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    attrs = [col for col in [*FAMILY_COLUMNS, "Size", "Velocity", "SlotTier", "HasSkuPDLPromotion"] if col in df.columns]
    for sku, group in df.groupby("SKU", dropna=False):
        model = metric_row(group, MODEL_COLUMN)
        corporate = metric_row(group, CORPORATE_COLUMN)
        attr_values = {col: group[col].dropna().iloc[0] if group[col].notna().any() else "" for col in attrs}
        rows.append(
            {
                "SKU": sku,
                **attr_values,
                "ActualUnits": model["ActualUnits"],
                "ModelForecastUnits": model["ForecastUnits"],
                "ModelBiasUnits": model["BiasUnits"],
                "ModelBiasPct": model["BiasPct"] if model["ActualUnits"] else np.nan,
                "ModelWAPE": model["WAPE"],
                "CorporateForecastUnits": corporate["ForecastUnits"],
                "CorporateBiasUnits": corporate["BiasUnits"],
                "CorporateBiasPct": corporate["BiasPct"] if corporate["ActualUnits"] else np.nan,
                "CorporateWAPE": corporate["WAPE"],
                "ModelWAPEAdvantage": corporate["WAPE"] - model["WAPE"],
            }
        )
    out = pd.DataFrame(rows)
    out["ModelAbsErrorUnits"] = out["ModelBiasUnits"].abs()
    out["CorporateAbsErrorUnits"] = out["CorporateBiasUnits"].abs()
    out["ModelAbsErrorAdvantageUnits"] = out["CorporateAbsErrorUnits"] - out["ModelAbsErrorUnits"]
    return out.sort_values("ModelAbsErrorUnits", ascending=False).head(top_n)


def missing_attribute_rows(df: pd.DataFrame) -> pd.DataFrame:
    available = [col for col in REQUIRED_AX_COLUMNS if col in df.columns]
    if not available:
        return pd.DataFrame()
    required = df[available].fillna("").astype(str)
    missing = df.loc[~required.ne("").all(axis=1)].copy()
    if missing.empty:
        return pd.DataFrame()
    return (
        missing.groupby("SKU", as_index=False)
        .agg(
            ActualUnits=(TARGET_COLUMN, "sum"),
            ModelForecastUnits=(MODEL_COLUMN, "sum"),
            CorporateForecastUnits=(CORPORATE_COLUMN, "sum"),
            MissingAttributeCount=("SKU", "size"),
            **{col: (col, lambda s: ",".join(sorted(set(s.fillna("").astype(str))))) for col in available},
        )
        .sort_values("ModelForecastUnits", ascending=False)
    )


def main() -> None:
    args = parse_args()
    df = pd.read_parquet(args.input)
    for col in REVIEW_FORECAST_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).clip(lower=0)
    df[TARGET_COLUMN] = pd.to_numeric(df[TARGET_COLUMN], errors="coerce").fillna(0).clip(lower=0)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = overall_summary(df)
    segment = grouped_comparison(df, [col for col in SEGMENT_COLUMNS if col in df.columns], args.top_n)
    family = grouped_comparison(df, [col for col in FAMILY_COLUMNS if col in df.columns], args.top_n)
    sku = sku_comparison(df, args.top_n)
    missing = missing_attribute_rows(df)

    summary.to_csv(args.output_dir / "forecast_review_overall.csv", index=False)
    segment.to_csv(args.output_dir / "forecast_review_by_segment_top.csv", index=False)
    family.to_csv(args.output_dir / "forecast_review_by_item_color_top.csv", index=False)
    sku.to_csv(args.output_dir / "forecast_review_by_sku_top.csv", index=False)
    missing.to_csv(args.output_dir / "forecast_review_missing_ax_attributes.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "output_dir": str(args.output_dir),
        "rows": int(len(df)),
        "skus": int(df["SKU"].nunique()),
        "review_forecasts": [col for col in REVIEW_FORECAST_COLUMNS if col in df.columns],
        "outputs": {
            "overall": str(args.output_dir / "forecast_review_overall.csv"),
            "segment": str(args.output_dir / "forecast_review_by_segment_top.csv"),
            "item_color": str(args.output_dir / "forecast_review_by_item_color_top.csv"),
            "sku": str(args.output_dir / "forecast_review_by_sku_top.csv"),
            "missing_ax_attributes": str(args.output_dir / "forecast_review_missing_ax_attributes.csv"),
        },
    }
    with (args.output_dir / "forecast_review_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print(summary.to_string(index=False))
    print(f"Wrote forecast review outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
