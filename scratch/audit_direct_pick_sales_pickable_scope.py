"""Audit DirectPick history scope for forecast-training demand facts."""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import sqlalchemy as sa


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIR = PROJECT_ROOT / "scripts" / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from sql_utils import get_ax_engine  # noqa: E402


OUTPUT_DIR = PROJECT_ROOT / "scratch" / "direct_pick_scope_audit"
START_DATE = date(2022, 1, 1)
END_EXCLUSIVE = date(2026, 6, 19)
PICKABLE_PROFILES = ("Picking", "Picking A", "PalletPicking", "Picking D")
EXCLUDED_LOCATIONS = ("Bander", "AutoBagger")


def detect_archive_boundary(engine: sa.Engine) -> date:
    query = sa.text(
        """
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT CAST(MAX(CREATEDDATETIME) AS DATE) AS MaxArchiveDate
        FROM DAX_Archive.arc.WHSWORKTABLE WITH (NOLOCK)
        WHERE DATAAREAID = 'ha'
          AND [PARTITION] = 5637144576
          AND WORKSTATUS = 4
        """
    )
    with engine.connect() as conn:
        row = conn.execute(query).fetchone()
    if not row or row[0] is None:
        raise RuntimeError("Cannot determine archive boundary.")
    return pd.to_datetime(row[0]).date()


def source_segments(start: date, end_exclusive: date, archive_boundary: date) -> list[tuple[str, date, date]]:
    segments: list[tuple[str, date, date]] = []
    archive_end = min(end_exclusive, archive_boundary)
    prod_start = max(start, archive_boundary)
    if start < archive_end:
        segments.append(("DAX_Archive.arc", start, archive_end))
    if prod_start < end_exclusive:
        segments.append(("DAX_PROD.dbo", prod_start, end_exclusive))
    return segments


def year_windows(start: date, end_exclusive: date) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    current = start
    while current < end_exclusive:
        window_end = min(date(current.year + 1, 1, 1), end_exclusive)
        windows.append((current, window_end))
        current = window_end
    return windows


