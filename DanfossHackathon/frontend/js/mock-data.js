// ---------------------------------------------------------------
// MOCK DATA — stands in for the backend until it exists.
//
// The catalog is the single place that defines what the user can pick.
// To add a product family, valve type, product or competitor, add an
// entry here (or return it from GET /api/catalog). No UI code changes.
//
//   status: "active"       selectable, data available
//           "data-pending" selectable, but flagged as limited data
//           "coming-soon"  shown greyed out, not selectable
//   type:   "product"      a leaf the user compares (anything else is a group)
//   The tree can be any depth: family → type → product, or deeper.
// ---------------------------------------------------------------
window.MOCK_CATALOG = {
  version: "0.2-mock",
  rootPrompt: "Which product family do you want to compare?",
  tree: [
    {
      id: "valves",
      label: "Valves",
      desc: "Spool, cartridge and directional valves",
      status: "active",
      prompt: "Which type of valve?",
      children: [
        {
          id: "proportional",
          label: "Proportional valve groups (PVG)",
          desc: "Load-sensing proportional valve groups",
          status: "active",
          prompt: "Which Danfoss valve is the starting point?",
          children: [
            { id: "pvg16",  type: "product", label: "PVG 16",  desc: "No datasheet uploaded yet", status: "data-pending" },
            { id: "pvg32",  type: "product", label: "PVG 32",  desc: "Up to 125 l/min (A/B) · 350 bar continuous", status: "active",
              datasets: [{ name: "BC152886483664en-001602.pdf", source: "danfoss", competitorId: null, uploadedAt: "2026-11-13T23:00:00Z" }] },
            { id: "pvg48",  type: "product", label: "PVG 48",  desc: "No datasheet uploaded yet", status: "data-pending" },
            { id: "pvg128", type: "product", label: "PVG 128", desc: "No datasheet uploaded yet", status: "data-pending" },
            { id: "pvg256", type: "product", label: "PVG 256", desc: "No datasheet uploaded yet", status: "data-pending" },
          ],
        },
        { id: "cartridge", label: "Cartridge valves", desc: "Screw-in cartridge valves", status: "coming-soon" },
        { id: "noncomp",   label: "Non-compensated valves", desc: "Directional valves", status: "coming-soon" },
      ],
    },
    { id: "pumps",    label: "Pumps",    desc: "Open circuit pumps",       status: "coming-soon" },
    { id: "motors",   label: "Motors",   desc: "Hydraulic motors",         status: "coming-soon" },
    { id: "steering", label: "Steering", desc: "Steering units & systems", status: "coming-soon" },
  ],

  // Competitor companies (starting list from VPS_Customers_and_Competitors.xlsx).
  // hasData = at least one competitor product with an uploaded dataset.
  competitors: [
    { id: "rexroth",  name: "Bosch Rexroth",       hasData: false },
    { id: "bucher",   name: "Bucher Hydraulics",   hasData: false },
    { id: "walvoil",  name: "Walvoil",             hasData: false },
    { id: "hawe",     name: "HAWE Hydraulik",      hasData: false },
    { id: "parker",   name: "Parker",              hasData: false },
    { id: "husco",    name: "HUSCO",               hasData: false },
    { id: "hydac",    name: "HYDAC",               hasData: false },
    { id: "kawasaki", name: "Kawasaki Hydraulics", hasData: false },
    { id: "kyb",      name: "KYB",                 hasData: false },
    { id: "linde",    name: "Linde Hydraulics",    hasData: false },
    { id: "hengli",   name: "Hengli",              hasData: false },
  ],

  // Competitor products are their own items, created by uploading their datasheets.
  // categoryId = the Danfoss group they compete in (e.g. "proportional").
  // equivalentOf = Danfoss product ids a person has confirmed as equivalent (optional).
  //   These confirmed pairs double as the test set for measuring matching accuracy.
  competitorProducts: [],

  factors: [
    { id: "price",        label: "Price",                     group: "Commercial" },
    { id: "availability", label: "Availability / lead time",  group: "Commercial" },
    { id: "support",      label: "Service & distributor network", group: "Commercial" },
    { id: "lifetime",     label: "Longevity / durability",    group: "Commercial" },
    { id: "flow",         label: "Flow capacity",             group: "Technical" },
    { id: "pressure",     label: "Pressure rating",           group: "Technical" },
    { id: "size",         label: "Size & weight",             group: "Technical" },
    { id: "ports",        label: "Ports & interfaces",        group: "Technical" },
    { id: "electronics",  label: "Electronic control / CAN",  group: "Technical" },
    { id: "options",      label: "Configuration options",     group: "Technical" },
    { id: "environment",  label: "Operating conditions",      group: "Technical" },
  ],

  applications: ["Tractor", "Crane", "Forestry", "Material handling", "Construction", "Mining", "Other"],
};

