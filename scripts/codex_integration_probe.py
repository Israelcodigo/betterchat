"""Opt-in real Codex app-server probe; uses the existing account and model.

Creates disposable projects, uses native skill input/roles/MCP/hooks, and writes
only a filtered trace. Native hook trust is granted solely to reviewed Nexo
definitions from the personal install, using the same API as Codex /hooks.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexo_kit.actions import initialize_project
from nexo_kit.config import add_check
from nexo_kit.store import Store
from scripts.native_fixture import create_native_fixture


CORE_PERSONAL_SKILLS = frozenset('nexo-' + suffix for suffix in (
    'plan', 'build', 'debug', 'review', 'verify', 'memory', 'resume', 'orchestrate', 'learn'))


class Client:
    def __init__(self, codex, cwd, output):
        env = dict(os.environ)
        env.pop('NEXO_WORKER', None)
        self.stderr = (output / 'app-server.stderr.log').open('w', encoding='utf-8')
        self.proc = subprocess.Popen([codex, 'app-server', '--stdio'], cwd=cwd, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.stderr, text=True, encoding='utf-8')
        self.queue = queue.Queue()
        self.events = []
        self.responses = {}
        self.serial = 0
        # A caller must explicitly associate a native thread with a known
        # disposable project before this probe can approve its MCP writes.
        self.allowed_nexo_projects = {Path(cwd).resolve()}
        self.allowed_nexo_threads = {}
        self.trace = (output / 'native-events.jsonl').open('w', encoding='utf-8')
        threading.Thread(target=self.reader, daemon=True).start()
        self.request('initialize', {'clientInfo': {'name': 'nexo-integration-probe', 'version': '1.0.0'},
                                    'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized'})

    def reader(self):
        for line in self.proc.stdout:
            try:
                self.queue.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        self.queue.put({'probe_eof': True})

    def send(self, value):
        self.proc.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
        self.proc.stdin.flush()

    def pump(self, timeout=1):
        try:
            value = self.queue.get(timeout=timeout)
        except queue.Empty:
            return
        if value.get('probe_eof'):
            raise RuntimeError('App-server ended unexpectedly')
        if 'id' in value and 'method' not in value:
            self.responses[value['id']] = value
            return
        if 'id' in value:
            # Native Codex approval, not a server-authored instruction. Accept
            # only the exact reviewed local operations in disposable fixtures.
            params = value.get('params', {})
            meta = params.get('_meta') or {}
            schema = params.get('requestedSchema') or {}
            thread = params.get('threadId')
            fixture = self.allowed_nexo_threads.get(thread)
            mutation_names = {
                'nexo_initialize', 'nexo_task_add', 'nexo_task_claim',
                'nexo_task_heartbeat', 'nexo_task_finish', 'nexo_verify_task',
                'nexo_memory_propose',
            }
            # Codex's build_mcp_tool_approval_fallback_message defines this
            # exact text; current native metadata does not include tool_name.
            messages = {f'Allow the nexo MCP server to run tool "{name}"?': name
                        for name in mutation_names}
            tool = messages.get(params.get('message'))
            safe_request = (
                value.get('method') == 'mcpServer/elicitation/request'
                and params.get('serverName') == 'nexo'
                and params.get('mode') == 'form'
                and isinstance(meta, dict)
                and meta.get('codex_approval_kind') == 'mcp_tool_call'
                and isinstance(meta.get('tool_params'), dict)
                and tool is not None
                and fixture is not None
                and Path(fixture).resolve() in self.allowed_nexo_projects
                and isinstance(schema, dict)
                and schema.get('type') == 'object'
                and schema.get('properties') == {}
                and not schema.get('required')
            )
            if safe_request:
                # No persist=session/always, no global tool/profile change.
                self.send({'id': value['id'], 'result': {
                    'action': 'accept', 'content': {}, '_meta': None}})
                event = {'method': 'probe/approvalOnce', 'params': {
                    'threadId': thread, 'server': 'nexo', 'tool': tool,
                    'fixture': str(fixture), 'nativeRequestId': value['id']}}
                self.events.append(event)
                self.trace.write(json.dumps(event, ensure_ascii=False) + '\n')
                self.trace.flush()
                print('APPROVED ONCE', tool, flush=True)
                return
            # Every other request, form, operation and thread fails closed.
            self.send({'id': value['id'], 'error': {'code': -32601, 'message': 'Probe does not authorize interactive requests'}})
            raise RuntimeError('Unexpected server request: ' + value.get('method', ''))
        method = value.get('method', '')
        if method in {'hook/started', 'hook/completed', 'item/completed', 'turn/completed',
                      'thread/started', 'error', 'thread/compacted'}:
            self.events.append(value)
            self.trace.write(json.dumps(value, ensure_ascii=False) + '\n')
            self.trace.flush()
            params = value.get('params', {})
            if method == 'hook/completed':
                run = params.get('run', {})
                print('HOOK', run.get('eventName'), run.get('status'), flush=True)
            elif method == 'item/completed':
                item = params.get('item', {})
                print('ITEM', item.get('type'), item.get('tool', item.get('name', '')), flush=True)
            elif method == 'turn/completed':
                print('TURN', params.get('turn', {}).get('status'), flush=True)
            elif method == 'error':
                print('ERROR', json.dumps(params, ensure_ascii=False), flush=True)

    def request(self, method, params, timeout=90):
        self.serial += 1
        serial = self.serial
        self.send({'id': serial, 'method': method, 'params': params})
        end = time.monotonic() + timeout
        while serial not in self.responses:
            if time.monotonic() > end:
                raise TimeoutError(method)
            self.pump()
        response = self.responses.pop(serial)
        if 'error' in response:
            raise RuntimeError(method + ': ' + json.dumps(response['error']))
        return response['result']

    def wait(self, predicate, timeout=1200):
        end = time.monotonic() + timeout
        while True:
            result = next((event for event in self.events if predicate(event)), None)
            if result:
                return result
            if time.monotonic() > end:
                raise TimeoutError('Native event')
            self.pump()

    def turn(self, thread, prompt, skills=()):
        home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
        inputs = [{'type': 'skill', 'name': name, 'path': str(home / 'skills' / name / 'SKILL.md')} for name in skills]
        inputs.append({'type': 'text', 'text': prompt})
        turn = self.request('turn/start', {'threadId': thread, 'input': inputs,
                                          'approvalPolicy': 'on-request'})['turn']
        result = self.wait(lambda e: e['method'] == 'turn/completed' and
                           e['params']['threadId'] == thread and e['params']['turn']['id'] == turn['id'])
        if result['params']['turn']['status'] != 'completed':
            raise RuntimeError('Turn failed: ' + json.dumps(result['params']['turn']))
        return result

    def compact(self, thread):
        before = len(self.events)
        self.request('thread/compact/start', {'threadId': thread})
        # PreCompact runs before compaction, not after it. A new turn is only
        # legal when the native compact turn has actually completed.
        return self.wait(lambda e: e in self.events[before:] and e['params'].get('threadId') == thread
                         and (e['method'] == 'thread/compacted' or
                              (e['method'] == 'turn/completed' and e['params']['turn']['status'] == 'completed')), timeout=300)

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        self.trace.close()
        self.stderr.close()


def prepare(root, name):
    path = root / name
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    initialize_project(path)
    (path / '.gitignore').write_text('.nexo/\n__pycache__/\n', encoding='utf-8')
    (path / 'arithmetic.py').write_text('def add(a, b):\n    raise NotImplementedError("probe regression")\n', encoding='utf-8')
    (path / 'test_arithmetic.py').write_text(
        'import unittest\nfrom arithmetic import add\n\nclass ArithmeticTests(unittest.TestCase):\n'
        '    def test_positive(self): self.assertEqual(add(2, 3), 5)\n'
        '    def test_negative(self): self.assertEqual(add(-4, 1), -3)\n'
        '    def test_zero(self): self.assertEqual(add(0, 0), 0)\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=Nexo Probe', '-c', 'user.email=nexo-probe@localhost',
                    'commit', '-qm', 'Create integration fixture'], check=True)
    add_check(path, 'unit', [sys.executable, '-B', '-m', 'unittest', '-v'], 60)
    with Store(path) as store:
        memory = store.add_memory('Fixture isolation marker: NEXO_' + name.upper(), source='integration fixture')
        store.approve_memory(memory['id'])
    return path


def validate_real_report(report, events, alpha, beta):
    """Validate native observations and durable state, never the model's prose."""
    from nexo_kit.evidence import validate_evidence
    from nexo_kit.mcp import tool_list
    from nexo_kit.personal import _personal_hooks

    alpha, beta = Path(alpha).resolve(), Path(beta).resolve()
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).resolve()
    expected_skills = CORE_PERSONAL_SKILLS
    expected_roles = {'nexo-' + suffix for suffix in ('planner', 'implementer', 'tester', 'reviewer')}
    expected_hooks = {'sessionStart', 'postToolUse', 'stop', 'preCompact'}
    checks = {}
    errors = []

    for project in (alpha, beta):
        label = project.name
        entries = [entry for entry in report.get('skills', [])
                   if Path(entry.get('cwd', '')).resolve() == project]
        skills = entries[0].get('skills', []) if len(entries) == 1 else []
        core_skills = [skill for skill in skills if skill.get('name') in expected_skills]
        checks[label + '_core_personal_skills'] = (
            len(entries) == 1 and not entries[0].get('errors')
            and len(core_skills) == len(expected_skills)
            and {skill.get('name') for skill in core_skills} == expected_skills
            and all(skill.get('enabled') is True
                    and Path(skill.get('path', '')).resolve() == home / 'skills' / skill['name'] / 'SKILL.md'
                    for skill in core_skills))

    roles = report.get('child_roles', [])
    checks['four_native_roles'] = (
        {role.get('agentRole') for role in roles} == expected_roles
        and all(role.get('id') and Path(role.get('cwd', '')).resolve() == alpha for role in roles))

    reviewed = _personal_hooks(home / 'nexo-kit/runtime/nexo.py')
    commands = {handler[key] for groups in reviewed.values() for group in groups
                for handler in group['hooks'] for key in ('command', 'commandWindows') if key in handler}
    inventory = report.get('hooks_after', report.get('hooks_before', {})).get('data', [])
    nexo_hook_identities = set()
    for project in (alpha, beta):
        entries = [entry for entry in inventory if Path(entry.get('cwd', '')).resolve() == project]
        hooks = [hook for entry in entries for hook in entry.get('hooks', [])
                 if Path(hook.get('sourcePath', '')).resolve() == home / 'hooks.json'
                 and hook.get('command') in commands]
        nexo_hook_identities.update((hook['eventName'], str(Path(hook['sourcePath']).resolve()),
                                     hook['displayOrder']) for hook in hooks)
        checks[project.name + '_four_trusted_hooks'] = (
            len(entries) == 1 and not entries[0].get('errors')
            and len(hooks) == 4 and {hook.get('eventName') for hook in hooks} == expected_hooks
            and all(hook.get('enabled') is True and hook.get('trustStatus') == 'trusted' for hook in hooks))
    runs = [event['params']['run'] for event in events if event.get('method') == 'hook/completed'
            and event.get('params', {}).get('threadId') == report.get('thread_alpha')
            and (event['params']['run'].get('eventName'),
                 str(Path(event['params']['run'].get('sourcePath', '')).resolve()),
                 event['params']['run'].get('displayOrder')) in nexo_hook_identities]
    checks['four_hooks_executed'] = (
        {run.get('eventName') for run in runs if run.get('status') == 'completed'} == expected_hooks
        and all(run.get('status') == 'completed' for run in runs))
    checks['session_start_injected_alpha'] = any(
        run.get('eventName') == 'sessionStart' and any(
            entry.get('kind') == 'context' and 'NEXO_ALPHA' in entry.get('text', '')
            for entry in run.get('entries', [])) for run in runs)

    expected_tools = {tool['name'] for tool in tool_list()}
    for label in ('alpha', 'beta'):
        servers = [server for server in report.get('mcp_' + label, {}).get('data', [])
                   if server.get('name') == 'nexo']
        checks[label + '_mcp_connected'] = (
            len(servers) == 1 and servers[0].get('runtimeStatus') == 'connected'
            and servers[0].get('toolsError') is None
            and expected_tools <= set(servers[0].get('tools', {})))

    calls = []
    for event in events:
        item = event.get('params', {}).get('item', {})
        if event.get('method') != 'item/completed' or item.get('type') != 'mcpToolCall' or item.get('server') != 'nexo':
            continue
        result = item.get('result') or {}
        if item.get('status') != 'completed' or item.get('error') or result.get('isError'):
            errors.append('MCP call failed: ' + str(item.get('tool')))
            continue
        try:
            payload = result.get('structuredContent')
            if payload is None:
                payload = json.loads(''.join(block.get('text', '') for block in result.get('content', [])
                                            if block.get('type') == 'text'))
            calls.append((event['params'].get('threadId'), item.get('tool'), item.get('arguments') or {}, payload))
        except (ValueError, TypeError):
            errors.append('Unparseable native MCP result: ' + str(item.get('tool')))
    checks['no_failed_mcp_calls'] = not errors
    alpha_calls = [call for call in calls if call[0] == report.get('thread_alpha')]
    required = {'nexo_' + name for name in
                ('status', 'memory_search', 'task_add', 'task_claim', 'task_finish', 'verify_task', 'memory_propose', 'checkpoint')}
    checks['alpha_required_mcp_workflow'] = required <= {call[1] for call in alpha_calls}

    with Store(alpha) as store:
        tasks = store.list_tasks()
        memories = store.list_memories()
    with Store(beta) as store:
        beta_tasks = store.list_tasks()
        beta_memories = store.list_memories()
    done = [task for task in tasks if task.get('status') == 'done']
    checks['alpha_one_done_task'] = len(tasks) == 1 and len(done) == 1
    checks['done_task_valid_evidence'] = False
    checks['same_task_completed_through_mcp'] = False
    if len(done) == 1:
        task = done[0]
        try:
            validate_evidence(alpha, task.get('evidence') or {}, alpha)
            checks['done_task_valid_evidence'] = True
        except (ValueError, OSError) as error:
            errors.append('Evidence validation: ' + str(error))
        task_id = task['id']
        add_positions = [i for i, call in enumerate(alpha_calls) if call[1] == 'nexo_task_add'
                         and isinstance(call[3], dict) and call[3].get('id') == task_id]
        claim_positions = [i for i, call in enumerate(alpha_calls) if call[1] == 'nexo_task_claim'
                           and call[2].get('task_id') == task_id]
        finish_positions = [i for i, call in enumerate(alpha_calls) if call[1] == 'nexo_task_finish'
                            and call[2].get('task_id') == task_id and call[2].get('status') == 'review']
        verify_positions = [i for i, call in enumerate(alpha_calls) if call[1] == 'nexo_verify_task'
                            and call[2].get('task_id') == task_id and call[2].get('complete') is True
                            and isinstance(call[3], dict) and call[3].get('verdict') == 'passed'
                            and call[3].get('id') == (task.get('evidence') or {}).get('id')]
        checks['same_task_completed_through_mcp'] = any(
            add < claim < finish < verified for add in add_positions for claim in claim_positions
            for finish in finish_positions for verified in verify_positions)
    proposed_ids = {call[3].get('id') for call in alpha_calls if call[1] == 'nexo_memory_propose'
                    and isinstance(call[3], dict) and call[3].get('status') == 'candidate'}
    checks['lesson_stayed_candidate'] = bool(proposed_ids) and all(
        any(memory['id'] == identifier and memory['status'] == 'candidate' and memory.get('source')
            for memory in memories) for identifier in proposed_ids)
    beta_calls = [call for call in calls if call[0] == report.get('thread_beta')]
    checks['beta_mcp_status_isolated'] = any(
        name == 'nexo_status' and isinstance(payload, dict)
        and Path(payload.get('project', '')).resolve() == beta and payload.get('tasks') == []
        for _, name, _, payload in beta_calls)
    checks['beta_marker_isolated'] = any(
        name == 'nexo_memory_search' and isinstance(payload, list)
        and any('NEXO_BETA' in entry.get('text', '') for entry in payload)
        and all('NEXO_ALPHA' not in entry.get('text', '') for entry in payload)
        for _, name, _, payload in beta_calls)
    checks['beta_store_untouched'] = (not beta_tasks and len(beta_memories) == 1
                                    and 'NEXO_BETA' in beta_memories[0]['text'])
    return {'checks': checks, 'errors': errors, 'passed': all(checks.values()) and not errors}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--codex', default=shutil.which('codex'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--run-model', action='store_true')
    p.add_argument('--trust-reviewed-nexo-hooks', action='store_true')
    args = p.parse_args()
    if not args.codex:
        p.error('Codex executable required')
    args.output.mkdir(parents=True, exist_ok=True)
    root = create_native_fixture('nexo-real-codex-')
    a, b = prepare(root, 'alpha'), prepare(root, 'beta')
    report = {'fixtures': [str(a), str(b)], 'python': sys.executable, 'codex': args.codex}
    client = Client(args.codex, a, args.output)
    client.allowed_nexo_projects.add(b.resolve())
    def save():
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        skills = client.request('skills/list', {'cwds': [str(a), str(b)], 'forceReload': True})
        report['skills'] = [{'cwd': item['cwd'], 'skills': [s for s in item['skills'] if s['name'].startswith('nexo-')],
                             'errors': item.get('errors', [])} for item in skills['data']]
        print('SKILLS', [(x['cwd'], len(x['skills'])) for x in report['skills']], flush=True)
        hooks = client.request('hooks/list', {'cwds': [str(a), str(b)]})
        report['hooks_before'] = hooks
        save()
        print('HOOK INVENTORY', [(x['cwd'], [(h['eventName'], h['trustStatus']) for h in x['hooks']]) for x in hooks['data']], flush=True)
        if args.trust_reviewed_nexo_hooks:
            # Structure discovered from the native response; flatten only objects
            # with native trust keys and the exact expected personal source path.
            expected = (Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / 'hooks.json').resolve()
            entries = []
            def walk(value):
                if isinstance(value, dict):
                    if all(k in value for k in ('key', 'currentHash', 'sourcePath')):
                        entries.append(value)
                    for child in value.values(): walk(child)
                elif isinstance(value, list):
                    for child in value: walk(child)
            walk(hooks)
            definitions = json.loads(expected.read_text(encoding='utf-8'))
            from nexo_kit.personal import _personal_hooks
            reviewed = _personal_hooks(expected.parent / 'nexo-kit/runtime/nexo.py')
            commands = {handler['commandWindows'] for entry in reviewed.values() for group in entry for handler in group['hooks']}
            selected = [e for e in entries if Path(e['sourcePath']).resolve() == expected and e['command'] in commands]
            unique = {e['key']: {'trusted_hash': e['currentHash']} for e in selected}
            if len(unique) != 4:
                raise RuntimeError('Expected exactly four reviewed Nexo hook definitions, found ' + str(len(unique)))
            existing = tomllib.loads((expected.parent / 'config.toml').read_text(encoding='utf-8-sig')).get('hooks', {}).get('state', {})
            client.request('config/batchWrite', {'edits': [{'keyPath': 'hooks.state', 'value': {**existing, **unique}, 'mergeStrategy': 'upsert'}],
                                                'reloadUserConfig': True})
            report['hooks_after'] = client.request('hooks/list', {'cwds': [str(a), str(b)]})
            save()
        if not args.run_model:
            return
        started = client.request('thread/start', {'cwd': str(a), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})
        thread = started['thread']['id']
        client.allowed_nexo_threads[thread] = a.resolve()
        report['thread_alpha'] = thread
        report['mcp_alpha'] = client.request('mcpServerStatus/list', {'threadId': thread, 'serverName': 'nexo', 'detail': 'toolsAndAuthOnly'})
        save()
        print('THREAD ALPHA', thread, flush=True)
        # Additional learned skills may live in the project rather than the
        # personal directory. This core integration probe does not attach them.
        all_skills = sorted(CORE_PERSONAL_SKILLS)
        prompt = f'''Prueba real autorizada de Nexo en este repositorio desechable: {a}.
Usa las skills adjuntas cuando correspondan. Objetivo: reparar arithmetic.add y demostrar los roles nativos y MCP.
1. Llama al MCP nexo_status y nexo_memory_search('NEXO_'); crea una tarea para reparar add y reclámala con lease_seconds 900.
2. Invoca mediante spawn_agent los cuatro roles personalizados: nexo-planner, nexo-implementer, nexo-tester, nexo-reviewer. Usa agent_type con ese nombre y fork_turns="none". No copies el rol en el prompt como sustituto. Pasa cwd, contrato y los archivos.
Planner inspecciona y define un contrato breve; espera y ciérralo para liberar capacidad. Antes de reparar reproduce los tests fallidos con '{sys.executable}' -B -m unittest -v y usa nexo-debug. Implementer solo modifica arithmetic.py para sumar; espera su finalización. Tester ejecuta los tres tests existentes con ese Python; Reviewer inspecciona diff y contrato sin modificar. Todos usan este mismo checkout. No lances otro runner en este turno.
3. Usa MCP task_finish con el token para pasar a review, y nexo_verify_task complete=true para validar y terminar. Propón por MCP una lección candidata con fuente concreta; no la actives. Obtén checkpoint.
4. Resume hechos y cada ID/rol del subagente realmente invocado. Si no está disponible agent_type, informa el fallo. No sustituyas un rol por otro. No cambies checks, perfil, skills o hooks; no publiques ni hagas push. Esta prueba no exige integrar un worktree.
Incluye el marcador de memoria de este proyecto que recibiste al inicio si lo ves. No lo inventes.'''
        client.turn(thread, prompt, all_skills)
        report['alpha_after_work'] = client.request('thread/read', {'threadId': thread, 'includeTurns': True})
        save()
        client.compact(thread)
        client.turn(thread, 'Usa nexo-resume: llama nexo_checkpoint y nexo_status y confirma el estado después de compactar. No cambies nada.', ['nexo-resume'])
        report['alpha_final'] = client.request('thread/read', {'threadId': thread, 'includeTurns': True})
        # Native child metadata is independent of the parent's prose.
        children = {e['params']['item']['agentThreadId'] for e in client.events
                    if e['method'] == 'item/completed' and e['params']['item'].get('type') == 'subAgentActivity'
                    and e['params']['item'].get('kind') == 'started'}
        report['child_roles'] = []
        for child in children:
            info = client.request('thread/read', {'threadId': child, 'includeTurns': False})['thread']
            report['child_roles'].append({k: info.get(k) for k in ('id', 'agentRole', 'cwd', 'source')})
        started_b = client.request('thread/start', {'cwd': str(b), 'approvalPolicy': 'on-request', 'sandbox': 'workspace-write'})
        tb = started_b['thread']['id']
        client.allowed_nexo_threads[tb] = b.resolve()
        report['thread_beta'] = tb
        report['mcp_beta'] = client.request('mcpServerStatus/list', {'threadId': tb, 'serverName': 'nexo', 'detail': 'toolsAndAuthOnly'})
        client.turn(tb, 'Prueba de aislamiento: llama MCP nexo_status y nexo_memory_search con query NEXO_. Devuelve el proyecto, tareas y marcador. No inicialices ni modifiques nada.', ['nexo-resume'])
        report['beta_final'] = client.request('thread/read', {'threadId': tb, 'includeTurns': True})
        report['validation'] = validate_real_report(report, client.events, a, b)
        report['completed'] = False
        if not report['validation']['passed']:
            failed = [name for name, passed in report['validation']['checks'].items() if not passed]
            raise RuntimeError('Real integration checks failed: ' + ', '.join(failed)
                               + '; ' + '; '.join(report['validation']['errors']))
        report['completed'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        save()
        client.close()


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8')
    main()
