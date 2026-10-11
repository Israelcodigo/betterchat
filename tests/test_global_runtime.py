"""A personal runtime must stay inert until activated and isolate session cwd."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nexo_kit.actions import initialize_project, status
from nexo_kit.config import load_config
from nexo_kit.hooks import handle_hook
from nexo_kit.store import Store
from nexo_kit.util import write_json


class GlobalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-global-runtime-')
        self.root = Path(self.tmp.name)
        environment = patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'codex-home')})
        environment.start()
        self.addCleanup(environment.stop)
        self.alpha = self.root / 'project alpha'
        self.beta = self.root / 'project beta'
        self.alpha.mkdir()
        self.beta.mkdir()
        (self.alpha / 'user-file.txt').write_text('Keep my code.', encoding='utf-8')
        self.runtime = Path(__file__).resolve().parents[1] / 'nexo.py'

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def snapshot(project: Path) -> dict:
        return {path.relative_to(project).as_posix(): path.read_bytes() if path.is_file() else None
                for path in project.rglob('*')}

    def run_runtime(self, project: Path, arguments: list[str], payload: str = ''):
        environment = os.environ.copy()
        environment.pop('NEXO_WORKER', None)
        environment.pop('PYTHONPATH', None)
        environment['PYTHONUTF8'] = '1'
        return subprocess.run([sys.executable, '-B', str(self.runtime), *arguments], cwd=project,
                              input=payload, env=environment, capture_output=True, text=True,
                              encoding='utf-8', timeout=20)

    def exchange(self, project: Path, calls: list[tuple[str, dict]]):
        messages = [{'jsonrpc': '2.0', 'id': 'init', 'method': 'initialize', 'params': {
            'protocolVersion': '2025-11-25', 'capabilities': {},
            'clientInfo': {'name': 'project-scope-test', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 'list', 'method': 'tools/list'}]
        for number, (name, arguments) in enumerate(calls):
            messages.append({'jsonrpc': '2.0', 'id': number, 'method': 'tools/call',
                             'params': {'name': 'nexo_' + name, 'arguments': arguments}})
        run = self.run_runtime(project, ['mcp'], '\n'.join(json.dumps(message) for message in messages) + '\n')
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        return {message['id']: message for message in map(json.loads, run.stdout.splitlines())}

    @staticmethod
    def value(response: dict):
        return json.loads(response['result']['content'][0]['text'])

    def test_status_on_uninitialized_project_is_read_only_in_api_and_cli(self):
        before = self.snapshot(self.alpha)
        value = status(self.alpha)
        self.assertFalse(value['initialized'])
        self.assertEqual(value['tasks'], [])
        self.assertIn('init --state-only', value['next_step'])
        run = self.run_runtime(self.alpha, ['status'])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(json.loads(run.stdout)['initialized'])
        self.assertEqual(self.snapshot(self.alpha), before)

    def test_hooks_on_uninitialized_project_do_not_create_state(self):
        before = self.snapshot(self.alpha)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('NEXO_WORKER', None)
            for event in ('SessionStart', 'PostToolUse', 'PreCompact', 'Stop'):
                with self.subTest(event=event):
                    payload = {'session_id': 'uninitialized', 'cwd': str(self.alpha),
                               'tool_name': 'Read', 'tool_response': 'do not capture this'}
                    output = handle_hook(self.alpha, event, payload)
                    run = self.run_runtime(self.beta, ['hook', '--event', event], json.dumps(payload))
                    self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
                    self.assertEqual(json.loads(run.stdout), output)
                    if event == 'SessionStart':
                        self.assertIn('nexo_initialize', output['hookSpecificOutput']['additionalContext'])
                    else:
                        self.assertEqual(output, {})
        self.assertEqual(self.snapshot(self.alpha), before)
        self.assertEqual(self.snapshot(self.beta), {})

    def test_mcp_discovers_tools_and_reads_status_before_initialization_without_writes(self):
        before = self.snapshot(self.alpha)
        replies = self.exchange(self.alpha, [('status', {}), ('task_add', {'title': 'Not initialized'})])
        tools = replies['list']['result']['tools']
        self.assertIn('nexo_initialize', {tool['name'] for tool in tools})
        self.assertFalse(self.value(replies[0])['initialized'])
        self.assertTrue(replies[1]['result']['isError'])
        self.assertIn('not initialized', replies[1]['result']['content'][0]['text'].lower())
        self.assertEqual(self.snapshot(self.alpha), before)

    def test_state_only_initialization_creates_data_without_client_assets(self):
        target = self.alpha / 'new directory'
        dry = initialize_project(target, dry_run=True)
        self.assertTrue(dry['dry_run'])
        self.assertFalse(target.exists())
        run = self.run_runtime(self.alpha, ['init', '--state-only', str(target)])
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertTrue(json.loads(run.stdout)['state_only'])
        self.assertTrue((target / '.nexo/config.json').is_file())
        self.assertTrue((target / '.nexo/state.sqlite3').is_file())
        self.assertIn('.nexo/', (target / '.gitignore').read_text(encoding='utf-8'))
        for relative in ('AGENTS.md', 'CLAUDE.md', '.codex', '.agents', '.claude', '.nexo/runtime'):
            self.assertFalse((target / relative).exists(), relative)

    def test_mcp_initializes_and_keeps_separate_state_for_two_session_directories(self):
        for project, title in ((self.alpha, 'Alpha only'), (self.beta, 'Beta only')):
            replies = self.exchange(project, [('initialize', {}), ('task_add', {'title': title}), ('status', {})])
            self.assertTrue(self.value(replies[0])['initialized'])
            observed = self.value(replies[2])
            self.assertEqual(Path(observed['project']), project)
            self.assertEqual([task['title'] for task in observed['tasks']], [title])
        final = self.value(self.exchange(self.alpha, [('status', {})])[0])
        self.assertEqual([task['title'] for task in final['tasks']], ['Alpha only'])

    def test_initializing_existing_project_preserves_gates_data_and_gitignore(self):
        initialize_project(self.alpha)
        config = load_config(self.alpha)
        config['checks'] = [{'name': 'real gate', 'command': [sys.executable, '-c', 'print("pass")']}]
        write_json(self.alpha / '.nexo/config.json', config)
        (self.alpha / '.gitignore').write_text('user-ignore\n.nexo/\n', encoding='utf-8')
        with Store(self.alpha) as store:
            task = store.add_task('Existing task')
        initialize_project(self.alpha)
        self.assertEqual(load_config(self.alpha)['checks'], config['checks'])
        self.assertEqual((self.alpha / '.gitignore').read_text(encoding='utf-8'), 'user-ignore\n.nexo/\n')
        with Store(self.alpha) as store:
            self.assertEqual(store.get_task(task['id'])['title'], 'Existing task')

    def test_hook_payload_cwd_overrides_process_cwd_and_explicit_project(self):
        for project, memory in ((self.alpha, 'alpha-context-only'), (self.beta, 'beta-context-only')):
            initialize_project(project)
            with Store(project) as store:
                store.add_memory(memory, source='scope-test', status='active')
        with Store(self.beta) as store:
            beta_events = store.events()
        run = self.run_runtime(self.beta, ['--project', str(self.beta), 'hook', '--event', 'SessionStart'],
                               json.dumps({'cwd': str(self.alpha), 'session_id': 'payload-chosen'}))
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        context = json.loads(run.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('alpha-context-only', context)
        self.assertNotIn('beta-context-only', context)
        with Store(self.alpha) as store:
            hooks = [event for event in store.events() if event['kind'] == 'hook']
            self.assertEqual(hooks[0]['payload']['session'], 'payload-chosen')
        with Store(self.beta) as store:
            self.assertEqual(store.events(), beta_events)

    def test_hook_without_payload_cwd_falls_back_to_process_project(self):
        initialize_project(self.beta)
        run = self.run_runtime(self.beta, ['hook', '--event', 'Stop'], json.dumps({'session_id': 'fallback'}))
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        with Store(self.beta) as store:
            hooks = [event for event in store.events() if event['kind'] == 'hook']
            self.assertEqual(hooks[0]['payload']['session'], 'fallback')
        self.assertFalse((self.alpha / '.nexo').exists())

    def test_relative_payload_cwd_is_rejected_without_writes(self):
        before = self.snapshot(self.alpha)
        run = self.run_runtime(self.alpha, ['hook', '--event', 'Stop'], json.dumps({'cwd': '../beta'}))
        self.assertEqual(run.returncode, 2)
        self.assertIn('absolute', run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        self.assertEqual(self.snapshot(self.alpha), before)


if __name__ == '__main__':
    unittest.main()
