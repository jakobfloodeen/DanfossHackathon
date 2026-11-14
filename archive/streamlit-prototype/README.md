# Competitor Intelligence Assistant (Danfoss Case 5)

Customer + competitor product in → matched Danfoss product, sourced comparison, one-pager PDF out.
Everything the assistant knows lives in **one library** that grows as you add documents and customers.

## Run it
```
cd ~/Desktop/"Claude Code "/hackathon
source .venv/bin/activate
streamlit run app.py
```
Two pages: **🔍 Compare** and **📚 Library**. It starts with DEMO data (made up, clearly marked) so everyone
can work in parallel. Remove it on the Library page once real datasheets are in.

## The library (how to expand the system)
| You want to… | Do this |
|---|---|
| Add a datasheet / brochure | Library → Documents → upload PDF, brand, family, URL, product sizes → *Add* |
| Add a web-shop page | Library → "Add a web page" → paste text + URL (no scraping needed) |
| Add a customer | Library → Customers → new row → *Save* |
| Record what a customer uses | Library → Customers → "What each customer uses" (Compare page then pre-selects it) |
| Fix a wrong value | Library → Products → edit, set status **manual** → *Save* |
| Find something in any document | Library → Search all pages |
| Add a product family (e.g. motor) | Add it to `schema.py` – nothing else changes |

Where things are stored:
- `static/docs/` – every original file (the app opens them at the cited page)
- `data/library.db` – SQLite database: documents, page texts, products, values (+ page + quote), customers

**Team rule:** only the *library keeper* (P1) commits `data/library.db` and `static/docs/`, to avoid git
conflicts on the database. Everyone else pulls.

From the Terminal (handy for many files):
```
python extract.py data/raw/danfoss_s45.pdf --brand Danfoss --family pump --product "S45 J 45" --product "S45 J 75" --url "https://..."
python extract.py data/raw/rexroth_a10vo.pdf --brand "Bosch Rexroth" --family pump --no-ai     # store only
```

## Comparison engine (the CSV repository flow)
The website imports `comparison_engine` – one call does everything:
```python
from comparison_engine import analyse, validate_csv
result = analyse("competitorx pump 71", "Best Danfoss replacement?", "OEM, truck-mounted cranes",
                 use_ai=True)          # use_ai only when the user clicks the AI button
result.status       # ok / ok_with_problems / no_ai / not_found (-> offer PDF upload) / needs_choice
result.to_dict()    # facts, comparison_rows (with source links), interpretation, problems, onepager data
```
Try it without the website:
```
pytest -q                                                    # tests, no API key needed
python -m comparison_engine examples/repository_demo "Which Danfoss pump replaces it?"          # free
python -m comparison_engine examples/repository_demo "Which Danfoss pump replaces it?" --live   # 1 AI call
REPO_DIR=examples/repository_demo streamlit run examples/demo_page.py
```
- **Repository:** every `*.csv` in `REPO_DIR` (default `./repository`) is loaded automatically; broken files
  are skipped and listed in `repository_report()`. The PDF→CSV converter checks a file with `validate_csv(df)`
  before saving it.
- **LOGIC files (Otto):** `rules/_general.md` for every product + optional `rules/<family>.md`.
  YAML part = numbers for the code (weights, better direction, tolerances); text part = instructions for the AI.
- **Guardrails:** code checks every AI answer (fact ids, numbers, verdicts, citations), retries once, and
  shows any remaining problems instead of hiding them.

## AI key (one time per laptop)
```
cp .env.example .env
open -e .env        # paste the key after ANTHROPIC_API_KEY=
```
`.env` is in `.gitignore` – never commit it.

## Accuracy number for the pitch
Fill `golden.csv` by hand (same `product_id` spelling, e.g. `Danfoss|S45 J 75`), then `python evaluate.py`.

## Status flags
`ok` quote verified on the page · `unverified` quote not found on that page · `missing` not in the source
(never guessed) · `conflict` two sources disagree > 2 % · `calculated` computed by us · `manual` corrected by a human
· `unknown unit` the unit isn't in our unit table, so the value can't be compared

See `CLAUDE.md` for architecture, rules and file owners.
