"""Bounded, read-only dashboard projection of explicit Nexo projects and assets.

This module never opens Store, initializes a project, follows links, executes a
skill, or reads Codex authentication, configuration, or conversation files.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
import tomllib

from . import __version__

SCHEMA_VERSION = 1
DOCUMENT_BYTES = 256 * 1024
LIST_LIMIT = 1000
DATABASE_BYTES = 128 * 1024 * 1024
DATABASE_SECONDS = 2.0
THRESHOLD = 3
MAX_AGE_SECONDS = 90 * 24 * 3600
CORE_SKILLS = ('nexo-plan', 'nexo-build', 'nexo-debug', 'nexo-review',
               'nexo-verify', 'nexo-resume', 'nexo-orchestrate', 'nexo-memory', 'nexo-learn')
CORE_AGENTS = ('nexo-planner', 'nexo-implementer', 'nexo-reviewer', 'nexo-tester')
_SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def _absolute(path: Path | str) -> Path:
    # Do not resolve away a symlink before checking it.
    return Path(os.path.abspath(Path(path).expanduser()))


def _safe(root: Path, path: Path | str) -> Path:
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Parent traversal is not supported')
    target = _absolute(root / path)
    if not target.is_relative_to(root):
        raise ValueError('Path is outside the permitted asset root')
    for item in (*reversed(target.parents), target):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ValueError('Symbolic links and junctions are not supported')
    return target


def _document(root: Path, path: Path | str) -> str:
    path = _safe(root, path)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Document must be a regular file')
    with path.open('rb') as stream:
        raw = stream.read(DOCUMENT_BYTES + 1)
    if len(raw) > DOCUMENT_BYTES:
        raise ValueError('Document exceeds 256 KiB')
    return raw.decode('utf-8-sig')


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) else None


def _metadata(raw: str, fallback: str) -> tuple[str, str]:
    """Read display scalars without loading arbitrary YAML tags or objects."""
    lines = raw.splitlines()
    fields: dict[str, str] = {}
    if lines and lines[0] == '---':
        try:
            end = lines.index('---', 1)
        except ValueError:
            raise ValueError('Unterminated skill frontmatter') from None
        for index, line in enumerate(lines[1:end], 1):
            match = re.fullmatch(r'(name|description):\s*(.*)', line)
            if not match:
                continue
            key, value = match.groups()
            if value in {'>', '|', '>-', '|-', '>+', '|+'}:
                following = []
                for continuation in lines[index + 1:end]:
                    if continuation and not continuation[0].isspace():
                        break
                    following.append(continuation.strip())
                value = ' '.join(following).strip()
            elif value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1].replace("''", "'")
            if not isinstance(value, str):
                raise ValueError('Skill metadata must contain text')
            fields[key] = value
    return fields.get('name') or fallback, fields.get('description', '')


def _skill(root: Path, folder: Path, scope: str, project_id: str | None,
           origin: str, installed: bool, verified: bool) -> dict:
    path = _safe(root, folder / 'SKILL.md')
    raw = _document(root, path)
    name, description = _metadata(raw, folder.name)
    return {'id': str(path), 'name': name, 'description': description,
            'instructions': raw, 'path': str(path), 'scope': scope,
            'project_id': project_id, 'origin': origin, 'installed': installed,
            'verified_installation': verified}


def _installed_assets(root: Path, scope: str, project_id: str | None,
                      skills: dict[str, dict], agents: dict[str, dict], errors: list[str]) -> None:
    """Discover direct Nexo assets in Codex's installation roots only.

    Source checkouts, Claude marketplaces, arbitrary config-file references and
    nested directories are deliberately not installation evidence. Do not read
    config.toml (which may contain unrelated/private connection settings).
    """
    bases = (('skill', 'skills' if scope == 'personal' else '.agents/skills'),
             ('agent', 'agents' if scope == 'personal' else '.codex/agents'))
    for kind, relative in bases:
        try:
            base = _safe(root, relative)
            if not base.exists():
                continue
            entries = []
            with os.scandir(base) as stream:
                for index, entry in enumerate(stream):
                    if index >= LIST_LIMIT:
                        errors.append(f'{relative}: limited to {LIST_LIMIT} directory entries')
                        break
                    name = entry.name[:-5] if kind == 'agent' and entry.name.endswith('.toml') else entry.name
                    if name.startswith('nexo-') and _SLUG.fullmatch(name) and (kind == 'skill' or entry.name.endswith('.toml')):
                        entries.append((name, Path(entry.path)))
            for name, path in sorted(entries):
                try:
                    path = _safe(root, path)
                    if kind == 'skill':
                        if not path.is_dir():
                            continue
                        card = _skill(root, path, scope, project_id,
                                      'core' if scope == 'personal' and name in CORE_SKILLS else 'installed', True, False)
                        if card['name'] != name:
                            raise ValueError('Skill name does not match its installation directory')
                        skills.setdefault(card['id'], card)
                    else:
                        data = tomllib.loads(_document(root, path))
                        if any(not isinstance(data.get(key, ''), str) for key in ('name', 'description', 'developer_instructions')):
                            raise ValueError('Agent metadata and instructions must be text')
                        if data.get('name') != name:
                            raise ValueError('Agent name does not match its installation filename')
                        if not data.get('developer_instructions', '').strip():
                            raise ValueError('Agent instructions are empty')
                        agents[str(path)] = {'id': str(path), 'name': name,
                            'description': data.get('description', ''),
                            'instructions': data['developer_instructions'], 'path': str(path),
                            'scope': scope, 'project_id': project_id, 'installed': True,
                            'origin': 'core' if scope == 'personal' and name in CORE_AGENTS else 'installed'}
                except (OSError, ValueError, TypeError) as error:
                    errors.append(f'{kind.capitalize()} {name}: {error}')
        except (OSError, ValueError) as error:
            errors.append(f'{relative}: {error}')


def _signature(root: Path, folder: Path) -> str:
    """Match learning._signature(_tree(...)), with the same byte/file limits."""
    folder = _safe(root, folder)
    if not folder.is_dir():
        raise ValueError('Skill directory is missing')
    records, pending, total, nodes = [], [folder], 0, 0
    while pending:
        current = pending.pop()
        for item in sorted(current.iterdir()):
            nodes += 1
            if nodes > 512:
                raise ValueError('Skill exceeds the directory inspection limit')
            checked = _safe(root, item)
            mode = checked.stat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(checked)
                continue
            if not stat.S_ISREG(mode):
                raise ValueError('Skill contains an unsupported filesystem object')
            size = checked.stat().st_size
            if checked.name == 'SKILL.md' and size > DOCUMENT_BYTES:
                raise ValueError('Document exceeds 256 KiB')
            total += size
            if size > 20_000_000 or total > 100_000_000 or len(records) >= 128:
                raise ValueError('Skill exceeds the learning ledger file limits')
            digest = hashlib.sha256()
            read = 0
            with checked.open('rb') as stream:
                while chunk := stream.read(65536):
                    read += len(chunk)
                    if read > size:
                        raise ValueError('Skill changed during inspection')
                    digest.update(chunk)
            if read != size:
                raise ValueError('Skill changed during inspection')
            records.append({'path': checked.relative_to(folder).as_posix(),
                            'sha256': digest.hexdigest(), 'bytes': size})
    return hashlib.sha256(json.dumps(sorted(records, key=lambda r: r['path']),
                                     sort_keys=True).encode()).hexdigest()


def _asset_folder(root: Path, relative_base: str, value: str) -> Path:
    base = _safe(root, relative_base)
    folder = _safe(root, value)
    relative = folder.relative_to(base)
    if len(relative.parts) != 1 or not _SLUG.fullmatch(relative.name):
        raise ValueError('Ledger skill path is not a direct named skill directory')
    return folder


def _rows(db: sqlite3.Connection, query: str, label: str, errors: list[str],
          parameters: tuple = ()) -> list[dict]:
    try:
        rows = db.execute(query, parameters).fetchmany(LIST_LIMIT + 1)
        if len(rows) > LIST_LIMIT:
            errors.append(f'{label}: limited to {LIST_LIMIT} entries')
        return [dict(row) for row in rows[:LIST_LIMIT]]
    except sqlite3.Error as error:
        errors.append(f'{label}: {error}')
        return []


@contextmanager
def _open(root: Path, name: str):
    """Read a stable private copy: mode=ro alone still writes WAL sidecars.

    A dashboard poll must never create a source -wal/-shm or update its reader
    marks. Copy the committed database and WAL without consulting source SHM;
    SQLite builds any needed reader index solely in the disposable directory.
    Concurrent writes detected during copying become a partial, retryable error.
    The source is not locked: matching size/mtime is an optimistic check, not a
    guarantee of an atomic snapshot against a writer restoring those attributes.
    """
    path = _safe(root, '.nexo/' + name)
    if not path.exists():
        yield None
        return
    wal = _safe(root, str(path) + '-wal')
    _safe(root, str(path) + '-shm')
    journal = _safe(root, str(path) + '-journal')
    if journal.exists() and journal.stat().st_size:
        raise ValueError('Database has a rollback journal; retry after its writer finishes')

    def state():
        values = []
        for source in (path, wal):
            if source.exists():
                info = source.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Database must be a regular file')
                values.append((source, info.st_size, info.st_mtime_ns))
        return values

    before = state()
    if sum(row[1] for row in before) > DATABASE_BYTES:
        raise ValueError('Database and WAL exceed the 128 MiB inspection limit')
    deadline = time.monotonic() + DATABASE_SECONDS
    with tempfile.TemporaryDirectory(prefix='nexo-dashboard-read-') as temporary:
        target = Path(temporary) / name
        for source, size, _ in before:
            copied = 0
            destination = target if source == path else Path(str(target) + '-wal')
            with source.open('rb') as reader, destination.open('xb') as writer:
                while chunk := reader.read(65536):
                    if time.monotonic() > deadline:
                        raise ValueError('Database inspection exceeded 2 seconds; retry the snapshot')
                    copied += len(chunk)
                    if copied > size:
                        raise ValueError('Database changed during inspection; retry the snapshot')
                    writer.write(chunk)
            if copied != size:
                raise ValueError('Database changed during inspection; retry the snapshot')
        if before != state() or (journal.exists() and journal.stat().st_size):
            raise ValueError('Database changed during inspection; retry the snapshot')
        db = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=0.25)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            yield db
        finally:
            db.close()


def _validation(row: dict | None, candidate: dict | None, now: float) -> dict | None:
    if row is None:
        return None
    raw = row['receipt']
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > DOCUMENT_BYTES:
        raise ValueError('Validation receipt exceeds the inspection limit')
    receipt = json.loads(raw)
    if not isinstance(receipt, dict):
        raise ValueError('Validation receipt must be an object')
    created = _number(receipt.get('created_at'))
    fresh = created is not None and 0 <= now - created <= MAX_AGE_SECONDS
    same_generation = bool(candidate and row['generation'] == candidate['generation']
                           and receipt.get('generation') == candidate['generation'])
    same_signature = bool(candidate and receipt.get('skill_sha256') == candidate['signature'])
    # Evidence/log paths can point at private project data. The dashboard does
    # not dereference them or claim to have repeated install_learned's gate.
    current = bool(receipt.get('status') == 'passed' and fresh and same_generation
                   and same_signature and candidate.get('current'))
    return {'case_id': row['case_id'], 'status': receipt.get('status', 'unknown'),
            'created_at': created, 'generation': row['generation'], 'fresh': fresh,
            'same_generation': same_generation, 'same_signature': same_signature,
            'current': current, 'evidence_verified': False}


def _learning(project: Path, home: Path, now: float, result: dict,
              skills: dict[str, dict]) -> None:
    errors = result['errors']
    with _open(project, 'learning.sqlite3') as db:
        if db is None:
            return
        # Aggregate in SQLite so long histories retain exact counts without
        # loading attempt receipts or an unbounded number of observations.
        progress = _rows(db, '''WITH progress AS (
            SELECT key, COUNT(*) AS observations, MAX(id) AS last_id,
                SUM(CASE WHEN status='running' THEN 1 ELSE 0 END) AS running_observations,
                MAX(CASE WHEN status!='passed' OR typeof(created) NOT IN ('integer','real')
                    OR created<? THEN id ELSE 0 END) AS stop_id,
                MAX(CASE WHEN status!='passed' THEN id ELSE 0 END) AS invalidation_id
            FROM attempts GROUP BY key)
            SELECT p.key,p.observations,p.invalidation_id,p.running_observations,a.status AS last_status,
                (SELECT COUNT(*) FROM attempts b WHERE b.key=p.key AND b.id>p.stop_id)
                    AS consecutive_successes
            FROM progress p LEFT JOIN attempts a ON a.id=p.last_id ORDER BY p.key''',
                         'observations', errors, (now - MAX_AGE_SECONDS,))
        candidates = _rows(db, 'SELECT key,name,path,signature,sequence,generation FROM candidates', 'candidates', errors)
        validations = _rows(db, '''SELECT id,key,case_id,created,generation,substr(receipt,1,262145) AS receipt
            FROM validations WHERE id IN (SELECT MAX(id) FROM validations GROUP BY key)
            ORDER BY key''', 'validations', errors)
        installations = _rows(db, 'SELECT path,key,signature FROM installations ORDER BY path', 'installations', errors)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('candidate_invalidations','attempt_runs')")}
        invalidations = _rows(db, '''SELECT i.key,i.generation FROM candidate_invalidations i
            JOIN candidates c ON c.key=i.key AND c.generation=i.generation''', 'invalidations', errors) if 'candidate_invalidations' in tables else []
        retries = _rows(db, '''SELECT a.key,COUNT(*) AS retries FROM attempt_runs r
            JOIN attempts a ON a.id=r.attempt_id WHERE r.run_number>0 GROUP BY a.key''', 'retries', errors) if 'attempt_runs' in tables else []
    grouped = {row['key']: row for row in progress}
    invalidated_generations = {(row['key'], row['generation']) for row in invalidations}
    retry_counts = {row['key']: row['retries'] for row in retries}
    by_candidate = {row['key']: row for row in candidates}
    latest_validation = {}
    for row in validations:
        latest_validation.setdefault(row['key'], row)
    by_installation: dict[str, list[dict]] = {}
    for row in installations:
        installation = {**row, 'scope': None, 'ledger_recorded': True, 'exists': False,
                        'verified_installation': False, 'error': None}
        try:
            if not isinstance(row['path'], str) or not Path(row['path']).is_absolute():
                raise ValueError('Installation path must be absolute')
            raw = _absolute(row['path'])
            if raw.is_relative_to(home / 'skills'):
                root, base, scope = home, 'skills', 'personal'
            elif raw.is_relative_to(project / '.agents/skills'):
                root, base, scope = project, '.agents/skills', 'project'
            else:
                raise ValueError('Installation path is outside the permitted skill roots')
            folder = _asset_folder(root, base, row['path'])
            installation.update(scope=scope, exists=(folder / 'SKILL.md').is_file())
            if installation['exists']:
                installation['verified_installation'] = _signature(root, folder) == row['signature']
                card = _skill(root, folder, scope, None if scope == 'personal' else str(project),
                              'learned', True, installation['verified_installation'])
                previous = skills.get(card['id'])
                sources = previous.get('source_project_ids', []) if previous else []
                card['source_project_ids'] = sorted(set(sources + [str(project)]))
                # Two project ledgers may refer to one personal installation.
                card['verified_installation'] |= bool(previous and previous['verified_installation'])
                skills[card['id']] = card
        except (OSError, ValueError, TypeError) as error:
            installation['error'] = str(error)
            errors.append(f'Installation {row["key"]}: {error}')
        result['learning']['installations'].append(installation)
        by_installation.setdefault(row['key'], []).append(installation)
    keys = set(grouped) | set(by_candidate) | set(latest_validation) | set(by_installation)
    if len(keys) > LIST_LIMIT:
        errors.append(f'Patterns: limited to {LIST_LIMIT} entries')
    for key in sorted(keys)[:LIST_LIMIT]:
        observation = grouped.get(key, {})
        streak = observation.get('consecutive_successes', 0)
        candidate = None
        if key in by_candidate:
            candidate = {**by_candidate[key], 'exists': False, 'signature_matches': False,
                         'current': False, 'error': None, 'invalidated_by_retry': (key, by_candidate[key]['generation']) in invalidated_generations}
            try:
                folder = _asset_folder(project, '.nexo/learned-skills', candidate['path'])
                candidate['exists'] = (folder / 'SKILL.md').is_file()
                candidate['signature_matches'] = _signature(project, folder) == candidate['signature']
                invalidated = observation.get('invalidation_id', 0) > candidate['sequence']
                candidate['current'] = candidate['signature_matches'] and not invalidated and not candidate['invalidated_by_retry']
                if candidate['exists']:
                    card = _skill(project, folder, 'candidate', str(project), 'candidate', False, False)
                    skills[card['id']] = card
            except (OSError, ValueError, TypeError) as error:
                candidate['error'] = str(error)
                errors.append(f'Candidate {key}: {error}')
        validation = None
        try:
            validation = _validation(latest_validation.get(key), candidate, now)
        except (ValueError, TypeError, KeyError) as error:
            errors.append(f'Validation {key}: {error}')
        result['learning']['patterns'].append({
            'key': key, 'observations': observation.get('observations', 0), 'consecutive_successes': streak,
            'eligible': streak >= THRESHOLD and not observation.get('running_observations', 0),
            'running_observations': observation.get('running_observations', 0),
            'retry_count': retry_counts.get(key, 0), 'last_status': observation.get('last_status'),
            'candidate': candidate, 'validation': validation, 'installations': by_installation.get(key, [])})


def snapshot(codex_home: Path, projects: list[Path]) -> dict:
    """Return a stable JSON-ready view; partial failures remain on their card."""
    home, now = _absolute(codex_home), time.time()
    result = {'version': __version__, 'schema_version': SCHEMA_VERSION, 'generated_at': now,
              'preferences': {'automatic_routing': True, 'auto_learn': False, 'threshold': THRESHOLD},
              'projects': [], 'skills': [], 'agents': [], 'warnings': []}
    warnings, skills, agents = result['warnings'], {}, {}
    try:
        path = _safe(home, 'nexo-kit/preferences.json')
        if path.exists():
            values = json.loads(_document(home, path))
            if not isinstance(values, dict):
                raise ValueError('Personal preferences must be an object')
            for key in ('automatic_routing', 'auto_learn'):
                if key in values:
                    if type(values[key]) is not bool:
                        raise ValueError(f'Preference {key} must be a boolean')
                    result['preferences'][key] = values[key]
            if values.get('threshold', THRESHOLD) != THRESHOLD or type(values.get('threshold', THRESHOLD)) is not int:
                raise ValueError('Learning threshold must be 3')
    except (OSError, ValueError, TypeError) as error:
        warnings.append(f'Preferences: {error}')
    _installed_assets(home, 'personal', None, skills, agents, warnings)
    seen = set()
    for raw in projects:
        project = _absolute(raw)
        if project in seen:
            continue
        seen.add(project)
        card = {'id': str(project), 'name': project.name or str(project), 'path': str(project),
                'initialized': False, 'learning': {'threshold': THRESHOLD, 'patterns': [], 'installations': []},
                'tasks': [], 'errors': []}
        result['projects'].append(card)
        try:
            _safe(project, project)
            if not stat.S_ISDIR(project.stat().st_mode):
                card['errors'].append('La ruta registrada del proyecto no corresponde a una carpeta.')
                continue
            # Opening the directory also catches denied listing permissions;
            # stat alone may succeed for an otherwise inaccessible directory.
            with os.scandir(project):
                pass
        except FileNotFoundError:
            card['errors'].append('La carpeta del proyecto no existe. Su registro se conserva en el panel.')
            continue
        except (OSError, ValueError) as error:
            card['errors'].append(f'No se puede acceder a la carpeta del proyecto: {error}')
            continue
        _installed_assets(project, 'project', str(project), skills, agents, card['errors'])
        try:
            card['initialized'] = _safe(project, '.nexo/config.json').is_file()
            _learning(project, home, now, card, skills)
        except (OSError, ValueError, TypeError, sqlite3.Error) as error:
            card['errors'].append(f'Learning: {error}')
        try:
            with _open(project, 'state.sqlite3') as db:
                if db is not None:
                    card['tasks'] = _rows(db, 'SELECT id,title,status,lease_owner,lease_expires_at FROM tasks ORDER BY id', 'tasks', card['errors'])
                    for task in card['tasks']:
                        task['lease_expires_at'] = _number(task['lease_expires_at'])
        except (OSError, ValueError, TypeError, sqlite3.Error) as error:
            card['errors'].append(f'Tasks: {error}')
    result['skills'] = sorted(skills.values(), key=lambda row: (row['name'], row['id']))[:LIST_LIMIT]
    if len(skills) > LIST_LIMIT:
        warnings.append(f'Skills: limited to {LIST_LIMIT} entries')
    result['agents'] = sorted(agents.values(), key=lambda row: (row['name'], row['id']))[:LIST_LIMIT]
    if len(agents) > LIST_LIMIT:
        warnings.append(f'Agents: limited to {LIST_LIMIT} entries')
    return result
