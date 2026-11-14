// ---------------------------------------------------------------
// API layer. The UI only talks to these functions, so swapping
// mock data for the real backend is a one-line change in config.js.
// Request/response shapes are documented in README.md.
//
// The backend owns the catalog: after any create/upload the UI
// re-fetches GET /catalog, so front end and back end never drift.
// ---------------------------------------------------------------
const Api = (() => {
  // Fallback keeps the app working if config.js is missing or cached badly.
  const cfg = window.APP_CONFIG || { API_BASE: "/api", USE_MOCK: false };
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));

  async function call(path, options = {}) {
    const isForm = options.body instanceof FormData;
    const res = await fetch(cfg.API_BASE + path, {
      headers: isForm ? {} : { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) {
      // FastAPI puts the reason in {"detail": "..."}; show that instead of a bare status code.
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch (_) { /* not JSON */ }
      throw new Error(detail || `API ${path} failed with status ${res.status}`);
    }
    return res.json();
  }

  function form(meta, files) {
    const fd = new FormData();
    Object.entries(meta).forEach(([k, v]) => v != null && fd.append(k, v));
    [...(files || [])].forEach((f) => fd.append("files", f));
    return fd;
  }

  return {
    // ---------- account & roles ----------
    /** Current user, the demo accounts, and review counts. */
    async getMe() {
      if (cfg.USE_MOCK) return window.mockMe();
      return call("/me");
    },
    /** Demo account switch (stand-in for company single sign-on). */
    async switchUser(userId) {
      if (cfg.USE_MOCK) return window.mockSwitch(userId);
      return call("/session", { method: "POST", body: JSON.stringify({ userId }) });
    },

    // ---------- review queue ----------
    /** Viewer request: kind "node" (new family/type/product) or "dataset" (files). */
    async suggest(meta, files) {
      if (cfg.USE_MOCK) { await delay(400); return window.mockSuggest(meta, files); }
      return call("/suggestions", { method: "POST", body: form(meta, files) });
    },
    /** Editors get all requests, viewers only their own. */
    async listSuggestions() {
      if (cfg.USE_MOCK) return window.mockListSuggestions();
      return call("/suggestions");
    },
    async approveSuggestion(id) {
      if (cfg.USE_MOCK) { await delay(500); return window.mockApprove(id); }
      return call(`/suggestions/${encodeURIComponent(id)}/approve`, { method: "POST" });
    },
    async rejectSuggestion(id, reason) {
      if (cfg.USE_MOCK) return window.mockReject(id, reason);
      return call(`/suggestions/${encodeURIComponent(id)}/reject`, { method: "POST", body: JSON.stringify({ reason }) });
    },
    /** Link an editor opens to check a requested file before approving (null in mock mode). */
    suggestionFileUrl(id, name) {
      if (cfg.USE_MOCK) return null;
      return `${cfg.API_BASE}/suggestions/${encodeURIComponent(id)}/files/${encodeURIComponent(name)}`;
    },

    /** Product tree, competitors, factors, applications. */
    async getCatalog() {
      if (cfg.USE_MOCK) return JSON.parse(JSON.stringify(window.MOCK_CATALOG));
      return call("/catalog");
    },

    /** Create a product family, product type or product under parentId (null = root). */
    async createNode(body) {
      if (cfg.USE_MOCK) { await delay(300); return window.mockCreateNode(body); }
      return call("/catalog/nodes", { method: "POST", body: JSON.stringify(body) });
    },

    /**
     * Upload dataset files (PDF, CSV, XLSX, JSON …) and attach them to a catalog node.
     * meta = { nodeId, source: "danfoss" | "competitor", competitorId?, competitorName? }
     */
    async uploadDatasets(meta, files) {
      if (cfg.USE_MOCK) { await delay(600); return window.mockUpload(meta, files); }
      return call("/datasets", { method: "POST", body: form(meta, files) });
    },

    /** Full comparison for the brief the user built. */
    async compare(payload) {
      if (cfg.USE_MOCK) { await delay(2000); return window.mockCompare(payload); }
      return call("/compare", { method: "POST", body: JSON.stringify(payload) });
    },

    /** Free-text question typed into the chat box, with the current brief as context. */
    async ask(payload) {
      if (cfg.USE_MOCK) { await delay(700); return window.mockAsk(payload); }
      return call("/ask", { method: "POST", body: JSON.stringify(payload) });
    },
  };
})();
