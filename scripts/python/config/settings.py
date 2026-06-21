"""Centralized operational settings for ha-sales-forecast scripts.

Keep values here when they describe the runtime environment or operator-facing
workflow, especially when IT or Operations may need to find or change them.
Project-relative folders still live in output_paths.py.
"""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _get_int_env(name: str, default: int) -> int:
    """Read a positive integer from the environment, falling back safely."""
    value = os.getenv(name)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        return default

    return parsed if parsed >= 0 else default


def _get_list_env(name: str, default: list[str]) -> list[str]:
    """Read a comma-separated environment variable into a non-empty list."""
    value = os.getenv(name)
    if value is None:
        return default

    parsed = [part.strip() for part in value.split(",") if part.strip()]
    return parsed or default


# Source workbook expectations.
SOURCE_WORKBOOK_PATTERN = os.getenv(
    "ZS_SOURCE_WORKBOOK_PATTERN",
    "Product Info for BRG*.xlsx",
)
SOURCE_WORKBOOK_STALE_DAYS = _get_int_env("ZS_SOURCE_WORKBOOK_STALE_DAYS", 7)


# AX production connection defaults.
AX_SERVER = os.getenv("ZS_AX_SERVER", "prodaxsql2")
AX_DATABASE = os.getenv("ZS_AX_DATABASE", "DAX_PROD")
AX_DRIVERS = _get_list_env(
    "ZS_AX_DRIVERS",
    [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server",
    ],
)


# AX pickup folder for the weekly Forward Demand CSV handoff.
AX_FORWARD_REPLEN_SHARE = os.getenv(
    "ZS_AX_FORWARD_REPLEN_SHARE",
    r"\\tk-ax-report\Documents\ForwardReplen",
)
