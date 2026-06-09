"""Backend abstraction for SQLite + Postgres.

One module shared by cm.py and tm.py. Both backends accept `?` placeholders;
Postgres translates them to `%s` at execute time. Rows come back as plain
`dict`s in both backends.

UUID primary keys are generated in Python via `new_uuid()`. Each table also
carries a readable `idx INTEGER UNIQUE` maintained by `next_idx(table)` —
gaps from deletes are fine. Timestamps are written by the app via `now_iso()`,
not by the DB, so no dialect-specific datetime functions appear in the SQL.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable


def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_postgres_uri(s: str) -> bool:
    return s.startswith(("postgresql://", "postgres://"))


_STATEMENT_SPLIT_RE = re.compile(r";\s*(?:\n|$)")


class Backend:
    """Thin wrapper around a DB connection."""

    def __init__(self, conn: Any, dialect: str) -> None:
        self._conn = conn
        self.dialect = dialect  # 'sqlite' | 'postgres'

    def _translate(self, sql: str) -> str:
        if self.dialect == "postgres":
            return sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        cur = self._conn.cursor()
        cur.execute(self._translate(sql), tuple(params))
        return cur

    def executescript(self, sql: str) -> None:
        if self.dialect == "sqlite":
            self._conn.executescript(sql)
            return
        # Postgres: split on semicolons-at-end-of-line and run each non-empty
        # statement separately. Keeps us off psycopg's simple-query quirks.
        for stmt in _STATEMENT_SPLIT_RE.split(sql):
            s = stmt.strip()
            if s:
                self._conn.cursor().execute(s)

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return self.execute(sql, params).fetchall()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def next_idx(self, table: str) -> int:
        row = self.fetchone(f"SELECT COALESCE(MAX(idx), 0) + 1 AS n FROM {table}")
        return int(row["n"])

    @property
    def integrity_error(self) -> type[Exception]:
        if self.dialect == "sqlite":
            return sqlite3.IntegrityError
        import psycopg
        return psycopg.errors.IntegrityError


def _sqlite_dict_factory(cur: sqlite3.Cursor, row: tuple) -> dict:
    return {col[0]: row[i] for i, col in enumerate(cur.description)}


def open_db(uri_or_path: str, *, require_exists: bool = True) -> Backend:
    """Open a DB by SQLite path or Postgres URI."""
    if is_postgres_uri(uri_or_path):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            die("psycopg is required for Postgres support: pip install 'psycopg[binary]>=3'")
        conn = psycopg.connect(uri_or_path, row_factory=dict_row)
        return Backend(conn, "postgres")
    if require_exists and not os.path.exists(uri_or_path):
        die(f"database not found: {uri_or_path}")
    parent = os.path.dirname(os.path.abspath(uri_or_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(uri_or_path)
    conn.row_factory = _sqlite_dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return Backend(conn, "sqlite")


# ---------- shared CREATE TABLE script ----------
#
# The meta-doc-manager-owned tables. todo-manager appends its own (users, todos)
# via tm.py. Both scripts use IF NOT EXISTS so init is idempotent and the two
# skills can be initialized in either order.

META_DOC_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
  id          TEXT PRIMARY KEY,
  idx         INTEGER NOT NULL UNIQUE,
  parent_id   TEXT REFERENCES topics(id) ON DELETE CASCADE,
  slug        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  description TEXT,
  depth       INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS modules (
  id          TEXT PRIMARY KEY,
  idx         INTEGER NOT NULL UNIQUE,
  kind        TEXT NOT NULL,
  path        TEXT NOT NULL,
  symbol      TEXT,
  description TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE (kind, path, symbol)
);

CREATE TABLE IF NOT EXISTS topic_modules (
  topic_id   TEXT NOT NULL REFERENCES topics(id)  ON DELETE CASCADE,
  module_id  TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  note       TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (topic_id, module_id)
);

CREATE TABLE IF NOT EXISTS documents (
  id          TEXT PRIMARY KEY,
  idx         INTEGER NOT NULL UNIQUE,
  flavor      TEXT NOT NULL,
  title       TEXT NOT NULL,
  doc_path    TEXT,
  content     TEXT,
  summary     TEXT,
  created_by  TEXT,
  source_ref  TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_topics (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  topic_id    TEXT NOT NULL REFERENCES topics(id)    ON DELETE CASCADE,
  PRIMARY KEY (document_id, topic_id)
);

CREATE TABLE IF NOT EXISTS document_modules (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  module_id   TEXT NOT NULL REFERENCES modules(id)   ON DELETE CASCADE,
  PRIMARY KEY (document_id, module_id)
);

CREATE INDEX IF NOT EXISTS idx_modules_path     ON modules(path);
CREATE INDEX IF NOT EXISTS idx_topics_parent    ON topics(parent_id);
CREATE INDEX IF NOT EXISTS idx_documents_flavor ON documents(flavor);
"""


TODO_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id   TEXT PRIMARY KEY,
  idx  INTEGER NOT NULL UNIQUE,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS todos (
  id           TEXT PRIMARY KEY,
  idx          INTEGER NOT NULL UNIQUE,
  document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  assignee_id  TEXT REFERENCES users(id) ON DELETE SET NULL,
  status       TEXT NOT NULL DEFAULT 'backlog',
  blocks       TEXT,
  priority     INTEGER,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_todos_status   ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_priority ON todos(priority);
CREATE INDEX IF NOT EXISTS idx_todos_document ON todos(document_id);
"""
