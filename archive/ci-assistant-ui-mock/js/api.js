// ---------------------------------------------------------------
// API layer. The UI only talks to these functions, so swapping
// mock data for the real backend is a one-line change in config.js.
// Request/response shapes are documented in README.md.
//
// The backend owns the catalog: after any create/upload the UI
// re-fetches GET /catalog, so front end and back end never drift.
// ---------------------------------------------------------------
const Api = (() => {
  const cfg = window.APP_CONFIG;
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));

  async function call(path, options = {}) {
    const isForm = options.body instanceof FormData;
    const res = await fetch(cfg.API_BASE + path, {
      headers: isForm ? {} : { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) throw new Error(`API ${path} failed with status ${res.status}`);
    return res.json();
  }

  return {
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
      const fd = new FormData();
      Object.entries(meta).forEach(([k, v]) => v != null && fd.append(k, v));
      [...files].forEach((f) => fd.append("files", f));
      return call("/datasets", { method: "POST", body: fd });
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
