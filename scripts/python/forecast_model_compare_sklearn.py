"""Compare scikit-learn forecast model candidates on the model panel.

This runner is intentionally experiment-focused.  It reuses the first model
panel and feature contract, then writes side-by-side metrics for a small set of
scikit-learn candidates that are worth testing before adding external gradient
boosting dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
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
    BOOLEAN_FEATURES,
    CORPORATE_FEATURE_COLUMNS,
    DATE_COLUMN,
    DEFAULT_PANEL_PATH,
    MODEL_DIR,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    apply_calibration,
    configure_threads,
    evaluate_predictions,
    load_panel,
    period_summary,
    prepare_xy,
    resolve_holdout,
    sample_training_rows,
)


DEFAULT_OUTPUT_DIR = MODEL_DIR / "sklearn_compare"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare scikit-learn forecast models.")
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--holdout-start")
    parser.add_argument("--holdout-end")
    parser.add_argument("--holdout-days", type=int, default=28)
    parser.add_argument("--max-train-rows", type=int, default=500_000)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=180)
    parser.add_argument("--learning-rate", type=float, default=0.06)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument("--calibration-days", type=int, default=28)
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
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument(
        "--exclude-corporate-features",
        action="store_true",
        help="Exclude corporate forecast fields from model features; keep them as comparison baselines.",
    )
    parser.add_argument(
        "--include-product-identity-features",
        action="store_true",
        help="Add Item, Color, Size, and ItemColor categorical features for experimental family-aware models.",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=[
            "hgb_squared_log",
            "hgb_poisson",
            "hgb_absolute_log",
            "hgb_quantile_p50_log",
            "ridge_numeric_log",
            "two_stage_hgb_log",
        ],
        help="Model keys to run.",
    )
    return parser.parse_args()


def require_sklearn() -> dict[str, Any]:
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OrdinalEncoder, StandardScaler
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "scikit-learn is required for the model comparison runner."
        ) from exc
    return {
        "ColumnTransformer": ColumnTransformer,
        "HistGradientBoostingClassifier": HistGradientBoostingClassifier,
        "HistGradientBoostingRegressor": HistGradientBoostingRegressor,
        "OrdinalEncoder": OrdinalEncoder,
        "Pipeline": Pipeline,
        "Ridge": Ridge,
        "SimpleImputer": SimpleImputer,
        "StandardScaler": StandardScaler,
    }


def _exclude_corporate(columns: list[str], exclude_corporate_features: bool) -> list[str]:
    if not exclude_corporate_features:
        return columns
    return [col for col in columns if col not in CORPORATE_FEATURE_COLUMNS]


def numeric_feature_frame(
    df: pd.DataFrame,
    exclude_corporate_features: bool = False,
) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    numeric = _exclude_corporate(
        [col for col in NUMERIC_FEATURES if col in df.columns],
        exclude_corporate_features,
    )
    boolean = _exclude_corporate(
        [col for col in BOOLEAN_FEATURES if col in df.columns],
        exclude_corporate_features,
    )
    features = df.loc[:, [*numeric, *boolean]].copy()
    for col in numeric:
        features[col] = pd.to_numeric(features[col], errors="coerce")
    for col in boolean:
        features[col] = features[col].fillna(False).astype("int8")
    target = np.log1p(pd.to_numeric(df[TARGET_COLUMN], errors="coerce").fillna(0).clip(lower=0))
    return features, target.to_numpy(), [*numeric, *boolean]


def full_preprocessor(
    ml: dict[str, Any],
    numeric: list[str],
    categorical: list[str],
    boolean: list[str],
) -> Any:
    transformers = []
    if numeric:
        transformers.append(
            (
                "num",
                ml["Pipeline"]([("imputer", ml["SimpleImputer"](strategy="constant", fill_value=0.0))]),
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
    return ml["ColumnTransformer"](transformers=transformers, remainder="drop")


def numeric_preprocessor(ml: dict[str, Any], columns: list[str]) -> Any:
    return ml["ColumnTransformer"](
        [
            (
                "num",
                ml["Pipeline"](
                    [
                        ("imputer", ml["SimpleImputer"](strategy="constant", fill_value=0.0)),
                        ("scaler", ml["StandardScaler"]()),
                    ]
                ),
                columns,
            )
        ],
        remainder="drop",
    )


def build_candidate(
    name: str,
    ml: dict[str, Any],
    numeric: list[str],
    categorical: list[str],
    boolean: list[str],
    args: argparse.Namespace,
) -> tuple[Any, str]:
    hgb = ml["HistGradientBoostingRegressor"]
    preprocessor = full_preprocessor(ml, numeric, categorical, boolean)
    common = {
        "learning_rate": args.learning_rate,
        "max_iter": args.max_iter,
        "max_leaf_nodes": args.max_leaf_nodes,
        "l2_regularization": 0.05,
        "random_state": args.random_state,
    }

    if name == "hgb_squared_log":
        return ml["Pipeline"]([("prep", preprocessor), ("model", hgb(loss="squared_error", **common))]), "log"
    if name == "hgb_poisson":
        return ml["Pipeline"]([("prep", preprocessor), ("model", hgb(loss="poisson", **common))]), "raw"
    if name == "hgb_absolute_log":
        return ml["Pipeline"]([("prep", preprocessor), ("model", hgb(loss="absolute_error", **common))]), "log"
    if name == "hgb_quantile_p50_log":
        return (
            ml["Pipeline"](
                [
                    (
                        "prep",
                        preprocessor,
                    ),
                    ("model", hgb(loss="quantile", quantile=0.5, **common)),
                ]
            ),
            "log",
        )
    if name == "ridge_numeric_log":
        return (
            ml["Pipeline"](
                [
                    ("prep", numeric_preprocessor(ml, [*numeric, *boolean])),
                    ("model", ml["Ridge"](alpha=10.0, random_state=args.random_state)),
                ]
            ),
            "numeric_log",
        )
    raise ValueError(f"Unknown single-stage model: {name}")


def predict_candidate(model: Any, mode: str, x_holdout: pd.DataFrame) -> np.ndarray:
    predictions = model.predict(x_holdout)
    if mode in {"log", "numeric_log"}:
        return np.clip(np.expm1(predictions), 0, None)
    return np.clip(predictions, 0, None)


def run_single_stage(
    name: str,
    ml: dict[str, Any],
    train: pd.DataFrame,
    calibration: pd.DataFrame,
    holdout: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if name == "ridge_numeric_log":
        x_train, y_train, feature_columns = numeric_feature_frame(
            train,
            args.exclude_corporate_features,
        )
        x_calibration, _, _ = (
            numeric_feature_frame(calibration, args.exclude_corporate_features)
            if not calibration.empty
            else (None, None, None)
        )
        x_holdout, _, _ = numeric_feature_frame(holdout, args.exclude_corporate_features)
        numeric, categorical, boolean = feature_columns, [], []
    else:
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
        if name == "hgb_poisson":
            y_train = pd.to_numeric(train[TARGET_COLUMN], errors="coerce").fillna(0).clip(lower=0).to_numpy()

    model, mode = build_candidate(name, ml, numeric, categorical, boolean, args)
    model.fit(x_train, y_train)
    scored = holdout.copy()
    raw_col = f"{name}ForecastQty"
    cal_col = f"{name}CalibratedForecastQty"
    scored[raw_col] = predict_candidate(model, mode, x_holdout)

    calibration_scored = calibration.copy()
    if x_calibration is not None:
        calibration_scored[raw_col] = predict_candidate(model, mode, x_calibration)
    scored, factors = apply_calibration(scored, calibration_scored, raw_col, args)
    scored = scored.rename(columns={"MLCalibratedForecastQty": cal_col})
    return scored, factors


def run_two_stage(
    ml: dict[str, Any],
    train: pd.DataFrame,
    calibration: pd.DataFrame,
    holdout: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    x_train, _, numeric, categorical, boolean = prepare_xy(
        train,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    x_holdout, _, _, _, _ = prepare_xy(
        holdout,
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

    preprocessor = full_preprocessor(ml, numeric, categorical, boolean)
    classifier = ml["Pipeline"](
        [
            ("prep", preprocessor),
            (
                "model",
                ml["HistGradientBoostingClassifier"](
                    learning_rate=args.learning_rate,
                    max_iter=args.max_iter,
                    max_leaf_nodes=args.max_leaf_nodes,
                    l2_regularization=0.05,
                    random_state=args.random_state,
                ),
            ),
        ]
    )
    classifier.fit(x_train, pd.to_numeric(train[TARGET_COLUMN], errors="coerce").fillna(0).gt(0))

    positive_train = train.loc[pd.to_numeric(train[TARGET_COLUMN], errors="coerce").fillna(0).gt(0)].copy()
    x_positive, y_positive, numeric, categorical, boolean = prepare_xy(
        positive_train,
        args.exclude_corporate_features,
        args.include_product_identity_features,
    )
    regressor, mode = build_candidate(
        "hgb_squared_log",
        ml,
        numeric,
        categorical,
        boolean,
        args,
    )
    regressor.fit(x_positive, y_positive)

    scored = holdout.copy()
    raw_col = "two_stage_hgb_logForecastQty"
    cal_col = "two_stage_hgb_logCalibratedForecastQty"
    scored[raw_col] = classifier.predict_proba(x_holdout)[:, 1] * predict_candidate(
        regressor,
        mode,
        x_holdout,
    )

    calibration_scored = calibration.copy()
    if x_calibration is not None:
        calibration_scored[raw_col] = classifier.predict_proba(x_calibration)[:, 1] * predict_candidate(
            regressor,
            mode,
            x_calibration,
        )
    scored, factors = apply_calibration(scored, calibration_scored, raw_col, args)
    scored = scored.rename(columns={"MLCalibratedForecastQty": cal_col})
    return scored, factors


def split_panel(panel: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    holdout_start, holdout_end = resolve_holdout(panel, args)
    calibration_start = holdout_start - pd.Timedelta(days=max(args.calibration_days, 0))
    train = panel.loc[panel[DATE_COLUMN].lt(calibration_start)].copy()
    calibration = panel.loc[
        panel[DATE_COLUMN].between(calibration_start, holdout_start - pd.Timedelta(days=1))
    ].copy()
    if train.empty:
        train = panel.loc[panel[DATE_COLUMN].lt(holdout_start)].copy()
        calibration = panel.iloc[0:0].copy()
    holdout = panel.loc[panel[DATE_COLUMN].between(holdout_start, holdout_end)].copy()
    train = sample_training_rows(train, args.max_train_rows, args.random_state)
    return train, calibration, holdout, holdout_start, holdout_end


def main() -> None:
    args = parse_args()
    configure_threads(args.threads)
    os.environ.setdefault("PYTHONHASHSEED", str(args.random_state))
    ml = require_sklearn()

    panel = load_panel(args.panel, args.start_date)
    train, calibration, holdout, holdout_start, holdout_end = split_panel(panel, args)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_summary = []
    all_periods = []
    all_factors = []
    for name in args.models:
        print(f"Running {name}")
        if name == "two_stage_hgb_log":
            scored, factors = run_two_stage(ml, train, calibration, holdout, args)
        else:
            scored, factors = run_single_stage(name, ml, train, calibration, holdout, args)

        forecast_cols = [
            f"{name}ForecastQty",
            f"{name}CalibratedForecastQty",
            *[col for col in BASELINE_COLUMNS if col in scored.columns],
        ]
        summary = evaluate_predictions(scored, forecast_cols)
        summary.insert(0, "ModelKey", name)
        periods = period_summary(scored, forecast_cols, holdout_start, holdout_end)
        periods.insert(0, "ModelKey", name)
        factors.insert(0, "ModelKey", name)

        all_summary.append(summary)
        all_periods.append(periods)
        all_factors.append(factors)

    summary_out = pd.concat(all_summary, ignore_index=True)
    periods_out = pd.concat(all_periods, ignore_index=True)
    factors_out = pd.concat(all_factors, ignore_index=True)

    model_rows = summary_out.loc[summary_out["ForecastName"].str.contains("ForecastQty", regex=False)].copy()
    model_rows = model_rows.sort_values(["WAPE", "BiasPct"], ascending=[True, True])
    summary_out.to_csv(args.output_dir / "sklearn_model_comparison_summary.csv", index=False)
    periods_out.to_csv(args.output_dir / "sklearn_model_comparison_periods.csv", index=False)
    factors_out.to_csv(args.output_dir / "sklearn_model_comparison_calibration_factors.csv", index=False)
    model_rows.to_csv(args.output_dir / "sklearn_model_comparison_ranked.csv", index=False)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(args.panel),
        "output_dir": str(args.output_dir),
        "models": args.models,
        "train_rows": int(len(train)),
        "calibration_rows": int(len(calibration)),
        "holdout_rows": int(len(holdout)),
        "holdout_date_range": [str(holdout_start.date()), str(holdout_end.date())],
        "max_train_rows": args.max_train_rows,
        "max_iter": args.max_iter,
        "learning_rate": args.learning_rate,
        "calibration_mode": args.calibration_mode,
        "exclude_corporate_features": args.exclude_corporate_features,
        "include_product_identity_features": args.include_product_identity_features,
    }
    with (args.output_dir / "sklearn_model_comparison_metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)

    print(model_rows[["ModelKey", "ForecastName", "ActualUnits", "ForecastUnits", "BiasPct", "WAPE"]].to_string(index=False))
    print(f"Wrote sklearn comparison outputs to {args.output_dir}")


if __name__ == "__main__":
    main()
