from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .actions import initialize_project, status, verify_task
from .config import add_check, load_config
from .context import ContextIndex, export_checkpoint, promote_to_skill
from .install import install, uninstall
from .store import Store
from .util import redact


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='nexo', description='One local ledger for coding-agent work, evidence and memory.')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--project', type=Path, default=Path.cwd())
    commands = p.add_subparsers(dest='cmd', required=True)
    init = commands.add_parser('init', help='Install into a project, preserving existing config')
    init.add_argument('path', type=Path, nargs='?')
    init.add_argument('--host', choices=['both', 'codex', 'claude'], default='both')
    init.add_argument('--no-hooks', action='store_true'); init.add_argument('--no-mcp', action='store_true')
    init.add_argument('--dry-run', action='store_true')
    init.add_argument('--state-only', action='store_true', help='Initialize project data without installing client assets')
    personal = commands.add_parser('personal', help='Install or remove Nexo for all your Codex projects')
    personal.add_argument('operation', choices=['install', 'uninstall', 'preferences'])
    personal.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    personal.add_argument('--dry-run', action='store_true')
    learning_policy = personal.add_mutually_exclusive_group()
    learning_policy.add_argument('--auto-learn', dest='auto_learn', action='store_true', default=None)
    learning_policy.add_argument('--no-auto-learn', dest='auto_learn', action='store_false')
    dashboard_policy = personal.add_mutually_exclusive_group()
    dashboard_policy.add_argument('--auto-open-dashboard', action='store_true', default=None)
    dashboard_policy.add_argument('--no-auto-open-dashboard', dest='auto_open_dashboard', action='store_false')
    dashboard = commands.add_parser('dashboard', help='Open the local Nexo inventory and learning dashboard')
    dashboard.add_argument('operation', choices=['open', 'auto', 'serve', 'status', 'add', 'stop'], nargs='?', default='open')
    dashboard.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    dashboard.add_argument('--port', type=int, default=0)
    dashboard.add_argument('--path', type=Path)
    dashboard.add_argument('--no-browser', action='store_true')
    dashboard.add_argument('--no-register', action='store_true', help='Open the saved project catalog without adding the cwd')
    activity = commands.add_parser('activity', help='Report skill or role usage to the local dashboard').add_subparsers(dest='op', required=True)
    activity_record = activity.add_parser('record')
    activity_record.add_argument('--kind', choices=['skill', 'agent'], required=True)
    activity_record.add_argument('--name', required=True)
    activity_record.add_argument('--status', choices=['started', 'completed'], required=True)
    activity_record.add_argument('--session', default='')
    activity_record.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    remove = commands.add_parser('uninstall'); remove.add_argument('--dry-run', action='store_true')
    for name in ('status', 'doctor', 'checkpoint', 'mcp', 'export'):
        commands.add_parser(name)
    task = commands.add_parser('task').add_subparsers(dest='op', required=True)
    add = task.add_parser('add'); add.add_argument('title'); add.add_argument('--description', default='')
    add.add_argument('--depends', action='append', default=[]); add.add_argument('--id'); add.add_argument('--max-attempts', type=int, default=3)
    for name in ('list', 'ready', 'recover'): task.add_parser(name)
    for name in ('show', 'retry'):
        sub = task.add_parser(name); sub.add_argument('id')
    dep = task.add_parser('depend'); dep.add_argument('id'); dep.add_argument('dependency')
    claim = task.add_parser('claim'); claim.add_argument('id'); claim.add_argument('--owner', required=True)
    claim.add_argument('--lease-seconds', type=int, default=120)
    heartbeat = task.add_parser('heartbeat'); heartbeat.add_argument('id'); heartbeat.add_argument('--token', required=True)
    heartbeat.add_argument('--lease-seconds', type=int, default=120)
    finish = task.add_parser('finish'); finish.add_argument('id'); finish.add_argument('--token', required=True)
    finish.add_argument('--status', choices=['review', 'failed', 'blocked'], default='review'); finish.add_argument('--error')
    check = commands.add_parser('check').add_subparsers(dest='op', required=True)
    check.add_parser('list')
    check_add = check.add_parser('add'); check_add.add_argument('name'); check_add.add_argument('--command-json', required=True)
    check_add.add_argument('--timeout', type=int, default=120); check_add.add_argument('--junit')
    verify = commands.add_parser('verify'); verify.add_argument('--task'); verify.add_argument('--complete', action='store_true')
    run = commands.add_parser('run'); run.add_argument('--backend', choices=['codex', 'claude', 'gemini', 'command'])
    run.add_argument('--model', help='Explicit model ID; omitted means the client default')
    run.add_argument('--routing-file', type=Path, help='JSON profiles and task-ID assignments')
    run.add_argument('--workers', type=int); run.add_argument('--cycles', type=int); run.add_argument('--command-json')
    run.add_argument('--no-worktrees', action='store_true')
    integrate = commands.add_parser('integrate'); integrate.add_argument('id')
    memory = commands.add_parser('memory').add_subparsers(dest='op', required=True)
    memadd = memory.add_parser('add'); memadd.add_argument('text'); memadd.add_argument('--kind', default='finding')
    memadd.add_argument('--source', default=''); memadd.add_argument('--confidence', type=float, default=0.5)
    search = memory.add_parser('search'); search.add_argument('query'); search.add_argument('--include-candidates', action='store_true')
    memory.add_parser('list')
    for name in ('approve', 'archive'):
        sub = memory.add_parser(name); sub.add_argument('id')
    promote = memory.add_parser('promote'); promote.add_argument('id'); promote.add_argument('--name', required=True)
    link = memory.add_parser('link'); link.add_argument('source'); link.add_argument('target'); link.add_argument('--relation', default='related')
    learn = commands.add_parser('learn', help='Record repeated workflows and validate learned skills').add_subparsers(dest='op', required=True)
    learn.add_parser('list')
    history = learn.add_parser('history', help='Read the original observation and all retries')
    history.add_argument('key'); history.add_argument('--request-id', required=True)
    for operation in ('record', 'retry', 'validate'):
        sub = learn.add_parser(operation)
        sub.add_argument('key')
        sub.add_argument('--case-id' if operation == 'validate' else '--request-id', required=True)
        if operation != 'validate': sub.add_argument('--summary', required=True)
        if operation == 'retry': sub.add_argument('--reason', required=True)
        sub.add_argument('--evidence', action='append', required=True)
        sub.add_argument('--check-json', required=True, help='Check argv as a JSON array; no shell evaluation')
        sub.add_argument('--timeout', type=int, default=120)
    stage = learn.add_parser('stage'); stage.add_argument('key'); stage.add_argument('--skill-dir', type=Path, required=True)
    activate = learn.add_parser('install'); activate.add_argument('key')
    activate.add_argument('--scope', choices=['project', 'personal'], default='project')
    activate.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    context = commands.add_parser('context').add_subparsers(dest='op', required=True)
    capture = context.add_parser('capture'); capture.add_argument('--file', type=Path); capture.add_argument('--source', default='manual')
    search = context.add_parser('search'); search.add_argument('query')
    fetch = context.add_parser('fetch'); fetch.add_argument('id'); fetch.add_argument('--offset', type=int, default=0)
    fetch.add_argument('--limit', type=int, default=4000)
    hook = commands.add_parser('hook'); hook.add_argument('--event', required=True,
                                                    choices=['SessionStart', 'PreCompact', 'PostToolUse', 'Stop'])
    return p


