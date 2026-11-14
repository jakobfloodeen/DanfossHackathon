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

    /** Reverse flow: competitor product the customer uses -> closest Danfoss products (same response shape). */
    async match(payload) {
      if (cfg.USE_MOCK) { await delay(300); throw new Error("Danfoss match needs the real backend (USE_MOCK is on)."); }
      return call("/match", { method: "POST", body: JSON.stringify(payload) });
    },

    /** Scraper: find datasheets on a manufacturer website. */
    async webDiscover(url, topic) {
      return call("/web/discover", { method: "POST", body: JSON.stringify({ url, topic }) });
    },

    /** Scraper: download one found document and store it like an upload (its web link is kept for footnotes). */
    async webImport(payload) {
      return call("/web/import", { method: "POST", body: JSON.stringify(payload) });
    },

    /** Start turning a stored PDF into facts (teammates' converter, one AI call per page). */
    async extract(ownerId, fileId, pages) {
      return call("/extract", { method: "POST", body: JSON.stringify({ ownerId, fileId, pages }) });
    },

    /** Progress of a background job. */
    async job(id) {
      return call("/jobs/" + encodeURIComponent(id));
    },

    /** All products that have extracted facts (for the system builder). */
    async products() {
      if (cfg.USE_MOCK) return { products: [] };
      return call("/products");
    },

    /** System comparison: a set of components (pump + valve ...) against the other side's set. */
    async compareSystem(payload) {
      if (cfg.USE_MOCK) { await delay(300); throw new Error("System comparison needs the real backend (USE_MOCK is on)."); }
      return call("/compare-system", { method: "POST", body: JSON.stringify(payload) });
    },

    /** Free-text question typed into the chat box, with the current brief as context. */
    async ask(payload) {
      if (cfg.USE_MOCK) { await delay(700); return window.mockAsk(payload); }
      return call("/ask", { method: "POST", body: JSON.stringify(payload) });
    },
  };
})();
