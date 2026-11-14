---
# ===== MOTOR LOGIC (hydraulic motors: orbital and piston) =====
# DRAFT for the product team: the attribute names and directions are standard hydraulics; the weights
# and filters have not been reviewed against motor sales guidelines yet (the student case material
# covers valves and open-circuit pumps). The engine works the moment motor facts are loaded.
# PART 1: settings the CODE reads. Everything here is a HINT for ranking, never a final verdict.
family: motor

attribute_aliases:
  displacement: max_displacement
  geometric_displacement: max_displacement
  nominal_pressure: rated_pressure
  continuous_pressure: rated_pressure
  max_pressure_intermittent: peak_pressure
  intermittent_pressure: peak_pressure
  max_rpm: max_speed
  rated_speed: max_speed
  max_torque: rated_torque
  continuous_torque: rated_torque
  output_torque: rated_torque
  mass: weight

weights:
  max_displacement: 5
  rated_torque: 4
  rated_pressure: 4
  max_speed: 3
  peak_pressure: 2
  weight: 1

better:
  rated_torque: higher
  rated_pressure: higher
  peak_pressure: higher
  max_speed: higher
  weight: lower

hard_filters:
  within:
    max_displacement: 0.25
  at_least:
    rated_pressure: 0.9

size_attribute: max_displacement
---
# Skill: comparing hydraulic motors (PART 2: the AI reads this)

## What matters to customers
- Displacement and the available flow decide the speed (speed ~ flow / displacement); displacement and
  pressure decide the torque. Match displacement first, then check that speed and torque ratings hold.
- Rated (continuous) pressure and torque decide the duty cycle; peak values only cover short spikes.
- An orbital motor (low speed, high torque) and an axial piston motor do different jobs in many
  machines: say so when the types differ instead of declaring a winner.
- Mounting flange, shaft and port sizes decide whether it is a drop-in replacement.

## Watch out
- Never pair continuous values with peak/intermittent values.
- Speed limits often depend on displacement (variable motors): compare like with like, and name the
  condition when the datasheet states one.
