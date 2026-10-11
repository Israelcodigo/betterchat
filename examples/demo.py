"""Run a complete offline project, retaining its evidence for inspection.

From the kit directory: python examples/demo.py
Only a newly created temporary directory is modified. It is not auto-deleted.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

from nexo_kit.config import add_check
from nexo_kit.context import ContextIndex, export_checkpoint, promote_to_skill
from nexo_kit.install import install
from nexo_kit.runner import run
from nexo_kit.store import Store
from nexo_kit.util import atomic_write
from nexo_kit.workspace import git, integrate


def main():
    project = Path(tempfile.mkdtemp(prefix='nexo-demo-')).resolve()
    install(project, hooks=False, mcp=False)
    atomic_write(project / '.gitignore', '.nexo/\n__pycache__/\n*.pyc\n')
    atomic_write(project / 'README.md', 'Nexo offline demonstration\n')
    git(project, 'init', '--quiet')
    git(project, 'add', '.')
    git(project, 'commit', '--quiet', '-m', 'Demo starting point')
    add_check(project, 'unit-tests', ['{python}', '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    with Store(project) as store:
        store.add_task('Implement addition and its tests', task_id='arithmetic', max_attempts=2)
        store.add_task('Implement result formatting and tests', task_id='formatting')
        store.add_task('Compose both modules and test the application', task_id='application',
                       depends_on=['arithmetic', 'formatting'])
    report = run(project, backend='command', command=[sys.executable, str(KIT / 'examples/demo_worker.py')],
                 workers=2, cycles=2)
    if not report['complete']:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        raise SystemExit(1)
    with Store(project) as store:
        merged = integrate(project, store.get_task('application'))
        if not merged['integrated']:
            raise RuntimeError(merged)
        memory = store.add_memory('Addition and formatting are verified separately, then composed.',
                                  kind='lesson', source='demo:application; verified unit-test receipts')
        store.approve_memory(memory['id'])
        skill = promote_to_skill(project, store, memory['id'], 'demo-composition')
        checkpoint = export_checkpoint(project, store)
    with ContextIndex(project) as index:
        document = index.capture('Demo build: arithmetic repair succeeded; application passed all four tests.',
                                 source='demo', kind='verification')
        matches = index.search('arithmetic repair')
    result = {'project': str(project), 'complete': True,
              'tasks': [{k: t[k] for k in ('id', 'status', 'attempts')} for t in report['tasks']],
              'integrated': merged['integrated'], 'staged_learning_skill': str(skill),
              'checkpoint': checkpoint, 'context_document': document, 'search': matches,
              'model_used': False, 'network_used': False}
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
