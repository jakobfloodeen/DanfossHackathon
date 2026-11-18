// ---------------------------------------------------------------
// Sales view: turns a comparison result into what a salesperson needs first.
// Pure logic, no DOM. app.js draws it. Everything here is computed by CODE from the
// backend's facts (never by the AI), so it can be unit-tested and never "sounds right but is wrong".
//
//   bottom line  -> fit (strong / partial / weak / unclear) + one plain sentence
//   scoreboard   -> the points that matter, with a winner mark per row
//   pitch cards  -> where we win / where they win / check before you promise
//   status       -> only three labels: Verified, Check, Unknown
// ---------------------------------------------------------------
const SalesView = (() => {
  // ---- plain-language lines: what the attribute means for the customer -------------------
  // Keys = the engine's standard attribute names (rules/*.md) plus the ids used in examples/.
  const PLAIN = {
    rated_flow_per_section: "How much oil one valve section can pass. More flow means faster movement of the machine.",
    max_inlet_flow: "How much oil the whole valve stack can take from the pump.",
    max_pressure_continuous: "The pressure it can run at all day. Sets how heavy a load it can push.",
    max_pressure_intermittent: "Short pressure peaks it can survive. Not for everyday running.",
    max_work_sections: "How many machine functions (lift, tilt, steer ...) one valve stack can control.",
    actuation_options: "How the valve is operated: lever, electric signal or CAN bus.",
    work_port_threads: "Hose connection types. Must fit the hoses the customer already has.",
    weight_per_section: "Weight. Lighter is easier on mobile machines.",
    oil_cleanliness: "How clean the oil must be. Stricter means more filter cost for the customer.",
    coil_current: "Electric current the control coil needs.",
    max_displacement: "Oil moved per turn. Sets how much flow the pump gives.",
    rated_pressure: "The pressure it can run at continuously. Sets what loads it can push.",
    peak_pressure: "Short pressure peaks it can take.",
    max_speed: "How fast it can spin. Faster means more flow from the same pump.",
    max_flow: "The most oil it can deliver per minute.",
    // ids used in examples/compare_response.json
    flowAB: "How much oil one valve section can pass. More flow means faster movement of the machine.",
    flowP: "How much oil the whole valve stack can take from the pump.",
    pressureCont: "The pressure it can run at all day. Sets how heavy a load it can push.",
    pressureInt: "Short pressure peaks it can survive. Not for everyday running.",
    sections: "How many machine functions (lift, tilt, steer ...) one valve stack can control.",
    compensation: "Whether each function keeps its speed when the load changes.",
    portsAB: "Hose connection types. Must fit the hoses the customer already has.",
    portsP: "Connection types for the oil supply. Must fit the customer's hoses.",
    actuation: "How the valve is operated: lever, electric signal or CAN bus.",
    weight: "Weight. Lighter is easier on mobile machines.",
    pitch: "Width of one section. Decides how much space the stack needs.",
    length: "Length of the finished stack. Decides if it fits in the machine.",
    temp: "Oil temperatures it can work in.",
    filtration: "How clean the oil must be. Stricter means more filter cost for the customer.",
    price: "What the customer pays.",
    lifetime: "How long it lasts in service.",
    availability: "How fast the customer can get it.",
  };
  const PLAIN_GUESS = [   // fallback by words in the label, for attributes nobody has described yet
    [/pressure/i, "How much pressure it can handle. Sets how heavy a load it can push."],
    [/flow/i, "How much oil it can pass. More flow means faster movement."],
    [/weight|mass/i, "Weight. Lighter is easier on mobile machines."],
    [/speed|rpm/i, "How fast it can run."],
    [/displacement/i, "Oil moved per turn. Sets how much flow it gives."],
    [/temperature|temp\b/i, "Temperatures it can work in."],
    [/thread|port/i, "Connection types. Must fit the customer's hoses."],
  ];
  function plainFor(attr) {
    if (attr.placeholder) return "Not found in either datasheet yet. Upload a document that states it, or ask the product team.";
    if (PLAIN[attr.id]) return PLAIN[attr.id];
    if (!attr.unit) return "";        // a guess by name only fits measured values, not features like "pressure compensator"
    const hit = PLAIN_GUESS.find(([re]) => re.test(attr.label || ""));
    return hit ? hit[1] : "";
  }

  // ---- which direction is "better" (fallback; the backend's rules/*.md `better:` map wins) ----
  const BETTER = {
    rated_flow_per_section: "higher", max_inlet_flow: "higher", max_pressure_continuous: "higher",
    max_pressure_intermittent: "higher", max_work_sections: "higher", max_displacement: "higher",
    rated_pressure: "higher", peak_pressure: "higher", max_speed: "higher", max_flow: "higher",
    weight_per_section: "lower", weight: "lower",
    flowAB: "higher", flowP: "higher", pressureCont: "higher", pressureInt: "higher", sections: "higher",
  };
  const SIMILAR = 0.03;   // within 3 % counts as level (same default as the engine)

  // ---- small helpers -------------------------------------------------------------------
  const hasValue = (v) => !!v && v.value != null && v.status !== "pending" && v.status !== "na";
  const pureNumber = (s) => /^\s*[-+]?\d+(?:[.,]\d+)?\s*$/.test(String(s ?? ""));
  const toNumber = (s) => parseFloat(String(s).replace(",", "."));
  /** "40 (with 4 bar ...)" -> 40; "-30 to 60", "P: 400", text -> null */
  const headNumber = (s) => {
    const t = String(s ?? "").trim();
    const m = t.match(/^([-+]?\d+(?:[.,]\d+)?)(?!\s*(?:to|–|-)\s*\d)(?=\s|$|\(|·)/);
    return m ? toNumber(m[1]) : null;
  };
  const norm = (s) => String(s ?? "").toLowerCase().replace(/\s+/g, " ").trim();

  /** "Max. flow per work section (A/B)" -> "max. flow per work section" */
  function shortLabel(label) {
    const s = String(label || "").replace(/\s*\([^)]*\)\s*/g, " ").replace(/\s+/g, " ").trim();
    return s.charAt(0).toLowerCase() + s.slice(1);
  }
  const niceLabel = (label) => { const s = shortLabel(label); return s.charAt(0).toUpperCase() + s.slice(1); };

  /** Cut at the first full sentence, or at a word boundary, so a card line stays one glance long. */
  function shorten(text, max = 130) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    if (t.length <= max) return t;
    const sentence = t.match(/^.{20,}?[.!?](?=\s|$)/);
    if (sentence && sentence[0].length <= max) return sentence[0];
    const cut = t.slice(0, max).replace(/\s+\S*$/, "");
    return cut.replace(/[,;:(\-]\s*$/, "") + "…";
  }

  /** "140 (230 with PVPM)" -> { big: "140", rest: "(230 with PVPM)" }; text values keep big = "". */
  function splitValue(text) {
    const t = String(text ?? "").trim();
    const m = t.match(/^([-+]?\d[\d.,]*(?:\s?(?:to|–|-)\s?[-+]?\d[\d.,]*)?)(?=\s|$|\()\s*(.*)$/);
    return m ? { big: m[1], rest: m[2] } : { big: "", rest: t };
  }

  /** The three status labels the salesperson sees. Detail stays in tooltips. */
  function simpleStatus(status, hasVal) {
    if (!hasVal || status === "pending" || status === "na" || status === "missing") return { key: "unknown", label: "Unknown", icon: "?" };
    if (["ok", "converted", undefined, null, ""].includes(status)) return { key: "good", label: "Verified", icon: "✓" };
    return { key: "check", label: "Check", icon: "!" };
  }
  const STATUS_WHY = {
    conflict: "Sources disagree", sample: "Sample data, not a real datasheet value", outdated: "Only an old source supports this",
    unverified: "Not yet confirmed by a person", inferred: "Calculated or read from a drawing, not stated in a datasheet",
    converted: "Unit converted by the system", missing: "Not stated in the datasheet", pending: "Waiting for data extraction",
    warning: "Worth a second look", error: "Problem", na: "No matching value",
  };

  // ---- who is "us" ---------------------------------------------------------------------
  const isDanfoss = (col) => /danfoss/i.test(`${col?.manufacturer || ""} ${col?.name || ""}`);

  /** Verdict for one row and one competitor column, from the point of view of the first column (anchor). */
  function edgeOf(attr, anchor, match, oursIsAnchor) {
    const a = attr.values[anchor.key], m = attr.values[match.key];
    if (!hasValue(a) || !hasValue(m)) return { kind: "unknown" };
    if (a.status === "conflict" || m.status === "conflict") return { kind: "check" };
    let side = null;                       // "anchor" | "match" | "level" | "differs"
    const b = attr.better ? attr.better[match.key] : undefined;      // computed by the backend's code
    if (b === "candidate") side = "match";
    else if (b === "target") side = "anchor";
    else if (b === "similar") side = "level";
    else if (norm(a.value) === norm(m.value)) side = "level";
    let approx = false;
    if (side === null && b === undefined && BETTER[attr.id]) {
      // plain numbers: exact. Numbers with conditions ("40 (with 4 bar ...)"): compare the headline number,
      // but mark it "likely" so the salesperson knows the conditions differ. Ranges ("-30 to 60") are never compared.
      const ha = headNumber(a.value), hm = headNumber(m.value);
      if (ha != null && hm != null) {
        approx = !(pureNumber(a.value) && pureNumber(m.value));
        const rel = Math.abs(ha - hm) / (Math.abs(ha) || 1);
        side = rel <= SIMILAR ? "level" : (BETTER[attr.id] === "higher") === (hm > ha) ? "match" : "anchor";
      }
    }
    if (side === null) side = "differs";
    if (side === "level") return { kind: "level", approx };
    if (side === "differs") return { kind: "differs" };
    const winner = side === "anchor" ? anchor : match;
    return { kind: winner._ours ? "ahead" : "behind", winner, approx };
  }

  const VERDICT = {   // label shown next to a row (we = Danfoss)
    ahead:   { label: "We're ahead",   likely: "Likely ahead",  tone: "win" },
    behind:  { label: "They're ahead", likely: "Likely behind", tone: "lose" },
    level:   { label: "Level",         likely: "Likely level",  tone: "level" },
    differs: { label: "Different",     tone: "level" },
    check:   { label: "Check data",    tone: "check" },
    unknown: { label: "Can't tell",    tone: "unknown" },
  };

  // ---- the analysis --------------------------------------------------------------------
  function analyse(r, factors = {}, buyer = {}, factorLabels = {}) {
    const cols = [r.anchor, ...(r.matches || [])];
    const anyDan = cols.some(isDanfoss);
    cols.forEach((c, i) => { c._ours = anyDan ? isDanfoss(c) : i === 0; });
    const oursIsAnchor = !!cols[0]._ours;
    const rank = (a) => ({ must: 0, nice: 1 }[factors[a.factor]] ?? 2);

    // Rows the scoreboard can use: everything comparable on both sides ("core"), PLUS, for every priority the
    // salesperson chose, its best rows even when only one side has a value - a chosen priority never disappears.
    const MAX_PER_PRIORITY = 2;
    const all = r.attributes || [];
    const extra = [];
    Object.keys(factors).forEach((f) => {
      const haveCore = all.some((a) => a.core !== false && a.factor === f);
      if (haveCore) return;
      const oneSided = all.filter((a) => a.core === false && a.factor === f && cols.some((c) => hasValue(a.values[c.key])))
        .sort((x, y) => cols.filter((c) => hasValue(y.values[c.key])).length - cols.filter((c) => hasValue(x.values[c.key])).length);
      if (oneSided.length) extra.push(...oneSided.slice(0, MAX_PER_PRIORITY));
      else extra.push({ id: "_none_" + f, factor: f, label: factorLabels[f] || f, unit: "", values: {}, placeholder: true });
    });
    const rows = [...all.filter((a) => a.core !== false), ...extra]
      .map((attr, i) => ({
        attr, i, prio: factors[attr.factor] || null,
        verdicts: (r.matches || []).map((m) => edgeOf(attr, r.anchor, m, oursIsAnchor)),
        anyData: attr.placeholder || cols.some((c) => hasValue(attr.values[c.key])),
      }))
      .filter((x) => x.anyData)
      .sort((x, y) => rank(x.attr) - rank(y.attr) || x.i - y.i);

    // the points that matter: chosen must-haves, else chosen nice-to-haves, else the first rows with a verdict
    const musts = rows.filter((x) => x.prio === "must");
    const nices = rows.filter((x) => x.prio === "nice");
    const priorityChosen = musts.length > 0;
    const pool = musts.length ? musts : nices.length ? nices : rows.filter((x) => x.verdicts[0] && x.verdicts[0].kind !== "unknown").slice(0, 8);
    const MAX_ROWS = 8;
    const prioRows = rows.filter((x) => x.prio);
    const shown = (prioRows.length ? [...prioRows, ...rows.filter((x) => !x.prio)] : rows).slice(0, MAX_ROWS);

    // Several competitors: the bottom line is about the one we can judge best (most must-have points decided),
    // the others get a one-line summary. Wins and losses below cover ALL competitors.
    const countFor = (j) => {
      const k = (x) => (x.verdicts[j] ? x.verdicts[j].kind : "unknown");
      const c = { ahead: 0, level: 0, behind: 0, open: 0 };
      pool.forEach((x) => { const v = k(x); if (v === "ahead") c.ahead++; else if (v === "behind") c.behind++; else if (v === "level") c.level++; else c.open++; });
      return c;
    };
    const perMatch = (r.matches || []).map((m, j) => ({ m, j, c: countFor(j) }));
    const decidedOf = (p) => p.c.ahead + p.c.level + p.c.behind;
    const focusIdx = perMatch.length ? perMatch.reduce((best, p) => (decidedOf(p) > decidedOf(best) ? p : best), perMatch[0]).j : 0;
    const primary = (r.matches || [])[focusIdx] || null;
    const kindOf = (x) => (x.verdicts[focusIdx] ? x.verdicts[focusIdx].kind : "unknown");
    const sel = (list, ...kinds) => list.filter((x) => kinds.includes(kindOf(x)));
    const ahead = sel(pool, "ahead"), behind = sel(pool, "behind"), level = sel(pool, "level");
    const open = sel(pool, "unknown", "check", "differs");
    const decided = ahead.length + behind.length + level.length;
    const others = perMatch.filter((p) => p.j !== focusIdx).map((p) => ({ name: p.m.name, counts: p.c }));

    let fit = "unclear";
    if (primary && decided >= 2 && decided >= pool.length / 3) {
      if (behind.length / decided >= 0.5) fit = "weak";
      else if (behind.length === 0 && open.length === 0) fit = "strong";
      else fit = "partial";
    }
    const FIT_LABEL = { strong: "Strong fit", partial: "Partial fit", weak: "Weak fit", unclear: "Not enough data" };

    // ---- plain headline --------------------------------------------------------------
    const names = (list, n = 3) => {
      const l = list.slice(0, n).map((x) => shortLabel(x.attr.label));
      const extra = list.length - n;
      if (extra > 0) return `${l.join(", ")} and ${extra} more`;
      return l.length > 1 ? `${l.slice(0, -1).join(", ")} and ${l[l.length - 1]}` : l[0];
    };
    const ourName = (oursIsAnchor ? r.anchor : primary)?.name || "Danfoss";
    const theirName = (oursIsAnchor ? primary : r.anchor)?.name || "the competitor";
    let headline, detail = [];
    if (!primary) {
      headline = "No comparable product with data yet.";
      detail.push("Upload a competitor datasheet, or pick another product, to get a comparison.");
    } else {
      const verb = { strong: "is a strong fit against", partial: "is a partial fit against", weak: "is a weak fit against", unclear: "cannot be fully judged yet against" }[fit];
      headline = `${ourName} ${verb} ${theirName}.`;
      if (ahead.length) detail.push(`We are ahead on ${names(ahead)}.`);
      if (level.length) detail.push(`Level on ${names(level)}.`);
      if (behind.length) detail.push(`They are ahead on ${names(behind)}.`);
      const unk = sel(pool, "unknown", "differs"), chk = sel(pool, "check");
      if (chk.length) detail.push(`${chk.length} point${chk.length > 1 ? "s" : ""} to double-check before quoting.`);
      if (unk.length) detail.push(`${unk.length} point${unk.length > 1 ? "s" : ""} cannot be judged (no data on one side).`);
      if (!detail.length) detail.push("There is not enough data on both sides to say who is ahead.");
    }
    const basisWho = (r.matches || []).length > 1 && primary ? ` Judged against ${primary.name}: the competitor with the most comparable data.` : "";
    const basis = pool.length
      ? `Based on the ${pool.length} ${priorityChosen ? "must-have" : nices.length ? "nice-to-have" : "main"} point${pool.length > 1 ? "s" : ""}${buyer.company ? ` for ${buyer.company}` : ""}.${basisWho}`
      : "";

    // ---- pitch cards ------------------------------------------------------------------
    const val = (x, col) => {
      const v = x.attr.values[col.key]; const s = splitValue(v.value);
      return s.big ? `${s.big}${x.attr.unit ? " " + x.attr.unit : ""}` : shorten(v.value, 40);
    };
    const many = (r.matches || []).length > 1;
    const srcOf = (x, col) => [...new Set([r.anchor, col].flatMap((c) => (c && x.attr.values[c.key]?.sources) || []))];
    const pairFor = (x, j) => {
      const m = r.matches[j];
      const o = oursIsAnchor ? r.anchor : m, t = oursIsAnchor ? m : r.anchor;
      const v = x.verdicts[j];
      return `${niceLabel(x.attr.label)}: ${val(x, o)} vs ${val(x, t)}${many ? ` (${t.name})` : ""}${v.approx ? " (probably, conditions differ)" : ""}`;
    };
    const across = (kind) => rows.flatMap((x) => x.verdicts.map((v, j) => ({ x, v, j })).filter((e) => e.v.kind === kind))
      .sort((p, q) => (p.j === focusIdx ? -1 : 0) - (q.j === focusIdx ? -1 : 0))
      .map(({ x, j }) => ({ text: pairFor(x, j), plain: plainFor(x.attr), sources: srcOf(x, r.matches[j]) }));
    const wins = primary ? across("ahead") : [];
    const losses = primary ? across("behind") : [];
    const checks = [];
    if (primary) {
      rows.filter((x) => kindOf(x) === "check").forEach((x) => checks.push({ text: `${niceLabel(x.attr.label)}: sources disagree. Confirm before quoting.`, sources: srcOf(x, primary) }));
      musts.filter((x) => kindOf(x) === "unknown").forEach((x) => checks.push({ text: `${niceLabel(x.attr.label)}: no value for ${hasValue(x.attr.values[r.anchor.key]) ? primary.name : r.anchor.name} yet.`, sources: srcOf(x, primary) }));
    }
    // every open point the print sheet lists, most serious first (a conflict already named above is not repeated)
    const FLAG_ORDER = ["conflict", "unverified", "outdated", "missing", "warning", "inferred", "sample", "pending"];
    [...(r.flags || [])].filter((f) => f.level !== "ok")
      .sort((x, y) => (FLAG_ORDER.indexOf(x.level) + 99) % 99 - (FLAG_ORDER.indexOf(y.level) + 99) % 99)
      .forEach((f) => {
        if (f.level === "conflict" && checks.some((c) => /sources disagree/.test(c.text))) return;
        checks.push({ text: shorten(f.text, 160), full: f.text });
      });

    return { cols, oursIsAnchor, primary, rows, shown, restCount: Math.max(0, rows.length - shown.length),
             pool, priorityChosen, counts: { ahead: ahead.length, level: level.length, behind: behind.length, open: open.length },
             fit, fitLabel: FIT_LABEL[fit], headline, detail, basis, wins, losses, checks, others, focusKey: primary?.key };
  }

  return { analyse, plainFor, splitValue, simpleStatus, STATUS_WHY, VERDICT, shorten, shortLabel, niceLabel, edgeOf, isDanfoss };
})();
if (typeof module !== "undefined") module.exports = SalesView;
