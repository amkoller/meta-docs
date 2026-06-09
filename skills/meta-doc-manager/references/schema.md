# meta-doc-manager — database schema

The database created by `cm.py init` works identically against SQLite and
Postgres. Schema-creation SQL is in `scripts/db.py` as `META_DOC_SCHEMA_SQL`
and uses only features supported by both backends (modulo trivial syntax —
e.g. `?` placeholders translated to `%s` for Postgres at execute time).

All timestamps are ISO-8601 UTC strings written by the application via
`now_iso()` — no `datetime('now')` / `NOW()` defaults appear in the schema.
Foreign keys are enabled per-connection on SQLite via `PRAGMA foreign_keys = ON`;
Postgres always enforces them.

## Identifiers

Every row has two identifiers:

- **`id TEXT PRIMARY KEY`** — a UUID4 generated in Python on insert. Used for
  all foreign-key references. Stored as a string in both backends to keep the
  schema string-for-string identical and avoid a Postgres-only extension.
- **`idx INTEGER UNIQUE`** — a readable handle, allocated as
  `MAX(idx)+1` per-table on insert. This is what the CLI surfaces as `--id N`
  and what `topic list` / `module list` / `doc list` display in the `id`
  column. Gaps from deletes are fine; uniqueness is the only guarantee.

The UUID is the stable, internal identifier. The `idx` exists purely so
humans (and other CLIs) can refer to things with short integers.

## `config`

Key/value bag for project-wide settings.

| column | type | notes        |
|--------|------|--------------|
| key    | TEXT | primary key  |
| value  | TEXT | not null     |

Known keys:
- `project_root` — absolute path to the project, if recorded.
- `docs_root` — default base for document paths, if recorded.
- `path_mode` — `"relative"` or `"absolute"` (advisory; not enforced).
- `todo_priority_guidance` — set by todo-manager (see its schema doc).

## `topics`

The hierarchical backbone. Depth is capped at 3 (values 0, 1, 2) by the CLI.

| column      | type    | notes                                                       |
|-------------|---------|-------------------------------------------------------------|
| id          | TEXT    | UUID4, primary key                                          |
| idx         | INTEGER | user-facing handle, unique                                  |
| parent_id   | TEXT    | nullable; FK → `topics.id`, `ON DELETE CASCADE`             |
| slug        | TEXT    | not null, unique; kebab-case ASCII                          |
| name        | TEXT    | not null; human-readable                                    |
| description | TEXT    | nullable                                                    |
| depth       | INTEGER | not null; 0 for root, 1 for child, 2 for grandchild         |
| created_at  | TEXT    | not null                                                    |
| updated_at  | TEXT    | not null                                                    |

The CLI rejects inserts/updates that would push `depth > 2` or that would
reparent a topic such that its descendants exceed the cap. The constraint is
enforced in application code, not by the DB.

## `modules`

A unit of code at one of three granularities.

| column      | type    | notes                                                                 |
|-------------|---------|-----------------------------------------------------------------------|
| id          | TEXT    | UUID4, primary key                                                    |
| idx         | INTEGER | user-facing handle, unique                                            |
| kind        | TEXT    | not null; one of `file` / `dir` / `symbol` (enforced in CLI)          |
| path        | TEXT    | not null; relative or absolute (consistent within a project)          |
| symbol      | TEXT    | nullable; required when `kind = 'symbol'`, null otherwise (CLI rule)  |
| description | TEXT    | nullable                                                              |
| created_at  | TEXT    | not null                                                              |
| updated_at  | TEXT    | not null                                                              |

Unique constraint: `UNIQUE (kind, path, symbol)`. NULL `symbol` is the
convention for non-symbol kinds, and `NULL` is treated as distinct by the
UNIQUE constraint in both backends.

## `topic_modules`

Many-to-many between topics and modules.

| column     | type    | notes                                                  |
|------------|---------|--------------------------------------------------------|
| topic_id   | TEXT    | FK → `topics.id`, `ON DELETE CASCADE`                  |
| module_id  | TEXT    | FK → `modules.id`, `ON DELETE CASCADE`                 |
| note       | TEXT    | nullable; free-form                                    |
| created_at | TEXT    | not null                                               |

Primary key: `(topic_id, module_id)`.

