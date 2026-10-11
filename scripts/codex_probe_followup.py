"""Continue the real probe after correcting its native approval policy."""
from pathlib import Path
import argparse
import json
import os
import sys
from codex_integration_probe import Client
from nexo_kit.actions import status
from nexo_kit.context import ContextIndex
from nexo_kit.store import Store


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--previous', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    before = json.loads((args.previous / 'report.json').read_text(encoding='utf-8'))
    a, b = map(Path, before['fixtures'])
    assert a.parent == b.parent and a.parent.name.startswith('nexo-real-codex-')
    args.output.mkdir(parents=True, exist_ok=True)
    client = Client(before['codex'], a, args.output)
    report = {'fixtures': before['fixtures'], 'previous_thread': before['thread_alpha']}
    def save():
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        events = [json.loads(line) for line in (args.previous / 'native-events.jsonl').read_text(encoding='utf-8').splitlines()]
        children = {e['params']['item']['agentThreadId'] for e in events
                    if e['method'] == 'item/completed' and e['params']['item'].get('type') == 'subAgentActivity'
                    and e['params']['item'].get('kind') == 'started'}
        report['child_roles'] = []
        for child in children:
            info = client.request('thread/read', {'threadId': child, 'includeTurns': False})['thread']
            report['child_roles'].append({k: info.get(k) for k in ('id', 'agentRole', 'cwd', 'source')})
        print('ROLES', json.dumps(report['child_roles'], ensure_ascii=False), flush=True)
        thread = client.request('thread/start', {'cwd': str(a), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})['thread']['id']
        client.allowed_nexo_threads[thread] = a.resolve()
        report['thread_alpha'] = thread
        save()
        with ContextIndex(a) as context:
            captured = context.capture('NEXO_CONTEXT_FIXTURE: arithmetic.add is independently tested for positive, negative, and zero.', source='probe controlled fixture')
        prompt = '''Continuación de prueba real Nexo autorizada. El cliente acepta una vez las escrituras MCP Nexo de este proyecto temporal mediante el control nativo. No uses CLI como alternativa si el MCP falla.
El código arithmetic.py ya fue reparado por el implementer nativo y revisado/testeado por los otros roles. No repitas roles ni modifiques código/tests/perfil.
Usa las skills: consulta nexo_status; llama nexo_initialize para comprobar inicialización idempotente; crea una tarea que describa validar la reparación add, consulta ready_tasks, reclámala con lease 300 y renueva con task_heartbeat. Conserva token; pasa a review con task_finish y completa mediante nexo_verify_task complete=true. Propón memoria candidata con fuente test_arithmetic.py y no la actives. Busca NEXO_CONTEXT_FIXTURE con context_search y recupera el ID real con context_fetch. Consulta checkpoint y status finales. Todos los pasos de estado deben ser herramientas MCP reales, sin editar SQLite ni recibos. Devuelve resultados verificados.'''
        client.turn(thread, prompt, ['nexo-plan', 'nexo-build', 'nexo-verify', 'nexo-memory'])
        report['mcp_workflow'] = client.request('thread/read', {'threadId': thread, 'includeTurns': True})
        with Store(a) as store:
            report['tasks_after_mcp'] = store.list_tasks()
        assert any(task['status'] == 'done' for task in report['tasks_after_mcp']), 'MCP workflow did not complete'
        save()
        client.compact(thread)
        client.turn(thread, 'Usa nexo-resume tras compactación: consulta nexo_memory_search query NEXO_, nexo_checkpoint y nexo_status. Confirma marcador y tarea terminada. No cambies nada.', ['nexo-resume'])
        report['after_compaction'] = client.request('thread/read', {'threadId': thread, 'includeTurns': True})
        save()
        client.turn(thread, '''Ejecuta ahora nexo-orchestrate en este repositorio temporal. Crea exactamente una tarea pendiente: añadir un docstring breve y correcto a arithmetic.add, sin cambiar comportamiento ni test_arithmetic.py. Ejecuta el runner real con backend codex, workers 1, cycles 1 y --no-worktrees (este ensayo usa el mismo checkout y evita anidar worktrees dentro del sandbox). No reclames manualmente esta tarea: el runner la reclama. Usa el Python persistente y runtime de las instrucciones globales. Espera el proceso hasta finalizar; consulta estado y evidencia. No publiques ni integres ni crees más tareas, no alteres checks, modelos o permisos. Debe haber un cambio visible de docstring y tests reales satisfactorios. Si algo falla, informa el error sin ocultarlo ni repetir indefinidamente.''', ['nexo-orchestrate'])
        report['after_orchestrate'] = client.request('thread/read', {'threadId': thread, 'includeTurns': True})
        report['final_state'] = status(a)
        save()
        client.allowed_nexo_projects.add(b.resolve())
        tb = client.request('thread/start', {'cwd': str(b), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})['thread']['id']
        client.allowed_nexo_threads[tb] = b.resolve()
        report['thread_beta'] = tb
        report['mcp_beta'] = client.request('mcpServerStatus/list', {'threadId': tb, 'serverName': 'nexo', 'detail': 'toolsAndAuthOnly'})
        client.turn(tb, 'Prueba de aislamiento Nexo: llama nexo_status y nexo_memory_search query NEXO_. Devuelve proyecto, tareas y marcador. No inicialices ni modifiques nada.', ['nexo-resume'])
        report['beta_final'] = client.request('thread/read', {'threadId': tb, 'includeTurns': True})
        required_mcp = {'nexo_' + x for x in ('status', 'initialize', 'task_add', 'ready_tasks', 'task_claim', 'task_heartbeat',
                                             'task_finish', 'verify_task', 'memory_propose', 'context_search', 'context_fetch', 'checkpoint')}
        successful = {e['params']['item']['tool'] for e in client.events if e['method'] == 'item/completed'
                      and e['params']['item'].get('type') == 'mcpToolCall' and e['params']['item'].get('status') == 'completed'
                      and not (e['params']['item'].get('result') or {}).get('isError')}
        report['successful_mcp'] = sorted(successful)
        report['missing_mcp'] = sorted(required_mcp - successful)
        report['checks'] = {'mcp_workflow': not report['missing_mcp'],
                            'four_native_roles': {x['agentRole'] for x in report['child_roles']} ==
                            {'nexo-planner', 'nexo-implementer', 'nexo-tester', 'nexo-reviewer'},
                            'two_done_tasks': len([t for t in report['final_state']['tasks'] if t['status'] == 'done']) == 2,
                            'beta_no_tasks': not status(b)['tasks'],
                            'docstring_added': '"""' in (a / 'arithmetic.py').read_text(encoding='utf-8') or
                                               "'''" in (a / 'arithmetic.py').read_text(encoding='utf-8')}
        report['passed'] = all(report['checks'].values())
        print('CHECKS', report['checks'], flush=True)
        assert report['passed'], 'Incomplete real probe: ' + str(report['checks'])
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        save()
        client.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr): stream.reconfigure(encoding='utf-8')
    main()
