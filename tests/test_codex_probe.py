"""Native probe control-flow tests; never launch Codex or call a model."""
from __future__ import annotations

import contextlib
import copy
import io
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import codex_integration_probe as probe


class ProbeApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-probe-unit-')
        self.fixture = Path(self.tmp.name).resolve()
        self.client = probe.Client.__new__(probe.Client)
        self.client.queue = queue.Queue()
        self.client.responses = {}
        self.client.events = []
        self.client.trace = io.StringIO()
        self.client.allowed_nexo_projects = {self.fixture}
        self.client.allowed_nexo_threads = {'fixture-thread': self.fixture}
        self.client.send = Mock()
        self.request = {
            'id': 123, 'method': 'mcpServer/elicitation/request',
            'params': {
                'threadId': 'fixture-thread', 'serverName': 'nexo', 'mode': 'form',
                '_meta': {'codex_approval_kind': 'mcp_tool_call', 'tool_params': {'title': 'Repair'}},
                'message': 'Allow the nexo MCP server to run tool "nexo_task_add"?',
                'requestedSchema': {'type': 'object', 'properties': {}},
            },
        }

    def tearDown(self):
        self.tmp.cleanup()

    def pump(self, request):
        self.client.queue.put(request)
        with contextlib.redirect_stdout(io.StringIO()):
            self.client.pump(timeout=0.01)

    def assert_allowed_once(self, name):
        request = copy.deepcopy(self.request)
        request['params']['message'] = f'Allow the nexo MCP server to run tool "{name}"?'
        # Persistence offered by Codex must never be selected by the probe.
        request['params']['_meta']['persist'] = ['session', 'always']
        self.pump(request)
        self.client.send.assert_called_once_with({
            'id': 123, 'result': {'action': 'accept', 'content': {}, '_meta': None}})
        self.assertEqual(self.client.events[0]['method'], 'probe/approvalOnce')
        self.assertEqual(self.client.events[0]['params']['tool'], name)
        self.assertNotIn('persist', self.client.trace.getvalue())

    def assert_rejected(self, request):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected server request'):
            self.pump(request)
        sent = self.client.send.call_args.args[0]
        self.assertIn('error', sent)
        self.assertNotIn('result', sent)
        self.assertEqual(self.client.events, [])

    def test_rejects_unregistered_project_even_with_registered_thread(self):
        self.client.allowed_nexo_threads['fixture-thread'] = self.fixture / 'other'
        self.assert_rejected(self.request)

    def test_rejects_a_different_native_approval_api(self):
        self.request['method'] = 'item/tool/requestUserInput'
        self.assert_rejected(self.request)


def _allowed_case(name):
    def test(self):
        self.assert_allowed_once(name)
    return test


for _name in ('initialize', 'task_add', 'task_claim', 'task_heartbeat', 'task_finish',
              'verify_task', 'memory_propose'):
    setattr(ProbeApprovalTests, 'test_accepts_once_nexo_' + _name, _allowed_case('nexo_' + _name))


def _rejected_param_case(key, value):
    def test(self):
        request = copy.deepcopy(self.request)
        request['params'][key] = value
        self.assert_rejected(request)
    return test


for _name, _key, _value in (
    ('other_server', 'serverName', 'other'),
    ('other_thread', 'threadId', 'other'),
    ('url_mode', 'mode', 'url'),
    ('unknown_tool', 'message', 'Allow the nexo MCP server to run tool "delete_all"?'),
    ('missing_privileged_metadata', '_meta', {}),
    ('nonempty_form', 'requestedSchema', {'type': 'object', 'properties': {'password': {'type': 'string'}}}),
):
    setattr(ProbeApprovalTests, 'test_rejects_' + _name, _rejected_param_case(_key, _value))


