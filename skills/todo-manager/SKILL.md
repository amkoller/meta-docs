---
name: todo-manager
description: Track actionable todos drawn from `flavor = 'todo'` meta-documents indexed by meta-doc-manager. Each todo has an assignee, status (backlog/in_progress/in_review/done), a JSON-list of other todos it blocks, and an integer priority maintained as a multiple of 16384 to allow easy reorderings. Prioritization is driven by a freeform project-specific guidance string in config (`todo_priority_guidance`) that Claude reads and applies with discretion via `tm.py priority set` — there is no structured classifier. Use this skill whenever the user wants to record, list, prioritize, assign, or update workflow state for action items that live as `todo`-flavored meta-docs. Does not author the underlying meta-docs — see write-meta-doc / meta-doc-manager for that. Trigger phrases include "add a todo", "what's on my plate", "rank the todos", "who's working on X", "rebalance priority", "block this on Y".
---

# todo-manager

Per-document workflow state for action items recorded as `flavor = 'todo'`
meta-documents. Lives in the same database (SQLite or Postgres) used by
`meta-doc-manager` — todos reference `documents.id` directly (FK).

## Mental model

A **todo** is a single actionable item drawn from a `todo`-flavored meta-doc.
The meta-doc describes *what* needs to happen and provides the body (either
as a file path or inline content); the todo row records *who* is doing it,
*what state* it's in, *what other todos it gates*, and *how important* it
is right now.

One meta-doc can spawn multiple todos. A `todo`-flavored document might
describe a feature whose work decomposes into several distinct,
separately-trackable action items; each gets its own row.

Only `todo`-flavored documents are eligible to be referenced. The CLI
rejects any `tm.py todo add --doc-id N` where the document has a different
flavor. (To make a non-`todo` document eligible, change its flavor first
with `cm.py doc update --id N --flavor todo`.)

## When to use this skill

- "Add a todo for the X doc."
- "What's in flight? What's blocked? Who's assigned to Y?"
- "Reorder priority — make Z the top item." / "Recompute priorities."
- "Mark these todos done."
- "Set the category preferences for prioritization."

Do **not** use this skill to:
- Author the meta-doc body itself (use `write-meta-doc` or your editor).
- Rebalance documentation coverage across modules (use `meta-doc-manager`).
- Run a general project tracker — this is scoped to todos tied to docs.

## Storage

Lives in the same database as `meta-doc-manager` — either a SQLite file or
a Postgres URI. `tm.py init --db PATH` creates the `users` and `todos` tables
on an existing meta-doc DB and seeds the default priority guidance. There is
no separate DB for todos; the FK on `todos.document_id → documents.id`
requires they share the connection.

## CLI

```
python ~/.claude/skills/todo-manager/scripts/tm.py <command> [...]
```

