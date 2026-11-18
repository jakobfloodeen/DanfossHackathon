// ---------------------------------------------------------------
// API layer. The UI only talks to these functions.
// Request/response shapes are documented in README.md.
//
// The backend owns the catalog: after any create/upload the UI
// re-fetches GET /catalog, so front end and back end never drift.
// ---------------------------------------------------------------
const Api = (() => {
  const cfg = window.APP_CONFIG;

  async function call(path, options = {}) {
    const isForm = options.body instanceof FormData;
    const user = localStorage.getItem("dani-user") || "senior";   // demo login: senior (editor) / junior (viewer)
    const res = await fetch(cfg.API_BASE + path, {
      headers: { "X-User": user, ...(isForm ? {} : { "Content-Type": "application/json" }) },
      ...options,
    });
    if (!res.ok) throw new Error(`API ${path} failed with status ${res.status}`);
    return res.json();
  }

  return {
    /** Product tree, competitors, factors, applications. */
    async getCatalog() {
      return call("/catalog");
    },

    /** Create a product family, product type or product under parentId (null = root). */
    async createNode(body) {
      return call("/catalog/nodes", { method: "POST", body: JSON.stringify(body) });
    },

    /**
     * Upload dataset files (PDF, CSV, XLSX, JSON …) and attach them to a catalog node.
     * meta = { nodeId, source: "danfoss" | "competitor", competitorId?, competitorName? }
     */
    async uploadDatasets(meta, files) {
      const fd = new FormData();
      Object.entries(meta).forEach(([k, v]) => v != null && fd.append(k, v));
      [...files].forEach((f) => fd.append("files", f));
      return call("/datasets", { method: "POST", body: fd });
    },

    /** Full comparison for the brief the user built. */
    async compare(payload) {
      return call("/compare", { method: "POST", body: JSON.stringify(payload) });
    },

    /** Reverse flow: competitor product the customer uses -> closest Danfoss products (same response shape). */
    async match(payload) {
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
      return call("/products");
    },
    /** Every extracted fact of one product (Browse data). */
    async facts(productId) {
      return call("/facts?product=" + encodeURIComponent(productId));
    },

    /** System comparison: a set of components (pump + valve ...) against the other side's set. */
    async compareSystem(payload) {
      return call("/compare-system", { method: "POST", body: JSON.stringify(payload) });
    },

    /** Free-text question typed into the chat box, with the current brief as context. */
    async user() {
      return call("/user");
    },
    async addCommercial(payload) {
      return call("/commercial", { method: "POST", body: JSON.stringify(payload) });
    },
    async decideCommercial(id, action) {
      return call("/commercial/decide", { method: "POST", body: JSON.stringify({ id, action }) });
    },
    async usage() {
      return call("/usage");
    },
    async ask(payload) {
      return call("/ask", { method: "POST", body: JSON.stringify(payload) });
    },
    /** Read the customer's website into a saved profile (once per customer). -> {status:"cached", profile} | {jobId, company} */
    async researchCustomer(payload) {
      const user = localStorage.getItem("dani-user") || "senior";
      const res = await fetch(cfg.API_BASE + "/customers/research", {
        method: "POST", headers: { "X-User": user, "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Research failed (status ${res.status})`);
      return body;
    },
    async customerProfile(company) {
      return call("/customers/profile?company=" + encodeURIComponent(company));
    },
    /** Suggestions while typing a customer name: names, aliases ("Fendt" -> AGCO) and misspellings. */
    async searchCustomers(q) {
      return call("/customers/search?q=" + encodeURIComponent(q));
    },
    async addCustomerAlias(alias, company) {
      return call("/customers/alias", { method: "POST", body: JSON.stringify({ alias, company }) });
    },
  };
})();
