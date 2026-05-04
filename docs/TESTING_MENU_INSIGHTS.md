# Testing Brief — Menu Insights

You own the **Menu Insights page** (Boston Matrix classification + What-If
price/cost simulator). Your job is to evaluate whether the classifications and
the price/cost suggestions are realistic given the menu economics. You don't
need to change code — just use the deployed site and report findings.

## Live URL
**https://gp-restaurant-frontend.vercel.app**

Log in with any of the four team accounts (password `demo1234`).

## How the system works (2 minutes)

### Boston Matrix classification

Every item is labeled **Star / Plowhorse / Puzzle / Dog** based on two metrics:

- **Popularity** = (item's units sold ÷ total menu units) × 100
- **Profit margin** = (revenue − total cost) ÷ revenue × 100

Thresholds are **menu-wide averages** (recalculated each time the page loads):

| Quadrant | Rule | Meaning |
|---|---|---|
| ⭐ **Star** | popularity ≥ avg AND margin ≥ avg | Keep featuring |
| 🐎 **Plowhorse** | popularity ≥ avg AND margin < avg | Raise price or cut cost |
| 🧩 **Puzzle** | popularity < avg AND margin ≥ avg | Promote — demand-limited |
| 🐕 **Dog** | popularity < avg AND margin < avg | Discount or remove |

### What-If simulator — the equations

When you move the **price slider**, demand is projected with a
**constant-elasticity equation**:

```
new_qty = old_qty × (new_price / old_price)^elasticity
```

When you move the **cost slider**, demand stays the same — only margin and
profit change:

```
new_profit = new_qty × (new_price − new_cost)
new_margin = (new_profit / new_revenue) × 100
```

The elasticity coefficient is **per-category**, hardcoded from hospitality
research. Examples relevant to our data:

| Category | Elasticity (e) | Behavior |
|---|---|---|
| Espresso drinks | **−0.5** | Very inelastic — coffee drinkers keep buying when prices rise |
| Hot Drinks (tea, hot chocolate) | −0.6 to −0.9 | Mostly inelastic |
| Cold Coffee Drinks | −0.8 | Slightly elastic |
| Cold Drinks / juices | −1.0 to −1.2 | Around unit-elastic |
| Bakery | −0.9 to −1.3 | Slightly elastic |
| Hot Sweets / waffles / pancakes | **−1.6** | Discretionary — demand drops fast on a price hike |
| Sweets / desserts | **−1.8** | Most elastic in the menu |

Negative values mean quantity goes **down** as price goes **up**. The closer
to 0, the less customers care about a price change. ±30 % uncertainty band on
each value.

### Optimal price suggestion

Uses the constant-elasticity profit-maximizing formula with classification
overrides:

```
P* = e × cost / (1 + e)        (when |e| > 1, the elastic region)
```

Capped at ±20 % from the current price. Classification-aware overrides:

- **Dog** → suggest a 10 % discount (price hike won't fix the problem)
- **Puzzle** → suggest a discount to unlock demand
- **Star / Plowhorse** → use the elasticity math
- **Inelastic items** (|e| < 1) → suggest a small test increase (since the
  formula has no interior optimum — profit grows monotonically with price)

### Cost-cut bonus suggestion

Shown only when **all three** conditions hold:

1. Current cost > 1 SAR (anything tiny leaves no negotiation room)
2. Current margin < 75 % (already-very-profitable items don't need it)
3. Classification is NOT Puzzle (Puzzle's bottleneck is demand, not margin)

Target = whatever brings margin up to the menu average. Capped at a 30 %
supplier reduction. Below 5 % reduction → not surfaced.

## Test scenarios

Run these in order. Paste each result into the tracker.

### 1. Quadrant tile counts
- Look at the four tiles at the top of Menu Insights
- **Expected**: each quadrant has at least a few items; no single quadrant is empty or holds 90 %+ of the menu
- **Red flag**: 0 Stars, or all 129 items in one quadrant

### 2. A Star item — no slider movement
- Click a Star item (e.g. Spanish latte). Don't move the sliders yet.
- **Expected**: Suggested price is **at or slightly above** current. Rationale text: "current price looks about right" or similar.
- **Red flag**: huge suggested cut, or cost-cut bonus shown for a 73 %+ margin item

### 3. A Plowhorse — read the suggestion + cost-cut combo
- Click a Plowhorse item (high volume, tight margin)
- **Expected**: Suggested price is *higher* than current (raise to lift margin). Cost-cut bonus is **shown** with a realistic 5–30 % reduction target.
- Verdict box should describe the price+cost combo in business terms.
- **Red flag**: suggestion to lower price, or no cost-cut bonus shown

### 4. A Dog item with margin < 30 %
- Click a Dog. Slide price **up by 20 %**.
- **Expected**: verdict reads "Not recommended — wrong direction for a Dog. Discount or replace."
- Cost-cut bonus should NOT appear (Dogs need volume rescue, not margin).
- **Red flag**: verdict says "Good idea" when raising price on a Dog

### 5. A Puzzle — demand-limited
- Click a Puzzle (low volume, high margin)
- **Expected**: suggestion to **lower** price (unlock demand). Cost-cut bonus is **hidden** (Puzzles are not margin-limited).
- **Red flag**: cost-cut bonus shows up for a Puzzle

### 6. Elasticity sanity — coffee vs sweet
- Pick an espresso drink (e.g. Americano, Spanish latte). Slide price **up 20 %**.
- Note the projected quantity change. Math: new_qty = old × (1.2)^(−0.5) ≈ old × 0.91, so qty should drop ~9 %.
- Now pick a Hot Sweet (e.g. Chocolate waffle, Mini pancake). Slide price **up 20 %**.
- Math: new_qty = old × (1.2)^(−1.6) ≈ old × 0.72, so qty should drop ~28 %.
- **Expected**: coffee qty drops barely, sweet qty drops noticeably. The verdict on the sweet should be much more cautious.
- **Red flag**: both items behave identically (means elasticity isn't being applied per-category)

### 7. Classification transition
- Pick a Plowhorse with a moderate margin gap (so it's close to the Star line)
- Slide price up gradually, watching the "Moves [old] → [new]" pill
- **Expected**: at some price level, the pill flips from "Stays Plowhorse" to "Moves Plowhorse → Star"
- **Red flag**: classification never changes no matter how high you push the price

### 8. Discount on a Star
- Pick a popular Star (e.g. Spanish latte). Slide price **down 20 %**.
- **Expected**: projected qty rises modestly (low elasticity), revenue could go up or down depending on the math, profit drops because margin shrinks faster than volume rises.
- **Red flag**: profit goes up significantly when discounting a Star (model is mis-applying elasticity)

### 9. Per-page export
- Click the Export button at the top-right of Menu Insights → choose CSV
- **Expected**: downloaded file opens cleanly in Excel. Has sections for Stars, Plowhorses, Puzzles, Dogs, plus the menu portfolio summary.
- **Red flag**: file won't open, or Arabic product names are mojibake (encoding issue)

## Report format

For every scenario, paste this block:

```
Item:                 Spanish latte
Current state:        Star — popularity 8.2%, margin 73%, 5,039 units sold
Action tested:        slide price +20% to 23 SAR
What I saw:           projected qty 3,400, profit +18%, classification Star
What I expected:      qty around 4,500 (using e = -0.5), classification Star
Gap:                  qty drop too aggressive — feels like wrong elasticity is being used
```

Or for tile / classification findings:

```
Tile:                 Plowhorse count
Showed:               12 items
Why I think it's off: 4 of those have margins above 75% which feels too high for "tight margin"
Item examples:        Mini Pancake (margin 78%), Brownie (margin 81%)
```

## Out of scope for this role

- Don't open GitHub or read code
- Don't try to fix the math yourself
- Don't worry about forecasting accuracy or upload flow — separate roles

## Where findings go

The team tracker. One row per finding. Wait for the implementer to mark
"fixed" before re-testing the same scenario.
