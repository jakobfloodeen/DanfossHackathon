# CLAUDE.md – project briefing for Claude Code

## What this is
Hackathon prototype (Danfoss × SDU G.E.A.R., Case 5: AI competitor-intelligence assistant for salespeople).
One app in `DanfossHackathon/`: website (`frontend/`) + FastAPI (`backend.py`) + `comparison_engine/`.
The jury cares most about **accuracy, source traceability, honest uncertainty**. See `README.md` for flows,
folders and the PDF→CSV contract.

## Hard rules
1. Never guess or invent a technical value. Not stated → status `missing`, value empty.
2. Every value carries document, page and exact quote; `ok` only if `verify_quote()` finds the quote on the page.
3. Units are converted in code (`comparison_engine/units.py`), never by the AI.
4. The AI only interprets numbered facts and cites their ids; `guardrails.py` checks every answer.
5. Keep it simple and demo-safe; the app must work without an API key.
6. Never put keys in code or commit `.env`.

## Run / test
```
cd DanfossHackathon && source .venv/bin/activate
uvicorn backend:app --port 8000          # website at http://localhost:8000
pytest -q                                # all tests, fake AI, no key needed
python -m comparison_engine data/repository "question" --product pvg32   # engine in the terminal
```

## Teammates' tools (use them unchanged)
- `pdf_to_csv_test.py` (repo root): PDF → facts; called through `comparison_engine/bridge.py` (`POST /api/extract`).
- `scraper/` (repo root): datasheet discovery/download; called by `/api/web/discover` and `/api/web/import`.

## Shared contracts (tell the team before changing)
- Facts CSV columns (`comparison_engine/config.py` → `COLUMNS`) and `product_id` = website catalog id.
- Website JSON shapes: `frontend/README.md` and `frontend/examples/*.json`.
- LOGIC files `rules/*.md` belong to Otto (YAML keys are read by code).

## Working style
Small steps, run the tests after each change, explain changes in plain language (the team are
3rd-semester students and must be able to explain every part to the jury).
