"""Scratch grid for transparent champion forecast overlay rules."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


INPUT = Path("Output/ForecastAccuracy/model/champion_candidate_shadow/champion_sku_day_forecast.parquet")
OUTPUT = Path("Output/ForecastAccuracy/model/champion_candidate_shadow/champion_overlay_rule_grid.csv")


def metric(df: pd.DataFrame, name: str, pred: pd.Series) -> dict[str, float | str]:
    pred = pred.clip(lower=0)
    actual = df["SoldUnits"]
    actual_units = float(actual.sum())
    forecast_units = float(pred.sum())
    return {
        "Name": name,
        "ForecastUnits": forecast_units,
        "BiasPct": (forecast_units - actual_units) / actual_units if actual_units else 0.0,
        "WAPE": float((pred - actual).abs().sum() / actual_units) if actual_units else 0.0,
    }


def main() -> None:
    df = pd.read_parquet(INPUT)
    raw = df["SelectedForecastQty"]
    r7 = df["Recent7BaselineQty"]
    r28 = df["Recent28BaselineQty"]

    rows: list[dict[str, float | str]] = [
        metric(df, "raw", raw),
        metric(df, "recent7", r7),
        metric(df, "recent28", r28),
    ]

    for weight in [0.1, 0.2, 0.3, 0.4, 0.5]:
        rows.append(metric(df, f"blend_raw_r7_{weight}", raw * (1 - weight) + r7 * weight))

    for factor in [0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.0]:
        rows.append(metric(df, f"global_floor_{factor}r7", np.maximum(raw, r7 * factor)))

    velocity_sets = [
        (["AA"], "AA"),
        (["AA", "A"], "AA_A"),
        (["AA", "A", "B"], "AA_A_B"),
    ]
    promo_mask = df["HasSkuPDLPromotion"].fillna(False)
    for velocities, label in velocity_sets:
        mask = df["Velocity"].isin(velocities) & promo_mask
        for factor in [0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.0]:
            pred = raw.copy()
            pred.loc[mask] = np.maximum(pred.loc[mask], r7.loc[mask] * factor)
            rows.append(metric(df, f"promo_{label}_floor_{factor}r7", pred))
        for weight in [0.15, 0.25, 0.35, 0.45]:
            pred = raw.copy()
            pred.loc[mask] = raw.loc[mask] * (1 - weight) + r7.loc[mask] * weight
            rows.append(metric(df, f"promo_{label}_blend_r7_{weight}", pred))

    for factor in [0.35, 0.45, 0.55, 0.65, 0.75]:
        rows.append(metric(df, f"floor_{factor}r28", np.maximum(raw, r28 * factor)))

    out = pd.DataFrame(rows).sort_values(["WAPE", "BiasPct"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT, index=False)
    print(out.head(40).to_string(index=False))
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
