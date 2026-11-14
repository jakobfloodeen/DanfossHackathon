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
        with patch.object(w, 'fetch', side_effect=lambda url: (html, url, 'text/html', False)):
            result = w.discover('https://maker.example/')
        self.assertEqual(result['pagesVisited'], w.MAX_PAGES)      # crawl budget respected
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

    # ---- general use: the "Look for" choice steers the crawl, on any manufacturer's site ----
    def site(self, pages):
        def fetch(url):
            if url not in pages:
                raise ValueError('404')
            body = pages[url]
            kind = 'application/pdf' if body.startswith(b'%PDF-') else 'text/xml' if body.startswith(b'<?xml') \
                else 'text/plain' if url.endswith('.txt') else 'text/html'
            return body, url, kind, False
        return patch.object(w, 'fetch', side_effect=fetch)

    MENU = (b'<nav><a href="/valves">Valves</a><a href="/pumps">Pumps</a><a href="/motors">Motors</a>'
            b'<a href="/steering">Steering</a></nav>')

    def test_choice_in_the_menu_decides_what_is_crawled(self):
        pages = {'https://maker.example/': self.MENU,
                 'https://maker.example/valves': b'<h1>M4 control block</h1><a href="/docs/m4.pdf">Data sheet</a>',
                 'https://maker.example/pumps': b'<h1>A10 pump</h1><a href="/docs/a10.pdf">Data sheet</a>',
                 'https://maker.example/motors': b'<h1>OMR motor</h1><a href="/docs/omr.pdf">Data sheet</a>',
                 'https://maker.example/steering': b'<h1>OSPC</h1><a href="/docs/ospc.pdf">Data sheet</a>'}
        for topic, pdf in (('valves', 'm4.pdf'), ('pumps', 'a10.pdf'), ('motors', 'omr.pdf'), ('steering', 'ospc.pdf')):
            w._CACHE.clear()
            with self.subTest(topic=topic), self.site(pages) as fetcher:
                result = w.discover('https://maker.example/', topic)
                crawled = [c.args[0] for c in fetcher.call_args_list]
                self.assertEqual([d['name'] for d in result['documents']], [pdf])
                self.assertEqual(len(crawled), 2)                       # start page + the chosen category only

    def test_no_kawasaki_shortcut_the_start_page_is_crawled(self):
        pages = {'https://www.kawasakihydraulics.com/': self.MENU.replace(b'/valves', b'/type/valves'),
                 'https://www.kawasakihydraulics.com/type/valves': b'<h1>KMX valve</h1><a href="/kmx.pdf">PDF</a>'}
        with self.site(pages) as fetcher:
            result = w.discover('https://www.kawasakihydraulics.com/', 'valves')
        self.assertEqual(fetcher.call_args_list[0].args[0], 'https://www.kawasakihydraulics.com/')
        self.assertEqual(result['documents'][0]['name'], 'kmx.pdf')

    def test_media_download_links_and_sister_sites(self):
        # Bosch-style: datasheets behind '/media/<id>' links labelled 'Download', catalog on another subdomain
        pages = {'https://www.maker.example/': b'<a href="https://apps.maker.example/catalog/mobile-valves">Mobile valves</a>',
                 'https://apps.maker.example/catalog/mobile-valves':
                     b'<h2>M4-12 load sensing control block</h2><a href="https://www.maker.example/media/d8715dbe">Download</a>'}
        with self.site(pages):
            result = w.discover('https://www.maker.example/', 'valves')
        doc = result['documents'][0]
        self.assertEqual((doc['url'], doc['kind']), ('https://www.maker.example/media/d8715dbe', 'auto'))
        self.assertIn('M4-12', doc['product'])

    def test_documents_about_other_components_are_left_out(self):
        pages = {'https://maker.example/': b'<a href="/a10-pump-datasheet.pdf">A10 pump datasheet</a>'
                                          b'<a href="/m4-valve-datasheet.pdf">M4 valve datasheet</a>'
                                          b'<a href="/company-brochure.pdf">Company brochure</a>'}
        with self.site(pages):
            names = [d['name'] for d in w.discover('https://maker.example/', 'valves')['documents']]
        self.assertEqual(names[0], 'm4-valve-datasheet.pdf')             # most relevant first
        self.assertNotIn('a10-pump-datasheet.pdf', names)
        self.assertIn('company-brochure.pdf', names)                     # neutral: kept, ranked lower

    def test_sitemap_is_used_when_pages_are_built_by_javascript(self):
        pages = {'https://maker.example/': b'<div id="app"></div><script>render()</script>',
                 'https://maker.example/robots.txt': b'Sitemap: https://maker.example/sitemap.xml',
                 'https://maker.example/sitemap.xml': b'<?xml version="1.0"?><urlset>'
                     b'<url><loc>https://maker.example/products/valves/m4-12</loc></url>'
                     b'<url><loc>https://maker.example/products/pumps/a10vo</loc></url></urlset>',
                 'https://maker.example/products/valves/m4-12': b'<h1>M4-12</h1><a href="/media/m4-12-re64276">Data sheet</a>'}
        with self.site(pages) as fetcher:
            result = w.discover('https://maker.example/', 'valves')
        crawled = [c.args[0] for c in fetcher.call_args_list]
        self.assertEqual(result['documents'][0]['url'], 'https://maker.example/media/m4-12-re64276')
        self.assertNotIn('https://maker.example/products/pumps/a10vo', crawled)

    def test_page_text_only_offered_when_it_states_values(self):
        marketing = b'<h1>Mobile valves</h1><p>' + b'Discover our innovative solutions for your machine. ' * 10 + b'</p>'
        specs = b'<h1>M4-12 valve</h1><table><tr><td>Max pressure</td><td>350 bar</td></tr></table><p>' + b'Details. ' * 30 + b'</p>'
        for body, offered in ((marketing, False), (specs, True)):
            w._CACHE.clear()
            with self.subTest(offered=offered), self.site({'https://maker.example/valves': body}):
                docs = w.discover('https://maker.example/valves', 'valves')['documents']
            self.assertEqual(any(d['kind'] == 'html' for d in docs), offered)

    def test_site_key(self):
        self.assertEqual(w.site_key('apps.boschrexroth.com'), w.site_key('www.boschrexroth.com'))
        self.assertEqual(w.site_key('www.maker.co.uk'), 'maker.co.uk')
        self.assertNotEqual(w.site_key('maker.example'), w.site_key('other.example'))

if __name__ == '__main__':
    unittest.main()
