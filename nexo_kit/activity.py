"""Small, best-effort hook observations for the local dashboard.

This separate profile database never initializes project state. It stores only
identifiers, paths and observed statuses: never commands, output or prompts.
Recent activity is not proof that a process or an agent remains alive.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tomllib

from .util import canonical

FRESH_SECONDS = 120
MAX_EVENTS = 10000
_EVENTS = {'SessionStart', 'PreCompact', 'PostToolUse', 'Stop'}
_TOKEN = re.compile(r'''"(?:[^"`]|`.)*"|'(?:[^']|'')*'|[^\s;|&<>]+|[;|&<>]''')
_IDENTIFIER = re.compile(r'^[\w.:/@+\-]{1,200}$', re.UNICODE)
_AGENT_OPERATIONS = {'spawn_agent', 'close_agent', 'wait_agent', 'wait', 'interrupt_agent'}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _database(codex_home: Path | None) -> Path:
    home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    return canonical(home.expanduser()) / 'nexo-kit/activity.sqlite3'


def _connect(codex_home: Path | None, *, write: bool) -> sqlite3.Connection | None:
    path = _database(codex_home)
    if not write and not path.is_file():
        return None
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path) if write else path.as_uri() + '?mode=ro',
                                 uri=not write, timeout=2.5)
    connection.row_factory = sqlite3.Row
    try:
        if write:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS projects (
                    path TEXT PRIMARY KEY, name TEXT NOT NULL, last_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT NOT NULL, project TEXT NOT NULL, last_seen TEXT NOT NULL,
                    status TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(id, project));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                    project TEXT NOT NULL, session TEXT NOT NULL, kind TEXT NOT NULL,
                    name TEXT NOT NULL, path TEXT NOT NULL, status TEXT NOT NULL,
                    source TEXT NOT NULL, agent_id TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS events_agent ON events(project, agent_id, id);
            ''')
        else:
            connection.execute('PRAGMA query_only=ON')
        return connection
    except Exception:
        connection.close()
        raise


def _project(project: Path, timestamp: str) -> dict:
    path = canonical(Path(project).expanduser())
    return {'path': str(path), 'name': path.name or str(path), 'last_seen': timestamp}


def _register(connection: sqlite3.Connection, project: dict) -> None:
    connection.execute('''INSERT INTO projects(path,name,last_seen) VALUES(:path,:name,:last_seen)
        ON CONFLICT(path) DO UPDATE SET name=excluded.name,
        last_seen=MAX(projects.last_seen,excluded.last_seen)''', project)


def register_project(project: Path, codex_home: Path | None = None) -> dict:
    """Register a path globally without touching it; storage errors are nonfatal."""
    result = _project(project, _now())
    connection = None
    try:
        connection = _connect(codex_home, write=True)
        with connection:
            _register(connection, result)
    except (OSError, sqlite3.Error, ValueError):
        result['error'] = 'Activity storage is unavailable.'
    finally:
        if connection is not None:
            connection.close()
    return result


def registered_projects(codex_home: Path | None = None) -> list[dict]:
    """Read discovery metadata without creating profile or project directories."""
    connection = None
    try:
        connection = _connect(codex_home, write=False)
        if connection is None:
            return []
        return [dict(row) for row in connection.execute(
            'SELECT path,name,last_seen FROM projects ORDER BY last_seen DESC,path')]
    except (OSError, sqlite3.Error, ValueError):
        return []
    finally:
        if connection is not None:
            connection.close()


def _identifier(value: object) -> str:
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else ''


def usage_session(session: str = '') -> str:
    """Use an explicit ID or the calling Codex thread, never a nearby session.

    CODEX_SESSION_ID can be inherited from a parent by native subagents, whereas
    CODEX_THREAD_ID identifies the actual caller. A missing or malformed
    environment ID stays unknown; reports never create native session evidence.
    """
    if not isinstance(session, str) or (session and not _identifier(session)):
        raise ValueError('Usage session must be a session identifier')
    return session or _identifier(os.environ.get('CODEX_THREAD_ID'))


def hook_session(payload: dict) -> str:
    """Return the client's explicit hook session, without an environment guess."""
    return _identifier(payload.get('session_id', payload.get('sessionId', '')))


def _regular_asset(path: Path) -> bool:
    """Require an existing regular asset without following links or junctions."""
    try:
        if any(item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction())
               for item in (*path.parents, path)):
            return False
        return stat.S_ISREG(path.stat().st_mode)
    except (OSError, ValueError):
        return False


