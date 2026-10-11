"""End-to-end local workers: real subprocesses, Git worktrees and check receipts.

These tests exercise the orchestration runtime without a model, credentials or
network. The worker is a small Python fixture; it is deliberately independent of
the runner implementation.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nexo_kit.config import defaults
from nexo_kit.actions import verify_task
from nexo_kit.evidence import parse_junit, validate_evidence, verify
from nexo_kit.runner import run
from nexo_kit.store import Store
from nexo_kit.util import fingerprint, inside, read_json, write_json
from nexo_kit.workspace import git, integrate, prepare_workspace, resolve_workspace, workspace_record


@unittest.skipUnless(shutil.which("git"), "Git is required for worktree integration tests")
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nexo runtime ")
        self.project = Path(self.tmp.name).resolve()
        (self.project / ".gitignore").write_text(".nexo/\n__pycache__/\n", encoding="utf-8")
        self.config = defaults()
        self.config["runner"].update({"max_workers": 1, "max_cycles": 1, "timeout_seconds": 10})
        self.config["checks"] = [{"name": "fixture-check", "command": ["{python}", "-c", "print('checked')"],
                                  "timeout_seconds": 5}]

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self, worker="from pathlib import Path\nPath('artifact.txt').write_text('correct')\n"):
        (self.project / "worker.py").write_text(worker, encoding="utf-8")
        write_json(self.project / ".nexo/config.json", self.config)
        git(self.project, "init", "--quiet")
        git(self.project, "add", ".")
        git(self.project, "commit", "--quiet", "-m", "Fixture base")

    def task(self, task_id="work", depends_on=(), max_attempts=3):
        with Store(self.project) as store:
            return store.add_task("Implement " + task_id, depends_on=depends_on,
                                  task_id=task_id, max_attempts=max_attempts)

    def run_tasks(self, **kwargs):
        return run(self.project, backend="command", command=["{python}", "worker.py"], **kwargs)

    def test_worker_isolated_commit_verified_receipt_and_done(self):
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "from pathlib import Path; assert Path('artifact.txt').read_text() == 'correct'; print('artifact verified')"]
        self.initialize()
        self.task()
        result = self.run_tasks()
        self.assertTrue(result["complete"], result)
        task = result["tasks"][0]
        workspace = resolve_workspace(self.project, "work")
        self.assertEqual(task["status"], "done")
        self.assertEqual((workspace / "artifact.txt").read_text(), "correct")
        self.assertFalse((self.project / "artifact.txt").exists())
        self.assertTrue(read_json(workspace_record(self.project, "work"))["commit"])
        validate_evidence(self.project, task["evidence"], workspace)

    def test_dependent_task_inherits_verified_prerequisite_commit(self):
        self.initialize("""import os
from pathlib import Path
task = os.environ['NEXO_TASK_ID']
if task == 'a':
    Path('a.txt').write_text('prerequisite')
elif task == 'b':
    assert Path('a.txt').read_text() == 'prerequisite', 'dependency output absent'
    Path('b.txt').write_text('dependent')
""")
        self.task("a")
        self.task("b", depends_on=["a"])
        result = self.run_tasks()
        self.assertTrue(result["complete"], result)
        workspace = resolve_workspace(self.project, "b")
        self.assertEqual((workspace / "a.txt").read_text(), "prerequisite")
        self.assertEqual((workspace / "b.txt").read_text(), "dependent")
        self.assertEqual(len(result["runs"]), 2)

    def test_parallel_workers_have_isolated_files_and_run_concurrently(self):
        self.initialize("""import os, time
from pathlib import Path
task = os.environ['NEXO_TASK_ID']
other = 'y' if task == 'x' else 'x'
Path(task + '.txt').write_text(task)
coordination = Path(os.environ['NEXO_PROJECT']) / '.nexo' / 'fixture-barrier'
coordination.mkdir(exist_ok=True)
(coordination / task).write_text(str(Path.cwd()))
deadline = time.monotonic() + 6
while not (coordination / other).exists():
    assert time.monotonic() < deadline, 'Workers were not run concurrently'
    time.sleep(0.02)
