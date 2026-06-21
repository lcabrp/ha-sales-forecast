"""Extract promotion planning workbook features for forecast modeling.

The PDL workbooks are business-authored Excel files, not a strict schema.  This
extractor is intentionally content-driven: it looks for Event Name,
Effective Date(s), and recognizable offer/product headers instead of relying on
specific sheet names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


DEFAULT_SOURCE_DIR = PROJECT_ROOT / "Source" / "Promotions"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "promotions"
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "promotions.db"

TEXT_NA = {"", "nan", "nat", "none", "<na>"}
DATE_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?:[./](\d{2,4}))?(?!\d)")
FILENAME_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[.](\d{1,2})(?:[.](\d{2,4}))?(?!\d)")
DISCOUNT_RE = re.compile(r"(?<!\d)(\d{1,3})(?:\.\d+)?\s*%")

HEADER_ALIASES = {
    "%_off_msrp": "pct_off_msrp",
    "pct_off_msrp": "pct_off_msrp",
    "markdown_pct_off_msrp": "pct_off_msrp",
    "original_msrp": "original_msrp",
    "original_msrp_": "original_msrp",
    "orig_msrp": "original_msrp",
    "msrp": "msrp",
    "promo_price": "promo_price",
    "new_promo_price": "new_promo_price",
    "markdown_price": "markdown_price",
    "mkd_price": "mkd_price",
    "mkd_price_badge": "mkd_price_badge",
    "avail_inv": "avail_inv",
    "avail_inv_": "avail_inv",
    "inventory": "inventory",
    "avail_oo": "avail_oo",
    "prior_wk_w_o_h": "prior_wk_w_o_h",
    "lw_unit_sales": "lw_unit_sales",
    "dept": "department",
    "exec": "exec_view",
    "key_category_view": "key_category_view",
    "style_id": "style",
    "style_2": "style_2",
}

EXPECTED_OFFER_COLUMNS = [
    "file_id",
    "source_file",
    "sheet_name",
    "sheet_type",
    "event_name",
    "effective_date_text",
    "start_date",
    "end_date",
    "source_file_date",
    "promo_scope",
    "excel_row",
    "style",
    "offer",
    "division",
    "department",
    "class",
    "style_2",
    "description",
    "color",
    "key_category_view",
    "exec_view",
    "life_cycle",
    "season",
    "season_code",
    "next_season",
    "capsule",
    "collection",
    "price_range",
    "original_msrp",
    "promo_price",
    "new_promo_price",
    "pct_off_msrp",
    "price_bucket",
    "discount_bucket",
    "avail_inv",
    "avail_oo",
    "prior_wk_w_o_h",
    "offer_in_stock",
    "lw_unit_sales",
    "updated_status",
    "pin_recommendations",
    "used_in_creative_use_dropdown",
    "red_text",
    "creative_notes",
    "merch_planning_feedback",
    "msrp",
    "new_drpct",
    "category",
    "offer_cc",
    "style_description",
    "original_msrp_num",
    "promo_price_num",
    "percent_off_msrp_num",
    "avail_inv_num",
    "avail_plus_oo_num",
    "prior_wk_woh_num",
    "lw_unit_sales_num",
    "new_msrps",
    "mkd_price",
    "inventory",
    "markdown_price",
    "gm",
    "category_2",
]

COUPON_OUTPUT_COLUMNS = [
    "source_file",
    "excel_row",
    "approval",
    "start_date",
    "end_date",
    "marketing_channel",
    "campaign_name_description",
    "discount_level",
    "code_type",
    "unique_code_count",
    "vanity_code",
    "auto_or_manual",
    "new_customer_only",
    "exclusions",
    "combinable",
    "stackable_with_loyalty",
    "campaign_id",
    "disclaimer_details",
    "notes",
    "completed",
    "inferred_scope",
    "max_discount_percent",
]


@dataclass
class SheetExtract:
    sheet_row: dict[str, Any]
    event_row: dict[str, Any] | None
    offers: pd.DataFrame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract PDL and coupon promotion features.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help=(
            "Replace the promotion parquet store with only the current source-dir workbooks. "
            "By default, extracted workbooks are merged into the existing parquet store."
        ),
    )
    parser.add_argument(
        "--no-sqlite",
        action="store_true",
        help="Write CSV/Parquet/JSON only; skip the convenience SQLite database.",
    )
    return parser.parse_args()


def clean_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return "" if text.lower() in TEXT_NA else text


def normalize_key(value: Any) -> str:
    text = clean_text(value).lower().replace("%", " pct ")
    text = text.replace("&", " and ").replace("$", " dollar ")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return HEADER_ALIASES.get(text, text)


def unique_headers(values: Iterable[Any]) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for idx, value in enumerate(values):
        key = normalize_key(value)
        if not key:
            key = f"unnamed_{idx + 1}"
        count = seen.get(key, 0)
        seen[key] = count + 1
        headers.append(key if count == 0 else f"{key}_{count + 1}")
    return headers


def stable_file_id(path: Path) -> str:
    return hashlib.sha256(path.name.lower().encode("utf-8")).hexdigest()[:12]


def infer_year(month: int) -> int:
    return 2025 if month >= 7 else 2026


def normalize_year(year_text: str | None, month: int) -> int:
    if not year_text:
        return infer_year(month)
    year = int(year_text)
    if year < 100:
        year += 2000
    if year < 2020:
        return infer_year(month)
    return year


def source_file_date(path: Path) -> date | None:
    match = FILENAME_DATE_RE.search(path.name)
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    year = normalize_year(match.group(3), month)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date_token(token: re.Match[str], default_year: int | None) -> date | None:
    month = int(token.group(1))
    day = int(token.group(2))
    year = normalize_year(token.group(3), month) if token.group(3) else default_year
    if year is None:
        year = infer_year(month)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date_range(text: Any, default_year: int | None) -> tuple[date | None, date | None]:
    if isinstance(text, datetime):
        return text.date(), text.date()
    if isinstance(text, date):
        return text, text

    cleaned = clean_text(text).replace("\u2013", "-").replace("\u2014", "-")
    tokens = list(DATE_TOKEN_RE.finditer(cleaned))
    if not tokens:
        return None, None

    first = parse_date_token(tokens[0], default_year)
    if first is None:
        return None, None
    if len(tokens) == 1:
        return first, first

    end_default = first.year
    second_has_year = tokens[1].group(3) is not None
    second = parse_date_token(tokens[1], end_default)
    if second is None:
        return first, first
    if not second_has_year and second < first:
        try:
            second = date(first.year + 1, second.month, second.day)
        except ValueError:
            second = first
    return first, second


def adjust_for_source_file_year(
    start_date: date | None,
    end_date: date | None,
    file_date: date | None,
) -> tuple[date | None, date | None]:
    if file_date is None or start_date is None:
        return start_date, end_date
    if file_date.month >= 10 and start_date.month <= 2 and start_date < file_date:
        start_date = date(start_date.year + 1, start_date.month, start_date.day)
        if end_date is not None and end_date.month <= 2 and end_date < file_date:
            end_date = date(end_date.year + 1, end_date.month, end_date.day)
    return start_date, end_date


def find_label_value(df: pd.DataFrame, labels: set[str], max_rows: int = 25) -> Any:
    max_row = min(max_rows, len(df))
    for row_idx in range(max_row):
        row = df.iloc[row_idx]
        for col_idx, value in enumerate(row):
            if normalize_key(value) in labels:
                for next_idx in range(col_idx + 1, len(row)):
                    candidate = row.iloc[next_idx]
                    if clean_text(candidate):
                        return candidate
    return ""


def header_score(keys: list[str]) -> int:
    key_set = set(keys)
    score = 0
    if "offer" in key_set:
        score += 5
    if "style" in key_set:
        score += 3
    for key in (
        "division",
        "department",
        "description",
        "color",
        "promo_price",
        "markdown_price",
        "mkd_price",
        "original_msrp",
        "pct_off_msrp",
        "inventory",
        "avail_inv",
    ):
        if key in key_set:
            score += 1
    return score


def find_header_row(df: pd.DataFrame, max_rows: int = 35) -> int | None:
    best_idx: int | None = None
    best_score = 0
    for row_idx in range(min(max_rows, len(df))):
        keys = unique_headers(df.iloc[row_idx].tolist())
        score = header_score(keys)
        if score > best_score:
            best_idx = row_idx
            best_score = score
    return best_idx if best_score >= 7 else None


def classify_sheet(
    sheet_name: str,
    event_name: str,
    header_row: int | None,
    source_file: str = "",
) -> str:
    haystack = f"{source_file} {sheet_name} {event_name}".lower()
    if "coupon" in haystack:
        return "coupon"
    if "tier 1" in haystack and ("recommend" in haystack or "pin" in haystack):
        return "tier1_recommendation"
    if "revision" in haystack or "correction" in haystack:
        return "correction"
    if header_row is None:
        if "roll up" in haystack or "pivot" in haystack:
            return "rollup_or_pivot"
        if "marketing dropdown" in haystack or "data" == sheet_name.strip().lower():
            return "support"
        return "non_offer"
    if "0% offer" in haystack or "0 percent offer" in haystack:
        return "zero_percent_offers"
    if "final sale" in haystack:
        return "final_sale"
    if any(token in haystack for token in ("markdown", "markdowns", "mkd", "further")):
        return "markdown"
    if "badge" in haystack:
        return "badge"
    if sheet_name.lower().startswith("promo offers"):
        return "aggregate_promo_offers"
    if "all promo discount" in haystack:
        return "aggregate_promo_offers"
    if "price change" in haystack or "msrp" in haystack:
        return "price_change"
    return "promo_detail"


def infer_scope(*values: Any) -> str:
    text = " ".join(clean_text(value).lower() for value in values)
    scopes = []
    checks = [
        ("Valentine", ("vday", "valentine")),
        ("Dresses", ("dress",)),
        ("Sleep/PJs", ("sleep", "pj", "pjs", "pajama", "shortjohn")),
        ("Swim", ("swim",)),
        ("Clearance", ("clearance", "markdown", "hanna sale", "hannasale", "final sale")),
        ("New Arrivals", ("new arrival",)),
        ("Halloween", ("halloween", "hannaween")),
        ("Holiday", ("holiday", "black friday", "cyber", "grinch", "dear deer")),
        ("Easter", ("easter",)),
        ("Baby", ("baby",)),
        ("Kids", ("kids",)),
        ("Sitewide", ("sitewide", "friends", "family", "everything")),
    ]
    for label, needles in checks:
        if any(needle in text for needle in needles):
            scopes.append(label)
    return "|".join(dict.fromkeys(scopes))


def to_number(value: Any) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    text = text.replace("$", "").replace(",", "").replace("%", "")
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    try:
        return float(text)
    except ValueError:
        return None


def first_existing(df: pd.DataFrame, columns: Iterable[str]) -> pd.Series:
    for column in columns:
        if column in df.columns:
            return df[column]
    return pd.Series([None] * len(df), index=df.index)


def numeric_series(df: pd.DataFrame, columns: Iterable[str]) -> pd.Series:
    values = first_existing(df, columns).map(to_number)
    return pd.to_numeric(values, errors="coerce")


def normalize_offer_frame(raw: pd.DataFrame, header_row: int) -> pd.DataFrame:
    headers = unique_headers(raw.iloc[header_row].tolist())
    frame = raw.iloc[header_row + 1 :].copy()
    frame.columns = headers
    frame = frame.dropna(how="all").copy()
    if frame.empty:
        return frame

    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(clean_text)

    offer = first_existing(frame, ("offer", "offer_cc"))
    style = first_existing(frame, ("style", "style_id"))
    description = first_existing(frame, ("description", "style_description"))
    valid = (
        offer.map(clean_text).str.lower().ne("offer")
        & (
            offer.map(clean_text).ne("")
            | style.map(clean_text).ne("")
            | description.map(clean_text).ne("")
        )
    )
    frame = frame.loc[valid].copy()

    if "style" not in frame.columns and "style_id" in frame.columns:
        frame["style"] = frame["style_id"]
    if "offer" not in frame.columns and "offer_cc" in frame.columns:
        frame["offer"] = frame["offer_cc"]
    if "offer_cc" not in frame.columns:
        frame["offer_cc"] = first_existing(frame, ("offer",))
    if "style_description" not in frame.columns:
        frame["style_description"] = first_existing(frame, ("description",))

    for col in ("style", "offer", "offer_cc"):
        if col in frame.columns:
            frame[col] = frame[col].map(clean_text)

    if "style" not in frame.columns:
        frame["style"] = frame["offer"].map(lambda value: clean_text(value).split("-")[0])
    else:
        missing_style = frame["style"].map(clean_text).eq("")
        frame.loc[missing_style, "style"] = frame.loc[missing_style, "offer"].map(
            lambda value: clean_text(value).split("-")[0]
        )

    frame["original_msrp_num"] = numeric_series(frame, ("original_msrp", "msrp"))
    frame["promo_price_num"] = numeric_series(
        frame,
        ("promo_price", "new_promo_price", "markdown_price", "mkd_price"),
    )
    frame["percent_off_msrp_num"] = numeric_series(
        frame,
        ("pct_off_msrp", "new_drpct", "markdown_pct_off_msrp"),
    )
    pct = frame["percent_off_msrp_num"]
    frame.loc[pct > 1, "percent_off_msrp_num"] = pct[pct > 1] / 100
    frame["avail_inv_num"] = numeric_series(frame, ("avail_inv", "inventory"))
    avail_inv = frame["avail_inv_num"].fillna(0)
    avail_oo = numeric_series(frame, ("avail_oo",)).fillna(0)
    frame["avail_plus_oo_num"] = avail_inv + avail_oo
    frame["prior_wk_woh_num"] = numeric_series(frame, ("prior_wk_w_o_h",))
    frame["lw_unit_sales_num"] = numeric_series(frame, ("lw_unit_sales",))
    return frame


def extract_sheet(
    *,
    file_id: str,
    source_file: str,
    sheet_name: str,
    raw: pd.DataFrame,
    file_date: date | None,
    inherited_event_name: str,
    inherited_effective: str,
) -> SheetExtract:
    header_row = find_header_row(raw)
    event_name = clean_text(find_label_value(raw, {"event_name"})) or inherited_event_name
    raw_effective_text = clean_text(find_label_value(raw, {"effective_date_s"}))
    effective_text = raw_effective_text or inherited_effective
    start_date, end_date = parse_date_range(effective_text, file_date.year if file_date else None)
    if start_date is None and inherited_effective and raw_effective_text != inherited_effective:
        effective_text = inherited_effective
        start_date, end_date = parse_date_range(effective_text, file_date.year if file_date else None)
    start_date, end_date = adjust_for_source_file_year(start_date, end_date, file_date)
    sheet_start_date, sheet_end_date = parse_date_range(sheet_name, file_date.year if file_date else None)
    sheet_start_date, sheet_end_date = adjust_for_source_file_year(
        sheet_start_date,
        sheet_end_date,
        file_date,
    )
    if start_date is None:
        start_date, end_date = sheet_start_date, sheet_end_date
    elif (
        file_date is not None
        and sheet_start_date is not None
        and abs((sheet_start_date - file_date).days) <= 45
        and abs((start_date - file_date).days) > 45
    ):
        start_date, end_date = sheet_start_date, sheet_end_date
    elif (
        sheet_start_date is not None
        and start_date is not None
        and sheet_start_date != start_date
        and len(DATE_TOKEN_RE.findall(effective_text)) == 1
        and DATE_TOKEN_RE.search(effective_text).group(3) is None
        and DATE_TOKEN_RE.search(sheet_name) is not None
        and abs((sheet_start_date - start_date).days) <= 31
    ):
        start_date, end_date = sheet_start_date, sheet_end_date

    sheet_type = classify_sheet(sheet_name, event_name, header_row, source_file)
    promo_scope = infer_scope(source_file, sheet_name, event_name)
    offers = pd.DataFrame()
    if header_row is not None and sheet_type not in {"zero_percent_offers", "price_change"}:
        offers = normalize_offer_frame(raw, header_row)

    sheet_row = {
        "file_id": file_id,
        "source_file": source_file,
        "sheet_name": sheet_name,
        "read_status": "ok",
        "height": int(raw.shape[0]),
        "width": int(raw.shape[1]),
        "header_row_zero_based": header_row,
        "event_name": event_name,
        "effective_date_text": effective_text,
        "start_date": start_date.isoformat() if start_date else "",
        "end_date": end_date.isoformat() if end_date else "",
        "is_product_offer_sheet": bool(header_row is not None and not offers.empty),
        "is_aggregate_offer_sheet": sheet_type == "aggregate_promo_offers",
        "sheet_type": sheet_type,
        "extracted_row_count": int(len(offers)),
    }

    event_row = None
    placeholder = (
        sheet_type == "promo_detail"
        and len(offers) <= 1
        and (offers["avail_inv_num"].fillna(0).sum() if "avail_inv_num" in offers else 0) == 0
        and re.fullmatch(r"(promo\s*#?\s*\d+|\d+)", sheet_name.strip().lower()) is not None
    )
    if placeholder:
        sheet_type = "empty_placeholder"
        sheet_row["sheet_type"] = sheet_type
        sheet_row["is_product_offer_sheet"] = False
        sheet_row["extracted_row_count"] = 0
        offers = pd.DataFrame()

    if sheet_type not in {
        "support",
        "rollup_or_pivot",
        "non_offer",
        "coupon",
        "zero_percent_offers",
        "correction",
        "empty_placeholder",
        "price_change",
    }:
        row_count = int(len(offers))
        event_row = {
            "file_id": file_id,
            "source_file": source_file,
            "sheet_name": sheet_name,
            "sheet_type": sheet_type,
            "event_name": event_name or sheet_name,
            "effective_date_text": effective_text,
            "start_date": start_date.isoformat() if start_date else "",
            "end_date": end_date.isoformat() if end_date else "",
            "source_file_date": file_date.isoformat() if file_date else "",
            "promo_scope": promo_scope,
            "row_count": row_count,
            "distinct_style_count": int(offers["style"].nunique()) if "style" in offers else 0,
            "distinct_offer_cc_count": int(offers["offer_cc"].nunique()) if "offer_cc" in offers else 0,
            "total_avail_inv": float(offers["avail_inv_num"].fillna(0).sum())
            if "avail_inv_num" in offers
            else 0.0,
        }

    if not offers.empty:
        offers.insert(0, "excel_row", offers.index + 1)
        offers.insert(0, "promo_scope", promo_scope)
        offers.insert(0, "source_file_date", file_date.isoformat() if file_date else "")
        offers.insert(0, "end_date", end_date.isoformat() if end_date else "")
        offers.insert(0, "start_date", start_date.isoformat() if start_date else "")
        offers.insert(0, "effective_date_text", effective_text)
        offers.insert(0, "event_name", event_name or sheet_name)
        offers.insert(0, "sheet_type", sheet_type)
        offers.insert(0, "sheet_name", sheet_name)
        offers.insert(0, "source_file", source_file)
        offers.insert(0, "file_id", file_id)

    return SheetExtract(sheet_row=sheet_row, event_row=event_row, offers=offers)


def extract_workbook(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[pd.DataFrame]]:
    file_id = stable_file_id(path)
    file_date = source_file_date(path)
    workbook_type = "coupon" if "coupon" in path.name.lower() else "unknown"
    workbook_row = {
        "file_id": file_id,
        "source_file": path.name,
        "source_path": str(path.resolve()),
        "file_size_bytes": path.stat().st_size,
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat(),
        "source_file_date": file_date.isoformat() if file_date else "",
        "workbook_type": workbook_type,
    }

    sheet_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    offer_frames: list[pd.DataFrame] = []

    try:
        excel = pd.ExcelFile(path, engine="calamine")
    except Exception as exc:
        workbook_row["workbook_type"] = "unreadable"
        sheet_rows.append(
            {
                "file_id": file_id,
                "source_file": path.name,
                "sheet_name": "",
                "read_status": f"error: {exc}",
                "height": 0,
                "width": 0,
                "header_row_zero_based": None,
                "event_name": "",
                "effective_date_text": "",
                "start_date": "",
                "end_date": "",
                "is_product_offer_sheet": False,
                "is_aggregate_offer_sheet": False,
                "sheet_type": "unreadable",
                "extracted_row_count": 0,
            }
        )
        return workbook_row, sheet_rows, event_rows, offer_frames

    inherited_event_name = ""
    inherited_effective = ""
    pending: list[tuple[str, pd.DataFrame, bool]] = []
    for sheet_name in excel.sheet_names:
        try:
            preview = pd.read_excel(
                excel,
                sheet_name=sheet_name,
                header=None,
                dtype=object,
                nrows=40,
            )
        except Exception as exc:
            sheet_rows.append(
                {
                    "file_id": file_id,
                    "source_file": path.name,
                    "sheet_name": sheet_name,
                    "read_status": f"error: {exc}",
                    "height": 0,
                    "width": 0,
                    "header_row_zero_based": None,
                    "event_name": "",
                    "effective_date_text": "",
                    "start_date": "",
                    "end_date": "",
                    "is_product_offer_sheet": False,
                    "is_aggregate_offer_sheet": False,
                    "sheet_type": "read_error",
                    "extracted_row_count": 0,
                }
            )
            continue
        preview_header = find_header_row(preview)
        preview_event = clean_text(find_label_value(preview, {"event_name"}))
        preview_type = classify_sheet(sheet_name, preview_event, preview_header, path.name)
        needs_full_read = preview_header is not None and preview_type not in {"zero_percent_offers"}
        pending.append((sheet_name, preview, needs_full_read))
        inherited_event_name = inherited_event_name or preview_event
        inherited_effective = inherited_effective or clean_text(
            find_label_value(preview, {"effective_date_s"})
        )

    for sheet_name, preview, needs_full_read in pending:
        raw = preview
        if needs_full_read:
            try:
                raw = pd.read_excel(excel, sheet_name=sheet_name, header=None, dtype=object)
            except Exception as exc:
                sheet_rows.append(
                    {
                        "file_id": file_id,
                        "source_file": path.name,
                        "sheet_name": sheet_name,
                        "read_status": f"error: {exc}",
                        "height": int(preview.shape[0]),
                        "width": int(preview.shape[1]),
                        "header_row_zero_based": find_header_row(preview),
                        "event_name": inherited_event_name,
                        "effective_date_text": inherited_effective,
                        "start_date": "",
                        "end_date": "",
                        "is_product_offer_sheet": False,
                        "is_aggregate_offer_sheet": False,
                        "sheet_type": "read_error",
                        "extracted_row_count": 0,
                    }
                )
                continue
        extracted = extract_sheet(
            file_id=file_id,
            source_file=path.name,
            sheet_name=sheet_name,
            raw=raw,
            file_date=file_date,
            inherited_event_name=inherited_event_name,
            inherited_effective=inherited_effective,
        )
        sheet_rows.append(extracted.sheet_row)
        if extracted.event_row is not None:
            event_rows.append(extracted.event_row)
        if not extracted.offers.empty:
            offer_frames.append(extracted.offers)

    pdl_event_types = {
        row["sheet_type"]
        for row in sheet_rows
        if row["is_product_offer_sheet"]
        and row["sheet_type"] not in {"price_change", "badge", "correction", "empty_placeholder"}
    }
    if pdl_event_types:
        workbook_row["workbook_type"] = "pdl"
    elif workbook_type != "coupon":
        workbook_row["workbook_type"] = "other"
    return workbook_row, sheet_rows, event_rows, offer_frames


def matching_column(columns: Iterable[str], *needles: str) -> str | None:
    for column in columns:
        key = normalize_key(column)
        if all(needle in key for needle in needles):
            return column
    return None


def extract_coupon_rows(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name="COUPON TRACKER", header=0, dtype=object, engine="calamine")
    except Exception:
        return pd.DataFrame(columns=COUPON_OUTPUT_COLUMNS)
    df = df.dropna(how="all").copy()
    if df.empty:
        return pd.DataFrame(columns=COUPON_OUTPUT_COLUMNS)

    columns = list(df.columns)
    mapping = {
        "approval": matching_column(columns, "approval"),
        "start_date": matching_column(columns, "customer_campaign_start_date"),
        "end_date": matching_column(columns, "customer_campaign_end_date"),
        "marketing_channel": matching_column(columns, "marketing_channel"),
        "campaign_name_description": matching_column(columns, "campaign_name", "description"),
        "discount_level": matching_column(columns, "discount"),
        "code_type": matching_column(columns, "code_type"),
        "unique_code_count": matching_column(columns, "unique"),
        "vanity_code": matching_column(columns, "vanity"),
        "auto_or_manual": matching_column(columns, "auto"),
        "new_customer_only": matching_column(columns, "new_customer"),
        "exclusions": matching_column(columns, "exclusion"),
        "combinable": matching_column(columns, "combinable"),
        "stackable_with_loyalty": matching_column(columns, "loyalty"),
        "campaign_id": matching_column(columns, "campaign_id"),
        "disclaimer_details": matching_column(columns, "disclaimer"),
        "notes": matching_column(columns, "notes"),
        "completed": matching_column(columns, "completed"),
    }
    out = pd.DataFrame({"source_file": path.name, "excel_row": df.index + 2})
    for output_col in COUPON_OUTPUT_COLUMNS:
        if output_col in {"source_file", "excel_row", "inferred_scope", "max_discount_percent"}:
            continue
        source_col = mapping.get(output_col)
        out[output_col] = df[source_col].map(clean_text) if source_col in df else ""

    for col in ("start_date", "end_date"):
        out[col] = pd.to_datetime(out[col], errors="coerce").dt.date.map(
            lambda value: value.isoformat() if pd.notna(value) else ""
        )
    out = out[out["start_date"].ne("") & out["end_date"].ne("")].copy()
    out = out[~out["approval"].str.lower().eq("example")].copy()
    out["inferred_scope"] = out.apply(
        lambda row: infer_scope(
            row.get("campaign_name_description", ""),
            row.get("discount_level", ""),
            row.get("exclusions", ""),
            row.get("disclaimer_details", ""),
        ),
        axis=1,
    )
    discount_text = (
        out["discount_level"].fillna("")
        + " "
        + out["campaign_name_description"].fillna("")
        + " "
        + out["disclaimer_details"].fillna("")
    )
    out["max_discount_percent"] = discount_text.map(max_discount_percent)
    return out[COUPON_OUTPUT_COLUMNS].reset_index(drop=True)


def max_discount_percent(text: str) -> float | None:
    values = [float(match.group(1)) for match in DISCOUNT_RE.finditer(clean_text(text))]
    values = [value for value in values if 0 <= value <= 100]
    return max(values) if values else None


def date_range_rows(start: str, end: str) -> Iterable[str]:
    if not start or not end:
        return []
    start_dt = date.fromisoformat(start)
    end_dt = date.fromisoformat(end)
    if end_dt < start_dt:
        return []
    return ((start_dt + timedelta(days=offset)).isoformat() for offset in range((end_dt - start_dt).days + 1))


def build_pdl_daily(events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if events.empty:
        empty_event = pd.DataFrame(
            columns=[
                "date",
                "source",
                "file_id",
                "source_file",
                "sheet_name",
                "event_name",
                "promo_scope",
                "offer_cc_count",
                "style_count",
                "total_avail_inv",
            ]
        )
        return empty_event, pd.DataFrame(
            columns=[
                "date",
                "pdl_active_events",
                "pdl_offer_cc_count",
                "pdl_style_count",
                "pdl_total_avail_inv",
                "pdl_scopes",
            ]
        )

    dated = events[events["start_date"].ne("") & events["end_date"].ne("")].copy()
    active_types = {"promo_detail", "markdown", "final_sale"}
    include = dated["sheet_type"].isin(active_types)
    files_without_detail = set(dated.loc[~include, "file_id"]) - set(dated.loc[include, "file_id"])
    include |= dated["file_id"].isin(files_without_detail) & dated["sheet_type"].eq("aggregate_promo_offers")
    daily_events = []
    for row in dated.loc[include].itertuples(index=False):
        for day in date_range_rows(row.start_date, row.end_date):
            daily_events.append(
                {
                    "date": day,
                    "source": "pdl",
                    "file_id": row.file_id,
                    "source_file": row.source_file,
                    "sheet_name": row.sheet_name,
                    "event_name": row.event_name,
                    "promo_scope": row.promo_scope,
                    "offer_cc_count": row.distinct_offer_cc_count,
                    "style_count": row.distinct_style_count,
                    "total_avail_inv": row.total_avail_inv,
                }
            )
    daily_event_df = pd.DataFrame(daily_events)
    if daily_event_df.empty:
        return daily_event_df, pd.DataFrame()
    summary = (
        daily_event_df.groupby("date", as_index=False)
        .agg(
            pdl_active_events=("event_name", "count"),
            pdl_offer_cc_count=("offer_cc_count", "sum"),
            pdl_style_count=("style_count", "sum"),
            pdl_total_avail_inv=("total_avail_inv", "sum"),
            pdl_scopes=("promo_scope", lambda values: "|".join(dict.fromkeys(v for v in values if v))),
        )
        .sort_values("date")
    )
    return daily_event_df.sort_values(["date", "source_file", "sheet_name"]), summary


def build_coupon_daily(coupon_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily_rows = []
    for row in coupon_rows.itertuples(index=False):
        for day in date_range_rows(row.start_date, row.end_date):
            daily_rows.append(
                {
                    "date": day,
                    "source": "coupon",
                    "source_file": row.source_file,
                    "excel_row": row.excel_row,
                    "campaign_name_description": row.campaign_name_description,
                    "inferred_scope": row.inferred_scope,
                    "max_discount_percent": row.max_discount_percent,
                }
            )
    daily_df = pd.DataFrame(daily_rows)
    if daily_df.empty:
        return daily_df, pd.DataFrame(
            columns=["date", "coupon_active_rows", "coupon_max_discount_percent", "coupon_scopes"]
        )
    summary = (
        daily_df.groupby("date", as_index=False)
        .agg(
            coupon_active_rows=("campaign_name_description", "count"),
            coupon_max_discount_percent=("max_discount_percent", "max"),
            coupon_scopes=("inferred_scope", lambda values: "|".join(dict.fromkeys(v for v in values if v))),
        )
        .sort_values("date")
    )
    return daily_df.sort_values(["date", "source_file", "excel_row"]), summary


def build_combined_daily(pdl_daily: pd.DataFrame, coupon_daily: pd.DataFrame) -> pd.DataFrame:
    starts = []
    ends = []
    for df in (pdl_daily, coupon_daily):
        if not df.empty:
            starts.append(pd.to_datetime(df["date"]).min().date())
            ends.append(pd.to_datetime(df["date"]).max().date())
    if not starts:
        return pd.DataFrame(
            columns=[
                "date",
                "pdl_active_events",
                "pdl_offer_cc_count",
                "pdl_style_count",
                "pdl_total_avail_inv",
                "pdl_scopes",
                "coupon_active_rows",
                "coupon_max_discount_percent",
                "coupon_scopes",
            ]
        )
    dates = pd.DataFrame({"date": pd.date_range(min(starts), max(ends)).date.astype(str)})
    combined = dates.merge(pdl_daily, on="date", how="left").merge(coupon_daily, on="date", how="left")
    for col in ("pdl_active_events", "pdl_offer_cc_count", "pdl_style_count", "coupon_active_rows"):
        if col in combined:
            combined[col] = combined[col].fillna(0).astype(int)
    for col in ("pdl_total_avail_inv", "coupon_max_discount_percent"):
        if col in combined:
            combined[col] = combined[col].fillna(0.0)
    for col in ("pdl_scopes", "coupon_scopes"):
        if col in combined:
            combined[col] = combined[col].fillna("")
    return combined


def order_columns(df: pd.DataFrame, preferred: list[str]) -> pd.DataFrame:
    for column in preferred:
        if column not in df.columns:
            df[column] = ""
    extra = [column for column in df.columns if column not in preferred]
    return df[preferred + extra]


def align_table_columns(existing: pd.DataFrame, fresh: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return both frames with a shared column set, preserving existing column order."""
    columns = list(existing.columns) + [column for column in fresh.columns if column not in existing.columns]
    if not columns:
        columns = list(fresh.columns)
    existing_aligned = existing.copy()
    fresh_aligned = fresh.copy()
    for column in columns:
        if column not in existing_aligned.columns:
            existing_aligned[column] = pd.NA
        if column not in fresh_aligned.columns:
            fresh_aligned[column] = pd.NA
    return existing_aligned.loc[:, columns], fresh_aligned.loc[:, columns]


