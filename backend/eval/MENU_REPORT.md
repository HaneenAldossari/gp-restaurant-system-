# Menu Engineering / Boston Matrix — Evaluation Report

> **Scope.** This report evaluates the menu-engineering module that lives in
> `backend/routes_menu.py` — the Boston-Matrix classifier
> (`/api/menu-engineering`) and the price simulator
> (`/api/menu-engineering/simulate`, `…/simulate-bulk`). Evaluation is
> **read-only** against the production code and the local Excel sample;
> nothing under `backend/` was modified outside the new `eval/` files.
>
> **Data.** `backend/sample_data/orders_2022.xlsx`. 60 368 line items,
> 339 days, 129 products, 8 categories. The file carries an `is_imputed`
> flag; **49 102 rows on 277 real POS days** are kept for evaluation,
> the 11 266 imputed rows on 62 fill-in days are excluded — matching the
> production endpoint, which calls `load_data(..., include_synthetic=False)`.

---

## 1. Methodology

### 1.1 What we replicated

`backend/eval/menu_eval.py` re-implements the production logic verbatim
(class definitions, elasticity table, Dog/Puzzle override branches,
±20 % safety caps). Re-implementing rather than importing avoids pulling
the FastAPI/auth/DB stack into the eval, but every code path tested
below is a direct paste from `routes_menu.py`. A textbook check
(`menu_simulator_textbook_check.csv`) confirms the simulator's qty
projection equals `q₀ · (p₁/p₀)^e` to 6 decimal places — i.e. the math
itself is faithful to a constant-elasticity demand model.

### 1.2 What the eval measures

1. **Cutoff sensibility.** The production thresholds are the
   *arithmetic mean* of per-item popularity and per-item profit margin.
   We compare against the per-item median and against the canonical
   Kasavana–Smith menu-engineering threshold (70 % of avg popularity).
2. **Boundary instability.** Items within ±0.20 pp of the popularity
   cutoff or ±2.0 pp of the margin cutoff — the band where
   classification flips on noise.
3. **Elasticity coverage.** Every category in the dataset is mapped to
   a value in `ELASTICITY_BY_CATEGORY` or falls through to
   `DEFAULT_ELASTICITY = -1.5`.
4. **Suggestion outcome.** For every item, we feed the suggested price
   back through the same constant-elasticity demand model and compute
   `Δprofit = q₁(p_suggest) · (p_suggest − cost) − q₀ · (p₀ − cost)`.
   A suggestion that *destroys* projected profit is counted as broken on
   the engine's own terms.
5. **Imputed-row drift.** We re-classify with imputed rows mixed in to
   measure how badly the eval would be invalidated by the constraint
   the report is required to enforce.

Every artifact below lives next to this report (`backend/eval/`).

---

## 2. Headline findings

| # | Finding | Severity |
|---|---------|----------|
| 1 | **56 % of price suggestions (73 of 129) reduce projected profit** when scored against the simulator's own demand model. | **Critical** |
| 2 | **Mean cutoffs misclassify 22 % of the menu (29 of 129)** vs. median cutoffs; 15 popular high-margin coffee drinks are demoted from Star → Puzzle, which then triggers a wrong-direction discount. | **Critical** |
| 3 | The Dog/Puzzle "discount" override applies **uniform price cuts to inelastic items** (e ≥ −1). For coffee/tea, lowering the price never raises profit — the demand model the simulator already trusts says so. | **Critical** |
| 4 | The discount math uses `floor(price × 0.90)` and `floor(price × 0.92)`. For low-priced items (the bulk of the menu, SAR 9–17) the realised cut is **−12 % to −15 %**, not the intended −10 % / −8 %. | High |
| 5 | The popularity threshold is mathematically `mean popularity = 100 / N` (≈ 0.78 % at N = 129). With a heavy right-skewed sales distribution, this is a tail-test, not a "above average" test — only **30 %** of items pass. The canonical Kasavana–Smith literature uses **70 % × mean** for exactly this reason. | High |
| 6 | `simulate_bulk` uses the **integer-rounded** projected qty inside the profit math (whereas `simulate` uses the float). For low-volume items this introduces ±25 SAR / item / scenario staircase error in the aggregated bulk impact. | Medium |
| 7 | 32 of 129 items sit in the **boundary band** where a 1–2 pp wobble in cost or sales noise flips the quadrant. The classifier exposes no margin of error to the manager. | Medium |
| 8 | `DEFAULT_ELASTICITY = -1.5` is **never reached** on the current menu — every category maps via exact or partial keyword. But a future category like "Salads" would partial-match "salads" and inherit −1.5; "Mojitos" would *not* match anything and would fall through. (Asfahani, Blue Mojito, etc. correctly resolve to "cold drinks".) Worth keeping in mind. | Low |

