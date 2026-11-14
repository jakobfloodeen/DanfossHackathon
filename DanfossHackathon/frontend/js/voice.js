// ---------------------------------------------------------------
// Voice input (speech → text).
//
// Any <input> or <textarea> with the attribute  data-voice  gets a
// 🎤 button. Tap it, speak, and the spoken words are added to the field.
// Fields that appear later (e.g. the buyer form) are picked up
// automatically.
//
// Two engines, chosen automatically:
//   1. In the Android app: the native speech recognizer of the phone
//      (Capacitor plugin @capacitor-community/speech-recognition).
//   2. In a browser: the Web Speech API (works in Chrome and Edge).
// If neither exists, no button is shown, so nothing breaks.
//
// The spoken text is only typed into the field. Nothing is sent
// until the user presses Send, exactly like typed text.
// ---------------------------------------------------------------
const Voice = (() => {
  "use strict";

  const cfg = window.APP_CONFIG || {};
  const nativePlugin = window.Capacitor?.isNativePlatform?.() ? window.Capacitor.Plugins?.SpeechRecognition : null;
  const WebRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = Boolean(nativePlugin || WebRecognition);

  /** Language to listen for, e.g. "en-US" or "da-DK". Can be changed in the app's settings. */
  function language() {
    try { return localStorage.getItem("voiceLang") || cfg.VOICE_LANG || "en-US"; }
    catch { return cfg.VOICE_LANG || "en-US"; }
  }

  let active = null; // { button, stop } while listening

  /** Add text to the field (with a space if there is already text) and tell the app it changed. */
  function insert(field, text) {
    text = String(text || "").trim();
    if (!text) return;
    const before = field.value.trimEnd();
    field.value = before ? before + " " + text : text;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    field.focus();
  }

  function setListening(button, on) {
    button.classList.toggle("is-listening", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.title = on ? "Listening… tap to stop" : "Speak instead of typing";
  }

  function showError(button, message) {
    button.classList.add("has-error");
    button.title = message;
    setTimeout(() => { button.classList.remove("has-error"); button.title = "Speak instead of typing"; }, 3000);
  }

  // ---------- engine 1: Android app ----------
  async function listenNative(field, button) {
    const perm = await nativePlugin.checkPermissions();
    if (perm.speechRecognition !== "granted") {
      const asked = await nativePlugin.requestPermissions();
      if (asked.speechRecognition !== "granted") throw new Error("Microphone permission was not given.");
    }
    active = { button, stop: () => nativePlugin.stop() };
    setListening(button, true);
    try {
      // partialResults:false → the promise returns the final text when the user stops talking
      const res = await nativePlugin.start({ language: language(), maxResults: 1, partialResults: false, popup: false });
      insert(field, res?.matches?.[0]);
    } finally {
      setListening(button, false);
      active = null;
    }
  }

  // ---------- engine 2: browser ----------
  function listenWeb(field, button) {
    return new Promise((resolve, reject) => {
      const rec = new WebRecognition();
      rec.lang = language();
      rec.interimResults = false;
      rec.maxAlternatives = 1;
      let text = "";
      rec.onresult = (e) => { text = [...e.results].map((r) => r[0].transcript).join(" "); };
      rec.onerror = (e) => reject(new Error(e.error === "not-allowed" ? "Microphone permission was not given." : "Speech recognition error: " + e.error));
      rec.onend = () => { setListening(button, false); active = null; insert(field, text); resolve(); };
      active = { button, stop: () => rec.stop() };
      setListening(button, true);
      rec.start();
    });
  }

  async function toggle(field, button) {
    if (active) { const wasMe = active.button === button; active.stop(); if (wasMe) return; }
    try {
      if (nativePlugin) await listenNative(field, button);
      else await listenWeb(field, button);
    } catch (err) {
      setListening(button, false);
      active = null;
      // "No match" just means nothing was heard: not worth an error
      if (!/no match/i.test(err.message)) showError(button, err.message);
    }
  }

  /** Put a mic button right after the field (once). */
  function attach(field) {
    if (field.dataset.voiceReady) return;
    field.dataset.voiceReady = "1";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-ghost mic-btn";
    button.setAttribute("aria-label", "Voice input");
    button.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z"/></svg>';
    setListening(button, false);
    button.addEventListener("click", () => toggle(field, button));
    if (field.tagName === "TEXTAREA") {
      // textareas: wrap so the button sits in the corner
      const wrap = document.createElement("div");
      wrap.className = "voice-wrap";
      field.replaceWith(wrap);
      wrap.append(field, button);
    } else {
      field.after(button);
    }
  }

  function scan(root = document) {
    root.querySelectorAll?.("[data-voice]").forEach(attach);
  }

  if (supported) {
    const start = () => {
      scan();
      // fields rendered later by app.js
      new MutationObserver((list) => list.forEach((m) => m.addedNodes.forEach((n) => n.nodeType === 1 && (n.matches("[data-voice]") ? attach(n) : scan(n)))))
        .observe(document.body, { childList: true, subtree: true });
    };
    document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", start) : start();
  }

  return { supported, language };
})();
