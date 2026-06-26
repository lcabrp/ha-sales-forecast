"""
Ingestion Pipeline v3 — Zoning & Slotting Modernization
========================================================

This script replaces the legacy Excel-based workflow that involved two sequential
workbooks (Case Quantity Calcs → Active Storage Tool) and manual copy-paste steps.

It reads the single SharePoint source file ("Product Info for BRG.xlsx") and
produces two outputs:
  1. A 36-column "Forward Demand CSV" for AX 2012 ingestion.
  2. A "Required Slots" summary showing estimated active-storage slot counts
     per SKU, grouped by Slot Tier — the same data the legacy Active Storage
     Tool fed into the Slot Assignment Tool.

Data Flow (mirrors the legacy tools):
  ┌─────────────────────────────────────────────────────────────┐
  │ Source File (Product Info for BRG.xlsx)                      │
  │   ├── "Product Forecast Tool by Week"  → 13-week demand     │
  │   ├── "SKU Level 14 Day Forecast"      → FD1-FD14           │
  │   └── "Product Attributes"             → Hierarchy & status │
  └───────────┬─────────────────────────────────────────────────┘
              │
              ▼
  ┌─ SKU UNIVERSE (union of Weekly + 14-Day, deduplicated) ─────┐
  │ Only forecast-active SKUs are processed (~30K), matching the │
  │ legacy SKU_LIST_ACTIVE macro in Case Quantity Calcs.         │
  └──────────────────────────┬──────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
   Velocity Calc      Putaway Indicator     Slot Requirement
   (13-wk demand      (weeks until first    (weekly demand ÷
    vs thresholds)      demand begins)        case qty, rounded
                                              up + buffer)
        │                    │                    │
        └────────────────────┼────────────────────┘
                             ▼
                    36-column AX CSV
                    + Required Slots CSV
"""
from __future__ import annotations

import argparse
import contextlib
import io
import shutil
from typing import Any, Callable
import pandas as pd
import numpy as np
import os
import glob
import time
import sqlalchemy as sa
from sql_utils import get_ax_engine
from pathlib import Path
from datetime import datetime
from config.settings import (
    AX_FORWARD_REPLEN_SHARE,
    SOURCE_WORKBOOK_PATTERN,
    SOURCE_WORKBOOK_STALE_DAYS,
)
from output_paths import INGESTION_OUTPUT_DIR, PROJECT_ROOT, ensure_phase1_output_dirs
#from sharepoint_source import get_source_file


# ══════════════════════════════════════════════════════════════════════════════
# Project Paths — resolved relative to this script's location so there are
# no hardcoded absolute paths.
# ══════════════════════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_DIR = PROJECT_ROOT / "Source"


# ══════════════════════════════════════════════════════════════════════════════
# MAPPING TABLES
# These replicate the lookup tables found in the legacy Active Storage Tool's
# "Product Group" and "Size Group" sheets.  The Python equivalent of the
# XLOOKUP formulas in columns J3 and K3 of the 'Fwd Demand File' sheet.
# ══════════════════════════════════════════════════════════════════════════════

# Division → ProductGroupCode  (from 'Product Group' sheet, cols A:B)
DIVISION_TO_PGC = {
    "Adult Apparel":      "AAP",
    "Adult Sleep":        "ASL",
    "Baby Accessories":   "ACC",
    "Baby Apparel":       "BAP",
    "Baby Sleepwear":     "BSL",
    "Boys":               "BOY",
    "Collab Accessories": "COL",
    "Collab Apparel":     "COL",
    "Collab Sleepwear":   "CSL",
    "Girls":              "GIR",
    "Kids Accessories":   "ACC",
    "Kids Unders":        "KUN",
    "Kids Unisex":        "KNI",
    "Kids Unisex Sleep":  "KUS",
    "License Accessories":"ACC",
    "Misc/Misc":          "OTH",
    # Newer divisions observed in recent source data that were not in the
    # original BRG lookup table — mapped to their nearest equivalent.
    "Other":              "OTH",
    "W Adult Sleep":      "ASL",
    "W Baby Accessories": "ACC",
    "W Baby Apparel":     "BAP",
    "W Baby Sleepwear":   "BSL",
    "W Boys":             "BOY",
    "W Girls":            "GIR",
    "W Kids Unders":      "KUN",
    "W Kids Unisex Sleep":"KUS",
    "W Other":            "OTH",
}

# Item-specific corrections for AX hierarchy labels that are merchandised under
# a broad/collab division but should slot with their operational category.
ITEM_PRODUCT_GROUP_OVERRIDES = {
    "47054": "KUS",  # KU Swedish Heart LJ
    "47057": "KUS",  # KU Peanuts Hol LJ
}

# Size → SizeGroupCode  (from 'Size Group' sheet, cols F:H)
# The legacy tool uses XLOOKUP against a two-column table.  We flatten it
# into a direct dictionary for performance.
SIZE_TO_SGC = {}
_xs = ["1", "80", "85", "90", "80_85", "80/90", "XS", "XXS", "XS/S",
       "1/2", "2", "2/4"]  # compound baby/toddler sizes → X (extra-small)
_s  = ["5", "7", "100", "110", "1Y", "2Y", "S", "S/M",
       "5/7", "7/9", "100/110"]  # compound kids sizes → S (small)
_m  = ["9", "10", "120", "130", "3Y", "4Y", "M", "M/L",
       "12/1Y", "1Y/3Y", "120/130"]  # compound mid-range sizes → M (medium)
_l  = ["11", "12", "140", "150", "160", "13Y", "L", "L/XL", "XL", "XXL", "XL/XXL",
       "10/12", "3Y/5Y", "140/150"]  # compound larger sizes → L (large)
_i  = ["50", "60", "65", "70", "75", "NB", "1/4"]
_o  = ["OS"]
for s in _xs:
    SIZE_TO_SGC[s] = "X"
for s in _s:
    SIZE_TO_SGC[s] = "S"
for s in _m:
    SIZE_TO_SGC[s] = "M"
for s in _l:
    SIZE_TO_SGC[s] = "L"
for s in _i:
    SIZE_TO_SGC[s] = "I"
for s in _o:
    SIZE_TO_SGC[s] = "O"


# ══════════════════════════════════════════════════════════════════════════════
# CONTROL PARAMETERS
# These mirror the values on the 'Control and Dashboard' sheet of the legacy
# Active Storage Tool.  They drive velocity classification, putaway indicator
# logic, and the slot-requirement math.
# ══════════════════════════════════════════════════════════════════════════════

# --- Velocity thresholds (rows 16-19, cols B-D) ---
# The legacy formula in AG17 classifies SKUs into tiers based on the sum of
# their first 13 weeks of demand (column AF in Slotting Calcs).
# If demand <= 20 → C, <= 40 → B, <= 100 → A, > 100 → AA
VELOCITY_THRESHOLDS = [
    ("AA", 100),   # > 100 units in 13 weeks
    ("A",   40),   # > 40 units
    ("B",   20),   # > 20 units
    ("C",    0),   # everything else
]

# --- Days of Supply per velocity group (rows 16-19, col E) ---
# This is the number of days of inventory the DC wants to keep on the pick
# floor for each velocity tier.  The legacy tool looks this up via:
#   AS17 = INDEX('Control and Dashboard'!$E$15:$E$19, MATCH(AG17, ...))
# Currently all tiers use 6 days, but this is configurable in the tool.
DAYS_OF_SUPPLY = {"AA": 6, "A": 6, "B": 6, "C": 6}

# --- DOS buffer (row 7, col H) ---
# Extra days added to the Days of Supply when computing target inventory.
# Formula: AT17 = AS17 + 'Control and Dashboard'!$H$7
DOS_BUFFER = 0

# --- Quantity threshold (row 6, col H) ---
# A SKU's weekly demand must exceed this value to be considered "active"
# for slot assignment.  The formula in BX17 checks:
#   IF(weekly_target_pieces < threshold, 0, ROUNDUP(...))
QTY_THRESHOLD = 0.05

# --- Slot round-up factor (row 8, col H) ---
# After computing ROUNDUP(target_pieces / case_qty), this factor is added.
# It prevents zero-rounding edge cases on low-demand SKUs.
# Formula: BX17 = ... ROUNDUP(AW17 / AV17, 0) + $H$8
SLOT_ROUND_UP_FACTOR = 0.1

# --- Default case quantity (row 9, col H) ---
# Fallback carton size when the Case Qty sheet has no data for a SKU.
# Formula: AV17 = XLOOKUP(SKU, 'Case Qty'!A:A, 'Case Qty'!J:J, $H$9)
DEFAULT_CASE_QTY = 36

# --- Carton zone classification (from case_qty_calcs_reference.md §10) ---
# Classifies Bulk zones by name (immune to the column-shift bug in the legacy
# Excel pivot).  Bulk52 is permanently decommissioned in AX.
HALF_CARTON_ZONES = {"Bulk100"}
FULL_CARTON_ZONES = {"Bulk50", "Bulk56", "Bulk60", "Bulk70", "Bulk80", "Bulk90"}
PICKABLE_FLOOR_PROFILES = {"Picking", "Picking A", "Picking D", "PalletPicking"}
REPLENISHMENT_TARGET_PROFILES = {"Picking", "Picking A", "PalletPicking"}
# Note: 'Overflow' here is the AX LOCPROFILEID value (location profile name).
# The corresponding AX ZONEID is 'OVFLO'. The SQL filters on LOCPROFILEID;
# OVFLO is the zone assigned to those locations in the slot map.
REPLENISHMENT_SOURCE_PROFILES = {"Bulk", "Overflow"}
GUARDRAIL_ON_HAND_PROFILES = REPLENISHMENT_TARGET_PROFILES | REPLENISHMENT_SOURCE_PROFILES
# Inventory-classification coverage is broader than replenishment targeting:
# Picking D / Zone D receives returns and should get a forecast-zone
# classification, but replenishment directives should not target it.
CLASSIFICATION_ON_HAND_PROFILES = PICKABLE_FLOOR_PROFILES | REPLENISHMENT_SOURCE_PROFILES
EXCLUDED_CLASSIFICATION_ZONES = {"Current Pick"}

# --- Putaway indicator thresholds (rows 7-9, cols K-M) ---
# Formula: AQ17 =
#   IF "No Demand"          → 0 (Reserve)
#   IF weeks_until <= 2     → 1 (Active)
#   IF weeks_until <= 8     → 0 (Reserve)
#   ELSE                    → 2 (Offsite)
PUTAWAY_ACTIVE_WEEKS = 2
PUTAWAY_RESERVE_WEEKS = 8

