"""repo_health_check.py - Lightweight pre-flight checks for the repo.

Default checks:
    1. Parse-only syntax validation for repo Python files (no .pyc writes)
    2. Optional `ruff check` if Ruff is installed

Examples:
    python scripts/python/repo_health_check.py
    python scripts/python/repo_health_check.py --syntax-only
    python scripts/python/repo_health_check.py --skip-ruff
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Resolve workspace directories
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent

# Target paths for ruff syntax/lint checks
RUFF_TARGETS = [
    "scripts/python",
]


def _iter_python_files() -> list[Path]:
    """Gather all Python files in the scripts/python folder and root main.py.

    Excludes package initialization files (`__init__.py`).

    Returns:
        Sorted list of Path objects pointing to Python files to check.
    """
    files: list[Path] = []
    # Discover all script files in scripts/python/
    for path in sorted((PROJECT_ROOT / "scripts" / "python").glob("*.py")):
        if path.name == "__init__.py":
            continue
        files.append(path)

    # Include project root level main.py if it is present
    top_level_main = PROJECT_ROOT / "main.py"
    if top_level_main.exists():
        files.append(top_level_main)

    return files


def run_syntax_check() -> int:
    """Validate python file parseability using standard compile() function.

    No bytecode (.pyc) files are generated during this check.

    Returns:
        0 if all files compiled successfully, 1 if any file has syntax errors.
    """
    print("=" * 72)
    print("SYNTAX CHECK")
    print("=" * 72)

    files = _iter_python_files()
    failures: list[tuple[Path, Exception]] = []
    
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
            # Parse only, don't execute or write compiled files
            compile(source, str(path), "exec")
        except Exception as exc:  # noqa: BLE001 - we want the exact parse failure
            failures.append((path, exc))

    if failures:
        for path, exc in failures:
            rel = path.relative_to(PROJECT_ROOT)
            print(f"[FAIL] {rel}: {type(exc).__name__}: {exc}")
        return 1

    print(f"[PASS] Parsed {len(files)} Python files successfully.")
    return 0


def _find_ruff() -> str | None:
    """Locate the ruff executable in the virtual environment or system PATH.

    Checks virtual environment paths (.venv) first, and falls back to system PATH.

    Returns:
        The string path to the ruff executable, or None if not found.
    """
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "ruff.exe",
        PROJECT_ROOT / ".venv" / "bin" / "ruff",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Search folders on the user's environment PATH variable
    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not path_dir:
            continue
        candidate = Path(path_dir) / ("ruff.exe" if sys.platform == "win32" else "ruff")
        if candidate.exists():
            return str(candidate)

    return None


def run_ruff_check() -> int:
    """Run `ruff check` on target directories if Ruff is installed.

    Returns:
        Ruff's exit code (0 for pass, non-zero for failure/error).
    """
    print()
    print("=" * 72)
    print("RUFF CHECK")
    print("=" * 72)

    ruff = _find_ruff()
    if not ruff:
        print("[SKIP] Ruff is not installed.")
        print("       Install it with: uv sync --group dev")
        print("       Then rerun:      python scripts/python/repo_health_check.py")
        return 0

    # Run the ruff checker command via subprocess
    result = subprocess.run(
        [ruff, "check", *RUFF_TARGETS],
        cwd=PROJECT_ROOT,
        text=True,
    )
    if result.returncode == 0:
        print("[PASS] Ruff check passed.")
    else:
        print(f"[FAIL] Ruff check exited with code {result.returncode}.")
    return result.returncode


def main() -> None:
    """Main CLI entry point for executing syntax and linter checks."""
    parser = argparse.ArgumentParser(description="Run lightweight repo health checks.")
    parser.add_argument("--syntax-only", action="store_true", help="Run only parse/syntax validation.")
    parser.add_argument("--skip-ruff", action="store_true", help="Skip Ruff even if installed.")
    args = parser.parse_args()

    exit_code = 0

    # Always execute compile check
    syntax_code = run_syntax_check()
    exit_code = max(exit_code, syntax_code)

    # Conditionally execute Ruff linter check
    if not args.syntax_only and not args.skip_ruff:
        ruff_code = run_ruff_check()
        exit_code = max(exit_code, ruff_code)

    print()
    print("=" * 72)
    if exit_code == 0:
        print("HEALTH CHECK PASSED")
    else:
        print(f"HEALTH CHECK FAILED (exit code {exit_code})")
    print("=" * 72)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
