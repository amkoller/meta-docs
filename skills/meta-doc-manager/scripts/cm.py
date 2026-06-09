#!/usr/bin/env python3
"""meta-doc-manager CLI.

Track topics, modules, and meta-documents about a codebase in a SQLite or
Postgres index. See SKILL.md for the conceptual model and references/schema.md
for the underlying schema.

Backend is selected by the form of --db: a value starting with
`postgresql://` or `postgres://` opens a Postgres connection; anything else is
treated as a SQLite file path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Iterable

# db.py lives next to this script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import (  # noqa: E402
    META_DOC_SCHEMA_SQL,
    Backend,
    die,
    new_uuid,
    now_iso,
    open_db,
)

MAX_DEPTH = 2  # depth 0/1/2 → 3 levels total
ENV_DB = "META_DOC_MANAGER_DB"


# ---------- helpers ----------

def slugify(name: str) -> str:
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        die(f"cannot derive slug from name {name!r}")
    return s


def resolve_db_uri(args: argparse.Namespace) -> str:
    db = getattr(args, "db", None) or os.environ.get(ENV_DB)
    if not db:
        die(f"--db is required (or set ${ENV_DB})")
    return db


def connect(args: argparse.Namespace, *, require_exists: bool = True) -> Backend:
    return open_db(resolve_db_uri(args), require_exists=require_exists)


def csv_ints(s: str | None) -> list[int]:
    if not s:
        return []
    out: list[int] = []
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


def in_placeholders(n: int) -> str:
    return ",".join(["?"] * n)


def emit(rows: Iterable[dict], fmt: str, *, columns: list[str] | None = None) -> None:
    rows = list(rows)
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=str))
        return
    if fmt == "paths":
        for r in rows:
            sym = r.get("symbol")
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


# ---------- topic helpers ----------

def get_topic_by_slug(db: Backend, slug: str) -> dict:
    row = db.fetchone("SELECT * FROM topics WHERE slug = ?", (slug,))
    if not row:
        die(f"no such topic: {slug}")
    return row


def get_topic_by_idx(db: Backend, idx: int) -> dict:
    row = db.fetchone("SELECT * FROM topics WHERE idx = ?", (idx,))
    if not row:
        die(f"no topic with idx {idx}")
    return row


def load_topic_tree(db: Backend) -> dict[str, dict]:
    """Return {uuid: topic_row} for every topic. Caller walks parent_id in
    Python to find ancestors/descendants — depth is capped at 3, so the tree
    is always tiny.
    """
    rows = db.fetchall("SELECT id, idx, parent_id, slug, depth FROM topics")
    return {r["id"]: r for r in rows}


def descendants_of(tree: dict[str, dict], root_id: str) -> set[str]:
    children: dict[str | None, list[str]] = {}
    for tid, row in tree.items():
        children.setdefault(row["parent_id"], []).append(tid)
    out: set[str] = set()
    stack = [root_id]
    while stack:
        tid = stack.pop()
        if tid in out:
            continue
        out.add(tid)
        stack.extend(children.get(tid, []))
    return out


def ancestors_of(tree: dict[str, dict], leaf_id: str) -> set[str]:
    out: set[str] = set()
    cur: str | None = leaf_id
    while cur is not None:
        out.add(cur)
        cur = tree[cur]["parent_id"] if cur in tree else None
    return out


def max_depth_in_subtree(tree: dict[str, dict], root_id: str) -> int:
    return max(tree[t]["depth"] for t in descendants_of(tree, root_id))


# ---------- module helpers ----------

def find_module(db: Backend, *, idx: int | None, kind: str | None,
                path: str | None, symbol: str | None) -> dict:
    if idx is not None:
        row = db.fetchone("SELECT * FROM modules WHERE idx = ?", (idx,))
        if not row:
            die(f"no module with idx {idx}")
        return row
    if not (kind and path):
        die("specify --id, or --kind and --path (and --symbol for symbol modules)")
    row = db.fetchone(
        "SELECT * FROM modules WHERE kind = ? AND path = ? AND symbol IS NOT DISTINCT FROM ?",
        (kind, path, symbol),
    )
    if not row:
        die(f"no matching module: kind={kind} path={path} symbol={symbol}")
    return row


def resolve_module_idxs_to_ids(db: Backend, idxs: list[int]) -> list[str]:
    if not idxs:
        return []
    rows = db.fetchall(
        f"SELECT id, idx FROM modules WHERE idx IN ({in_placeholders(len(idxs))})",
        idxs,
    )
    found = {r["idx"]: r["id"] for r in rows}
    missing = [i for i in idxs if i not in found]
    if missing:
        die(f"no module(s) with idx: {missing}")
    return [found[i] for i in idxs]


# ---------- commands ----------

def cmd_init(args: argparse.Namespace) -> None:
    db = open_db(resolve_db_uri(args), require_exists=False)
    db.executescript(META_DOC_SCHEMA_SQL)
    project_root = getattr(args, "project_root", None)
    if project_root:
        _set_config(db, "project_root", os.path.abspath(project_root))
    if args.docs_root:
        _set_config(db, "docs_root", os.path.abspath(args.docs_root))
    db.commit()
    print(f"initialized {resolve_db_uri(args)}")


def _set_config(db: Backend, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def cmd_topic_add(args: argparse.Namespace) -> None:
    db = connect(args)
    slug = args.slug or slugify(args.name)
    parent_id: str | None = None
    depth = 0
    if args.parent:
        parent = get_topic_by_slug(db, args.parent)
        parent_id = parent["id"]
        depth = parent["depth"] + 1
        if depth > MAX_DEPTH:
            die(f"cannot nest topic under {args.parent!r}: would exceed max depth "
                f"({MAX_DEPTH + 1} levels)")
    now = now_iso()
    tid = new_uuid()
    idx = db.next_idx("topics")
    try:
        db.execute(
            "INSERT INTO topics(id, idx, parent_id, slug, name, description, depth, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, idx, parent_id, slug, args.name, args.description, depth, now, now),
        )
    except db.integrity_error as e:
        die(f"could not add topic: {e}")
    db.commit()
    print(f"topic added: id={idx} slug={slug} depth={depth}")


def cmd_topic_list(args: argparse.Namespace) -> None:
    db = connect(args)
    rows = db.fetchall(
        "SELECT t.id AS uuid, t.idx AS id, t.parent_id AS parent_uuid, "
        "p.slug AS parent_slug, t.slug, t.name, t.description, t.depth "
        "FROM topics t LEFT JOIN topics p ON p.id = t.parent_id "
        "ORDER BY t.depth, t.slug"
    )
    if args.format == "tree":
        by_parent: dict[str | None, list[dict]] = {}
        for r in rows:
            by_parent.setdefault(r["parent_uuid"], []).append(r)

        def walk(parent_uuid: str | None, prefix: str = "") -> None:
            kids = sorted(by_parent.get(parent_uuid, []), key=lambda r: r["slug"])
            for i, k in enumerate(kids):
                last = i == len(kids) - 1
                branch = "└── " if last else "├── "
                desc = f"  — {k['description']}" if k["description"] else ""
                print(f"{prefix}{branch}{k['slug']}  ({k['name']}){desc}")
                walk(k["uuid"], prefix + ("    " if last else "│   "))

        walk(None)
        if not rows:
            print("(no topics)")
        return
    emit(rows, args.format,
         columns=["id", "parent_slug", "slug", "name", "depth", "description"])


def cmd_topic_update(args: argparse.Namespace) -> None:
    db = connect(args)
    topic = get_topic_by_slug(db, args.slug)
    fields: dict[str, Any] = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.description is not None:
        fields["description"] = args.description
    if args.parent is not None:
        tree = load_topic_tree(db)
        if args.parent.upper() == "NONE":
            new_parent_id, new_depth = None, 0
        else:
            if args.parent == args.slug:
                die("a topic cannot be its own parent")
            parent = get_topic_by_slug(db, args.parent)
            new_parent_id, new_depth = parent["id"], parent["depth"] + 1
            if new_parent_id in descendants_of(tree, topic["id"]):
                die("cannot reparent under a descendant")
        depth_shift = new_depth - topic["depth"]
        deepest = max_depth_in_subtree(tree, topic["id"])
        if deepest + depth_shift > MAX_DEPTH:
            die(f"reparent would push descendants past max depth "
                f"({MAX_DEPTH + 1} levels)")
        fields["parent_id"] = new_parent_id
        fields["depth"] = new_depth
        if depth_shift:
            subtree = descendants_of(tree, topic["id"]) - {topic["id"]}
            if subtree:
                ids = list(subtree)
                db.execute(
                    f"UPDATE topics SET depth = depth + ?, updated_at = ? "
                    f"WHERE id IN ({in_placeholders(len(ids))})",
                    [depth_shift, now_iso(), *ids],
                )
    if not fields:
        print("nothing to update")
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
    params = [*fields.values(), now_iso(), topic["id"]]
    db.execute(f"UPDATE topics SET {set_clause} WHERE id = ?", params)
    db.commit()
    print(f"topic updated: {args.slug}")


def cmd_topic_rename_slug(args: argparse.Namespace) -> None:
    db = connect(args)
    topic = get_topic_by_slug(db, args.slug)
    new_slug = args.new_slug
    if new_slug == args.slug:
        print("nothing to update")
        return
    if db.fetchone("SELECT id FROM topics WHERE slug = ?", (new_slug,)):
        die(f"slug already in use: {new_slug}")
    try:
        db.execute(
            "UPDATE topics SET slug = ?, updated_at = ? WHERE id = ?",
            (new_slug, now_iso(), topic["id"]),
        )
    except db.integrity_error as e:
        die(f"could not rename slug: {e}")
    db.commit()
    print(f"topic slug renamed: {args.slug} -> {new_slug}")


def cmd_topic_delete(args: argparse.Namespace) -> None:
    db = connect(args)
    topic = get_topic_by_slug(db, args.slug)
    db.execute("DELETE FROM topics WHERE id = ?", (topic["id"],))
    db.commit()
    print(f"topic deleted: {args.slug}")


# ---------- modules ----------

VALID_KINDS = ("file", "dir", "symbol")


def cmd_module_add(args: argparse.Namespace) -> None:
    db = connect(args)
    if args.kind not in VALID_KINDS:
        die(f"--kind must be one of {VALID_KINDS}")
    if args.kind == "symbol" and not args.symbol:
        die("--symbol is required when --kind=symbol")
    if args.kind != "symbol" and args.symbol:
        die("--symbol is only allowed when --kind=symbol")
    mid = new_uuid()
    idx = db.next_idx("modules")
    now = now_iso()
    try:
        db.execute(
            "INSERT INTO modules(id, idx, kind, path, symbol, description, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, idx, args.kind, args.path, args.symbol, args.description, now, now),
        )
    except db.integrity_error as e:
        die(f"could not add module: {e}")
    db.commit()
    suffix = f"::{args.symbol}" if args.symbol else ""
    print(f"module added: id={idx} {args.kind} {args.path}{suffix}")


def cmd_module_list(args: argparse.Namespace) -> None:
    db = connect(args)
    sql = ("SELECT m.id AS uuid, m.idx AS id, m.kind, m.path, m.symbol, "
           "m.description FROM modules m")
    params: list[Any] = []
    clauses: list[str] = []
    if args.topic:
        topic = get_topic_by_slug(db, args.topic)
        sql += " JOIN topic_modules tm ON tm.module_id = m.id"
        clauses.append("tm.topic_id = ?")
        params.append(topic["id"])
    if args.unassigned:
        sql += " LEFT JOIN topic_modules tm2 ON tm2.module_id = m.id"
        clauses.append("tm2.module_id IS NULL")
    if args.kind:
        if args.kind not in VALID_KINDS:
            die(f"--kind must be one of {VALID_KINDS}")
        clauses.append("m.kind = ?")
        params.append(args.kind)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY m.kind, m.path, m.symbol"
    rows = db.fetchall(sql, params)
    emit(rows, args.format, columns=["id", "kind", "path", "symbol", "description"])


def cmd_module_show(args: argparse.Namespace) -> None:
    db = connect(args)
    module = find_module(db, idx=args.id, kind=args.kind, path=args.path, symbol=args.symbol)
    topics = [
        t["slug"]
        for t in db.fetchall(
            "SELECT t.slug FROM topic_modules tm JOIN topics t ON t.id = tm.topic_id "
            "WHERE tm.module_id = ? ORDER BY t.slug",
            (module["id"],),
        )
    ]
    record = {
        "id": module["idx"],
        "uuid": module["id"],
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
    db = connect(args)
    module = find_module(db, idx=args.id, kind=None, path=None, symbol=None)
    if args.description is None:
        print("nothing to update")
        return
    db.execute(
        "UPDATE modules SET description = ?, updated_at = ? WHERE id = ?",
        (args.description, now_iso(), module["id"]),
    )
    db.commit()
    print(f"module updated: id={args.id}")


def cmd_module_delete(args: argparse.Namespace) -> None:
    db = connect(args)
    module = find_module(db, idx=args.id, kind=None, path=None, symbol=None)
    db.execute("DELETE FROM modules WHERE id = ?", (module["id"],))
    db.commit()
    print(f"module deleted: id={args.id}")


def cmd_module_assign(args: argparse.Namespace) -> None:
    db = connect(args)
    topic = get_topic_by_slug(db, args.topic)
    module = find_module(db, idx=args.id, kind=args.kind, path=args.path, symbol=args.symbol)
    try:
        db.execute(
            "INSERT INTO topic_modules(topic_id, module_id, note, created_at) "
            "VALUES (?, ?, ?, ?)",
            (topic["id"], module["id"], args.note, now_iso()),
        )
    except db.integrity_error:
        die(f"module {module['idx']} already assigned to topic {args.topic}")
    db.commit()
    print(f"assigned module {module['idx']} → topic {args.topic}")


def cmd_module_unassign(args: argparse.Namespace) -> None:
    db = connect(args)
    topic = get_topic_by_slug(db, args.topic)
    module = find_module(db, idx=args.id, kind=args.kind, path=args.path, symbol=args.symbol)
    cur = db.execute(
        "DELETE FROM topic_modules WHERE topic_id = ? AND module_id = ?",
        (topic["id"], module["id"]),
    )
    db.commit()
    if cur.rowcount:
        print(f"unassigned module {module['idx']} from topic {args.topic}")
    else:
        print("(no such assignment)")


# ---------- documents ----------

def _resolve_doc_body(args: argparse.Namespace) -> tuple[str | None, str | None]:
    provided = [
        ("--doc-path",     getattr(args, "doc_path", None)),
        ("--content",      getattr(args, "content", None)),
        ("--content-file", getattr(args, "content_file", None)),
    ]
    chosen = [(flag, val) for flag, val in provided if val is not None]
    if len(chosen) == 0:
        die("one of --doc-path, --content, --content-file is required")
    if len(chosen) > 1:
        die(f"--doc-path, --content, --content-file are mutually exclusive "
            f"(got {', '.join(f for f,_ in chosen)})")
    flag, val = chosen[0]
    if flag == "--doc-path":
        return val, None
    if flag == "--content":
        return None, val
    try:
        with open(val, "r", encoding="utf-8") as f:
            return None, f.read()
    except OSError as e:
        die(f"could not read --content-file {val!r}: {e}")
        return None, None  # unreachable, satisfy type checker


def _check_doc_body_invariant(doc_path: str | None, content: str | None) -> None:
    """Enforce doc_path XOR content in the application layer (the DB no longer
    has a CHECK constraint for it)."""
    has_path = doc_path is not None
    has_content = content is not None
    if has_path == has_content:
        die("internal: exactly one of doc_path/content must be set")


def cmd_doc_add(args: argparse.Namespace) -> None:
    db = connect(args)
    topic_slugs = csv_strs(args.topics)
    module_idxs = csv_ints(args.modules)
    topic_ids = [get_topic_by_slug(db, s)["id"] for s in topic_slugs]
    module_ids = resolve_module_idxs_to_ids(db, module_idxs)
    doc_path, content = _resolve_doc_body(args)
    _check_doc_body_invariant(doc_path, content)
    did = new_uuid()
    idx = db.next_idx("documents")
    now = now_iso()
    db.execute(
        "INSERT INTO documents(id, idx, flavor, title, doc_path, content, summary, "
        "created_by, source_ref, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (did, idx, args.flavor, args.title, doc_path, content, args.summary,
         args.created_by, args.source_ref, now, now),
    )
    for tid in topic_ids:
        db.execute(
            "INSERT INTO document_topics(document_id, topic_id) VALUES (?, ?)",
            (did, tid),
        )
    for mid in module_ids:
        db.execute(
            "INSERT INTO document_modules(document_id, module_id) VALUES (?, ?)",
            (did, mid),
        )
    db.commit()
    print(f"document added: id={idx} flavor={args.flavor}")


def cmd_doc_list(args: argparse.Namespace) -> None:
    db = connect(args)
    sql = (
        "SELECT DISTINCT d.id AS uuid, d.idx AS id, d.flavor, d.title, d.doc_path, "
        "d.created_by, d.source_ref, d.created_at FROM documents d"
    )
    params: list[Any] = []
    clauses: list[str] = []
    if args.topic:
        topic = get_topic_by_slug(db, args.topic)
        sql += " JOIN document_topics dt ON dt.document_id = d.id"
        clauses.append("dt.topic_id = ?")
        params.append(topic["id"])
    if args.module is not None:
        module = find_module(db, idx=args.module, kind=None, path=None, symbol=None)
        sql += " JOIN document_modules dm ON dm.document_id = d.id"
        clauses.append("dm.module_id = ?")
        params.append(module["id"])
    if args.flavor:
        clauses.append("d.flavor = ?")
        params.append(args.flavor)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY d.created_at DESC"
    rows = db.fetchall(sql, params)
    emit(rows, args.format,
         columns=["id", "flavor", "title", "doc_path", "created_by", "source_ref", "created_at"])


def cmd_doc_flavors(args: argparse.Namespace) -> None:
    db = connect(args)
    rows = db.fetchall(
        "SELECT flavor, COUNT(*) AS count FROM documents "
        "GROUP BY flavor ORDER BY count DESC, flavor ASC"
    )
    emit(rows, args.format, columns=["flavor", "count"])


def cmd_doc_show(args: argparse.Namespace) -> None:
    db = connect(args)
    row = db.fetchone(
        "SELECT id, idx, flavor, title, doc_path, content, summary, created_by, "
        "source_ref, created_at, updated_at FROM documents WHERE idx = ?",
        (args.id,),
    )
    if not row:
        die(f"no document with id {args.id}")
    topics = [
        t["slug"]
        for t in db.fetchall(
            "SELECT t.slug FROM document_topics dt JOIN topics t ON t.id = dt.topic_id "
            "WHERE dt.document_id = ? ORDER BY t.slug",
            (row["id"],),
        )
    ]
    modules = [
        m["idx"]
        for m in db.fetchall(
            "SELECT m.idx FROM document_modules dm JOIN modules m ON m.id = dm.module_id "
            "WHERE dm.document_id = ? ORDER BY m.idx",
            (row["id"],),
        )
    ]
    record = {
        "id": row["idx"],
        "uuid": row["id"],
        "flavor": row["flavor"],
        "title": row["title"],
        "doc_path": row["doc_path"],
        "content": row["content"],
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
            elif k == "content" and v is not None:
                preview = v if len(v) <= 2048 else v[:2048] + "\n…(truncated; use --format json for full body)"
                print(f"content: |\n{preview}")
            else:
                print(f"{k}: {v if v is not None else '-'}")


def cmd_doc_update(args: argparse.Namespace) -> None:
    db = connect(args)
    row = db.fetchone("SELECT id, idx, doc_path, content FROM documents WHERE idx = ?",
                      (args.id,))
    if not row:
        die(f"no document with id {args.id}")
    doc_uuid = row["id"]
    updates: dict[str, Any] = {}
    for field in ("title", "summary", "flavor", "source_ref"):
        v = getattr(args, field.replace("-", "_"), None)
        if v is not None:
            updates[field] = v
    body_flags = [
        ("--doc-path",     getattr(args, "doc_path", None)),
        ("--content",      getattr(args, "content", None)),
        ("--content-file", getattr(args, "content_file", None)),
    ]
    body_chosen = [(f, v) for f, v in body_flags if v is not None]
    if len(body_chosen) > 1:
        die(f"--doc-path, --content, --content-file are mutually exclusive "
            f"(got {', '.join(f for f,_ in body_chosen)})")
    if body_chosen:
        flag, val = body_chosen[0]
        if flag == "--doc-path":
            updates["doc_path"], updates["content"] = val, None
        elif flag == "--content":
            updates["doc_path"], updates["content"] = None, val
        else:
            try:
                with open(val, "r", encoding="utf-8") as f:
                    updates["doc_path"], updates["content"] = None, f.read()
            except OSError as e:
                die(f"could not read --content-file {val!r}: {e}")
    if "doc_path" in updates or "content" in updates:
        new_path = updates.get("doc_path", row["doc_path"])
        new_content = updates.get("content", row["content"])
        _check_doc_body_invariant(new_path, new_content)
    if updates:
        cols = ", ".join(f"{k} = ?" for k in updates)
        db.execute(
            f"UPDATE documents SET {cols}, updated_at = ? WHERE id = ?",
            [*updates.values(), now_iso(), doc_uuid],
        )
    for slug in csv_strs(args.add_topics):
        t = get_topic_by_slug(db, slug)
        db.execute(
            "INSERT INTO document_topics(document_id, topic_id) VALUES (?, ?) "
            "ON CONFLICT (document_id, topic_id) DO NOTHING",
            (doc_uuid, t["id"]),
        )
    for slug in csv_strs(args.remove_topics):
        t = get_topic_by_slug(db, slug)
        db.execute("DELETE FROM document_topics WHERE document_id = ? AND topic_id = ?",
                   (doc_uuid, t["id"]))
    for mid_int in csv_ints(args.add_modules):
        mid_uuid = resolve_module_idxs_to_ids(db, [mid_int])[0]
        db.execute(
            "INSERT INTO document_modules(document_id, module_id) VALUES (?, ?) "
            "ON CONFLICT (document_id, module_id) DO NOTHING",
            (doc_uuid, mid_uuid),
        )
    for mid_int in csv_ints(args.remove_modules):
        mid_uuid = resolve_module_idxs_to_ids(db, [mid_int])[0]
        db.execute("DELETE FROM document_modules WHERE document_id = ? AND module_id = ?",
                   (doc_uuid, mid_uuid))
    db.commit()
    print(f"document updated: id={args.id}")


def cmd_doc_delete(args: argparse.Namespace) -> None:
    db = connect(args)
    row = db.fetchone("SELECT id FROM documents WHERE idx = ?", (args.id,))
    if not row:
        die(f"no document with id {args.id}")
    db.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
    db.commit()
    print(f"document deleted: id={args.id}")


# ---------- config ----------

def cmd_config_get(args: argparse.Namespace) -> None:
    db = connect(args)
    if args.key:
        row = db.fetchone("SELECT value FROM config WHERE key = ?", (args.key,))
        if not row:
            die(f"no config key {args.key!r}")
        print(row["value"])
        return
    rows = db.fetchall("SELECT key, value FROM config ORDER BY key")
    if args.format == "json":
        print(json.dumps({r["key"]: r["value"] for r in rows}, indent=2))
    else:
        for r in rows:
            print(f"{r['key']}={r['value']}")


def cmd_config_set(args: argparse.Namespace) -> None:
    db = connect(args)
    value = os.path.abspath(args.value) if args.path else args.value
    _set_config(db, args.key, value)
    db.commit()
    print(f"config set: {args.key}={value}")


# ---------- select ----------

def _module_ancestor_ids(modules: list[dict]) -> dict[str, set[str]]:
    by_id = {m["id"]: m for m in modules}
    ancestors: dict[str, set[str]] = {mid: {mid} for mid in by_id}
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


def _topic_ancestors(topics: list[dict]) -> dict[str, set[str]]:
    parent = {t["id"]: t["parent_id"] for t in topics}
    out: dict[str, set[str]] = {}
    for tid in parent:
        chain = {tid}
        cur = parent[tid]
        while cur is not None:
            chain.add(cur)
            cur = parent.get(cur)
        out[tid] = chain
    return out


def _topic_descendants(topics: list[dict]) -> dict[str, set[str]]:
    children: dict[str | None, list[str]] = {}
    for t in topics:
        children.setdefault(t["parent_id"], []).append(t["id"])
    out: dict[str, set[str]] = {}

    def walk(tid: str) -> set[str]:
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
    db = connect(args)
    if args.topic and args.no_topic:
        die("--topic and --no-topic are mutually exclusive")
    topics = db.fetchall("SELECT id, parent_id, slug FROM topics")
    topic_by_slug = {t["slug"]: t for t in topics}
    if args.topic and args.topic not in topic_by_slug:
        die(f"no such topic: {args.topic}")
    modules = db.fetchall(
        "SELECT id, idx, kind, path, symbol, description FROM modules"
    )
    if args.kind:
        if args.kind not in VALID_KINDS:
            die(f"--kind must be one of {VALID_KINDS}")
        modules = [m for m in modules if m["kind"] == args.kind]

    tm_rows = db.fetchall("SELECT topic_id, module_id FROM topic_modules")
    module_topics: dict[str, set[str]] = {}
    for r in tm_rows:
        module_topics.setdefault(r["module_id"], set()).add(r["topic_id"])

    doc_rows = db.fetchall("SELECT id, flavor FROM documents")
    doc_flavor = {d["id"]: d["flavor"] for d in doc_rows}
    dt_rows = db.fetchall("SELECT document_id, topic_id FROM document_topics")
    doc_topics: dict[str, set[str]] = {}
    for r in dt_rows:
        doc_topics.setdefault(r["document_id"], set()).add(r["topic_id"])
    dm_rows = db.fetchall("SELECT document_id, module_id FROM document_modules")
    doc_modules: dict[str, set[str]] = {}
    for r in dm_rows:
        doc_modules.setdefault(r["document_id"], set()).add(r["module_id"])

    greedy = not args.strict
    mod_ancestors = (_module_ancestor_ids(modules) if greedy
                     else {m["id"]: {m["id"]} for m in modules})
    topic_ancestors = (_topic_ancestors(topics) if greedy
                       else {t["id"]: {t["id"]} for t in topics})
    topic_descendants = (_topic_descendants(topics) if greedy
                         else {t["id"]: {t["id"]} for t in topics})

    def covering_topics(m_id: str) -> set[str]:
        direct: set[str] = set()
        for anc in mod_ancestors.get(m_id, {m_id}):
            direct |= module_topics.get(anc, set())
        if not greedy:
            return direct
        expanded: set[str] = set()
        for t in direct:
            expanded |= topic_ancestors.get(t, {t})
        return expanded

    def has_doc_with_flavor(m_id: str, flavor: str) -> bool:
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

    def in_topic_scope(m_id: str, topic_slug: str) -> bool:
        t = topic_by_slug[topic_slug]
        scope = topic_descendants.get(t["id"], {t["id"]})
        direct: set[str] = set()
        for anc in mod_ancestors.get(m_id, {m_id}):
            direct |= module_topics.get(anc, set())
        return bool(direct & scope)

    def has_any_topic(m_id: str) -> bool:
        for anc in mod_ancestors.get(m_id, {m_id}):
            if module_topics.get(anc):
                return True
        return False

    out: list[dict] = []
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
        out.append({
            "id": m["idx"],
            "uuid": m["id"],
            "kind": m["kind"],
            "path": m["path"],
            "symbol": m["symbol"],
            "description": m["description"],
        })

    out.sort(key=lambda r: (r["kind"], r["path"], r["symbol"] or ""))
    emit(out, args.format, columns=["id", "kind", "path", "symbol", "description"])


# ---------- argparse ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cm.py", description=__doc__.strip().splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_db(ap: argparse.ArgumentParser) -> None:
        ap.add_argument("--db", help=f"sqlite path or postgres:// URI (or set ${ENV_DB})")

    pi = sub.add_parser("init", help="create or update the database")
    add_db(pi)
    pi.add_argument("--project-root", help="path to the project (may contain multiple repos)")
    pi.add_argument("--docs-root")
    pi.set_defaults(func=cmd_init)

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
    a = pt.add_parser("rename-slug", help="rename a topic's stable slug identifier")
    add_db(a)
    a.add_argument("--slug", required=True, help="current slug")
    a.add_argument("--new-slug", required=True, help="new slug")
    a.set_defaults(func=cmd_topic_rename_slug)
    a = pt.add_parser("delete"); add_db(a)
    a.add_argument("--slug", required=True)
    a.set_defaults(func=cmd_topic_delete)

    pm = sub.add_parser("module", help="manage modules").add_subparsers(dest="sub", required=True)
    a = pm.add_parser("add"); add_db(a)
    a.add_argument("--kind", required=True, choices=list(VALID_KINDS))
    a.add_argument("--path", required=True); a.add_argument("--symbol")
    a.add_argument("--description")
    a.set_defaults(func=cmd_module_add)
    a = pm.add_parser("list"); add_db(a)
    a.add_argument("--topic"); a.add_argument("--unassigned", action="store_true")
    a.add_argument("--kind", choices=list(VALID_KINDS))
    a.add_argument("--format", choices=["table", "json", "paths"], default="table")
    a.set_defaults(func=cmd_module_list)
    a = pm.add_parser("show", help="show a single module including its topic assignments")
    add_db(a)
    a.add_argument("--id", type=int)
    a.add_argument("--kind", choices=list(VALID_KINDS))
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
    a.add_argument("--id", type=int); a.add_argument("--kind", choices=list(VALID_KINDS))
    a.add_argument("--path"); a.add_argument("--symbol")
    a.add_argument("--topic", required=True); a.add_argument("--note")
    a.set_defaults(func=cmd_module_assign)
    a = pm.add_parser("unassign"); add_db(a)
    a.add_argument("--id", type=int); a.add_argument("--kind", choices=list(VALID_KINDS))
    a.add_argument("--path"); a.add_argument("--symbol")
    a.add_argument("--topic", required=True)
    a.set_defaults(func=cmd_module_unassign)

    pd = sub.add_parser("doc", help="manage documents").add_subparsers(dest="sub", required=True)
    a = pd.add_parser("add"); add_db(a)
    a.add_argument("--flavor", required=True); a.add_argument("--title", required=True)
    a.add_argument("--doc-path", help="path to an external file holding the doc body")
    a.add_argument("--content", help="inline doc body stored in the DB")
    a.add_argument("--content-file", help="read inline body from this local file (stored in DB)")
    a.add_argument("--summary")
    a.add_argument("--created-by"); a.add_argument("--source-ref")
    a.add_argument("--topics", help="comma-separated topic slugs")
    a.add_argument("--modules", help="comma-separated module ids")
    a.set_defaults(func=cmd_doc_add)
    a = pd.add_parser("list"); add_db(a)
    a.add_argument("--flavor"); a.add_argument("--topic"); a.add_argument("--module", type=int)
    a.add_argument("--format", choices=["table", "json"], default="table")
    a.set_defaults(func=cmd_doc_list)
    a = pd.add_parser("flavors", help="list distinct flavors in use with document counts")
    add_db(a)
    a.add_argument("--format", choices=["table", "json"], default="table")
    a.set_defaults(func=cmd_doc_flavors)
    a = pd.add_parser("show", help="show a single document including topics, modules, and timestamps")
    add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--format", choices=["table", "json"], default="table")
    a.set_defaults(func=cmd_doc_show)
    a = pd.add_parser("update"); add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.add_argument("--title"); a.add_argument("--summary")
    a.add_argument("--doc-path", help="switch the doc to file-backed at this path")
    a.add_argument("--content", help="switch the doc to inline body with this content")
    a.add_argument("--content-file", help="switch to inline; read body from this local file")
    a.add_argument("--flavor"); a.add_argument("--source-ref")
    a.add_argument("--add-topics"); a.add_argument("--remove-topics")
    a.add_argument("--add-modules"); a.add_argument("--remove-modules")
    a.set_defaults(func=cmd_doc_update)
    a = pd.add_parser("delete"); add_db(a)
    a.add_argument("--id", type=int, required=True)
    a.set_defaults(func=cmd_doc_delete)

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

    a = sub.add_parser("select", help="select modules by topic/flavor criteria")
    add_db(a)
    a.add_argument("--topic"); a.add_argument("--no-topic", action="store_true")
    a.add_argument("--kind", choices=list(VALID_KINDS))
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
