# Lever-Choice Evaluation — Menu Engineering

> **Scope.** Audits the price-vs-cost lever-choice logic that drives the
> hero panel of the What-If simulator (`backend/routes_menu.py`). For
> every item on the menu, we recompute the price-only and cost-only
> profit lifts and compare the system's primary-lever choice against
> the math-optimal one.
>
> **Data.** `backend/sample_data/orders_2022.xlsx`, real POS days only
> (`is_imputed = FALSE`) — 277 days × 129 products × 8 categories,
> matching the production endpoint's `include_synthetic=False` filter.
>
> **Source.** `backend/eval/lever_choice_eval.py` imports the live
> `_optimal_price`, `_cost_lowering_suggestion`, `_simulate_one`,
> `_classify`, and `_elasticity_for` from `routes_menu.py` — every
> path tested below is the real production path. Outputs:
> `lever_choice_audit.csv`, `lever_choice_by_classification.csv`,
> `lever_choice_cost_skipped.csv`, `lever_choice_summary.json`.

---

## 1. Methodology

For each of the 129 menu items I rebuilt the per-item state
(`current_price`, `current_cost`, `current_qty`, `current_profit`,
`current_classification`, elasticity) using the same group-by /
threshold logic the simulate endpoint uses (Kasavana–Smith popularity
cutoff = 0.7 × mean = 0.5426 %; margin cutoff = mean = 74.67 %). I
then called `_optimal_price` to obtain the price suggestion and
direction (`raise / lower / hold`), called `_simulate_one` at the
suggested price (with cost held at its current value) to get
`projectedProfit` for the **price-only** scenario, and called
`_cost_lowering_suggestion` to obtain `suggestedCost` and
`flipsAtCurrentPrice` for the **cost-only** scenario.

- **Price-only lift** = `projectedProfit_at_suggested_price − currentProfit`,
  using the same float-qty math the endpoint uses (no integer
  staircase).
- **Cost-only lift** = `(currentCost − suggestedCost) × currentQty`,
  i.e. `costLowering.additionalProfit` (verified to match within
  rounding).

The **system primary lever** follows the documented hero-panel rule:
`cost` when `direction == "hold"` OR
(`flipsAtCurrentPrice AND cost_lift > price_lift`); otherwise `price`.

The **expected primary lever** follows the math-optimal rule from the
ground rules: whichever single lever lifts profit more wins; ties
(within 5 % or SAR 50/yr) are broken by classification flip
(cost flip > price flip > no flip); a `hold` price suggestion paired
with a viable cost suggestion → `cost`; no cost suggestion AND no
profitable price → `neither — flag for removal`. A "mismatch" is any
item where the two disagree.

---

## 2. Headline numbers

| Metric                  | Value                  |
| ----------------------- | ---------------------- |
| Items evaluated         | **129**                |
| System correct          | **90 (69.8 %)**        |
| System wrong            | **39 (30.2 %)**        |
| Mismatch direction      | 39/39 = **system says price, math says cost** |
| Total foregone profit   | **≈ SAR 24,655 / yr**  |
| Worst single item       | Saffron Cake — SAR 6,489 / yr |

By Boston-Matrix quadrant:

| Classification | n   | Correct | Wrong | Wrong %   |
| -------------- | --- | ------- | ----- | --------- |
| Star           | 36  | 23      | 13    | 36.1 %    |
| Plowhorse      | 16  |  9      |  7    | 43.8 %    |
| Puzzle         | 33  | 14      | 19    | **57.6 %** |
| Dog            | 44  | 44      |  0    |  0.0 %    |

Dogs are uniformly correct because `_optimal_price` already returns
`hold` for inelastic Dogs, which routes the rule into the
`direction == "hold"` branch and surfaces cost as primary. Every
other quadrant leaks profit, with Puzzles the worst hit.

---

## 3. Mismatch table

All 39 mismatches in one place — sorted by `|cost_lift − price_lift|`,
the size of the misallocation. **System lever** is the one the hero
panel renders today; **Expected** is the math-optimal one.

