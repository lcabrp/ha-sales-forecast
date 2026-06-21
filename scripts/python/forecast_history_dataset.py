"""Build multi-year forecast accuracy datasets from AX forecast files and picks.

Outputs are generated local artifacts under Output/ForecastAccuracy/history:

- raw_forecasts/: unique recovered source CSV copies
- parquet/forecast_snapshot_files.parquet
- parquet/forecast_sku_snapshot.parquet
- parquet/forecast_sku_day.parquet
- parquet/actual_sku_day_modified.parquet
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import sqlalchemy as sa

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402
from sql_utils import get_ax_engine  # noqa: E402


DEFAULT_HISTORY_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy" / "history"
DEFAULT_RAW_DIR = DEFAULT_HISTORY_DIR / "raw_forecasts"
DEFAULT_PARQUET_DIR = DEFAULT_HISTORY_DIR / "parquet"
DEFAULT_FOLDERS = [
    Path(r"\\tk-ax-report\Documents\ForwardReplen\Error"),
    Path(r"\\tk-ax-report\Documents\ForwardReplen\Complete"),
    Path(r"\\tk-ax-report\Documents\ForwardReplen\Processing"),
]
FD_COLUMNS = [f"FD{i}" for i in range(1, 15)]
SNAPSHOT_COLUMNS = [
    "Division",
    "Department",
    "Class",
    "KeyCategoryView",
    "SKU",
    "Item",
    "Color",
    "Size",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "ReplenishmentThreshold",
    "PutawayIndicator",
    "ForecastStartDate",
]
CSV_NAME_RE = re.compile(r"(fwd|forward).*demand|forecast", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build historical forecast accuracy datasets.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect-forecasts")
    collect.add_argument("--output-dir", type=Path, default=DEFAULT_HISTORY_DIR)
    collect.add_argument("--since", default="2022-01-01")
    collect.add_argument("--folder", type=Path, action="append", dest="folders")

    actuals = subparsers.add_parser("collect-actuals")
    actuals.add_argument("--output-dir", type=Path, default=DEFAULT_HISTORY_DIR)
    actuals.add_argument("--start-date", required=True)
    actuals.add_argument("--end-date", required=True)
    actuals.add_argument("--date-field", choices=("modified", "created"), default="modified")
    actuals.add_argument("--server", default="prodaxsql2")
    actuals.add_argument("--database", default="DAX_PROD")

    summaries = subparsers.add_parser("build-summaries")
    summaries.add_argument("--output-dir", type=Path, default=DEFAULT_HISTORY_DIR)
    summaries.add_argument("--date-basis", choices=("modified",), default="modified")

    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("._") or "forecast"


def normalize_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def candidate_dates_from_name(name: str, modified_date: date) -> list[date]:
    candidates: list[date] = []
    for year, month, day in re.findall(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})", name):
        candidates.append(date(int(year), int(month), int(day)))
    for month, day, year in re.findall(r"(?<!\d)(\d{1,2})[._-](\d{1,2})[._-](\d{2,4})(?!\d)", name):
        y = int(year)
        if y < 100:
            y += 2000
        candidates.append(date(y, int(month), int(day)))

    compact_values = re.findall(r"(?<!\d)(\d{3,6})(?!\d)", name)
    for value in compact_values:
        if len(value) in {3, 4, 5, 6}:
            y = int(value[-2:]) + 2000
            body = value[:-2]
            possible = []
            if len(body) >= 2:
                possible.append((int(body[:-2] or "0"), int(body[-2:])))
            if len(body) >= 2:
                possible.append((int(body[:-1]), int(body[-1:])))
            for month, day in possible:
                try:
                    candidates.append(date(y, month, day))
                except ValueError:
                    pass
    if not candidates:
        candidates.append(modified_date)
    unique = sorted(set(candidates), key=lambda d: abs((d - modified_date).days))
    return unique


def read_forecast(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=lambda col: col in {*SNAPSHOT_COLUMNS, *FD_COLUMNS}, low_memory=False)
    missing = sorted({"SKU", "ForecastStartDate", *FD_COLUMNS} - set(df.columns))
    if missing:
        raise ValueError(f"{path.name} missing required columns: {missing}")
    for col in SNAPSHOT_COLUMNS:
        if col in df.columns and col not in {"PutawayIndicator", "ReplenishmentThreshold"}:
            df[col] = normalize_text(df[col])
    for col in FD_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "PutawayIndicator" in df.columns:
        df["PutawayIndicator"] = pd.to_numeric(df["PutawayIndicator"], errors="coerce").astype("Int64")
    if "ReplenishmentThreshold" in df.columns:
        df["ReplenishmentThreshold"] = pd.to_numeric(df["ReplenishmentThreshold"], errors="coerce")
    parsed_start = pd.to_datetime(df["ForecastStartDate"], errors="coerce").dt.date
    invalid_start_count = int(parsed_start.isna().sum())
    if invalid_start_count:
        valid_start = parsed_start.dropna()
        if valid_start.empty:
            raise ValueError(f"{path.name} has no valid ForecastStartDate values")
        fill_start = valid_start.mode().iloc[0]
        print(
            f"[!] {path.name}: filled {invalid_start_count:,} invalid ForecastStartDate rows with {fill_start}"
        )
        parsed_start = parsed_start.fillna(fill_start)
    df["ForecastStartDate"] = parsed_start
    for col in SNAPSHOT_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col not in {"PutawayIndicator", "ReplenishmentThreshold"} else pd.NA
    return df


def list_forecast_files(folders: list[Path], since: date) -> list[Path]:
    paths = []
    for folder in folders:
        if not folder.exists():
            print(f"[!] Missing folder: {folder}")
            continue
        for child in folder.iterdir():
            if not child.is_file() or child.suffix.lower() != ".csv":
                continue
            if not CSV_NAME_RE.search(child.name):
                continue
            if datetime.fromtimestamp(child.stat().st_mtime).date() < since:
                continue
            paths.append(child)
    return sorted(paths, key=lambda p: (p.stat().st_mtime, str(p)))


def select_weekly(metadata: pd.DataFrame) -> pd.DataFrame:
    metadata = metadata.copy()
    metadata["IsSelectedWeekly"] = False
    metadata["SelectionReason"] = "not_selected"
    metadata["FolderPriority"] = metadata["SourceFolderName"].map(
        {"Error": 0, "Complete": 1, "Processing": 2}
    ).fillna(9)
    keys = ["ForecastStartDateMin", "ForecastedSKUs", "TotalForecastUnits"]
    for _key, group in metadata.groupby(keys, dropna=False):
        ordered = group.sort_values(["FolderPriority", "SourceModifiedTime", "SourceFile"])
        idx = ordered.index[0]
        metadata.loc[idx, "IsSelectedWeekly"] = True
        metadata.loc[idx, "SelectionReason"] = "selected_weekly_forecast_key"
    return metadata.drop(columns=["FolderPriority"])


def collect_forecasts(output_dir: Path, folders: list[Path], since_text: str) -> None:
    since = date.fromisoformat(since_text)
    raw_dir = output_dir / "raw_forecasts"
    parquet_dir = output_dir / "parquet"
    raw_dir.mkdir(parents=True, exist_ok=True)
    parquet_dir.mkdir(parents=True, exist_ok=True)

    files = list_forecast_files(folders, since)
    print(f"Discovered forecast CSVs: {len(files):,}")
    seen_hashes: dict[str, str] = {}
    metadata_rows = []
    sku_frames = []
    day_frames = []

    for ordinal, path in enumerate(files, start=1):
        source_sha = sha256(path)
        if source_sha in seen_hashes:
            print(f"skip hash duplicate {path.name} -> {seen_hashes[source_sha]}")
            continue
        seen_hashes[source_sha] = path.name
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        file_date = candidate_dates_from_name(path.name, modified.date())[0]
        if path.parent.resolve() == raw_dir.resolve():
            dest = path
        else:
            dest_name = f"{file_date.isoformat()}_{source_sha[:12]}_{safe_name(path.name)}"
            dest = raw_dir / dest_name
        if path.resolve() != dest.resolve() and not dest.exists():
            shutil.copy2(path, dest)

        try:
            df = read_forecast(dest)
        except Exception as exc:
            print(f"[!] Failed to parse {path}: {exc}")
            continue
        df["ForecastTotal14Day"] = df[FD_COLUMNS].sum(axis=1)
        df["HasForecastDemand"] = (df["ForecastTotal14Day"] > 0).astype("int8")
        snapshot_id = hashlib.sha256(f"{source_sha}|{path.name}".encode("utf-8")).hexdigest()
        file_date_text = file_date.isoformat()

        putaway = df["PutawayIndicator"].value_counts(dropna=False).to_dict()
        metadata_rows.append(
            {
                "SnapshotId": snapshot_id,
                "SourceFile": path.name,
                "SourcePath": str(path),
                "LocalRawPath": str(dest),
                "SourceFolderName": path.parent.name,
                "SourceSha256": source_sha,
                "InferredFileDate": file_date_text,
                "SourceModifiedTime": modified.isoformat(),
                "ImportedAtUTC": datetime.now(UTC).replace(microsecond=0).isoformat(),
                "RowsImported": int(len(df)),
                "DistinctSKUs": int(df["SKU"].nunique()),
                "ForecastStartDateMin": min(df["ForecastStartDate"]).isoformat(),
                "ForecastStartDateMax": max(df["ForecastStartDate"]).isoformat(),
                "ForecastedSKUs": int(df["HasForecastDemand"].sum()),
                "ZeroForecastSKUs": int((df["ForecastTotal14Day"] == 0).sum()),
                "TotalForecastUnits": float(df["ForecastTotal14Day"].sum()),
                "ActiveSKUCount": int(putaway.get(1, 0)),
                "ReserveSKUCount": int(putaway.get(0, 0)),
                "OffsiteSKUCount": int(putaway.get(2, 0)),
            }
        )

        sku_df = df[SNAPSHOT_COLUMNS + ["ForecastTotal14Day", "HasForecastDemand"]].copy()
        sku_df.insert(0, "InferredFileDate", file_date_text)
        sku_df.insert(0, "SnapshotId", snapshot_id)
        sku_df["ForecastStartDate"] = sku_df["ForecastStartDate"].map(lambda value: value.isoformat())
        sku_frames.append(sku_df)

        base = sku_df[["SnapshotId", "InferredFileDate", "SKU", "ForecastStartDate"]].copy()
        base["ForecastStartDate"] = pd.to_datetime(base["ForecastStartDate"]).dt.date
        for offset, fd_col in enumerate(FD_COLUMNS, start=1):
            day_df = base.copy()
            day_df["ForecastDayOffset"] = offset
            day_df["ForecastDate"] = day_df["ForecastStartDate"].map(
                lambda value, offset=offset: (value + timedelta(days=offset - 1)).isoformat()
            )
            day_df["ForecastQty"] = df[fd_col].astype(float).to_numpy()
            day_df = day_df[day_df["ForecastQty"] != 0].copy()
            day_df["ForecastStartDate"] = day_df["ForecastStartDate"].map(lambda value: value.isoformat())
            day_frames.append(day_df)
        if ordinal % 25 == 0:
            print(f"processed {ordinal:,}/{len(files):,}")

    metadata = select_weekly(pd.DataFrame(metadata_rows))
    selected_ids = set(metadata.loc[metadata["IsSelectedWeekly"], "SnapshotId"])
    sku = pd.concat(sku_frames, ignore_index=True)
    sku = sku[sku["SnapshotId"].isin(selected_ids)].copy()
    day = pd.concat(day_frames, ignore_index=True)
    day = day[day["SnapshotId"].isin(selected_ids)].copy()

    metadata.to_parquet(parquet_dir / "forecast_snapshot_files.parquet", index=False, compression="zstd")
    sku.to_parquet(parquet_dir / "forecast_sku_snapshot.parquet", index=False, compression="zstd")
    day.to_parquet(parquet_dir / "forecast_sku_day.parquet", index=False, compression="zstd")
    metadata.to_csv(output_dir / "forecast_snapshot_manifest.csv", index=False)

    print(f"Unique raw forecast CSVs: {len(metadata):,}")
    print(f"Selected weekly snapshots: {metadata['IsSelectedWeekly'].sum():,}")
    print(f"SKU snapshot rows: {len(sku):,}")
    print(f"Nonzero forecast day rows: {len(day):,}")
    print(f"Parquet: {parquet_dir}")


def actuals_query(schema: str, date_expr: str) -> sa.TextClause:
    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            CAST({date_expr} AS DATE) AS ActualDate,
            CASE
                WHEN ISNULL(idim.INVENTCOLORID, '') = '' THEN wkln.ITEMID
                WHEN ISNULL(idim.INVENTSIZEID, '') = '' THEN wkln.ITEMID + '-' + idim.INVENTCOLORID
                ELSE wkln.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID
            END AS SKU,
            SUM(CAST(wkln.QTYWORK AS FLOAT)) AS SoldUnits,
            COUNT(*) AS PickLines,
            COUNT(DISTINCT wktbl.ORDERNUM) AS DistinctOrders
        FROM {schema}.WHSWORKTABLE wktbl WITH (NOLOCK)
        JOIN {schema}.WHSWORKLINE wkln WITH (NOLOCK)
            ON wktbl.WORKID = wkln.WORKID
           AND wktbl.DATAAREAID = wkln.DATAAREAID
           AND wktbl.[PARTITION] = wkln.[PARTITION]
        JOIN {schema}.INVENTDIM idim WITH (NOLOCK)
            ON idim.INVENTDIMID = wkln.INVENTDIMID
           AND idim.DATAAREAID = wkln.DATAAREAID
           AND idim.[PARTITION] = wkln.[PARTITION]
        WHERE wktbl.DATAAREAID = 'ha'
          AND wktbl.[PARTITION] = 5637144576
          AND wktbl.WORKSTATUS = 4
          AND wkln.WORKSTATUS = 4
          AND wkln.WORKTYPE = 1
          AND wkln.WORKCLASSID = 'DirectPick'
          AND {date_expr} >= :start_dt
          AND {date_expr} < :end_dt
        GROUP BY
            CAST({date_expr} AS DATE),
            CASE
                WHEN ISNULL(idim.INVENTCOLORID, '') = '' THEN wkln.ITEMID
                WHEN ISNULL(idim.INVENTSIZEID, '') = '' THEN wkln.ITEMID + '-' + idim.INVENTCOLORID
                ELSE wkln.ITEMID + '-' + idim.INVENTCOLORID + '-' + idim.INVENTSIZEID
            END
        """
    )


