---
# ===== PUMP LOGIC (open circuit piston pumps) =====
# Filled from "02 Open Circuit Pumps 2026 Q1 Sales Guidelines" (slide numbers in comments).
# PART 1: settings the CODE reads. Everything here is a HINT for ranking, never a final verdict.
family: pump

attribute_aliases:
  displacement: max_displacement
  geometric_displacement: max_displacement
  max_displacement_per_rev: max_displacement
  nominal_pressure: rated_pressure
  continuous_pressure: rated_pressure
  rated_operating_pressure: rated_pressure
  max_pressure_intermittent: peak_pressure
  intermittent_pressure: peak_pressure
  max_rpm: max_speed
  rated_speed: max_speed
  max_rotational_speed: max_speed
  mass: weight

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
  within:                   # candidate should be within +/- X of the competitor's value
    max_displacement: 0.25
  at_least:                 # candidate should reach at least X times the competitor's value
    rated_pressure: 0.9

size_attribute: max_displacement
similar_within: 0.03        # differences up to 3 % count as "similar"
conflict_tolerance: 0.02    # two sources disagreeing by more than 2 % = "conflict"
---
# Skill: comparing open circuit pumps (PART 2: the AI reads this)

## Who you write for
A Danfoss account manager preparing for a customer meeting in a few hours. Be short, concrete, honest.

## What matters to customers (OCP Sales Guidelines 2026, slides 2-10)
- Displacement and speed decide the flow the machine gets - the first thing to match.
- The pressure class decides the duty cycle: the medium-power range (Series 45 frames K2/J/F/E, X1P C,
  X20 220) works up to ~260-310 bar; the high-power D1P family reaches 320-350 bar. A 350 bar
  requirement rules out a medium-power frame - check rated (continuous) pressure, not peak.
- Controls decide application fit: pressure compensation (PC), load sensing (PC/LS), electric
  displacement control (EDC), fan drive control (FDC), torque limiting (ETL). Load sensing matters in
  telehandlers, cranes and loaders; FDC in fan drives. A customer using LS needs an LS-capable pump.
- Mounting, shaft and through-drive decide whether the pump physically replaces the competitor pump
  (flange standard, splined/tapered shaft, auxiliary pads for tandem pumps).
- Typical segments: skid steers, telehandlers, drill rigs, wheel loaders (medium power); concrete pump
  trucks, drilling, cranes, tunneling (high power, slides 5 and 9).

## Watch out
- Never compare rated pressure with peak pressure - they are different attributes.
- Displacement classes: Danfoss frames step 18-290cc (S45 K2 25-45, J 45-75, F 74-90, E 100-147,
  X1P C 45-75, D1P 65-290). The nearest frame above the competitor's displacement is usually the match.
- "Not stated in source" is not the same as "worse". Say what is missing and that it must be checked.
- Values marked unverified or conflict must go to "Verify before the meeting".
- A difference of a few percent is rarely a selling point - focus on the big differences.

## How to write the answer
- Every bullet cites its fact ids.
- Only numbers that appear in the cited facts. No prices, delivery times or claims that are not in the facts.
- Talking points = customer benefit, not a spec list ("higher rated pressure leaves margin for ...").
