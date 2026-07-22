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
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

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

# Regex matches offer codes like 67513-43Q, capturing group 1 as item and group 2 as color.
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
    """Parse command line arguments for the PDL promotion features pipeline.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
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
    parser.add_argument(
        "--merge-existing",
        action="store_true",
        help=(
            "Replace the generated date range inside an existing feature Parquet instead of "
            "rebuilding all history in memory. Requires --start-date."
        ),
    )
    return parser.parse_args()


def clean_text(value: object) -> str:
    """Clean and normalize string values, mapping common text-based nulls to empty string.

    Args:
        value: The raw value to clean.

    Returns:
        str: Cleansed string.
    """
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in TEXT_NA else text


def normalize_code(value: object) -> str:
    """Normalize code values (e.g. style/color) by stripping whitespace and uppercasing.

    Args:
        value: The code value to normalize.

    Returns:
        str: Normalized uppercase code.
    """
    return clean_text(value).upper().replace(" ", "")


def parse_offer_code(*values: object) -> tuple[str, str]:
    """Parse multiple code candidate strings to extract a valid item/color code.

    Loops through candidates in priority order, attempts to match standard offer code
    regex, and returns the first successfully parsed item and color code.

    Args:
        *values: Candidate code values to inspect.

    Returns:
        tuple[str, str]: Extracted (item, color) code tuple. Returns ("", "") if no match found.
    """
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
    """Normalize date series to a consistent pandas datetime index (without timezone).

    Args:
        series: Pandas series containing dates.

    Returns:
        pd.Series: Normalized datetime series.
    """
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def normalized_discount(series: pd.Series) -> pd.Series:
    """Normalize discount values to a float between 0 and 1.

    Handles percentages (e.g. 20%) by dividing values > 1 by 100.0, and clips results.

    Args:
        series: Series of discount numbers.

    Returns:
        pd.Series: Float discount values in the range [0.0, 1.0].
    """
    values = pd.to_numeric(series, errors="coerce")
    values = values.where(values.le(1), values / 100.0)
    return values.clip(lower=0, upper=1)


def load_sku_universe(path: Path) -> pd.DataFrame:
    """Load and deduplicate the active SKU universe from forecast snapshots.

    Args:
        path: Path to the forecast SKU snapshot Parquet file.

    Returns:
        pd.DataFrame: Active SKU mapping containing SKU, Item, Color, and Size.

    Raises:
        FileNotFoundError: If the input file is missing.
    """
    if not path.exists():
        raise FileNotFoundError(f"Forecast snapshot not found: {path}")
    df = pd.read_parquet(path, columns=["SKU", "Item", "Color", "Size", "InferredFileDate"])
    df["InferredFileDate"] = normalize_date(df["InferredFileDate"])
    for col in ["SKU", "Item", "Color", "Size"]:
        df[col] = df[col].map(normalize_code)
    # Exclude empty rows and keep the most recent SKU mapping
    df = df.loc[df["SKU"].ne("") & df["Item"].ne("") & df["Color"].ne("")].copy()
    df = df.sort_values(["SKU", "InferredFileDate"]).drop_duplicates("SKU", keep="last")
    return df[["SKU", "Item", "Color", "Size"]].drop_duplicates()


def load_pdl_rows(path: Path) -> pd.DataFrame:
    """Load promotion detail list (PDL) offer rows from Parquet.

    Args:
        path: Path to the PDL offer rows Parquet.

    Returns:
        pd.DataFrame: Dataframe of loadable columns.

    Raises:
        FileNotFoundError: If the input file is missing.
    """
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
    """Clean, filter, parse, and score PDL offer rows.

    Filters rows based on sheet types, event duration constraints, and date filters. It extracts
    the best item/color keys and derives the max discount percentage and minimum promo price.

    Args:
        df: Raw PDL offer rows.
        args: Pipeline command-line options.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Prepared dataframe and processing metadata summary.
    """
    start_filter = pd.Timestamp(date.fromisoformat(args.start_date)) if args.start_date else None
    end_filter = pd.Timestamp(date.fromisoformat(args.end_date)) if args.end_date else None

    rows = df.copy()
    rows = rows.loc[rows["sheet_type"].isin(MODELABLE_SHEET_TYPES)].copy()
    rows = rows.loc[rows["start_date"].notna() & rows["end_date"].notna()].copy()
    rows = rows.loc[rows["end_date"].ge(rows["start_date"])].copy()
    rows["event_days"] = (rows["end_date"] - rows["start_date"]).dt.days + 1
    # Ignore suspiciously long windows; site-wide promos have their own global features.
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

    # Collect and normalize discount rates across various layout sources
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

    # Collect and normalize price points across various layout sources
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

    # Flag promotion categories based on sheet types
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
    """Duplicate promotion offer rows for each day of their start-to-end event duration.

    Args:
        rows: Dataframe containing start_date, end_date, and event_days columns.

    Returns:
        pd.DataFrame: Exploded dataframe containing a single 'Date' column per active event day.
    """
    if rows.empty:
        return rows.assign(Date=pd.NaT)
    repeated = rows.loc[rows.index.repeat(rows["event_days"].astype(int))].copy()
    repeated["day_offset"] = repeated.groupby(level=0).cumcount()
    repeated["Date"] = repeated["start_date"] + pd.to_timedelta(repeated["day_offset"], unit="D")
    return repeated.drop(columns=["day_offset"])


def build_sku_day_features(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Join PDL promotions with the SKU universe and generate SKU/day features.

    Args:
        args: Command-line parameters.

    Returns:
        tuple[pd.DataFrame, dict[str, Any]]: Aggregated features dataframe and metadata summary.
    """
    sku_universe = load_sku_universe(args.forecast_snapshot)
    pdl_rows_raw = load_pdl_rows(args.pdl_offer_rows)
    pdl_rows, summary = prepare_offer_rows(pdl_rows_raw, args)

    daily_offers = expand_offer_dates(pdl_rows)
    joined = daily_offers.merge(sku_universe, on=["Item", "Color"], how="inner")

    if joined.empty:
        output = pd.DataFrame(columns=["Date", "SKU"])
    else:
        # Group by Date and SKU to collapse overlapping events into single daily features
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
    """Persist generated promotion features as Parquet, CSV samples, and JSON summary.

    Args:
        output: Dataframe of SKU/day promotion features.
        summary: Metadata summary dictionary.
        args: Command-line options.
    """
    args.output_dir.mkdir(parents=True, exist_ok=True)
    feature_path = args.output_dir / "pdl_sku_day_features.parquet"
    if args.merge_existing and feature_path.exists():
        merge_existing_feature_store(feature_path, output)
        store_summary = summarize_feature_store(feature_path)
        summary["generated_sku_day_feature_rows"] = summary["sku_day_feature_rows"]
        summary["sku_day_feature_rows"] = store_summary["rows"]
        summary["distinct_skus_with_sku_pdl"] = store_summary["distinct_skus"]
        summary["feature_date_range"] = store_summary["date_range"]
        summary["merge_existing"] = True
    else:
        output.to_parquet(feature_path, index=False, compression="zstd")
        summary["merge_existing"] = False
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


