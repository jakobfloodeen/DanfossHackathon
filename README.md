# Dani – Competitor Intelligence Assistant (Danfoss × SDU G.E.A.R., Case 5)

One app: the website (HTML/JS) + FastAPI backend + the comparison engine.
A Danfoss salesperson compares a Danfoss product with competitor products (or the other way round),
and **every value links to its source page, uncertain data is flagged, and the AI's text is checked by code.**

**New here? Start with [HOW_TO_RUN.md](HOW_TO_RUN.md)** – step-by-step setup for Mac and Windows.

## Run it (short version)
```
cd DanfossHackathon
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env        # then paste your key after ANTHROPIC_API_KEY=   (never commit .env)
uvicorn backend:app --port 8000
```
Open http://localhost:8000. Without a key everything still works, minus the AI interpretation.
Tests (no key, no cost): `pytest -q` inside `DanfossHackathon/`.

## Why Dani is not just a chatbot
The case asks for *"accuracy, source traceability and transparent uncertainty … at every level"*. A chatbot
answers from its training data and sounds right either way. Dani is an **engine with an AI in one, checked seat**:

1. **Code decides what is true.** Facts come only from uploaded datasheets (one AI extraction call per page,
   every quote re-checked by code against the page text). Shortlisting, unit conversion, differences,
   conflicts and system formulas are plain Python - deterministic and testable (200 tests).
2. **The AI only interprets, and is never trusted.** It gets numbered facts and must cite ids for every
   statement. Code re-checks every citation and **every number** in its answer against the cited facts;
   a failed check sends the answer back once, then the problems are shown to the user under *Open points*.
   The result card shows the meter: "N AI statements · M citations · every number checked by code".
3. **Uncertainty is a feature.** Unverified, conflicting and missing values are flagged, never smoothed
   over ("not stated" is not "worse"). Dani happily says what it cannot know and what to upload to fix it.
4. **Commercial claims are governed.** Only sales-guideline notes an **editor approved** can appear or be
   cited (K1, K2 …); viewers can suggest, the review queue decides. No invented prices, ever.
5. **Every answer carries its receipts**: footnotes open the source PDF at the cited page, the AI cost of
   the answer is printed under it, and a cached answer is reused for free.

## The context window, and why Dani does not hallucinate your specs
The engineers asked how we handle the AI context window. Short version: **the model never sees the
documents during a comparison.** The 240-page manual is read once at upload (page by page, ~$0.002/page);
after that, comparisons run on the extracted, code-verified fact table. One comparison sends ~3,000 input
tokens: the family's rules, at most 3 candidates, at most `PROMPT_MAX_FACTS` (40) one-line facts per product
(no quotes, no code numbers), at most 6 approved commercial notes - and the API enforces the answer schema.
Small context = cheap, fast, and nothing irrelevant for the model to "remember wrong". Hallucination control
is not a prompt plea: it is the schema + the number/citation re-check + the retry + the visible meter above.

## Three ways to compare
| Flow | Where in the website | API |
|---|---|---|
| Danfoss product → competitor products | the normal chat steps (Product → Competitors → Priorities → Buyer → Results) | `POST /api/compare` |
| Competitor product the customer uses → closest Danfoss products | header button **Match** | `POST /api/match` |
| **System** (e.g. pump + valve) → the other side's system, as a whole and per component | header button **System** | `POST /api/compare-system` |

**Customer feedback:** "Upload data" → *Customer feedback* (PDF, Excel, CSV, TXT). Only the text is kept
(`data/customers/<customer>/<file>.csv`, never committed). It is used automatically whenever that customer is the
buyer, and the AI cites it as C1, C2 … (code checks every customer number against the cited passage).

All four families of the success criteria (pump + motor + valve + steering) have rule files in `rules/`
and combine in System mode; motor/steering rules are marked as drafts for the product team until their
sales guidelines are loaded. **Commercial layer:** every comparison shows the approved sales-guideline
notes for the involved products (positioning, availability, transitions, warnings - `data/commercial/`),
and the AI may cite them as K1, K2 …; a demo login in the top bar switches between a viewer (suggests
notes) and an editor (approves them).

