---
# ===== PART 1: settings the CODE reads (Otto: change the numbers, keep the names) =====
family: pump
danfoss_brand: Danfoss

weights:                    # how much each attribute counts for "best match" (0 = ignore, 5 = crucial)
  max_displacement: 5       # attributes listed here also set the row order in the comparison table
  rated_pressure: 4
  max_flow: 3
  peak_pressure: 2
  max_speed: 2
  weight: 1

better:                     # which direction wins (leave out = no winner, just show the difference)
  rated_pressure: higher
  peak_pressure: higher
  max_speed: higher
  max_flow: higher
  weight: lower

hard_filters:
  within:                   # candidate must be within +/- X of the competitor's value
    max_displacement: 0.25
  at_least:                 # candidate must reach at least X times the competitor's value
    rated_pressure: 0.9

similar_within: 0.03        # differences up to 3 % count as "similar"
conflict_tolerance: 0.02    # two sources disagreeing by more than 2 % = "conflict"
---
# Skill: comparing open circuit pumps (PART 2: the AI reads this)

## Who you write for
A Danfoss account manager preparing for a customer meeting in a few hours. Be short, concrete, honest.

## What matters to customers  (Otto: fill this from the sales guidelines / tech day decks)
- Displacement and speed decide the flow the machine gets - the first thing to match.
- Rated (continuous) pressure decides whether the pump survives the duty cycle.
- (add: which controls matter in which application, e.g. load sensing in cranes/telehandlers)
- (add: mounting / shaft / through-drive = can it physically replace the competitor pump?)

## Watch out
- Never compare rated pressure with peak pressure - they are different attributes.
- "Not stated in source" is not the same as "worse". Say what is missing and that it must be checked.
- Values marked unverified or conflict must go to "Verify before the meeting".
- A difference of a few percent is rarely a selling point - focus on the big differences.

## How to write the answer
- Every bullet cites its row ids, e.g. [R2, R4].
- Only numbers that appear in the table. No prices, delivery times or claims that are not in the rows.
- Talking points = customer benefit, not a spec list ("higher rated pressure leaves margin for ..." [R2]).
