"""Project-local observations and verified promotion of agent-authored skills.

This ledger measures repetition and checks, not semantic skill quality. The agent
must review scope, routing, and portability before choosing personal installation.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid

from .util import atomic_write, digest, expand_argv, inside, process_options, redact, stop_process

THRESHOLD = 3
MAX_AGE_SECONDS = 90 * 24 * 3600
MAX_FILES = 128
MAX_FILE_BYTES = 20_000_000
MAX_TOTAL_BYTES = 100_000_000
MAX_LOG_BYTES = 65_536
MAX_OUTPUT_BYTES = 2_000_000
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
WINDOWS_RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}


def _slug(value: str, label: str = 'key') -> str:
    if not isinstance(value, str) or len(value) > 64 or not SLUG.fullmatch(value) or value in WINDOWS_RESERVED:
        raise ValueError(f'{label} must be a lowercase slug of at most 64 characters')
    return value


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ValueError(f'{label} must be nonempty text of at most 200 characters')
    return value


def _safe(root: Path, path: Path | str) -> Path:
    """Reject links before resolving, including Windows junction ancestors."""
    candidate = Path(os.path.abspath(root / path))
    root = Path(os.path.abspath(root))
    if not candidate.is_relative_to(root):
        raise ValueError('Path escapes project or installation root')
    for current in (root, *reversed(candidate.relative_to(root).parents), candidate):
        current = current if current.is_absolute() else root / current
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError(f'Symlinks and junctions are not supported: {current}')
    return inside(root, candidate)


def _project(project: Path) -> Path:
    project = Path(project).expanduser().resolve()
    if not _safe(project, '.nexo/config.json').is_file():
        raise ValueError('Initialize this project with init --state-only before recording learning')
    return project


@contextlib.contextmanager
def _db(project: Path):
    path = _safe(project, '.nexo/learning.sqlite3')
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS attempts (
          id INTEGER PRIMARY KEY, key TEXT NOT NULL, request_id TEXT NOT NULL,
          created REAL NOT NULL, status TEXT NOT NULL, receipt TEXT NOT NULL,
          UNIQUE(key, request_id));
        CREATE TABLE IF NOT EXISTS candidates (
          key TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
          signature TEXT NOT NULL, sequence INTEGER NOT NULL, generation TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS validations (
          id INTEGER PRIMARY KEY, key TEXT NOT NULL, case_id TEXT NOT NULL,
          created REAL NOT NULL, generation TEXT NOT NULL, receipt TEXT NOT NULL,
          UNIQUE(key, case_id));
        CREATE TABLE IF NOT EXISTS installations (
          path TEXT PRIMARY KEY, key TEXT NOT NULL, signature TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempt_runs (
          id INTEGER PRIMARY KEY, attempt_id INTEGER NOT NULL, run_number INTEGER NOT NULL,
          created REAL NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT NOT NULL,
          UNIQUE(attempt_id, run_number));
        CREATE TABLE IF NOT EXISTS candidate_invalidations (
          key TEXT NOT NULL, generation TEXT NOT NULL, created REAL NOT NULL,
          request_id TEXT NOT NULL, reason TEXT NOT NULL,
          UNIQUE(key, generation));
        INSERT OR IGNORE INTO attempt_runs(attempt_id,run_number,created,reason,status,receipt)
          SELECT id,0,created,'',status,receipt FROM attempts;
    ''')
    try:
        yield db
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()


def _snapshot(root: Path, paths: list[str]) -> list[dict]:
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES:
        raise ValueError(f'Evidence requires 1..{MAX_FILES} existing project files')
    result, seen, total = [], set(), 0
    for name in paths:
        if not isinstance(name, str) or not name:
            raise ValueError('Evidence paths must be nonempty strings')
        path = _safe(root, name)
        if not path.is_file():
            raise ValueError(f'Evidence file does not exist: {name}')
        relative = path.relative_to(root).as_posix()
        if relative in seen:
            raise ValueError('Duplicate evidence file')
        seen.add(relative)
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError('Evidence exceeds file or total size limit')
        result.append({'path': relative, 'sha256': digest(path), 'bytes': size})
    return sorted(result, key=lambda row: row['path'])


def _tree(folder: Path) -> list[dict]:
    if not folder.is_dir():
        raise ValueError('Skill directory does not exist')
    files = []
    for path in folder.rglob('*'):
        checked = _safe(folder, path)
        if checked.is_file():
            files.append(checked.relative_to(folder).as_posix())
        elif not checked.is_dir():
            raise ValueError('Skill contains an unsupported filesystem object')
        if len(files) > MAX_FILES:
            raise ValueError('Skill exceeds file count limit')
    return _snapshot(folder, files)


def _signature(snapshot: list[dict]) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def _metadata(folder: Path) -> dict:
    path = _safe(folder, 'SKILL.md')
    if not path.is_file() or path.stat().st_size > 65_536:
        raise ValueError('SKILL.md is required and must be at most 64 KiB')
    raw = path.read_text(encoding='utf-8-sig')
    lines = raw.splitlines()
    if not lines or lines[0] != '---':
        raise ValueError('SKILL.md requires YAML frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError as error:
        raise ValueError('Unterminated skill frontmatter') from error
    fields = {}
    # Deliberately support a small unambiguous scalar subset, not arbitrary YAML.
    # Optional nested metadata is allowed but cannot supply required fields.
    for line in lines[1:end]:
        if not line or line[0].isspace() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]*):\s*(.*)', line)
        if not match:
            raise ValueError('Unsupported frontmatter: use scalar name and description')
        key, value = match.groups()
        if key in fields:
            raise ValueError('Duplicate skill frontmatter field')
        if key in ('name', 'description'):
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError as error:
                    raise ValueError('Use JSON-compatible double-quoted YAML scalars') from error
            elif value.startswith("'"):
                if len(value) < 2 or not value.endswith("'"):
                    raise ValueError('Unterminated quoted frontmatter scalar')
                value = value[1:-1].replace("''", "'")
            elif not value or value[0] in '|>&*!{[?:%@`' or ' #' in value or ': ' in value:
                raise ValueError('Use a plain or quoted single-line frontmatter scalar')
            elif (value.lower() in {'true', 'false', 'null', '~', 'yes', 'no', 'on', 'off', '.nan', '.inf', '-.inf', '+.inf'}
                  or re.fullmatch(r'[-+]?\d[\d_.:eExXoObBa-fA-F+-]*', value)
                  or re.match(r'\d{4}-\d{1,2}-\d{1,2}(?:[Tt ]|$)', value)):
                raise ValueError('Quote frontmatter scalars that YAML could interpret as non-text values')
            if not isinstance(value, str):
                raise ValueError('Skill name and description must be strings')
        fields[key] = value
    name = _slug(fields.get('name'), 'skill name')
    description = fields.get('description', '')
    if not description.strip() or len(description) > 1024 or any(c in description for c in '<>\n\r'):
        raise ValueError('Skill description must be nonempty, single-line text of at most 1024 characters')
    if name != folder.name:
        raise ValueError('Skill directory name must match frontmatter name')
    if not '\n'.join(lines[end + 1:]).strip():
        raise ValueError('Skill instructions are empty')
    return {'name': name, 'description': description}


def _command(check: list[str], project: Path, timeout: float) -> list[str]:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 600:
        raise ValueError('Check timeout must be between 0.1 and 600 seconds')
    argv = expand_argv(check, project)
    if len(argv) > 128 or sum(len(arg) for arg in argv) > 32_768 or any('\0' in arg for arg in argv):
        raise ValueError('Check command exceeds limits or contains NUL')
    return argv


def _run(project: Path, evidence: list[str], argv: list[str], timeout: float, before: list[dict]) -> dict:
    receipt = {'status': 'failed', 'exit_code': None, 'command': [redact(arg) for arg in argv],
               'evidence': before, 'created_at': time.time()}
    captured = bytearray()
    output_limit = threading.Event()
    total = [0]
    process = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=project, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   shell=False, **process_options())

        def read_output():
            try:
                while chunk := process.stdout.read(8192):
                    total[0] += len(chunk)
                    captured.extend(chunk[:max(0, MAX_LOG_BYTES - len(captured))])
                    if total[0] > MAX_OUTPUT_BYTES:
                        output_limit.set()
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        while process.poll() is None:
            if output_limit.is_set():
                receipt['error'] = 'Check output exceeded 2 MB'
                stop_process(process)
                break
            if time.monotonic() - started > timeout:
                receipt['error'] = 'Check timed out'
                stop_process(process)
                break
            time.sleep(0.01)
        receipt['exit_code'] = process.wait()
        reader.join(timeout=2)
        if reader.is_alive():
            receipt['error'] = 'Check left a descendant holding its output pipe open'
        elif output_limit.is_set():
            receipt['error'] = 'Check output exceeded 2 MB'
        if not reader.is_alive():
            process.stdout.close()
        after, unavailable = [], []
        for item in before:
            try:
                after.extend(_snapshot(project, [item['path']]))
            except (OSError, ValueError) as error:
                unavailable.append({'path': item['path'], 'error': redact(str(error))})
        changed = sorted(item['path'] for item in before if item not in after)
        if changed:
            receipt['changed_evidence'] = changed
            receipt['error'] = ('Evidence files changed while the check ran: ' + ', '.join(changed)
                                + '. Evidence must stay immutable: use source/input files and assertions; '
                                'write generated check reports to a separate output path. Correct the check '
                                'and, for an observation, use learn retry with the same request-id and an '
                                'explicit reason; validation needs a new case-id.')
        if unavailable:
            receipt['unavailable_evidence'] = unavailable
        receipt['evidence_after'] = after
        if receipt['exit_code'] == 0 and 'error' not in receipt:
            receipt['status'] = 'passed'
    except (OSError, ValueError) as error:
        receipt['error'] = redact(str(error))
    finally:
        if process is not None and process.poll() is None:
            stop_process(process)
    log_path = _safe(project, '.nexo/learning-logs/' + uuid.uuid4().hex + '.log')
    text = redact(captured.decode('utf-8', errors='replace'))
    # Redaction can expand text, so bound the persisted bytes again afterward.
    text = text.encode('utf-8')[:MAX_LOG_BYTES].decode('utf-8', errors='ignore')
    atomic_write(log_path, text)
    receipt.update(log=log_path.relative_to(project).as_posix(), log_sha256=digest(log_path),
                   output_truncated=total[0] > MAX_LOG_BYTES,
                   duration_seconds=round(time.monotonic() - started, 3))
    return receipt


def _progress(db: sqlite3.Connection, key: str) -> dict:
    rows = db.execute('SELECT * FROM attempts WHERE key=? ORDER BY id DESC', (key,)).fetchall()
    cutoff, streak = time.time() - MAX_AGE_SECONDS, 0
    for row in rows:
        if row['status'] != 'passed' or row['created'] < cutoff:
            break
        streak += 1
    candidate = db.execute('SELECT * FROM candidates WHERE key=?', (key,)).fetchone()
    candidate = dict(candidate) if candidate else None
    if candidate and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_invalidations'").fetchone():
        invalidation = db.execute('SELECT * FROM candidate_invalidations WHERE key=? AND generation=?',
                                  (key, candidate['generation'])).fetchone()
        candidate['invalidation'] = dict(invalidation) if invalidation else None
    running = sum(row['status'] == 'running' for row in rows)
    return {'key': key, 'observations': len(rows), 'consecutive_successes': streak,
            'eligible': streak >= THRESHOLD and not running, 'last_status': rows[0]['status'] if rows else None,
            'running_observations': running, 'candidate': candidate}


def patterns(project: Path) -> dict:
    project = Path(project).expanduser().resolve()
    path = _safe(project, '.nexo/learning.sqlite3')
    result = {'project': str(project), 'initialized': path.exists(), 'threshold': THRESHOLD, 'patterns': []}
    if not path.exists():
        return result
    # Read-only connection: looking for opportunities never creates project state.
    with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        result['patterns'] = [_progress(db, row[0]) for row in db.execute('SELECT DISTINCT key FROM attempts ORDER BY key')]
    return result


def record(project: Path, key: str, request_id: str, summary: str,
           evidence: list[str], check: list[str], timeout: float = 120) -> dict:
    project, key = _project(project), _slug(key)
    _identifier(request_id, 'request_id')
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 2000:
        raise ValueError('Summary must contain 1..2000 characters')
    argv = _command(check, project, timeout)
    with _db(project) as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT receipt FROM attempts WHERE key=? AND request_id=?', (key, request_id)).fetchone()
        if existing:
            return {**json.loads(existing[0]), 'duplicate': True, 'pattern': _progress(db, key)}
        before = _snapshot(project, evidence)
        created = time.time()
        receipt = {'key': key, 'request_id': request_id, 'summary': redact(summary), 'status': 'running',
                   'run_number': 0, 'observation_created': created}
        row_id = db.execute('INSERT INTO attempts(key,request_id,created,status,receipt) VALUES(?,?,?,?,?)',
                            (key, request_id, created, 'running', json.dumps(receipt))).lastrowid
        db.execute('INSERT INTO attempt_runs(attempt_id,run_number,created,reason,status,receipt) VALUES(?,?,?,?,?,?)',
                   (row_id, 0, created, '', 'running', json.dumps(receipt)))
        db.commit()
        receipt.update(_run(project, evidence, argv, timeout, before))
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE attempts SET status=?,receipt=? WHERE id=?', (receipt['status'], json.dumps(receipt), row_id))
        db.execute('UPDATE attempt_runs SET status=?,receipt=? WHERE attempt_id=? AND run_number=0',
                   (receipt['status'], json.dumps(receipt), row_id))
        db.commit()
        return {**receipt, 'duplicate': False, 'pattern': _progress(db, key)}


def retry(project: Path, key: str, request_id: str, reason: str, summary: str,
          evidence: list[str], check: list[str], timeout: float = 120) -> dict:
    """Correct one failed observation without counting or rejuvenating a request."""
    project, key = _project(project), _slug(key)
    _identifier(request_id, 'request_id')
    for label, value in (('Reason', reason), ('Summary', summary)):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(f'{label} must contain 1..2000 characters')
    argv = _command(check, project, timeout)
    with _db(project) as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM attempts WHERE key=? AND request_id=?', (key, request_id)).fetchone()
        if not existing:
            raise ValueError('Unknown request-id; use learn record for a real new request')
        if existing['status'] == 'running':
            raise ValueError('Observation is running; wait for its check to finish before retrying')
        if existing['status'] != 'failed':
            raise ValueError('Only failed observations can be retried; a passed request cannot count again')
        before = _snapshot(project, evidence)
        created = time.time()
        run_number = db.execute('SELECT MAX(run_number)+1 FROM attempt_runs WHERE attempt_id=?',
                                (existing['id'],)).fetchone()[0]
        receipt = {'key': key, 'request_id': request_id, 'summary': redact(summary), 'status': 'running',
                   'reason': redact(reason), 'run_number': run_number, 'observation_created': existing['created']}
        candidate = db.execute('SELECT generation FROM candidates WHERE key=?', (key,)).fetchone()
        if candidate:
            # A retry changes the evidence behind this pattern. Do not silently
            # resurrect a candidate whose post-stage failure is now successful.
            db.execute('INSERT OR IGNORE INTO candidate_invalidations VALUES(?,?,?,?,?)',
                       (key, candidate['generation'], created, request_id, 'Observation retried; restage and validate'))
            receipt['invalidated_candidate_generation'] = candidate['generation']
        db.execute('INSERT INTO attempt_runs(attempt_id,run_number,created,reason,status,receipt) VALUES(?,?,?,?,?,?)',
                   (existing['id'], run_number, created, redact(reason), 'running', json.dumps(receipt)))
        db.execute('UPDATE attempts SET status=?,receipt=? WHERE id=?',
                   ('running', json.dumps(receipt), existing['id']))
        db.commit()
        receipt.update(_run(project, evidence, argv, timeout, before))
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE attempts SET status=?,receipt=? WHERE id=?',
                   (receipt['status'], json.dumps(receipt), existing['id']))
        db.execute('UPDATE attempt_runs SET status=?,receipt=? WHERE attempt_id=? AND run_number=?',
                   (receipt['status'], json.dumps(receipt), existing['id'], run_number))
        db.commit()
        return {**receipt, 'duplicate': False, 'pattern': _progress(db, key)}


def history(project: Path, key: str, request_id: str) -> dict:
    """Read durable runs, including legacy observations, without migrating on read."""
    project, key = Path(project).expanduser().resolve(), _slug(key)
    _identifier(request_id, 'request_id')
    path = _safe(project, '.nexo/learning.sqlite3')
    result = {'key': key, 'request_id': request_id, 'observation_created': None, 'runs': []}
    if not path.is_file():
        return result
    with contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        attempt = db.execute('SELECT * FROM attempts WHERE key=? AND request_id=?', (key, request_id)).fetchone()
        if not attempt:
            return result
        result['observation_created'] = attempt['created']
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempt_runs'").fetchone():
            rows = db.execute('SELECT * FROM attempt_runs WHERE attempt_id=? ORDER BY run_number', (attempt['id'],)).fetchall()
            result['runs'] = [{**dict(row), 'receipt': json.loads(row['receipt'])} for row in rows]
        else:
            result['runs'] = [{'attempt_id': attempt['id'], 'run_number': 0, 'created': attempt['created'],
                               'reason': '', 'status': attempt['status'], 'receipt': json.loads(attempt['receipt'])}]
        return result


def _candidate(db: sqlite3.Connection, project: Path, key: str) -> dict:
    row = db.execute('SELECT * FROM candidates WHERE key=?', (key,)).fetchone()
    if not row:
        raise ValueError('Stage a skill candidate before validating or installing')
    candidate = dict(row)
    if db.execute('SELECT 1 FROM candidate_invalidations WHERE key=? AND generation=?',
                  (key, candidate['generation'])).fetchone():
        raise ValueError('A retried observation invalidated this candidate; restage and validate again')
    folder = _safe(project, candidate['path'])
    if _signature(_tree(folder)) != candidate['signature']:
        raise ValueError('Staged skill changed; stage and validate its final contents again')
    if db.execute("SELECT 1 FROM attempts WHERE key=? AND id>? AND status!='passed'",
                  (key, candidate['sequence'])).fetchone():
        raise ValueError('A failed or unfinished observation invalidated this candidate; restage after three successes')
    return candidate


def _copy(source: Path, destination: Path, snapshot: list[dict]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    created = []
    try:
        # Exclusive file creation also preserves a concurrent writer's files.
        for item in sorted(snapshot, key=lambda item: item['path'] == 'SKILL.md'):
            target = _safe(destination, item['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            with _safe(source, item['path']).open('rb') as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            created.append(item)
        if _tree(destination) != snapshot:
            raise ValueError('Skill changed during copying')
    except BaseException:
        _remove_owned(destination, created)
        raise


def _remove_owned(folder: Path, snapshot: list[dict]) -> None:
    """Rollback only unchanged files created by this operation; never rmtree."""
    for item in snapshot:
        path = _safe(folder, item['path'])
        if path.is_file() and digest(path) == item['sha256']:
            path.unlink()
    if folder.exists():
        for path in sorted(folder.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            path = _safe(folder, path)
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            folder.rmdir()
        except OSError:
            pass


def stage(project: Path, key: str, skill_dir: Path | str) -> dict:
    project, key = _project(project), _slug(key)
    source = _safe(project, skill_dir)
    metadata, snapshot = _metadata(source), _tree(source)
    name, signature = metadata['name'], _signature(snapshot)
    destination = _safe(project, '.nexo/learned-skills/' + name)
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('Stage from a separate authored skill directory inside the project')
    with _db(project) as db:
        db.execute('BEGIN IMMEDIATE')
        if not _progress(db, key)['eligible']:
            raise ValueError('Three recent successful distinct requests are required before staging')
        old = db.execute('SELECT * FROM candidates WHERE key=?', (key,)).fetchone()
        owner = db.execute('SELECT key FROM candidates WHERE name=? AND key!=?', (name, key)).fetchone()
        if owner or (destination.exists() and (not old or old['path'] != destination.relative_to(project).as_posix())):
            raise ValueError('Candidate directory is already owned by another skill')
        if old and old['name'] != name:
            raise ValueError('An existing pattern cannot change skill name')
        if destination.exists() and _signature(_tree(destination)) != old['signature']:
            raise ValueError('Candidate directory was modified outside the ledger; preserve it and use another pattern')
        generation = uuid.uuid4().hex
        temporary = _safe(project, '.nexo/learned-skills/.stage-' + generation)
        _copy(source, temporary, snapshot)
        backup, installed_new = None, False
        try:
            if destination.exists():
                if not old or _signature(_tree(destination)) != old['signature']:
                    raise ValueError('Candidate directory changed concurrently; refusing to replace it')
                backup = _safe(project, '.nexo/learned-skill-backups/' + name + '-' + generation)
                backup.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(backup)
            _copy(temporary, destination, snapshot)
            installed_new = True
            sequence = db.execute('SELECT MAX(id) FROM attempts WHERE key=?', (key,)).fetchone()[0]
            row = dict(key=key, name=name, path=destination.relative_to(project).as_posix(),
                       signature=signature, sequence=sequence, generation=generation)
            db.execute('INSERT OR REPLACE INTO candidates VALUES(:key,:name,:path,:signature,:sequence,:generation)', row)
            db.commit()
            return {**row, 'status': 'staged', 'files': len(snapshot), 'description': metadata['description']}
        except BaseException:
            if installed_new:
                _remove_owned(destination, snapshot)
            if backup and backup.exists() and not destination.exists():
                backup.rename(destination)
            raise
        finally:
            if temporary.exists():
                _remove_owned(temporary, snapshot)


def validate(project: Path, key: str, case_id: str, evidence: list[str],
             check: list[str], timeout: float = 120) -> dict:
    project, key = _project(project), _slug(key)
    _identifier(case_id, 'case_id')
    argv = _command(check, project, timeout)
    with _db(project) as db:
        db.execute('BEGIN IMMEDIATE')
        candidate = _candidate(db, project, key)
        if db.execute('SELECT 1 FROM attempts WHERE key=? AND request_id=?', (key, case_id)).fetchone():
            raise ValueError('Validation requires a new case, not one of the observed requests')
        if db.execute('SELECT 1 FROM validations WHERE key=? AND case_id=?', (key, case_id)).fetchone():
            raise ValueError('Validation case has already been used; select a new case')
        before = _snapshot(project, evidence)
        receipt = {'key': key, 'case_id': case_id, 'status': 'running',
                   'generation': candidate['generation'], 'skill_sha256': candidate['signature']}
        row_id = db.execute('INSERT INTO validations(key,case_id,created,generation,receipt) VALUES(?,?,?,?,?)',
                            (key, case_id, time.time(), candidate['generation'], json.dumps(receipt))).lastrowid
        db.commit()
        receipt.update(_run(project, evidence, argv, timeout, before))
        db.execute('BEGIN IMMEDIATE')
        try:
            current = _candidate(db, project, key)
            if current != candidate:
                raise ValueError('Skill candidate changed during validation')
        except ValueError as error:
            receipt.update(status='failed', error=str(error))
        db.execute('UPDATE validations SET receipt=? WHERE id=?', (json.dumps(receipt), row_id))
        db.commit()
        return receipt


def install_learned(project: Path, key: str, scope: str = 'project', codex_home: Path | None = None) -> dict:
    project, key = _project(project), _slug(key)
    if scope not in ('project', 'personal'):
        raise ValueError('Skill scope must be project or personal')
    if scope == 'personal' and codex_home is None:
        raise ValueError('Personal installation requires an explicit Codex home')
    root = Path(codex_home).expanduser().absolute() if scope == 'personal' else project
    with _db(project) as db:
        db.execute('BEGIN IMMEDIATE')
        candidate = _candidate(db, project, key)
        if not _progress(db, key)['eligible']:
            raise ValueError('Three recent successful requests are still required at installation')
        row = db.execute('SELECT receipt FROM validations WHERE key=? ORDER BY id DESC LIMIT 1', (key,)).fetchone()
        receipt = json.loads(row[0]) if row else {}
        if receipt.get('status') != 'passed' or receipt.get('generation') != candidate['generation']:
            raise ValueError('A successful validation of the current skill on a new case is required')
        if receipt.get('skill_sha256') != candidate['signature']:
            raise ValueError('Validation belongs to different skill contents')
        if time.time() - receipt['created_at'] > MAX_AGE_SECONDS:
            raise ValueError('Validation is older than 90 days')
        if _snapshot(project, [item['path'] for item in receipt['evidence']]) != receipt['evidence']:
            raise ValueError('Validation evidence changed')
        log = _safe(project, receipt['log'])
        if not log.is_file() or digest(log) != receipt['log_sha256']:
            raise ValueError('Validation log is missing or modified')
        source = _safe(project, candidate['path'])
        snapshot = _tree(source)
        name = candidate['name']
        destination = _safe(root, ('skills/' if scope == 'personal' else '.agents/skills/') + name)
        existing = db.execute('SELECT * FROM installations WHERE path=?', (str(destination),)).fetchone()
        if destination.exists():
            if not existing or existing['key'] != key:
                raise ValueError('Refusing to overwrite a skill not installed by this project ledger')
            if _signature(_tree(destination)) != existing['signature']:
                raise ValueError('Installed skill was changed outside this ledger; refusing to overwrite')
            if existing['signature'] == candidate['signature']:
                return {'status': 'installed', 'unchanged': True, 'scope': scope, 'path': str(destination), 'name': name}
        temporary = _safe(root, destination.parent / ('.nexo-stage-' + uuid.uuid4().hex))
        _copy(source, temporary, snapshot)
        backup, retired, installed_new, committed = None, None, False, False
        try:
            if destination.exists():
                if not existing or existing['key'] != key:
                    raise ValueError('Destination appeared concurrently; refusing to replace another skill')
                backup = _safe(project, '.nexo/learned-skill-backups/' + name + '-installed-' + uuid.uuid4().hex)
                old_snapshot = _tree(destination)
                _copy(destination, backup, old_snapshot)
                if _signature(_tree(destination)) != existing['signature']:
                    raise ValueError('Installed skill changed during backup; original preserved')
                retired = _safe(root, destination.parent / ('.nexo-previous-' + uuid.uuid4().hex))
                destination.rename(retired)
            _copy(temporary, destination, snapshot)
            installed_new = True
            db.execute('INSERT OR REPLACE INTO installations(path,key,signature) VALUES(?,?,?)',
                       (str(destination), key, candidate['signature']))
            db.commit()
            committed = True
            return {'status': 'installed', 'unchanged': False, 'scope': scope, 'path': str(destination),
                    'name': name, 'backup': str(backup) if backup else None}
        except BaseException:
            if installed_new:
                _remove_owned(destination, snapshot)
            if retired and retired.exists() and not destination.exists():
                retired.rename(destination)
            raise
        finally:
            if temporary.exists():
                _remove_owned(temporary, snapshot)
            if committed and retired and retired.exists():
                _remove_owned(retired, old_snapshot)
