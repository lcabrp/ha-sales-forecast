"""Train the first SKU/day forecast model from the model panel.

The production-grade model family is expected to be gradient-boosted trees.  To
keep this repo portable on Windows, the first implementation uses
``sklearn.ensemble.HistGradientBoostingRegressor`` when scikit-learn is
installed.  The script deliberately avoids same-day sales-order demand/price
fields as training features; those are diagnostics in the panel, not information
we should assume is known before the forecast day.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from output_paths import PROJECT_ROOT  # noqa: E402


FORECAST_ACCURACY_DIR = PROJECT_ROOT / "Output" / "ForecastAccuracy"
MODEL_DIR = FORECAST_ACCURACY_DIR / "model"
DEFAULT_PANEL_PATH = MODEL_DIR / "model_sku_day_panel_parts"
DEFAULT_OUTPUT_DIR = MODEL_DIR / "ml"

TARGET_COLUMN = "SoldUnits"
DATE_COLUMN = "Date"

NUMERIC_FEATURES = [
    "CorporateForecastQty",
    "SoldUnitsLag1",
    "SoldUnitsLag7",
    "SoldUnitsLag14",
    "SoldUnitsRolling7",
    "SoldUnitsRolling28",
    "OrderedUnitsLag1",
    "OrderedUnitsLag7",
    "OrderedUnitsRolling7",
    "OrderedUnitsRolling28",
    "CorporateForecastQtyLag1",
    "CorporateForecastQtyRolling7",
    "pdl_active_events",
    "pdl_offer_cc_count",
    "pdl_style_count",
    "coupon_active_rows",
    "coupon_max_discount_percent",
    "pdl_sku_offer_rows",
    "pdl_sku_active_events",
    "pdl_sku_max_discount_pct",
    "pdl_sku_avg_discount_pct",
    "pdl_sku_min_promo_price",
    "pdl_sku_total_avail_inv",
    "pdl_sku_total_avail_plus_oo",
    "pdl_sku_lw_unit_sales",
    "InventoryAvailPhysicalLag1",
    "InventoryOrderedInTotalLag1",
    "InventoryPhysicalReservedLag1",
    "InventoryNetAvailablePhysicalLag1",
    "InventoryAvgUnitPriceLag1",
    "InventoryAvgLandedCostLag1",
    "InboundSnapshotAgeDays",
    "InboundPastDueUnits",
    "InboundNext7Units",
    "InboundNext8To14Units",
    "InboundNext15To30Units",
    "InboundNext31To60Units",
    "InboundNext61To90Units",
    "InboundLaterUnits",
    "InboundOpenPOLines",
    "InboundDistinctPOs",
    "SupplyWorkUnitsLag1",
    "ReplenishmentUnitsLag1",
    "ReceivingPutawayUnitsLag1",
    "ReturnSellableFloorUnitsLag1",
    "ReturnNonSellableUnitsLag1",
    "TransferUnitsLag1",
    "ReplenishmentToFloorUnitsLag1",
    "ReserveOrBulkSupplyUnitsLag1",
    "StagingMovementUnitsLag1",
    "SellableFloorSupplyUnitsLag1",
    "NonSellableSupplyUnitsLag1",
    "ItemColorSoldUnitsLag1",
    "ItemColorSoldUnitsRolling7",
    "ItemColorSoldUnitsRolling28",
    "ItemColorOrderedUnitsLag1",
    "ItemColorOrderedUnitsRolling7",
    "ItemColorOrderedUnitsRolling28",
    "CategorySizeSoldUnitsLag1",
    "CategorySizeSoldUnitsRolling7",
    "CategorySizeSoldUnitsRolling28",
    "CategorySizeOrderedUnitsLag1",
    "CategorySizeOrderedUnitsRolling7",
    "CategorySizeOrderedUnitsRolling28",
    "SeasonalSkuSoldUnitsAvg",
    "SeasonalItemColorSoldUnitsAvg",
    "SeasonalProductGroupSoldUnitsAvg",
    "SeasonalCategorySizeSoldUnitsAvg",
    "DayOfWeek",
    "WeekOfYear",
    "Month",
]

CATEGORICAL_FEATURES = [
    "Division",
    "Department",
    "Class",
    "KeyCategoryView",
    "ProductGroupCode",
    "SizeGroupCode",
    "Velocity",
    "SlotTier",
    "pdl_sku_primary_sheet_type",
    "pdl_sku_primary_scope",
]
PRODUCT_IDENTITY_FEATURES = [
    "Item",
    "Color",
    "Size",
]

BOOLEAN_FEATURES = [
    "IsWeekend",
    "HasCorporateForecast",
    "HasPDLPromotion",
    "HasCouponPromotion",
    "HasAnyPromotion",
    "HasSkuPDLPromotion",
    "pdl_sku_has_markdown",
    "pdl_sku_has_final_sale",
    "pdl_sku_has_tier1_recommendation",
    "HasAvailableInventoryLag1",
    "HasNetAvailableInventoryLag1",
    "HasOrderedInventoryLag1",
    "HasInboundPastDue",
    "HasInboundNext14",
    "HasInboundNext30",
    "HasInboundNext90",
]

BASELINE_COLUMNS = [
    "CorporateBaselineQty",
    "Recent28BaselineQty",
    "Recent7BaselineQty",
    "HybridBaselineQty",
]
CORPORATE_FEATURE_COLUMNS = {
    "CorporateForecastQty",
    "CorporateForecastQtyLag1",
    "CorporateForecastQtyRolling7",
    "HasCorporateForecast",
}


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the model training and evaluation script.

    Returns:
        argparse.Namespace: The parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Train and backtest the first forecast ML model.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument(
        "--holdout-start",
        help="Inclusive holdout start date. Defaults to trailing --holdout-days from panel max date.",
    )
    parser.add_argument("--holdout-end", help="Inclusive holdout end date. Defaults to panel max date.")
    parser.add_argument("--holdout-days", type=int, default=28)
    parser.add_argument(
        "--max-train-rows",
        type=int,
        default=1_000_000,
        help="Deterministic training sample cap. Set 0 to use all rows.",
    )
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=250)
    parser.add_argument("--learning-rate", type=float, default=0.06)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument(
        "--calibration-days",
        type=int,
        default=28,
        help=(
            "Days immediately before the holdout used to estimate a multiplicative "
            "post-model calibration factor. Set 0 to disable calibration."
        ),
    )
    parser.add_argument(
        "--calibration-mode",
        choices=[
            "global",
            "sku-promo",
            "category",
            "category-velocity",
            "category-velocity-promo",
            "none",
        ],
        default="global",
        help="Calibration grouping used for multiplicative post-model factors.",
    )
    parser.add_argument(
        "--calibration-min-rows",
        type=int,
        default=500,
        help="Minimum calibration rows required before trusting a segment factor.",
    )
    parser.add_argument(
        "--calibration-min-actual-units",
        type=float,
        default=50.0,
        help="Minimum calibration actual units required before trusting a segment factor.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Thread cap for sklearn/joblib/OpenMP. Keep 1 inside Codex sandbox.",
    )
    parser.add_argument(
        "--exclude-corporate-features",
        action="store_true",
        help=(
            "Keep corporate forecast columns for baseline comparison, but exclude "
            "them from model training features."
        ),
    )
    parser.add_argument(
        "--include-product-identity-features",
        action="store_true",
        help="Add Item, Color, Size, and ItemColor categorical features for experimental family-aware models.",
    )
    parser.add_argument("--check-deps", action="store_true")
    return parser.parse_args()


def configure_threads(thread_count: int) -> None:
    """Set thread caps for CPU parallel computing libraries to limit resource utilization.

    Args:
        thread_count: Max number of threads.
    """
    value = str(max(1, int(thread_count)))
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", value)
    os.environ.setdefault("OMP_NUM_THREADS", value)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", value)
    os.environ.setdefault("MKL_NUM_THREADS", value)
    os.environ.setdefault("NUMEXPR_NUM_THREADS", value)


def require_sklearn() -> dict[str, Any]:
    """Import and return the required scikit-learn components, raising helpful error if missing.

    Returns:
        dict[str, Any]: Dictionary of imported library objects.

    Raises:
        RuntimeError: If scikit-learn or joblib are not installed.
    """
    try:
        from joblib import dump
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import HistGradientBoostingRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OrdinalEncoder
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "scikit-learn is required for the ML runner. Install with: uv add scikit-learn"
        ) from exc
    return {
        "ColumnTransformer": ColumnTransformer,
        "HistGradientBoostingRegressor": HistGradientBoostingRegressor,
        "OrdinalEncoder": OrdinalEncoder,
        "Pipeline": Pipeline,
        "SimpleImputer": SimpleImputer,
        "dump": dump,
    }


def normalize_date(series: pd.Series) -> pd.Series:
    """Normalize a series to pandas datetime index.

    Args:
        series: Date series to normalize.

    Returns:
        pd.Series: Cleaned datetime series.
    """
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def available_columns(path: Path) -> list[str]:
    """Inspect schema of a single Parquet file or a folder of Parquet parts.

    Args:
        path: Path to the Parquet dataset or directory.

    Returns:
        list[str]: Column names present in the dataset schema.

    Raises:
        FileNotFoundError: If the dataset path is missing.
    """
    if not path.exists():
        raise FileNotFoundError(f"Model panel not found: {path}")
    if path.is_dir():
        parquet_files = sorted(path.glob("*.parquet"))
        if not parquet_files:
            raise FileNotFoundError(f"No Parquet parts found in model panel directory: {path}")
        return list(pq.read_schema(parquet_files[0]).names)
    return list(pq.read_schema(path).names)


def load_panel(path: Path, start_date: str) -> pd.DataFrame:
    """Load model panel files, filtering by start date.

    Args:
        path: Parquet directory or file path.
        start_date: ISO format date boundary (inclusive).

    Returns:
        pd.DataFrame: Loaded dataset containing select features.
    """
    columns = set([DATE_COLUMN, "SKU", TARGET_COLUMN, *BASELINE_COLUMNS])
    columns.update(NUMERIC_FEATURES)
    columns.update(CATEGORICAL_FEATURES)
    columns.update(PRODUCT_IDENTITY_FEATURES)
    columns.update(BOOLEAN_FEATURES)

    schema_columns = available_columns(path)
    # Filter columns to read only what actually exists in the Parquet schema
    read_columns = [col for col in columns if col in schema_columns]
    if path.is_dir():
        parquet_files = sorted(path.glob("*.parquet"))
        panel = pd.concat([pd.read_parquet(f, columns=read_columns) for f in parquet_files], ignore_index=True)
    else:
        panel = pd.read_parquet(path, columns=read_columns)
    panel[DATE_COLUMN] = normalize_date(panel[DATE_COLUMN])
    return panel.loc[panel[DATE_COLUMN].ge(pd.Timestamp(date.fromisoformat(start_date)))].copy()


def resolve_holdout(df: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Decide holdout time boundaries based on options and data range.

    Args:
        df: Input dataframe.
        args: Pipeline options.

    Returns:
        tuple[pd.Timestamp, pd.Timestamp]: Start and end holdout dates.

    Raises:
        ValueError: If holdout start date is after the end date.
    """
    panel_end = df[DATE_COLUMN].max()
    end = pd.Timestamp(date.fromisoformat(args.holdout_end)) if args.holdout_end else panel_end
    start = (
        pd.Timestamp(date.fromisoformat(args.holdout_start))
        if args.holdout_start
        else end - pd.Timedelta(days=args.holdout_days - 1)
    )
    if start > end:
        raise ValueError("Holdout start is after holdout end.")
    return start, end