The classifier's quadrant math is correct. The simulator's qty
projection is correct to a textbook constant-elasticity model. The
defects sit in the **strategy layer** that converts those correct
numbers into a recommendation.

---

## 3. The classification engine

### 3.1 Cutoff distribution

```
items: 129    mean popularity = 0.7752 %    mean margin = 74.67 %
              median popularity = 0.3974 %  median margin = 75.43 %
```

| classification | count | share | revenue share | median pop | median margin |
|---------------:|------:|------:|--------------:|-----------:|--------------:|
| Star           |    27 | 20.9 % |        58.4 % |   1.295 %  |     82.92 %   |
| Plowhorse      |    12 |  9.3 % |        16.7 % |   1.014 %  |     65.59 %   |
| Puzzle         |    42 | 32.6 % |        12.8 % |   0.582 %  |     85.67 %   |
| Dog            |    48 | 37.2 % |        12.0 % |   0.180 %  |     65.67 %   |

A canonical Boston Matrix puts ~25 % of items in each quadrant. Here,
**70 %** of items sit on the "low popularity" side — that is the
fingerprint of a popularity threshold that is too aggressive, not of a
genuinely Dog-heavy menu.

### 3.2 Why the popularity cutoff is too aggressive

`popularity = qty / total_qty × 100`, so the per-item mean is
exactly `100 / N`. With `N = 129` that is `0.7752 %`. Sales in this
dataset follow a heavy right-skew (Pareto-like): the median is
`0.3974 %`. Spanish Latte alone is `8.82 %` — pulling the mean to twice
the median.

Standard menu-engineering literature (Kasavana & Smith, 1982) uses
**70 % of the mean** as the popularity threshold precisely because of
this skew. Switching to the median or to 70 % × mean redistributes
classifications more sensibly:

| variant                        | Star | Plowhorse | Puzzle | Dog |
|--------------------------------|-----:|----------:|-------:|----:|
| **production (mean)**          |  27  |    12     |   42   | 48  |
| Kasavana–Smith (0.7 × mean)    |  36  |    16     |   33   | 44  |
| median cutoffs                 |  40  |    25     |   25   | 39  |

See `menu_quadrants.csv`, `menu_distributions.png`,
`menu_boston_matrix.png`.

### 3.3 Concrete misclassifications under mean cutoffs

`menu_mean_vs_median_disagreement.csv` lists all 29 items that flip
when the cutoff changes. The most damaging pattern: 15 of 17
mean-Puzzles become medians-Stars. These are **popular, high-margin
coffee drinks** that the production rule labels as "low popularity"
because they don't beat 0.78 %:

| item | popularity | margin | mean class | median class |
|---|---:|---:|---|---|
| Saudi Coffee – Small Cup    | 0.60 % | 86.74 % | Puzzle | **Star** |
| Mocha                        | 0.42 % | 83.60 % | Puzzle | **Star** |
| Iced Americano               | 0.58 % | 87.88 % | Puzzle | **Star** |
| Caramel Macchiato            | 0.36 % | 75.43 % | Puzzle | **Star** |
| Chemex – Colombian           | 0.59 % | 85.67 % | Puzzle | **Star** |
| Black Tea – Small Teapot     | 0.77 % | 94.85 % | Puzzle | **Star** |
| Italian Coffee – Small       | 0.48 % | 77.26 % | Puzzle | **Star** |
| Mini pancake – Large         | 0.72 % | 84.69 % | Puzzle | **Star** |
| Saudi Coffee – Large pot     | 0.55 % | 77.08 % | Puzzle | **Star** |
| Turkish Tea – Small Teapot   | 0.68 % | 95.79 % | Puzzle | **Star** |
| Turkish Tea – Large Cup      | 0.61 % | 96.69 % | Puzzle | **Star** |
| Orange Juice                 | 0.70 % | 81.61 % | Puzzle | **Star** |
| Oreo Frappuccino             | 0.43 % | 76.42 % | Puzzle | **Star** |
| Asfahani                     | 0.45 % | 86.56 % | Puzzle | **Star** |
| Fruites Crepe                | 0.49 % | 78.13 % | Puzzle | **Star** |

These 15 items get labeled "Hidden gem — needs a discount" by the
recommendation engine. They are nothing of the sort: half of them are
inelastic coffee drinks. **Section 4** shows how that misclassification
turns into actual SAR-denominated profit destruction.