| # | Item | Category | Class. | e | Price | Cost | Margin % | Price-only lift (SAR/yr) | Cost-only lift (SAR/yr) | System | Expected | Reason for mismatch |
|---|------|----------|--------|---|------:|-----:|---------:|-------------------------:|------------------------:|--------|----------|---------------------|
|  1 | Saffron Cake             | Sweets             | Plowhorse | -1.8 | 24 | 14.50 | 39.6 |   791 | **7,280** | price | cost | cost lift ≫ price lift; cost reduction does not flip classification |
|  2 | Spanish latte            | Cold Coffee Drinks | Star      | -0.8 | 19 |  3.41 | 82.1 | 3,257 | **7,090** | price | cost | cost lift > price lift; no flip |
|  3 | Lotus Cake               | Sweets             | Plowhorse | -1.8 | 23 | 11.00 | 52.2 |    27 | **2,750** | price | cost | cost lift ≫ price lift; no flip |
|  4 | Saudi Coffee - Small pot | Hot Drinks         | Star      | -0.7 | 19 |  3.86 | 79.7 | 1,260 | **2,791** | price | cost | cost lift > price lift; no flip |
|  5 | Blue Mojito              | Cold Drinks        | Star      | -1.0 | 17 |  3.82 | 77.5 |   406 | **1,834** | price | cost | cost lift ≫ price lift; no flip |
|  6 | Pistachio latte          | Cold Coffee Drinks | Plowhorse | -0.8 | 19 |  6.87 | 63.8 | 1,028 | **2,106** | price | cost | cost lift > price lift; no flip |
|  7 | Passion Mojito           | Cold Drinks        | Star      | -1.0 | 17 |  3.83 | 77.5 |   232 | **1,052** | price | cost | cost lift ≫ price lift; no flip |
|  8 | Iced Mocha               | Cold Coffee Drinks | Star      | -0.8 | 18 |  4.39 | 75.6 |   817 | **1,542** | price | cost | cost lift > price lift; no flip |
|  9 | Saudi Coffee - Large pot | Hot Drinks         | Star      | -0.7 | 34 |  7.79 | 77.1 |   546 | **1,191** | price | cost | cost lift > price lift; no flip |
| 10 | Fruites Crepe            | Hot Sweets         | Puzzle    | -1.6 | 31 |  6.78 | 78.1 |   141 |   **781** | price | cost | cost lift ≫ price lift; no flip |
| 11 | Iced Caramel Macchiato   | Cold Coffee Drinks | Star      | -0.8 | 19 |  4.52 | 76.2 |   538 | **1,116** | price | cost | cost lift > price lift; no flip |
| 12 | Red Mojito               | Cold Drinks        | Star      | -1.0 | 17 |  4.24 | 75.1 |   321 |   **892** | price | cost | cost lift ≫ price lift; no flip |
| 13 | Relax                    | Cold Drinks        | Puzzle    | -1.0 | 17 |  3.95 | 76.8 |    64 |   **570** | price | cost | cost lift ≫ price lift; no flip |
| 14 | Cheese Croissant         | Bakery             | Plowhorse | -1.0 | 11 |  4.36 | 60.4 |   380 |   **769** | price | cost | cost lift > price lift; no flip |
| 15 | Oreo Frappuccino         | Cold Drinks        | Puzzle    | -1.0 | 19 |  4.48 | 76.4 |    55 |   **361** | price | cost | cost lift ≫ price lift; no flip |
| 16 | chocolate cookies        | Bakery             | Plowhorse | -1.0 |  7 |  2.91 | 58.4 |   190 |   **474** | price | cost | cost lift > price lift; no flip |
| 17 | Vanilla Cookies          | Bakery             | Plowhorse | -1.0 |  7 |  2.90 | 58.6 |   183 |   **452** | price | cost | cost lift > price lift; no flip |
| 18 | Stick waffle             | Hot Sweets         | Star      | -1.6 | 16 |  3.96 | 75.2 |   368 |   **630** | price | cost | cost lift > price lift; no flip |
| 19 | Manan Smoothie           | Cold Drinks        | Puzzle    | -1.0 | 19 |  3.42 | 82.0 |    36 |   **297** | price | cost | cost lift ≫ price lift; no flip |
| 20 | French Coffee - Small    | Hot Drinks         | Puzzle    | -0.7 | 13 |  3.21 | 75.3 |   136 |   **363** | price | cost | cost lift > price lift; no flip |
| 21 | Caramel Macchiato        | Espresso Drinks    | Puzzle    | -0.5 | 15 |  3.69 | 75.4 |   124 |   **342** | price | cost | cost lift > price lift; no flip |
| 22 | Milk - Large             | Hot Drinks         | Star      | -0.7 |  8 |  1.66 | 79.2 |   312 |   **494** | price | cost | cost lift > price lift; no flip |
| 23 | Italian Coffee - Large   | Hot Drinks         | Puzzle    | -0.7 | 15 |  3.76 | 74.9 |    51 |   **193** | price | cost | cost lift > price lift; no flip |
| 24 | Italian Coffee - Small   | Hot Drinks         | Puzzle    | -0.7 | 13 |  2.96 | 77.3 |   121 |   **263** | price | cost | cost lift > price lift; no flip |
| 25 | Esspresso                | Espresso Drinks    | Star      | -0.5 |  9 |  1.67 | 81.5 |   689 |   **804** | price | cost | cost lift > price lift; no flip |
| 26 | Chocolate Crepe          | Hot Sweets         | Star      | -1.6 | 26 |  4.77 | 81.7 |   979 | **1,073** | price | cost | cost lift > price lift; no flip |
| 27 | Asfahani                 | Cold Drinks        | Puzzle    | -1.0 | 17 |  2.29 | 86.6 |    33 |    **73** | price | cost | cost lift > price lift; no flip |
| 28 | Hot chocolate - Small    | Hot Drinks         | Plowhorse | -0.7 | 13 |  3.84 | 70.4 |   406 |   **365** | price | cost | lifts ~tied; cost flips Plowhorse → Star, price doesn't |
| 29 | Mini pancake Sticks      | Hot Sweets         | Puzzle    | -1.6 | 21 |  3.80 | 81.9 |    20 |    **56** | price | cost | cost lift > price lift; no flip |
| 30 | Macchiato                | Espresso Drinks    | Puzzle    | -0.5 | 10 |  1.85 | 81.5 |    67 |   **100** | price | cost | cost lift > price lift; no flip |
| 31 | Peach Iced Tea           | Cold Drinks        | Puzzle    | -1.0 | 17 |  2.43 | 85.7 |    14 |    **44** | price | cost | cost lift > price lift; no flip |
| 32 | Orange juice             | Cold Drinks        | Star      | -1.0 | 13 |  2.39 | 81.6 |   128 |   **157** | price | cost | cost lift > price lift; no flip |
| 33 | Affogato                 | Cold Coffee Drinks | Puzzle    | -0.8 | 17 |  3.25 | 80.9 |    10 |    **35** | price | cost | cost lift ≫ price lift; no flip |
| 34 | Chocolate with ice cream Waffle | Hot Sweets  | Puzzle    | -1.6 | 23 |  3.72 | 83.8 |    11 |    **26** | price | cost | cost lift > price lift; no flip |
| 35 | Chemex - Java            | Dripp Coffee Drinks| Puzzle    | -0.5 | 15 |  2.67 | 82.2 |    47 |    **54** | price | cost | cost lift > price lift; no flip |
| 36 | Ginger milk - Large      | Hot Drinks         | Puzzle    | -0.7 |  9 |  1.69 | 81.3 |     8 |    **13** | price | cost | cost lift > price lift; no flip |
| 37 | Ruby Mini pancake - Large| Hot Sweets         | Puzzle    | -1.6 | 38 |  8.21 | 78.4 |     2 |     **6** | price | cost | cost lift > price lift; no flip |
| 38 | Ruby Mini pancake - Small| Hot Sweets         | Puzzle    | -1.6 | 20 |  4.24 | 78.8 |     2 |     **4** | price | cost | cost lift > price lift; no flip |
| 39 | Green Tea - Large Teapot | Hot Drinks         | Puzzle    | -0.7 | 17 |  1.37 | 92.0 |    16 |    **17** | price | cost | cost lift > price lift; no flip |

