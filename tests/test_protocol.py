"""Black-box hook and MCP wire tests using local Python subprocesses, no models."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nexo_kit.context import ContextIndex
from nexo_kit.hooks import handle_hook
from nexo_kit.install import install
from nexo_kit.store import Store
from nexo_kit.util import read_json, write_json


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nexo protocol ")
        self.project = Path(self.tmp.name)
        self.profile = tempfile.TemporaryDirectory(prefix="nexo-protocol-profile-")
        self.addCleanup(self.profile.cleanup)
        environment = patch.dict(os.environ, {"CODEX_HOME": self.profile.name})
        environment.start()
        self.addCleanup(environment.stop)
        install(self.project)
        self.runtime = self.project / ".nexo/runtime/nexo.py"

    def tearDown(self):
        self.tmp.cleanup()

    def run_runtime(self, arguments: list[str], payload: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("NEXO_WORKER", None)
        env["PYTHONUTF8"] = "1"
        return subprocess.run([sys.executable, str(self.runtime), "--project", str(self.project), *arguments],
                              input=payload, capture_output=True, text=True, encoding="utf-8",
                              cwd=self.project, env=env, timeout=25)

    def run_runtime_utf8_bytes(self, arguments: list[str], payload: str) -> subprocess.CompletedProcess:
        """A real pipe, with the legacy Windows input encoding before CLI startup.

        MCP and lifecycle JSON use UTF-8 even when Python inherits cp1252. Do
        not enable PYTHONUTF8 here: doing so would hide an adapter regression.
        """
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("NEXO_WORKER", None)
        env["PYTHONUTF8"] = "0"
        env["PYTHONIOENCODING"] = "cp1252"
        return subprocess.run([sys.executable, str(self.runtime), "--project", str(self.project), *arguments],
                              input=payload.encode("utf-8"), capture_output=True,
                              cwd=self.project, env=env, timeout=25)

    @staticmethod
    def request(identifier, method, params=None):
        message = {"jsonrpc": "2.0", "id": identifier, "method": method}
        if params is not None:
            message["params"] = params
        return message

    def exchange(self, messages):
        initialized_here = not any(isinstance(message, dict) and message.get("method") == "initialize" for message in messages)
        if initialized_here:
            messages = [self.request("test-init", "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                          "clientInfo": {"name": "nexo-wire-test", "version": "1"}}),
                        {"jsonrpc": "2.0", "method": "notifications/initialized"}, *messages]
        result = self.run_runtime(["mcp"], "\n".join(json.dumps(m) if not isinstance(m, str) else m for m in messages) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        return [response for response in responses if response.get("id") != "test-init"]

    def call(self, identifier, name, arguments=None):
        return self.request(identifier, "tools/call", {"name": "nexo_" + name, "arguments": arguments or {}})

    @staticmethod
    def tool_value(response):
        result = response["result"]
        if "structuredContent" in result:
            return result["structuredContent"]
        text = "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")
        return json.loads(text)

    def test_hook_context_only_includes_active_memory(self):
        with Store(self.project) as store:
            store.add_memory("unreviewed-hook-instruction", source="user", status="candidate")
            store.add_memory("known-active-observation", source="unit-test", status="active")
        result = handle_hook(self.project, "SessionStart", {"session_id": "fixture"})
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        self.assertIn("known-active-observation", output["additionalContext"])
        self.assertNotIn("unreviewed-hook-instruction", output["additionalContext"])
        self.assertEqual(handle_hook(self.project, "Stop", {}), {})

    def test_hook_capture_is_opt_in_redacted_and_avoids_recursive_tools(self):
        handle_hook(self.project, "PostToolUse", {"tool_name": "Bash", "tool_response": "uncapturedunique"})
        with ContextIndex(self.project) as index:
            self.assertEqual(index.search("uncapturedunique"), [])
        config = read_json(self.project / ".nexo/config.json")
        config["hooks"]["capture_tool_output"] = True
        write_json(self.project / ".nexo/config.json", config)
        handle_hook(self.project, "PostToolUse", {"tool_name": "Bash", "tool_response": "capturedunique password=do-not-store"})
        handle_hook(self.project, "PostToolUse", {"tool_name": "Read", "tool_response":
                                                    {"label": "structuredunique", "password": "structured-secret-value"}})
        handle_hook(self.project, "PostToolUse", {"tool_name": "mcp__nexo__context_fetch", "tool_response": "recursiveunique"})
        with ContextIndex(self.project) as index:
            hits = index.search("capturedunique")
            self.assertTrue(hits)
            self.assertNotIn("do-not-store", index.fetch(hits[0]["id"])["text"])
            structured = index.search("structuredunique")
            self.assertTrue(structured)
            self.assertNotIn("structured-secret-value", index.fetch(structured[0]["id"])["text"])
            self.assertEqual(index.search("recursiveunique"), [])

    def test_worker_hooks_do_not_write_shared_state(self):
        with Store(self.project) as store:
            before = store.events()
        with patch.dict(os.environ, {"NEXO_WORKER": "1"}):
            self.assertEqual(handle_hook(self.project, "SessionStart", {}), {})
        with Store(self.project) as store:
            self.assertEqual(store.events(), before)

    def test_hook_subprocess_emits_only_json(self):
        result = self.run_runtime(["hook", "--event", "SessionStart"], json.dumps({"session_id": "stdio-fixture"}))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
        stopped = self.run_runtime(["hook", "--event", "Stop"], "{}")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(json.loads(stopped.stdout), {})

    def test_hook_reads_utf8_bytes_under_legacy_windows_encoding(self):
        expected = "utf8hook Español 漢字 🔧"
        config = read_json(self.project / ".nexo/config.json")
        config["hooks"]["capture_tool_output"] = True
        write_json(self.project / ".nexo/config.json", config)
        payload = json.dumps({"session_id": "sesión-漢字", "tool_name": "Read",
                              "tool_response": expected}, ensure_ascii=False)
        result = self.run_runtime_utf8_bytes(["hook", "--event", "PostToolUse"], payload)
        self.assertEqual(result.returncode, 0, repr(result.stderr))
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), {})
        with ContextIndex(self.project) as index:
            hits = index.search("utf8hook")
            self.assertEqual(len(hits), 1)
            self.assertEqual(json.loads(index.fetch(hits[0]["id"])["text"]), expected)
        with Store(self.project) as store:
            events = [event for event in store.events() if event["kind"] == "hook"]
            self.assertEqual(events[0]["payload"]["session"], "sesión-漢字")

    def test_mcp_reads_and_returns_utf8_bytes_under_legacy_windows_encoding(self):
        expected = "utf8mcp Español 漢字 🔧"
        messages = [self.request(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                 "clientInfo": {"name": "utf8-wire-test", "version": "1"}}),
                    self.call(2, "memory_propose", {"text": expected, "source": "prueba-漢字"})]
        result = self.run_runtime_utf8_bytes(["mcp"], "\n".join(json.dumps(message, ensure_ascii=False)
                                             for message in messages) + "\n")
        self.assertEqual(result.returncode, 0, repr(result.stderr))
        responses = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
        value = self.tool_value(responses[1])
        self.assertEqual(value["text"], expected)
        self.assertEqual(value["source"], "prueba-漢字")
        with Store(self.project) as store:
            self.assertEqual(store.get_memory(value["id"])["text"], expected)

    @unittest.skipUnless(os.name == "nt", "Codex commandWindows hook requires Windows")
    def test_codex_encoded_hook_command_passes_json_stdin(self):
        hooks = read_json(self.project / ".codex/hooks.json")["hooks"]
        command = hooks["SessionStart"][0]["hooks"][0]["commandWindows"]
        result = subprocess.run(command, input='{"session_id":"encoded-hook"}', capture_output=True,
                                text=True, encoding="utf-8", errors="replace", cwd=self.project, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"], "SessionStart")

    @unittest.skipUnless(os.name == "nt", "Codex commandWindows hook requires Windows")
    def test_codex_encoded_hook_preserves_utf8_input_and_output(self):
        expected = "encodedunique Español 漢字 🔧"
        with Store(self.project) as store:
            store.add_memory(expected, source="encoding-test", status="active")
        hooks = read_json(self.project / ".codex/hooks.json")["hooks"]
        command = hooks["SessionStart"][0]["hooks"][0]["commandWindows"]
        env = os.environ.copy()
        env.pop("NEXO_WORKER", None)
        env["PYTHONUTF8"] = "0"
        env["PYTHONIOENCODING"] = "cp1252"
        payload = json.dumps({"session_id": "encoded-sesión-漢字"}, ensure_ascii=False).encode("utf-8")
        result = subprocess.run(command, input=payload, capture_output=True, cwd=self.project,
                                env=env, timeout=25)
        self.assertEqual(result.returncode, 0, repr(result.stderr))
        output = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn(expected, output)
        with Store(self.project) as store:
            events = [event for event in store.events() if event["kind"] == "hook"]
            self.assertEqual(events[0]["payload"]["session"], "encoded-sesión-漢字")

    def test_mcp_initialization_notification_and_tools(self):
        responses = self.exchange([
            self.request(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                             "clientInfo": {"name": "nexo-wire-test", "version": "1"}}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            self.request(2, "tools/list"), self.call(3, "status")])
        self.assertEqual([r["id"] for r in responses], [1, 2, 3])
        self.assertIsInstance(responses[0]["result"]["protocolVersion"], str)
        names = {t["name"].removeprefix("nexo_") for t in responses[1]["result"]["tools"]}
        self.assertTrue({"status", "task_add", "ready_tasks", "memory_search", "memory_propose",
                         "context_search", "context_fetch", "checkpoint", "verify_task"}.issubset(names))
        self.assertIsInstance(self.tool_value(responses[2]), dict)

    def test_mcp_task_workflow_and_candidate_memory_filter(self):
        with Store(self.project) as store:
            store.add_memory("sharedkeyword approved knowledge", source="test", status="active")
            store.add_memory("sharedkeyword candidate secret instruction", source="test", status="candidate")
        responses = self.exchange([self.call(1, "task_add", {"title": "Wire task", "description": "Make a fixture"}),
                                   self.call(2, "ready_tasks"),
                                   self.call(3, "memory_search", {"query": "sharedkeyword"}),
                                   self.call(4, "memory_propose", {"text": "new observation", "source": "wire-test"})])
        values = [self.tool_value(response) for response in responses]
        self.assertIn("Wire task", json.dumps(values[0]))
        self.assertIn("Wire task", json.dumps(values[1]))
        self.assertIn("approved knowledge", json.dumps(values[2]))
        self.assertNotIn("candidate secret instruction", json.dumps(values[2]))
        self.assertIn("candidate", json.dumps(values[3]))

    def test_mcp_context_bounds_and_path_rejection(self):
        with ContextIndex(self.project) as index:
            artifact = index.capture("wirecontext " * 10000)
        responses = self.exchange([self.call(1, "context_fetch", {"id": artifact["id"], "limit": 15}),
                                   self.call(2, "context_search", {"query": "wirecontext"}),
                                   self.call(3, "context_fetch", {"id": "../state.sqlite3"})])
        first = self.tool_value(responses[0])
        self.assertLessEqual(len(first["text"]), 15)
        hits = self.tool_value(responses[1])
        self.assertLessEqual(sum(len(hit["snippet"]) for hit in hits), 6000)
        self.assertTrue("error" in responses[2] or responses[2].get("result", {}).get("isError"))

    def test_mcp_parse_error_unknown_method_and_missing_args_are_recoverable(self):
        responses = self.exchange(["{not-json", self.request(1, "method/does-not-exist"),
                                   self.call(2, "task_add", {}), self.call(3, "status")])
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertIsNone(responses[0]["id"])
        self.assertEqual(responses[1]["error"]["code"], -32601)
        self.assertTrue("error" in responses[2] or responses[2].get("result", {}).get("isError"))
        self.assertIsInstance(self.tool_value(responses[3]), dict)

    def test_mcp_verification_requires_checks_before_completing_review(self):
        with Store(self.project) as store:
            task = store.add_task("Verify wire fixture")
            claim = store.claim_task(task["id"], "wire-test")
            store.finish_task(task["id"], claim["lease_token"], "review")
        response = self.exchange([self.call(1, "verify_task", {"task_id": task["id"], "complete": True})])[0]
        self.assertEqual(self.tool_value(response)["verdict"], "failed")
        with Store(self.project) as store:
            self.assertEqual(store.get_task(task["id"])["status"], "failed")
            store.retry_task(task["id"])
            claim = store.claim_task(task["id"], "wire-retry")
            store.finish_task(task["id"], claim["lease_token"], "review")
        (self.project / "result.txt").write_text("verified", encoding="utf-8")
        config = read_json(self.project / ".nexo/config.json")
        config["checks"] = [{"name": "wire-evidence", "command": ["{python}", "-c",
                              "from pathlib import Path; assert Path('result.txt').read_text() == 'verified'"],
                              "timeout_seconds": 10}]
        write_json(self.project / ".nexo/config.json", config)
        response = self.exchange([self.call(1, "verify_task", {"task_id": task["id"], "complete": True})])[0]
        self.assertEqual(self.tool_value(response)["verdict"], "passed")
        with Store(self.project) as store:
            self.assertEqual(store.get_task(task["id"])["status"], "done")

    def test_mcp_rejects_wrong_argument_types_without_mutating_tasks(self):
        responses = self.exchange([self.call(1, "task_add", {"title": ["not", "a", "string"]}),
                                   self.call(2, "task_add", {"title": "should not exist", "command": "unexpected"}),
                                   self.call(3, "status")])
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertTrue(responses[1]["result"]["isError"])
        self.assertEqual(self.tool_value(responses[2])["tasks"], [])


if __name__ == "__main__":
    unittest.main()
