"""Personal installation tests use disposable profiles, never the real home."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from nexo_kit.config import defaults
from nexo_kit.personal import EVENTS, MANIFEST, PREFERENCES, RUNTIME, install_personal, preferences, uninstall_personal
from nexo_kit.store import Store
from nexo_kit.util import atomic_write, write_json


class PersonalInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-personal-')
        self.home = Path(self.tmp.name) / "Codex profile O'Brien $nexo_test"
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_neither_initializes_profile_nor_creates_project_state(self):
        result = install_personal(self.home, dry_run=True)
        self.assertEqual(result['scope'], 'personal')
        self.assertEqual(result['state_scope'], 'active-project')
        self.assertTrue(result['hooks_require_native_trust'])
        self.assertIn('skills/nexo-plan/SKILL.md', result['files'])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_installs_nine_skills_four_roles_and_dynamic_mcp(self):
        install_personal(self.home)
        skills = list((self.home / 'skills').glob('nexo-*/SKILL.md'))
        roles = list((self.home / 'agents').glob('nexo-*.toml'))
        self.assertEqual(len(skills), 9)
        self.assertEqual(len(roles), 4)
        for path in skills:
            text = path.read_text(encoding='utf-8')
            self.assertNotIn('{{NEXO_COMMAND}}', text)
            self.assertNotIn('{{PROJECT}}', text)
            self.assertNotIn('--project', text.split('~~~')[1])
            self.assertIn('estado separado por proyecto', text)
        for path in roles:
            role = tomllib.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(role['name'], path.stem)
            self.assertIn('developer_instructions', role)
            self.assertNotIn('model', role)
        config = tomllib.loads((self.home / 'config.toml').read_text(encoding='utf-8'))
        self.assertEqual(config['mcp_servers']['nexo']['args'], [str(self.home / RUNTIME), 'mcp'])
        self.assertEqual(config['mcp_servers']['nexo']['command'], sys.executable)
        self.assertFalse((self.home / '.nexo').exists())
        instructions = (self.home / 'AGENTS.md').read_text(encoding='utf-8')
        self.assertIn('init --state-only .', instructions)
        self.assertIn('fork_turns="none"', instructions)

    def test_preserves_user_models_mcp_hooks_and_instructions(self):
        existing_hook = {'hooks': [{'type': 'command', 'command': 'user-owned-command'}]}
        hooks = {'userOption': True, 'hooks': {'SessionStart': [existing_hook],
                                            'UserPromptSubmit': [existing_hook]}}
        (self.home / 'hooks.json').write_text(json.dumps(hooks), encoding='utf-8')
        (self.home / 'config.toml').write_text('model = "user-model"\n[mcp_servers.other]\ncommand = "keep"\n', encoding='utf-8')
        (self.home / 'AGENTS.md').write_text('# User rules\nKeep my instructions.\n', encoding='utf-8')
        install_personal(self.home)
        current = json.loads((self.home / 'hooks.json').read_text(encoding='utf-8'))
        self.assertTrue(current['userOption'])
        self.assertEqual(current['hooks']['SessionStart'][0], existing_hook)
        self.assertEqual(current['hooks']['UserPromptSubmit'], [existing_hook])
        config = tomllib.loads((self.home / 'config.toml').read_text(encoding='utf-8'))
        self.assertEqual(config['model'], 'user-model')
        self.assertEqual(config['mcp_servers']['other']['command'], 'keep')
        self.assertIn('Keep my instructions.', (self.home / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_hooks_quote_windows_arguments_without_fixed_project(self):
        install_personal(self.home)
        hooks = json.loads((self.home / 'hooks.json').read_text(encoding='utf-8'))['hooks']
        self.assertEqual(set(hooks), set(EVENTS))
        for event, entries in hooks.items():
            handler = entries[0]['hooks'][0]
            expected = [sys.executable, str(self.home / RUNTIME), 'hook', '--event', event]
            self.assertEqual(shlex.split(handler['command']), expected)
            encoded = handler['commandWindows'].split()[-1]
            powershell = base64.b64decode(encoded).decode('utf-16-le')
            self.assertEqual(powershell, '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in expected))
            self.assertNotIn('--project', powershell)

    def test_reinstall_keeps_unrelated_new_config_and_does_not_duplicate_hooks(self):
        install_personal(self.home)
        path = self.home / 'config.toml'
        path.write_text('user_changed = true\n' + path.read_text(encoding='utf-8'), encoding='utf-8')
        install_personal(self.home)
        snapshot = {path.relative_to(self.home).as_posix(): path.read_bytes()
                    for path in self.home.rglob('*') if path.is_file()}
        install_personal(self.home)
        self.assertEqual(snapshot, {path.relative_to(self.home).as_posix(): path.read_bytes()
                                    for path in self.home.rglob('*') if path.is_file()})
        hooks = json.loads((self.home / 'hooks.json').read_text(encoding='utf-8'))['hooks']
        self.assertTrue(all(len(entries) == 1 for entries in hooks.values()))
        self.assertTrue(tomllib.loads(path.read_text(encoding='utf-8'))['user_changed'])

    def test_rejects_unowned_mcp_and_asset_collisions_before_writing(self):
        for location, content in [('config.toml', '[mcp_servers.nexo]\ncommand = "other"\n'),
                                  ('skills/nexo-plan/SKILL.md', 'User owned skill')]:
            with self.subTest(location=location):
                home = self.home / location.split('/')[0]
                destination = home / location
                destination.parent.mkdir(parents=True)
                destination.write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    install_personal(home)
                self.assertEqual(destination.read_text(encoding='utf-8'), content)
                self.assertFalse((home / 'nexo-kit').exists())

    def test_invalid_config_and_hooks_abort_without_runtime_write(self):
        for filename, content in [('config.toml', 'model = "unterminated'), ('hooks.json', '{"hooks": []}')]:
            with self.subTest(filename=filename):
                home = self.home / filename
                home.mkdir()
                (home / filename).write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    install_personal(home)
                self.assertFalse((home / 'nexo-kit').exists())

    def test_modified_skill_is_not_overwritten(self):
        install_personal(self.home)
        skill = self.home / 'skills/nexo-build/SKILL.md'
        content = skill.read_text(encoding='utf-8') + '\nPersonal edit.\n'
        skill.write_text(content, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'modified file'):
            install_personal(self.home)
        self.assertEqual(skill.read_text(encoding='utf-8'), content)

    def test_reinstall_rebases_backup_so_uninstall_retains_later_user_changes(self):
        install_personal(self.home)
        config = self.home / 'config.toml'
        config.write_text('user_changed = true\n' + config.read_text(encoding='utf-8'), encoding='utf-8')
        agents = self.home / 'AGENTS.md'
        agents.write_text('# Added later\n' + agents.read_text(encoding='utf-8'), encoding='utf-8')
        hook_path = self.home / 'hooks.json'
        hooks = json.loads(hook_path.read_text(encoding='utf-8'))
        user_hook = {'hooks': [{'type': 'command', 'command': 'new user hook'}]}
        hooks['hooks']['Stop'].append(user_hook)
        hooks['newPreference'] = 'keep'
        hook_path.write_text(json.dumps(hooks), encoding='utf-8')
        install_personal(self.home)
        result = uninstall_personal(self.home)
        self.assertTrue(result['complete'])
        self.assertEqual(tomllib.loads(config.read_text(encoding='utf-8')), {'user_changed': True})
        self.assertIn('# Added later', agents.read_text(encoding='utf-8'))
        self.assertNotIn('nexo-kit:start', agents.read_text(encoding='utf-8'))
        self.assertEqual(json.loads(hook_path.read_text(encoding='utf-8')),
                         {'newPreference': 'keep', 'hooks': {'Stop': [user_hook]}})

    def test_reinstall_refuses_changes_within_owned_managed_blocks_or_hooks(self):
        for filename in ('AGENTS.md', 'config.toml', 'hooks.json'):
            with self.subTest(filename=filename):
                home = self.home / filename
                install_personal(home)
                path = home / filename
                if filename == 'hooks.json':
                    content = json.loads(path.read_text(encoding='utf-8'))
                    content['hooks']['Stop'][0]['hooks'][0]['timeout'] = 99
                    changed = json.dumps(content)
                else:
                    changed = path.read_text(encoding='utf-8').replace('20', '21') if filename == 'config.toml' else path.read_text(encoding='utf-8').replace('estado local', 'estado personalizado')
                path.write_text(changed, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'user changes'):
                    install_personal(home)
                self.assertEqual(path.read_text(encoding='utf-8'), changed)

    def test_write_failure_restores_exact_original_profile(self):
        originals = {'config.toml': b'\xef\xbb\xbfmodel = "keep"\r\n',
                     'AGENTS.md': b'# My rules\r\nExact bytes.\r\n'}
        for name, content in originals.items():
            (self.home / name).write_bytes(content)
        failed = False

        def fail_once(path, text):
            nonlocal failed
            atomic_write(path, text)
            if path.name == 'config.toml' and not failed:
                failed = True
                raise OSError('Injected personal write failure')

        with patch('nexo_kit.install.atomic_write', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'Injected personal'):
                install_personal(self.home)
        self.assertTrue(failed)
        remaining = {path.relative_to(self.home).as_posix(): path.read_bytes()
                     for path in self.home.rglob('*') if path.is_file()}
        self.assertEqual(remaining, originals)

    def test_uninstall_restores_originals_and_retains_separate_project_state(self):
        originals = {'config.toml': b'\xef\xbb\xbfmodel = "keep"\r\n',
                     'AGENTS.md': b'# My rules\r\nExact bytes.\r\n',
                     'hooks.json': b'{\r\n  "userOption": true\r\n}\r\n'}
        for name, content in originals.items():
            (self.home / name).write_bytes(content)
        project = Path(self.tmp.name) / 'actual-project'
        with Store(project) as store:
            task = store.add_task('Keep me')
        install_personal(self.home)
        preview = uninstall_personal(self.home, dry_run=True)
        self.assertTrue(preview['complete'])
        self.assertTrue((self.home / RUNTIME).exists())
        result = uninstall_personal(self.home)
        self.assertTrue(result['complete'])
        self.assertFalse((self.home / MANIFEST).exists())
        for name, content in originals.items():
            self.assertEqual((self.home / name).read_bytes(), content)
        with Store(project) as store:
            self.assertEqual(store.get_task(task['id'])['title'], 'Keep me')

    def test_partial_uninstall_preserves_modified_config_and_live_runtime(self):
        install_personal(self.home)
        path = self.home / 'config.toml'
        changed = 'user_changed = true\n' + path.read_text(encoding='utf-8')
        path.write_text(changed, encoding='utf-8')
        result = uninstall_personal(self.home)
        self.assertFalse(result['complete'])
        self.assertIn('config.toml', result['preserved_modified'])
        self.assertTrue(result['runtime_retained'])
        self.assertEqual(path.read_text(encoding='utf-8'), changed)
        self.assertTrue((self.home / RUNTIME).exists())
        self.assertTrue((self.home / MANIFEST).exists())

    def test_copied_runtime_runs_without_checkout_and_scopes_state_by_cwd(self):
        install_personal(self.home)
        environment = os.environ.copy()
        environment.pop('PYTHONPATH', None)
        environment['PYTHONUTF8'] = '1'
        for name in ('alpha', 'beta'):
            project = Path(self.tmp.name) / name
            write_json(project / '.nexo/config.json', defaults())
            with Store(project) as store:
                store.add_task(name + ' task')
            run = subprocess.run([sys.executable, str(self.home / RUNTIME), 'status'], cwd=project,
                                 env=environment, capture_output=True, text=True, encoding='utf-8', timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            result = json.loads(run.stdout)
            self.assertEqual(Path(result['project']), project)
            self.assertEqual([task['title'] for task in result['tasks']], [name + ' task'])
        self.assertFalse((self.home / '.nexo').exists())

    def test_preferences_are_read_only_and_auto_learning_requires_opt_in(self):
        self.assertEqual(preferences(self.home),
                         {'automatic_routing': True, 'auto_learn': False, 'threshold': 3, 'auto_open_dashboard': False})
        self.assertEqual(list(self.home.iterdir()), [])
        result = install_personal(self.home)
        self.assertFalse(result['auto_learn'])
        self.assertEqual(preferences(self.home),
                         {'automatic_routing': True, 'auto_learn': False, 'threshold': 3, 'auto_open_dashboard': False})
        self.assertIn('Automejora: desactivada.', (self.home / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_explicit_opt_in_survives_reinstall_and_can_be_disabled(self):
        installed = install_personal(self.home, auto_learn=True)
        self.assertTrue(installed['auto_learn'])
        self.assertTrue(preferences(self.home)['auto_learn'])
        self.assertIn('Automejora: activada por autorización del usuario',
                      (self.home / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertTrue(install_personal(self.home)['auto_learn'])
        self.assertTrue(preferences(self.home)['auto_learn'])
        self.assertFalse(install_personal(self.home, auto_learn=False)['auto_learn'])
        self.assertFalse(preferences(self.home)['auto_learn'])
        self.assertIn('Automejora: desactivada.', (self.home / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_preferences_opt_in_dry_run_changes_no_files(self):
        self.assertTrue(install_personal(self.home, dry_run=True, auto_learn=True)['auto_learn'])
        self.assertEqual(list(self.home.iterdir()), [])
        install_personal(self.home, auto_learn=True)
        snapshot = {path.relative_to(self.home).as_posix(): path.read_bytes()
                    for path in self.home.rglob('*') if path.is_file()}
        self.assertFalse(install_personal(self.home, dry_run=True, auto_learn=False)['auto_learn'])
        self.assertEqual(snapshot, {path.relative_to(self.home).as_posix(): path.read_bytes()
                                    for path in self.home.rglob('*') if path.is_file()})
        self.assertTrue(preferences(self.home)['auto_learn'])

    def test_preserves_unknown_preferences_when_reinstalled_and_restores_on_uninstall(self):
        policy = {'automatic_routing': True, 'auto_learn': False, 'threshold': 3,
                  'future_feature': {'enabled': False}}
        original = json.dumps(policy, ensure_ascii=False, indent=2) + '\n'
        path = self.home / PREFERENCES
        path.parent.mkdir(parents=True)
        path.write_bytes(original.encode('utf-8'))
        install_personal(self.home)
        self.assertEqual(preferences(self.home)['future_feature'], {'enabled': False})
        install_personal(self.home, auto_learn=True)
        self.assertEqual(preferences(self.home)['future_feature'], {'enabled': False})
        self.assertTrue(uninstall_personal(self.home)['complete'])
        self.assertEqual(path.read_text(encoding='utf-8'), original)

    def test_rejects_malformed_policy_before_writing_runtime(self):
        for invalid in ([], {'auto_learn': 'true'}, {'automatic_routing': 1}, {'threshold': 2}):
            with self.subTest(invalid=invalid):
                home = self.home / str(len(list(self.home.iterdir())))
                path = home / PREFERENCES
                path.parent.mkdir(parents=True)
                original = json.dumps(invalid)
                path.write_text(original, encoding='utf-8')
                with self.assertRaises(ValueError):
                    install_personal(home)
                self.assertEqual(path.read_text(encoding='utf-8'), original)
                self.assertFalse((home / RUNTIME).exists())
        with self.assertRaises(ValueError):
            install_personal(self.home, auto_learn='true')

    def test_does_not_overwrite_user_modified_preferences(self):
        install_personal(self.home)
        path = self.home / PREFERENCES
        policy = json.loads(path.read_text(encoding='utf-8'))
        policy['future_feature'] = 'user-owned'
        modified = json.dumps(policy)
        path.write_text(modified, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'modified file'):
            install_personal(self.home, auto_learn=True)
        self.assertEqual(path.read_text(encoding='utf-8'), modified)


if __name__ == '__main__':
    unittest.main()
