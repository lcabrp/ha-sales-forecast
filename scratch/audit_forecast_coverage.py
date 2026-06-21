"""Audit whether current AX forecast output covers the pipeline SKU universe."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "python"))

from ingestion_pipeline import (  # noqa: E402
    get_guardrail_supply_skus,
    get_latest_source_file,
    is_blank_or_na,
    normalize_sku,
    parse_forecast_source,
    read_14day_forecast,
    read_on_hand,
    read_product_attributes,
    read_weekly_forecast,
)

ING = ROOT / "Output" / "Ingestion"
OUT = ING / "FwdDemandCSV_2026-05-12.csv"
MISSING = ING / "MissingProductAttributes_2026-05-12.csv"
DETAIL = ROOT / "tmp" / "forecast_coverage_audit_missing.csv"


def source_frame(name: str, skus: set[str]) -> pd.DataFrame:
    return pd.DataFrame({"SKU": sorted(skus), name: True})


def main() -> None:
    source_file = get_latest_source_file()
    weekly, _, _ = read_weekly_forecast(source_file)
    day14, _ = read_14day_forecast(source_file)
    hier, _ = read_product_attributes(source_file)
    _, on_hand = read_on_hand(source_file)
    guardrail_skus, guardrail_source = get_guardrail_supply_skus(source_file)

    weekly_skus = {normalize_sku(sku) for sku in weekly["SKU"].dropna()}
    day14_skus = {normalize_sku(sku) for sku in day14["SKU"].dropna()}
    on_hand_skus = {normalize_sku(sku) for sku in on_hand["SKU"].dropna()}
    guardrail_skus = {normalize_sku(sku) for sku in guardrail_skus}
    for sku_set in [weekly_skus, day14_skus, on_hand_skus, guardrail_skus]:
        sku_set.discard("")

    universe = weekly_skus | day14_skus | on_hand_skus | guardrail_skus
    out = pd.read_csv(OUT, dtype=str, keep_default_na=False, low_memory=False)
    out_skus = set(out["SKU"])
    missing = (
        pd.read_csv(MISSING, dtype=str, keep_default_na=False)
        if MISSING.exists()
        else pd.DataFrame(columns=["SKU", "MissingHierarchyFields"])
    )
    missing_skus = set(missing["SKU"])

    frames = [
        source_frame("InWeeklyForecast", weekly_skus),
        source_frame("In14DayForecast", day14_skus),
        source_frame("InOnHand", on_hand_skus),
        source_frame("InGuardrailSupply", guardrail_skus),
    ]
    detail = pd.DataFrame({"SKU": sorted(universe)})
    for frame in frames:
        detail = detail.merge(frame, on="SKU", how="left")
    for col in ["InWeeklyForecast", "In14DayForecast", "InOnHand", "InGuardrailSupply"]:
        detail[col] = detail[col].fillna(False)

    hier_detail = hier[["SKU", "Division", "Department", "Class", "KeyCategoryView"]].copy()
    detail = detail.merge(hier_detail, on="SKU", how="left")
    detail["InOutput"] = detail["SKU"].isin(out_skus)
    detail["InMissingProductAttributes"] = detail["SKU"].isin(missing_skus)
    detail["BlankColor"] = detail["SKU"].map(lambda sku: normalize_sku(sku).endswith("--"))
    detail["SourceFoundIn"] = detail.apply(
        lambda row: parse_forecast_source(
            row["InWeeklyForecast"],
            row["In14DayForecast"],
            row["InOnHand"] or row["InGuardrailSupply"],
        ),
        axis=1,
    )
    detail["MissingReason"] = ""
    detail.loc[detail["InMissingProductAttributes"], "MissingReason"] = "Exception file"
    no_hierarchy = detail["Division"].apply(is_blank_or_na)
    detail.loc[no_hierarchy & detail["MissingReason"].eq(""), "MissingReason"] = "No Product Attributes hierarchy"

    not_covered = detail[~detail["InOutput"]].copy()
    not_covered.to_csv(DETAIL, index=False)

    print(f"Source workbook                         : {source_file.name}")
    print(f"Guardrail source                        : {guardrail_source}")
    print(f"Weekly canonical SKUs                   : {len(weekly_skus):,}")
    print(f"14-day canonical SKUs                   : {len(day14_skus):,}")
    print(f"On-hand relevant canonical SKUs         : {len(on_hand_skus):,}")
    print(f"Guardrail supply canonical SKUs         : {len(guardrail_skus):,}")
    print(f"Union canonical SKU universe            : {len(universe):,}")
    print(f"Current AX output SKUs                  : {len(out_skus):,}")
    print(f"MissingProductAttributes SKUs           : {len(missing_skus):,}")
    print(f"Universe SKUs not in AX output          : {len(not_covered):,}")
    print()
    if not not_covered.empty:
        print("Not-covered rows by source:")
        print(not_covered["SourceFoundIn"].value_counts(dropna=False).to_string())
        print()
        print("Not-covered rows:")
        cols = [
            "SKU",
            "SourceFoundIn",
            "InMissingProductAttributes",
            "MissingReason",
            "Division",
            "Department",
            "Class",
        ]
        print(not_covered[cols].to_string(index=False))
    print()
    print(f"Detail written to                       : {DETAIL}")


if __name__ == "__main__":
    main()
