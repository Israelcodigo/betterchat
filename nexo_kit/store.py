"""Durable local task and memory storage for Nexo Kit.

The database is the source of truth. Task claims use SQLite write transactions,
expiring leases and fresh fencing tokens; an expired worker cannot finish a task.
No third-party packages or background services are required.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Any, Iterable, Mapping
import uuid


class KitError(ValueError):
    """An invalid operation on kit state, safe to report to a CLI user."""


SCHEMA_VERSION = 1
TASK_STATUSES = frozenset({"pending", "running", "review", "done", "failed", "blocked"})
MEMORY_STATUSES = frozenset({"candidate", "active", "archived"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise KitError("ID must contain 1-80 ASCII letters, digits, underscores or hyphens.")
    return value


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise KitError("State must be JSON-serializable and contain finite numbers.") from exc


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000:
        raise KitError("Limit must be an integer between 1 and 1000.")
    return value


class Store:
    """Project-scoped SQLite store at ``project/.nexo/state.sqlite3``.

    Methods are safe to call from multiple threads on one Store or from separate
    processes using separate Store instances. A Store should not be inherited
    across a process fork. Returned task dictionaries expose ``evidence`` and
    ``depends_on`` rather than SQLite's serialized fields. ``readonly=True``
    opens a bounded DB+WAL snapshot without source writes or schema migration;
    mutations are rejected and this view does not refresh after construction.
    """

    def __init__(self, project: Path | str, *, readonly: bool = False):
        self.project = Path(project).expanduser().resolve()
        self.state_dir = self.project / ".nexo"
        self.db_path = self.state_dir / "state.sqlite3"
        self._lock = threading.RLock()
        self._closed = False
        self._readonly = readonly
        self._read_context = None
        if readonly:
            from .sqlite_read import readonly_snapshot
            self._read_context = readonly_snapshot(self.db_path)
            self._conn = self._read_context.__enter__()
            try:
                version = self._conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise KitError(f"Database schema {version} is newer than supported {SCHEMA_VERSION}.")
                self.fts_enabled = self._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'").fetchone() is not None
            except BaseException:
                self.close()
                raise
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=15, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self.fts_enabled = False
        try:
            self._initialize()
        except Exception:
            self._conn.close()
            self._closed = True
            raise

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                if self._read_context is not None:
                    self._read_context.__exit__(None, None, None)
                else:
                    self._conn.close()
                self._closed = True

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self._closed:
                raise KitError("Store is closed.")
            if self._readonly:
                raise KitError("Cannot mutate a read-only Store.")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def _initialize(self) -> None:
        with self._transaction() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise KitError(f"Database schema {version} is newer than supported {SCHEMA_VERSION}.")
            statements = [
                """CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','running','review','done','failed','blocked')),
                    version INTEGER NOT NULL DEFAULT 1,
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                    max_attempts INTEGER NOT NULL CHECK(max_attempts > 0),
                    lease_owner TEXT, lease_token TEXT, lease_expires_at REAL,
                    evidence_json TEXT, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    CHECK ((status='running' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
                         AND lease_expires_at IS NOT NULL) OR
                        (status!='running' AND lease_owner IS NULL AND lease_token IS NULL
                         AND lease_expires_at IS NULL)))""",
                """CREATE TABLE IF NOT EXISTS dependencies (
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    dependency_id TEXT NOT NULL REFERENCES tasks(id),
                    PRIMARY KEY(task_id, dependency_id), CHECK(task_id != dependency_id))""",
                "CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created_at)",
                "CREATE INDEX IF NOT EXISTS dependencies_reverse ON dependencies(dependency_id)",
                """CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL,
                    source TEXT NOT NULL, status TEXT NOT NULL
                      CHECK(status IN ('candidate','active','archived')),
                    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
                    dedup_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                "CREATE INDEX IF NOT EXISTS memories_status ON memories(status)",
                """CREATE TABLE IF NOT EXISTS memory_links (
                    source_id TEXT NOT NULL REFERENCES memories(id),
                    target_id TEXT NOT NULL REFERENCES memories(id),
                    relation TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(source_id, target_id, relation), CHECK(source_id != target_id))""",
                "CREATE INDEX IF NOT EXISTS memory_links_target ON memory_links(target_id)",
            ]
            for statement in statements:
                conn.execute(statement)
            try:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING "
                             "fts5(memory_id UNINDEXED, text, source, tokenize='unicode61')")
                # Rebuild missing rows when opening a DB previously used without FTS5.
                conn.execute("INSERT INTO memories_fts(memory_id,text,source) "
                             "SELECT id,text,source FROM memories WHERE id NOT IN "
                             "(SELECT memory_id FROM memories_fts)")
                self.fts_enabled = True
            except sqlite3.OperationalError as exc:
                if "fts5" not in str(exc).lower() and "no such module" not in str(exc).lower():
                    raise
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _event(self, conn: sqlite3.Connection, kind: str, payload: Any) -> int:
        if not isinstance(kind, str) or not kind.strip():
            raise KitError("Event kind cannot be empty.")
        cursor = conn.execute("INSERT INTO events(kind,payload_json,created_at) VALUES(?,?,?)",
                              (kind, _json(payload), _now()))
        return cursor.lastrowid

    def add_event(self, kind: str, payload: Any) -> dict:
        with self._transaction() as conn:
            event_id = self._event(conn, kind, payload)
            row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            return self._event_dict(row)

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def events(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?",
                                      (_limit(limit),)).fetchall()
            return [self._event_dict(row) for row in rows]

    def _task(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (_id(task_id),)).fetchone()
        if row is None:
            raise KitError(f"Unknown task: {task_id}")
        return row

    def _task_dict(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        item = dict(row)
        raw = item.pop("evidence_json")
        item["evidence"] = json.loads(raw) if raw is not None else None
        item["depends_on"] = [dep[0] for dep in conn.execute(
            "SELECT dependency_id FROM dependencies WHERE task_id=? ORDER BY dependency_id",
            (item["id"],))]
        return item

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            return self._task_dict(self._conn, self._task(self._conn, task_id))

    def list_tasks(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tasks ORDER BY created_at,id").fetchall()
            return [self._task_dict(self._conn, row) for row in rows]

    def _add_dependency(self, conn: sqlite3.Connection, task_id: str, dependency_id: str) -> None:
        self._task(conn, task_id)
        self._task(conn, dependency_id)
        if task_id == dependency_id:
            raise KitError("A task cannot depend on itself.")
        cycle = conn.execute("""WITH RECURSIVE ancestors(id) AS (
            SELECT dependency_id FROM dependencies WHERE task_id=?
            UNION SELECT d.dependency_id FROM dependencies d JOIN ancestors a ON d.task_id=a.id
            ) SELECT 1 FROM ancestors WHERE id=? LIMIT 1""", (dependency_id, task_id)).fetchone()
        if cycle:
            raise KitError("Dependency would introduce a cycle.")
        conn.execute("INSERT OR IGNORE INTO dependencies(task_id,dependency_id) VALUES(?,?)",
                     (task_id, dependency_id))

    def add_task(self, title: str, description: str = "", depends_on: Iterable[str] = (),
                 max_attempts: int = 3, task_id: str | None = None) -> dict:
        if not isinstance(title, str) or not title.strip():
            raise KitError("Task title cannot be empty.")
        if not isinstance(description, str):
            raise KitError("Task description must be text.")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
            raise KitError("max_attempts must be a positive integer.")
        if isinstance(depends_on, (str, bytes)):
            raise KitError("depends_on must be a collection of task IDs.")
        dependencies = list(dict.fromkeys(_id(dep) for dep in depends_on))
        task_id = _id(task_id if task_id is not None else "t_" + uuid.uuid4().hex)
        now = _now()
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise KitError(f"Task already exists: {task_id}")
            conn.execute("""INSERT INTO tasks(id,title,description,status,max_attempts,created_at,updated_at)
                VALUES(?,?,?,'pending',?,?,?)""", (task_id, title.strip(), description, max_attempts, now, now))
            for dependency in dependencies:
                self._add_dependency(conn, task_id, dependency)
            self._event(conn, "task.created", {"task_id": task_id, "depends_on": dependencies})
            return self._task_dict(conn, self._task(conn, task_id))

    def add_dependency(self, task_id: str, dependency_id: str) -> dict:
        with self._transaction() as conn:
            task = self._task(conn, task_id)
            if task["status"] != "pending":
                raise KitError("Dependencies can only be changed while a task is pending.")
            self._add_dependency(conn, task_id, dependency_id)
            conn.execute("UPDATE tasks SET version=version+1,updated_at=? WHERE id=?", (_now(), task_id))
            self._event(conn, "task.dependency_added", {"task_id": task_id, "dependency_id": dependency_id})
            return self._task_dict(conn, self._task(conn, task_id))

    def ready_tasks(self) -> list[dict]:
        """Return runnable tasks; dependencies only unlock after verified completion."""
        with self._lock:
            rows = self._conn.execute("""SELECT t.* FROM tasks t
                WHERE status='pending' AND attempts<max_attempts AND NOT EXISTS (
                    SELECT 1 FROM dependencies d JOIN tasks p ON d.dependency_id=p.id
                    WHERE d.task_id=t.id AND p.status!='done') ORDER BY t.created_at,t.id""").fetchall()
            return [self._task_dict(self._conn, row) for row in rows]

    @staticmethod
    def _lease_seconds(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not 0 < value <= 86400:
            raise KitError("Lease duration must be greater than zero and at most 86400 seconds.")
        return float(value)

    def _recover_expired(self, conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute("SELECT * FROM tasks WHERE status='running' AND lease_expires_at<=?",
                            (time.time(),)).fetchall()
        recovered = []
        for row in rows:
            status = "pending" if row["attempts"] < row["max_attempts"] else "failed"
            conn.execute("""UPDATE tasks SET status=?,lease_owner=NULL,lease_token=NULL,
                lease_expires_at=NULL,error='Worker lease expired',version=version+1,updated_at=?
                WHERE id=?""", (status, _now(), row["id"]))
            self._event(conn, "task.lease_expired", {"task_id": row["id"], "status": status,
                                                    "previous_owner": row["lease_owner"]})
            recovered.append(row["id"])
        return recovered

    def recover_expired(self) -> list[str]:
        with self._transaction() as conn:
            return self._recover_expired(conn)

    def claim_task(self, task_id: str, owner: str, lease_seconds: float = 120) -> dict:
        duration = self._lease_seconds(lease_seconds)
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 256:
            raise KitError("Worker owner must contain 1-256 characters.")
        with self._transaction() as conn:
            self._recover_expired(conn)
            row = self._task(conn, task_id)
            if row["status"] != "pending":
                raise KitError(f"Task is {row['status']}, not pending.")
            if row["attempts"] >= row["max_attempts"]:
                raise KitError("Task has exhausted its attempts.")
            unfinished = conn.execute("""SELECT p.id FROM dependencies d JOIN tasks p
                ON d.dependency_id=p.id WHERE d.task_id=? AND p.status!='done'""", (task_id,)).fetchall()
            if unfinished:
                raise KitError("Task dependencies are incomplete: " + ", ".join(r[0] for r in unfinished))
            conn.execute("""UPDATE tasks SET status='running',lease_owner=?,lease_token=?,
                lease_expires_at=?,attempts=attempts+1,evidence_json=NULL,
                version=version+1,updated_at=? WHERE id=?""",
                         (owner.strip(), secrets.token_urlsafe(24), time.time() + duration, _now(), task_id))
            self._event(conn, "task.claimed", {"task_id": task_id, "owner": owner.strip(),
                                              "attempt": row["attempts"] + 1})
            return self._task_dict(conn, self._task(conn, task_id))

    def _check_lease(self, conn: sqlite3.Connection, task_id: str, token: str) -> sqlite3.Row:
        row = self._task(conn, task_id)
        if row["status"] != "running" or not isinstance(token, str) or \
                not secrets.compare_digest((row["lease_token"] or "").encode("utf-8"), token.encode("utf-8")):
            raise KitError("Invalid worker lease or stale fencing token.")
        if row["lease_expires_at"] <= time.time():
            raise KitError("Worker lease has expired; recover and claim the task again.")
        return row

    def heartbeat(self, task_id: str, token: str, lease_seconds: float = 120) -> dict:
        duration = self._lease_seconds(lease_seconds)
        with self._transaction() as conn:
            self._check_lease(conn, task_id, token)
            conn.execute("UPDATE tasks SET lease_expires_at=?,version=version+1,updated_at=? WHERE id=?",
                         (time.time() + duration, _now(), task_id))
            return self._task_dict(conn, self._task(conn, task_id))

    def finish_task(self, task_id: str, token: str, status: str,
                    evidence: Any = None, error: str | None = None) -> dict:
        if status not in {"review", "failed", "blocked"}:
            raise KitError("Workers may finish only as review, failed or blocked.")
        if error is not None and not isinstance(error, str):
            raise KitError("Error must be text.")
        serialized = None if evidence is None else _json(evidence)
        with self._transaction() as conn:
            self._check_lease(conn, task_id, token)
            conn.execute("""UPDATE tasks SET status=?,evidence_json=?,error=?,lease_owner=NULL,
                lease_token=NULL,lease_expires_at=NULL,version=version+1,updated_at=? WHERE id=?""",
                         (status, serialized, error, _now(), task_id))
            self._event(conn, "task.worker_finished", {"task_id": task_id, "status": status, "error": error})
            return self._task_dict(conn, self._task(conn, task_id))

    def complete_task(self, task_id: str, evidence: Mapping[str, Any]) -> dict:
        """Approve reviewed work with externally validated verification evidence.

        This structural guard does not execute checks or authenticate receipts.
        The caller must validate the fingerprint and actual check results first.
        """
        if not isinstance(evidence, Mapping) or evidence.get("verdict") != "passed":
            raise KitError("Completion requires verification evidence with verdict='passed'.")
        checks = evidence.get("checks")
        fingerprint = evidence.get("fingerprint")
        if not isinstance(checks, list) or not checks or any(
                not isinstance(check, Mapping) or check.get("status") != "passed" for check in checks):
            raise KitError("Completion requires a non-empty list of passing checks.")
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise KitError("Completion requires a non-empty workspace fingerprint.")
        serialized = _json(dict(evidence))
        with self._transaction() as conn:
            row = self._task(conn, task_id)
            if row["status"] != "review":
                raise KitError("Only a task awaiting review can be completed.")
            conn.execute("""UPDATE tasks SET status='done',evidence_json=?,error=NULL,
                version=version+1,updated_at=? WHERE id=?""", (serialized, _now(), task_id))
            self._event(conn, "task.verified", {"task_id": task_id, "fingerprint": fingerprint,
                                              "check_count": len(checks)})
            return self._task_dict(conn, self._task(conn, task_id))

    def reject_review(self, task_id: str, error: str, evidence: Any = None) -> dict:
        """Reject verified work without bypassing the bounded retry budget.

        A failed check releases no dependency: the task returns to ``failed``
        and may only run again through the ordinary explicit retry path.
        """
        if not isinstance(error, str) or not error.strip():
            raise KitError("Review rejection requires a non-empty error.")
        serialized = None if evidence is None else _json(evidence)
        with self._transaction() as conn:
            row = self._task(conn, task_id)
            if row["status"] != "review":
                raise KitError("Only a task awaiting review can be rejected.")
            conn.execute("""UPDATE tasks SET status='failed',evidence_json=?,error=?,
                version=version+1,updated_at=? WHERE id=?""", (serialized, error, _now(), task_id))
            self._event(conn, "task.review_rejected", {"task_id": task_id, "error": error})
            return self._task_dict(conn, self._task(conn, task_id))

    def retry_task(self, task_id: str) -> dict:
        with self._transaction() as conn:
            row = self._task(conn, task_id)
            if row["status"] not in {"failed", "blocked"}:
                raise KitError("Only failed or blocked tasks can be retried.")
            if row["attempts"] >= row["max_attempts"]:
                raise KitError("Task has exhausted its attempts; create a new task after diagnosis.")
            conn.execute("""UPDATE tasks SET status='pending',evidence_json=NULL,
                version=version+1,updated_at=? WHERE id=?""", (_now(), task_id))
            self._event(conn, "task.retried", {"task_id": task_id})
            return self._task_dict(conn, self._task(conn, task_id))

    @staticmethod
    def _confidence(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                not math.isfinite(value) or not 0 <= value <= 1:
            raise KitError("Memory confidence must be between 0 and 1.")
        return float(value)

    @staticmethod
    def _memory_dict(row: sqlite3.Row) -> dict:
        item = dict(row)
        item.pop("dedup_key", None)
        return item

    def _memory(self, conn: sqlite3.Connection, memory_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM memories WHERE id=?", (_id(memory_id),)).fetchone()
        if row is None:
            raise KitError(f"Unknown memory: {memory_id}")
        return row

    def add_memory(self, text: str, kind: str = "finding", source: str = "",
                   status: str = "candidate", confidence: float = 0.5) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise KitError("Memory text cannot be empty.")
        if not isinstance(kind, str) or not kind.strip() or not isinstance(source, str):
            raise KitError("Memory kind must be non-empty text and source must be text.")
        if status not in MEMORY_STATUSES:
            raise KitError("Memory status must be candidate, active or archived.")
        confidence = self._confidence(confidence)
        text = text.strip()
        kind = kind.strip()
        source = source.strip()
        digest = hashlib.sha256(_json([kind, " ".join(text.split()), source]).encode("utf-8")).hexdigest()
        with self._transaction() as conn:
            existing = conn.execute("SELECT * FROM memories WHERE dedup_key=?", (digest,)).fetchone()
            if existing:
                return self._memory_dict(existing)
            memory_id = "m_" + uuid.uuid4().hex
            now = _now()
            conn.execute("""INSERT INTO memories(id,text,kind,source,status,confidence,dedup_key,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (memory_id, text, kind, source, status, confidence, digest, now, now))
            if self.fts_enabled:
                conn.execute("INSERT INTO memories_fts(memory_id,text,source) VALUES(?,?,?)",
                             (memory_id, text, source))
            self._event(conn, "memory.created", {"memory_id": memory_id, "status": status, "kind": kind})
            return self._memory_dict(self._memory(conn, memory_id))

    def get_memory(self, memory_id: str) -> dict:
        with self._lock:
            return self._memory_dict(self._memory(self._conn, memory_id))

    def list_memories(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memories ORDER BY created_at,id").fetchall()
            return [self._memory_dict(row) for row in rows]

    def update_memory(self, memory_id: str, *, confidence: float | None = None,
                      status: str | None = None) -> dict:
        """Explicit, audited updates; repeated observations never auto-promote rules."""
        if confidence is not None:
            confidence = self._confidence(confidence)
        if status is not None and status not in MEMORY_STATUSES:
            raise KitError("Memory status must be candidate, active or archived.")
        with self._transaction() as conn:
            row = self._memory(conn, memory_id)
            new_confidence = row["confidence"] if confidence is None else confidence
            new_status = row["status"] if status is None else status
            conn.execute("UPDATE memories SET confidence=?,status=?,updated_at=? WHERE id=?",
                         (new_confidence, new_status, _now(), memory_id))
            self._event(conn, "memory.updated", {"memory_id": memory_id, "status": new_status,
                                                "confidence": new_confidence})
            return self._memory_dict(self._memory(conn, memory_id))

    def approve_memory(self, memory_id: str) -> dict:
        return self.update_memory(memory_id, status="active")

    def search_memory(self, query: str, limit: int = 10,
                      include_candidates: bool = False) -> list[dict]:
        limit = _limit(limit)
        if not isinstance(query, str):
            raise KitError("Memory query must be text.")
        terms = list(dict.fromkeys(re.findall(r"\w+", query, flags=re.UNICODE)))[:40]
        if not terms:
            return []
        statuses = "('active','candidate')" if include_candidates else "('active')"
        with self._lock:
            if self.fts_enabled:
                expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
                rows = self._conn.execute(f"""SELECT m.*,bm25(memories_fts,0,1,0.25) AS score
                    FROM memories_fts JOIN memories m ON m.id=memories_fts.memory_id
                    WHERE memories_fts MATCH ? AND m.status IN {statuses}
                    ORDER BY score,m.confidence DESC,m.id LIMIT ?""", (expression, limit)).fetchall()
                return [self._memory_dict(row) for row in rows]
            # Builds without FTS5 remain functional. SQL wildcards are escaped;
            # rank the filtered rows by term occurrence, not confidence alone.
            escaped = [term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for term in terms]
            clauses = " OR ".join("(text LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\')" for _ in terms)
            parameters = ["%" + term + "%" for term in escaped for _ in range(2)]
            rows = self._conn.execute(f"SELECT * FROM memories WHERE status IN {statuses} AND ({clauses})",
                                      parameters).fetchall()
            results = []
            for row in rows:
                item = self._memory_dict(row)
                haystack = (item["text"] + " " + item["source"]).casefold()
                item["score"] = -sum(haystack.count(term.casefold()) for term in terms)
                results.append(item)
            results.sort(key=lambda item: (item["score"], -item["confidence"], item["id"]))
            return results[:limit]

    def link_memory(self, source: str, target: str, relation: str) -> dict:
        if not isinstance(relation, str) or not relation.strip() or len(relation) > 80:
            raise KitError("Memory relation must contain 1-80 characters.")
        if source == target:
            raise KitError("A memory cannot link to itself.")
        with self._transaction() as conn:
            self._memory(conn, source)
            self._memory(conn, target)
            conn.execute("""INSERT OR IGNORE INTO memory_links(source_id,target_id,relation,created_at)
                VALUES(?,?,?,?)""", (source, target, relation.strip(), _now()))
            self._event(conn, "memory.linked", {"source_id": source, "target_id": target,
                                              "relation": relation.strip()})
            return dict(conn.execute("SELECT * FROM memory_links WHERE source_id=? AND target_id=? AND relation=?",
                                     (source, target, relation.strip())).fetchone())

    def related_memories(self, memory_id: str, include_candidates: bool = False) -> list[dict]:
        """Return direct neighbors, with relation and direction; exclude archived rows."""
        with self._lock:
            self._memory(self._conn, memory_id)
            statuses = "('active','candidate')" if include_candidates else "('active')"
            rows = self._conn.execute(f"""SELECT m.*,l.relation,
                CASE WHEN l.source_id=? THEN 'outgoing' ELSE 'incoming' END AS direction
                FROM memory_links l JOIN memories m ON m.id=
                CASE WHEN l.source_id=? THEN l.target_id ELSE l.source_id END
                WHERE (l.source_id=? OR l.target_id=?) AND m.status IN {statuses}
                ORDER BY l.relation,m.id""", (memory_id, memory_id, memory_id, memory_id)).fetchall()
            return [self._memory_dict(row) for row in rows]

    def export_state(self) -> dict:
        """A consistent JSON snapshot; the SQLite DB remains the source of truth."""
        with self._transaction() as conn:
            return {
                "schema_version": SCHEMA_VERSION,
                "exported_at": _now(),
                "project": str(self.project),
                "fts_enabled": self.fts_enabled,
                "tasks": [self._task_dict(conn, row) for row in conn.execute(
                    "SELECT * FROM tasks ORDER BY created_at,id").fetchall()],
                "memories": [self._memory_dict(row) for row in conn.execute(
                    "SELECT * FROM memories ORDER BY created_at,id").fetchall()],
                "memory_links": [dict(row) for row in conn.execute(
                    "SELECT * FROM memory_links ORDER BY source_id,target_id,relation").fetchall()],
                "events": [self._event_dict(row) for row in conn.execute(
                    "SELECT * FROM events ORDER BY id").fetchall()],
            }
