"""Opt-in real native Codex probe in a fresh fixture, without the CLI runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from codex_integration_probe import Client, prepare
from nexo_kit.evidence import validate_evidence
from nexo_kit.store import Store


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-model', action='store_true')
    args = parser.parse_args()
    if not args.run_model or not args.codex:
        parser.error('Explicit --run-model and an available Codex executable are required')
    args.output.mkdir(parents=True, exist_ok=True)
    fixture_root = Path(tempfile.mkdtemp(prefix='nexo-real-codex-native-'))
    project = prepare(fixture_root, 'gamma')
    hashes = {name: sha(project / name) for name in ('test_arithmetic.py', '.nexo/config.json')}
    report = {'fixture': str(project), 'codex': args.codex, 'python': sys.executable,
              'mode': 'native-subagents-and-mcp', 'cli_runner_used': False,
              'baseline_sha256': hashes, 'passed': False}

    def save():
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    save()
    client = Client(args.codex, project, args.output)
    try:
        report['skills'] = client.request('skills/list', {'cwds': [str(project)], 'forceReload': True})
        report['hooks'] = client.request('hooks/list', {'cwds': [str(project)]})
        started = client.request('thread/start', {'cwd': str(project), 'approvalPolicy': 'on-request',
                                                  'sandbox': 'workspace-write'})
        thread = started['thread']['id']
        report['thread_id'] = thread
        client.allowed_nexo_threads[thread] = project.resolve()
        report['mcp'] = client.request('mcpServerStatus/list', {
            'threadId': thread, 'serverName': 'nexo', 'detail': 'toolsAndAuthOnly'})
        save()
        print('THREAD GAMMA', thread, flush=True)
        prompt = f'''Prueba real autorizada de Nexo en fixture NUEVO y aislado: {project}.
Usa nexo-orchestrate en su modo de subagentes nativos y MCP. Nunca ejecutes nexo.py run, codex exec ni otro runner CLI; no salgas de este proyecto ni cambies ACLs, permisos, perfiles, hooks, skills o configuración.
Objetivo: reparar arithmetic.add, que lanza NotImplementedError, para devolver a+b conservando su firma. Solo arithmetic.py puede modificarse; test_arithmetic.py y .nexo/config.json deben conservar exactamente sus bytes. Un único ciclo: crea EXACTAMENTE UNA tarea MCP nueva y no la reintentes si falla.
1. Consulta nexo_status y nexo_memory_search query NEXO_GAMMA. Reproduce antes los tres tests fallidos ejecutando el Python {sys.executable!r} con -B -m unittest -v; conserva el resultado.
2. Crea la tarea mediante nexo_task_add con contrato preciso y reclámala mediante nexo_task_claim con lease_seconds=900. Guarda ID y token reales. La tarea se implementa en este mismo checkout, no en worktrees. No crees metadatos de workspace ni edites SQLite.
3. Usa spawn_agent con agent_type="nexo-implementer", fork_turns="none". Dale cwd absoluto, contrato, archivo permitido y prohibición de runner o trabajo de coordinación. El implementador debe modificar solo arithmetic.py a return a+b y entregar el diff. Espera que termine; no sustituyas el rol por instrucciones copiadas. Renueva lease antes del siguiente paso si procede.
4. Usa spawn_agent con agent_type="nexo-tester", fork_turns="none", pasando cwd y ruta Python anteriores. Debe ejecutar los tres tests EXISTENTES con -B -m unittest -v y reportar resultado sin modificar ningún archivo. Espera su finalización. Tú, coordinador, revisa diff, firma, alcance y evidencia independientemente mediante nexo-review.
5. Si todo pasa, nexo_task_finish con el ID/token y status="review", y nexo_verify_task con task_id y complete=true. No marques éxito sin recibo passed y tarea done. Consulta nexo_status final y checkpoint. Si una herramienta falla, informa y deja la evidencia sin eludir el control ni intentar cambios de permisos.
No hagas commits ni push. No instales dependencias ni publiques. Todos los cambios de estado se hacen por MCP. Devuelve IDs de tarea y dos roles realmente invocados, estado final y qué se comprobó.'''
        client.turn(thread, prompt, ['nexo-orchestrate', 'nexo-build', 'nexo-debug', 'nexo-review', 'nexo-verify'])
        report['thread'] = client.request('thread/read', {'threadId': thread, 'includeTurns': True})
        children = {event['params']['item']['agentThreadId'] for event in client.events
                    if event['method'] == 'item/completed'
                    and event['params']['item'].get('type') == 'subAgentActivity'
                    and event['params']['item'].get('kind') == 'started'}
        report['native_roles'] = []
        for child in sorted(children):
            info = client.request('thread/read', {'threadId': child, 'includeTurns': False})['thread']
            report['native_roles'].append({key: info.get(key) for key in ('id', 'agentRole', 'cwd', 'source')})
        with Store(project) as store:
            report['tasks'] = store.list_tasks()
        report['final_sha256'] = {name: sha(project / name) for name in hashes}
        calls = [event['params']['item'] for event in client.events
                 if event['method'] == 'item/completed' and event['params'].get('threadId') == thread
                 and event['params']['item'].get('type') == 'mcpToolCall'
                 and event['params']['item'].get('server') == 'nexo']
        good = [item for item in calls if item.get('status') == 'completed' and not item.get('error')
                and not (item.get('result') or {}).get('isError')]
        report['mcp_tools_succeeded'] = sorted({item['tool'] for item in good})
        report['mcp_failures'] = [{'tool': item['tool'], 'error': item.get('error'), 'result': item.get('result')}
                                  for item in calls if item not in good]
        tasks = report['tasks']
        evidence_valid = False
        if len(tasks) == 1 and tasks[0].get('status') == 'done':
            validate_evidence(project, tasks[0].get('evidence') or {}, project)
            evidence_valid = True
        required = {'nexo_' + suffix for suffix in ('status', 'memory_search', 'task_add', 'task_claim',
                                                   'task_finish', 'verify_task', 'checkpoint')}
        role_counts = {role: sum(item['agentRole'] == role for item in report['native_roles'])
                       for role in ('nexo-implementer', 'nexo-tester')}
        report['checks'] = {
            'one_task_done_one_attempt': len(tasks) == 1 and tasks[0]['status'] == 'done' and tasks[0]['attempts'] == 1,
            'valid_evidence_for_final_source': evidence_valid,
            'tests_and_check_config_unchanged_bytes': report['final_sha256'] == hashes,
            'exact_two_native_roles': len(report['native_roles']) == 2 and all(count == 1 for count in role_counts.values())
                                      and all(Path(item['cwd']).resolve() == project.resolve() for item in report['native_roles']),
            'required_mcp_tools': required <= set(report['mcp_tools_succeeded']),
            'no_mcp_failures': not report['mcp_failures'],
            'no_cli_workspace_metadata': not (project / '.nexo/workspaces').exists(),
        }
        if len(tasks) == 1:
            task_id = tasks[0]['id']
            report['checks']['same_task_claim_finish_verify'] = all(any(
                item['tool'] == name and item.get('arguments', {}).get('task_id') == task_id
                and (name != 'nexo_verify_task' or item['arguments'].get('complete') is True)
                for item in good) for name in ('nexo_task_claim', 'nexo_task_finish', 'nexo_verify_task'))
        report['passed'] = all(report['checks'].values())
        save()
        print('CHECKS', json.dumps(report['checks']), flush=True)
        if not report['passed']:
            raise RuntimeError('Native clean probe failed: ' + ', '.join(key for key, value in report['checks'].items() if not value))
    except Exception as error:
        report['error'] = str(error)
        report['passed'] = False
        raise
    finally:
        save()
        client.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    main()
