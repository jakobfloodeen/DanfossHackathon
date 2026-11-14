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
MAX_PAGES = 6
MAX_DEPTH = 2
CACHE_TTL = 900
_CACHE = OrderedDict()
_LOCK = RLock()
PUMPS_URL = "https://www.kawasakihydraulics.com/type/axial-piston-pumps/"


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
                with _LOCK:
                    _CACHE[url] = (monotonic(), result)
                    _CACHE.move_to_end(url)
                    while len(_CACHE) > 4:
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


def discover(url, topic='pumps'):
    url = validate_url(url)
    if urlsplit(url).hostname in ('kawasakihydraulics.com', 'www.kawasakihydraulics.com') and urlsplit(url).path == '/':
        url = PUMPS_URL
    queue, visited, seen = [(100, 0, url)], set(), set()
    documents, warnings = [], []
    start, all_cached = monotonic(), True
    root_host = None
    terms = re.findall(r'[\w-]{2,}', topic.lower())
    while queue and len(visited) < MAX_PAGES and monotonic() - start < 60:
        queue.sort(key=lambda item: -item[0])
        _, depth, current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            data, final, content_type, cached = fetch(current)
            all_cached &= cached
            if root_host is None:
                root_host = urlsplit(final).hostname
            if depth and urlsplit(final).hostname != root_host:
                warnings.append('A page redirected outside the starting website; traversal stopped for that link.')
                continue
            if data.startswith(b'%PDF-'):
                if final not in seen:
                    seen.add(final)
                    documents.append({'url': final, 'name': PurePosixPath(urlsplit(final).path).name or 'datasheet.pdf', 'product': '', 'label': 'PDF', 'kind': 'pdf'})
                continue
            if 'html' not in content_type.lower():
                warnings.append('A page did not contain HTML or a PDF.')
                continue
            page = parse_html(data)
            found_pdf = False
            candidates = []
            for link in page.links:
                try:
                    target = validate_url(urljoin(final, link['href']))
                except ValueError:
                    continue
                text = unquote(link['label'] + ' ' + target).lower()
                if any(t in text for t in ('privacy', 'slavery', 'gender-pay', 'cookie', 'terms-and-conditions', 'careers', 'login', 'logout')):
                    continue
                pdf = urlsplit(target).path.lower().endswith('.pdf') or bool(re.search(r'\b(pdf|datasheet|data sheet)\b', link['label'].lower()))
                if pdf and not link['layout']:
                    found_pdf = True
                    if target not in seen:
                        seen.add(target)
                        documents.append({'url': target, 'name': PurePosixPath(urlsplit(target).path).name if urlsplit(target).path.lower().endswith('.pdf') else link['label'],
                                          'product': link['product'], 'label': 'Document link', 'kind': 'auto'})
                    continue
                score = sum(10 for term in terms if term in text)
                score += sum(2 for term in ('product', 'catalog', 'download', 'technical') if term in text)
                if score and depth < MAX_DEPTH and urlsplit(target).hostname == root_host and target not in visited:
                    candidates.append((score, depth + 1, target))
            # Let users explicitly import readable HTML where no PDF is exposed.
            if not found_pdf and len(page.readable_text()) >= 200 and final not in seen:
                seen.add(final)
                documents.append({'url': final, 'name': 'Web page text (review relevance)',
                                  'product': '', 'label': 'HTML', 'kind': 'html'})
            # Stop here when documents are already available on this page.
            if not found_pdf:
                queue.extend(sorted(candidates, reverse=True)[:10])
        except Exception as exc:
            if len(visited) == 1:
                raise
            warnings.append(f'Could not read {current}: {exc}')
    return {'documents': documents[:40], 'pageUrl': url, 'cached': all_cached,
            'pagesVisited': len(visited), 'limited': bool(queue), 'warnings': warnings[:5]}


def download_pdf(url):
    data, final, _, cached = fetch(url)
    if not data.startswith(b'%PDF-'):
        raise ValueError('The selected source did not return a PDF.')
    name = PurePosixPath(urlsplit(final).path).name
    return data, name if name.lower().endswith('.pdf') else 'datasheet.pdf', final, cached


def download_source(url):
    data, final, content_type, cached = fetch(url)
    name = PurePosixPath(urlsplit(final).path).name
    if data.startswith(b'%PDF-'):
        return data, name if name.lower().endswith('.pdf') else 'datasheet.pdf', final, cached
    if 'html' in content_type.lower():
        text = parse_html(data).readable_text()
        if len(text) < 200:
            raise ValueError('Too little readable text. This page may need JavaScript or login; upload its PDF instead.')
        safe_name = re.sub(r'[^\w.-]', '_', name)[:100] or 'web-page'
        return text.encode('utf-8'), safe_name + '.txt', final, cached
    raise ValueError('This link returned neither a PDF nor a readable HTML page.')
