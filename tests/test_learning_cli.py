"""Exercise the public CLI in subprocesses using disposable projects and profiles."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ENTRYPOINT = Path(__file__).resolve().parents[1] / 'nexo.py'


class LearningCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='nexo-learning-cli-')
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / 'Proyecto café 漢'
        self.project.mkdir()
        self.profile = self.root / "Perfil O'Brien $literal"
        self.environment = os.environ.copy()
        self.environment['CODEX_HOME'] = str(self.profile)
        self.environment['PYTHONUTF8'] = '1'
        self.environment.pop('PYTHONPATH', None)

    def tearDown(self):
        self.temporary.cleanup()

    def cli(self, *arguments: str, exit_code: int = 0) -> dict:
        process = subprocess.run([sys.executable, '-B', str(ENTRYPOINT), '--project', str(self.project),
                                  *arguments], cwd=self.project, env=self.environment,
                                 capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(process.returncode, exit_code, process.stderr + process.stdout)
        return json.loads(process.stdout if process.stdout.strip() else process.stderr)

    def initialize(self):
        self.cli('init', '--state-only', str(self.project))

    def test_list_in_uninitialized_project_is_read_only(self):
        result = self.cli('learn', 'list')
        self.assertFalse(result['initialized'])
        self.assertEqual(result['patterns'], [])
        self.assertEqual(list(self.project.iterdir()), [])
        self.assertFalse(self.profile.exists())

    def test_failed_record_returns_exit_one_with_failed_receipt(self):
        self.initialize()
        (self.project / 'resultado.txt').write_text('Evidencia sin modificar', encoding='utf-8')
        result = self.cli('learn', 'record', 'procedimiento-fallido', '--request-id', 'petición-uno',
                          '--summary', 'Comprobación deliberadamente fallida', '--evidence', 'resultado.txt',
                          '--check-json', json.dumps([sys.executable, '-c', 'raise SystemExit(7)']),
                          exit_code=1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['exit_code'], 7)
        self.assertEqual(result['pattern']['consecutive_successes'], 0)
        self.assertFalse(result['pattern']['eligible'])
        self.assertEqual(self.cli('learn', 'list')['patterns'][0]['last_status'], 'failed')

    def test_retry_and_history_cli_keep_one_real_request(self):
        self.initialize()
        (self.project / 'resultado.txt').write_text('Evidencia sin modificar', encoding='utf-8')
        arguments = ('procedimiento-fallido', '--request-id', 'petición-uno',
                     '--summary', 'Comprobación corregida', '--evidence', 'resultado.txt')
        failed = self.cli('learn', 'record', *arguments, '--check-json',
                          json.dumps([sys.executable, '-c', 'raise SystemExit(7)']), exit_code=1)
        retried = self.cli('learn', 'retry', *arguments, '--reason', 'Corregido el comprobador Unicode',
                           '--check-json', json.dumps([sys.executable, '-c',
                           'from pathlib import Path; assert Path("resultado.txt").read_text(encoding="utf-8") == "Evidencia sin modificar"']))
        self.assertEqual(retried['status'], 'passed')
        self.assertEqual(retried['pattern']['observations'], 1)
        history = self.cli('learn', 'history', 'procedimiento-fallido', '--request-id', 'petición-uno')
        self.assertEqual([run['status'] for run in history['runs']], ['failed', 'passed'])
        self.assertEqual(history['runs'][0]['receipt']['log'], failed['log'])
        self.assertEqual(history['runs'][1]['reason'], 'Corregido el comprobador Unicode')
        result = self.cli('learn', 'retry', *arguments, '--reason', 'No debe repetirse', '--check-json',
                          json.dumps([sys.executable, '-c', 'raise SystemExit(99)']), exit_code=2)
        self.assertIn('Only failed', result['error'])

    def test_personal_preferences_default_is_read_only_and_policy_flags_apply_only_to_install(self):
        self.assertEqual(self.cli('personal', 'preferences'),
                         {'automatic_routing': True, 'auto_learn': False, 'threshold': 3, 'auto_open_dashboard': False})
        for operation in ('preferences', 'uninstall'):
            for flag in ('--auto-learn', '--no-auto-learn'):
                result = self.cli('personal', operation, flag, exit_code=2)
                self.assertIn('install only', result['error'])
        preview = self.cli('personal', 'install', '--auto-learn', '--dry-run')
        self.assertTrue(preview['auto_learn'])
        self.assertTrue(preview['dry_run'])
        self.assertFalse(self.profile.exists())
        self.assertEqual(list(self.project.iterdir()), [])

    def test_full_cycle_preserves_json_argv_and_utf8_and_installs_only_in_temporary_profile(self):
        self.initialize()
        self.assertTrue(self.cli('personal', 'install', '--auto-learn')['auto_learn'])
        self.assertTrue(self.cli('personal', 'preferences')['auto_learn'])
        name = 'normalizar-texto-prueba'
        draft = self.project / '.nexo' / 'skill-drafts' / name
        (draft / 'scripts').mkdir(parents=True)
        (draft / 'SKILL.md').write_text(
            '---\nname: ' + name + '\ndescription: Normaliza texto Unicode y elimina espacios externos.\n---\n\n'
            '# Normalizar texto\n\nUsa scripts/normalize.py para obtener NFC sin espacios externos.\n',
            encoding='utf-8')
        (draft / 'scripts' / 'normalize.py').write_text(
            "import unicodedata\n\ndef normalize(text):\n    return unicodedata.normalize('NFC', text.strip())\n",
            encoding='utf-8')
        literal = 'comillas "dobles", apóstrofo \' | $HOME %PATH% & ñ漢😀'
        (self.project / 'check_inputs.py').write_text(
            'import importlib.util, json, sys\nfrom pathlib import Path\n'
            f'assert sys.argv[3] == {literal!r}\n'
            "spec = importlib.util.spec_from_file_location('normalizer', sys.argv[1])\n"
            'module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n'
            "case = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
            "assert module.normalize(case['input']) == case['expected']\n"
            "print(json.dumps({'argument': sys.argv[3], 'result': case['expected']}, ensure_ascii=False))\n",
            encoding='utf-8')

        def evidence(case_id, raw, expected):
            relative = 'caso-' + case_id + '-ñ漢.json'
            (self.project / relative).write_text(json.dumps({'input': raw, 'expected': expected},
                                                           ensure_ascii=False), encoding='utf-8')
            return relative

        def check(helper, file):
            return json.dumps([sys.executable, '-B', 'check_inputs.py', str(helper), file, literal],
                              ensure_ascii=False)

        for index, (raw, expected) in enumerate([('  café  ', 'café'), ('\t漢字\n', '漢字'),
                                                ('  Cafe\u0301 😀  ', 'Café 😀')], 1):
            file = evidence(str(index), raw, expected)
            result = self.cli('learn', 'record', 'normalizar-texto', '--request-id', f'petición-{index}',
                              '--summary', 'Normalización NFC comprobada de texto con acentos y símbolos',
                              '--evidence', file, '--evidence', 'check_inputs.py', '--check-json',
                              check(draft / 'scripts' / 'normalize.py', file))
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['pattern']['consecutive_successes'], index)
            self.assertEqual(result['command'][-1], literal)
            self.assertIn(literal, json.loads((self.project / result['log']).read_text(encoding='utf-8'))['argument'])
        self.assertTrue(result['pattern']['eligible'])
        staged = self.cli('learn', 'stage', 'normalizar-texto', '--skill-dir', str(draft))
        self.assertEqual(staged['status'], 'staged')
        staged_path = self.project / staged['path']
        file = evidence('nuevo', '\r\n A\u0301rbol 漢 😀 \t', 'Árbol 漢 😀')
        verified = self.cli('learn', 'validate', 'normalizar-texto', '--case-id', 'caso-nuevo-ñ漢',
                            '--evidence', file, '--evidence', 'check_inputs.py', '--check-json',
                            check(staged_path / 'scripts' / 'normalize.py', file))
        self.assertEqual(verified['status'], 'passed')
        self.assertEqual(verified['skill_sha256'], staged['signature'])
        installed = self.cli('learn', 'install', 'normalizar-texto', '--scope', 'personal',
                             '--codex-home', str(self.profile))
        self.assertEqual(installed['status'], 'installed')
        self.assertEqual(Path(installed['path']), self.profile / 'skills' / name)
        for relative in ('SKILL.md', 'scripts/normalize.py'):
            self.assertEqual((Path(installed['path']) / relative).read_bytes(),
                             (staged_path / relative).read_bytes())
        self.assertFalse((self.project / '.agents').exists())
        self.assertTrue(self.cli('learn', 'install', 'normalizar-texto', '--scope', 'personal',
                                 '--codex-home', str(self.profile))['unchanged'])


if __name__ == '__main__':
    unittest.main()
