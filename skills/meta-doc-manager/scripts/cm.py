#!/usr/bin/env python3
"""meta-doc-manager CLI.

Track topics, modules, and meta-documents about a codebase in a SQLite index.
See SKILL.md in the parent directory for the full conceptual model and the
recommended workflows. See references/schema.md for the underlying schema.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from typing import Any, Iterable

SCHEMA_VERSION = "1"
MAX_DEPTH = 2  # depth 0/1/2 → 3 levels total
ENV_DB = "META_DOC_MANAGER_DB"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id   INTEGER REFERENCES topics(id) ON DELETE CASCADE,
  slug        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  description TEXT,
  depth       INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS modules (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL CHECK (kind IN ('file','dir','symbol')),
  path        TEXT NOT NULL,
  symbol      TEXT,
  description TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (kind, path, symbol)
);

CREATE TABLE IF NOT EXISTS topic_modules (
  topic_id   INTEGER NOT NULL REFERENCES topics(id)  ON DELETE CASCADE,
  module_id  INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  note       TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (topic_id, module_id)
);

CREATE TABLE IF NOT EXISTS documents (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  flavor      TEXT NOT NULL,
  title       TEXT NOT NULL,
  doc_path    TEXT NOT NULL,
  summary     TEXT,
  created_by  TEXT,
  source_ref  TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS document_topics (
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  topic_id    INTEGER NOT NULL REFERENCES topics(id)    ON DELETE CASCADE,
  PRIMARY KEY (document_id, topic_id)
);

CREATE TABLE IF NOT EXISTS document_modules (
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  module_id   INTEGER NOT NULL REFERENCES modules(id)   ON DELETE CASCADE,
  PRIMARY KEY (document_id, module_id)
);

CREATE INDEX IF NOT EXISTS idx_modules_path     ON modules(path);
CREATE INDEX IF NOT EXISTS idx_topics_parent    ON topics(parent_id);
CREATE INDEX IF NOT EXISTS idx_documents_flavor ON documents(flavor);
"""


# ---------- helpers ----------

def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        die(f"cannot derive slug from name {name!r}")
    return s


def connect(db_path: str, *, require_exists: bool = True) -> sqlite3.Connection:
    if require_exists and not os.path.exists(db_path):
        die(f"database not found: {db_path} (run `cm.py init --db {db_path}` first)")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def resolve_db(args: argparse.Namespace) -> str:
    db = getattr(args, "db", None) or os.environ.get(ENV_DB)
    if not db:
        die(f"--db is required (or set ${ENV_DB})")
    return db


def csv_ints(s: str | None) -> list[int]:
    if not s:
        return []
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            die(f"expected comma-separated integers, got {part!r}")
    return out


def csv_strs(s: str | None) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def emit(rows: Iterable[Any], fmt: str, *, columns: list[str] | None = None) -> None:
    rows = list(rows)
    if fmt == "json":
        if rows and isinstance(rows[0], sqlite3.Row):
            data = [dict(r) for r in rows]
        else:
            data = list(rows)
        print(json.dumps(data, indent=2, default=str))
        return
    if fmt == "paths":
        for r in rows:
            sym = r["symbol"] if "symbol" in r.keys() else None
            if sym:
                print(f"{r['path']}::{sym}")
            else:
                print(r["path"])
        return
    # table
    if not rows:
        print("(no rows)")
        return
    if columns is None:
        columns = list(rows[0].keys()) if isinstance(rows[0], sqlite3.Row) else list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c] if r[c] is not None else "")) for r in rows)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    print(header)
    print("  ".join("-" * widths[c] for c in columns))
    for r in rows:
        print("  ".join(str(r[c] if r[c] is not None else "").ljust(widths[c]) for c in columns))


# ---------- topic helpers ----------

