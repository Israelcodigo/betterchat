"""Read commands must work without creating source WAL/SHM or migrating state."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from nexo_kit.actions import status
from nexo_kit.cli import execute, parser
from nexo_kit.config import defaults
from nexo_kit.sqlite_read import readonly_snapshot
from nexo_kit.store import KitError, Store


class ReadOnlyQueryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / 'project'
        self.project.mkdir()
        self.writer = Store(self.project)
        self.writer._conn.execute('PRAGMA wal_autocheckpoint=0')
        self.writer._conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.writer.add_task('Committed in WAL', task_id='first')
        self.writer.add_task('Dependency remains blocked', task_id='second', depends_on=['first'])
        memory = self.writer.add_memory('Wal search finding', source='read-only test')
        self.writer.approve_memory(memory['id'])
        (self.project / '.nexo/config.json').write_text(json.dumps(defaults()), encoding='utf-8')

    def tearDown(self):
        self.writer.close()
        self.temporary.cleanup()

    def files(self):
        return {str(path.relative_to(self.project)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.project.rglob('*') if path.is_file()}

    def command(self, *args):
        return execute(parser().parse_args(['--project', str(self.project), *args]))

    def test_cli_queries_read_wal_without_project_writes_or_source_connections(self):
        # An immutable read of the main file cannot see these WAL-only tasks.
        with closing(sqlite3.connect(self.writer.db_path.as_uri() + '?immutable=1', uri=True)) as base:
            self.assertEqual(base.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
        before = self.files()
        original_open, original_connect, original_mkdir = Path.open, sqlite3.connect, Path.mkdir

        def checked_open(path, mode='r', *args, **kwargs):
            if path.is_relative_to(self.project) and any(flag in mode for flag in 'wax+'):
                self.fail('Read command attempted a project write: ' + str(path))
            return original_open(path, mode, *args, **kwargs)

        def checked_connect(database, *args, **kwargs):
            self.assertNotIn(self.project.as_posix(), str(database).replace('\\', '/'))
            return original_connect(database, *args, **kwargs)

        def checked_mkdir(path, *args, **kwargs):
            if path.is_relative_to(self.project):
                self.fail('Read command attempted to create a project directory')
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, 'open', checked_open), patch.object(Path, 'mkdir', checked_mkdir), \
                patch.object(sqlite3, 'connect', checked_connect):
            self.assertEqual([t['id'] for t in self.command('task', 'list')], ['first', 'second'])
            self.assertEqual([t['id'] for t in self.command('task', 'ready')], ['first'])
            self.assertEqual(self.command('task', 'show', 'first')['title'], 'Committed in WAL')
            self.assertEqual(len(self.command('memory', 'list')), 1)
            self.assertEqual(len(self.command('memory', 'search', 'finding')), 1)
            self.assertEqual(self.command('status')['memory']['active'], 1)
        self.assertEqual(before, self.files())

    def test_read_does_not_create_sidecars_when_none_exist(self):
        self.writer.close()
        self.assertFalse(Path(str(self.writer.db_path) + '-wal').exists())
        before = self.files()
        self.assertEqual(len(self.command('task', 'list')), 2)
        self.assertEqual(before, self.files())

    def test_checkpointed_wal_header_queries_need_no_writable_directory(self):
        self.writer.close()
        self.assertEqual(self.writer.db_path.read_bytes()[18:20], b'\x02\x02')
        before = self.files()
        original_open = Path.open

        def no_writes(path, mode='r', *args, **kwargs):
            if any(flag in mode for flag in 'wax+'):
                raise PermissionError('All filesystem writes denied by client')
            return original_open(path, mode, *args, **kwargs)

        with patch('nexo_kit.sqlite_read.tempfile.TemporaryDirectory',
                   side_effect=FileNotFoundError('No usable temporary directory')) as temporary, \
                patch.object(Path, 'open', no_writes):
            self.assertEqual(self.command('status')['ready'], ['first'])
            self.assertEqual(len(self.command('task', 'list')), 2)
            self.assertEqual(self.command('task', 'show', 'first')['title'], 'Committed in WAL')
            self.assertEqual(len(self.command('memory', 'search', 'finding')), 1)
            with Store(self.project, readonly=True) as reader:
                self.assertEqual(reader._conn.execute('PRAGMA database_list').fetchone()[2], '')
                with self.assertRaises(sqlite3.OperationalError):
                    reader._conn.execute("UPDATE tasks SET title='forbidden'")
            temporary.assert_not_called()
        self.assertEqual(before, self.files())
        self.assertEqual(self.writer.db_path.read_bytes()[18:20], b'\x02\x02')

    def test_zero_length_wal_uses_memory_without_temporary_writes(self):
        self.writer._conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.assertEqual(Path(str(self.writer.db_path) + '-wal').stat().st_size, 0)
        before = self.files()
        with patch('nexo_kit.sqlite_read.tempfile.TemporaryDirectory',
                   side_effect=PermissionError('Temporary writes denied')) as temporary:
            self.assertEqual(len(self.command('task', 'list')), 2)
            temporary.assert_not_called()
        self.assertEqual(before, self.files())

    def test_wal_appearing_during_memory_copy_is_rejected(self):
        self.writer.close()
        self.assertFalse(Path(str(self.writer.db_path) + '-wal').exists())
        original_open = Path.open
        concurrent = []

        def concurrent_open(path, *args, **kwargs):
            reader = original_open(path, *args, **kwargs)
            if path == self.writer.db_path and not concurrent:
                connection = sqlite3.connect(path)
                concurrent.append(connection)
                connection.execute("UPDATE tasks SET title='Concurrent commit' WHERE id='first'")
                connection.commit()
            return reader

        try:
            with patch.object(Path, 'open', concurrent_open), \
                    patch('nexo_kit.sqlite_read.tempfile.TemporaryDirectory',
                          side_effect=PermissionError('Temporary writes denied')) as temporary:
                with self.assertRaisesRegex(ValueError, 'changed during inspection'):
                    self.command('status')
                temporary.assert_not_called()
            self.assertTrue(concurrent)
        finally:
            for connection in concurrent:
                connection.close()

    def test_active_wal_without_temporary_directory_reports_limit_not_stale_data(self):
        self.assertGreater(Path(str(self.writer.db_path) + '-wal').stat().st_size, 0)
        before = self.files()
        with patch('nexo_kit.sqlite_read.tempfile.TemporaryDirectory',
                   side_effect=FileNotFoundError('No usable temporary directory')):
            with self.assertRaisesRegex(ValueError, 'WAL data has not been ignored'):
                self.command('status')
        self.assertEqual(before, self.files())

    def test_missing_database_is_not_initialized_by_queries(self):
        project = Path(self.temporary.name) / 'empty'
        self.assertFalse(status(project)['initialized'])
        self.assertFalse(project.exists())
        (project / '.nexo').mkdir(parents=True)
        (project / '.nexo/config.json').write_text(json.dumps(defaults()), encoding='utf-8')
        for args in (['status'], ['task', 'list'], ['memory', 'list']):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'database is missing'):
                execute(parser().parse_args(['--project', str(project), *args]))
        self.assertEqual([p.name for p in (project / '.nexo').iterdir()], ['config.json'])

    def test_read_store_rejects_mutations_and_sql_writes(self):
        before = self.files()
        with Store(self.project, readonly=True) as reader:
            with self.assertRaisesRegex(KitError, 'read-only'):
                reader.add_task('Forbidden')
            with self.assertRaisesRegex(KitError, 'read-only'):
                reader.add_event('forbidden', {})
            with self.assertRaisesRegex(KitError, 'read-only'):
                reader.recover_expired()
            with self.assertRaises(sqlite3.OperationalError):
                reader._conn.execute("UPDATE tasks SET title='forbidden'")
            self.assertEqual(reader.get_task('first')['title'], 'Committed in WAL')
        self.assertEqual(before, self.files())

    def test_snapshot_is_disposed_after_close(self):
        self.assertGreater(Path(str(self.writer.db_path) + '-wal').stat().st_size, 0)
        reader = Store(self.project, readonly=True)
        target = Path(reader._conn.execute('PRAGMA database_list').fetchone()[2])
        self.assertTrue(target.exists())
        reader.close()
        reader.close()
        self.assertFalse(target.parent.exists())

    def test_concurrent_commit_is_rejected_and_copy_cleaned(self):
        directory = tempfile.TemporaryDirectory
        temporary_paths = []

        def changed_directory(*args, **kwargs):
            temporary = directory(*args, **kwargs)
            temporary_paths.append(Path(temporary.name))
            self.writer.add_event('concurrent', {'committed': True})
            return temporary

        with patch('nexo_kit.sqlite_read.tempfile.TemporaryDirectory', changed_directory):
            with self.assertRaisesRegex(ValueError, 'changed during inspection'):
                Store(self.project, readonly=True)
        self.assertTrue(temporary_paths)
        self.assertTrue(all(not path.exists() for path in temporary_paths))

    def test_source_read_denial_propagates_without_alternate_state(self):
        before = self.files()
        original_open = Path.open

        def denied_open(path, *args, **kwargs):
            if path == self.writer.db_path:
                raise PermissionError('Source database is not readable')
            return original_open(path, *args, **kwargs)

        with patch.object(Path, 'open', denied_open):
            with self.assertRaisesRegex(PermissionError, 'not readable'):
                self.command('status')
        self.assertEqual(before, self.files())

    def test_size_limit_and_active_journal_are_explicit_errors(self):
        before = self.files()
        with self.assertRaisesRegex(ValueError, 'size limit'):
            with readonly_snapshot(self.writer.db_path, max_bytes=1):
                self.fail('Oversize snapshot opened')
        self.assertEqual(before, self.files())
        journal = Path(str(self.writer.db_path) + '-journal')
        journal.write_bytes(b'writer in progress')
        before = self.files()
        with self.assertRaisesRegex(ValueError, 'rollback journal'):
            self.command('status')
        self.assertEqual(before, self.files())
        journal.unlink()

    def test_mutating_cli_retains_writable_store_semantics(self):
        added = self.command('task', 'add', 'Still writable', '--id', 'third')
        self.assertEqual(added['id'], 'third')
        self.assertEqual(self.writer.get_task('third')['title'], 'Still writable')


if __name__ == '__main__':
    unittest.main()
