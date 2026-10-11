"""Run dashboard release checks with an isolated Codex profile, never models."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    if node is None:
        print('Node.js is needed for the dashboard JavaScript behavior checks.', file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix='nexo-dashboard-checks-') as temporary:
        env = dict(os.environ, CODEX_HOME=str(Path(temporary) / 'profile'), PYTHONUTF8='1')
        commands = [
            [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'],
            [node, '--check', 'nexo_kit/assets/dashboard/app.js'],
            [node, 'scripts/test_dashboard_ui.cjs'],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=root, env=env, timeout=240)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
