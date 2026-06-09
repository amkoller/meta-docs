#!/usr/bin/env python3
"""todo-manager CLI.

Track per-meta-document todos in the same SQLite file used by
meta-doc-manager. Each todo references a `flavor = 'todo'` document and
carries workflow state (assignee, status, blocks, priority). See
SKILL.md in the parent directory for the conceptual model and
references/schema.md for the underlying schema.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Iterable

ENV_DB = "META_DOC_MANAGER_DB"
STATUSES = ("backlog", "in_progress", "in_review", "done")
PRIORITY_STEP = 16384
GUIDANCE_KEY = "todo_priority_guidance"
DEFAULT_GUIDANCE = (
    "Prioritize each todo by reading its body and applying discretion. Two axes "
    "typically dominate:\n"
    "\n"
    "1. Substance (cross-cutting): items affecting data integrity, security, or "
    "system liveness rank above items affecting only developer ergonomics or pure "
    "efficiency.\n"
    "2. Category: bugs and incorrect behavior rank above developer-confusion items "
    "rank above pure performance optimization.\n"
    "\n"
    "Within \"developer confusion\", dead code typically ranks above naming-only or "
    "docs-only confusion. Honor explicit `defer-until-X` markers in the doc.\n"
    "\n"
    "Replace this guidance with `tm.py priority guidance set --text \"...\"` to fit "
    "your project's conventions."
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS todos (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  assignee_id  INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
  status       TEXT NOT NULL DEFAULT 'backlog'
               CHECK (status IN ('backlog','in_progress','in_review','done')),
  blocks       TEXT NULL,
  priority     INTEGER NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_todos_status   ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_priority ON todos(priority);
CREATE INDEX IF NOT EXISTS idx_todos_document ON todos(document_id);
"""


# ---------- helpers ----------

def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def connect(db_path: str, *, require_exists: bool = True) -> sqlite3.Connection:
    if require_exists and not os.path.exists(db_path):
        die(f"database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def resolve_db(args: argparse.Namespace) -> str:
    db = getattr(args, "db", None) or os.environ.get(ENV_DB)
    if not db:
        die(f"--db is required (or set ${ENV_DB})")
    return db


def require_meta_doc_db(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
    ).fetchone()
    if not row:
        die("this DB has no `documents` table; init it with cm.py first")


def get_user_by_name(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    if not row:
        die(f"no such user: {name}")
    return row


def get_todo(conn: sqlite3.Connection, tid: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (tid,)).fetchone()
    if not row:
        die(f"no todo with id {tid}")
    return row


def csv_ints(s: str | None) -> list[int]:
    if not s:
        return []
    out = []
    for p in s.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            out.append(int(p))
        except ValueError:
            die(f"expected comma-separated integers, got {p!r}")
    return out


def parse_blocks_json(s: str | None) -> list[int]:
    if not s:
        return []
    try:
        v = json.loads(s)
    except json.JSONDecodeError:
        die(f"blocks column is not valid JSON: {s!r}")
    if not isinstance(v, list) or not all(isinstance(x, int) for x in v):
        die(f"blocks column must be a JSON array of integers: {s!r}")
    return v


def canonicalize_blocks(ids: Iterable[int]) -> str | None:
    """Normalize a list of blocked-todo ids into the stored JSON form."""
    deduped = sorted(set(int(x) for x in ids))
    if not deduped:
        return None
    return json.dumps(deduped)


def emit(rows: Iterable[Any], fmt: str, *, columns: list[str] | None = None) -> None:
    rows = list(rows)
    if fmt == "json":
        if rows and isinstance(rows[0], sqlite3.Row):
            data = [dict(r) for r in rows]
        else:
            data = list(rows)
        print(json.dumps(data, indent=2, default=str))
        return
    if not rows:
        print("(no rows)")
        return
    if columns is None:
        columns = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c] if r[c] is not None else "")) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r[c] if r[c] is not None else "").ljust(widths[c]) for c in columns))


