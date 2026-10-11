"""Behavioral tests for durable task ownership, verification gates and memory."""
import concurrent.futures
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from nexo_kit.store import KitError, Store


PASS = {"verdict": "passed", "fingerprint": "sha256:workspace",
        "checks": [{"name": "tests", "status": "passed"}]}


def _process_claim(project, owner, barrier, results):
    """Top-level spawn target, required on Windows."""
    try:
        with Store(project) as store:
            barrier.wait(timeout=10)
            try:
                task = store.claim_task("process_race", owner)
                results.put(("claimed", task["lease_owner"]))
            except KitError:
                results.put(("busy", owner))
    except Exception as exc:
        results.put(("error", repr(exc)))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.store = Store(self.project)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def finish(self, task_id):
        task = self.store.claim_task(task_id, "worker")
        self.store.finish_task(task_id, task["lease_token"], "review")
        return self.store.complete_task(task_id, PASS)

    def expire(self, task_id):
        # Deterministic simulated passage of time, without sleeps in the suite.
        conn = sqlite3.connect(self.store.db_path)
        try:
            with conn:
                conn.execute("UPDATE tasks SET lease_expires_at=0 WHERE id=?", (task_id,))
        finally:
            conn.close()

    def test_dependency_chain_unlocks_only_after_verified_completion(self):
        self.store.add_task("First", task_id="first")
        self.store.add_task("Second", task_id="second", depends_on=["first"])
        self.store.add_task("Third", task_id="third", depends_on=["second"])
        self.assertEqual([t["id"] for t in self.store.ready_tasks()], ["first"])
        with self.assertRaises(KitError):
            self.store.claim_task("second", "worker")
        first = self.store.claim_task("first", "worker")
        self.store.finish_task("first", first["lease_token"], "review")
        self.assertEqual(self.store.ready_tasks(), [])
        self.store.complete_task("first", PASS)
        self.assertEqual([t["id"] for t in self.store.ready_tasks()], ["second"])
        self.assertEqual(self.store.get_task("third")["status"], "pending")
        self.finish("second")
        self.assertEqual([t["id"] for t in self.store.ready_tasks()], ["third"])

    def test_cycles_missing_dependencies_and_invalid_ids_are_atomic(self):
        self.store.add_task("A", task_id="a")
        self.store.add_task("B", task_id="b", depends_on=["a"])
        self.store.add_task("C", task_id="c", depends_on=["b"])
        with self.assertRaisesRegex(KitError, "cycle"):
            self.store.add_dependency("a", "c")
        with self.assertRaises(KitError):
            self.store.add_dependency("a", "a")
        with self.assertRaises(KitError):
            self.store.add_task("bad", task_id="bad", depends_on=["missing"])
        with self.assertRaises(KitError):
            self.store.add_task("bad", task_id="../unsafe")
        self.assertEqual(len(self.store.list_tasks()), 3)
        self.assertEqual(self.store.get_task("a")["depends_on"], [])

    def test_two_connections_cannot_claim_same_task(self):
        self.store.add_task("Only one worker", task_id="race")
        barrier = threading.Barrier(2)

        def claim(owner):
            with Store(self.project) as other:
                barrier.wait(timeout=5)
                try:
                    return other.claim_task("race", owner)
                except KitError:
                    return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ["worker-a", "worker-b"]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.store.get_task("race")["attempts"], 1)

    def test_expired_worker_cannot_heartbeat_or_finish_reclaimed_task(self):
        self.store.add_task("Lease test", task_id="lease")
        first = self.store.claim_task("lease", "worker-a")
        self.expire("lease")
        with self.assertRaisesRegex(KitError, "expired"):
            self.store.heartbeat("lease", first["lease_token"])
        self.assertEqual(self.store.recover_expired(), ["lease"])
        second = self.store.claim_task("lease", "worker-b")
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaises(KitError):
            self.store.finish_task("lease", first["lease_token"], "review")
        with self.assertRaises(KitError):
            self.store.heartbeat("lease", first["lease_token"])
        self.assertEqual(self.store.get_task("lease")["lease_owner"], "worker-b")
        self.store.finish_task("lease", second["lease_token"], "review")

    def test_two_processes_cannot_claim_same_task(self):
        self.store.add_task("Process race", task_id="process_race")
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        results = ctx.Queue()
        processes = [ctx.Process(target=_process_claim,
                                 args=(str(self.project), owner, barrier, results))
                     for owner in ("process-a", "process-b")]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=15)
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=2), results.get(timeout=2)]
            self.assertEqual(sorted(item[0] for item in outcomes), ["busy", "claimed"])
            self.assertEqual(self.store.get_task("process_race")["attempts"], 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                process.close()
            results.close()
            results.join_thread()

    def test_heartbeat_extends_lease_and_rejects_bad_duration(self):
        self.store.add_task("Heartbeat", task_id="hb")
        task = self.store.claim_task("hb", "worker", lease_seconds=10)
        renewed = self.store.heartbeat("hb", task["lease_token"], lease_seconds=120)
        self.assertGreater(renewed["lease_expires_at"], task["lease_expires_at"])
        self.assertGreater(renewed["version"], task["version"])
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaises(KitError):
                self.store.heartbeat("hb", task["lease_token"], value)
        with self.assertRaises(KitError):
            self.store.heartbeat("hb", "invalid-token-\N{LATIN SMALL LETTER E WITH ACUTE}")

    def test_attempts_are_bounded_including_expired_attempts(self):
        self.store.add_task("Retry", task_id="retry", max_attempts=2)
        first = self.store.claim_task("retry", "worker")
        self.store.finish_task("retry", first["lease_token"], "failed", error="test failed")
        self.store.retry_task("retry")
        self.store.claim_task("retry", "worker")
        self.expire("retry")
        self.store.recover_expired()
        self.assertEqual(self.store.get_task("retry")["status"], "failed")
        self.assertEqual(self.store.get_task("retry")["attempts"], 2)
        with self.assertRaisesRegex(KitError, "exhausted"):
            self.store.retry_task("retry")
        self.assertEqual(self.store.ready_tasks(), [])

    def test_completion_requires_review_and_evidence(self):
        self.store.add_task("Verify", task_id="verify")
        with self.assertRaises(KitError):
            self.store.complete_task("verify", PASS)
        task = self.store.claim_task("verify", "worker")
        with self.assertRaises(KitError):
            self.store.finish_task("verify", task["lease_token"], "done")
        self.store.finish_task("verify", task["lease_token"], "review", evidence={"claim": "done"})
        invalid = [None, {}, {"verdict": "passed"},
                   {"verdict": "passed", "checks": [], "fingerprint": "abc"},
                   {"verdict": "passed", "checks": [{"status": "failed"}], "fingerprint": "abc"},
                   {"verdict": "passed", "checks": [{"status": "passed"}], "fingerprint": ""}]
        for evidence in invalid:
            with self.subTest(evidence=evidence), self.assertRaises(KitError):
                self.store.complete_task("verify", evidence)
        done = self.store.complete_task("verify", PASS)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["evidence"], PASS)

    def test_blocked_task_requires_explicit_retry(self):
        self.store.add_task("Blocked", task_id="blocked")
        task = self.store.claim_task("blocked", "worker")
        self.store.finish_task("blocked", task["lease_token"], "blocked", error="missing tool")
        self.assertEqual(self.store.ready_tasks(), [])
        self.store.retry_task("blocked")
        self.assertEqual([t["id"] for t in self.store.ready_tasks()], ["blocked"])

    def test_rejected_review_can_retry_without_unlocking_dependents(self):
        self.store.add_task("Rejected", task_id="rejected")
        self.store.add_task("Dependent", task_id="dependent", depends_on=["rejected"])
        with self.assertRaises(KitError):
            self.store.reject_review("rejected", "not reviewed")
        claimed = self.store.claim_task("rejected", "worker")
        reviewed = self.store.finish_task("rejected", claimed["lease_token"], "review")
        evidence = {"verdict": "failed", "checks": [{"status": "failed"}]}
        rejected = self.store.reject_review("rejected", "Unit tests failed", evidence)
        self.assertEqual(rejected["status"], "failed")
        self.assertEqual(rejected["evidence"], evidence)
        self.assertEqual(rejected["error"], "Unit tests failed")
        self.assertGreater(rejected["version"], reviewed["version"])
        self.assertEqual(self.store.ready_tasks(), [])
        self.store.retry_task("rejected")
        self.assertEqual([t["id"] for t in self.store.ready_tasks()], ["rejected"])
        reclaimed = self.store.claim_task("rejected", "repair-worker")
        self.assertEqual(reclaimed["error"], "Unit tests failed")

    def test_reopen_keeps_state_and_audit(self):
        self.store.add_task("Persist", task_id="persist")
        memory = self.store.add_memory("Use a virtual environment", source="review")
        self.store.approve_memory(memory["id"])
        self.finish("persist")
        self.store.close()
        self.store = Store(self.project)
        self.assertEqual(self.store.get_task("persist")["status"], "done")
        self.assertEqual(self.store.search_memory("virtual")[0]["id"], memory["id"])
        snapshot = self.store.export_state()
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertTrue(any(event["kind"] == "task.verified" for event in snapshot["events"]))
        json.dumps(snapshot)

    def test_memory_candidates_do_not_become_rules_from_repetition(self):
        first = self.store.add_memory("Prefer small functions", confidence=0.6)
        repeated = self.store.add_memory("Prefer   small functions", confidence=0.9, status="active")
        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(repeated["status"], "candidate")
        self.assertEqual(repeated["confidence"], 0.6)
        self.assertEqual(self.store.search_memory("functions"), [])
        self.assertEqual(self.store.search_memory("functions", include_candidates=True)[0]["id"], first["id"])
        self.store.approve_memory(first["id"])
        self.assertEqual(self.store.search_memory("functions")[0]["id"], first["id"])
        self.store.update_memory(first["id"], confidence=0.8, status="archived")
        self.assertEqual(self.store.search_memory("functions", include_candidates=True), [])

    def test_memory_rank_and_fts_query_escaping(self):
        exact = self.store.add_memory("sqlite lease sqlite lease", status="active")
        self.store.add_memory("a long unrelated note that mentions sqlite once among many words", status="active")
        self.store.add_memory("everything about colors", status="active")
        results = self.store.search_memory('sqlite OR lease " ) *')
        self.assertEqual(results[0]["id"], exact["id"])
        self.assertEqual(len(results), 2)
        self.assertEqual(self.store.search_memory('"*()'), [])

    def test_memory_search_fallback_and_literal_underscore(self):
        wanted = self.store.add_memory("worker_lease renewal", status="active")
        self.store.add_memory("workerXlease incorrect", status="active")
        self.store.fts_enabled = False
        results = self.store.search_memory("worker_lease")
        self.assertEqual([item["id"] for item in results], [wanted["id"]])

    def test_memory_graph_has_direction_and_filters_candidates(self):
        a = self.store.add_memory("Decision A", status="active")
        b = self.store.add_memory("Evidence B", status="active")
        c = self.store.add_memory("Unreviewed C")
        self.store.link_memory(a["id"], b["id"], "supported_by")
        self.store.link_memory(a["id"], c["id"], "related")
        outgoing = self.store.related_memories(a["id"])
        self.assertEqual(len(outgoing), 1)
        self.assertEqual(outgoing[0]["direction"], "outgoing")
        self.assertEqual(outgoing[0]["relation"], "supported_by")
        self.assertEqual(self.store.related_memories(b["id"])[0]["direction"], "incoming")
        self.assertEqual(len(self.store.related_memories(a["id"], include_candidates=True)), 2)
        with self.assertRaises(KitError):
            self.store.link_memory(a["id"], "missing", "bad")
        with self.assertRaises(KitError):
            self.store.link_memory(a["id"], a["id"], "self")

    def test_schema_constraints_and_validation(self):
        for kwargs in ({"title": ""}, {"title": "x", "max_attempts": 0},
                       {"title": "x", "max_attempts": True}, {"title": "x", "depends_on": "abc"}):
            with self.assertRaises(KitError):
                self.store.add_task(**kwargs)
        for confidence in (-0.1, 1.1, float("nan")):
            with self.assertRaises(KitError):
                self.store.add_memory("invalid", confidence=confidence)
        with self.assertRaises(KitError):
            self.store.add_event("", {})
        with self.assertRaises(KitError):
            self.store.add_event("test", {"bad": float("nan")})
        self.assertEqual(self.store.events(), [])


if __name__ == "__main__":
    unittest.main()