def _exclude_corporate(columns: list[str], exclude_corporate_features: bool) -> list[str]:
    """Helper to remove corporate columns from a feature list if requested."""
    if not exclude_corporate_features:
        return columns
    return [col for col in columns if col not in CORPORATE_FEATURE_COLUMNS]


def prepare_xy(
    df: pd.DataFrame,
    exclude_corporate_features: bool = False,
    include_product_identity_features: bool = False,
) -> tuple[pd.DataFrame, np.ndarray, list[str], list[str], list[str]]:
    """Slice and clean panel features and log-transform the target values.

    Applies log1p to the target SoldUnits to stabilize regression variance.

    Args:
        df: Input dataset.
        exclude_corporate_features: If True, excludes corporate forecast features.
        include_product_identity_features: If True, adds product identities to categorical features.

    Returns:
        tuple[pd.DataFrame, np.ndarray, list[str], list[str], list[str]]:
            Features dataframe, log-transformed target array, numeric feature columns,
            categorical feature columns, and boolean feature columns.
    """
    numeric = _exclude_corporate(
        [col for col in NUMERIC_FEATURES if col in df.columns],
        exclude_corporate_features,
    )
    work = df
    categorical = [col for col in CATEGORICAL_FEATURES if col in df.columns]
    if include_product_identity_features:
        identity = [col for col in PRODUCT_IDENTITY_FEATURES if col in df.columns]
        categorical.extend([col for col in identity if col not in categorical])
        if {"Item", "Color"}.issubset(df.columns):
            work = df.copy()
            work["ItemColor"] = (
                work["Item"].fillna("").astype(str).str.strip()
                + "-"
                + work["Color"].fillna("").astype(str).str.strip()
            )
            categorical.append("ItemColor")
    boolean = _exclude_corporate(
        [col for col in BOOLEAN_FEATURES if col in df.columns],
        exclude_corporate_features,
    )

    features = work.loc[:, [*numeric, *categorical, *boolean]].copy()
    for col in categorical:
        features[col] = features[col].fillna("").astype(str)
    for col in boolean:
        features[col] = features[col].fillna(False).astype("int8")
    for col in numeric:
        features[col] = pd.to_numeric(features[col], errors="coerce")

    # Apply log1p scaling to stabilize demand target variance
    target = np.log1p(pd.to_numeric(df[TARGET_COLUMN], errors="coerce").fillna(0).clip(lower=0))
    return features, target.to_numpy(), numeric, categorical, boolean