// ---------------------------------------------------------------
// Mock /compare response.
// Danfoss values come ONLY from the PVG 32 Technical Information
// (BC152886483664, July 2024), with page numbers.
// Competitor values are either SAMPLE placeholders (no competitor
// dataset yet) or "pending" (dataset uploaded, awaiting extraction).
// ---------------------------------------------------------------
window.mockCompare = function (payload) {
  const cat = window.MOCK_CATALOG;
  const product = findProduct(cat.tree, payload.productId) || { id: payload.productId, label: payload.productId };
  const buyer = payload.buyer || {};
  const company = buyer.company || "the customer";
  const app = (buyer.application || "application").toLowerCase();
  const isPvg32 = product.id === "pvg32";

  const DOC = { title: "PVG 32 Proportional Valve Group – Technical Information", doc: "BC152886483664en-001602.pdf", date: "July 2024" };
  const sources = isPvg32 ? [
    { id: 1, ...DOC, page: "7" },
    { id: 2, ...DOC, page: "8" },
    { id: 3, ...DOC, page: "12" },
    { id: 4, ...DOC, page: "46" },
    { id: 5, ...DOC, page: "48, 49, 56" },
    { id: 6, ...DOC, page: "54" },
    { id: 7, ...DOC, page: "222" },
    { id: 8, ...DOC, page: "14" },
    { id: 9, ...DOC, page: "16" },
  ] : [];

  // ---- competitor columns ----
  const chosen = (cat.competitorProducts || []).filter((p) => (payload.competitorProductIds || []).includes(p.id));
  const useSample = chosen.length === 0;
  const matches = useSample
    ? [{ key: "sample-cmp", name: "Competitor X", manufacturer: "Sample data", matchScore: 0.78, isSample: true }]
    : chosen.map((p) => ({
        key: p.id, name: p.name,
        manufacturer: (cat.competitors.find((c) => c.id === p.competitorId) || {}).name || "",
        matchScore: (p.equivalentOf || []).includes(product.id) ? null : 0.7,
        knownEquivalent: (p.equivalentOf || []).includes(product.id),
      }));

  // Sample values only exist so the layout can be judged before real data arrives.
  const SAMPLE = { flowAB: "120", pressureCont: "300", sections: "10", actuation: "Electro-hydraulic, CAN", weight: "Sample: 3.4 kg per section" };
  const compVal = (rowId) => {
    if (!useSample) return { value: null, status: "pending" };
    return SAMPLE[rowId] ? { value: SAMPLE[rowId], status: "sample" } : { value: null, status: "missing" };
  };

  const A = product.id;
  const row = (id, factor, label, unit, anchor) => {
    const values = { [A]: anchor };
    matches.forEach((m) => (values[m.key] = compVal(id)));
    return { id, factor, label, unit, values };
  };

  const attributes = isPvg32 ? [
    row("flowAB", "flow", "Max. flow per work section (A/B)", "l/min",
      { value: "125", status: "conflict", sources: [3, 6, 1],
        note: "p.12: 100 l/min with pressure compensator, 125 without. p.54: compensated PVB rated 125 l/min (130 with turbo spool)." }),
    row("flowP", "flow", "Max. inlet flow (port P)", "l/min",
      { value: "140 (230 with PVPM/PVPVM mid inlet)", status: "ok", sources: [3] }),
    row("pressureCont", "pressure", "Max. pressure P, A/B, continuous", "bar",
      { value: "350 with PVSI end plate · 300 with PVS end plate", status: "ok", sources: [3] }),
    row("pressureInt", "pressure", "Max. pressure, intermittent", "bar",
      { value: "P: 400 · A/B: 420", status: "ok", sources: [3] }),
    row("sections", "options", "Max. work sections per valve group", "",
      { value: "12", status: "ok", sources: [1] }),
    row("compensation", "options", "Work section types", "",
      { value: "Compensated and uncompensated PVB, PVBZ with pilot-operated check", status: "ok", sources: [4] }),
    row("portsAB", "ports", "Work port threads (A/B)", "",
      { value: "G1/2 · 7/8-14 UNF · M22x1.5", status: "ok", sources: [5] }),
    row("portsP", "ports", "Inlet port threads (P/T)", "",
      { value: "G1/2 · G3/4 · 7/8-14 UNF · 1 1/16-12 UNF", status: "ok", sources: [9] }),
    row("actuation", "electronics", "Actuation", "",
      { value: "Manual, hydraulic, electro-hydraulic (ON/OFF, ratiometric, CAN bus, PWM)", status: "ok", sources: [2] }),
    row("weight", "size", "Weight", "kg",
      { value: "PVB work section 3.05 · PVP inlet 3.1", status: "ok", sources: [4, 8] }),
    row("pitch", "size", "Section width (pitch)", "mm",
      { value: "48", status: "inferred", sources: [4, 7],
        note: "Read from the dimension drawing (p.46) and the 48 mm step in the stack-length table (p.222); not stated as a spec." }),
    row("length", "size", "Stack length", "mm",
      { value: "119 (1 section) to 647 (12 sections)", status: "ok", sources: [7] }),
    row("temp", "environment", "Oil temperature", "°C",
      { value: "-30 to 90 (recommended 30 to 60)", status: "ok", sources: [3] }),
    row("filtration", "environment", "Required oil cleanliness (ISO 4406)", "",
      { value: "23/19/16 mechanical · 18/16/13 with PVE", status: "ok", sources: [6] }),
    row("price", "price", "Price level", "",
      { value: null, status: "missing", note: "Not in the datasheet; needs approved pricing." }),
    row("lifetime", "lifetime", "Service life", "",
      { value: null, status: "missing", note: "Datasheet gives operating limits but no service-life figure." }),
    row("availability", "availability", "Availability / lead time", "",
      { value: null, status: "missing" }),
  ] : [
    row("none", "options", "Datasheet", "", { value: null, status: "missing", note: "Upload the Danfoss datasheet for this product." }),
  ];

  const sampleNote = useSample ? [{ level: "sample", text: "No competitor datasheet selected, so the competitor column shows sample placeholders. Upload one in the Competitors step." }]
                               : [{ level: "pending", text: "Competitor datasheets are uploaded but not yet extracted; their values appear after processing." }];

  return {
    title: `${product.label} vs. ${useSample ? "closest competitor matches" : matches.map((m) => m.name).join(", ")}`,
    isSample: useSample,
    anchor: { key: A, name: product.label, manufacturer: "Danfoss" },
    matches,
    attributes,
    insights: isPvg32 ? {
      differences: [
        { text: "Flow to each function is independent of its own load pressure and of the other functions' load pressures (load-independent flow control).", sources: [1] },
        { text: "Rated flow per section depends on configuration: 100 l/min with pressure compensator and 125 l/min without, per the technical data table.", sources: [3] },
      ],
      benefits: [
        { text: `For ${company}'s ${app}: up to 12 work sections in one PVG 32 group, and other PVG families can be added through an interface module, so all machine functions can run from a single valve stack.`, sources: [1] },
        { text: "Load-sense relief valves on the A and B ports reduce energy loss at the target pressure.", sources: [1] },
        { text: "CAN bus, PWM, ratiometric and ON/OFF electro-hydraulic control are all available, which suits connected machines.", sources: [2] },
      ],
      tradeoffs: [
        { text: "Work ports come as G1/2, 7/8-14 UNF or M22x1.5, so existing hose ends can usually be matched; check against the valve in use today.", sources: [5] },
        { text: "Electro-hydraulic (PVE) actuation needs cleaner oil (18/16/13) than mechanical actuation (23/19/16).", sources: [6] },
      ],
    } : { differences: [], benefits: [], tradeoffs: [] },
    flags: [
      ...(isPvg32 ? [
        { level: "conflict", text: "Rated flow for a compensated work section: 100 l/min in the technical data table (p.12), 125 l/min on the compensated PVB page (p.54). Confirm with the product team before quoting." },
        { level: "warning", text: "350 bar is converted as 5075 psi on p.12 and 5067 psi on p.54 (correct value ≈ 5076 psi)." },
        { level: "warning", text: "The file name says revision 001602, but the page footers and revision table show 001601 (July 2024). Check PowerSource for the latest revision." },
        { level: "inferred", text: "Section width (48 mm) is read from drawings, not stated as a specification." },
        { level: "missing", text: "Price, service life and lead time are not in the datasheet." },
      ] : [{ level: "missing", text: `No datasheet uploaded for ${product.label}.` }]),
      ...sampleNote,
      ...(payload.competitorProductIds?.length ? [] : [{ level: "unverified", text: "Competitor match was selected automatically. Confirm functional equivalence before showing it to the customer." }]),
    ],
    confidence: { level: isPvg32 ? "medium" : "low",
      reason: isPvg32 ? "Danfoss side sourced from the official datasheet; competitor side not yet extracted." : "No Danfoss datasheet loaded." },
    sources,
  };

  function findProduct(nodes, id) {
    for (const n of nodes) {
      if (n.id === id && n.type === "product") return n;
      if (n.children) { const f = findProduct(n.children, id); if (f) return f; }
    }
    return null;
  }
};

