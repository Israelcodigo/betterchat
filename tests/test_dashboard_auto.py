"""Automatic panel opening: opt-in, actual usage, and one live browser tab.

All profiles are temporary. Browser launches are mocked even when the server,
HTTP requests, activity database, installed launcher, and process are real.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nexo_kit.activity import observe_hook, read_activity, record_usage
from nexo_kit.dashboard import (
    DashboardServer, OPEN_RETRY_SECONDS, PRESENCE_SECONDS, auto_open_dashboard,
    running_server, stop_dashboard,
)
from nexo_kit.personal import PREFERENCES, install_personal, preferences, uninstall_personal


class TemporaryProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-auto-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'profile'
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        environment = patch.dict(os.environ, {
            'CODEX_HOME': str(self.home), 'CODEX_THREAD_ID': 'test-auto-thread',
            'CODEX_SESSION_ID': '', 'NEXO_WORKER': '0',
        })
        environment.start()
        self.addCleanup(environment.stop)
        # Catch accidental real launches in every test, including failure cases.
        browser = patch('nexo_kit.dashboard.webbrowser.open', return_value=True)
        self.browser = browser.start()
        self.addCleanup(browser.stop)

    def enable(self):
        path = self.home / PREFERENCES
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'auto_open_dashboard': True}), encoding='utf-8')

    def assets(self):
        skill = self.home / 'skills/nexo-example/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: nexo-example\ndescription: Test procedure.\n---\nTest.', encoding='utf-8')
        script = skill.parent / 'scripts/check.py'
        script.parent.mkdir()
        script.write_text('print("ok")\n', encoding='utf-8')
        role = self.home / 'agents/nexo-reviewer.toml'
        role.parent.mkdir(parents=True)
        role.write_text('name = "nexo-reviewer"\ndeveloper_instructions = "Review evidence."\n', encoding='utf-8')
        return skill, script

    def hook(self, name, arguments, response, event='PostToolUse'):
        return observe_hook(self.project, event, {
            'session_id': 'test-auto-thread', 'tool_name': name,
            'tool_input': arguments, 'tool_response': response,
        }, self.home)


class AutomaticPolicyTests(TemporaryProfileTests):
    def test_default_disabled_reads_without_creating_profile_or_project_state(self):
        self.assertFalse(preferences(self.home)['auto_open_dashboard'])
        self.assertEqual(auto_open_dashboard(self.home, self.project),
                         {'enabled': False, 'browser_opened': False})
        self.assertFalse(self.home.exists())
        self.assertEqual(list(self.project.iterdir()), [])
        self.browser.assert_not_called()

    def test_opt_in_survives_upgrade_can_disable_and_uninstall_restores_original(self):
        path = self.home / PREFERENCES
        path.parent.mkdir(parents=True)
        original = (json.dumps({'automatic_routing': True, 'auto_learn': False, 'threshold': 3,
                               'auto_open_dashboard': False, 'future_preference': 'preserve'},
                              indent=2) + '\n').encode('utf-8')
        path.write_bytes(original)
        # Adopt an identical pre-existing policy first; installation deliberately
        # refuses to overwrite unmanaged customizations.
        install_personal(self.home)
        installed = install_personal(self.home, auto_open_dashboard=True)
        self.assertTrue(installed['auto_open_dashboard'])
        self.assertTrue(install_personal(self.home)['auto_open_dashboard'])
        self.assertEqual(preferences(self.home)['future_preference'], 'preserve')
        self.assertFalse(install_personal(self.home, auto_open_dashboard=False)['auto_open_dashboard'])
        self.assertTrue(uninstall_personal(self.home)['complete'])
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.home / 'nexo-kit/Open-Nexo-Panel.cmd').exists())
        self.assertFalse((self.project / '.nexo').exists())
        self.browser.assert_not_called()

    def test_opt_in_dry_run_and_invalid_policy_do_not_create_runtime(self):
        self.assertTrue(install_personal(self.home, dry_run=True, auto_open_dashboard=True)['auto_open_dashboard'])
        self.assertFalse(self.home.exists())
        with self.assertRaises(ValueError):
            install_personal(self.home, auto_open_dashboard='true')
        self.assertFalse(self.home.exists())
        path = self.home / PREFERENCES
        path.parent.mkdir(parents=True)
        path.write_text('{"auto_open_dashboard":"true"}', encoding='utf-8')
        result = auto_open_dashboard(self.home, self.project)
        self.assertFalse(result['browser_opened'])
        self.assertIn('ValueError', result['error'])
        self.assertFalse((self.home / 'nexo-kit/runtime').exists())
        self.browser.assert_not_called()

    def test_worker_does_not_spawn_dashboard_even_when_enabled(self):
        self.enable()
        with patch.dict(os.environ, {'NEXO_WORKER': '1'}), \
                patch('nexo_kit.dashboard.open_dashboard') as opener:
            result = auto_open_dashboard(self.home, self.project)
        self.assertEqual(result['browser_reason'], 'runner-worker')
        opener.assert_not_called()

    def test_launcher_failures_are_nonfatal_and_do_not_expose_capability_or_path(self):
        self.enable()
        for failure in (PermissionError('private-path token=private-token'),
                        ValueError('startup problem'), RuntimeError('unexpected launcher failure')):
            with self.subTest(failure=type(failure).__name__), \
                    patch('nexo_kit.dashboard.open_dashboard', side_effect=failure):
                result = auto_open_dashboard(self.home, self.project)
                self.assertTrue(result['enabled'])
                self.assertFalse(result['browser_opened'])
                self.assertIn(type(failure).__name__, result['error'])
                self.assertNotIn('private', json.dumps(result))
        self.assertEqual(list(self.project.iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Installed launcher is a Windows script')
    def test_installed_script_handles_special_paths_without_policy_bypass(self):
        shell = shutil.which('cmd')
        if not shell:
            self.skipTest('Windows command processor is unavailable')
        home = self.root / "perfil de O'Brien ñ 100% !"
        install_personal(home, auto_open_dashboard=False)
        script = home / 'nexo-kit/Open-Nexo-Panel.cmd'
        before = {str(path.relative_to(home)): path.read_bytes()
                  for path in home.rglob('*') if path.is_file()}
        run = subprocess.run([shell, '/d', '/c', str(script)],
                             cwd=self.project, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result, {'enabled': False, 'browser_opened': False})
        self.assertEqual(before, {str(path.relative_to(home)): path.read_bytes()
                                  for path in home.rglob('*') if path.is_file()})
        script_bytes = script.read_bytes()
        install_personal(home)
        self.assertEqual(script.read_bytes(), script_bytes)
        self.assertTrue(uninstall_personal(home)['complete'])
        self.assertFalse(script.exists())
        self.assertFalse((self.home / 'nexo-kit').exists())
        self.assertEqual(list(self.project.iterdir()), [])


class UsageTriggerTests(TemporaryProfileTests):
    def test_reports_open_only_on_start_of_actual_installed_nexo_capability(self):
        self.assets()
        plain = self.home / 'skills/unrelated/SKILL.md'
        plain.parent.mkdir(parents=True)
        plain.write_text('Unrelated instructions', encoding='utf-8')
        with patch('nexo_kit.dashboard.auto_open_dashboard', return_value={'enabled': False}) as opener:
            for kind, name in [('skill', 'nexo-example'), ('agent', 'nexo-reviewer')]:
                started = record_usage(self.project, kind, name, 'started', codex_home=self.home)
                completed = record_usage(self.project, kind, name, 'completed', codex_home=self.home)
                self.assertTrue(started['recorded'])
                self.assertTrue(completed['recorded'])
                self.assertIn('dashboard', started)
                self.assertNotIn('dashboard', completed)
            record_usage(self.project, 'skill', 'unrelated', 'started', codex_home=self.home)
            with self.assertRaises(ValueError):
                record_usage(self.project, 'agent', 'nexo-absent', 'started', codex_home=self.home)
            self.assertEqual(opener.call_count, 2)
        self.assertFalse((self.project / '.nexo').exists())

    def test_successful_skill_load_script_execution_and_installed_role_open(self):
        skill, script = self.assets()
        examples = [
            ('Read', {'file_path': str(skill)}, {'file': {'filePath': str(skill), 'content': 'instructions'}}),
            ('functions.exec_command', {'cmd': ['python', str(script)]}, {'exit_code': 0}),
            ('collaborationspawn_agent', {'agent_type': 'nexo-reviewer'}, {'task_name': '/root/reviewer'}),
        ]
        with patch('nexo_kit.dashboard.auto_open_dashboard') as opener:
            for name, arguments, response in examples:
                with self.subTest(tool=name):
                    opener.reset_mock()
                    self.assertTrue(self.hook(name, arguments, response))
                    opener.assert_called_once_with(self.home, self.project)

    def test_lifecycle_failure_reference_unknown_role_and_non_nexo_skill_do_not_open(self):
        skill, _ = self.assets()
        source_only = self.project / 'research/nexo-uninstalled/SKILL.md'
        source_only.parent.mkdir(parents=True)
        source_only.write_text('Research reference, not installed.', encoding='utf-8')
        unrelated = self.home / 'skills/unrelated/SKILL.md'
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text('Instructions', encoding='utf-8')
        cases = [
            ('Read', {'file_path': str(skill)}, {'success': True}, 'SessionStart'),
            ('Read', {'file_path': str(skill)}, {'success': True}, 'PreCompact'),
            ('Read', {'file_path': str(skill)}, {'success': True}, 'Stop'),
            ('Read', {'file_path': str(skill)}, {'success': False}, 'PostToolUse'),
            ('Read', {'file_path': str(skill)}, None, 'PostToolUse'),
            ('functions.exec_command', {'cmd': ['echo', str(skill)]}, {'exit_code': 0}, 'PostToolUse'),
            ('Read', {'file_path': str(unrelated)}, {'success': True}, 'PostToolUse'),
            ('Read', {'file_path': str(source_only)}, {'success': True}, 'PostToolUse'),
            ('spawn_agent', {'agent_type': 'nexo-absent'}, {'id': 'child'}, 'PostToolUse'),
            ('spawn_agent', {'agent_type': 'nexo-reviewer'}, {'error': 'denied'}, 'PostToolUse'),
        ]
        with patch('nexo_kit.dashboard.auto_open_dashboard') as opener:
            for name, arguments, response, event in cases:
                with self.subTest(tool=name, response=response, event=event):
                    self.assertTrue(self.hook(name, arguments, response, event))
            opener.assert_not_called()

    def test_known_mcp_operation_opens_but_unknown_failure_or_ambiguous_response_does_not(self):
        with patch('nexo_kit.dashboard.auto_open_dashboard') as opener:
            for name in ('nexo_status', 'mcp__nexo__nexo_status', 'mcp.nexo.nexo_status'):
                opener.reset_mock()
                self.assertTrue(self.hook(name, {}, {'success': True}))
                opener.assert_called_once_with(self.home, self.project)
            opener.reset_mock()
            for name, response in [('mcp__nexo__nexo_unknown', {'success': True}),
                                   ('mcp__other__nexo_status', {'success': True}),
                                   ('nexo_status', {'isError': True}),
                                   ('nexo_status', {'isError': True, 'content': [{'type': 'text', 'text': 'denied'}]}),
                                   ('nexo_status', {'isError': False, 'error': 'denied',
                                                    'content': [{'type': 'text', 'text': 'error'}]}),
                                   ('nexo_status', None)]:
                self.assertTrue(self.hook(name, {}, response))
            opener.assert_not_called()

    def test_standard_successful_mcp_response_opens_dashboard(self):
        with patch('nexo_kit.dashboard.auto_open_dashboard') as opener:
            self.assertTrue(self.hook('mcp__nexo__nexo_status', {}, {
                'isError': False, 'content': [{'type': 'text', 'text': '{"tasks":[]}'}],
            }))
            opener.assert_called_once_with(self.home, self.project)

    def test_permission_failure_opening_panel_keeps_activity_and_hook_result(self):
        skill, _ = self.assets()
        self.enable()
        with patch('nexo_kit.dashboard.open_dashboard', side_effect=PermissionError('denied')):
            result = record_usage(self.project, 'skill', 'nexo-example', 'started', codex_home=self.home)
            self.assertTrue(result['recorded'])
            self.assertIn('PermissionError', result['dashboard']['error'])
            self.assertTrue(self.hook('Read', {'file_path': str(skill)}, {'success': True}))
        self.assertEqual(len([event for event in read_activity(self.home)['events']
                              if event['kind'] == 'skill']), 2)


class PresenceProtocolTests(TemporaryProfileTests):
    def setUp(self):
        super().setUp()
        self.server = DashboardServer(self.home, token='private-test-capability')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('POST', path, body=b'', headers=headers or {})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def authenticated(self, path):
        code, result = self.request(path, {'X-Nexo-Token': self.server.token})
        self.assertEqual(code, 200, result)
        return result

    def test_presence_and_open_claim_require_token_and_same_origin(self):
        for path in ('/api/presence', '/api/open-claim', '/api/open-manual'):
            for headers, expected in [({}, 401), ({'X-Nexo-Token': 'wrong'}, 401),
                                      ({'X-Nexo-Token': self.server.token, 'Origin': 'https://example.com'}, 403),
                                      ({'X-Nexo-Token': self.server.token, 'Host': 'evil.example'}, 403),
                                      ({'X-Nexo-Token': self.server.token, 'Sec-Fetch-Site': 'cross-site'}, 403)]:
                with self.subTest(path=path, headers=headers):
                    status, result = self.request(path, headers)
                    self.assertEqual(status, expected)
                    self.assertNotIn(self.server.token, json.dumps(result))
        self.assertIsNone(self.server.last_client_seen)
        self.assertIsNone(self.server.last_open_requested)

    def test_live_presence_pending_claim_expiry_and_manual_open(self):
        self.assertTrue(self.authenticated('/api/open-claim')['open'])
        self.assertEqual(self.authenticated('/api/open-claim'),
                         {'open': False, 'reason': 'opening-recently-requested'})
        self.server.last_open_requested = time.monotonic() - OPEN_RETRY_SECONDS - 1
        self.assertTrue(self.authenticated('/api/open-claim')['open'])
        self.assertTrue(self.authenticated('/api/presence')['connected'])
        self.assertEqual(self.authenticated('/api/open-claim'), {'open': False, 'reason': 'panel-connected'})
        self.assertTrue(self.authenticated('/api/open-manual')['open'])
        self.server.last_client_seen = time.monotonic() - PRESENCE_SECONDS - 1
        self.server.last_open_requested = time.monotonic() - OPEN_RETRY_SECONDS - 1
        self.assertTrue(self.authenticated('/api/open-claim')['open'])

    def test_concurrent_http_claims_allow_exactly_one_launcher(self):
        with ThreadPoolExecutor(max_workers=6) as executor:
            claims = list(executor.map(lambda _: self.authenticated('/api/open-claim'), range(12)))
        self.assertEqual(sum(item['open'] for item in claims), 1)


class RealLauncherTests(TemporaryProfileTests):
    def setUp(self):
        super().setUp()
        self.processes = []
        original_popen = subprocess.Popen

        def launch(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            self.processes.append(process)
            return process

        # Preserve real process objects until shutdown so the test reaps them.
        self.addCleanup(self.reap_launchers)
        launches = patch('nexo_kit.dashboard.subprocess.Popen', side_effect=launch)
        launches.start()
        self.addCleanup(launches.stop)

    def reap_launchers(self):
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                self.fail('Temporary dashboard server did not exit after authenticated shutdown')

    def test_concurrent_auto_calls_reuse_one_real_server_and_request_one_browser(self):
        self.enable()
        self.addCleanup(stop_dashboard, self.home)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: auto_open_dashboard(self.home, self.project), range(4)))
        self.assertFalse([item for item in results if item.get('error')], results)
        self.assertEqual(sum(item['browser_opened'] for item in results), 1)
        self.assertEqual(len({item['pid'] for item in results}), 1)
        self.assertEqual(sum(not item['reused'] for item in results), 1)
        self.browser.assert_called_once()
        info = running_server(self.home)
        self.assertIsNotNone(info)
        self.assertEqual(self.browser.call_args.args[0], info['url'])
        self.assertNotIn('token', json.dumps(results))
        self.assertNotIn(info['token'], json.dumps(results))
        self.assertFalse((self.home / 'nexo-kit/activity.sqlite3').exists())
        self.assertEqual(list(self.project.iterdir()), [])

    def test_browser_unavailable_leaves_server_reusable_without_crashing(self):
        self.enable()
        self.browser.return_value = False
        self.addCleanup(stop_dashboard, self.home)
        first = auto_open_dashboard(self.home, self.project)
        self.assertFalse(first['browser_opened'])
        self.assertEqual(first['browser_reason'], 'browser-unavailable')
        second = auto_open_dashboard(self.home, self.project)
        self.assertTrue(second['reused'])
        self.assertEqual(second['browser_reason'], 'opening-recently-requested')
        self.assertEqual(first['pid'], second['pid'])


if __name__ == '__main__':
    unittest.main()
