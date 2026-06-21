"""
repo_health_check.py - Lightweight pre-flight checks for the repo.

Default checks:
    1. Parse-only syntax validation for repo Python files (no .pyc writes)
    2. Optional `ruff check` if Ruff is installed
    3. Optional zone-map QA via audit_map_quality.py if Proposed_Zone_Map.csv exists

Examples:
    python scripts/python/repo_health_check.py
    python scripts/python/repo_health_check.py --syntax-only
    python scripts/python/repo_health_check.py --skip-audit
    python scripts/python/repo_health_check.py --skip-ruff
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "Output"
ZONE_MAP = OUTPUT_DIR / "Layout" / "maps" / "Proposed_Zone_Map.csv"
RUFF_TARGETS = [
    "scripts/python/market_basket_analysis.py",
    "scripts/python/zone_allocator.py",
    "scripts/python/audit_map_quality.py",
    "scripts/python/create_virtual_layout.py",
    "scripts/python/evaluate_layout.py",
    "scripts/python/generate_move_list.py",
    "scripts/python/repo_health_check.py",
]


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted((PROJECT_ROOT / "scripts" / "python").glob("*.py")):
        if path.name == "__init__.py":
            continue
        files.append(path)

    top_level_main = PROJECT_ROOT / "main.py"
    if top_level_main.exists():
        files.append(top_level_main)

    return files


def run_syntax_check() -> int:
    print("=" * 72)
    print("SYNTAX CHECK")
    print("=" * 72)

    files = _iter_python_files()
    failures: list[tuple[Path, Exception]] = []
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
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
    candidates = [
        PROJECT_ROOT / ".venv" / "Scripts" / "ruff.exe",
        PROJECT_ROOT / ".venv" / "bin" / "ruff",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    for path_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not path_dir:
            continue
        candidate = Path(path_dir) / ("ruff.exe" if sys.platform == "win32" else "ruff")
        if candidate.exists():
            return str(candidate)

    return None


def run_ruff_check() -> int:
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


def run_zone_map_audit() -> int:
    print()
    print("=" * 72)
    print("ZONE MAP AUDIT")
    print("=" * 72)

    if not ZONE_MAP.exists():
        print(
            "[SKIP] Output/Layout/maps/Proposed_Zone_Map.csv not found. "
            "Generate it with zone_allocator.py first."
        )
        return 0

    audit_script = SCRIPT_DIR / "audit_map_quality.py"
    result = subprocess.run(
        [sys.executable, str(audit_script)],
        cwd=PROJECT_ROOT,
        text=True,
    )
    if result.returncode == 0:
        print("[PASS] Zone map audit passed.")
    else:
        print(f"[FAIL] Zone map audit exited with code {result.returncode}.")
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Run lightweight repo health checks.")
    parser.add_argument("--syntax-only", action="store_true", help="Run only parse/syntax validation.")
    parser.add_argument("--skip-ruff", action="store_true", help="Skip Ruff even if installed.")
    parser.add_argument("--skip-audit", action="store_true", help="Skip audit_map_quality.py.")
    args = parser.parse_args()

    exit_code = 0

    syntax_code = run_syntax_check()
    exit_code = max(exit_code, syntax_code)

    if not args.syntax_only and not args.skip_ruff:
        ruff_code = run_ruff_check()
        exit_code = max(exit_code, ruff_code)

    if not args.syntax_only and not args.skip_audit:
        audit_code = run_zone_map_audit()
        exit_code = max(exit_code, audit_code)

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