assert not Path(other + '.txt').exists(), 'Workspaces leaked between tasks'
""")
        self.task("x")
        self.task("y")
        result = self.run_tasks(workers=2)
        self.assertTrue(result["complete"], result)
        workspaces = {item["workspace"] for item in result["runs"]}
        self.assertEqual(len(workspaces), 2)
        self.assertFalse((self.project / "x.txt").exists())
        self.assertFalse((self.project / "y.txt").exists())

    def test_worker_timeout_fails_without_completing(self):
        self.config["runner"]["timeout_seconds"] = 1
        self.initialize("import time\ntime.sleep(15)\n")
        self.task(max_attempts=1)
        started = time.monotonic()
        result = self.run_tasks()
        self.assertFalse(result["complete"])
        self.assertEqual(result["tasks"][0]["status"], "failed", result)
        self.assertIn("timeout", result["tasks"][0]["error"].lower())
        self.assertLess(time.monotonic() - started, 10)

    def test_external_cancellation_stops_worker_and_does_not_retry_or_complete(self):
        self.initialize("""import os, time
from pathlib import Path
Path(os.environ['NEXO_PROJECT'], '.nexo', 'fixture-started').write_text('started')
time.sleep(20)
Path('late.txt').write_text('must not happen')
""")
        self.task(max_attempts=3)
        cancelled = threading.Event()

        def cancel_after_start():
            deadline = time.monotonic() + 5
            while not (self.project / ".nexo/fixture-started").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            cancelled.set()

        thread = threading.Thread(target=cancel_after_start)
        thread.start()
        started = time.monotonic()
        try:
            result = self.run_tasks(cycles=3, cancel_event=cancelled)
        finally:
            thread.join(timeout=5)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["tasks"][0]["status"], "blocked", result)
        self.assertEqual(result["tasks"][0]["attempts"], 1)
        self.assertLess(time.monotonic() - started, 8)
        self.assertFalse((resolve_workspace(self.project, "work") / "late.txt").exists())

    def test_keyboard_interrupt_cancels_before_executor_shutdown(self):
        self.initialize("""import os, time
