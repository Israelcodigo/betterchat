"""Opt-in live provider repair with coordinator-owned checks. Uses account quota."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
from nexo_kit.config import defaults
from nexo_kit.runner import run
from nexo_kit.store import Store
from nexo_kit.util import write_json
from nexo_kit.workspace import git, resolve_workspace
from scripts.native_fixture import create_native_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-model', action='store_true', required=True)
    parser.add_argument('--backend', choices=['codex', 'claude', 'gemini'], required=True)
    parser.add_argument('--model')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not shutil.which(args.backend):
        parser.error(f'{args.backend} is not installed in PATH; no model was called')
    project = create_native_fixture('nexo-provider-smoke-')
    (project / '.gitignore').write_text('.nexo/\n__pycache__/\n', encoding='utf-8')
    (project / 'normalize.py').write_text(
        'def normalize(values):\n    raise NotImplementedError\n', encoding='utf-8')
    git(project, 'init', '--quiet')
    git(project, 'add', '.')
    git(project, 'commit', '--quiet', '-m', 'Provider smoke fixture')
    config = defaults()
    config['runner'].update(max_workers=1, max_cycles=1, timeout_seconds=180)
    # The actual check is held by the coordinator, outside the worker worktree.
    check = ("from normalize import normalize; "
             "assert normalize([])==[]; "
             "assert normalize([' A ', 'b', 'a', '', ' B '])==['a','b']; "
             "assert normalize(['Straße','STRASSE','  É  '])==['strasse','é']; "
             "assert normalize(iter([' Z ','z','Y']))==['z','y']; "
             "print('4 independent cases passed')")
    config['checks'] = [{'name': 'normalization-contract',
                         'command': [sys.executable, '-B', '-c', check], 'timeout_seconds': 20}]
    write_json(project / '.nexo/config.json', config)
    with Store(project) as store:
        store.add_task('Normalize labels', task_id='repair', max_attempts=1, description=
            'Implement normalize(values) in normalize.py. Input is an iterable of strings. '
            'Strip surrounding whitespace, use casefold, drop empty strings and duplicates, '
            'preserve order of first occurrence, return a list. Only edit normalize.py. '
            'Do not spawn other agents, modify coordinator state, or contact external services. '
            'The coordinator runs independent checks. Use your normal file editing tool.')
    print(json.dumps({'started': True, 'backend': args.backend, 'project': str(project)}), flush=True)
    routing = {'version': 1, 'profiles': {'live': {'backend': args.backend}}, 'default': 'live'}
    if args.model:
        routing['profiles']['live']['model'] = args.model
    result = run(project, workers=1, cycles=1, routing=routing)
    write_json(project / '.nexo/live-result.json', result)
    workspace = resolve_workspace(project, 'repair')
    source = (workspace / 'normalize.py').read_bytes()
    summary = {
        'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'kind': 'live-provider-smoke', 'backend': args.backend,
        'requested_model': args.model, 'model_selection': 'explicit' if args.model else 'client-default',
        'complete': result['complete'], 'cases': 4,
        'case_result': 'passed' if result['complete'] else 'not_proven',
        'source_sha256': hashlib.sha256(source).hexdigest(),
        'runs': [{k: r.get(k) for k in ('status', 'profile', 'elapsed_seconds')} for r in result['runs']],
        'cost_usd': None,
        'limitations': 'One small repair; not a comparative benchmark. Cost and actual model are not inferred.'}
    if args.output:
        write_json(args.output, summary)
    print(json.dumps(summary, indent=2))
    print(json.dumps({'local_evidence': str(project / '.nexo/live-result.json')}))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
