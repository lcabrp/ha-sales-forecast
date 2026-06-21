"""Refresh WHSPHYSDIMUOM cube signals for the shadow velocity-policy work.

This is intentionally parallel-mode analysis: it reads AX dimensions and local
forecast artifacts, then writes shadow outputs. It does not change ingestion,
the allocator, AX upload files, or production map logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sqlalchemy as sa


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / "scripts" / "python"
SHADOW_DIR = PROJECT_ROOT / "Output" / "Monitoring" / "shadow_velocity_policy"
INGESTION_DIR = PROJECT_ROOT / "Output" / "Ingestion"
DIMENSIONS_SQL = """
SELECT
    CASE
        WHEN ISNULL(ECORESITEMSIZENAME, '') <> ''
            THEN ITEMID + '-' + ECORESITEMCOLORNAME + '-' + ECORESITEMSIZENAME
        WHEN ISNULL(ECORESITEMCOLORNAME, '') <> ''
            THEN ITEMID + '-' + ECORESITEMCOLORNAME
        ELSE ITEMID
    END AS SKU,
    ITEMID AS Item,
    ECORESITEMCOLORNAME AS Color,
    ECORESITEMSIZENAME AS Size,
    CONVERT(decimal(18, 4), ISNULL([WEIGHT], 0)) AS Weight,
    CONVERT(decimal(18, 4), ISNULL([DEPTH], 0)) AS Depth,
    CONVERT(decimal(18, 4), ISNULL([WIDTH], 0)) AS Width,
    CONVERT(decimal(18, 4), ISNULL([HEIGHT], 0)) AS Height,
    CONVERT(decimal(18, 4), ISNULL([DEPTH], 0) * ISNULL([WIDTH], 0) * ISNULL([HEIGHT], 0)) AS UnitCube,
    HA_MEASUREDBY AS MeasuredBy,
    HA_MEASUREDDATE AS MeasuredDate,
    MODIFIEDBY AS ModifiedBy,
    MODIFIEDDATETIME AS ModifiedDateTime
FROM WHSPHYSDIMUOM WITH (NOLOCK)
WHERE DATAAREAID = 'ha'
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=SHADOW_DIR)
    parser.add_argument("--ingestion-dir", type=Path, default=INGESTION_DIR)
    parser.add_argument(
        "--dimensions-parquet",
        type=Path,
        default=None,
        help="Use an existing WHSPHYSDIMUOM parquet instead of querying AX.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "dimensions": output_dir / "velocity_policy_sku_cube_whsphysdimuom.parquet",
        "slot_summary": output_dir / "velocity_policy_cube_slottier_summary.csv",
        "activation_summary": output_dir / "velocity_policy_cube_activation_summary.csv",
        "metadata": output_dir / "velocity_policy_cube_metadata.json",
    }