(Adjust the path to wherever the skill lives. SQLite usage is stdlib-only;
Postgres usage additionally requires `psycopg[binary]>=3`. `PATH` below may
be a SQLite path *or* a Postgres URI. The `META_DOC_MANAGER_DB` env var
supplies a default for `--db` if set; don't set it unless the user asks.)

```
tm.py init                       --db PATH

tm.py user add                   --db PATH --name STR
tm.py user list                  --db PATH [--format table|json]
tm.py user delete                --db PATH (--id N | --name STR)

tm.py todo add                   --db PATH --doc-id N
                                 [--assignee NAME] [--status STATUS]
                                 [--blocks ID,ID,...] [--priority INT]
tm.py todo list                  --db PATH [--status S] [--assignee NAME|NONE]
                                 [--doc-id N] [--format table|json]
tm.py todo top                   --db PATH [--limit N] [--assignee NAME|NONE]
                                 [--include-done] [--format table|json]
tm.py todo show                  --db PATH --id N [--format table|json]
tm.py todo update                --db PATH --id N
                                 [--status S] [--assignee NAME|NONE]
                                 [--priority INT] [--blocks ID,ID,...|NONE]
tm.py todo delete                --db PATH --id N

tm.py priority set               --db PATH --id N --value INT
tm.py priority guidance get      --db PATH [--format text|json]
tm.py priority guidance set      --db PATH (--text STR | --from-file PATH)
```

See `references/schema.md` for the schema. Prefer the CLI for writes; it
enforces the flavor check, validates the blocks DAG (cycle detection),
canonicalizes the JSON-list form of `blocks`, and seeds defaults. Note that
the integer the CLI calls `id` is the `idx` column in the schema; the
actual PK is a UUID string.

**Don't write ad-hoc SQL against this DB.** If the user asks a question
the existing subcommands don't answer (e.g. "what's the top priority
todo?", "what's blocking X?", "what's the oldest backlog item?"), do not
reach for raw `sqlite3` queries. Instead, first ask the user whether to
extend `tm.py` with a new subcommand that captures the question. The
short-term cost (one extra round trip + small script edit) buys a
repeatable, documented capability for next time, and avoids drift between
what the schema reference promises and what ad-hoc queries assume. Only
fall back to raw SQL if the user explicitly declines the extension or
asks for a one-off read they don't want codified.

## Actions

### 1. Initialize

Run `tm.py init --db PATH` once. Requires the meta-doc DB to already
exist (run `cm.py init` first). On first run it creates the `users` and
`todos` tables and seeds the `todo_priority_guidance` config key with a
generic two-axis default (substance × category). The default is meant
to be overwritten per-project — see action 4.

### 2. Add users

```
tm.py user add --db PATH --name alice
```

Names are unique. Users are referenced from `todos.assignee_id`. Deleting
a user nulls out their assignments (`ON DELETE SET NULL`).

### 3. Add todos for `todo`-flavored docs

```
tm.py todo add --db PATH --doc-id 42 [--assignee alice] [--blocks 7,9]
```

- `--doc-id` is required; the document must have `flavor = 'todo'`.
- `--blocks` is a comma-separated list of other todo ids this todo blocks
  (i.e. it must be done first). Stored as canonical JSON
  (`json.dumps(sorted(set(...)))`). Cycle detection runs at write time.
- `--status` defaults to `backlog`. Allowed: `backlog`, `in_progress`,
  `in_review`, `done`.
- `--priority` is optional; leave it unset and assign one later via
  `tm.py priority set` once you've decided where it belongs in the
  ordering.

### 4. Set the project's priority guidance

Replace the default with project-specific wording:

```
tm.py priority guidance set --db PATH --text "..."
tm.py priority guidance set --db PATH --from-file priorities.md
tm.py priority guidance get --db PATH
```

The guidance is a single freeform string. There is no structured
classifier and no interactive `priority init` flow — Claude reads the
guidance plus each todo's body and assigns priorities by discretion
via `tm.py priority set`. The string should capture both *what* matters
for this project (the substance axis: data integrity, security, sync
liveness, missing features, observability, confusion, optimization)
and *how* to break ties (the category axis: correctness over confusion
over optimization, with any project-specific refinements like "treat
dead code as the most serious confusion class").

**Larger priority value = more important.** Initial values are
multiples of `PRIORITY_STEP = 16384`. The spacing means arbitrary
single-pair reorderings can be done by averaging neighbors (`new =
(a + b) // 2`) without renumbering for a long time. When gaps
eventually compress, renumber by reassigning multiples of 16384 in
order.

### 5. Update status / assignee / priority

```
tm.py todo update --db PATH --id 12 --status in_progress
tm.py todo update --db PATH --id 12 --assignee alice
tm.py todo update --db PATH --id 12 --assignee NONE        # clear
tm.py todo update --db PATH --id 12 --blocks 7,9
tm.py todo update --db PATH --id 12 --blocks NONE          # clear
tm.py priority set --db PATH --id 12 --value 81920         # pin
```

`tm.py priority set` accepts any integer; use it for ad-hoc "make X the
top priority" moves (e.g. set to `max + 16384`).

### 6. List and filter

```
tm.py todo list --db PATH                         # all, priority DESC nulls last
tm.py todo list --db PATH --status in_progress
tm.py todo list --db PATH --assignee alice
tm.py todo list --db PATH --assignee NONE         # unassigned
```

`tm.py todo show --id N` shows the todo plus its linked document (title,
flavor, path or content preview).

For "what's the top priority?" style questions, use `tm.py todo top`:

```
tm.py todo top --db PATH                 # single top non-done todo
tm.py todo top --db PATH --limit 5       # top 5
tm.py todo top --db PATH --assignee alice
tm.py todo top --db PATH --include-done  # include status=done in ranking
```

`top` is just `list` ordered by `priority DESC NULLS LAST, idx ASC` with
`status != 'done'` applied by default and a `LIMIT`. Use it whenever the
user asks "what should I work on next?" or "what's the most important
todo right now?".

## Conventions and judgment

- **Status values** are snake_case: `backlog`, `in_progress`, `in_review`,
  `done`. Enforced by CHECK.
- **`blocks`** is a JSON array of integers (todo ids). Canonical form is
  `json.dumps(sorted(set(ids)))`. `NULL` (not `[]`) means "blocks nothing".
- **Priority** semantics: higher number = more important. Initial values
  are multiples of 16384. The exact magnitudes don't matter — only the
  ordering.
- **Priority guidance is freeform.** It lives in
  `config.todo_priority_guidance` as a single string and exists to be
  read and applied with discretion, not parsed. Update it when the
  project's prioritization conventions shift; don't try to encode the
  rules structurally.
- **Read the doc body before ranking.** Summaries and titles aren't
  enough to judge substance; the body usually surfaces the load-bearing
  reason, defer-until markers, and cross-todo dependencies.

## What this skill is not

- Not an issue tracker (no labels, no comments, no notifications).
- Not a project planner (no sprints, milestones, or burn-down).
- Not a content authoring tool — the body lives in the meta-doc, not on
  the todo row.

## Reference

- `references/schema.md` — the `users` and `todos` table definitions,
  with notes on the `blocks` JSON convention and the
  `todo_priority_guidance` config key.
- For the `documents` table the FK points at, see
  `../meta-doc-manager/references/schema.md`.
