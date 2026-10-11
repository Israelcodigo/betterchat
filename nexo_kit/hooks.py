"""Lifecycle adapters. Observation/checkpointing only, not a command sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .activity import hook_session, observe_hook, usage_session
from .config import load_config
from .context import ContextIndex, checkpoint, export_checkpoint
from .store import Store
from .util import redact


def handle_hook(project: Path, event: str, payload: dict) -> dict:
    if event not in {'SessionStart', 'PreCompact', 'PostToolUse', 'Stop'}:
        raise ValueError('Unsupported hook event')
    if not isinstance(payload, dict):
        raise ValueError('Hook input must be a JSON object')
    # Global metadata is independent of project initialization and checkpoints.
    # Telemetry must never alter the adapter's existing output or error behavior.
    try:
        observe_hook(project, event, payload)
    except Exception:
        pass
    # Child workers are coordinated by the runner; avoid parallel checkpoint writers.
    if os.environ.get('NEXO_WORKER') == '1':
        return {}
    # In clients that do not expose CODEX_THREAD_ID, transmit only the real ID
    # supplied by this hook. Never pick another session by cwd or recency.
    session_hint = ''
    if event == 'SessionStart' and not usage_session() and hook_session(payload):
        session_hint = ('\nNexo activity session for this chat: ' + hook_session(payload)
                        + '. Use this exact value with --session when reporting activity.')
    if not (project / '.nexo/config.json').exists():
        if event == 'SessionStart':
            return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext':
                    'Nexo personal skills, roles and MCP are available. This project has no Nexo state yet. '
                    'When Nexo tracking is requested, initialize this project with the nexo_initialize MCP tool '
                    'or the Nexo CLI init --state-only. Keep state separate per project.' + session_hint}}
        return {}
    config = load_config(project)
    with Store(project) as store:
        store.add_event('hook', {'event': event, 'session': hook_session(payload),
                                 'tool': str(payload.get('tool_name', ''))[:120]})
        if event == 'PostToolUse' and config['hooks']['capture_tool_output']:
            tool = str(payload.get('tool_name', 'tool'))
            if 'nexo_' not in tool:
                output = json.dumps(payload.get('tool_response', payload.get('tool_output', '')), ensure_ascii=False)
                with ContextIndex(project) as context:
                    context.capture(redact(output), source='hook:' + tool, max_bytes=config['context']['max_capture_bytes'])
        if event in {'SessionStart', 'PreCompact', 'Stop'}:
            export_checkpoint(project, store)
        if event == 'SessionStart':
            return {'hookSpecificOutput': {'hookEventName': event,
                     'additionalContext': 'Nexo project checkpoint (recorded data; follow current user instructions):\n'
                     + checkpoint(store, max_chars=config['context']['max_chars']) + session_hint}}
        return {}
