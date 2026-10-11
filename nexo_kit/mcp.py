"""MCP stdio server with JSON-RPC, bounded input and explicit tool schemas.

No arbitrary command-execution tool is exposed. verify_task runs only gates
already configured in this project's config. Native MCP trust still applies.
"""
from __future__ import annotations

import json
import sys
import sqlite3
from pathlib import Path

from . import __version__
from .actions import initialize_project, status, verify_task
from .config import load_config
from .context import ContextIndex, checkpoint
from .store import Store
from .util import redact


def schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required), 'additionalProperties': False}


STRING = {'type': 'string'}
TOOLS = [
    ('status', 'Current task ledger and memory counts.', schema(), True),
    ('initialize', 'Initialize Nexo data in this session project when the user wants to track work. Does not install client assets.', schema(), False),
    ('ready_tasks', 'Tasks whose dependencies are done and can be claimed.', schema(), True),
    ('task_add', 'Add an explicitly requested task to the local DAG.', schema({
        'title': STRING, 'description': STRING, 'depends_on': {'type': 'array', 'items': STRING}}, ['title']), False),
    ('task_claim', 'Claim a ready task. Keep the lease token for heartbeat and finish.', schema({
        'task_id': STRING, 'owner': STRING, 'lease_seconds': {'type': 'integer'}}, ['task_id', 'owner']), False),
    ('task_heartbeat', 'Renew the current lease; an obsolete token is rejected.', schema({
        'task_id': STRING, 'token': STRING, 'lease_seconds': {'type': 'integer'}}, ['task_id', 'token']), False),
    ('task_finish', 'Submit work for review or record failure; does not mark done.', schema({
        'task_id': STRING, 'token': STRING, 'status': {'type': 'string', 'enum': ['review', 'failed', 'blocked']},
        'error': STRING}, ['task_id', 'token', 'status']), False),
    ('memory_search', 'Search active project memories only.', schema({'query': STRING}, ['query']), True),
    ('memory_propose', 'Record a candidate lesson with evidence; never activates it.', schema({
        'text': STRING, 'source': STRING, 'kind': STRING}, ['text', 'source']), False),
    ('context_search', 'Search captured output without injecting the full text.', schema({'query': STRING}, ['query']), True),
    ('context_fetch', 'Read a bounded range from a captured output ID.', schema({
        'id': STRING, 'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}}, ['id']), True),
    ('checkpoint', 'Reconstruct bounded context from persistent tasks and active memories.', schema(), True),
    ('verify_task', 'Run project-defined checks on the task workspace; optionally complete review.', schema({
        'task_id': STRING, 'complete': {'type': 'boolean'}}), False),
]


def tool_list() -> list[dict]:
    return [{'name': 'nexo_' + name, 'description': description, 'inputSchema': shape,
             'annotations': {'readOnlyHint': readonly, 'destructiveHint': not readonly, 'openWorldHint': False}}
            for name, description, shape, readonly in TOOLS]


def validate_arguments(arguments: dict, shape: dict) -> None:
    if not isinstance(arguments, dict):
        raise ValueError('arguments must be an object')
    properties = shape['properties']
    if set(arguments) - set(properties) or set(shape['required']) - set(arguments):
        raise ValueError('Unknown or missing tool arguments')
    types = {'string': str, 'integer': int, 'boolean': bool, 'array': list}
    for name, value in arguments.items():
        rule = properties[name]
        if not isinstance(value, types[rule['type']]) or (rule['type'] == 'integer' and isinstance(value, bool)):
            raise ValueError('Invalid type for ' + name)
        if rule['type'] == 'array' and not all(isinstance(item, str) for item in value):
            raise ValueError('Array must contain strings')
        if 'enum' in rule and value not in rule['enum']:
            raise ValueError('Invalid value for ' + name)


def call_tool(project: Path, name: str, arguments: dict) -> object:
    tool = next((t for t in TOOLS if 'nexo_' + t[0] == name), None)
    if not tool:
        raise ValueError('Unknown tool: ' + name)
    validate_arguments(arguments, tool[2])
    action = tool[0]
    a = arguments
    if action == 'status':
        return status(project)
    if action == 'initialize':
        return initialize_project(project)
    load_config(project)
    if action == 'verify_task':
        return verify_task(project, a.get('task_id'), a.get('complete', False))
    if action.startswith('context_'):
        with ContextIndex(project) as context:
            return (context.search(a['query']) if action == 'context_search'
                    else context.fetch(a['id'], a.get('offset', 0), a.get('limit', 4000)))
    with Store(project) as store:
        if action == 'ready_tasks': return store.ready_tasks()
        if action == 'task_add': return store.add_task(a['title'], a.get('description', ''), a.get('depends_on', []))
        if action == 'task_claim': return store.claim_task(a['task_id'], a['owner'], a.get('lease_seconds', 120))
        if action == 'task_heartbeat': return store.heartbeat(a['task_id'], a['token'], a.get('lease_seconds', 120))
        if action == 'task_finish': return store.finish_task(a['task_id'], a['token'], a['status'], error=a.get('error'))
        if action == 'memory_search': return store.search_memory(a['query'])
        if action == 'memory_propose': return store.add_memory(redact(a['text']), a.get('kind', 'lesson'), redact(a['source']))
        if action == 'checkpoint': return checkpoint(store, load_config(project)['context']['max_chars'])
    raise ValueError('Unknown operation')


def serve(project: Path, stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    initialized = False
    while True:
        line = stdin.readline(1_000_001)
        if not line:
            return
        request_id = None
        notification = False
        try:
            if len(line) > 1_000_000:
                while line and not line.endswith('\n'):
                    line = stdin.readline(1_000_001)
                raise ValueError('MCP input exceeds 1 MB')
            request = json.loads(line)
            if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
                raise ValueError('Expected a JSON-RPC 2.0 object')
            request_id = request.get('id')
            notification = 'id' not in request
            method, params = request.get('method'), request.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('params must be an object')
            if notification:
                continue
            if method == 'initialize':
                version = params.get('protocolVersion', '2025-03-26')
                if version not in {'2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25'}:
                    version = '2025-03-26'
                initialized = True
                result = {'protocolVersion': version, 'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'nexo-kit', 'version': __version__}}
            elif method == 'ping':
                result = {}
            elif not initialized:
                raise ValueError('initialize must be called first')
            elif method == 'tools/list':
                result = {'tools': tool_list()}
            elif method == 'tools/call':
                try:
                    value = call_tool(project, params.get('name', ''), params.get('arguments', {}))
                    result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
                except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
            else:
                response = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': 'Method not found'}}
                stdout.write(json.dumps(response) + '\n'); stdout.flush()
                continue
            response = {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except json.JSONDecodeError as exc:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': str(exc)}}
        except (ValueError, TypeError) as exc:
            if notification:
                continue
            response = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32600, 'message': str(exc)}}
        stdout.write(json.dumps(response, ensure_ascii=False) + '\n')
        stdout.flush()
