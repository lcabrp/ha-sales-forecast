"""Shared output folder contract for forecast tooling."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = PROJECT_ROOT / "Output"

INGESTION_OUTPUT_DIR = OUTPUT_DIR / "Ingestion"

ARCHIVE_OUTPUT_DIR = OUTPUT_DIR / "Archive"
LEGACY_ARCHIVE_DIR = ARCHIVE_OUTPUT_DIR / "legacy"


def ensure_phase1_output_dirs() -> None:
    """Create the first-class output folders used by the active tools."""
    for path in (
        INGESTION_OUTPUT_DIR,
        LEGACY_ARCHIVE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def existing_path(preferred: Path, *fallbacks: Path) -> Path:
    """Return the preferred path if present, otherwise the first existing fallback.

    This keeps phase 1 backward-compatible with files that still live directly
    under Output until we do the deliberate cleanup/archive pass.
    """
    if preferred.exists():
        return preferred
    for fallback in fallbacks:
        if fallback.exists():
            return fallback
    return preferred


def latest_file(pattern: str, *directories: Path) -> Path:
    """Find the newest matching file, searching preferred directories first."""
    for directory in directories:
        candidates = list(directory.glob(pattern))
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)

    searched = ", ".join(str(directory) for directory in directories)
    raise FileNotFoundError(f"No {pattern} file found in {searched}")
