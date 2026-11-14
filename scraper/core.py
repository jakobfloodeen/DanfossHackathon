"""Public-web source discovery with bounded, topic-directed traversal. No AI calls."""
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import PurePosixPath
from threading import RLock
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
import ipaddress
import re
import socket
import httpx

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 12            # HTML pages crawled per search
MAX_DEPTH = 3
MAX_SECONDS = 120          # pages rendered in a headless browser (below) take a few seconds each
MAX_SITEMAP_FETCHES = 3   # robots.txt / sitemap files read when crawling finds nothing relevant
ENOUGH_DOCUMENTS = 15     # stop early once this many relevant documents were found
CACHE_TTL = 900
CACHE_ITEMS = 32
CACHE_MAX_ITEM = 5 * 1024 * 1024   # big downloads are not kept in memory
_CACHE = OrderedDict()
_LOCK = RLock()

# What each choice in the website's "Look for" menu means. Links that mention the chosen component are
# followed first; links that ONLY mention another component (e.g. "Pumps" when looking for valves) are skipped.
# Websites are often not in English (e.g. boschrexroth.com/da/dk/), so the words also include common
# Danish/German/French/Spanish/Italian terms (Mattias).
TOPICS = {
    'valves': ('valve', 'styreblok', 'ventil', 'ventile', 'ventiler', 'valvula', 'vanne', 'valvola', 'directional', 'proportional', 'control block', 'control-block', 'spool', 'cartridge',
               'sectional', 'monoblock', 'mono block', 'load sensing', 'load-sensing', 'flow control',
               'pressure relief'),
    'pumps': ('pump', 'pumpe', 'pumpen', 'pompe', 'bomba', 'pompa', 'piston pump', 'gear pump', 'axial piston', 'axial-piston', 'variable displacement',
              'open circuit', 'open-circuit', 'closed circuit', 'closed-circuit'),
    'motors': ('motor', 'moteur', 'motore', 'orbital', 'geroler', 'gerotor', 'radial piston', 'radial-piston', 'travel motor',
               'swing motor', 'wheel motor'),
    'steering': ('steering', 'orbitrol', 'steering unit', 'priority valve', 'steer-by-wire'),
}
GENERIC_WORDS = ('product', 'catalog', 'catalogue', 'download', 'technical', 'documents', 'documentation',
                 'literature', 'media', 'datasheet', 'data sheet', 'series', 'mobile', 'hydraulic',
                 'produkt', 'produit', 'producto', 'prodott', 'katalog', 'hent', 'teknisk', 'technisch',
                 'data-sheet', 'datablad', 'datenblatt', 'brochure', 'dokument', 'hydraulik')
SKIP_WORDS = ('privacy', 'slavery', 'gender-pay', 'cookie', 'terms-and-conditions', 'careers', 'login', 'logout',
              'imprint', 'legal', 'press', 'news', 'event', 'investor', 'jobs', 'contact', 'linkedin', 'facebook',
              'youtube', 'twitter', 'instagram', 'mailto:', 'tel:')
DOC_LABEL = re.compile(r'\b(pdf|datasheet|data sheet|datablad|datenblatt|download|brochure|catalog(ue)?|leaflet|technical information|'
                       r'product information|technical data|manual|specification)\b')
DOC_PATH = re.compile(r'(\.pdf$|/media/|/download|/documents?/|/files?/|/assets?/|/dam/|mediadirectory|getdocument|'
                      r'/literature/)')
