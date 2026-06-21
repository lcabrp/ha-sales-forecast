"""Train a SKU/day forecast model from the model panel using Quantile Regression and Demand Censoring.

This script implements Phase 1 improvements:
1. Quantile Loss: Trains the model using HistGradientBoostingRegressor(loss='quantile')
   with a conservative quantile (default: 0.35) to prioritize under-forecasting risk.
2. Demand Censoring: Drops training rows during known stockout periods (where 1-day lagged 
   inventory was explicitly 0 and not NaN) so the model does not learn stockouts as zero demand.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_model_train import (  # noqa: E402
    BASELINE_COLUMNS,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    TARGET_COLUMN,
    apply_calibration,
    configure_threads,
    evaluate_predictions,
    load_panel,
    period_summary,
    prepare_xy,
    require_sklearn,
    resolve_holdout,
    sample_training_rows,
)

DEFAULT_OUTPUT_DIR = MODEL_DIR / "ml_quantile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and backtest a forecast model with Quantile Loss.")
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
        "--quantile",
        type=float,
        default=0.35,
        help="Quantile parameter for HistGradientBoostingRegressor. Lower values (e.g. 0.35) reduce over-forecasting.",
    )
    parser.add_argument(
        "--disable-censoring",
        action="store_true",
        help="Disable demand censoring of stockout periods during training.",
    )
    parser.add_argument(
        "--calibration-days",
        type=int,
        default=28,
        help="Days immediately before the holdout used for post-model calibration.",
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
    )
    parser.add_argument("--calibration-min-rows", type=int, default=500)
    parser.add_argument("--calibration-min-actual-units", type=float, default=50.0)
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Thread cap for sklearn/joblib/OpenMP. Keep 1 inside Codex sandbox.",
    )
    parser.add_argument(
        "--exclude-corporate-features",
        action="store_true",
        default=True,
        help="Keep corporate forecast columns for baseline comparison, but exclude them from model training.",
    )
    parser.add_argument(
        "--include-product-identity-features",
        action="store_true",
        default=True,
        help="Add Item, Color, Size, and ItemColor categorical features.",
    )
    parser.add_argument("--check-deps", action="store_true")
    return parser.parse_args()


def build_quantile_model(
    ml: dict[str, Any],
    numeric: list[str],
    categorical: list[str],
    boolean: list[str],
    args: argparse.Namespace,
) -> Any:
    """Build HistGradientBoostingRegressor model configured for Quantile Loss."""
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
        loss="quantile",
        quantile=args.quantile,
        learning_rate=args.learning_rate,
        max_iter=args.max_iter,
        max_leaf_nodes=args.max_leaf_nodes,
        l2_regularization=0.05,
        random_state=args.random_state,
    )
    return ml["Pipeline"]([("prep", preprocessor), ("model", regressor)])


def main() -> None:
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

    # Split panel
    train = panel.loc[panel[DATE_COLUMN].lt(calibration_start)].copy()
    calibration = panel.loc[
        panel[DATE_COLUMN].between(calibration_start, holdout_start - pd.Timedelta(days=1))
    ].copy()
    if train.empty:
        train = panel.loc[panel[DATE_COLUMN].lt(holdout_start)].copy()
        calibration = panel.iloc[0:0].copy()
    holdout = panel.loc[panel[DATE_COLUMN].between(holdout_start, holdout_end)].copy()

    # Demand Censoring (Phase 1)
    if not args.disable_censoring:
        # Check stockout using InventoryAvailPhysicalLag1 or HasAvailableInventoryLag1
        # Only censor rows where we explicitly have inventory snapshot history (i.e. not NaN)
        # and physical available quantity is 0 or HasAvailableInventoryLag1 is False
        if "InventoryAvailPhysicalLag1" in train.columns:
            is_stockout = train["InventoryAvailPhysicalLag1"].eq(0.0) & train["InventoryAvailPhysicalLag1"].notna()
            print(f"Censoring: dropping {is_stockout.sum():,} training rows due to stockouts (InventoryAvailPhysicalLag1 == 0).", flush=True)
            train = train.loc[~is_stockout].copy()
        elif "HasAvailableInventoryLag1" in train.columns:
            is_stockout = train["HasAvailableInventoryLag1"].eq(False) & train["HasAvailableInventoryLag1"].notna()
            print(f"Censoring: dropping {is_stockout.sum():,} training rows due to stockouts (HasAvailableInventoryLag1 == False).", flush=True)
            train = train.loc[~is_stockout].copy()
        else:
            print("Censoring skipped: Inventory features not found in panel.", flush=True)

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

    # Build and fit quantile model
    model = build_quantile_model(ml, numeric, categorical, boolean, args)
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

    model_path = output_dir / "hist_gradient_boosting_model_quantile.joblib"
    ml["dump"](model, model_path)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "model_path": str(model_path),
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "holdout_rows": int(len(holdout)),
        "quantile": args.quantile,
        "demand_censoring_enabled": not args.disable_censoring,
        "calibration_mode": args.calibration_mode,
        "exclude_corporate_features": args.exclude_corporate_features,
        "include_product_identity_features": args.include_product_identity_features,
        "features": {
            "numeric": numeric,
            "categorical": categorical,
            "boolean": boolean,
        },
    }
    with (output_dir / "ml_model_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print(summary.to_string(index=False))
    print(f"Wrote ML Quantile outputs to {output_dir}")


if __name__ == "__main__":
    main()