def sample_training_rows(df: pd.DataFrame, max_rows: int, random_state: int) -> pd.DataFrame:
    """Downsample training rows if they exceed the row threshold limit.

    Args:
        df: Training dataframe.
        max_rows: Max rows cap.
        random_state: Seed.

    Returns:
        pd.DataFrame: Sampled training rows.
    """
    if max_rows <= 0 or len(df) <= max_rows:
        return df
    return df.sample(n=max_rows, random_state=random_state)


def build_model(
    ml: dict[str, Any],
    numeric: list[str],
    categorical: list[str],
    boolean: list[str],
    args: argparse.Namespace,
) -> Any:
    """Construct a scikit-learn Pipeline with ColumnTransformer preprocessor and HGB regressor.

    Args:
        ml: Dictionary of scikit-learn components.
        numeric: List of numeric column names.
        categorical: List of categorical column names.
        boolean: List of boolean column names.
        args: Pipeline configuration options.

    Returns:
        Pipeline: Unfit scikit-learn Pipeline.
    """
    transformers = []
    if numeric:
        transformers.append(
            (
                "num",
                ml["Pipeline"](
                    [
                        ("imputer", ml["SimpleImputer"](strategy="constant", fill_value=0.0)),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "cat",
                ml["OrdinalEncoder"](
                    handle_unknown="use_encoded_value",
                    unknown_value=-1,
                    encoded_missing_value=-1,
                ),
                categorical,
            )
        )
    if boolean:
        transformers.append(("bool", "passthrough", boolean))

    preprocessor = ml["ColumnTransformer"](transformers=transformers, remainder="drop")
    regressor = ml["HistGradientBoostingRegressor"](
        loss="squared_error",
        learning_rate=args.learning_rate,
        max_iter=args.max_iter,
        max_leaf_nodes=args.max_leaf_nodes,
        l2_regularization=0.05,
        random_state=args.random_state,
    )
    return ml["Pipeline"]([("prep", preprocessor), ("model", regressor)])


def safe_divide(numerator: float, denominator: float) -> float:
    """Perform division returning 0.0 if denominator is 0.

    Args:
        numerator: Divisor.
        denominator: Dividend.

    Returns:
        float: Quotient.
    """
    return 0.0 if denominator == 0 else numerator / denominator


def evaluate_predictions(
    df: pd.DataFrame,
    forecast_cols: list[str],
    group_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Evaluate WAPE and bias error metrics across grouped segments of holdout data.

    Args:
        df: Target dataframe containing forecasts and actuals.
        forecast_cols: List of forecast columns to evaluate.
        group_cols: Optional columns to group by.

    Returns:
        pd.DataFrame: Compiled metrics dataframe.
    """
    group_cols = group_cols or []
    groups = df.groupby(group_cols, dropna=False) if group_cols else [((), df)]
    rows: list[dict[str, Any]] = []
    for key, group in groups:
        key_values = key if isinstance(key, tuple) else (key,)
        base = dict(zip(group_cols, key_values, strict=False))
        actual = float(group[TARGET_COLUMN].sum())
        for col in forecast_cols:
            forecast = float(group[col].sum())
            abs_error = float((group[col] - group[TARGET_COLUMN]).abs().sum())
            rows.append(
                {
                    **base,
                    "ForecastName": col,
                    "Rows": int(len(group)),
                    "ActualUnits": actual,
                    "ForecastUnits": forecast,
                    "BiasUnits": forecast - actual,
                    "BiasPct": safe_divide(forecast - actual, actual),
                    "WAPE": safe_divide(abs_error, actual),
                }
            )
    return pd.DataFrame(rows)


def bounded_factor(actual: float, forecast: float, minimum: float = 0.25, maximum: float = 4.0) -> float:
    """Compute bounded post-model multiplicative scaling factor.

    Limits factors to prevent extreme scaling shifts.

    Args:
        actual: Sum of actual units.
        forecast: Sum of predicted forecast units.
        minimum: Lowest allowed scaling factor.
        maximum: Highest allowed scaling factor.

    Returns:
        float: The bounded calibration factor.
    """
    if forecast <= 0:
        return 1.0
    return float(np.clip(actual / forecast, minimum, maximum))


def calibration_factor_table(
    df: pd.DataFrame,
    raw_forecast_col: str,
    group_cols: list[str],
) -> pd.DataFrame:
    """Build a lookup table of calibration scaling factors by groups.

    Args:
        df: Calibration dataset.
        raw_forecast_col: Uncalibrated forecast column name.
        group_cols: Group columns to segment factors.

    Returns:
        pd.DataFrame: Factor lookup dataframe.
    """
    if df.empty:
        return pd.DataFrame(columns=[*group_cols, "CalibrationFactor"])

    rows: list[dict[str, Any]] = []
    groups = df.groupby(group_cols, dropna=False) if group_cols else [((), df)]
    for key, group in groups:
        key_values = key if isinstance(key, tuple) else (key,)
        actual = float(group[TARGET_COLUMN].sum())
        forecast = float(group[raw_forecast_col].sum())
        rows.append(
            {
                **dict(zip(group_cols, key_values, strict=False)),
                "CalibrationRows": int(len(group)),
                "CalibrationActualUnits": actual,
                "CalibrationForecastUnits": forecast,
                "CalibrationFactor": bounded_factor(actual, forecast),
            }
        )
    return pd.DataFrame(rows)


def calibration_group_columns(mode: str, df: pd.DataFrame) -> list[str]:
    """Retrieve segment group columns corresponding to a calibration mode.

    Args:
        mode: Calibration mode string.
        df: Input dataframe to inspect.

    Returns:
        list[str]: Matched columns present in dataframe.
    """
    candidates = {
        "global": [],
        "none": [],
        "sku-promo": ["HasSkuPDLPromotion"],
        "category": ["Division", "Department", "Class"],
        "category-velocity": ["Division", "Department", "Class", "Velocity"],
        "category-velocity-promo": [
            "Division",
            "Department",
            "Class",
            "Velocity",
            "HasSkuPDLPromotion",
        ],
    }
    return [col for col in candidates.get(mode, []) if col in df.columns]


def apply_calibration(
    holdout: pd.DataFrame,
    calibration: pd.DataFrame,
    raw_forecast_col: str,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply post-model calibration multipliers to predictions in the holdout window.

    Ensures that segment-specific factors are backed by enough history; otherwise, it
    falls back to a global calibration factor.

    Args:
        holdout: Prediction dataset.
        calibration: Calibration history window dataset.
        raw_forecast_col: Raw model forecast column name.
        args: Pipeline options.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: Updated prediction dataset and calibration factors table.
    """
    if args.calibration_days <= 0 or args.calibration_mode == "none" or calibration.empty:
        holdout["MLCalibratedForecastQty"] = holdout[raw_forecast_col]
        factors = pd.DataFrame(
            [
                {
                    "CalibrationMode": "none",
                    "CalibrationRows": int(len(calibration)),
                    "CalibrationFactor": 1.0,
                }
            ]
        )
        return holdout, factors

    group_cols = calibration_group_columns(args.calibration_mode, calibration)
    factors = calibration_factor_table(calibration, raw_forecast_col, group_cols)
    factors["CalibrationMode"] = args.calibration_mode
    fallback = bounded_factor(
        float(calibration[TARGET_COLUMN].sum()),
        float(calibration[raw_forecast_col].sum()),
    )
    if not factors.empty:
        factors["GlobalFallbackFactor"] = fallback
        factors["CalibrationFallbackUsed"] = False
        reliable = factors["CalibrationRows"].ge(args.calibration_min_rows) & factors[
            "CalibrationActualUnits"
        ].ge(args.calibration_min_actual_units)
        factors.loc[~reliable, "CalibrationFallbackUsed"] = True
        factors.loc[~reliable, "CalibrationFactor"] = fallback

    if group_cols:
        calibrated = holdout.merge(
            factors[[*group_cols, "CalibrationFactor"]],
            on=group_cols,
            how="left",
        )
        calibrated["CalibrationFactor"] = calibrated["CalibrationFactor"].fillna(fallback)
    else:
        calibrated = holdout.copy()
        calibrated["CalibrationFactor"] = (
            float(factors["CalibrationFactor"].iloc[0]) if not factors.empty else 1.0
        )

    calibrated["MLCalibratedForecastQty"] = (
        calibrated[raw_forecast_col] * calibrated["CalibrationFactor"]
    ).clip(lower=0)
    return calibrated.drop(columns=["CalibrationFactor"]), factors


def period_summary(
    holdout: pd.DataFrame,
    forecast_cols: list[str],
    holdout_start: pd.Timestamp,
    holdout_end: pd.Timestamp,
) -> pd.DataFrame:
    """Build performance evaluation summaries for multiple historical sub-intervals.

    Calculates metrics for the overall holdout, trailing 7 days, trailing 28 days,
    YTD, and individual months.

    Args:
        holdout: Holdout prediction dataset.
        forecast_cols: Forecast columns to evaluate.
        holdout_start: Start timestamp of the holdout period.
        holdout_end: End timestamp of the holdout period.

    Returns:
        pd.DataFrame: Summary table rows.
    """
    windows: list[tuple[str, pd.Timestamp, pd.Timestamp]] = [
        ("holdout", holdout_start, holdout_end),
        ("last_7_days", max(holdout_start, holdout_end - pd.Timedelta(days=6)), holdout_end),
        ("last_28_days", max(holdout_start, holdout_end - pd.Timedelta(days=27)), holdout_end),
    ]
    if holdout_start <= pd.Timestamp(date(holdout_end.year, 1, 1)) <= holdout_end:
        windows.append(("year_to_date", pd.Timestamp(date(holdout_end.year, 1, 1)), holdout_end))

    month_starts = pd.date_range(
        holdout_start.replace(day=1),
        holdout_end,
        freq="MS",
    )
    for month_start in month_starts:
        month_end = month_start + pd.offsets.MonthEnd(0)
        start = max(holdout_start, month_start)
        end = min(holdout_end, month_end)
        if start <= end:
            windows.append((f"month_{month_start:%Y_%m}", start, end))

    rows = []
    for name, start, end in windows:
        window_df = holdout.loc[holdout[DATE_COLUMN].between(start, end)].copy()
        if window_df.empty:
            continue
        evaluated = evaluate_predictions(window_df, forecast_cols)
        evaluated.insert(0, "PeriodName", name)
        evaluated.insert(1, "PeriodStart", str(start.date()))
        evaluated.insert(2, "PeriodEnd", str(end.date()))
        rows.append(evaluated)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def main() -> None:
    """Execute the command line entry point to train a forecasting model and run backtests."""
    args = parse_args()
    configure_threads(args.threads)
    try:
        ml = require_sklearn()
    except RuntimeError as exc:
        print(exc)
        raise SystemExit(2) from exc
    if args.check_deps:
        print("scikit-learn dependencies are available.")
        return

    panel = load_panel(args.panel, args.start_date)
    holdout_start, holdout_end = resolve_holdout(panel, args)
    calibration_start = holdout_start - pd.Timedelta(days=max(args.calibration_days, 0))
    train = panel.loc[panel[DATE_COLUMN].lt(calibration_start)].copy()
    calibration = panel.loc[panel[DATE_COLUMN].between(calibration_start, holdout_start - pd.Timedelta(days=1))].copy()
    if train.empty:
        train = panel.loc[panel[DATE_COLUMN].lt(holdout_start)].copy()
        calibration = panel.iloc[0:0].copy()
    holdout = panel.loc[panel[DATE_COLUMN].between(holdout_start, holdout_end)].copy()
    train = sample_training_rows(train, args.max_train_rows, args.random_state)

    x_train, y_train, numeric, categorical, boolean = prepare_xy(
        train,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    x_calibration, _, _, _, _ = (
        prepare_xy(
            calibration,
            args.exclude_corporate_features,
            args.include_product_identity_features,
        )
        if not calibration.empty
        else (None, None, None, None, None)
    )
    x_holdout, _, _, _, _ = prepare_xy(
        holdout,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )

    model = build_model(ml, numeric, categorical, boolean, args)
    model.fit(x_train, y_train)
    if x_calibration is not None:
        calibration["MLForecastQty"] = np.clip(np.expm1(model.predict(x_calibration)), 0, None)
    holdout["MLForecastQty"] = np.clip(np.expm1(model.predict(x_holdout)), 0, None)
    holdout, calibration_factors = apply_calibration(holdout, calibration, "MLForecastQty", args)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    forecast_cols = [
        "MLForecastQty",
        "MLCalibratedForecastQty",
        *[col for col in BASELINE_COLUMNS if col in holdout.columns],
    ]
    summary = evaluate_predictions(holdout, forecast_cols)
    periods = period_summary(holdout, forecast_cols, holdout_start, holdout_end)
    by_promo = evaluate_predictions(holdout, forecast_cols, ["HasSkuPDLPromotion"])
    by_category = evaluate_predictions(holdout, forecast_cols, ["Division", "Department", "Class"])
    by_category = by_category.sort_values(["ForecastName", "ActualUnits"], ascending=[True, False])

    summary.to_csv(output_dir / "ml_backtest_summary.csv", index=False)
    periods.to_csv(output_dir / "ml_backtest_period_summary.csv", index=False)
    calibration_factors.to_csv(output_dir / "ml_calibration_factors.csv", index=False)
    by_promo.to_csv(output_dir / "ml_backtest_by_sku_promo_flag.csv", index=False)
    by_category.head(1000).to_csv(output_dir / "ml_backtest_by_category_top1000.csv", index=False)
    holdout.loc[
        holdout[TARGET_COLUMN].gt(0),
        [
            DATE_COLUMN,
            "SKU",
            TARGET_COLUMN,
            "MLForecastQty",
            "MLCalibratedForecastQty",
            *[col for col in BASELINE_COLUMNS if col in holdout.columns],
            "HasSkuPDLPromotion",
            "Division",
            "Department",
            "Class",
        ],
    ].head(10000).to_csv(output_dir / "ml_prediction_sample.csv", index=False)

    model_path = output_dir / "hist_gradient_boosting_model.joblib"
    ml["dump"](model, model_path)
    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "model_path": str(model_path),
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "holdout_rows": int(len(holdout)),
        "calibration_date_range": [
            str(calibration_start.date()),
            str((holdout_start - pd.Timedelta(days=1)).date()),
        ]
        if not calibration.empty
        else None,
        "holdout_date_range": [str(holdout_start.date()), str(holdout_end.date())],
        "calibration_mode": args.calibration_mode,
        "exclude_corporate_features": args.exclude_corporate_features,
        "include_product_identity_features": args.include_product_identity_features,
        "features": {
            "numeric": numeric,
            "categorical": categorical,
            "boolean": boolean,
        },
        "outputs": {
            "summary": str(output_dir / "ml_backtest_summary.csv"),
            "period_summary": str(output_dir / "ml_backtest_period_summary.csv"),
            "calibration_factors": str(output_dir / "ml_calibration_factors.csv"),
            "by_promo": str(output_dir / "ml_backtest_by_sku_promo_flag.csv"),
            "by_category": str(output_dir / "ml_backtest_by_category_top1000.csv"),
            "prediction_sample": str(output_dir / "ml_prediction_sample.csv"),
        },
    }
    with (output_dir / "ml_model_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print(summary.to_string(index=False))
    print(f"Wrote ML outputs to {output_dir}")


if __name__ == "__main__":
    main()
