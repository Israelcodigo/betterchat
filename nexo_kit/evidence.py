"""Execute deterministic gates and bind their results to the checked content."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import load_config
from .util import digest, expand_argv, fingerprint, inside, process_options, redact, stop_process, write_json


def parse_junit(path: Path) -> dict:
    if path.stat().st_size > 20_000_000:
        raise ValueError('JUnit report exceeds 20 MB')
    raw = path.read_bytes()
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('DTD/entities are not supported in JUnit')
    root = ET.fromstring(raw)
    cases = list(root.iter('testcase'))
    stats = {'tests': len(cases), 'failures': 0, 'errors': 0, 'skipped': 0}
    for case in cases:
        for tag, key in [('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')]:
            stats[key] += int(case.find(tag) is not None)
    if not cases or stats['tests'] <= stats['skipped']:
        raise ValueError('JUnit must contain at least one executed testcase')
    return stats


def _stat(path: Path | None):
    if not path or not path.exists():
        return None
    s = path.stat()
    return (s.st_mtime_ns, s.st_size, digest(path))


def checks_signature(checks: list[dict]) -> str:
    return hashlib.sha256(json.dumps(checks, sort_keys=True).encode()).hexdigest()


def verify(project: Path, workspace: Path | None = None, task_id: str | None = None, cancel_event=None) -> dict:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('Verification cancelled')
    workspace = (workspace or project).resolve()
    config = load_config(project)
    evidence_id = 'v_' + uuid.uuid4().hex
    folder = project / '.nexo/evidence' / evidence_id
    folder.mkdir(parents=True)
    before = fingerprint(workspace)
    result = {'id': evidence_id, 'task_id': task_id, 'workspace': str(workspace), 'created_at': time.time(),
              'fingerprint': before, 'checks_signature': checks_signature(config['checks']),
              'checks': [], 'verdict': 'failed'}
    for index, check in enumerate(config['checks']):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('Verification cancelled')
        command = expand_argv(check['command'], project, workspace)
        record = {'name': check['name'], 'command': command, 'status': 'failed', 'exit_code': None}
        log = folder / f'{index}.log'
        junit = inside(workspace, check['junit']) if check.get('junit') else None
        previous = _stat(junit)
        started = time.monotonic()
        try:
            with log.open('wb') as handle:
                process = subprocess.Popen(command, cwd=workspace, stdout=handle, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL, **process_options())
                try:
                    while process.poll() is None:
                        if cancel_event is not None and cancel_event.is_set():
                            raise InterruptedError('Verification cancelled')
                        if time.monotonic() - started > check.get('timeout_seconds', 120):
                            raise subprocess.TimeoutExpired(command, check.get('timeout_seconds', 120))
                        if log.stat().st_size > 50_000_000:
                            stop_process(process)
                            raise ValueError('Check log exceeded 50 MB')
                        time.sleep(0.05)
                    record['exit_code'] = process.returncode
                except subprocess.TimeoutExpired:
                    stop_process(process)
                    record['error'] = 'timeout'
                except BaseException:
                    stop_process(process)
                    raise
            if record['exit_code'] == 0:
                if junit:
                    if _stat(junit) == previous:
                        raise ValueError('JUnit missing or unchanged: stale report rejected')
                    record['junit'] = parse_junit(junit)
                    if record['junit']['errors'] or record['junit']['failures']:
                        raise ValueError('JUnit contains failures/errors despite zero exit code')
                    record['junit_sha256'] = digest(junit)
                    record['junit_path'] = str(junit.relative_to(workspace))
                record['status'] = 'passed'
        except InterruptedError:
            raise
        except (OSError, ValueError, ET.ParseError) as error:
            record['error'] = str(error)
        record['duration_seconds'] = round(time.monotonic() - started, 3)
        if log.exists():
            # Persist the full check log for audit; only the excerpt is redacted.
            record['log'] = str(log.relative_to(project))
            record['log_sha256'] = digest(log)
            with log.open('rb') as stream:
                excerpt = stream.read(8192).decode('utf-8', errors='replace')
            record['excerpt'] = redact(excerpt)
        result['checks'].append(record)
    after = fingerprint(workspace)
    result['fingerprint_after'] = after
    if not result['checks']:
        result['error'] = 'No checks configured; completion is not verified'
    elif before != after:
        result['error'] = 'Workspace content changed during verification; rerun on the final content'
    elif all(c['status'] == 'passed' for c in result['checks']):
        result['verdict'] = 'passed'
    write_json(folder / 'result.json', result)
    return result


def validate_evidence(project: Path, evidence: dict, workspace: Path | None = None) -> None:
    workspace = (workspace or project).resolve()
    if evidence.get('verdict') != 'passed' or not evidence.get('checks'):
        raise ValueError('Passing, nonempty checks are required')
    if str(workspace) != evidence.get('workspace'):
        raise ValueError('Evidence belongs to another workspace')
    if fingerprint(workspace) != evidence.get('fingerprint'):
        raise ValueError('Stale evidence: workspace changed')
    configured = load_config(project)['checks']
    if checks_signature(configured) != evidence.get('checks_signature'):
        raise ValueError('Check configuration changed since verification')
    if len(configured) != len(evidence['checks']):
        raise ValueError('Evidence must cover every configured check')
    for expected, check in zip(configured, evidence['checks']):
        if check.get('name') != expected['name'] or check.get('command') != expand_argv(expected['command'], project, workspace):
            raise ValueError('Evidence check identity differs from configured checks')
        if check.get('status') != 'passed' or check.get('exit_code') != 0:
            raise ValueError('A check did not pass')
        log = inside(project, check.get('log', ''))
        if not log.is_file() or digest(log) != check.get('log_sha256'):
            raise ValueError('Missing or modified evidence log')
        if expected.get('junit'):
            junit = inside(workspace, expected['junit'])
            if check.get('junit_path') != str(junit.relative_to(workspace)):
                raise ValueError('JUnit path differs from configured report')
            if not junit.is_file() or digest(junit) != check.get('junit_sha256'):
                raise ValueError('Missing or modified JUnit evidence')
            if parse_junit(junit) != check.get('junit'):
                raise ValueError('JUnit summary differs from verified evidence')