def scope_query(schema: str, location_schema: str = "DAX_PROD.dbo") -> sa.TextClause:
    return sa.text(
        f"""
        SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;

        SELECT
            YEAR(CAST(wl.MODIFIEDDATETIME AS DATE)) AS PickYear,
            wt.INVENTLOCATIONID AS Warehouse,
            wt.WORKTRANSTYPE AS WorkTransType,
            COALESCE(loc.LOCPROFILEID, '<missing WMSLOCATION>') AS LocProfileId,
            CASE
                WHEN loc.LOCPROFILEID IN ('Picking', 'Picking A', 'PalletPicking', 'Picking D') THEN 1
                ELSE 0
            END AS IsPickableProfile,
            CASE
                WHEN wl.WMSLOCATIONID IN ('Bander', 'AutoBagger') THEN 1
                ELSE 0
            END AS IsExcludedLocation,
            COUNT_BIG(*) AS PickLines,
            COUNT(DISTINCT wt.ORDERNUM) AS DistinctOrders,
            SUM(CAST(wl.QTYWORK AS DECIMAL(18, 4))) AS PickUnits
        FROM {schema}.WHSWORKTABLE wt WITH (NOLOCK)
        INNER JOIN {schema}.WHSWORKLINE wl WITH (NOLOCK)
            ON wt.[PARTITION] = wl.[PARTITION]
           AND wt.DATAAREAID = wl.DATAAREAID
           AND wt.WORKID = wl.WORKID
        LEFT JOIN {location_schema}.WMSLOCATION loc WITH (NOLOCK)
            ON loc.WMSLOCATIONID = wl.WMSLOCATIONID
           AND loc.INVENTLOCATIONID = wt.INVENTLOCATIONID
           AND loc.DATAAREAID = wl.DATAAREAID
           AND loc.[PARTITION] = wl.[PARTITION]
        WHERE wt.DATAAREAID = 'ha'
          AND wt.[PARTITION] = 5637144576
          AND wt.WORKSTATUS = 4
          AND wl.WORKSTATUS = 4
          AND wl.WORKTYPE = 1
          AND wl.WORKCLASSID = 'DirectPick'
          AND wl.MODIFIEDDATETIME >= :start_dt
          AND wl.MODIFIEDDATETIME < :end_dt
        GROUP BY
            YEAR(CAST(wl.MODIFIEDDATETIME AS DATE)),
            wt.INVENTLOCATIONID,
            wt.WORKTRANSTYPE,
            COALESCE(loc.LOCPROFILEID, '<missing WMSLOCATION>'),
            CASE
                WHEN loc.LOCPROFILEID IN ('Picking', 'Picking A', 'PalletPicking', 'Picking D') THEN 1
                ELSE 0
            END,
            CASE
                WHEN wl.WMSLOCATIONID IN ('Bander', 'AutoBagger') THEN 1
                ELSE 0
            END
        """
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    engine = get_ax_engine(verbose=True)
    archive_boundary = detect_archive_boundary(engine)
    frames: list[pd.DataFrame] = []
    with engine.connect() as conn:
        for year_start, year_end in year_windows(START_DATE, END_EXCLUSIVE):
            for schema, seg_start, seg_end in source_segments(year_start, year_end, archive_boundary):
                print(f"Auditing {schema} {seg_start} to {seg_end}")
                frame = pd.read_sql_query(
                    scope_query(schema),
                    conn,
                    params={"start_dt": seg_start.isoformat(), "end_dt": seg_end.isoformat()},
                )
                if not frame.empty:
                    frame["SourceSchema"] = schema
                    frame["SegmentStart"] = seg_start.isoformat()
                    frame["SegmentEndExclusive"] = seg_end.isoformat()
                    frames.append(frame)
                    print(f"  profile rows {len(frame):,} units {frame['PickUnits'].sum():,.0f}")

    if not frames:
        raise RuntimeError("No DirectPick rows returned for audit.")

    detail = pd.concat(frames, ignore_index=True)
    detail["MeetsStrictSalesPickableScope"] = (
        detail["Warehouse"].astype(str).eq("4010")
        & detail["WorkTransType"].eq(2)
        & detail["IsPickableProfile"].eq(1)
        & detail["IsExcludedLocation"].eq(0)
    )
    numeric = ["PickLines", "DistinctOrders", "PickUnits"]
    for column in numeric:
        detail[column] = pd.to_numeric(detail[column], errors="coerce").fillna(0)

    by_year_scope = (
        detail.groupby(["PickYear", "MeetsStrictSalesPickableScope"], as_index=False)[numeric]
        .sum()
        .sort_values(["PickYear", "MeetsStrictSalesPickableScope"])
    )
    profile_summary = (
        detail.groupby(
            [
                "Warehouse",
                "WorkTransType",
                "LocProfileId",
                "IsPickableProfile",
                "IsExcludedLocation",
                "MeetsStrictSalesPickableScope",
            ],
            as_index=False,
        )[numeric]
        .sum()
        .sort_values("PickUnits", ascending=False)
    )
    totals = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "window": {"start_date": START_DATE.isoformat(), "end_date_exclusive": END_EXCLUSIVE.isoformat()},
        "archive_boundary": archive_boundary.isoformat(),
        "strict_scope": {
            "warehouse": "4010",
            "work_trans_type": 2,
            "work_class_id": "DirectPick",
            "work_type": 1,
            "work_status": 4,
            "loc_profiles": list(PICKABLE_PROFILES),
            "excluded_locations": list(EXCLUDED_LOCATIONS),
            "date_basis": "WHSWORKLINE.MODIFIEDDATETIME",
            "location_profile_source": "DAX_PROD.dbo.WMSLOCATION",
            "location_profile_assumption": "Location profiles are treated as stable enough to classify archived picks.",
        },
        "broad_direct_pick_units": float(detail["PickUnits"].sum()),
        "strict_sales_pickable_units": float(detail.loc[detail["MeetsStrictSalesPickableScope"], "PickUnits"].sum()),
        "excluded_units": float(detail.loc[~detail["MeetsStrictSalesPickableScope"], "PickUnits"].sum()),
        "strict_sales_pickable_pick_lines": int(detail.loc[detail["MeetsStrictSalesPickableScope"], "PickLines"].sum()),
    }
    totals["strict_sales_pickable_unit_pct"] = (
        totals["strict_sales_pickable_units"] / totals["broad_direct_pick_units"]
        if totals["broad_direct_pick_units"]
        else 0.0
    )

    detail.to_csv(OUTPUT_DIR / "direct_pick_scope_audit_detail.csv", index=False)
    by_year_scope.to_csv(OUTPUT_DIR / "direct_pick_scope_audit_by_year_scope.csv", index=False)
    profile_summary.to_csv(OUTPUT_DIR / "direct_pick_scope_audit_profile_summary.csv", index=False)
    (OUTPUT_DIR / "direct_pick_scope_audit_metadata.json").write_text(
        json.dumps(totals, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(totals, indent=2))
    print(by_year_scope.to_string(index=False))
    print(profile_summary.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
