"""Learning gates execute real tiny processes in disposable projects/profiles."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from nexo_kit import learning
from nexo_kit.util import digest


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-learning-')
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.nexo').mkdir()
        (self.project / '.nexo/config.json').write_text('{}', encoding='utf-8')
        (self.project / 'result.txt').write_text('verified result', encoding='utf-8')
        self.home = self.root / 'Codex home'
        self.home.mkdir()
        self.source = self.project / 'authored/repeated-report'
        self.source.mkdir(parents=True)
        self.skill = self.source / 'SKILL.md'
        self.skill.write_text('---\nname: repeated-report\ndescription: Create validated reports from local task data.\n---\n\nRead the schema and validate the resulting report.\n', encoding='utf-8')
        (self.source / 'references').mkdir()
        (self.source / 'references/schema.txt').write_text('Report schema version 1', encoding='utf-8')
        self.check = [sys.executable, '-B', '-c', 'from pathlib import Path; assert Path("result.txt").read_text() == "verified result"; print("checked")']

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, request='r1', check=None, **kwargs):
        return learning.record(self.project, 'repeat-report', request, 'Create a report',
                               ['result.txt'], self.check if check is None else check, **kwargs)

    def qualify(self):
        for request in ('r1', 'r2', 'r3'):
            self.assertEqual(self.record(request)['status'], 'passed')

    def stage(self):
        self.qualify()
        return learning.stage(self.project, 'repeat-report', self.source)

    def validate(self, case='holdout1', check=None):
        return learning.validate(self.project, 'repeat-report', case, ['result.txt'], self.check if check is None else check)

    def install(self, **kwargs):
        return learning.install_learned(self.project, 'repeat-report', **kwargs)

    def test_read_does_not_create_state_and_mutation_requires_initialization(self):
        empty = self.root / 'empty'
        empty.mkdir()
        self.assertFalse(learning.patterns(empty)['initialized'])
        self.assertEqual(list(empty.iterdir()), [])
        with self.assertRaisesRegex(ValueError, 'Initialize'):
            learning.record(empty, 'report', 'r1', 'summary', ['missing'], self.check)
        self.assertEqual(list(empty.iterdir()), [])

    def test_three_distinct_requests_and_duplicate_does_not_execute(self):
        first = self.record()
        duplicate = self.record(check=[sys.executable, '-c', 'raise RuntimeError("must not execute")'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['log'], first['log'])
        self.assertEqual(duplicate['pattern']['consecutive_successes'], 1)
        self.record('r2')
        with self.assertRaisesRegex(ValueError, 'Three'):
            learning.stage(self.project, 'repeat-report', self.source)
        self.record('r3')
        staged = learning.stage(self.project, 'repeat-report', self.source)
        self.assertEqual(staged['files'], 2)
        self.assertEqual(learning.patterns(self.project)['patterns'][0]['consecutive_successes'], 3)

    def test_concurrent_duplicate_reserves_one_attempt_and_one_process(self):
        entered, release = threading.Event(), threading.Event()
        original_run = learning._run

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError('Concurrent test did not release check')
            return original_run(*args, **kwargs)

        with ThreadPoolExecutor(max_workers=1) as executor, patch('nexo_kit.learning._run', side_effect=delayed) as run:
            first = executor.submit(self.record)
            try:
                self.assertTrue(entered.wait(timeout=10))
                second = self.record()
                self.assertTrue(second['duplicate'])
                self.assertEqual(second['status'], 'running')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10)['status'], 'passed')
            self.assertEqual(run.call_count, 1)
        self.assertEqual(learning.patterns(self.project)['patterns'][0]['observations'], 1)

    def test_failure_resets_streak_without_erasing_history(self):
        self.qualify()
        failure = self.record('r4', [sys.executable, '-c', 'raise SystemExit(3)'])
        self.assertEqual(failure['exit_code'], 3)
        self.assertEqual(failure['status'], 'failed')
        self.assertFalse(failure['pattern']['eligible'])
        self.record('r5')
        self.assertEqual(learning.patterns(self.project)['patterns'][0]['consecutive_successes'], 1)
        self.assertEqual(learning.patterns(self.project)['patterns'][0]['observations'], 5)

    def test_holdout_is_new_and_install_needs_validation(self):
        self.stage()
        with self.assertRaisesRegex(ValueError, 'successful validation'):
            self.install()
        with self.assertRaisesRegex(ValueError, 'new case'):
            self.validate('r2')
        valid = self.validate()
        self.assertEqual(valid['status'], 'passed')
        with self.assertRaisesRegex(ValueError, 'already been used'):
            self.validate()
        installed = self.install()
        self.assertEqual(installed['scope'], 'project')
        self.assertTrue(Path(installed['path']).is_relative_to(self.project / '.agents/skills'))
        self.assertEqual(digest(Path(installed['path']) / 'references/schema.txt'), digest(self.source / 'references/schema.txt'))
        self.assertTrue(self.install()['unchanged'])

    def test_failed_holdout_cannot_be_ignored_for_install(self):
        self.stage()
        self.validate()
        failed = self.validate('holdout2', [sys.executable, '-c', 'raise SystemExit(1)'])
        self.assertEqual(failed['status'], 'failed')
        with self.assertRaisesRegex(ValueError, 'successful validation'):
            self.install()

    def test_changed_supporting_skill_file_invalidates_receipt(self):
        staged = self.stage()
        self.validate()
        (self.project / staged['path'] / 'references/schema.txt').write_text('edited', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Staged skill changed'):
            self.install()

    def test_changed_evidence_or_log_invalidates_receipt(self):
        self.stage()
        valid = self.validate()
        (self.project / 'result.txt').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.install()
        (self.project / 'result.txt').write_text('verified result', encoding='utf-8')
        (self.project / valid['log']).write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'log is missing or modified'):
            self.install()

    def test_later_failure_invalidates_even_after_three_new_successes(self):
        self.stage()
        self.validate()
        self.record('r4', [sys.executable, '-c', 'raise SystemExit(1)'])
        for request in ('r5', 'r6', 'r7'):
            self.record(request)
        with self.assertRaisesRegex(ValueError, 'invalidated'):
            self.install()
        learning.stage(self.project, 'repeat-report', self.source)
        self.validate('holdout2')
        self.assertEqual(self.install()['status'], 'installed')

    def test_personal_scope_is_explicit_and_foreign_skill_is_preserved(self):
        self.stage()
        self.validate()
        with self.assertRaisesRegex(ValueError, 'explicit Codex home'):
            self.install(scope='personal')
        destination = self.home / 'skills/repeated-report'
        destination.mkdir(parents=True)
        (destination / 'SKILL.md').write_text('Existing user skill', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'not installed by this project'):
            self.install(scope='personal', codex_home=self.home)
        self.assertEqual((destination / 'SKILL.md').read_text(), 'Existing user skill')
        other_home = self.root / 'other-profile'
        installed = self.install(scope='personal', codex_home=other_home)
        self.assertEqual(Path(installed['path']), other_home / 'skills/repeated-report')
        self.assertFalse((self.project / '.agents').exists())

    def test_update_backs_up_intact_owned_version(self):
        self.stage()
        self.validate()
        first = self.install(scope='personal', codex_home=self.home)
        previous = digest(Path(first['path']) / 'SKILL.md')
        self.skill.write_text(self.skill.read_text() + '\nUse the newer schema when present.\n', encoding='utf-8')
        learning.stage(self.project, 'repeat-report', self.source)
        with self.assertRaisesRegex(ValueError, 'successful validation'):
            self.install(scope='personal', codex_home=self.home)
        self.validate('holdout2')
        second = self.install(scope='personal', codex_home=self.home)
        self.assertEqual(digest(Path(second['backup']) / 'SKILL.md'), previous)
        self.assertNotEqual(digest(Path(second['path']) / 'SKILL.md'), previous)
        (Path(second['path']) / 'SKILL.md').write_text('User modified it', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed outside'):
            self.install(scope='personal', codex_home=self.home)

    def test_failed_backup_leaves_original_intact(self):
        self.stage()
        self.validate()
        first = self.install(scope='personal', codex_home=self.home)
        destination = Path(first['path'])
        previous = learning._tree(destination)
        self.skill.write_text(self.skill.read_text() + '\nNew instructions.\n', encoding='utf-8')
        learning.stage(self.project, 'repeat-report', self.source)
        self.validate('holdout2')
        original_copy = learning._copy

        def interrupted(source, target, snapshot):
            if '-installed-' in target.name:
                target.mkdir(parents=True)
                (target / 'partial.txt').write_text('partial backup')
                raise OSError('simulated backup failure')
            return original_copy(source, target, snapshot)

        with patch('nexo_kit.learning._copy', side_effect=interrupted), self.assertRaisesRegex(OSError, 'backup failure'):
            self.install(scope='personal', codex_home=self.home)
        self.assertEqual(learning._tree(destination), previous)

    def test_install_concurrent_destination_is_not_deleted(self):
        self.stage()
        self.validate()
        destination = self.home / 'skills/repeated-report'
        original_copy = learning._copy

        def race(source, target, snapshot):
            if target == destination:
                destination.mkdir(parents=True)
                (destination / 'SKILL.md').write_text('Concurrent foreign skill')
            return original_copy(source, target, snapshot)

        with patch('nexo_kit.learning._copy', side_effect=race), self.assertRaises(FileExistsError):
            self.install(scope='personal', codex_home=self.home)
        self.assertEqual((destination / 'SKILL.md').read_text(), 'Concurrent foreign skill')

    def test_stage_concurrent_destination_is_not_deleted(self):
        self.qualify()
        destination = self.project / '.nexo/learned-skills/repeated-report'
        original_copy = learning._copy

        def race(source, target, snapshot):
            if target == destination:
                destination.mkdir(parents=True)
                (destination / 'SKILL.md').write_text('Concurrent foreign candidate')
            return original_copy(source, target, snapshot)

        with patch('nexo_kit.learning._copy', side_effect=race), self.assertRaises(FileExistsError):
            learning.stage(self.project, 'repeat-report', self.source)
        self.assertEqual((destination / 'SKILL.md').read_text(), 'Concurrent foreign candidate')

    def test_traversal_and_links_are_rejected(self):
        outside = self.root / 'outside.txt'
        outside.write_text('outside', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'escapes'):
            learning.record(self.project, 'repeat-report', 'bad', 'summary', ['../outside.txt'], self.check)
        with self.assertRaises(ValueError):
            learning.stage(self.project, '../bad', self.source)
        link = self.project / 'linked.txt'
        try:
            link.symlink_to(outside)
        except OSError:
            # Windows without developer mode cannot create test symlinks.
            return
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            learning.record(self.project, 'repeat-report', 'linked', 'summary', ['linked.txt'], self.check)

    def test_invalid_metadata_empty_body_and_name_mismatch_are_rejected(self):
        self.qualify()
        for value in ('---\nname: other-name\ndescription: Good description\n---\nBody',
                      '---\nname: repeated-report\ndescription: |\n  multiline\n---\nBody',
                      '---\nname: repeated-report\nname: repeated-report\ndescription: Good\n---\nBody',
                      '---\nname: repeated-report\ndescription: Good\n---\n'):
            with self.subTest(value=value):
                self.skill.write_text(value, encoding='utf-8')
                with self.assertRaises(ValueError):
                    learning.stage(self.project, 'repeat-report', self.source)

    def test_quoted_frontmatter_and_optional_metadata_are_supported(self):
        self.skill.write_text('---\nname: "repeated-report"\ndescription: "Create reports: validate them."\nmetadata:\n  short-description: Reports\n---\nRead the schema.\n', encoding='utf-8')
        self.assertEqual(self.stage()['description'], 'Create reports: validate them.')

    def test_nontext_yaml_and_windows_device_names_are_rejected(self):
        self.qualify()
        for description in ('true', 'false', 'null', '123', '1.5', '0xFF', 'on', '2026-10-10'):
            with self.subTest(description=description):
                self.skill.write_text(f'---\nname: repeated-report\ndescription: {description}\n---\nBody.\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'non-text'):
                    learning.stage(self.project, 'repeat-report', self.source)
        for key in ('con', 'prn', 'aux', 'nul', 'com1', 'lpt9'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                learning.record(self.project, key, 'r1', 'summary', ['result.txt'], self.check)

    def test_timeout_and_command_limits_are_enforced(self):
        for timeout in (0, 601, True, float('nan')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.record(timeout=timeout)
        with self.assertRaises(ValueError):
            self.record(check='python something')
        receipt = self.record('slow', [sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.1)
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('timed out', receipt['error'])

    def test_logs_are_redacted_bounded_and_excess_output_fails(self):
        receipt = self.record('log', [sys.executable, '-c', 'print("password=secretvalue"); print("x" * 70000)'])
        log = self.project / receipt['log']
        self.assertEqual(receipt['status'], 'passed')
        self.assertTrue(receipt['output_truncated'])
        self.assertLessEqual(log.stat().st_size, learning.MAX_LOG_BYTES)
        self.assertNotIn('secretvalue', log.read_text())
        huge = self.record('huge', [sys.executable, '-c', 'print("x" * 2100000)'])
        self.assertEqual(huge['status'], 'failed')
        self.assertIn('exceeded', huge['error'])

    def test_check_modifying_evidence_cannot_record_success(self):
        receipt = self.record('mutating', [sys.executable, '-c', 'from pathlib import Path; Path("result.txt").write_text("modified")'])
        self.assertEqual(receipt['status'], 'failed')
        self.assertIn('changed', receipt['error'])
        self.assertEqual(receipt['changed_evidence'], ['result.txt'])
        self.assertIn('learn retry', receipt['error'])
        self.assertNotEqual(receipt['evidence'][0]['sha256'], receipt['evidence_after'][0]['sha256'])

    def retry(self, request='r1', check=None, **kwargs):
        return learning.retry(self.project, 'repeat-report', request, 'Corrected the failing check',
                              'Create a report', ['result.txt'], self.check if check is None else check, **kwargs)

    def test_retry_preserves_original_failure_and_counts_one_request(self):
        first = self.record(check=[sys.executable, '-c', 'raise SystemExit(7)'])
        original = learning.history(self.project, 'repeat-report', 'r1')
        failed_retry = self.retry(check=[sys.executable, '-c', 'raise SystemExit(8)'])
        repaired = self.retry()
        history = learning.history(self.project, 'repeat-report', 'r1')
        self.assertEqual(repaired['status'], 'passed')
        self.assertEqual(repaired['pattern']['observations'], 1)
        self.assertEqual(repaired['pattern']['consecutive_successes'], 1)
        self.assertEqual(history['observation_created'], original['observation_created'])
        self.assertEqual(history['runs'][0], original['runs'][0])
        self.assertEqual([row['run_number'] for row in history['runs']], [0, 1, 2])
        self.assertEqual([row['status'] for row in history['runs']], ['failed', 'failed', 'passed'])
        self.assertEqual(history['runs'][1]['receipt']['exit_code'], 8)
        self.assertEqual(history['runs'][2]['reason'], 'Corrected the failing check')
        self.assertEqual(len({row['receipt']['log'] for row in history['runs']}), 3)
        duplicate = self.record(check=[sys.executable, '-c', 'raise SystemExit(99)'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(duplicate['log'], repaired['log'])
        self.assertEqual(len(learning.history(self.project, 'repeat-report', 'r1')['runs']), 3)

    def test_retry_requires_failed_request_and_explicit_reason(self):
        with self.assertRaisesRegex(ValueError, 'Unknown request-id'):
            self.retry()
        self.record()
        with self.assertRaisesRegex(ValueError, 'Only failed'):
            self.retry()
        self.record('r2', [sys.executable, '-c', 'raise SystemExit(1)'])
        for reason in ('', ' ', None, 'x' * 2001):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, 'Reason'):
                learning.retry(self.project, 'repeat-report', 'r2', reason, 'summary', ['result.txt'], self.check)
        self.assertEqual(len(learning.history(self.project, 'repeat-report', 'r2')['runs']), 1)

    def test_retry_rejects_concurrent_original_and_retry_checks(self):
        entered, release = threading.Event(), threading.Event()
        original_run = learning._run

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError('Concurrent test did not release check')
            return original_run(*args, **kwargs)

        for retrying in (False, True):
            request = 'retrying' if retrying else 'original'
            if retrying:
                self.record(request, [sys.executable, '-c', 'raise SystemExit(1)'])
            entered.clear(); release.clear()
            with ThreadPoolExecutor(max_workers=1) as executor, patch('nexo_kit.learning._run', side_effect=delayed) as run:
                future = executor.submit(self.retry if retrying else self.record, request)
                try:
                    self.assertTrue(entered.wait(timeout=10))
                    with self.assertRaisesRegex(ValueError, 'running'):
                        self.retry(request)
                    duplicate = self.record(request)
                    self.assertTrue(duplicate['duplicate'])
                    self.assertEqual(duplicate['status'], 'running')
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10)['status'], 'passed')
                self.assertEqual(run.call_count, 1)

    def test_retry_old_request_does_not_rejuvenate_it_or_reorder_observations(self):
        with patch('nexo_kit.learning.time.time', return_value=1):
            first = self.record(check=[sys.executable, '-c', 'raise SystemExit(1)'])
        self.record('r2')
        self.record('r3')
        repaired = self.retry()
        self.assertEqual(repaired['observation_created'], 1)
        self.assertEqual(repaired['pattern']['consecutive_successes'], 2)
        self.assertFalse(repaired['pattern']['eligible'])
        self.assertGreater(repaired['created_at'], first['created_at'])

    def test_retry_does_not_resurrect_candidate_or_previous_validation(self):
        staged = self.stage()
        valid = self.validate()
        self.record('r4', [sys.executable, '-c', 'raise SystemExit(1)'])
        repaired = self.retry('r4')
        self.assertEqual(repaired['invalidated_candidate_generation'], staged['generation'])
        self.assertEqual(repaired['pattern']['candidate']['signature'], staged['signature'])
        self.assertIsNotNone(repaired['pattern']['candidate']['invalidation'])
        self.assertTrue(repaired['pattern']['eligible'])
        with self.assertRaisesRegex(ValueError, 'invalidated'):
            self.install()
        with self.assertRaisesRegex(ValueError, 'invalidated'):
            self.validate('cannot-reuse-stage')
        restaged = learning.stage(self.project, 'repeat-report', self.source)
        self.assertNotEqual(restaged['generation'], staged['generation'])
        with self.assertRaisesRegex(ValueError, 'successful validation'):
            self.install()
        self.validate('new-validation')
        self.assertEqual(self.install()['status'], 'installed')

    def test_retry_older_failed_observation_blocks_staging_while_running(self):
        self.record('old', [sys.executable, '-c', 'raise SystemExit(1)'])
        self.qualify()
        entered, release = threading.Event(), threading.Event()
        original_run = learning._run

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError('Concurrent test did not release check')
            return original_run(*args, **kwargs)

        with ThreadPoolExecutor(max_workers=1) as executor, patch('nexo_kit.learning._run', side_effect=delayed):
            future = executor.submit(self.retry, 'old')
            try:
                self.assertTrue(entered.wait(timeout=10))
                self.assertFalse(learning.patterns(self.project)['patterns'][0]['eligible'])
                with self.assertRaisesRegex(ValueError, 'Three'):
                    learning.stage(self.project, 'repeat-report', self.source)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10)['status'], 'passed')

    def test_legacy_database_history_is_read_only_and_retry_migrates_original(self):
        path = self.project / '.nexo/learning.sqlite3'
        original_receipt = {'key': 'repeat-report', 'request_id': 'legacy', 'status': 'failed', 'error': 'original failure'}
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE attempts (id INTEGER PRIMARY KEY,key TEXT NOT NULL,request_id TEXT NOT NULL,'
                       'created REAL NOT NULL,status TEXT NOT NULL,receipt TEXT NOT NULL,UNIQUE(key,request_id))')
            db.execute('INSERT INTO attempts VALUES(42,?,?,?,?,?)',
                       ('repeat-report', 'legacy', 1, 'failed', json.dumps(original_receipt)))
        db.close()
        before = path.read_bytes()
        history = learning.history(self.project, 'repeat-report', 'legacy')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(history['runs'][0]['receipt'], original_receipt)
        self.assertEqual(self.retry('legacy')['status'], 'passed')
        history = learning.history(self.project, 'repeat-report', 'legacy')
        self.assertEqual(history['observation_created'], 1)
        self.assertEqual([row['attempt_id'] for row in history['runs']], [42, 42])
        self.assertEqual(history['runs'][0]['receipt'], original_receipt)
        self.assertEqual(len(history['runs']), 2)
        self.record('other')  # Another migration pass must not replace the original receipt.
        self.assertEqual(learning.history(self.project, 'repeat-report', 'legacy')['runs'], history['runs'])

    def test_deleted_evidence_reports_path_and_remedy(self):
        receipt = self.record(check=[sys.executable, '-c', 'from pathlib import Path; Path("result.txt").unlink()'])
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['changed_evidence'], ['result.txt'])
        self.assertEqual(receipt['unavailable_evidence'][0]['path'], 'result.txt')
        self.assertIn('immutable', receipt['error'])

    def test_old_successes_expire_without_deleting_history(self):
        self.qualify()
        with patch('nexo_kit.learning.time.time', return_value=10**12):
            self.assertFalse(learning.patterns(self.project)['patterns'][0]['eligible'])

    def test_evidence_limits_missing_files_and_duplicate_paths(self):
        for evidence in ([], ['missing'], ['result.txt', 'result.txt'], ['result.txt'] * 129):
            with self.subTest(evidence=evidence[:2]), self.assertRaises(ValueError):
                learning.record(self.project, 'repeat-report', 'bad', 'summary', evidence, self.check)
        with patch('nexo_kit.learning.MAX_FILE_BYTES', 1), self.assertRaisesRegex(ValueError, 'size limit'):
            self.record()


if __name__ == '__main__':
    unittest.main()
