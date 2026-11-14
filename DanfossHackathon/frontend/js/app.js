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
  let me = null;   // { user: {id, name, title, role}, users: [...], pendingReviews, myOpenRequests }
  /** Editors change the catalog directly; viewers send requests to the review queue. */
  const isEditor = () => me?.user?.role === "editor";
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
    }, isEditor()
      ? `<span class="option-title">+ Add ${levelName}</span><span class="option-desc">Create it and upload its datasets</span>`
      : `<span class="option-title">+ Suggest ${levelName}</span><span class="option-desc">Sent to an editor for review</span>`));
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
      ${isEditor() ? "" : `<label class="field span-2"><span>Note for the reviewer <span class="opt">(optional: where it's from, why it's needed)</span></span>
        <input name="note" placeholder="e.g. Found on PowerSource, needed for the AGCO meeting"></label>`}
      <div class="form-issues span-2"></div>
      <div class="actions span-2">
        <button class="btn btn-ghost" type="button" data-act="cancel">Cancel</button>
        <button class="btn btn-primary" type="submit">${isEditor() ? `Add ${esc(levelName)}` : "Send for review"}</button>
      </div>`;
    form.querySelector('[data-act="cancel"]').addEventListener("click", onCancel);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const btn = form.querySelector('[type="submit"]');
      btn.disabled = true;
      const issueBox = form.querySelector(".form-issues");
      const nodeMeta = { parentId, label: fd.get("label"), desc: fd.get("desc"), type: depth === 0 ? "group" : fd.get("type") };
      try {
        if (!isEditor()) {
          const res = await Api.suggest({ kind: "node", ...nodeMeta, note: fd.get("note") || "" },
                                        form.querySelector('input[type="file"]').files);
          if (!res.suggestion) { issueBox.innerHTML = res.issues.map(flagHtml).join(""); return; }
          await reloadMe();
          state.log.push({ stage: segments().length, role: "issues", title: `Request sent: ${res.suggestion.summary}`, issues: res.issues });
          render();
          return;
        }
        const res = await Api.createNode(nodeMeta);
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
        <div class="dialog-head"><h3>${isEditor() ? "Upload data" : "Request upload"}</h3><button class="link-btn" value="cancel" type="submit">Close</button></div>
        ${isEditor() ? "" : `<p class="hint">An editor reviews your request before the data is used in comparisons.</p>`}
        <fieldset class="field seg">
          <legend>Whose data is it?</legend>
          <label><input type="radio" name="source" value="danfoss" ${source0 === "danfoss" ? "checked" : ""}> Danfoss</label>
          <label><input type="radio" name="source" value="competitor" ${source0 === "competitor" ? "checked" : ""}> Competitor</label>
        </fieldset>

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
        ${isEditor() ? "" : `<label class="field">Note for the reviewer <input name="note" placeholder="Optional: where it's from, why it's needed"></label>`}
        <div class="form-issues"></div>
        <div class="actions">
          <button class="btn btn-ghost" value="cancel" type="submit">Cancel</button>
          <button class="btn btn-primary" type="button" data-act="upload">${isEditor() ? "Upload" : "Send for review"}</button>
        </div>
      </form>`;
    const f = dlg.querySelector("form");
    const fileInput = f.querySelector('input[type="file"]');
    const list = f.querySelector(".dz-list");
    const syncSource = () => {
      const comp = f.source.value === "competitor";
      f.querySelector(".competitor-fields").hidden = !comp;
      f.querySelector(".danfoss-fields").hidden = comp;
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
      const meta = isComp ? {
        source: "competitor",
        competitorId: f.competitorId.value !== "__new" ? f.competitorId.value : null,
        competitorName: f.competitorId.value === "__new" ? f.competitorName.value.trim() : null,
        productName: f.productName.value.trim(),
        categoryId: f.categoryId.value,
        equivalentOf: f.equivalentOf.value || null,
      } : { source: "danfoss", nodeId: f.nodeId.value };
      btn.disabled = true;
      try {
        if (!isEditor()) {
          const res = await Api.suggest({ kind: "dataset", ...meta, note: f.note.value }, fileInput.files);
          issueBox.innerHTML = res.issues.map(flagHtml).join("");
          if (res.suggestion) {
            await reloadMe();
            state.log.push({ stage: segments().length, role: "issues", title: `Request sent: ${res.suggestion.summary}`, issues: res.issues });
            render();
            fileInput.value = ""; list.textContent = "";
          }
          return;
        }
        const res = await Api.uploadDatasets(meta, fileInput.files);
        issueBox.innerHTML = res.issues.map(flagHtml).join("");
        if (res.uploaded?.length) {
          await reloadCatalog();
          const title = isComp ? `Competitor upload: ${meta.productName}` : `Upload to ${findNode(catalog.tree, meta.nodeId)?.label || meta.nodeId}`;
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
    }, isEditor() ? "+ Upload competitor datasheet" : "+ Request competitor datasheet upload"));

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

  function cell(v) {
    if (v && v.value == null && v.status === "pending") {
      return `<td class="cell-pending">Awaiting extraction${v.note ? `<div class="cell-note">${esc(v.note)}</div>` : ""}</td>`;
    }
    if (!v || v.value == null) {
      return `<td class="cell-missing" title="${esc(v?.note || "Not found in sources")}">Not found${v?.note ? `<div class="cell-note">${esc(v.note)}</div>` : ""}</td>`;
    }
    const refs = (v.sources || []).map((s) => `<a href="#src-${s}" class="ref">[${s}]</a>`).join("");
    const st = CELL_STATUS[v.status];
    const tag = st ? `<span class="badge ${st[1]}">${st[0]}</span>` : "";
    const note = v.note ? `<div class="cell-note">${esc(v.note)}</div>` : "";
    return `<td class="cell-${v.status || "ok"}">${esc(v.value)}${refs}${tag}${note}</td>`;
  }

  /** Count every flagged cell by type, e.g. "1 conflict · 6 missing · 1 outdated". */
  function issueSummary(r) {
    const counts = {};
    r.attributes.forEach((a) => Object.values(a.values).forEach((v) => {
      const s = v?.status === "pending" ? "pending" : !v || v.value == null ? "missing" : v.status;
      if (s && s !== "ok") counts[s] = (counts[s] || 0) + 1;
    }));
    (r.flags || []).forEach((f) => { if (!(f.level in counts) && f.level !== "missing") counts[f.level] = 1; });
    const parts = Object.entries(counts).map(([k, n]) =>
      `<span class="issue-chip flag-${esc(k)}">${n} ${esc((FLAG_LABEL[k] || k).toLowerCase())}</span>`);
    return parts.length ? parts.join("") : '<span class="issue-chip flag-ok">No data issues found</span>';
  }

  function insightList(title, items) {
    if (!items || !items.length) return "";
    return `<section class="insight"><h4>${esc(title)}</h4><ul>${items.map((it) =>
      `<li>${esc(it.text)}${(it.sources || []).map((s) => `<a href="#src-${s}" class="ref">[${s}]</a>`).join("")}${it.sample ? ' <span class="badge badge-muted">Sample</span>' : ""}</li>`).join("")}</ul></section>`;
  }

  function resultWidget(r) {
    const cols = [r.anchor, ...r.matches];
    const rows = [...r.attributes].sort((a, b) => priorityRank(a.factor) - priorityRank(b.factor));
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
      <div class="table-wrap"><table class="cmp">
        <thead><tr><th>Attribute</th>${cols.map((c) =>
          `<th><div class="col-name">${esc(c.name)}</div><div class="col-sub">${esc(c.manufacturer)}${c.knownEquivalent ? " · confirmed equivalent" : c.matchScore ? ` · match ${Math.round(c.matchScore * 100)}%` : ""}</div></th>`).join("")}</tr></thead>
        <tbody>${rows.map((a) => {
          const pr = state.factors[a.factor];
          return `<tr class="${pr ? "is-" + pr : ""}"><th scope="row">${pr ? `<span class="prio prio-${pr}" title="${pr === "must" ? "Must-have" : "Nice-to-have"}"></span>` : ""}${esc(a.label)}${a.unit ? ` <span class="unit">(${esc(a.unit)})</span>` : ""}</th>${cols.map((c) => cell(a.values[c.key])).join("")}</tr>`;
        }).join("")}</tbody>
      </table></div>
      <div class="insights">
        ${insightList("Key differences", r.insights?.differences)}
        ${insightList(`Benefits for ${state.buyer.company || "the customer"}`, r.insights?.benefits)}
        ${insightList("Switching trade-offs", r.insights?.tradeoffs)}
      </div>
      ${r.flags?.length ? `<section class="insight"><h4>Open points</h4>${r.flags.map(flagHtml).join("")}</section>` : ""}
      <section class="sources"><h4>Sources</h4><ol>${r.sources.map((s) =>
        `<li id="src-${s.id}">${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>` : esc(s.title)}${s.page ? `, p. ${esc(s.page)}` : ""}${s.date ? ` <span class="hint">(${esc(s.date)})</span>` : ""}${s.doc ? `<div class="hint">${esc(s.doc)}</div>` : ""}</li>`).join("")}</ol></section>
      <div class="actions">
        <button class="btn btn-ghost" type="button" data-act="print">Print / save as PDF</button>
        <button class="btn btn-ghost" type="button" data-act="edit">Adjust priorities</button>
      </div>`;
    card.querySelector('[data-act="print"]').addEventListener("click", () => window.print());
    card.querySelector('[data-act="edit"]').addEventListener("click", () => {
      const idx = segments().findIndex((s) => s.key === "factors");
      editSegment(idx);
    });
    botMsg(card).classList.add("bubble-wide");
  }

  // ---------- sidebar + stepper ----------
  function renderBrief() {
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
  function render() {
    chat.innerHTML = "";
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

  // ---------- account (demo switch) + review queue ----------
  async function reloadMe() { me = await Api.getMe(); renderAccount(); }

  function renderAccount() {
    const box = $("#account");
    if (!me) { box.innerHTML = ""; return; }
    const u = me.user;
    const editor = u.role === "editor";
    const count = editor ? me.pendingReviews : me.myOpenRequests;
    box.innerHTML = `
      <button class="btn btn-ghost btn-queue ${editor && count ? "has-items" : ""}" type="button" id="btn-queue">
        ${editor ? "Review" : "My requests"}${count ? ` <span class="count">${count}</span>` : ""}
      </button>
      <label class="account-switch" title="${esc(u.title)} · Demo account switch: in production this comes from the company login">
        <span class="role-badge role-${esc(u.role)}">${editor ? "Editor" : "View only"}</span>
        <select id="account-select" aria-label="Switch demo account">
          ${me.users.map((x) => `<option value="${esc(x.id)}" ${x.id === u.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}
        </select>
      </label>`;
    $("#btn-upload").textContent = editor ? "Upload" : "Request upload";
    $("#btn-queue").addEventListener("click", openReviewDialog);
    $("#account-select").addEventListener("change", async (e) => {
      try {
        await Api.switchUser(e.target.value);
        await Promise.all([reloadMe(), reloadCatalog()]);
        render();
      } catch (err) {
        alertFlag(err.message);
      }
    });
  }

  function alertFlag(text) {
    state.log.push({ stage: segments().length, role: "issues", title: "Something went wrong", issues: [{ level: "error", text }] });
    render();
  }

  const fmtDate = (iso) => { try { return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); } catch (_) { return iso || ""; } };
  const fmtSize = (n) => (n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

  async function openReviewDialog() {
    const dlg = $("#review-dialog");
    const editor = isEditor();
    dlg.innerHTML = `<div class="dialog-body"><div class="dialog-head"><h3>${editor ? "Review queue" : "My requests"}</h3>
      <button class="link-btn" type="button" data-act="close">Close</button></div><div class="review-list"><p class="hint">Loading…</p></div></div>`;
    dlg.querySelector('[data-act="close"]').addEventListener("click", () => dlg.close());
    if (!dlg.open) dlg.showModal();
    let items;
    try { items = await Api.listSuggestions(); }
    catch (err) { dlg.querySelector(".review-list").innerHTML = flagHtml({ level: "error", text: err.message }); return; }

    const pending = items.filter((s) => s.status === "pending");
    const done = items.filter((s) => s.status !== "pending").reverse();
    const listEl = dlg.querySelector(".review-list");
    if (!items.length) {
      listEl.innerHTML = `<p class="hint">${editor ? "Nothing to review. Requests from viewers appear here." : "You haven't sent any requests yet. Use “Request upload” or a “+ Suggest” card."}</p>`;
      return;
    }
    listEl.innerHTML = "";
    const section = (title, list) => {
      if (!list.length) return;
      listEl.appendChild(el("div", { class: "group-label" }, `${esc(title)} (${list.length})`));
      list.forEach((s) => listEl.appendChild(requestCard(s, editor)));
    };
    section(editor ? "Waiting for review" : "Waiting", pending);
    section("Handled", done);
  }

  function requestCard(s, editor) {
    const card = el("div", { class: `request request-${s.status}` });
    const files = s.files.map((f) => {
      const url = Api.suggestionFileUrl(s.id, f.name);
      return `<li>${url && editor && s.status === "pending" ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(f.name)}</a>` : esc(f.name)} <span class="hint">${fmtSize(f.size)}</span></li>`;
    }).join("");
    const statusTxt = s.status === "pending" ? "Pending review"
      : `${s.status === "approved" ? "Approved" : "Rejected"} by ${esc(s.reviewedBy?.name || "?")} · ${fmtDate(s.reviewedAt)}`;
    card.innerHTML = `
      <div class="request-head"><strong>${esc(s.summary)}</strong><span class="status status-${esc(s.status)}">${statusTxt}</span></div>
      <div class="hint">From ${esc(s.submittedBy.name)} · ${fmtDate(s.submittedAt)}</div>
      ${s.note ? `<p class="request-note">“${esc(s.note)}”</p>` : ""}
      ${files ? `<ul class="request-files">${files}</ul>` : ""}
      ${s.reason ? `<p class="request-note">Reason: ${esc(s.reason)}</p>` : ""}
      <div class="form-issues"></div>`;
    if (editor && s.status === "pending") {
      const actions = el("div", { class: "actions" });
      const reason = el("input", { type: "text", class: "reject-reason", placeholder: "Reason (shown to the requester)", hidden: true });
      const rejectBtn = el("button", { class: "btn btn-ghost", type: "button" }, "Reject");
      const approveBtn = el("button", { class: "btn btn-primary", type: "button" }, "Approve");
      const out = card.querySelector(".form-issues");
      const act = async (fn) => {
        approveBtn.disabled = rejectBtn.disabled = true;
        try {
          const res = await fn();
          out.innerHTML = res.issues.map(flagHtml).join("");
          if (res.suggestion?.status !== "pending") {
            await Promise.all([reloadMe(), reloadCatalog()]);
            state.log.push({ stage: segments().length, role: "issues", title: res.issues[0]?.text || "Request handled", issues: res.issues.slice(1) });
            render();
            setTimeout(openReviewDialog, 900);
          }
        } catch (err) {
          out.innerHTML = flagHtml({ level: "error", text: err.message });
        } finally {
          approveBtn.disabled = rejectBtn.disabled = false;
        }
      };
      rejectBtn.addEventListener("click", () => {
        if (reason.hidden) { reason.hidden = false; reason.focus(); rejectBtn.textContent = "Confirm reject"; return; }
        act(() => Api.rejectSuggestion(s.id, reason.value));
      });
      approveBtn.addEventListener("click", () => act(() => Api.approveSuggestion(s.id)));
      card.appendChild(reason);
      actions.appendChild(rejectBtn);
      actions.appendChild(approveBtn);
      card.appendChild(actions);
    }
    return card;
  }

  // ---------- boot ----------
  Promise.all([Api.getCatalog(), Api.getMe()])
    .then(([c, m]) => { catalog = c; me = m; renderAccount(); render(); })
    .catch((err) => { chat.innerHTML = `<div class="flag flag-conflict">Couldn't load the app: ${esc(err.message)}</div>`; });
})();
