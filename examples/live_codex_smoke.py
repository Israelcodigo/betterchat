"""Optional real CLI smoke. Explicitly opt in: python ... --run-model.

Uses your existing Codex authentication and may consume model usage. Creates
only a new temporary Git repository. No global settings or trust are changed.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
from nexo_kit.config import defaults
from nexo_kit.runner import run
from nexo_kit.store import Store
from nexo_kit.util import atomic_write, write_json
from nexo_kit.workspace import git


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-model', action='store_true', required=True)
    parser.parse_args()
    project = Path(tempfile.mkdtemp(prefix='nexo-codex-live-')).resolve()
    atomic_write(project / '.gitignore', '.nexo/\n__pycache__/\n*.pyc\n')
    atomic_write(project / 'sum_values.py', 'def add(a, b):\n    raise NotImplementedError\n')
    atomic_write(project / 'test_sum_values.py', '''import unittest
from sum_values import add
class SumTests(unittest.TestCase):
    def test_positive(self): self.assertEqual(add(2, 3), 5)
    def test_negative(self): self.assertEqual(add(-2, -3), -5)
    def test_zero(self): self.assertEqual(add(0, 0), 0)
''')
    git(project, 'init', '--quiet')
    git(project, 'add', '.')
    git(project, 'commit', '--quiet', '-m', 'Codex smoke fixture')
    config = defaults()
    config['runner'].update({'max_workers': 1, 'max_cycles': 1, 'timeout_seconds': 180})
    config['checks'] = [{'name': 'unit-tests', 'command': ['{python}', '-B', '-m', 'unittest', '-v'], 'timeout_seconds': 30}]
    write_json(project / '.nexo/config.json', config)
    with Store(project) as store:
        store.add_task('Implement addition', description='Implement add(a, b) in sum_values.py so the existing tests pass. '
                       'Only edit sum_values.py. Do not edit the tests. Do not run other agents or contact external services. '
                       'Use apply_patch or the available file editing tool. The coordinator runs Python tests.',
                       task_id='smoke', max_attempts=1)
    print(json.dumps({'started': True, 'project': str(project)}), flush=True)
    report = run(project, backend='codex', workers=1, cycles=1)
    write_json(project / '.nexo/live-result.json', report)
    print(json.dumps({'project': str(project), 'complete': report['complete'],
                      'runs': report['runs'], 'tasks': [{k: t[k] for k in ('id', 'status', 'error')} for t in report['tasks']]},
                     ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['complete'] else 1)


if __name__ == '__main__':
    main()
