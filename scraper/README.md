# Standalone scraper

Copy the whole `scraper` folder into the repository root. Adding the folder does
not change or start the existing app. Integration can wait until the CSV converter
is ready. Python 3.11+ is required; HTTPX is the only third-party dependency.
There are no AI calls, API keys, UI files, database writes, or CSV conversion here.

## Install and test

Run from the repository root, using your project's Python environment:

```powershell
py -m pip install -r scraper/requirements.txt
py -m unittest discover -s scraper/tests -v
```

## Interface for the CSV-conversion developer

```python
from scraper import discover, download_source

# 1. Discover candidates; present these to the user or apply your selection logic.
result = discover("https://www.kawasakihydraulics.com/", topic="valves")   # any manufacturer's site
candidates = result["documents"]

# 2. Once your app selects a source, call:
# content, filename, source_url, cached = download_source(selected_url)
#
# 3. Pass content and filename to your converter. Keep source_url with its output.
# The converter owns PDF parsing, product identification, and the CSV schema.
```

`discover(url, topic="pumps")` works on any manufacturer's website. `topic` is the website's "Look for" choice
(`valves`, `pumps`, `motors`, `steering`; other words are used as given) and steers the crawl:

- links about the chosen component are followed first; links only about another component are skipped
  (e.g. "Pumps" when looking for valves). The words per component are in `TOPICS` in `core.py`.
- document links: `.pdf` addresses, links labelled PDF / data sheet / download / brochure / catalog / technical
  information, and download-style addresses (`/media/`, `/download`, `/documents/`, `/dam/` ...).
  `download_source` still checks that the bytes really are a PDF.
- other websites of the same company are followed too (`apps.maker.com` from `www.maker.com`, see `site_key`).
- when the crawled pages hold nothing relevant (e.g. pages built by JavaScript), the site map (`robots.txt` /
  `sitemap.xml`) is read and the pages it lists for the chosen component are crawled.
- web-page text is only offered when the page states values (bar, l/min, mm ...), not for marketing pages.
- limits: `MAX_PAGES` (12) pages + half that from the site map, `MAX_SECONDS` (60), results ranked by relevance.

Not possible without a real browser: content that only appears after JavaScript runs, and pages behind a login.

It returns a JSON-serializable dictionary:

| Field | Meaning |
|---|---|
| `documents` | Candidate sources, most relevant first, possibly empty. Each has `url`, `name`, `product`, `label`, `kind`, `relevance`. |
| `documents[].kind` | `pdf`: detected PDF bytes; `html`: readable page; `auto`: document link whose content still needs checking. |
| `documents[].product` | Nearby heading as an unverified suggestion; may be empty. |
| `pageUrl` | Starting URL after any supported shortcut. |
| `pagesVisited` | Number of attempted discovery URLs. |
| `limited` | More queued pages remained when discovery stopped. |
| `cached` | Whether all successfully fetched discovery responses came from cache. |
| `warnings` | Problems on later pages; successful discoveries remain available. |

`download_source(selected_url)` returns a tuple:

| Position | Type | Meaning |
|---|---|---|
| `content` | `bytes` | Original PDF bytes, or readable HTML converted to UTF-8 text. |
| `filename` | `str` | Suggested `.pdf` or `.txt` filename. |
| `source_url` | `str` | Final source URL after redirects. Retain it for provenance. |
| `cached` | `bool` | Whether the response was reused from the in-memory cache. |

PDF text extraction and page references belong in the downstream converter.
HTML text snapshots have no PDF page numbers and do not preserve full table
structure or layout. They may need a different converter from PDFs. Neither
function claims that two products are equivalent or that a value is verified.
The module returns data in memory and writes no files. If the consuming app saves
documents, it should generate its own unique storage name rather than treating
the suggested filename as a unique product identifier.

Invalid destinations and unsupported content raise `ValueError`. HTTP/network
failures can raise `httpx.HTTPError`. A failure on the starting URL propagates to
the caller; later-page failures appear in `warnings`. Handle these at your app boundary.
These are synchronous functions; an async website should run them in a worker
thread so downloads do not block its event loop.

## Behavior and limits

- Follows topic-ranked links within the starting hostname, up to six pages and
  two link levels. Public external PDF/document links can be selected and downloaded.
  PDF-looking links are candidates until downloaded; discovery does not validate each one.
- Stops queueing further traversal from a page when it exposes document links.
  This is a bounded discovery helper, not a complete site inventory.
- Stops starting requests after a 60-second search budget; the current request
  may finish later. Each download is limited to 50 MB. DNS resolution uses OS timeouts.
- Retains the Kawasaki homepage-to-pump-category shortcut; other domains use
  generic keyword-ranked traversal without supplier-specific configuration.
- Caches up to four responses for 15 minutes in memory. There is no persistent cache.
- Blocks private/reserved addresses and checks redirects and DNS answers.
  Connections use a checked public IP while retaining the original TLS hostname.
  Environment proxies are disabled; proxy-only networks need separate integration.
- Reads server-provided HTML. JavaScript rendering, login, anti-bot bypass, OCR,
  and automated robots.txt handling are not implemented. Review site permissions
  before expanding this into unattended bulk crawling.

Generic discovery can miss documents or suggest irrelevant content. Prefer a
specific category/product URL and validate the selected source. The module does
not guarantee coverage of every manufacturer website.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Exposes the two public functions. |
| `core.py` | Retrieval, URL checks, discovery, and HTML text extraction. |
| `requirements.txt` | HTTPX dependency. |
| `tests/test_core.py` | Isolated tests; no backend imports or paid API calls. |

Tests use synthetic pages and mocked HTTP responses. Live verification of the
general transport was blocked by DNS restrictions in the development workspace.
Run the intended manufacturer URLs on your own machine before demonstration.
