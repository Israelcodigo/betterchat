"""Native recheck after changing the reviewed hook launcher or timeout."""
import argparse
import json
import os
from pathlib import Path
import sys
import tomllib
from codex_integration_probe import Client
from nexo_kit.personal import _personal_hooks


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--initial', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    prior = json.loads((args.initial / 'report.json').read_text(encoding='utf-8'))
    project = Path(prior['fixtures'][0])
    assert project.parent.name.startswith('nexo-real-codex-')
    args.output.mkdir(parents=True, exist_ok=True)
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()
    client = Client(prior['codex'], project, args.output)
    report = {'project': str(project)}
    try:
        inventory = client.request('hooks/list', {'cwds': [str(project)]})
        reviewed = _personal_hooks(home / 'nexo-kit/runtime/nexo.py')
        expected = {handler['commandWindows']: handler['timeout'] for groups in reviewed.values() for group in groups for handler in group['hooks']}
        entries = [entry for row in inventory['data'] for entry in row['hooks'] if
                   Path(entry['sourcePath']).resolve() == home / 'hooks.json' and
                   entry['command'] in expected and entry['timeoutSec'] == expected[entry['command']]]
        assert len(entries) == 4
        trust = tomllib.loads((home / 'config.toml').read_text(encoding='utf-8-sig')).get('hooks', {}).get('state', {})
        trust.update({entry['key']: {'trusted_hash': entry['currentHash']} for entry in entries})
        client.request('config/batchWrite', {'edits': [{'keyPath': 'hooks.state', 'value': trust, 'mergeStrategy': 'upsert'}], 'reloadUserConfig': True})
        report['inventory'] = client.request('hooks/list', {'cwds': [str(project)]})
        thread = client.request('thread/start', {'cwd': str(project), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})['thread']['id']
        client.allowed_nexo_threads[thread] = project.resolve()
        report['thread'] = thread
        client.turn(thread, 'Prueba breve de hooks: llama solo al MCP nexo_status y responde con el número de tareas. No hagas cambios.', ['nexo-resume'])
        client.compact(thread)
        client.turn(thread, 'Después de compactar, llama solo al MCP nexo_checkpoint y confirma el marcador de memoria de este proyecto. No hagas cambios.', ['nexo-resume'])
        hooks = [event['params']['run'] for event in client.events if event['method'] == 'hook/completed' and event['params']['threadId'] == thread]
        report['runs'] = hooks
        report['passed'] = (not any(h['status'] != 'completed' for h in hooks) and
                            {h['eventName'] for h in hooks} == {'sessionStart', 'postToolUse', 'preCompact', 'stop'})
        print('FINAL HOOK CHECK', report['passed'], [(h['eventName'], h['status'], h['durationMs']) for h in hooks], flush=True)
        assert report['passed']
    finally:
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        client.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr): stream.reconfigure(encoding='utf-8')
    main()
