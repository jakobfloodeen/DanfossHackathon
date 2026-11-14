---
# ===== GENERAL LOGIC – used for EVERY product family (pumps, valves, motors, systems, ...) =====
# PART 1: settings the CODE reads. A family file (rules/<family>.md) overrides any key it repeats.
# OTTO: change the numbers, keep the names. Everything here is a HINT for ranking, never a final verdict.

danfoss_brand: Danfoss

family_aliases:             # other names the PDF->CSV converter might write -> our family name
  piston pump: pump
  axial piston pump: pump
  open circuit pump: pump
  hydraulic pump: pump
  directional valve: valve
  proportional valve: valve
  hydraulic motor: motor
  # OTTO: add more as you see them in real files

weights: {}                 # attribute -> importance for ranking (0 = ignore, 5 = crucial). Family files fill this.
better: {}                  # attribute -> higher / lower (which direction is better). Family files fill this.
hard_filters:               # used only to RANK candidates lower (penalty) - customer must-haves are the real filters
  within: {}                # attribute -> candidate should be within +/- X (0.25 = 25 %) of the competitor
  at_least: {}              # attribute -> candidate should reach at least X times the competitor's value

size_attribute:             # the attribute that says how "big" a product is (pump: max_displacement).
                            # Empty -> first 'within' attribute, else the attribute with the highest weight.
size_window: 0.5            # shortlist only keeps candidates within +/- 50 % of that size (if enough exist)
shortlist_size: 8           # how many Danfoss candidates the AI gets to see (5-10)
similar_within: 0.03        # differences up to 3 % count as "similar"
conflict_tolerance: 0.02    # two sources disagreeing by more than 2 % = "conflict"
---
# General comparison logic (PART 2: the AI reads this for EVERY comparison)

## Your job
You help a Danfoss account manager prepare a customer meeting. A competitor product (the TARGET) is
compared with a short list of Danfoss CANDIDATES. You get numbered facts (F1, F2, ...) and code-computed
differences. You decide what is comparable, what matters for this customer, and which candidate fits best.

## How to decide what is comparable
- Attributes can have different names in different datasheets (e.g. "rated flow" vs "max flow per section").
  Pair them only if they really mean the same physical quantity under the same conditions.
- Never pair a continuous/rated value with a peak/intermittent value.
- If the products do different jobs (e.g. a pump vs a valve) say clearly that they are NOT comparable.
- OTTO: add general rules from the sales guidelines here (e.g. what makes two products "drop-in" replacements).

## What matters to customers
- OTTO: general priorities across product families (performance first? size/mounting? efficiency? service?)
- Use the customer context: application, must-haves, what they complain about today.

## Honesty rules
- "Not stated in source" is NOT the same as "worse". Say what is missing and that it must be checked.
- Facts with status unverified, conflict, missing or unknown unit go into "verify".
- A difference of a few percent is rarely a selling point - focus on the big differences.
- Calculated facts are our own calculation - say so.

## How to write
- Short, concrete, customer benefit first ("higher rated pressure leaves margin for ...").
- Every statement cites the fact ids it is based on.
- Only numbers that appear in the cited facts. No prices, delivery times, or knowledge from outside the facts.
