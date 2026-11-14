---
# ===== SYSTEM LOGIC =====
# A "system" = several components that work together: pump + valve, or pump + valve + motor + steering.
# Facts of each component keep their name with the component in front:  pump__max_flow, valve__max_inlet_flow.
# PART 1: settings the CODE reads.
family: system

# System values calculated by CODE from the component facts (never by the AI).
#   op:      min | max | sum (2 or more inputs; components the system does not have are simply left out)
#            difference | ratio (exactly 2 inputs; skipped when a named component is not in the system)
#   inputs:  <component>.<attribute>   (standard attribute names, see rules/pump.md, valve.md, motor.md, steering.md)
#   scale:   multiply the result (flow [l/min] / displacement [cm3/rev] needs x1000 to become rpm)
#   unit:    unit of the result when it is not the inputs' unit
# If an input is missing, in conflict or has the wrong unit, the system value is shown as "cannot calculate"
# with the reason - it is never guessed.
system_rules:
  - name: max_flow
    op: min
    inputs: [pump.max_flow, valve.max_inlet_flow]
    explain: the system can only use the smaller of pump flow and valve inlet flow
  - name: pump_to_valve_flow_ratio
    op: ratio
    inputs: [pump.max_flow, valve.max_inlet_flow]
    explain: above 1 the pump delivers more than the valve inlet accepts (oversized pump or extra inlet needed)
  - name: max_pressure_continuous
    op: min
    inputs: [pump.rated_pressure, valve.max_pressure_continuous, motor.rated_pressure, steering.rated_pressure]
    explain: the weakest component limits the continuous system pressure
  - name: motor_speed_at_full_flow
    op: ratio
    inputs: [pump.max_flow, motor.max_displacement]
    scale: 1000
    unit: rpm
    explain: pump flow through the motor gives roughly this speed - check it against the motor's max speed
  - name: steering_share_of_pump_flow
    op: ratio
    inputs: [steering.max_flow, pump.max_flow]
    explain: the share of pump flow the steering priority can claim; the rest remains for the work functions
  - name: total_weight
    op: sum
    inputs: [pump.weight, valve.weight, motor.weight, steering.weight]
    explain: weight of the listed components (valves stated only per section are not included)

weights:                    # ranking hints for system values
  system__max_flow: 5
  system__max_pressure_continuous: 4
  system__pump_to_valve_flow_ratio: 2
  system__motor_speed_at_full_flow: 2
  system__steering_share_of_pump_flow: 1
  system__total_weight: 1

better:
  system__max_flow: higher
  system__max_pressure_continuous: higher
  system__total_weight: lower
---
# Skill: comparing systems of components (PART 2: the AI reads this)

## How to read the facts
- Attribute names start with the component: "pump__max_flow" is the pump's max flow, "valve__..." the valve's.
- "system__..." values were calculated by code from the component facts (formula in the quote). They show how
  well the components fit TOGETHER - this is the main point of a system comparison.
- A system value that cannot be calculated means a component fact is missing: say which one must be checked.

## What matters in a system deal (SANY mining-truck value-selling case)
- Do the components fit each other? Flow (pump vs valve inlet), pressure class (the weakest part limits
  the system), load sensing on both pump and valve, motor speed inside its rating, enough priority flow
  left for steering. A mismatch costs energy, performance or component life - point it out.
- Fewer components and connections is itself a selling point: fewer tubes, fittings and potential leak
  points, less space, less weight, shorter assembly time. Say so when one side needs fewer parts for
  the same functions - and only then.
- A higher system pressure rating can allow smaller actuators for the same force (compact, lighter
  machine) - mention it only when the pressure facts support it.
- One supplier for the whole system (support, compatibility, one contact) can be a benefit - say it as
  a general benefit, not as a fact about this customer.
- Compare component by component AND as a whole; a system can be better even if one component is weaker.

## Honesty rules for systems
- Never invent a system value: only use the system__ facts calculated by code.
- If components differ in count or role (one side has no separate motor), say what that means for the
  comparison instead of forcing a verdict.
