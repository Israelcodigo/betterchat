"""Small filesystem/process primitives shared by all adapters."""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

EXCLUDED = {'.git', '.nexo', '__pycache__', '.venv', 'node_modules', '.pytest_cache'}


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def canonical(path: Path) -> Path:
    value = str(path.resolve())
    # Windows realpath can switch to an extended prefix while another process
    # creates a parent directory. Normalize equivalent anchors before containment.
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return Path(value)


def inside(root: Path, path: str | Path) -> Path:
    root = canonical(root)
    result = canonical(root / path)
    if not result.is_relative_to(root):
        raise ValueError(f'Path escapes project: {path}')
    return result


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(root: Path) -> str:
    """Hash tracked + nonignored untracked content, including deleted paths."""
    root = root.resolve()
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            capture_output=True, timeout=30) if git_available() else None
    if result and result.returncode == 0:
        paths = {os.fsdecode(p) for p in result.stdout.split(b'\0') if p}
    else:
        paths = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    h = hashlib.sha256()
    for name in sorted(paths):
        rel = Path(name)
        if any(part in EXCLUDED for part in rel.parts) or name.endswith(('.pyc', '.pyo')):
            continue
        p = root / rel
        h.update(name.replace('\\', '/').encode('utf-8', errors='surrogateescape') + b'\0')
        if p.is_symlink():
            h.update(b'link:' + os.fsencode(os.readlink(p)))
        elif p.is_file():
            h.update(digest(p).encode())
        elif p.is_dir():
            # Submodules: include their content as well as the gitlink path.
            h.update(fingerprint(p).encode())
        else:
            h.update(b'deleted')
        h.update(b'\0')
    return h.hexdigest()


def git_available() -> bool:
    import shutil
    return shutil.which('git') is not None


def redact(text: str) -> str:
    """Best effort before indexing, never a promise to find every secret."""
    # Structured tool responses commonly use quoted JSON keys and values.
    keys = r'(?:api[_-]?key|access[_-]?token|password|secret)'
    text = re.sub(r'(?i)("' + keys + r'"\s*:\s*)"(?:\\.|[^"\\])*"',
                  r'\1"[REDACTED]"', text)
    text = re.sub(r"(?i)('" + keys + r"'\s*:\s*)'(?:\\.|[^'\\])*'",
                  r"\1'[REDACTED]'", text)
    text = re.sub(r'(?i)\b(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*["\']?[^\s"\',;}]+', r'\1=[REDACTED]', text)
    text = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b', '[REDACTED]', text)
    text = re.sub(r'(?i)(authorization:\s*bearer\s+)\S+', r'\1[REDACTED]', text)
    return text


def expand_argv(argv: list[str], project: Path, worktree: Path | None = None) -> list[str]:
    import sys
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError('Command must be a non-empty JSON array of strings, never a shell string')
    replacements = {'{python}': sys.executable, '{project}': str(project), '{worktree}': str(worktree or project)}
    expanded = []
    for arg in argv:
        for key, value in replacements.items():
            arg = arg.replace(key, value)
        expanded.append(arg)
    return expanded


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # One Windows process tree; no filesystem operations through another shell.
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        process.wait(timeout=5)


def process_options() -> dict:
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {'start_new_session': True}
