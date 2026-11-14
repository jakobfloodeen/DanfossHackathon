// ---------------------------------------------------------------
// Read aloud (text → speech) and hands-free mode.
//
// • Every assistant answer in the chat and every comparison result
//   gets a 🔊 button that reads it aloud.
// • "Hands-free" (top bar) is for when the user can't look at or touch
//   the phone, e.g. while driving:
//     1. the microphone starts,
//     2. the spoken question is sent automatically (no Send tap),
//     3. the answer is read aloud,
//     4. the microphone starts again for the next question.
//   If nothing is said, the loop stops. Tap Hands-free again to switch it off.
//
// Two engines, chosen automatically:
//   1. In the Android app: the phone's own text-to-speech
//      (Capacitor plugin @capacitor-community/text-to-speech).
//   2. In a browser: the Web Speech API (speechSynthesis).
// Nothing new is sent to the backend: it only reads what is on screen.
// ---------------------------------------------------------------
window.Speech = (() => {
  "use strict";

  const nativeTts = window.Capacitor?.isNativePlatform?.() ? window.Capacitor.Plugins?.TextToSpeech : null;
  const webTts = window.speechSynthesis || null;
  const supported = Boolean(nativeTts || webTts);

  const read = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
  const write = (k, v) => { try { localStorage.setItem(k, v); } catch { /* fine: only for this visit */ } };

  let handsFree = read("handsFree") === "1";
  let generation = 0;           // increases on every stop(), so a finished old speech can't restart the mic
  let speakingButton = null;

  const language = () => (window.Voice?.language?.() || window.APP_CONFIG?.VOICE_LANG || "en-US");

  /** Make answer text pleasant to listen to: drop citations, spell out common units. */
  function forListening(text) {
    return String(text || "")
      .replace(/\(([^()]*\bp\.\s*\d+[^()]*)\)/g, "")       // (PVG 32 datasheet, p. 12)
      .replace(/\[\d+\]/g, "")                                // [3]
      .replace(/\bvs\.?(?=\s)/gi, "versus")
      .replace(/\bl\/min\b/gi, " litres per minute")
      .replace(/°C\b/g, " degrees Celsius")
      .replace(/\bcm³\/rev\b|\bcm3\/rev\b|\bcc\/rev\b/gi, " cubic centimetres per revolution")
      .replace(/\s{2,}/g, " ")
      .replace(/\s+([.,;:!?])/g, "$1")
      .trim();
  }

  /** Split into sentences so long answers don't get cut off (a Chrome limitation) and stop is instant. */
  function chunks(text) {
    return text.match(/[^.!?]+[.!?]*\s*/g)?.map((s) => s.trim()).filter(Boolean) || [text];
  }

  function stop() {
    generation++;
    if (nativeTts) nativeTts.stop().catch(() => {});
    if (webTts) webTts.cancel();
    if (speakingButton) speakingButton.classList.remove("is-speaking");
    speakingButton = null;
  }

  /** Speak the text. Resolves true when it was read to the end, false when it was stopped. */
  async function speak(text, button) {
    stop();
    const mine = generation;
    text = forListening(text);
    if (!text || !supported) return false;
    if (button) { speakingButton = button; button.classList.add("is-speaking"); }
    try {
      for (const part of chunks(text)) {
        if (mine !== generation) return false;
        if (nativeTts) await nativeTts.speak({ text: part, lang: language(), rate: 1.0 });
        else await new Promise((resolve) => {
          const u = new SpeechSynthesisUtterance(part);
          u.lang = language();
          u.onend = u.onerror = resolve;
          webTts.speak(u);
        });
      }
      return mine === generation;
    } catch {
      return false;
    } finally {
      if (mine === generation && button) { button.classList.remove("is-speaking"); speakingButton = null; }
    }
  }

  /** Start the chat microphone (used by hands-free). */
  function listenForQuestion() {
    document.querySelector("#composer .mic-btn:not(.is-listening)")?.click();
  }

  /** A new assistant answer arrived (called by app.js). In hands-free mode: read it, then listen again. */
  async function onAnswer(text) {
    if (!handsFree) return;
    if (await speak(text)) listenForQuestion();
  }

  /** A new comparison result arrived (called by app.js). In hands-free mode: read the summary. */
  async function onResult(result, company) {
    if (!handsFree || !result) return;
    if (await speak(resultText(result, company))) listenForQuestion();
  }

  /** The spoken version of a comparison: title, confidence and the AI's key points (no tables, no numbers lists). */
  function resultText(r, company) {
    const part = (title, items) => (items?.length ? `${title}. ${items.map((i) => i.text).join(" ")}` : "");
    return [
      r.title + ".",
      r.confidence?.level ? `Confidence ${r.confidence.level}.` : "",
      part("Key differences", r.insights?.differences),
      part(`Benefits for ${company || "the customer"}`, r.insights?.benefits),
      part("Switching trade-offs", r.insights?.tradeoffs),
      r.flags?.length ? `There are ${r.flags.length} open points on screen.` : "",
    ].filter(Boolean).join(" ");
  }

  /** A small 🔊 button that reads the given text (or the text returned by a function) aloud. */
  function button(getText, label = "Read aloud") {
    if (!supported) return null;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "speak-btn";
    b.title = label;
    b.setAttribute("aria-label", label);
    b.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M3 10v4h4l5 5V5L7 10H3Zm13.5 2A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4ZM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6Z"/></svg>';
    b.addEventListener("click", () => {
      if (b.classList.contains("is-speaking")) stop();
      else speak(typeof getText === "function" ? getText() : getText, b);
    });
    return b;
  }

  // ---------- hands-free switch in the top bar ----------
  function setHandsFree(on) {
    handsFree = on;
    write("handsFree", on ? "1" : "0");
    const t = document.getElementById("btn-handsfree");
    if (t) t.setAttribute("aria-pressed", on ? "true" : "false");
    document.documentElement.classList.toggle("is-handsfree", on);
  }

  function addToggle() {
    const actions = document.querySelector(".top-actions");
    if (!actions || !supported) return;
    const t = document.createElement("button");
    t.id = "btn-handsfree";
    t.type = "button";
    t.className = "btn btn-ghost btn-sm handsfree-btn";
    t.title = "Hands-free: speak your question, hear the answer";
    t.textContent = "Hands-free";
    t.addEventListener("click", async () => {
      if (handsFree) { setHandsFree(false); stop(); return; }
      setHandsFree(true);
      // if no product is chosen yet, the backend answers "Pick a product above first", which is read aloud too
      if (await speak("Hands-free is on. Ask your question.")) listenForQuestion();
    });
    actions.prepend(t);
    setHandsFree(handsFree);
  }

  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", addToggle) : addToggle();

  return {
    supported, speak, stop, button, onAnswer, onResult, resultText,
    get handsFree() { return handsFree; },
  };
})();
