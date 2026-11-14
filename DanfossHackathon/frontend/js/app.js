// ---------------------------------------------------------------
// Chat-style guided flow:
//   product tree (any depth) → competitors → priorities → buyer → review → result
// Everything the user can pick comes from the catalog (see mock-data.js),
// so adding families/products/competitors never requires edits here.
// ---------------------------------------------------------------
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const chat = $("#chat");
  const briefEl = $("#brief");
  const stepperEl = $("#stepper");

  const COMP_PROMPT = "Who should it be compared against?";
  const FACTOR_PROMPT = "What matters most in this deal? Click once for nice-to-have, twice for must-have.";
  const BUYER_PROMPT = "Who is the comparison for?";
  const LEVELS = ["off", "nice", "must"];

  // Every data issue the backend can report. Unknown levels still render (as "Check").
  const CELL_STATUS = {
    conflict:   ["Conflict", "badge-conflict"],
    sample:     ["Sample", "badge-muted"],
    outdated:   ["Outdated source", "badge-warn"],
    unverified: ["Unverified", "badge-warn"],
    inferred:   ["Inferred", "badge-warn"],
    converted:  ["Unit converted", "badge-muted"],
    pending:    ["Awaiting extraction", "badge-muted"],
  };
  const FLAG_LABEL = {
    conflict: "Conflict", missing: "Missing", sample: "Sample data", outdated: "Outdated",
    unverified: "Unverified", inferred: "Inferred", error: "Error", warning: "Check",
    pending: "Pending", ok: "Done",
  };
  const ACCEPT = ".pdf,.csv,.xlsx,.xls,.json,.txt,.html,.htm,.docx";

  function flagHtml(f) {
    const level = FLAG_LABEL[f.level] ? f.level : "warning";
    return `<div class="flag flag-${esc(level)}"><strong>${esc(FLAG_LABEL[level])}:</strong> ${esc(f.text)}</div>`;
  }

  let catalog = null;
  let state = freshState();

  function freshState() {
    return {
      path: [],                                   // selected node ids, root → product
      competitors: { mode: "auto", ids: [], productIds: [] }, // auto = let backend find best matches
      factors: {},                                // { factorId: "nice" | "must" }
      customFactors: [],                          // user-typed factor labels
      buyer: { company: "", application: "", currentSupplier: "", notes: "" },
      done: { competitors: false, factors: false, buyer: false },
      log: [],                                    // free-text chat: { stage, role, text }
      loading: false,
      result: null,
      error: null,
      mode: "compare",                            // "compare" (Danfoss first) | "match" (competitor first)
      matchProduct: null,                         // { id, label } of the competitor product in match mode
    };
  }

  // ---------- helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function el(tag, attrs = {}, html = "") {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
    }
    if (html) e.innerHTML = html;
    return e;
  }

  function nodeAt(depth) {
    let list = catalog.tree, node = null;
    for (let i = 0; i <= depth; i++) {
      node = list.find((n) => n.id === state.path[i]);
      if (!node) return null;
      list = node.children || [];
    }
    return node;
  }
  const optionsAt = (depth) => (depth === 0 ? catalog.tree : (nodeAt(depth - 1)?.children || []));
  const promptAt = (depth) => (depth === 0 ? catalog.rootPrompt : nodeAt(depth - 1)?.prompt || "Choose one:");
  function selectedProduct() {
    const n = nodeAt(state.path.length - 1);
    return n && n.type === "product" ? n : null;
  }
  const competitorName = (id) => catalog.competitors.find((c) => c.id === id)?.name || id;
  const factorLabel = (id) => catalog.factors.find((f) => f.id === id)?.label || id;

  function competitorSummary() {
    const prods = (state.competitors.productIds || []).map((id) => {
      const p = (catalog.competitorProducts || []).find((x) => x.id === id);
      return p ? `${competitorName(p.competitorId)} ${p.name}` : id;
    });
    const all = prods.concat(state.competitors.ids.map(competitorName));
    if (state.competitors.mode === "auto" || !all.length) return "Find the best matches automatically";
    return all.join(", ");
  }
  function factorSummary() {
    const must = Object.keys(state.factors).filter((k) => state.factors[k] === "must").map(factorLabel);
    const nice = Object.keys(state.factors).filter((k) => state.factors[k] === "nice").map(factorLabel);
    const parts = [];
    if (must.length) parts.push("Must-have: " + must.join(", "));
    if (nice.length) parts.push("Nice-to-have: " + nice.join(", "));
    return parts.join(" · ") || "No specific priorities";
  }
  /** Customer feedback files on record for this company (matched like the backend: case and spaces ignored). */
  function customerFilesFor(company) {
    const key = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
    const c = (catalog?.customers || []).find((x) => key(x.name) === key(company) && key(company));
    return c ? `${c.files.length} file(s): ${c.files.join(", ")}` : "";
  }
  function buyerSummary() {
    const b = state.buyer;
    return [b.company, b.application, b.currentSupplier && "uses " + b.currentSupplier].filter(Boolean).join(" · ");
  }

  /** The payload sent to POST /compare and as context for /ask. */
  function buildPayload() {
    const product = selectedProduct();
    return {
      path: [...state.path],
      productId: product ? product.id : null,
      competitors: state.competitors.mode === "auto" ? [] : [...state.competitors.ids],
      competitorProductIds: state.competitors.mode === "auto" ? [] : [...(state.competitors.productIds || [])],
      factors: { ...state.factors },
      customFactors: [...state.customFactors],
      buyer: { ...state.buyer },
      chatNotes: state.log.filter((m) => m.role === "user").map((m) => m.text),
    };
  }

  function currentStep() {
    if (!selectedProduct()) return { kind: "node", depth: state.path.length };
    if (!state.done.competitors) return { kind: "competitors" };
    if (!state.done.factors) return { kind: "factors" };
    if (!state.done.buyer) return { kind: "buyer" };
    if (!state.result && !state.loading) return { kind: "review" };
    return { kind: "result" };
  }

  /** Answered steps, in conversation order. */
  function segments() {
    const segs = state.path.map((id, i) => ({
      key: "node" + i, prompt: promptAt(i), answer: nodeAt(i)?.label || id,
      edit: () => { state.path = state.path.slice(0, i); },
    }));
    if (selectedProduct()) {
      if (state.done.competitors) segs.push({ key: "competitors", prompt: COMP_PROMPT, answer: competitorSummary(),
        edit: () => { state.done.competitors = false; } });
      if (state.done.factors) segs.push({ key: "factors", prompt: FACTOR_PROMPT, answer: factorSummary(),
        edit: () => { state.done.factors = false; } });
      if (state.done.buyer) segs.push({ key: "buyer", prompt: BUYER_PROMPT, answer: buyerSummary(),
        edit: () => { state.done.buyer = false; } });
    }
    return segs;
  }

  function editSegment(index) {
    const seg = segments()[index];
    if (!seg) return;
    seg.edit();
    state.result = null;
    state.error = null;
    state.log = state.log.filter((m) => m.stage <= index);
    render();
  }

  // ---------- message builders ----------
  function botMsg(content, { compact = false } = {}) {
    const wrap = el("div", { class: "msg msg-bot" + (compact ? " is-compact" : "") });
    wrap.appendChild(el("div", { class: "avatar", "aria-hidden": "true" }, "AI"));
    const bubble = el("div", { class: "bubble" });
    if (typeof content === "string") bubble.innerHTML = content; else bubble.appendChild(content);
    wrap.appendChild(bubble);
    chat.appendChild(wrap);
    return bubble;
  }
  function userMsg(text, onEdit) {
    const wrap = el("div", { class: "msg msg-user" });
    const bubble = el("div", { class: "bubble" }, esc(text));
    wrap.appendChild(bubble);
    if (onEdit) wrap.appendChild(el("button", { class: "link-btn", type: "button", onclick: onEdit }, "Edit"));
    chat.appendChild(wrap);
  }
  function renderLog(stage) {
    state.log.filter((m) => m.stage === stage).forEach((m) => {
      if (m.role === "user") userMsg(m.text);
      else if (m.role === "typing") botMsg('<span class="typing"><i></i><i></i><i></i></span>');
      else if (m.role === "issues") botMsg(`<p><strong>${esc(m.title)}</strong></p>${m.issues.map(flagHtml).join("")}`).classList.add("bubble-wide");
      else botMsg(esc(m.text));
    });
  }

  // ---------- step widgets ----------
  function nodeWidget(depth) {
    const box = el("div");
    box.appendChild(el("p", {}, esc(promptAt(depth))));
    const grid = el("div", { class: "option-grid" });
    optionsAt(depth).forEach((opt) => {
      const disabled = opt.status === "coming-soon";
      const badge = opt.status === "coming-soon" ? '<span class="badge badge-muted">Coming soon</span>'
        : opt.status === "data-pending" ? '<span class="badge badge-warn">Limited data</span>'
        : opt.tag ? `<span class="badge badge-brand">${esc(opt.tag)}</span>` : "";
      grid.appendChild(el("button", {
        class: "option-card", type: "button", disabled,
        onclick: () => { state.path = state.path.slice(0, depth).concat(opt.id); state.result = null; render(); },
      }, `<span class="option-title">${esc(opt.label)}</span>
          <span class="option-desc">${esc(opt.desc || "")}</span>${badge}
          ${opt.datasets?.length ? `<span class="option-meta">${opt.datasets.length} dataset${opt.datasets.length > 1 ? "s" : ""} uploaded</span>` : ""}`));
    });
    const levelName = depth === 0 ? "product family" : depth === 1 ? "product type" : "product";
    const form = addNodeForm(depth, levelName, () => { form.hidden = true; grid.hidden = false; });
    form.hidden = true;
    grid.appendChild(el("button", {
      class: "option-card option-add", type: "button",
      onclick: () => { grid.hidden = true; form.hidden = false; form.querySelector("input").focus(); },
    }, `<span class="option-title">+ Add ${levelName}</span><span class="option-desc">Create it and upload its datasets</span>`));
    box.appendChild(grid);
    box.appendChild(form);
    botMsg(box).classList.add("bubble-wide");
  }

  /** Inline form: create a family / type / product (backend validates) + optional Danfoss datasets. */
  function addNodeForm(depth, levelName, onCancel) {
    const parentId = depth === 0 ? null : state.path[depth - 1];
    const defaultKind = depth <= 1 ? "group" : "product";
    const form = el("form", { class: "form-grid add-form" });
    form.innerHTML = `
      <div class="span-2"><strong>New ${esc(levelName)}</strong>
        ${parentId ? `<span class="hint"> in ${esc(nodeAt(depth - 1).label)}</span>` : ""}</div>
      <label class="field"><span>Name <span class="req">*</span></span><input name="label" required placeholder="${depth === 0 ? "e.g. Pumps" : depth === 1 ? "e.g. Cartridge valves" : "e.g. PVG 40"}"></label>
      <label class="field">Kind
        <select name="type" ${depth === 0 ? "disabled" : ""}>
          <option value="group" ${defaultKind === "group" ? "selected" : ""}>Group (contains products)</option>
          <option value="product" ${defaultKind === "product" ? "selected" : ""}>Product (can be compared)</option>
        </select></label>
      <label class="field span-2"><span>Description <span class="opt">(optional)</span></span><input name="desc" placeholder="e.g. 1–170 L/min · 350 bar"></label>
      <label class="field span-2"><span>Danfoss datasets <span class="opt">(optional: datasheets, technical info, price lists)</span></span>
        <input name="files" type="file" multiple accept="${ACCEPT}"></label>
      <div class="form-issues span-2"></div>
      <div class="actions span-2">
        <button class="btn btn-ghost" type="button" data-act="cancel">Cancel</button>
        <button class="btn btn-primary" type="submit">Add ${esc(levelName)}</button>
      </div>`;
    form.querySelector('[data-act="cancel"]').addEventListener("click", onCancel);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const btn = form.querySelector('[type="submit"]');
      btn.disabled = true;
      const issueBox = form.querySelector(".form-issues");
      try {
        const res = await Api.createNode({
          parentId, label: fd.get("label"), desc: fd.get("desc"),
          type: depth === 0 ? "group" : fd.get("type"),
        });
        if (!res.node) { issueBox.innerHTML = res.issues.map(flagHtml).join(""); return; }
        let issues = res.issues;
        const files = form.querySelector('input[type="file"]').files;
        if (files.length) {
          const up = await Api.uploadDatasets({ nodeId: res.node.id, source: "danfoss" }, files);
          issues = issues.concat(up.issues);
        }
        await reloadCatalog();
        state.log.push({ stage: segments().length, role: "issues", title: `New ${levelName}: ${res.node.label}`, issues });
        render();
      } catch (err) {
        issueBox.innerHTML = flagHtml({ level: "error", text: err.message });
      } finally {
        btn.disabled = false;
      }
    });
    return form;
  }

  // ---------- upload dialog (attach datasets to any existing item) ----------
  function flattenTree(nodes, depth = 0, out = []) {
    nodes.forEach((n) => {
      if (n.status !== "coming-soon" || n.children?.length) out.push({ node: n, depth });
      if (n.children) flattenTree(n.children, depth + 1, out);
    });
    return out;
  }

  /** Danfoss groups (not products), for "which category does this compete in". */
  const groupsOnly = () => flattenTree(catalog.tree).filter(({ node }) => node.type !== "product");
  const productsIn = (groupId) => (findNode(catalog.tree, groupId)?.children || []).filter((n) => n.type === "product");
  function findNode(nodes, id) {
    for (const n of nodes) {
      if (n.id === id) return n;
      if (n.children) { const f = findNode(n.children, id); if (f) return f; }
    }
    return null;
  }
  /** Group the currently selected product sits in (e.g. "proportional"). */
  const currentCategory = () => (selectedProduct() ? state.path[state.path.length - 2] : state.path[state.path.length - 1]);

  /**
   * Upload dialog with two modes:
   *  - Danfoss: attach files to an existing family / type / product.
   *  - Competitor: create (or add to) a competitor PRODUCT, filed under a Danfoss category,
   *    optionally marked as a confirmed equivalent of a Danfoss product.
   */
  function openUploadDialog(opts = {}) {
    const dlg = $("#upload-dialog");
    const current = selectedProduct()?.id || state.path[state.path.length - 1];
    const targets = flattenTree(catalog.tree);
    const groups = groupsOnly();
    const cat0 = opts.categoryId || currentCategory() || groups.find((g) => productsIn(g.node.id).length)?.node.id;
    const source0 = opts.source || "danfoss";
    dlg.innerHTML = `
      <form method="dialog" class="dialog-body">
        <div class="dialog-head"><h3>Upload data</h3><button class="link-btn" value="cancel" type="submit">Close</button></div>
        <fieldset class="field seg">
          <legend>Whose data is it?</legend>
          <label><input type="radio" name="source" value="danfoss" ${source0 === "danfoss" ? "checked" : ""}> Danfoss</label>
          <label><input type="radio" name="source" value="competitor" ${source0 === "competitor" ? "checked" : ""}> Competitor</label>
          <label><input type="radio" name="source" value="customer" ${source0 === "customer" ? "checked" : ""}> Customer feedback</label>
        </fieldset>

        <div class="customer-fields">
          <label class="field"><span>Customer <span class="req">*</span></span>
            <input name="customerName" list="customer-names" placeholder="e.g. AGCO" value="${esc(state.buyer.company || "")}">
            <datalist id="customer-names">${(catalog.customers || []).map((c) => `<option value="${esc(c.name)}">`).join("")}</datalist></label>
          <p class="hint">Requirements, complaints or specs the customer sent (PDF, Excel, CSV, TXT). Only the text is kept,
            not the file. It is used automatically when this customer is the buyer in a comparison, and cited as C1, C2 …</p>
        </div>

        <div class="danfoss-fields">
          <label class="field">Attach to
            <select name="nodeId">${targets.map(({ node, depth }) =>
              `<option value="${esc(node.id)}" ${node.id === current ? "selected" : ""}>${"&nbsp;&nbsp;&nbsp;".repeat(depth)}${esc(node.label)}${node.type === "product" ? "" : " (group)"}</option>`).join("")}</select>
          </label>
        </div>

        <div class="competitor-fields">
          <div class="form-grid tight">
            <label class="field">Competitor
              <select name="competitorId">
                ${catalog.competitors.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}
                <option value="__new">+ New competitor…</option>
              </select></label>
            <label class="field new-comp" hidden><span>New competitor name</span><input name="competitorName" placeholder="e.g. Eaton"></label>
            <label class="field"><span>Product / series <span class="req">*</span></span>
              <input name="productName" placeholder="Model name or code from the datasheet"></label>
            <label class="field">Competes in category
              <select name="categoryId">${groups.map(({ node, depth }) =>
                `<option value="${esc(node.id)}" ${node.id === cat0 ? "selected" : ""}>${"&nbsp;&nbsp;&nbsp;".repeat(depth)}${esc(node.label)}</option>`).join("")}</select></label>
            <label class="field span-2"><span>Confirmed Danfoss equivalent <span class="opt">(optional, only if you know it)</span></span>
              <select name="equivalentOf"></select></label>
          </div>
        </div>

        <label class="dropzone">
          <input name="files" type="file" multiple accept="${ACCEPT}">
          <span class="dz-title">Drop datasheets here or click to choose</span>
          <span class="hint">PDF, CSV, Excel, JSON, HTML, Word · max 50 MB each</span>
          <span class="dz-list"></span>
        </label>
        <div class="form-issues"></div>
        <div class="actions">
          <button class="btn btn-ghost" value="cancel" type="submit">Cancel</button>
          <button class="btn btn-primary" type="button" data-act="upload">Upload</button>
        </div>
      </form>`;
    const f = dlg.querySelector("form");
    const fileInput = f.querySelector('input[type="file"]');
    const list = f.querySelector(".dz-list");
    const syncSource = () => {
      const src = f.source.value;
      f.querySelector(".competitor-fields").hidden = src !== "competitor";
      f.querySelector(".danfoss-fields").hidden = src !== "danfoss";
      f.querySelector(".customer-fields").hidden = src !== "customer";
      fileInput.accept = src === "customer" ? ".pdf,.xlsx,.xls,.csv,.txt" : ACCEPT;
      f.querySelector(".dz-title").textContent = src === "customer" ? "Drop the customer's files here or click to choose" : "Drop datasheets here or click to choose";
    };
    const syncEquivalents = () => {
      const prods = productsIn(f.categoryId.value);
      const sel = selectedProduct()?.id;
      f.equivalentOf.innerHTML = `<option value="">Not known: let the assistant find the match</option>` +
        prods.map((p) => `<option value="${esc(p.id)}" ${opts.equivalentOf === p.id ? "selected" : ""}>${esc(p.label)}${p.id === sel ? " (current)" : ""}</option>`).join("");
    };
    const showFiles = () => (list.textContent = [...fileInput.files].map((x) => x.name).join(", "));
    f.querySelectorAll('input[name="source"]').forEach((r) => r.addEventListener("change", syncSource));
    f.competitorId.addEventListener("change", () => (f.querySelector(".new-comp").hidden = f.competitorId.value !== "__new"));
    f.categoryId.addEventListener("change", syncEquivalents);
    fileInput.addEventListener("change", showFiles);
    const dz = f.querySelector(".dropzone");
    dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("is-over"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("is-over"));
    dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("is-over"); fileInput.files = e.dataTransfer.files; showFiles(); });
    syncSource(); syncEquivalents();

    f.querySelector('[data-act="upload"]').addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const issueBox = f.querySelector(".form-issues");
      const isComp = f.source.value === "competitor";
      const isCustomer = f.source.value === "customer";
      const meta = isCustomer ? { source: "customer", customerName: f.customerName.value.trim() } : isComp ? {
        source: "competitor",
        competitorId: f.competitorId.value !== "__new" ? f.competitorId.value : null,
        competitorName: f.competitorId.value === "__new" ? f.competitorName.value.trim() : null,
        productName: f.productName.value.trim(),
        categoryId: f.categoryId.value,
        equivalentOf: f.equivalentOf.value || null,
      } : { source: "danfoss", nodeId: f.nodeId.value };
      btn.disabled = true;
      try {
        const res = await Api.uploadDatasets(meta, fileInput.files);
        issueBox.innerHTML = res.issues.map(flagHtml).join("");
        if (res.uploaded?.length) {
          await reloadCatalog();
          const title = isCustomer ? `Customer feedback: ${meta.customerName}` : isComp ? `Competitor upload: ${meta.productName}` : `Upload to ${findNode(catalog.tree, meta.nodeId)?.label || meta.nodeId}`;
          state.log.push({ stage: segments().length, role: "issues", title, issues: res.issues });
          render();
          fileInput.value = ""; list.textContent = "";
        }
      } catch (err) {
        issueBox.innerHTML = flagHtml({ level: "error", text: err.message });
      } finally {
        btn.disabled = false;
      }
    });
    dlg.showModal();
  }

  function competitorWidget() {
    const draft = {
      mode: state.competitors.mode,
      ids: new Set(state.competitors.ids),
      productIds: new Set(state.competitors.productIds || []),
    };
    const category = currentCategory();
    const compProducts = (catalog.competitorProducts || []).filter((p) => p.categoryId === category);
    const box = el("div");
    box.appendChild(el("p", {}, `${esc(COMP_PROMPT)} <span class="hint">Pick uploaded competitor products, whole competitors, or leave it on automatic.</span>`));

    const auto = el("label", { class: "toggle-row" });
    const autoInput = el("input", { type: "checkbox" });
    autoInput.checked = draft.mode === "auto";
    auto.appendChild(autoInput);
    auto.appendChild(el("span", {}, "<strong>Find best matches automatically</strong>"));
    box.appendChild(auto);

    const pick = (set, id) => { autoInput.checked = false; draft.mode = "manual"; set.has(id) ? set.delete(id) : set.add(id); sync(); };

    // Competitor products uploaded for this category
    box.appendChild(el("div", { class: "group-label" }, "Competitor products with datasheets"));
    const prodRow = el("div", { class: "chip-row" });
    if (!compProducts.length) {
      prodRow.appendChild(el("span", { class: "hint" }, "None uploaded for this category yet."));
    }
    compProducts.forEach((p) => {
      const known = (p.equivalentOf || []).includes(selectedProduct()?.id);
      prodRow.appendChild(el("button", {
        class: "chip chip-product", type: "button", "data-pid": p.id, "aria-pressed": "false",
        onclick: () => pick(draft.productIds, p.id),
      }, `<strong>${esc(competitorName(p.competitorId))}</strong> ${esc(p.name)}
          <span class="lvl">${known ? "Confirmed equivalent" : p.status === "pending" ? "Awaiting extraction" : esc(p.datasets.length + " file(s)")}</span>`));
    });
    box.appendChild(prodRow);
    box.appendChild(el("button", {
      class: "btn btn-ghost btn-sm", type: "button",
      onclick: () => openUploadDialog({ source: "competitor", categoryId: category }),
    }, "+ Upload competitor datasheet"));

    // Whole competitors
    box.appendChild(el("div", { class: "group-label" }, "Or search across competitors"));
    const chips = el("div", { class: "chip-row" });
    catalog.competitors.forEach((c) => {
      chips.appendChild(el("button", {
        class: "chip", type: "button", "data-id": c.id, "aria-pressed": "false",
        title: c.hasData ? "Datasheets uploaded" : "No datasheets yet",
        onclick: () => pick(draft.ids, c.id),
      }, `<span class="dot ${c.hasData ? "dot-ok" : "dot-off"}"></span>${esc(c.name)}`));
    });
    box.appendChild(chips);
    box.appendChild(el("p", { class: "legend" }, '<span class="dot dot-ok"></span>datasheets uploaded <span class="dot dot-off"></span>none yet'));

    function sync() {
      box.querySelectorAll(".chip-row").forEach((r) => r.classList.toggle("is-disabled", autoInput.checked));
      chips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", draft.ids.has(c.dataset.id)));
      prodRow.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", draft.productIds.has(c.dataset.pid)));
    }
    autoInput.addEventListener("change", () => { draft.mode = autoInput.checked ? "auto" : "manual"; sync(); });

    box.appendChild(el("div", { class: "actions" })).appendChild(el("button", {
      class: "btn btn-primary", type: "button",
      onclick: () => {
        const ids = [...draft.ids], productIds = [...draft.productIds];
        const none = !ids.length && !productIds.length;
        state.competitors = { mode: draft.mode === "auto" || none ? "auto" : "manual", ids, productIds };
        state.done.competitors = true; render();
      },
    }, "Continue"));
    sync();
    botMsg(box).classList.add("bubble-wide");
  }

  function factorWidget() {
    const draft = { ...state.factors };
    const custom = [...state.customFactors];
    const box = el("div");
    box.appendChild(el("p", {}, esc(FACTOR_PROMPT)));

    const groups = {};
    catalog.factors.forEach((f) => (groups[f.group || "Other"] ||= []).push(f));
    Object.entries(groups).forEach(([name, list]) => {
      box.appendChild(el("div", { class: "group-label" }, esc(name)));
      const row = el("div", { class: "chip-row" });
      list.forEach((f) => {
        const chip = el("button", { class: "chip chip-level", type: "button" });
        const paint = () => {
          const lvl = draft[f.id] || "off";
          chip.dataset.level = lvl;
          chip.innerHTML = `${esc(f.label)}${lvl === "must" ? '<span class="lvl">Must</span>' : lvl === "nice" ? '<span class="lvl">Nice</span>' : ""}`;
          chip.setAttribute("aria-label", `${f.label}: ${lvl === "off" ? "not relevant" : lvl === "nice" ? "nice to have" : "must have"}`);
        };
        chip.addEventListener("click", () => {
          const next = LEVELS[(LEVELS.indexOf(draft[f.id] || "off") + 1) % LEVELS.length];
          if (next === "off") delete draft[f.id]; else draft[f.id] = next;
          paint();
        });
        paint();
        row.appendChild(chip);
      });
      box.appendChild(row);
    });

    // Custom factor input — anything the catalog doesn't cover yet.
    const customRow = el("div", { class: "chip-row" });
    const paintCustom = () => {
      customRow.innerHTML = "";
      custom.forEach((label, i) => customRow.appendChild(el("button", {
        class: "chip chip-level", type: "button", "data-level": "nice", title: "Remove",
        onclick: () => { custom.splice(i, 1); paintCustom(); },
      }, `${esc(label)}<span class="lvl">×</span>`)));
    };
    const addForm = el("form", { class: "inline-add" });
    const addInput = el("input", { type: "text", placeholder: "Add your own factor, e.g. noise level" });
    addForm.appendChild(addInput);
    addForm.appendChild(el("button", { class: "btn btn-ghost", type: "submit" }, "Add"));
    addForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = addInput.value.trim();
      if (v && !custom.includes(v)) custom.push(v);
      addInput.value = ""; paintCustom();
    });
    box.appendChild(el("div", { class: "group-label" }, "Your own"));
    box.appendChild(customRow);
    box.appendChild(addForm);
    paintCustom();

    box.appendChild(el("div", { class: "actions" })).appendChild(el("button", {
      class: "btn btn-primary", type: "button",
      onclick: () => { state.factors = draft; state.customFactors = custom; state.done.factors = true; render(); },
    }, "Continue"));
    botMsg(box).classList.add("bubble-wide");
  }

  function buyerWidget() {
    const b = state.buyer;
    const box = el("div");
    box.appendChild(el("p", {}, esc(BUYER_PROMPT) + ' <span class="hint">This tailors benefits and switching trade-offs to the customer.</span>'));
    const form = el("form", { class: "form-grid" });
    form.innerHTML = `
      <label class="field span-2"><span>Company / buyer name <span class="req">*</span></span>
        <input name="company" required value="${esc(b.company)}" placeholder="e.g. AGCO">
      </label>
      <label class="field">Application
        <select name="application">
          <option value="">Select…</option>
          ${catalog.applications.map((a) => `<option ${a === b.application ? "selected" : ""}>${esc(a)}</option>`).join("")}
        </select>
      </label>
      <label class="field"><span>Currently using <span class="opt">(optional)</span></span>
        <input name="currentSupplier" value="${esc(b.currentSupplier)}" placeholder="e.g. competitor valve in use today">
      </label>
      <label class="field span-2"><span>Additional notes <span class="opt">(optional)</span></span>
        <textarea name="notes" rows="3" placeholder="Requirements, constraints, volumes, meeting goal…">${esc(b.notes)}</textarea>
      </label>
      <div class="actions span-2"><button class="btn btn-primary" type="submit">Continue</button></div>`;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      state.buyer = Object.fromEntries(["company", "application", "currentSupplier", "notes"].map((k) => [k, (fd.get(k) || "").trim()]));
      if (!state.buyer.company) return;
      state.done.buyer = true; render();
    });
    box.appendChild(form);
    botMsg(box).classList.add("bubble-wide");
  }

  function reviewWidget() {
    const p = selectedProduct();
    const box = el("div");
    box.appendChild(el("p", {}, "Here's the brief. Generate the comparison when it looks right."));
    box.appendChild(el("dl", { class: "summary" }, `
      <dt>Product</dt><dd>${esc(state.path.map((_, i) => nodeAt(i).label).join(" › "))}</dd>
      <dt>Compare against</dt><dd>${esc(competitorSummary())}</dd>
      <dt>Priorities</dt><dd>${esc(factorSummary())}${state.customFactors.length ? " · Own: " + esc(state.customFactors.join(", ")) : ""}</dd>
      <dt>Buyer</dt><dd>${esc(buyerSummary())}</dd>
      ${state.buyer.notes ? `<dt>Notes</dt><dd>${esc(state.buyer.notes)}</dd>` : ""}`));
    if (p.status === "data-pending") box.appendChild(el("p", { class: "flag flag-missing" }, "Limited data is loaded for this product, so the comparison will be partial."));
    box.appendChild(el("div", { class: "actions" })).appendChild(el("button", {
      class: "btn btn-primary btn-lg", type: "button", onclick: generate,
    }, "Generate comparison"));
    botMsg(box).classList.add("bubble-wide");
  }

  // ---------- generate + results ----------
  const PIPELINE = ["Find", "Normalize", "Compare", "Reference"];

  async function generate() {
    state.loading = true; state.error = null; render();
    try {
      state.result = await Api.compare(buildPayload());
    } catch (err) {
      state.error = err.message;
    } finally {
      state.loading = false; render();
    }
  }

  function loadingWidget() {
    const box = el("div", { class: "pipeline" });
    box.appendChild(el("p", {}, "Building the comparison…"));
    const row = el("div", { class: "pipeline-row" });
    PIPELINE.forEach((s) => row.appendChild(el("span", { class: "pipe-step" }, esc(s))));
    box.appendChild(row);
    botMsg(box).classList.add("bubble-wide");
    const steps = row.querySelectorAll(".pipe-step");
    let i = 0;
    const tick = () => {
      if (!state.loading || !document.body.contains(row)) return;
      steps.forEach((s, k) => {
        s.classList.toggle("is-done", k < i);
        s.classList.toggle("is-active", k === i);
      });
      i = Math.min(i + 1, PIPELINE.length - 1);
      setTimeout(tick, 480);
    };
    tick();
  }

  const priorityRank = (factor) => ({ must: 0, nice: 1 }[state.factors[factor]] ?? 2);

  // Footnote [n]: opens the original document at the cited page when we know its link (documents.csv,
  // filled by the datasheet crawler); otherwise it jumps to the source list below the result.
  let sourceById = {};
  function ref(n) {
    const s = sourceById[n];
    return s?.url
      ? `<a href="${esc(s.url)}" target="_blank" rel="noopener" class="ref" title="Open ${esc(s.title)}${s.page ? ", p. " + esc(s.page) : ""}">[${n}]</a>`
      : `<a href="#src-${n}" class="ref" title="No online link yet">[${n}]</a>`;
  }

  function cell(v) {
    if (v && v.value == null && v.status === "pending") {
      return `<td class="cell-pending">Awaiting extraction${v.note ? `<div class="cell-note">${esc(v.note)}</div>` : ""}</td>`;
    }
    if (v && v.status === "na") {                 // this product has no value under this name: not "missing"
      return `<td class="cell-na" title="${esc(v.note || "No matching value")}">–</td>`;
    }
    if (!v || v.value == null) {                  // the datasheet does not state it (detail in the tooltip)
      return `<td class="cell-missing" title="${esc(v?.note || "Not found in sources")}">Not stated</td>`;
    }
    const refs = (v.sources || []).map(ref).join("");
    const st = CELL_STATUS[v.status];
    const tag = st ? `<span class="badge ${st[1]}" title="${esc(v.note || "")}">${st[0]}</span>` : "";
    // short notes that change the meaning stay visible; the rest is a tooltip on the badge
    const note = v.note && ["conflict", "converted", "inferred"].includes(v.status) ? `<div class="cell-note">${esc(v.note)}</div>` : "";
    return `<td class="cell-${v.status || "ok"}">${esc(v.value)}${refs}${tag}${note}</td>`;
  }

  /** Count every flagged cell by type, e.g. "1 conflict · 6 missing · 1 outdated". */
  function issueSummary(r) {
    const counts = {};
    r.attributes.forEach((a) => Object.values(a.values).forEach((v) => {
      const s = v?.status === "pending" || v?.status === "na" ? v.status : !v || v.value == null ? "missing" : v.status;
      if (s && s !== "ok" && s !== "na") counts[s] = (counts[s] || 0) + 1;
    }));
    (r.flags || []).forEach((f) => { if (!(f.level in counts) && f.level !== "missing") counts[f.level] = 1; });
    const parts = Object.entries(counts).map(([k, n]) =>
      `<span class="issue-chip flag-${esc(k)}">${n} ${esc((FLAG_LABEL[k] || k).toLowerCase())}</span>`);
    return parts.length ? parts.join("") : '<span class="issue-chip flag-ok">No data issues found</span>';
  }

  function insightList(title, items) {
    if (!items || !items.length) return "";
    return `<section class="insight"><h4>${esc(title)}</h4><ul>${items.map((it) =>
      `<li>${esc(it.text)}${(it.sources || []).map(ref).join("")}${it.sample ? ' <span class="badge badge-muted">Sample</span>' : ""}</li>`).join("")}</ul></section>`;
  }

  function resultWidget(r) {
    const cols = [r.anchor, ...r.matches];
    sourceById = Object.fromEntries((r.sources || []).map((s) => [s.id, s]));
    const sorted = [...r.attributes].sort((a, b) => priorityRank(a.factor) - priorityRank(b.factor));
    // short table = rows comparable across products ("core"); everything else is collapsed and not printed
    const rows = sorted.filter((a) => a.core !== false);
    const more = sorted.filter((a) => a.core === false);
    const rowHtml = (a) => {
      const pr = state.factors[a.factor];
      return `<tr class="${pr ? "is-" + pr : ""}${a.paired ? " is-paired" : ""}"><th scope="row">${pr ? `<span class="prio prio-${pr}" title="${pr === "must" ? "Must-have" : "Nice-to-have"}"></span>` : ""}${esc(a.label)}${a.unit ? ` <span class="unit">(${esc(a.unit)})</span>` : ""}${a.paired ? `<div class="cell-note" title="${esc(a.note || "")}">paired by AI</div>` : ""}</th>${cols.map((c) => cell(a.values[c.key])).join("")}</tr>`;
    };
    const head = `<thead><tr><th>Attribute</th>${cols.map((c) =>
          `<th><div class="col-name">${esc(c.name)}</div><div class="col-sub">${esc(c.manufacturer)}${c.knownEquivalent ? " · confirmed equivalent" : c.matchScore ? ` · match ${Math.round(c.matchScore * 100)}%` : ""}</div></th>`).join("")}</tr></thead>`;
    const card = el("div", { class: "result" });
    card.innerHTML = `
      <div class="result-head">
        <div>
          <h3>${esc(r.title)}</h3>
          <div class="hint">Prepared for ${esc(state.buyer.company || "—")}${state.buyer.application ? " · " + esc(state.buyer.application) : ""}</div>
        </div>
        <span class="confidence conf-${esc(r.confidence?.level)}" title="${esc(r.confidence?.reason)}">Confidence: ${esc(r.confidence?.level || "n/a")}</span>
      </div>
      <div class="issue-summary">${issueSummary(r)}
      </div>
      ${r.isSample ? '<div class="flag flag-sample">Prototype view: competitor values are sample data until the competitor dataset is loaded.</div>' : ""}
      ${rows.length ? `<div class="table-wrap"><table class="cmp">${head}<tbody>${rows.map(rowHtml).join("")}</tbody></table></div>`
        : '<p class="hint">No attribute could be compared directly between these products - see the notes below.</p>'}
      ${more.length ? `<details class="more-facts"><summary>All facts (${more.length} more rows, only one product has a value)</summary>
        <div class="table-wrap"><table class="cmp">${head}<tbody>${more.map(rowHtml).join("")}</tbody></table></div></details>` : ""}
      <div class="insights">
        ${insightList("Key differences", r.insights?.differences)}
        ${insightList(`Benefits for ${state.buyer.company || "the customer"}`, r.insights?.benefits)}
        ${insightList("Switching trade-offs", r.insights?.tradeoffs)}
      </div>
      ${r.flags?.length ? `<section class="insight"><h4>Open points</h4>${r.flags.map(flagHtml).join("")}</section>` : ""}
      <section class="sources"><h4>Sources</h4><ol>${r.sources.map((s) =>
        `<li id="src-${s.id}">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)}${s.page ? `, p. ${esc(s.page)}` : ""}${s.date ? ` <span class="hint">(${esc(s.date)})</span>` : ""}${s.url ? "" : ' <span class="hint">· no online link yet</span>'}${s.doc ? `<div class="hint">${esc(s.doc)}</div>` : ""}</li>`).join("")}</ol></section>
      <div class="actions">
        <button class="btn btn-ghost" type="button" data-act="print">Print / save as PDF</button>
        <button class="btn btn-ghost" type="button" data-act="edit">Adjust priorities</button>
      </div>`;
    card.querySelector('[data-act="print"]').addEventListener("click", () => window.print());
    if (state.mode === "match") card.querySelector('[data-act="edit"]').remove();
    else card.querySelector('[data-act="edit"]').addEventListener("click", () => {
      const idx = segments().findIndex((s) => s.key === "factors");
      editSegment(idx);
    });
    botMsg(card).classList.add("bubble-wide");
  }

  // ---------- sidebar + stepper ----------
  function renderBrief() {
    if (state.mode === "match") {
      briefEl.innerHTML = `<h2>Danfoss match</h2>
        <div class="brief-item"><div class="brief-label">Customer uses</div><div class="brief-value">${esc(state.matchProduct.label)}</div></div>
        <div class="brief-item"><div class="brief-label">Buyer</div><div class="brief-value ${buyerSummary() ? "" : "is-empty"}">${esc(buyerSummary() || "Not set")}</div></div>
        <p class="hint brief-foot">Danfoss candidates come from the facts database. Every value links to its source; missing or conflicting data is flagged, never guessed.</p>`;
      return;
    }
    const segs = segments();
    const find = (key) => segs.findIndex((s) => s.key === key);
    const item = (label, value, key) => {
      const idx = key === "product" ? (state.path.length ? 0 : -1) : find(key);
      return `<div class="brief-item"><div class="brief-label">${label}${idx >= 0 ? `<button class="link-btn" data-edit="${idx}" type="button">Edit</button>` : ""}</div>
        <div class="brief-value ${value ? "" : "is-empty"}">${value ? esc(value) : "Not set yet"}</div></div>`;
    };
    const productPath = state.path.map((_, i) => nodeAt(i)?.label).join(" › ");
    briefEl.innerHTML = `<h2>Comparison brief</h2>
      ${item("Product", productPath, "product")}
      ${item("Compare against", state.done.competitors ? competitorSummary() : "", "competitors")}
      ${item("Priorities", state.done.factors ? factorSummary() : "", "factors")}
      ${item("Buyer", state.done.buyer ? buyerSummary() : "", "buyer")}
      ${state.done.buyer && state.buyer.notes ? item("Notes", state.buyer.notes, "buyer") : ""}
      ${customerFilesFor(state.buyer.company) ? `<div class="brief-item"><div class="brief-label">Customer feedback</div><div class="brief-value">${esc(customerFilesFor(state.buyer.company))}</div></div>` : ""}
      <p class="hint brief-foot">Every fact in the result links to its source. Missing or conflicting data is flagged, never guessed.</p>`;
    briefEl.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => editSegment(+b.dataset.edit)));
  }

  function renderStepper() {
    const k = currentStep().kind;
    const order = ["node", "competitors", "factors", "buyer", "review"];
    const at = k === "result" ? (state.result ? 5 : 4) : order.indexOf(k);
    const labels = ["Product", "Competitors", "Priorities", "Buyer", "Results"];
    stepperEl.innerHTML = labels.map((l, i) => {
      const cls = i < at || (i === 4 && state.result) ? "is-done" : i === Math.min(at, 4) ? "is-current" : "";
      return `<li class="${cls}"><span class="num">${i + 1}</span>${l}</li>`;
    }).join("");
  }

  // ---------- main render ----------
  // ---------- reverse flow: competitor product -> closest Danfoss products ----------
  function openMatchDialog() {
    const dlg = $("#match-dialog");
    const prods = catalog.competitorProducts || [];
    dlg.innerHTML = `
      <form method="dialog" class="dialog-body">
        <div class="dialog-head"><h3>Find Danfoss match</h3><button class="link-btn" value="cancel" type="submit">Close</button></div>
        <p class="hint">The customer uses a competitor product today. We look for the closest Danfoss products in the facts database.</p>
        ${prods.length ? `
        <label class="field">Competitor product the customer uses
          <select name="productId">${prods.map((p) =>
            `<option value="${esc(p.id)}">${esc(competitorName(p.competitorId) + " " + p.name)}${p.datasets?.length ? "" : " (no datasheet yet)"}</option>`).join("")}</select></label>
        <div class="form-grid tight">
          <label class="field"><span>Customer <span class="opt">(optional)</span></span><input name="company" placeholder="e.g. AGCO"></label>
          <label class="field"><span>Application <span class="opt">(optional)</span></span><input name="application" placeholder="e.g. Tractor"></label>
        </div>
        <div class="actions"><button class="btn btn-primary" value="go" type="submit">Find match</button></div>`
        : `<p class="flag flag-missing">No competitor products yet. Add one with "Upload data" → Competitor.</p>`}
      </form>`;
    dlg.querySelector("form").addEventListener("submit", (e) => {
      if (e.submitter?.value !== "go") return;
      const f = new FormData(e.target);
      const prod = prods.find((p) => p.id === f.get("productId"));
      state = freshState();
      state.mode = "match";
      state.matchProduct = { id: prod.id, label: competitorName(prod.competitorId) + " " + prod.name };
      state.buyer.company = (f.get("company") || "").trim();
      state.buyer.application = (f.get("application") || "").trim();
      runMatch();
    });
    dlg.showModal();
  }

  async function runMatch() {
    state.loading = true; state.error = null; state.result = null; render();
    try {
      state.result = await Api.match({ competitorProductId: state.matchProduct.id, buyer: { ...state.buyer },
                                       factors: {}, customFactors: [], chatNotes: [] });
    } catch (err) {
      state.error = err.message;
    } finally {
      state.loading = false; render();
    }
  }

  function renderMatch() {
    botMsg("Which competitor product does the customer use today?", { compact: true });
    userMsg(state.matchProduct.label + (buyerSummary() ? " · " + buyerSummary() : ""), openMatchDialog);
    if (state.loading) loadingWidget();
    else if (state.error) botMsg(`<div class="flag flag-conflict">Couldn't find a match: ${esc(state.error)}</div>`);
    else if (state.result) resultWidget(state.result);
  }

  function render() {
    chat.innerHTML = "";
    if (state.mode === "match") {
      botMsg("Let's find the closest Danfoss products for the competitor product your customer uses.");
      renderMatch();
      renderBrief();
      renderStepper();
      requestAnimationFrame(() => (chat.scrollTop = chat.scrollHeight));
      return;
    }
    botMsg("Hi! I'll help you compare a Danfoss product with its competitors. Pick the options below, or type a question at any time.");
    renderLog(0);
    segments().forEach((seg, i) => {
      botMsg(esc(seg.prompt), { compact: true });
      userMsg(seg.answer, () => editSegment(i));
      renderLog(i + 1);
    });

    const step = currentStep();
    if (step.kind === "node") nodeWidget(step.depth);
    else if (step.kind === "competitors") competitorWidget();
    else if (step.kind === "factors") factorWidget();
    else if (step.kind === "buyer") buyerWidget();
    else if (step.kind === "review") reviewWidget();
    else if (state.loading) loadingWidget();
    else if (state.error) botMsg(`<div class="flag flag-conflict">Couldn't build the comparison: ${esc(state.error)}</div>`);
    else if (state.result) resultWidget(state.result);

    renderBrief();
    renderStepper();
    requestAnimationFrame(() => (chat.scrollTop = chat.scrollHeight));
  }

  // ---------- free-text composer ----------
  $("#composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#composer-input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    const stage = segments().length;
    state.log.push({ stage, role: "user", text });
    const typing = { stage, role: "typing" };
    state.log.push(typing);
    render();
    let answer;
    try {
      answer = (await Api.ask({ question: text, context: buildPayload() })).answer;
    } catch (err) {
      answer = "Sorry, I couldn't reach the assistant: " + err.message;
    }
    state.log = state.log.filter((m) => m !== typing);
    state.log.push({ stage, role: "bot", text: answer });
    render();
  });

  $("#btn-restart").addEventListener("click", () => { state = freshState(); render(); });

  async function reloadCatalog() { catalog = await Api.getCatalog(); }
  $("#btn-upload").addEventListener("click", () => catalog && openUploadDialog());
  $("#btn-match").addEventListener("click", () => catalog && openMatchDialog());

  // ---------- boot ----------
  Api.getCatalog()
    .then((c) => { catalog = c; render(); })
    .catch((err) => { chat.innerHTML = `<div class="flag flag-conflict">Couldn't load the catalog: ${esc(err.message)}</div>`; });
})();
