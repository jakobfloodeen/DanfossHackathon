# CLAUDE.md – project briefing for Claude Code

## What this is
A 2-day hackathon prototype (Danfoss × SDU G.E.A.R., Case 5: "AI-Powered Competitor Intelligence Assistant").
User = a Danfoss salesperson. Input: customer + the competitor product they use today (+ datasheets).
Output: the matching Danfoss product, a side-by-side technical comparison where **every value links to its
source page**, flags for missing/uncertain data, and a one-pager PDF for the customer meeting.

The jury cares most about: **accuracy, source traceability, transparent uncertainty.** Then: more products,
system-level comparison (pump + valve), and commercial insight. A small thing that is correct beats a big
thing that is sometimes wrong.

## Hard rules (never break these)
1. **Never guess or invent a technical value.** Not found → `null` / status `missing`.
2. **Every value carries its source:** document, page, exact quote. No source → it doesn't go in the table.
3. **Units are converted in code** (`compare.py`), never by the LLM.
4. **The LLM only explains rows it is given** and cites row ids like [R3]. No outside facts, no prices it wasn't given.
5. Keep it simple and demo-safe. Prefer boring, working code over clever code. The demo runs on stored data.
6. Never put API keys in code or commit `.env`.

## Architecture (pipeline)
Ingest (`db.add_document`: file → `static/docs/`, page text → SQLite) →
Extract (`extract.py` + `llm.extract_attributes`: LLM fills the schema with page + quote) →
Verify (quote must fuzzy-match the page text, else `unverified`) →
Normalize (`compare.normalize`) → Flag (`missing` / `unverified` / `conflict` / `calculated` / `manual`) →
Match (`compare.match`: hard filters + weighted closeness, top 3) →
Compare (`compare.compare`) → Explain (`llm.explain`) → One-pager (`onepager.py`).

## Files and owners (work mostly in your own files to avoid git conflicts)
| File | Purpose | Owner |
|---|---|---|
| `schema.py` | attribute list per product family (+ hints for the LLM). Add a family here. | P2 + P3 |
| `db.py` | the library: SQLite tables + stored files. **Data model lives here.** | P1 + P3 |
| `extract.py`, `llm.py` | AI extraction + quote verification, sales notes | P2 |
| `compare.py` | units, conflicts, calculated values, comparison, matching (pandas, no AI) | P3 |
| `app.py` | Streamlit UI: Compare page + Library page | P4 |
| `onepager.py` | PDF one-pager (fpdf2, latin-1 safe text via `safe()`) | P4 |
| `golden.csv`, `evaluate.py` | hand-checked truth → accuracy number for the pitch | P1 |
| **`comparison_engine/`** | **the comparison package the website imports** (no Streamlit/Flask inside). Main call: `analyse(query, question, customer_context)` → one `AnalysisResult` (`.to_dict()`). Also `search`, `shortlist`, `interpret`, `validate_csv`, `repository_report`. See "Comparison engine" below. | JL |
| `engine.py` | old entry point, now a thin wrapper around `comparison_engine` (old names keep working). `python engine.py examples/repository_demo` | JL |
| **`rules/_general.md`** | general comparison LOGIC for ANY product (template with `OTTO:` placeholders): YAML settings for code + text the AI follows | OH |
| **`rules/<family>.md`** | optional extra LOGIC per family (e.g. `pump.md`); overrides `_general.md` settings with the same name | OH |
| `tests/` | pytest tests for the engine (fake LLM, no API key needed): `pytest -q` | JL |
| `data/customers.csv`, `data/products.csv` | seed data loaded on first run (products.csv = DEMO, made up) | P5 / – |

**Shared contracts – don't change without telling the team:** the table columns in `db.py`,
the attribute names in `schema.py`, and `product_id` format `"Brand|Product name"`.

## How to run
```
source .venv/bin/activate
streamlit run app.py                     # the app (Compare + Library pages)
python extract.py FILE --brand X --family pump --product "NAME" [--product ...] [--url URL]
python extract.py FILE --brand X --family pump --no-ai     # store only
python evaluate.py                       # accuracy vs golden.csv
```
Delete `data/library.db` to rebuild the library from the seed CSVs (stored PDFs in static/docs stay).

Comparison engine (CSV repository):
```
pytest -q                                                           # all engine tests, no key needed
python -m comparison_engine examples/repository_demo "question" [--product Q] [--customer C] [--must attr=value] [--live]
REPO_DIR=examples/repository_demo streamlit run examples/demo_page.py   # example of how the website calls it
```

## Comparison engine (`comparison_engine/`)
Flow: `repository` (all CSVs in `REPO_DIR`, column names via ONE dict `config.COLUMNS`, auto-reload,
broken files skipped + reported) → `units` (code converts; text is never a number) → statuses
(`ok`/`manual`/`unverified`/`missing`/`conflict`/`calculated`/`unknown unit`) → `search` (fuzzy, every word
must match; empty → website offers PDF upload) → `shortlist` (Danfoss, same family; customer must-haves are
hard filters, LOGIC hints only rank) → `facts` (every value as F1, F2… + code-computed differences) →
`interpret` (ONE AI call with the LOGIC text; JSON answer in `guardrails.OUTPUT_SCHEMA`) → `guardrails`
(code checks ids, products, citations, numbers, verdicts; one retry; leftover problems stay visible) →
`results` (comparison rows + one-pager DATA; the UI renders).
- The PDF→CSV converter should call `validate_csv(df)` before saving a file into the repository.
- No key / API error / timeout → `status="no_ai"`: facts + code differences still returned.
- AI only on explicit user action (`use_ai=True`); answers cached in `.cache/interpret/` by input hash.
- No `temperature`: the installed `anthropic` SDK (2.x) rejects it and Sonnet 5.5 only allows the default.
  Consistency comes from the JSON schema, fixed prompts and the cache. Env: `LLM_MODEL`, `LLM_EFFORT`,
  `LLM_TIMEOUT`, `REPO_DIR`, `RULES_DIR`, `CACHE_DIR`, `DOCS_URL_PREFIX`.

## Environment
Python 3.13 venv in `.venv`, macOS. Packages in `requirements.txt`. LLM = Anthropic API
(`ANTHROPIC_API_KEY` and `LLM_MODEL` in `.env`). Streamlit static serving is on (`.streamlit/config.toml`) so
stored PDFs open at `/app/static/docs/<file>#page=N`.

## Working style for Claude
- Small steps. After each change, run the relevant script or `streamlit run app.py` and check it works.
- Explain changes briefly in plain language – the team are 3rd-semester students and must be able to explain
  every part to the jury.
- Don't rewrite files wholesale; edit what's needed.