def get_topic(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM topics WHERE slug = ?", (slug,)).fetchone()
    if not row:
        die(f"no such topic: {slug}")
    return row


def max_descendant_depth(conn: sqlite3.Connection, topic_id: int) -> int:
    """Return the deepest depth value among topic_id and its descendants."""
    cur = conn.execute(
        """
        WITH RECURSIVE sub(id, depth) AS (
          SELECT id, depth FROM topics WHERE id = ?
          UNION ALL
          SELECT t.id, t.depth FROM topics t JOIN sub s ON t.parent_id = s.id
        )
        SELECT MAX(depth) FROM sub
        """,
        (topic_id,),
    )
    return cur.fetchone()[0] or 0


# ---------- module helpers ----------

def find_module(conn: sqlite3.Connection, *, mid: int | None, kind: str | None,
                path: str | None, symbol: str | None) -> sqlite3.Row:
    if mid is not None:
        row = conn.execute("SELECT * FROM modules WHERE id = ?", (mid,)).fetchone()
        if not row:
            die(f"no module with id {mid}")
        return row
    if not (kind and path):
        die("specify --id, or --kind and --path (and --symbol for symbol modules)")
    row = conn.execute(
        "SELECT * FROM modules WHERE kind = ? AND path = ? AND symbol IS ?",
        (kind, path, symbol),
    ).fetchone()
    if not row:
        die(f"no matching module: kind={kind} path={path} symbol={symbol}")
    return row


# ---------- commands ----------

def cmd_init(args: argparse.Namespace) -> None:
    db = resolve_db(args)
    existed = os.path.exists(db)
    parent = os.path.dirname(os.path.abspath(db))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR IGNORE INTO config(key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    project_root = getattr(args, "project_root", None) or getattr(args, "repo_root", None)
    if project_root:
        if getattr(args, "repo_root", None) and not getattr(args, "project_root", None):
            print("warning: --repo-root is deprecated; use --project-root", file=sys.stderr)
        conn.execute(
            "INSERT INTO config(key, value) VALUES ('project_root', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (os.path.abspath(project_root),),
        )
    if args.docs_root:
        conn.execute(
            "INSERT INTO config(key, value) VALUES ('docs_root', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (os.path.abspath(args.docs_root),),
        )
    conn.commit()
    print(f"{'updated' if existed else 'initialized'} {db}")


def cmd_migrate(args: argparse.Namespace) -> None:
    """Run idempotent config-key migrations on an existing DB.

    Currently handles: repo_root -> project_root rename. Safe to re-run.
    """
    conn = connect(resolve_db(args))
    changes: list[str] = []

    old = conn.execute("SELECT value FROM config WHERE key='repo_root'").fetchone()
    new = conn.execute("SELECT value FROM config WHERE key='project_root'").fetchone()
    if old and not new:
        conn.execute(
            "INSERT INTO config(key, value) VALUES ('project_root', ?)",
            (old["value"],),
        )
        conn.execute("DELETE FROM config WHERE key='repo_root'")
        changes.append(f"repo_root -> project_root ({old['value']})")
    elif old and new:
        conn.execute("DELETE FROM config WHERE key='repo_root'")
        changes.append(f"dropped legacy repo_root (project_root already set to {new['value']!r})")

    conn.commit()
    if changes:
        for c in changes:
            print(f"migrated: {c}")
    else:
        print("no migrations needed")


def cmd_topic_add(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    slug = args.slug or slugify(args.name)
    parent_id, depth = None, 0
    if args.parent:
        parent = get_topic(conn, args.parent)
        parent_id = parent["id"]
        depth = parent["depth"] + 1
        if depth > MAX_DEPTH:
            die(f"cannot nest topic under {args.parent!r}: would exceed max depth ({MAX_DEPTH + 1} levels)")
    try:
        cur = conn.execute(
            "INSERT INTO topics(parent_id, slug, name, description, depth) VALUES (?, ?, ?, ?, ?)",
            (parent_id, slug, args.name, args.description, depth),
        )
    except sqlite3.IntegrityError as e:
        die(f"could not add topic: {e}")
    conn.commit()
    print(f"topic added: id={cur.lastrowid} slug={slug} depth={depth}")


def cmd_topic_list(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    rows = conn.execute(
        "SELECT id, parent_id, slug, name, description, depth FROM topics ORDER BY depth, slug"
    ).fetchall()
    if args.format == "tree":
        by_parent: dict[int | None, list[sqlite3.Row]] = {}
        for r in rows:
            by_parent.setdefault(r["parent_id"], []).append(r)

        def walk(parent_id: int | None, prefix: str = "") -> None:
            kids = sorted(by_parent.get(parent_id, []), key=lambda r: r["slug"])
            for i, k in enumerate(kids):
                last = i == len(kids) - 1
                branch = "└── " if last else "├── "
                desc = f"  — {k['description']}" if k["description"] else ""
                print(f"{prefix}{branch}{k['slug']}  ({k['name']}){desc}")
                walk(k["id"], prefix + ("    " if last else "│   "))

        walk(None)
        if not rows:
            print("(no topics)")
        return
    emit(rows, args.format, columns=["id", "parent_id", "slug", "name", "depth", "description"])


def cmd_topic_update(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    topic = get_topic(conn, args.slug)
    fields: dict[str, Any] = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.description is not None:
        fields["description"] = args.description
    if args.parent is not None:
        if args.parent.upper() == "NONE":
            new_parent_id, new_depth = None, 0
        else:
            if args.parent == args.slug:
                die("a topic cannot be its own parent")
            parent = get_topic(conn, args.parent)
            new_parent_id, new_depth = parent["id"], parent["depth"] + 1
        depth_shift = new_depth - topic["depth"]
        deepest = max_descendant_depth(conn, topic["id"])
        if deepest + depth_shift > MAX_DEPTH:
            die(f"reparent would push descendants past max depth ({MAX_DEPTH + 1} levels)")
        # ensure new parent is not a descendant of topic
        descendants = conn.execute(
            """
            WITH RECURSIVE sub(id) AS (
              SELECT id FROM topics WHERE id = ?
              UNION ALL
              SELECT t.id FROM topics t JOIN sub s ON t.parent_id = s.id
            )
            SELECT id FROM sub
            """,
            (topic["id"],),
        ).fetchall()
        if new_parent_id in {r["id"] for r in descendants}:
            die("cannot reparent under a descendant")
        fields["parent_id"] = new_parent_id
        fields["depth"] = new_depth
        # cascade depth shift
        if depth_shift:
            conn.execute(
                """
                WITH RECURSIVE sub(id) AS (
                  SELECT id FROM topics WHERE parent_id = ?
                  UNION ALL
                  SELECT t.id FROM topics t JOIN sub s ON t.parent_id = s.id
                )
                UPDATE topics SET depth = depth + ?, updated_at = datetime('now')
                WHERE id IN sub
                """,
                (topic["id"], depth_shift),
            )
    if not fields:
        print("nothing to update")
        return
    fields["updated_at"] = "datetime('now')"  # placeholder, handled below
    set_clause = ", ".join(f"{k} = ?" if k != "updated_at" else "updated_at = datetime('now')"
                           for k in fields)
    params = [v for k, v in fields.items() if k != "updated_at"]
    params.append(topic["id"])
    conn.execute(f"UPDATE topics SET {set_clause} WHERE id = ?", params)
    conn.commit()
    print(f"topic updated: {args.slug}")


def cmd_topic_delete(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    topic = get_topic(conn, args.slug)
    conn.execute("DELETE FROM topics WHERE id = ?", (topic["id"],))
    conn.commit()
    print(f"topic deleted: {args.slug}")


def cmd_module_add(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    if args.kind == "symbol" and not args.symbol:
        die("--symbol is required when --kind=symbol")
    if args.kind != "symbol" and args.symbol:
        die("--symbol is only allowed when --kind=symbol")
    try:
        cur = conn.execute(
            "INSERT INTO modules(kind, path, symbol, description) VALUES (?, ?, ?, ?)",
            (args.kind, args.path, args.symbol, args.description),
        )
    except sqlite3.IntegrityError as e:
        die(f"could not add module: {e}")
    conn.commit()
    print(f"module added: id={cur.lastrowid} {args.kind} {args.path}"
          + (f"::{args.symbol}" if args.symbol else ""))


def cmd_module_list(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    sql = "SELECT m.id, m.kind, m.path, m.symbol, m.description FROM modules m"
    params: list[Any] = []
    clauses: list[str] = []
    if args.topic:
        topic = get_topic(conn, args.topic)
        sql += " JOIN topic_modules tm ON tm.module_id = m.id"
        clauses.append("tm.topic_id = ?")
        params.append(topic["id"])
    if args.unassigned:
        sql += " LEFT JOIN topic_modules tm2 ON tm2.module_id = m.id"
        clauses.append("tm2.module_id IS NULL")
    if args.kind:
        clauses.append("m.kind = ?")
        params.append(args.kind)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY m.kind, m.path, m.symbol"
    rows = conn.execute(sql, params).fetchall()
    emit(rows, args.format, columns=["id", "kind", "path", "symbol", "description"])


def cmd_module_show(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    module = find_module(conn, mid=args.id, kind=args.kind, path=args.path, symbol=args.symbol)
    topics = [
        t["slug"]
        for t in conn.execute(
            "SELECT t.slug FROM topic_modules tm JOIN topics t ON t.id = tm.topic_id "
            "WHERE tm.module_id = ? ORDER BY t.slug",
            (module["id"],),
        ).fetchall()
    ]
    record = {
        "id": module["id"],
        "kind": module["kind"],
        "path": module["path"],
        "symbol": module["symbol"],
        "description": module["description"],
        "created_at": module["created_at"],
        "updated_at": module["updated_at"],
        "topics": topics,
    }
    if args.format == "json":
        print(json.dumps(record, indent=2, default=str))
    else:
        for k, v in record.items():
            if isinstance(v, list):
                print(f"{k}: {', '.join(str(x) for x in v) if v else '-'}")
            else:
                print(f"{k}: {v if v is not None else '-'}")


def cmd_module_update(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    row = conn.execute("SELECT id FROM modules WHERE id = ?", (args.id,)).fetchone()
    if not row:
        die(f"no module with id {args.id}")
    if args.description is None:
        print("nothing to update")
        return
    conn.execute(
        "UPDATE modules SET description = ?, updated_at = datetime('now') WHERE id = ?",
        (args.description, args.id),
    )
    conn.commit()
    print(f"module updated: id={args.id}")


def cmd_module_delete(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    conn.execute("DELETE FROM modules WHERE id = ?", (args.id,))
    conn.commit()
    print(f"module deleted: id={args.id}")


def cmd_module_assign(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    topic = get_topic(conn, args.topic)
    module = find_module(conn, mid=args.id, kind=args.kind, path=args.path, symbol=args.symbol)
    try:
        conn.execute(
            "INSERT INTO topic_modules(topic_id, module_id, note) VALUES (?, ?, ?)",
            (topic["id"], module["id"], args.note),
        )
    except sqlite3.IntegrityError:
        die(f"module {module['id']} already assigned to topic {args.topic}")
    conn.commit()
    print(f"assigned module {module['id']} → topic {args.topic}")


def cmd_module_unassign(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    topic = get_topic(conn, args.topic)
    module = find_module(conn, mid=args.id, kind=args.kind, path=args.path, symbol=args.symbol)
    cur = conn.execute(
        "DELETE FROM topic_modules WHERE topic_id = ? AND module_id = ?",
        (topic["id"], module["id"]),
    )
    conn.commit()
    if cur.rowcount:
        print(f"unassigned module {module['id']} from topic {args.topic}")
    else:
        print("(no such assignment)")


def cmd_doc_add(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    topic_slugs = csv_strs(args.topics)
    module_ids = csv_ints(args.modules)
    topic_ids = [get_topic(conn, s)["id"] for s in topic_slugs]
    # validate module ids exist
    for mid in module_ids:
        if not conn.execute("SELECT 1 FROM modules WHERE id = ?", (mid,)).fetchone():
            die(f"no module with id {mid}")
    cur = conn.execute(
        """INSERT INTO documents(flavor, title, doc_path, summary, created_by, source_ref)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (args.flavor, args.title, args.doc_path, args.summary, args.created_by, args.source_ref),
    )
    doc_id = cur.lastrowid
    for tid in topic_ids:
        conn.execute("INSERT INTO document_topics(document_id, topic_id) VALUES (?, ?)",
                     (doc_id, tid))
    for mid in module_ids:
        conn.execute("INSERT INTO document_modules(document_id, module_id) VALUES (?, ?)",
                     (doc_id, mid))
    conn.commit()
    print(f"document added: id={doc_id} flavor={args.flavor}")


def cmd_doc_list(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    sql = (
        "SELECT DISTINCT d.id, d.flavor, d.title, d.doc_path, d.created_by, d.source_ref, "
        "d.created_at FROM documents d"
    )
    params: list[Any] = []
    clauses: list[str] = []
    if args.topic:
        topic = get_topic(conn, args.topic)
        sql += " JOIN document_topics dt ON dt.document_id = d.id"
        clauses.append("dt.topic_id = ?")
        params.append(topic["id"])
    if args.module is not None:
        sql += " JOIN document_modules dm ON dm.document_id = d.id"
        clauses.append("dm.module_id = ?")
        params.append(args.module)
    if args.flavor:
        clauses.append("d.flavor = ?")
        params.append(args.flavor)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY d.created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    emit(rows, args.format,
         columns=["id", "flavor", "title", "doc_path", "created_by", "source_ref", "created_at"])


def cmd_doc_show(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    row = conn.execute(
        "SELECT id, flavor, title, doc_path, summary, created_by, source_ref, "
        "created_at, updated_at FROM documents WHERE id = ?",
        (args.id,),
    ).fetchone()
    if not row:
        die(f"no document with id {args.id}")
    topics = [
        t["slug"]
        for t in conn.execute(
            "SELECT t.slug FROM document_topics dt JOIN topics t ON t.id = dt.topic_id "
            "WHERE dt.document_id = ? ORDER BY t.slug",
            (args.id,),
        ).fetchall()
    ]
    modules = [
        m["id"]
        for m in conn.execute(
            "SELECT module_id AS id FROM document_modules WHERE document_id = ? ORDER BY module_id",
            (args.id,),
        ).fetchall()
    ]
    record = {
        "id": row["id"],
        "flavor": row["flavor"],
        "title": row["title"],
        "doc_path": row["doc_path"],
        "summary": row["summary"],
        "created_by": row["created_by"],
        "source_ref": row["source_ref"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "topics": topics,
        "modules": modules,
    }
    if args.format == "json":
        print(json.dumps(record, indent=2, default=str))
    else:
        for k, v in record.items():
            if isinstance(v, list):
                print(f"{k}: {', '.join(str(x) for x in v) if v else '-'}")
            else:
                print(f"{k}: {v if v is not None else '-'}")


def cmd_config_get(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    if args.key:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (args.key,)).fetchone()
        if not row:
            die(f"no config key {args.key!r}")
        print(row["value"])
        return
    rows = conn.execute("SELECT key, value FROM config ORDER BY key").fetchall()
    if args.format == "json":
        print(json.dumps({r["key"]: r["value"] for r in rows}, indent=2))
    else:
        for r in rows:
            print(f"{r['key']}={r['value']}")


def cmd_config_set(args: argparse.Namespace) -> None:
    if args.key in ("schema_version",):
        die(f"refusing to set reserved key {args.key!r}")
    conn = connect(resolve_db(args))
    value = os.path.abspath(args.value) if args.path else args.value
    conn.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (args.key, value),
    )
    conn.commit()
    print(f"config set: {args.key}={value}")


def cmd_doc_update(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    row = conn.execute("SELECT id FROM documents WHERE id = ?", (args.id,)).fetchone()
    if not row:
        die(f"no document with id {args.id}")
    updates: dict[str, Any] = {}
    for field in ("title", "summary", "doc_path", "flavor", "source_ref"):
        v = getattr(args, field.replace("-", "_"), None)
        if v is not None:
            updates[field] = v
    if updates:
        cols = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE documents SET {cols}, updated_at = datetime('now') WHERE id = ?",
            list(updates.values()) + [args.id],
        )
    for slug in csv_strs(args.add_topics):
        t = get_topic(conn, slug)
        conn.execute("INSERT OR IGNORE INTO document_topics(document_id, topic_id) VALUES (?, ?)",
                     (args.id, t["id"]))
    for slug in csv_strs(args.remove_topics):
        t = get_topic(conn, slug)
        conn.execute("DELETE FROM document_topics WHERE document_id = ? AND topic_id = ?",
                     (args.id, t["id"]))
    for mid in csv_ints(args.add_modules):
        conn.execute("INSERT OR IGNORE INTO document_modules(document_id, module_id) VALUES (?, ?)",
                     (args.id, mid))
    for mid in csv_ints(args.remove_modules):
        conn.execute("DELETE FROM document_modules WHERE document_id = ? AND module_id = ?",
                     (args.id, mid))
    conn.commit()
    print(f"document updated: id={args.id}")


def cmd_doc_delete(args: argparse.Namespace) -> None:
    conn = connect(resolve_db(args))
    conn.execute("DELETE FROM documents WHERE id = ?", (args.id,))
    conn.commit()
    print(f"document deleted: id={args.id}")


def _module_ancestor_ids(modules: list[sqlite3.Row]) -> dict[int, set[int]]:
    """For each module, return the set of module ids that semantically cover it
    (including itself). A `dir` covers anything whose path is under it. A `file`
    covers same-path symbol modules. Module-ancestors form the "module
    hierarchy" half of greedy matching.
    """
    by_id = {m["id"]: m for m in modules}
    ancestors: dict[int, set[int]] = {mid: {mid} for mid in by_id}
    for m in modules:
        mp = m["path"]
        mk = m["kind"]
        for a in modules:
            if a["id"] == m["id"]:
                continue
            if a["kind"] == "dir":
                ap = a["path"].rstrip("/")
                if mp == ap or mp.startswith(ap + "/"):
                    ancestors[m["id"]].add(a["id"])
            elif a["kind"] == "file" and mk == "symbol" and a["path"] == mp:
                ancestors[m["id"]].add(a["id"])
    return ancestors


def _topic_ancestors(topics: list[sqlite3.Row]) -> dict[int, set[int]]:
    """Return ancestor-or-self set for each topic (rooted via parent_id)."""
    parent = {t["id"]: t["parent_id"] for t in topics}
    out: dict[int, set[int]] = {}
    for tid in parent:
        chain = {tid}
        cur = parent[tid]
        while cur is not None:
            chain.add(cur)
            cur = parent.get(cur)
        out[tid] = chain
    return out


def _topic_descendants(topics: list[sqlite3.Row]) -> dict[int, set[int]]:
    """Return descendant-or-self set for each topic."""
    children: dict[int | None, list[int]] = {}
    for t in topics:
        children.setdefault(t["parent_id"], []).append(t["id"])
    out: dict[int, set[int]] = {}

    def walk(tid: int) -> set[int]:
        if tid in out:
            return out[tid]
        s = {tid}
        for c in children.get(tid, []):
            s |= walk(c)
        out[tid] = s
        return s

    for t in topics:
        walk(t["id"])
    return out


def cmd_select(args: argparse.Namespace) -> None:
    """Return modules matching criteria — the working-set query.

    Matching is *greedy down* in both hierarchies: a higher-level topic or
    module is treated as equivalent to enumerating its descendants. So a doc
    linked to topic `auth` covers modules assigned to `login-form` (descendant
    topic); a doc linked to `dir src/auth` covers `file src/auth/login.ts` and
    its symbol modules. Use `--strict` to disable greedy matching if you ever
    need exact-link semantics.
    """
    conn = connect(resolve_db(args))
    if args.topic and args.no_topic:
        die("--topic and --no-topic are mutually exclusive")

    topics = conn.execute(
        "SELECT id, parent_id, slug FROM topics"
    ).fetchall()
    topic_by_slug = {t["slug"]: t for t in topics}
    if args.topic and args.topic not in topic_by_slug:
        die(f"no such topic: {args.topic}")

    modules = conn.execute(
        "SELECT id, kind, path, symbol, description FROM modules"
    ).fetchall()
    if args.kind:
        modules = [m for m in modules if m["kind"] == args.kind]

    # topic_modules: module_id -> {topic_id}
    tm_rows = conn.execute("SELECT topic_id, module_id FROM topic_modules").fetchall()
    module_topics: dict[int, set[int]] = {}
    for r in tm_rows:
        module_topics.setdefault(r["module_id"], set()).add(r["topic_id"])

    # documents and links
    doc_rows = conn.execute("SELECT id, flavor FROM documents").fetchall()
    doc_flavor = {d["id"]: d["flavor"] for d in doc_rows}
    dt_rows = conn.execute("SELECT document_id, topic_id FROM document_topics").fetchall()
    doc_topics: dict[int, set[int]] = {}
    for r in dt_rows:
        doc_topics.setdefault(r["document_id"], set()).add(r["topic_id"])
    dm_rows = conn.execute("SELECT document_id, module_id FROM document_modules").fetchall()
    doc_modules: dict[int, set[int]] = {}
    for r in dm_rows:
        doc_modules.setdefault(r["document_id"], set()).add(r["module_id"])

    greedy = not args.strict
    mod_ancestors = (_module_ancestor_ids(modules) if greedy
                     else {m["id"]: {m["id"]} for m in modules})
    topic_ancestors = (_topic_ancestors(topics) if greedy
                       else {t["id"]: {t["id"]} for t in topics})
    topic_descendants = (_topic_descendants(topics) if greedy
                         else {t["id"]: {t["id"]} for t in topics})

    def covering_topics(m_id: int) -> set[int]:
        """Topics that, if a doc is linked to them, cover module m_id."""
        direct: set[int] = set()
        for anc in mod_ancestors.get(m_id, {m_id}):
            direct |= module_topics.get(anc, set())
        # In greedy mode, a doc on an ancestor topic also covers us.
        expanded: set[int] = set()
        for t in direct:
            expanded |= topic_ancestors.get(t, {t})
        return expanded if greedy else direct

    def has_doc_with_flavor(m_id: int, flavor: str) -> bool:
        cov_mods = mod_ancestors.get(m_id, {m_id})
        cov_topics = covering_topics(m_id)
        for d_id, fl in doc_flavor.items():
            if fl != flavor:
                continue
            if doc_modules.get(d_id, set()) & cov_mods:
                return True
            if doc_topics.get(d_id, set()) & cov_topics:
                return True
        return False

    def in_topic_scope(m_id: int, topic_slug: str) -> bool:
        t = topic_by_slug[topic_slug]
        scope = topic_descendants.get(t["id"], {t["id"]})
        # direct assignments (and module-ancestor assignments in greedy mode)
        direct: set[int] = set()
        for anc in mod_ancestors.get(m_id, {m_id}):
            direct |= module_topics.get(anc, set())
        return bool(direct & scope)

    def has_any_topic(m_id: int) -> bool:
        for anc in mod_ancestors.get(m_id, {m_id}):
            if module_topics.get(anc):
                return True
        return False

    out: list[sqlite3.Row] = []
    for m in modules:
        mid = m["id"]
        if args.topic and not in_topic_scope(mid, args.topic):
            continue
        if args.no_topic and has_any_topic(mid):
            continue
        if args.has_flavor and not has_doc_with_flavor(mid, args.has_flavor):
            continue
        if args.missing_flavor and has_doc_with_flavor(mid, args.missing_flavor):
            continue
        out.append(m)

    out.sort(key=lambda r: (r["kind"], r["path"], r["symbol"] or ""))
    emit(out, args.format, columns=["id", "kind", "path", "symbol", "description"])


# ---------- argparse wiring ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cm.py", description=__doc__.strip().splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_db(ap: argparse.ArgumentParser) -> None:
        ap.add_argument("--db", help=f"path to SQLite file (or set ${ENV_DB})")

    # init
    pi = sub.add_parser("init", help="create or update the database")
    add_db(pi)
    pi.add_argument("--project-root", help="path to the project (may contain multiple repos)")
    pi.add_argument("--repo-root", help=argparse.SUPPRESS)  # deprecated alias for --project-root
    pi.add_argument("--docs-root")
    pi.set_defaults(func=cmd_init)

    # migrate
    pmig = sub.add_parser("migrate", help="run pending config-key migrations on an existing DB")
    add_db(pmig)
    pmig.set_defaults(func=cmd_migrate)

    # topic
    pt = sub.add_parser("topic", help="manage topics").add_subparsers(dest="sub", required=True)
    a = pt.add_parser("add"); add_db(a)
    a.add_argument("--name", required=True); a.add_argument("--slug")
    a.add_argument("--parent"); a.add_argument("--description")
    a.set_defaults(func=cmd_topic_add)
    a = pt.add_parser("list"); add_db(a)
    a.add_argument("--format", choices=["tree", "json", "table"], default="tree")
    a.set_defaults(func=cmd_topic_list)
    a = pt.add_parser("update"); add_db(a)
    a.add_argument("--slug", required=True)
    a.add_argument("--name"); a.add_argument("--description")
    a.add_argument("--parent", help="new parent slug, or NONE to clear")
    a.set_defaults(func=cmd_topic_update)
    a = pt.add_parser("delete"); add_db(a)
    a.add_argument("--slug", required=True)
    a.set_defaults(func=cmd_topic_delete)

    # module
    pm = sub.add_parser("module", help="manage modules").add_subparsers(dest="sub", required=True)
    a = pm.add_parser("add"); add_db(a)
    a.add_argument("--kind", required=True, choices=["file", "dir", "symbol"])
    a.add_argument("--path", required=True); a.add_argument("--symbol")
    a.add_argument("--description")
    a.set_defaults(func=cmd_module_add)
    a = pm.add_parser("list"); add_db(a)
    a.add_argument("--topic"); a.add_argument("--unassigned", action="store_true")
    a.add_argument("--kind", choices=["file", "dir", "symbol"])
    a.add_argument("--format", choices=["table", "json", "paths"], default="table")
    a.set_defaults(func=cmd_module_list)
    a = pm.add_parser("show", help="show a single module including its topic assignments")
    add_db(a)
    a.add_argument("--id", type=int)
    a.add_argument("--kind", choices=["file", "dir", "symbol"])
    a.add_argument("--path"); a.add_argument("--symbol")
    a.add_argument("--format", choices=["table", "json"], default="table")
    a.set_defaults(func=cmd_module_show)
    a = pm.add_parser("update"); add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--description")
    a.set_defaults(func=cmd_module_update)
    a = pm.add_parser("delete"); add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.set_defaults(func=cmd_module_delete)
    a = pm.add_parser("assign"); add_db(a)
    a.add_argument("--id", type=int); a.add_argument("--kind", choices=["file", "dir", "symbol"])
    a.add_argument("--path"); a.add_argument("--symbol")
    a.add_argument("--topic", required=True); a.add_argument("--note")
    a.set_defaults(func=cmd_module_assign)
    a = pm.add_parser("unassign"); add_db(a)
    a.add_argument("--id", type=int); a.add_argument("--kind", choices=["file", "dir", "symbol"])
    a.add_argument("--path"); a.add_argument("--symbol")
    a.add_argument("--topic", required=True)
    a.set_defaults(func=cmd_module_unassign)

    # doc
    pd = sub.add_parser("doc", help="manage documents").add_subparsers(dest="sub", required=True)
    a = pd.add_parser("add"); add_db(a)
    a.add_argument("--flavor", required=True); a.add_argument("--title", required=True)
    a.add_argument("--doc-path", required=True); a.add_argument("--summary")
    a.add_argument("--created-by"); a.add_argument("--source-ref")
    a.add_argument("--topics", help="comma-separated topic slugs")
    a.add_argument("--modules", help="comma-separated module ids")
    a.set_defaults(func=cmd_doc_add)
    a = pd.add_parser("list"); add_db(a)
    a.add_argument("--flavor"); a.add_argument("--topic"); a.add_argument("--module", type=int)
    a.add_argument("--format", choices=["table", "json"], default="table")
    a.set_defaults(func=cmd_doc_list)
    a = pd.add_parser("show", help="show a single document including topics, modules, and timestamps")
    add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--format", choices=["table", "json"], default="table")
    a.set_defaults(func=cmd_doc_show)
    a = pd.add_parser("update"); add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--title"); a.add_argument("--summary"); a.add_argument("--doc-path")
    a.add_argument("--flavor"); a.add_argument("--source-ref")
    a.add_argument("--add-topics"); a.add_argument("--remove-topics")
    a.add_argument("--add-modules"); a.add_argument("--remove-modules")
    a.set_defaults(func=cmd_doc_update)
    a = pd.add_parser("delete"); add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.set_defaults(func=cmd_doc_delete)

    # config
    pc = sub.add_parser("config", help="read/write config keys").add_subparsers(dest="sub", required=True)
    a = pc.add_parser("get", help="get one config value, or all if --key omitted"); add_db(a)
    a.add_argument("--key")
    a.add_argument("--format", choices=["table", "json"], default="table")
    a.set_defaults(func=cmd_config_get)
    a = pc.add_parser("set", help="set or replace a config value"); add_db(a)
    a.add_argument("--key", required=True)
    a.add_argument("--value", required=True)
    a.add_argument("--path", action="store_true",
                   help="treat --value as a filesystem path; store its absolute form")
    a.set_defaults(func=cmd_config_set)

    # select
    a = sub.add_parser("select", help="select modules by topic/flavor criteria")
    add_db(a)
    a.add_argument("--topic"); a.add_argument("--no-topic", action="store_true")
    a.add_argument("--kind", choices=["file", "dir", "symbol"])
    a.add_argument("--has-flavor"); a.add_argument("--missing-flavor")
    a.add_argument("--strict", action="store_true",
                   help="disable greedy matching (exact topic/module links only)")
    a.add_argument("--format", choices=["paths", "json", "table"], default="paths")
    a.set_defaults(func=cmd_select)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
