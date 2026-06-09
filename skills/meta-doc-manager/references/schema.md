# meta-doc-manager — database schema

The SQLite file created by `cm.py init` contains the following tables. All timestamps are ISO-8601 UTC strings written by `datetime('now')`. Foreign keys are enabled per-connection via `PRAGMA foreign_keys = ON`.

## `config`

Key/value bag for project-wide settings.

| column | type | notes                                             |
|--------|------|---------------------------------------------------|
| key    | TEXT | primary key                                       |
| value  | TEXT | not null                                          |

Known keys:
- `schema_version` — integer as string, currently `"2"`.
- `project_root` — absolute path to the project, if recorded. A "project" may be an umbrella directory containing multiple sibling repos (e.g. `~/projects/starkeep/` holding `starkeep-core/`, `starkeep-org/`, `starkeep-apps/`); use the umbrella, not any single repo. *(Renamed from `repo_root` in schema version 1; run `cm.py migrate` on older DBs.)*
- `docs_root` — default base for document paths, if recorded.
- `path_mode` — `"relative"` or `"absolute"` (advisory; not enforced).

## `topics`

The hierarchical backbone. Depth is capped at 3 (values 0, 1, 2) by the CLI.

| column      | type    | notes                                                          |
|-------------|---------|----------------------------------------------------------------|
| id          | INTEGER | primary key                                                    |
| parent_id   | INTEGER | nullable; FK → `topics.id`, `ON DELETE CASCADE`                |
| slug        | TEXT    | not null, unique; kebab-case ASCII                             |
| name        | TEXT    | not null; human-readable                                       |
| description | TEXT    | nullable                                                       |
| depth       | INTEGER | not null; 0 for root, 1 for child, 2 for grandchild            |
| created_at  | TEXT    | not null; default `datetime('now')`                            |
| updated_at  | TEXT    | not null; default `datetime('now')`                            |

The CLI rejects inserts/updates that would push `depth > 2` or that would reparent a topic such that its descendants exceed the cap.

## `modules`

A unit of code at one of three granularities.

| column      | type    | notes                                                                 |
|-------------|---------|-----------------------------------------------------------------------|
| id          | INTEGER | primary key                                                           |
| kind        | TEXT    | not null; CHECK in (`file`, `dir`, `symbol`)                          |
| path        | TEXT    | not null; relative or absolute (consistent within a project)          |
| symbol      | TEXT    | nullable; required when `kind = 'symbol'`, null otherwise (enforced by CLI) |
| description | TEXT    | nullable                                                              |
| created_at  | TEXT    | not null                                                              |
| updated_at  | TEXT    | not null                                                              |

Unique constraint on `(kind, path, COALESCE(symbol, ''))` — implemented as `UNIQUE (kind, path, symbol)` with the convention that `symbol IS NULL` for non-symbol kinds.

## `topic_modules`

Many-to-many between topics and modules.

| column     | type    | notes                                                  |
|------------|---------|--------------------------------------------------------|
| topic_id   | INTEGER | FK → `topics.id`, `ON DELETE CASCADE`                  |
| module_id  | INTEGER | FK → `modules.id`, `ON DELETE CASCADE`                 |
| note       | TEXT    | nullable; free-form ("primary owner", "shared utility") |
| created_at | TEXT    | not null                                               |

Primary key: `(topic_id, module_id)`.

## `documents`

A meta-document. Body is stored in **one of two modes**: either as a path to an external file (`doc_path`) or inline in the DB (`content`). A `CHECK` constraint enforces exactly one is non-null per row, so every document has exactly one body location.

| column      | type    | notes                                                            |
|-------------|---------|------------------------------------------------------------------|
| id          | INTEGER | primary key                                                      |
| flavor      | TEXT    | not null; freeform (`functional-review`, `code-review/security`, etc.) |
| title       | TEXT    | not null                                                         |
| doc_path    | TEXT    | nullable; path to the external document file. Must be null when `content` is set. |
| content     | TEXT    | nullable; inline document body. Must be null when `doc_path` is set. |
| summary     | TEXT    | nullable                                                         |
| created_by  | TEXT    | nullable; freeform (`human`, `claude`, `mixed`, tool name)       |
| source_ref  | TEXT    | nullable; typically a git SHA at the time of authoring           |
| created_at  | TEXT    | not null                                                         |
| updated_at  | TEXT    | not null                                                         |

Storage-mode invariant: `CHECK ((doc_path IS NOT NULL) <> (content IS NOT NULL))`. Use file-backed mode when the doc lives alongside the codebase and benefits from version control; use inline mode when the DB itself is the source of truth (e.g. when this index will eventually be served from a centralized server where filesystem paths are meaningless).

## `document_topics` / `document_modules`

Many-to-many coverage links. A document can be linked to any combination of topics and modules.

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

Modules under a topic (direct assignments only):
```sql
SELECT m.* FROM modules m
JOIN topic_modules tm ON tm.module_id = m.id
JOIN topics t ON t.id = tm.topic_id
WHERE t.slug = ?;
```

Modules under a topic including descendant topics:
```sql
WITH RECURSIVE sub(id) AS (
  SELECT id FROM topics WHERE slug = ?
  UNION ALL
  SELECT t.id FROM topics t JOIN sub s ON t.parent_id = s.id
)
SELECT DISTINCT m.* FROM modules m
JOIN topic_modules tm ON tm.module_id = m.id
WHERE tm.topic_id IN sub;
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

Distinct flavors in use (useful for the user when picking one):
```sql
SELECT flavor, COUNT(*) AS n FROM documents GROUP BY flavor ORDER BY n DESC;
```

## A note on greedy matching

The raw queries above use **direct-link** semantics: a module is "covered" by a doc only if the join paths visible in the schema link them. The `cm.py select` command implements a richer **greedy-down** semantics on top of this, computed in application code rather than SQL:

- The **module hierarchy** is implicit by path: a `dir` module covers any module whose path is at or under its path (file or sub-dir); a `file` module covers same-path `symbol` modules.
- The **topic hierarchy** is explicit via `parent_id`. A doc linked to a parent topic is treated as covering modules in any descendant topic.
- Composing both: doc → topic `auth` → module-ancestor-assignment (`dir src/auth` is in `auth`) → descendant module (`symbol src/auth/login.ts::validateCredentials`) is a valid coverage chain.

If you need this semantics outside `cm.py select`, the cleanest path is to call `cm.py select` (with `--format json` for structured output). Re-deriving it in raw SQL is doable but requires recursive CTEs in both dimensions and string-prefix joins on `path`, which is awkward enough that we keep it in Python.
