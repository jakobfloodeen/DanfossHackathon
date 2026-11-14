"""Public-only HTTP transport, isolated from the product scraper and application."""
from collections import OrderedDict
from threading import RLock
from time import monotonic
from urllib.parse import urljoin, urlsplit, urlunsplit
import ipaddress
import socket
import httpx

MAX_BYTES = 50 * 1024 * 1024
CACHE_TTL = 900
_CACHE = OrderedDict()
_LOCK = RLock()


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


def fetch(url, allowed_hosts=None):
    url = validate_url(url)
    if allowed_hosts is not None and urlsplit(url).hostname not in allowed_hosts:
        raise ValueError("URL is outside the approved customer hosts.")
    with _LOCK:
        cached = _CACHE.get(url)
        if cached and monotonic() - cached[0] < CACHE_TTL and (allowed_hosts is None or urlsplit(cached[1][1]).hostname in allowed_hosts):
            _CACHE.move_to_end(url)
            return (*cached[1], True)
    current = url
    started = monotonic()
    # Pin the validated address for the connection; retain the hostname for TLS
    # verification/SNI and Host. Redirects undergo the same checks. Environment
    # proxies are disabled so they cannot bypass the destination validation.
    with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            if allowed_hosts is not None and urlsplit(current).hostname not in allowed_hosts:
                raise ValueError("Redirect leaves the approved customer hosts.")
            address = public_address(current)
            p = urlsplit(current)
            host = f'[{address}]' if ':' in address else address
            pinned = urlunsplit((p.scheme, host, p.path, p.query, ''))
            with client.stream('GET', pinned, headers={
                'Host': p.netloc, 'Connection': 'close', 'User-Agent': 'DanfossHackathon-CustomerResearch/1.0'
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
