"""Audit promo and newness coverage for independent forecast misses.

This script parses forecast outputs, merges them with promotion logs from the
SKU-day panel parts, and pulls product attributes (like launch dates) from the
corporate Product Info sheet ("Product Info for BRG*.xlsx").

It computes error metrics (Bias, WAPE) across different dimensions:
1. Product families (Division, Department, Class, Item, Color)
2. Newness cohorts based on days elapsed since the "Go Live Date"
3. PDL (promotion/discount) event status

These slices help isolate whether forecasting errors are concentrated in newly
launched styles, promotional periods, or specific categories.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# Default output/input paths corresponding to standard forecast pipeline structures
DEFAULT_FORECAST = Path(
    "Output/ForecastAccuracy/model/champion_candidate_independent_shadow/champion_sku_day_forecast.parquet"
)
DEFAULT_PANEL = Path("Output/ForecastAccuracy/model/model_sku_day_panel_parts")
DEFAULT_PRODUCT_INFO_DIR = Path("Source")
DEFAULT_OUTPUT_DIR = Path("Output/ForecastAccuracy/model/champion_candidate_independent_shadow/review")

# Standard forecast target and forecast comparison columns
TARGET_COLUMN = "SoldUnits"
MODEL_COLUMN = "SelectedForecastQty"
CORPORATE_COLUMN = "CorporateForecastQty"

# Hierarchical levels to audit forecast performance
FAMILY_COLUMNS = ["Division", "Department", "Class", "Item", "Color"]

# Subset of panel columns required to audit promotional effects
PANEL_AUDIT_COLUMNS = [
    "SKU",
    "Date",
    "LatestProductSnapshotDate",
    "LatestProductForecastStartDate",
    "OrderedUnits",
    "HasObservedDiscount",
    "DiscountedLineCount",
    "MaxObservedDiscountPct",
    "WeightedDiscountPctVsMSRP",
    "WeightedDiscountPctVsSalesPrice",
    "HasPDLPromotion",
    "HasCouponPromotion",
    "HasAnyPromotion",
    "coupon_max_discount_percent",
    "pdl_active_events",
    "pdl_offer_cc_count",
    "pdl_style_count",
    "pdl_sku_offer_rows",
    "pdl_sku_active_events",
    "pdl_sku_distinct_offer_colors",
    "pdl_sku_max_discount_pct",
    "pdl_sku_avg_discount_pct",
    "pdl_sku_min_promo_price",
    "pdl_sku_total_avail_inv",
    "pdl_sku_total_avail_plus_oo",
    "pdl_sku_lw_unit_sales",
    "pdl_sku_has_markdown",
    "pdl_sku_has_final_sale",
    "pdl_sku_has_tier1_recommendation",
    "pdl_sku_primary_sheet_type",
    "pdl_sku_primary_scope",
    "pdl_sku_primary_event_name",
    "HasSkuPDLPromotion",
]


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the promo and newness audit script.

    Returns:
        argparse.Namespace: Populated argument values containing file and directory paths.
    """
    parser = argparse.ArgumentParser(description="Audit promo/newness coverage on forecast misses.")
    parser.add_argument(
        "--forecast",
        type=Path,
        default=DEFAULT_FORECAST,
        help="Path to the champion forecast Parquet file.",
    )
    parser.add_argument(
        "--panel",
        type=Path,
        default=DEFAULT_PANEL,
        help="Directory containing the model SKU-day panel Parquet partitions.",
    )
    parser.add_argument(
        "--product-info-dir",
        type=Path,
        default=DEFAULT_PRODUCT_INFO_DIR,
        help="Directory containing Product Info Excel sheets.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where output audit CSVs and metadata will be saved.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=250,
        help="Number of worst-performing groups to output in the family report.",
    )
    parser.add_argument(
        "--skip-product-info",
        action="store_true",
        help="If set, skips reading the product info sheets and checking go-live dates.",
    )
    return parser.parse_args()


def normalize_sku(value: Any) -> str:
    """Clean and normalize SKU identifiers to ensure join consistency.

    Args:
        value (Any): Raw SKU values from excel, database, or parquet.

    Returns:
        str: Upper-cased, whitespace-stripped string or empty string.
    """
    if pd.isna(value):
        return ""
    return str(value).strip().upper()


