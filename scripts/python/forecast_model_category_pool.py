"""Two-stage category-pool forecast candidates for the KYDC DirectPick horizon.

This module implements the architecture that ``FORECAST_CURRENT_STATE.md`` and
``FORECAST_DATA_LANDSCAPE_2026-07-20.md`` describe as the correct direction, but
that had not yet been implemented as a runnable candidate:

1. Forecast a *stable category-size pool* total (``GIRM``/``BOYM`` grain =
   ``ProductGroupCode + SizeGroupCode``) for the 14-day horizon.
2. Allocate each category total across the *current* eligible assortment using
   recent within-category DirectPick shape, with deterministic
   largest-remainder (Hamilton) rounding so category and daily totals are
   preserved exactly.

Two Stage-1 volume anchors are supported:

* ``independent``  - current pre-origin run-rate x horizon x shrunk multi-year
  same-calendar event lift (fully self-contained, no corporate feed);
* ``corporate_anchor`` - take the corporate daily totals as the volume truth
  (they carry promotion/commercial knowledge the warehouse model lacks), but
  *reconcile them by category* before allocating to SKUs. This is the documented
  missing step: the July closeout champion split the corporate daily total
  across a single global recent-share pool with no category reconciliation.

An optional season-transition activation layer (``--activation``) reshapes the
within-category SKU weights using origin-safe inventory/inbound evidence so that
newly activated SKUs receive a category/size prior instead of a zero, and
ending-season SKUs with no supply/inbound support are down-weighted. This
addresses the season-transition failure mode called out in the current-state
doc.

Everything here is intentionally free of live-AX / corporate-DB access; it runs
on the portable Parquet facts already tracked in this repo, so it is honestly
freezable and reproducible on any PC.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from forecast_schema import FD_COLUMNS, normalize_sku_series, same_month_day  # noqa: E402
from output_paths import PROJECT_ROOT  # noqa: E402

FORECAST_ACCURACY_ROOT = PROJECT_ROOT / "Output" / "ForecastAccuracy"
DIRECT_PICK_DIR = FORECAST_ACCURACY_ROOT / "direct_pick_history" / "parquet"
PICKFACE_INVENTORY_PATH = FORECAST_ACCURACY_ROOT / "inventory" / "pickface_inventory_sku_day.parquet"
OPEN_INBOUND_PATH = FORECAST_ACCURACY_ROOT / "inbound" / "ax_open_inbound_sku_day.parquet"

HORIZON_DAYS = 14
DEFAULT_LOOKBACK_DAYS = 56
DEFAULT_SEASONAL_YEARS = 3
DEFAULT_SEASONAL_WINDOW_DAYS = 3
DEFAULT_BASELINE_DAYS = 28
# Empirical-Bayes style shrinkage: category lift is pulled toward 1.0 until the
# prior-year baseline supplies at least this many units of evidence.
DEFAULT_LIFT_SHRINK_UNITS = 400.0
LIFT_FLOOR = 0.5
LIFT_CEIL = 3.0
# Activation-layer tuning.
NEW_SKU_PRIOR_FRACTION = 0.5  # fraction of a "typical" positive SKU weight given to activated new SKUs
ENDING_SEASON_DECAY = 0.35  # multiplier applied to recent weight of unsupported ending-season SKUs


@dataclass
class ModelConfig:
    """Runtime configuration for a category-pool candidate build."""

    origin: pd.Timestamp
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    seasonal_years: int = DEFAULT_SEASONAL_YEARS
    seasonal_window_days: int = DEFAULT_SEASONAL_WINDOW_DAYS
    baseline_days: int = DEFAULT_BASELINE_DAYS
    lift_shrink_units: float = DEFAULT_LIFT_SHRINK_UNITS
    use_activation: bool = False
    direct_pick_dir: Path = DIRECT_PICK_DIR
    inventory_path: Path = PICKFACE_INVENTORY_PATH
    inbound_path: Path = OPEN_INBOUND_PATH
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def horizon_dates(self) -> list[pd.Timestamp]:
        return [self.origin + pd.Timedelta(days=idx) for idx in range(HORIZON_DAYS)]


# --------------------------------------------------------------------------- #
# Data loading (origin-safe: nothing after the origin is ever read)
# --------------------------------------------------------------------------- #
def _read_direct_pick_year(directory: Path, year: int) -> pd.DataFrame:
    path = directory / f"direct_pick_sku_day_modified_{year}.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["ActualDate", "SKU", "SoldUnits"])
    frame = pd.read_parquet(path, columns=["PickDate", "SKU", "PickUnits"])
    frame = frame.rename(columns={"PickDate": "ActualDate", "PickUnits": "SoldUnits"})
    frame["ActualDate"] = pd.to_datetime(frame["ActualDate"], errors="coerce").dt.normalize()
    frame["SKU"] = normalize_sku_series(frame["SKU"])
    frame["SoldUnits"] = pd.to_numeric(frame["SoldUnits"], errors="coerce").fillna(0).clip(lower=0)
    return frame.loc[frame["ActualDate"].notna() & frame["SKU"].ne("") & frame["SoldUnits"].gt(0)]


def load_history(config: ModelConfig) -> pd.DataFrame:
    """Load only the DirectPick date ranges required by this origin.

    Reads the recent lookback window plus each prior-year seasonal event and
    baseline window. Never returns a row dated on or after the origin.
    """
    keep_ranges: dict[int, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}

    lb_start = config.origin - pd.Timedelta(days=config.lookback_days)
    lb_end = config.origin - pd.Timedelta(days=1)
    keep_ranges.setdefault(config.origin.year, []).append((lb_start, lb_end))
    keep_ranges.setdefault(lb_start.year, []).append((lb_start, lb_end))

    horizon_end = config.origin + pd.Timedelta(days=HORIZON_DAYS - 1)
    for offset in range(1, config.seasonal_years + 1):
        year = config.origin.year - offset
        event_start = same_month_day(year, config.origin)
        event_end = same_month_day(year, horizon_end)
        base_start = event_start - pd.Timedelta(days=config.baseline_days)
        base_end = event_start - pd.Timedelta(days=1)
        keep_ranges.setdefault(year, []).extend(
            [(event_start, event_end), (base_start, base_end)]
        )

    frames: list[pd.DataFrame] = []
    for year, ranges in keep_ranges.items():
        year_frame = _read_direct_pick_year(config.direct_pick_dir, year)
        if year_frame.empty:
            continue
        mask = pd.Series(False, index=year_frame.index)
        for start, end in ranges:
            mask = mask | year_frame["ActualDate"].between(start, end)
        frames.append(year_frame.loc[mask])
    if not frames:
        return pd.DataFrame(columns=["ActualDate", "SKU", "SoldUnits"])
    history = pd.concat(frames, ignore_index=True)
    # A range can be requested twice (lookback spanning a year boundary); dedupe.
    return history.drop_duplicates(["ActualDate", "SKU"]).reset_index(drop=True)


def load_crosswalk(ledger_db: Path) -> pd.DataFrame:
    """Return a SKU -> category-size crosswalk from an ingestion ledger snapshot."""
    if not ledger_db.exists():
        raise FileNotFoundError(f"Category ledger not found: {ledger_db}")
    uri = f"file:{ledger_db.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        ledger = pd.read_sql_query(
            "SELECT sku, product_group, size_group, last_seen, source_file FROM sku_ledger",
            connection,
        )
    ledger["SKU"] = normalize_sku_series(ledger["sku"])
    ledger["last_seen"] = pd.to_datetime(ledger["last_seen"], errors="coerce")
    ledger = ledger.sort_values(["last_seen", "source_file"], kind="mergesort")
    ledger = ledger.drop_duplicates("SKU", keep="last")
    product_group = ledger["product_group"].fillna("").astype(str).str.strip().str.upper()
    size_group = ledger["size_group"].fillna("").astype(str).str.strip().str.upper()
    ledger["Category"] = (product_group + size_group).replace("", "UNKNOWN")
    return ledger.loc[ledger["SKU"].ne(""), ["SKU", "Category"]].reset_index(drop=True)


def _latest_snapshot(path: Path, origin: pd.Timestamp) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    frame["SnapshotDate"] = pd.to_datetime(frame["SnapshotDate"], errors="coerce").dt.normalize()
    frame = frame.loc[frame["SnapshotDate"].le(origin - pd.Timedelta(days=1))]
    if frame.empty:
        return frame
    latest = frame["SnapshotDate"].max()
    out = frame.loc[frame["SnapshotDate"].eq(latest)].copy()
    out["SKU"] = normalize_sku_series(out["SKU"])
    return out


# --------------------------------------------------------------------------- #
# Stage 1 - category volume
# --------------------------------------------------------------------------- #
def category_run_rate(history: pd.DataFrame, crosswalk: pd.DataFrame, config: ModelConfig) -> pd.DataFrame:
    """Recent per-category daily run-rate on a complete calendar-day spine.

    Uses ``lookback_days`` as the denominator (not the number of non-zero
    calendar days) so closed / zero-pick days remain zeros. This fixes the
    documented ``nunique()`` denominator bug in the retired overlay.
    """
    start = config.origin - pd.Timedelta(days=config.lookback_days)
    end = config.origin - pd.Timedelta(days=1)
    window = history.loc[history["ActualDate"].between(start, end)].merge(
        crosswalk, on="SKU", how="left"
    )
    window["Category"] = window["Category"].fillna("UNKNOWN")
    grouped = window.groupby("Category", as_index=False)["SoldUnits"].sum()
    grouped["DailyRunRate"] = grouped["SoldUnits"] / float(config.lookback_days)
    return grouped.rename(columns={"SoldUnits": "LookbackUnits"})


def category_event_lift(history: pd.DataFrame, crosswalk: pd.DataFrame, config: ModelConfig) -> pd.DataFrame:
    """Shrunk multi-year same-calendar event lift per category.

    For each prior year, lift = (event-window daily rate) / (pre-event baseline
    daily rate). Lifts are pooled across years weighted by baseline units and
    shrunk toward 1.0 by ``lift_shrink_units`` of evidence, then clipped.
    """
    horizon_end = config.origin + pd.Timedelta(days=HORIZON_DAYS - 1)
    per_year: list[pd.DataFrame] = []
    for offset in range(1, config.seasonal_years + 1):
        year = config.origin.year - offset
        event_start = same_month_day(year, config.origin)
        event_end = same_month_day(year, horizon_end)
        base_start = event_start - pd.Timedelta(days=config.baseline_days)
        base_end = event_start - pd.Timedelta(days=1)
        event_days = (event_end - event_start).days + 1
        joined = history.merge(crosswalk, on="SKU", how="left")
        joined["Category"] = joined["Category"].fillna("UNKNOWN")
        event = (
            joined.loc[joined["ActualDate"].between(event_start, event_end)]
            .groupby("Category", as_index=False)["SoldUnits"].sum()
            .rename(columns={"SoldUnits": "EventUnits"})
        )
        base = (
            joined.loc[joined["ActualDate"].between(base_start, base_end)]
            .groupby("Category", as_index=False)["SoldUnits"].sum()
            .rename(columns={"SoldUnits": "BaseUnits"})
        )
        merged = event.merge(base, on="Category", how="outer").fillna(0)
        merged["EventDailyRate"] = merged["EventUnits"] / float(event_days)
        merged["BaseDailyRate"] = merged["BaseUnits"] / float(config.baseline_days)
        merged["Year"] = year
        per_year.append(merged)
    if not per_year:
        return pd.DataFrame(columns=["Category", "ShrunkLift"])
    stacked = pd.concat(per_year, ignore_index=True)
    stacked = stacked.loc[stacked["BaseDailyRate"].gt(0)].copy()
    stacked["RawLift"] = (stacked["EventDailyRate"] / stacked["BaseDailyRate"]).clip(
        lower=LIFT_FLOOR, upper=LIFT_CEIL
    )

    def _shrink(group: pd.DataFrame) -> float:
        weights = group["BaseUnits"].to_numpy(dtype=float)
        lifts = group["RawLift"].to_numpy(dtype=float)
        total_w = weights.sum()
        if total_w <= 0:
            return 1.0
        pooled = float((weights * lifts).sum() / total_w)
        return (total_w * pooled + config.lift_shrink_units * 1.0) / (
            total_w + config.lift_shrink_units
        )

    lift = (
        stacked.groupby("Category")
        .apply(_shrink, include_groups=False)
        .rename("ShrunkLift")
        .reset_index()
    )
    lift["ShrunkLift"] = lift["ShrunkLift"].clip(lower=LIFT_FLOOR, upper=LIFT_CEIL)
    return lift


def stage1_independent_targets(history: pd.DataFrame, crosswalk: pd.DataFrame, config: ModelConfig) -> pd.DataFrame:
    """Category 14-day target = run-rate x horizon x shrunk lift."""
    run_rate = category_run_rate(history, crosswalk, config)
    lift = category_event_lift(history, crosswalk, config)
    targets = run_rate.merge(lift, on="Category", how="left")
    targets["ShrunkLift"] = targets["ShrunkLift"].fillna(1.0)
    targets["CategoryTarget"] = targets["DailyRunRate"] * HORIZON_DAYS * targets["ShrunkLift"]
    return targets.loc[targets["CategoryTarget"].gt(0), ["Category", "CategoryTarget"]]


# --------------------------------------------------------------------------- #
# Stage 2 - SKU allocation
# --------------------------------------------------------------------------- #
def hamilton_round(weights: np.ndarray, total: int) -> np.ndarray:
    """Largest-remainder integer allocation of ``total`` proportional to weights."""
    total = int(total)
    if total <= 0 or weights.sum() <= 0:
        return np.zeros(len(weights), dtype=int)
    raw = weights / weights.sum() * total
    floors = np.floor(raw).astype(int)
    remainder = int(total - floors.sum())
    if remainder > 0:
        order = np.lexsort((np.arange(len(weights)), -(raw - floors)))
        floors[order[:remainder]] += 1
    return floors


def sku_recent_weights(history: pd.DataFrame, crosswalk: pd.DataFrame, config: ModelConfig) -> pd.DataFrame:
    """Per-SKU recent lookback units with category tag (Stage-2 base shape)."""
    start = config.origin - pd.Timedelta(days=config.lookback_days)
    end = config.origin - pd.Timedelta(days=1)
    window = history.loc[history["ActualDate"].between(start, end)]
    weights = window.groupby("SKU", as_index=False)["SoldUnits"].sum().rename(
        columns={"SoldUnits": "RecentUnits"}
    )
    weights = weights.merge(crosswalk, on="SKU", how="left")
    weights["Category"] = weights["Category"].fillna("UNKNOWN")
    return weights


def apply_activation_layer(
    weights: pd.DataFrame, crosswalk: pd.DataFrame, config: ModelConfig
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Reshape SKU weights with origin-safe inventory/inbound evidence.

    * Activate new SKUs: present in the latest pre-origin inventory/inbound
      snapshot but with negligible recent demand -> give a category prior.
    * Down-weight ending-season SKUs: positive recent demand but no pickable
      inventory and no open inbound -> shrink the weight.
    """
    inventory = _latest_snapshot(config.inventory_path, config.origin)
    inbound = _latest_snapshot(config.inbound_path, config.origin)

    has_inv = set()
    if not inventory.empty:
        col = "HasPickableInventory" if "HasPickableInventory" in inventory.columns else None
        pos = inventory if col is None else inventory.loc[inventory[col].fillna(False).astype(bool)]
        has_inv = set(pos["SKU"])
    has_inb = set()
    if not inbound.empty:
        units = pd.to_numeric(inbound.get("InboundTotalUnits", 0), errors="coerce").fillna(0)
        has_inb = set(inbound.loc[units.gt(0), "SKU"])

    active_evidence = has_inv | has_inb
    weights = weights.copy()
    weights["HasInv"] = weights["SKU"].isin(has_inv)
    weights["HasInbound"] = weights["SKU"].isin(has_inb)

    # Category prior = median positive recent weight per category.
    positive = weights.loc[weights["RecentUnits"].gt(0)]
    cat_prior = (
        positive.groupby("Category")["RecentUnits"].median().to_dict() if not positive.empty else {}
    )
    global_prior = float(positive["RecentUnits"].median()) if not positive.empty else 1.0

    # Add currently-active SKUs that had no recent demand at all (brand-new).
    known = set(weights["SKU"])
    new_rows = []
    for sku in active_evidence - known:
        cat = crosswalk.loc[crosswalk["SKU"].eq(sku), "Category"]
        category = cat.iloc[0] if not cat.empty else "UNKNOWN"
        new_rows.append(
            {"SKU": sku, "RecentUnits": 0.0, "Category": category, "HasInv": sku in has_inv,
             "HasInbound": sku in has_inb}
        )
    activated_new = len(new_rows)
    if new_rows:
        weights = pd.concat([weights, pd.DataFrame(new_rows)], ignore_index=True)

    weights["Weight"] = weights["RecentUnits"].astype(float)

    # New/low-history but active SKUs get a category prior weight.
    low_history = weights["RecentUnits"].le(0.0) | (
        weights["RecentUnits"] < 0.25 * weights["Category"].map(cat_prior).fillna(global_prior)
    )
    active = weights["HasInv"] | weights["HasInbound"]
    boost_mask = low_history & active
    prior_weight = (
        weights["Category"].map(cat_prior).fillna(global_prior) * NEW_SKU_PRIOR_FRACTION
    )
    weights.loc[boost_mask, "Weight"] = np.maximum(
        weights.loc[boost_mask, "Weight"], prior_weight.loc[boost_mask]
    )

    # Ending-season down-weight: recent demand but no supply and no inbound.
    ending_mask = weights["RecentUnits"].gt(0) & ~weights["HasInv"] & ~weights["HasInbound"]
    weights.loc[ending_mask, "Weight"] = weights.loc[ending_mask, "Weight"] * ENDING_SEASON_DECAY

    meta = {
        "inventory_snapshot": (
            None if inventory.empty else str(inventory["SnapshotDate"].max().date())
        ),
        "inbound_snapshot": None if inbound.empty else str(inbound["SnapshotDate"].max().date()),
        "skus_with_pickable_inventory": len(has_inv),
        "skus_with_open_inbound": len(has_inb),
        "activated_brand_new_skus": activated_new,
        "boosted_low_history_active_skus": int(boost_mask.sum()),
        "downweighted_ending_season_skus": int(ending_mask.sum()),
    }
    return weights[["SKU", "Category", "Weight"]], meta