### 3.4 Boundary instability

`menu_boundary_items.csv` lists 32 items within ±0.20 pp of the
popularity cutoff or ±2.0 pp of the margin cutoff. Several are textbook
cases:

- **Chocolate Crush** — popularity 0.32 %, margin 74.28 %. Margin
  trails the mean by 0.39 pp → labeled Dog. A 5-halala increase in unit
  price (or a small cost reduction) would flip it to Puzzle.
- **Karak tea – Small Teapot** — margin 72.80 %, 1.87 pp under the
  cutoff. Sits 2 pp from being a Plowhorse.
- **Souffle / Stick waffle / Red Mojito** — all Stars whose margin sits
  0.4–0.6 pp above the cutoff. A small cost rise demotes them to
  Plowhorse and changes the recommendation strategy.

The classifier doesn't expose this fragility. A manager looking at
"Chocolate Crush is a Dog" has no signal that the verdict turns on
a single percentage point.

---

## 4. The price-suggestion engine

### 4.1 The math is correct, the strategy is not

The constant-elasticity demand model
`q₁ = q₀ · (p₁/p₀)^e` is implemented exactly
(`menu_simulator_textbook_check.csv`, max abs deviation = 0.0). The
profit-maximising formula `P* = e·c / (1+e)` for elastic items is the
textbook monopoly result. The ±20 % safety cap is a defensible
practical guard.

The defects are in the **classification-aware override branches** at
the top of `_optimal_price` (`routes_menu.py:277–306`).

### 4.2 56 % of suggestions destroy projected profit

