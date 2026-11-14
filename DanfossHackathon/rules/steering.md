---
# ===== STEERING LOGIC (hydrostatic steering units and priority valves) =====
# DRAFT for the product team: standard attribute names and directions; weights not yet reviewed
# against steering sales guidelines (the student case material covers valves and open-circuit pumps).
# The engine works the moment steering facts are loaded.
# PART 1: settings the CODE reads. Everything here is a HINT for ranking, never a final verdict.
family: steering

attribute_aliases:
  displacement: max_displacement
  steering_displacement: max_displacement
  nominal_pressure: rated_pressure
  continuous_pressure: rated_pressure
  max_pressure_intermittent: peak_pressure
  rated_flow: max_flow
  steering_flow: max_flow
  mass: weight

weights:
  max_displacement: 5
  rated_pressure: 4
  max_flow: 3
  peak_pressure: 2
  weight: 1

better:
  rated_pressure: higher
  peak_pressure: higher
  max_flow: higher
  weight: lower

hard_filters:
  within:
    max_displacement: 0.3

size_attribute: max_displacement
---
# Skill: comparing steering units (PART 2: the AI reads this)

## What matters to customers
- Displacement (cm3/rev) decides how many steering-wheel turns move the cylinder lock to lock: it must
  fit the machine's steering cylinder, so a very different displacement is NOT a drop-in replacement.
- Rated pressure and flow decide whether the unit fits the vehicle class.
- Open-center vs load-sensing (and static vs dynamic LS) must match the rest of the hydraulic system:
  an LS steering unit belongs with an LS system.
- Emergency steering behaviour (manual steering when the engine dies) is a safety property: flag any
  difference loudly and tell the account manager to verify it.

## Watch out
- Never pair continuous values with peak/intermittent values.
- A steering unit and a priority valve are different components, even when sold together.
