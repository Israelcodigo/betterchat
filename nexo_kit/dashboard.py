"""Local Nexo dashboard. Loopback only; capability-protected, bounded API."""
from __future__ import annotations

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import webbrowser

from . import __version__
from .util import atomic_write

ASSETS = Path(__file__).parent / 'assets' / 'dashboard'
SERVICE = 'nexo-dashboard'
MAX_BODY = 8192
PRESENCE_SECONDS = 120
OPEN_RETRY_SECONDS = 30


def default_home() -> Path:
    return Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser().resolve()


def state_file(home: Path) -> Path:
    return home / 'nexo-kit' / 'dashboard-server.json'


@contextlib.contextmanager
def _startup_lock(home: Path, timeout: float = 20):
    """Serialize launchers across processes; Windows releases byte locks on exit."""
    path = home / 'nexo-kit' / 'dashboard-open.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise ValueError('Otra apertura del panel sigue en curso. Vuelve a intentarlo.')
                time.sleep(0.1)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def get_state(home: Path) -> dict:
    from .activity import registered_projects, read_activity
    from .dashboard_data import snapshot
    projects = [Path(item['path']) for item in registered_projects(home)]
    result = snapshot(home, projects)
    result['activity'] = read_activity(home)
    if result['activity'].get('error'):
        result['warnings'].append('No se puede leer el registro de actividad. Los datos de uso pueden estar incompletos.')
    return result


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, home: Path, port: int = 0, token: str | None = None):
        self.home = home.expanduser().resolve()
        self.token = token or secrets.token_urlsafe(32)
        self.instance = secrets.token_hex(12)
        self.snapshot_lock = threading.Lock()
        self.snapshot_cache = None
        self.snapshot_at = 0.0
        self.presence_lock = threading.Lock()
        self.last_client_seen = None
        self.last_open_requested = None
        super().__init__(('127.0.0.1', port), DashboardHandler)

    @property
    def origin(self) -> str:
        return f'http://127.0.0.1:{self.server_port}'

    def info(self) -> dict:
        return {'service': SERVICE, 'version': __version__, 'pid': os.getpid(),
                'port': self.server_port, 'token': self.token, 'instance': self.instance,
                'url': self.origin + '/#token=' + self.token}

    def snapshot(self) -> dict:
        with self.snapshot_lock:
            now = time.monotonic()
            if self.snapshot_cache is None or now - self.snapshot_at > 2:
                self.snapshot_cache = get_state(self.home)
                self.snapshot_at = now
            return self.snapshot_cache

    def touch_client(self):
        with self.presence_lock:
            self.last_client_seen = time.monotonic()

    def claim_browser(self, force: bool = False) -> dict:
        # Kept in the server instance, shared by all sessions and launchers.
        # A live server alone does not prove that a dashboard tab is open.
        with self.presence_lock:
            now = time.monotonic()
            active = self.last_client_seen is not None and now - self.last_client_seen < PRESENCE_SECONDS
            pending = self.last_open_requested is not None and now - self.last_open_requested < OPEN_RETRY_SECONDS
            launch = force or not (active or pending)
            if launch:
                self.last_open_requested = now
            return {'open': launch, 'reason': 'requested' if launch else 'panel-connected' if active else 'opening-recently-requested'}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = 'NexoLocal'

    def log_message(self, *_args):
        # Paths and capability tokens must never enter access logs.
        pass

    def _send(self, code: int, body: bytes, content_type: str = 'application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, code: int, value: dict):
        self._send(code, json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))

    def _allowed(self, auth: bool = True) -> bool:
        expected = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host', '') not in expected:
            self._json(403, {'error': 'Host no permitido.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in expected}:
            self._json(403, {'error': 'Origen no permitido.'})
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self._json(403, {'error': 'Solicitud externa no permitida.'})
            return False
        if auth and not secrets.compare_digest(self.headers.get('X-Nexo-Token', '').encode('utf-8'), self.server.token.encode('utf-8')):
            self._json(401, {'error': 'Abre el panel con su acceso directo para conectar esta sesión.'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self._allowed(auth=path.startswith('/api/')):
            return
        if path == '/health':
            return self._json(200, {'service': SERVICE, 'version': __version__, 'instance': self.server.instance})
        if path == '/api/state':
            self.server.touch_client()
            try:
                return self._json(200, self.server.snapshot())
            except (OSError, ValueError) as error:
                return self._json(500, {'error': str(error)[:500]})
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/index.html': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in assets:
            return self._json(404, {'error': 'Ruta no encontrada.'})
        name, mime = assets[path]
        try:
            self._send(200, (ASSETS / name).read_bytes(), mime)
        except OSError:
            self._json(503, {'error': 'Faltan los archivos del panel. Reinstala Nexo.'})

    def do_POST(self):
        if not self._allowed():
            return
        if urlsplit(self.path).path == '/api/presence':
            self.server.touch_client()
            return self._json(200, {'connected': True})
        if urlsplit(self.path).path in {'/api/open-claim', '/api/open-manual'}:
            return self._json(200, self.server.claim_browser(urlsplit(self.path).path == '/api/open-manual'))
        if urlsplit(self.path).path == '/api/shutdown':
            self._json(200, {'stopping': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if urlsplit(self.path).path != '/api/projects':
            return self._json(404, {'error': 'Ruta no encontrada.'})
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self._json(415, {'error': 'Se requiere JSON.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                return self._json(413, {'error': 'Solicitud demasiado grande o vacía.'})
            self.connection.settimeout(10)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or not isinstance(payload.get('path'), str):
                raise ValueError('Indica una carpeta de proyecto.')
            project = Path(payload['path']).expanduser()
            if not project.is_absolute() or not project.is_dir():
                raise ValueError('La ruta debe ser absoluta y corresponder a una carpeta existente.')
            from .activity import register_project
            item = register_project(project, self.server.home)
            if item.get('error'):
                return self._json(503, {'error': 'No se pudo guardar el proyecto. Comprueba el acceso al perfil de Nexo.'})
            with self.server.snapshot_lock:
                self.server.snapshot_cache = None
            self._json(200, {'project': item})
        except (ValueError, OSError) as error:
            self._json(400, {'error': str(error)[:500]})

    def do_OPTIONS(self):
        self._json(405, {'error': 'Método no permitido.'})


def running_server(home: Path) -> dict | None:
    try:
        saved = json.loads(state_file(home).read_text(encoding='utf-8'))
        port = saved['port']
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        request = Request(f'http://127.0.0.1:{port}/health')
        with urlopen(request, timeout=1) as response:
            found = json.load(response)
        if found.get('service') != SERVICE or found.get('instance') != saved.get('instance'):
            return None
        if not isinstance(saved.get('token'), str) or not saved['token']:
            return None
        # Never trust a URL read from disk to launch a browser.
        saved['url'] = f'http://127.0.0.1:{port}/#token=' + saved['token']
        return saved
    except (OSError, ValueError, KeyError, TypeError, URLError):
        return None


def serve(home: Path, port: int = 0):
    home = home.expanduser().resolve()
    with DashboardServer(home, port) as server:
        info = server.info()
        state_file(home).parent.mkdir(parents=True, exist_ok=True)
        atomic_write(state_file(home), json.dumps(info, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'url': info['url'], 'pid': info['pid'], 'local_only': True}), flush=True)
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            # Preserve newer instances if the user deliberately launched another server.
            with contextlib.suppress(OSError, ValueError):
                current = json.loads(state_file(home).read_text(encoding='utf-8'))
                if current.get('instance') == server.instance:
                    state_file(home).unlink()


def open_dashboard(home: Path, project: Path, port: int = 0, browser: bool = True, register: bool = True,
                   automatic: bool = False) -> dict:
    from .activity import register_project
    home = home.expanduser().resolve()
    if register:
        added = register_project(project, home)
        if added.get('error'):
            raise ValueError('No se pudo registrar el proyecto: ' + added['error'])
    with _startup_lock(home, timeout=2 if automatic else 20):
        info = running_server(home)
        if info and info.get('version') != __version__:
            if stop_dashboard(home).get('running'):
                raise ValueError('No se pudo detener la versión anterior del panel.')
            info = None
        reused = info is not None
        if info is None:
            package_root = str(Path(__file__).resolve().parent.parent)
            bootstrap = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from nexo_kit.cli import main; raise SystemExit(main())'
            command = [sys.executable, '-I', '-B', '-c', bootstrap, package_root, '--project', str(project), 'dashboard',
                       'serve', '--codex-home', str(home), '--port', str(port)]
            env = dict(os.environ, CODEX_HOME=str(home), PYTHONUTF8='1')
            options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
            process = subprocess.Popen(command, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ValueError('No se pudo iniciar el panel. Ejecuta dashboard serve para consultar el error.')
                info = running_server(home)
                if info:
                    break
                time.sleep(0.15)
            if not info:
                process.terminate()
                process.wait(timeout=5)
                raise ValueError('El panel no respondió dentro del tiempo de arranque.')
        browser_opened = False
        browser_reason = 'not-requested'
        if browser:
            route = '/api/open-claim' if automatic else '/api/open-manual'
            request = Request(f'http://127.0.0.1:{info["port"]}' + route, data=b'',
                              headers={'X-Nexo-Token': info['token']}, method='POST')
            with urlopen(request, timeout=2) as response:
                claim = json.load(response)
            browser_reason = claim['reason']
            if claim['open']:
                browser_opened = bool(webbrowser.open(info['url']))
                if not browser_opened:
                    browser_reason = 'browser-unavailable'
    return {'url': info['url'], 'pid': info['pid'], 'reused': reused, 'local_only': True,
            'browser_opened': browser_opened, 'browser_reason': browser_reason}


def auto_open_dashboard(home: Path | None, project: Path, browser: bool = True) -> dict:
    """Opt-in convenience; never break work or bypass a host's denied access."""
    try:
        home = (home or default_home()).expanduser().resolve()
        from .personal import preferences
        if not preferences(home)['auto_open_dashboard']:
            return {'enabled': False, 'browser_opened': False}
        if os.environ.get('NEXO_WORKER') == '1':
            return {'enabled': True, 'browser_opened': False, 'browser_reason': 'runner-worker'}
        result = open_dashboard(home, project, register=False, automatic=True, browser=browser)
        # Capability URLs remain in the launcher, never in telemetry/hook output.
        return {'enabled': True, **{key: result[key] for key in
                ('pid', 'reused', 'browser_opened', 'browser_reason')}}
    except Exception as error:
        return {'enabled': True, 'browser_opened': False,
                'error': 'No se pudo abrir el panel automáticamente (' + type(error).__name__ + '). Usa dashboard para diagnosticarlo.'}


def stop_dashboard(home: Path) -> dict:
    info = running_server(home)
    if not info:
        return {'running': False, 'stopped': False}
    request = Request(f'http://127.0.0.1:{info["port"]}/api/shutdown', data=b'{}',
                      headers={'X-Nexo-Token': info['token'], 'Content-Type': 'application/json'}, method='POST')
    with urlopen(request, timeout=3) as response:
        result = json.load(response)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and running_server(home):
        time.sleep(0.1)
    return {'stopped': result.get('stopping', False), 'running': bool(running_server(home))}


def execute(args, project: Path):
    home = args.codex_home.expanduser().resolve()
    if args.operation == 'auto':
        return auto_open_dashboard(home, project, browser=not args.no_browser)
    if args.operation == 'serve':
        serve(home, args.port)
        return None
    if args.operation == 'status':
        info = running_server(home)
        return {'running': bool(info), 'url': info['url'] if info else None}
    if args.operation == 'stop':
        return stop_dashboard(home)
    if args.operation == 'add':
        from .activity import register_project
        path = (args.path or project).expanduser()
        if not path.is_dir():
            raise ValueError('La carpeta del proyecto no existe.')
        result = register_project(path, home)
        if result.get('error'):
            raise ValueError('No se pudo registrar el proyecto: ' + result['error'])
        return result
    return open_dashboard(home, project, args.port, not args.no_browser, not args.no_register)
