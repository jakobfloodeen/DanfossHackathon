---
# ===== VALVE LOGIC =====
# Filled from "02 Spool Valves 2026 Q1 Sales Guidelines" (slide numbers in comments).
# PART 1: settings the CODE reads. Overrides _general.md where the same name is used.
# Everything here is a HINT for ranking, never a final verdict.
family: valve

# STANDARD ATTRIBUTE NAMES. Datasheets name things differently; the engine renames every alias on the
# left to the standard name on the right when it loads the CSVs, so both products land in ONE table row.
# Only add an alias if it really is the same quantity under the same conditions
# (e.g. NOT "max pressure" -> continuous pressure: a leaflet's "max" can be peak or continuous).
attribute_aliases:
  rated_flow: rated_flow_per_section
  max_flow_per_section: rated_flow_per_section
  nominal_flow_per_section: rated_flow_per_section
  nominal_flow: rated_flow_per_section
  work_port_flow: rated_flow_per_section
  inlet_flow: max_inlet_flow
  max_pump_flow: max_inlet_flow
  max_flow_inlet: max_inlet_flow
  rated_pressure: max_pressure_continuous
  continuous_pressure: max_pressure_continuous
  continuous_operating_pressure: max_pressure_continuous
  max_operating_pressure_continuous: max_pressure_continuous
  peak_pressure: max_pressure_intermittent
  intermittent_pressure: max_pressure_intermittent
  max_sections: max_work_sections
  number_of_sections: max_work_sections
  work_sections: max_work_sections
  actuation: actuation_options
  actuation_type: actuation_options
  control_type: actuation_options
  control_options: actuation_options
  port_sizes: work_port_threads
  port_size: work_port_threads
  work_port_size: work_port_threads
  weight_work_section: weight_per_section
  section_weight: weight_per_section
  oil_cleanliness_iso4406: oil_cleanliness
  rated_current: coil_current
  rated_flow_port_p: max_inlet_flow          # converter style: flow at the P (inlet) port
  max_flow_port_p: max_inlet_flow
  rated_flow_port_ab: rated_flow_per_section
  max_flow_port_ab: rated_flow_per_section
  max_pressure_continuous_ab: max_pressure_continuous
  max_pressure_port_ab_continuous: max_pressure_continuous
  max_pressure_intermittent_ab: max_pressure_intermittent
  max_pressure_port_ab_intermittent: max_pressure_intermittent

weights:                    # importance for ranking candidates (0 = ignore, 5 = crucial)
  rated_flow_per_section: 5
  max_pressure_continuous: 4
  max_inlet_flow: 3
  max_work_sections: 3
  actuation_options: 2
  max_pressure_intermittent: 2
  work_port_threads: 1
  weight_per_section: 1

better:                     # which direction wins (leave out = no winner, just show the difference)
  rated_flow_per_section: higher
  max_inlet_flow: higher
  max_pressure_continuous: higher
  max_pressure_intermittent: higher
  max_work_sections: higher
  weight_per_section: lower

hard_filters:               # used to RANK candidates lower, never to hide them
  within:
    rated_flow_per_section: 0.35      # a valve sized far off the competitor's flow class is a poor match
  at_least:
    max_pressure_continuous: 0.9      # should roughly reach the competitor's pressure class

size_attribute: rated_flow_per_section
---
# Skill: comparing directional / proportional valves (PART 2: the AI reads this)

## What matters to customers (VPS Sales Guidelines Spool Valves, slides 4-13 and 38)
- Flow per work section and continuous pressure decide whether the valve can drive the machine's
  functions. The Danfoss PVG family covers 1-500 l/min (PVG16 to 65, PVG32 to ~125-130, PVG48 to 180,
  PVG128 65-300, PVG256 65-500 l/min; CVG8 to 40 l/min), all rated 350 bar.
- Number of work sections and the actuation technology decide system fit. Actuation runs from mechanical
  (PVM) and hydraulic (PVH) over ON/OFF (PVEO) and proportional analog (PVEA/PVEH/PVES) to proportional
  digital / CAN-bus (PVED). A customer on CAN-bus control cannot drop to a valve without it.
- Compensation type matters: pre-compensated sections give load-independent flow sharing; a
  non-compensated valve behaves differently under multi-function load. Say which type each product is
  when the datasheets state it.
- Load-holding and protection options are selling points: shock valves (PVLP), anti-cavitation (PVLA),
  leak-free sections (PVBZ) for load holding, LS relief per section (LSRV), neutral bleed-up protection, T0.
- Platform fit: PVG 16, 32 and 48 interface directly and share inlets; PVG 128/256 combine with each
  other and reach the small frames via an interface plate. A customer already on one frame can mix.
- Port threads/sizes decide whether the valve is a drop-in on existing hoses (slides 6-10 list them).

## When is a compact / mono-block valve comparable with a sectional valve group? (slide 38)
- Comparable when the job fits BOTH: needed flow per function, section count, pressure class and
  actuation are all inside both products' ranges (e.g. CVG8 only up to 40 l/min, 8 sections, 350 bar).
- A single-function valve (e.g. an implement-levelling or brake valve) and a sectional valve group do
  different jobs: say so instead of declaring a winner.

## Watch out
- Flow "per section" and flow "at the inlet / P port" are different quantities - never pair them.
- Never pair a continuous/rated pressure with a peak/intermittent one.
- Actuation options are text: compare them by listing what each side offers, not as a number.
