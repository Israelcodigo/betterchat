"""Re-test the same failed task after the Windows child environment fix."""
import argparse
import ast
import json
from pathlib import Path
import sys
from codex_integration_probe import Client
from nexo_kit.actions import status
from nexo_kit.evidence import validate_evidence
from nexo_kit.store import Store


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--initial', type=Path, required=True)
    p.add_argument('--followup', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--native', action='store_true', help='Exercise the supported native orchestration route inside Codex')
    args = p.parse_args()
    initial = json.loads((args.initial / 'report.json').read_text(encoding='utf-8'))
    final_path = args.followup / 'report.json'
    final = json.loads(final_path.read_text(encoding='utf-8'))
    a = Path(initial['fixtures'][0]).resolve()
    assert a.parent.name.startswith('nexo-real-codex-')
    with Store(a) as store:
        failed = [t for t in store.list_tasks() if t['status'] == 'failed']
    assert len(failed) == 1 and failed[0]['attempts'] < failed[0]['max_attempts']
    args.output.mkdir(parents=True, exist_ok=True)
    client = Client(initial['codex'], a, args.output)
    report = {'fixture': str(a), 'task_id': failed[0]['id'], 'previous_error': failed[0]['error']}
    try:
        thread = client.request('thread/start', {'cwd': str(a), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})['thread']['id']
        client.allowed_nexo_threads[thread] = a
        report['thread'] = thread
        prompt = f'''Verificación real autorizada tras corregir el runtime Nexo instalado. La tarea {failed[0]['id']} falló antes de arrancar el modelo porque faltaba el directorio home en el entorno del Codex hijo. El runner instalado ahora conserva/resuelve explícitamente el perfil cuando sea necesario.
Usa nexo-debug y nexo-orchestrate. Inspecciona el estado y el error registrado, reabre esta misma tarea con task retry y ejecuta exactamente un nuevo intento de run --backend codex --workers 1 --cycles 1 --no-worktrees usando el runtime personal. No crees tareas nuevas ni edites manualmente arithmetic.py: el trabajador debe añadir el docstring. No cambies checks, modelos, sandbox ni permisos y no prepares variables de entorno manualmente para esconder un problema del runtime. Espera al proceso, consulta estado/evidencia y diff. No hagas push ni integres. Si falla, conserva y comunica el error sin repetir otro intento.'''
        if args.native:
            prompt = f'''Verifica el modo NATIVO de la skill nexo-orchestrate actualizada, dentro de este Codex Windows. El runner CLI anidado está bloqueado por las escrituras del sandbox en el perfil .codex. Esta prueba usa la API nativa de subagentes prevista por el host y no vuelve a ejecutar el CLI anidado ni cambia permisos.
Una única tarea existente, {failed[0]['id']}, está failed tras dos intentos de CLI. Reábrela mediante task retry del runtime personal, consúltala y reclámala por MCP nexo_task_claim con lease_seconds 900. Conserva el token y renueva si procede. Máximo un ciclo y un implementador activo.
Invoca el rol nativo nexo-implementer mediante spawn_agent agent_type y fork_turns="none", dale contrato y cwd. Debe añadir solo un docstring correcto a arithmetic.add, conservar return a+b y no tocar tests/config/perfil/estado. Espera su resultado y libera capacidad. Verifica mediante nexo-tester nativo los tres tests existentes (Python persistente) y revisa el diff como coordinador. No modifiques arithmetic.py tú mismo.
Si cumple, nexo_task_finish review con token y nexo_verify_task complete=true para terminar con evidencia. Si falla registra fallo sin otro reintento. No crees tareas nuevas, no cambies permisos/modelos/checks, no integres ni publiques. Devuelve ruta usada, IDs de roles, estado final y evidencia. No afirmes que el runner CLI anidado funcionó.'''
        client.turn(thread, prompt, ['nexo-debug', 'nexo-orchestrate'])
        report['thread_result'] = client.request('thread/read', {'threadId': thread, 'includeTurns': True})
        with Store(a) as store:
            task = store.get_task(failed[0]['id'])
        report['task_status'] = task['status']
        report['mode'] = 'native-subagents' if args.native else 'nested-cli'
        report['attempts'] = task['attempts']
        if task['status'] == 'done':
            validate_evidence(a, task['evidence'], a)
        function = next(n for n in ast.parse((a / 'arithmetic.py').read_text(encoding='utf-8')).body
                        if isinstance(n, ast.FunctionDef) and n.name == 'add')
        report['docstring'] = ast.get_docstring(function)
        report['passed'] = task['status'] == 'done' and bool(report['docstring'])
        if report['passed']:
            final['initial_followup_checks'] = dict(final['checks'])
            final['initial_followup_error'] = final.pop('error', None)
            final['orchestration_repair_thread'] = thread
            final['orchestration_mode'] = report['mode']
            final['final_state'] = status(a)
            final['checks']['two_done_tasks'] = len([t for t in final['final_state']['tasks'] if t['status'] == 'done']) == 2
            final['checks']['docstring_added'] = bool(report['docstring'])
            final['checks']['final_evidence_valid'] = True
            final['passed'] = all(final['checks'].values())
            final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
        print('RUNNER REPAIR', {k: report[k] for k in ('task_status', 'attempts', 'docstring', 'passed')}, flush=True)
        assert report['passed'], 'Worker environment repair did not complete the real task'
    finally:
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        client.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr): stream.reconfigure(encoding='utf-8')
    main()
