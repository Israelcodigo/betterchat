"""Audit completion is explicit; project setup preserves Windows user bytes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from nexo_kit.actions import initialize_project, verify_task
from nexo_kit.config import add_check
from nexo_kit.store import Store


class AuditWorkflowTests(unittest.TestCase):
    def test_state_initialization_preserves_gitignore_bytes(self):
        examples = [
            (b'# windows\r\ncache/  \r\n\r\n', b'.nexo/\r\n'),
            (b'\xef\xbb\xbf# windows\r\ncache/', b'\r\n.nexo/\r\n'),
            (b'cache/\n\n', b'.nexo/\n'),
            (b'cache/  ', b'\n.nexo/\n'),
            (b'', b'.nexo/\n'),
        ]
        for original, addition in examples:
            with self.subTest(original=original), tempfile.TemporaryDirectory() as folder:
                project = Path(folder)
                ignore = project / '.gitignore'
                ignore.write_bytes(original)
                initialize_project(project)
                self.assertEqual(ignore.read_bytes(), original + addition)
                initialize_project(project)
                self.assertEqual(ignore.read_bytes(), original + addition)

    def test_existing_bom_rule_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            content = b'\xef\xbb\xbf.nexo/\r\ncache/\r\n'
            (project / '.gitignore').write_bytes(content)
            initialize_project(project)
            self.assertEqual((project / '.gitignore').read_bytes(), content)

    def test_standalone_verification_warns_and_task_completion_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            initialize_project(project)
            (project / 'review.json').write_text(json.dumps({'finding': 'reproduced', 'limits': ['no production']}))
            add_check(project, 'audit-deliverable', [sys.executable, '-B', '-c',
                'import json; from pathlib import Path; r=json.loads(Path("review.json").read_text()); '
                'assert r["finding"]=="reproduced" and r["limits"]==["no production"]'])
            with Store(project) as store:
                for name in ('audit-code', 'audit-accessibility'):
                    store.add_task(name, task_id=name)
                    claim = store.claim_task(name, 'auditor', 120)
                    store.finish_task(name, claim['lease_token'], 'review')
            general = verify_task(project)
            self.assertEqual(general['verdict'], 'passed')
            self.assertFalse(general['completion']['performed'])
            self.assertEqual({r['id'] for r in general['completion']['pending_review']}, {'audit-code', 'audit-accessibility'})
            with Store(project) as store:
                self.assertEqual([t['status'] for t in store.list_tasks()], ['review', 'review'])
            scoped = verify_task(project, 'audit-code')
            self.assertEqual([r['id'] for r in scoped['completion']['pending_review']], ['audit-code'])
            completed = verify_task(project, 'audit-code', complete=True)
            self.assertEqual(completed['verdict'], 'passed')
            with Store(project) as store:
                self.assertEqual(store.get_task('audit-code')['status'], 'done')
                self.assertEqual(store.get_task('audit-accessibility')['status'], 'review')


if __name__ == '__main__':
    unittest.main()
