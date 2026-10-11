from __future__ import annotations

from pathlib import Path
from .util import read_json, write_json, expand_argv


def defaults() -> dict:
    return {
        'version': 1,
        'runner': {'backend': 'codex', 'max_workers': 2, 'timeout_seconds': 900,
                   'lease_seconds': 120, 'max_cycles': 3, 'worktrees': True},
        'checks': [],
        'context': {'max_chars': 12000, 'max_capture_bytes': 2000000},
        'hooks': {'capture_tool_output': False},
    }


def load_config(project: Path) -> dict:
    path = project / '.nexo/config.json'
    config = read_json(path)
    if config is None:
        raise ValueError(f'Project not initialized. Run: nexo --project "{project}" init --state-only')
    if config.get('version') != 1:
        raise ValueError('Unsupported configuration version')
    base = defaults()
    for key in ('runner', 'context', 'hooks'):
        base[key].update(config.get(key, {}))
    base['checks'] = config.get('checks', [])
    for key in ('max_workers', 'timeout_seconds', 'lease_seconds', 'max_cycles'):
        value = base['runner'][key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f'runner.{key} must be a positive integer')
    if base['runner']['max_workers'] > 16 or base['runner']['max_cycles'] > 20:
        raise ValueError('Limits: max_workers <= 16; max_cycles <= 20')
    names = set()
    if not isinstance(base['checks'], list):
        raise ValueError('checks must be a list')
    for check in base['checks']:
        if not isinstance(check, dict) or not isinstance(check.get('name'), str) or not check['name']:
            raise ValueError('Each check needs a name')
        if check['name'] in names:
            raise ValueError('Duplicate check name')
        names.add(check['name'])
        expand_argv(check.get('command'), project)
        if not isinstance(check.get('timeout_seconds', 120), (int, float)) or check.get('timeout_seconds', 120) <= 0:
            raise ValueError('Check timeout must be positive')
    for key in ('max_chars', 'max_capture_bytes'):
        if not isinstance(base['context'][key], int) or base['context'][key] < 256:
            raise ValueError(f'context.{key} must be >= 256')
    return base


def add_check(project: Path, name: str, command: list[str], timeout: int = 120, junit: str | None = None) -> dict:
    config = load_config(project)
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Check name must be nonempty')
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError('Check timeout must be positive')
    if junit:
        from .util import inside
        inside(project, junit)
    expand_argv(command, project)
    if any(check['name'] == name for check in config['checks']):
        raise ValueError(f'Check already exists: {name}')
    check = {'name': name, 'command': command, 'timeout_seconds': timeout}
    if junit:
        check['junit'] = junit
    config['checks'].append(check)
    write_json(project / '.nexo/config.json', config)
    return check
