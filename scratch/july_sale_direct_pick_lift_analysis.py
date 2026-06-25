"""DirectPick-based July sale lift investigation.

This scratch analysis avoids Planner totals and uses warehouse DirectPick actuals
from local Parquet artifacts:

- 2025 analog sale and baseline:
    Output/ForecastAccuracy/direct_pick_history/parquet/direct_pick_sku_day_modified_2025.parquet
- 2026 current pre-sale baseline:
  Output/ForecastAccuracy/history/parquet/actual_sku_day_modified.parquet

It compares a 2025 category lift projection against the frozen 2026 ML shadow.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORECAST_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
OUT_DIR = PROJECT_ROOT / "scratch" / "july_sale_direct_pick_lift_outputs"

CATEGORY_COLUMNS = ["Division", "Department", "Class", "KeyCategoryView"]
ANALOG_SALE_2025 = (pd.Timestamp("2025-06-21"), pd.Timestamp("2025-07-04"))
ANALOG_BASE_2025 = (pd.Timestamp("2025-05-24"), pd.Timestamp("2025-06-20"))
CURRENT_BASE_2026 = (pd.Timestamp("2026-05-21"), pd.Timestamp("2026-06-17"))
CURRENT_SHADOW = (pd.Timestamp("2026-06-18"), pd.Timestamp("2026-07-01"))
HYBRID_CANDIDATE = "hybrid_ml_raw_min20_recent_w0p1_cap_recent_x0p85"


def date_mask(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    values = pd.to_datetime(series)
    return values.between(start, end)


def safe_ratio(num: pd.Series | float, den: pd.Series | float) -> pd.Series | float:
    return np.where(np.asarray(den, dtype="float64") > 0, num / den, np.nan)


def markdown_table(df: pd.DataFrame) -> str:
    display = df.copy()
    for col in display.columns:
        if pd.api.types.is_float_dtype(display[col]):
            display[col] = display[col].map(lambda value: "" if pd.isna(value) else f"{value:.2f}")
    display = display.astype(str)
    header = "| " + " | ".join(display.columns) + " |"
    sep = "| " + " | ".join(["---"] * len(display.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in display.to_numpy()]
    return "\n".join([header, sep, *rows])


def load_category_map() -> pd.DataFrame:
    cols = ["SKU", "Date", *CATEGORY_COLUMNS, "ProductGroupCode", "SizeGroupCode"]
    panel = pd.read_parquet(FORECAST_ROOT / "model" / "model_sku_day_panel.parquet", columns=cols)
    panel["Date"] = pd.to_datetime(panel["Date"])
    category_map = panel.sort_values(["SKU", "Date"]).drop_duplicates("SKU", keep="last")
    category_map = category_map.drop(columns=["Date"])
    for col in CATEGORY_COLUMNS:
        category_map[col] = category_map[col].fillna("Unknown")
    return category_map


def load_2025_direct_pick() -> pd.DataFrame:
    df = pd.read_parquet(
        FORECAST_ROOT / "direct_pick_history" / "parquet" / "direct_pick_sku_day_modified_2025.parquet"
    )
    df["Date"] = pd.to_datetime(df["PickDate"]).dt.normalize()
    df["Units"] = pd.to_numeric(df["PickUnits"], errors="coerce").fillna(0)
    return df[["Date", "SKU", "Units", "PickLines", "DistinctOrders"]]


def load_current_direct_pick() -> pd.DataFrame:
    df = pd.read_parquet(
        FORECAST_ROOT / "history" / "parquet" / "actual_sku_day_modified.parquet",
        columns=["ActualDate", "SKU", "SoldUnits", "PickLines", "DistinctOrders"],
    )
    df["Date"] = pd.to_datetime(df["ActualDate"]).dt.normalize()
    df["Units"] = pd.to_numeric(df["SoldUnits"], errors="coerce").fillna(0)
    return df[["Date", "SKU", "Units", "PickLines", "DistinctOrders"]]


def attach_categories(df: pd.DataFrame, category_map: pd.DataFrame) -> pd.DataFrame:
    out = df.merge(category_map[["SKU", *CATEGORY_COLUMNS]], on="SKU", how="left")
    for col in CATEGORY_COLUMNS:
        out[col] = out[col].fillna("Unknown")
    return out


def total_lift_summary(direct_2025: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, df, sale_window, base_window in [
        ("2025 analog DirectPick", direct_2025, ANALOG_SALE_2025, ANALOG_BASE_2025),
        ("2026 current pre-sale DirectPick", current, CURRENT_SHADOW, CURRENT_BASE_2026),
    ]:
        base = df[date_mask(df["Date"], *base_window)]
        base_daily = float(base["Units"].sum() / max(base["Date"].nunique(), 1))
        if label.startswith("2025"):
            sale = df[date_mask(df["Date"], *sale_window)]
            sale_units = float(sale["Units"].sum())
            sale_days = sale["Date"].nunique()
        else:
            sale_units = np.nan
            sale_days = (CURRENT_SHADOW[1] - CURRENT_SHADOW[0]).days + 1
        expected = base_daily * sale_days
        rows.append(
            {
                "Source": label,
                "SaleStart": sale_window[0].date(),
                "SaleEnd": sale_window[1].date(),
                "SaleDays": sale_days,
                "SaleUnits": sale_units,
                "BaselineStart": base_window[0].date(),
                "BaselineEnd": base_window[1].date(),
                "BaselineObservedDays": base["Date"].nunique(),
                "BaselineDailyUnits": base_daily,
                "BaselineExpectedUnits": expected,
                "LiftVsBaseline": float(sale_units / expected)
                if pd.notna(sale_units) and expected
                else np.nan,
            }
        )
    return pd.DataFrame(rows)


def category_lift(direct_2025: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    sale = direct_2025[date_mask(direct_2025["Date"], *ANALOG_SALE_2025)]
    base = direct_2025[date_mask(direct_2025["Date"], *ANALOG_BASE_2025)]
    sale_cat = sale.groupby(CATEGORY_COLUMNS, dropna=False)["Units"].sum().rename("Sale2025Units")
    base_cat = base.groupby(CATEGORY_COLUMNS, dropna=False)["Units"].sum().rename("Baseline2025Units")
    out = pd.concat([sale_cat, base_cat], axis=1).fillna(0).reset_index()
    sale_days = sale["Date"].nunique()
    base_days = base["Date"].nunique()
    out["Baseline2025DailyUnits"] = out["Baseline2025Units"] / max(base_days, 1)
    out["BaselineExpected2025Units"] = out["Baseline2025DailyUnits"] * sale_days
    out["CategoryLift2025"] = safe_ratio(out["Sale2025Units"], out["BaselineExpected2025Units"])

    current_base = current[date_mask(current["Date"], *CURRENT_BASE_2026)]
    current_cat = (
        current_base.groupby(CATEGORY_COLUMNS, dropna=False)["Units"]
        .sum()
        .rename("CurrentBaselineUnits")
        .reset_index()
    )
    current_days = current_base["Date"].nunique()
    current_cat["CurrentBaselineObservedDays"] = current_days
    current_cat["CurrentBaselineDailyUnits"] = current_cat["CurrentBaselineUnits"] / max(current_days, 1)
    out = out.merge(current_cat, on=CATEGORY_COLUMNS, how="outer").fillna(0)
    out["DirectPickLiftProjection"] = (
        out["CurrentBaselineDailyUnits"]
        * ((CURRENT_SHADOW[1] - CURRENT_SHADOW[0]).days + 1)
        * out["CategoryLift2025"].replace(0, np.nan)
    ).fillna(0)
    return out


def current_pdl_by_category(category_map: pd.DataFrame) -> pd.DataFrame:
    pdl = pd.read_parquet(
        FORECAST_ROOT / "promotions" / "pdl_sku_day_features.parquet",
        columns=["Date", "SKU", "pdl_sku_max_discount_pct", "pdl_sku_total_avail_inv"],
    )
    pdl["Date"] = pd.to_datetime(pdl["Date"]).dt.normalize()
    pdl = pdl[date_mask(pdl["Date"], *CURRENT_SHADOW)]
    pdl = attach_categories(pdl, category_map)
    return (
        pdl.groupby(CATEGORY_COLUMNS, dropna=False)
        .agg(
            CurrentPromoSkuDays=("SKU", "count"),
            CurrentPromoSKUs=("SKU", "nunique"),
            CurrentMaxDiscount=("pdl_sku_max_discount_pct", "max"),
            CurrentAvgDiscount=("pdl_sku_max_discount_pct", "mean"),
            CurrentPDLAvailInv=("pdl_sku_total_avail_inv", "max"),
        )
        .reset_index()
    )


def shadow_by_category(category_map: pd.DataFrame) -> pd.DataFrame:
    shadow_path = (
        FORECAST_ROOT
        / "replacement_shadow_pdl_sku_refreshed"
        / "shadow_2026-06-18_2026-07-01"
        / "shadow_daily_forecasts.parquet"
    )
    shadow = pd.read_parquet(shadow_path)
    shadow = attach_categories(shadow, category_map)
    grouped = (
        shadow.groupby(["Candidate", *CATEGORY_COLUMNS], dropna=False)["ForecastUnits"]
        .sum()
        .reset_index()
    )
    pivot = grouped.pivot_table(
        index=CATEGORY_COLUMNS,
        columns="Candidate",
        values="ForecastUnits",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    pivot.columns.name = None
    return pivot


def write_summary(
    total_summary: pd.DataFrame,
    compare: pd.DataFrame,
    metadata: dict,
) -> None:
    top = compare.sort_values("GapVsHybrid10Cap085", ascending=False).head(20)
    total_projection = compare.loc[compare["CurrentPromoSKUs"].gt(0), "DirectPickLiftProjection"].sum()
    total_hybrid = compare.loc[compare["CurrentPromoSKUs"].gt(0), HYBRID_CANDIDATE].sum()
    total_recent = compare.loc[compare["CurrentPromoSKUs"].gt(0), "recent_no_ml_no_promo_floor"].sum()
    lines = [
        "# July Sale DirectPick YoY Lift Scratch Findings",
        "",
        "## Total DirectPick Lift",
        markdown_table(total_summary),
        "",
        "## Current Shadow Context",
        f"- Window: {metadata.get('forecast_start')} through {metadata.get('forecast_end')}",
        f"- Promo-horizon SKUs: {metadata.get('future_inputs', {}).get('promo_horizon_skus'):,}",
        f"- DirectPick lift projection across current promoted categories: {total_projection:,.0f}",
        f"- Current hybrid 10% / 0.85x across current promoted categories: {total_hybrid:,.0f}",
        f"- Recent no-ML floor across current promoted categories: {total_recent:,.0f}",
        "",
        "## Largest Category Gaps vs Hybrid",
        markdown_table(
            top[
                [
                    *CATEGORY_COLUMNS,
                    "Sale2025Units",
                    "CategoryLift2025",
                    "CurrentPromoSKUs",
                    "DirectPickLiftProjection",
                    HYBRID_CANDIDATE,
                    "GapVsHybrid10Cap085",
                ]
            ]
        ),
        "",
        "## Read",
        "- This rerun uses WHSWorkLine-derived DirectPick actuals, not Planner.",
        "- The 2025 July sale lifted DirectPick units materially versus its pre-sale baseline.",
        "- Category-level prior-sale lift still explains a large share of the current ML under-forecast.",
    ]
    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    category_map = load_category_map()
    direct_2025 = attach_categories(load_2025_direct_pick(), category_map)
    current = attach_categories(load_current_direct_pick(), category_map)

    total_summary = total_lift_summary(direct_2025, current)
    total_summary.to_csv(OUT_DIR / "direct_pick_total_lift_summary.csv", index=False)

    compare = category_lift(direct_2025, current)
    compare = compare.merge(current_pdl_by_category(category_map), on=CATEGORY_COLUMNS, how="outer")
    compare = compare.merge(shadow_by_category(category_map), on=CATEGORY_COLUMNS, how="outer")
    numeric_cols = compare.select_dtypes(include=["number"]).columns
    compare[numeric_cols] = compare[numeric_cols].fillna(0)
    if HYBRID_CANDIDATE in compare.columns:
        compare["GapVsHybrid10Cap085"] = compare["DirectPickLiftProjection"] - compare[HYBRID_CANDIDATE]
        compare["ProjectionToHybridRatio"] = safe_ratio(
            compare["DirectPickLiftProjection"], compare[HYBRID_CANDIDATE]
        )
    compare = compare.sort_values("GapVsHybrid10Cap085", ascending=False)
    compare.to_csv(OUT_DIR / "category_direct_pick_lift_vs_current_shadow.csv", index=False)

    metadata_path = (
        FORECAST_ROOT
        / "replacement_shadow_pdl_sku_refreshed"
        / "shadow_2026-06-18_2026-07-01"
        / "shadow_metadata.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    write_summary(total_summary, compare, metadata)
    print(f"Wrote DirectPick July-sale lift outputs to {OUT_DIR}")


if __name__ == "__main__":
    main()