For every item we computed the projected profit at the suggested price
(using the simulator's own demand math) and compared against current
profit. Result:

| classification | n  | mean Δprofit | suggestions losing > 1 SAR |
|----------------|---:|-------------:|---------------------------:|
| Star           | 27 |  +1 184 SAR  |  0 / 27                    |
| Plowhorse      | 12 |    +402 SAR  |  0 / 12                    |
| **Puzzle**     | 42 |     **−89 SAR**   |  **42 / 42**         |
| **Dog**        | 48 |     **−52 SAR**   |  **31 / 48**         |
| **all**        | 129 |   |  **73 / 129 (56 %)** |

`menu_price_suggestions.csv` and
`menu_price_suggestions_profit_negative.csv`.

Stars and Plowhorses get sensible elasticity-based advice (almost
always +10 % or capped +20 %). Puzzles and Dogs get a hardcoded
discount which loses money on the engine's own terms.

### 4.3 Worst offenders (Puzzles)

| item | category | e | current → suggested | Δprofit |
|---|---|---:|---|---:|
| Saudi Coffee – Large pot   | Hot Drinks         | −0.7 | 34 → 31 | **−455 SAR** |
| Chemex – Colombian         | Dripp Coffee       | −0.5 | 15 → 13 | −402 SAR |
| French Coffee – Small      | Hot Drinks         | −0.7 | 13 → 11 | −309 SAR |
| Mocha                      | Espresso           | −0.5 | 15 → 13 | −292 SAR |
| Italian Coffee – Small     | Hot Drinks         | −0.7 | 13 → 11 | −276 SAR |
| Caramel Macchiato          | Espresso           | −0.5 | 15 → 13 | −266 SAR |
| Iced Americano             | Cold Coffee        | −0.8 | 14 → 12 | −216 SAR |
| Saudi Coffee – Large Cup   | Hot Drinks         | −0.7 | 14 → 12 | −185 SAR |
| Orange Juice               | Cold Drinks        | −1.0 | 13 → 11 | −175 SAR |
| Black Tea – Small Teapot   | Hot Drinks         | −0.7 |  9 →  8 | −154 SAR |

Sum of all Puzzle suggestions: **−3 721 SAR / year** projected loss.
Sum of all Dog suggestions: **−2 512 SAR / year** projected loss.

### 4.4 Why it's wrong

The Puzzle override (`routes_menu.py:293`) runs *before* the elasticity
branch. It assumes "high margin, low volume → discount unlocks demand".
That assumption is **conditional on |e| > 1**. For a coffee drink with
e = −0.5, dropping price 13 % only lifts qty 7 % — revenue and profit
both fall. The override fires anyway.

Worse, this is the *exact case where misclassification (§3.3) lands*:
inelastic coffee drinks pushed into the Puzzle bucket by the mean
cutoff, then handed a discount their elasticity says will hurt.

The same issue affects the Dog branch
(`routes_menu.py:277`) more weakly: Dog items have lower qty, so the
SAR loss is smaller, but the direction is still wrong for inelastic
Dogs (Hot Pistachio Latte, e = −0.5, current 16 SAR → suggested 14 SAR
→ −596 SAR/year — the largest single loss in the report).

### 4.5 The discount-floor bug

```python
# routes_menu.py:278
suggested = max(int(math.floor(current_price * 0.90)),
                int(math.ceil(cost * 1.20)))
# routes_menu.py:294
suggested = max(int(math.floor(current_price * 0.92)),
                int(math.ceil(cost * 1.30)))
```

`floor(price * 0.90)` produces a discount **larger** than the intended
10 % whenever the price is not a multiple of 10. With most menu items
priced 9–17 SAR, the realised cut is consistently −12 % to −15 %:

| item | intended | realised |
|---|---:|---:|
| Hot Pistachio Latte (Dog, 16 → 14) |  −10 % | **−12.5 %** |
| Hot chocolate – Small (Dog, 13 → 11) |  −10 % | **−15.4 %** |
| iced Sandwich (Dog, 21 → 18) |  −10 % | **−14.3 %** |
| French Coffee – Small (Puzzle, 13 → 11) |  −8 % | **−15.4 %** |
| Caramel Macchiato (Puzzle, 15 → 13) |  −8 % | **−13.3 %** |
| Italian Coffee – Small (Puzzle, 13 → 11) |  −8 % | **−15.4 %** |

`menu_discount_floor_bug.csv` has the full table. The fix is
`int(round(price * 0.90))`.

### 4.6 +10 % textbook check

`menu_simulator_textbook_check.csv` runs five items × five price deltas
through both the production projector and the textbook formula. Max
absolute discrepancy: **0.0**. The simulator's quantity projection is
exactly textbook. The reviewer's "does +10 % match a constant-
elasticity model" question is **yes, to numerical precision** — the
problem is downstream in the strategy layer, not the math layer.

### 4.7 Bulk simulator staircase error

`simulate_bulk` (`routes_menu.py:833`) does:

```python
new_qty = _project_qty(int(row["qtySold"]), float(row["price"]),
                       new_price, elast)        # ← integer rounded
new_rev  = new_qty * new_price
new_prof = new_qty * (new_price - row["cost"])
```

while `simulate` (single-item) keeps a float qty inside the profit
math. The difference for a low-volume item (Saudi Coffee 1L pot, qty=4,
e = −0.7):

| Δprice | float qty | int qty | profit (float) | profit (int) | error |
|---:|---:|---:|---:|---:|---:|
| −20 % | 4.68 | 5 | 159.93 | 171.00 | **+11 SAR** |
| −10 % | 4.31 | 4 | 168.37 | 156.40 | **−12 SAR** |
| +10 % | 3.74 | 4 | 182.98 | 195.60 | **+13 SAR** |
| +20 % | 3.52 | 4 | 189.42 | 215.20 | **+26 SAR** |

For a bulk move on 42 Puzzle items, those staircase errors don't
cancel — they bias the aggregate result by an unpredictable few
hundred SAR. The fix is the same one already used in `simulate`:
keep the float qty inside the profit math, round only for display.

### 4.8 Elasticity table coverage

```
Bakery               n=11  e=-1.0  exact match
Cold Coffee Drinks   n=10  e=-0.8  exact match
Cold Drinks          n=15  e=-1.0  exact match
Dripp Coffee Drinks  n= 5  e=-0.5  partial match → "coffee"
Espresso Drinks      n=11  e=-0.5  exact match
Hot Drinks           n=39  e=-0.7  exact match
Hot Sweets           n=18  e=-1.6  exact match
Sweets               n=20  e=-1.8  exact match
```

Every category resolves to a sensible value; none falls through to
`DEFAULT_ELASTICITY = -1.5`. The values are reasonable against published
hospitality literature (espresso/coffee inelastic at ~−0.5, desserts
elastic at ~−1.7). One small note: "Dripp Coffee Drinks" matches via
the partial-match path on "coffee", which gives e = −0.5. That works
out, but the exact-match dictionary would be cleaner if "drip coffee
drinks" were keyed directly. (`menu_elasticity_by_category.csv`.)

---

## 5. Imputed-row constraint

The eval excluded 11 266 imputed rows on 62 fill-in days, leaving
49 102 rows on 277 real POS days (81.3 % of total qty). For comparison,
re-classifying with imputed rows mixed in flips **6 of 129 items**
(`menu_imputed_drift.csv`). All six are low-volume boundary items —
margin is unchanged by imputation (cost/price are constant), only
popularity wobbles slightly. The constraint is correct; honouring it
is straightforward and the production endpoint already does so via
`include_synthetic=False`.

---

## 6. Ranked issues

| # | Issue | Where | Severity | Suggested fix |
|---|-------|-------|---------:|---------------|
| 1 | Puzzle override prescribes a discount regardless of elasticity, destroying profit on inelastic items. | `routes_menu.py:293–306` | **Critical** | Gate the override on `e < -1.05`. For inelastic Puzzles, suggest a small price *increase* or "merchandising / visibility push, not price cut". |
| 2 | Mean cutoffs misclassify 22 % of the menu vs. median; 15 popular high-margin items demoted Star → Puzzle. | `routes_menu.py:48–49` (`avg_pop`, `avg_margin`) | **Critical** | Use median, or the canonical 0.7 × mean for popularity (Kasavana–Smith). Document the choice. |
| 3 | Dog override prescribes a discount regardless of elasticity — same root cause, smaller blast radius. | `routes_menu.py:277–291` | High | Same gate as #1. Inelastic Dogs should be flagged for *removal* or *recipe rework*, not discount. |
| 4 | `floor(price · 0.90)` and `floor(price · 0.92)` produce 12–15 % cuts instead of 10/8 % on low-priced items. | `routes_menu.py:278, 294` | High | Replace `floor` with `round`. |
| 5 | Popularity threshold = `100/N` flags only 30 % of items as popular; ~70 % land on the "low pop" side. | `routes_menu.py:48` | High | Switch to median or 0.7 × mean. Combined with #2. |
| 6 | `simulate_bulk` profit math uses int-rounded qty, introducing staircase errors that don't cancel. | `routes_menu.py:833–836` | Medium | Switch to `_project_qty_float` inside profit math, mirror what `simulate` already does. |
| 7 | 32 boundary items hidden from the manager. Single-pp wobble flips quadrants silently. | classifier output | Medium | Surface a `boundaryDistance` field per item, or render boundary items in a softer color in the UI. |
| 8 | Cost-defense `_cost_defense` searches `[current_price, 3 × current_price]` upward only — fine, but for elastic items the new-cost optimum may be below current price. | `routes_menu.py:495` | Low | Search around the theoretical new optimum instead. |

---

## 7. Bottom line

- **Boston-Matrix quadrant logic:** mathematically clean, but the
  thresholds are biased by the choice of mean over median. ~1 in 5
  classifications is wrong on the popularity axis. **Fix needed.**
- **Constant-elasticity simulator (`/simulate`):** correct to textbook,
  no defects in the demand model itself. Confidence band, scenarios
  table, and break-even/cost-defense routines are sensible.
- **Price-suggestion engine (`/optimal`):** Stars and Plowhorses get
  sound advice. Puzzles and Dogs get a hardcoded discount that
  destroys money on the engine's own demand model whenever the item is
  inelastic — which is most coffee, all tea, and Saudi Coffee. **Fix
  needed.**
- **Bulk simulator:** correct in direction, off by a few hundred SAR
  per group due to staircase rounding. **Fix nice-to-have.**

The simulator-level math is one of the strongest pieces of the system.
The classifier and the optimisation strategy on top of it currently
mislead a manager on roughly half the menu — and the items they
mislead on are the cafe's bread-and-butter coffee SKUs.

---

## Appendix — artifacts

| file | what it is |
|------|-----------|
| `menu_eval.py` | evaluation harness; rerun with `python3 backend/eval/menu_eval.py` |
| `menu_items_classified.csv` | the full per-item table with classification + elasticity |
| `menu_quadrants.csv` | quadrant counts, qty, revenue, profit, median pop/margin |
| `menu_distributions.png` | per-item popularity / margin histograms with mean & median lines |
| `menu_boston_matrix.png` | full scatter, mean and median cutoffs overlaid |
| `menu_mean_vs_median_disagreement.csv` | the 29 items that flip between mean and median cutoffs |
| `menu_boundary_items.csv` | 32 items within ±0.20 pp pop / ±2 pp margin of cutoffs |
| `menu_elasticity_by_category.csv` | elasticity assignment per category |
| `menu_simulator_textbook_check.csv` | qty-projection vs textbook constant-elasticity, max abs err = 0.0 |
| `menu_price_suggestions.csv` | every item, suggested price, projected Δprofit |
| `menu_price_suggestions_profit_negative.csv` | the 73 suggestions that lose money |
| `menu_discount_floor_bug.csv` | intended vs realised cut on every Dog/Puzzle |
| `menu_imputed_drift.csv` | the 6 items whose classification would change if imputed rows were mixed in |
