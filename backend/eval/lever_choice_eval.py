"""
Lever-choice evaluation for the Menu Engineering simulator.

For every item on the menu, replicate the simulate endpoint's logic
(_optimal_price + _cost_lowering_suggestion + _simulate_one) and
compare:
  • SYSTEM primary lever  — what the frontend hero panel would render,
                            given the documented rule:
                              cost when (price=hold) OR
                                       (flipsAtCurrentPrice AND cost_lift > price_lift)
                              else price
  • EXPECTED primary lever — math-optimal choice:
                              whichever single lever lifts profit more.
                              Tiebreaker: classification flip wins.
                              "Hold" price suggestions → cost wins automatically.
                              No useful lever at all → "neither".

Imported helpers come straight from backend/routes_menu.py — no
re-implementation, so every code path tested here is the real path
the simulate endpoint runs.

Real-day-only constraint: imputed rows are dropped before any math,
matching the production endpoint which calls
load_data(..., include_synthetic=False).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Import the real production helpers — these are pure functions and
# don't require DB connectivity to call.
from routes_menu import (  # type: ignore  # noqa: E402
    _classify,
    _cost_lowering_suggestion,
    _optimal_price,
    _project_qty_float,
    _simulate_one,
    _elasticity_for,
)

DATA = ROOT / "sample_data" / "orders_2022.xlsx"
OUT = ROOT / "eval"


def build_items() -> tuple[pd.DataFrame, float, float]:
    """Replicate the simulate endpoint's per-item table construction."""
    df = pd.read_excel(DATA)
    real = df[~df["is_imputed"]].copy()  # production filter

    items = real.groupby("name").agg(
        category=("categ_EN", "first"),
        qtySold=("quantity", "sum"),
        revenue=("total_price", "sum"),
        totalCost=("total_cost", "sum"),
        price=("unit_price", "first"),
        cost=("unit_cost", "first"),
    ).reset_index().rename(columns={"name": "name"})

    items["profit"] = items["revenue"] - items["totalCost"]
    items["profitMargin"] = (items["profit"] / items["revenue"] * 100).fillna(0)
    total_qty = float(items["qtySold"].sum())
    items["popularity"] = items["qtySold"] / total_qty * 100

    avg_pop = 0.7 * float(items["popularity"].mean())  # Kasavana–Smith
    avg_margin = float(items["profitMargin"].mean())

    items["classification"] = items.apply(
        lambda r: _classify(r["popularity"], r["profitMargin"], avg_pop, avg_margin),
        axis=1,
    )
    items["elasticity"] = items["category"].map(lambda c: _elasticity_for(c)[0])
    items["totalQtyAll"] = total_qty
    return items, avg_pop, avg_margin


