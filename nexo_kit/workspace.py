"""Isolated workspaces and explicit, verified integration."""
from __future__ import annotations

import subprocess
import os
from pathlib import Path

from .util import EXCLUDED, inside, read_json, write_json


def git(project: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(['git', '-c', 'user.name=Nexo Kit', '-c', 'user.email=nexo@local.invalid',
                             '-C', str(project), *args], capture_output=True, text=True, encoding='utf-8',
                            errors='replace', timeout=120)
    if check and result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def workspace_record(project: Path, task_id: str) -> Path:
    return inside(project / '.nexo/workspaces', task_id + '.json')


def resolve_workspace(project: Path, task_id: str) -> Path:
    record = read_json(workspace_record(project, task_id))
    if not record:
        return project
    path = Path(record['path']).resolve()
    if path != project and not path.is_relative_to((project / '.nexo/worktrees').resolve()):
        raise ValueError('Workspace record points outside managed directory')
    if not path.is_dir():
        raise ValueError('Recorded workspace is missing; inspect or recover it before retrying')
    return path


def prepare_workspace(project: Path, task: dict, isolated: bool = True) -> Path:
    record_path = workspace_record(project, task['id'])
    if record_path.exists():
        previous = read_json(record_path)
        if previous.get('preparation_error') or (previous.get('isolated') and not previous.get('prepared')):
            raise ValueError(previous.get('preparation_error', 'Workspace preparation was interrupted; inspect the retained workspace'))
        return resolve_workspace(project, task['id'])
    if not isolated:
        write_json(record_path, {'task_id': task['id'], 'path': str(project), 'isolated': False})
        return project
    # Pin the base before invoking any worker. A repository needs a real commit.
    head = git(project, 'rev-parse', '--verify', 'HEAD')
    path = inside(project / '.nexo/worktrees', task['id'])
    branch = 'codex/nexo/' + task['id']
    path.parent.mkdir(parents=True, exist_ok=True)
    git(project, 'worktree', 'add', '-b', branch, str(path), head)
    record = {'task_id': task['id'], 'path': str(path), 'branch': branch, 'base': head, 'isolated': True}
    write_json(record_path, record)
    # Completed prerequisite code must actually be visible to this task.
    for dependency in task.get('depends_on', []):
        dep = read_json(workspace_record(project, dependency))
        if not dep or not dep.get('commit'):
            raise ValueError(f'Dependency {dependency} has no committed workspace to inherit')
        try:
            git(path, 'merge', '--no-edit', dep['commit'])
        except ValueError as exc:
            git(path, 'merge', '--abort', check=False)
            record['preparation_error'] = f'Dependency merge conflict: {dependency}'
            write_json(record_path, record)
            raise ValueError(record['preparation_error']) from exc
    record['prepared'] = True
    write_json(record_path, record)
    return path


def commit_workspace(project: Path, task: dict, workspace: Path) -> str | None:
    record_path = workspace_record(project, task['id'])
    record = read_json(record_path)
    if not record or not record.get('isolated'):
        return None
    if record.get('preparation_error'):
        raise ValueError(record['preparation_error'])
    # Enumerate first: explicit exclude pathspecs can make Windows Git complain
    # about ignored cache directories on the second repair attempt. NUL-separated
    # literal paths also handle spaces, leading colons and command-line limits.
    listed = subprocess.run(['git', '-C', str(workspace), 'ls-files', '-z', '--cached', '--others',
                             '--exclude-standard'], capture_output=True, check=True, timeout=30)
    paths = sorted({p for p in listed.stdout.split(b'\0') if p
                    and not any(part in EXCLUDED for part in Path(os.fsdecode(p)).parts)})
    if paths:
        staged = subprocess.run(['git', '--literal-pathspecs', '-C', str(workspace), 'add', '--all',
                                 '--pathspec-from-file=-', '--pathspec-file-nul'],
                                input=b'\0'.join(paths) + b'\0', capture_output=True, timeout=120)
        if staged.returncode:
            raise ValueError(staged.stderr.decode('utf-8', errors='replace'))
    changed = git(workspace, 'diff', '--cached', '--name-only')
    if changed:
        git(workspace, 'commit', '-m', f"nexo: {task['id']} {task['title'][:120]}")
    commit = git(workspace, 'rev-parse', 'HEAD')
    record['commit'] = commit
    write_json(record_path, record)
    return commit


def integrate(project: Path, task: dict) -> dict:
    from .evidence import validate_evidence, verify
    if task['status'] != 'done':
        raise ValueError('Only a verified done task can be integrated')
    record_path = workspace_record(project, task['id'])
    record = read_json(record_path)
    if not record or not record.get('isolated') or not record.get('commit'):
        raise ValueError('Task has no committed isolated workspace')
    workspace = resolve_workspace(project, task['id'])
    validate_evidence(project, task['evidence'], workspace)
    if git(workspace, 'rev-parse', 'HEAD') != record['commit'] or git(workspace, 'status', '--porcelain'):
        raise ValueError('Verified workspace and recorded commit diverged; do not integrate a stale proposal')
    if git(project, 'status', '--porcelain'):
        raise ValueError('Integration requires a clean destination worktree; commit or stash your work yourself')
    if git(project, 'diff', '--name-only', '--diff-filter=U'):
        raise ValueError('Unresolved merge conflicts')
    before = git(project, 'rev-parse', 'HEAD')
    try:
        git(project, 'merge', '--no-ff', '--no-edit', record['commit'])
    except ValueError:
        git(project, 'merge', '--abort', check=False)
        raise
    report = verify(project)
    result = {'task_id': task['id'], 'before': before, 'after': git(project, 'rev-parse', 'HEAD'),
              'verification': report, 'integrated': report['verdict'] == 'passed'}
    if not result['integrated'] and result['after'] != before:
        # A visible revert preserves history; no reset of the user's worktree.
        # Only if the failed checks left content untouched can we revert safely.
        if not git(project, 'status', '--porcelain'):
            git(project, 'revert', '-m', '1', '--no-edit', result['after'])
            result['reverted'] = True
        else:
            result['reverted'] = False
            result['action_required'] = 'Checks changed the destination. Inspect the merge and local changes before reverting.'
    record['integration'] = result
    write_json(record_path, record)
    return result
