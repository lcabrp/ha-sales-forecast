"""Investigate prior July-sale lift as an anchor for the 2026 forward shadow.

This is a scratch-only analysis. It reads existing portable forecast artifacts
and writes compact CSV/Markdown outputs under scratch/.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORECAST_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
OUT_DIR = PROJECT_ROOT / "scratch" / "july_sale_yoy_lift_outputs"

CURRENT_WINDOW = (pd.Timestamp("2026-06-18"), pd.Timestamp("2026-07-01"))
CURRENT_BASELINE = (pd.Timestamp("2026-05-21"), pd.Timestamp("2026-06-17"))
ANALOG_WINDOWS = {
    2024: (pd.Timestamp("2024-06-18"), pd.Timestamp("2024-07-06")),
    2025: (pd.Timestamp("2025-06-21"), pd.Timestamp("2025-07-04")),
}
ANALOG_BASELINES = {
    2024: (pd.Timestamp("2024-05-21"), pd.Timestamp("2024-06-17")),
    2025: (pd.Timestamp("2025-05-24"), pd.Timestamp("2025-06-20")),
}
CATEGORY_COLUMNS = ["Division", "Department", "Class", "KeyCategoryView"]


def date_mask(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    values = pd.to_datetime(series)
    return values.between(start, end)


def safe_divide(numerator: pd.Series | float, denominator: pd.Series | float) -> pd.Series | float:
    return np.where(np.asarray(denominator, dtype="float64") > 0, numerator / denominator, np.nan)


def load_category_map() -> pd.DataFrame:
    """Use the model panel as a broad SKU -> hierarchy bridge."""
    cols = ["SKU", "Date", *CATEGORY_COLUMNS, "ProductGroupCode", "SizeGroupCode"]
    panel = pd.read_parquet(FORECAST_ROOT / "model" / "model_sku_day_panel.parquet", columns=cols)
    panel["Date"] = pd.to_datetime(panel["Date"])
    panel = panel.sort_values(["SKU", "Date"])
    category_map = panel.drop_duplicates("SKU", keep="last").drop(columns=["Date"])
    for col in CATEGORY_COLUMNS:
        category_map[col] = category_map[col].fillna("Unknown")
    return category_map


def load_planner_daily() -> pd.DataFrame:
    frames = []
    for year in (2024, 2025, 2026):
        path = FORECAST_ROOT / "planner" / f"planner_daily_totals_{year}.parquet"
        df = pd.read_parquet(path)
        df["Year"] = year
        df["Date"] = pd.to_datetime(df["Date"])
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def planner_lift(planner: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    curves = []
    for year, (sale_start, sale_end) in ANALOG_WINDOWS.items():
        base_start, base_end = ANALOG_BASELINES[year]
        year_df = planner[planner["Year"].eq(year)].copy()
        metric = "ops_imf_plan_forecasted_units"
        sale = year_df[date_mask(year_df["Date"], sale_start, sale_end)].copy()
        base = year_df[date_mask(year_df["Date"], base_start, base_end)].copy()
        sale_units = float(sale[metric].fillna(0).sum())
        base_daily = float(base[metric].fillna(0).mean())
        if year == 2025 and sale_units == 0 and (not base_daily or np.isnan(base_daily)):
            metric = "actual_demand_units"
            sale_units = float(sale[metric].fillna(0).sum())
            base_daily = float(base[metric].fillna(0).mean())
        expected = base_daily * len(sale)
        rows.append(
            {
                "Year": year,
                "Metric": metric,
                "SaleStart": sale_start.date(),
                "SaleEnd": sale_end.date(),
                "SaleDays": len(sale),
                "SaleUnits": sale_units,
                "BaselineStart": base_start.date(),
                "BaselineEnd": base_end.date(),
                "BaselineDailyUnits": base_daily,
                "BaselineExpectedUnits": expected,
                "LiftVsBaseline": float(sale_units / expected) if expected else np.nan,
            }
        )
        sale["RelativeDay"] = (sale["Date"] - sale_start).dt.days
        sale["Units"] = sale[metric]
        curves.append(sale[["Year", "Date", "RelativeDay", "Units"]])

    current = planner[planner["Year"].eq(2026)].copy()
    current_sale = current[date_mask(current["Date"], *CURRENT_WINDOW)]
    current_base = current[date_mask(current["Date"], *CURRENT_BASELINE)]
    current_units = float(current_sale["ops_imf_plan_forecasted_units"].fillna(0).sum())
    current_base_daily = float(current_base["ops_imf_plan_forecasted_units"].fillna(0).mean())
    current_expected = current_base_daily * len(current_sale)
    rows.append(
        {
            "Year": 2026,
            "Metric": "ops_imf_plan_forecasted_units",
            "SaleStart": CURRENT_WINDOW[0].date(),
            "SaleEnd": CURRENT_WINDOW[1].date(),
            "SaleDays": len(current_sale),
            "SaleUnits": current_units,
            "BaselineStart": CURRENT_BASELINE[0].date(),
            "BaselineEnd": CURRENT_BASELINE[1].date(),
            "BaselineDailyUnits": current_base_daily,
            "BaselineExpectedUnits": current_expected,
            "LiftVsBaseline": float(current_units / current_expected) if current_expected else np.nan,
        }
    )
    current_sale = current_sale.copy()
    current_sale["RelativeDay"] = (current_sale["Date"] - CURRENT_WINDOW[0]).dt.days
    current_sale["Units"] = current_sale["ops_imf_plan_forecasted_units"]
    curves.append(current_sale[["Year", "Date", "RelativeDay", "Units"]])
    return pd.DataFrame(rows), pd.concat(curves, ignore_index=True)


def category_lift_2025(category_map: pd.DataFrame) -> pd.DataFrame:
    sales = pd.read_parquet(
        FORECAST_ROOT / "sales_orders" / "sales_order_sku_day.parquet",
        columns=["OrderDateUTC", "SKU", "OrderedUnits", "AbsOrderedUnits"],
    )
    sales["Date"] = pd.to_datetime(sales["OrderDateUTC"]).dt.normalize()
    sales["Units"] = sales["OrderedUnits"].clip(lower=0)
    sales = sales.merge(category_map[["SKU", *CATEGORY_COLUMNS]], on="SKU", how="left")
    for col in CATEGORY_COLUMNS:
        sales[col] = sales[col].fillna("Unknown")

    sale_start, sale_end = ANALOG_WINDOWS[2025]
    base_start, base_end = ANALOG_BASELINES[2025]
    sale = sales[date_mask(sales["Date"], sale_start, sale_end)]
    base = sales[date_mask(sales["Date"], base_start, base_end)]
    sale_cat = sale.groupby(CATEGORY_COLUMNS, dropna=False)["Units"].sum().rename("Sale2025Units")
    base_cat = base.groupby(CATEGORY_COLUMNS, dropna=False)["Units"].sum().rename("Baseline2025Units")
    result = pd.concat([sale_cat, base_cat], axis=1).fillna(0).reset_index()
    sale_days = (sale_end - sale_start).days + 1
    base_days = (base_end - base_start).days + 1
    result["Baseline2025DailyUnits"] = result["Baseline2025Units"] / base_days
    result["BaselineExpected2025Units"] = result["Baseline2025DailyUnits"] * sale_days
    result["CategoryLift2025"] = safe_divide(
        result["Sale2025Units"], result["BaselineExpected2025Units"]
    )
    result["Sale2025Share"] = safe_divide(result["Sale2025Units"], result["Sale2025Units"].sum())
    return result


def current_shadow_by_category(category_map: pd.DataFrame) -> pd.DataFrame:
    shadow_path = (
        FORECAST_ROOT
        / "replacement_shadow_pdl_sku_refreshed"
        / "shadow_2026-06-18_2026-07-01"
        / "shadow_daily_forecasts.parquet"
    )
    shadow = pd.read_parquet(shadow_path)
    shadow = shadow.merge(category_map[["SKU", *CATEGORY_COLUMNS]], on="SKU", how="left")
    for col in CATEGORY_COLUMNS:
        shadow[col] = shadow[col].fillna("Unknown")
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


def current_pdl_by_category(category_map: pd.DataFrame) -> pd.DataFrame:
    pdl = pd.read_parquet(
        FORECAST_ROOT / "promotions" / "pdl_sku_day_features.parquet",
        columns=["Date", "SKU", "pdl_sku_max_discount_pct", "pdl_sku_total_avail_inv"],
    )
    pdl["Date"] = pd.to_datetime(pdl["Date"]).dt.normalize()
    pdl = pdl[date_mask(pdl["Date"], *CURRENT_WINDOW)]
    pdl = pdl.merge(category_map[["SKU", *CATEGORY_COLUMNS]], on="SKU", how="left")
    for col in CATEGORY_COLUMNS:
        pdl[col] = pdl[col].fillna("Unknown")
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


def current_recent_sales_by_category(category_map: pd.DataFrame) -> pd.DataFrame:
    sales = pd.read_parquet(
        FORECAST_ROOT / "sales_orders" / "sales_order_sku_day.parquet",
        columns=["OrderDateUTC", "SKU", "OrderedUnits"],
    )
    sales["Date"] = pd.to_datetime(sales["OrderDateUTC"]).dt.normalize()
    sales = sales[date_mask(sales["Date"], CURRENT_BASELINE[0], pd.Timestamp("2026-06-08"))]
    sales["Units"] = sales["OrderedUnits"].clip(lower=0)
    sales = sales.merge(category_map[["SKU", *CATEGORY_COLUMNS]], on="SKU", how="left")
    for col in CATEGORY_COLUMNS:
        sales[col] = sales[col].fillna("Unknown")
    days = sales["Date"].nunique()
    grouped = sales.groupby(CATEGORY_COLUMNS, dropna=False)["Units"].sum().reset_index()
    grouped["CurrentBaselineObservedDays"] = days
    grouped["CurrentBaselineDailyUnits"] = grouped["Units"] / days if days else np.nan
    grouped["CurrentExpectedBy2025Lift"] = grouped["CurrentBaselineDailyUnits"] * 14
    grouped = grouped.drop(columns=["Units"])
    return grouped


def build_markdown(
    planner_summary: pd.DataFrame,
    category_compare: pd.DataFrame,
    metadata: dict,
) -> str:
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

    top = category_compare.sort_values("GapVsHybrid10Cap085", ascending=False).head(15)
    lines = [
        "# July Sale YoY Lift Scratch Findings",
        "",
        "## Planner / Total-Unit Lift",
        markdown_table(planner_summary),
        "",
        "## Shadow Metadata",
        f"- Forecast window: {metadata.get('forecast_start')} through {metadata.get('forecast_end')}",
        f"- Promo-horizon SKUs: {metadata.get('future_inputs', {}).get('promo_horizon_skus'):,}",
        f"- Future rows: {metadata.get('future_inputs', {}).get('future_rows'):,}",
        "",
        "## Largest Category Gaps vs Current Hybrid 10% / 0.85x Cap",
        markdown_table(
            top[
                [
                    *CATEGORY_COLUMNS,
                    "Sale2025Units",
                    "CategoryLift2025",
                    "CurrentPromoSKUs",
                    "CurrentExpectedBy2025Lift",
                    "hybrid_ml_raw_min20_recent_w0p1_cap_recent_x0p85",
                    "GapVsHybrid10Cap085",
                ]
            ]
        ),
        "",
        "## Read",
        "- Prior-year July sale behavior creates a materially higher category-level volume",
        "  expectation than the current ML shadow in several promoted categories.",
        "- Treat this as directional: 2025 category lift is based on retail sales-order",
        "  SKU/day rows, while the replacement model is scored against DC DirectPick demand.",
        "- The result supports adding a sale-event category lift / total-volume anchor",
        "  rather than relying on PDL presence as a binary feature.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    category_map = load_category_map()

    planner_summary, planner_curve = planner_lift(load_planner_daily())
    planner_summary.to_csv(OUT_DIR / "planner_total_lift_summary.csv", index=False)
    planner_curve.to_csv(OUT_DIR / "planner_event_daily_curve.csv", index=False)

    lift_2025 = category_lift_2025(category_map)
    shadow = current_shadow_by_category(category_map)
    pdl = current_pdl_by_category(category_map)
    current_base = current_recent_sales_by_category(category_map)

    compare = lift_2025.merge(current_base, on=CATEGORY_COLUMNS, how="outer")
    compare = compare.merge(shadow, on=CATEGORY_COLUMNS, how="outer")
    compare = compare.merge(pdl, on=CATEGORY_COLUMNS, how="outer")
    numeric_cols = compare.select_dtypes(include=["number"]).columns
    compare[numeric_cols] = compare[numeric_cols].fillna(0)
    compare["CurrentExpectedBy2025Lift"] = (
        compare["CurrentExpectedBy2025Lift"] * compare["CategoryLift2025"].replace(0, np.nan)
    ).fillna(0)

    hybrid_col = "hybrid_ml_raw_min20_recent_w0p1_cap_recent_x0p85"
    if hybrid_col in compare.columns:
        compare["GapVsHybrid10Cap085"] = compare["CurrentExpectedBy2025Lift"] - compare[hybrid_col]
        compare["LiftExpectedToHybridRatio"] = safe_divide(
            compare["CurrentExpectedBy2025Lift"], compare[hybrid_col]
        )
    else:
        compare["GapVsHybrid10Cap085"] = np.nan
        compare["LiftExpectedToHybridRatio"] = np.nan

    compare = compare.sort_values("GapVsHybrid10Cap085", ascending=False)
    compare.to_csv(OUT_DIR / "category_lift_vs_current_shadow.csv", index=False)

    metadata_path = (
        FORECAST_ROOT
        / "replacement_shadow_pdl_sku_refreshed"
        / "shadow_2026-06-18_2026-07-01"
        / "shadow_metadata.json"
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    (OUT_DIR / "summary.md").write_text(
        build_markdown(planner_summary, compare, metadata), encoding="utf-8"
    )
    print(f"Wrote July sale YoY lift scratch outputs to {OUT_DIR}")


if __name__ == "__main__":
    main()