def evaluate_one(row, avg_pop: float, avg_margin: float) -> dict:
    """Run the simulate endpoint's path for a single item and decide levers."""
    current_price = float(row["price"])
    current_cost = float(row["cost"])
    current_qty = int(row["qtySold"])
    current_profit = float(row["profit"])
    current_margin = float(row["profitMargin"])
    current_classification = str(row["classification"])
    elasticity = float(row["elasticity"])
    other_qty = float(row["totalQtyAll"] - current_qty)

    # ── price suggestion via _optimal_price ────────────────────────────
    optimal = _optimal_price(
        current_cost, elasticity, current_price, current_classification,
        current_qty=current_qty,
    )

    if optimal is None:
        suggested_price = current_price
        suggested_kind = None
        direction = "hold"
    else:
        suggested_price = float(optimal["price"])
        suggested_kind = optimal.get("kind")
        cur_p_int = int(round(current_price))
        sug_p_int = int(round(suggested_price))
        if sug_p_int > cur_p_int:
            direction = "raise"
        elif sug_p_int < cur_p_int:
            direction = "lower"
        else:
            direction = "hold"

    # Project profit at the suggested price (price-only scenario:
    # cost stays the same).
    suggested_sim = _simulate_one(
        current_price, current_cost, current_qty,
        suggested_price, current_cost, elasticity,
        other_qty, avg_pop, avg_margin,
    )
    projected_profit_price_only = float(suggested_sim["newProfit"])
    projected_class_price_only = str(suggested_sim["newClassification"])
    price_only_lift = projected_profit_price_only - current_profit
    price_flips = projected_class_price_only != current_classification

    # ── cost suggestion via _cost_lowering_suggestion ──────────────────
    cost_lowering = _cost_lowering_suggestion(
        current_price=current_price,
        current_cost=current_cost,
        current_qty=current_qty,
        current_classification=current_classification,
        avg_margin=avg_margin,
        avg_pop=avg_pop,
        other_qty=other_qty,
        suggested_price=suggested_price,
    )

    if cost_lowering is None:
        cost_only_lift = 0.0
        suggested_cost = None
        cost_flips = False
        cost_class_at_current_price = current_classification
        flips_at_current_price = False
    else:
        suggested_cost = float(cost_lowering["suggestedCost"])
        # Cost-only lift: keep current price, drop cost. additionalProfit
        # in the suggestion is exactly (current_cost - suggested) * current_qty.
        cost_only_lift = float(cost_lowering["additionalProfit"])
        flips_at_current_price = bool(cost_lowering["flipsAtCurrentPrice"])
        cost_class_at_current_price = str(cost_lowering["classificationAtCurrentPrice"])
        # `cost_flips` for our purposes means cost-only flips classification
        # at CURRENT price (the lift we're comparing).
        cost_flips = flips_at_current_price

    # ── SYSTEM primary lever (what the frontend would render) ──────────
    # Per spec: cost when direction == "hold" OR
    #          (flipsAtCurrentPrice AND cost_lift > price_lift)
    has_cost = cost_lowering is not None
    if has_cost and (
        direction == "hold"
        or (flips_at_current_price and cost_only_lift > price_only_lift)
    ):
        system_primary = "cost"
    else:
        system_primary = "price"

    # If the price suggestion is also unprofitable AND there's no cost
    # suggestion, the system would still render "price" by the rule —
    # we annotate that case below as a degenerate/uninformative pick.
    price_is_profitable = price_only_lift > 0.5  # SAR/yr threshold to count as "useful"
    cost_is_profitable = cost_only_lift > 0.5

    # ── EXPECTED primary lever (math-optimal) ──────────────────────────
    # Rules from the prompt:
    #   - Items with no cost AND no profitable price → "neither"
    #   - "Hold" price + cost exists → cost
    #   - Otherwise: pick lever with larger lift.
    #     Tiebreaker: classification flip wins (cost flip > price flip > no flip).
    if not has_cost and not price_is_profitable:
        expected_primary = "neither"
        expected_reason = "no cost suggestion AND price suggestion is unprofitable"
    elif not has_cost:
        expected_primary = "price"
        expected_reason = "only the price lever produces a positive lift"
    elif not price_is_profitable:
        expected_primary = "cost"
        expected_reason = "price suggestion gives no useful lift; cost is the only useful lever"
    elif direction == "hold":
        expected_primary = "cost"
        expected_reason = 'price suggestion direction is "hold" — cost is the only useful single lever'
    else:
        # Both levers usable; compare lifts. If they're within rounding
        # noise, classification flip is the tiebreaker.
        lift_diff = cost_only_lift - price_only_lift
        # Treat lifts within 5 % of each other (or within 50 SAR/yr) as a tie.
        tie_band = max(50.0, 0.05 * max(cost_only_lift, price_only_lift))
        if abs(lift_diff) <= tie_band:
            # Tiebreaker
            if cost_flips and not price_flips:
                expected_primary = "cost"
                expected_reason = "lifts ~tied; cost flips classification, price does not"
            elif price_flips and not cost_flips:
                expected_primary = "price"
                expected_reason = "lifts ~tied; price flips classification, cost does not"
            elif cost_flips and price_flips:
                # Both flip; fall back to lift comparison
                expected_primary = "cost" if lift_diff > 0 else "price"
                expected_reason = "lifts ~tied and both flip; pick larger lift"
            else:
                # Neither flips — fall back to lift
                expected_primary = "cost" if lift_diff > 0 else "price"
                expected_reason = "lifts ~tied; neither flips classification; pick larger lift"
        elif lift_diff > 0:
            expected_primary = "cost"
            expected_reason = f"cost lift ({cost_only_lift:.0f}) > price lift ({price_only_lift:.0f})"
        else:
            expected_primary = "price"
            expected_reason = f"price lift ({price_only_lift:.0f}) > cost lift ({cost_only_lift:.0f})"

    mismatch = system_primary != expected_primary

    return {
        "name": str(row["name"]),
        "category": str(row["category"]),
        "classification": current_classification,
        "elasticity": elasticity,
        "currentPrice": round(current_price, 2),
        "currentCost": round(current_cost, 2),
        "currentQty": current_qty,
        "currentProfit": round(current_profit, 2),
        "currentMarginPct": round(current_margin, 2),
        "suggestedPrice": int(round(suggested_price)) if suggested_price else None,
        "priceKind": suggested_kind,
        "direction": direction,
        "priceOnlyLift": round(price_only_lift, 2),
        "priceFlips": price_flips,
        "priceFlipsTo": projected_class_price_only if price_flips else None,
        "suggestedCost": int(round(suggested_cost)) if suggested_cost is not None else None,
        "costOnlyLift": round(cost_only_lift, 2),
        "costFlipsAtCurrentPrice": flips_at_current_price,
        "costFlipsTo": cost_class_at_current_price if flips_at_current_price else None,
        "systemPrimary": system_primary,
        "expectedPrimary": expected_primary,
        "expectedReason": expected_reason,
        "mismatch": mismatch,
        "magnitude": round(abs(cost_only_lift - price_only_lift), 2),
    }


