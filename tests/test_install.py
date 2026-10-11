"""Installation behavior on real local paths; no global agent configuration used."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from nexo_kit.install import install, uninstall
from nexo_kit.util import atomic_write


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nexo-install-")
        self.project = Path(self.tmp.name).resolve() / "project spaces O'Brien $nexo_test"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_has_no_filesystem_side_effects(self):
        result = install(self.project, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertIn(".nexo/runtime/nexo.py", result["files"])
        self.assertEqual(list(self.project.iterdir()), [])

    def test_invalid_existing_toml_aborts_before_any_write(self):
        (self.project / '.codex').mkdir()
        config = self.project / '.codex/config.toml'
        original = b'model = "unterminated\r\n[mcp_servers.other]\r\ncommand = "keep"\r\n'
        config.write_bytes(original)
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(ValueError, 'Invalid TOML.*config.toml'):
                    install(self.project, dry_run=dry_run)
                self.assertEqual(config.read_bytes(), original)
                self.assertFalse((self.project / '.nexo').exists())
                self.assertFalse((self.project / 'AGENTS.md').exists())

    def test_injected_write_failure_restores_exact_originals(self):
        # Cover failures before and after replacement, plus the manifest commit.
        for stage in ('before', 'after', 'manifest'):
            with self.subTest(stage=stage):
                project = self.project / stage
                (project / '.claude').mkdir(parents=True)
                (project / 'AGENTS.md').write_bytes(b'\xef\xbb\xbf# User rules\r\nPreserve exact bytes.\r\n')
                (project / '.claude/settings.json').write_bytes(b'{\r\n  "userPreference": "retain"\r\n}\r\n')
                original_files = {str(path.relative_to(project)): path.read_bytes()
                                  for path in project.rglob('*') if path.is_file()}
                failed = False

                def fail_once(path, text):
                    nonlocal failed
                    selected = (path.name == 'install-manifest.json' if stage == 'manifest'
                                else path.name == 'settings.json' and path.parent.name == '.claude')
                    if selected and not failed:
                        failed = True
                        if stage in {'after', 'manifest'}:
                            atomic_write(path, text)
                        raise OSError('Injected disk-write failure')
                    atomic_write(path, text)

                with patch('nexo_kit.install.atomic_write', side_effect=fail_once):
                    with self.assertRaisesRegex(OSError, 'Injected disk-write failure'):
                        install(project)
                self.assertTrue(failed, 'The injected write failure was not reached')
                after_files = {str(path.relative_to(project)): path.read_bytes()
                               for path in project.rglob('*') if path.is_file()}
                self.assertEqual(after_files, original_files)
                self.assertFalse((project / '.nexo/install-manifest.json').exists())

    def test_preserves_existing_settings_and_generates_valid_toml_json(self):
        (self.project / ".claude").mkdir()
        (self.project / ".codex").mkdir()
        settings = {"permissions": {"deny": ["Read(.env)"]}, "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo existing"}]}]}}
        (self.project / ".claude/settings.json").write_text(json.dumps(settings), encoding="utf-8")
        (self.project / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "other": {"command": "custom-other", "args": ["keep"]}}}), encoding="utf-8")
        (self.project / ".codex/config.toml").write_text('model = "retain-model"\n[mcp_servers.other]\ncommand = "other"\n', encoding="utf-8")
        (self.project / "AGENTS.md").write_text("# Existing team rules\nKeep this paragraph.\n", encoding="utf-8")
        install(self.project)
        current = json.loads((self.project / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(current["permissions"], settings["permissions"])
        self.assertEqual(current["hooks"]["SessionStart"][0], settings["hooks"]["SessionStart"][0])
        mcp = json.loads((self.project / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["mcpServers"]["other"]["args"], ["keep"])
        self.assertIn(str(self.project), mcp["mcpServers"]["nexo"]["args"])
        config = tomllib.loads((self.project / ".codex/config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["model"], "retain-model")
        self.assertEqual(config["mcp_servers"]["other"]["command"], "other")
        self.assertIn(str(self.project), config["mcp_servers"]["nexo"]["args"])
        self.assertIn("Keep this paragraph.", (self.project / "AGENTS.md").read_text(encoding="utf-8"))
        for path in (self.project / ".codex/agents").glob("*.toml"):
            self.assertIn("developer_instructions", tomllib.loads(path.read_text(encoding="utf-8")))

    def test_reinstall_is_idempotent_without_hook_duplication(self):
        install(self.project)
        before = {name: (self.project / name).read_bytes() for name in
                  (".claude/settings.json", ".codex/hooks.json", "AGENTS.md", ".codex/config.toml")}
        install(self.project)
        for name, content in before.items():
            self.assertEqual((self.project / name).read_bytes(), content)
        for name in (".claude/settings.json", ".codex/hooks.json"):
            hooks = json.loads((self.project / name).read_text(encoding="utf-8"))["hooks"]
            self.assertTrue(all(len(entries) == 1 for entries in hooks.values()))

    def test_modified_asset_is_not_overwritten_on_upgrade(self):
        install(self.project)
        skill = self.project / ".agents/skills/nexo-plan/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nUser customization.\n", encoding="utf-8")
        before = skill.read_bytes()
        with self.assertRaises(ValueError):
            install(self.project)
        self.assertEqual(skill.read_bytes(), before)

    def test_uninstall_restores_originals_preserves_user_edits_and_data(self):
        original = "# My instructions\nDo not remove this.\n"
        (self.project / "AGENTS.md").write_text(original, encoding="utf-8")
        install(self.project)
        skill = self.project / ".agents/skills/nexo-plan/SKILL.md"
        changed = skill.read_text(encoding="utf-8") + "\nMy edited rule.\n"
        skill.write_text(changed, encoding="utf-8")
        (self.project / "my-code.py").write_text("print('user')\n", encoding="utf-8")
        preview = uninstall(self.project, dry_run=True)
        self.assertIn(".agents/skills/nexo-plan/SKILL.md", preview["preserved_modified"])
        self.assertTrue((self.project / ".nexo/runtime/nexo.py").exists())
        result = uninstall(self.project)
        self.assertIn(".agents/skills/nexo-plan/SKILL.md", result["preserved_modified"])
        self.assertEqual(skill.read_text(encoding="utf-8"), changed)
        self.assertEqual((self.project / "AGENTS.md").read_text(encoding="utf-8"), original)
        self.assertTrue((self.project / ".nexo/state.sqlite3").is_file())
        self.assertTrue((self.project / ".nexo/config.json").is_file())
        self.assertTrue((self.project / "my-code.py").is_file())

    def test_owned_mcp_name_collision_fails_without_partial_install(self):
        original = {"mcpServers": {"nexo": {"command": "another-program"}}}
        (self.project / ".mcp.json").write_text(json.dumps(original), encoding="utf-8")
        with self.assertRaises(ValueError):
            install(self.project)
        self.assertEqual(json.loads((self.project / ".mcp.json").read_text(encoding="utf-8")), original)
        self.assertFalse((self.project / ".nexo").exists())

    def test_uninstall_after_config_edit_keeps_hook_targets_resolvable(self):
        install(self.project)
        path = self.project / ".claude/settings.json"
        settings = json.loads(path.read_text(encoding="utf-8"))
        settings["userPreference"] = "keep this change"
        path.write_text(json.dumps(settings), encoding="utf-8")
        uninstall(self.project)
        current = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(current["userPreference"], "keep this change")
        if "nexo.py" in json.dumps(current.get("hooks", {})):
            self.assertTrue((self.project / ".nexo/runtime/nexo.py").is_file(),
                            "Retained Nexo hooks must not target a deleted runtime")

    def test_copied_runtime_runs_without_source_on_pythonpath(self):
        install(self.project)
        runtime = self.project / ".nexo/runtime/nexo.py"
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONUTF8"] = "1"
        result = subprocess.run([sys.executable, str(runtime), "--project", str(self.project), "status"],
                                cwd=self.project, env=environment, capture_output=True, text=True,
                                encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIsInstance(json.loads(result.stdout), dict)
        # Isolated Python removes the source working directory and user site.
        code = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                "import nexo_kit; print(Path(nexo_kit.__file__).resolve())")
        probe = subprocess.run([sys.executable, "-I", "-c", code, str(runtime.parent)],
                               cwd=self.project, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertTrue(Path(probe.stdout.strip()).is_relative_to(runtime.parent))

    @unittest.skipUnless(os.name == "nt", "PowerShell installation command is Windows-specific")
    def test_documented_command_executes_with_literal_special_characters(self):
        install(self.project)
        instructions = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        command = next(line for line in instructions.splitlines() if line.startswith("& ") and line.endswith(" status"))
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                cwd=self.project, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIsInstance(json.loads(result.stdout), dict)


if __name__ == "__main__":
    unittest.main()
