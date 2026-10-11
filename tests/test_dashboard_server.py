from __future__ import annotations

import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from nexo_kit.dashboard import DashboardServer


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = DashboardServer(self.root / 'profile', token='private-test-token')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request(method, path, body=body, headers=headers or {})
        result = conn.getresponse()
        response = (result.status, dict(result.getheaders()), result.read())
        conn.close()
        return response

    def test_data_requires_token_and_rejects_cross_origin_and_rebinding(self):
        for headers, expected in [({}, 401), ({'X-Nexo-Token': 'wrong'}, 401),
                                  ({'X-Nexo-Token': 'private-test-token', 'Origin': 'https://example.com'}, 403),
                                  ({'X-Nexo-Token': 'private-test-token', 'Host': 'evil.example'}, 403),
                                  ({'X-Nexo-Token': 'private-test-token', 'Sec-Fetch-Site': 'cross-site'}, 403)]:
            with self.subTest(headers=headers):
                code, _, body = self.request('GET', '/api/state', headers=headers)
                self.assertEqual(code, expected)
                self.assertNotIn(b'private-test-token', body)

    def test_snapshot_served_and_briefly_cached_only_for_authorized_browser(self):
        headers = {'X-Nexo-Token': 'private-test-token'}
        with patch('nexo_kit.dashboard.get_state', return_value={'skills': [{'name': 'nexo-plan'}]}) as snapshot:
            for _ in range(2):
                code, response_headers, body = self.request('GET', '/api/state', headers=headers)
                self.assertEqual(code, 200)
                self.assertEqual(json.loads(body)['skills'][0]['name'], 'nexo-plan')
                self.assertEqual(response_headers['Cache-Control'], 'no-store')
                self.assertNotIn('Access-Control-Allow-Origin', response_headers)
            self.assertEqual(snapshot.call_count, 1)

    def test_static_routes_never_read_arbitrary_files(self):
        for path in ['/../config.toml', '/%2e%2e/config.toml', '/.codex/auth.json', '/api/file?path=anything']:
            code, _, _ = self.request('GET', path, headers={'X-Nexo-Token': 'private-test-token'})
            self.assertEqual(code, 404)
        code, headers, _ = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_add_project_validates_input_without_initializing_project(self):
        headers = {'X-Nexo-Token': 'private-test-token', 'Content-Type': 'application/json'}
        folder = self.root / 'real project'
        folder.mkdir()
        code, _, body = self.request('POST', '/api/projects', json.dumps({'path': str(folder)}), headers)
        self.assertEqual(code, 200, body)
        self.assertFalse((folder / '.nexo').exists())
        code, _, _ = self.request('POST', '/api/projects', json.dumps({'path': 'relative/path'}), headers)
        self.assertEqual(code, 400)
        code, _, _ = self.request('POST', '/api/projects', 'broken', headers)
        self.assertEqual(code, 400)
        code, _, _ = self.request('POST', '/api/projects', 'x' * 9000, headers)
        self.assertEqual(code, 413)

    def test_no_shell_or_generic_write_endpoints(self):
        headers = {'X-Nexo-Token': 'private-test-token', 'Content-Type': 'application/json'}
        code, _, _ = self.request('POST', '/api/execute', '{}', headers)
        self.assertEqual(code, 404)
        code, _, body = self.request('GET', '/health')
        self.assertEqual(code, 200)
        self.assertNotIn(b'private-test-token', body)
        self.assertEqual(json.loads(body)['service'], 'nexo-dashboard')


if __name__ == '__main__':
    unittest.main()