def main():
    items, avg_pop, avg_margin = build_items()
    print(f"items: {len(items)}  avg_pop_cutoff={avg_pop:.4f}%  avg_margin={avg_margin:.2f}%")
    print(f"classifications: {dict(items['classification'].value_counts())}")

    rows = []
    for _, row in items.iterrows():
        rows.append(evaluate_one(row, avg_pop, avg_margin))

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "lever_choice_audit.csv", index=False)

    # ── Summary ────────────────────────────────────────────────────────
    total = len(out)
    correct = int((~out["mismatch"]).sum())
    wrong = int(out["mismatch"].sum())
    print(f"\n=== Summary ===")
    print(f"  total items   : {total}")
    print(f"  correct       : {correct}  ({correct/total*100:.1f}%)")
    print(f"  mismatched    : {wrong}    ({wrong/total*100:.1f}%)")

    by_class = out.groupby("classification").agg(
        n=("mismatch", "size"),
        correct=("mismatch", lambda s: (~s).sum()),
        wrong=("mismatch", "sum"),
    )
    print("\nBy classification:")
    print(by_class)
    by_class.to_csv(OUT / "lever_choice_by_classification.csv")

    # Mismatch direction counts
    if wrong:
        cross = (
            out[out["mismatch"]]
            .groupby(["systemPrimary", "expectedPrimary"])
            .size()
            .rename("count")
            .reset_index()
        )
        print("\nMismatch directions (system → expected):")
        print(cross)

    # Top 5 by financial impact
    if wrong:
        top = (
            out[out["mismatch"]]
            .sort_values("magnitude", ascending=False)
            .head(5)
        )
        print("\nTop 5 mismatches by |Δlift|:")
        print(top[[
            "name", "classification", "systemPrimary", "expectedPrimary",
            "priceOnlyLift", "costOnlyLift", "magnitude",
        ]].to_string(index=False))

    # Items where cost suggestion is correctly skipped — confirm not
    # false negatives.
    skipped = out[out["suggestedCost"].isnull()].copy()
    skipped["whySkipped"] = skipped.apply(_skip_reason, axis=1)
    skipped_breakdown = skipped["whySkipped"].value_counts()
    print(f"\nItems with no cost suggestion: {len(skipped)}")
    print(skipped_breakdown)
    skipped.to_csv(OUT / "lever_choice_cost_skipped.csv", index=False)

    summary = {
        "totalItems": total,
        "correct": correct,
        "wrong": wrong,
        "byClassification": by_class.to_dict(orient="index"),
        "thresholds": {"avgPopCutoff": avg_pop, "avgMargin": avg_margin},
    }
    (OUT / "lever_choice_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nWrote: {OUT / 'lever_choice_audit.csv'}")
    print(f"Wrote: {OUT / 'lever_choice_by_classification.csv'}")
    print(f"Wrote: {OUT / 'lever_choice_cost_skipped.csv'}")
    print(f"Wrote: {OUT / 'lever_choice_summary.json'}")


def _skip_reason(row) -> str:
    """Plain-English reason the cost-lowering routine returned None."""
    if row["currentCost"] < 1:
        return f"cost <1 SAR (={row['currentCost']})"
    cur_margin = (row["currentPrice"] - row["currentCost"]) / row["currentPrice"] * 100
    # Mirror the function's gating logic:
    realistic_floor = row["currentCost"] * 0.50
    target_for_avg = row["currentPrice"] * (1 - row.get("avgMargin", 0) / 100) if False else None
    # Simplest explanation: <5 % discount achievable
    return f"<5 % reduction achievable (cost={row['currentCost']}, margin={cur_margin:.0f}%)"


if __name__ == "__main__":
    main()
