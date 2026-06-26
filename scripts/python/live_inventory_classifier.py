"""Live floor SKU classification for layout fit checks.

The allocator and what-if simulation both need the same answer to one question:
if a SKU is physically on the picking floor today, which SlotTier should reserve
capacity for it? Forecast CSV data is authoritative. Forecast-missing live SKUs
fall back to the SKU ledger, then the current product hierarchy chain used by
ingestion. Hierarchy fallback writes are disabled by default so validation runs
do not mutate the ledger.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from ingestion_pipeline import (
    build_hierarchy_fallback,
    get_latest_source_file,
    is_blank_or_na,
    map_product_group_code,
    map_size_group,
    parse_sku,
    product_group_override_for_item,
    read_ax_product_hierarchy,
    read_product_attributes,
)
from output_paths import INGESTION_OUTPUT_DIR
from sku_ledger import UPSERT_SQL, init_db


SKU_LEDGER_DB = INGESTION_OUTPUT_DIR / "sku_ledger.db"
WHOLESALE_LOCATION_CODES = {"W001"}
HIERARCHY_FIELDS = ["Division", "Department", "Class", "KeyCategoryView", "SizeGroup", "GoLiveDate"]


def velocity_from_slot_tier(slot_tier: str) -> str:
    """Return the velocity suffix from a SlotTier or zone label.

    Matches patterns like 'WAA' -> 'AA', 'PJA' -> 'A', etc.

    Args:
        slot_tier: The SlotTier/Zone identifier.

    Returns:
        str: The velocity suffix ('AA', 'A', 'B', 'C'), or empty string if unparseable.
    """
    value = str(slot_tier)
    if value.endswith("AA"):
        return "AA"
    if value and value[-1] in {"A", "B", "C"}:
        return value[-1]
    return ""


def build_tier_to_zone_map(proposed_map: pd.DataFrame) -> tuple[set[str], dict[str, list[str]]]:
    """Build available zone and family indexes from a proposed warehouse map.

    Args:
        proposed_map: DataFrame representing the warehouse layout zone-map.

    Returns:
        tuple[set[str], dict[str, list[str]]]: Distinct zone set and mapped zone-family list.
    """
    zone_col = "ProposedZoneId" if "ProposedZoneId" in proposed_map.columns else "ZONEID"
    available_zones = {
        str(zone).strip()
        for zone in proposed_map[zone_col].dropna().unique()
        if str(zone).strip()
    }

    zone_families: dict[str, list[str]] = {}
    for zone in sorted(available_zones):
        if zone == "OVFLO":
            continue
        prefix = zone[:-2] if zone.endswith("AA") else zone[:-1]
        zone_families.setdefault(prefix, []).append(zone)

    return available_zones, zone_families


def _same_family_velocity_order(source_velocity: str) -> list[str]:
    """AX-like fallback order within a size family.

    High-velocity items avoid C as a normal fallback because C locations can be
    much farther away. C remains usable for C items, where distance pressure is
    lower and the alternative is a hard unmapped exception.

    Args:
        source_velocity: Target velocity band ('AA', 'A', 'B', or 'C').

    Returns:
        list[str]: Falls back ordered velocity list.
    """
    if source_velocity == "AA":
        return ["AA", "A", "B"]
    if source_velocity == "A":
        return ["A", "AA", "B"]
    if source_velocity == "B":
        return ["B", "A", "AA"]
    return ["C", "B", "A", "AA"]


def resolve_zone(slot_tier: str, available_zones: set[str], zone_families: dict[str, list[str]]) -> str:
    """Resolve a SlotTier to the best available zone in a proposed map.

    Attempts exact mapping first. If missing, traverses same-family velocity ordering,
    and falls back to similar product groups if necessary.

    Args:
        slot_tier: SlotTier code to map.
        available_zones: Available zones set.
        zone_families: Mapped zone family definitions.

    Returns:
        str: Resolved zone ID, or 'Unmapped'.
    """
    tier = str(slot_tier).strip()
    if not tier:
        return "Unmapped"
    if tier in available_zones:
        return tier

    velocity = velocity_from_slot_tier(tier)
    prefix = tier[:-2] if tier.endswith("AA") else tier[:-1]
    if not prefix:
        return "Unmapped"

    candidates = zone_families.get(prefix, [])
    if candidates:
        for fallback_velocity in _same_family_velocity_order(velocity):
            candidate = prefix + fallback_velocity
            if candidate in available_zones:
                return candidate
        return sorted(candidates)[0]

    product_group = prefix[:3] if len(prefix) >= 3 else prefix
    product_group_candidates = [
        zone
        for family_prefix, zones in zone_families.items()
        if family_prefix.startswith(product_group)
        for zone in zones
        if velocity_from_slot_tier(zone) in _same_family_velocity_order(velocity)
    ]
    if product_group_candidates:
        product_group_candidates.sort(
            key=lambda zone: (
                _same_family_velocity_order(velocity).index(velocity_from_slot_tier(zone)),
                zone,
            )
        )
        return product_group_candidates[0]

    return "Unmapped"


def _load_forward_demand(demand_path: Path) -> pd.DataFrame:
    """Read authoritative forward demand forecast to map SKU -> SlotTier.

    Args:
        demand_path: Path to the FwdDemand CSV file.

    Returns:
        pd.DataFrame: DataFrame containing SKU, SlotTier and Total14DayDemand columns.
    """
    header = pd.read_csv(demand_path, nrows=0)
    fd_cols = [f"FD{i}" for i in range(1, 15) if f"FD{i}" in header.columns]
    usecols = ["SKU", "SlotTier", *fd_cols]
    demand = pd.read_csv(demand_path, usecols=usecols).drop_duplicates("SKU")
    if fd_cols:
        demand["Total14DayDemand"] = demand[fd_cols].sum(axis=1)
    else:
        demand["Total14DayDemand"] = 0
    return demand[["SKU", "SlotTier", "Total14DayDemand"]]


def _load_sku_ledger_fallback(sku_ledger_db: Path = SKU_LEDGER_DB) -> pd.Series:
    """Load historical SKU category families from the SKU ledger.

    Constructs a fallback SlotTier (suffix 'C') using product group and size.

    Args:
        sku_ledger_db: Database file path.

    Returns:
        pd.Series: Fallback SlotTier indexed by SKU.
    """
    if not sku_ledger_db.exists():
        return pd.Series(dtype="object")

    with sqlite3.connect(sku_ledger_db) as conn:
        ledger = pd.read_sql_query(
            """
            SELECT sku, product_group, size_group
            FROM sku_ledger
            WHERE product_group IS NOT NULL
              AND size_group IS NOT NULL
              AND TRIM(product_group) != ''
              AND TRIM(product_group) != 'U'
              AND TRIM(size_group) != ''
            """,
            conn,
        )

    if ledger.empty:
        return pd.Series(dtype="object")

    ledger["FallbackSlotTier"] = (
        ledger["product_group"].astype(str).str.strip()
        + ledger["size_group"].astype(str).str.strip()
        + "C"
    )
    return ledger.drop_duplicates("sku").set_index("sku")["FallbackSlotTier"]


def _merge_classification_fallback(
    df: pd.DataFrame,
    fallback_df: pd.DataFrame,
    key_col: str,
    prefix: str,
    source_name: str,
) -> tuple[pd.DataFrame, int]:
    """Fill blank hierarchy fields from an ingestion-style fallback table.

    Args:
        df: Target DataFrame to update.
        fallback_df: Fallback hierarchy DataFrame.
        key_col: Join column name.
        prefix: Column prefix for fallback keys.
        source_name: Source name descriptor.

    Returns:
        tuple[pd.DataFrame, int]: Updated DataFrame and count of recovered rows.
    """
    if fallback_df.empty:
        return df, 0

    rename = {field: f"{prefix}{field}" for field in HIERARCHY_FIELDS}
    df = df.merge(fallback_df.rename(columns=rename), on=key_col, how="left")

    missing_before = df["Division"].apply(is_blank_or_na)
    for base_col in HIERARCHY_FIELDS:
        fallback_col = f"{prefix}{base_col}"
        fill_mask = df[base_col].apply(is_blank_or_na) & ~df[fallback_col].apply(is_blank_or_na)
        df.loc[fill_mask, base_col] = df.loc[fill_mask, fallback_col]

    recovered = missing_before & ~df["Division"].apply(is_blank_or_na)
    df.loc[recovered, "HierarchyResolutionSource"] = source_name
    df = df.drop(columns=list(rename.values()), errors="ignore")
    return df, int(recovered.sum())


def _load_current_hierarchy_fallback(skus: pd.Series, context_label: str) -> pd.DataFrame:
    """Resolve forecast-missing live SKUs with ingestion hierarchy precedence.

    Resolves items by checking exact SKU, then Item-Color, then Item, and lastly
    checks AX product database hierarchies.

    Args:
        skus: Series of SKUs to resolve.
        context_label: Log label descriptor.

    Returns:
        pd.DataFrame: Resolved hierarchy DataFrame.
    """
    sku_values = sorted({str(sku).strip() for sku in skus.dropna() if str(sku).strip()})
    columns = [
        "SKU",
        "HierarchySlotTier",
        "HierarchyResolutionSource",
        "Division",
        "Department",
        "Class",
        "KeyCategoryView",
        "ProductGroupCode",
        "SizeGroupCode",
    ]
    if not sku_values:
        return pd.DataFrame(columns=columns)

    try:
        source_file = get_latest_source_file()
        df_hier, _df_status = read_product_attributes(source_file)
    except Exception as exc:
        print(f"    WARNING: Product Attributes hierarchy fallback failed ({exc})")
        df_hier = pd.DataFrame(columns=["SKU", *HIERARCHY_FIELDS, "Offer", "Item"])

    df = pd.DataFrame({"SKU": sku_values})
    parsed = df["SKU"].apply(parse_sku)
    df["Item"] = parsed.apply(lambda value: value[0])
    df["Color"] = parsed.apply(lambda value: value[1])
    df["Size"] = parsed.apply(lambda value: value[2])
    df["Offer"] = df["Item"] + "-" + df["Color"]

    df = df.merge(df_hier[["SKU", *HIERARCHY_FIELDS]], on="SKU", how="left")
    df["HierarchyResolutionSource"] = ""
    exact_mask = ~df["Division"].apply(is_blank_or_na)
    df.loc[exact_mask, "HierarchyResolutionSource"] = "ProductAttributesSKU"
    print(f"    Product Attributes exact SKU fallback resolved {int(exact_mask.sum()):,} {context_label} SKUs")

    offer_fallback = build_hierarchy_fallback(df_hier, "Offer", f"Offer-level {context_label}")
    df, offer_count = _merge_classification_fallback(
        df, offer_fallback, "Offer", "OfferFallback", "ProductAttributesItemColor"
    )
    print(f"    Product Attributes item-color fallback resolved {offer_count:,} {context_label} SKUs")

    item_fallback = build_hierarchy_fallback(df_hier, "Item", f"Item-level {context_label}")
    df, item_count = _merge_classification_fallback(
        df, item_fallback, "Item", "ItemFallback", "ProductAttributesItem"
    )
    print(f"    Product Attributes item fallback resolved {item_count:,} {context_label} SKUs")

    missing_before_ax = df["Division"].apply(is_blank_or_na)
    ax_candidates = int(missing_before_ax.sum())
    if ax_candidates:
        try:
            ax_hierarchy = read_ax_product_hierarchy(df.loc[missing_before_ax, "Item"].unique().tolist())
        except Exception as exc:
            print(f"    WARNING: AX product hierarchy fallback failed ({exc})")
            ax_hierarchy = pd.DataFrame()

        if not ax_hierarchy.empty:
            ax_fallback = ax_hierarchy.rename(
                columns={
                    "AXHierarchyDivision": "Division",
                    "AXHierarchyDepartment": "Department",
                    "AXHierarchyClass": "Class",
                    "AXHierarchyKeyCategoryView": "KeyCategoryView",
                }
            )
            ax_fallback["SizeGroup"] = ""
            ax_fallback["GoLiveDate"] = ""
            df, ax_count = _merge_classification_fallback(
                df,
                ax_fallback[["Item", *HIERARCHY_FIELDS]],
                "Item",
                "AXFallback",
                "AXHierarchy",
            )
            ax_remaining = int(df["Division"].apply(is_blank_or_na).sum())
            print(
                "    AX product hierarchy fallback resolved "
                f"{ax_count:,}/{ax_candidates:,} {context_label} SKUs "
                f"({ax_remaining:,} still unresolved)"
            )
        else:
            print(f"    AX product hierarchy fallback resolved 0/{ax_candidates:,} {context_label} SKUs")
    else:
        print("    AX product hierarchy fallback skipped: Product Attributes covered forecast-missing SKUs")

    resolved_mask = ~df["Division"].apply(is_blank_or_na)
    df.loc[resolved_mask, "ProductGroupCode"] = df.loc[resolved_mask, "Division"].apply(map_product_group_code)
    item_overrides = df["Item"].apply(product_group_override_for_item)
    df.loc[item_overrides.notna(), "ProductGroupCode"] = item_overrides[item_overrides.notna()]
    df.loc[resolved_mask, "SizeGroupCode"] = df.loc[resolved_mask, "Size"].apply(map_size_group)
    df.loc[resolved_mask, "HierarchySlotTier"] = (
        df.loc[resolved_mask, "ProductGroupCode"]
        + df.loc[resolved_mask, "SizeGroupCode"]
        + "C"
    )
    unresolved_count = int((~resolved_mask).sum())
    if unresolved_count:
        print(f"    Forecast-missing SKUs still uncategorized after current hierarchy checks: {unresolved_count:,}")

    return df[columns]


def _write_hierarchy_fallback_to_ledger(
    hierarchy_fallback: pd.DataFrame,
    *,
    persist: bool,
    sku_ledger_db: Path,
    source_prefix: str,
) -> None:
    """Persist trusted hierarchy fallback classifications when explicitly enabled.

    Args:
        hierarchy_fallback: Fallback classifications table.
        persist: If True, executes inserts/updates to the SQLite database.
        sku_ledger_db: SQLite ledger database file path.
        source_prefix: Source tag label.
    """
    if not persist:
        if hierarchy_fallback.empty:
            print("    SKU ledger hierarchy cache: no current hierarchy fallback rows to save")
        else:
            print(
                "    SKU ledger hierarchy cache: skipped for read-only validation "
                f"({len(hierarchy_fallback):,} candidate rows)"
            )
        return

    if hierarchy_fallback.empty:
        print("    SKU ledger hierarchy cache: no current hierarchy fallback rows to save")
        return

    eligible = hierarchy_fallback[
        hierarchy_fallback["HierarchySlotTier"].notna()
        & hierarchy_fallback["ProductGroupCode"].notna()
        & hierarchy_fallback["SizeGroupCode"].notna()
        & hierarchy_fallback["ProductGroupCode"].astype(str).str.strip().ne("")
        & hierarchy_fallback["ProductGroupCode"].astype(str).str.strip().ne("U")
        & hierarchy_fallback["SizeGroupCode"].astype(str).str.strip().ne("")
    ].copy()
    skipped = len(hierarchy_fallback) - len(eligible)
    if eligible.empty:
        print(f"    SKU ledger hierarchy cache: no eligible rows saved ({skipped:,} skipped)")
        return

    today = date.today().isoformat()
    source_name = f"{source_prefix}_{today}"

    def ledger_value(value: object) -> str | None:
        if pd.isna(value):
            return None
        text = str(value).strip()
        return text or None

    rows = [
        (
            ledger_value(row.SKU),
            ledger_value(row.ProductGroupCode),
            ledger_value(row.SizeGroupCode),
            ledger_value(row.Division),
            ledger_value(row.Department),
            ledger_value(row.Class),
            today,
            today,
            source_name,
        )
        for row in eligible.itertuples(index=False)
    ]

    conn = init_db(sku_ledger_db)
    try:
        count_before = conn.execute("SELECT COUNT(*) FROM sku_ledger").fetchone()[0]
        conn.executemany(UPSERT_SQL, rows)
        conn.commit()
        count_after = conn.execute("SELECT COUNT(*) FROM sku_ledger").fetchone()[0]
    finally:
        conn.close()

    print(
        "    SKU ledger hierarchy cache saved "
        f"{len(rows):,} rows ({count_after - count_before:,} new, {skipped:,} skipped)"
    )


def _add_hierarchy_context(sql_df: pd.DataFrame, hierarchy_fallback: pd.DataFrame) -> pd.DataFrame:
    """Insert fallback category fields back into the main live inventory DataFrame.

    Args:
        sql_df: Input raw live inventory records.
        hierarchy_fallback: Table containing parsed hierarchies.

    Returns:
        pd.DataFrame: DataFrame populated with hierarchy context columns.
    """
    if hierarchy_fallback.empty:
        for hierarchy_col in ["Division", "Department", "Class", "KeyCategoryView", "ProductGroupCode", "SizeGroupCode"]:
            sql_df[f"Hierarchy{hierarchy_col}"] = ""
        return sql_df

    indexed = hierarchy_fallback.set_index("SKU")
    for hierarchy_col in ["Division", "Department", "Class", "KeyCategoryView", "ProductGroupCode", "SizeGroupCode"]:
        sql_df[f"Hierarchy{hierarchy_col}"] = sql_df["SKU"].map(indexed[hierarchy_col]).fillna("")
    return sql_df


def classify_live_inventory(
    sql_df: pd.DataFrame,
    demand_path: Path,
    *,
    proposed_map: pd.DataFrame | None = None,
    sku_ledger_db: Path = SKU_LEDGER_DB,
    persist_hierarchy_fallback: bool = False,
    source_prefix: str = "LiveInventoryHierarchyFallback",
    context_label: str = "forecast-missing",
) -> pd.DataFrame:
    """Classify live picking inventory into SlotTiers and optional map zones.

    Filters out wholesale location entries, resolves SlotTiers sequentially via
    demand forecasts, historical ledger cache, and current hierarchy attributes.

    Args:
        sql_df: DataFrame representing live DC inventory records.
        demand_path: Path to the FwdDemand CSV file.
        proposed_map: Optional proposed map DataFrame.
        sku_ledger_db: Sku ledger cache path.
        persist_hierarchy_fallback: If True, writes resolved fallbacks to ledger.
        source_prefix: Logging source tag prefix.
        context_label: Category resolver descriptor.

    Returns:
        pd.DataFrame: Categorized unique SKU records.
    """
    demand = _load_forward_demand(demand_path)
    sku_to_tier = demand.set_index("SKU")["SlotTier"]
    sku_to_demand = demand.set_index("SKU")["Total14DayDemand"]

    df = sql_df.copy()
    wholesale_mask = (
        df["LocProfile"].astype(str).str.upper().isin(WHOLESALE_LOCATION_CODES)
        | df["ZoneId"].astype(str).str.upper().isin(WHOLESALE_LOCATION_CODES)
    )
    wholesale_rows = int(wholesale_mask.sum())
    print(f"    W001/Wholesale rows excluded from capacity fit: {wholesale_rows:,}")
    if wholesale_rows:
        df = df[~wholesale_mask].copy()

    df["SittingDays"] = pd.to_numeric(df.get("SittingDays", 0), errors="coerce").fillna(0).astype(int)
    df["Reserved"] = pd.to_numeric(df.get("Reserved", 0), errors="coerce").fillna(0)
    df["Total14DayDemand"] = df["SKU"].map(sku_to_demand).fillna(0)

    ledger_to_tier = _load_sku_ledger_fallback(sku_ledger_db)
    forecast_tier = df["SKU"].map(sku_to_tier)
    ledger_tier = df["SKU"].map(ledger_to_tier)
    hierarchy_candidate_mask = forecast_tier.isna() & ledger_tier.isna()
    hierarchy_fallback = _load_current_hierarchy_fallback(df.loc[hierarchy_candidate_mask, "SKU"], context_label)
    _write_hierarchy_fallback_to_ledger(
        hierarchy_fallback,
        persist=persist_hierarchy_fallback,
        sku_ledger_db=sku_ledger_db,
        source_prefix=source_prefix,
    )

    hierarchy_index = hierarchy_fallback.dropna(subset=["HierarchySlotTier"]).set_index("SKU")
    hierarchy_to_tier = hierarchy_index["HierarchySlotTier"] if not hierarchy_index.empty else pd.Series(dtype="object")
    hierarchy_to_source = (
        hierarchy_fallback.set_index("SKU")["HierarchyResolutionSource"]
        if not hierarchy_fallback.empty
        else pd.Series(dtype="object")
    )
    hierarchy_tier = df["SKU"].map(hierarchy_to_tier)
    hierarchy_source = df["SKU"].map(hierarchy_to_source)

    raw_tier = forecast_tier.fillna(ledger_tier).fillna(hierarchy_tier)
    df["ResolutionSource"] = "ForecastCSV"
    ledger_mask = forecast_tier.isna() & ledger_tier.notna()
    hierarchy_mask = forecast_tier.isna() & ledger_tier.isna() & hierarchy_tier.notna()
    df.loc[ledger_mask, "ResolutionSource"] = "SkuLedgerCategory"
    df.loc[hierarchy_mask, "ResolutionSource"] = hierarchy_source[hierarchy_mask]
    df.loc[raw_tier.isna(), "ResolutionSource"] = "Uncategorized"
    df["ClassificationSlotTier"] = raw_tier.fillna("")
    df = _add_hierarchy_context(df, hierarchy_fallback)

    if proposed_map is not None:
        available_zones, zone_families = build_tier_to_zone_map(proposed_map)
        df["ProposedSlotTier"] = df["ClassificationSlotTier"].apply(
            lambda tier: resolve_zone(tier, available_zones, zone_families)
        )
    else:
        df["ProposedSlotTier"] = df["ClassificationSlotTier"].replace("", "Unmapped")

    df["_ProtectRank"] = (
        df["Online"].eq("Yes").astype(int) * 2
        + df["Reserved"].gt(0).astype(int)
    )
    unique_skus = (
        df.sort_values(["_ProtectRank", "Online"], ascending=[False, False])
        .drop_duplicates(subset=["SKU"])
        .drop(columns=["_ProtectRank"])
    )

    print(f"    Total Unique SKUs On-Hand in Picking: {len(unique_skus):,}")
    print("    Classification source:")
    for source, count in unique_skus["ResolutionSource"].value_counts().items():
        print(f"      {source:<24s} {count:>6,}")
    uncategorized_count = int(unique_skus["ResolutionSource"].eq("Uncategorized").sum())
    print(f"    SKUs with no category fallback: {uncategorized_count:,}")
    return unique_skus