# --- Hardcoded CSV fields ---
# These columns in the AX CSV are static constants, not calculated.
REPLENISHMENT_THRESHOLD = 3       # Overridden by AX location profile for Gaylords
PRODUCT_STAGE = "TBD - TEXT"
RETURN_ACTION = "TBD - TEXT"
RETURN_ACTION_DATE = "12/25/2022"
NVAR_EXPECTED_QTY = 0


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    """Parse command-line options for technical and operator-facing runs."""
    parser = argparse.ArgumentParser(
        description="Generate AX Forward Demand and Required Slots ingestion files."
    )
    parser.add_argument(
        "--operator-mode",
        "--quiet",
        action="store_true",
        dest="operator_mode",
        help=(
            "Show a concise, operator-friendly progress log. "
            "Default mode keeps the detailed technical output."
        ),
    )
    parser.add_argument(
        "--prompt-copy-to-ax-share",
        action="store_true",
        help="Ask whether to copy the generated AX Forward Demand CSV to the AX pickup share.",
    )
    parser.add_argument(
        "--copy-to-ax-share",
        action="store_true",
        help="Copy the generated AX Forward Demand CSV to the AX pickup share without prompting.",
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        help=(
            "Use a specific Product Info workbook instead of selecting the latest "
            "file from Source/. Intended for forecast-candidate round-trip tests."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=INGESTION_OUTPUT_DIR,
        help=(
            "Directory for generated Forward Demand, Required Slots, exceptions, "
            "and the SKU ledger. Defaults to Output/Ingestion."
        ),
    )
    return parser.parse_args()


def run_pipeline_step(label: str, func: Callable[[], Any], *, operator_mode: bool) -> Any:
    """Run a pipeline step, suppressing detailed stdout in operator mode.

    Args:
        label: Descriptive label of the step for logging.
        func: Callback function to execute.
        operator_mode: True to run in quiet/operator mode.

    Returns:
        The return value of func.
    """
    if not operator_mode:
        return func()

    print(f"[*] {label}...")
    with contextlib.redirect_stdout(io.StringIO()):
        result = func()
    print(f"    {label}: complete")
    return result


def read_last_refreshed_timestamp(filepath: Path) -> pd.Timestamp:
    """
    Reads LAST REFRESHED!C5 from a Product Info workbook.

    This is more reliable than the local file modified time when source files are
    manually downloaded from SharePoint onto different laptops.
    """
    try:
        df = pd.read_excel(
            filepath,
            sheet_name="LAST REFRESHED",
            header=None,
            usecols=[2],  # Column C
            nrows=5,
            engine="calamine",
        )
    except Exception:
        return pd.NaT

    if df.empty or len(df.index) < 5:
        return pd.NaT

    refresh_value = df.iloc[4, 0]  # C5
    try:
        return pd.Timestamp(refresh_value)
    except (TypeError, ValueError):
        return pd.NaT


def get_latest_source_file() -> Path:
    """
    Finds the best Product Info workbook in Source/.

    Selection order:
      1. Highest LAST REFRESHED!C5 timestamp across matching files
      2. Filesystem modified time if LAST REFRESHED is unavailable
    """
    pattern = str(SOURCE_DIR / SOURCE_WORKBOOK_PATTERN)
    files = [Path(p) for p in glob.glob(pattern)]
    if not files:
        raise FileNotFoundError(f"No source files matching: {pattern}")

    ranked_files = []
    for path in files:
        refresh_ts = read_last_refreshed_timestamp(path)
        ranked_files.append((path, refresh_ts, path.stat().st_mtime))

    refresh_ranked = [entry for entry in ranked_files if pd.notna(entry[1])]
    if refresh_ranked:
        latest, refresh_ts, _mtime = max(refresh_ranked, key=lambda item: (item[1], item[2]))
        print(
            f"[*] Source file: {latest.name} "
            f"(selected by LAST REFRESHED: {pd.Timestamp(refresh_ts).strftime('%Y-%m-%d %H:%M:%S')})"
        )
        return latest

    latest = max(files, key=os.path.getmtime)
    print(f"[*] Source file: {latest.name} (LAST REFRESHED unavailable; using file modified time)")
    return latest


def print_operator_source_preflight(source_file: Path) -> None:
    """Show the operator which workbook will be used and whether it looks fresh."""
    refresh_ts = read_last_refreshed_timestamp(source_file)
    print("\nSource workbook check:")
    print(f"  Folder: {SOURCE_DIR}")
    print(f"  Workbook selected: {source_file.name}")

    if pd.isna(refresh_ts):
        modified_ts = datetime.fromtimestamp(source_file.stat().st_mtime)
        print("  LAST REFRESHED: unavailable")
        print(f"  File modified: {modified_ts:%Y-%m-%d %H:%M:%S}")
        print("  Warning: please confirm this is the latest SharePoint download.")
        return

    age_days = max(0, (datetime.now() - pd.Timestamp(refresh_ts).to_pydatetime()).days)
    print(f"  LAST REFRESHED: {pd.Timestamp(refresh_ts).strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Workbook age: {age_days} day(s)")

    if age_days > SOURCE_WORKBOOK_STALE_DAYS:
        print(
            "  Warning: this workbook appears older than "
            f"{SOURCE_WORKBOOK_STALE_DAYS} days."
        )
        print("  Please confirm the latest Product Info for BRG file was downloaded.")


def print_operator_existing_output_warning(paths: list[Path]) -> None:
    """Warn operators when a same-day run will replace existing output files."""
    existing_paths = [path for path in paths if path.exists()]
    if not existing_paths:
        return

    print("    Note: this run will replace existing output file(s):")
    for path in existing_paths:
        print(f"      {path.name}")


def prompt_yes_no(question: str, *, default: bool = False) -> bool:
    """Ask a yes/no question and return the operator's choice."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{question} {suffix}: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer Y or N.")


def copy_file_to_ax_share(source_path: Path, destination_dir: Path) -> Path:
    """Copy a generated CSV to the AX pickup share, verifying size after copy."""
    if not source_path.exists():
        raise FileNotFoundError(f"Generated file not found: {source_path}")

    if not destination_dir.exists():
        raise FileNotFoundError(f"AX pickup folder is not reachable: {destination_dir}")

    destination_path = destination_dir / source_path.name
    temp_path = destination_dir / (
        f".{source_path.stem}.{datetime.now():%Y%m%d_%H%M%S}.{os.getpid()}.tmp"
    )

    try:
        shutil.copy2(source_path, temp_path)
        if temp_path.stat().st_size != source_path.stat().st_size:
            raise OSError("Copied file size does not match the generated file size.")
        os.replace(temp_path, destination_path)
        if destination_path.stat().st_size != source_path.stat().st_size:
            raise OSError("Final AX pickup file size does not match the generated file size.")
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return destination_path


def maybe_copy_to_ax_share(csv_path: Path, *, prompt: bool, force_copy: bool) -> Path | None:
    """Optionally copy the Forward Demand CSV to the configured AX pickup folder."""
    if not prompt and not force_copy:
        return None

    destination_dir = Path(AX_FORWARD_REPLEN_SHARE)
    print("\nAX pickup folder:")
    print(f"  {destination_dir}")

    if prompt:
        if not prompt_yes_no("Copy the AX Forward Demand file to this folder now?"):
            print("AX pickup copy skipped. Copy the file manually before the AX import.")
            return None

    destination_path = destination_dir / csv_path.name
    if destination_path.exists() and prompt:
        print(f"Destination file already exists: {destination_path.name}")
        if not prompt_yes_no("Replace the existing file in the AX pickup folder?"):
            print("AX pickup copy skipped. Existing remote file was left unchanged.")
            return None

    print("Copying AX Forward Demand file to pickup folder...")
    try:
        copied_path = copy_file_to_ax_share(csv_path, destination_dir)
    except Exception as exc:
        print(f"WARNING: AX pickup copy failed: {exc}")
        print("Please copy the AX Forward Demand file manually before the AX import.")
        return None

    print("AX pickup copy completed:")
    print(f"  {copied_path}")
    return copied_path


def parse_sku(sku_str: Any) -> tuple[Any, Any, Any]:
    """Splits a SKU string into (Item, Color, Size).

    Legacy formula equivalent (Fwd Demand File):
      G3 = LEFT(F3, 5)          → Item (first 5 chars)
      H3 = MID(F3, 7, 3)        → Color (chars 7-9)
      I3 = RIGHT(F3, LEN-10)    → Size (everything after char 10)

    We use split("-") instead because the delimiter-based approach is more
    robust against variable-length item codes in newer data.

    Args:
        sku_str: Input SKU identifier (typically Item-Color-Size).

    Returns:
        A tuple of (Item, Color, Size). If invalid, returns nan/blank fallbacks.
    """
    if pd.isna(sku_str):
        return (np.nan, np.nan, np.nan)
    parts = str(sku_str).split("-")
    if len(parts) >= 3:
        return (parts[0], parts[1], "-".join(parts[2:]))
    elif len(parts) == 2:
        return (parts[0], parts[1], "")
    return (sku_str, "", "")


def normalize_dimension_code(value: Any) -> str:
    """Normalize parsed AX dimension codes without inventing missing values.

    Args:
        value: Input raw dimension code (e.g. color or size).

    Returns:
        Cleaned uppercase string, or empty string if null/blank.
    """
    if is_blank_or_na(value):
        return ""
    return str(value).strip().upper()


def format_sku(item: str, color: str, size: str) -> str:
    """Rebuild SKU from normalized components while preserving blank-size form.

    Args:
        item: Canonical product item code.
        color: Cleaned color code.
        size: Cleaned size code.

    Returns:
        Reconstructed SKU string.
    """
    if color == "":
        return f"{item}--{size}" if size else f"{item}--"
    if size == "":
        return f"{item}-{color}"
    return f"{item}-{color}-{size}"


def normalize_sku(value: Any) -> str:
    """Canonicalize SKU casing so AX-equivalent variants dedupe before output.

    AX/SQL is case-insensitive for the Forecast replenishment staging key
    (ITEM, SIZE_, COLOR). The May 12, 2026 import failed with 0 staging rows
    because on-hand data carried lowercase dimensions while forecast/product
    attributes carried uppercase variants of the same SKU. Normalize before
    any source-universe union or aggregation so those rows collapse safely here
    instead of colliding inside DIXF/SSIS.

    Args:
        value: Input SKU representation.

    Returns:
        A fully normalized and uppercase canonical SKU string.
    """
    if pd.isna(value):
        return ""
    raw = str(value).strip()
    if "-" not in raw:
        return raw
    item, color, size = parse_sku(raw)
    item = "" if pd.isna(item) else str(item).strip()
    if item == "":
        return ""
    return format_sku(item, normalize_dimension_code(color), normalize_dimension_code(size))


def map_size_group(size_str: Any) -> str:
    """Maps a size string to its SizeGroupCode. Unknown sizes default to 'U'.

    Args:
        size_str: Size code to map.

    Returns:
        The mapped size group code (e.g., 'X', 'S', 'M', 'L', 'I', 'O', or 'U').
    """
    if pd.isna(size_str):
        return "U"
    return SIZE_TO_SGC.get(str(size_str).strip().upper(), "U")


def calculate_velocity(demand_13wk: Any) -> str:
    """Assigns a velocity tier based on 13-week forecast demand.

    This replicates the nested IF in the legacy Slotting Calcs sheet (AG17):
      =IF(AF17 <= D19, B19,           -- <= 20  → "C"
        IF(AF17 <= D18, B18,           -- <= 40  → "B"
          IF(AF17 <= D17, B17, B16)))   -- <= 100 → "A", else "AA"

    The thresholds come from 'Control and Dashboard' rows 16-19.

    Args:
        demand_13wk: Cumulative unit demand over first 13 weeks.

    Returns:
        The velocity classification tier string.
    """
    if pd.isna(demand_13wk) or demand_13wk <= 20:
        return "C"
    elif demand_13wk > 100:
        return "AA"
    elif demand_13wk > 40:
        return "A"
    else:  # 20 < demand <= 40
        return "B"


def calculate_putaway(weekly_demands: list[float], week_dates: list[pd.Timestamp], today: datetime) -> int:
    """Determines the Putaway Indicator for a SKU based on WHEN demand begins.

    Replicates the legacy formula chain from Slotting Calcs (AO17-AQ17).
    The weekly forecast uses Sunday-start weeks, so Week 1's start date may
    be a few days before today but still represents the CURRENT week.

    Step 1 -- AO17: Find the first week with demand >= QTY_THRESHOLD.
    Step 2 -- AP17: Convert to weeks from today: (date - TODAY()) / 7
    Step 3 -- AQ17: Classify:
        <= 2 weeks  -> 1 (Active)
        <= 8 weeks  -> 0 (Reserve)
        > 8 weeks   -> 2 (Offsite)
        No demand   -> 0 (Reserve)

    Args:
        weekly_demands: list of weekly demand numbers.
        week_dates: list of dates for the week columns.
        today: The reference current date.

    Returns:
        The putaway indicator code (0 = Reserve, 1 = Active, 2 = Offsite).
    """
    # Step 1: Check if total demand across all weeks is negligible
    total_demand = sum(weekly_demands)
    if total_demand < QTY_THRESHOLD * 26:  # < 1.3 units across 26 weeks
        return 0  # "No Demand" -> Reserve

    # Step 2: Find the first week where demand >= QTY_THRESHOLD
    first_demand_date = None
    for demand, date in zip(weekly_demands, week_dates):
        if demand >= QTY_THRESHOLD:
            first_demand_date = date
            break

    if first_demand_date is None:
        return 0  # Reserve

    # Step 3: Calculate weeks from today to first demand
    weeks_until = (first_demand_date - today).days / 7.0

    # Step 4: Classify
    # Note: We are not currently sending anything to 'Offsite'. We might revisit
    # the criteria later because we do send Inbound boxes to a different site
    # (leased space) when constrained with space, usually around peak season.
    if weeks_until <= PUTAWAY_ACTIVE_WEEKS:    # <= 2 weeks
        return 1  # Active
    elif weeks_until <= PUTAWAY_RESERVE_WEEKS:  # <= 8 weeks
        return 0  # Reserve
    else:
        return 2  # Offsite


def calculate_required_slots(weekly_demand: float, velocity: str, case_qty: float) -> float:
    """Estimates the number of physical pick-face slots a SKU needs.

    This replicates the slot-requirement math from the legacy Slotting Calcs sheet.
    The legacy tool computes this for EACH of the 26 forecast weeks and then takes
    the MAX.  Our pipeline simplifies by using the peak 13-week demand divided by
    13 to get an average weekly rate, which is a close approximation.

    Legacy formula chain (Slotting Calcs, columns AW-CW, row 17):

    Step 1 — Target pieces to keep on the pick floor for one week:
      AW17 = (Weekly_Demand / 7) * Target_Days_of_Supply
      where Target_Days_of_Supply = Days_of_Supply[velocity] + DOS_Buffer
      Example: (50 units/week / 7) * (6 + 0) = 42.86 pieces

    Step 2 — Convert pieces to slots (one slot holds one carton):
      BX17 = IF(AW17 < QTY_THRESHOLD, 0,
                ROUNDUP(AW17 / Case_Qty, 0) + SLOT_ROUND_UP_FACTOR)
      Example: IF(42.86 < 0.05, 0, ROUNDUP(42.86 / 36, 0) + 0.1) = 2.1

    Step 3 — The legacy tool takes MAX across 26 weeks (CX17 = MAX(BX:CW)).
             Our approximation uses the average weekly demand instead.

    Parameters:
        weekly_demand: Average weekly demand (13Wk_Demand / 13)
        velocity:      Velocity tier (AA, A, B, C)
        case_qty:      Estimated carton quantity for this SKU

    Returns:
        Estimated number of required slots (float, not rounded to int).
    """
    dos = DAYS_OF_SUPPLY.get(velocity, 6) + DOS_BUFFER
    target_pieces = (weekly_demand / 7.0) * dos

    if target_pieces < QTY_THRESHOLD:
        return 0.0

    # Avoid division by zero if case_qty is missing or zero
    if pd.isna(case_qty) or case_qty <= 0:
        case_qty = DEFAULT_CASE_QTY

    slots = np.ceil(target_pieces / case_qty) + SLOT_ROUND_UP_FACTOR
    return slots


def is_blank_or_na(value: Any) -> bool:
    """Treats NaN, blanks, and 'n/a' strings as missing values.

    Args:
        value: The value to evaluate.

    Returns:
        True if the value represents a missing attribute.
    """
    if pd.isna(value):
        return True
    return str(value).strip().lower() in {"", "n/a"}


def map_product_group_code(division: Any) -> str:
    """Map Division to ProductGroupCode, including legacy AX label variants.

    Args:
        division: The raw division string.

    Returns:
        The mapped ProductGroupCode, defaulting to 'U' (Unknown) if unmapped.
    """
    if is_blank_or_na(division):
        return "U"

    value = str(division).strip()
    candidates = [value]
    if value.upper().startswith("LEG"):
        candidates.append(value[3:].strip())

    for candidate in candidates:
        product_group = DIVISION_TO_PGC.get(candidate)
        if product_group:
            return product_group
    return "U"


def product_group_override_for_item(item: Any) -> str | None:
    """Return a ProductGroupCode override for known item-level corrections.

    Args:
        item: Item number/identifier to look up overrides for.

    Returns:
        The matching ProductGroupCode override or None if not found.
    """
    if is_blank_or_na(item):
        return None
    return ITEM_PRODUCT_GROUP_OVERRIDES.get(str(item).strip())


def parse_forecast_source(
    in_weekly: bool,
    in_14day: bool,
    in_on_hand: bool = False,
    in_inbound: bool = False,
) -> str:
    """Summarizes which forecast tabs or operational sources contributed a SKU.

    Args:
        in_weekly: True if SKU is present in weekly forecast.
        in_14day: True if SKU is present in 14-day forecast.
        in_on_hand: True if SKU is present in on-hand inventory.
        in_inbound: True if SKU is present in inbound shipments.

    Returns:
        A text description of the SKU's source origins.
    """
    parts = []
    if in_weekly:
        parts.append("Weekly")
    if in_14day:
        parts.append("14-Day")
    if in_on_hand:
        parts.append("On-Hand")
    if in_inbound:
        parts.append("Inbound/Cubiscan")
        
    if not parts:
        return "Unknown"
    return " + ".join(parts)


def summarize_missing_fields(row: dict[str, Any] | pd.Series, field_names: list[str]) -> str:
    """Returns a comma-separated list of missing hierarchy fields for a row.

    Args:
        row: The dictionary or pandas Series representing a row.
        field_names: A list of field names to check for missing values.

    Returns:
        A comma-separated string of the missing field names.
    """
    missing_fields = [field for field in field_names if is_blank_or_na(row.get(field))]
    return ", ".join(missing_fields)


def validate_ax_upload_ready(df_out: pd.DataFrame) -> None:
    """Fail fast on conditions that cause AX DIXF staging failures.

    HAFORECASTREPLENISHMENTENTITY has a unique case-insensitive staging key on
    PARTITION, EXECUTIONID, DEFINITIONGROUP, ITEM, SIZE_, COLOR. We cannot see
    execution fields in the CSV, so Item/Color/Size must be unique in the file.
    """
    duplicate_keys = df_out.duplicated(["Item", "Color", "Size"], keep=False)
    if duplicate_keys.any():
        samples = (
            df_out.loc[duplicate_keys, ["SKU", "Item", "Color", "Size"]]
            .sort_values(["Item", "Color", "Size", "SKU"])
            .head(10)
            .to_dict("records")
        )
        raise ValueError(
            "AX upload blocked: duplicate canonical Item/Color/Size rows detected. "
            f"Sample rows: {samples}"
        )

    blank_color = df_out["Color"].apply(is_blank_or_na)
    if blank_color.any():
        samples = df_out.loc[blank_color, ["SKU", "Item", "Color", "Size"]].head(10).to_dict("records")
        raise ValueError(
            "AX upload blocked: blank Color rows detected. "
            f"Sample rows: {samples}"
        )


def build_hierarchy_fallback(
    df_hier: pd.DataFrame,
    key_col: str,
    fallback_name: str,
) -> pd.DataFrame:
    """
    Builds a hierarchy fallback table keyed by Offer or Item.

    Classification uniqueness is based only on the core merch hierarchy fields.
    GoLiveDate is treated as supplemental metadata and does not block fallback.

    Performance note:
    This function is intentionally written in a vectorized style.  An earlier
    implementation looped through each Offer/Item group in Python and built many
    tiny temporary DataFrames, which pushed build_output() from ~4 seconds to
    ~89 seconds.  The current approach normalizes once, de-duplicates once, and
    uses groupby/merge operations across the full table to keep the fallback
    logic CPU-efficient.
    """
    core_fields = ["Division", "Department", "Class", "KeyCategoryView"]
    supplemental_fields = ["GoLiveDate", "SizeGroup"]
    fields_to_normalize = [key_col] + core_fields + supplemental_fields
    normalized = df_hier[fields_to_normalize].copy()
    normalized = normalized.dropna(subset=[key_col])
    for field in fields_to_normalize:
        normalized[field] = normalized[field].fillna("").map(lambda value: str(value).strip())
    normalized = normalized[normalized[key_col] != ""].copy()

    # A fallback key is only safe when its core merch classification is unique.
    # We deliberately ignore GoLiveDate here because newer colors often inherit
    # the same category assignment even when their launch dates differ.
    core_unique = normalized[[key_col] + core_fields].drop_duplicates()
    valid_keys = core_unique.groupby(key_col).size()
    valid_keys = valid_keys[valid_keys == 1].index

    fallback_df = core_unique[core_unique[key_col].isin(valid_keys)].copy()
    fallback_df = fallback_df.drop_duplicates(subset=[key_col], keep="first")

    for field in supplemental_fields:
        # Supplemental fields are filled only when the key resolves to a single
        # non-blank value.  They are metadata, not part of the classification key.
        field_values = normalized[[key_col, field]].copy()
        field_values = field_values[field_values[field] != ""].drop_duplicates()
        unique_counts = field_values.groupby(key_col).size()
        unique_keys = unique_counts[unique_counts == 1].index
        single_values = field_values[field_values[key_col].isin(unique_keys)].copy()
        single_values = single_values.drop_duplicates(subset=[key_col], keep="first")
        fallback_df = fallback_df.merge(single_values, on=key_col, how="left")
        fallback_df[field] = fallback_df[field].fillna("")

    print(f"      {fallback_name} fallback rows: {len(fallback_df)}")
    return fallback_df


# ══════════════════════════════════════════════════════════════════════════════
# DATA READERS
# Each function reads one sheet from the source file and returns a clean
# DataFrame ready for joining.
# ══════════════════════════════════════════════════════════════════════════════

def read_weekly_forecast(filepath: Path) -> tuple[pd.DataFrame, list, list[str]]:
    """
    Reads the 'Product Forecast Tool by Week' sheet.

    Layout: Row 0-2 = metadata, Row 3 = headers ('Row Labels' + date columns),
            Row 4+ = data.  Each cell is the forecasted demand for that SKU in
            that week.

    Returns:
        - DataFrame with SKU, 13Wk_Demand, and per-week demand columns
        - List of datetime objects for each week column (calendar dates)
        - List of week column names (for referencing demand values)

    The per-week data is needed for the PutawayIndicator calculation, which
    finds the FIRST week where demand >= QTY_THRESHOLD and computes how many
    weeks from TODAY that date is (legacy formula AO17 in Slotting Calcs).
    """
    print("    - Reading 'Product Forecast Tool by Week'...")
    df = pd.read_excel(filepath, sheet_name="Product Forecast Tool by Week", header=3, engine="calamine")
    df = df[df["Row Labels"].notna() & (df["Row Labels"] != "Grand Total")].copy()
    df = df.rename(columns={"Row Labels": "SKU"})

    wk_cols = [c for c in df.columns if c != "SKU" and str(c).strip().lower() != "grand total"]
    
    # The legacy 'Slotting Calcs' sheet exactly processes 26 week columns (D:AC).
    # We must limit to 26 weeks so SKUs whose only demand falls in week 27+ are
    # correctly classified as "No Demand" (0) instead of "Offsite" (2).
    wk_cols = wk_cols[:26]

    # Extract the calendar dates from the column headers.
    # The legacy tool's row 16 in Slotting Calcs contains these same dates,
    # which it uses to compute "weeks until demand begins" (AO17).
    week_dates = []
    for c in wk_cols:
        if isinstance(c, (datetime, pd.Timestamp)):
            week_dates.append(pd.Timestamp(c))
        else:
            try:
                week_dates.append(pd.Timestamp(c))
            except (ValueError, TypeError):
                week_dates.append(pd.NaT)

    for c in wk_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    df["SKU"] = df["SKU"].map(normalize_sku)
    df = df[df["SKU"] != ""].copy()
    df = df.groupby("SKU", as_index=False)[wk_cols].sum()

    # The legacy tool sums exactly the first 13 week columns for velocity
    # classification.  We round to 2 decimals to avoid floating-point edge
    # cases (e.g. 100.0000001 being classified as AA instead of A).
    first_13 = wk_cols[:13]
    df["13Wk_Demand"] = df[first_13].sum(axis=1).round(2)

    # Keep all week columns for the PutawayIndicator calculation
    keep_cols = ["SKU", "13Wk_Demand"] + wk_cols
    result = df[keep_cols].copy()
    print(f"      Weekly Forecast SKUs: {len(result)}, Week columns: {len(wk_cols)}")
    return result, week_dates, wk_cols


def read_on_hand_location_block(filepath: Path) -> pd.DataFrame:
    """
    Reads the right-side WITH License Plates block from On Hand by Location.

    Returns a normalized DataFrame with [WMSLOCATIONID, LOCPROFILEID, ZONEID, SKU, Physical].
    """
    df_raw = pd.read_excel(filepath, sheet_name="On Hand by Location", header=None, engine="calamine")

    # Right block: row 3 = headers, row 4+ = data
    df = df_raw.iloc[3:, [11, 14, 15, 18, 19]].copy()
    df.columns = ["WMSLOCATIONID", "LOCPROFILEID", "ZONEID", "SKU", "Physical"]
    df = df[df["SKU"].notna() & (df["SKU"] != "")].copy()
    df["SKU"] = df["SKU"].map(normalize_sku)
    df = df[df["SKU"] != ""].copy()
    df["Physical"] = pd.to_numeric(df["Physical"], errors="coerce").fillna(0)
    return df


def read_14day_forecast(filepath: Path) -> tuple[pd.DataFrame, str]:
    """
    Reads the 'SKU Level 14 Day Forecast' sheet.

    Layout: Row 0 = blank, Row 1 = dates, Row 2 = column labels, Row 3+ = data.
    Returns (DataFrame with SKU + FD1..FD14, forecast_start_date_string).
    """
    print("    - Reading 'SKU Level 14 Day Forecast'...")

    # Extract the forecast start date from the first date cell (Row 1, Col B)
    df_dates = pd.read_excel(filepath, sheet_name="SKU Level 14 Day Forecast",
                              header=None, nrows=2, engine="calamine")
    forecast_start = df_dates.iloc[1, 1]
    if isinstance(forecast_start, (datetime, pd.Timestamp)):
        forecast_start_str = f"{forecast_start.month}/{forecast_start.day}/{forecast_start.year}"
    else:
        forecast_start_str = str(forecast_start)

    # Read data with row 2 as header
    df = pd.read_excel(filepath, sheet_name="SKU Level 14 Day Forecast", header=2, engine="calamine")

    # Rename to standard FD1..FD14 column names
    cols = df.columns.tolist()
    rename = {cols[0]: "SKU"}
    for i in range(1, min(15, len(cols))):
        rename[cols[i]] = f"FD{i}"
    df = df.rename(columns=rename)

    keep = ["SKU"] + [f"FD{i}" for i in range(1, 15) if f"FD{i}" in df.columns]
    df = df[keep].copy()

    # Clean: drop non-SKU rows, coerce to int
    df = df[df["SKU"].notna()].copy()
    df["SKU"] = df["SKU"].map(normalize_sku)
    df = df[df["SKU"] != ""].copy()
    for c in keep[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    df = df.groupby("SKU", as_index=False)[keep[1:]].sum()

    print(f"      14-Day Forecast SKUs: {len(df)}, start date: {forecast_start_str}")
    return df, forecast_start_str


def read_product_attributes(filepath: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Reads the 'Product Attributes' sheet.

    Layout: Row 0-2 = metadata, Row 3 = headers, Row 4 = bracket names, Row 5+ = data.
    Left block (cols 0-8): Offer, SKU, Division, Department, Class, KCV, SizeGroup, GoLiveDate, OfferCount
    Right block (cols 10-12): SKU, Type(ProductStatus), Date(ProductStatusDate)

    Returns (hierarchy_df, status_df) — both deduplicated by SKU.
    """
    print("    - Reading 'Product Attributes'...")
    df = pd.read_excel(filepath, sheet_name="Product Attributes", header=3, engine="calamine")
    df = df.iloc[1:].copy().reset_index(drop=True)  # skip bracket-name row

    # Left block: hierarchy info used for Division → ProductGroupCode mapping
    left_cols = df.columns[:9].tolist()
    df_hier = df[left_cols].copy()
    df_hier.columns = ["Offer", "SKU", "Division", "Department", "Class",
                        "KeyCategoryView", "SizeGroup", "GoLiveDate", "OfferCount"]
    df_hier = df_hier[df_hier["Division"].notna() & (df_hier["Division"] != "")].copy()
    df_hier["SKU"] = df_hier["SKU"].map(normalize_sku)
    df_hier = df_hier[df_hier["SKU"] != ""].copy()
    df_hier = df_hier.drop_duplicates(subset=["SKU"], keep="first")
    parsed = df_hier["SKU"].apply(parse_sku)
    df_hier["Item"] = parsed.apply(lambda x: x[0])
    df_hier["Color"] = parsed.apply(lambda x: normalize_dimension_code(x[1]))
    df_hier["Offer"] = df_hier["Item"] + "-" + df_hier["Color"]

    # Right block: product status ("Active", "Discontinued", etc.)
    if len(df.columns) > 12:
        df_status = df[[df.columns[10], df.columns[11], df.columns[12]]].copy()
        df_status.columns = ["StatusSKU", "ProductStatus", "ProductStatusDate"]
        df_status = df_status[df_status["StatusSKU"].notna()].copy()
        df_status["StatusSKU"] = df_status["StatusSKU"].map(normalize_sku)
        df_status = df_status[df_status["StatusSKU"] != ""].copy()
        df_status = df_status.drop_duplicates(subset=["StatusSKU"], keep="first")
    else:
        df_status = pd.DataFrame(columns=["StatusSKU", "ProductStatus", "ProductStatusDate"])

    print(
        f"      Attributes: {len(df_hier)} unique SKUs, "
        f"Status: {len(df_status)} entries"
    )
    return df_hier, df_status


def read_load_data(filepath: Path) -> pd.DataFrame:
    """
    Reads the 'Load Data' sheet and returns MAX(LP Units) by SKU.

    Layout (from product_info_brg_reference.md §4):
      Row 1 = blank, Row 2 = headers, Row 3+ = data.
      Col 4 = SKU, Col 6 = LP Units.

    Returns DataFrame with columns [SKU, LoadMaxQty].
    """
    print("    - Reading 'Load Data'...")
    df = pd.read_excel(filepath, sheet_name="Load Data", header=1, engine="calamine")

    # Identify SKU and LP Units columns by position (col 4 and col 6)
    cols = df.columns.tolist()
    sku_col = cols[4]   # SKU
    lp_col = cols[6]    # LP Units

    df = df[[sku_col, lp_col]].copy()
    df.columns = ["SKU", "LP_Units"]

    # Filter out blank SKUs and Grand Total rows
    df = df[df["SKU"].notna() & (df["SKU"] != "")].copy()
    df["SKU"] = df["SKU"].map(normalize_sku)
    df = df[~df["SKU"].str.lower().isin(["grand total", ""])].copy()

    df["LP_Units"] = pd.to_numeric(df["LP_Units"], errors="coerce").fillna(0)

    # MAX(LP Units) grouped by SKU
    load_max = df.groupby("SKU")["LP_Units"].max().reset_index()
    load_max.columns = ["SKU", "LoadMaxQty"]

    print(f"      Load Data: {len(df)} rows -> {len(load_max)} unique SKUs")
    return load_max


def read_on_hand(filepath: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Reads the 'On Hand by Location' sheet (right block with License Plates)
    and returns half/full carton classification by SKU, plus all on-hand SKUs.

    Layout (from product_info_brg_reference.md §3):
      Right block starts at col 11.  We need:
        Col 14 = LOCPROFILEID, Col 15 = ZONEID, Col 18 = SKU, Col 19 = Physical
      Row 3 = headers, Row 4+ = data.

    Returns DataFrame with columns [SKU, FullCartonQty, HalfCartonQty, CartonSizeType].
    """
    print("    - Reading 'On Hand by Location' (right block)...")
    df = read_on_hand_location_block(filepath)
    # Include SKUs from profiles that should receive a forecast-zone
    # classification. This intentionally includes Picking D / Zone D returns so
    # they do not disappear from HAFORECASTREPLENISHMENTTABLE, while still
    # excluding Current Pick gift-card/admin rows and non-positive inventory.
    df_classification = df[
        df["LOCPROFILEID"].isin(CLASSIFICATION_ON_HAND_PROFILES)
        & ~df["ZONEID"].isin(EXCLUDED_CLASSIFICATION_ZONES)
        & (df["Physical"] > 0)
    ].copy()
    df_all_skus = df_classification[["SKU"]].drop_duplicates()
    classification_dist = df_classification.groupby("LOCPROFILEID")["SKU"].nunique().sort_index().to_dict()
    print(f"      On Hand classification profiles: {classification_dist}")

    # Filter to stock-source profiles.  Known Bulk zone IDs still drive the
    # half/full carton type classification below; Overflow (ZONEID: OVFLO) is
    # included as a replenishment source profile but does not create new
    # carton-type rules.
    df_bulk = df[df["LOCPROFILEID"].isin(REPLENISHMENT_SOURCE_PROFILES)].copy()
    source_dist = df_bulk["LOCPROFILEID"].value_counts().to_dict()
    print(f"      On Hand: {len(df)} total rows, source profile rows: {source_dist}")

    if df_bulk.empty:
        return pd.DataFrame(columns=["SKU", "FullCartonQty", "HalfCartonQty", "CartonSizeType"]), df_all_skus

    # Pivot: MAX(Physical) by SKU × ZONEID
    oh_pivot = df_bulk.pivot_table(
        index="SKU", columns="ZONEID", values="Physical", aggfunc="max", fill_value=0
    )

    # Classify using zone names (immune to column-shift bug)
    half_zones_present = [z for z in HALF_CARTON_ZONES if z in oh_pivot.columns]
    full_zones_present = [z for z in FULL_CARTON_ZONES if z in oh_pivot.columns]

    result = pd.DataFrame(index=oh_pivot.index)
    result["FullCartonQty"] = oh_pivot[full_zones_present].max(axis=1) if full_zones_present else 0
    result["HalfCartonQty"] = oh_pivot[half_zones_present].max(axis=1) if half_zones_present else 0

    # Classification logic (case_qty_calcs_reference.md §2.5)
    result["CartonSizeType"] = np.where(
        (result["HalfCartonQty"] > 0) & (result["FullCartonQty"] == 0), "Half Carton Only",
        np.where(
            (result["HalfCartonQty"] == 0) & (result["FullCartonQty"] > 0), "Full Carton Only",
            np.where(
                (result["HalfCartonQty"] == 0) & (result["FullCartonQty"] == 0), "n/a",
                "Half and Full Cartons"
            )
        )
    )

    result = result.reset_index()
    type_dist = result["CartonSizeType"].value_counts().to_dict()
    print(f"      OH Carton classification: {type_dist}")
    return result, df_all_skus


def read_guardrail_supply_skus() -> set[str]:
    """Query live AX supply signals that should block new Active putaway.

    This guardrail is intentionally conservative: if stock is already on the
    pick floor, in Bulk/Overflow (ZONEID: OVFLO), or actively moving to the
    pick floor, AX replenishment/work execution should satisfy demand before
    the forecast file sends another first carton directly to active storage.

    Returns:
        A set of canonical SKU strings with existing supply.
    """
    query = """
    WITH OnHandSupply AS (
        SELECT DISTINCT
            isum.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID AS SKU,
            CASE
                WHEN loc.LOCPROFILEID IN ('Bulk', 'Overflow') THEN 'OnHandSource'
                ELSE 'OnHandFloor'
            END AS Reason
        FROM INVENTSUM isum WITH (NOLOCK)
        JOIN INVENTDIM idim WITH (NOLOCK)
            ON isum.INVENTDIMID = idim.INVENTDIMID
            AND isum.DATAAREAID = idim.DATAAREAID
            AND isum.[PARTITION] = idim.[PARTITION]
        JOIN WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = idim.WMSLOCATIONID
            AND loc.DATAAREAID = idim.DATAAREAID
            AND loc.[PARTITION] = idim.[PARTITION]
        WHERE
            isum.DATAAREAID = 'ha'
            AND isum.PHYSICALINVENT > 0
            AND idim.INVENTLOCATIONID = '4010'
            AND idim.INVENTSITEID = 'HA USA'
            AND loc.LOCPROFILEID IN (
                'Picking', 'Picking A', 'PalletPicking', 'Bulk', 'Overflow'
            )
            AND loc.ZONEID NOT IN ('Current Pick')
    ),
    PendingPutToFloor AS (
        SELECT DISTINCT
            wkln.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID AS SKU,
            'PendingPutToFloor' AS Reason
        FROM WHSWORKLINE wkln WITH (NOLOCK)
        JOIN WHSWORKTABLE wktbl WITH (NOLOCK)
            ON wktbl.WORKID = wkln.WORKID
            AND wktbl.DATAAREAID = wkln.DATAAREAID
            AND wktbl.[PARTITION] = wkln.[PARTITION]
        JOIN INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = wkln.INVENTDIMID
            AND idim.DATAAREAID = wkln.DATAAREAID
            AND idim.[PARTITION] = wkln.[PARTITION]
        JOIN WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = wkln.WMSLOCATIONID
            AND loc.DATAAREAID = wkln.DATAAREAID
            AND loc.[PARTITION] = wkln.[PARTITION]
        WHERE
            wkln.DATAAREAID = 'ha'
            AND wktbl.INVENTLOCATIONID = '4010'
            AND loc.INVENTLOCATIONID = '4010'
            AND wkln.WORKTYPE = 2
            AND wkln.QTYWORK > 0
            AND wkln.WORKSTATUS IN (0, 1)
            AND wktbl.WORKSTATUS IN (0, 1)
            AND loc.LOCPROFILEID IN ('Picking', 'Picking A', 'PalletPicking')
    )
    SELECT DISTINCT
        SKU,
        Reason
    FROM (
        SELECT SKU, Reason FROM OnHandSupply
        UNION ALL
        SELECT SKU, Reason FROM PendingPutToFloor
    ) supply
    WHERE SKU IS NOT NULL
    """
    print("    - Querying live Guardrail 2 supply signals from AX...")
    engine = get_ax_engine()
    df = pd.read_sql_query(sa.text(query), engine)

    skus = {normalize_sku(sku) for sku in df["SKU"].dropna().unique()}
    skus.discard("")
    reason_counts = df.groupby("Reason")["SKU"].nunique().sort_index().to_dict()
    print(f"      Guardrail 2 supply signals: {reason_counts}")
    print(f"      Guardrail 2 total supply SKUs: {len(skus):,} unique SKUs")
    return skus


def read_guardrail_supply_skus_from_workbook(filepath: Path) -> set[str]:
    """Uses the Product Info workbook's On Hand snapshot as a fallback for Guardrail 2.

    Workbook data cannot see pending work, so this fallback only covers existing
    on-hand supply in floor and replenishment source profiles.

    Args:
        filepath: Path to the Excel workbook containing On Hand by Location data.

    Returns:
        A set of canonical SKU strings with existing supply in the workbook.
    """
    print("      Falling back to workbook snapshot from 'On Hand by Location'...")
    df = read_on_hand_location_block(filepath)
    df_guardrail = df[
        df["LOCPROFILEID"].isin(GUARDRAIL_ON_HAND_PROFILES)
        & (df["Physical"] > 0)
        & ~df["ZONEID"].isin(EXCLUDED_CLASSIFICATION_ZONES)
    ].copy()
    skus = {normalize_sku(sku) for sku in df_guardrail["SKU"].dropna().unique()}
    skus.discard("")
    profile_counts = df_guardrail.groupby("LOCPROFILEID")["SKU"].nunique().sort_index().to_dict()
    print(f"      Workbook Guardrail 2 on-hand profiles: {profile_counts}")
    print(f"      Workbook Guardrail 2 on-hand supply: {len(skus):,} unique SKUs")
    return skus


def get_guardrail_supply_skus(filepath: Path) -> tuple[set[str], str]:
    """Returns SKUs with existing or inbound supply plus the source used for Guardrail 2.

    Source order:
      1. Live AX SQL: on-hand floor/source supply plus pending put work to floor
      2. Product Info workbook snapshot: on-hand floor/source supply only
      3. Empty set if both sources fail

    Args:
        filepath: Path to reference Excel workbook.

    Returns:
        A tuple of (set of supply SKUs, source descriptor string).
    """
    try:
        return read_guardrail_supply_skus(), "AX"
    except Exception as exc:
        print(f"      WARNING: Could not query AX ({exc}).")

    try:
        return read_guardrail_supply_skus_from_workbook(filepath), "WorkbookSnapshot"
    except Exception as exc:
        print(f"      WARNING: Workbook fallback failed ({exc}). Guardrail 2 disabled.")
        return set(), "Unavailable"


def read_inbound_coverage_skus() -> set[str]:
    """Query live AX inbound/Cubiscan signals that should receive forecast rows.

    This is a coverage guardrail, not an Active-putaway guardrail.  These SKUs
    are added to the output universe so AX has a SlotTier / Cubiscan-to-active
    decision instead of producing blank Cubiscan labels for unforecasted freight.

    Returns:
        A set of canonical inbound/Cubiscan SKU strings.
    """
    query = """
    WITH CubeInventory AS (
        SELECT DISTINCT
            isum.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID AS SKU,
            'CubeInventory' AS Reason
        FROM INVENTSUM isum WITH (NOLOCK)
        JOIN INVENTDIM idim WITH (NOLOCK)
            ON isum.INVENTDIMID = idim.INVENTDIMID
            AND isum.DATAAREAID = idim.DATAAREAID
            AND isum.[PARTITION] = idim.[PARTITION]
        JOIN WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = idim.WMSLOCATIONID
            AND loc.DATAAREAID = idim.DATAAREAID
            AND loc.[PARTITION] = idim.[PARTITION]
            AND loc.INVENTLOCATIONID = idim.INVENTLOCATIONID
        WHERE
            isum.DATAAREAID = 'ha'
            AND isum.[PARTITION] = 5637144576
            AND isum.PHYSICALINVENT > 0
            AND idim.INVENTLOCATIONID = '4010'
            AND (
                UPPER(loc.WMSLOCATIONID) LIKE '%CUBE%'
                OR UPPER(loc.ZONEID) LIKE '%CUBE%'
                OR UPPER(loc.LOCPROFILEID) LIKE '%CUBE%'
            )
    ),
    RecentCubeWork AS (
        SELECT DISTINCT
            wkln.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID AS SKU,
            'RecentCubeWork' AS Reason
        FROM WHSWORKLINE wkln WITH (NOLOCK)
        JOIN WHSWORKTABLE wktbl WITH (NOLOCK)
            ON wktbl.WORKID = wkln.WORKID
            AND wktbl.DATAAREAID = wkln.DATAAREAID
            AND wktbl.[PARTITION] = wkln.[PARTITION]
        JOIN INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = wkln.INVENTDIMID
            AND idim.DATAAREAID = wkln.DATAAREAID
            AND idim.[PARTITION] = wkln.[PARTITION]
        WHERE
            wkln.DATAAREAID = 'ha'
            AND wkln.[PARTITION] = 5637144576
            AND idim.INVENTLOCATIONID = '4010'
            AND wktbl.CREATEDDATETIME >= DATEADD(day, -14, GETUTCDATE())
            AND wkln.ITEMID IS NOT NULL
            AND wkln.ITEMID <> ''
            AND (
                UPPER(ISNULL(wkln.WMSLOCATIONID, '')) LIKE '%CUBE%'
                OR UPPER(ISNULL(wktbl.WORKTEMPLATECODE, '')) LIKE '%CUBE%'
            )
    ),
    OpenInboundWork AS (
        SELECT DISTINCT
            wkln.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID AS SKU,
            'OpenInboundWork' AS Reason
        FROM WHSWORKLINE wkln WITH (NOLOCK)
        JOIN WHSWORKTABLE wktbl WITH (NOLOCK)
            ON wktbl.WORKID = wkln.WORKID
            AND wktbl.DATAAREAID = wkln.DATAAREAID
            AND wktbl.[PARTITION] = wkln.[PARTITION]
        JOIN INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = wkln.INVENTDIMID
            AND idim.DATAAREAID = wkln.DATAAREAID
            AND idim.[PARTITION] = wkln.[PARTITION]
        WHERE
            wkln.DATAAREAID = 'ha'
            AND wkln.[PARTITION] = 5637144576
            AND wktbl.INVENTLOCATIONID = '4010'
            AND idim.INVENTLOCATIONID = '4010'
            AND wkln.ITEMID IS NOT NULL
            AND wkln.ITEMID <> ''
            AND wkln.WORKSTATUS IN (0, 1)
            AND wktbl.WORKSTATUS IN (0, 1)
            AND wktbl.WORKTRANSTYPE IN (1, 7)
    )
    SELECT DISTINCT
        SKU,
        Reason
    FROM (
        SELECT SKU, Reason FROM CubeInventory
        UNION ALL
        SELECT SKU, Reason FROM RecentCubeWork
        UNION ALL
        SELECT SKU, Reason FROM OpenInboundWork
    ) inbound
    WHERE SKU IS NOT NULL
    """
    print("    - Querying live inbound/Cubiscan coverage signals from AX...")
    engine = get_ax_engine()
    df = pd.read_sql_query(sa.text(query), engine)

    skus = {normalize_sku(sku) for sku in df["SKU"].dropna().unique()}
    skus.discard("")
    reason_counts = df.groupby("Reason")["SKU"].nunique().sort_index().to_dict()
    print(f"      Inbound/Cubiscan coverage signals: {reason_counts}")
    print(f"      Inbound/Cubiscan coverage SKUs: {len(skus):,} unique SKUs")
    return skus


def get_inbound_coverage_skus() -> tuple[set[str], str]:
    """Returns live inbound/Cubiscan SKUs that should not be absent from AX forecast.

    There is no workbook fallback for this guardrail. The BRG workbook's Load
    Data can be broad or stale; live AX work/location signals are narrower and
    directly tied to the blank-label failure mode.

    Returns:
        A tuple of (set of coverage SKUs, source descriptor string).
    """
    try:
        return read_inbound_coverage_skus(), "AX"
    except Exception as exc:
        print(f"      WARNING: Could not query inbound/Cubiscan coverage from AX ({exc}).")
        return set(), "Unavailable"


def read_ax_product_hierarchy(items: list[str], chunk_size: int = 1000) -> pd.DataFrame:
    """
    Recover item hierarchy from AX product master tables.

    This mirrors the ItemMaster.sql pattern in ha-sql.  The direct
    ECORESPRODUCT corporate hierarchy fields are preferred, with the
    ECORESPRODUCTCATEGORY parent chain as a fallback.
    """
    item_list = sorted({str(item).strip() for item in items if not is_blank_or_na(item)})
    columns = [
        "Item",
        "AXHierarchyDivision",
        "AXHierarchyDepartment",
        "AXHierarchyClass",
        "AXHierarchyKeyCategoryView",
        "AXHierarchySource",
    ]
    if not item_list:
        return pd.DataFrame(columns=columns)

    query = sa.text(
        """
        SELECT
            it.ITEMID AS Item,
            direct_class.NAME AS AXDirectClass,
            direct_dept.NAME AS AXDirectDepartment,
            direct_div.NAME AS AXDirectDivision,
            low1.NAME AS AXHierarchyLeaf,
            parent_class.NAME AS AXParentClass,
            parent_dept.NAME AS AXParentDepartment,
            parent_div.NAME AS AXParentDivision
        FROM INVENTTABLE it WITH (NOLOCK)
        JOIN ECORESPRODUCT erp WITH (NOLOCK)
            ON erp.RECID = it.PRODUCT
            AND erp.PARTITION = it.PARTITION
        LEFT JOIN ECORESCATEGORY direct_class WITH (NOLOCK)
            ON direct_class.RECID = erp.HAECORESCATEGORYCLASS
            AND direct_class.PARTITION = erp.PARTITION
        LEFT JOIN ECORESCATEGORY direct_dept WITH (NOLOCK)
            ON direct_dept.RECID = erp.HAECORESCATEGORYDEPARTMENT
            AND direct_dept.PARTITION = erp.PARTITION
        LEFT JOIN ECORESCATEGORY direct_div WITH (NOLOCK)
            ON direct_div.RECID = erp.HAECORESCATEGORYDIVISION
            AND direct_div.PARTITION = erp.PARTITION
        LEFT JOIN ECORESPRODUCTCATEGORY epc WITH (NOLOCK)
            ON epc.PRODUCT = erp.RECID
            AND epc.PARTITION = erp.PARTITION
        LEFT JOIN ECORESCATEGORY low1 WITH (NOLOCK)
            ON epc.CATEGORY = low1.RECID
            AND low1.PARTITION = epc.PARTITION
        LEFT JOIN ECORESCATEGORY parent_class WITH (NOLOCK)
            ON low1.PARENTCATEGORY = parent_class.RECID
            AND parent_class.PARTITION = low1.PARTITION
        LEFT JOIN ECORESCATEGORY parent_dept WITH (NOLOCK)
            ON parent_class.PARENTCATEGORY = parent_dept.RECID
            AND parent_dept.PARTITION = parent_class.PARTITION
        LEFT JOIN ECORESCATEGORY parent_div WITH (NOLOCK)
            ON parent_dept.PARENTCATEGORY = parent_div.RECID
            AND parent_div.PARTITION = parent_dept.PARTITION
        WHERE it.DATAAREAID = 'ha'
          AND it.ITEMID IN :items
        """
    ).bindparams(sa.bindparam("items", expanding=True))

    frames = []
    engine = get_ax_engine()
    with engine.connect() as conn:
        for start in range(0, len(item_list), chunk_size):
            chunk = item_list[start:start + chunk_size]
            frames.append(pd.read_sql_query(query, conn, params={"items": chunk}))

    ax = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)
    if ax.empty:
        return pd.DataFrame(columns=columns)

    hierarchy_cols = [
        "AXDirectClass",
        "AXDirectDepartment",
        "AXDirectDivision",
        "AXHierarchyLeaf",
        "AXParentClass",
        "AXParentDepartment",
        "AXParentDivision",
    ]
    for col in hierarchy_cols:
        ax[col] = ax[col].fillna("").astype(str).str.strip()

    ax["AXHierarchyDivision"] = ax["AXDirectDivision"].where(
        ax["AXDirectDivision"].ne(""), ax["AXParentDivision"]
    )
    ax["AXHierarchyDepartment"] = ax["AXDirectDepartment"].where(
        ax["AXDirectDepartment"].ne(""), ax["AXParentDepartment"]
    )
    ax["AXHierarchyClass"] = ax["AXDirectClass"].where(
        ax["AXDirectClass"].ne(""), ax["AXParentClass"]
    )
    ax["AXHierarchyKeyCategoryView"] = ax["AXHierarchyLeaf"].where(
        ax["AXHierarchyLeaf"].ne(""), ax["AXHierarchyClass"]
    )
    ax["AXHierarchySource"] = ax["AXDirectDivision"].map(
        lambda value: "ECORESPRODUCT direct fields" if value else "ECORESPRODUCTCATEGORY parent chain"
    )
    ax["HasAXHierarchy"] = ax["AXHierarchyDivision"].ne("")
    ax = ax.sort_values(["Item", "HasAXHierarchy"], ascending=[True, False])
    ax = ax.drop_duplicates("Item", keep="first")
    return ax[columns]


def compute_case_qty(
    load_df: pd.DataFrame,
    oh_df: pd.DataFrame,
    hier_df: pd.DataFrame,
    sku_universe: pd.DataFrame,
) -> pd.DataFrame:
    """
    Implements the full 5-step Case Quantity logic chain from
    case_qty_calcs_reference.md §4.

    Steps:
      1. Join Load + OH data per SKU
      2. Determine FinalType (Half or Full)
      3. Compute FinalQty (Load preferred, OH Full fallback for Full; Load only for Half)
      4. Compute merch hierarchy averages (Class → Dept → Div)
      5. Apply hierarchical fallback: SKU → Class → Dept → Div → Default (36)

    Returns DataFrame with columns [SKU, CaseQty, CartonSizeType, CaseQtySource].
    """
    print("\n[*] Computing Case Quantities...")

    # Start with the full SKU universe
    df = sku_universe[["SKU"]].copy()

    # Join Load MAX qty
    df = df.merge(load_df, on="SKU", how="left")
    df["LoadMaxQty"] = df["LoadMaxQty"].fillna(0)

    # Join OH classification
    df = df.merge(oh_df[["SKU", "FullCartonQty", "HalfCartonQty", "CartonSizeType"]],
                  on="SKU", how="left")
    df["FullCartonQty"] = df["FullCartonQty"].fillna(0)
    df["HalfCartonQty"] = df["HalfCartonQty"].fillna(0)
    df["CartonSizeType"] = df["CartonSizeType"].fillna("SKU not in OH file")

    # Step 2: FinalType — only "Half Carton Only" passes through
    df["FinalType"] = np.where(
        df["CartonSizeType"] == "Half Carton Only", "Half Carton Only", "Full Carton Only"
    )

    # Step 3: FinalQty — per-SKU carton qty
    df["SKU_CaseQty"] = np.where(
        df["FinalType"] == "Full Carton Only",
        np.where(df["LoadMaxQty"] != 0, df["LoadMaxQty"], df["FullCartonQty"]),
        df["LoadMaxQty"]  # Half carton: always use Load only
    )

    # Join hierarchy for averaging
    df = df.merge(
        hier_df[["SKU", "Division", "Department", "Class"]],
        on="SKU", how="left"
    )

    # Step 4: Merch hierarchy averages (AVERAGEIF + ROUNDDOWN)
    # Note: Excel's AVERAGEIF includes zeros in the average.  We match that
    # behavior here.  The IFERROR wrapper in Excel returns 0 when all values
    # are zero, which np.floor naturally handles.
    valid = df[df["SKU_CaseQty"] > 0]

    class_avg = (valid.groupby("Class")["SKU_CaseQty"].mean()
                 .apply(np.floor).fillna(0).astype(int))
    dept_avg = (valid.groupby("Department")["SKU_CaseQty"].mean()
                .apply(np.floor).fillna(0).astype(int))
    div_avg = (valid.groupby("Division")["SKU_CaseQty"].mean()
               .apply(np.floor).fillna(0).astype(int))

    df["ClassAvg"] = df["Class"].map(class_avg).fillna(0).astype(int)
    df["DeptAvg"] = df["Department"].map(dept_avg).fillna(0).astype(int)
    df["DivAvg"] = df["Division"].map(div_avg).fillna(0).astype(int)

    # Step 5: Hierarchical fallback
    df["CaseQty"] = np.where(
        df["SKU_CaseQty"] != 0, df["SKU_CaseQty"],
        np.where(
            df["ClassAvg"] != 0, df["ClassAvg"],
            np.where(
                df["DeptAvg"] != 0, df["DeptAvg"],
                np.where(
                    df["DivAvg"] != 0, df["DivAvg"],
                    DEFAULT_CASE_QTY
                )
            )
        )
    )
    df["CaseQty"] = df["CaseQty"].astype(int)

    # Track the source of each CaseQty value for diagnostics
    df["CaseQtySource"] = np.where(
        df["SKU_CaseQty"] != 0, "SKU",
        np.where(
            df["ClassAvg"] != 0, "Class",
            np.where(
                df["DeptAvg"] != 0, "Dept",
                np.where(
                    df["DivAvg"] != 0, "Div",
                    "Default"
                )
            )
        )
    )

    # Print diagnostics
    source_dist = df["CaseQtySource"].value_counts()
    source_pct = (source_dist / len(df) * 100).round(1)
    print("    CaseQty source distribution:")
    for src in ["SKU", "Class", "Dept", "Div", "Default"]:
        if src in source_dist.index:
            print(f"      {src:>7}: {source_dist[src]:>6,} ({source_pct[src]}%)")
    print(f"    CaseQty stats: min={df['CaseQty'].min()}, max={df['CaseQty'].max()}, "
          f"mean={df['CaseQty'].mean():.1f}, median={df['CaseQty'].median():.0f}")

    return df[["SKU", "CaseQty", "CartonSizeType", "FinalType", "CaseQtySource"]]


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_output(
    df_weekly: pd.DataFrame,
    df_14day: pd.DataFrame,
    df_hier: pd.DataFrame,
    df_status: pd.DataFrame,
    forecast_start: Any,
    week_dates: list[pd.Timestamp],
    wk_cols: list[str],
    df_case_qty: pd.DataFrame,
    guardrail_supply_skus: set[str] | None = None,
    df_on_hand_skus: pd.DataFrame | None = None,
    inbound_coverage_skus: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Assembles the 36-column AX CSV output and computes Required Slots.

    This function mirrors the entire calculation flow of the legacy 'Fwd Demand File'
    and 'Slotting Calcs' sheets, producing the same output structure.

    Args:
        df_weekly: Weekly forecast dataframe.
        df_14day: 14-day daily forecast dataframe.
        df_hier: Product attributes hierarchy details.
        df_status: Product attributes status details.
        forecast_start: Forecast start date reference.
        week_dates: Week start dates list.
        wk_cols: List of weekly columns in forecast data.
        df_case_qty: Case quantity calculations dataframe.
        guardrail_supply_skus: Set of SKUs containing active supply.
        df_on_hand_skus: Dataframe of SKUs having on-hand inventory.
        inbound_coverage_skus: Set of inbound coverage SKUs.

    Returns:
        A tuple of (AX Forward Demand DataFrame, Required Slots DataFrame, Missing Attributes DataFrame).
    """
    print("\n[*] Building output...")
    today = datetime.now()

    # ── Step 1: Build SKU Universe ──────────────────────────────────────────
    # The legacy macro SKU_LIST_ACTIVE creates a deduplicated union of SKUs
    # from the Weekly Forecast and 14-Day Forecast sheets.  Only these SKUs
    # appear in the final CSV — NOT all 235K from Product Attributes.
    skus_weekly = df_weekly[["SKU"]].copy()
    skus_14day = df_14day[["SKU"]].copy()
    
    skus_to_concat = [skus_weekly, skus_14day]
    if df_on_hand_skus is not None:
        skus_to_concat.append(df_on_hand_skus[["SKU"]].copy())
    if guardrail_supply_skus:
        skus_to_concat.append(pd.DataFrame({"SKU": list(guardrail_supply_skus)}))
    if inbound_coverage_skus:
        skus_to_concat.append(pd.DataFrame({"SKU": list(inbound_coverage_skus)}))
        
    sku_universe = pd.concat(skus_to_concat).drop_duplicates(subset=["SKU"]).copy()
    print(f"    SKU universe (Weekly + 14-Day + On-Hand + Inbound/Cubiscan): {len(sku_universe)}")
    weekly_sku_set = set(skus_weekly["SKU"].dropna())
    day14_sku_set = set(skus_14day["SKU"].dropna())

    # ── Step 2: Join 13-week demand (for Velocity) ─────────────────────────
    df = sku_universe.merge(df_weekly, on="SKU", how="left")
    df["13Wk_Demand"] = df["13Wk_Demand"].fillna(0)
    # Fill week columns for SKUs that only appear in 14-day forecast
    for c in wk_cols:
        if c in df.columns:
            df[c] = df[c].fillna(0)

    # ── Step 3: Join 14-day forecast (for FD1-FD14 columns) ────────────────
    df = df.merge(df_14day, on="SKU", how="left")
    fd_cols = [f"FD{i}" for i in range(1, 15)]
    for c in fd_cols:
        if c not in df.columns:
            df[c] = 0
        df[c] = df[c].fillna(0).astype(int)
    df["InWeeklyForecast"] = df["SKU"].isin(weekly_sku_set)
    df["In14DayForecast"] = df["SKU"].isin(day14_sku_set)

    if df_on_hand_skus is not None:
        on_hand_sku_set = set(df_on_hand_skus["SKU"].dropna())
        if guardrail_supply_skus:
            on_hand_sku_set.update(guardrail_supply_skus)
    else:
        on_hand_sku_set = set()
    df["InOnHand"] = df["SKU"].isin(on_hand_sku_set)
    inbound_sku_set = set(inbound_coverage_skus or set())
    df["InInboundCoverage"] = df["SKU"].isin(inbound_sku_set)
    if inbound_sku_set:
        inbound_only = (
            df["InInboundCoverage"]
            & ~df["InWeeklyForecast"]
            & ~df["In14DayForecast"]
            & ~df["InOnHand"]
        )
        print(
            "    Inbound/Cubiscan coverage added "
            f"{int(inbound_only.sum()):,} otherwise-uncovered SKUs"
        )

    # ── Step 4: Join hierarchy from Product Attributes ─────────────────────
    # This is the Python equivalent of the VLOOKUP/INDEX-MATCH formulas in
    # columns A-E of the 'Fwd Demand File' sheet.
    df = df.merge(
        df_hier[["SKU", "Division", "Department", "Class", "KeyCategoryView", "SizeGroup", "GoLiveDate"]],
        on="SKU", how="left"
    )
    parsed = df["SKU"].apply(parse_sku)
    df["Item"] = parsed.apply(lambda x: x[0])
    df["Color"] = parsed.apply(lambda x: normalize_dimension_code(x[1]))
    df["Size"] = parsed.apply(lambda x: normalize_dimension_code(x[2]))
    df["SKU"] = df.apply(lambda row: format_sku(row["Item"], row["Color"], row["Size"]), axis=1)
    df["Offer"] = df["Item"] + "-" + df["Color"]

    recovered_offer_count = 0
    # Fallback order matters:
    # 1. exact SKU match from Product Attributes
    # 2. exact Offer (Item-Color) fallback for new sizes of an existing color
    # 3. Item-level fallback for new colors of an existing item
    #
    # This preserves as much specificity as possible before generalizing.
    offer_fallback = build_hierarchy_fallback(df_hier, "Offer", "Offer-level")
    if not offer_fallback.empty:
        df = df.merge(
            offer_fallback.rename(
                columns={
                    "Division": "OfferFallbackDivision",
                    "Department": "OfferFallbackDepartment",
                    "Class": "OfferFallbackClass",
                    "KeyCategoryView": "OfferFallbackKeyCategoryView",
                    "GoLiveDate": "OfferFallbackGoLiveDate",
                    "SizeGroup": "OfferFallbackSizeGroup",
                }
            ),
            on="Offer",
            how="left",
        )
        missing_before = df["Division"].apply(is_blank_or_na)
        for base_col, fallback_col in [
            ("Division", "OfferFallbackDivision"),
            ("Department", "OfferFallbackDepartment"),
            ("Class", "OfferFallbackClass"),
            ("KeyCategoryView", "OfferFallbackKeyCategoryView"),
            ("GoLiveDate", "OfferFallbackGoLiveDate"),
            ("SizeGroup", "OfferFallbackSizeGroup"),
        ]:
            fill_mask = df[base_col].apply(is_blank_or_na) & ~df[fallback_col].apply(is_blank_or_na)
            df.loc[fill_mask, base_col] = df.loc[fill_mask, fallback_col]

        recovered_offer_count = (missing_before & ~df["Division"].apply(is_blank_or_na)).sum()
        if recovered_offer_count > 0:
            print(f"    Offer-level Product Attributes fallback recovered {recovered_offer_count:,} SKUs")
        df = df.drop(
            columns=[
                "OfferFallbackDivision",
                "OfferFallbackDepartment",
                "OfferFallbackClass",
                "OfferFallbackKeyCategoryView",
                "OfferFallbackGoLiveDate",
                "OfferFallbackSizeGroup",
            ]
        )

    recovered_item_count = 0
    item_fallback = build_hierarchy_fallback(df_hier, "Item", "Item-level")
    if not item_fallback.empty:
        df = df.merge(
            item_fallback.rename(
                columns={
                    "Division": "ItemFallbackDivision",
                    "Department": "ItemFallbackDepartment",
                    "Class": "ItemFallbackClass",
                    "KeyCategoryView": "ItemFallbackKeyCategoryView",
                    "GoLiveDate": "ItemFallbackGoLiveDate",
                    "SizeGroup": "ItemFallbackSizeGroup",
                }
            ),
            on="Item",
            how="left",
        )
        missing_before = df["Division"].apply(is_blank_or_na)
        for base_col, fallback_col in [
            ("Division", "ItemFallbackDivision"),
            ("Department", "ItemFallbackDepartment"),
            ("Class", "ItemFallbackClass"),
            ("KeyCategoryView", "ItemFallbackKeyCategoryView"),
            ("GoLiveDate", "ItemFallbackGoLiveDate"),
            ("SizeGroup", "ItemFallbackSizeGroup"),
        ]:
            fill_mask = df[base_col].apply(is_blank_or_na) & ~df[fallback_col].apply(is_blank_or_na)
            df.loc[fill_mask, base_col] = df.loc[fill_mask, fallback_col]

        recovered_item_count = (missing_before & ~df["Division"].apply(is_blank_or_na)).sum()
        if recovered_item_count > 0:
            print(f"    Item-level Product Attributes fallback recovered {recovered_item_count:,} SKUs")
        df = df.drop(
            columns=[
                "ItemFallbackDivision",
                "ItemFallbackDepartment",
                "ItemFallbackClass",
                "ItemFallbackKeyCategoryView",
                "ItemFallbackGoLiveDate",
                "ItemFallbackSizeGroup",
            ]
        )

    recovered_ax_count = 0
    missing_before_ax = df["Division"].apply(is_blank_or_na)
    ax_fallback_candidates = int(missing_before_ax.sum())
    if missing_before_ax.any():
        missing_class_before_ax = df["Class"].apply(is_blank_or_na)
        try:
            print("    - Querying AX product hierarchy fallback...")
            ax_hierarchy = read_ax_product_hierarchy(df.loc[missing_before_ax, "Item"].unique().tolist())
        except Exception as exc:
            print(f"    WARNING: AX product hierarchy fallback failed ({exc})")
            ax_hierarchy = pd.DataFrame()

        if not ax_hierarchy.empty:
            df = df.merge(ax_hierarchy, on="Item", how="left")
            for base_col, fallback_col in [
                ("Division", "AXHierarchyDivision"),
                ("Department", "AXHierarchyDepartment"),
                ("Class", "AXHierarchyClass"),
                ("KeyCategoryView", "AXHierarchyKeyCategoryView"),
            ]:
                fill_mask = df[base_col].apply(is_blank_or_na) & ~df[fallback_col].apply(is_blank_or_na)
                df.loc[fill_mask, base_col] = df.loc[fill_mask, fallback_col]

            recovered_ax_count = (missing_before_ax & ~df["Division"].apply(is_blank_or_na)).sum()
            ax_class_candidates = missing_before_ax & missing_class_before_ax
            recovered_ax_class_count = (
                ax_class_candidates & ~df["Class"].apply(is_blank_or_na)
            ).sum()
            remaining_ax_count = (missing_before_ax & df["Division"].apply(is_blank_or_na)).sum()
            print(
                "    AX product hierarchy fallback associated "
                f"Division for {recovered_ax_count:,}/{ax_fallback_candidates:,} SKUs "
                f"and Class for {recovered_ax_class_count:,}/{int(ax_class_candidates.sum()):,} SKUs"
            )
            if remaining_ax_count > 0:
                print(f"    AX product hierarchy fallback left {remaining_ax_count:,} SKUs unresolved")
            df = df.drop(
                columns=[
                    "AXHierarchyDivision",
                    "AXHierarchyDepartment",
                    "AXHierarchyClass",
                    "AXHierarchyKeyCategoryView",
                    "AXHierarchySource",
                ],
                errors="ignore",
            )
        else:
            print(
                "    AX product hierarchy fallback associated "
                f"Division/Class for 0/{ax_fallback_candidates:,} SKUs"
            )
    else:
        print("    AX product hierarchy fallback skipped: Product Attributes covered all SKUs")

    # ── Step 5: Filter out SKUs with no usable hierarchy ───────────────────
    # These are excluded from the AX import CSV to preserve a valid schema,
    # but written to a separate exception report for the planning team.
    missing_hierarchy_mask = df["Division"].apply(is_blank_or_na)
    df_missing_hier = df[missing_hierarchy_mask].copy()
    if not df_missing_hier.empty:
        hierarchy_fields = ["Division", "Department", "Class", "KeyCategoryView", "SizeGroup", "GoLiveDate"]
        df_missing_hier["FD1_to_FD14_Total"] = df_missing_hier[fd_cols].sum(axis=1)
        df_missing_hier["ForecastStartDate"] = forecast_start
        df_missing_hier["SourceFoundIn"] = df_missing_hier.apply(
            lambda row: parse_forecast_source(
                row["InWeeklyForecast"],
                row["In14DayForecast"],
                row["InOnHand"],
                row["InInboundCoverage"],
            ),
            axis=1,
        )
        df_missing_hier["MissingHierarchyFields"] = df_missing_hier.apply(
            lambda row: summarize_missing_fields(row, hierarchy_fields),
            axis=1,
        )
        df_missing_hier = df_missing_hier[
            [
                "SKU",
                "Item",
                "Color",
                "Size",
                "SourceFoundIn",
                "13Wk_Demand",
                "FD1_to_FD14_Total",
                "ForecastStartDate",
                "MissingHierarchyFields",
            ]
        ].copy()

    # The legacy Fwd_Demand_CSV macro filters out rows where Field 1 (Division)
    # equals "n/a".  We apply that only after Product Attributes and AX product
    # hierarchy recovery have both had a chance to fill the merch hierarchy.
    before = len(df)
    df = df[~missing_hierarchy_mask].copy()
    print(f"    After Division filter: {len(df)} (removed {before - len(df)} w/o hierarchy)")

    # Exclude Wholesale/Legacy divisions ONLY if they are not in a DTC area.
    # This preserves shared SKUs (pilot tests) while removing Wholesale-only junk.
    # Note: Check prefixes BEFORE sanitization truncates them.
    ws_leg_prefix = df["Division"].str.startswith("W ", na=False) | df["Division"].str.startswith("LEG", na=False)
    to_exclude = ws_leg_prefix & (~df["InOnHand"]) & (~df["InInboundCoverage"])
    if to_exclude.any():
        print(f"    Excluded {to_exclude.sum()} Wholesale/Legacy rows not in DTC areas")
        df = df[~to_exclude].copy()

    missing_color_mask = df["Color"].apply(is_blank_or_na)
    df_missing_color = df[missing_color_mask].copy()
    if not df_missing_color.empty:
        df_missing_color["FD1_to_FD14_Total"] = df_missing_color[fd_cols].sum(axis=1)
        df_missing_color["ForecastStartDate"] = forecast_start
        df_missing_color["SourceFoundIn"] = df_missing_color.apply(
            lambda row: parse_forecast_source(
                row["InWeeklyForecast"],
                row["In14DayForecast"],
                row["InOnHand"],
                row["InInboundCoverage"],
            ),
            axis=1,
        )
        df_missing_color["MissingHierarchyFields"] = "Color"
        df_missing_color = df_missing_color[
            [
                "SKU",
                "Item",
                "Color",
                "Size",
                "SourceFoundIn",
                "13Wk_Demand",
                "FD1_to_FD14_Total",
                "ForecastStartDate",
                "MissingHierarchyFields",
            ]
        ].copy()
        df_missing_hier = pd.concat([df_missing_hier, df_missing_color], ignore_index=True)
        df = df[~missing_color_mask].copy()
        print(f"    Excluded {len(df_missing_color)} rows with blank Color")

    # ── Step 5: Map ProductGroupCode and SizeGroupCode ─────────────────────
    # Run lookups against the RAW Division/Size values, before sanitization
    # rewrites '&'/'/' or truncates strings. DIVISION_TO_PGC keys include
    # entries like 'Misc/Misc' that would not match the sanitized form.
    df["ProductGroupCode"] = df["Division"].apply(map_product_group_code)
    item_overrides = df["Item"].apply(product_group_override_for_item)
    df.loc[item_overrides.notna(), "ProductGroupCode"] = item_overrides[item_overrides.notna()]
    unmapped = df[df["ProductGroupCode"] == "U"]["Division"].unique()
    if len(unmapped) > 0:
        print(f"    WARNING: {len(unmapped)} unmapped divisions: {list(unmapped)[:5]}")

    df["SizeGroupCode"] = df["Size"].apply(map_size_group)

    # ── Step 6: Sanitize and Truncate for AX SSIS Compliance ──────────────
    # Legacy DIXF SSIS packages often have fixed-width column constraints
    # and fail on certain characters like '&' or '/'.
    def sanitize_ax(val, max_len):
        if is_blank_or_na(val):
            return val
        # Conservative DIXF field hygiene for descriptive hierarchy labels.
        # This was not the May 12 RCA (duplicate canonical SKUs were), but the
        # accepted payload used these substitutions after PGC/SGC mapping. Keep
        # mapping on raw values above; only sanitize the labels sent to AX.
        s = str(val).replace("&", "+").replace("/", "-")
        # Truncate to known-working widths from legacy successful uploads
        return s[:max_len].strip()

    print("    - Sanitizing hierarchy strings for AX compliance...")
    df["Division"] = df["Division"].apply(lambda x: sanitize_ax(x, 18))
    df["Department"] = df["Department"].apply(lambda x: sanitize_ax(x, 22))
    df["Class"] = df["Class"].apply(lambda x: sanitize_ax(x, 27))
    df["KeyCategoryView"] = df["KeyCategoryView"].apply(lambda x: sanitize_ax(x, 15))

    # After sanitization, re-check for mandatory field completion
    missing_kcv = df["KeyCategoryView"].apply(is_blank_or_na)
    if missing_kcv.any():
        print(f"    WARNING: Filled missing KeyCategoryView with 'Other' for {missing_kcv.sum()} SKUs")
        df.loc[missing_kcv, "KeyCategoryView"] = "Other"

    # ── Step 7: Calculate Velocity ─────────────────────────────────────────
    df["Velocity"] = df["13Wk_Demand"].apply(calculate_velocity)

    # ── Step 8: Build SlotTier ─────────────────────────────────────────────
    # SlotTier = ProductGroupCode + SizeGroupCode + Velocity
    # e.g. "GIR" + "M" + "A" = "GIRMA"
    # This is the key used by the Slot Assignment Tool to place SKUs into
    # physical DC locations.
    df["SlotTier"] = df["ProductGroupCode"] + df["SizeGroupCode"] + df["Velocity"]

    # ── Step 9: PutawayIndicator ───────────────────────────────────────────
    # Uses the weekly forecast date columns to compute weeks-until-demand,
    # exactly replicating the legacy AO17 → AP17 → AQ17 formula chain.
    def _putaway_for_row(row):
        demands = [float(row.get(c, 0)) for c in wk_cols]
        return calculate_putaway(demands, week_dates, today)

    df["PutawayIndicator"] = df.apply(_putaway_for_row, axis=1)

    # ── Step 9b: Guardrail 1 — Gate Active on 14-day demand ─────────────────
    # Only mark Active if the 14-day daily forecast (FD1-FD14) shows
    # actual demand.  The weekly tab over-flags ~12K SKUs as Active when
    # the 14-day tab (what AX uses for replenishment) shows zero demand.
    fd_cols = [f"FD{i}" for i in range(1, 15)]
    fd_total = df[fd_cols].sum(axis=1)
    active_no_14d = (df["PutawayIndicator"] == 1) & (fd_total == 0)
    n_g1 = active_no_14d.sum()
    df.loc[active_no_14d, "PutawayIndicator"] = 0
    if n_g1 > 0:
        print(f"    Guardrail 1: {n_g1:,} Active -> Reserve (zero 14-day demand)")

    # ── Step 9c: Guardrail 2 — Skip Active if supply already exists ─────────
    # The two-week demand is a forecast. If stock exists in Bulk/Overflow or is
    # already moving to the pick floor, AX replenishment/work should satisfy
    # demand before the ingestion file sends another carton directly to active.
    if guardrail_supply_skus is not None and len(guardrail_supply_skus) > 0:
        active_with_supply = (
            (df["PutawayIndicator"] == 1)
            & (df["SKU"].isin(guardrail_supply_skus))
        )
        n_g2 = active_with_supply.sum()
        df.loc[active_with_supply, "PutawayIndicator"] = 0
        if n_g2 > 0:
            print(f"    Guardrail 2: {n_g2:,} Active -> Reserve (supply already exists)")

    # ── Step 10: Merge Product Status ───────────────────────────────────────
    df = df.merge(df_status, left_on="SKU", right_on="StatusSKU", how="left")
    df["ProductStatus"] = df["ProductStatus"].fillna("")
    df["ProductStatusDate"] = df["ProductStatusDate"].fillna("")

    # ── Step 11: Merge Case Quantity data ───────────────────────────────────
    # Per-SKU case qty computed from Load Data + On Hand with hierarchical
    # fallback (SKU → Class → Dept → Div → Default 36).
    df = df.merge(df_case_qty[["SKU", "CaseQty", "CartonSizeType", "FinalType", "CaseQtySource"]],
                  on="SKU", how="left")
    df["CaseQty"] = df["CaseQty"].fillna(DEFAULT_CASE_QTY).astype(int)
    df["CartonSizeType"] = df["CartonSizeType"].fillna("Full Carton Only")
    df["FinalType"] = df["FinalType"].fillna("Full Carton Only")
    df["CaseQtySource"] = df["CaseQtySource"].fillna("Default")

    # ── Step 12: ReplenishmentThreshold ─────────────────────────────────────
    # Pick slot capacity: 1 full-size box OR 2 half-size boxes.
    #
    # Full carton → threshold = 3 (replenish with 1 new full box when ≤3 units remain)
    # Half carton → threshold = CaseQty - 1 (when 1 unit left, pull a SECOND half
    #               box into the slot — there's physical room for 2 half boxes)
    #
    # Gaylord locations override this via AX location profile config (e.g., 120).
    #
    # Legacy formula (Fwd Demand File, N3):
    #   =IF(AL3="Half Carton Only", AM3-1,
    #        XLOOKUP(E3, 'Product Group'!D:D, 'Product Group'!E:E, "", 0))
    df["ReplenishmentThreshold"] = np.where(
        df["FinalType"] == "Half Carton Only",
        df["CaseQty"] - 1,
        REPLENISHMENT_THRESHOLD
    )

    # ── Step 13: Hardcoded columns ──────────────────────────────────────────
    df["Subclass"] = ""
    df["ProductStage"] = PRODUCT_STAGE
    df["ReturnAction"] = RETURN_ACTION
    df["ReturnActionDate"] = RETURN_ACTION_DATE
    df["NVARExpectedQty"] = NVAR_EXPECTED_QTY
    df["ForecastStartDate"] = forecast_start

    # ── Step 14: Required Slots calculation ─────────────────────────────────
    # This replicates the slot-requirement math from the Slotting Calcs sheet
    # (columns AW through CW) so we can feed the Slot Assignment pipeline
    # without needing the legacy Excel tool.
    #
    # Average weekly demand is used as a proxy for the per-week columns.
    # The legacy tool computes slots for each of the 26 weeks and takes the
    # MAX — our average is a reasonable first approximation.
    df["AvgWeeklyDemand"] = df["13Wk_Demand"] / 13.0

    df["RequiredSlots"] = df.apply(
        lambda row: calculate_required_slots(
            row["AvgWeeklyDemand"], row["Velocity"], row["CaseQty"]
        ),
        axis=1,
    )

    # ── Step 15: Select and order the 36 AX CSV columns ─────────────────────
    output_cols = [
        "Division", "Department", "Class", "Subclass", "KeyCategoryView",
        "SKU", "Item", "Color", "Size",
        "ProductGroupCode", "SizeGroupCode", "Velocity", "SlotTier",
        "ReplenishmentThreshold", "PutawayIndicator",
        "ProductStatus", "ProductStatusDate",
        "ProductStage", "ReturnAction", "ReturnActionDate", "NVARExpectedQty",
        "ForecastStartDate",
        "FD1", "FD2", "FD3", "FD4", "FD5", "FD6", "FD7",
        "FD8", "FD9", "FD10", "FD11", "FD12", "FD13", "FD14",
    ]
    df_out = df[output_cols].copy()
    df_out = df_out.sort_values(["Division", "SKU"]).reset_index(drop=True)
    validate_ax_upload_ready(df_out)

    # ── Build Required Slots summary ───────────────────────────────────────
    # Group by SlotTier to produce the same pivot table that the legacy tool
    # generates in the 'Rqd Slots' output sheet.
    df_slots = (
        df.groupby(["ProductGroupCode", "SizeGroupCode", "Velocity", "SlotTier"])
        .agg(
            SKU_Count=("SKU", "count"),
            TotalRequiredSlots=("RequiredSlots", "sum"),
        )
        .reset_index()
        .sort_values("SlotTier")
    )

    if not df_missing_hier.empty:
        df_missing_hier = df_missing_hier[
            [
                "SKU",
                "Item",
                "Color",
                "Size",
                "SourceFoundIn",
                "13Wk_Demand",
                "FD1_to_FD14_Total",
                "ForecastStartDate",
                "MissingHierarchyFields",
            ]
        ].sort_values(["SourceFoundIn", "SKU"]).reset_index(drop=True)

    return df_out, df_slots, df_missing_hier


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Orchestrates the entire zoning and slotting ingestion pipeline.

    Parses command-line arguments, locates the latest or specified source workbook,
    reads various worksheets (weekly forecasts, 14-day forecasts, product attributes,
    on-hand data, load data), queries AX SQL database for active supply and inbound coverage
    signals (with workbook fallback), computes case quantities with fallback logic, builds
    AX Forward Demand and Required Slots outputs, writes final CSV files, and registers
    data to the SQLite SKU ledger database.
    """
    args = parse_args()
    operator_mode = args.operator_mode
    prompt_copy_to_ax_share = args.prompt_copy_to_ax_share
    copy_to_ax_share = args.copy_to_ax_share
    output_dir = args.output_dir
    timings = {}  # phase_name → seconds
    t_total_start = time.perf_counter()

    if operator_mode:
        print("=" * 60)
        print("  Forward Demand File Generator")
        print("=" * 60)
        print("Operator mode: showing summary progress only.\n")
    else:
        print("=" * 60)
        print("  Zoning & Slotting Ingestion Pipeline v3  (benchmarked)")
        print("=" * 60)

    def timed_step(phase_name: str, operator_label: str, func):
        t0 = time.perf_counter()
        result = run_pipeline_step(operator_label, func, operator_mode=operator_mode)
        timings[phase_name] = time.perf_counter() - t0
        return result

    # ── Phase 1: Locate source file ────────────────────────────────────────
    try:
        if args.source_file is not None:
            source_file = args.source_file
            if not source_file.exists():
                raise FileNotFoundError(f"Source workbook not found: {source_file}")
            print(f"[*] Source file: {source_file.name} (explicit --source-file)")
        else:
            source_file = timed_step(
                "1. Locate source file",
                "Finding latest Product Info workbook",
                get_latest_source_file,
            )
    except FileNotFoundError:
        if not operator_mode:
            raise
        print("\nERROR: No Product Info for BRG workbook was found.")
        print("Please download the latest file from SharePoint and place it here:")
        print(f"  {SOURCE_DIR}")
        print("\nExpected file name pattern:")
        print(f"  {SOURCE_WORKBOOK_PATTERN}")
        raise SystemExit(1) from None

    if operator_mode:
        print(f"    Source workbook: {source_file.name}")
        print_operator_source_preflight(source_file)

    # ── Phase 2: Read Excel sheets ─────────────────────────────────────────
    df_weekly, week_dates, wk_cols = timed_step(
        "2a. Read weekly forecast",
        "Reading weekly forecast",
        lambda: read_weekly_forecast(source_file),
    )

    df_14day, forecast_start = timed_step(
        "2b. Read 14-day forecast",
        "Reading 14-day forecast",
        lambda: read_14day_forecast(source_file),
    )

    df_hier, df_status = timed_step(
        "2c. Read product attributes",
        "Reading product attributes",
        lambda: read_product_attributes(source_file),
    )

    df_load = timed_step(
        "2d. Read load data",
        "Reading load data",
        lambda: read_load_data(source_file),
    )

    df_oh, df_on_hand_skus = timed_step(
        "2e. Read on-hand data",
        "Reading on-hand inventory",
        lambda: read_on_hand(source_file),
    )

    timings["2. TOTAL Excel I/O"] = sum(
        v for k, v in timings.items() if k.startswith("2")
    )

    # ── Phase 2f: Guardrail 2 supply signals from AX SQL ───────────────────
    guardrail_supply_skus, guardrail_supply_source = timed_step(
        "2f. Query Guardrail 2 supply",
        "Checking AX inventory and replenishment guardrails",
        lambda: get_guardrail_supply_skus(source_file),
    )
    if operator_mode:
        print(
            f"    Guardrail supply SKUs: {len(guardrail_supply_skus):,} "
            f"(source: {guardrail_supply_source})"
        )
        if guardrail_supply_source != "AX":
            print("    WARNING: live AX guardrail query was not used.")
            print("    Please notify Luis/IT before uploading the AX file.")
    else:
        print(f"    Guardrail 2 supply source: {guardrail_supply_source}")

    # ── Phase 2g: Inbound/Cubiscan coverage signals from AX SQL ────────────
    inbound_coverage_skus, inbound_coverage_source = timed_step(
        "2g. Query inbound/Cubiscan coverage",
        "Checking AX inbound and Cubiscan coverage",
        get_inbound_coverage_skus,
    )
    if operator_mode:
        print(
            f"    Inbound/Cubiscan coverage SKUs: {len(inbound_coverage_skus):,} "
            f"(source: {inbound_coverage_source})"
        )
        if inbound_coverage_source != "AX":
            print("    WARNING: live AX inbound/Cubiscan coverage query was not used.")
            print("    Blank Cubiscan labels are still possible for unforecasted inbound SKUs.")
    else:
        print(f"    Inbound/Cubiscan coverage source: {inbound_coverage_source}")

    # ── Phase 3: Compute case quantities ───────────────────────────────────
    def compute_case_quantities():
        skus_weekly = df_weekly[["SKU"]].copy()
        skus_14day = df_14day[["SKU"]].copy()
        skus_to_concat = [skus_weekly, skus_14day, df_on_hand_skus]
        if guardrail_supply_skus:
            skus_to_concat.append(pd.DataFrame({"SKU": list(guardrail_supply_skus)}))
        if inbound_coverage_skus:
            skus_to_concat.append(pd.DataFrame({"SKU": list(inbound_coverage_skus)}))
        sku_universe = (
            pd.concat(skus_to_concat)
            .drop_duplicates(subset=["SKU"])
            .copy()
        )
        return compute_case_qty(df_load, df_oh, df_hier, sku_universe)

    df_case_qty = timed_step(
        "3. Compute case quantities",
        "Computing case quantities",
        compute_case_quantities,
    )

    # ── Phase 4: Build output DataFrames ───────────────────────────────────
    df_out, df_slots, df_missing_hier = timed_step(
        "4. Build output",
        "Building AX output files",
        lambda: build_output(
            df_weekly,
            df_14day,
            df_hier,
            df_status,
            forecast_start,
            week_dates,
            wk_cols,
            df_case_qty,
            guardrail_supply_skus=guardrail_supply_skus,
            df_on_hand_skus=df_on_hand_skus,
            inbound_coverage_skus=inbound_coverage_skus,
        ),
    )

    # ── Phase 5: Write CSVs ────────────────────────────────────────────────
    if operator_mode:
        print("[*] Writing output files...")

    t0 = time.perf_counter()
    if output_dir == INGESTION_OUTPUT_DIR:
        ensure_phase1_output_dirs()
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    csv_filename = f"FwdDemandCSV_{today_str}.csv"
    csv_path = output_dir / csv_filename
    slots_filename = f"RequiredSlots_{today_str}.csv"
    slots_path = output_dir / slots_filename
    missing_filename = f"MissingProductAttributes_{today_str}.csv"
    missing_path = (
        output_dir / missing_filename
        if len(df_missing_hier) > 0
        else None
    )

    if operator_mode:
        output_paths_to_check = [csv_path, slots_path]
        if missing_path is not None:
            output_paths_to_check.append(missing_path)
        print_operator_existing_output_warning(output_paths_to_check)

    df_out.to_csv(csv_path, index=False)
    timings["5a. Write AX CSV"] = time.perf_counter() - t0

    if not operator_mode:
        print(f"\n[*] AX CSV written: {csv_path}")
        print(f"    Columns: {len(df_out.columns)}")
        print(f"    Rows:    {len(df_out)}")

        vel_dist = df_out["Velocity"].value_counts().to_dict()
        print(f"    Velocity distribution: {vel_dist}")

    t0 = time.perf_counter()
    df_slots.to_csv(slots_path, index=False)
    timings["5b. Write Slots CSV"] = time.perf_counter() - t0

    if not operator_mode:
        print(f"\n[*] Required Slots written: {slots_path}")
        print(f"    Slot Tiers: {len(df_slots)}")
        print(f"    Total Slots: {df_slots['TotalRequiredSlots'].sum():.0f}")

    t0 = time.perf_counter()
    if len(df_missing_hier) > 0:
        df_missing_hier.to_csv(missing_path, index=False)
        if not operator_mode:
            print(f"\n[*] Missing Product Attributes written: {missing_path}")
            print(f"    Rows:    {len(df_missing_hier)}")
    else:
        if not operator_mode:
            print("\n[*] Missing Product Attributes: none")
    timings["5c. Write exceptions CSV"] = time.perf_counter() - t0
    if operator_mode:
        print("    Writing output files: complete")

    # ── Phase 6: Auto-ingest into SKU Ledger ──────────────────────────────
    if operator_mode:
        print("[*] Updating SKU ledger...")

    t0 = time.perf_counter()
    try:
        from sku_ledger import init_db, ingest_csv, extract_date_from_filename

        ledger_db = output_dir / "sku_ledger.db"
        ledger_conn = init_db(ledger_db)
        file_date = extract_date_from_filename(csv_path)
        if operator_mode:
            with contextlib.redirect_stdout(io.StringIO()):
                n_read, n_new = ingest_csv(ledger_conn, csv_path, file_date)
        else:
            n_read, n_new = ingest_csv(ledger_conn, csv_path, file_date)
        ledger_conn.close()
        if operator_mode:
            print(f"    SKU ledger: updated ({n_new:,} new SKUs)")
        else:
            print(f"\n[*] SKU Ledger updated: {n_read:,} read, {n_new:,} new SKUs")
    except Exception as e:
        if operator_mode:
            print(f"    SKU ledger warning: update failed ({e})")
        else:
            print(f"\n[!] SKU Ledger ingest failed: {e}")
    timings["6. SKU Ledger ingest"] = time.perf_counter() - t0

    # ── Performance Summary ────────────────────────────────────────────────
    t_total = time.perf_counter() - t_total_start
    timings["TOTAL"] = t_total
    copied_ax_pickup_path = maybe_copy_to_ax_share(
        csv_path,
        prompt=prompt_copy_to_ax_share,
        force_copy=copy_to_ax_share,
    )

    if operator_mode:
        if copied_ax_pickup_path is not None:
            print("\nAX pickup file ready:")
            print(f"  {copied_ax_pickup_path}")
        else:
            print("\nUpload this file to AX:")
            print(f"  {csv_path}")

        print("\nFiles created:")
        print(f"  AX Forward Demand: {csv_path}")
        print(f"    Rows: {len(df_out):,}")
        print(f"  Required Slots: {slots_path}")
        print(f"    Total slots: {df_slots['TotalRequiredSlots'].sum():,.0f}")
        if missing_path is not None:
            print(f"  Missing Product Attributes: {missing_path}")
            print(f"    Rows: {len(df_missing_hier):,}")
        else:
            print("  Missing Product Attributes: none")
        print(f"\nCompleted successfully in {t_total:.1f} seconds.")
    else:
        print(f"\n{'='*60}")
        print("  PERFORMANCE BENCHMARK")
        print(f"{'='*60}")
        for phase, secs in timings.items():
            bar = "#" * int(secs / t_total * 40) if t_total > 0 else ""
            pct = secs / t_total * 100 if t_total > 0 else 0
            print(f"  {phase:<30s} {secs:>7.2f}s  {pct:>5.1f}%  {bar}")
        print("\n[*] Done!")


if __name__ == "__main__":
    main()
