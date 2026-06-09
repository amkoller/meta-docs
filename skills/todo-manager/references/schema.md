# todo-manager — database schema

The `users` and `todos` tables are created on an existing `meta-doc-manager`
SQLite file by `tm.py init`. Foreign keys are enabled per-connection via
`PRAGMA foreign_keys = ON`.

## `users`

| column | type    | notes                       |
|--------|---------|-----------------------------|
| id     | INTEGER | primary key                 |
| name   | TEXT    | not null, unique            |

Referenced from `todos.assignee_id` with `ON DELETE SET NULL` — deleting a
user clears their assignments without removing the todos.

## `todos`

| column       | type    | notes                                                                                          |
|--------------|---------|------------------------------------------------------------------------------------------------|
| id           | INTEGER | primary key                                                                                    |
| document_id  | INTEGER | not null; FK → `documents.id`, `ON DELETE CASCADE`. Not unique — one doc can spawn many todos. |
| assignee_id  | INTEGER | nullable; FK → `users.id`, `ON DELETE SET NULL`                                                |
| status       | TEXT    | not null, default `backlog`; CHECK in (`backlog`, `in_progress`, `in_review`, `done`)          |
| blocks       | TEXT    | nullable; JSON array of todo ids this todo blocks (must be done first)                         |
| priority     | INTEGER | nullable; higher = more important. Initial values are multiples of 16384.                      |
| created_at   | TEXT    | not null; default `datetime('now')`                                                            |
| updated_at   | TEXT    | not null; default `datetime('now')`                                                            |

`tm.py` enforces, on writes, that the linked document has
`flavor = 'todo'`. The DB itself does not — flavor is freeform and the
constraint is an application-layer rule.

### `blocks` format

A JSON array of integers, e.g. `[7, 9, 12]`. Canonical form, written by
`tm.py`, is `json.dumps(sorted(set(ids)))` so identical sets always
serialize identically. `NULL` (not `[]`) is the empty value — "this todo
blocks nothing." Edge direction: `A.blocks` contains `B` means **A must be
completed before B** (A is a prerequisite of B), so A's priority must be
strictly greater than B's. `tm.py` rejects writes that would introduce a
cycle.

## Indexes

```sql
CREATE INDEX idx_todos_status   ON todos(status);
CREATE INDEX idx_todos_priority ON todos(priority);
CREATE INDEX idx_todos_document ON todos(document_id);
```

## Config keys used

Stored in the existing `meta-doc-manager` `config` table:

- `todo_priority_guidance` — freeform text describing how to rank todos
  for this project. Read by `tm.py priority guidance get`, replaced by
  `tm.py priority guidance set --text "..."` (or `--from-file PATH`).
  Seeded on first `tm.py init` with a generic two-axis default; intended
  to be overwritten with project-specific wording. The skill assigns
  priorities via `tm.py priority set` while applying this guidance with
  discretion — there is no structured classifier and no interactive
  `priority init` flow.

## Useful queries

Todos for one document, newest first:
```sql
SELECT t.id, t.status, t.priority, u.name AS assignee
FROM todos t
LEFT JOIN users u ON u.id = t.assignee_id
WHERE t.document_id = ?
ORDER BY t.created_at DESC;
```

All open todos by priority (descending):
```sql
SELECT t.id, d.title, t.status, t.priority
FROM todos t
JOIN documents d ON d.id = t.document_id
WHERE t.status != 'done'
ORDER BY t.priority IS NULL, t.priority DESC, t.id ASC;
```

Todos blocked by a given todo (read the JSON array):
```sql
-- SQLite json_each requires the json1 extension (bundled in most builds).
SELECT t.id, t.status
FROM todos t, json_each(t.blocks)
WHERE json_each.value = ?;
```

Todos with no priority assigned yet:
```sql
SELECT id, document_id FROM todos WHERE priority IS NULL;
```

Current priority guidance in config:
```sql
SELECT value FROM config WHERE key = 'todo_priority_guidance';
```
