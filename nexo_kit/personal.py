"""Reversible user-level Codex installation with project-local state.

This module installs client assets only. It neither initializes the current
directory nor grants native hook trust. Hooks and MCP choose the active session
directory at invocation time, so no repository is baked into personal settings.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tomllib

from . import __version__
from .install import MD_END, MD_START, START, END, _apply_writes, _block, _hash, _windows_hook_command, _remove_block
from .util import atomic_write, canonical, inside, read_json

MANIFEST = 'nexo-kit/install-manifest.json'
PREFERENCES = 'nexo-kit/preferences.json'
RUNTIME = 'nexo-kit/runtime/nexo.py'
EVENTS = ('SessionStart', 'PreCompact', 'PostToolUse', 'Stop')


def preferences(codex_home: Path) -> dict:
    """Read personal policy without creating a profile or project state."""
    codex_home = canonical(codex_home.expanduser())
    stored = read_json(inside(codex_home, PREFERENCES), {})
    if not isinstance(stored, dict):
        raise ValueError('Personal Nexo preferences must contain an object')
    result = {'automatic_routing': True, 'auto_learn': False, 'threshold': 3, 'auto_open_dashboard': False}
    result.update(stored)
    for key in ('automatic_routing', 'auto_learn', 'auto_open_dashboard'):
        if type(result[key]) is not bool:
            raise ValueError(f'Personal Nexo preference {key} must be a boolean')
    if type(result['threshold']) is not int or result['threshold'] != 3:
        raise ValueError('Personal Nexo learning threshold must be 3 distinct successful requests')
    return result


def _personal_hooks(runtime: Path) -> dict:
    result = {}
    for event in EVENTS:
        argv = [sys.executable, str(runtime), 'hook', '--event', event]
        entry = {'hooks': [{'type': 'command', 'command': shlex.join(argv),
                            'commandWindows': _windows_hook_command(argv), 'timeout': 30}]}
        if event == 'SessionStart':
            entry['matcher'] = 'startup|resume|clear|compact'
        result[event] = [entry]
    return result


def _our_hook(entry: object, runtime: Path) -> bool:
    """Identify only handlers pointing at this installation's exact runtime."""
    if not isinstance(entry, dict) or not isinstance(entry.get('hooks'), list):
        return False
    for handler in entry['hooks']:
        if not isinstance(handler, dict):
            continue
        command = handler.get('command')
        if isinstance(command, str):
            try:
                argv = shlex.split(command)
            except ValueError:
                continue
            if len(argv) >= 3 and argv[1] == str(runtime) and argv[2] == 'hook':
                return True
    return False


def _merge_personal_hooks(old: dict, runtime: Path, owned: bool) -> dict:
    hooks = old.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Existing hooks config must be an object')
    for event, additions in _personal_hooks(runtime).items():
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            raise ValueError(f'Existing {event} hook must be an array')
        if not owned and any(_our_hook(entry, runtime) for entry in existing):
            raise ValueError('Existing personal Nexo hook has no installation manifest')
        hooks[event] = [entry for entry in existing if not _our_hook(entry, runtime)] + additions
    return old