---

## 4. Top 5 by financial impact

Sorted by `|expected lift − system lift|` — the SAR/yr the hero panel
is leaving on the table by leading with the wrong lever.

| Rank | Item                     | Class.    | System lift | Expected lift | Foregone (SAR/yr) |
| ---- | ------------------------ | --------- | ----------: | ------------: | ----------------: |
| 1    | Saffron Cake             | Plowhorse |   791       |  7,280        | **6,489**         |
| 2    | Spanish latte            | Star      | 3,257       |  7,090        | **3,833**         |
| 3    | Lotus Cake               | Plowhorse |    27       |  2,750        | **2,723**         |
| 4    | Saudi Coffee - Small pot | Star      | 1,260       |  2,791        | **1,532**         |
| 5    | Blue Mojito              | Star      |   406       |  1,834        | **1,429**         |

These five alone account for **SAR 16,006/yr** of the SAR 24,655/yr
total — about 65 % of the misallocation comes from the long tail of
high-volume / low-cost-share items where supplier negotiation lifts
profit far more than any safe price move.

---

## 5. Special-case findings — cost suggestion correctly skipped

`_cost_lowering_suggestion` returns `None` for **16 of 129 items**.
Each was inspected against the function's three skip gates
(`current_cost < 1`, ≥5 % reduction not achievable,
`current_price ≤ 0`):

