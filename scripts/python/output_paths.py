"""Shared output folder contract for forecast tooling.

This module centralizes all path calculations relative to the project root,
ensuring consistent folder structures across all forecasting and model-training scripts.
"""

from __future__ import annotations

from pathlib import Path

# Resolve project root relative to this file's position (scripts/python/output_paths.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Output directory for all generated outputs and datasets
OUTPUT_DIR = PROJECT_ROOT / "Output"

# Output directory for data ingestion pipelines
INGESTION_OUTPUT_DIR = OUTPUT_DIR / "Ingestion"

# Archive and legacy directories for storing historical forecast datasets
ARCHIVE_OUTPUT_DIR = OUTPUT_DIR / "Archive"
LEGACY_ARCHIVE_DIR = ARCHIVE_OUTPUT_DIR / "legacy"


def ensure_phase1_output_dirs() -> None:
    """Create the first-class output folders used by the active tools.

    This ensures that target directories exist prior to write operations,
    preventing FileNotFoundError from being raised.
    """
    for path in (
        INGESTION_OUTPUT_DIR,
        LEGACY_ARCHIVE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def existing_path(preferred: Path, *fallbacks: Path) -> Path:
    """Return the preferred path if present, otherwise the first existing fallback.

    This keeps phase 1 backward-compatible with files that still live directly
    under Output until we do the deliberate cleanup/archive pass.

    Args:
        preferred: The primary/preferred Path to check.
        *fallbacks: Variable length list of fallback Paths to check if the
          preferred path does not exist.

    Returns:
        The first path that exists (starting with preferred), or preferred if none exist.
    """
    if preferred.exists():
        return preferred
    for fallback in fallbacks:
        if fallback.exists():
            return fallback
    return preferred


def latest_file(pattern: str, *directories: Path) -> Path:
    """Find the newest matching file, searching preferred directories first.

    Args:
        pattern: A glob pattern (e.g. "*.xlsx") to match files.
        *directories: Ordered list of directories to search in.

    Returns:
        The path to the newest file matching the pattern.

    Raises:
        FileNotFoundError: If no matching file is found in any searched directory.
    """
    for directory in directories:
        candidates = list(directory.glob(pattern))
        if candidates:
            # Sort candidate files by modification time and return the newest
            return max(candidates, key=lambda path: path.stat().st_mtime)

    searched = ", ".join(str(directory) for directory in directories)
    raise FileNotFoundError(f"No {pattern} file found in {searched}")