SPEC_VALUE = re.compile(r'\d[\d.,]*\s?(bar|psi|mpa|l/min|gpm|cm3|cm³|cc/rev|rpm|min-1|mm|kg|°c|kw)\b', re.I)
# Many manufacturer sites (e.g. boschrexroth.com) build their menus and product lists with JavaScript,
# so the raw page the server sends holds almost no links. If Playwright is installed, such pages are
# opened in a real (headless) browser and the finished page is read instead. Without Playwright
# nothing changes: the scraper behaves as before.
RENDER_IF_LINKS_BELOW = 15
RENDER_IF_BYTES_ABOVE = 20000
MEDIA_LINK = re.compile(r'/media/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)


DEEP_LINKS_JS = """() => { const out = []; const walk = (root) => { root.querySelectorAll('*').forEach(el => {
  if (el.tagName === 'A' && el.href) out.push([(el.innerText || el.textContent || '').trim().slice(0, 80), el.href]);
  if (el.shadowRoot) walk(el.shadowRoot); }); }; walk(document); return out; }"""


def browser_available():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def render_page(url, timeout_ms=30000):
    """Open the page in a headless browser and return the finished HTML as bytes."""
    from playwright.sync_api import sync_playwright
    url = validate_url(url)

    def only_public(route):                     # same public-address rule as the normal downloads
        try:
            validate_url(route.request.url)
            route.continue_()
        except ValueError:
            route.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(user_agent='DanfossHackathon-DatasheetImporter/2.0')
            page.route('**/*', only_public)
            page.goto(url, wait_until='networkidle', timeout=timeout_ms)
            for _ in range(3):                  # scroll so lazy-loaded lists appear
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(500)
            html = page.content()
            # Links inside web components (shadow DOM) are not part of the page HTML, so add them as plain links.
            deep = page.evaluate(DEEP_LINKS_JS)
            seen_href, extra = set(), []
            for label, href in deep[:4000]:
                if href not in seen_href and href.startswith(('http://', 'https://')):
                    seen_href.add(href)
                    extra.append('<a href="%s">%s</a>' % (href.replace('"', '%22'),
                                                          label.replace('<', ' ').replace('>', ' ')))
            return (html + '<div>' + ' '.join(extra) + '</div>').encode('utf-8')
        finally:
            browser.close()


def validate_url(url):
    p = urlsplit(url.strip())
    if (p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password
            or p.port not in (None, 80 if p.scheme == 'http' else 443)):
        raise ValueError('Use a public HTTP or HTTPS URL with its standard port and no credentials.')
    host = p.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('Local and private network addresses are blocked.')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError('Local and private network addresses are blocked.')
    return urlunsplit((p.scheme, p.netloc, p.path or '/', p.query, ''))


def public_address(url):
    p = urlsplit(url)
    try:
        addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
            p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise ValueError('Could not resolve this website address.') from exc
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('The website resolves to a private or reserved network address.')
    return sorted(addresses, key=lambda a: ':' in a)[0]


def fetch(url):
    url = validate_url(url)
    with _LOCK:
        cached = _CACHE.get(url)
        if cached and monotonic() - cached[0] < CACHE_TTL:
            _CACHE.move_to_end(url)
            return (*cached[1], True)
    current = url
    started = monotonic()
    # Pin the validated address for the connection; retain the hostname for TLS
    # verification/SNI and Host. Redirects undergo the same checks. Environment
    # proxies are disabled so they cannot bypass the destination validation.
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            address = public_address(current)
            p = urlsplit(current)
            host = f'[{address}]' if ':' in address else address
            pinned = urlunsplit((p.scheme, host, p.path, p.query, ''))
            with client.stream('GET', pinned, headers={
                'Host': p.netloc, 'Connection': 'close', 'User-Agent': 'DanfossHackathon-DatasheetImporter/2.0'
            }, extensions={'sni_hostname': p.hostname}) as response:
                if response.is_redirect:
                    current = validate_url(urljoin(current, response.headers['location']))
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('Source exceeds the 50 MB download limit.')
                    if monotonic() - started > 45:
                        raise ValueError('Download took too long. Try a direct document URL.')
                    chunks.append(chunk)
                result = (b''.join(chunks), current, response.headers.get('content-type', ''))
                if len(result[0]) <= CACHE_MAX_ITEM:
                    with _LOCK:
                        _CACHE[url] = (monotonic(), result)
                        _CACHE.move_to_end(url)
                        while len(_CACHE) > CACHE_ITEMS:
                            _CACHE.popitem(last=False)
                return (*result, False)
    raise ValueError('Too many redirects.')


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.text = [], []
        self.heading = ''
        self.heading_parts = None
        self.anchor = None
        self.layout_depth = 0
        self.ignore_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.ignore_depth += 1
        if tag in ('nav', 'header', 'footer'):
            self.layout_depth += 1
        if self.ignore_depth:
            return
        attrs = dict(attrs)
        if tag in ('h1', 'h2', 'h3') and not self.layout_depth:
            self.heading_parts = []
        if tag == 'a' and attrs.get('href'):
            self.anchor = {'href': attrs['href'], 'product': self.heading,
                           'parts': [], 'layout': bool(self.layout_depth)}
        if tag in ('p', 'div', 'tr', 'h1', 'h2', 'h3', 'li', 'br'):
            self.text.append('\n')
        elif tag in ('td', 'th'):
            self.text.append(' | ')

    def handle_data(self, data):
        if self.ignore_depth:
            return
        if not self.layout_depth:
            self.text.append(data)
        if self.heading_parts is not None:
            self.heading_parts.append(data)
        if self.anchor is not None:
            self.anchor['parts'].append(data)

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
        if self.ignore_depth:
            return
        if tag in ('nav', 'header', 'footer'):
            self.layout_depth = max(0, self.layout_depth - 1)
        if tag in ('h1', 'h2', 'h3') and self.heading_parts is not None:
            self.heading = ' '.join(' '.join(self.heading_parts).split())
            if tag == 'h1' and not getattr(self, 'title', ''):
                self.title = self.heading
            self.heading_parts = None
        if tag == 'a' and self.anchor is not None:
            self.anchor['label'] = ' '.join(' '.join(self.anchor.pop('parts')).split())
            self.links.append(self.anchor)
            self.anchor = None

    def readable_text(self):
        return '\n'.join(line for line in (' '.join(s.split()) for s in ''.join(self.text).splitlines()) if line)


def parse_html(data):
    page = PageLinks()
    page.feed(data.decode('utf-8', errors='replace'))
    return page


def topic_words(topic: str) -> tuple:
    """The words for one menu choice ('valves' -> valve, directional, ...). Unknown topics use their own words."""
    key = (topic or '').strip().lower()
    return TOPICS.get(key) or TOPICS.get(key + 's') or tuple(re.findall(r'[\w-]{3,}', key))


def relevance(text: str, topic: str) -> int:
    """> 0: about the chosen component; 0: neutral (e.g. 'Products', 'Downloads'); < 0: only about another one."""
    text = text.lower()
    own = topic_words(topic)
    score = sum(10 for w in own if w in text)
    if not score:
        others = [w for t, words in TOPICS.items() if words != own for w in words if w in text]
        # 'priority valve' is steering, 'valve' is valves: a word of the chosen topic inside another topic's word
        if others:
            return -10
    return score + sum(2 for w in GENERIC_WORDS if w in text)


def site_key(host: str) -> str:
    """'apps.boschrexroth.com' and 'www.boschrexroth.com' -> 'boschrexroth.com' (same company website).
    Handles two-part country endings like 'example.co.uk'."""
    parts = (host or '').lower().rstrip('.').split('.')
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ('co', 'com', 'ac', 'org', 'net', 'gov', 'or', 'ne'):
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])