def doctor(project: Path) -> dict:
    config = load_config(project)
    with Store(project) as store:
        with sqlite3.connect(store.db_path) as db:
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        from .install import _hash
        from .util import read_json
        manifest = read_json(project / '.nexo/install-manifest.json', {'files': {}})
        changed = [name for name, entry in manifest['files'].items()
                   if not (project / name).is_file() or _hash((project / name).read_text(encoding='utf-8-sig')) != entry['installed_hash']]
        return {'version': __version__, 'python': sys.executable, 'database': integrity, 'fts5': store.fts_enabled,
                'executables': {name: shutil.which(name) for name in ('git', 'codex', 'claude')},
                'checks_configured': len(config['checks']), 'modified_installation_files': changed,
                'ready_for_autonomy': bool(config['checks']) and integrity == 'ok',
                'hook_trust': 'Review hooks in your client; installation does not grant trust.',
                'client_probe': 'Executable discovery only; authentication and host loading are not tested by doctor.'}


def execute(a) -> object:
    project = a.project.expanduser().resolve()
    if a.cmd == 'dashboard':
        from .dashboard import execute as dashboard_execute
        return dashboard_execute(a, project)
    if a.cmd == 'activity':
        from .activity import record_usage
        return record_usage(project, a.kind, a.name, a.status, a.session, a.codex_home)
    if a.cmd == 'personal':
        from .personal import install_personal, uninstall_personal, preferences
        if a.operation != 'install' and (a.auto_learn is not None or a.auto_open_dashboard is not None):
            raise ValueError('Personal policy flags apply to personal install only')
        if a.operation == 'preferences': return preferences(a.codex_home)
        if a.operation == 'uninstall': return uninstall_personal(a.codex_home, a.dry_run)
        return install_personal(a.codex_home, a.dry_run, auto_learn=a.auto_learn,
                                auto_open_dashboard=a.auto_open_dashboard)
    if a.cmd == 'init':
        if a.state_only:
            return initialize_project(a.path or project, a.dry_run)
        return install(a.path or project, a.host, not a.no_hooks, not a.no_mcp, a.dry_run)
    if a.cmd == 'uninstall': return uninstall(project, a.dry_run)
    if a.cmd == 'status': return status(project)
    if a.cmd == 'mcp':
        from .mcp import serve
        serve(project); return None
    if a.cmd == 'hook':
        from .hooks import handle_hook
        data = sys.stdin.read(1_000_001)
        if len(data) > 1_000_000: raise ValueError('Hook payload exceeds 1 MB')
        payload = json.loads(data or '{}')
        if not isinstance(payload, dict): raise ValueError('Hook input must be a JSON object')
        # Global hooks inherit a process cwd that may differ from the active turn.
        # The native host supplies the session cwd in its payload.
        if payload.get('cwd'):
            candidate = Path(payload['cwd'])
            if not candidate.is_absolute(): raise ValueError('Hook cwd must be absolute')
            project = candidate.resolve()
        return handle_hook(project, a.event, payload)
    if a.cmd == 'learn':
        from . import learning
        if a.op == 'list': return learning.patterns(project)
        if a.op == 'history': return learning.history(project, a.key, a.request_id)
        if a.op == 'record':
            return learning.record(project, a.key, a.request_id, a.summary, a.evidence,
                                   json.loads(a.check_json), a.timeout)
        if a.op == 'retry':
            return learning.retry(project, a.key, a.request_id, a.reason, a.summary, a.evidence,
                                  json.loads(a.check_json), a.timeout)
        if a.op == 'stage': return learning.stage(project, a.key, a.skill_dir)
        if a.op == 'validate':
            return learning.validate(project, a.key, a.case_id, a.evidence,
                                     json.loads(a.check_json), a.timeout)
        if a.op == 'install': return learning.install_learned(project, a.key, a.scope, a.codex_home)
    load_config(project)
    if a.cmd == 'doctor': return doctor(project)
    if a.cmd == 'check':
        return load_config(project)['checks'] if a.op == 'list' else add_check(project, a.name, json.loads(a.command_json), a.timeout, a.junit)
    if a.cmd == 'verify': return verify_task(project, a.task, a.complete)
    if a.cmd == 'run':
        from .runner import run
        if a.routing_file and (a.backend or a.model or a.command_json):
            raise ValueError('--routing-file cannot be combined with --backend, --model or --command-json')
        routing = json.loads(a.routing_file.read_text(encoding='utf-8')) if a.routing_file else None
        return run(project, a.backend, a.workers, a.cycles, json.loads(a.command_json) if a.command_json else None,
                   False if a.no_worktrees else None, model=a.model, routing=routing)
    if a.cmd == 'context':
        with ContextIndex(project) as context:
            if a.op == 'search': return context.search(a.query)
            if a.op == 'fetch': return context.fetch(a.id, a.offset, a.limit)
            limit = load_config(project)['context']['max_capture_bytes']
            if a.file:
                with a.file.open('rb') as handle: data = handle.read(limit + 1).decode('utf-8', errors='replace')
            else: data = sys.stdin.read(limit + 1)
            return context.capture(data, source=a.source, max_bytes=limit)
    read_only = ((a.cmd == 'task' and a.op in {'list', 'ready', 'show'})
                 or (a.cmd == 'memory' and a.op in {'list', 'search'}))
    with Store(project, readonly=read_only) as store:
        if a.cmd == 'export': return store.export_state()
        if a.cmd == 'checkpoint': return export_checkpoint(project, store)
        if a.cmd == 'integrate':
            from .workspace import integrate
            return integrate(project, store.get_task(a.id))
        if a.cmd == 'task':
            if a.op == 'add': return store.add_task(a.title, a.description, a.depends, a.max_attempts, a.id)
            if a.op == 'list': return store.list_tasks()
            if a.op == 'ready': return store.ready_tasks()
            if a.op == 'show': return store.get_task(a.id)
            if a.op == 'recover': return store.recover_expired()
            if a.op == 'depend': return store.add_dependency(a.id, a.dependency)
            if a.op == 'claim': return store.claim_task(a.id, a.owner, a.lease_seconds)
            if a.op == 'heartbeat': return store.heartbeat(a.id, a.token, a.lease_seconds)
            if a.op == 'finish': return store.finish_task(a.id, a.token, a.status, error=a.error)
            if a.op == 'retry': return store.retry_task(a.id)
        if a.cmd == 'memory':
            if a.op == 'add': return store.add_memory(redact(a.text), a.kind, redact(a.source), confidence=a.confidence)
            if a.op == 'search': return store.search_memory(a.query, include_candidates=a.include_candidates)
            if a.op == 'list': return store.list_memories()
            if a.op == 'approve': return store.approve_memory(a.id)
            if a.op == 'archive': return store.update_memory(a.id, status='archived')
            if a.op == 'link': return store.link_memory(a.source, a.target, a.relation)
            if a.op == 'promote': return {'path': str(promote_to_skill(project, store, a.id, a.name))}
    raise ValueError('Unsupported command')


def main(argv=None) -> int:
    if hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8')
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    args = parser().parse_args(argv)
    try:
        result = execute(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if isinstance(result, dict):
            if result.get('verdict') == 'failed' or (args.cmd == 'learn' and result.get('status') == 'failed') or result.get('complete') is False or result.get('integrated') is False:
                return 1
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('Interrupted. State preserved; inspect leases before resuming.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