def _rebase_shared_original(previous: str, info: dict, mode: str, runtime: Path) -> str | None:
    """Carry external edits through upgrades into the eventual restore target.

The previous managed contribution must still match. Refusing edits *inside* that
contribution avoids treating user customization as disposable installer content.
"""
    installed = info.get('installed_text')
    if not isinstance(installed, str):
        raise ValueError('Cannot safely upgrade edited settings without their previous installed snapshot')
    if mode in {'markdown', 'toml'}:
        start, end = (MD_START, MD_END) if mode == 'markdown' else (START, END)

        def managed(text: str) -> str:
            if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
                raise ValueError('A managed Nexo block was changed or removed; preserve it before reinstalling')
            return text[text.index(start):text.index(end) + len(end)]

        # Editors on Windows commonly change LF to CRLF without changing rules.
        if managed(previous).replace('\r\n', '\n') != managed(installed).replace('\r\n', '\n'):
            raise ValueError('Will not overwrite user changes inside a managed Nexo block')
        restored = _remove_block(previous, start, end)
        return None if info.get('original') is None and not restored.strip() else restored
    if mode == 'hooks':
        current, old = json.loads(previous), json.loads(installed)
        original = json.loads((info.get('original') or '{}').lstrip('\ufeff'))
        for event in EVENTS:
            entries = current.get('hooks', {}).get(event, [])
            previous_entries = old.get('hooks', {}).get(event, [])
            if ([entry for entry in entries if _our_hook(entry, runtime)] !=
                    [entry for entry in previous_entries if _our_hook(entry, runtime)]):
                raise ValueError('Will not overwrite user changes inside a personal Nexo hook')
            remaining = [entry for entry in entries if not _our_hook(entry, runtime)]
            if remaining or event in original.get('hooks', {}):
                current['hooks'][event] = remaining
            else:
                current.get('hooks', {}).pop(event, None)
        if not current.get('hooks') and 'hooks' not in original:
            current.pop('hooks', None)
        return None if not current and info.get('original') is None else json.dumps(current, ensure_ascii=False, indent=2) + '\n'
    raise ValueError('Unknown shared installation mode')


