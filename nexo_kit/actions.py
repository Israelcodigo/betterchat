"""Operations shared by the CLI and MCP frontends."""
from pathlib import Path

from .evidence import validate_evidence, verify
from .store import Store
from .workspace import commit_workspace, resolve_workspace


def initialize_project(project: Path, dry_run: bool = False) -> dict:
    """Create only per-project data when using a personal Codex installation."""
    from .config import defaults, load_config
    from .util import atomic_write, write_json
    project = project.expanduser().resolve()
    config_path = project / '.nexo/config.json'
    if dry_run:
        return {'project': str(project), 'state_only': True, 'dry_run': True}
    project.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        write_json(config_path, defaults())
    load_config(project)
    ignore = project / '.gitignore'
    # Read bytes so Windows CRLF, BOM and trailing whitespace survive unchanged.
    previous = ignore.read_bytes().decode('utf-8') if ignore.exists() else ''
    if '.nexo/' not in previous.lstrip('\ufeff').splitlines():
        newline = '\r\n' if '\r\n' in previous else '\n'
        separator = newline if previous.lstrip('\ufeff') and not previous.endswith(('\n', '\r')) else ''
        atomic_write(ignore, previous + separator + '.nexo/' + newline)
    from .context import export_checkpoint
    with Store(project) as store:
        store.add_event('initialized', {'scope': 'project-data'})
        export_checkpoint(project, store)
    return {'project': str(project), 'initialized': True, 'state_only': True}


def status(project: Path) -> dict:
    if not (project / '.nexo/config.json').exists():
        return {'project': str(project), 'initialized': False, 'tasks': [], 'ready': [],
                'next_step': 'Initialize this project with nexo_initialize or init --state-only before tracking work.'}
    with Store(project, readonly=True) as store:
        tasks = store.list_tasks()
        return {'project': str(project), 'initialized': True, 'tasks': [{k: task[k] for k in
                ('id', 'title', 'status', 'depends_on', 'attempts', 'max_attempts', 'version')} for task in tasks],
                'ready': [t['id'] for t in store.ready_tasks()],
                'memory': {state: sum(m['status'] == state for m in store.list_memories())
                           for state in ('candidate', 'active', 'archived')}}


def verify_task(project: Path, task_id: str | None = None, complete: bool = False) -> dict:
    if complete and not task_id:
        raise ValueError('--complete requires a task ID')
    workspace = resolve_workspace(project, task_id) if task_id else project
    with Store(project) as store:
        if task_id:
            task = store.get_task(task_id)
            if complete and task['status'] != 'review':
                raise ValueError('Task must be in review before completing it')
            if complete:
                # Manual repairs of an isolated proposal must update the commit
                # that integration will merge, not just its working-directory hash.
                commit_workspace(project, task, workspace)
        report = verify(project, workspace, task_id)
        store.add_event('verification', {'task_id': task_id, 'id': report['id'], 'verdict': report['verdict']})
        if complete and report['verdict'] == 'passed':
            validate_evidence(project, report, workspace)
            store.complete_task(task_id, report)
        elif complete:
            store.reject_review(task_id, report.get('error', 'Verification checks failed'), evidence=report)
        if not complete:
            pending = [{'id': item['id'], 'title': item['title']} for item in store.list_tasks()
                       if item['status'] == 'review' and (task_id is None or item['id'] == task_id)]
            if pending:
                # Response metadata, outside the immutable evidence receipt.
                report = dict(report, completion={
                    'performed': False, 'pending_review': pending,
                    'next_step': 'Review each task contract and its deliverable evidence. '
                                 'Then verify --task ID --complete, or report why review remains pending.'})
        return report
