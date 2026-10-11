from pathlib import Path
import tempfile
import unittest

from nexo_kit.context import ContextIndex, checkpoint, export_checkpoint, promote_to_skill
from nexo_kit.store import Store


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nexo-context-ñ-")
        self.project = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_redacts_before_storage_and_bounds_multibyte_text(self):
        with ContextIndex(self.project) as index:
            result = index.capture("password=do-not-store\n" + "á" * 5000,
                                   source="api_key=label-secret", max_bytes=2001)
            self.assertTrue(result["truncated"])
            self.assertLessEqual(result["bytes"], 2001)
            self.assertLessEqual(len(result["preview"]), 2000)
            self.assertNotIn("do-not-store", result["preview"])
            self.assertNotIn("label-secret", result["source"])
            stored = (self.project / ".nexo/context" / (result["id"] + ".txt")).read_text(encoding="utf-8")
            self.assertNotIn("do-not-store", stored)
            self.assertEqual(len(stored.encode("utf-8")), result["bytes"])

    def test_search_fetch_and_reopen_persist(self):
        with ContextIndex(self.project) as index:
            item = index.capture("alpha beta needle " * 1000, source="unit-test")
            hits = index.search("needle", limit=20, max_chars=57)
            self.assertTrue(hits)
            self.assertLessEqual(sum(len(h["snippet"]) for h in hits), 57)
            self.assertEqual(hits[0]["id"], item["id"])
            self.assertEqual(index.search('" OR * \"'), [])
            fetched = index.fetch(item["id"], offset=5, limit=8)
            self.assertEqual(fetched["text"], ("alpha beta needle " * 1000)[5:13])
            self.assertTrue(fetched["has_more"])
        with ContextIndex(self.project) as index:
            self.assertEqual(index.fetch(item["id"], limit=6)["text"], "alpha ")

    def test_fallback_then_fts_backfill(self):
        with ContextIndex(self.project, force_fallback=True) as index:
            self.assertFalse(index.fts_enabled)
            item = index.capture("special_under exact token")
            self.assertEqual(index.search("special_under")[0]["id"], item["id"])
            self.assertEqual(index.search("absentterm"), [])
        with ContextIndex(self.project) as index:
            self.assertEqual(index.search("exact")[0]["id"], item["id"])

    def test_fetch_rejects_traversal_and_limits(self):
        with ContextIndex(self.project) as index:
            item = index.capture("x" * 100000)
            self.assertEqual(len(index.fetch(item["id"], limit=999999)["text"]), 32000)
            for invalid in ("../state.sqlite3", "/etc/passwd", "ctx_" + "f" * 32 + ".txt"):
                with self.assertRaises(ValueError):
                    index.fetch(invalid)
            with self.assertRaises(ValueError):
                index.fetch(item["id"], offset=-1)
            with self.assertRaises(ValueError):
                index.capture("anything", max_bytes=False)

    def test_checkpoint_omits_candidates_and_event_payload_content(self):
        with Store(self.project) as store:
            task = store.add_task("Inspect parser")
            store.add_memory("never-inject-candidate", kind="correction", source="test")
            approved = store.add_memory("active-finding password=hidden", kind="fact", source="evidence-file")
            store.approve_memory(approved["id"])
            result = checkpoint(store)
            self.assertIn(task["id"], result)
            self.assertIn("active-finding", result)
            self.assertNotIn("never-inject-candidate", result)
            self.assertNotIn("password=hidden", result)
            self.assertIn("[REDACTED]", result)
            self.assertLessEqual(len(checkpoint(store, max_chars=87)), 87)
            paths = export_checkpoint(self.project, store)
            self.assertEqual(set(paths), {"plan", "findings", "progress", "checkpoint"})
            for path in paths.values():
                self.assertTrue(Path(path).is_file())
                self.assertNotIn("never-inject-candidate", Path(path).read_text(encoding="utf-8"))

    def test_learning_requires_activation_and_stages_without_installing(self):
        with Store(self.project) as store:
            candidate = store.add_memory("Quote this: \"safe\"\nDo not blindly execute", kind="correction", source="tests/parser.py")
            with self.assertRaises(ValueError):
                promote_to_skill(self.project, store, candidate["id"], "parser-lesson")
            store.approve_memory(candidate["id"])
            for bad in ("../escape", "con", "CON", "a/b", "a--b", "x" * 65):
                with self.assertRaises(ValueError):
                    promote_to_skill(self.project, store, candidate["id"], bad)
            path = promote_to_skill(self.project, store, candidate["id"], "parser-lesson")
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith('---\nname: "parser-lesson"\n'))
            self.assertIn(candidate["id"], text)
            self.assertIn('> Quote this: "safe"', text)
            self.assertIn("staged candidate", text)
            self.assertFalse((self.project / ".agents/skills/parser-lesson").exists())
            self.assertFalse((self.project / ".claude/skills/parser-lesson").exists())
            with self.assertRaises(FileExistsError):
                promote_to_skill(self.project, store, candidate["id"], "parser-lesson")

    def test_learning_requires_evidence_and_project_match(self):
        with Store(self.project) as store:
            memory = store.add_memory("Needs a source", kind="fact", status="active")
            with self.assertRaises(ValueError):
                promote_to_skill(self.project, store, memory["id"], "no-evidence")
            with self.assertRaises(ValueError):
                export_checkpoint(self.project / "elsewhere", store)

    def test_store_and_context_share_database_without_schema_collisions(self):
        with Store(self.project) as store, ContextIndex(self.project) as index:
            task = store.add_task("Shared SQLite")
            artifact = index.capture("retained context")
            self.assertEqual(store.list_tasks()[0]["id"], task["id"])
            self.assertEqual(index.search("retained")[0]["id"], artifact["id"])


if __name__ == "__main__":
    unittest.main()