def install_personal(codex_home: Path, dry_run: bool = False, auto_learn: bool | None = None,
                     auto_open_dashboard: bool | None = None) -> dict:
    """Install into the explicit Codex home; never alter hook trust or models."""
    codex_home = canonical(codex_home.expanduser())
    runtime = codex_home / RUNTIME
    manifest_path = inside(codex_home, MANIFEST)
    old_manifest_bytes = manifest_path.read_bytes() if manifest_path.exists() else None
    manifest = (json.loads(old_manifest_bytes.decode('utf-8-sig'))
                if old_manifest_bytes is not None else {'files': {}})
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), dict):
        raise ValueError('Malformed personal installation manifest')
    if manifest.get('scope', 'personal') != 'personal':
        raise ValueError('Installation manifest is not personal')
    if auto_learn is not None and type(auto_learn) is not bool:
        raise ValueError('auto_learn must be a boolean or None')
    policy = preferences(codex_home)
    policy['automatic_routing'] = True
    if auto_learn is not None:
        policy['auto_learn'] = auto_learn
    if auto_open_dashboard is not None:
        if type(auto_open_dashboard) is not bool:
            raise ValueError('auto_open_dashboard must be a boolean or None')
        policy['auto_open_dashboard'] = auto_open_dashboard
    assets = Path(__file__).parent / 'assets'
    argv = [sys.executable, str(runtime)]
    command = (' '.join("'" + item.replace("'", "''") + "'" for item in argv)
               if os.name == 'nt' else shlex.join(argv))
    substitutions = {'{{NEXO_COMMAND}}': command,
                     '{{PROJECT}}': 'directorio activo de esta sesión (estado separado por proyecto)'}
    planned: dict[str, str] = {}
    originals: dict[str, bytes | None] = {}
    modes: dict[str, str] = {}
    rebased_originals: dict[str, str | None] = {}

    def render(text: str) -> str:
        if os.name != 'nt':
            text = text.replace('& {{NEXO_COMMAND}}', '{{NEXO_COMMAND}}')
            text = text.replace('~~~powershell', '~~~sh').replace('```powershell', '```sh')
            text = text.replace('En PowerShell:', 'En un shell POSIX:')
        for token, replacement in substitutions.items():
            text = text.replace(token, replacement)
        return text

    def put(relative: str | Path, text: str, mode: str = 'owned') -> None:
        target = inside(codex_home, relative)
        key = target.relative_to(codex_home).as_posix()
        before = target.read_bytes() if target.exists() else None
        previous = before.decode('utf-8-sig') if before is not None else None
        owned = manifest['files'].get(key)
        if mode == 'owned' and previous is not None and previous != text:
            if not owned or _hash(previous) != owned.get('installed_hash'):
                raise ValueError(f'Will not overwrite a user-owned or modified file: {key}')
        if mode != 'owned' and owned and previous is not None and _hash(previous) != owned.get('installed_hash'):
            rebased_originals[key] = _rebase_shared_original(previous, owned, mode, runtime)
        planned[key], originals[key], modes[key] = text, before, mode

    saved_policy = dict(policy)
    # Preserve a pre-existing legacy preference document byte-for-byte when
    # only this new default is absent; explicit opt-in still goes through the
    # ownership checks and is never inferred from an installation.
    old_policy = read_json(inside(codex_home, PREFERENCES), {})
    if (codex_home / PREFERENCES).exists() and auto_open_dashboard is None and 'auto_open_dashboard' not in old_policy:
        saved_policy.pop('auto_open_dashboard')
    put(PREFERENCES, json.dumps(saved_policy, ensure_ascii=False, indent=2) + '\n')
    put(RUNTIME, 'from nexo_kit.cli import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
    if os.name == 'nt':
        # Batch invokes Python directly: no PowerShell execution-policy changes.
        # Escape percent expansion and disable ! expansion even in caller shells.
        quote = lambda value: '"' + str(value).replace('%', '%%') + '"'
        script = '@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\n'
        script += quote(sys.executable) + ' -B ' + quote(runtime)
        script += ' dashboard auto --no-register --codex-home ' + quote(codex_home) + '\nexit /b %errorlevel%\n'
        put('nexo-kit/Open-Nexo-Panel.cmd', script.replace('\n', '\r\n'))
    package = Path(__file__).parent
    for path in sorted(package.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py', '.md', '.json', '.yaml', '.toml', '.html', '.css', '.js'}:
            put(Path('nexo-kit/runtime/nexo_kit') / path.relative_to(package), path.read_text(encoding='utf-8'))

    instructions = (assets / 'templates/instructions.md').read_text(encoding='utf-8')
    personal = '''# Nexo personal: estado separado por proyecto

Nexo está disponible en todos los proyectos de Codex. El directorio activo de
esta sesión determina el estado local; no reutilices el estado de otro proyecto.
Los hooks no inicializan proyectos automáticamente. Cuando el usuario invoque
Nexo o el trabajo requiera sus tareas/memoria y falte .nexo/config.json, inicializa
solo el estado local desde ese directorio, sin reinstalar skills ni hooks:

~~~powershell
& {{NEXO_COMMAND}} init --state-only .
~~~

Los comandos siguientes usan el directorio actual. Si ejecutas desde otro cwd,
añade --project con la ruta absoluta del proyecto antes del subcomando. No fijes
un único proyecto en la configuración global de MCP ni en los hooks.

Los roles base son nexo-planner, nexo-implementer, nexo-reviewer y nexo-tester.
Puede haber otros roles Nexo instalados: consulta el catálogo real del cliente
y sus instrucciones; no te limites a estos cuatro nombres. Para invocarlos
con spawn_agent, si la herramienta admite agent_type, usa su nombre como
agent_type y fork_turns="none"; transmite el contrato y el contexto pertinente en
el mensaje. Un fork de todo el historial hereda el rol del padre y es incompatible
con seleccionar estos roles personalizados.
Si el host no expone agent_type, no inventes ese parámetro ni afirmes que se
cargó un rol personalizado: delega con el contrato permitido por el host y
distingue esa vía de la invocación del rol instalado.

'''
    personal += '''## Preferencias personales vigentes

Selección automática de skills: activada. No esperes que el usuario escriba
`$nexo-*`; selecciona las capacidades pertinentes según la tarea.
Las preferencias se consultan sin modificar estado con:

~~~powershell
& {{NEXO_COMMAND}} personal preferences
~~~

'''
    if policy['auto_learn']:
        personal += '''Automejora: activada por autorización del usuario para este perfil.
Al finalizar un procedimiento reutilizable con evidencia comprobada, aplica
nexo-learn: registra la petición y sus checks en el proyecto activo. Después
de tres peticiones distintas resueltas con éxito consecutivo, redacta una skill,
pruébala con un caso nuevo, revisa sus límites y valídala antes de instalarla.
Esta autorización permite crear, validar e instalar esas skills automáticamente
sin pedir confirmación cada vez: las específicas de proyecto en .agents/skills;
las generales, portables y sin datos privados en el perfil personal de Codex.
Conserva el alcance original y respeta los permisos del cliente. Si una escritura
está denegada, conserva la candidata validada e informa; no fuerces la instalación.
No autoriza aprobar memorias arbitrarias, publicar, enviar mensajes ni desplegar.

'''
    else:
        personal += '''Automejora: desactivada. La selección automática de skills no autoriza
crear o instalar nuevas skills por repetición. Utiliza nexo-learn para este flujo
solo cuando el usuario lo solicite o active la preferencia de automejora.

'''
    personal += '''## Visibilidad del trabajo en el panel Nexo

El panel local se abre con `dashboard`. Al comenzar a aplicar una skill Nexo,
o un rol Nexo delegado, registra su uso mediante la CLI; al terminar esa fase,
registra completed. Conserva el mismo identificador de sesión real en ambos
eventos. Puedes omitir --session: la CLI usa CODEX_THREAD_ID cuando el cliente
lo facilita. Un --session explícito tiene prioridad y debe ser un ID real.
No uses CODEX_SESSION_ID como sustituto: un subagente puede heredar el del padre.
Si no existe un ID fiable, omítelo; no lo deduzcas de la última sesión activa.
Registra únicamente
capacidades que realmente estés aplicando, sin contar inspecciones de inventario
como uso. El panel distingue este reporte del agente de la evidencia de hooks.

Cuando la preferencia auto_open_dashboard esté activada, el registro started
y los hooks de uso comprobado solicitan la apertura automática en el navegador
predeterminado. Reutilizan el panel conectado, también si está dentro de Codex.
No ejecutes dashboard open en cada fase: duplicaría pestañas. dashboard auto
permite ejecutar el mismo lanzador con deduplicación; una denegación del cliente
no autoriza sortear permisos ni elevarlos para abrir el panel.

~~~powershell
& {{NEXO_COMMAND}} activity record --kind skill --name NOMBRE_REAL --status started --session ID_REAL
& {{NEXO_COMMAND}} activity record --kind skill --name NOMBRE_REAL --status completed --session ID_REAL
~~~

Cuando CODEX_THREAD_ID esté presente puedes quitar --session y su valor en
ambas órdenes. Se rechazan nombres de skills o roles no instalados.

Para un rol usa --kind agent. El registro sirve para visibilidad; no sustituye
checks ni cambia tareas o aprendizaje. Si el cliente deniega la escritura,
continúa el trabajo autorizado e informa del límite sin forzar permisos. Los
hooks registran actividad mínima y carpetas de proyecto; no inicializan .nexo,
no conservan prompts ni comandos completos y no cuentan repeticiones por sí solos.

'''
    agent_path = codex_home / 'AGENTS.md'
    old_agents = agent_path.read_text(encoding='utf-8-sig') if agent_path.exists() else ''
    if (MD_START in old_agents or MD_END in old_agents) and 'AGENTS.md' not in manifest['files']:
        raise ValueError('Existing Nexo instruction block has no personal installation manifest')
    put('AGENTS.md', _block(old_agents, render(personal + instructions), MD_START, MD_END), 'markdown')

    for path in sorted((assets / 'skills').rglob('*')):
        if path.is_file():
            put(Path('skills') / path.relative_to(assets / 'skills'), render(path.read_text(encoding='utf-8')))
    for path in sorted((assets / 'roles').glob('*.md')):
        text = render(path.read_text(encoding='utf-8'))
        body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', text, flags=re.S)
        name = 'nexo-' + path.stem
        descriptions = {
            'planner': 'Planifica tareas, dependencias y criterios de aceptación.',
            'implementer': 'Implementa una tarea acotada y entrega cambios con evidencia.',
            'reviewer': 'Revisa cambios de forma independiente y señala defectos demostrables.',
            'tester': 'Ejecuta pruebas y contrasta el resultado con los criterios acordados.',
        }
        role = 'name = ' + json.dumps(name) + '\ndescription = ' + json.dumps(descriptions.get(path.stem, f'Nexo {path.stem}'), ensure_ascii=False)
        role += '\ndeveloper_instructions = ' + json.dumps(body, ensure_ascii=False) + '\n'
        put(f'agents/{name}.toml', role)

    old_hooks = read_json(codex_home / 'hooks.json', {})
    if not isinstance(old_hooks, dict):
        raise ValueError('hooks.json must contain an object')
    put('hooks.json', json.dumps(_merge_personal_hooks(old_hooks, runtime, 'hooks.json' in manifest['files']),
                                 ensure_ascii=False, indent=2) + '\n', 'hooks')
    config_path = codex_home / 'config.toml'
    config = config_path.read_text(encoding='utf-8-sig') if config_path.exists() else ''
    try:
        parsed = tomllib.loads(config)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'Invalid TOML in config.toml: {error}') from error
    if ('nexo' in parsed.get('mcp_servers', {}) or START in config or END in config) and 'config.toml' not in manifest['files']:
        raise ValueError('An existing Nexo MCP server or managed block is not owned by this installation')
    server = '[mcp_servers.nexo]\ncommand = ' + json.dumps(sys.executable)
    server += '\nargs = ' + json.dumps([str(runtime), 'mcp'], ensure_ascii=False)
    server += '\nstartup_timeout_sec = 20\n'
    put('config.toml', _block(config, server, START, END), 'toml')
    for relative, text in planned.items():
        if Path(relative).suffix.lower() == '.toml':
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as error:
                raise ValueError(f'Invalid TOML in {relative}: {error}') from error
    result = {'scope': 'personal', 'codex_home': str(codex_home), 'files': sorted(planned),
              'dry_run': dry_run, 'hooks_require_native_trust': True,
              'state_scope': 'active-project', 'auto_learn': policy['auto_learn'],
              'auto_open_dashboard': policy['auto_open_dashboard'],
              'version': __version__}
    if dry_run:
        return result
    updated = copy.deepcopy(manifest)
    updated.update({'version': __version__, 'scope': 'personal'})
    for relative, text in planned.items():
        before = originals[relative]
        info = updated['files'].setdefault(relative, {'original': before.decode('utf-8') if before is not None else None})
        info.update({'installed_hash': _hash(text), 'mode': modes[relative]})
        if modes[relative] != 'owned':
            info['installed_text'] = text
        if relative in rebased_originals:
            info['original'] = rebased_originals[relative]
    writes = dict(planned)
    writes[MANIFEST] = json.dumps(updated, ensure_ascii=False, indent=2) + '\n'
    originals[MANIFEST] = old_manifest_bytes
    _apply_writes(codex_home, writes, originals)
    return result


def uninstall_personal(codex_home: Path, dry_run: bool = False) -> dict:
    """Restore exact originals; preserve edited assets and their runtime targets.

Preserved modified files make this a partial uninstall and keep the manifest for
an explicit subsequent cleanup. Per-project state is never traversed or deleted.
"""
    codex_home = canonical(codex_home.expanduser())
    manifest_path = inside(codex_home, MANIFEST)
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get('scope') != 'personal' or not isinstance(manifest.get('files'), dict):
        raise ValueError('No valid personal installation manifest found')
    result = {'scope': 'personal', 'codex_home': str(codex_home), 'removed': [], 'restored': [],
              'preserved_modified': [], 'runtime_retained': [], 'data_preserved': True, 'dry_run': dry_run}
    modified = set()
    for relative, info in manifest['files'].items():
        path = inside(codex_home, relative)
        if path.exists():
            # cmd.exe requires CRLF; retain those bytes for its ownership hash.
            current = (path.read_bytes().decode('utf-8-sig') if path.suffix.lower() == '.cmd'
                       else path.read_text(encoding='utf-8-sig'))
            if _hash(current) != info['installed_hash']:
                modified.add(relative)
    for relative, info in manifest['files'].items():
        path = inside(codex_home, relative)
        if not path.exists():
            continue
        if relative in modified:
            result['preserved_modified'].append(relative)
            continue
        if modified and relative.startswith('nexo-kit/runtime/'):
            result['runtime_retained'].append(relative)
            continue
        original = info.get('original')
        result['removed' if original is None else 'restored'].append(relative)
        if not dry_run:
            if original is None:
                path.unlink()
            else:
                atomic_write(path, original)
    if not dry_run and not modified:
        manifest_path.unlink()
    result['complete'] = not modified
    return result
