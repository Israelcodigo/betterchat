"""Project-scoped, reversible installation; never edits global client settings."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shlex
import sys
import tomllib
from pathlib import Path, PureWindowsPath

from . import __version__
from .config import defaults
from .util import atomic_write, inside, read_json

START = '# >>> nexo-kit managed >>>'
END = '# <<< nexo-kit managed <<<'
MD_START = '<!-- nexo-kit:start -->'
MD_END = '<!-- nexo-kit:end -->'


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _block(text: str, content: str, start: str, end: str) -> str:
    if (start in text) != (end in text) or text.count(start) > 1 or text.count(end) > 1:
        raise ValueError('Malformed managed block; repair it before installation')
    replacement = f'{start}\n{content.rstrip()}\n{end}'
    if start in text:
        return text[:text.index(start)] + replacement + text[text.index(end) + len(end):]
    return text.rstrip() + ('\n\n' if text.strip() else '') + replacement + '\n'


def _remove_block(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return text
    return text[:text.index(start)] + text[text.index(end) + len(end):]


def _powershell(argv: list[str]) -> str:
    body = '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in argv)
    encoded = base64.b64encode(body.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded


def _windows_hook_command(argv: list[str]) -> str:
    """Avoid a nested shell only when every token is literal in cmd and PowerShell.

    Codex normally supplies the session shell, but can fall back to COMSPEC.
    An ASCII allowlist keeps the unquoted fast path valid in either parent;
    spaces, Unicode and shell syntax retain the encoded PowerShell launcher.
    """
    if (argv and PureWindowsPath(argv[0]).is_absolute()
            and all(isinstance(arg, str) and re.fullmatch(r'[A-Za-z0-9_:\\./-]+', arg)
                    for arg in argv)):
        return ' '.join(argv)
    return _powershell(argv)


def _hook_entries(project: Path, host: str) -> dict:
    runtime = str(project / '.nexo/runtime/nexo.py')
    entries = {}
    for event in ('SessionStart', 'PreCompact', 'PostToolUse', 'Stop'):
        argv = [sys.executable, runtime, '--project', str(project), 'hook', '--event', event]
        if host == 'claude':
            handler = {'type': 'command', 'command': sys.executable, 'args': argv[1:], 'timeout': 15}
        else:
            handler = {'type': 'command', 'command': shlex.join(argv), 'commandWindows': _windows_hook_command(argv), 'timeout': 30}
        entry = {'hooks': [handler]}
        if event == 'SessionStart':
            entry['matcher'] = 'startup|resume|clear|compact'
        entries[event] = [entry]
    return entries


def _ours(entry: dict) -> bool:
    for handler in entry.get('hooks', []):
        if '.nexo/runtime/nexo.py' in json.dumps(handler).replace('\\\\', '/'):
            return True
        # Windows path separators and encoded commandWindows: command remains inspectable.
        if '.nexo\\runtime\\nexo.py' in str(handler.get('args')):
            return True
    return False


def _merge_hooks(old: dict, project: Path, host: str) -> dict:
    hooks = old.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Existing hooks config must be an object')
    for event, entries in _hook_entries(project, host).items():
        previous = hooks.get(event, [])
        if not isinstance(previous, list):
            raise ValueError(f'Existing {event} hook must be an array')
        hooks[event] = [e for e in previous if not _ours(e)] + entries
    return old


def _apply_writes(project: Path, writes: dict[str, str], originals: dict[str, bytes | None]) -> None:
    """Restore this attempt's files after an I/O failure, without deleting trees.

    Files changed by another writer are preserved and reported. This is recovery
    from caught write failures, not a filesystem transaction across power loss.
    Empty directories created by atomic_write may remain after rollback.
    """
    attempted = []
    try:
        for relative, text in writes.items():
            target = inside(project, relative)
            previous = originals[relative]
            current = target.read_bytes() if target.exists() else None
            if current != previous:
                raise ValueError(f'File changed while preparing installation: {relative}')
            if current == text.encode('utf-8'):
                continue
            # Include the current write: an I/O wrapper may report failure after
            # replacement, so checking only previously completed calls is unsafe.
            attempted.append((relative, text.encode('utf-8')))
            atomic_write(target, text)
    except Exception as error:
        unresolved = []
        for relative, installed in reversed(attempted):
            try:
                target = inside(project, relative)
                previous = originals[relative]
                current = target.read_bytes() if target.exists() else None
                if current == previous:
                    continue
                if current != installed:
                    unresolved.append(relative + ' (changed concurrently; preserved)')
                    continue
                if previous is None:
                    target.unlink()
                else:
                    # Decode without utf-8-sig: preserve original BOM and CRLF.
                    atomic_write(target, previous.decode('utf-8'))
            except (OSError, ValueError) as rollback_error:
                unresolved.append(relative + ': ' + str(rollback_error))
        if unresolved:
            raise OSError('Installation failed; rollback incomplete for: ' + '; '.join(unresolved)) from error
        raise


def install(project: Path, host: str = 'both', hooks: bool = True, mcp: bool = True,
            dry_run: bool = False) -> dict:
    project = project.expanduser().resolve()
    if host not in {'both', 'codex', 'claude'}:
        raise ValueError('host must be both, codex or claude')
    assets = Path(__file__).parent / 'assets'
    runtime = project / '.nexo/runtime/nexo.py'
    argv = [sys.executable, str(runtime), '--project', str(project)]
    command = (' '.join("'" + v.replace("'", "''") + "'" for v in argv)
               if os.name == 'nt' else shlex.join(argv))
    replacements = {'{{NEXO_COMMAND}}': command, '{{PROJECT}}': str(project)}
    manifest_path = project / '.nexo/install-manifest.json'
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    old_manifest = json.loads(previous_manifest.decode('utf-8-sig')) if previous_manifest is not None else {'files': {}}
    planned = {}
    modes = {}
    originals = {}

    def put(path: str | Path, text: str, mode: str = 'owned'):
        target = inside(project, path)
        relative = target.relative_to(project).as_posix()
        original = target.read_bytes() if target.exists() else None
        previous = original.decode('utf-8-sig') if original is not None else None
        owned = old_manifest['files'].get(relative)
        if mode == 'owned' and previous is not None and previous != text:
            if not owned or _hash(previous) != owned.get('installed_hash'):
                raise ValueError(f'Will not overwrite a user-owned or modified file: {relative}')
        planned[relative] = text
        modes[relative] = mode
        originals[relative] = original

    def render(text: str) -> str:
        if os.name != 'nt':
            text = text.replace('& {{NEXO_COMMAND}}', '{{NEXO_COMMAND}}')
            text = text.replace('~~~powershell', '~~~sh').replace('```powershell', '```sh')
            text = text.replace('En PowerShell:', 'En un shell POSIX:')
        for key, value in replacements.items():
            text = text.replace(key, value)
        return text

    # Embed the runtime so removing the source checkout does not break this project.
    put('.nexo/runtime/nexo.py', 'from nexo_kit.cli import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
    source_package = Path(__file__).parent
    for path in source_package.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py', '.md', '.json', '.yaml', '.toml'}:
            put(Path('.nexo/runtime/nexo_kit') / path.relative_to(source_package), path.read_text(encoding='utf-8'))
    if not (project / '.nexo/config.json').exists():
        # Project configuration and database are user data, not uninstallable assets.
        put('.nexo/config.json', json.dumps(defaults(), indent=2) + '\n', 'data')
    instructions_path = assets / 'templates/instructions.md'
    if not instructions_path.exists():
        raise ValueError('Kit is missing packaged instructions; installation aborted')
    instructions = render(instructions_path.read_text(encoding='utf-8'))
    for name in ('AGENTS.md', 'CLAUDE.md'):
        if (name == 'AGENTS.md' and host == 'claude') or (name == 'CLAUDE.md' and host == 'codex'):
            continue
        current = (project / name).read_text(encoding='utf-8-sig') if (project / name).exists() else ''
        put(name, _block(current, instructions, MD_START, MD_END), 'markdown')
    for path in (assets / 'skills').rglob('*'):
        if path.is_file():
            for client, base in [('codex', '.agents/skills'), ('claude', '.claude/skills')]:
                if host in {'both', client}:
                    put(Path(base) / path.relative_to(assets / 'skills'), render(path.read_text(encoding='utf-8')))
    for path in (assets / 'roles').glob('*.md'):
        text = render(path.read_text(encoding='utf-8'))
        name = 'nexo-' + path.stem
        if host in {'both', 'claude'}:
            put(f'.claude/agents/{name}.md', text)
        if host in {'both', 'codex'}:
            body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', text, flags=re.S)
            role = 'name = ' + json.dumps(name) + '\ndescription = ' + json.dumps(f'Nexo {path.stem}')
            role += '\ndeveloper_instructions = ' + json.dumps(body, ensure_ascii=False) + '\n'
            put(f'.codex/agents/{name}.toml', role)
    if hooks:
        for client, name in [('claude', '.claude/settings.json'), ('codex', '.codex/hooks.json')]:
            if host in {'both', client}:
                old = read_json(project / name, {})
                if not isinstance(old, dict):
                    raise ValueError(f'{name} must contain an object')
                put(name, json.dumps(_merge_hooks(old, project, client), ensure_ascii=False, indent=2) + '\n', 'hooks')
    if mcp:
        server = {'command': sys.executable, 'args': [str(runtime), '--project', str(project), 'mcp']}
        if host in {'both', 'claude'}:
            old = read_json(project / '.mcp.json', {})
            if 'nexo' in old.get('mcpServers', {}) and '.mcp.json' not in old_manifest['files']:
                raise ValueError('An existing MCP server named nexo is not owned by this kit')
            old.setdefault('mcpServers', {})['nexo'] = server
            put('.mcp.json', json.dumps(old, ensure_ascii=False, indent=2) + '\n', 'mcp')
        if host in {'both', 'codex'}:
            current = (project / '.codex/config.toml').read_text(encoding='utf-8-sig') if (project / '.codex/config.toml').exists() else ''
            if re.search(r'\[mcp_servers\.(?:nexo|"nexo")\]', current) and START not in current:
                raise ValueError('An existing Codex MCP server named nexo is not owned by this kit')
            content = '[mcp_servers.nexo]\ncommand = ' + json.dumps(sys.executable)
            content += '\nargs = ' + json.dumps(server['args'], ensure_ascii=False) + '\nstartup_timeout_sec = 20\n'
            put('.codex/config.toml', _block(current, content, START, END), 'toml')
    gitignore = (project / '.gitignore').read_text(encoding='utf-8-sig') if (project / '.gitignore').exists() else ''
    if '.nexo/' not in gitignore.splitlines():
        put('.gitignore', _block(gitignore, '.nexo/', START, END), 'toml')
    result = {'project': str(project), 'host': host, 'files': sorted(planned), 'dry_run': dry_run,
              'hooks_require_native_trust': hooks, 'version': __version__}
    # Validate the final merged document, not just the new block. This also
    # catches duplicate tables in pre-existing Codex configuration before I/O.
    for relative, text in planned.items():
        if Path(relative).suffix.lower() == '.toml':
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as error:
                raise ValueError(f'Invalid TOML in {relative}: {error}') from error
    if dry_run:
        return result
    manifest = copy.deepcopy(old_manifest)
    manifest['version'] = __version__
    for relative, text in planned.items():
        if modes[relative] != 'data':
            original = originals[relative]
            info = manifest['files'].setdefault(relative, {'original': original.decode('utf-8') if original is not None else None})
            info.update({'installed_hash': _hash(text), 'mode': modes[relative]})
    manifest_relative = '.nexo/install-manifest.json'
    writes = dict(planned)
    writes[manifest_relative] = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    originals[manifest_relative] = previous_manifest
    # The manifest is the final write/commit point for the installation assets.
    _apply_writes(project, writes, originals)
    from .store import Store
    from .context import export_checkpoint
    with Store(project) as store:
        store.add_event('installed', {'host': host, 'version': __version__})
        export_checkpoint(project, store)
    return result


def uninstall(project: Path, dry_run: bool = False) -> dict:
    manifest_path = project / '.nexo/install-manifest.json'
    manifest = read_json(manifest_path)
    if not manifest:
        raise ValueError('No installation manifest found')
    result = {'removed': [], 'restored': [], 'preserved_modified': [], 'runtime_retained': [],
              'data_preserved': True, 'dry_run': dry_run}
    modified = set()
    for relative, info in manifest['files'].items():
        path = inside(project, relative)
        if path.exists() and _hash(path.read_text(encoding='utf-8-sig')) != info['installed_hash']:
            modified.add(relative)
    # Modified instructions/configuration may still refer to the copied runtime.
    # Keep that target functional until the user resolves the preserved files.
    retain_runtime = bool(modified)
    for relative, info in manifest['files'].items():
        path = inside(project, relative)
        if not path.exists():
            continue
        current = path.read_text(encoding='utf-8-sig')
        if relative in modified:
            # Preserve edited config and assets rather than silently undoing user changes.
            result['preserved_modified'].append(relative)
            continue
        if retain_runtime and relative.replace('\\', '/').startswith('.nexo/runtime/'):
            result['runtime_retained'].append(relative)
            continue
        original = info.get('original')
        category = 'removed' if original is None else 'restored'
        result[category].append(relative)
        if not dry_run:
            if original is None:
                path.unlink()
            else:
                atomic_write(path, original)
    if not dry_run and not result['preserved_modified']:
        manifest_path.unlink()
    return result