def category_daily_shape(history: pd.DataFrame, crosswalk: pd.DataFrame, config: ModelConfig) -> dict[str, dict[int, float]]:
    """Per-category day-of-week weights (normalized) from the lookback window."""
    start = config.origin - pd.Timedelta(days=config.lookback_days)
    end = config.origin - pd.Timedelta(days=1)
    window = history.loc[history["ActualDate"].between(start, end)].merge(
        crosswalk, on="SKU", how="left"
    )
    window["Category"] = window["Category"].fillna("UNKNOWN")
    window["dow"] = window["ActualDate"].dt.dayofweek
    shapes: dict[str, dict[int, float]] = {}
    for category, group in window.groupby("Category"):
        by_dow = group.groupby("dow")["SoldUnits"].sum()
        total = float(by_dow.sum())
        if total <= 0:
            continue
        shapes[category] = {int(d): float(v) / total for d, v in by_dow.items()}
    return shapes


def allocate(
    category_targets: pd.DataFrame,
    sku_weights: pd.DataFrame,
    daily_shapes: dict[str, dict[int, float]],
    config: ModelConfig,
    daily_category_totals: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Allocate category targets to SKUs and spread across the 14 days.

    If ``daily_category_totals`` is provided (Candidate x Category x day ->
    integer units), those exact per-day category totals are honored (corporate
    anchor path). Otherwise a single 14-day category target is split across days
    using the category day-of-week shape.
    """
    horizon = config.horizon_dates
    rows: list[pd.DataFrame] = []
    weights_by_cat = {cat: grp for cat, grp in sku_weights.groupby("Category")}

    for _, target_row in category_targets.iterrows():
        category = target_row["Category"]
        group = weights_by_cat.get(category)
        if group is None or group["Weight"].sum() <= 0:
            continue
        skus = group["SKU"].to_numpy()
        weights = group["Weight"].to_numpy(dtype=float)

        if daily_category_totals is not None:
            per_day = daily_category_totals.loc[
                daily_category_totals["Category"].eq(category)
            ]
            for _, drow in per_day.iterrows():
                units = int(drow["Units"])
                if units <= 0:
                    continue
                alloc = hamilton_round(weights, units)
                mask = alloc > 0
                rows.append(
                    pd.DataFrame(
                        {
                            "SKU": skus[mask],
                            "ForecastDate": drow["ForecastDate"],
                            "ForecastUnits": alloc[mask],
                        }
                    )
                )
        else:
            total = int(round(float(target_row["CategoryTarget"])))
            if total <= 0:
                continue
            sku_alloc = hamilton_round(weights, total)
            shape = daily_shapes.get(category)
            for sku, sku_total in zip(skus, sku_alloc):
                if sku_total <= 0:
                    continue
                if shape:
                    day_weights = np.array([shape.get(int(d.dayofweek), 0.0) for d in horizon])
                    if day_weights.sum() <= 0:
                        day_weights = np.ones(len(horizon))
                else:
                    day_weights = np.ones(len(horizon))
                day_alloc = hamilton_round(day_weights, int(sku_total))
                mask = day_alloc > 0
                rows.append(
                    pd.DataFrame(
                        {
                            "SKU": sku,
                            "ForecastDate": [horizon[i] for i in np.where(mask)[0]],
                            "ForecastUnits": day_alloc[mask],
                        }
                    )
                )
    if not rows:
        return pd.DataFrame(columns=["SKU", "ForecastDate", "ForecastUnits"])
    return pd.concat(rows, ignore_index=True)


def _corporate_daily_category_totals(
    corporate_daily: pd.DataFrame,
    category_targets: pd.DataFrame,
    crosswalk: pd.DataFrame,
    config: ModelConfig,
) -> pd.DataFrame:
    """Split each corporate daily total across categories (Hamilton), by category mix.

    Preserves each corporate daily total exactly, but routes the volume through
    the independent category mix instead of a single global SKU pool.
    """
    mix = category_targets.copy()
    mix_weights = mix.set_index("Category")["CategoryTarget"]
    daily = corporate_daily.groupby("ForecastDate", as_index=False)["ForecastUnits"].sum()
    out_rows = []
    cats = mix_weights.index.to_numpy()
    weights = mix_weights.to_numpy(dtype=float)
    for _, row in daily.iterrows():
        day_total = int(round(float(row["ForecastUnits"])))
        alloc = hamilton_round(weights, day_total)
        for cat, units in zip(cats, alloc):
            if units > 0:
                out_rows.append(
                    {"Category": cat, "ForecastDate": row["ForecastDate"], "Units": int(units)}
                )
    return pd.DataFrame(out_rows)


# --------------------------------------------------------------------------- #
# Candidate builders
# --------------------------------------------------------------------------- #
def build_candidates(
    config: ModelConfig,
    crosswalk: pd.DataFrame,
    corporate_daily: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build all category-pool candidates for one frozen origin.

    Returns a long-format frame (Candidate, SKU, ForecastDate, ForecastUnits)
    and a metadata dict.
    """
    history = load_history(config)
    category_targets = stage1_independent_targets(history, crosswalk, config)
    sku_weights = sku_recent_weights(history, crosswalk, config)
    daily_shapes = category_daily_shape(history, crosswalk, config)

    activation_meta: dict[str, Any] = {"status": "disabled"}
    if config.use_activation:
        sku_weights_alloc, activation_meta = apply_activation_layer(sku_weights, crosswalk, config)
    else:
        sku_weights_alloc = sku_weights.rename(columns={"RecentUnits": "Weight"})[
            ["SKU", "Category", "Weight"]
        ]

    candidates: list[pd.DataFrame] = []

    independent = allocate(category_targets, sku_weights_alloc, daily_shapes, config)
    independent["Candidate"] = (
        "catpool_activation" if config.use_activation else "catpool_independent"
    )
    candidates.append(independent)

    corporate_meta: dict[str, Any] = {"status": "no_corporate_feed"}
    if corporate_daily is not None and not corporate_daily.empty:
        daily_cat_totals = _corporate_daily_category_totals(
            corporate_daily, category_targets, crosswalk, config
        )
        anchored = allocate(
            category_targets,
            sku_weights_alloc,
            daily_shapes,
            config,
            daily_category_totals=daily_cat_totals,
        )
        anchored["Candidate"] = (
            "catpool_corporate_anchor_activation"
            if config.use_activation
            else "catpool_corporate_anchor"
        )
        candidates.append(anchored)
        corporate_meta = {
            "status": "ok",
            "corporate_total_units": float(corporate_daily["ForecastUnits"].sum()),
            "reconciled_categories": int(daily_cat_totals["Category"].nunique()),
        }

    combined = pd.concat(candidates, ignore_index=True)
    combined = combined[["Candidate", "SKU", "ForecastDate", "ForecastUnits"]]
    combined = combined.loc[combined["ForecastUnits"].gt(0)].reset_index(drop=True)

    metadata = {
        "origin": config.origin.date().isoformat(),
        "horizon_end": (config.origin + pd.Timedelta(days=HORIZON_DAYS - 1)).date().isoformat(),
        "lookback_days": config.lookback_days,
        "seasonal_years": config.seasonal_years,
        "history_rows": int(len(history)),
        "categories_modeled": int(len(category_targets)),
        "independent_category_units": float(category_targets["CategoryTarget"].sum()),
        "activation": activation_meta,
        "corporate_anchor": corporate_meta,
        "candidate_units": {
            name: float(grp["ForecastUnits"].sum())
            for name, grp in combined.groupby("Candidate")
        },
    }
    return combined, metadata


def _to_wide(daily: pd.DataFrame, candidate: str, origin: pd.Timestamp) -> pd.DataFrame:
    frame = daily.loc[daily["Candidate"].eq(candidate)].copy()
    frame["ForecastDay"] = (frame["ForecastDate"] - origin).dt.days + 1
    wide = (
        frame.pivot_table(index="SKU", columns="ForecastDay", values="ForecastUnits",
                          aggfunc="sum", fill_value=0)
        .rename(columns={day: f"FD{day}" for day in range(1, HORIZON_DAYS + 1)})
        .reset_index()
    )
    for column in FD_COLUMNS:
        if column not in wide.columns:
            wide[column] = 0
    return wide[["SKU", *FD_COLUMNS]].sort_values("SKU", kind="mergesort")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, type=lambda s: pd.Timestamp(s).normalize())
    parser.add_argument("--ledger-db", required=True, type=Path)
    parser.add_argument("--corporate-daily", type=Path,
                        help="Optional long/CSV of corporate daily forecast (Candidate/SKU/ForecastDate/ForecastUnits).")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--seasonal-years", type=int, default=DEFAULT_SEASONAL_YEARS)
    parser.add_argument("--activation", action="store_true")
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def load_corporate_daily(path: Path, origin: pd.Timestamp) -> pd.DataFrame:
    frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    if "Candidate" in frame.columns:
        # Anchor strictly on the raw corporate feed; do not sum derived corporate candidates.
        candidates = set(frame["Candidate"].astype(str).unique())
        chosen = "corporate_raw" if "corporate_raw" in candidates else next(
            (c for c in candidates if "corporate" in c.lower()), None
        )
        frame = frame.loc[frame["Candidate"].astype(str).eq(chosen)]
    frame["SKU"] = normalize_sku_series(frame["SKU"])
    frame["ForecastDate"] = pd.to_datetime(frame["ForecastDate"]).dt.normalize()
    frame["ForecastUnits"] = pd.to_numeric(frame["ForecastUnits"], errors="coerce").fillna(0)
    horizon_end = origin + pd.Timedelta(days=HORIZON_DAYS - 1)
    return frame.loc[frame["ForecastDate"].between(origin, horizon_end),
                     ["SKU", "ForecastDate", "ForecastUnits"]]


def main() -> int:
    args = parse_args()
    crosswalk = load_crosswalk(args.ledger_db)
    corporate = (
        load_corporate_daily(args.corporate_daily, args.origin) if args.corporate_daily else None
    )
    config = ModelConfig(
        origin=args.origin,
        lookback_days=args.lookback_days,
        seasonal_years=args.seasonal_years,
        use_activation=args.activation,
    )
    combined, metadata = build_candidates(config, crosswalk, corporate)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(output_dir / "category_pool_daily_forecasts.parquet", index=False)
    for candidate in combined["Candidate"].unique():
        _to_wide(combined, str(candidate), args.origin).to_csv(
            output_dir / f"{candidate}_fd14.csv", index=False
        )
    (output_dir / "category_pool_metadata.json").write_text(
        json.dumps({"generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
                    **metadata}, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(metadata["candidate_units"], indent=2))
    print(f"Wrote {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