def latest_product_info_file(directory: Path) -> Path | None:
    """Find the latest modified Product Info sheet in the target directory.

    Args:
        directory (Path): Source directory to search for Product Info.

    Returns:
        Path | None: Path to the latest file, or None if no matching files exist.
    """
    files = sorted(directory.glob("Product Info for BRG*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def load_product_info_attrs(directory: Path) -> tuple[pd.DataFrame, str | None]:
    """Load SKU go-live dates and status attributes from the latest Product Info file.

    Args:
        directory (Path): Directory where Product Info Excel files reside.

    Returns:
        tuple[pd.DataFrame, str | None]: A dataframe with attributes and the path of the parsed file.
    """
    path = latest_product_info_file(directory)
    if path is None:
        return pd.DataFrame(columns=["SKU", "GoLiveDate", "ProductStatus", "ProductStatusDate"]), None

    # Skip first 3 lines as headers/metadata in the standard corporate spreadsheet format
    df = pd.read_excel(path, sheet_name="Product Attributes", header=3, engine="calamine")
    df = df.iloc[1:].copy().reset_index(drop=True)

    # Slice left-most columns related to classification and launch dates
    left = df[df.columns[:9]].copy()
    left.columns = [
        "Offer",
        "SKU",
        "DivisionPI",
        "DepartmentPI",
        "ClassPI",
        "KeyCategoryViewPI",
        "SizeGroupPI",
        "GoLiveDate",
        "OfferCount",
    ]
    left["SKU"] = left["SKU"].map(normalize_sku)
    left = left.loc[left["SKU"].ne("")].copy()
    left["GoLiveDate"] = pd.to_datetime(left["GoLiveDate"], errors="coerce").dt.normalize()
    left = left.drop_duplicates("SKU", keep="first")

    # Slice status and lifecycle tracking attributes if available
    if len(df.columns) > 12:
        status = df[[df.columns[10], df.columns[11], df.columns[12]]].copy()
        status.columns = ["SKU", "ProductStatus", "ProductStatusDate"]
        status["SKU"] = status["SKU"].map(normalize_sku)
        status = status.loc[status["SKU"].ne("")].copy()
        status["ProductStatusDate"] = pd.to_datetime(status["ProductStatusDate"], errors="coerce").dt.normalize()
        status = status.drop_duplicates("SKU", keep="first")
    else:
        status = pd.DataFrame(columns=["SKU", "ProductStatus", "ProductStatusDate"])

    attrs = left[["SKU", "GoLiveDate"]].merge(status, on="SKU", how="left")
    return attrs, str(path)


def read_panel_audit_rows(panel_path: Path, forecast: pd.DataFrame) -> pd.DataFrame:
    """Read promo and discount columns from the panel partition matching the forecast window.

    This restricts panel reading only to SKUs and date ranges present in the forecast
    to manage memory footprints.

    Args:
        panel_path (Path): Path to the panel directory.
        forecast (pd.DataFrame): Dataframe containing the forecast records to join.

    Returns:
        pd.DataFrame: Merged panel attributes for matching SKU and Date records.
    """
    schema = set(pq.read_schema(panel_path).names)
    columns = [col for col in PANEL_AUDIT_COLUMNS if col in schema]
    panel = pd.read_parquet(panel_path, columns=columns)
    panel["Date"] = pd.to_datetime(panel["Date"], errors="coerce").dt.normalize()
    skus = set(forecast["SKU"].map(normalize_sku).unique())
    start = forecast["Date"].min()
    end = forecast["Date"].max()
    panel["SKU"] = panel["SKU"].map(normalize_sku)
    return panel.loc[panel["SKU"].isin(skus) & panel["Date"].between(start, end)].copy()


def bool_rate(series: pd.Series) -> float:
    """Calculate the fraction of true flags in a pandas Series.

    Args:
        series (pd.Series): Boolean or numeric series to average.

    Returns:
        float: Percentage of True records.
    """
    if series.empty:
        return 0.0
    return float(series.fillna(False).astype(bool).mean())


def joined_event_names(series: pd.Series, limit: int = 5) -> str:
    """Consolidate distinct event/promotional sheet names into a single string separator.

    Args:
        series (pd.Series): Event name text column.
        limit (int, optional): Max number of distinct event names to report. Defaults to 5.

    Returns:
        str: Separated list of top unique names.
    """
    names = [str(value).strip() for value in series.dropna().unique() if str(value).strip()]
    return " | ".join(sorted(names)[:limit])


def classify_newness(days_since_go_live: float | None) -> str:
    """Classify product lifecycle stage relative to its launch/go-live date.

    Args:
        days_since_go_live (float | None): Days between record date and go-live date.

    Returns:
        str: Cohort classification tag.
    """
    if days_since_go_live is None or pd.isna(days_since_go_live):
        return "UnknownGoLive"
    if days_since_go_live < 0:
        return "PreGoLive"
    if days_since_go_live <= 30:
        return "New0To30"
    if days_since_go_live <= 90:
        return "New31To90"
    return "Established"


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add cohort groups and normalize numeric metrics in the merged audit dataframe.

    Args:
        df (pd.DataFrame): Dataframe containing merged forecast, panel, and product info.

    Returns:
        pd.DataFrame: Enriched dataframe with newness buckets and normalized targets.
    """
    df = df.copy()
    for col in [TARGET_COLUMN, MODEL_COLUMN, CORPORATE_COLUMN, "OrderedUnits"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["GoLiveDate", "LatestProductForecastStartDate"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.normalize()
    if "GoLiveDate" in df.columns:
        df["DaysSinceGoLive"] = (df["Date"] - df["GoLiveDate"]).dt.days
        df["NewnessBucket"] = df["DaysSinceGoLive"].map(classify_newness)
    else:
        df["DaysSinceGoLive"] = np.nan
        df["NewnessBucket"] = "UnknownGoLive"
    if "LatestProductForecastStartDate" in df.columns:
        df["DaysSinceProductForecastStart"] = (df["Date"] - df["LatestProductForecastStartDate"]).dt.days
    else:
        df["DaysSinceProductForecastStart"] = np.nan

    # Parse Item and Color from standard SKU format if missing
    if "Item" not in df.columns or "Color" not in df.columns:
        parsed = df["SKU"].astype(str).str.extract(r"^(?P<Item>[^-]+)-(?P<Color>[^-]+)-")
        for col in ["Item", "Color"]:
            if col not in df.columns:
                df[col] = parsed[col].fillna("")
    return df


def family_audit(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """Group forecast errors at family hierarchies to diagnose top miss contributors.

    Args:
        df (pd.DataFrame): Enriched SKU-day observations.
        top_n (int): Number of worst-performing rows to slice.

    Returns:
        pd.DataFrame: Grouped metrics sorted by bias.
    """
    rows: list[dict[str, Any]] = []
    group_cols = [col for col in FAMILY_COLUMNS if col in df.columns]
    for key, group in df.groupby(group_cols, dropna=False):
        key_values = key if isinstance(key, tuple) else (key,)
        actual = float(group[TARGET_COLUMN].sum())
        model = float(group[MODEL_COLUMN].sum())
        corporate = float(group[CORPORATE_COLUMN].sum())
        model_abs_error = float((group[MODEL_COLUMN] - group[TARGET_COLUMN]).abs().sum())
        corporate_abs_error = float((group[CORPORATE_COLUMN] - group[TARGET_COLUMN]).abs().sum())
        rows.append(
            {
                **dict(zip(group_cols, key_values, strict=False)),
                "Rows": int(len(group)),
                "SKUs": int(group["SKU"].nunique()),
                "ActualUnits": actual,
                "ModelForecastUnits": model,
                "ModelBiasUnits": model - actual,
                "ModelBiasPct": (model - actual) / actual if actual else np.nan,
                "ModelWAPE": model_abs_error / actual if actual else 0.0,
                "CorporateForecastUnits": corporate,
                "CorporateBiasUnits": corporate - actual,
                "CorporateWAPE": corporate_abs_error / actual if actual else 0.0,
                "SkuPDLPromoRowRate": bool_rate(group.get("HasSkuPDLPromotion", pd.Series(dtype=bool))),
                "AnyPromoRowRate": bool_rate(group.get("HasAnyPromotion", pd.Series(dtype=bool))),
                "ObservedDiscountRowRate": bool_rate(group.get("HasObservedDiscount", pd.Series(dtype=bool))),
                "MaxSkuPDLDiscountPct": float(
                    pd.to_numeric(group.get("pdl_sku_max_discount_pct", 0), errors="coerce").max() or 0
                ),
                "AvgObservedDiscountPctVsMSRP": float(
                    pd.to_numeric(group.get("WeightedDiscountPctVsMSRP", 0), errors="coerce").fillna(0).mean()
                ),
                "SkuPDLActiveEvents": float(
                    pd.to_numeric(group.get("pdl_sku_active_events", 0), errors="coerce").fillna(0).sum()
                ),
                "PdlPrimarySheetTypes": joined_event_names(group.get("pdl_sku_primary_sheet_type", pd.Series(dtype=str))),
                "PdlPrimaryEventNames": joined_event_names(group.get("pdl_sku_primary_event_name", pd.Series(dtype=str))),
                "GoLiveMin": str(group["GoLiveDate"].min().date()) if group["GoLiveDate"].notna().any() else "",
                "GoLiveMax": str(group["GoLiveDate"].max().date()) if group["GoLiveDate"].notna().any() else "",
                "MedianDaysSinceGoLive": float(group["DaysSinceGoLive"].median())
                if group["DaysSinceGoLive"].notna().any()
                else np.nan,
                "NewnessBuckets": joined_event_names(group["NewnessBucket"]),
                "ProductStatuses": joined_event_names(group.get("ProductStatus", pd.Series(dtype=str))),
            }
        )
    out = pd.DataFrame(rows)
    out["AbsModelBiasUnits"] = out["ModelBiasUnits"].abs()
    return out.sort_values(["ModelBiasUnits", "ActualUnits"], ascending=[True, False]).head(top_n)


def newness_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Summarize error performance by product age cohort and promotional status.

    Args:
        df (pd.DataFrame): Enriched observations.

    Returns:
        pd.DataFrame: WAPE and bias summarized for each cohort-promotion slice.
    """
    rows: list[dict[str, Any]] = []
    for key, group in df.groupby(["NewnessBucket", "HasSkuPDLPromotion"], dropna=False):
        bucket, promo = key
        actual = float(group[TARGET_COLUMN].sum())
        model = float(group[MODEL_COLUMN].sum())
        corporate = float(group[CORPORATE_COLUMN].sum())
        rows.append(
            {
                "NewnessBucket": bucket,
                "HasSkuPDLPromotion": bool(promo),
                "Rows": int(len(group)),
                "SKUs": int(group["SKU"].nunique()),
                "ActualUnits": actual,
                "ModelForecastUnits": model,
                "ModelBiasPct": (model - actual) / actual if actual else np.nan,
                "ModelWAPE": float((group[MODEL_COLUMN] - group[TARGET_COLUMN]).abs().sum() / actual) if actual else 0.0,
                "CorporateForecastUnits": corporate,
                "CorporateBiasPct": (corporate - actual) / actual if actual else np.nan,
                "CorporateWAPE": float((group[CORPORATE_COLUMN] - group[TARGET_COLUMN]).abs().sum() / actual)
                if actual
                else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("ActualUnits", ascending=False)


def main() -> None:
    """Read forecast artifacts, merge promo/newness metadata, and save audit reports."""
    args = parse_args()
    forecast = pd.read_parquet(args.forecast)
    forecast["SKU"] = forecast["SKU"].map(normalize_sku)
    forecast["Date"] = pd.to_datetime(forecast["Date"], errors="coerce").dt.normalize()

    panel = read_panel_audit_rows(args.panel, forecast)
    enriched = forecast.merge(panel, on=["SKU", "Date"], how="left", suffixes=("", "_Panel"))

    product_info_path = None
    if not args.skip_product_info:
        attrs, product_info_path = load_product_info_attrs(args.product_info_dir)
        enriched = enriched.merge(attrs, on="SKU", how="left")

    enriched = add_derived_columns(enriched)
    families = family_audit(enriched, args.top_n)
    newness = newness_summary(enriched)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    family_path = args.output_dir / "forecast_promo_newness_by_item_color_top.csv"
    newness_path = args.output_dir / "forecast_promo_newness_by_newness_bucket.csv"
    metadata_path = args.output_dir / "forecast_promo_newness_metadata.json"
    families.to_csv(family_path, index=False)
    newness.to_csv(newness_path, index=False)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "forecast": str(args.forecast),
        "panel": str(args.panel),
        "product_info_file": product_info_path,
        "output_dir": str(args.output_dir),
        "rows": int(len(enriched)),
        "skus": int(enriched["SKU"].nunique()),
        "date_range": [str(enriched["Date"].min().date()), str(enriched["Date"].max().date())],
        "outputs": {
            "family_audit": str(family_path),
            "newness_summary": str(newness_path),
        },
    }
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print(families.head(20).to_string(index=False))
    print(f"Wrote promo/newness audit outputs to {args.output_dir}")


if __name__ == "__main__":
    main()