**System comparison:** each system becomes one virtual product (`pump__max_flow`, `valve__max_inlet_flow`, …).
Code calculates system values from `rules/system.md` (e.g. system flow = min(pump flow, valve inlet flow),
pump/valve flow ratio); missing inputs give "cannot calculate: <reason>", never a guess. Components left open
are matched automatically (best shortlist match).

## AI cost (token diet)
Every Claude call is logged with its tokens and estimated USD in `DanfossHackathon/data/usage.csv` (not committed);
`GET /api/usage` gives the totals, each result shows "AI cost: $…" and the sidebar shows the spend so far.
Model per job is set in `.env` (see `.env.example`):

| Job | Default | Why |
|---|---|---|
| PDF → facts (`EXTRACT_MODEL`), one call per page | `claude-haiku-5-5` | the biggest bill by far; quotes are still verified by code |
| Comparison text (`LLM_MODEL`), one call per comparison | `claude-haiku-5-5`, `LLM_EFFORT=low`; retry on `claude-sonnet-5-5` only when code rejects the answer | the text the salesperson reads |
| Chat-box questions (`ASK_MODEL`) | `claude-haiku-5-5` | short answers from a few pages |

This cheap-first setup is the default; for best text quality set `LLM_MODEL=claude-sonnet-5-5`. The comparison prompt sends short fact lines (no quotes), never code numbers, and at most
`PROMPT_MAX_FACTS` (40) facts per product for at most `PROMPT_MAX_CANDIDATES` (3) candidates; the system prompt is prompt-cached; same question twice = stored answer, $0.

## Getting data in
| Step | In the website | What happens |
|---|---|---|
| Upload a datasheet | **Upload data** → Danfoss / Competitor → drop the PDF | stored in `data/files/`, page text in `data/extracted/` |
| …or import it from the web | **Upload data** → "…or import a datasheet from a manufacturer's website" → Search website → pick → Import | the `scraper/` crawls the site steered by the "Look for" choice (valves/pumps/motors/steering); JavaScript sites are rendered in a hidden browser when Playwright is installed (`python -m playwright install chromium`); its web address goes into `data/documents.csv`, so footnotes open the online PDF at the cited page |
| Turn it into facts | **Extract facts** (shown after an upload; also lists every PDF without facts) – optionally only some pages, e.g. `1-12` | the teammates' converter `pdf_to_csv_test.py` (one AI call per page, ~cents/page) runs in the background; `comparison_engine/bridge.py` translates its rows into `data/repository/<product>__<file>.csv`; page text goes to `data/pages/` |
| Customer feedback | **Upload data** → Customer feedback | text only, `data/customers/` (not committed) |

The converter and scraper are used **unchanged**; `bridge.py` maps the converter's columns (manufacturer, product,
variant, condition, quote_verified, uncertain …) to the app's facts format: the uploaded product gets its catalog id,
sizes/versions get their own rows, values under different conditions are not conflicts, ordering-code tables are
skipped, and a fact is `ok` only if its quote was found on the page and the converter was not unsure.

## How a comparison is made
1. **Upload / web import** (see above).
2. **PDF → facts CSV** with the converter via `bridge.py` (contract below, for anyone writing facts CSVs by hand).
3. **Engine** (`comparison_engine/`): loads all CSVs, converts units in code, flags conflicts, picks a shortlist,
   numbers every fact (F1, F2 …) and asks Claude ONE question with the LOGIC files (`rules/`).
4. **Guardrails** (code): every fact id, number, product and verdict in the AI answer is checked; one retry;
   remaining problems are shown, never hidden. No key / API error → facts + code differences only.
5. **Website adapter** (`comparison_engine/website.py`): turns the result into the table the website draws.
   Each cell has a status: ok · converted · unverified · conflict · inferred (calculated) · missing (the datasheet
   does not state it) · "–" (no matching value for this product, not counted as missing) · pending.
