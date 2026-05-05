"""
Menu Engineering / Boston Matrix evaluation.

Reads backend/sample_data/orders_2022.xlsx, replicates the production
classification + price-simulation logic from backend/routes_menu.py
WITHOUT importing it (the module pulls FastAPI/auth deps that don't
matter here), and writes diagnostic CSVs/plots into backend/eval/.

Real-day-only constraint: imputed rows are dropped before any
classification or price math, matching the production endpoint
which calls load_data(..., include_synthetic=False).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "sample_data" / "orders_2022.xlsx"
OUT = ROOT / "eval"
OUT.mkdir(exist_ok=True)


# ─── replicated from routes_menu.py (kept verbatim so any drift surfaces) ───

ELASTICITY_BY_CATEGORY: dict[str, float] = {
    "espresso drinks": -0.5, "espresso": -0.5, "specialty coffee": -0.5, "coffee": -0.5,
    "hot coffee": -0.5, "americano": -0.5, "filter coffee": -0.6, "drip coffee": -0.6,
    "saudi coffee": -0.4, "turkish coffee": -0.6, "arabic coffee": -0.4,
    "hot drinks": -0.7, "hot beverages": -0.7, "tea": -0.6, "hot tea": -0.6,
    "herbal tea": -0.8, "matcha": -0.9, "hot chocolate": -0.8, "chocolate drinks": -0.8,
    "cold coffee drinks": -0.8, "iced coffee": -0.8, "cold coffee": -0.8,
    "frappe": -1.0, "frappes": -1.0, "frappuccino": -1.0,
    "cold drinks": -1.0, "cold beverages": -1.0, "beverages": -1.0, "drinks": -1.0,
    "soft drinks": -1.1, "sodas": -1.1, "soda": -1.1,
    "juices": -1.1, "juice": -1.1, "fresh juice": -1.0,
    "smoothies": -1.2, "milkshakes": -1.2, "shakes": -1.2,
    "water": -0.3, "mineral water": -0.3, "bottled water": -0.3,
    "energy drinks": -1.0, "mocktails": -1.3,
    "bakery": -1.0, "bread": -0.9, "breads": -0.9, "baked goods": -1.0,
    "pastries": -1.2, "pastry": -1.2, "croissants": -1.2, "croissant": -1.2,
    "muffins": -1.3, "muffin": -1.3, "scones": -1.2, "donuts": -1.3, "bagels": -1.1,
    "sweets": -1.8, "desserts": -1.8, "dessert": -1.8,
    "cakes": -1.7, "cake": -1.7, "cheesecakes": -1.7, "cheesecake": -1.7,
    "cupcakes": -1.6, "pies": -1.6, "tarts": -1.6,
    "ice cream": -1.6, "gelato": -1.6, "frozen desserts": -1.6, "sorbet": -1.6,
    "hot sweets": -1.6, "waffles": -1.6, "waffle": -1.6, "pancakes": -1.6,
    "crepes": -1.6, "french toast": -1.5,
    "cookies": -1.5, "brownies": -1.5, "puddings": -1.6, "custards": -1.6,
    "candy": -1.7, "chocolates": -1.7, "confectionery": -1.7,
    "savory": -1.3, "mains": -1.3, "main course": -1.3, "entrees": -1.3, "meals": -1.3,
    "sandwiches": -1.1, "sandwich": -1.1, "paninis": -1.1, "panini": -1.1,
    "wraps": -1.1, "wrap": -1.1, "subs": -1.1,
    "burgers": -1.2, "burger": -1.2, "sliders": -1.3,
    "pizza": -1.3, "pizzas": -1.3, "flatbread": -1.3,
    "pasta": -1.4, "pastas": -1.4, "noodles": -1.3,
    "salads": -1.5, "salad": -1.5,
    "soups": -1.0, "soup": -1.0, "broths": -1.0,
    "breakfast": -0.9, "brunch": -1.0, "breakfast items": -0.9,
    "appetizers": -1.6, "starters": -1.6, "sides": -1.4, "finger food": -1.5,
    "steaks": -1.5, "steak": -1.5, "grills": -1.5, "grill": -1.5, "grilled": -1.5,
    "bbq": -1.6, "barbecue": -1.6,
    "seafood": -1.7, "fish": -1.6, "shrimp": -1.8, "lobster": -2.0, "crab": -1.9,
    "chicken": -1.2, "poultry": -1.2, "wings": -1.3,
    "beef": -1.4, "lamb": -1.4, "meat": -1.3,
    "sushi": -2.0, "japanese": -1.8, "ramen": -1.4,
    "asian": -1.5, "chinese": -1.3, "thai": -1.4, "korean": -1.5, "vietnamese": -1.3,
    "italian": -1.4, "french": -1.8,
    "mexican": -1.4, "tex-mex": -1.4, "latin": -1.4,
    "middle eastern": -1.1, "arabic": -1.1, "arabic food": -1.1, "saudi": -1.0,
    "shawarma": -0.9, "falafel": -0.9, "kebabs": -1.0, "kebab": -1.0,
    "shish": -1.0, "mandi": -1.0, "kabsa": -1.0, "mandy": -1.0, "madfoon": -1.0,
    "indian": -1.3, "curry": -1.3, "biryani": -1.2,
    "mediterranean": -1.2, "greek": -1.3, "lebanese": -1.1, "turkish": -1.2,
    "kids menu": -0.8, "kids": -0.8, "children's menu": -0.8,
    "catering": -1.5, "combos": -1.0, "meal deals": -1.0, "family meals": -0.9,
    "healthy": -1.3, "vegan": -1.5, "vegetarian": -1.3, "gluten-free": -1.4,
}
DEFAULT_ELASTICITY = -1.5


def elasticity_for(category: str | None) -> tuple[float, str]:
    if not category:
        return DEFAULT_ELASTICITY, f"unknown→default ({DEFAULT_ELASTICITY})"
    key = str(category).strip().lower()
    if key in ELASTICITY_BY_CATEGORY:
        return ELASTICITY_BY_CATEGORY[key], f"{category}→{ELASTICITY_BY_CATEGORY[key]}"
    for known_key, value in ELASTICITY_BY_CATEGORY.items():
        if known_key in key or key in known_key:
            return value, f"{category}~'{known_key}'→{value}"
    return DEFAULT_ELASTICITY, f"{category}→default ({DEFAULT_ELASTICITY})"


def classify(pop: float, margin: float, avg_pop: float, avg_margin: float) -> str:
    hi_pop = pop >= avg_pop
    hi_mar = margin >= avg_margin
    if hi_pop and hi_mar: return "Star"
    if hi_pop and not hi_mar: return "Plowhorse"
    if not hi_pop and hi_mar: return "Puzzle"
    return "Dog"


def project_qty_float(q0: float, p0: float, p1: float, e: float) -> float:
    if p0 <= 0:
        return float(q0)
    return max(0.0, float(q0) * (p1 / p0) ** e)


def optimal_price(cost: float, e: float, current_price: float, classification: str | None = None) -> dict:
    """Direct port of routes_menu._optimal_price."""
    SAFE_UP_CAP = 0.20
    SAFE_DOWN_CAP = 0.20

    if classification == "Dog":
        suggested = max(int(math.floor(current_price * 0.90)),
                        int(math.ceil(cost * 1.20)))
        if suggested >= int(round(current_price)):
            suggested = int(round(current_price)) - 1
        suggested = max(suggested, int(math.ceil(cost * 1.05)))
        return {"price": suggested, "kind": "dog_discount", "inelastic": e > -1.0}

    if classification == "Puzzle":
        suggested = max(int(math.floor(current_price * 0.92)),
                        int(math.ceil(cost * 1.30)))
        if suggested >= int(round(current_price)):
            suggested = int(round(current_price)) - 1
        return {"price": suggested, "kind": "puzzle_discount", "inelastic": e > -1.0}

    if e > -1.05:
        test_price = int(math.ceil(current_price * 1.10))
        if test_price <= int(round(current_price)):
            test_price = int(round(current_price)) + 1
        return {"price": test_price, "kind": "test_increase", "inelastic": True}

    theoretical = e * cost / (1 + e)
    safe_max = current_price * (1 + SAFE_UP_CAP)
    safe_min = current_price * (1 - SAFE_DOWN_CAP)
    if theoretical > safe_max:
        return {"price": int(round(safe_max)), "theoreticalPrice": int(round(theoretical)),
                "kind": "capped_up", "inelastic": False}
    elif theoretical < safe_min:
        return {"price": int(round(safe_min)), "theoreticalPrice": int(round(theoretical)),
                "kind": "capped_down", "inelastic": False}
    else:
        return {"price": int(round(theoretical)), "theoreticalPrice": int(round(theoretical)),
                "kind": "direct", "inelastic": False}


# ─────────────────────────── load + per-item table ───────────────────────────

def build_items() -> tuple[pd.DataFrame, float, float]:
    df = pd.read_excel(DATA)
    real = df[~df.is_imputed].copy()  # production filter

    # The endpoint groups by Product and uses .first() for unit_price / unit_cost.
    # Replicate exactly.
    items = real.groupby("name").agg(
        category=("categ_EN", "first"),
        qtySold=("quantity", "sum"),
        revenue=("total_price", "sum"),
        totalCost=("total_cost", "sum"),
        price=("unit_price", "first"),
        cost=("unit_cost", "first"),
    ).reset_index().rename(columns={"name": "name"})

    items["profit"] = items["revenue"] - items["totalCost"]
    items["profitMargin"] = items["profit"] / items["revenue"] * 100
    total_qty = items["qtySold"].sum()
    items["popularity"] = items["qtySold"] / total_qty * 100

    avg_pop = items["popularity"].mean()
    avg_margin = items["profitMargin"].mean()
    items["classification"] = items.apply(
        lambda r: classify(r["popularity"], r["profitMargin"], avg_pop, avg_margin),
        axis=1,
    )
    items["elasticity"], items["elasticitySource"] = zip(
        *items["category"].map(elasticity_for)
    )
    return items, avg_pop, avg_margin


def main():
    items, avg_pop, avg_margin = build_items()
    print(f"items: {len(items)}  avg_pop={avg_pop:.4f}%  avg_margin={avg_margin:.2f}%")

    # ── Save the fully-classified item table (the source of truth) ───────
    cols = [
        "name", "category", "qtySold", "revenue", "profit", "profitMargin",
        "price", "cost", "popularity", "classification",
        "elasticity", "elasticitySource",
    ]
    items[cols].sort_values(["classification", "qtySold"], ascending=[True, False]) \
        .to_csv(OUT / "menu_items_classified.csv", index=False)

    # ── Quadrant counts + revenue share ──────────────────────────────────
    quadrants = items.groupby("classification").agg(
        count=("name", "size"),
        qty=("qtySold", "sum"),
        revenue=("revenue", "sum"),
        profit=("profit", "sum"),
        med_popularity=("popularity", "median"),
        med_margin=("profitMargin", "median"),
    ).round(2)
    quadrants["count_share_%"] = (quadrants["count"] / quadrants["count"].sum() * 100).round(1)
    quadrants["revenue_share_%"] = (quadrants["revenue"] / quadrants["revenue"].sum() * 100).round(1)
    quadrants.to_csv(OUT / "menu_quadrants.csv")
    print("\nquadrants:")
    print(quadrants)

    # ── Methodology check: where do mean vs median cutoffs disagree? ─────
    median_pop = items["popularity"].median()
    median_margin = items["profitMargin"].median()

    items["pop_above_mean"] = items["popularity"] >= avg_pop
    items["pop_above_median"] = items["popularity"] >= median_pop
    items["margin_above_mean"] = items["profitMargin"] >= avg_margin
    items["margin_above_median"] = items["profitMargin"] >= median_margin

    items["classification_median_cutoffs"] = items.apply(
        lambda r: classify(r["popularity"], r["profitMargin"], median_pop, median_margin),
        axis=1,
    )
    disagree = items[items["classification"] != items["classification_median_cutoffs"]]
    disagree[["name", "category", "popularity", "profitMargin",
              "classification", "classification_median_cutoffs"]] \
        .to_csv(OUT / "menu_mean_vs_median_disagreement.csv", index=False)
    print(f"\nclassification flips when switching mean→median cutoffs: {len(disagree)}/{len(items)}")
    print(f"  mean popularity = {avg_pop:.4f}%   median popularity = {median_pop:.4f}%")
    print(f"  mean margin     = {avg_margin:.2f}%   median margin     = {median_margin:.2f}%")

    # ── Boundary items: within ±2 percentage points of either cutoff ─────
    items["pop_dist_pp"] = (items["popularity"] - avg_pop).round(4)
    items["margin_dist_pp"] = (items["profitMargin"] - avg_margin).round(4)

    pop_band_pp = 0.20  # popularity is roughly 0–10%, ±0.2 pp is meaningful
    margin_band_pp = 2.0

    boundary = items[
        (items["pop_dist_pp"].abs() <= pop_band_pp)
        | (items["margin_dist_pp"].abs() <= margin_band_pp)
    ].copy()
    boundary[["name", "category", "popularity", "profitMargin",
              "pop_dist_pp", "margin_dist_pp", "classification"]] \
        .sort_values(["classification", "name"]) \
        .to_csv(OUT / "menu_boundary_items.csv", index=False)
    print(f"\nboundary items (within ±{pop_band_pp}pp pop OR ±{margin_band_pp}pp margin): {len(boundary)}")

    # ── Boston Matrix scatter plot ───────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, 8))
    color = {"Star": "#2ca02c", "Plowhorse": "#1f77b4", "Puzzle": "#ff7f0e", "Dog": "#d62728"}
    for cls, sub in items.groupby("classification"):
        ax.scatter(sub["popularity"], sub["profitMargin"], s=24, alpha=0.75,
                   color=color[cls], label=f"{cls} (n={len(sub)})")
    ax.axvline(avg_pop, ls="--", color="grey", lw=1, label=f"mean pop = {avg_pop:.2f}%")
    ax.axhline(avg_margin, ls="--", color="grey", lw=1, label=f"mean margin = {avg_margin:.1f}%")
    ax.axvline(median_pop, ls=":", color="black", lw=1, label=f"median pop = {median_pop:.2f}%")
    ax.axhline(median_margin, ls=":", color="black", lw=1, label=f"median margin = {median_margin:.1f}%")
    ax.set_xlabel("Popularity (% of total qty)")
    ax.set_ylabel("Profit margin (%)")
    ax.set_title("Boston Matrix — real-POS-day data only")
    ax.set_xscale("symlog", linthresh=0.05)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(OUT / "menu_boston_matrix.png", dpi=130)
    plt.close(fig)

    # ── Distribution histograms (popularity + margin) ────────────────────
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.5))
    a1.hist(items["popularity"], bins=40, color="#1f77b4", alpha=0.85)
    a1.axvline(avg_pop, ls="--", color="red", label=f"mean = {avg_pop:.2f}%")
    a1.axvline(median_pop, ls=":", color="black", label=f"median = {median_pop:.2f}%")
    a1.set_title("Per-item popularity distribution")
    a1.set_xlabel("popularity (%)"); a1.set_ylabel("# items"); a1.legend()
    a1.set_xscale("symlog", linthresh=0.05)

    a2.hist(items["profitMargin"], bins=40, color="#2ca02c", alpha=0.85)
    a2.axvline(avg_margin, ls="--", color="red", label=f"mean = {avg_margin:.1f}%")
    a2.axvline(median_margin, ls=":", color="black", label=f"median = {median_margin:.1f}%")
    a2.set_title("Per-item profit-margin distribution")
    a2.set_xlabel("profit margin (%)"); a2.set_ylabel("# items"); a2.legend()
    plt.tight_layout()
    plt.savefig(OUT / "menu_distributions.png", dpi=130)
    plt.close(fig)

    # ── Elasticity coverage: which categories used a fallback? ───────────
    cat_summary = items.groupby("category").agg(
        n_items=("name", "size"),
        elasticity=("elasticity", "first"),
        source=("elasticitySource", "first"),
    ).reset_index()
    cat_summary.to_csv(OUT / "menu_elasticity_by_category.csv", index=False)
    print("\nelasticity coverage per category:")
    print(cat_summary.to_string(index=False))

    # ── Constant-elasticity textbook check ───────────────────────────────
    # For a +10% price change, textbook says new_qty ≈ q0 * 1.10^e.
    rng = np.random.default_rng(0)
    samples = items.sample(min(8, len(items)), random_state=0).copy()
    rows = []
    for _, r in samples.iterrows():
        p0, q0, e = float(r["price"]), int(r["qtySold"]), float(r["elasticity"])
        for delta_pct in (-20, -10, +10, +20, +30):
            p1 = p0 * (1 + delta_pct / 100)
            our_qty = project_qty_float(q0, p0, p1, e)
            textbook_qty = q0 * (1 + delta_pct / 100) ** e
            rows.append({
                "name": r["name"], "category": r["category"], "elasticity": e,
                "p0": round(p0, 2), "delta_pct": delta_pct, "p1": round(p1, 2),
                "qty0": q0, "our_qty_float": round(our_qty, 4),
                "textbook_qty": round(textbook_qty, 4),
                "abs_diff": round(abs(our_qty - textbook_qty), 6),
            })
    pd.DataFrame(rows).to_csv(OUT / "menu_simulator_textbook_check.csv", index=False)

    # ── Optimal-price suggestions, item by item ──────────────────────────
    sugg_rows = []
    for _, r in items.iterrows():
        e = float(r["elasticity"])
        cls = r["classification"]
        cur_p = float(r["price"]); cur_c = float(r["cost"]); cur_q = int(r["qtySold"])
        cur_margin = (cur_p - cur_c) / cur_p * 100 if cur_p > 0 else 0
        rec = optimal_price(cur_c, e, cur_p, cls)
        sp = float(rec["price"])
        # Project profit at the suggested price using THE SAME constant-elasticity
        # math the production simulator uses, so we can flag suggestions that
        # actively LOWER profit.
        new_q = project_qty_float(cur_q, cur_p, sp, e)
        new_profit = new_q * (sp - cur_c)
        cur_profit = cur_q * (cur_p - cur_c)
        delta_profit = new_profit - cur_profit
        delta_pct = (sp - cur_p) / cur_p * 100 if cur_p > 0 else 0
        sugg_rows.append({
            "name": r["name"], "category": r["category"], "classification": cls,
            "elasticity": e, "current_price": round(cur_p, 2),
            "current_cost": round(cur_c, 2), "current_margin_pct": round(cur_margin, 2),
            "current_qty": cur_q, "current_profit": round(cur_profit, 2),
            "suggested_price": sp, "suggested_kind": rec["kind"],
            "theoretical_price": rec.get("theoreticalPrice"),
            "price_change_pct": round(delta_pct, 2),
            "projected_qty": round(new_q, 2),
            "projected_profit": round(new_profit, 2),
            "profit_delta": round(delta_profit, 2),
            "profit_delta_pct": round(delta_profit / cur_profit * 100, 2) if cur_profit else None,
        })
    sugg = pd.DataFrame(sugg_rows)
    sugg.sort_values("profit_delta", inplace=True)
    sugg.to_csv(OUT / "menu_price_suggestions.csv", index=False)

    # ── Suggestions that REDUCE projected profit ─────────────────────────
    bad = sugg[sugg["profit_delta"] < -1].copy()
    bad.to_csv(OUT / "menu_price_suggestions_profit_negative.csv", index=False)
    print(f"\nsuggestions that reduce projected profit (>1 SAR loss): {len(bad)}/{len(sugg)}")
    if len(bad):
        print(bad[["name", "classification", "elasticity", "current_price",
                   "suggested_price", "profit_delta"]].head(15).to_string(index=False))

    # ── Discount-floor bug evidence ──────────────────────────────────────
    # Dog uses floor(p * 0.90); Puzzle uses floor(p * 0.92).
    # Show the realized vs intended discount on Dogs and Puzzles.
    dp = sugg[sugg["classification"].isin(["Dog", "Puzzle"])].copy()
    dp["intended_change_pct"] = dp["classification"].map({"Dog": -10.0, "Puzzle": -8.0})
    dp["realized_change_pct"] = dp["price_change_pct"]
    dp["overshoot_pp"] = (dp["realized_change_pct"] - dp["intended_change_pct"]).round(2)
    dp[["name", "category", "classification", "current_price",
        "suggested_price", "intended_change_pct", "realized_change_pct",
        "overshoot_pp"]].to_csv(OUT / "menu_discount_floor_bug.csv", index=False)
    print("\nsample Dog/Puzzle discount overshoot rows:")
    print(dp[["name", "classification", "current_price", "suggested_price",
              "intended_change_pct", "realized_change_pct"]].head(12).to_string(index=False))

    # ── Imputed vs real-day check (sanity: are we really excluding?) ─────
    df_all = pd.read_excel(DATA)
    df_imp = df_all[df_all.is_imputed]
    real_share_qty = (df_all.loc[~df_all.is_imputed, "quantity"].sum()
                      / df_all["quantity"].sum())
    print(f"\nreal-day share of total qty: {real_share_qty:.3f} "
          f"(imputed-day share: {1 - real_share_qty:.3f})")

    # Also produce a "what if we mistakenly used imputed days" comparison
    # for popularity rankings — to quantify the reviewer's concern.
    full = df_all.groupby("name").agg(
        category=("categ_EN", "first"),
        qtySold=("quantity", "sum"),
        revenue=("total_price", "sum"),
        totalCost=("total_cost", "sum"),
        price=("unit_price", "first"),
        cost=("unit_cost", "first"),
    ).reset_index()
    full["popularity"] = full["qtySold"] / full["qtySold"].sum() * 100
    full["margin"] = (full["revenue"] - full["totalCost"]) / full["revenue"] * 100
    avg_pop_full = full["popularity"].mean()
    avg_mar_full = full["margin"].mean()
    full["classification_with_imputed"] = full.apply(
        lambda r: classify(r["popularity"], r["margin"], avg_pop_full, avg_mar_full),
        axis=1,
    )
    cmp = items[["name", "classification"]].rename(columns={"classification": "real_only"}) \
        .merge(full[["name", "classification_with_imputed"]], on="name", how="left")
    diff = cmp[cmp["real_only"] != cmp["classification_with_imputed"]]
    diff.to_csv(OUT / "menu_imputed_drift.csv", index=False)
    print(f"items whose classification flips if imputed days are mixed in: {len(diff)}")


if __name__ == "__main__":
    main()