class ProbeCompactionTests(unittest.TestCase):
    def client(self):
        client = probe.Client.__new__(probe.Client)
        client.queue = queue.Queue()
        client.responses = {}
        client.events = [{'method': 'turn/completed', 'params': {
            'threadId': 'alpha', 'turn': {'id': 'previous-turn', 'status': 'completed'}}}]
        client.trace = io.StringIO()
        client.request = Mock(return_value={})
        return client

    @staticmethod
    def precompact():
        return {'method': 'hook/completed', 'params': {
            'threadId': 'alpha', 'run': {'eventName': 'preCompact', 'status': 'completed'}}}

    def test_waits_past_precompact_old_turn_and_other_thread(self):
        client = self.client()
        client.queue.put(self.precompact())
        client.queue.put({'method': 'thread/compacted', 'params': {'threadId': 'other'}})
        expected = {'method': 'thread/compacted', 'params': {'threadId': 'alpha'}}
        client.queue.put(expected)
        with contextlib.redirect_stdout(io.StringIO()):
            actual = client.compact('alpha')
        self.assertEqual(actual, expected)
        self.assertTrue(client.queue.empty())
        client.request.assert_called_once_with('thread/compact/start', {'threadId': 'alpha'})

    def test_accepts_completed_native_compact_turn(self):
        client = self.client()
        client.queue.put(self.precompact())
        expected = {'method': 'turn/completed', 'params': {
            'threadId': 'alpha', 'turn': {'id': 'new-compact-turn', 'status': 'completed'}}}
        client.queue.put(expected)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(client.compact('alpha'), expected)
        self.assertTrue(client.queue.empty())

    def test_precompact_alone_cannot_release_the_next_turn(self):
        client = self.client()
        client.queue.put(self.precompact())
        with patch.object(probe.time, 'monotonic', side_effect=[0, 1, 301]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(TimeoutError, 'Native event'):
                client.compact('alpha')


class ProbeResultTests(unittest.TestCase):
    def test_core_discovery_accepts_nine_personal_skills_and_learned_extras(self):
        with tempfile.TemporaryDirectory(prefix='nexo-probe-skills-') as directory:
            root = Path(directory).resolve()
            alpha, beta, home = root / 'alpha', root / 'beta', root / 'codex-home'
            probe.initialize_project(alpha)
            probe.initialize_project(beta)
            names = ['nexo-' + suffix for suffix in (
                'plan', 'build', 'debug', 'review', 'verify', 'memory', 'resume', 'orchestrate', 'learn')]
            self.assertEqual(probe.CORE_PERSONAL_SKILLS, set(names))
            entries = [{'cwd': str(project), 'errors': [], 'skills': [
                {'name': name, 'enabled': True, 'path': str(home / 'skills' / name / 'SKILL.md')}
                for name in names]} for project in (alpha, beta)]
            for extras in ([], [
                {'name': 'nexo-learned-project-task', 'enabled': True,
                 'path': str(alpha / '.agents/skills/nexo-learned-project-task/SKILL.md')},
                {'name': 'nexo-learned-disabled-task', 'enabled': False,
                 'path': str(home / 'skills/nexo-learned-disabled-task/SKILL.md')},
            ]):
                with self.subTest(extras=bool(extras)), patch.dict(probe.os.environ, {'CODEX_HOME': str(home)}):
                    inventory = copy.deepcopy(entries)
                    for entry in inventory:
                        entry['skills'].extend(extras)
                    result = probe.validate_real_report({'skills': inventory}, [], alpha, beta)
                    for label in ('alpha', 'beta'):
                        self.assertTrue(result['checks'][label + '_core_personal_skills'])
                        self.assertNotIn(label + '_eight_personal_skills', result['checks'])

    def test_core_discovery_rejects_missing_disabled_wrong_path_duplicate_and_errors(self):
        with tempfile.TemporaryDirectory(prefix='nexo-probe-skills-') as directory:
            root = Path(directory).resolve()
            alpha, beta, home = root / 'alpha', root / 'beta', root / 'codex-home'
            probe.initialize_project(alpha)
            probe.initialize_project(beta)
            skills = [{'name': name, 'enabled': True, 'path': str(home / 'skills' / name / 'SKILL.md')}
                      for name in sorted(probe.CORE_PERSONAL_SKILLS)]
            for mutation in ('missing-learn', 'disabled', 'wrong-path', 'duplicate', 'errors'):
                with self.subTest(mutation=mutation), patch.dict(probe.os.environ, {'CODEX_HOME': str(home)}):
                    entry = {'cwd': str(alpha), 'skills': copy.deepcopy(skills), 'errors': []}
                    if mutation == 'missing-learn':
                        entry['skills'] = [skill for skill in entry['skills'] if skill['name'] != 'nexo-learn']
                    elif mutation == 'disabled':
                        entry['skills'][0]['enabled'] = False
                    elif mutation == 'wrong-path':
                        entry['skills'][0]['path'] = str(alpha / '.agents/skills/SKILL.md')
                    elif mutation == 'duplicate':
                        entry['skills'].append(copy.deepcopy(entry['skills'][0]))
                    else:
                        entry['errors'].append({'message': 'Invalid skill metadata'})
                    result = probe.validate_real_report({'skills': [entry]}, [], alpha, beta)
                    self.assertFalse(result['checks']['alpha_core_personal_skills'])

    def test_model_success_claim_cannot_replace_native_evidence(self):
        with tempfile.TemporaryDirectory(prefix='nexo-probe-report-') as directory:
            alpha, beta = Path(directory) / 'alpha', Path(directory) / 'beta'
            probe.initialize_project(alpha)
            probe.initialize_project(beta)
            report = {'completed': True, 'thread_alpha': 'alpha', 'thread_beta': 'beta',
                      'alpha_final': {'text': 'All skills, hooks, roles and MCP work perfectly.'}}
            result = probe.validate_real_report(report, [], alpha, beta)
            self.assertFalse(result['passed'])
            for key in ('four_native_roles', 'four_hooks_executed', 'alpha_mcp_connected',
                        'same_task_completed_through_mcp', 'beta_marker_isolated'):
                self.assertFalse(result['checks'][key], key)


if __name__ == '__main__':
    unittest.main()