6. **Short result:** the table shows only rows where both sides have a value, incl. values the AI paired across
   different names ("paired by AI", each value labelled with the datasheet's own name). Other rows are under
   "All facts" (collapsed, not printed). AI text: max 3 differences, 3 benefits, 2 trade-offs, ~30 words each.

## Contract for the PDF → CSV converter
Write **one CSV per uploaded file** into `DanfossHackathon/data/repository/`. The engine picks it up automatically.

| column | meaning |
|---|---|
| `product_id` | **the website catalog id** of the product the file was uploaded for: `nodeId` (Danfoss, e.g. `pvg32`) or the competitor product id (e.g. `kawasaki-kmp`) |
| `brand`, `product_name`, `family` | e.g. `Danfoss`, `PVG 32`, `valves` (the catalog's top-level family id is fine) |
| `attribute` | snake_case name. Prefer the standard names in `rules/<family>.md` (e.g. `rated_flow_per_section`, `max_pressure_continuous`); other names are mapped via `attribute_aliases` or paired by the AI |
| `value`, `unit` | exactly as written in the datasheet (`4,33` + `in3/rev`). Units are converted by the engine, never by the AI |
| `source_file`, `page`, `quote` | the uploaded file name, the page number, the exact text the value comes from |
| `status` | `ok` only if `comparison_engine.quotes.verify_quote(quote, page_text)` is True, else `unverified`; `missing` when the datasheet doesn't state it (value empty) |

Before saving: `comparison_engine.validate_csv(df)` must return `[]`.
Until the converter is ready, two seed files are in `data/repository/`:
`kawasaki-kmp_KPES_leaflet.csv` (quotes verified by code against the uploaded leaflet) and
`pvg32_from_website_mockdata.csv` (values from the official PVG 32 Technical Information as transcribed in
`frontend/js/mock-data.js`; status `unverified` because the PDF is not in the repo to check quotes).
Delete them when the converter produces real files for these products (the KPES seed was removed after the real KMP
leaflet was imported and extracted). The `DEMO_*.csv` pump files are made-up
demo data (clearly marked in the app) so the system comparison can be shown before real pump datasheets exist.

## `data/documents.csv` (online links, filled automatically by the web import)
We keep only CSVs, so every source links to the ORIGINAL document online, opened at the cited page.
One row per document; `source_file` exactly as in the facts CSVs:

| column | meaning |
|---|---|
| `source_file` | e.g. `BC152886483664en-001602.pdf` |
| `url` | direct link to the document (for a PDF the app adds `#page=N`); empty = no link yet |
| `title`, `revision`, `date` | shown in the source list |
| `found_by`, `checked_at` | e.g. `crawler`, `2026-11-14` |

Check with `comparison_engine.documents.validate_documents(df)` before saving. Every footnote [n] in the
result then opens the document at its page; sources without a url say "no online link yet".

## Folders
| Path | What |
|---|---|
| `DanfossHackathon/backend.py` | FastAPI: catalog, uploads (incl. customer feedback), `/api/web/discover`, `/api/web/import`, `/api/extract` + `/api/jobs/<id>`, `/api/compare`, `/api/match`, `/api/compare-system`, `/api/products`, `/api/ask`, serves the website and `/files` |
| `DanfossHackathon/frontend/` | the website (no build step) |
| `DanfossHackathon/comparison_engine/` | comparison logic; each module starts with a plain-English explanation |
| `DanfossHackathon/rules/` | LOGIC files (Otto): `_general.md` + `valve.md` / `pump.md` / `system.md` (YAML for code + text for the AI). `attribute_aliases` map datasheet names to standard names, e.g. `max_flow_per_section` → `rated_flow_per_section` |
| `DanfossHackathon/data/` | `catalog.json`, uploads (`files/`), page text (`extracted/`), facts CSVs (`repository/`), document links (`documents.csv`), customer feedback (`customers/`, not committed) |
| `DanfossHackathon/tests/` | 177 tests, fake AI, temporary data |
| `scraper/` | teammates' datasheet discovery/download (unchanged; its own tests: `python -m unittest discover -s scraper/tests`) |
| `pdf_to_csv_test.py` | teammates' PDF → facts converter (unchanged; called by `comparison_engine/bridge.py`) |
| `customer_scraper/` | teammates' scraper variant for customer websites (not connected to the app yet) |
| `archive/` | earlier versions kept for reference: Streamlit prototype, mock-only website copy, Streamlit library page |