def merge_existing_feature_store(feature_path: Path, output: pd.DataFrame) -> None:
    """Stream an incremental date replacement into the existing Parquet store."""
    if output.empty:
        return
    date_min = pd.Timestamp(output["Date"].min())
    date_max = pd.Timestamp(output["Date"].max())
    parquet_file = pq.ParquetFile(feature_path)
    schema = parquet_file.schema_arrow
    date_type = schema.field("Date").type
    lower = pa.scalar(date_min.to_pydatetime(), type=date_type)
    upper = pa.scalar(date_max.to_pydatetime(), type=date_type)
    temp_path = feature_path.with_name(f".{feature_path.name}.merge.tmp")

    writer = pq.ParquetWriter(temp_path, schema=schema, compression="zstd")
    try:
        for batch in parquet_file.iter_batches(batch_size=250_000):
            table = pa.Table.from_batches([batch], schema=schema)
            dates = table.column("Date")
            keep = pc.or_(pc.less(dates, lower), pc.greater(dates, upper))
            retained = table.filter(keep)
            if retained.num_rows:
                writer.write_table(retained)

        generated = pa.Table.from_pandas(
            output[schema.names],
            schema=schema,
            preserve_index=False,
            safe=False,
        )
        writer.write_table(generated)
    finally:
        writer.close()
        parquet_file.close()
    temp_path.replace(feature_path)


def summarize_feature_store(feature_path: Path) -> dict[str, Any]:
    """Summarize a large feature store without materializing it in pandas."""
    parquet_file = pq.ParquetFile(feature_path)
    date_min: pd.Timestamp | None = None
    date_max: pd.Timestamp | None = None
    distinct_skus: set[str] = set()
    for batch in parquet_file.iter_batches(batch_size=500_000, columns=["Date", "SKU"]):
        dates = pd.to_datetime(batch.column("Date").to_pandas(), errors="coerce")
        if not dates.dropna().empty:
            batch_min = pd.Timestamp(dates.min())
            batch_max = pd.Timestamp(dates.max())
            date_min = batch_min if date_min is None else min(date_min, batch_min)
            date_max = batch_max if date_max is None else max(date_max, batch_max)
        distinct_skus.update(
            value for value in batch.column("SKU").to_pylist() if value not in (None, "")
        )
    parquet_file.close()
    return {
        "rows": int(parquet_file.metadata.num_rows),
        "distinct_skus": int(len(distinct_skus)),
        "date_range": [
            None if date_min is None else date_min.date().isoformat(),
            None if date_max is None else date_max.date().isoformat(),
        ],
    }


def main() -> None:
    """Execute the command line entry point for promotion SKU feature generation."""
    args = parse_args()
    if args.merge_existing and not args.start_date:
        raise ValueError("--merge-existing requires --start-date for a bounded replacement.")
    output, summary = build_sku_day_features(args)
    write_outputs(output, summary, args)
    print(f"PDL SKU/day feature rows: {len(output):,}")
    print(f"Distinct SKUs: {output['SKU'].nunique() if not output.empty else 0:,}")
    print(f"Wrote: {args.output_dir / 'pdl_sku_day_features.parquet'}")


if __name__ == "__main__":
    main()
