"""Persist read-only AX replenishment history for offline velocity-policy analysis.

This exporter deliberately writes two grains:

1. Raw allocation links preserve how existing replenishment work was consumed
   by sales-order demand.
2. Physical touches deduplicate those links so warehouse labor is not
   overstated when one replenishment movement satisfies multiple demand lines.

Historical movement quantity comes from the last put line. Do not hard-code
line 5: older AX templates had four lines before a Print step was introduced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from sql_utils import get_ax_engine  # noqa: E402


DEFAULT_SQL = PROJECT_ROOT / "scratch" / "velocity_policy_sales_order_replen_extract.sql"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "scratch" / "velocity_policy_replay"
ALLOCATION_NAME = "sales_order_replen_allocations_3y.parquet"
TOUCH_NAME = "physical_replen_touches_3y.parquet"
SUMMARY_NAME = "physical_replen_touches_3y_by_category.csv"
LINE_PROFILE_NAME = "physical_replen_touches_3y_by_final_put_line.csv"
METADATA_NAME = "replenishment_history_3y_metadata.json"

PHYSICAL_COLUMNS = [
    "SourceDatabase",
    "ReplenWorkId",
    "ReplenLineNum",
    "TouchKey",
    "ReplenCategory",
    "ReplenTemplate",
    "WorkBuildId",
    "ReplenCreatedDateTimeUtc",
    "ReplenClosedDateTimeUtc",
    "ItemId",
    "ColorId",
    "SizeId",
    "SKU",
    "SourceLocation",
    "FinalPutLineNum",
    "FinalTargetLocation",
    "ReplenTouchInventQty",
    "FinalPutInventQty",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", type=Path, default=DEFAULT_SQL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing local snapshot intentionally.",
    )
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _prepare_outputs(output_dir: Path, overwrite: bool) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "allocations": output_dir / ALLOCATION_NAME,
        "touches": output_dir / TOUCH_NAME,
        "summary": output_dir / SUMMARY_NAME,
        "line_profile": output_dir / LINE_PROFILE_NAME,
        "metadata": output_dir / METADATA_NAME,
    }
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Local snapshot already exists. Pass --overwrite to replace it intentionally: "
            + ", ".join(str(path) for path in existing)
        )
    return outputs


def _temporary_outputs(outputs: dict[str, Path]) -> dict[str, Path]:
    return {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}


def _remove_existing(paths: list[Path]) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _write_raw_allocations(
    query: str,
    chunk_size: int,
    tmp_path: Path,
) -> tuple[pd.DataFrame, int]:
    writer: pq.ParquetWriter | None = None
    physical_frames: list[pd.DataFrame] = []
    seen_touch_keys: set[str] = set()
    allocation_rows = 0

    engine = get_ax_engine()
    try:
        with engine.connect() as conn:
            for chunk in pd.read_sql_query(query, conn, chunksize=chunk_size):
                allocation_rows += len(chunk)
                chunk["TouchKey"] = chunk["TouchKey"].astype(str)

                # Keep allocation links at their native grain for attribution.
                table = pa.Table.from_pandas(chunk, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(tmp_path, table.schema, compression="zstd")
                writer.write_table(table)

                # A physical reserve-to-floor movement may satisfy multiple
                # demand lines. Keep only one touch row for labor analysis.
                unique = chunk.drop_duplicates("TouchKey", keep="first")
                unique = unique.loc[~unique["TouchKey"].isin(seen_touch_keys), PHYSICAL_COLUMNS]
                seen_touch_keys.update(unique["TouchKey"].tolist())
                physical_frames.append(unique.copy())
    finally:
        if writer is not None:
            writer.close()
        engine.dispose()

    if writer is None:
        raise RuntimeError("AX query returned no replenishment allocation rows.")
    return pd.concat(physical_frames, ignore_index=True), allocation_rows


def _normalize_physical_touches(touches: pd.DataFrame) -> pd.DataFrame:
    for column in ("ReplenCreatedDateTimeUtc", "ReplenClosedDateTimeUtc"):
        touches[column] = pd.to_datetime(touches[column], utc=True)
    for column in ("ReplenTouchInventQty", "FinalPutInventQty", "FinalPutLineNum"):
        touches[column] = pd.to_numeric(touches[column], errors="coerce")
    return touches.sort_values(["ReplenCreatedDateTimeUtc", "ReplenWorkId", "ReplenLineNum"])


def _build_summary(touches: pd.DataFrame) -> pd.DataFrame:
    return (
        touches.groupby("ReplenCategory", dropna=False)
        .agg(
            PhysicalTouches=("TouchKey", "nunique"),
            ActualLastPutInventQty=("FinalPutInventQty", "sum"),
            MissingFinalPutLine=("FinalPutLineNum", lambda values: int(values.isna().sum())),
            MissingFinalPutQty=("FinalPutInventQty", lambda values: int(values.isna().sum())),
            MissingFinalTarget=("FinalTargetLocation", lambda values: int(values.isna().sum())),
        )
        .reset_index()
        .sort_values("PhysicalTouches", ascending=False)
    )


def _build_line_profile(touches: pd.DataFrame) -> pd.DataFrame:
    profile = touches.copy()
    profile["FinalPutLineNum"] = profile["FinalPutLineNum"].astype("Int64")
    return (
        profile.groupby(["ReplenCategory", "FinalPutLineNum"], dropna=False)
        .agg(
            PhysicalTouches=("TouchKey", "nunique"),
            ActualLastPutInventQty=("FinalPutInventQty", "sum"),
        )
        .reset_index()
        .sort_values(["ReplenCategory", "FinalPutLineNum"], na_position="last")
    )


def main() -> None:
    args = parse_args()
    outputs = _prepare_outputs(args.output_dir, args.overwrite)
    temporary = _temporary_outputs(outputs)

    query = args.sql.read_text(encoding="utf-8")
    _remove_existing(list(temporary.values()))

    try:
        touches, allocation_rows = _write_raw_allocations(
            query,
            args.chunk_size,
            temporary["allocations"],
        )
        touches = _normalize_physical_touches(touches)
        summary = _build_summary(touches)
        line_profile = _build_line_profile(touches)

        metadata = {
            "extracted_at_utc": datetime.now(UTC).isoformat(),
            "sql_path": str(args.sql.relative_to(PROJECT_ROOT)),
            "sql_sha256": _sha256(args.sql),
            "allocation_rows": allocation_rows,
            "physical_touches": int(touches["TouchKey"].nunique()),
            "first_replen_created_utc": touches["ReplenCreatedDateTimeUtc"].min().isoformat(),
            "last_replen_created_utc": touches["ReplenCreatedDateTimeUtc"].max().isoformat(),
            "missing_final_put_line": int(touches["FinalPutLineNum"].isna().sum()),
            "missing_final_put_qty": int(touches["FinalPutInventQty"].isna().sum()),
            "missing_final_target_location": int(touches["FinalTargetLocation"].isna().sum()),
            "quantity_semantics": {
                "FinalPutInventQty": "Authoritative historical quantity moved into forward pick.",
                "ReplenTouchInventQty": "Linked reserve-pick line quantity retained for reconciliation.",
                "AllocatedInventQty": "Raw sales-order allocation quantity retained only in allocation fact.",
            },
        }
        touches.to_parquet(temporary["touches"], index=False, compression="zstd")
        summary.to_csv(temporary["summary"], index=False)
        line_profile.to_csv(temporary["line_profile"], index=False)
        temporary["metadata"].write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )

        # Build a complete snapshot before replacing any prior local export.
        for name, path in outputs.items():
            temporary[name].replace(path)
    except Exception:
        _remove_existing(list(temporary.values()))
        raise

    print(summary.to_string(index=False))
    print("\nFinal put-line profile:")
    print(line_profile.to_string(index=False))
    print(f"\nRaw allocations:   {outputs['allocations']}")
    print(f"Physical touches:  {outputs['touches']}")
    print(f"Metadata:          {outputs['metadata']}")


if __name__ == "__main__":
    main()
