"""Run: python -m unittest discover -s tests -v (from DanfossHackathon/)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
import backend
import web_sources


class WebSourceTests(unittest.TestCase):
    def setUp(self):
        web_sources._CACHE.clear()
        resolver = patch.object(web_sources, 'public_address', return_value='93.184.216.34')
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_homepage_fetches_only_pump_index(self):
        html = b'<h3>K3VLS</h3><a href="/a.pdf">Datasheet</a><a href="/motors/">Motors</a><a href="/a.pdf">Duplicate</a>'
        with patch.object(web_sources, 'fetch', return_value=(html, web_sources.PUMPS_URL, 'text/html', False)) as fetch:
            result = web_sources.discover('https://www.kawasakihydraulics.com/')
        fetch.assert_called_once_with(web_sources.PUMPS_URL)
        self.assertEqual(len(result['documents']), 1)
        self.assertEqual(result['documents'][0]['product'], 'K3VLS')

    def test_redirect_outside_supported_host_rejected(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={'location': 'http://127.0.0.1/private'}))
        client = httpx.Client(transport=transport)
        with patch.object(web_sources.httpx, 'Client', return_value=client):
            with self.assertRaises(ValueError):
                web_sources.fetch(web_sources.PUMPS_URL)

    def test_navigation_and_footer_pdfs_are_excluded(self):
        html = b'<nav><a href="/policy.pdf">Policy</a></nav><h3>K3VLS</h3><a href="/pump.pdf">Datasheet</a><footer><h3>Legal</h3><a href="/report.pdf">Report</a></footer>'
        with patch.object(web_sources, 'fetch', return_value=(html, web_sources.PUMPS_URL, 'text/html', False)):
            documents = web_sources.discover(web_sources.PUMPS_URL)['documents']
        self.assertEqual([d['name'] for d in documents], ['pump.pdf'])
        self.assertEqual(documents[0]['product'], 'K3VLS')

    def test_direct_pdf_discovery(self):
        url = 'https://www.kawasakihydraulics.com/pump.pdf'
        with patch.object(web_sources, 'fetch', return_value=(b'%PDF-1.4 test', url, 'application/pdf', True)):
            result = web_sources.discover(url)
        self.assertEqual(result['documents'][0]['url'], url)
        self.assertTrue(result['cached'])

    def test_unsupported_urls_are_rejected_before_fetch(self):
        for url in ('file:///etc/passwd',
                    'https://127.0.0.1/a.pdf',
                    'https://localhost/a.pdf',
                    'https://user@www.kawasakihydraulics.com/a.pdf',
                    'https://www.kawasakihydraulics.com:8443/a.pdf'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                web_sources.validate_url(url)

    def test_failed_download_does_not_modify_catalog(self):
        cat = {'tree': [{'id': 'pumps', 'label': 'Pumps'}]}
        with patch.object(backend, 'load_catalog', return_value=cat), patch.object(backend, 'download_source', side_effect=ValueError('Download failed')), patch.object(backend, 'store_datasets') as store:
            result = TestClient(backend.app).post('/api/web/import', json={
                'url': 'https://www.kawasakihydraulics.com/a.pdf',
                'productName': 'K3VLS', 'categoryId': 'pumps', 'competitorName': 'Example Hydraulics',
            }).json()
        self.assertEqual(result['uploaded'], [])
        self.assertEqual(result['issues'][0]['level'], 'error')
        store.assert_not_called()

    def test_repeated_download_uses_cache(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, content=b'%PDF-1.4 test', headers={'content-type': 'application/pdf'})
        client = httpx.Client(transport=httpx.MockTransport(respond))
        url = 'https://www.kawasakihydraulics.com/a.pdf'
        with patch.object(web_sources.httpx, 'Client', return_value=client):
            self.assertFalse(web_sources.download_pdf(url)[3])
            self.assertTrue(web_sources.download_pdf(url)[3])
        self.assertEqual(len(calls), 1)

    def test_wrong_content_and_download_limit(self):
        with patch.object(web_sources, 'fetch', return_value=(b'<html>error</html>', web_sources.PUMPS_URL, 'text/html', False)):
            with self.assertRaises(ValueError):
                web_sources.download_pdf(web_sources.PUMPS_URL)
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'too long')))
        with patch.object(web_sources.httpx, 'Client', return_value=client), patch.object(web_sources, 'MAX_BYTES', 2):
            with self.assertRaises(ValueError):
                web_sources.fetch(web_sources.PUMPS_URL)

    def test_import_and_existing_upload_share_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'files').mkdir(); (root / 'extracted').mkdir()
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'tree': [{'id': 'pumps', 'label': 'Pumps', 'children': []}], 'competitors': [], 'competitorProducts': []}))
            url = 'https://www.kawasakihydraulics.com/a.pdf'
            with patch.multiple(backend, FILES_DIR=root/'files', EXTRACT_DIR=root/'extracted', CATALOG_FILE=catalog), patch.object(backend, 'download_source', return_value=(b'%PDF-test', 'a.pdf', url, False)), patch.object(backend, 'extract_pages', return_value=[{'page': 1, 'text': 'Technical information '*20}]) as extract:
                client = TestClient(backend.app)
                payload = {'url': url, 'productName': 'K3VLS', 'categoryId': 'pumps', 'competitorName': 'Example Hydraulics'}
                self.assertEqual(client.post('/api/web/import', json=payload).json()['uploaded'], ['a.pdf'])
                client.post('/api/web/import', json=payload)
                extract.assert_called_once()
                cat = client.get('/api/catalog').json()
                products = cat['competitorProducts']
                self.assertEqual(len(products), 1)
                self.assertEqual(cat['competitors'][0]['name'], 'Example Hydraulics')
                self.assertEqual(len(products[0]['datasets']), 1)
                self.assertEqual(backend.pages_for(products[0]['datasets'])[0]['sourceUrl'], url)
                response = client.post('/api/datasets', data={'source':'danfoss', 'nodeId':'pumps'}, files={'files': ('notes.txt', b'Example document', 'text/plain')})
                self.assertEqual(response.json()['uploaded'], ['notes.txt'])
                response = client.post('/api/web/import', json={**payload, 'categoryId':'absent'})
                self.assertEqual(response.json()['uploaded'], [])

    def test_source_url_survives_citation_building(self):
        result = {'attributes':[{'values': {'pump': {'value':'45', 'sources':[{'file_id':'a','page':2}]}}}]}
        sources = backend.build_sources(result, {('a',2): {'title':'Pump', 'doc':'a.pdf', 'url':'https://www.kawasakihydraulics.com/a.pdf'}})
        self.assertEqual(sources[0]['url'], 'https://www.kawasakihydraulics.com/a.pdf')


if __name__ == '__main__':
    unittest.main()
