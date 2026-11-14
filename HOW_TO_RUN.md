# How to run the app on your computer

About 10 minutes, once. After that, starting the app is one command (step 5).
Commands are shown for **Mac** (Terminal) and **Windows** (PowerShell). Copy them one line at a time.

## 1. What you need
- **Python 3.11 or newer** – check with `python3 --version` (Mac) or `py --version` (Windows).
  Not installed? Download it from https://www.python.org/downloads/ – on Windows tick **"Add python.exe to PATH"**.
- **Git** – check with `git --version`. Not installed? https://git-scm.com/downloads
- **An Anthropic API key** for the AI parts (see step 4). Everything else works without one.

## 2. Get the code (branch `combine-app`)
```
git clone https://github.com/jakobfloodeen/DanfossHackathon.git
cd DanfossHackathon
git checkout combine-app
```
Already cloned it earlier? Go into that folder and run `git fetch` and then `git checkout combine-app` and `git pull`.

The app lives in the inner folder, which has the same name: `DanfossHackathon/DanfossHackathon/`.

## 3. Install (once)
**Mac**
```
cd DanfossHackathon
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
**Windows**
```
cd DanfossHackathon
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
This creates a private Python environment (`.venv`) inside the app folder, so nothing else on your computer changes.

## 4. Your API key (once)
The key is personal and secret. **Never put it into a file that gets committed, never paste it into chat or GitHub.**
1. Create one at https://console.anthropic.com → API Keys → Create Key (add a few dollars of credit under Billing),
   or ask Jesper to send you the team key **privately** (direct message, not GitHub).
2. Create the file `.env` in the app folder (`DanfossHackathon/DanfossHackathon/.env`):

   **Mac:** `cp ../.env.example .env` then `open -e .env`
   **Windows:** `copy ..\.env.example .env` then `notepad .env`
3. Paste the key directly after `ANTHROPIC_API_KEY=` (no spaces, no quotes) and save.

`.env` is listed in `.gitignore`, so Git ignores it. Without a key the app still runs: you get the sourced facts and
code-computed differences, just no AI text.

## 5. Start the app (every time)
In the app folder (`DanfossHackathon/DanfossHackathon`):

**Mac:** `.venv/bin/uvicorn backend:app --port 8000`
**Windows:** `.venv\Scripts\uvicorn backend:app --port 8000`

Open **http://localhost:8000** in your browser. Keep the terminal window open while you use the app;
**Ctrl+C** stops it.

## 6. What to try
| In the website | What it does |
|---|---|
| Valves → Proportional → PVG 32 → … → Generate comparison | Danfoss product vs competitor products |
| **Match** | the customer uses a competitor product → closest Danfoss products |
| **System** | pump + valve system vs the other side's system (pumps are DEMO data for now) |
| **Upload data** → drop a PDF, or "…import a datasheet from a manufacturer's website" | add datasheets |
| **Upload data** → **Extract facts** | turn a PDF into comparable facts (AI, a few cents per page – choose pages for long PDFs, e.g. `1-12`) |
| **Upload data** → Customer feedback | requirements or complaints from a customer (only the text is kept) |

Every footnote [n] opens its source; values marked Unverified, Conflict or Not stated must be checked before quoting.

## 7. Get the newest version
In the repo folder: `git pull` – then start the app again (step 5). If `requirements.txt` changed, repeat step 3.

## Problems?
| Message | Fix |
|---|---|
| `address already in use` | the app is already running somewhere – open http://localhost:8000, or start it with `--port 8001` |
| `python3` / `py` not found | install Python (step 1); on Windows re-run the installer and tick "Add python.exe to PATH" |
| `No module named ...` | you are not using the `.venv` commands, or step 3 failed – run step 3 again |
| "AI interpretation not available … no ANTHROPIC_API_KEY" | `.env` is missing or in the wrong folder – it must be next to `backend.py` (step 4) |
| "API key was rejected" / "rate limit" | wrong key, no credit left, or too many calls – check https://console.anthropic.com |
| Extract facts says "failed" | check the key; scanned PDFs (pictures, no text) can't be read |

## For developers
- Tests (no key, no cost): `.venv/bin/pytest -q` (Windows: `.venv\Scripts\pytest -q`) in the app folder.
- How everything fits together: `README.md`. Rules for working with Claude Code: `CLAUDE.md`.


## One extra step for importing datasheets from manufacturer websites (e.g. Bosch Rexroth)

Some manufacturer sites build their pages with JavaScript, so the importer opens them in a hidden browser.
Install it once per computer, after `pip install -r requirements.txt`:

    python -m playwright install chromium

Without it, importing from JavaScript-based sites (like Bosch Rexroth) will not list the datasheets.
Sites with plain links (like Kawasaki) work without it.
