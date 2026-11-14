---
# ===== SYSTEM LOGIC (template - OTTO: check the rules with the product team) =====
# A "system" = several components that work together, e.g. a pump + a valve group.
# Facts of each component keep their name with the component in front:  pump__max_flow, valve__max_inlet_flow.
# PART 1: settings the CODE reads.
family: system

# System values calculated by CODE from the component facts (never by the AI).
#   op:      min | max | sum | difference | ratio      (ratio = first / second)
#   inputs:  <component>.<attribute>   (standard attribute names, see rules/pump.md and rules/valve.md)
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
    inputs: [pump.rated_pressure, valve.max_pressure_continuous]
    explain: the weaker component limits the continuous system pressure
  # OTTO: add rules, e.g. motor speed x displacement, total weight of all components

weights:                    # ranking hints for system values
  system__max_flow: 5
  system__max_pressure_continuous: 4
  system__pump_to_valve_flow_ratio: 2

better:
  system__max_flow: higher
  system__max_pressure_continuous: higher
---
# Skill: comparing systems of components (PART 2: the AI reads this)

## How to read the facts
- Attribute names start with the component: "pump__max_flow" is the pump's max flow, "valve__..." the valve's.
- "system__..." values were calculated by code from the component facts (formula in the quote). They show how
  well the components fit TOGETHER - this is the main point of a system comparison.
- A system value that cannot be calculated means a component fact is missing: say which one must be checked.

## What matters  (OTTO: fill from the sales guidelines)
- Do pump and valve fit each other (flow, pressure, load sensing on both)? A mismatch costs energy or performance.
- One supplier for the whole system (support, compatibility) can be a benefit - only say so if the facts allow it.
- Compare component by component AND as a whole; a system can be better even if one component is weaker.