## `documents`

A meta-document. Body is stored in one of two modes: a path to an external
file (`doc_path`) or inline in the DB (`content`). The CLI enforces "exactly
one is non-null" at write time; there is no DB-level CHECK constraint.

| column      | type    | notes                                                            |
|-------------|---------|------------------------------------------------------------------|
| id          | TEXT    | UUID4, primary key                                               |
| idx         | INTEGER | user-facing handle, unique                                       |
| flavor      | TEXT    | not null; freeform (`functional-review`, `code-review/security`, etc.) |
| title       | TEXT    | not null                                                         |
| doc_path    | TEXT    | nullable; path to external file (XOR with `content`)             |
| content     | TEXT    | nullable; inline body (XOR with `doc_path`)                      |
| summary     | TEXT    | nullable                                                         |
| created_by  | TEXT    | nullable; freeform (`human`, `claude`, `mixed`, tool name)       |
| source_ref  | TEXT    | nullable; typically a git SHA at the time of authoring           |
| created_at  | TEXT    | not null                                                         |
| updated_at  | TEXT    | not null                                                         |

## `document_topics` / `document_modules`

Many-to-many coverage links. A document can be linked to any combination of
topics and modules.

```sql
document_topics  (document_id, topic_id)   PRIMARY KEY (document_id, topic_id)
document_modules (document_id, module_id)  PRIMARY KEY (document_id, module_id)
```

Both FKs use `ON DELETE CASCADE`.

## Indexes

```sql
CREATE INDEX idx_modules_path     ON modules(path);
CREATE INDEX idx_topics_parent    ON topics(parent_id);
CREATE INDEX idx_documents_flavor ON documents(flavor);
```

## Useful queries

Topic IDs are UUIDs. Most of these examples take a slug input instead, which
is the more useful entry point in practice. `idx` is also available as a
short-int alternative.

Modules under a topic (direct assignments only):
```sql
SELECT m.* FROM modules m
JOIN topic_modules tm ON tm.module_id = m.id
JOIN topics t ON t.id = tm.topic_id
WHERE t.slug = ?;
```

Modules under a topic including descendant topics:
```sql
-- The topic tree is depth-capped at 3 and tiny in practice, so the easy path
-- is to load the topics table and walk parent_id in Python:
--
--   topics = fetchall("SELECT id, parent_id, slug FROM topics")
--   wanted_topic_ids = descendants_of_in_python(topics, slug='auth')
--
-- ...then filter topic_modules.topic_id by that set. cm.py does exactly this
-- in the `select` command; there is no recursive CTE in the codebase.
```

Modules with no topic assignment:
```sql
SELECT m.* FROM modules m
LEFT JOIN topic_modules tm ON tm.module_id = m.id
WHERE tm.module_id IS NULL;
```

Modules missing a given document flavor (directly or via any of their topics):
```sql
SELECT m.* FROM modules m
WHERE NOT EXISTS (
  SELECT 1 FROM documents d
  LEFT JOIN document_modules dm ON dm.document_id = d.id AND dm.module_id = m.id
  LEFT JOIN document_topics  dt ON dt.document_id = d.id
  LEFT JOIN topic_modules    tm ON tm.topic_id    = dt.topic_id AND tm.module_id = m.id
  WHERE d.flavor = ?
    AND (dm.module_id IS NOT NULL OR tm.module_id IS NOT NULL)
);
```

Distinct flavors in use:
```sql
SELECT flavor, COUNT(*) AS n FROM documents GROUP BY flavor ORDER BY n DESC;
```

## A note on greedy matching

The raw queries above use **direct-link** semantics. The `cm.py select` command
implements a richer **greedy-down** semantics on top of this, computed in
application code rather than SQL:

- The **module hierarchy** is implicit by path: a `dir` module covers any
  module whose path is at or under its path; a `file` module covers same-path
  `symbol` modules.
- The **topic hierarchy** is explicit via `parent_id`. A doc linked to a parent
  topic is treated as covering modules in any descendant topic.

If you need this semantics outside `cm.py select`, call `cm.py select` (with
`--format json` for structured output) — reimplementing it in raw SQL would
require recursive CTEs in both dimensions, which the codebase deliberately
avoids.