def detect_archive_boundary(engine: sa.Engine) -> date | None:
    query = sa.text(
        """
        SELECT CAST(MAX(CREATEDDATETIME) AS DATE) AS MaxArchiveDate
        FROM DAX_Archive.arc.WHSWORKTABLE WITH (NOLOCK)
        WHERE DATAAREAID = 'ha'
          AND [PARTITION] = 5637144576
          AND WORKSTATUS = 4
        """
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(query).fetchone()
    except Exception as exc:
        print(f"[!] Could not detect archive boundary; using both sources by year. Detail: {exc}")
        return None
    if not row or row[0] is None:
        return None
    return pd.to_datetime(row[0]).date()


def source_segments(start: date, end: date, archive_boundary: date | None) -> list[tuple[str, date, date]]:
    if archive_boundary is None:
        return [("DAX_Archive.arc", start, end), ("DAX_PROD.dbo", start, end)]
    segments: list[tuple[str, date, date]] = []
    if start < archive_boundary:
        segments.append(("DAX_Archive.arc", start, min(end, archive_boundary - timedelta(days=1))))
    if end >= archive_boundary:
        segments.append(("DAX_PROD.dbo", max(start, archive_boundary), end))
    return [(schema, seg_start, seg_end) for schema, seg_start, seg_end in segments if seg_start <= seg_end]


def collect_actuals(
    output_dir: Path,
    start_text: str,
    end_text: str,
    date_field: str,
    server: str,
    database: str,
) -> None:
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    parquet_dir = output_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    engine = get_ax_engine(server=server, database=database, verbose=True)
    archive_boundary = detect_archive_boundary(engine)
    print(f"Archive boundary: {archive_boundary}")
    date_expr = "wkln.MODIFIEDDATETIME" if date_field == "modified" else "wktbl.CREATEDDATETIME"
    frames = []
    with engine.connect() as conn:
        for schema, seg_start, seg_end in source_segments(start, end, archive_boundary):
            current = seg_start
            while current <= seg_end:
                year_end = min(date(current.year, 12, 31), seg_end)
                print(f"pull {schema} {current} to {year_end}")
                frame = pd.read_sql_query(
                    actuals_query(schema, date_expr),
                    conn,
                    params={
                        "start_dt": current.isoformat(),
                        "end_dt": (year_end + timedelta(days=1)).isoformat(),
                    },
                )
                if not frame.empty:
                    frame["SourceSchema"] = schema
                    frames.append(frame)
                    print(f"  rows {len(frame):,} units {frame['SoldUnits'].sum():,.0f}")
                current = year_end + timedelta(days=1)

    if not frames:
        raise RuntimeError("No actual rows returned.")
    actuals = pd.concat(frames, ignore_index=True)
    actuals["ActualDate"] = pd.to_datetime(actuals["ActualDate"]).dt.date.map(lambda value: value.isoformat())
    actuals["SKU"] = normalize_text(actuals["SKU"])
    grouped = (
        actuals.groupby(["ActualDate", "SKU"], as_index=False)
        .agg(
            SoldUnits=("SoldUnits", "sum"),
            PickLines=("PickLines", "sum"),
            DistinctOrders=("DistinctOrders", "sum"),
        )
        .sort_values(["ActualDate", "SKU"])
    )
    grouped.insert(1, "DateBasis", date_field)
    output = parquet_dir / f"actual_sku_day_{date_field}.parquet"
    grouped.to_parquet(output, index=False, compression="zstd")
    summary = pd.DataFrame(
        [
            {
                "DateBasis": date_field,
                "StartDate": grouped["ActualDate"].min(),
                "EndDate": grouped["ActualDate"].max(),
                "Rows": len(grouped),
                "DistinctSKUs": grouped["SKU"].nunique(),
                "SoldUnits": float(grouped["SoldUnits"].sum()),
                "OutputPath": str(output),
            }
        ]
    )
    summary.to_csv(output_dir / f"actual_sku_day_{date_field}_summary.csv", index=False)
    print(summary.to_string(index=False))


def build_summaries(output_dir: Path, date_basis: str) -> None:
    parquet_dir = output_dir / "parquet"
    metadata = pd.read_parquet(parquet_dir / "forecast_snapshot_files.parquet")
    sku = pd.read_parquet(parquet_dir / "forecast_sku_snapshot.parquet")
    actual = pd.read_parquet(parquet_dir / f"actual_sku_day_{date_basis}.parquet")

    metadata = metadata[metadata["IsSelectedWeekly"]].copy()
    sku["ForecastStartDate"] = pd.to_datetime(sku["ForecastStartDate"])
    actual["ActualDate"] = pd.to_datetime(actual["ActualDate"])
    actual_max = actual["ActualDate"].max()

    summary_rows = []
    category_frames = []
    bucket_rows = []
    for _idx, snapshot in metadata.sort_values("ForecastStartDateMin").iterrows():
        snapshot_id = snapshot["SnapshotId"]
        start = pd.to_datetime(snapshot["ForecastStartDateMin"])
        end = pd.to_datetime(snapshot["ForecastStartDateMax"]) + pd.Timedelta(days=13)
        forecast = sku[sku["SnapshotId"].eq(snapshot_id)].copy()
        forecast["ForecastUnits"] = forecast["ForecastTotal14Day"].astype(float)
        actual_window = (
            actual[(actual["ActualDate"] >= start) & (actual["ActualDate"] <= end)]
            .groupby("SKU", as_index=False)["SoldUnits"]
            .sum()
        )

        compare = forecast[["SKU", "KeyCategoryView", "ForecastUnits"]].merge(
            actual_window, on="SKU", how="outer"
        )
        compare["KeyCategoryView"] = normalize_text(compare["KeyCategoryView"])
        compare["ForecastUnits"] = compare["ForecastUnits"].fillna(0.0)
        compare["SoldUnits"] = compare["SoldUnits"].fillna(0.0)
        compare["AbsError"] = (compare["ForecastUnits"] - compare["SoldUnits"]).abs()

        sold_units = float(compare["SoldUnits"].sum())
        forecast_units = float(compare["ForecastUnits"].sum())
        sold_units_with_forecast = float(
            compare[(compare["SoldUnits"] > 0) & (compare["ForecastUnits"] > 0)]["SoldUnits"].sum()
        )
        zero_forecast_sold_units = float(
            compare[(compare["SoldUnits"] > 0) & (compare["ForecastUnits"] == 0)]["SoldUnits"].sum()
        )
        sold_skus = int((compare["SoldUnits"] > 0).sum())
        sold_skus_with_forecast = int(
            ((compare["SoldUnits"] > 0) & (compare["ForecastUnits"] > 0)).sum()
        )

        summary_rows.append(
            {
                "SnapshotId": snapshot_id,
                "SourceFile": snapshot["SourceFile"],
                "InferredFileDate": snapshot["InferredFileDate"],
                "ForecastStartDate": start.date().isoformat(),
                "ForecastEndDate": end.date().isoformat(),
                "CompleteActualWindow": bool(end <= actual_max),
                "ForecastedSKUs": int((compare["ForecastUnits"] > 0).sum()),
                "SoldSKUs": sold_skus,
                "UnionSKUs": int(((compare["ForecastUnits"] > 0) | (compare["SoldUnits"] > 0)).sum()),
                "ForecastUnits": forecast_units,
                "SoldUnits": sold_units,
                "NetErrorUnits": sold_units - forecast_units,
                "AbsErrorUnits": float(compare["AbsError"].sum()),
                "WAPE": float(compare["AbsError"].sum() / sold_units) if sold_units else pd.NA,
                "SoldSKUsWithForecast": sold_skus_with_forecast,
                "SoldSKUForecastCoveragePct": (
                    float(sold_skus_with_forecast / sold_skus) if sold_skus else pd.NA
                ),
                "SoldUnitsWithForecast": sold_units_with_forecast,
                "SoldUnitForecastCoveragePct": (
                    float(sold_units_with_forecast / sold_units) if sold_units else pd.NA
                ),
                "ZeroForecastSoldSKUs": int(
                    ((compare["SoldUnits"] > 0) & (compare["ForecastUnits"] == 0)).sum()
                ),
                "ZeroForecastSoldUnits": zero_forecast_sold_units,
                "ZeroForecastSoldUnitPct": (
                    float(zero_forecast_sold_units / sold_units) if sold_units else pd.NA
                ),
            }
        )

        active_compare = compare[(compare["ForecastUnits"] > 0) | (compare["SoldUnits"] > 0)]
        absolute_variance = (active_compare["ForecastUnits"] - active_compare["SoldUnits"]).abs()
        bucket_rows.append(
            {
                "SnapshotId": snapshot_id,
                "SourceFile": snapshot["SourceFile"],
                "InferredFileDate": snapshot["InferredFileDate"],
                "ForecastStartDate": start.date().isoformat(),
                "Variances of 0": int((absolute_variance == 0).sum()),
                "Variances of +/- 1-5": int(((absolute_variance >= 1) & (absolute_variance <= 5)).sum()),
                "Variances of +/- 6-10": int(((absolute_variance >= 6) & (absolute_variance <= 10)).sum()),
                "Variances of +/- 11-25": int(((absolute_variance >= 11) & (absolute_variance <= 25)).sum()),
                "Variances of +/- 26-50": int(((absolute_variance >= 26) & (absolute_variance <= 50)).sum()),
                "Variances of +/- 51-75": int(((absolute_variance >= 51) & (absolute_variance <= 75)).sum()),
                "Variances of +/- 76-99": int(((absolute_variance >= 76) & (absolute_variance <= 99)).sum()),
                "Variances of +/- 100+": int((absolute_variance >= 100).sum()),
            }
        )

        category = (
            compare.groupby("KeyCategoryView", dropna=False)
            .agg(
                ForecastUnits=("ForecastUnits", "sum"),
                SoldUnits=("SoldUnits", "sum"),
                AbsErrorUnits=("AbsError", "sum"),
                ForecastedSKUs=("ForecastUnits", lambda values: int((values > 0).sum())),
                SoldSKUs=("SoldUnits", lambda values: int((values > 0).sum())),
            )
            .reset_index()
        )
        category.insert(0, "SnapshotId", snapshot_id)
        category.insert(1, "InferredFileDate", snapshot["InferredFileDate"])
        category.insert(2, "ForecastStartDate", start.date().isoformat())
        category_frames.append(category)

    summary = pd.DataFrame(summary_rows)
    category = pd.concat(category_frames, ignore_index=True)
    buckets = pd.DataFrame(bucket_rows)
    summary.to_parquet(parquet_dir / "forecast_accuracy_snapshot_summary.parquet", index=False, compression="zstd")
    category.to_parquet(parquet_dir / "forecast_accuracy_category_summary.parquet", index=False, compression="zstd")
    buckets.to_parquet(parquet_dir / "forecast_accuracy_variance_buckets.parquet", index=False, compression="zstd")
    summary.to_csv(output_dir / "forecast_accuracy_snapshot_summary.csv", index=False)
    buckets.to_csv(output_dir / "forecast_accuracy_variance_buckets.csv", index=False)
    print(f"Snapshot summaries: {len(summary):,}")
    print(f"Category summary rows: {len(category):,}")
    print(f"Actual max date: {actual_max.date()}")


def main() -> None:
    args = parse_args()
    if args.command == "collect-forecasts":
        collect_forecasts(args.output_dir, args.folders or DEFAULT_FOLDERS, args.since)
    elif args.command == "collect-actuals":
        collect_actuals(
            args.output_dir,
            args.start_date,
            args.end_date,
            args.date_field,
            args.server,
            args.database,
        )
    elif args.command == "build-summaries":
        build_summaries(args.output_dir, args.date_basis)


if __name__ == "__main__":
    main()
