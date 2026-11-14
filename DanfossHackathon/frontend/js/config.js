// ---------------------------------------------------------------
// The only file the backend person needs to touch to go live.
// ---------------------------------------------------------------
window.APP_CONFIG = {
  // Base URL of the backend (FastAPI / Flask / Streamlit API etc.).
  // Endpoints used: GET {API_BASE}/catalog, POST {API_BASE}/compare, POST {API_BASE}/ask
  API_BASE: "/api",

  // true  = use js/mock-data.js (works by just opening index.html)
  // false = call the real backend at API_BASE
  USE_MOCK: false,

  // Voice input (🎤) and read aloud (🔊 / Hands-free): language to listen and speak in.
  // In the Android app it can be changed under "Server" in the top bar.
  VOICE_LANG: "en-US",
  VOICE_LANGS: { "en-US": "English", "en-GB": "English (UK)", "da-DK": "Dansk", "de-DE": "Deutsch" },
};
