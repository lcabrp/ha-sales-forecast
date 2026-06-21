"""Build SKU/day promotion features from PDL offer rows.

The promotion extractor preserves the workbook rows at offer/style-color grain.
Forecast modeling needs SKU/day features.  This script bridges that gap by:

1. parsing PDL offer codes such as ``67513-43Q`` into item/color keys,
2. joining those keys to the known SKU universe from forecast snapshots, and
3. expanding event windows into SKU/day feature rows.

This is intentionally conservative.  Rows that cannot be tied to an item/color
code are left out of the SKU-specific feature table; broad sitewide/date-level
promotion signals already live in ``combined_daily_promo_features.parquet``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


FORECAST_ACCURACY_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy"
PROMOTIONS_DIR = FORECAST_ACCURACY_DIR / "promotions"
HISTORY_PARQUET_DIR = FORECAST_ACCURACY_DIR / "history" / "parquet"

PDL_OFFER_ROWS_PATH = PROMOTIONS_DIR / "pdl_offer_rows.parquet"
FORECAST_SNAPSHOT_PATH = HISTORY_PARQUET_DIR / "forecast_sku_snapshot.parquet"
DEFAULT_OUTPUT_DIR = PROMOTIONS_DIR

OFFER_CODE_RE = re.compile(r"^\s*([A-Za-z0-9]+)(?:-+([A-Za-z0-9]+))?(?:-+[A-Za-z0-9]+)?\s*$")
TEXT_NA = {"", "nan", "nat", "none", "<na>"}
OFFER_CODE_COLUMNS = ["offer_cc", "offer", "style_2", "style"]
MODELABLE_SHEET_TYPES = {
    "aggregate_promo_offers",
    "promo_detail",
    "markdown",
    "final_sale",
    "tier1_recommendation",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create SKU/day PDL promotion features for forecast modeling."
    )
    parser.add_argument("--pdl-offer-rows", type=Path, default=PDL_OFFER_ROWS_PATH)
    parser.add_argument("--forecast-snapshot", type=Path, default=FORECAST_SNAPSHOT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", help="Inclusive event date filter, YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Inclusive event date filter, YYYY-MM-DD.")
    parser.add_argument(
        "--max-event-days",
        type=int,
        default=60,
        help="Skip suspiciously long PDL event windows. Date-level coupon features handle long campaigns.",
    )
    parser.add_argument("--sample-rows", type=int, default=5000)
    return parser.parse_args()


def clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in TEXT_NA else text


def normalize_code(value: object) -> str:
    return clean_text(value).upper().replace(" ", "")


def parse_offer_code(*values: object) -> tuple[str, str]:
    """Return an item/color key from the first parseable offer-like value."""
    for value in values:
        text = normalize_code(value)
        if not text:
            continue
        match = OFFER_CODE_RE.match(text)
        if not match:
            continue
        item = match.group(1) or ""
        color = match.group(2) or ""
        if item and color:
            return item, color
    return "", ""


def normalize_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def normalized_discount(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    values = values.where(values.le(1), values / 100.0)
    return values.clip(lower=0, upper=1)


def load_sku_universe(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Forecast snapshot not found: {path}")
    df = pd.read_parquet(path, columns=["SKU", "Item", "Color", "Size", "InferredFileDate"])
    df["InferredFileDate"] = normalize_date(df["InferredFileDate"])
    for col in ["SKU", "Item", "Color", "Size"]:
        df[col] = df[col].map(normalize_code)
    df = df.loc[df["SKU"].ne("") & df["Item"].ne("") & df["Color"].ne("")].copy()
    df = df.sort_values(["SKU", "InferredFileDate"]).drop_duplicates("SKU", keep="last")
    return df[["SKU", "Item", "Color", "Size"]].drop_duplicates()


def load_pdl_rows(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"PDL offer rows not found: {path}")
    columns = [
        "file_id",
        "source_file",
        "sheet_name",
        "sheet_type",
        "event_name",
        "start_date",
        "end_date",
        "promo_scope",
        "offer_cc",
        "offer",
        "style_2",
        "style",
        "original_msrp_num",
        "promo_price_num",
        "percent_off_msrp_num",
        "avail_inv_num",
        "avail_plus_oo_num",
        "lw_unit_sales_num",
        "markdown_price",
        "mkd_price",
        "new_promo_price",
        "promo_price",
        "pct_off_msrp",
        "new_drpct",
        "new_dr_pct",
        "extra_20_pct_off_msrp",
        "clearance_price",
    ]
    raw = pd.read_parquet(path)
    read_columns = [col for col in columns if col in raw.columns]
    df = raw.loc[:, read_columns].copy()
    for col in read_columns:
        if col not in {"start_date", "end_date"}:
            continue
        df[col] = normalize_date(df[col])
    return df


def prepare_offer_rows(df: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    start_filter = pd.Timestamp(date.fromisoformat(args.start_date)) if args.start_date else None
    end_filter = pd.Timestamp(date.fromisoformat(args.end_date)) if args.end_date else None

    rows = df.copy()
    rows = rows.loc[rows["sheet_type"].isin(MODELABLE_SHEET_TYPES)].copy()
    rows = rows.loc[rows["start_date"].notna() & rows["end_date"].notna()].copy()
    rows = rows.loc[rows["end_date"].ge(rows["start_date"])].copy()
    rows["event_days"] = (rows["end_date"] - rows["start_date"]).dt.days + 1
    rows = rows.loc[rows["event_days"].between(1, args.max_event_days)].copy()
    if start_filter is not None:
        rows = rows.loc[rows["end_date"].ge(start_filter)].copy()
        rows["start_date"] = rows["start_date"].where(rows["start_date"].ge(start_filter), start_filter)
    if end_filter is not None:
        rows = rows.loc[rows["start_date"].le(end_filter)].copy()
        rows["end_date"] = rows["end_date"].where(rows["end_date"].le(end_filter), end_filter)
    rows["event_days"] = (rows["end_date"] - rows["start_date"]).dt.days + 1

    parsed = rows.apply(
        lambda row: parse_offer_code(*(row.get(col, "") for col in OFFER_CODE_COLUMNS)),
        axis=1,
        result_type="expand",
    )
    rows["Item"] = parsed[0]
    rows["Color"] = parsed[1]
    rows = rows.loc[rows["Item"].ne("") & rows["Color"].ne("")].copy()

    discount_sources = [
        col
        for col in [
            "percent_off_msrp_num",
            "pct_off_msrp",
            "new_drpct",
            "new_dr_pct",
            "extra_20_pct_off_msrp",
        ]
        if col in rows.columns
    ]
    if discount_sources:
        discounts = [normalized_discount(rows[col]) for col in discount_sources]
        rows["discount_pct"] = pd.concat(discounts, axis=1).max(axis=1)
    else:
        rows["discount_pct"] = pd.NA

    price_sources = [
        col
        for col in ["promo_price_num", "markdown_price", "mkd_price", "new_promo_price", "promo_price", "clearance_price"]
        if col in rows.columns
    ]
    if price_sources:
        prices = [pd.to_numeric(rows[col], errors="coerce") for col in price_sources]
        rows["effective_promo_price"] = pd.concat(prices, axis=1).min(axis=1)
    else:
        rows["effective_promo_price"] = pd.NA

    rows["is_markdown"] = rows["sheet_type"].fillna("").astype(str).str.contains(
        "markdown|clearance", case=False, regex=True
    )
    rows["is_final_sale"] = rows["sheet_type"].fillna("").astype(str).str.contains(
        "final_sale|final sale", case=False, regex=True
    )
    rows["is_tier1_recommendation"] = rows["sheet_type"].fillna("").astype(str).str.contains(
        "tier1", case=False, regex=True
    )

    summary = {
        "source_offer_rows": int(len(df)),
        "parseable_offer_rows": int(len(rows)),
        "parseable_offer_color_keys": int(rows[["Item", "Color"]].drop_duplicates().shape[0]),
        "date_range": [
            str(rows["start_date"].min().date()) if not rows.empty else None,
            str(rows["end_date"].max().date()) if not rows.empty else None,
        ],
    }
    return rows, summary


def expand_offer_dates(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows.assign(Date=pd.NaT)
    repeated = rows.loc[rows.index.repeat(rows["event_days"].astype(int))].copy()
    repeated["day_offset"] = repeated.groupby(level=0).cumcount()
    repeated["Date"] = repeated["start_date"] + pd.to_timedelta(repeated["day_offset"], unit="D")
    return repeated.drop(columns=["day_offset"])


def build_sku_day_features(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    sku_universe = load_sku_universe(args.forecast_snapshot)
    pdl_rows_raw = load_pdl_rows(args.pdl_offer_rows)
    pdl_rows, summary = prepare_offer_rows(pdl_rows_raw, args)

    daily_offers = expand_offer_dates(pdl_rows)
    joined = daily_offers.merge(sku_universe, on=["Item", "Color"], how="inner")

    if joined.empty:
        output = pd.DataFrame(columns=["Date", "SKU"])
    else:
        output = (
            joined.groupby(["Date", "SKU"], dropna=False)
            .agg(
                pdl_sku_offer_rows=("file_id", "size"),
                pdl_sku_active_events=("file_id", "nunique"),
                pdl_sku_distinct_offer_colors=("Item", "nunique"),
                pdl_sku_max_discount_pct=("discount_pct", "max"),
                pdl_sku_avg_discount_pct=("discount_pct", "mean"),
                pdl_sku_min_promo_price=("effective_promo_price", "min"),
                pdl_sku_total_avail_inv=("avail_inv_num", "max"),
                pdl_sku_total_avail_plus_oo=("avail_plus_oo_num", "max"),
                pdl_sku_lw_unit_sales=("lw_unit_sales_num", "max"),
                pdl_sku_has_markdown=("is_markdown", "max"),
                pdl_sku_has_final_sale=("is_final_sale", "max"),
                pdl_sku_has_tier1_recommendation=("is_tier1_recommendation", "max"),
                pdl_sku_primary_sheet_type=("sheet_type", "first"),
                pdl_sku_primary_scope=("promo_scope", "first"),
                pdl_sku_primary_event_name=("event_name", "first"),
            )
            .reset_index()
        )
        output["HasSkuPDLPromotion"] = True

    summary.update(
        {
            "sku_universe_rows": int(len(sku_universe)),
            "expanded_offer_day_rows": int(len(daily_offers)),
            "joined_offer_sku_day_rows": int(len(joined)),
            "sku_day_feature_rows": int(len(output)),
            "distinct_skus_with_sku_pdl": int(output["SKU"].nunique()) if "SKU" in output else 0,
            "feature_date_range": [
                str(output["Date"].min().date()) if not output.empty else None,
                str(output["Date"].max().date()) if not output.empty else None,
            ],
        }
    )
    return output, summary


def write_outputs(output: pd.DataFrame, summary: dict[str, Any], args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    feature_path = args.output_dir / "pdl_sku_day_features.parquet"
    output.to_parquet(feature_path, index=False, compression="zstd")
    if args.sample_rows > 0:
        output.head(args.sample_rows).to_csv(args.output_dir / "pdl_sku_day_features_sample.csv", index=False)
    summary["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["outputs"] = {
        "pdl_sku_day_features": str(feature_path),
        "pdl_sku_day_features_sample": str(args.output_dir / "pdl_sku_day_features_sample.csv")
        if args.sample_rows > 0
        else None,
        "pdl_sku_day_feature_summary": str(args.output_dir / "pdl_sku_day_feature_summary.json"),
    }
    with (args.output_dir / "pdl_sku_day_feature_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)


def main() -> None:
    args = parse_args()
    output, summary = build_sku_day_features(args)
    write_outputs(output, summary, args)
    print(f"PDL SKU/day feature rows: {len(output):,}")
    print(f"Distinct SKUs: {output['SKU'].nunique() if not output.empty else 0:,}")
    print(f"Wrote: {args.output_dir / 'pdl_sku_day_features.parquet'}")


if __name__ == "__main__":
    main()
