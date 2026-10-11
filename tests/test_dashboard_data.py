"""Dashboard reads disposable profiles and explicit projects without mutation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nexo_kit import dashboard_data as dashboard
from nexo_kit import learning, __version__


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='nexo-dashboard-data-')
        self.root = Path(self.tmp.name)
        self.home = self.root / 'Codex home'
        self.project = self.root / 'First project'
        self.home.mkdir()
        (self.project / '.nexo').mkdir(parents=True)
        (self.project / '.nexo/config.json').write_text('{}', encoding='utf-8')
        self.now = time.time()
        self.connections = []

    def tearDown(self):
        for db in self.connections:
            db.close()
        self.tmp.cleanup()

    def connect(self, filename):
        db = sqlite3.connect(self.project / '.nexo' / filename)
        self.connections.append(db)
        return db

    def ledger(self):
        db = self.connect('learning.sqlite3')
        db.executescript('''
            CREATE TABLE attempts(id INTEGER PRIMARY KEY,key TEXT,request_id TEXT,created REAL,status TEXT,receipt TEXT);
            CREATE TABLE candidates(key TEXT PRIMARY KEY,name TEXT,path TEXT,signature TEXT,sequence INTEGER,generation TEXT);
            CREATE TABLE validations(id INTEGER PRIMARY KEY,key TEXT,case_id TEXT,created REAL,generation TEXT,receipt TEXT);
            CREATE TABLE installations(path TEXT PRIMARY KEY,key TEXT,signature TEXT);
        ''')
        return db

    def skill(self, folder, name='example'):
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: Read explicit task evidence.\n---\n\nFull instructions.\n', encoding='utf-8')
        return learning._signature(learning._tree(folder))

    def attempts(self, db, key='repeat', statuses=('passed', 'passed', 'passed')):
        for index, status in enumerate(statuses):
            db.execute('INSERT INTO attempts(key,request_id,created,status,receipt) VALUES(?,?,?,?,?)',
                       (key, f'request{index}', self.now, status, '{}'))

    def candidate(self, db):
        folder = self.project / '.nexo/learned-skills/example'
        signature = self.skill(folder)
        db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',
                   ('repeat', 'example', '.nexo/learned-skills/example', signature, 3, 'generation-a'))
        return signature

    def validation(self, db, signature, generation='generation-a', created=None, status='passed'):
        created = self.now if created is None else created
        receipt = {'status': status, 'created_at': created, 'generation': generation,
                   'skill_sha256': signature, 'command': ['SECRET-MUST-NOT-RETURN'],
                   'log': '../private.txt', 'evidence': [{'path': '../private.txt'}]}
        db.execute('INSERT INTO validations(key,case_id,created,generation,receipt) VALUES(?,?,?,?,?)',
                   ('repeat', 'fresh-case', created, generation, json.dumps(receipt)))

    def read(self, projects=None):
        with patch.object(dashboard.time, 'time', return_value=self.now):
            return dashboard.snapshot(self.home, projects or [self.project])

    def test_empty_projects_remain_visible_without_creating_anything(self):
        empty, nonexistent = self.root / 'empty', self.root / 'missing'
        empty.mkdir()
        state = self.read([empty, nonexistent, self.project])
        self.assertEqual(len(state['projects']), 3)
        self.assertEqual([p['initialized'] for p in state['projects']], [False, False, True])
        self.assertEqual(state['projects'][0]['errors'], [])
        self.assertEqual(state['projects'][1]['path'], str(nonexistent))
        self.assertTrue(any('no existe' in error for error in state['projects'][1]['errors']))
        self.assertEqual(state['projects'][1]['tasks'], [])
        self.assertEqual(state['projects'][1]['learning']['patterns'], [])
        self.assertEqual(list(empty.iterdir()), [])
        self.assertFalse(nonexistent.exists())
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(state['version'], __version__)
        self.assertEqual(state['schema_version'], 1)
        self.assertEqual(state['generated_at'], self.now)

    def test_inaccessible_project_retains_its_card_without_hiding_other_projects(self):
        accessible = self.root / 'accessible project'
        accessible.mkdir()
        actual_scandir = dashboard.os.scandir
        def scandir(path):
            if Path(path) == self.project:
                raise PermissionError('Access denied by the project directory')
            return actual_scandir(path)
        before = (self.project / '.nexo/config.json').read_bytes()
        with patch.object(dashboard.os, 'scandir', side_effect=scandir):
            state = self.read([self.project, accessible])
        denied, available = state['projects']
        self.assertEqual(denied['id'], str(self.project))
        self.assertFalse(denied['initialized'])
        self.assertTrue(any('No se puede acceder' in error for error in denied['errors']))
        self.assertEqual(denied['tasks'], [])
        self.assertEqual(denied['learning']['patterns'], [])
        self.assertEqual(available['path'], str(accessible))
        self.assertEqual(available['errors'], [])
        self.assertEqual((self.project / '.nexo/config.json').read_bytes(), before)
        self.assertEqual(list(accessible.iterdir()), [])

    def test_core_assets_and_preferences_are_allowlisted(self):
        self.skill(self.home / 'skills/nexo-build', 'nexo-build')
        self.skill(self.home / 'skills/unrelated-private')
        agents = self.home / 'agents'
        agents.mkdir()
        (agents / 'nexo-reviewer.toml').write_text(
            'name="nexo-reviewer"\ndescription="Review tasks"\ndeveloper_instructions="Read the complete diff."', encoding='utf-8')
        (self.home / 'auth.json').write_text('DO-NOT-READ', encoding='utf-8')
        (self.home / 'config.toml').write_text('DO-NOT-READ', encoding='utf-8')
        (self.home / 'nexo-kit').mkdir()
        (self.home / 'nexo-kit/preferences.json').write_text(
            json.dumps({'auto_learn': True, 'threshold': 3, 'secret': 'DO-NOT-READ'}), encoding='utf-8')
        state = self.read()
        self.assertEqual(len(state['skills']), 1)
        self.assertEqual(state['skills'][0]['origin'], 'core')
        self.assertIn('Full instructions.', state['skills'][0]['instructions'])
        self.assertEqual(state['agents'][0]['instructions'], 'Read the complete diff.')
        self.assertTrue(state['preferences']['auto_learn'])
        self.assertNotIn('DO-NOT-READ', json.dumps(state))

    def test_installed_extensions_and_same_name_project_roles_are_discovered(self):
        name = 'nexo-web-accesibilidad'
        personal = self.home / 'agents' / (name + '.toml')
        local = self.project / '.codex/agents' / (name + '.toml')
        for path, instructions in ((personal, 'Personal instructions'), (local, 'Project instructions')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'name="{name}"\ndescription="Accessible interfaces"\ndeveloper_instructions="{instructions}"', encoding='utf-8')
        self.skill(self.home / 'skills/nexo-web-audit', 'nexo-web-audit')
        self.skill(self.project / '.agents/skills/nexo-web-audit', 'nexo-web-audit')
        self.skill(self.project / '.claude/skills/nexo-private', 'nexo-private')
        state = self.read()
        self.assertEqual(len(state['agents']), 2)
        self.assertEqual({row['scope'] for row in state['agents']}, {'personal', 'project'})
        self.assertEqual({row['instructions'] for row in state['agents']}, {'Personal instructions', 'Project instructions'})
        self.assertEqual(len({row['id'] for row in state['agents']}), 2)
        self.assertEqual(next(row for row in state['agents'] if row['scope'] == 'project')['project_id'], str(self.project))
        self.assertTrue(all(row['origin'] == 'installed' for row in state['agents'] + state['skills']))
        self.assertEqual({row['name'] for row in state['skills']}, {'nexo-web-audit'})
        self.assertEqual(len(state['skills']), 2)

    def test_malformed_oversized_or_linked_extensions_do_not_hide_valid_roles(self):
        agents = self.home / 'agents'
        agents.mkdir()
        def role(name, text):
            (agents / (name + '.toml')).write_text(text, encoding='utf-8')
        role('nexo-good', 'name="nexo-good"\ndeveloper_instructions="Good role"')
        role('nexo-malformed', 'developer_instructions = [')
        role('nexo-wrong-type', 'developer_instructions = 42')
        role('nexo-missing-name', 'developer_instructions="Unnamed role"')
        role('nexo-renamed', 'name="not-nexo"\ndeveloper_instructions="Private"')
        role('nexo-huge', '#' * (dashboard.DOCUMENT_BYTES + 1))
        role('nexo-linked', 'developer_instructions="Must not be read"')
        original = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda path: path == agents / 'nexo-linked.toml' or original(path)):
            state = self.read()
        self.assertEqual([row['name'] for row in state['agents']], ['nexo-good'])
        self.assertEqual(len(state['warnings']), 6)
        self.assertTrue(any('nexo-missing-name' in warning and 'name does not match' in warning for warning in state['warnings']))
        self.assertNotIn('Must not be read', json.dumps(state))

    def test_asset_directory_listing_is_bounded(self):
        agents = self.home / 'agents'
        agents.mkdir()
        for name in ('nexo-one', 'nexo-two', 'nexo-three'):
            (agents / (name + '.toml')).write_text(f'name="{name}"\ndeveloper_instructions="Instructions"', encoding='utf-8')
        with patch.object(dashboard, 'LIST_LIMIT', 2):
            state = self.read()
        self.assertEqual(len(state['agents']), 2)
        self.assertTrue(any('directory entries' in item for item in state['warnings']))

    def test_streak_matches_learning_with_failure_and_expiry(self):
        with self.ledger() as db:
            self.attempts(db, 'recent', ('passed', 'failed', 'passed', 'passed', 'passed'))
            self.attempts(db, 'old', ('passed', 'passed', 'passed'))
            db.execute('UPDATE attempts SET created=? WHERE key=? AND request_id=?',
                       (self.now - dashboard.MAX_AGE_SECONDS - 1, 'old', 'request1'))
            self.attempts(db, 'unfinished', ('passed', 'passed', 'running'))
        expected = learning.patterns(self.project)['patterns']
        actual = self.read()['projects'][0]['learning']['patterns']
        fields = ('key', 'observations', 'consecutive_successes', 'eligible', 'last_status')
        self.assertEqual([{k: p[k] for k in fields} for p in actual],
                         [{k: p[k] for k in fields} for p in expected])

    def test_staged_validated_and_installed_are_distinct_and_hashes_are_checked(self):
        installed = self.project / '.agents/skills/example'
        with self.ledger() as db:
            self.attempts(db)
            signature = self.candidate(db)
            self.validation(db, signature)
            self.skill(installed)
            db.execute('INSERT INTO installations VALUES(?,?,?)', (str(installed), 'repeat', signature))
        state = self.read()
        pattern = state['projects'][0]['learning']['patterns'][0]
        self.assertTrue(pattern['eligible'])
        self.assertTrue(pattern['candidate']['current'])
        self.assertTrue(pattern['validation']['current'])
        self.assertFalse(pattern['validation']['evidence_verified'])
        self.assertTrue(pattern['installations'][0]['verified_installation'])
        self.assertEqual({s['origin'] for s in state['skills']}, {'candidate', 'learned'})
        self.assertNotIn('SECRET-MUST-NOT-RETURN', json.dumps(state))
        (installed / 'SKILL.md').write_text('Changed skill', encoding='utf-8')
        state = self.read()
        installation = state['projects'][0]['learning']['installations'][0]
        self.assertTrue(installation['ledger_recorded'])
        self.assertTrue(installation['exists'])
        self.assertFalse(installation['verified_installation'])

        (installed / 'SKILL.md').unlink()
        installation = self.read()['projects'][0]['learning']['installations'][0]
        self.assertFalse(installation['exists'])
        self.assertFalse(installation['verified_installation'])

    def test_retried_request_counts_once_and_invalidates_its_existing_generation(self):
        with self.ledger() as db:
            self.attempts(db)
            signature = self.candidate(db)
            self.validation(db, signature)
            db.executescript('''CREATE TABLE attempt_runs(attempt_id INTEGER,run_number INTEGER);
                CREATE TABLE candidate_invalidations(key TEXT,generation TEXT);''')
            db.execute('INSERT INTO attempt_runs VALUES(1,0)')
            db.execute('INSERT INTO attempt_runs VALUES(1,1)')
            db.execute('INSERT INTO candidate_invalidations VALUES(?,?)', ('repeat', 'generation-a'))
        pattern = self.read()['projects'][0]['learning']['patterns'][0]
        self.assertEqual(pattern['observations'], 3)
        self.assertEqual(pattern['retry_count'], 1)
        self.assertEqual(pattern['consecutive_successes'], 3)
        self.assertTrue(pattern['candidate']['invalidated_by_retry'])
        self.assertFalse(pattern['candidate']['current'])
        self.assertFalse(pattern['validation']['current'])
        with self.connect('learning.sqlite3') as db:
            db.execute("UPDATE candidates SET generation='generation-b'")
            self.validation(db, signature, generation='generation-b')
        pattern = self.read()['projects'][0]['learning']['patterns'][0]
        self.assertTrue(pattern['candidate']['current'])
        self.assertTrue(pattern['validation']['current'])

    def test_running_older_request_prevents_eligibility_despite_three_later_successes(self):
        with self.ledger() as db:
            self.attempts(db, statuses=('running', 'passed', 'passed', 'passed'))
        pattern = self.read()['projects'][0]['learning']['patterns'][0]
        self.assertEqual(pattern['consecutive_successes'], 3)
        self.assertEqual(pattern['running_observations'], 1)
        self.assertFalse(pattern['eligible'])

    def test_generation_expiry_and_post_stage_failure_invalidate_current_validation(self):
        with self.ledger() as db:
            self.attempts(db)
            signature = self.candidate(db)
            self.validation(db, signature, generation='old-generation')
        pattern = self.read()['projects'][0]['learning']['patterns'][0]
        self.assertFalse(pattern['validation']['same_generation'])
        self.assertFalse(pattern['validation']['current'])
        with self.connect('learning.sqlite3') as db:
            self.validation(db, signature, created=self.now - dashboard.MAX_AGE_SECONDS - 1)
        self.assertFalse(self.read()['projects'][0]['learning']['patterns'][0]['validation']['fresh'])
        with self.connect('learning.sqlite3') as db:
            self.validation(db, signature)
            self.attempts(db, statuses=('failed', 'passed', 'passed', 'passed'))
        pattern = self.read()['projects'][0]['learning']['patterns'][0]
        self.assertTrue(pattern['eligible'])
        self.assertFalse(pattern['candidate']['current'])
        self.assertFalse(pattern['validation']['current'])

    def test_corrupt_ledger_and_malformed_receipt_do_not_break_other_cards(self):
        with self.ledger() as db:
            self.attempts(db)
            db.execute('INSERT INTO validations VALUES(1,?,?,?,?,?)', ('repeat', 'case', self.now, 'g', '{bad json'))
        corrupt = self.root / 'corrupt'
        (corrupt / '.nexo').mkdir(parents=True)
        (corrupt / '.nexo/learning.sqlite3').write_bytes(b'not sqlite')
        state = self.read([corrupt, self.project])
        self.assertTrue(state['projects'][0]['errors'])
        self.assertTrue(state['projects'][1]['errors'])
        self.assertEqual(state['projects'][1]['learning']['patterns'][0]['observations'], 3)

    def test_reads_use_mode_ro_and_leave_bytes_and_mtimes_unchanged(self):
        with self.ledger() as db:
            self.attempts(db)
        with self.connect('state.sqlite3') as db:
            db.execute('CREATE TABLE tasks(id TEXT,title TEXT,status TEXT,lease_owner TEXT,lease_expires_at REAL)')
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?)', ('task1', 'Actual task', 'running', 'worker1', self.now + 30))
        def fingerprint():
            return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                    for p in self.root.rglob('*') if p.is_file()}
        before = fingerprint()
        real_connect = sqlite3.connect
        with patch.object(dashboard.sqlite3, 'connect', wraps=real_connect) as connect:
            state = self.read()
        for call in connect.call_args_list:
            self.assertIn('mode=ro', call.args[0])
            self.assertTrue(call.kwargs['uri'])
        self.assertEqual(fingerprint(), before)
        self.assertEqual(state['projects'][0]['tasks'][0]['lease_owner'], 'worker1')

    def test_ledger_cannot_read_outside_permitted_skill_roots(self):
        private = self.root / 'private/example'
        self.skill(private)
        with self.ledger() as db:
            self.attempts(db)
            db.execute('INSERT INTO candidates VALUES(?,?,?,?,?,?)',
                       ('repeat', 'example', str(private), 'hash', 3, 'g'))
            db.execute('INSERT INTO installations VALUES(?,?,?)', (str(private), 'repeat', 'hash'))
        state = self.read()
        self.assertEqual(state['skills'], [])
        self.assertEqual(len(state['projects'][0]['errors']), 2)

    def test_wal_reads_latest_commits_without_creating_or_modifying_source_sidecars(self):
        db = self.connect('state.sqlite3')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE tasks(id TEXT,title TEXT,status TEXT,lease_owner TEXT,lease_expires_at REAL)')
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?)', ('wal-task', 'Committed in WAL', 'done', None, None))
        db.commit()
        def files():
            return {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in self.project.rglob('*') if p.is_file()}
        before = files()
        state = self.read()
        self.assertEqual(state['projects'][0]['tasks'][0]['id'], 'wal-task')
        self.assertEqual(before, files())
        db.close()
        before = files()
        state = self.read()
        self.assertEqual(state['projects'][0]['tasks'][0]['id'], 'wal-task')
        self.assertEqual(before, files())

    def test_concurrent_wal_commit_is_reported_and_temporary_copy_is_cleaned(self):
        db = self.connect('state.sqlite3')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE tasks(id TEXT,title TEXT,status TEXT,lease_owner TEXT,lease_expires_at REAL)')
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?)', ('task', 'Before', 'done', None, None))
        db.commit()
        start, finished, failures = threading.Event(), threading.Event(), []
        def writer():
            try:
                if not start.wait(2):
                    raise AssertionError('Reader did not reach the copy boundary')
                connection = sqlite3.connect(self.project / '.nexo/state.sqlite3')
                try:
                    connection.execute("UPDATE tasks SET title='Committed concurrently'")
                    connection.commit()
                finally:
                    connection.close()
            except Exception as error:
                failures.append(error)
            finally:
                finished.set()
        thread = threading.Thread(target=writer)
        thread.start()
        temporary_paths = []
        factory = tempfile.TemporaryDirectory
        def directory(*args, **kwargs):
            temporary = factory(*args, **kwargs)
            temporary_paths.append(Path(temporary.name))
            start.set()
            if not finished.wait(2):
                temporary.cleanup()
                raise AssertionError('Writer did not finish')
            return temporary
        try:
            with patch.object(dashboard.tempfile, 'TemporaryDirectory', side_effect=directory):
                state = self.read()
        finally:
            start.set()
            thread.join(3)
        self.assertFalse(failures)
        self.assertFalse(thread.is_alive())
        self.assertTrue(any('changed during inspection' in error for error in state['projects'][0]['errors']))
        self.assertEqual(state['projects'][0]['tasks'], [])
        self.assertTrue(temporary_paths)
        self.assertTrue(all(not path.exists() for path in temporary_paths))

    def test_junction_and_parent_traversal_are_rejected(self):
        target = self.home / 'skills/nexo-plan'
        self.skill(target, 'nexo-plan')
        original = Path.is_junction if hasattr(Path, 'is_junction') else None
        def is_junction(path):
            return path == target or bool(original and original(path))
        with patch.object(Path, 'is_junction', is_junction, create=True):
            state = self.read()
        self.assertEqual(state['skills'], [])
        self.assertTrue(state['warnings'])
        with self.assertRaisesRegex(ValueError, 'traversal'):
            dashboard._safe(self.home, 'skills/../auth.json')

    def test_document_and_row_limits_preserve_other_assets_and_exact_counts(self):
        folder = self.home / 'skills/nexo-plan'
        folder.mkdir(parents=True)
        (folder / 'SKILL.md').write_bytes(b'x' * (dashboard.DOCUMENT_BYTES + 1))
        self.skill(self.home / 'skills/nexo-build', 'nexo-build')
        with self.ledger() as db:
            self.attempts(db, statuses=('passed',) * 1100)
        with self.connect('state.sqlite3') as db:
            db.execute('CREATE TABLE tasks(id TEXT,title TEXT,status TEXT,lease_owner TEXT,lease_expires_at REAL)')
            db.executemany('INSERT INTO tasks VALUES(?,?,?,?,?)',
                           ((str(i), 'Task', 'done', None, None) for i in range(1100)))
        state = self.read()
        self.assertEqual(len(state['skills']), 1)
        self.assertIn('256 KiB', state['warnings'][0])
        self.assertEqual(state['projects'][0]['learning']['patterns'][0]['observations'], 1100)
        self.assertEqual(len(state['projects'][0]['tasks']), 1000)
        self.assertTrue(state['projects'][0]['errors'])


if __name__ == '__main__':
    unittest.main()
