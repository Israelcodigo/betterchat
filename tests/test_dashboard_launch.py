"""Real local server processes in disposable profiles; no browser or model."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from nexo_kit import dashboard


class DashboardLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-launch-test-')
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.home = self.root / 'disposable Codex profile'
        self.children = []

    def tearDown(self):
        try:
            dashboard.stop_dashboard(self.home)
        finally:
            for child in self.children:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)
            self.tmp.cleanup()

    def track_processes(self):
        real = subprocess.Popen
        def create(*args, **kwargs):
            child = real(*args, **kwargs)
            self.children.append(child)
            return child
        return patch.object(dashboard.subprocess, 'Popen', side_effect=create)

    def test_two_concurrent_openings_create_one_process_and_reuse_same_access(self):
        from nexo_kit import activity
        barrier = threading.Barrier(2)
        original_register = activity.register_project
        def register(*args, **kwargs):
            result = original_register(*args, **kwargs)
            barrier.wait(timeout=5)
            return result
        with self.track_processes(), patch.object(activity, 'register_project', side_effect=register):
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = [pool.submit(dashboard.open_dashboard, self.home, self.project, browser=False)
                           for _ in range(2)]
                results = [future.result(timeout=25) for future in pending]
        self.assertEqual(len(self.children), 1)
        self.assertEqual(results[0]['pid'], results[1]['pid'])
        self.assertEqual(results[0]['url'], results[1]['url'])
        self.assertEqual(sorted(result['reused'] for result in results), [False, True])
        self.assertFalse((self.project / '.nexo').exists())
        info = dashboard.running_server(self.home)
        request = Request(f'http://127.0.0.1:{info["port"]}/api/state', headers={'X-Nexo-Token': info['token']})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(json.load(response)['projects'][0]['path'], str(self.project))

    def test_wheel_layout_launch_ignores_project_package_shadow(self):
        # A wheel contains the package/assets, not an adjacent nexo.py script.
        installed = self.root / 'site-packages' / 'nexo_kit'
        shutil.copytree(Path(dashboard.__file__).parent, installed,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        self.assertFalse((installed.parent / 'nexo.py').exists())
        shadow = self.project / 'nexo_kit'
        shadow.mkdir()
        (shadow / '__init__.py').write_text('', encoding='utf-8')
        marker = self.project / 'project-package-executed.txt'
        (shadow / 'cli.py').write_text(
            "from pathlib import Path\nPath('project-package-executed.txt').write_text('unexpected import')\n",
            encoding='utf-8')
        with self.track_processes(), patch.object(dashboard, '__file__', str(installed / 'dashboard.py')):
            info = dashboard.open_dashboard(self.home, self.project, browser=False)
        self.assertFalse(info['reused'])
        self.assertFalse(marker.exists())
        self.assertEqual(len(self.children), 1)
        with urlopen(info['url'].split('#')[0], timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b'Nexo', response.read())

    def test_operating_system_releases_startup_lock_after_launcher_crashes(self):
        marker = self.root / 'lock-held.txt'
        package_root = str(Path(dashboard.__file__).resolve().parent.parent)
        program = '''import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from nexo_kit.dashboard import _startup_lock
with _startup_lock(Path(sys.argv[2])):
    Path(sys.argv[3]).write_text('held')
    time.sleep(30)
'''
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', program,
                                  package_root, str(self.home), str(marker)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, **options)
        self.children.append(child)
        deadline = time.monotonic() + 5
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.03)
        self.assertTrue(marker.exists(), 'Temporary launcher did not acquire the OS lock')
        child.terminate()
        child.wait(timeout=5)
        started = time.monotonic()
        with dashboard._startup_lock(self.home):
            self.assertLess(time.monotonic() - started, 2)


if __name__ == '__main__':
    unittest.main()