def _is_document_link(link: dict, target: str) -> bool:
    path = urlsplit(target).path.lower()
    return not link['layout'] and (bool(DOC_PATH.search(path)) or bool(DOC_LABEL.search(link['label'].lower())))


def _document(target: str, link: dict, score: int) -> dict:
    path = urlsplit(target).path
    is_pdf = path.lower().endswith('.pdf') or bool(MEDIA_LINK.search(target))   # Rexroth: /media/<id> is the PDF
    name = (PurePosixPath(path).name if path.lower().endswith('.pdf')
            else (link['label'] or PurePosixPath(path).name or 'document'))
    return {'url': target, 'name': name, 'product': link['product'], 'label': 'PDF' if is_pdf else 'Document link',
            'kind': 'pdf' if is_pdf else 'auto', 'relevance': score}


def _sitemap_urls(root: str, budget: list) -> list:
    """URLs listed in the site's sitemap (robots.txt 'Sitemap:' lines, else /sitemap.xml). Never raises."""
    found, queue = [], []
    base = urlunsplit((urlsplit(root).scheme, urlsplit(root).netloc, '/', '', ''))
    try:
        budget[0] -= 1
        robots, _, _, _ = fetch(urljoin(base, '/robots.txt'))
        queue = re.findall(r'(?im)^\s*sitemap:\s*(\S+)', robots.decode('utf-8', errors='replace'))
    except Exception:
        pass
    queue = queue or [urljoin(base, '/sitemap.xml')]
    while queue and budget[0] > 0:
        url = queue.pop(0)
        if url.lower().endswith('.gz'):
            continue
        try:
            budget[0] -= 1
            data, _, _, _ = fetch(url)
        except Exception:
            continue
        locs = re.findall(r'<loc>\s*([^<\s]+)\s*</loc>', data.decode('utf-8', errors='replace'))
        if b'<sitemapindex' in data[:2000]:
            queue += [u for u in locs if not u.lower().endswith('.gz')][:5]   # a list of sitemaps: open a few
        else:
            found += locs
    return found


