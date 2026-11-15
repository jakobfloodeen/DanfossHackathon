---
# ===== GENERAL LOGIC – used for EVERY product family (pumps, valves, motors, steering, systems) =====
# PART 1: settings the CODE reads. A family file (rules/<family>.md) overrides any key it repeats.
# Everything here is a HINT for ranking, never a final verdict.

danfoss_brand: Danfoss

family_aliases:             # other names the PDF->CSV converter might write -> our family name
  piston pump: pump
  axial piston pump: pump
  open circuit pump: pump
  hydraulic pump: pump
  gear pump: pump
  directional valve: valve
  directional control valve: valve
  proportional valve: valve
  proportional valve group: valve
  proportional valve groups: valve
  control block: valve
  load sensing control block: valve
  monoblock valve: valve
  mono block valve: valve
  sectional valve: valve
  valve group: valve
  hydraulic motor: motor
  orbital motor: motor
  axial piston motor: motor
  piston motor: motor
  steering unit: steering
  hydraulic steering unit: steering
  steering valve: steering
  valves: valve             # the website's catalog family ids
  pumps: pump
  motors: motor
  steering: steering

# Recognising the same quantity under different datasheet names (comparison_engine/attributes.py).
# Two names match when they have the same words after these rules - word order does not matter.
# Built-in defaults already cover: hydraulic fluid/fluid -> oil, maximum -> max, minimum -> min,
# temp -> temperature, mass -> weight, contamination -> cleanliness, and the filler words range,
# permissible, allowable, iso4406 ... Add your own here:
attribute_words:
  same: {}                  # e.g.  nominal: rated   (only if it really means the same everywhere!)
  ignore: []                # e.g.  [operating]

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
You help a Danfoss account manager prepare a customer meeting. One product (the TARGET) is compared with
a short list of CANDIDATES: either a competitor product vs Danfoss candidates ("which Danfoss product
replaces it?") or a Danfoss product vs competitor candidates ("how do we compare?"). The prompt says which.
You get numbered facts (F1, F2, ...) and code-computed differences. You decide what is comparable, what
matters for this customer, and which candidate is the closest match.

## How to decide what is comparable
- Attributes can have different names in different datasheets (e.g. "rated flow" vs "max flow per section").
  Pair them only if they really mean the same physical quantity under the same conditions.
- Never pair a continuous/rated value with a peak/intermittent value.
- If the products do different jobs (e.g. a pump vs a valve) say clearly that they are NOT comparable.
- A "drop-in" replacement needs more than matching performance: mounting, ports/threads, envelope and
  control interface must fit too. When the facts do not cover those, say the fit must be verified -
  never assume it.

## What matters to customers
- First performance fit (can it drive the machine: flow/displacement, pressure class, sections), then
  physical fit (mounting, ports, size, weight), then controls (load sensing, proportional/CAN actuation),
  then efficiency and service. The customer's own priorities override this order - use the buyer context:
  application, must-haves, what they complain about today.
- Danfoss sells the system as well as the component: when the facts show components that fit together
  (shared platform, matching LS, matching pressure class) this is worth a talking point.

## Honesty rules
- "Not stated in source" is NOT the same as "worse". Say what is missing and that it must be checked.
- "verify": at most 3 doubtful facts (unverified, conflict, missing, unknown unit) - the ones that matter most for
  this customer. Code adds all other doubtful facts itself.
- A difference of a few percent is rarely a selling point - focus on the big differences.
- Calculated facts are our own calculation - say so.

## How to write
- Short, concrete, customer benefit first ("higher rated pressure leaves margin for ...").
- Every statement cites the fact ids it is based on.
- Only numbers that appear in the cited facts. No prices, delivery times, or knowledge from outside the facts.