window.mockAsk = function (payload) {
  const q = (payload.question || "").toLowerCase();
  if (!payload.context || !payload.context.productId) {
    return { answer: "Pick a product family above to start. I'll use your question as extra context when building the comparison." };
  }
  if (q.includes("price") || q.includes("cost")) {
    return { answer: "Pricing isn't in the datasheet and no approved price list is loaded, so I can't compare price yet. It's listed as an open point." };
  }
  return { answer: "Noted. I'll take that into account in the comparison. (Mock reply: the real answer will come from the backend.)" };
};

// ---------------------------------------------------------------
// Mock create / upload. A real backend does the same checks and
// returns the same { issues: [...] } list so the UI can flag them.
//   issue.level: "error" (rejected) | "conflict" | "warning" | "pending" | "ok"
// ---------------------------------------------------------------
const ALLOWED_EXT = ["pdf", "csv", "xlsx", "xls", "json", "txt", "html", "htm", "docx"];
const MAX_MB = 50;

function mockFindNode(nodes, id) {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) { const f = mockFindNode(n.children, id); if (f) return f; }
  }
  return null;
}
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "item";

window.mockCreateNode = function ({ parentId, label, desc, type }) {
  const cat = window.MOCK_CATALOG;
  const parent = parentId ? mockFindNode(cat.tree, parentId) : null;
  if (parentId && !parent) return { node: null, issues: [{ level: "error", text: "Parent group not found." }] };
  if (parent && parent.type === "product") return { node: null, issues: [{ level: "error", text: "A product cannot contain other items." }] };
  const siblings = parent ? (parent.children ||= []) : cat.tree;
  const name = (label || "").trim();
  if (!name) return { node: null, issues: [{ level: "error", text: "Name is required." }] };
  if (siblings.some((n) => n.label.toLowerCase() === name.toLowerCase())) {
    return { node: null, issues: [{ level: "conflict", text: `"${name}" already exists at this level.` }] };
  }
  let id = slug(name);
  while (mockFindNode(cat.tree, id)) id += "-2";
  const node = { id, label: name, desc: (desc || "").trim(), status: "data-pending", datasets: [] };
  if (type === "product") node.type = "product";
  else { node.prompt = "Choose one:"; node.children = []; }
  siblings.push(node);
  const issues = [{ level: "ok", text: `Added "${name}".` }];
  if (!node.desc) issues.push({ level: "warning", text: "No description given; add one so users can tell items apart." });
  issues.push({ level: "pending", text: "Marked as limited data until a dataset is uploaded and processed." });
  return { node, issues };
};

