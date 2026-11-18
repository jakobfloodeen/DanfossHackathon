# Customer research scraper

Standalone evidence collection for Danfoss sales preparation. Copy this whole
`customer_scraper` folder to the repository root. It does not activate or change
the website. It can run without the existing `scraper` folder or backend.

## Purpose

The product scraper supplies component documents. This module supplies excerpts
from a prospective customer's own public pages and reports. The consuming app can
use these to prepare questions and tailor a technically valid shortlist.

A plan to electrify equipment can justify investigating electric-control options.
An old expansion announcement may warrant a question about current requirements.
Company revenue or a factory investment cannot establish the budget for a pump.
Public announcements alone do not reveal purchasing behavior or willingness to pay.
The output deliberately leaves financial scope and project timing unverified.

Use customer research as optional evidence alongside the salesperson's confirmed
requirements. It should not override technical compatibility checks or silently
turn weak evidence into a product ranking. Validate its usefulness against a few
real customer briefs before expanding the crawler.

## Install and test

Python 3.11+. Run from the repository root using your project's Python environment:

```powershell
py -m pip install -r customer_scraper/requirements.txt
py -m unittest discover -s customer_scraper/tests -v
```

Dependencies: HTTPX for retrieval and pdfplumber for PDF text. No model, API key,
paid search service, database, CSV converter, or frontend is required. Source
websites still require internet access. Tests run offline.

## Public interface

```python
from customer_scraper import research_customer, build_llm_context

report = research_customer(
    "https://customer.example/",  # replace with an actual customer website
    company_name="Customer name",  # caller-provided identity
    topic="electrification hydraulic equipment",
    # Optional explicit customer URLs, including another approved host:
    seed_urls=["https://customer.example/projects"],
    max_pages=10,
)

# Feed this dictionary as source DATA to your future comparison/briefing logic.
customer_context = build_llm_context(report)

# Optional persistence belongs to your app:
# import json
# from pathlib import Path
# Path("customer-research.json").write_text(
#     json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
# )
```

Both functions return ordinary JSON-serializable dictionaries. They write no
files, modify no catalog, and call no LLM. `build_llm_context` copies the evidence
and handling rules into a separate object; it neither summarizes nor recommends
products. Call the synchronous research function in a worker thread if connecting
it to an async web app.

## Evidence contract

| Field | Meaning |
|---|---|
| `company` | Caller-supplied name and starting URL; ownership/identity remains caller-confirmed. |
| `sources` | Stable ID, final URL, title, content hash, source type, and access time. |
| `sources[].publication_date_raw` | Explicit HTML publication metadata, if available. Preserved as a raw value, without interpreting project dates. PDF publication date remains unknown. |
| `sources[].accessed_at` | Time this run read the response. Cached content may have been fetched up to 15 minutes earlier. |
| `evidence` | Normalized text excerpts with IDs, category tags, source IDs, and HTML block/PDF page references. |
| `evidence[].review_status` | Always `unverified_excerpt`; tags are keyword heuristics. |
| `evidence[].temporal_status` | Always `not_determined`; a past article containing “planned” can describe a project already completed. |
| `evidence[].financial_scope` | `not_determined` for financial excerpts. No budget is calculated, converted, or inferred. |
| `coverage` | Per-category evidence IDs, or `not_found_in_inspected_sources`. Missing evidence represents unknown information. |
| `warnings`, `crawl` | Failed/skipped pages, search limits, and scope inspected. |
| `external_links_not_followed` | Relevant links on unapproved hosts; review and explicitly add suitable customer URLs as seeds in a later run. |
| `usage_rules` | Guidance for downstream interpretation and citation. These rules still need enforcement by the consuming app. |

Categories cover projects, plans/timing, financial context, technical priorities,
operations/applications, and procurement. A single excerpt can have multiple tags.
A source heading is a hint; it is not a verified product preference or project name.
Numeric amounts remain in their original quoted context. Financial parsing and
project lifecycle resolution can be added downstream with human review.

HTML locators refer to normalized extracted blocks, not browser DOM selectors.
PDF references include 1-based page numbers. Excerpts are chunks of up to 1,800
characters and may contain partial sentences; consult the source before acting.
Table layout, financial column associations, and scanned PDFs require special care.
Source hashes help identify versions but do not preserve an archive; save original
responses separately if reproducible archival citations are required.

See `examples/fictional_report.json` for a synthetic output from fictional pages.
It is not research about a real customer.

## Boundaries and failure behavior

- Default: ten content URLs, two link levels, and a 60-second budget before starting
  another request. The current download/PDF parse can finish later. Separate
  robots.txt requests are counted by origin, outside the content-page count.
- Reads up to 40 pages per PDF by default (configurable to 100). HTML and PDF text
  extraction also have per-block/page limits listed in each source record.
  The evidence list defaults to 60 excerpts. This is a partial research pass.
- Reads only the requested hosts and their `www` counterparts. Explicit `seed_urls`
  approve additional hosts. Off-scope redirects are blocked before fetching their
  targets. Caller must confirm that supplied URLs belong to the intended customer.
- Checks robots.txt. A 404 permits access; a failed check or disallow skips the site.
  Sites requesting crawl pacing are skipped in this prototype. Reports expose gaps.
- Private/reserved IP addresses, DNS answers, and redirects are blocked. Requests
  use checked destination IPs and original TLS hostnames. Environment proxies are
  disabled, so proxy-only corporate networks require a separate integration.
- Individual downloads are capped at 50 MB. Four responses can be cached for 15
  minutes in memory. DNS resolution uses operating-system timeouts.
- English keyword heuristics drive discovery/tagging. Other languages, JS-rendered
  content, login, OCR, and anti-bot bypass are not implemented. There is no web-wide
  search or automatic company identification, and no completeness guarantee.
- Invalid inputs/limits raise `ValueError`. Page failures become warnings and any
  successful evidence is retained. Even a completely failed crawl returns a report
  with empty evidence: the consuming app must inspect warnings and coverage.

The downstream LLM must treat scraped text as untrusted evidence, not instructions.
Preserve source citations, distinguish explicit statements from hypotheses, and
verify a customer's current requirements before using any inferred priorities.

## Isolation and future integration

`_transport.py` reuses the product scraper's retrieval approach in a self-contained
copy. Keeping it here lets the folder work independently today. Once both modules
are adopted, a shared retrieval utility would avoid maintaining two copies.

This folder has no UI or storage migrations. No git commit or push was performed.
Tests cover synthetic sites, actual extraction of a small generated PDF, robots
rules, destination controls, and the JSON handoff. Live customer-site coverage has
not been validated in this workspace; validate your intended URLs locally.

## Rights reservations (terms.py, no AI)

Before anything is kept, `research_customer(..., check_terms=True)` checks whether the site owner reserves the
site against automated reading / text and data mining (EU DSM Directive art. 4(3); in Denmark ophavsretsloven
§ 11 b). What STOPS the read - it covers what this tool does: TDMRep (`/.well-known/tdmrep.json`,
`<meta name="tdm-reservation" content="1">`), and a sentence on the terms of use / legal notice / imprint / privacy
pages that forbids scraping, crawling, robots, automated access or data mining. The ordinary robots.txt rules for
our own user agent are followed as before. What is only REPORTED (in `report["terms"]["notes"]`) - it is aimed at
collecting AI training data, which this tool does not do: `<meta name="robots" content="noai">`, robots.txt blocks
of AI crawlers (GPTBot, ClaudeBot, CCBot ...), and terms sentences that only forbid training AI on the content.
A stop returns no evidence and `report["terms"]` names the page and the sentence. Not legal advice.
