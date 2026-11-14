---
# ===== VALVE LOGIC (template - OTTO: check every line against the sales guidelines) =====
# PART 1: settings the CODE reads. Overrides _general.md where the same name is used.
family: valve

# STANDARD ATTRIBUTE NAMES. Datasheets name things differently; the engine renames every alias on the
# left to the standard name on the right when it loads the CSVs, so both products land in ONE table row.
# Only add an alias if it really is the same quantity under the same conditions
# (e.g. NOT "max pressure" -> continuous pressure: a leaflet's "max" can be peak or continuous).
attribute_aliases:
  rated_flow: rated_flow_per_section
  max_flow_per_section: rated_flow_per_section
  nominal_flow_per_section: rated_flow_per_section
  inlet_flow: max_inlet_flow
  max_pump_flow: max_inlet_flow
  rated_pressure: max_pressure_continuous
  continuous_pressure: max_pressure_continuous
  peak_pressure: max_pressure_intermittent
  intermittent_pressure: max_pressure_intermittent
  max_sections: max_work_sections
  number_of_sections: max_work_sections
  actuation: actuation_options
  control_options: actuation_options
  port_sizes: work_port_threads
  weight_work_section: weight_per_section
  section_weight: weight_per_section
  oil_cleanliness_iso4406: oil_cleanliness
  rated_current: coil_current
  # OTTO: add the names the PDF->CSV converter produces for real datasheets

weights:                    # importance for ranking candidates (0 = ignore, 5 = crucial)
  rated_flow_per_section: 5
  max_pressure_continuous: 4
  max_inlet_flow: 3
  max_work_sections: 3
  max_pressure_intermittent: 2
  weight_per_section: 1

better:                     # which direction wins (leave out = no winner, just show the difference)
  rated_flow_per_section: higher
  max_inlet_flow: higher
  max_pressure_continuous: higher
  max_pressure_intermittent: higher
  max_work_sections: higher
  weight_per_section: lower

size_attribute: rated_flow_per_section
---
# Skill: comparing directional / proportional valves (PART 2: the AI reads this)

## What matters to customers  (OTTO: fill this from the sales guidelines / tech day decks)
- Flow per work section and continuous pressure decide whether the valve can drive the machine's functions.
- Number of work sections and actuation (manual, hydraulic, electro-hydraulic, CAN) decide system fit.
- (add: when is a mono-block valve comparable with a sectional valve group?)
- (add: which load-holding / compensation functions matter in which application?)

## Watch out
- A single-function valve (e.g. an implement-levelling valve) and a sectional valve group do different jobs:
  say so instead of declaring a winner.
- Flow "per section" and flow "at the inlet / P port" are different quantities.