from pathlib import Path
Path(os.environ['NEXO_PROJECT'], '.nexo', 'fixture-started').write_text('started')
time.sleep(20)
""")
        self.task()
        from nexo_kit import runner
        real_wait = runner.concurrent.futures.wait

        def interrupted_wait(*args, **kwargs):
            if (self.project / ".nexo/fixture-started").exists():
                raise KeyboardInterrupt()
            return real_wait(*args, **kwargs)

        started = time.monotonic()
        with patch("nexo_kit.runner.concurrent.futures.wait", side_effect=interrupted_wait):
            with self.assertRaises(KeyboardInterrupt):
                self.run_tasks()
        self.assertLess(time.monotonic() - started, 8)
        with Store(self.project) as store:
            task = store.get_task("work")
            self.assertEqual(task["status"], "blocked")
            self.assertIsNone(task["lease_token"])
            self.assertTrue(any(item["kind"] == "run.cancelled" for item in store.events()))

    def test_cancellation_during_verification_preserves_review_and_stops_check(self):
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "import time, sys; from pathlib import Path; Path(sys.argv[1]).write_text('started'); "
            "time.sleep(20); Path('late-check.txt').write_text('must not happen')",
            "{project}/.nexo/check-started"]
        self.initialize()
        self.task()
        cancelled = threading.Event()

        def cancel_after_check_start():
            deadline = time.monotonic() + 8
            while not (self.project / ".nexo/check-started").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            cancelled.set()

        thread = threading.Thread(target=cancel_after_check_start)
        thread.start()
        started = time.monotonic()
        try:
            result = self.run_tasks(cancel_event=cancelled)
        finally:
            thread.join(timeout=10)
        self.assertTrue(result["cancelled"], result)
        self.assertFalse(result["complete"])
        self.assertEqual(result["tasks"][0]["status"], "review", result)
        self.assertLess(time.monotonic() - started, 10)
        self.assertFalse((resolve_workspace(self.project, "work") / "late-check.txt").exists())

    def test_zero_worker_or_cycle_override_is_rejected(self):
        self.initialize()
        self.task()
        for kwargs in ({"workers": 0}, {"cycles": 0}, {"workers": True}, {"cycles": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_tasks(**kwargs)

    def test_stale_worker_cannot_commit_or_change_replacement_owners_state(self):
        self.initialize()
        self.task()

        def lose_lease_before_commit(_log):
            db = sqlite3.connect(self.project / ".nexo/state.sqlite3")
            try:
                with db:
                    db.execute("UPDATE tasks SET lease_expires_at=0 WHERE id='work'")
            finally:
                db.close()
            with Store(self.project) as store:
                store.recover_expired()
                store.claim_task("work", "replacement-owner")
            return None

        with patch("nexo_kit.runner._provider_error", side_effect=lose_lease_before_commit), \
                patch("nexo_kit.runner.commit_workspace") as commit:
            result = self.run_tasks()
        commit.assert_not_called()
        self.assertFalse(result["complete"])
        self.assertEqual(result["tasks"][0]["status"], "running")
        self.assertEqual(result["tasks"][0]["lease_owner"], "replacement-owner")
        self.assertNotIn("commit", read_json(workspace_record(self.project, "work")))

    def test_cancellation_before_completion_preserves_review(self):
        self.initialize()
        self.task()
        cancelled = threading.Event()

        def cancel_after_validation(*args, **kwargs):
            validate_evidence(*args, **kwargs)
            cancelled.set()

        with patch("nexo_kit.runner.validate_evidence", side_effect=cancel_after_validation):
            result = self.run_tasks(cancel_event=cancelled)
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["tasks"][0]["status"], "review")

    def test_failed_gate_is_retried_with_a_bound_and_never_unlocks_dependent(self):
        self.config["checks"][0]["command"] = ["{python}", "-c", "raise SystemExit(7)"]
        self.initialize()
        self.task("broken", max_attempts=2)
        self.task("blocked", depends_on=["broken"])
        result = self.run_tasks(cycles=3)
        by_id = {task["id"]: task for task in result["tasks"]}
        self.assertFalse(result["complete"])
        self.assertEqual(by_id["broken"]["status"], "failed", result)
        self.assertEqual(by_id["broken"]["attempts"], 2)
        self.assertEqual(by_id["broken"]["evidence"]["verdict"], "failed")
        self.assertEqual(by_id["blocked"]["status"], "pending")
        self.assertEqual(by_id["blocked"]["attempts"], 0)

    def test_repair_worker_receives_previous_check_failure(self):
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "from pathlib import Path; assert Path('artifact.txt').read_text() == 'repaired', 'please repair artifact'"]
        self.initialize("""import sys
