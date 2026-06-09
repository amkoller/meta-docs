#!/usr/bin/env python3
"""todo-manager CLI.

Track per-meta-document todos in the same DB used by meta-doc-manager (SQLite
or Postgres). Each todo references a `flavor = 'todo'` document and carries
workflow state (assignee, status, blocks, priority). See SKILL.md for the
conceptual model and references/schema.md for the underlying schema.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

# Import the shared backend from meta-doc-manager.
_META_DOC_SCRIPTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "meta-doc-manager", "scripts")
)
sys.path.insert(0, _META_DOC_SCRIPTS)
from db import (  # noqa: E402
    TODO_SCHEMA_SQL,
    Backend,
    die,
    new_uuid,
    now_iso,
    open_db,
)

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


# ---------- helpers ----------

def resolve_db_uri(args: argparse.Namespace) -> str:
    db = getattr(args, "db", None) or os.environ.get(ENV_DB)
    if not db:
        die(f"--db is required (or set ${ENV_DB})")
    return db


def connect(args: argparse.Namespace, *, require_exists: bool = True) -> Backend:
    return open_db(resolve_db_uri(args), require_exists=require_exists)


def require_meta_doc_db(db: Backend) -> None:
    if db.dialect == "sqlite":
        row = db.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
        )
    else:
        row = db.fetchone(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'documents'"
        )
    if not row:
        die("this DB has no `documents` table; init it with cm.py first")


def get_user_by_name(db: Backend, name: str) -> dict:
    row = db.fetchone("SELECT * FROM users WHERE name = ?", (name,))
    if not row:
        die(f"no such user: {name}")
    return row


def get_todo_by_idx(db: Backend, idx: int) -> dict:
    row = db.fetchone("SELECT * FROM todos WHERE idx = ?", (idx,))
    if not row:
        die(f"no todo with id {idx}")
    return row


def csv_ints(s: str | None) -> list[int]:
    if not s:
        return []
    out: list[int] = []
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
    """Parse the stored JSON list of todo idx values."""
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
    deduped = sorted(set(int(x) for x in ids))
    if not deduped:
        return None
    return json.dumps(deduped)


def emit(rows: Iterable[dict], fmt: str, *, columns: list[str] | None = None) -> None:
    rows = list(rows)
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        print("(no rows)")
        return
    if columns is None:
        columns = list(rows[0].keys())
    widths = {
        c: max(len(c), *(len(str(r.get(c) if r.get(c) is not None else "")) for r in rows))
        for c in columns
    }
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(
            str(r.get(c) if r.get(c) is not None else "").ljust(widths[c]) for c in columns
        ))


def load_guidance(db: Backend) -> str:
    row = db.fetchone("SELECT value FROM config WHERE key = ?", (GUIDANCE_KEY,))
    if not row:
        die("no priority guidance configured; run `tm.py priority guidance set` "
            "or rerun `tm.py init` to seed the default")
    return row["value"]


def validate_status(s: str | None) -> None:
    if s is not None and s not in STATUSES:
        die(f"invalid status {s!r}; allowed: {', '.join(STATUSES)}")


def _resolve_assignee_uuid(db: Backend, name: str | None) -> str | None:
    if name is None or name.upper() == "NONE":
        return None
    return get_user_by_name(db, name)["id"]


def _build_blocks_graph(db: Backend) -> dict[int, set[int]]:
    adj: dict[int, set[int]] = {}
    for r in db.fetchall("SELECT idx, blocks FROM todos"):
        adj[r["idx"]] = set(parse_blocks_json(r["blocks"]))
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


def _validate_blocks(db: Backend, blocks: list[int], *,
                     this_idx: int | None = None) -> None:
    if not blocks:
        return
    if this_idx is not None and this_idx in blocks:
        die("a todo cannot block itself")
    for b in blocks:
        if not db.fetchone("SELECT 1 FROM todos WHERE idx = ?", (b,)):
            die(f"--blocks references unknown todo id {b}")
    if this_idx is None:
        return
    adj = _build_blocks_graph(db)
    adj[this_idx] = set(blocks)
    if _has_cycle(adj):
        die("proposed --blocks would introduce a cycle in the blocking DAG")


# ---------- commands ----------

def cmd_init(args: argparse.Namespace) -> None:
    db = open_db(resolve_db_uri(args), require_exists=False)
    require_meta_doc_db(db)
    db.executescript(TODO_SCHEMA_SQL)
    existing = db.fetchone("SELECT 1 AS x FROM config WHERE key = ?", (GUIDANCE_KEY,))
    if not existing:
        db.execute(
            "INSERT INTO config(key, value) VALUES (?, ?)",
            (GUIDANCE_KEY, DEFAULT_GUIDANCE),
        )
        print("seeded default priority guidance")
    db.commit()
    print(f"todo-manager tables ready in {resolve_db_uri(args)}")


def cmd_user_add(args: argparse.Namespace) -> None:
    db = connect(args)
    uid = new_uuid()
    idx = db.next_idx("users")
    try:
        db.execute("INSERT INTO users(id, idx, name) VALUES (?, ?, ?)",
                   (uid, idx, args.name))
    except db.integrity_error:
        die(f"user already exists: {args.name}")
    db.commit()
    print(f"user added: id={idx} name={args.name}")


def cmd_user_list(args: argparse.Namespace) -> None:
    db = connect(args)
    rows = db.fetchall("SELECT idx AS id, name FROM users ORDER BY name")
    emit(rows, args.format, columns=["id", "name"])


def cmd_user_delete(args: argparse.Namespace) -> None:
    db = connect(args)
    if args.id is not None:
        row = db.fetchone("SELECT id, idx FROM users WHERE idx = ?", (args.id,))
        if not row:
            die(f"no user with id {args.id}")
    else:
        row = get_user_by_name(db, args.name)
    db.execute("DELETE FROM users WHERE id = ?", (row["id"],))
    db.commit()
    print(f"user deleted: id={row['idx']}")


def cmd_todo_add(args: argparse.Namespace) -> None:
    db = connect(args)
    doc = db.fetchone(
        "SELECT id, idx, flavor, title FROM documents WHERE idx = ?", (args.doc_id,)
    )
    if not doc:
        die(f"no document with id {args.doc_id}")
    if doc["flavor"] != "todo":
        die(f"document {args.doc_id} has flavor {doc['flavor']!r}; "
            "todos only attach to docs with flavor 'todo'")
    assignee_uuid = _resolve_assignee_uuid(db, args.assignee)
    status = args.status or "backlog"
    validate_status(status)
    blocks = csv_ints(args.blocks)
    _validate_blocks(db, blocks)
    blocks_json = canonicalize_blocks(blocks)
    now = now_iso()
    tid = new_uuid()
    idx = db.next_idx("todos")
    db.execute(
        "INSERT INTO todos(id, idx, document_id, assignee_id, status, blocks, priority, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (tid, idx, doc["id"], assignee_uuid, status, blocks_json, args.priority, now, now),
    )
    db.commit()
    print(f"todo added: id={idx} document={args.doc_id} status={status}")


def cmd_todo_list(args: argparse.Namespace) -> None:
    db = connect(args)
    sql = (
        "SELECT t.idx AS id, d.idx AS document_id, d.title AS doc_title, "
        "u.name AS assignee, t.status, t.blocks, t.priority "
        "FROM todos t "
        "JOIN documents d ON d.id = t.document_id "
        "LEFT JOIN users u ON u.id = t.assignee_id"
    )
    params: list[Any] = []
    clauses: list[str] = []
    if args.status:
        validate_status(args.status)
        clauses.append("t.status = ?")
        params.append(args.status)
    if args.assignee is not None:
        if args.assignee.upper() == "NONE":
            clauses.append("t.assignee_id IS NULL")
        else:
            uid = get_user_by_name(db, args.assignee)["id"]
            clauses.append("t.assignee_id = ?")
            params.append(uid)
    if args.doc_id is not None:
        doc = db.fetchone("SELECT id FROM documents WHERE idx = ?", (args.doc_id,))
        if not doc:
            die(f"no document with id {args.doc_id}")
        clauses.append("t.document_id = ?")
        params.append(doc["id"])
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # Postgres needs NULLS LAST; SQLite tolerates it (since 3.30). Use it everywhere.
    sql += " ORDER BY t.priority DESC NULLS LAST, t.idx ASC"
    rows = db.fetchall(sql, params)
    emit(rows, args.format,
         columns=["id", "document_id", "doc_title", "assignee", "status", "blocks", "priority"])


def cmd_todo_top(args: argparse.Namespace) -> None:
    """Show the top-N highest-priority todos that aren't done.

    Default excludes `done`; pass --include-done to include it. Tied or
    NULL priorities sort after numbered ones; ties break by idx ASC.
    """
    db = connect(args)
    sql = (
        "SELECT t.idx AS id, d.idx AS document_id, d.title AS doc_title, "
        "u.name AS assignee, t.status, t.blocks, t.priority "
        "FROM todos t "
        "JOIN documents d ON d.id = t.document_id "
        "LEFT JOIN users u ON u.id = t.assignee_id"
    )
    params: list[Any] = []
    clauses: list[str] = []
    if not args.include_done:
        clauses.append("t.status != ?")
        params.append("done")
    if args.assignee is not None:
        if args.assignee.upper() == "NONE":
            clauses.append("t.assignee_id IS NULL")
        else:
            uid = get_user_by_name(db, args.assignee)["id"]
            clauses.append("t.assignee_id = ?")
            params.append(uid)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY t.priority DESC NULLS LAST, t.idx ASC"
    sql += f" LIMIT {int(args.limit)}"
    rows = db.fetchall(sql, params)
    emit(rows, args.format,
         columns=["id", "document_id", "doc_title", "assignee", "status", "blocks", "priority"])


def cmd_todo_show(args: argparse.Namespace) -> None:
    db = connect(args)
    todo = get_todo_by_idx(db, args.id)
    doc = db.fetchone(
        "SELECT idx, flavor, title, doc_path, content, summary FROM documents WHERE id = ?",
        (todo["document_id"],),
    )
    assignee = None
    if todo["assignee_id"] is not None:
        u = db.fetchone("SELECT name FROM users WHERE id = ?", (todo["assignee_id"],))
        assignee = u["name"] if u else None
    record = {
        "id": todo["idx"],
        "document_id": doc["idx"] if doc else None,
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
    db = connect(args)
    todo = get_todo_by_idx(db, args.id)
    updates: dict[str, Any] = {}
    if args.status is not None:
        validate_status(args.status)
        updates["status"] = args.status
    if args.assignee is not None:
        updates["assignee_id"] = _resolve_assignee_uuid(db, args.assignee)
    if args.priority is not None:
        updates["priority"] = args.priority
    if args.blocks is not None:
        if args.blocks.upper() == "NONE" or args.blocks == "":
            updates["blocks"] = None
        else:
            blocks = csv_ints(args.blocks)
            _validate_blocks(db, blocks, this_idx=todo["idx"])
            updates["blocks"] = canonicalize_blocks(blocks)
    if not updates:
        print("nothing to update")
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    db.execute(
        f"UPDATE todos SET {cols}, updated_at = ? WHERE id = ?",
        [*updates.values(), now_iso(), todo["id"]],
    )
    db.commit()
    print(f"todo updated: id={todo['idx']}")


def cmd_todo_delete(args: argparse.Namespace) -> None:
    db = connect(args)
    todo = get_todo_by_idx(db, args.id)
    db.execute("DELETE FROM todos WHERE id = ?", (todo["id"],))
    db.commit()
    print(f"todo deleted: id={args.id}")


def cmd_priority_set(args: argparse.Namespace) -> None:
    db = connect(args)
    todo = get_todo_by_idx(db, args.id)
    db.execute(
        "UPDATE todos SET priority = ?, updated_at = ? WHERE id = ?",
        (args.value, now_iso(), todo["id"]),
    )
    db.commit()
    print(f"priority set: id={args.id} value={args.value}")


def cmd_priority_guidance_get(args: argparse.Namespace) -> None:
    db = connect(args)
    text = load_guidance(db)
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
    db = connect(args)
    db.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (GUIDANCE_KEY, text),
    )
    db.commit()
    print(f"priority guidance set ({len(text)} chars)")


# ---------- argparse ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tm.py", description=__doc__.strip().splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_db(ap):
        ap.add_argument("--db", help=f"sqlite path or postgres:// URI (or set ${ENV_DB})")

    a = sub.add_parser("init", help="create users + todos tables on an existing meta-doc DB")
    add_db(a)
    a.set_defaults(func=cmd_init)

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
    x = pt.add_parser("top", help="show top-N highest-priority non-done todos")
    add_db(x)
    x.add_argument("--limit", type=int, default=1)
    x.add_argument("--assignee", help="user name, or NONE for unassigned")
    x.add_argument("--include-done", action="store_true",
                   help="include todos with status=done")
    x.add_argument("--format", choices=["table", "json"], default="table")
    x.set_defaults(func=cmd_todo_top)
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
