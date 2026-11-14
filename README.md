# Competitor Intelligence Assistant (Danfoss × SDU G.E.A.R., Case 5)

One app: the website (HTML/JS) + FastAPI backend + the comparison engine.
A Danfoss salesperson compares a Danfoss product with competitor products (or the other way round),
and **every value links to its source page, uncertain data is flagged, and the AI's text is checked by code.**

## Run it
```
cd DanfossHackathon
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env        # then paste your key after ANTHROPIC_API_KEY=   (never commit .env)
uvicorn backend:app --port 8000
```
Open http://localhost:8000. Without a key everything still works, minus the AI interpretation.
Tests (no key, no cost): `pytest -q` inside `DanfossHackathon/`.

## Two ways to compare
| Flow | Where in the website | API |
|---|---|---|
| Danfoss product → competitor products | the normal chat steps (Product → Competitors → Priorities → Buyer → Results) | `POST /api/compare` |
| Competitor product the customer uses → closest Danfoss products | header button **Find Danfoss match** | `POST /api/match` |

## How a comparison is made
1. **Upload** (website, `/api/datasets`): the file is stored in `data/files/`, its page text in `data/extracted/`.
2. **PDF → facts CSV** (teammate's converter, in progress): one CSV per upload into `data/repository/` (contract below).
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
Delete them when the converter produces real files for these products.

## Folders
| Path | What |
|---|---|
| `DanfossHackathon/backend.py` | FastAPI: catalog, uploads, `/api/compare`, `/api/match`, `/api/ask`, serves the website and `/files` |
| `DanfossHackathon/frontend/` | the website (no build step) |
| `DanfossHackathon/comparison_engine/` | comparison logic; each module starts with a plain-English explanation |
| `DanfossHackathon/rules/` | LOGIC files (Otto): `_general.md` + `valve.md` / `pump.md` (YAML for code + text for the AI). `attribute_aliases` map datasheet names to standard names, e.g. `max_flow_per_section` → `rated_flow_per_section` |
| `DanfossHackathon/data/` | `catalog.json`, uploads (`files/`), page text (`extracted/`), facts CSVs (`repository/`) |
| `DanfossHackathon/tests/` | 138 tests, fake AI, temporary data |
| `archive/` | earlier versions kept for reference: Streamlit prototype, mock-only website copy, Streamlit library page |
