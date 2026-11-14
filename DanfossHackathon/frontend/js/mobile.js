// ---------------------------------------------------------------
// Android app glue. Does nothing in a normal browser.
//
// In the app the website files live on the phone, but the backend
// (FastAPI + comparison engine) still runs on a computer or server.
// So the app needs the backend's address, e.g.
//   https://abcd-1234.ngrok-free.app   or   http://192.168.1.20:8000
// It is asked for on first start and can be changed with the
// "Server" button in the top bar. It is stored on the phone only.
//
// Must load after config.js and before api.js / app.js.
// ---------------------------------------------------------------
(() => {
  "use strict";
  if (!window.Capacitor?.isNativePlatform?.()) return;

  const cfg = window.APP_CONFIG;
  const read = (k) => { try { return localStorage.getItem(k) || ""; } catch { return ""; } };
  const write = (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage off: keep for this session only */ } };

  const clean = (url) => String(url || "").trim().replace(/\/+$/, "").replace(/\/api$/, "");
  // default comes from the build (mobile/copy-web.mjs → js/mobile-defaults.js), may be empty
  let server = clean(read("serverUrl") || window.MOBILE_DEFAULT_SERVER);

  cfg.API_BASE = server ? server + "/api" : "/api-not-set";
  // free ngrok tunnels show a warning page instead of the API unless this header is sent
  cfg.EXTRA_HEADERS = { "ngrok-skip-browser-warning": "1" };
  document.documentElement.classList.add("is-app");

  // Source links from the backend can be relative ("/files/<owner>/<file>.pdf#page=3"):
  // they live on the server, not in the app, so open them on the server in the phone's browser.
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.('a[href^="/files/"]');
    if (!a || !server) return;
    e.preventDefault();
    window.open(server + a.getAttribute("href"), "_blank");
  }, true);

  function openSettings(firstRun) {
    let dlg = document.getElementById("server-dialog");
    if (!dlg) {
      dlg = document.createElement("dialog");
      dlg.id = "server-dialog";
      document.body.append(dlg);
    }
    const lang = read("voiceLang") || cfg.VOICE_LANG || "en-US";
    const langs = cfg.VOICE_LANGS || { "en-US": "English", "da-DK": "Dansk" };
    dlg.innerHTML = `
      <form method="dialog" class="dialog-body">
        <div class="dialog-head"><h3>App settings</h3>${firstRun ? "" : '<button class="link-btn" value="cancel" type="submit">Close</button>'}</div>
        <label class="field"><span>Backend address</span>
          <input name="server" type="url" inputmode="url" required placeholder="https://your-tunnel.ngrok-free.app" value="${server.replace(/"/g, "&quot;")}"></label>
        <p class="hint">Where <code>uvicorn backend:app</code> runs. The phone must be able to reach it
          (same Wi-Fi with the computer's IP and port 8000, or a tunnel/cloud address).</p>
        <label class="field"><span>Voice language</span>
          <select name="lang">${Object.entries(langs).map(([k, v]) => `<option value="${k}" ${k === lang ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <p class="hint server-status" aria-live="polite"></p>
        <div class="actions"><button class="btn btn-primary" value="save" type="submit">Save</button></div>
      </form>`;
    const form = dlg.querySelector("form");
    const status = dlg.querySelector(".server-status");
    form.addEventListener("submit", async (e) => {
      if (e.submitter?.value !== "save") return;
      e.preventDefault();
      const url = clean(form.server.value);
      write("voiceLang", form.lang.value);
      status.textContent = "Checking the backend…";
      try {
        const res = await fetch(url + "/api/catalog", { headers: cfg.EXTRA_HEADERS });
        if (!res.ok) throw new Error("status " + res.status);
      } catch (err) {
        status.textContent = `Couldn't reach ${url} (${err.message}). Saved anyway; check the address and that the backend is running.`;
        write("serverUrl", url);
        return;
      }
      write("serverUrl", url);
      location.reload(); // restart the app with the new backend
    });
    dlg.addEventListener("cancel", (e) => { if (firstRun) e.preventDefault(); });
    dlg.showModal();
  }

  function addButton() {
    const actions = document.querySelector(".top-actions");
    if (!actions) return;
    const b = document.createElement("button");
    b.className = "btn btn-ghost btn-sm";
    b.type = "button";
    b.textContent = "Server";
    b.title = "Backend address and voice language";
    b.addEventListener("click", () => openSettings(false));
    actions.prepend(b);
  }

  const start = () => { addButton(); if (!server) openSettings(true); };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start) : start();
})();
