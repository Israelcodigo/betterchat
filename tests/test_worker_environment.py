"""Codex workers reuse an existing profile under a minimal inherited environment."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nexo_kit.actions import initialize_project
from nexo_kit.config import add_check
from nexo_kit.runner import run_task, worker_command, worker_environment
from nexo_kit.store import Store
from nexo_kit.util import write_json


class WorkerEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-worker-env-')
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'user home'
        self.home.mkdir()
        self.source = self.root / 'checkout/nexo_kit/runner.py'

    def tearDown(self):
        self.tmp.cleanup()

    def environment(self, inherited, backend='codex'):
        with patch('nexo_kit.runner.__file__', str(self.source)), patch('nexo_kit.runner.Path.home', return_value=self.home):
            return worker_environment(backend, inherited)

    def test_missing_or_empty_codex_home_uses_existing_python_home_profile(self):
        profile = self.home / '.codex'
        profile.mkdir()
        for value in ({'PATH': 'keep', 'NEXO_TEST': 'preserve'}, {'CODEX_HOME': ''}):
            with self.subTest(value=value):
                original = value.copy()
                result = self.environment(value)
                self.assertEqual(result['CODEX_HOME'], str(profile))
                self.assertEqual(value, original)
                self.assertNotIn('USERPROFILE', result)
                self.assertTrue(all(result[key] == val for key, val in original.items() if key != 'CODEX_HOME'))

    def test_explicit_profile_is_never_overridden_even_if_not_accessible(self):
        (self.home / '.codex').mkdir()
        inherited = {'CODEX_HOME': str(self.root / 'explicit absent profile'), 'MODEL_SETTING': 'preserve'}
        self.assertEqual(self.environment(inherited), inherited)

    def test_personal_runtime_uses_its_custom_profile_before_default(self):
        custom = self.root / 'custom profile'
        module = custom / 'nexo-kit/runtime/nexo_kit/runner.py'
        module.parent.mkdir(parents=True)
        write_json(custom / 'nexo-kit/install-manifest.json', {'scope': 'personal', 'files': {}})
        (self.home / '.codex').mkdir()
        with patch('nexo_kit.runner.__file__', str(module)), patch('nexo_kit.runner.Path.home', return_value=self.home):
            result = worker_environment('codex', {})
        self.assertEqual(result, {'CODEX_HOME': str(custom)})

    def test_unowned_runtime_layout_does_not_select_an_unrelated_profile(self):
        custom = self.root / 'unowned directory'
        module = custom / 'nexo-kit/runtime/nexo_kit/runner.py'
        module.parent.mkdir(parents=True)
        fallback = self.home / '.codex'
        fallback.mkdir()
        with patch('nexo_kit.runner.__file__', str(module)), patch('nexo_kit.runner.Path.home', return_value=self.home):
            result = worker_environment('codex', {})
        self.assertEqual(result, {'CODEX_HOME': str(fallback)})

    def test_other_backends_do_not_get_codex_profile_or_home_changes(self):
        (self.home / '.codex').mkdir()
        for backend in ('claude', 'command'):
            with self.subTest(backend=backend):
                self.assertEqual(self.environment({'PATH': 'keep'}, backend), {'PATH': 'keep'})

    def test_missing_profile_is_not_created_and_home_lookup_failure_is_tolerated(self):
        self.assertEqual(self.environment({}), {})
        self.assertFalse((self.home / '.codex').exists())
        with patch('nexo_kit.runner.__file__', str(self.source)), patch('nexo_kit.runner.Path.home', side_effect=RuntimeError('unavailable')):
            self.assertEqual(worker_environment('codex', {'PATH': 'keep'}), {'PATH': 'keep'})

    def test_worker_command_keeps_workspace_sandbox_and_no_model_override(self):
        command = worker_command('codex', self.root, self.root / 'prompt', self.root / 'answer')
        self.assertEqual(command[command.index('--sandbox') + 1], 'workspace-write')
        self.assertNotIn('--model', command)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)

    @unittest.skipUnless(shutil.which('git'), 'Git required for actual runner fixture')
    def test_real_worker_subprocess_receives_resolved_profile_and_finishes_gates(self):
        profile = self.home / '.codex'
        profile.mkdir()
        project = self.root / 'project'
        project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True, capture_output=True)
        (project / 'fixture.txt').write_text('Fixture repository.\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(project), 'add', '.'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(project), '-c', 'user.name=Nexo Test', '-c', 'user.email=test@local.invalid',
                        'commit', '-qm', 'Fixture'], check=True, capture_output=True)
        initialize_project(project)
        with Store(project) as store:
            task = store.add_task('Observe worker profile')
        check = ('import json; from pathlib import Path; '
                 'assert json.loads(Path("observed.json").read_text())["profile"] == ' + repr(str(profile)))
        add_check(project, 'profile-observed', [sys.executable, '-B', '-c', check])
        script = ('import json,os; from pathlib import Path; '
                  'Path("observed.json").write_text(json.dumps({"profile":os.environ.get("CODEX_HOME")}))')
        with patch('nexo_kit.runner.__file__', str(self.source)), \
                patch('nexo_kit.runner.Path.home', return_value=self.home), \
                patch.dict(os.environ, {'CODEX_HOME': ''}), \
                patch('nexo_kit.runner.worker_command', return_value=[sys.executable, '-B', '-c', script]):
            result = run_task(project, task['id'], 'codex', None, isolated=False)
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(json.loads((project / 'observed.json').read_text())['profile'], str(profile))


if __name__ == '__main__':
    unittest.main()