def _installed_asset(project: Path, codex_home: Path | None, kind: str, name: str) -> str:
    """Find an installed direct child. Project definitions take precedence."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', name):
        return ''
    home = _database(codex_home).parent.parent
    if kind == 'skill':
        candidates = (project / '.agents/skills' / name / 'SKILL.md',
                      home / 'skills' / name / 'SKILL.md')
    else:
        candidates = (project / '.codex/agents' / (name + '.toml'),
                      home / 'agents' / (name + '.toml'))
    for candidate in candidates:
        if not _regular_asset(candidate):
            continue
        if kind == 'agent':
            try:
                with candidate.open('rb') as stream:
                    raw = stream.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    continue
                definition = tomllib.loads(raw.decode('utf-8-sig'))
                if (definition.get('name') != name
                        or not isinstance(definition.get('developer_instructions'), str)
                        or not definition['developer_instructions'].strip()):
                    continue
            except (OSError, ValueError, UnicodeError):
                continue
        return str(candidate)
    return ''


def _agent_operation(tool: str) -> str:
    """Known client spellings only; arbitrary suffixes cannot imply an agent."""
    if tool in _AGENT_OPERATIONS:
        return tool
    for namespace in ('collaboration', 'functions'):
        for separator in ('.', ':', '__', ''):
            prefix = namespace + separator
            if tool.startswith(prefix) and tool[len(prefix):] in _AGENT_OPERATIONS:
                return tool[len(prefix):]
    return ''


def _object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and len(value) <= 1_000_000:
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except ValueError:
            pass
    return {}


def _responses(response: object) -> list[dict]:
    """Unwrap known structured envelopes, never interpret arbitrary output text."""
    result = []
    current = _object(response)
    for _ in range(5):
        if not current:
            break
        result.append(current)
        current = _object(current.get('structuredContent', current.get('result')))
    return result


def _success(response: object) -> bool | None:
    outcome = None
    for item in _responses(response):
        if (item.get('isError') is True or item.get('is_error') is True
                or item.get('error') or item.get('success') is False):
            return False
        for field in ('exit_code', 'exitCode', 'returncode'):
            code = item.get(field)
            if type(code) is int:
                if code != 0:
                    return False
                outcome = True
        status = item.get('status')
        if isinstance(status, str) and status in {'failed', 'error', 'cancelled', 'interrupted'}:
            return False
        if item.get('success') is True or (isinstance(status, str) and status in {'success', 'succeeded', 'completed'}):
            outcome = True
    return outcome


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1].replace("''", "'")
    return token


def _skill_path(raw: object, project: Path) -> tuple[str, str] | None:
    if not isinstance(raw, str) or len(raw) > 4096 or '\n' in raw or '\x00' in raw:
        return None
    raw = _unquote(raw).strip()
    if not raw or any(char in raw for char in '$`*?'):
        return None
    candidate = Path(raw).expanduser()
    candidate = Path(os.path.abspath(candidate if candidate.is_absolute() else project / candidate))
    if not _regular_asset(candidate):
        return None
    if candidate.name.lower() == 'skill.md':
        return candidate.parent.name, str(candidate)
    # Script execution is attributed only to an actual skill directory.
    if candidate.suffix.lower() in {'.py', '.ps1', '.sh', '.js', '.mjs', '.cjs', '.exe'}:
        for parent in list(candidate.parents)[:8]:
            if _regular_asset(parent / 'SKILL.md') and candidate.is_relative_to(parent / 'scripts'):
                return parent.name, str(candidate)
    return None


def _skill_events(project: Path, tool: str, tool_input: dict, response: object) -> list[dict]:
    outcome = _success(response)
    candidates = {}
    operation = ''
    target = ''
    short = re.split(r'[.:]', tool)[-1].rsplit('__', 1)[-1].lower()
    if short in {'read', 'read_file', 'readfile', 'get_file_contents'}:
        value = tool_input.get('file_path', tool_input.get('path'))
        skill = _skill_path(value, project)
        if skill:
            candidates[skill[1]] = skill[0]
            if Path(skill[1]).name.lower() == 'skill.md':
                target, operation = skill[1], 'loaded'
        # A native reader's structured file/content result is read evidence.
        # Plain text could instead be an error; never infer success from it.
        if outcome is None:
            for result in _responses(response):
                file = result.get('file')
                if isinstance(file, dict) and 'content' in file:
                    returned = _skill_path(file.get('filePath', file.get('file_path')), project)
                    if returned and returned[1] == target:
                        outcome = True
    for field in ('file_path', 'path'):
        skill = _skill_path(tool_input.get(field), project)
        if skill:
            candidates[skill[1]] = skill[0]
    command = tool_input.get('command', tool_input.get('cmd'))
    tokens = []
    simple = False
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        tokens, simple = command, True
    elif isinstance(command, str) and len(command) <= 100_000:
        raw_tokens = _TOKEN.findall(command)
        # Only one simple statement can establish success. A final exit code for
        # a compound shell does not establish that each earlier command worked.
        simple = not any(token in {';', '|', '>', '<', '&'} for token in raw_tokens[1:])
        simple = simple and '\n' not in command and not any(x in command for x in ('`', '$(', '${'))
        tokens = [_unquote(token) for token in raw_tokens]
        if tokens and tokens[0] == '&':
            tokens = tokens[1:]
    for token in tokens:
        skill = _skill_path(token, project)
        if skill:
            candidates[skill[1]] = skill[0]
    shell_tools = {'bash', 'shell', 'shell_command', 'exec_command', 'run_command', 'powershell'}
    if simple and tokens and short in shell_tools:
        executable = Path(tokens[0]).name.lower()
        args = tokens[1:]
        operands = [arg for arg in args if not arg.startswith('-')]
        read_flags = {'get-content': {'-raw', '-literalpath', '-path'},
                      'cat': {'-n', '-b', '-e', '-s', '-t', '-v', '--'},
                      'type': set(), 'head': set(), 'tail': set()}
        if (executable in read_flags and len(operands) == 1
                and all(arg.lower() in read_flags[executable] for arg in args if arg.startswith('-'))):
            skill = _skill_path(operands[0], project)
            if skill and Path(skill[1]).name.lower() == 'skill.md':
                target, operation = skill[1], 'loaded'
        elif re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|py|node|bash|sh|pwsh|powershell)(?:\.exe)?', executable):
            # Inline programs (-c/-Command/-EncodedCommand/-e) are intentionally
            # not parsed: a path in their text does not prove script execution.
            allowed = {'-u', '-b', '-i', '-s', '-e', '-x'}
            # Python -I/-E are safe flags, whereas node -e executes inline text.
            # Accept only the common script invocation shape; unfamiliar flags
            # remain a reference rather than guessing their interpreter meaning.
            if executable.startswith(('node', 'bash', 'sh')):
                allowed = set()
            elif executable.startswith(('pwsh', 'powershell')):
                allowed = {'-noprofile', '-noninteractive', '-file'}
            if all(arg.lower() in allowed for arg in args[:next(
                    (index for index, arg in enumerate(args) if not arg.startswith('-')), len(args))]):
                script_args = [arg for arg in args if not arg.startswith('-')]
                if script_args:
                    skill = _skill_path(script_args[0], project)
                    if skill and Path(skill[1]).name.lower() != 'skill.md':
                        target, operation = skill[1], 'executed'
        else:
            skill = _skill_path(tokens[0], project)
            if skill and Path(skill[1]).name.lower() != 'skill.md':
                target, operation = skill[1], 'executed'
    events = []
    for path, name in candidates.items():
        status = operation if path == target and outcome is True else 'referenced'
        if path == target and outcome is False:
            status = 'failed'
        source = 'hook:PostToolUse:' + ('successful-operation' if status in {'loaded', 'executed'}
                                       else 'failed-operation' if status == 'failed' else 'reference-only')
        events.append({'kind': 'skill', 'name': name[:200], 'path': path,
                       'status': status, 'source': source, 'agent_id': ''})
    return events


def _agent_events(connection: sqlite3.Connection, project: str, tool: str,
                  tool_input: dict, response: object, session: str = '',
                  codex_home: Path | None = None) -> list[dict]:
    short = _agent_operation(tool)
    data = _responses(response)
    outcome = _success(response)
    if short == 'spawn_agent':
        role = _identifier(tool_input.get('agent_type'))
        if not role:
            return []
        # Codex desktop returns a canonical task_name; the CLI can return an ID.
        # Only returned values prove creation, never the requested task_name.
        agent = next((value for item in data for key in ('agent_id', 'id', 'task_name')
                      if (value := _identifier(item.get(key)))), '')
        status = 'failed' if outcome is False else 'spawned' if agent else 'referenced'
        return [{'kind': 'agent', 'name': role,
                 'path': _installed_asset(Path(project), codex_home, 'agent', role), 'status': status,
                 'source': 'hook:PostToolUse:' + short, 'agent_id': agent}]
    if short not in {'close_agent', 'wait_agent', 'wait', 'interrupt_agent'}:
        return []
    observed = {}
    for item in data:
        statuses = item.get('status')
        if isinstance(statuses, dict):
            for agent, value in statuses.items():
                state = next(iter(value), '') if isinstance(value, dict) else value
                if state in {'completed', 'failed', 'shutdown', 'interrupted', 'not_found'}:
                    observed[_identifier(agent)] = state
        state = item.get('status')
        if isinstance(state, str) and state in {'completed', 'failed', 'shutdown', 'interrupted', 'closed'}:
            agent = next((value for key in ('agent_id', 'id', 'task_name')
                          if (value := _identifier(item.get(key)))), '')
            if not agent:
                agent = next((value for key in ('id', 'agent_id', 'target')
                              if (value := _identifier(tool_input.get(key)))), '')
            observed[agent] = state
    events = []
    for agent, status in observed.items():
        if not agent:
            continue
        # Canonical task names can repeat in another chat. An unknown session
        # cannot safely correlate a later event with a previous role.
        previous = connection.execute('''SELECT name,path FROM events
            WHERE project=? AND session=? AND agent_id=? AND kind='agent' ORDER BY id DESC LIMIT 1''',
            (project, session, agent)).fetchone() if session else None
        events.append({'kind': 'agent', 'name': previous['name'] if previous else 'agent',
                       'path': previous['path'] if previous else '', 'status': status, 'source': 'hook:PostToolUse:' + short,
                       'agent_id': agent})
    return events


def observe_hook(project: Path, event: str, payload: dict, codex_home: Path | None = None) -> bool:
    """Record bounded metadata; a telemetry failure must not break a native hook."""
    if event not in _EVENTS or not isinstance(payload, dict):
        return False
    connection = None
    try:
        timestamp = _now()
        metadata = _project(project, timestamp)
        session = hook_session(payload)
        connection = _connect(codex_home, write=True)
        with connection:
            _register(connection, metadata)
            if session:
                status = 'stopped' if event == 'Stop' else 'observed'
                connection.execute('''INSERT INTO sessions(id,project,last_seen,status,source) VALUES(?,?,?,?,?)
                    ON CONFLICT(id,project) DO UPDATE SET last_seen=excluded.last_seen,
                    status=excluded.status,source=excluded.source
                    WHERE excluded.last_seen >= sessions.last_seen''',
                    (session, metadata['path'], timestamp, status, 'hook:' + event))
            events = [{'kind': 'hook', 'name': event, 'path': '',
                       'status': 'stopped' if event == 'Stop' else 'observed',
                       'source': 'hook:' + event, 'agent_id': ''}]
            if event == 'PostToolUse':
                tool = _identifier(payload.get('tool_name', payload.get('toolName'))) or 'unknown'
                tool_input = _object(payload.get('tool_input', payload.get('toolInput')))
                response = payload.get('tool_response', payload.get('tool_output', payload.get('toolResponse')))
                outcome = _success(response)
                # MCP CallToolResult need not include isError:false or an exit
                # code. Only our exact tool names use this success envelope.
                if outcome is None and _nexo_mcp_tool(tool) and any(
                        isinstance(item.get('content'), list) and item['content']
                        and all(isinstance(block, dict) and block.get('type') == 'text'
                                and isinstance(block.get('text'), str) for block in item['content'])
                        for item in _responses(response)):
                    outcome = True
                events.append({'kind': 'tool', 'name': tool, 'path': '',
                               'status': 'succeeded' if outcome is True else 'failed' if outcome is False else 'observed',
                               'source': 'hook:PostToolUse', 'agent_id': ''})
                events.extend(_skill_events(Path(metadata['path']), tool, tool_input, response))
                events.extend(_agent_events(connection, metadata['path'], tool, tool_input, response,
                                            session, codex_home))
            for observation in events:
                connection.execute('''INSERT INTO events(timestamp,project,session,kind,name,path,status,source,agent_id)
                    VALUES(?,?,?,?,?,?,?,?,?)''', (timestamp, metadata['path'], session,
                    *(observation[key] for key in ('kind', 'name', 'path', 'status', 'source', 'agent_id'))))
            connection.execute('DELETE FROM events WHERE id <= (SELECT MAX(id)-? FROM events)', (MAX_EVENTS,))
        if event == 'PostToolUse' and _opens_dashboard(events, project, codex_home):
            from .dashboard import auto_open_dashboard
            auto_open_dashboard(codex_home, project)
        return True
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
        return False
    finally:
        if connection is not None:
            connection.close()


def _nexo_mcp_tool(name: str) -> bool:
    from .mcp import TOOLS
    return any(name in {tool, 'mcp__nexo__' + tool, 'mcp.nexo.' + tool}
               for tool in ('nexo_' + item[0] for item in TOOLS))


def _opens_dashboard(events: list[dict], project: Path, codex_home: Path | None = None) -> bool:
    """Positive Nexo observations only; lifecycle hooks and mentions are inert."""
    for item in events:
        kind, name, status = item['kind'], item['name'], item['status']
        if (kind in {'skill', 'agent'} and name.startswith('nexo-') and item.get('path')
                and status in {'loaded', 'executed', 'spawned'}):
            installed = _installed_asset(project, codex_home, kind, name)
            if installed:
                actual, expected = canonical(Path(item['path'])), canonical(Path(installed))
                if actual == expected or (kind == 'skill' and actual.is_relative_to(expected.parent / 'scripts')):
                    return True
        if kind == 'tool' and status == 'succeeded' and _nexo_mcp_tool(name):
            return True
    return False


def record_usage(project: Path, kind: str, name: str, status: str, session: str = '',
                 codex_home: Path | None = None) -> dict:
    """Record an explicit cooperative report, clearly separate from hook evidence.

    Reports accept identifiers of actual installed skill/role files only. This
    API is not a general log for arbitrary prose or commands.
    """
    if kind not in {'skill', 'agent'} or status not in {'started', 'completed'}:
        raise ValueError('Usage requires kind skill/agent and status started/completed')
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}', name):
        raise ValueError('Usage name must be a skill or Nexo agent identifier')
    session = usage_session(session)
    project = canonical(Path(project).expanduser())
    path = _installed_asset(project, codex_home, kind, name)
    if not path:
        raise ValueError('Usage name must identify an installed project/personal skill or agent')
    timestamp = _now()
    observation = {'timestamp': timestamp, 'project': str(project), 'session': session,
                   'kind': kind, 'name': name, 'path': path, 'status': status,
                   'source': 'agent-report', 'agent_id': ''}
    connection = None
    try:
        connection = _connect(codex_home, write=True)
        with connection:
            _register(connection, _project(project, timestamp))
            cursor = connection.execute('''INSERT INTO events(timestamp,project,session,kind,name,path,status,source,agent_id)
                VALUES(:timestamp,:project,:session,:kind,:name,:path,:status,:source,:agent_id)''', observation)
            observation['id'] = cursor.lastrowid
            connection.execute('DELETE FROM events WHERE id <= (SELECT MAX(id)-? FROM events)', (MAX_EVENTS,))
        result = {'recorded': True, 'event': observation}
        if status == 'started' and name.startswith('nexo-'):
            from .dashboard import auto_open_dashboard
            result['dashboard'] = auto_open_dashboard(codex_home, project)
        return result
    except (OSError, sqlite3.Error):
        return {'recorded': False, 'error': 'Activity storage is unavailable.'}
    finally:
        if connection is not None:
            connection.close()


def read_activity(codex_home: Path | None = None, limit: int = 200) -> dict:
    """Read observations, report phases and freshness without creating any state.

    latest_reports is independent of the visible history limit. Each entry is
    the latest retained agent report per project/session/kind/name, up to 1000
    groups. Consumers must combine its phase with session freshness; a report
    by itself does not prove that work is still running.
    """
    result = {'events': [], 'sessions': [], 'latest_reports': []}
    connection = None
    try:
        connection = _connect(codex_home, write=False)
        if connection is None:
            return result
        limit = max(0, min(int(limit), MAX_EVENTS))
        result['events'] = [dict(row) for row in connection.execute(
            'SELECT * FROM events ORDER BY id DESC LIMIT ?', (limit,))]
        result['latest_reports'] = [dict(row) for row in connection.execute('''
            SELECT * FROM events WHERE id IN (
                SELECT MAX(id) FROM events WHERE source='agent-report'
                GROUP BY project,session,kind,name
            ) ORDER BY id DESC LIMIT 1000''')]
        now = datetime.fromisoformat(_now())
        for row in connection.execute('SELECT * FROM sessions ORDER BY last_seen DESC LIMIT 1000'):
            session = dict(row)
            age = (now - datetime.fromisoformat(session['last_seen'])).total_seconds()
            if session['status'] != 'stopped':
                session['status'] = 'recent' if 0 <= age <= FRESH_SECONDS else 'stale'
            result['sessions'].append(session)
    except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
        result['error'] = 'Activity storage is unavailable.'
    finally:
        if connection is not None:
            connection.close()
    return result
