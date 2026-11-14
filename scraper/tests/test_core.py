import unittest
from unittest.mock import patch
import httpx
from scraper import core as w

class GeneralSourcesTests(unittest.TestCase):
    def setUp(self):
        w._CACHE.clear()

    def test_navigation_category_and_external_pdf(self):
        pages = {
            'https://maker.example/': b'<nav><a href="/pumps">Pumps</a><a href="/motors">Motors</a></nav>',
            'https://maker.example/pumps': b'<h1>Pump X</h1><a href="https://cdn.example/x.pdf">Datasheet</a>',
        }
        def fetch(url):
            return pages[url], url, 'text/html', False
        with patch.object(w, 'fetch', side_effect=fetch) as fetcher:
            result = w.discover('https://maker.example/', 'pumps')
        self.assertEqual(fetcher.call_count, 2)
        self.assertEqual(result['documents'][0]['url'], 'https://cdn.example/x.pdf')

    def test_page_limit_and_deduplication(self):
        html = ''.join(f'<a href="/pumps/{i}">Pumps {i}</a>' for i in range(20)).encode()
        with patch.object(w, 'fetch', side_effect=lambda url: (html, url, 'text/html', False)) as fetcher:
            result = w.discover('https://maker.example/')
        self.assertEqual(fetcher.call_count, 6)
        self.assertTrue(result['limited'])

    def test_one_child_failure_keeps_other_results(self):
        def fetch(url):
            if url.endswith('/broken'):
                raise ValueError('Unavailable')
            html = b'<a href="/broken">Pumps broken</a><a href="/working">Pumps working</a>' if url.endswith('/') else b'<a href="/x.pdf">Datasheet</a>'
            return html, url, 'text/html', False
        with patch.object(w, 'fetch', side_effect=fetch):
            result = w.discover('https://maker.example/')
        self.assertEqual(len(result['documents']), 1)
        self.assertTrue(result['warnings'])

    def test_html_snapshot_removes_script_and_navigation(self):
        html = b'<nav>unwanted navigation</nav><script>secret script</script><h1>Pump X</h1><table><tr><td>Pressure</td><td>280 bar</td></tr></table><p>' + b'Technical description. ' * 20 + b'</p>'
        with patch.object(w, 'fetch', return_value=(html, 'https://maker.example/x', 'text/html', False)):
            data, name, _, _ = w.download_source('https://maker.example/x')
            result = w.discover('https://maker.example/x')
        self.assertTrue(name.endswith('.txt'))
        self.assertIn(b'280 bar', data)
        self.assertNotIn(b'secret script', data)
        self.assertNotIn(b'unwanted navigation', data)
        self.assertEqual(result['documents'][0]['kind'], 'html')

    def test_extensionless_pdf(self):
        with patch.object(w, 'fetch', return_value=(b'%PDF-1.4 example', 'https://cdn.example/download?id=9', 'application/octet-stream', False)):
            self.assertEqual(w.download_source('https://cdn.example/download?id=9')[1], 'datasheet.pdf')

    def test_dns_mixed_public_and_private_rejected(self):
        records = [(2, 1, 6, '', ('93.184.216.34', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]
        with patch.object(w.socket, 'getaddrinfo', return_value=records):
            with self.assertRaises(ValueError):
                w.public_address('https://maker.example/')

    def test_connection_is_pinned_and_cross_host_redirect_validated(self):
        requests, lookups = [], []
        def resolve(url):
            lookups.append(url)
            return '93.184.216.34' if 'maker' in url else '93.184.216.35'
        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(302, headers={'location': 'https://cdn.example/x.pdf'})
            return httpx.Response(200, content=b'%PDF-1.4 test')
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch.object(w, 'public_address', side_effect=resolve), patch.object(w.httpx, 'Client', return_value=client):
            w.download_source('https://maker.example/x.pdf')
        self.assertEqual(len(lookups), 2)
        self.assertEqual(requests[0].url.host, '93.184.216.34')
        self.assertEqual(requests[0].headers['host'], 'maker.example')
        self.assertEqual(requests[1].extensions['sni_hostname'], 'cdn.example')

    def test_direct_private_ipv6_and_metadata_blocked(self):
        for url in ('http://169.254.169.254/', 'http://[::1]/', 'http://10.0.0.1/', 'http://192.168.0.1/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                w.validate_url(url)

    def test_public_non_kawasaki_url_is_accepted(self):
        self.assertEqual(w.validate_url('https://another-manufacturer.example/pumps#details'), 'https://another-manufacturer.example/pumps')

    def test_public_api_returns_original_pdf_and_provenance(self):
        from scraper import download_source
        raw = b'%PDF-1.4 original source bytes'
        with patch.object(w, 'fetch', return_value=(raw, 'https://cdn.example/pump.pdf', 'application/pdf', True)):
            content, filename, source_url, cached = download_source('https://maker.example/download')
        self.assertEqual(content, raw)
        self.assertEqual(filename, 'pump.pdf')
        self.assertEqual(source_url, 'https://cdn.example/pump.pdf')
        self.assertTrue(cached)

    def test_private_redirect_rejected(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request:
            httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})))
        with patch.object(w, 'public_address', return_value='93.184.216.34'), patch.object(w.httpx, 'Client', return_value=client):
            with self.assertRaises(ValueError):
                w.download_source('https://maker.example/pump.pdf')

    def test_download_cache_avoids_second_request(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, content=b'%PDF-1.4 example')
        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch.object(w, 'public_address', return_value='93.184.216.34'), patch.object(w.httpx, 'Client', return_value=client):
            self.assertFalse(w.download_source('https://maker.example/x.pdf')[3])
            self.assertTrue(w.download_source('https://maker.example/x.pdf')[3])
        self.assertEqual(len(calls), 1)

    def test_size_limit_rejects_source(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, content=b'%PDF-1.4 example')))
        with patch.object(w, 'public_address', return_value='93.184.216.34'), patch.object(w.httpx, 'Client', return_value=client), patch.object(w, 'MAX_BYTES', 2):
            with self.assertRaises(ValueError):
                w.download_source('https://maker.example/x.pdf')

if __name__ == '__main__':
    unittest.main()