def discover(url: str, topic: str = 'pumps') -> dict:
    """Return candidate sources and crawl diagnostics; do not persist anything.
    Works for any manufacturer website: follows links about the chosen component (`topic`: valves, pumps,
    motors, steering) on the same company's websites, collects document links (PDFs, downloads, media links),
    and, when the crawled pages had nothing relevant, reads the site's sitemap and crawls the pages it lists
    for that component."""
    url = validate_url(url)
    visited, seen = set(), set()
    documents, warnings = [], []
    state = {'start': monotonic(), 'cached': True, 'site': None, 'limited': False}

    def add_document(doc):
        if doc['url'] not in seen and doc['relevance'] >= 0:
            seen.add(doc['url'])
            documents.append(doc)

    def relevant_count():
        return sum(1 for d in documents if d['relevance'] > 0)

    def crawl(queue, max_pages):
        pages = 0
        while queue and pages < max_pages and monotonic() - state['start'] < MAX_SECONDS \
                and relevant_count() < ENOUGH_DOCUMENTS:
            queue.sort(key=lambda item: -item[0])
            _, depth, current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            pages += 1
            try:
                visit(current, depth, queue)
            except Exception as exc:
                if len(visited) == 1:
                    raise
                warnings.append(f'Could not read {current}: {exc}')
        state['limited'] |= bool(queue)

    def visit(current, depth, queue):
        data, final, content_type, cached = fetch(current)
        state['cached'] &= cached
        if state['site'] is None:
            state['site'] = site_key(urlsplit(final).hostname)
        if depth and site_key(urlsplit(final).hostname) != state['site']:
            warnings.append('A page redirected outside the starting website; traversal stopped for that link.')
            return
        if data.startswith(b'%PDF-'):                           # the URL itself is a document
            name = PurePosixPath(urlsplit(final).path).name or 'datasheet.pdf'
            add_document({'url': final, 'name': name, 'product': '', 'label': 'PDF', 'kind': 'pdf',
                          'relevance': max(relevance(final, topic), 1 if depth == 0 else 0)})
            return
        if 'html' not in content_type.lower():
            warnings.append('A page did not contain HTML or a PDF.')
            return
        page = parse_html(data)
        if len(page.links) < RENDER_IF_LINKS_BELOW and len(data) > RENDER_IF_BYTES_ABOVE and browser_available():
            try:                                                 # menus built by JavaScript: read the finished page
                data = render_page(final)
                page = parse_html(data)
            except Exception as exc:
                warnings.append(f'Could not render {current} in a browser: {exc}')
        title = getattr(page, 'title', '')
        page_score = relevance(final + ' ' + title, topic)
        for link in page.links:
            try:
                target = validate_url(urljoin(final, link['href']))
            except ValueError:
                continue
            text = unquote(link['label'] + ' ' + target).lower()
            if any(t in text for t in SKIP_WORDS):
                continue
            if _is_document_link(link, target):
                # a document on a page about the chosen component counts as relevant even if its own link
                # just says "Download"
                doc_score = relevance(f"{text} {link['product']}", topic)
                add_document(_document(target, link, doc_score if doc_score else (1 if page_score > 0 else 0)))
                continue
            score = relevance(text, topic)
            if score > 0 and depth < MAX_DEPTH and site_key(urlsplit(target).hostname) == state['site'] \
                    and target not in visited:
                queue.append((score, depth + 1, target))
        # page text only when the page itself states values (a spec table), not for marketing pages
        text = page.readable_text()
        if (len(text) >= 200 and SPEC_VALUE.search(text) and (page_score > 0 or depth == 0)
                and final not in seen):
            seen.add(final)
            documents.append({'url': final, 'name': f"Web page: {title or final}"[:120], 'product': title,
                              'label': 'HTML', 'kind': 'html', 'relevance': max(page_score, 1)})

    crawl([(1000, 0, url)], MAX_PAGES)

    if not relevant_count() and state['site'] and monotonic() - state['start'] < MAX_SECONDS:
        budget = [MAX_SITEMAP_FETCHES]
        pages = []
        for loc in _sitemap_urls(url, budget):
            try:
                target = validate_url(loc)
            except ValueError:
                continue
            score = relevance(unquote(target), topic)
            if score <= 0 or site_key(urlsplit(target).hostname) != state['site'] or target in visited:
                continue
            if DOC_PATH.search(urlsplit(target).path.lower()):
                add_document(_document(target, {'label': '', 'product': '', 'layout': False}, score))
            else:
                pages.append((score, MAX_DEPTH, target))          # crawl these, but no deeper
        if budget[0] < MAX_SITEMAP_FETCHES:
            warnings.append('No relevant documents on the crawled pages; the site map was searched as well.')
        crawl(sorted(pages, reverse=True)[:30], MAX_PAGES // 2)

    documents.sort(key=lambda d: -d['relevance'])
    documents = resolve_documents(documents[:40])
    return {'documents': documents, 'pageUrl': url, 'cached': state['cached'], 'topic': topic,
            'pagesVisited': len(visited), 'limited': state['limited'], 'warnings': warnings[:5]}


def find_document_in_browser(url, timeout_ms=30000):
    """Open a JavaScript 'download' page in the headless browser and return the address of the real file
    (a PDF response, a started download, or a /media/<id> or .pdf link), or '' if none is found."""
    from playwright.sync_api import sync_playwright
    url = validate_url(url)
    files = []

    def only_public(route):
        try:
            validate_url(route.request.url)
            route.continue_()
        except ValueError:
            route.abort()

    def on_response(response):
        try:
            kind = response.headers.get('content-type', '').lower()
            if 'pdf' in kind or MEDIA_LINK.search(response.url):
                if 'html' not in kind and response.url not in files:
                    files.append(response.url)
        except Exception:
            pass

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(user_agent='DanfossHackathon-DatasheetImporter/2.0', accept_downloads=True)
            page.route('**/*', only_public)
            page.on('response', on_response)
            page.on('download', lambda d: files.append(d.url))
            try:
                page.goto(url, wait_until='networkidle', timeout=timeout_ms)
            except Exception:
                pass                              # a page that starts a file download aborts the normal load
            page.wait_for_timeout(3000)
            if len(files) < 12:
                try:
                    for label, href in page.evaluate(DEEP_LINKS_JS):
                        if (MEDIA_LINK.search(href) or urlsplit(href).path.lower().endswith('.pdf')) and href not in files:
                            files.append(href)
                except Exception:
                    pass
        finally:
            browser.close()
    return files[:12]


def download_source(url: str, _depth: int = 0) -> tuple[bytes, str, str, bool]:
    """Return (content, filename, final_url, cached): original PDF or UTF-8 text."""
    data, final, content_type, cached = fetch(url)
    name = PurePosixPath(urlsplit(final).path).name
    if data.startswith(b'%PDF-'):
        return data, name if name.lower().endswith('.pdf') else 'datasheet.pdf', final, cached
    if 'html' in content_type.lower():
        text = parse_html(data).readable_text()
        if len(text) < 200:
            if _depth == 0 and browser_available():
                candidates = []
                for t in find_document_in_browser(final):
                    for option in (t.split('?')[0], t):    # thumbnails (?derivative=...) -> try the original file first
                        if option != final and option not in candidates:
                            candidates.append(option)
                for target in candidates:          # use the first candidate that really is a PDF
                    try:
                        if fetch(target)[0].startswith(b'%PDF-'):
                            return download_source(target, _depth + 1)
                    except Exception:
                        continue
                if candidates:
                    raise ValueError('The page lists %d file(s) but none is a PDF: %s' % (len(candidates), '; '.join(candidates[:4])))
            raise ValueError('Too little readable text. This page may need JavaScript or login; upload its PDF instead.')
        safe_name = re.sub(r'[^\w.-]', '_', name)[:100] or 'web-page'
        return text.encode('utf-8'), safe_name + '.txt', final, cached
    raise ValueError('This link returned neither a PDF nor a readable HTML page.')


_RESOLVED = {}


def _is_pdf_address(address):
    """True when the address really serves a PDF (only the first bytes are read)."""
    try:
        validate_url(address)
        with httpx.stream('GET', address, follow_redirects=True, timeout=25,
                          headers={'User-Agent': 'DanfossHackathon-DatasheetImporter/2.0'}) as response:
            validate_url(str(response.url))
            for chunk in response.iter_bytes():
                return chunk.startswith(b'%PDF-')
    except Exception:
        pass
    return False


def _real_pdf_address(page_url):
    """Find the direct PDF address behind a JavaScript download page ('' when not found)."""
    if page_url in _RESOLVED:
        return _RESOLVED[page_url]
    found = ''
    try:
        for target in find_document_in_browser(page_url):
            for option in (target.split('?')[0], target):
                if _is_pdf_address(option):
                    found = option
                    break
            if found:
                break
    except Exception:
        found = ''
    if found:
        _RESOLVED[page_url] = found
    return found


def resolve_documents(documents, budget=150, workers=4):
    """Replace download-page entries with the direct PDF address, so the list links straight to the PDFs."""
    from concurrent.futures import ThreadPoolExecutor, wait
    todo = [d for d in documents if d.get('kind') == 'auto']
    if not todo or not browser_available():
        return documents
    pool = ThreadPoolExecutor(max_workers=workers)
    jobs = {pool.submit(_real_pdf_address, d['url']): d for d in todo}
    done, _pending = wait(jobs, timeout=budget)
    pool.shutdown(wait=False, cancel_futures=True)
    for job in done:
        document = jobs[job]
        try:
            address = job.result()
        except Exception:
            address = ''
        if address:
            document['url'] = address
            document['kind'] = 'pdf'
    seen, result = set(), []
    for document in documents:
        if document['url'] in seen:
            continue
        seen.add(document['url'])
        result.append(document)
    return result
