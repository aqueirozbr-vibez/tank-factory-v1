"""The single connection to ``trace.db`` and the forward-only migration runner.

Only the orchestrator process opens the trace for writing
(architecture.md · Invariant 1). Every write happens inside
:meth:`TraceDB.transaction`; readers use :meth:`fetchone` and
:meth:`fetchall`.
"""

import re
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from orchestrator.clock import utc_now
from orchestrator.errors import TraceError

_MIGRATION_NAME = re.compile(r"^\d{4}_[a-z0-9_]+$")
_PACKAGED_MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
_REPO_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

SqlParams = Sequence[object]


def default_migrations_dir() -> Path:
    """The repository's ``migrations/`` in development, the packaged copy when installed."""
    if _REPO_MIGRATIONS.is_dir():
        return _REPO_MIGRATIONS
    return _PACKAGED_MIGRATIONS


class TraceDB:
    """One SQLite connection in WAL mode with the schema applied."""

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self._conn = conn
        self._path = path
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: Path, *, migrations_dir: Path | None = None) -> "TraceDB":
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        db = cls(conn, path)
        db._migrate(migrations_dir or default_migrations_dir())
        return db

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._conn.close()

    # --- transactions -------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` … ``COMMIT``; rollback on any exception."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # --- reads --------------------------------------------------------------

    def fetchone(self, sql: str, params: SqlParams = ()) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._conn.execute(sql, params).fetchone()
            return row

    def fetchall(self, sql: str, params: SqlParams = ()) -> list[sqlite3.Row]:
        with self._lock:
            rows: list[sqlite3.Row] = self._conn.execute(sql, params).fetchall()
            return rows

    def scalar(self, sql: str, params: SqlParams = ()) -> Any:  # noqa: ANN401 - SQLite returns untyped values
        row = self.fetchone(sql, params)
        return None if row is None else row[0]

    # --- migrations ---------------------------------------------------------

    def applied_migrations(self) -> list[str]:
        return [str(r["id"]) for r in self.fetchall("SELECT id FROM schema_migrations ORDER BY id")]

    def _migrate(self, migrations_dir: Path) -> None:
        if not migrations_dir.is_dir():
            raise TraceError(f"migrations directory not found: {migrations_dir}")
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = set(self.applied_migrations())
            for file in sorted(migrations_dir.glob("*.sql")):
                migration_id = file.stem
                if not _MIGRATION_NAME.match(migration_id):
                    raise TraceError(
                        f"migration file name must be NNNN_short_name.sql: {file.name}"
                    )
                if migration_id in applied:
                    continue
                self._apply(migration_id, file.read_text(encoding="utf-8"))

    def _apply(self, migration_id: str, sql: str) -> None:
        script = (
            "BEGIN;\n"
            f"{sql}\n"
            "INSERT INTO schema_migrations (id, applied_at) VALUES "
            f"('{migration_id}', '{utc_now()}');\n"
            "COMMIT;"
        )
        try:
            self._conn.executescript(script)
        except sqlite3.Error as exc:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise TraceError(f"migration {migration_id} failed: {exc}") from exc