def load_guidance(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM config WHERE key = ?", (GUIDANCE_KEY,)).fetchone()
    if not row:
        die(f"no priority guidance configured; run `tm.py priority guidance set` "
            f"or rerun `tm.py init` to seed the default")
    return row["value"]


# ---------- commands ----------

def cmd_init(args: argparse.Namespace) -> None:
    db = resolve_db(args)
    conn = connect(db, require_exists=False)
    require_meta_doc_db(conn)
    conn.executescript(SCHEMA_SQL)
    # Seed default priority guidance if not already set.
    existing = conn.execute("SELECT 1 FROM config WHERE key = ?", (GUIDANCE_KEY,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO config(key, value) VALUES (?, ?)",
            (GUIDANCE_KEY, DEFAULT_GUIDANCE),
        )
        print("seeded default priority guidance")
    conn.commit()
    print(f"todo-manager tables ready in {db}")


def cmd_user_add(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    try:
        cur = conn.execute("INSERT INTO users(name) VALUES (?)", (args.name,))
    except sqlite3.IntegrityError:
        die(f"user already exists: {args.name}")
    conn.commit()
    print(f"user added: id={cur.lastrowid} name={args.name}")


def cmd_user_list(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    rows = conn.execute("SELECT id, name FROM users ORDER BY name").fetchall()
    emit(rows, args.format, columns=["id", "name"])


def cmd_user_delete(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    if args.id is not None:
        target = args.id
    else:
        target = get_user_by_name(conn, args.name)["id"]
    conn.execute("DELETE FROM users WHERE id = ?", (target,))
    conn.commit()
    print(f"user deleted: id={target}")


def _resolve_assignee(conn: sqlite3.Connection, name: str | None) -> int | None:
    if name is None or name.upper() == "NONE":
        return None
    return get_user_by_name(conn, name)["id"]


def _validate_blocks(conn: sqlite3.Connection, blocks: list[int], *,
                     this_id: int | None = None) -> None:
    """Verify each blocked id exists, isn't self, and doesn't introduce a cycle."""
    if not blocks:
        return
    if this_id is not None and this_id in blocks:
        die("a todo cannot block itself")
    for b in blocks:
        if not conn.execute("SELECT 1 FROM todos WHERE id = ?", (b,)).fetchone():
            die(f"--blocks references unknown todo id {b}")
    # Cycle check: build full graph and DFS from `this_id` through proposed edges.
    if this_id is None:
        return
    adj = _build_blocks_graph(conn)
    adj[this_id] = set(blocks)  # apply proposed change
    if _has_cycle(adj):
        die("proposed --blocks would introduce a cycle in the blocking DAG")


def _build_blocks_graph(conn: sqlite3.Connection) -> dict[int, set[int]]:
    adj: dict[int, set[int]] = {}
    for r in conn.execute("SELECT id, blocks FROM todos").fetchall():
        adj[r["id"]] = set(parse_blocks_json(r["blocks"]))
    return adj


def _has_cycle(adj: dict[int, set[int]]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in adj}

    def dfs(n: int) -> bool:
        color[n] = GRAY
        for m in adj.get(n, ()):
            if m not in color:
                color[m] = WHITE
            if color[m] == GRAY:
                return True
            if color[m] == WHITE and dfs(m):
                return True
        color[n] = BLACK
        return False

    return any(color[n] == WHITE and dfs(n) for n in list(color))


def cmd_todo_add(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    doc = conn.execute(
        "SELECT id, flavor, title FROM documents WHERE id = ?", (args.doc_id,)
    ).fetchone()
    if not doc:
        die(f"no document with id {args.doc_id}")
    if doc["flavor"] != "todo":
        die(f"document {args.doc_id} has flavor {doc['flavor']!r}; "
            f"todos only attach to docs with flavor 'todo'")
    assignee_id = _resolve_assignee(conn, args.assignee)
    status = args.status or "backlog"
    if status not in STATUSES:
        die(f"invalid status {status!r}; allowed: {', '.join(STATUSES)}")
    blocks = csv_ints(args.blocks)
    _validate_blocks(conn, blocks)  # no this_id yet
    blocks_json = canonicalize_blocks(blocks)
    cur = conn.execute(
        """INSERT INTO todos(document_id, assignee_id, status, blocks, priority)
           VALUES (?, ?, ?, ?, ?)""",
        (args.doc_id, assignee_id, status, blocks_json, args.priority),
    )
    conn.commit()
    print(f"todo added: id={cur.lastrowid} document={args.doc_id} status={status}")


def cmd_todo_list(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    sql = (
        "SELECT t.id, t.document_id, d.title AS doc_title, "
        "u.name AS assignee, t.status, t.blocks, t.priority "
        "FROM todos t "
        "JOIN documents d ON d.id = t.document_id "
        "LEFT JOIN users u ON u.id = t.assignee_id"
    )
    params: list[Any] = []
    clauses: list[str] = []
    if args.status:
        if args.status not in STATUSES:
            die(f"invalid status {args.status!r}; allowed: {', '.join(STATUSES)}")
        clauses.append("t.status = ?")
        params.append(args.status)
    if args.assignee is not None:
        if args.assignee.upper() == "NONE":
            clauses.append("t.assignee_id IS NULL")
        else:
            uid = get_user_by_name(conn, args.assignee)["id"]
            clauses.append("t.assignee_id = ?")
            params.append(uid)
    if args.doc_id is not None:
        clauses.append("t.document_id = ?")
        params.append(args.doc_id)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY t.priority IS NULL, t.priority DESC, t.id ASC"
    rows = conn.execute(sql, params).fetchall()
    emit(rows, args.format,
         columns=["id", "document_id", "doc_title", "assignee", "status", "blocks", "priority"])


def cmd_todo_show(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    todo = get_todo(conn, args.id)
    doc = conn.execute(
        "SELECT id, flavor, title, doc_path, content, summary FROM documents WHERE id = ?",
        (todo["document_id"],),
    ).fetchone()
    assignee = None
    if todo["assignee_id"] is not None:
        u = conn.execute("SELECT name FROM users WHERE id = ?", (todo["assignee_id"],)).fetchone()
        assignee = u["name"] if u else None
    record = {
        "id": todo["id"],
        "document_id": todo["document_id"],
        "doc_title": doc["title"] if doc else None,
        "doc_flavor": doc["flavor"] if doc else None,
        "doc_path": doc["doc_path"] if doc else None,
        "doc_summary": doc["summary"] if doc else None,
        "assignee": assignee,
        "status": todo["status"],
        "blocks": parse_blocks_json(todo["blocks"]),
        "priority": todo["priority"],
        "created_at": todo["created_at"],
        "updated_at": todo["updated_at"],
    }
    content = doc["content"] if doc else None
    if content is not None:
        record["doc_content_preview"] = (content if len(content) <= 512
                                         else content[:512] + "…")
    if args.format == "json":
        if content is not None:
            record["doc_content"] = content
        print(json.dumps(record, indent=2, default=str))
    else:
        for k, v in record.items():
            if isinstance(v, list):
                print(f"{k}: {', '.join(str(x) for x in v) if v else '-'}")
            else:
                print(f"{k}: {v if v is not None else '-'}")


def cmd_todo_update(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    todo = get_todo(conn, args.id)
    updates: dict[str, Any] = {}
    if args.status is not None:
        if args.status not in STATUSES:
            die(f"invalid status {args.status!r}; allowed: {', '.join(STATUSES)}")
        updates["status"] = args.status
    if args.assignee is not None:
        updates["assignee_id"] = _resolve_assignee(conn, args.assignee)
    if args.priority is not None:
        updates["priority"] = args.priority
    if args.blocks is not None:
        if args.blocks.upper() == "NONE" or args.blocks == "":
            updates["blocks"] = None
        else:
            blocks = csv_ints(args.blocks)
            _validate_blocks(conn, blocks, this_id=todo["id"])
            updates["blocks"] = canonicalize_blocks(blocks)
    if not updates:
        print("nothing to update")
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE todos SET {cols}, updated_at = datetime('now') WHERE id = ?",
        list(updates.values()) + [todo["id"]],
    )
    conn.commit()
    print(f"todo updated: id={todo['id']}")


def cmd_todo_delete(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    conn.execute("DELETE FROM todos WHERE id = ?", (args.id,))
    conn.commit()
    print(f"todo deleted: id={args.id}")


def cmd_priority_set(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    get_todo(conn, args.id)
    conn.execute(
        "UPDATE todos SET priority = ?, updated_at = datetime('now') WHERE id = ?",
        (args.value, args.id),
    )
    conn.commit()
    print(f"priority set: id={args.id} value={args.value}")


def cmd_priority_guidance_get(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    text = load_guidance(conn)
    if args.format == "json":
        print(json.dumps({"guidance": text}, indent=2))
    else:
        print(text)


def cmd_priority_guidance_set(args: argparse.Namespace) -> None:
    text = args.text
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as fh:
            text = fh.read()
    if text is None:
        die("provide --text or --from-file")
    if not text.strip():
        die("guidance text is empty")
    conn = connect(resolve_db(args))
    conn.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (GUIDANCE_KEY, text),
    )
    conn.commit()
    print(f"priority guidance set ({len(text)} chars)")


# ---------- argparse wiring ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tm.py", description=__doc__.strip().splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_db(ap):
        ap.add_argument("--db", help=f"path to SQLite file (or set ${ENV_DB})")

    # init
    a = sub.add_parser("init", help="create users + todos tables on an existing meta-doc DB")
    add_db(a)
    a.set_defaults(func=cmd_init)

    # user
    pu = sub.add_parser("user", help="manage users").add_subparsers(dest="sub", required=True)
    x = pu.add_parser("add"); add_db(x); x.add_argument("--name", required=True)
    x.set_defaults(func=cmd_user_add)
    x = pu.add_parser("list"); add_db(x)
    x.add_argument("--format", choices=["table", "json"], default="table")
    x.set_defaults(func=cmd_user_list)
    x = pu.add_parser("delete"); add_db(x)
    g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", type=int); g.add_argument("--name")
    x.set_defaults(func=cmd_user_delete)

    # todo
    pt = sub.add_parser("todo", help="manage todos").add_subparsers(dest="sub", required=True)
    x = pt.add_parser("add"); add_db(x)
    x.add_argument("--doc-id", type=int, required=True)
    x.add_argument("--assignee")
    x.add_argument("--status", choices=list(STATUSES))
    x.add_argument("--blocks", help="comma-separated todo ids this todo blocks")
    x.add_argument("--priority", type=int)
    x.set_defaults(func=cmd_todo_add)
    x = pt.add_parser("list"); add_db(x)
    x.add_argument("--status", choices=list(STATUSES))
    x.add_argument("--assignee", help="user name, or NONE for unassigned")
    x.add_argument("--doc-id", type=int)
    x.add_argument("--format", choices=["table", "json"], default="table")
    x.set_defaults(func=cmd_todo_list)
    x = pt.add_parser("show"); add_db(x)
    x.add_argument("--id", type=int, required=True)
    x.add_argument("--format", choices=["table", "json"], default="table")
    x.set_defaults(func=cmd_todo_show)
    x = pt.add_parser("update"); add_db(x)
    x.add_argument("--id", type=int, required=True)
    x.add_argument("--status", choices=list(STATUSES))
    x.add_argument("--assignee", help="user name, or NONE to clear")
    x.add_argument("--priority", type=int)
    x.add_argument("--blocks", help="comma-separated ids, NONE to clear, '' to clear")
    x.set_defaults(func=cmd_todo_update)
    x = pt.add_parser("delete"); add_db(x)
    x.add_argument("--id", type=int, required=True)
    x.set_defaults(func=cmd_todo_delete)

    # priority
    pp = sub.add_parser("priority", help="manage todo priorities").add_subparsers(
        dest="sub", required=True)
    x = pp.add_parser("set", help="set an arbitrary priority value on one todo")
    add_db(x)
    x.add_argument("--id", type=int, required=True)
    x.add_argument("--value", type=int, required=True)
    x.set_defaults(func=cmd_priority_set)
    pg = pp.add_parser("guidance", help="manage freeform priority guidance").add_subparsers(
        dest="subsub", required=True)
    x = pg.add_parser("get", help="print the current guidance text")
    add_db(x)
    x.add_argument("--format", choices=["text", "json"], default="text")
    x.set_defaults(func=cmd_priority_guidance_get)
    x = pg.add_parser("set", help="replace the guidance text")
    add_db(x)
    g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="guidance text as a string")
    g.add_argument("--from-file", help="read guidance text from a file")
    x.set_defaults(func=cmd_priority_guidance_set)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
