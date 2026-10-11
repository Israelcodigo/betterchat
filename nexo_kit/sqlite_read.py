"""Bounded SQLite queries without opening the source database for writing."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time


@contextmanager
def readonly_snapshot(path: Path, *, max_bytes: int = 128 * 1024 * 1024,
                      timeout: float = 2.0):
    """Read DB+WAL into a disposable query-only snapshot, never source SHM.

    This is the normal read path, not a fallback after denied writes. Source
    read errors propagate. A readable database is required; no state is created
    when it is missing. Without an active WAL the snapshot stays in memory.
    With WAL data, SQLite builds its index only in a temporary directory; a
    client denying all temporary writes cannot inspect that case. Size, identity
    and timestamps detect concurrent ordinary writes;
    they are not an atomic snapshot guarantee against an adversarial writer
    restoring file attributes. A detected change is a retryable query error.
    """
    path = Path(path).absolute()
    wal = Path(str(path) + '-wal')
    journal = Path(str(path) + '-journal')
    for parent in path.parents:
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Database paths cannot contain symbolic links or junctions')

    def signature(source: Path, required: bool = False):
        try:
            info = source.lstat()
        except FileNotFoundError:
            if required:
                raise ValueError('Project state database is missing; initialize the project explicitly') from None
            return None
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Database and sidecars must be regular files')
        # Windows lstat/fstat expose different legacy ctime meanings in Python
        # 3.13; compare portable identity, length and last-modified fields.
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def journal_active():
        item = signature(journal)
        return item is not None and item[2] > 0

    def state():
        return {source: value for source in (path, wal)
                if (value := signature(source, source == path)) is not None}

    if journal_active():
        raise ValueError('Database has a rollback journal; retry after its writer finishes')
    before = state()
    if sum(value[2] for value in before.values()) > max_bytes:
        raise ValueError('Database and WAL exceed the read snapshot size limit')
    deadline = time.monotonic() + timeout

    def chunks(source: Path, expected: tuple):
        copied = 0
        with source.open('rb') as reader:
            info = os.fstat(reader.fileno())
            actual = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if actual != expected:
                raise ValueError('Database changed during inspection; retry the query')
            while chunk := reader.read(65536):
                if time.monotonic() > deadline:
                    raise ValueError('Database inspection timed out; retry the query')
                copied += len(chunk)
                if copied > expected[2]:
                    raise ValueError('Database changed during inspection; retry the query')
                yield chunk
        if copied != expected[2]:
            raise ValueError('Database changed during inspection; retry the query')

    def unchanged():
        if before != state() or journal_active():
            raise ValueError('Database changed during inspection; retry the query')

    def configure(connection):
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)

    if wal not in before or before[wal][2] == 0:
        data = bytearray()
        for chunk in chunks(path, before[path]):
            data.extend(chunk)
        unchanged()  # A WAL appearing or growing must never be silently ignored.
        if len(data) < 100 or data[:16] != b'SQLite format 3\x00':
            raise ValueError('Project state is not a valid SQLite database')
        if data[18] not in (1, 2) or data[19] not in (1, 2):
            raise ValueError('Unsupported SQLite file format version')
        # Only the private input copy changes. SQLite documents this conversion
        # for deserializing a fully checkpointed database with a WAL header:
        # https://sqlite.org/c3ref/deserialize.html
        data[18:20] = b'\x01\x01'
        connection = sqlite3.connect(':memory:', check_same_thread=False)
        try:
            if not hasattr(connection, 'deserialize'):
                raise ValueError('This Python SQLite build cannot deserialize an in-memory read snapshot')
            connection.deserialize(data)
            configure(connection)
            yield connection
        finally:
            connection.close()
        return

    try:
        temporary_directory = tempfile.TemporaryDirectory(prefix='nexo-query-read-')
    except OSError as error:
        raise ValueError('Reading an active SQLite WAL needs a writable temporary directory. '
                         'This client does not provide one; WAL data has not been ignored.') from error
    with temporary_directory as temporary:
        target = Path(temporary) / path.name
        for source, expected in before.items():
            destination = target if source == path else Path(str(target) + '-wal')
            with destination.open('xb') as writer:
                for chunk in chunks(source, expected):
                    writer.write(chunk)
        unchanged()
        connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True,
                                     timeout=0.25, check_same_thread=False)
        try:
            configure(connection)
            yield connection
        finally:
            connection.close()
