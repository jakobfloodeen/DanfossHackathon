# Dani: Android app (Capacitor)

The app is the normal website (`DanfossHackathon/frontend/`) packed into an Android app, plus
🎤 **voice input**, 🔊 **read aloud** and a **Hands-free** mode. The website is *not* copied into git here: `npm run sync` copies it into
`www/` at build time, so every change to the website is automatically in the next APK.

```
phone (APK)                                 computer / server
┌──────────────────────────┐   HTTPS/HTTP   ┌──────────────────────────────┐
│ website files (frontend) │ ─────────────► │ uvicorn backend:app          │
│ 🎤 Android speech → text  │   /api/...     │ comparison engine, Claude API│
└──────────────────────────┘                └──────────────────────────────┘
```

The backend (Python) cannot run on the phone, so it stays on a computer or server.
The API key stays there too; it is never inside the APK.

## Get the APK
GitHub builds it on every push (`.github/workflows/android-apk.yml`):
**Releases → "Android app (latest build, …)" → `app-debug.apk`**. Open that page on the phone,
download, allow "install unknown apps" for the browser, install.

## First start: connect to the backend
The app asks for the **backend address**. Options:

| Situation | Address to enter |
|---|---|
| Phone and laptop on the same Wi-Fi | `http://<laptop IP>:8000` – start the backend with `uvicorn backend:app --host 0.0.0.0 --port 8000` |
| Different networks (demo day) | a tunnel, e.g. `ngrok http 8000` → `https://xxxx.ngrok-free.app` |
| Hosted backend | its URL, e.g. `https://ci-assistant.onrender.com` |

Change it later with the **Server** button (top bar), which also sets the voice language.
The APK comes with the address in `mobile/backend-url.txt` already filled in (currently the team's
permanent ngrok domain), so new installs connect straight away. Start the tunnel on the laptop with
`ngrok http 8000 --url https://saturate-petal-oxidizing.ngrok-free.dev`.
To use another address, edit that file and push; a repository variable `BACKEND_URL`
(Settings → Secrets and variables → Actions → Variables) overrides it.

## Notice when the app opens
Every time the app opens it shows a short "Before you start" message: the AI runs on the team's own
API key, so testers are asked to keep it to a few questions and not to upload or import huge documents.
One tap closes it. The text lives in `showCostNotice()` in `DanfossHackathon/frontend/js/mobile.js`
(app only; the website on a computer does not show it).

## Voice input
`DanfossHackathon/frontend/js/voice.js` adds a 🎤 button to every field with `data-voice`
(the chat box and the buyer notes). In the app it uses the phone's own speech recognizer
(plugin `@capacitor-community/speech-recognition`, free, no key); in Chrome/Edge on a computer it uses
the browser's Web Speech API. The words are only typed into the field. Nothing is sent before **Send**.

## Read aloud and Hands-free
`DanfossHackathon/frontend/js/speech.js`:
- 🔊 under every assistant answer and **Read summary** on every comparison result read them aloud
  (citations like "(datasheet, p. 12)" are skipped, "l/min" is said as "litres per minute").
- **Hands-free** (top bar), for when you can't look at or touch the phone, e.g. driving:
  the mic starts → the spoken question is sent automatically → the answer is read aloud → the mic
  starts again. Say nothing and the loop stops; tap Hands-free again to switch it off.
  New comparison results are read aloud automatically while it is on.
  Choosing the product still needs taps (the chat questions are about the chosen product).
- Engines: the phone's own text-to-speech in the app (`@capacitor-community/text-to-speech`),
  `speechSynthesis` in Chrome/Edge. Language = the voice language under **Server**.
- It only reads what is already on screen; nothing extra is sent to the backend.

## Build it yourself (optional)
Needs Node 22+, JDK 21 and the Android SDK (easiest: install Android Studio).
```
cd mobile
npm ci
npm run sync          # copy website + update the Android project
npm run apk           # → android/app/build/outputs/apk/debug/app-debug.apk
npx cap open android  # or open it in Android Studio and press Run with a phone attached
```

| File | Purpose |
|---|---|
| `capacitor.config.json` | app id/name, allows `http://` backends on the local network |
| `copy-web.mjs` | copies the website into `www/`, writes the default backend address |
| `android/` | generated Android project; `AndroidManifest.xml` has the microphone permission and finds the speech engines |
| `../DanfossHackathon/frontend/js/mobile.js` | app-only glue: backend address, opening source PDFs |