- **15 items: `current_cost < 1 SAR`** — Black Tea, Green Tea, Turkish
  Tea, Saudi tea variants, Baby chino, Latte ingredients, etc. Tea/coffee
  raw-material costs are 0.06 – 0.93 SAR; there is genuinely no
  supplier-negotiation lever. Confirmed not a false negative — the math
  routes these correctly to `expected = price`, and `_optimal_price`
  returns a positive `test_increase` for every one of them.
- **1 item: Latte (cost = 2.10, margin = 84 %)** — already at high
  margin, so `target_cost_for_avg_margin` exceeds current cost, and the
  realistic-floor (50 % off) only buys a 4.76 % reduction (`ceil(1.05) = 2`),
  below the 5 % minimum gate. Correct skip; price-only lift = SAR 2,334/yr
  is the meaningful lever here.

No item that *could* benefit from cost reduction is being silently
dropped. The skip gates are working as intended.

---

## 6. Bottom line

**The lever-choice rule is unsound.** It is correct for Dogs (because
`_optimal_price` already returns `hold` for inelastic Dogs and the
`direction == "hold"` branch fires) and for the 90 items where price
genuinely is the bigger lever, but it leaks profit across **30 % of
the menu** — concentrated in Puzzles and Stars where margins are
healthy and unit costs are several SAR.

**Root cause.** The frontend rule conjoins two conditions to pick cost:

```
cost when direction == "hold"
       OR (flipsAtCurrentPrice AND cost_lift > price_lift)
```

`flipsAtCurrentPrice` is almost never true for Stars or healthy
Puzzles — they're already on the high-margin side of the matrix, so
trimming cost can't move them up an axis they're already past. So the
rule short-circuits to `price` for every healthy item, even when a
realistic supplier negotiation would beat the elasticity-bounded price
move by 2–10×.

**Suggested fix.** Drop `flipsAtCurrentPrice` from the gating
condition. The math is simpler and matches the documented intent
("hero panel leads with whichever single lever has the bigger
impact"):

```python
# rendering rule (frontend or new field on the API response)
if direction == "hold" or cost_lift > price_lift:
    primary = "cost"
else:
    primary = "price"
```

Two refinements worth considering when wiring this in:

1. **Surface a `recommendedPrimaryLever` field from the API.** Today
   the rendering rule lives in the frontend (and is unimplemented in
   `src/app/menu-engineering/page.tsx`). Returning it from
   `simulate_price_change` keeps the math + the rule colocated with
   `_optimal_price` / `_cost_lowering_suggestion` and prevents the
   frontend from re-deriving it.
2. **Tie-break on classification flip.** When the two lifts are within
   ~5 % or SAR 50/yr (e.g. *Hot chocolate - Small*: price lift 406 vs
   cost lift 365, but cost flips Plowhorse → Star while price doesn't),
   prefer the lever that flips. This single item is the only mismatch
   in the whole audit where cost lift is *smaller* than price lift —
   but the classification flip makes it the right call strategically.

`_cost_lowering_suggestion` itself produces correct values
(`additionalProfit`, `flipsAtCurrentPrice`, `classificationAtCurrentPrice`
all check out against `_simulate_one`); the bug is purely in how those
values are *used* to choose between levers.