window.mockUpload = function (meta, files) {
  const cat = window.MOCK_CATALOG;
  const issues = [];
  if (!files || !files.length) return { uploaded: [], issues: [{ level: "error", text: "No files selected." }] };

  let target;          // object that receives the datasets
  let label;
  if (meta.source === "competitor") {
    let comp = cat.competitors.find((c) => c.id === meta.competitorId);
    const newName = (meta.competitorName || "").trim();
    if (!comp && newName) {
      comp = cat.competitors.find((c) => c.name.toLowerCase() === newName.toLowerCase());
      if (comp) issues.push({ level: "warning", text: `"${newName}" already exists as a competitor; files attached to it.` });
      else { comp = { id: slug(newName), name: newName, hasData: false }; cat.competitors.push(comp); issues.push({ level: "ok", text: `Added competitor "${newName}".` }); }
    }
    if (!comp) return { uploaded: [], issues: [{ level: "error", text: "Choose or name a competitor." }] };
    const pname = (meta.productName || "").trim();
    if (!pname) return { uploaded: [], issues: [{ level: "error", text: "Name the competitor product or series (e.g. its model code)." }] };
    const category = mockFindNode(cat.tree, meta.categoryId);
    if (!category || category.type === "product") return { uploaded: [], issues: [{ level: "error", text: "Choose the product category it competes in." }] };

    cat.competitorProducts ||= [];
    target = cat.competitorProducts.find((p) => p.competitorId === comp.id && p.name.toLowerCase() === pname.toLowerCase());
    if (target) issues.push({ level: "warning", text: `${comp.name} ${pname} already exists; files added to it.` });
    else {
      target = { id: `${comp.id}-${slug(pname)}`, competitorId: comp.id, name: pname, categoryId: category.id, equivalentOf: [], datasets: [], status: "pending" };
      cat.competitorProducts.push(target);
      issues.push({ level: "ok", text: `Added competitor product ${comp.name} ${pname} under ${category.label}.` });
    }
    if (target.categoryId !== category.id) issues.push({ level: "conflict", text: `${pname} is already filed under another category; kept the original.` });
    if (meta.equivalentOf && !target.equivalentOf.includes(meta.equivalentOf)) {
      target.equivalentOf.push(meta.equivalentOf);
      issues.push({ level: "ok", text: "Saved as a confirmed equivalent pair (also used to test matching accuracy)." });
    }
    comp.hasData = true;
    label = `${comp.name} ${pname}`;
  } else {
    target = mockFindNode(cat.tree, meta.nodeId);
    if (!target) return { uploaded: [], issues: [{ level: "error", text: "Target product or group not found." }] };
    label = target.label;
  }

  target.datasets ||= [];
  const uploaded = [];
  [...files].forEach((f) => {
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    if (!ALLOWED_EXT.includes(ext)) { issues.push({ level: "error", text: `${f.name}: unsupported file type (.${ext}). Use ${ALLOWED_EXT.join(", ")}.` }); return; }
    if (f.size === 0) { issues.push({ level: "error", text: `${f.name}: file is empty.` }); return; }
    if (f.size > MAX_MB * 1024 * 1024) { issues.push({ level: "error", text: `${f.name}: larger than ${MAX_MB} MB.` }); return; }
    const dup = target.datasets.find((d) => d.name === f.name);
    if (dup) issues.push({ level: "warning", text: `${f.name}: already uploaded here; the new version replaces it.` });
    else target.datasets.push({ name: f.name, source: meta.source, uploadedAt: new Date().toISOString() });
    if (ext === "pdf" && f.size < 20 * 1024) issues.push({ level: "warning", text: `${f.name}: very small PDF; it may be scanned or contain no extractable text.` });
    uploaded.push(f.name);
  });
  if (uploaded.length) issues.push({ level: "pending", text: `${uploaded.length} file(s) for ${label} queued for extraction. Values appear after processing and must be checked before use.` });
  return { uploaded, issues };
};
