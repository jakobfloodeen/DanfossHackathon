// ---------------------------------------------------------------
// Chat-style guided flow:
//   product tree (any depth) → competitors → priorities → buyer → review → result
// Everything the user can pick comes from the catalog (GET /api/catalog),
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

  // Detailed status labels, used by the print sheet (the screen uses the three simple labels from sales-view.js).
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
      system: null,                               // { label, payload } in system mode
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
  /** PDFs and web pages saved by the web import (.txt) can be turned into facts. */
  const extractable = (name) => /\.(pdf|txt)$/i.test(name || "");

  /** Uploaded datasheets that have not been turned into facts yet (for the Extract panel). */
  function datasetsWithoutFacts() {
    const out = [];
    const add = (owner, label, d) => { if (!d.facts && extractable(d.name)) out.push({ ownerId: owner, fileId: d.fileId, name: d.name, label }); };
    const walkTree = (nodes) => nodes.forEach((n) => { (n.datasets || []).forEach((d) => add(n.id, n.label, d)); walkTree(n.children || []); });
    walkTree(catalog?.tree || []);
    (catalog?.competitorProducts || []).forEach((p) => (p.datasets || []).forEach((d) => add(p.id, `${competitorName(p.competitorId)} ${p.name}`, d)));
    return out;
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
    const bubble = el("div", { class: "bubble" });
    if (typeof content === "string") bubble.innerHTML = content; else bubble.appendChild(content);
    wrap.appendChild(bubble);
    chat.appendChild(wrap);
    return bubble;
  }
  /** One answered step as a single line: "Product family  Valves  Edit" (instead of a question + answer bubble). */
  function doneRow(label, answer, onEdit) {
    const row = el("div", { class: "done-row" });
    row.appendChild(el("span", { class: "done-label" }, esc(label)));
    row.appendChild(el("span", { class: "done-answer" }, esc(answer)));
    if (onEdit) row.appendChild(el("button", { class: "link-btn", type: "button", onclick: onEdit }, "Edit"));
    chat.appendChild(row);
  }
  const SHORT_LABEL = { node0: "Product family", node1: "Type", node2: "Product", competitors: "Compare against",
                        factors: "Priorities", buyer: "Buyer" };

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
        <details class="web-import">
          <summary>…or import a datasheet from a manufacturer's website</summary>
          <div class="form-grid tight">
            <label class="field span-2"><span>Website or product page</span>
              <input name="webUrl" type="url" placeholder="https://www.kawasakihydraulics.com/"></label>
            <label class="field"><span>Look for</span>
              <select name="webTopic"><option>valves</option><option>pumps</option><option>motors</option><option>steering</option></select></label>
            <div class="field"><span>&nbsp;</span><button class="btn btn-ghost" type="button" data-act="web-search">Search website</button></div>
          </div>
          <div class="web-results"></div>
        </details>
        <div class="form-issues"></div>
        <div class="extract-box"></div>
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

    // which catalog item the files belong to (same fields for file upload and web import)
    const targetMeta = () => {
      const isComp = f.source.value === "competitor";
      if (f.source.value === "customer") return { source: "customer", customerName: f.customerName.value.trim() };
      return isComp ? {
        source: "competitor",
        competitorId: f.competitorId.value !== "__new" ? f.competitorId.value : null,
        competitorName: f.competitorId.value === "__new" ? f.competitorName.value.trim() : null,
        productName: f.productName.value.trim(),
        categoryId: f.categoryId.value,
        equivalentOf: f.equivalentOf.value || null,
      } : { source: "danfoss", nodeId: f.nodeId.value };
    };

    // ---- import from a website (teammates' scraper) ----
    const webBox = f.querySelector(".web-results");
    f.querySelector('[data-act="web-search"]').addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (!f.webUrl.value.trim()) { webBox.innerHTML = flagHtml({ level: "error", text: "Enter a website address." }); return; }
      btn.disabled = true;
      webBox.innerHTML = '<p class="hint">Searching the website (up to a minute)…</p>';
      try {
        const res = await Api.webDiscover(f.webUrl.value.trim(), f.webTopic.value);
        webBox.innerHTML = (res.issues || []).map(flagHtml).join("") + (res.documents.length ? `
          <div class="web-list">${res.documents.map((d, i) => `<label class="web-doc"><input type="radio" name="webPick" value="${esc(d.url)}" data-name="${esc(d.name)}" ${i === 0 ? "checked" : ""}>
            <span><strong>${esc(d.name)}</strong>${d.product ? ` · ${esc(d.product)}` : ""} <span class="hint">${esc(d.kind === "html" ? "web page text" : d.kind === "pdf" ? "PDF" : "document link (checked on import)")}</span>
            <a href="${esc(d.kind === "auto" ? "/api/web/open?url=" + encodeURIComponent(d.url) : d.url)}" target="_blank" rel="noopener" class="hint">open</a></span></label>`).join("")}</div>
          <div class="actions"><button class="btn btn-primary" type="button" data-act="web-import">Import selected for the product chosen above</button></div>` : "");
        webBox.querySelector('[data-act="web-import"]')?.addEventListener("click", importPicked);
      } catch (err) {
        webBox.innerHTML = flagHtml({ level: "error", text: err.message });
      } finally {
        btn.disabled = false;
      }
    });
    async function importPicked(e) {
      const pick = f.querySelector('input[name="webPick"]:checked');
      const meta = targetMeta();
      if (!pick || meta.source === "customer") return;
      const btn = e.currentTarget;                     // currentTarget is gone after an await
      btn.disabled = true;
      const issueBox = f.querySelector(".form-issues");
      issueBox.innerHTML = '<p class="hint">Downloading…</p>';
      try {
        const res = await Api.webImport({ ...meta, url: pick.value, title: pick.dataset.name });
        issueBox.innerHTML = res.issues.map(flagHtml).join("");
        if (res.uploaded?.length) { await reloadCatalog(); showExtract(res.datasets); }
      } catch (err) {
        issueBox.innerHTML = flagHtml({ level: "error", text: err.message });
      } finally {
        btn.disabled = false;
      }
    }

    // ---- extract facts (teammates' converter): from new uploads, or datasheets that have no facts yet ----
    const extractBox = f.querySelector(".extract-box");
    function showExtract(datasets) {
      const pdfs = (datasets || []).filter((d) => extractable(d.name));
      if (!pdfs.length) { extractBox.innerHTML = ""; return; }
      extractBox.innerHTML = `<h4>Turn datasheets into comparable facts</h4>
        <p class="hint">The AI reads each page with data (blank and cover pages are skipped; roughly $0.001–0.002 per page).
          For long datasheets you can limit it to the pages with technical data, e.g. 1-12.</p>
        ${pdfs.map((d) => `<div class="extract-row" data-owner="${esc(d.ownerId)}" data-file="${esc(d.fileId)}">
          <span class="extract-name">${esc(d.label ? d.label + " · " : "")}${esc(d.name)}</span>
          <input class="extract-pages" placeholder="all pages" size="8" aria-label="Pages">
          <button class="btn btn-ghost" type="button">Extract facts</button>
          <div class="extract-status hint"></div></div>`).join("")}`;
      extractBox.querySelectorAll(".extract-row").forEach((row) => row.querySelector("button").addEventListener("click", async (e) => {
        const btn = e.currentTarget, status = row.querySelector(".extract-status");
        btn.disabled = true;
        try {
          const res = await Api.extract(row.dataset.owner, row.dataset.file, row.querySelector(".extract-pages").value.trim());
          if (!res.jobId) { status.innerHTML = res.issues.map(flagHtml).join(""); btn.disabled = false; return; }
          for (;;) {                                         // poll the background job
            const job = await Api.job(res.jobId);
            status.textContent = job.progress || job.status;
            if (job.status === "done" || job.status === "failed" || job.status === "unknown") break;
            await new Promise((r) => setTimeout(r, 2000));
          }
          await reloadCatalog();
        } catch (err) {
          status.innerHTML = flagHtml({ level: "error", text: err.message });
        } finally {
          btn.disabled = false;
        }
      }));
    }
    showExtract(datasetsWithoutFacts());

    f.querySelector('[data-act="upload"]').addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const issueBox = f.querySelector(".form-issues");
      const isComp = f.source.value === "competitor";
      const isCustomer = f.source.value === "customer";
      const meta = targetMeta();
      if (!fileInput.files.length) {                   // nothing chosen yet: say so instead of a server error
        issueBox.innerHTML = flagHtml({ level: "error", text: "Choose a file first: drop it on the box above or click the box to pick one." });
        return;
      }
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
          if (!isCustomer) showExtract(res.datasets);
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
    box.appendChild(el("p", {}, esc(BUYER_PROMPT) + ' <span class="hint">Dani looks the customer up and tailors the business part to them.</span>'));
    const form = el("form", { class: "form-grid" });
    form.innerHTML = `
      <div class="field span-2 cust-field"><label for="cust-name">Company / buyer name <span class="req">*</span></label>
        <input id="cust-name" name="company" required autocomplete="off" value="${esc(b.company)}" placeholder="Start typing, e.g. John Deere">
        <ul class="cust-suggest" hidden></ul>
        <div class="cust-panel"></div>
      </div>
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
    const input = form.company, list = form.querySelector(".cust-suggest"), panel = form.querySelector(".cust-panel");
    const showPanel = () => { panel.innerHTML = customerPanelHtml(); wirePanel(); };
    function wirePanel() {
      panel.querySelectorAll('[data-act="read-site"]').forEach((btn) => btn.addEventListener("click", () => {
        const url = panel.querySelector("[name=custUrl]")?.value.trim() || state.customer?.entry?.website || "";
        startResearch(state.customer?.entry?.name || input.value.trim(), url, false, showPanel);
      }));
      panel.querySelector('[data-act="reread-site"]')?.addEventListener("click", () =>
        startResearch(state.customer.entry?.name || input.value.trim(), state.customer.profile?.website || "", true, showPanel));
      panel.querySelector('[data-act="other-url"]')?.addEventListener("click", (e) => {
        e.target.hidden = true;
        const box = panel.querySelector(".cust-url"); box.hidden = false; box.querySelector("input").focus();
      });
      panel.querySelector("[name=custUrl]")?.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); panel.querySelector('[data-act="read-site"]').click(); }
      });
    }
    let timer = null, picked = false;
    input.addEventListener("input", () => {
      picked = false;
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const q = input.value.trim();
        if (q.length < 2) { list.hidden = true; return; }
        try {
          const { suggestions } = await Api.searchCustomers(q);
          list.innerHTML = suggestions.map((h, i) => `<li><button type="button" data-i="${i}">
              <strong>${esc(h.name)}</strong>${h.type ? ` <span class="hint">· ${esc(h.type)}</span>` : ""}
              ${h.matchedBy === "alias" ? ` <span class="hint">· “${esc(h.matched)}”</span>` : ""}
              ${h.matchedBy === "similar" ? ' <span class="hint">· did you mean?</span>' : ""}
              <span class="cust-tag">${h.hasProfile ? "profile · " + esc((h.researchedAt || "").slice(0, 10)) : h.files?.length ? "feedback files" : "no profile yet"}</span>
            </button></li>`).join("");
          list.hidden = !suggestions.length;
          list.querySelectorAll("button").forEach((btn) => btn.addEventListener("mousedown", (e) => {
            e.preventDefault();                                  // keep focus: no blur before the pick
            const h = suggestions[+btn.dataset.i];
            if (h.matchedBy === "similar" && q.toLowerCase() !== h.name.toLowerCase())
              Api.addCustomerAlias(q, h.name).catch(() => {});  // remember this spelling for next time
            input.value = h.name; picked = true; list.hidden = true;
            lookUpCustomer(h.name, showPanel);
          }));
        } catch { list.hidden = true; }
      }, 180);
    });
    input.addEventListener("blur", () => {
      setTimeout(() => { list.hidden = true; }, 150);
      if (!picked && input.value.trim()) lookUpCustomer(input.value.trim(), (canonical) => {
        if (canonical && canonical !== input.value.trim()) input.value = canonical;
        showPanel();
      });
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      state.buyer = Object.fromEntries(["company", "application", "currentSupplier", "notes"].map((k) => [k, (fd.get(k) || "").trim()]));
      if (!state.buyer.company) return;
      state.done.buyer = true; render();
    });
    box.appendChild(form);
    botMsg(box).classList.add("bubble-wide");
    if (b.company && sameCustomer(state.customer?.typed, b.company)) showPanel();
    else if (b.company) lookUpCustomer(b.company, showPanel);
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
      ${state.buyer.notes ? `<dt>Notes</dt><dd>${esc(state.buyer.notes)}</dd>` : ""}
      ${customerSummaryText() ? `<dt>Customer profile</dt><dd>${esc(customerSummaryText())}</dd>` : ""}`));
    if (p.status === "data-pending") box.appendChild(el("p", { class: "flag flag-missing" }, "Limited data is loaded for this product, so the comparison will be partial."));
    box.appendChild(el("div", { class: "actions" })).appendChild(el("button", {
      class: "btn btn-primary btn-lg", type: "button", onclick: generate,
    }, "Generate comparison"));
    botMsg(box).classList.add("bubble-wide");
  }

  // ---------- customer lookup + website research (one AI call per new customer; saved for everyone) ----------
  const sameCustomer = (x, y) => String(x || "").toLowerCase().replace(/[^a-z0-9]+/g, "") ===
                                 String(y || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  const SECTION_LABEL = { summary: "What they do", key_facts: "Key facts", segments: "Markets", machines: "Machines",
    products: "Products & models", priorities: "Their stated priorities", hydraulics: "Hydraulics & controls",
    partners: "Partners", regions: "Regions", news: "News & expansion — opportunities to follow",
    used: "What they use today (named on their site)" };
  const NEWS_LABEL = { new_plant: "New plant", new_facility: "New facility", new_department: "New department",
    new_market: "New market", investment: "Investment", acquisition: "Acquisition", partnership: "Partnership",
    new_product: "Launch", electrification_program: "Electrification", other: "News" };
  const BIZ_KIND = [["opportunity", "Opportunities to follow"], ["fit", "Where the product fits their machines and goals"],
    ["displace", "Against what they use today"], ["risk", "Check before the meeting"]];
  const shortUrl = (u) => String(u || "").replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");

  /** state.customer = { typed, entry (known customer) | null, profile | null, research: {status, progress} } */
  async function lookUpCustomer(name, done) {
    try {
      const { customer, profile } = await Api.customerProfile(name);
      const running = state.customer?.research?.status === "running" && sameCustomer(state.customer.typed, name);
      state.customer = { typed: customer?.name || name, entry: customer, profile,
                         research: running ? state.customer.research : null };
      done(customer?.name);
    } catch { done(); }
  }

  function itemText(key, it) {
    if (key === "key_facts") return `${it.label}: ${it.value}`;
    if (it.supplier && it.quote === it.statement) return `${it.supplier}: “${it.statement}”`;       // found by code
    if (key === "used") return [it.supplier, it.component, it.machine].filter(Boolean).join(" / ") + ": " + (it.statement || "");
    const main = it.name || it.statement || it.text || "";
    const where = key === "news" && it.location && !main.toLowerCase().includes(it.location.toLowerCase()) ? ` (${it.location})` : "";
    return (it.detail ? `${main} — ${it.detail}` : main) + where;
  }

  /** The whole saved profile, every statement linked to the page it came from. The specific detail first;
   *  the basics a rep already knows (markets, regions, key facts) on one line at the bottom. */
  function profileHtml(p) {
    const link = (it) => it.url ? ` <a class="src-link" href="${esc(it.url)}" target="_blank" rel="noopener" title="${esc(it.quote ? "“" + it.quote + "”" : it.title || it.url)}">${esc(it.title || shortUrl(it.url))}</a>` : "";
    const badge = (key, it) => key === "priorities" ? `<span class="badge badge-muted">${esc(String(it.topic || "").replace(/_/g, " "))}</span> `
      : key === "news" ? `<span class="badge badge-brand">${esc(NEWS_LABEL[it.kind] || "News")}</span> ` : "";
    const sec = (key, items) => !items?.length ? "" : `<div class="prof-sec"><div class="biz-label">${esc(SECTION_LABEL[key])}</div><ul>
      ${items.map((it) => `<li>${badge(key, it)}${esc(itemText(key, it))}${it.date ? ` <span class="hint">(${esc(it.date)})</span>` : ""}${link(it)}</li>`).join("")}</ul></div>`;
    const used = [...(p.supplier_mentions || []), ...(p.installed || [])];
    const basics = [p.summary?.text, (p.segments || []).map((x) => x.name).join(", "), (p.regions || []).map((x) => x.name).join(", "),
      (p.key_facts || []).map((x) => `${x.label}: ${x.value}`).join(" · ")].filter(Boolean);
    return `<div class="prof">
      ${sec("used", used)}${sec("news", p.news)}${sec("priorities", p.priorities)}${sec("hydraulics", p.hydraulics)}
      ${sec("products", p.products)}${sec("machines", p.machines)}${sec("partners", p.partners)}
      ${!used.length ? '<p class="hint">No hydraulic supplier or component is named on the pages read.</p>' : ""}
      ${basics.length ? `<div class="hint"><b>Basics:</b> ${esc(basics.join(" · "))}</div>` : ""}
      <div class="hint">${esc(p.message || "")} · ${(p.pages || []).length} page(s) visited${p.dropped?.length ? ` · ${p.dropped.length} AI statement(s) dropped because their quote was not on the page` : ""}${p.terms?.status === "no_reservation_found" ? " · terms checked: no reservation against automated reading" : ""}</div></div>`;
  }

  function customerPanelHtml() {
    const c = state.customer;
    if (!c) return "";
    const name = c.entry?.name || c.typed;
    const r = c.research;
    if (r?.status === "running")
      return `<div class="cust-card"><div><span class="spinner-dot"></span> ${esc(r.progress || "Reading the website…")}</div>
        <div class="hint">This takes 1–3 minutes. You can continue; the comparison waits for it.</div></div>`;
    const err = r?.status === "error" ? `<div class="text-warn">${esc(r.progress || "Reading the website failed.")}</div>` : "";
    if (c.profile?.status === "ok") {
      const p = c.profile;
      return `<div class="cust-card is-ok">${err}
        <div><strong>${esc(name)}</strong> · customer profile from <a href="${esc(p.website)}" target="_blank" rel="noopener">${esc(shortUrl(p.website))}</a>
          · <span class="visited">website last read ${esc((p.researched_at || "").slice(0, 10))}</span></div>
        <div class="hint">${esc(p.summary?.text || p.message || "")}</div>
        <details><summary>View the full profile</summary>${profileHtml(p)}</details>
        <div class="cust-actions"><button class="link-btn" type="button" data-act="reread-site">Read the website again</button>
        <button class="link-btn" type="button" data-act="other-url">Use another address</button>
        <div class="cust-url" hidden><input name="custUrl" placeholder="e.g. www.deere.com">
        <button class="btn btn-ghost btn-sm" type="button" data-act="read-site">Read this address</button></div></div></div>`;
    }
    if (c.profile && c.profile.status !== "ok") {                // terms reservation, or nothing readable
      const p = c.profile;
      return `<div class="cust-card">${err}
        <div><strong>${esc(name)}</strong> · <span class="text-warn">${esc(p.message || "")}</span></div>
        ${p.terms?.quote ? `<div class="hint">“${esc(p.terms.quote)}” — <a href="${esc(p.terms.url)}" target="_blank" rel="noopener">${esc(shortUrl(p.terms.url))}</a></div>` : ""}
        <div class="hint">Checked ${esc((p.researched_at || "").slice(0, 10))}. The comparison's business part stays generic for ${esc(name)}.</div>
        <button class="link-btn" type="button" data-act="reread-site">Check again</button></div>`;
    }
    const known = c.entry?.website;
    if (known) return `<div class="cust-card">${err}
        <div>Dani has no profile of <strong>${esc(name)}</strong> yet. Website on file: ${esc(shortUrl(known))}</div>
        <div class="actions"><button class="btn btn-ghost btn-sm" type="button" data-act="read-site">Read their website</button>
        <span class="hint">One AI call (well under 1 cent); saved for every later comparison with ${esc(name)}.</span></div>
        <div class="cust-actions"><button class="link-btn" type="button" data-act="other-url">Use another address</button>
        <div class="cust-url" hidden><input name="custUrl" placeholder="e.g. www.deere.com">
        <button class="btn btn-ghost btn-sm" type="button" data-act="read-site">Read this address instead</button></div></div></div>`;
    return `<div class="cust-card">${err}
        <div>Dani has nothing on <strong>${esc(name)}</strong> yet. Paste their website to read it — they are then added to the customer list.</div>
        <div class="cust-url"><input name="custUrl" placeholder="e.g. www.${esc(name.toLowerCase().replace(/[^a-z0-9]+/g, ""))}.com">
        <button class="btn btn-ghost btn-sm" type="button" data-act="read-site">Read website</button></div>
        <div class="hint">Optional. One AI call (well under 1 cent); the profile is saved for everyone.</div></div>`;
  }

  async function startResearch(company, website, refresh, onUpdate) {
    if (!company) return;
    state.customer = { ...(state.customer || {}), typed: company, research: { status: "running", progress: "Starting…" } };
    onUpdate();
    try {
      const res = await Api.researchCustomer({ company, website, refresh });
      if (res.profile) { state.customer.profile = res.profile; state.customer.research = null; }
      else state.customer.research.jobId = res.jobId;
      onUpdate();
      while (state.customer.research?.jobId && state.customer.research.status === "running") {
        await new Promise((r) => setTimeout(r, 1500));
        const job = await Api.job(state.customer.research.jobId);
        state.customer.research.progress = job.progress;
        if (job.status === "done") { state.customer.profile = job.profile; state.customer.research = null; }
        else if (job.status === "error" || job.status === "unknown") state.customer.research = { status: "error", progress: job.progress };
        onUpdate();
      }
      const { customer } = await Api.customerProfile(company);   // a new customer is now in the list
      if (customer) state.customer.entry = customer;
    } catch (err) {
      state.customer.research = { status: "error", progress: err.message };
      onUpdate();
    }
  }

  function customerSummaryText() {
    const c = state.customer;
    if (!c || !sameCustomer(c.typed, state.buyer.company)) return "";
    if (c.research?.status === "running") return "website is being read…";
    if (c.profile?.status === "ok") return `from ${shortUrl(c.profile.website)}, read ${(c.profile.researched_at || "").slice(0, 10)}`;
    return "none (business part will be generic)";
  }

  async function researchFinished() {          // a comparison waits for a running website read (max ~4 min)
    for (let i = 0; i < 160 && state.customer?.research?.status === "running"; i++) await new Promise((r) => setTimeout(r, 1500));
  }

  // ---------- generate + results ----------
  const PIPELINE = ["Find", "Normalize", "Compare", "Reference"];

  async function generate() {
    state.loading = true; state.error = null; render();
    try {
      await researchFinished();
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
      return `<td class="cell-pending" title="${esc(v.note || "")}">Awaiting extraction</td>`;
    }
    if (v && v.status === "na") {                 // this product has no value under this name: not "missing"
      return `<td class="cell-na" title="${esc(v.note || "No matching value")}">–</td>`;
    }
    if (!v || v.value == null) {                  // the datasheet does not state it (detail in the tooltip)
      return `<td class="cell-missing" title="${esc(v?.note || "Not found in sources")}">Not stated</td>`;
    }
    // numbered footnotes in the short table; direct page links ("p.12") for rows under "All facts"
    const refs = (v.sources || []).map(ref).join("") + (v.links || []).map((l) => l.url
      ? `<a href="${esc(l.url)}" target="_blank" rel="noopener" class="ref" title="${esc(l.title)}">p.${esc(l.page)}</a>`
      : `<span class="ref" title="${esc(l.title)}">p.${esc(l.page)}</span>`).join("");
    const st = SalesView.simpleStatus(v.status, true);
    const why = SalesView.STATUS_WHY[v.status] || "";
    const tag = st.key === "good" || v.status === "sample" ? "" : `<span class="st st-${st.key}" title="${esc([why, v.note].filter(Boolean).join(": "))}">${st.icon} ${st.label}</span>`;
    // short notes that change the meaning stay visible; the rest is a tooltip on the label
    const note = v.note && ["conflict", "converted", "inferred"].includes(v.status) ? `<div class="cell-note">${esc(v.note)}</div>` : "";
    const build = v.build ? `<span class="st st-build" title="${esc(buildTitle(v))}">depends on build</span>` : "";
    return `<td class="cell-${v.status || "ok"}">${esc(v.value)}${refs}${tag}${build}${note}</td>`;
  }

  /** "2 to check · 6 unknown" - only three kinds of status exist for the reader: Verified, Check, Unknown. */
  function issueSummary(r) {
    let check = 0, unknown = 0;
    r.attributes.filter((a) => a.core !== false).forEach((a) => Object.values(a.values).forEach((v) => {
      if (v?.status === "na" || v?.status === "sample") return;
      const k = SalesView.simpleStatus(v?.status, v && v.value != null).key;
      if (k === "check") check++; else if (k === "unknown") unknown++;
    }));
    const parts = [];
    if (check) parts.push(`<span class="issue-chip flag-conflict">${check} to check</span>`);
    if (unknown) parts.push(`<span class="issue-chip flag-missing">${unknown} unknown</span>`);
    return parts.length ? parts.join("") : '<span class="issue-chip flag-ok">All shown values verified</span>';
  }

  /** Data issue in "Details": three labels (Verified / Check / Unknown), the specific kind in brackets. */
  function resultFlagHtml(f) {
    const st = SalesView.simpleStatus(f.level === "ok" ? "ok" : f.level, f.level !== "missing" && f.level !== "pending");
    const kind = FLAG_LABEL[f.level] || f.level;
    return `<div class="flag flag-${esc(FLAG_LABEL[f.level] ? f.level : "warning")}"><strong>${st.label}</strong> <span class="flag-kind">(${esc(kind.toLowerCase())})</span> ${esc(f.text)}</div>`;
  }

  /** "12 AI statements · 31 citations · every number checked by code" - the anti-hallucination meter. */
  function groundingText(g) {
    if (!g || !g.statements) return "";
    const checks = g.problems
      ? `${g.problems} failed check${g.problems > 1 ? "s" : ""} - see Open points`
      : "every number checked by code against its source";
    return `<div class="grounding" title="Every AI statement must cite fact ids; every number in it is verified against the cited facts by code. Statements that fail are listed under Open points - never silently accepted.">
      ${esc(g.statements)} AI statement${g.statements > 1 ? "s" : ""} · ${esc(g.citations)} citation${g.citations > 1 ? "s" : ""} · ${checks}</div>`;
  }

  /** "AI cost: $0.004 · 1 call" – the price of this answer, so the cost per comparison is visible. */
  function aiCostText(c) {
    if (!c) return "";
    if (c.cached) return `<span class="ai-cost" title="${esc(c.reason || "")}">AI cost: $0 (already compared)</span>`;
    if (!c.calls) return `<span class="ai-cost">No AI used${c.reason ? ` (${esc(c.reason)})` : ""}</span>`;
    return `<span class="ai-cost" title="${esc(c.input)} tokens in, ${esc(c.output)} tokens out${c.factsSent ? `, ${esc(c.factsSent)} facts sent` : ""}">AI cost: $${c.usd < 0.01 ? c.usd.toFixed(4) : c.usd.toFixed(3)} · ${c.calls} call${c.calls > 1 ? "s" : ""}</span>`;
  }

  // What level Dani compares at: the product as its datasheet describes it (everything any build can do), not the
  // customer's exact build / order code. Said on screen and in print so nobody mistakes the one for the other.
  const LEVEL_TEXT = "Compared at product level: the datasheet range across all builds, not the customer's exact build. " +
    "Values marked “depends on build” hold for some configurations only (end plate, inlet, spools, coil …). " +
    "Configure with application engineering before quoting.";
  const buildTitle = (v) => `Depends on the build: ${v.build}. Confirm the customer's configuration before quoting.`;

  const KIND_LABEL = { positioning: "Positioning", availability: "Availability", pricing: "Pricing",
                       selling_point: "Selling point", transition: "Transition", warning: "Warning" };

  // ---------- the customer's must-haves / nice-to-haves, from the business side ----------
  const FACTOR_TOPICS = { price: ["cost"], availability: ["service"], support: ["service", "uptime"],
    lifetime: ["reliability", "uptime"], flow: ["productivity"], pressure: ["productivity"], size: ["compactness"],
    electronics: ["automation", "connectivity", "precision_control", "electrification"],
    environment: ["emissions", "noise", "energy_efficiency", "sustainability"] };
  const FACTOR_NOTES = { price: ["pricing"], availability: ["availability", "transition"] };

  /** One entry per priority the user picked: the AI's answer for it, the compared values (code, from the table),
   *  what the customer's own website says about that topic, and the sales notes that answer it. */
  function priorityBriefs(r) {
    const picked = [...Object.entries(state.factors).filter(([, v]) => v === "must" || v === "nice")
                      .map(([id, level]) => ({ id, label: factorLabel(id), level })),
                    ...state.customFactors.map((c) => ({ id: "own:" + c, label: c, level: "nice" }))]
      .sort((a, b) => (a.level === "must" ? 0 : 1) - (b.level === "must" ? 0 : 1));
    const cols = [r.anchor, ...(r.matches || [])];
    const headline = (v) => (v && v.value != null ? String(v.value).split(" · ")[0] : "–");
    return picked.map((p) => {
      const rows = (r.attributes || []).filter((a) => a.factor === p.id && a.core !== false).slice(0, 3).map((a) => ({
        label: a.label, unit: a.unit,
        values: cols.map((c) => ({ name: c.name, value: headline(a.values[c.key]), sources: a.values[c.key]?.sources || [] })) }));
      const topics = FACTOR_TOPICS[p.id] || [];
      return { ...p, rows,
        ai: (r.insights?.priorities || []).find((x) => x.factor === p.id || x.factor === p.label),
        profile: (r.customerProfile?.items || []).filter((it) => topics.includes((it.kind.split(": ")[1] || ""))),
        notes: (r.commercial || []).filter((n) => (FACTOR_NOTES[p.id] || []).includes(n.kind)) };
    });
  }

  function prioritiesHtml(r) {
    const briefs = priorityBriefs(r);
    if (!briefs.length) return "";
    const refs = (list) => (list || []).map(ref).join("");
    return `<div class="biz-block biz-prio"><div class="biz-label">Specified requirements</div>
      ${briefs.map((b) => `<div class="prio-brief"><div class="prof-head"><span class="prio prio-${esc(b.level)}"></span>${esc(b.label)}
          <span class="hint">· ${b.level === "must" ? "must-have" : "nice-to-have"}</span></div>
        ${b.ai ? `<p>${esc(b.ai.text)}${refs(b.ai.sources)}</p>` : ""}
        ${!b.ai ? `<p class="hint">${b.rows.length ? "Values: see “The points that matter” above." : "No datasheet value in this comparison covers this priority."}</p>` : ""}
        ${b.profile.length ? `<ul class="prio-values">${b.profile.map((it) => `<li>Their website: ${esc(it.text)}${refs(it.sources)}</li>`).join("")}</ul>` : ""}
        ${b.notes.length ? `<ul class="prio-values">${b.notes.map((n) => `<li>${esc(KIND_LABEL[n.kind] || n.kind)} (approved): ${esc(n.text)}${refs(n.sources)}</li>`).join("")}</ul>` : ""}
      </div>`).join("")}</div>`;
  }

  /** Profile fact kinds (P1..) shown in the result -> section. The basics (markets, regions ...) are left out. */
  const FACT_SECTION = [["supplier mention", "used"], ["installed", "used"], ["news", "news"],
    ["priority", "priorities"], ["hydraulics", "hydraulics"], ["product", "products"]];

  /** "Business fit for <customer>": why this comparison matters to them (by kind), the specific things their
   *  website says (every line footnoted), and the approved sales-guideline notes that fit THIS comparison and
   *  customer (the rest folded away). Approved commercial notes + the review queue for pending ones. */
  function commercialHtml(r) {
    const approved = r.commercial || [];
    const pending = r.commercialPending || [];
    const other = r.commercialOther || [];
    const business = r.insights?.business || [];
    const prof = r.customerProfile;
    const prioHtml = prioritiesHtml(r);
    if (!approved.length && !pending.length && !other.length && !business.length && !prof && !prioHtml) return "";
    const who = state.buyer.company || prof?.company || "the customer";
    const profileItem = (it) => {
      const [kind, sub] = it.kind.split(": ");
      const badge = kind === "priority" ? `<span class="badge badge-muted">${esc((sub || "").replace(/_/g, " "))}</span> `
        : kind === "news" ? `<span class="badge badge-brand">${esc(NEWS_LABEL[sub] || "News")}</span> ` : "";
      return `<li>${badge}${esc(it.text)}${it.date ? ` <span class="hint">(${esc(it.date)})</span>` : ""}${(it.sources || []).map(ref).join("")}</li>`;
    };
    const groups = prof ? FACT_SECTION.reduce((acc, [kind, key]) => {
      const items = prof.items.filter((it) => it.kind.split(":")[0] === kind);
      if (items.length) (acc.find((g) => g[0] === key) || acc[acc.push([key, []]) - 1])[1].push(...items);
      return acc;
    }, []) : [];
    const snapshot = prof ? `<div class="biz-block"><div class="biz-label">From ${esc(who)}'s own website
        <span class="hint">· <a href="${esc(prof.website || "")}" target="_blank" rel="noopener">${esc(shortUrl(prof.website))}</a>${prof.researchedAt ? " · website last read " + esc(prof.researchedAt.slice(0, 10)) : ""}</span></div>
        <div class="prof-grid">${groups.map(([key, items]) => `<div class="prof-sec"><div class="prof-head">${esc(SECTION_LABEL[key])}</div><ul>${items.map(profileItem).join("")}</ul></div>`).join("")}</div>
        ${!groups.some(([k]) => k === "used") ? '<p class="hint">No hydraulic supplier or component is named on the pages read.</p>' : ""}
      </div>`
      : `<p class="hint">No customer profile for ${esc(who)} yet — look the customer up in the buyer step to tailor this part to their business.</p>`;
    const bizList = business.length ? `<div class="biz-block biz-why"><div class="biz-label">Why it matters to ${esc(who)}</div>
        ${BIZ_KIND.map(([kind, label]) => {
          const items = business.filter((it) => (it.kind || "fit") === kind);
          return items.length ? `<div class="prof-head">${esc(label)}</div><ul>${items.map((it) => `<li>${esc(it.text)}${(it.sources || []).map(ref).join("")}</li>`).join("")}</ul>` : "";
        }).join("")}</div>` : "";
    const note = (n, extra) => `<li class="com-note${extra ? " is-pending" : ""}"${n.why ? ` title="Picked because: ${esc(n.why)}"` : ""}>
      <span class="badge badge-${n.kind === "warning" || n.kind === "transition" ? "warn" : "brand"}">${esc(KIND_LABEL[n.kind] || n.kind)}</span>
      <span class="com-text">${esc(n.text)}${(n.sources || []).map(ref).join("")}</span>
      <span class="com-src">${esc(n.source)}${n.page ? ", slide " + esc(n.page) : ""}${n.regions && n.regions !== "all" ? " · " + esc(n.regions) : ""}</span>${extra || ""}</li>`;
    const canEdit = currentUser.role === "editor";
    const otherHtml = other.length ? `<details class="biz-other"><summary>${other.length} other approved note(s) not specific to this comparison or customer</summary>
        <ul>${other.map((n) => note({ ...n, product: n.product_id, source: n.source_file })).join("")}</ul></details>` : "";
    return `<section class="insight commercial"><h4>Business fit for ${esc(who)}</h4>
      ${prioHtml}${bizList}${snapshot}
      <div class="biz-label">Approved sales-guideline notes <span class="hint">(internal)</span></div>
      ${approved.length ? "" : '<p class="hint">No approved note matches this comparison and customer.</p>'}
      <ul>${approved.map((n) => note(n)).join("")}
      ${pending.map((n) => note({ ...n, product: n.product_id, source: n.source_file },
        canEdit ? ` <span class="pending-tools" data-id="${esc(n.id)}">· pending ·
            <button class="link-btn" type="button" data-decide="approve">Approve</button>
            <button class="link-btn" type="button" data-decide="reject">Reject</button></span>`
                : ' <span class="badge badge-muted">pending editor approval</span>')).join("")}</ul>
      ${otherHtml}
      <button class="link-btn" type="button" data-act="suggest-note">+ Suggest a note</button>
      <div class="suggest-note" hidden>
        <div class="form-grid tight">
          <label class="field"><span>Type</span><select name="nKind">${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
          <label class="field"><span>Source <span class="opt">(guideline + slide)</span></span><input name="nSource" placeholder="e.g. Q1 2026 guidelines, slide 6"></label>
          <label class="field span-2"><span>Note</span><input name="nText" placeholder="e.g. Standard all regions from Q3"></label>
        </div>
        <div class="actions"><span class="hint">${canEdit ? "You are an editor: the note goes live at once." : "Sent to an editor for approval first."}</span>
        <button class="btn btn-ghost btn-sm" type="button" data-act="send-note">Add</button></div>
      </div>
    </section>`;
  }

  function insightList(title, items) {
    if (!items || !items.length) return "";
    return `<section class="insight"><h4>${esc(title)}</h4><ul>${items.map((it) =>
      `<li>${esc(it.text)}${(it.sources || []).map(ref).join("")}${it.sample ? ' <span class="badge badge-muted">Sample</span>' : ""}</li>`).join("")}</ul></section>`;
  }

  // ---------- sales view of a result ----------
  // Order on screen: 1 bottom line · 2 scoreboard · 3 pitch cards · 4 sales notes · 5 details & proof (collapsed).
  // All numbers and verdicts come from code (sales-view.js); the AI's own text is only used as extra lines.

  /** Footnotes: the first two, then "+n" (all of them are in the source list and the full table). */
  function refsShort(list) {
    const l = list || [];
    return l.slice(0, 2).map(ref).join("") + (l.length > 2 ? `<a href="#src-${l[2]}" class="ref ref-more" title="${l.length - 2} more source${l.length > 3 ? "s" : ""}: ${l.slice(2).map((n) => "[" + n + "]").join(" ")}">+${l.length - 2}</a>` : "");
  }

  /** One value on the scoreboard: the number, a quiet unit, and at most one short verdict line. */
  function boardValue(x, col, verdict) {
    const v = x.attr.values[col.key];
    if (x.attr.placeholder || !v || v.value == null || v.status === "pending" || v.status === "na") {
      const why = v?.status === "pending" ? "Waiting for data extraction" : (v?.note || "Not stated in the datasheet");
      return `<div class="b-cell is-empty" title="${esc(why)}"><span class="b-none">${v?.status === "pending" ? "Awaiting data" : "—"}</span></div>`;
    }
    const st = SalesView.simpleStatus(v.status, true);
    const split = SalesView.splitValue(v.value);
    const unit = x.attr.unit ? `<span class="b-unit">${esc(x.attr.unit)}</span>` : "";
    const cond = v.build ? `<span class="b-build" title="${esc(buildTitle(v))}">depends on build</span>`
      : split.big && split.rest ? `<span class="b-cond" title="${esc(v.value)}">conditions</span>` : "";
    const body = split.big
      ? `<div class="b-num">${esc(split.big)}${unit}</div>`
      : `<div class="b-text" title="${esc(v.value)}">${esc(SalesView.shorten(v.value, 55))}</div>`;
    const why = [SalesView.STATUS_WHY[v.status], v.note].filter(Boolean).join(": ");
    const check = st.key === "check" && v.status !== "sample" ? `<span class="b-check" title="${esc(why)}">check</span>` : "";
    let line = "";
    if (verdict && ["ahead", "behind", "level"].includes(verdict.kind)) {
      const t = { ahead: ["▲", "We lead", "win"], behind: ["▼", "They lead", "lose"], level: ["≈", "Level", "level"] }[verdict.kind];
      line = `<div class="b-verdict v-${t[2]}"${verdict.approx ? ' title="Compared on the main number only: the datasheets state different conditions. Check before quoting."' : ""}>${t[0]} ${t[1]}${verdict.approx ? ' <span class="b-prob">(probably)</span>' : ""}</div>`;
    }
    return `<div class="b-cell">${body}<div class="b-meta">${refsShort(v.sources)}${cond}${check}</div>${line}</div>`;
  }

  function scoreboardHtml(a, r) {
    const cols = a.cols, n = cols.length;
    const head = cols.map((c) => `<div class="b-head ${c._ours ? "is-ours" : ""}"><div class="col-name">${esc(c.name)}</div>
        <div class="col-sub">${esc(c.manufacturer)}${c.knownEquivalent ? " · confirmed equivalent" : c.matchScore ? ` · match ${Math.round(c.matchScore * 100)}%` : ""}</div></div>`).join("");
    const rowHtml = (x) => {
      const pr = x.prio;
      const winners = x.verdicts.map((v) => v.winner?.key).filter(Boolean);
      const anchorWins = winners.length && winners.every((k) => k === r.anchor.key);
      const cells = cols.map((c, i) => {
        if (i === 0) return boardValue(x, c, null);
        return boardValue(x, c, x.verdicts[i - 1]);
      }).join("");
      const plain = SalesView.plainFor(x.attr);
      return `<div class="b-row${pr ? " is-" + pr : ""}">
        <div class="b-label">${pr ? `<div class="prio-tag prio-tag-${pr}">${pr === "must" ? "Must-have" : "Nice-to-have"}</div>` : ""}<div class="b-name">${esc(SalesView.niceLabel(x.attr.label))}</div>
        ${plain ? `<div class="b-plain">${esc(plain)}</div>` : ""}</div>${cells}</div>`;
    };
    if (!a.shown.length) return '<p class="hint">No point could be compared directly between these products. See the details below.</p>';
    return `<section class="board" style="--cols:${n}" aria-label="Scoreboard">
      <div class="b-row b-headrow"><div class="b-label"><h4>The points that matter</h4></div>${head}</div>
      ${a.shown.map(rowHtml).join("")}
      <div class="b-foot"><span><span class="b-verdict v-win">▲ We lead</span> / <span class="b-verdict v-lose">▼ They lead</span> Danfoss compared with that competitor</span>
        <span><span class="b-prob">(probably)</span> conditions differ</span>
        <span><span class="b-check">check</span> confirm before quoting</span>
        <span><span class="b-build">depends on build</span> holds for some configurations only</span>
        <span>— not stated</span>
        ${a.restCount ? `<button class="link-btn" type="button" data-act="open-details">Show ${a.restCount} more comparable point${a.restCount > 1 ? "s" : ""} in Details ↓</button>` : ""}</div>
    </section>`;
  }

  function bottomLineHtml(r, a) {
    const c = a.counts, total = c.ahead + c.level + c.behind + c.open;
    const seg = (cls, n, label) => n ? `<i class="${cls}" style="flex:${n}" title="${n} ${label}"></i>` : "";
    return `<section class="bottom-line fit-${a.fit}" aria-label="Bottom line">
      <div class="bl-fit"><div class="bl-fit-label">${esc(a.fitLabel)}</div>
        ${total ? `<div class="bl-bar" title="${esc(a.basis)}">${seg("w", c.ahead, "ahead")}${seg("l", c.level, "level")}${seg("b", c.behind, "behind")}${seg("o", c.open, "cannot be judged")}</div>
        <div class="bl-bar-key"><span>${c.ahead} ahead</span><span>${c.level} level</span><span>${c.behind} behind</span><span>${c.open} unclear</span></div>` : ""}</div>
      <div class="bl-text"><div class="bl-head">${esc(a.headline)}</div>
        <div class="bl-detail">${esc(a.detail.join(" "))}</div>
        ${a.others.length ? `<div class="bl-others">${a.others.map((o) => `<span><strong>${esc(o.name)}:</strong> ${o.counts.ahead} ahead · ${o.counts.level} level · ${o.counts.behind} behind · ${o.counts.open} unclear</span>`).join("")}</div>` : ""}
        ${a.basis ? `<div class="hint">${esc(a.basis)}</div>` : ""}</div>
    </section>`;
  }

  /** The three pitch cards. Each shows its first 4 lines; the rest open in place ("Show N more"), nothing is sent away. */
  function pitchHtml(r, a) {
    const SHOW = 4;
    const li = (it) => `<li>${esc(it.text)}${(it.sources || []).map(ref).join("")}</li>`;
    const ai = (list) => (list || []).map((it) => ({ text: SalesView.shorten(it.text, 170), sources: it.sources }));
    // table-based lines first (they are what the scoreboard shows), then the AI's checked sentences
    const wins = [...a.wins, ...ai(r.insights?.benefits)];
    const losses = a.losses;
    const checks = [...a.checks, ...ai(r.insights?.tradeoffs)];
    const list = (items, empty) => {
      if (!items.length) return `<ul><li class="pc-empty">${empty}</li></ul>`;
      const rest = items.slice(SHOW);
      return `<ul>${items.slice(0, SHOW).map(li).join("")}</ul>` + (rest.length
        ? `<details class="pc-more"><summary>Show ${rest.length} more</summary><ul>${rest.map(li).join("")}</ul></details>` : "");
    };
    const openCount = a.counts.open;
    return `<div class="pitch">
      <section class="pcard pc-win"><h4>Where we win</h4>${list(wins, "No clear win in the data yet.")}</section>
      <section class="pcard pc-lose"><h4>Where they win</h4>${list(losses,
        `No point where they clearly beat us in the data we have${openCount ? ` (${openCount} point${openCount > 1 ? "s" : ""} still unclear)` : ""}.`)}</section>
      <section class="pcard pc-check"><h4>Check before you promise</h4>${list(checks, "Nothing flagged.")}</section>
    </div>`;
  }

  // ---------- one-page print summary ("Print / save as PDF") ----------
  // The interactive result is too long to print (all value variants, every quote, every note).
  // The printout is this separate, compact sheet built from the same data: one headline value per
  // cell with its footnote numbers, capped lists, and a source key (document + page) instead of quotes.
  // Nothing is recalculated or invented here - it only shortens what the result already shows.
  const PRINT = { cellChars: 36, differences: 4, benefits: 3, tradeoffs: 3, openPoints: 4, commercial: 3, textChars: 140 };
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s);

  function printCell(v) {
    if (v && v.value == null && v.status === "pending") return '<td class="p-muted">Pending</td>';
    if (v && v.status === "na") return '<td class="p-muted">–</td>';
    if (!v || v.value == null) return '<td class="p-missing">Not stated</td>';
    const variants = String(v.value).split(" · ");          // the engine joins several stated values with " · "
    const more = variants.length > 1 ? ` <span class="p-more">+${variants.length - 1}</span>` : "";
    const refs = (v.sources || []).length ? `<sup>${v.sources.slice(0, 3).join(",")}${v.sources.length > 3 ? ",…" : ""}</sup>` : "";
    const links = (v.links || []).map((l) => `p.${esc(l.page)}`).join(" ");
    const st = CELL_STATUS[v.status];
    const tag = st && v.status !== "converted" ? ` <span class="p-flag">${esc(st[0])}</span>` : "";
    const build = v.build ? ' <span class="p-build" title="depends on build">build</span>' : "";
    return `<td>${esc(clip(variants[0], PRINT.cellChars))}${more}${refs}${links ? ` <span class="p-more">${links}</span>` : ""}${tag}${build}</td>`;
  }

  function printList(title, items, max) {
    if (!items || !items.length) return "";
    const shown = items.slice(0, max);
    return `<div class="p-block"><h5>${esc(title)}</h5><ul>${shown.map((it) =>
      `<li>${esc(clip(it.text, PRINT.textChars))}${(it.sources || []).length ? `<sup>${it.sources.join(",")}</sup>` : ""}</li>`).join("")}
      ${items.length > max ? `<li class="p-more">+${items.length - max} more in the app</li>` : ""}</ul></div>`;
  }

  /** Source key: one line per document with its footnote numbers and pages, e.g. "datasheet.pdf: 1 p.7 · 3 p.54". */
  function printSources(sources) {
    if (!sources || !sources.length) return "";
    const byDoc = new Map();
    sources.forEach((s) => {
      const k = s.title || "Unknown source";
      if (!byDoc.has(k)) byDoc.set(k, []);
      byDoc.get(k).push(`<b>${esc(s.id)}</b>${s.page ? " p." + esc(s.page) : ""}`);
    });
    return `<div class="p-sources"><h5>Sources (footnote number → document, page)</h5><ul>${[...byDoc].map(([doc, refs]) =>
      `<li><span class="p-doc">${esc(doc)}</span>: ${refs.join(" · ")}</li>`).join("")}</ul></div>`;
  }

  function printSheetHtml(r, cols, rows) {
    const flagText = (f) => `<li><b>${esc(FLAG_LABEL[f.level] || "Check")}:</b> ${esc(clip(f.text, PRINT.textChars))}</li>`;
    const flags = r.flags || [];
    const com = r.commercial || [];
    const issues = issueSummary(r).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
    const page2 = printBusinessPage(r);
    // page 1 keeps its own source key; customer-website and sales-guideline sources move to page 2 unless
    // something on page 1 cites them too
    const page1Ids = new Set(["differences", "benefits", "tradeoffs"].flatMap((k) => (r.insights?.[k] || []).flatMap((it) => it.sources || [])));
    const page1Sources = (r.sources || []).filter((s) => !page2 || !["profile", "commercial"].includes(s.kind) || page1Ids.has(s.id));
    return `<div class="print-sheet" aria-hidden="true"><section class="p-page p-page1">
      <header class="p-head">
        <div><div class="p-brand">Danfoss · Competitor Intelligence</div><h3>${esc(r.title)}</h3>
          <div class="p-sub">Prepared for ${esc(state.buyer.company || "—")}${state.buyer.application ? " · " + esc(state.buyer.application) : ""} · ${esc(new Date().toLocaleDateString())}</div>
          <div class="p-level">Product-level comparison (datasheet range across all builds) – the customer's build may differ; configure with application engineering before quoting.</div></div>
        <div class="p-meta"><b>Confidence: ${esc(r.confidence?.level || "n/a")}</b><br>${esc(issues)}
          ${r.grounding?.statements ? `<br>${esc(r.grounding.statements)} AI statements · ${esc(r.grounding.citations)} citations · numbers checked by code` : ""}</div>
      </header>
      ${r.isSample ? '<p class="p-warn">Prototype: competitor values are sample data.</p>' : ""}
      ${rows.length ? `<table class="p-table"><thead><tr><th>Attribute</th>${cols.map((c) =>
        `<th>${esc(c.name)}<span>${esc(c.manufacturer)}</span></th>`).join("")}</tr></thead>
        <tbody>${rows.map((a) => `<tr${state.factors[a.factor] ? ` class="p-${state.factors[a.factor]}"` : ""}><th>${esc(a.label)}${a.unit ? ` <span>(${esc(a.unit)})</span>` : ""}</th>${cols.map((c) => printCell(a.values[c.key])).join("")}</tr>`).join("")}</tbody></table>`
        : '<p class="p-sub">No attribute could be compared directly between these products.</p>'}
      <p class="p-cut p-more"></p>
      <div class="p-cols">
        ${printList("Key differences", r.insights?.differences, PRINT.differences)}
        ${printList(`Benefits for ${state.buyer.company || "the customer"}`, r.insights?.benefits, PRINT.benefits)}
        ${printList("Switching trade-offs", r.insights?.tradeoffs, PRINT.tradeoffs)}
        ${flags.length ? `<div class="p-block"><h5>Open points – check before quoting</h5><ul>${flags.slice(0, PRINT.openPoints).map(flagText).join("")}
          ${flags.length > PRINT.openPoints ? `<li class="p-more">+${flags.length - PRINT.openPoints} more in the app</li>` : ""}</ul></div>` : ""}
        ${com.length && !page2 ? printList("Commercial (approved, internal)", com.map((n) => ({ text: `${KIND_LABEL[n.kind] || n.kind}: ${n.text}`, sources: n.sources })), PRINT.commercial) : ""}
      </div>
      ${printSources(page1Sources)}
      <footer class="p-foot">Superscript numbers = sources below · +N = more stated values in the app · <b>build</b> = value holds for some configurations only · "Not stated" = not in the datasheets, never guessed. Full values, quotes and all other facts: open this comparison in Dani.${page2 ? " Business brief for the customer: page 2." : ""}</footer>
    </section>${page2 ? page2.html : ""}</div>`;
  }

  /** Page 2: the business brief - why it matters to this customer, what their own website says (what they use
   *  today, news and expansion, stated priorities) and the approved sales-guideline notes. Not shortened:
   *  these sentences are the point of the page. Same footnote numbers as in the app. */
  function printBusinessPage(r) {
    const business = r.insights?.business || [];
    const prof = r.customerProfile;
    const notes = r.commercial || [];
    const briefs = priorityBriefs(r);
    if (!business.length && !prof && !notes.length && !briefs.length) return null;
    const who = state.buyer.company || prof?.company || "the customer";
    const ids = new Set();
    const sup = (list) => { (list || []).forEach((n) => ids.add(n)); return (list || []).length ? `<sup>${list.join(",")}</sup>` : ""; };
    const li = (text, sources) => `<li>${esc(text)}${sup(sources)}</li>`;
    const group = (title, items, max) => items.length ? `<div class="p-block"><h5>${esc(title)}</h5><ul>${items.slice(0, max).join("")}
      ${items.length > max ? `<li class="p-more">+${items.length - max} more in the app</li>` : ""}</ul></div>` : "";
    const why = BIZ_KIND.map(([kind, label]) => group(label, business.filter((b) => (b.kind || "fit") === kind).map((b) => li(b.text, b.sources)), 6)).join("");
    const pick = (kinds) => (prof?.items || []).filter((it) => kinds.includes(it.kind.split(":")[0]))
      .map((it) => {
        const [kind, sub] = it.kind.split(": ");
        const tag = kind === "news" ? `${NEWS_LABEL[sub] || "News"}: ` : kind === "priority" ? `${(sub || "").replace(/_/g, " ")}: ` : "";
        return li(tag + it.text + (it.date ? ` (${it.date})` : ""), it.sources);
      });
    const site = prof ? `${group("What they use today (named on their site)", pick(["supplier mention", "installed"]), 6)}
      ${group("News & expansion — opportunities to follow", pick(["news"]), 6)}
      ${group("Their stated priorities", pick(["priority"]), 5)}
      ${group("Hydraulics & controls", pick(["hydraulics"]), 4)}` : "";
    const com = group("Approved sales-guideline notes (internal)", notes.map((n) => li(`${KIND_LABEL[n.kind] || n.kind}: ${n.text}`, n.sources)), 5);
    const prios = briefs.length ? `<h4 class="p-h4">Specified requirements</h4>${briefs.map((b) => `<div class="p-block p-prio p-${b.level}">
        <h5>${esc(b.label)} <span class="p-lvl">${b.level === "must" ? "must-have" : "nice-to-have"}</span></h5>
        ${b.ai ? `<p>${esc(b.ai.text)}${sup(b.ai.sources)}</p>` : ""}
        ${b.rows.map((row) => `<div class="p-vals"><b>${esc(row.label)}${row.unit ? " (" + esc(row.unit) + ")" : ""}:</b> ${row.values.map((v) => `${esc(v.name)} ${esc(v.value)}${sup(v.sources)}`).join(" · ")}</div>`).join("")}
        ${!b.ai && !b.rows.length ? '<p class="p-muted">No datasheet value in this comparison covers this priority.</p>' : ""}
        ${b.profile.map((it) => `<div class="p-vals">Their website: ${esc(it.text)}${sup(it.sources)}</div>`).join("")}
        ${b.notes.map((n) => `<div class="p-vals">${esc(KIND_LABEL[n.kind] || n.kind)} (approved): ${esc(n.text)}${sup(n.sources)}</div>`).join("")}
      </div>`).join("")}` : "";
    const html = `<section class="p-page p-page2">
      <header class="p-head">
        <div><div class="p-brand">Danfoss · Competitor Intelligence · Business brief</div><h3>${esc(who)} — ${esc(r.title)}</h3>
          <div class="p-sub">${prof ? `From ${esc(shortUrl(prof.website))}${prof.researchedAt ? ", website last read " + esc(prof.researchedAt.slice(0, 10)) : ""} · ` : ""}${esc(new Date().toLocaleDateString())}</div></div>
        <div class="p-meta">Every statement cites its source: the customer's own web page, a datasheet page or an approved sales guideline.</div>
      </header>
      <div class="p-cols p-cols-2">
        <div>${prios}${why ? `<h4 class="p-h4">Why it matters to ${esc(who)}</h4>${why}` : '<p class="p-sub">No customer-specific points (no customer profile or notes).</p>'}</div>
        <div>${site}${com}</div>
      </div>`;
    const sourcesHtml = printSources((r.sources || []).filter((s) => ids.has(s.id)));
    return { ids: [...ids], html: html + sourcesHtml + `<footer class="p-foot">Superscript numbers = sources below (the same numbers as in the app and on page 1).</footer></section>` };
  }

  /** Keeps the summary on one landscape A4 page: first shrink a little (never below 80 %, so it stays
   *  readable), then leave out the lowest-priority table rows from the bottom and say how many. */
  function fitPrintSheet() {
    const PAGE_W = 1060, PAGE_H = 725;                       // A4 landscape minus 8 mm margins, in CSS px
    const MIN_ZOOM = 0.8;
    document.querySelectorAll(".print-sheet").forEach((sheet) => {
      sheet.classList.add("is-measuring");
      sheet.querySelectorAll(".p-page").forEach((page) => {   // each printed page is fitted on its own
        const rows = page.classList.contains("p-page1") ? [...page.querySelectorAll(".p-table tbody tr")] : [];
        const note = page.querySelector(".p-cut");
        rows.forEach((tr) => (tr.hidden = false));
        if (note) note.textContent = "";
        page.style.zoom = "";
        // a zoomed page is laid out wider (PAGE_W / zoom), so measure at that width
        const scaledHeight = (z) => { page.style.width = PAGE_W / z + "px"; return page.scrollHeight * z; };
        let z = 1;
        const min = page.classList.contains("p-page2") ? 0.72 : MIN_ZOOM;   // page 2 is text: shrink, never cut
        while (z > min && scaledHeight(z) > PAGE_H) z = Math.max(min, z - 0.02);
        let cut = 0;
        for (let i = rows.length - 1; i > 0 && scaledHeight(z) > PAGE_H; i--) {
          if (rows[i].classList.contains("p-must") || rows[i].classList.contains("p-nice")) break;  // priorities stay
          rows[i].hidden = true; cut++;
          note.textContent = `+${cut} lower-priority attribute${cut > 1 ? "s" : ""} not printed – see the app.`;
        }
        page.style.width = "";
        page.style.zoom = z < 1 ? z.toFixed(2) : "";
      });
      sheet.classList.remove("is-measuring");
    });
  }

  function resultWidget(r) {
    const cols = [r.anchor, ...r.matches];
    sourceById = Object.fromEntries((r.sources || []).map((s) => [s.id, s]));
    const a = SalesView.analyse(r, state.factors, state.buyer,
      Object.fromEntries((catalog?.factors || []).map((f) => [f.id, f.label])));

    // ---- full table (kept for the people who want every value) ----
    const sorted = [...r.attributes].sort((x, y) => priorityRank(x.factor) - priorityRank(y.factor));
    const rows = sorted.filter((x) => x.core !== false);
    const more = sorted.filter((x) => x.core === false);
    const rowHtml = (x) => {
      const pr = state.factors[x.factor];
      return `<tr class="${pr ? "is-" + pr : ""}${x.paired ? " is-paired" : ""}"><th scope="row">${pr ? `<span class="prio prio-${pr}" title="${pr === "must" ? "Must-have" : "Nice-to-have"}"></span>` : ""}${esc(x.label)}${x.unit ? ` <span class="unit">(${esc(x.unit)})</span>` : ""}${x.paired ? `<div class="cell-note" title="${esc(x.note || "")}">paired by AI</div>` : ""}</th>${cols.map((c) => cell(x.values[c.key])).join("")}</tr>`;
    };
    const head = `<thead><tr><th>Attribute</th>${cols.map((c) =>
          `<th><div class="col-name">${esc(c.name)}</div><div class="col-sub">${esc(c.manufacturer)}${c.knownEquivalent ? " · confirmed equivalent" : c.matchScore ? ` · match ${Math.round(c.matchScore * 100)}%` : ""}</div></th>`).join("")}</tr></thead>`;

    const buyer = state.buyer.company || "—";
    const today = new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
    const card = el("div", { class: "result" });
    card.innerHTML = `
      <div class="result-head">
        <div>
          <h3>${esc(r.title)}</h3>
          <div class="hint">Prepared for ${esc(buyer)}${state.buyer.application ? " · " + esc(state.buyer.application) : ""}</div>
          <div class="hint level-line">${esc(LEVEL_TEXT)}</div>
          ${r.grounding?.statements ? `<div class="hint trust-line">AI text checked by code: ${esc(r.grounding.statements)} statements · ${esc(r.grounding.citations)} citations${r.grounding.problems ? ` · ${esc(r.grounding.problems)} failed, see Check before you promise` : " · every number verified"}</div>` : ""}
        </div>
        <div class="head-right"><span class="confidence conf-${esc(r.confidence?.level)}" title="${esc(r.confidence?.reason)}">Data confidence: ${esc(r.confidence?.level || "n/a")}</span></div>
      </div>
      ${r.isSample ? '<div class="flag flag-sample">Prototype view: competitor values are sample data until the competitor dataset is loaded.</div>' : ""}
      ${scoreboardHtml(a, r)}
      ${pitchHtml(r, a)}
      ${commercialHtml(r)}
      ${!r.sources.length ? "" : `<details class="sources-strip" aria-label="Sources"><summary><span class="src-title">Sources (${r.sources.length})</span> <span class="hint">· every [n] above links to its datasheet page</span></summary><ol>${r.sources.map((s) =>
        `<li id="src-${s.id}" value="${s.id}"><span class="src-print">${esc(SalesView.shorten(s.title, 24))}${s.page ? ", p. " + esc(s.page) : ""}</span><span class="src-full">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)}${s.page ? `, p. ${esc(s.page)}` : ""}${s.date ? ` <span class="hint">(${esc(s.date)})</span>` : ""}${s.url || s.kind === "customer" ? "" : ' <span class="hint">· no online link yet</span>'}${s.doc ? `<span class="src-doc"> · ${esc(s.doc)}</span>` : ""}</span></li>`).join("")}</ol></details>`}

      <details class="proof">
        <summary><span class="proof-title">Details &amp; proof</span><span class="hint">full table, data checks, AI cost</span><span class="issue-summary">${issueSummary(r)}</span></summary>
        <div class="proof-body">
          <h4>Full comparison table</h4>
          ${rows.length ? `<div class="table-wrap"><table class="cmp">${head}<tbody>${rows.map(rowHtml).join("")}</tbody></table></div>`
            : '<p class="hint">No attribute could be compared directly between these products.</p>'}
          ${more.length ? `<details class="more-facts"><summary>All other facts (${more.length})</summary>
            <input type="search" class="more-filter" placeholder="Filter, e.g. pressure, PVE, weight">
            <div class="table-wrap"><table class="cmp">${head}<tbody></tbody></table></div>
            <p class="hint more-count"></p></details>` : ""}
          ${r.flags?.length ? `<section class="insight"><h4>Data checks</h4>${r.flags.map(resultFlagHtml).join("")}</section>` : ""}
          <div class="proof-meta">${groundingText(r.grounding)}${aiCostText(r.aiCost)}</div>
        </div>
      </details>

      <div class="actions">
        <button class="btn btn-ghost" type="button" data-act="print" title="A one-page landscape summary for a meeting or to send on">Print / save as PDF</button>
        <button class="btn btn-ghost" type="button" data-act="edit">Adjust priorities</button>
      </div>
      ${printSheetHtml(r, cols, rows)}`;

    // one-page print: only this result, without the proof section (see @media print)
    card.querySelector('[data-act="print"]').addEventListener("click", () => window.print());
    card.querySelector('[data-act="open-details"]')?.addEventListener("click", () => {
      const d = card.querySelector("details.proof");
      d.open = true;
      d.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    card.querySelector('[data-act="suggest-note"]')?.addEventListener("click", () => {
      const box = card.querySelector(".suggest-note");
      box.hidden = !box.hidden;
    });
    card.querySelector('[data-act="send-note"]')?.addEventListener("click", async (ev) => {
      const box = card.querySelector(".suggest-note");
      const payload = { productId: r.anchor?.key || "", kind: box.querySelector("[name=nKind]").value,
                        text: box.querySelector("[name=nText]").value, source: box.querySelector("[name=nSource]").value };
      ev.target.disabled = true;
      try {
        const res = await Api.addCommercial(payload);
        box.innerHTML = (res.issues || []).map(flagHtml).join("");
      } catch (err) { box.innerHTML = flagHtml({ level: "error", text: err.message }); ev.target.disabled = false; }
    });
    card.querySelectorAll(".pending-tools button").forEach((b) => b.addEventListener("click", async () => {
      const id = b.closest(".pending-tools").dataset.id;
      b.disabled = true;
      try {
        await Api.decideCommercial(id, b.dataset.decide);
        b.closest(".com-note").style.opacity = "0.4";
        b.closest(".pending-tools").textContent = b.dataset.decide === "approve" ? "approved - rerun the comparison to use it" : "rejected";
      } catch (err) { b.disabled = false; alert(err.message); }
    }));
    // "All other facts" can be thousands of rows: drawn only when opened, filterable, first 150 matches
    const moreBox = card.querySelector(".more-facts");
    if (moreBox) {
      const body = moreBox.querySelector("tbody"), filter = moreBox.querySelector(".more-filter");
      const count = moreBox.querySelector(".more-count");
      const draw = () => {
        const words = filter.value.toLowerCase().split(/\s+/).filter(Boolean);
        const hits = more.filter((x) => words.every((w) => (x.label + " " + x.id).toLowerCase().includes(w)));
        body.innerHTML = hits.slice(0, 150).map(rowHtml).join("");
        count.textContent = hits.length > 150 ? `Showing 150 of ${hits.length} - type to narrow it down.` : "";
      };
      moreBox.addEventListener("toggle", () => moreBox.open && !body.children.length && draw());
      filter.addEventListener("input", draw);
    }
    // a footnote without an online link jumps to the visible source list and highlights the entry
    card.querySelectorAll('a.ref[href^="#src-"]').forEach((link) => link.addEventListener("click", (e) => {
      e.preventDefault();
      const strip = card.querySelector("details.sources-strip");
      if (strip) strip.open = true;
      const li = card.querySelector(link.getAttribute("href"));
      if (li) { li.scrollIntoView({ behavior: "smooth", block: "center" }); li.classList.add("is-hit"); setTimeout(() => li.classList.remove("is-hit"), 1800); }
    }));
    if (state.mode !== "compare") card.querySelector('[data-act="edit"]').remove();
    else card.querySelector('[data-act="edit"]').addEventListener("click", () => {
      const idx = segments().findIndex((s) => s.key === "factors");
      editSegment(idx);
    });
    botMsg(card).classList.add("bubble-wide");
  }

  // ---------- sidebar + stepper ----------
  function renderBrief() {
    if (state.mode === "system") {
      briefEl.innerHTML = `<h2>System comparison</h2>
        <div class="brief-item"><div class="brief-label">${state.system.start === "danfoss" ? "Danfoss system" : "Customer uses"}</div><div class="brief-value">${esc(state.system.label)}</div></div>
        <div class="brief-item"><div class="brief-label">Buyer</div><div class="brief-value ${buyerSummary() ? "" : "is-empty"}">${esc(buyerSummary() || "Not set")}</div></div>
        ${customerFilesFor(state.buyer.company) ? `<div class="brief-item"><div class="brief-label">Customer feedback</div><div class="brief-value">${esc(customerFilesFor(state.buyer.company))}</div></div>` : ""}
        <p class="hint brief-foot">System values (e.g. system flow) are calculated by code from the component facts, never by the AI.</p>`;
      return;
    }
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
      <p class="hint brief-foot">Every fact in the result links to its source. Missing or conflicting data is flagged, never guessed.</p>
      <p class="hint brief-spend" id="ai-spend"></p>`;
    showSpend();
    briefEl.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => editSegment(+b.dataset.edit)));
  }

  async function showSpend() {
    try {
      const u = await Api.usage();
      const box = $("#ai-spend");
      if (box && u?.total?.calls) box.textContent = `AI spend so far: $${u.total.usd.toFixed(2)} (${u.total.calls} calls)`;
    } catch { /* the spend line is optional */ }
  }

  function renderStepper() {
    ["compare", "match", "system"].forEach((m) => $("#btn-" + m).setAttribute("aria-pressed", state.mode === m));
    stepperEl.hidden = state.mode !== "compare";
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
  // ---------- Browse data: every extracted fact of one product (outside the comparison) ----------
  async function openFactsDialog(productId) {
    const dlg = $("#facts-dialog");
    dlg.innerHTML = `<div class="dialog-body"><div class="dialog-head"><h3>Browse extracted facts</h3>
      <button class="link-btn" type="button" data-act="close">Close</button></div><p class="hint">Loading products…</p></div>`;
    dlg.querySelector('[data-act="close"]').addEventListener("click", () => dlg.close());
    if (!dlg.open) dlg.showModal();
    let products = [];
    try { products = (await Api.products()).products || []; } catch (err) { dlg.querySelector(".hint").textContent = err.message; return; }
    const pick = productId || (selectedProduct() && products.find((p) => p.id === selectedProduct().id)?.id)
      || products.find((p) => !p.demo && p.side === "danfoss")?.id || products[0]?.id;
    const opt = (p) => `<option value="${esc(p.id)}" ${p.id === pick ? "selected" : ""}>${esc(p.brand)} ${esc(p.name)} · ${p.facts} facts${p.demo ? " (demo)" : ""}</option>`;
    const body = dlg.querySelector(".dialog-body");
    body.innerHTML = `<div class="dialog-head"><h3>Browse extracted facts</h3><button class="link-btn" type="button" data-act="close">Close</button></div>
      <p class="hint">Everything extracted from the datasheets, with the exact quote and the page it came from. The comparison only shows the rows that can be compared.</p>
      <div class="facts-tools">
        <select name="product">${["danfoss", "competitor"].map((side) => `<optgroup label="${side === "danfoss" ? "Danfoss" : "Competitors"}">${products.filter((p) => p.side === side).map(opt).join("")}</optgroup>`).join("")}</select>
        <input name="q" type="search" placeholder="Filter, e.g. pressure, ISO, CAN">
        <select name="status"><option value="">All statuses</option><option value="ok">Quote verified</option><option value="unverified">Unverified</option><option value="conflict">Conflict</option></select>
        <button class="btn btn-ghost btn-sm" type="button" data-act="csv">Download CSV</button>
      </div>
      <div class="facts-count hint"></div>
      <div class="facts-wrap"><table class="facts-table"><thead><tr><th>Attribute</th><th>Value</th><th>Condition</th><th>Status</th><th>Source</th></tr></thead><tbody></tbody></table></div>`;
    body.querySelector('[data-act="close"]').addEventListener("click", () => dlg.close());
    let facts = [], product = null;
    const tbody = body.querySelector("tbody"), count = body.querySelector(".facts-count");
    const shown = () => {
      const q = body.querySelector("[name=q]").value.trim().toLowerCase();
      const st = body.querySelector("[name=status]").value;
      return facts.filter((f) => (!st || String(f.status).startsWith(st)) &&
        (!q || [f.label, f.datasheetName, f.value, f.unit, f.condition, f.quote, f.note].join(" ").toLowerCase().includes(q)));
    };
    const draw = () => {
      const list = shown();
      count.textContent = `${list.length} of ${facts.length} facts${product ? " · " + product.brand + " " + product.name : ""}`;
      tbody.innerHTML = list.map((f) => `<tr>
        <th scope="row">${esc(f.label)}${f.datasheetName && f.datasheetName !== f.attribute ? `<div class="hint">datasheet: ${esc(f.datasheetName)}</div>` : ""}</th>
        <td>${esc(String(f.value))}${f.unit ? " " + esc(f.unit) : ""}${f.normalised && f.unit && !f.normalised.endsWith(" " + f.unit) ? `<div class="hint">= ${esc(f.normalised)}</div>` : ""}
          ${f.quote ? `<div class="facts-quote">“${esc(f.quote)}”</div>` : ""}</td>
        <td>${esc(f.condition || "")}${f.note ? `<div class="hint">${esc(f.note)}</div>` : ""}</td>
        <td><span class="badge ${f.status === "ok" ? "badge-brand" : "badge-muted"}">${esc(f.status || "–")}</span></td>
        <td>${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.source)}${f.page ? ", p. " + esc(f.page) : ""}</a>` : esc(f.source) + (f.page ? ", p. " + esc(f.page) : "")}</td></tr>`).join("");
    };
    const load = async (id) => {
      count.textContent = "Loading…"; tbody.innerHTML = "";
      try { ({ product, facts } = await Api.facts(id)); draw(); } catch (err) { count.textContent = err.message; }
    };
    body.querySelector("[name=product]").addEventListener("change", (e) => load(e.target.value));
    body.querySelector("[name=q]").addEventListener("input", draw);
    body.querySelector("[name=status]").addEventListener("change", draw);
    body.querySelector('[data-act="csv"]').addEventListener("click", () => {
      const cols = ["label", "value", "unit", "normalised", "condition", "status", "quote", "source", "page", "url"];
      const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
      const csv = [cols.join(","), ...shown().map((f) => cols.map((c) => cell(f[c])).join(","))].join("\n");
      const a = el("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: `${product?.id || "facts"}.csv` });
      a.click(); URL.revokeObjectURL(a.href);
    });
    if (pick) load(pick); else count.textContent = "No extracted facts yet.";
  }

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

  // ---------- system comparison: e.g. pump + valve against the other side's pump + valve ----------
  async function openSystemDialog() {
    const dlg = $("#system-dialog");
    dlg.innerHTML = `<div class="dialog-body"><p>Loading products…</p></div>`;
    dlg.showModal();
    let products = [];
    try { products = (await Api.products()).products || []; }
    catch (err) { dlg.innerHTML = `<div class="dialog-body">${flagHtml({ level: "error", text: err.message })}</div>`; return; }
    const families = [...new Set(products.map((p) => p.family))].filter(Boolean);
    const label = (p) => `${p.brand} ${p.name}${p.demo ? " (DEMO data)" : ""}`;
    const options = (fam, side, extra) => extra + products.filter((p) => p.family === fam && p.side === side)
      .map((p) => `<option value="${esc(p.id)}">${esc(label(p))}</option>`).join("");
    const draw = (start) => {
      const other = start === "danfoss" ? "competitor" : "danfoss";
      dlg.innerHTML = `
      <form method="dialog" class="dialog-body">
        <div class="dialog-head"><h3>Compare system</h3><button class="link-btn" value="cancel" type="submit">Close</button></div>
        <p class="hint">A system is a set of components that work together. Code also calculates how well they fit
          (e.g. system flow = the smaller of pump flow and valve inlet flow) – every value still links to its datasheet page.</p>
        <fieldset class="field seg"><legend>Start from</legend>
          <label><input type="radio" name="start" value="danfoss" ${start === "danfoss" ? "checked" : ""}> A Danfoss system</label>
          <label><input type="radio" name="start" value="competitor" ${start === "competitor" ? "checked" : ""}> The competitor system the customer uses</label>
        </fieldset>
        <div class="form-grid tight">${families.map((fam) => `
          <label class="field"><span>${esc(fam[0].toUpperCase() + fam.slice(1))} · ${start === "danfoss" ? "Danfoss" : "competitor"}</span>
            <select name="c-${esc(fam)}">${options(fam, start, '<option value="">– not part of the system –</option>')}</select></label>
          <label class="field"><span>compare with (${other === "danfoss" ? "Danfoss" : "competitor"})</span>
            <select name="a-${esc(fam)}">${options(fam, other, '<option value="">Find best match automatically</option>')}</select></label>`).join("")}
        </div>
        <div class="form-grid tight">
          <label class="field"><span>Customer <span class="opt">(optional)</span></span><input name="company" value="${esc(state.buyer.company || "")}" placeholder="e.g. AGCO"></label>
          <label class="field"><span>Application <span class="opt">(optional)</span></span><input name="application" value="${esc(state.buyer.application || "")}" placeholder="e.g. Wheel loader"></label>
        </div>
        <div class="form-issues"></div>
        <div class="actions"><button class="btn btn-primary" value="go" type="submit">Compare system</button></div>
      </form>`;
      const f = dlg.querySelector("form");
      f.querySelectorAll('input[name="start"]').forEach((r) => r.addEventListener("change", () => draw(r.value)));
      f.addEventListener("submit", (e) => {
        if (e.submitter?.value !== "go") return;
        const components = {}, against = {};
        families.forEach((fam) => { components[fam] = f["c-" + fam].value || null; against[fam] = f["a-" + fam].value || null; });
        const chosen = Object.entries(components).filter(([, v]) => v);
        if (chosen.length < 2) {
          e.preventDefault();
          f.querySelector(".form-issues").innerHTML = flagHtml({ level: "error", text: "Choose at least two components (e.g. a pump and a valve)." });
          return;
        }
        const name = (id) => label(products.find((p) => p.id === id) || { brand: "", name: id });
        state = freshState();
        state.mode = "system";
        state.buyer.company = f.company.value.trim();
        state.buyer.application = f.application.value.trim();
        state.system = {
          label: chosen.map(([fam, id]) => `${fam}: ${name(id)}`).join(" + "),
          start: f.start.value,
          payload: { start: f.start.value, components, against },
        };
        runSystem();
      });
    };
    if (!families.length) {
      dlg.innerHTML = `<div class="dialog-body"><p class="flag flag-missing">No products with extracted facts yet.</p></div>`;
      return;
    }
    draw("danfoss");
  }

  async function runSystem() {
    state.loading = true; state.error = null; state.result = null; render();
    try {
      state.result = await Api.compareSystem({ ...state.system.payload, buyer: { ...state.buyer }, factors: {}, chatNotes: [] });
    } catch (err) {
      state.error = err.message;
    } finally {
      state.loading = false; render();
    }
  }

  function renderSystem() {
    doneRow(state.system.start === "danfoss" ? "Danfoss system" : "Customer's system", state.system.label, openSystemDialog);
    if (buyerSummary()) doneRow("Buyer", buyerSummary(), openSystemDialog);
    if (state.loading) loadingWidget();
    else if (state.error) botMsg(`<div class="flag flag-conflict">Couldn't compare the systems: ${esc(state.error)}</div>`);
    else if (state.result) resultWidget(state.result);
  }

  function renderMatch() {
    doneRow("Customer uses", state.matchProduct.label, openMatchDialog);
    if (buyerSummary()) doneRow("Buyer", buyerSummary(), openMatchDialog);
    if (state.loading) loadingWidget();
    else if (state.error) botMsg(`<div class="flag flag-conflict">Couldn't find a match: ${esc(state.error)}</div>`);
    else if (state.result) resultWidget(state.result);
  }

  function render() {
    chat.innerHTML = "";
    if (state.mode === "match" || state.mode === "system") {
      botMsg(state.mode === "match" ? "Let's find the closest Danfoss products for the competitor product your customer uses."
                                    : "Let's compare a whole system – component by component and how well the parts fit together.");
      state.mode === "match" ? renderMatch() : renderSystem();
      renderBrief();
      renderStepper();
      requestAnimationFrame(() => (chat.scrollTop = chat.scrollHeight));
      return;
    }
    botMsg("Hi! I'm <strong>Dani</strong>. I'll help you compare a Danfoss product with its competitors - every value cited to its datasheet page. Pick the options below, or ask me a question at any time.");
    renderLog(0);
    segments().forEach((seg, i) => {
      doneRow(SHORT_LABEL[seg.key] || "Option", seg.answer, () => editSegment(i));
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
  $("#btn-compare").addEventListener("click", () => { if (state.mode !== "compare") { state = freshState(); render(); } });

  async function reloadCatalog() { catalog = await Api.getCatalog(); }
  $("#btn-upload").addEventListener("click", () => catalog && openUploadDialog());
  $("#btn-facts").addEventListener("click", () => catalog && openFactsDialog());
  $("#btn-match").addEventListener("click", () => catalog && openMatchDialog());
  $("#btn-system").addEventListener("click", () => catalog && openSystemDialog());

  window.addEventListener("beforeprint", fitPrintSheet);

  // ---------- demo login (viewer suggests, editor approves) ----------
  let currentUser = { id: localStorage.getItem("dani-user") || "senior", role: "editor", users: [] };

  async function initUser() {
    const sel = $("#user-select");
    try { currentUser = { ...currentUser, ...(await Api.user()) }; } catch { /* mock mode */ }
    const users = currentUser.users?.length ? currentUser.users
      : [{ id: "senior", name: "Senior (demo)", role: "editor" }, { id: "junior", name: "Junior (demo)", role: "viewer" }];
    sel.innerHTML = users.map((u) => `<option value="${esc(u.id)}" ${u.id === currentUser.id ? "selected" : ""}>${esc(u.name)} · ${u.role}</option>`).join("");
    sel.addEventListener("change", async () => {
      localStorage.setItem("dani-user", sel.value);
      currentUser = { ...currentUser, ...(await Api.user().catch(() => ({}))) };
      render();
    });
  }
  initUser();

  // ---------- boot ----------
  Api.getCatalog()
    .then((c) => { catalog = c; render(); })
    .catch((err) => { chat.innerHTML = `<div class="flag flag-conflict">Couldn't load the catalog: ${esc(err.message)}</div>`; });
})();