def merge_on_source_file(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    if fresh.empty:
        return existing.copy()
    if existing.empty:
        return fresh.copy()
    if "source_file" not in existing.columns or "source_file" not in fresh.columns:
        return fresh.copy()

    existing_aligned, fresh_aligned = align_table_columns(existing, fresh)
    replaced_sources = set(fresh_aligned["source_file"].dropna().astype(str))
    kept_existing = existing_aligned.loc[~existing_aligned["source_file"].astype(str).isin(replaced_sources)]
    return pd.concat([kept_existing, fresh_aligned], ignore_index=True)


def sort_table(name: str, df: pd.DataFrame) -> pd.DataFrame:
    sort_columns_by_table = {
        "workbook_files": ["source_file"],
        "workbook_sheets": ["source_file", "sheet_name"],
        "pdl_events": ["start_date", "source_file", "sheet_name"],
        "pdl_offer_rows": ["source_file", "sheet_name", "excel_row"],
        "coupon_tracker_rows": ["source_file", "start_date", "excel_row"],
    }
    sort_columns = [column for column in sort_columns_by_table.get(name, []) if column in df.columns]
    if not sort_columns:
        return df.reset_index(drop=True)
    return df.sort_values(sort_columns, kind="stable").reset_index(drop=True)


def merge_existing_source_tables(
    fresh_tables: dict[str, pd.DataFrame],
    output_dir: Path,
    replace_existing: bool,
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    source_table_names = [
        "workbook_files",
        "workbook_sheets",
        "pdl_events",
        "pdl_offer_rows",
        "coupon_tracker_rows",
    ]
    merged_tables = fresh_tables.copy()
    existing_counts: dict[str, int] = {}
    if replace_existing:
        return {name: sort_table(name, df) for name, df in merged_tables.items()}, existing_counts

    for name in source_table_names:
        path = output_dir / f"{name}.parquet"
        fresh = fresh_tables[name]
        if not path.exists():
            merged_tables[name] = sort_table(name, fresh)
            continue
        existing = pd.read_parquet(path)
        existing_counts[name] = int(len(existing))
        merged_tables[name] = sort_table(name, merge_on_source_file(existing, fresh))
    return merged_tables, existing_counts


def write_table(df: pd.DataFrame, name: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_dir / f"{name}.parquet", index=False)
    df.to_csv(output_dir / f"{name}.csv", index=False)


def write_sqlite(tables: dict[str, pd.DataFrame], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        for name, df in tables.items():
            df.to_sql(name, conn, if_exists="replace", index=False)
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS ix_pdl_offer_rows_offer_cc ON pdl_offer_rows (offer_cc);
            CREATE INDEX IF NOT EXISTS ix_pdl_offer_rows_style ON pdl_offer_rows (style);
            CREATE INDEX IF NOT EXISTS ix_pdl_events_date ON pdl_events (start_date, end_date);
            CREATE INDEX IF NOT EXISTS ix_combined_daily_promo_features_date
                ON combined_daily_promo_features (date);
            """
        )


def write_samples(events: pd.DataFrame, output_dir: Path) -> None:
    if events.empty:
        pd.DataFrame().to_csv(output_dir / "top_pdl_events_sample.csv", index=False)
        pd.DataFrame().to_csv(output_dir / "aggregate_pdl_events_sample.csv", index=False)
        return
    events.sort_values("row_count", ascending=False).head(25).to_csv(
        output_dir / "top_pdl_events_sample.csv",
        index=False,
    )
    events[events["sheet_type"].eq("aggregate_promo_offers")].sort_values(
        "row_count",
        ascending=False,
    ).head(25).to_csv(output_dir / "aggregate_pdl_events_sample.csv", index=False)


def main() -> None:
    args = parse_args()
    source_dir = args.source_dir
    output_dir = args.output_dir
    paths = sorted(path for path in source_dir.glob("*.xlsx") if not path.name.startswith("~$"))
    if not paths:
        raise FileNotFoundError(f"No .xlsx files found in {source_dir}")

    workbook_rows = []
    sheet_rows = []
    event_rows = []
    offer_frames = []
    coupon_frames = []

    for path in paths:
        workbook_row, workbook_sheets, workbook_events, workbook_offers = extract_workbook(path)
        workbook_rows.append(workbook_row)
        sheet_rows.extend(workbook_sheets)
        event_rows.extend(workbook_events)
        offer_frames.extend(workbook_offers)
        if workbook_row["workbook_type"] == "coupon":
            coupon_frames.append(extract_coupon_rows(path))
        print(
            f"{workbook_row['workbook_type']:10} {path.name} "
            f"sheets={len(workbook_sheets):3d} events={len(workbook_events):3d}"
        )

    workbook_files = pd.DataFrame(workbook_rows).sort_values("source_file").reset_index(drop=True)
    workbook_sheets = pd.DataFrame(sheet_rows).sort_values(["source_file", "sheet_name"]).reset_index(drop=True)
    pdl_events = pd.DataFrame(event_rows)
    if not pdl_events.empty:
        pdl_events = pdl_events.sort_values(["start_date", "source_file", "sheet_name"]).reset_index(drop=True)

    pdl_offer_rows = pd.concat(offer_frames, ignore_index=True) if offer_frames else pd.DataFrame()
    pdl_offer_rows = order_columns(pdl_offer_rows, EXPECTED_OFFER_COLUMNS)
    pdl_tier1_recommendations = pd.DataFrame({"_empty_marker": []})
    coupon_tracker_rows = (
        pd.concat(coupon_frames, ignore_index=True) if coupon_frames else pd.DataFrame(columns=COUPON_OUTPUT_COLUMNS)
    )
    coupon_tracker_rows = order_columns(coupon_tracker_rows, COUPON_OUTPUT_COLUMNS)

    source_tables = {
        "workbook_files": workbook_files,
        "workbook_sheets": workbook_sheets,
        "pdl_events": pdl_events,
        "pdl_offer_rows": pdl_offer_rows,
        "coupon_tracker_rows": coupon_tracker_rows,
    }
    source_tables, existing_table_counts = merge_existing_source_tables(
        source_tables,
        output_dir,
        replace_existing=args.replace_existing,
    )
    workbook_files = source_tables["workbook_files"]
    workbook_sheets = source_tables["workbook_sheets"]
    pdl_events = source_tables["pdl_events"]
    pdl_offer_rows = order_columns(source_tables["pdl_offer_rows"], EXPECTED_OFFER_COLUMNS)
    coupon_tracker_rows = order_columns(source_tables["coupon_tracker_rows"], COUPON_OUTPUT_COLUMNS)

    pdl_daily_event_rows, pdl_daily_summary = build_pdl_daily(pdl_events)
    coupon_daily_rows, coupon_daily_summary = build_coupon_daily(coupon_tracker_rows)
    combined_daily = build_combined_daily(pdl_daily_summary, coupon_daily_summary)

    tables = {
        "workbook_files": workbook_files,
        "workbook_sheets": workbook_sheets,
        "pdl_events": pdl_events,
        "pdl_offer_rows": pdl_offer_rows,
        "pdl_tier1_recommendations": pdl_tier1_recommendations,
        "pdl_daily_event_rows": pdl_daily_event_rows,
        "pdl_daily_summary": pdl_daily_summary,
        "coupon_tracker_rows": coupon_tracker_rows,
        "coupon_daily_rows": coupon_daily_rows,
        "coupon_daily_summary": coupon_daily_summary,
        "combined_daily_promo_features": combined_daily,
    }
    for name, df in tables.items():
        write_table(df, name, output_dir)
    if not args.no_sqlite:
        write_sqlite(tables, args.db)
    write_samples(pdl_events, output_dir)

    dated_events = pdl_events[pdl_events["start_date"].ne("")] if not pdl_events.empty else pdl_events
    summary = {
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "source_dir": str(source_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "sqlite_db": "" if args.no_sqlite else str(args.db.resolve()),
        "workbooks": int(len(workbook_files)),
        "pdl_workbooks": int((workbook_files["workbook_type"] == "pdl").sum()),
        "coupon_workbooks": int((workbook_files["workbook_type"] == "coupon").sum()),
        "other_workbooks": int((workbook_files["workbook_type"] == "other").sum()),
        "mode": "replace_existing" if args.replace_existing else "merge_existing",
        "current_source_workbooks": int(len(paths)),
        "existing_table_counts_before_merge": existing_table_counts,
        "pdl_events": int(len(pdl_events)),
        "pdl_detail_events": int(pdl_events["sheet_type"].isin(["promo_detail", "markdown", "final_sale"]).sum())
        if not pdl_events.empty
        else 0,
        "pdl_offer_rows": int(len(pdl_offer_rows)),
        "pdl_distinct_offer_cc": int(pdl_offer_rows["offer_cc"].replace("", pd.NA).nunique())
        if "offer_cc" in pdl_offer_rows
        else 0,
        "pdl_distinct_styles": int(pdl_offer_rows["style"].replace("", pd.NA).nunique())
        if "style" in pdl_offer_rows
        else 0,
        "pdl_date_min": str(dated_events["start_date"].min()) if not dated_events.empty else "",
        "pdl_date_max": str(dated_events["end_date"].max()) if not dated_events.empty else "",
        "combined_daily_dates": int(len(combined_daily)),
        "sheet_type_counts": workbook_sheets["sheet_type"].value_counts().to_dict(),
        "tables": {name: int(len(df)) for name, df in tables.items()},
    }
    (output_dir / "promotion_extraction_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )

    print(f"\nOutput: {output_dir}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
