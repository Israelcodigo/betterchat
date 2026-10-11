"""Windows hook launchers preserve argv/UTF-8 in either native parent shell."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from nexo_kit.install import _hook_entries, _powershell, _windows_hook_command
from nexo_kit.personal import EVENTS, _personal_hooks
from nexo_kit.util import process_options, stop_process


class HookCommandTests(unittest.TestCase):
    def test_safe_absolute_ascii_argv_uses_no_nested_powershell(self):
        argv = [r'C:\Python\python.exe', r'C:\profile\nexo-kit\runtime\nexo.py',
                'hook', '--event', 'SessionStart']
        self.assertEqual(_windows_hook_command(argv), ' '.join(argv))

    def test_shell_sensitive_tokens_retain_encoded_literal_fallback(self):
        for token in ['with space', "O'Brien", '$value', '$(command)', '%PATH%', '!value!',
                      'a&b', 'a|b', 'a;b', '(value)', 'a>b', 'a<b', '`escape', 'a"b',
                      '[value]', 'café', '漢字', '']:
            with self.subTest(token=token):
                argv = [r'C:\Python\python.exe', r'C:\nexo\probe.py', token]
                command = _windows_hook_command(argv)
                self.assertEqual(command, _powershell(argv))
                body = base64.b64decode(command.split()[-1]).decode('utf-16-le')
                self.assertEqual(body, '& ' + ' '.join("'" + item.replace("'", "''") + "'"
                                                      for item in argv))
        self.assertEqual(_windows_hook_command(['python.exe', 'hook']),
                         _powershell(['python.exe', 'hook']))

    def test_both_installation_scopes_apply_launcher_to_all_four_events(self):
        project = Path(r'C:\nexo_fixture')
        for scope, entries in [('project', _hook_entries(project, 'codex')),
                               ('personal', _personal_hooks(project / 'runtime/nexo.py'))]:
            with self.subTest(scope=scope):
                self.assertEqual(set(entries), set(EVENTS))
                for event, groups in entries.items():
                    handler = groups[0]['hooks'][0]
                    self.assertEqual(handler['timeout'], 30)
                    self.assertTrue(handler['commandWindows'])
                    self.assertIn(event, handler['command'])


@unittest.skipUnless(os.name == 'nt', 'Requires the actual Windows cmd and PowerShell parents')
class WindowsHookLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-launcher-')
        self.root = Path(self.tmp.name).resolve()
        self.safe = self.root / 'safe'
        self.special = self.root / "O'Brien $value %PATH% !literal! & (spaces) é漢"
        source = ("import json,sys\n"
                  "sys.stdin.reconfigure(encoding='utf-8')\n"
                  "sys.stdout.reconfigure(encoding='utf-8')\n"
                  "print(json.dumps({'argv':sys.argv[1:], 'payload':json.load(sys.stdin)},ensure_ascii=False))\n")
        for folder in (self.safe, self.special):
            folder.mkdir()
            (folder / 'probe.py').write_text(source, encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def check_parent(self, parent: str, folder: Path, fallback: bool):
        payload = {'session_id': 'sesión-漢字-🔧', 'cwd': str(folder)}
        for event in EVENTS:
            with self.subTest(parent=parent, event=event, fallback=fallback):
                argv = [sys.executable, str(folder / 'probe.py'), '--project', str(folder),
                        'hook', '--event', event]
                command = _windows_hook_command(argv)
                self.assertEqual(command.startswith('powershell.exe '), fallback)
                if parent == 'powershell':
                    outer = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command]
                else:
                    outer = [os.environ.get('COMSPEC', 'cmd.exe'), '/D', '/S', '/C', command]
                env = dict(os.environ)
                env['PYTHONUTF8'] = '0'
                env['PYTHONIOENCODING'] = 'cp1252'
                process = subprocess.Popen(outer, cwd=folder, env=env, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, **process_options())
                try:
                    out, err = process.communicate(json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                                   timeout=30)
                except subprocess.TimeoutExpired:
                    stop_process(process)
                    out, err = process.communicate()
                    self.fail(f'{parent} {event} exceeded hook budget: {err[:300]!r}')
                self.assertEqual(process.returncode, 0, repr(err))
                actual = json.loads(out.decode('utf-8'))
                self.assertEqual(actual['argv'], argv[2:])
                self.assertEqual(actual['payload'], payload)

    def test_cmd_parent_safe_ascii(self):
        self.check_parent('cmd', self.safe, False)

    def test_powershell_parent_safe_ascii(self):
        self.check_parent('powershell', self.safe, False)

    def test_cmd_parent_special_characters(self):
        self.check_parent('cmd', self.special, True)

    def test_powershell_parent_special_characters(self):
        self.check_parent('powershell', self.special, True)


if __name__ == '__main__':
    unittest.main()