from pathlib import Path
prompt = sys.stdin.read()
previous = 'Previous failure to investigate:' in prompt
Path('artifact.txt').write_text('repaired' if previous else 'broken')
""")
        self.task(max_attempts=2)
        result = self.run_tasks(cycles=2)
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["tasks"][0]["attempts"], 2)

    def test_repair_commit_handles_ignored_caches_and_literal_windows_filenames(self):
        filename = "module [draft] & data!.txt"
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "import sys; from pathlib import Path; "
            "cache = Path('__pycache__'); cache.mkdir(exist_ok=True); "
            "(cache / 'generated.pyc').write_bytes(b'cache'); "
            "state = Path('.nexo'); state.mkdir(exist_ok=True); "
            "(state / 'check-cache.txt').write_text('generated'); "
            "assert Path(sys.argv[1]).read_text() == 'repaired', 'repair the literal filename'",
            filename]
        self.initialize("import sys\nfrom pathlib import Path\n"
                        "prompt = sys.stdin.read()\n"
                        f"Path({filename!r}).write_text('repaired' if 'Previous failure to investigate:' in prompt else 'broken')\n")
        self.task(max_attempts=2)
        result = self.run_tasks(cycles=2)
        self.assertTrue(result["complete"], result)
        self.assertEqual(result["tasks"][0]["attempts"], 2)
        workspace = resolve_workspace(self.project, "work")
        self.assertEqual((workspace / filename).read_text(), "repaired")
        self.assertTrue((workspace / "__pycache__/generated.pyc").is_file())
        self.assertTrue((workspace / ".nexo/check-cache.txt").is_file())
        tracked = git(workspace, "ls-files", "-z").split("\0")
        self.assertIn(filename, tracked)
        self.assertFalse(any("__pycache__" in name or name.startswith(".nexo/") for name in tracked))

    def test_heartbeat_covers_slow_commit(self):
        self.config["runner"]["lease_seconds"] = 1
        self.initialize()
        self.task(max_attempts=1)
        from nexo_kit import runner
        original = runner.commit_workspace

        def slow_commit(*args, **kwargs):
            time.sleep(1.3)
            return original(*args, **kwargs)

        with patch("nexo_kit.runner.commit_workspace", side_effect=slow_commit):
            result = self.run_tasks()
        self.assertTrue(result["complete"], result)

    def test_heartbeat_covers_slow_preparation(self):
        self.config["runner"]["lease_seconds"] = 1
        self.initialize()
        self.task(max_attempts=1)
        from nexo_kit import runner
        original = runner.prepare_workspace

        def slow_prepare(*args, **kwargs):
            time.sleep(1.3)
            return original(*args, **kwargs)

        with patch("nexo_kit.runner.prepare_workspace", side_effect=slow_prepare):
            result = self.run_tasks()
        self.assertTrue(result["complete"], result)

    def test_stale_fingerprint_and_modified_logs_rejected(self):
        self.initialize()
        receipt = verify(self.project)
        self.assertEqual(receipt["verdict"], "passed")
        validate_evidence(self.project, receipt)
        original = (self.project / "worker.py").read_text()
        (self.project / "worker.py").write_text(original + "\n# changed after verification\n")
        with self.assertRaisesRegex(ValueError, "[Ss]tale"):
            validate_evidence(self.project, receipt)
        (self.project / "worker.py").write_text(original)
        log = self.project / receipt["checks"][0]["log"]
        log.write_text("forged check result")
        with self.assertRaisesRegex(ValueError, "log"):
            validate_evidence(self.project, receipt)

    def test_omitted_configured_check_is_not_valid_evidence(self):
        self.config["checks"].append({"name": "second-check", "command": ["{python}", "-c", "print('second')"]})
        self.initialize()
        receipt = verify(self.project)
        self.assertEqual(receipt["verdict"], "passed")
        forged = deepcopy(receipt)
        forged["checks"] = forged["checks"][:1]
        with self.assertRaises(ValueError):
            validate_evidence(self.project, forged)

    def test_no_checks_prevents_autonomous_run_and_successful_verification(self):
        self.config["checks"] = []
        self.initialize()
        self.task()
        receipt = verify(self.project)
        self.assertEqual(receipt["verdict"], "failed")
        with self.assertRaisesRegex(ValueError, "check"):
            self.run_tasks()

    def test_junit_zero_exit_with_failure_is_rejected_and_stale_report_is_rejected(self):
        self.config["checks"][0].update({"junit": ".nexo/junit.xml", "command": ["{python}", "-c",
            "from pathlib import Path; Path('.nexo/junit.xml').write_text('<testsuite><testcase name=\"bad\"><failure/></testcase></testsuite>')"]})
        self.initialize()
        report = verify(self.project)
        self.assertEqual(report["verdict"], "failed")
        self.assertEqual(report["checks"][0]["exit_code"], 0)
        self.assertEqual(report["checks"][0]["junit"]["failures"], 1)
        self.config["checks"][0]["command"] = ["{python}", "-c", "print('no fresh junit')"]
        write_json(self.project / ".nexo/config.json", self.config)
        stale = verify(self.project)
        self.assertEqual(stale["verdict"], "failed")
        self.assertIn("stale", stale["checks"][0]["error"])

    def test_mutating_check_cannot_verify_prior_content(self):
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "from pathlib import Path; Path('worker.py').write_text('changed by check')"]
        self.initialize()
        receipt = verify(self.project)
        self.assertEqual(receipt["verdict"], "failed")
        self.assertNotEqual(receipt["fingerprint"], receipt["fingerprint_after"])

    def test_changed_ignored_junit_artifact_invalidates_evidence(self):
        self.config["checks"][0].update({"junit": ".nexo/junit.xml", "command": ["{python}", "-c",
            "from pathlib import Path; Path('.nexo/junit.xml').write_text('<testsuite><testcase name=\"good\"/></testsuite>')"]})
        self.initialize()
        report = verify(self.project)
        self.assertEqual(report["verdict"], "passed")
        validate_evidence(self.project, report)
        (self.project / ".nexo/junit.xml").write_text("<testsuite><testcase><failure/></testcase></testsuite>")
        with self.assertRaises(ValueError):
            validate_evidence(self.project, report)

    def test_verified_branch_integrates_explicitly_and_rechecks_destination(self):
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "from pathlib import Path; assert Path('artifact.txt').read_text() == 'correct'"]
        self.initialize()
        self.task()
        result = self.run_tasks()
        self.assertTrue(result["complete"], result)
        integration = integrate(self.project, result["tasks"][0])
        self.assertTrue(integration["integrated"], integration)
        self.assertEqual(integration["verification"]["verdict"], "passed")
        self.assertEqual((self.project / "artifact.txt").read_text(), "correct")
        self.assertNotEqual(integration["before"], integration["after"])
        self.assertEqual(git(self.project, "status", "--porcelain"), "")

    def test_manual_commit_guard_repairs_and_integrates_current_content(self):
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "from pathlib import Path; assert Path('artifact.txt').read_text() == 'repaired'"]
        self.initialize("from pathlib import Path\nPath('artifact.txt').write_text('broken')\n")
        self.task(max_attempts=2)
        failed = self.run_tasks(cycles=1)
        self.assertFalse(failed["complete"])
        self.assertEqual(failed["tasks"][0]["status"], "failed")
        record_path = workspace_record(self.project, "work")
        failed_commit = read_json(record_path)["commit"]
        workspace = resolve_workspace(self.project, "work")
        with Store(self.project) as store:
            store.retry_task("work")
            claim = store.claim_task("work", "manual-repair", lease_seconds=3600)
            (workspace / "artifact.txt").write_text("repaired")
            store.finish_task("work", claim["lease_token"], "review")
        report = verify_task(self.project, "work", complete=True)
        self.assertEqual(report["verdict"], "passed", report)
        repaired_commit = read_json(record_path)["commit"]
        self.assertNotEqual(repaired_commit, failed_commit)
        self.assertEqual(repaired_commit, git(workspace, "rev-parse", "HEAD"))
        with Store(self.project) as store:
            repaired = store.get_task("work")
        self.assertEqual(repaired["status"], "done")
        integration = integrate(self.project, repaired)
        self.assertTrue(integration["integrated"], integration)
        self.assertEqual((self.project / "artifact.txt").read_text(), "repaired")

    def test_manual_commit_guard_rejects_stale_recorded_commit(self):
        self.initialize()
        self.task()
        result = self.run_tasks()
        self.assertTrue(result["complete"], result)
        task = result["tasks"][0]
        workspace = resolve_workspace(self.project, "work")
        recorded = read_json(workspace_record(self.project, "work"))["commit"]
        # An empty manual commit changes HEAD while keeping the verified content
        # identical, so only the record/HEAD invariant can detect this case.
        git(workspace, "commit", "--allow-empty", "-m", "Manual commit after verification")
        self.assertNotEqual(git(workspace, "rev-parse", "HEAD"), recorded)
        validate_evidence(self.project, task["evidence"], workspace)
        destination_before = git(self.project, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "stale|diverged"):
            integrate(self.project, task)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), destination_before)
        self.assertFalse((self.project / "artifact.txt").exists())

    def test_destination_failed_checks_revert_merge_without_resetting_history(self):
        self.config["checks"][0]["command"] = ["{python}", "-c",
            "from pathlib import Path; assert Path('.git').is_file(), 'fixture rejects main checkout'"]
        self.initialize()
        self.task()
        result = self.run_tasks()
        self.assertTrue(result["complete"], result)
        initial = git(self.project, "rev-parse", "HEAD")
        integration = integrate(self.project, result["tasks"][0])
        self.assertFalse(integration["integrated"])
        self.assertTrue(integration["reverted"], integration)
        self.assertFalse((self.project / "artifact.txt").exists())
        self.assertEqual(git(self.project, "diff", initial, "HEAD"), "")
        self.assertNotEqual(git(self.project, "rev-parse", "HEAD"), initial)

    def test_integration_preserves_uncommitted_user_work(self):
        self.initialize()
        self.task()
        result = self.run_tasks()
        self.assertTrue(result["complete"], result)
        (self.project / "user-work.txt").write_text("do not remove")
        before = git(self.project, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "clean"):
            integrate(self.project, result["tasks"][0])
        self.assertEqual((self.project / "user-work.txt").read_text(), "do not remove")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), before)

    def test_failed_dependency_preparation_cannot_be_reused_as_prepared(self):
        self.initialize()
        task = self.task("dependent", depends_on=[])
        task["depends_on"] = ["missing_workspace"]
        with self.assertRaises(ValueError):
            prepare_workspace(self.project, task)
        # A retained worktree must never silently bypass the missing prerequisite.
        with self.assertRaises(ValueError):
            prepare_workspace(self.project, task)

    def test_provider_error_envelope_is_failure_even_if_exit_zero(self):
        self.initialize("print('{\"type\":\"turn.failed\",\"error\":\"provider unavailable\"}')\n")
        self.task(max_attempts=1)
        result = self.run_tasks()
        self.assertFalse(result["complete"])
        self.assertEqual(result["tasks"][0]["status"], "failed")
        self.assertIn("provider unavailable", result["tasks"][0]["error"])


class JunitParsingTests(unittest.TestCase):
    def test_no_executed_tests_and_xml_entities_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "junit.xml"
            for content in ("<testsuite/>",
                            "<testsuite><testcase><skipped/></testcase></testsuite>",
                            '<!DOCTYPE testsuite [<!ENTITY x "bad">]><testsuite/>'):
                with self.subTest(content=content):
                    report.write_text(content)
                    with self.assertRaises(ValueError):
                        parse_junit(report)


class PathContainmentTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Extended-length Win32 paths")
    def test_concurrent_resolve_extended_prefix_does_not_reject_child(self):
        # Python 3.13 Windows realpath can retain the extended prefix if a
        # parent directory appears between resolution steps. Reproduce the
        # two observed forms without a nondeterministic timing test.
        root = Path("C:/nexo-test/worktrees")
        child = root / "worker"
        extended = Path("\\\\?\\C:\\nexo-test\\worktrees\\worker")

        def resolve(path, *args, **kwargs):
            return extended if path == child else path

        with patch.object(Path, "resolve", resolve):
            result = inside(root, "worker")
        self.assertTrue(result.is_relative_to(root))

    def test_parent_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                inside(Path(folder), "../outside")


if __name__ == "__main__":
    unittest.main()
