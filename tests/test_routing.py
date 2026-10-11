"""Routing contracts and real offline subprocesses; no model calls."""
import copy
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

from nexo_kit.config import defaults
from nexo_kit.routing import resolve_routes, validate_profile
from nexo_kit.runner import _provider_error, run, worker_command
from nexo_kit.store import Store
from nexo_kit.util import write_json
from nexo_kit.workspace import git, resolve_workspace


class RoutingTests(unittest.TestCase):
    def document(self):
        return {'version': 1, 'profiles': {'build': {'backend': 'codex', 'model': 'chosen-model'},
                                         'review': {'backend': 'claude'}},
                'tasks': {'a': 'build', 'b': 'review'}}

    def test_exact_assignment_and_no_implicit_model(self):
        result = resolve_routes(self.document(), {'a', 'b'})
        self.assertEqual(result['a']['model'], 'chosen-model')
        self.assertEqual(result['b']['backend'], 'claude')
        self.assertNotIn('model', result['b'])

    def test_default_and_input_not_mutated(self):
        doc = self.document()
        doc.update(default='review', tasks={'a': 'build'})
        before = copy.deepcopy(doc)
        result = resolve_routes(doc, {'a', 'b'})
        result['a']['model'] = 'changed'
        self.assertEqual(doc, before)
        self.assertEqual(result['b']['profile'], 'review')

    def test_unknown_missing_and_misspelled_routes_rejected(self):
        for change in [{'tasks': {'typo': 'build'}}, {'tasks': {}},
                       {'default': 'missing'}, {'profiles': {}}, {'version': True},
                       {'defaults': 'build'}, {'tasks': {'a': [], 'b': 'review'}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                resolve_routes(dict(self.document(), **change), {'a', 'b'})

    def test_invalid_profiles_rejected(self):
        for profile in [{'backend': 'magic'}, {'backend': 'codex', 'model': '--yolo'},
                        {'backend': []}, {'backend': 'codex', 'model': 'bad\x00model'},
                        {'backend': 'claude', 'model': ''}, {'backend': 'gemini', 'model': 'a b'},
                        {'backend': 'command'}, {'backend': 'command', 'command': 'shell string'},
                        {'backend': 'command', 'command': ['python'], 'model': 'x'},
                        {'backend': 'codex', 'command': ['other']},
                        {'backend': 'codex', 'fallback': 'claude'}]:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate_profile(profile)

    def test_native_commands_preserve_permission_flags_and_model_argument(self):
        for backend in ('codex', 'claude', 'gemini'):
            args = worker_command(backend, Path('space folder'), Path('prompt'), Path('answer'), model='exact-id')
            self.assertEqual(args[args.index('--model') + 1], 'exact-id')
            self.assertNotIn('--yolo', args)
            self.assertNotIn('--dangerously-skip-permissions', args)
            if backend == 'codex':
                self.assertEqual(args[args.index('--sandbox') + 1], 'workspace-write')

    def test_multiline_error_envelopes_and_jsonl_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'output'
            for event in [{'error': {'message': 'quota'}}, {'is_error': True}, {'type': 'turn.failed'}]:
                for content in (json.dumps(event, indent=2), '{}\n' + json.dumps(event) + '\n'):
                    log.write_text(content, encoding='utf-8')
                    self.assertIsNotNone(_provider_error(log))
            log.write_text('{"response":"ok","error":null}', encoding='utf-8')
            self.assertIsNone(_provider_error(log))

    def test_mixed_profiles_pass_artifacts_across_real_worktrees(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            (project / '.gitignore').write_text('.nexo/\n', encoding='utf-8')
            (project / 'worker.py').write_text(
                "from pathlib import Path\nimport sys\n"
                "if sys.argv[1]=='build': Path('value.txt').write_text('42')\n"
                "else:\n assert Path('value.txt').read_text()=='42'\n Path('review.txt').write_text('verified')\n",
                encoding='utf-8')
            git(project, 'init', '--quiet')
            git(project, 'add', '.')
            git(project, 'commit', '--quiet', '-m', 'Fixture')
            config = defaults()
            config['checks'] = [{'name': 'artifact', 'command': [sys.executable, '-c',
                "from pathlib import Path; assert Path('value.txt').read_text()=='42'"]}]
            write_json(project / '.nexo/config.json', config)
            with Store(project) as store:
                store.add_task('Build', task_id='a')
                store.add_task('Review', task_id='b', depends_on=['a'])
            doc = {'version': 1, 'profiles': {
                key: {'backend': 'command', 'command': [sys.executable, 'worker.py', key]}
                for key in ('build', 'review')}, 'tasks': {'a': 'build', 'b': 'review'}}
            result = run(project, workers=1, cycles=1, routing=doc)
            self.assertTrue(result['complete'], result)
            self.assertEqual([r['profile'] for r in result['runs']], ['build', 'review'])
            self.assertTrue(all(r['elapsed_seconds'] > 0 for r in result['runs']))
            self.assertEqual((resolve_workspace(project, 'b') / 'review.txt').read_text(), 'verified')
            self.assertFalse((project / 'value.txt').exists())

    def test_bad_route_leaves_task_unclaimed(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config = defaults()
            config['checks'] = [{'name': 'unused', 'command': [sys.executable, '-V']}]
            write_json(project / '.nexo/config.json', config)
            with Store(project) as store:
                store.add_task('Never start', task_id='a')
            with self.assertRaises(ValueError):
                run(project, routing=self.document())
            with Store(project) as store:
                self.assertEqual(store.get_task('a')['attempts'], 0)

    def test_provider_error_with_success_exit_never_completes(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            config = defaults()
            config['checks'] = [{'name': 'would-pass', 'command': [sys.executable, '-V']}]
            write_json(project / '.nexo/config.json', config)
            with Store(project) as store:
                store.add_task('Provider fails', task_id='a', max_attempts=1)
            document = {'version': 1, 'profiles': {'bad': {'backend': 'command', 'command': [
                sys.executable, '-c', "import json; print(json.dumps({'error': {'message': 'quota'}}, indent=2))"]}},
                'default': 'bad'}
            result = run(project, workers=1, cycles=1, isolated=False, routing=document)
            self.assertFalse(result['complete'])
            self.assertEqual(result['tasks'][0]['status'], 'failed')
            self.assertIn('quota', result['tasks'][0]['error'])

    def test_cli_rejects_ambiguous_routing_before_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            write_json(project / '.nexo/config.json', defaults())
            with Store(project) as store:
                store.add_task('Do not run', task_id='a')
            cli = Path(__file__).resolve().parents[1] / 'nexo.py'
            result = subprocess.run([sys.executable, str(cli), '--project', str(project), 'run',
                '--routing-file', 'unused.json', '--model', 'some-model'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cannot be combined', result.stdout + result.stderr)
            with Store(project) as store:
                self.assertEqual(store.get_task('a')['attempts'], 0)


if __name__ == '__main__':
    unittest.main()