def prepare_outputs(output_dir: Path, overwrite: bool) -> tuple[dict[str, Path], dict[str, Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = output_paths(output_dir)
    existing = [path for path in outputs.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Cube artifacts already exist. Pass --overwrite to replace them: "
            + ", ".join(str(path) for path in existing)
        )
    temporary = {name: path.with_name(f"{path.name}.tmp") for name, path in outputs.items()}
    for path in temporary.values():
        if path.exists():
            path.unlink()
    return outputs, temporary


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def latest_file(directory: Path, pattern: str) -> Path:
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"No files matched {pattern!r} under {directory}")
    return files[-1]


def normalize_sku(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def pull_dimensions() -> tuple[pd.DataFrame, str]:
    sys.path.insert(0, str(SCRIPT_DIR))
    from sql_utils import get_ax_engine  # noqa: PLC0415

    engine = get_ax_engine(verbose=True)
    with engine.connect() as conn:
        server_row = conn.execute(
            sa.text("SELECT DB_NAME() AS DatabaseName, @@SERVERNAME AS ServerName")
        ).mappings().first()
        server_label = f"{server_row['ServerName']} / {server_row['DatabaseName']}"
        dims = pd.read_sql_query(sa.text(DIMENSIONS_SQL), conn)
    return dims, server_label


def clean_dimensions(dims: pd.DataFrame) -> pd.DataFrame:
    output = dims.copy()
    output["SKU"] = normalize_sku(output["SKU"])
    for column in ["Weight", "Depth", "Width", "Height", "UnitCube"]:
        output[column] = pd.to_numeric(output[column], errors="coerce").fillna(0)
    output["HasCube"] = output["UnitCube"].gt(0)
    output["HasAllDimensions"] = output[["Depth", "Width", "Height"]].gt(0).all(axis=1)
    # If AX ever contains duplicate dimension rows, keep the most recently
    # modified measured row so every downstream SKU join is deterministic.
    output["MeasuredDate"] = pd.to_datetime(output["MeasuredDate"], errors="coerce")
    output["ModifiedDateTime"] = pd.to_datetime(output["ModifiedDateTime"], errors="coerce")
    output = output.sort_values(
        ["SKU", "HasAllDimensions", "ModifiedDateTime", "MeasuredDate"],
        ascending=[True, False, False, False],
        na_position="last",
    )
    output = output.drop_duplicates("SKU", keep="first").sort_values("SKU").reset_index(drop=True)
    return output


def summarize_forecast_cube(
    fwd: pd.DataFrame,
    required_slots: pd.DataFrame,
    dims: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    fd_cols = [column for column in fwd.columns if column.startswith("FD")]
    work = fwd.copy()
    work["SKU"] = normalize_sku(work["SKU"])
    work["SlotTier"] = work["SlotTier"].fillna("").astype(str).str.strip().str.upper()
    work["FD14Units"] = work[fd_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    work["DailyForecastUnits"] = work["FD14Units"] / max(len(fd_cols), 1)
    work = work.merge(dims[["SKU", "UnitCube", "HasCube"]], on="SKU", how="left", validate="many_to_one")
    work["UnitCube"] = pd.to_numeric(work["UnitCube"], errors="coerce").fillna(0)
    work["HasCube"] = work["HasCube"].fillna(False).astype(bool)
    work["DailyForecastUnitCube"] = work["DailyForecastUnits"] * work["UnitCube"]

    required = required_slots.copy()
    required["SlotTier"] = required["SlotTier"].astype(str).str.strip().str.upper()
    required["TotalRequiredSlots"] = pd.to_numeric(
        required["TotalRequiredSlots"], errors="coerce"
    ).fillna(0)

    summary = (
        work.groupby("SlotTier", as_index=False)
        .agg(
            ForecastRows=("SKU", "size"),
            ForecastSKUs=("SKU", "nunique"),
            CubeRows=("HasCube", "sum"),
            FD14Units=("FD14Units", "sum"),
            DailyForecastUnits=("DailyForecastUnits", "sum"),
            DailyForecastUnitCube=("DailyForecastUnitCube", "sum"),
            MedianUnitCube=("UnitCube", lambda values: float(values[values > 0].median())),
            P90UnitCube=("UnitCube", lambda values: float(values[values > 0].quantile(0.90))),
        )
        .merge(required[["SlotTier", "TotalRequiredSlots"]], on="SlotTier", how="left")
    )
    summary["TotalRequiredSlots"] = summary["TotalRequiredSlots"].fillna(0)
    summary["CubeCoveragePct"] = np.where(
        summary["ForecastRows"].gt(0),
        summary["CubeRows"] / summary["ForecastRows"] * 100,
        0,
    )
    summary["DailyCubePerRequiredSlot"] = np.where(
        summary["TotalRequiredSlots"].gt(0),
        summary["DailyForecastUnitCube"] / summary["TotalRequiredSlots"],
        0,
    )
    summary["Velocity"] = summary["SlotTier"].str.extract(r"(AA|A|B|C)$", expand=False).fillna("")
    summary = summary.sort_values(
        ["DailyCubePerRequiredSlot", "DailyForecastUnitCube", "SlotTier"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return work, summary


def summarize_activation(cube_summary: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    activation_path = output_dir / "velocity_policy_incremental_activation_candidates.parquet"
    pressure_path = output_dir / "velocity_policy_palletpicking_profile_pressure.csv"
    frames: list[pd.DataFrame] = []
    if activation_path.exists():
        activation = pd.read_parquet(activation_path)
        tier_col = "RoutingSlotTier" if "RoutingSlotTier" in activation.columns else "SlotTier"
        grouped = (
            activation.groupby(tier_col, as_index=False)
            .agg(ActivationCandidateRows=("SKU", "size"), ActivationCandidateSKUs=("SKU", "nunique"))
            .rename(columns={tier_col: "SlotTier"})
        )
        grouped["SourceScreen"] = "incremental_activation_candidates"
        frames.append(grouped)
    if pressure_path.exists():
        pressure = pd.read_csv(pressure_path)
        if "SlotTier" in pressure.columns:
            keep_cols = [
                col
                for col in [
                    "SlotTier",
                    "PalletPressureScore",
                    "CandidatePlanningShortfall",
                    "HasPalletPickingPaint",
                ]
                if col in pressure.columns
            ]
            grouped = pressure[keep_cols].copy()
            grouped["SourceScreen"] = "palletpicking_profile_pressure"
            frames.append(grouped)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined["SlotTier"] = combined["SlotTier"].astype(str).str.strip().str.upper()
    return combined.merge(cube_summary, on="SlotTier", how="left", validate="many_to_one")


def safe_write(outputs: dict[str, Path], temporary: dict[str, Path], frames: dict[str, pd.DataFrame], metadata: dict) -> None:
    frames["dimensions"].to_parquet(temporary["dimensions"], index=False)
    frames["slot_summary"].to_csv(temporary["slot_summary"], index=False)
    frames["activation_summary"].to_csv(temporary["activation_summary"], index=False)
    with temporary["metadata"].open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
    for name, path in outputs.items():
        temporary[name].replace(path)


def main() -> None:
    args = parse_args()
    outputs, temporary = prepare_outputs(args.output_dir, args.overwrite)

    if args.dimensions_parquet:
        dims_raw = pd.read_parquet(args.dimensions_parquet)
        server_label = f"existing parquet: {relative(args.dimensions_parquet)}"
    else:
        dims_raw, server_label = pull_dimensions()
    dims = clean_dimensions(dims_raw)

    fwd_path = latest_file(args.ingestion_dir, "FwdDemandCSV_*.csv")
    slots_path = latest_file(args.ingestion_dir, "RequiredSlots_*.csv")
    fwd = pd.read_csv(fwd_path, dtype={"Item": str, "Color": str, "Size": str})
    required_slots = pd.read_csv(slots_path)
    _, slot_summary = summarize_forecast_cube(fwd, required_slots, dims)
    activation_summary = summarize_activation(slot_summary, args.output_dir)

    metadata = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "mode": "shadow_only_no_production_changes",
        "cube_source": "AX WHSPHYSDIMUOM",
        "server": server_label,
        "input_files": {
            "fwd_demand": relative(fwd_path),
            "required_slots": relative(slots_path),
            **(
                {"dimensions_parquet": relative(args.dimensions_parquet)}
                if args.dimensions_parquet
                else {}
            ),
        },
        "notes": [
            "UnitCube is DEPTH * WIDTH * HEIGHT from WHSPHYSDIMUOM.",
            "This is a unit/piece cube proxy, not carton cube.",
            "Cube is an exception and capacity-pressure signal; it should not override co-purchase/travel adjacency by itself.",
        ],
        "row_counts": {
            "dimension_source_rows": int(len(dims_raw)),
            "dimension_unique_skus": int(len(dims)),
            "dimension_skus_with_cube": int(dims["HasCube"].sum()),
            "slot_summary_rows": int(len(slot_summary)),
            "activation_summary_rows": int(len(activation_summary)),
        },
    }
    safe_write(
        outputs,
        temporary,
        {
            "dimensions": dims,
            "slot_summary": slot_summary,
            "activation_summary": activation_summary,
        },
        metadata,
    )
    print(json.dumps({"outputs": {k: relative(v) for k, v in outputs.items()}}, indent=2))


if __name__ == "__main__":
    main()
