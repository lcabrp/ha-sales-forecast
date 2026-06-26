"""Centralized operational settings for ha-sales-forecast scripts.

Keep values here when they describe the runtime environment or operator-facing
workflow, especially when IT or Operations may need to find or change them.
Project-relative folders still live in output_paths.py.
"""

from __future__ import annotations

import os
from pathlib import Path

# Resolve project root relative to this file's position (scripts/python/config/settings.py)
# Parent levels:
# 1. scripts/python/config/ (this file's parent)
# 2. scripts/python/
# 3. scripts/
# 4. project root (ha-sales-forecast)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _get_int_env(name: str, default: int) -> int:
    """Read a positive integer from the environment, falling back safely.

    Args:
        name: Name of the environment variable to read.
        default: Default value if environment variable is not set, empty, or invalid.

    Returns:
        The environment variable value cast to an integer, or the default value.
        Guarantees returned integer is >= 0.
    """
    value = os.getenv(name)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        # Fall back to default if casting fails (e.g., if non-numeric value is provided)
        return default

    return parsed if parsed >= 0 else default


def _get_list_env(name: str, default: list[str]) -> list[str]:
    """Read a comma-separated environment variable into a non-empty list.

    Args:
        name: Name of the environment variable to read.
        default: Default value if environment variable is not set, empty, or invalid.

    Returns:
        A list of stripped string values parsed from the comma-separated environment
        variable, or the default list of values if empty.
    """
    value = os.getenv(name)
    if value is None:
        return default

    # Parse comma-separated list and discard empty parts
    parsed = [part.strip() for part in value.split(",") if part.strip()]
    return parsed or default


# =========================================================================
# Source Workbook Expectations
# =========================================================================

# Pattern used to discover the corporate Product Info for BRG workbook.
# The asterisk wildcard allows matching version-stamped filenames.
SOURCE_WORKBOOK_PATTERN = os.getenv(
    "ZS_SOURCE_WORKBOOK_PATTERN",
    "Product Info for BRG*.xlsx",
)

# Number of days before the corporate workbook is considered stale and
# triggers a warning or verification failure.
SOURCE_WORKBOOK_STALE_DAYS = _get_int_env("ZS_SOURCE_WORKBOOK_STALE_DAYS", 7)


# =========================================================================
# AX Production Database Connection Configuration
# =========================================================================

# Database server host name for Microsoft Dynamics AX SQL database.
# Defaults to the corporate production database server.
AX_SERVER = os.getenv("ZS_AX_SERVER", "prodaxsql2")

# Dynamics AX database catalog name.
AX_DATABASE = os.getenv("ZS_AX_DATABASE", "DAX_PROD")

# List of preferred SQL Server ODBC drivers.
# The code attempts to establish a connection by trying drivers in order
# of preference (newer drivers like v18 first, falling back to older versions).
AX_DRIVERS = _get_list_env(
    "ZS_AX_DRIVERS",
    [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server",
    ],
)


# =========================================================================
# AX Forward Demand Replenishment Handoff Target
# =========================================================================

# UNC network share directory path where weekly forward demand forecast
# CSV exports are copied to be picked up by AX ingestion processes.
AX_FORWARD_REPLEN_SHARE = os.getenv(
    "ZS_AX_FORWARD_REPLEN_SHARE",
    r"\\tk-ax-report\Documents\ForwardReplen",
)
