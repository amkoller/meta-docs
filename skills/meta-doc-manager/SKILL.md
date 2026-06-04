---
name: meta-doc-manager
description: Track and index "meta-documentation" about a codebase — topics, modules, and per-module/per-topic documents (functional reviews, user manuals, code reviews of various flavors, test plans, test coverage notes, todos). Stores a SQLite index plus pointers to the actual document files. Use this skill whenever the user wants to enumerate topics in a codebase, assign code (files / directories / functions) to topics, register or look up meta-documents about parts of the codebase, find which modules lack a given kind of documentation, or otherwise reason about *what documentation exists and what it covers*. Trigger on phrases like "topics in this repo", "what's documented", "register this review", "which modules have no test plan", "meta-docs", "doc coverage", or when the user is curating reviews, audits, manuals, or test plans across a non-trivial codebase. This skill does NOT generate document content — it indexes and tracks documents authored elsewhere (by the user, by other skills, or by other prompts).
---

# meta-doc-manager

A bookkeeping system for "meta-documentation" about a codebase: the topics the code is organized into, the modules (files, directories, or symbols) belonging to each topic, and the various documents that describe, review, plan, or audit those topics and modules.

The skill is **track-and-index only**. Document content lives in plain files on disk — wherever the user wants. This skill maintains a SQLite database that records what exists, what it covers, and where to find it.

## Mental model

Three core entities, plus join tables:

- **Topic** — a human-meaningful slice of functionality (e.g. "auth", "billing UI", "data ingest pipeline", "Terraform networking"). Topics may have a parent; the hierarchy is capped at **3 levels** (root, child, grandchild) to stay legible. The list of topics is the **backbone index** of the whole system.
- **Module** — a piece of code at one of three granularities: `file`, `dir`, or `symbol` (function/class/etc. within a file). Modules are assigned to topics (many-to-many, though separation of concerns should keep it sparse). A module unassigned to any topic is a known, queryable state.
- **Document** — a meta-doc on disk, with a freeform `flavor` string (e.g. `functional-review`, `user-manual`, `code-review/security`, `code-review/consistency`, `test-plan`, `test-coverage`, `todos`). Each document is linked to one or more topics and/or modules, with metadata about when, how, and by whom it was created.

The skill is agnostic to flavors — users define their own. Recommend, don't enforce, a starter taxonomy when the user asks.

## When to use this skill

Use this skill when the user is curating documentation *about* a codebase rather than writing the code itself. Concretely:

- Establishing or revising the list of topics for a project
- Assigning code to topics, or reviewing those assignments
- Recording that a new review / audit / manual / test plan has been written
- Looking up which documents exist for a topic or module
- Finding modules that lack a given flavor of documentation (e.g. "what files have no security review?")
- Producing a working set of modules to feed into another prompt or skill (e.g. "give me all modules under 'auth' that don't yet have a functional review")

Do **not** use this skill to write the document content itself; that is the user's job, or another skill's. This skill records that a document exists and what it covers.

## Storage

The user picks where to put things. Two paths matter:

1. **Database path** — typically a single `.sqlite` file. The skill never assumes a location; always ask, or use the path the user has already established for the project.
2. **Document files** — the user decides. Stored anywhere; the database records the path (absolute or relative — be consistent within a project).

For convenience, the CLI honors the `META_DOC_MANAGER_DB` env var as a default for `--db`. Don't set it unless the user asks.

## CLI

All actions go through one Python entry point. From any working directory:

```
python ~/.claude/skills/meta-doc-manager/scripts/cm.py <command> [...]
```

(Adjust the path if the skill lives elsewhere. The script has no third-party dependencies — stdlib only.)

Run `python .../cm.py --help` or `... <command> --help` for argument details. The high-level command surface:

```
cm.py init          --db PATH [--project-root PATH] [--docs-root PATH]
cm.py migrate       --db PATH

cm.py topic add     --db PATH --name STR [--parent SLUG] [--slug SLUG] [--description STR]
cm.py topic list    --db PATH [--format tree|json|table]
cm.py topic update  --db PATH --slug SLUG [--name STR] [--parent SLUG|NONE] [--description STR]
cm.py topic delete  --db PATH --slug SLUG

cm.py module add    --db PATH --kind file|dir|symbol --path PATH [--symbol NAME] [--description STR]
cm.py module list   --db PATH [--topic SLUG] [--unassigned] [--kind ...] [--format table|json|paths]
cm.py module show   --db PATH (--id N | --kind K --path P [--symbol S]) [--format table|json]
cm.py module update --db PATH --id N [--description STR]
cm.py module delete --db PATH --id N
cm.py module assign   --db PATH (--id N | --kind K --path P [--symbol S]) --topic SLUG [--note STR]
cm.py module unassign --db PATH (--id N | --kind K --path P [--symbol S]) --topic SLUG

cm.py doc add       --db PATH --flavor STR --title STR --doc-path PATH
                    [--summary STR] [--created-by STR] [--source-ref STR]
                    [--topics SLUG,SLUG] [--modules ID,ID]
cm.py doc list      --db PATH [--flavor STR] [--topic SLUG] [--module ID] [--format table|json]
cm.py doc show      --db PATH --id N [--format table|json]
cm.py doc update    --db PATH --id N [--title STR] [--summary STR] [--doc-path PATH]
                    [--flavor STR] [--source-ref STR]
                    [--add-topics ...] [--remove-topics ...]
                    [--add-modules ...] [--remove-modules ...]
cm.py doc delete    --db PATH --id N

cm.py config get    --db PATH [--key KEY] [--format table|json]
cm.py config set    --db PATH --key KEY --value VALUE [--path]

cm.py select        --db PATH [--topic SLUG] [--no-topic] [--kind ...]
                    [--has-flavor STR] [--missing-flavor STR] [--strict]
                    [--format paths|json|table]
```

**Prefer the prebuilt `cm.py` subcommands for index operations.** They encode the invariants (depth cap, unique constraints, flavor conventions, timestamp updates) and produce a uniform, machine-readable surface other skills can rely on. Ad-hoc SQL works too — sqlite is just a file — but it bypasses those invariants and makes the script's surface stagnate.

**Treat "I want to run raw SQL" as a signal, not a failure.** If you find yourself reaching for `sqlite3 SELECT ...` or a bespoke `INSERT`, that means `cm.py` is missing a subcommand the manager genuinely needs. Don't suppress the impulse — write the missing subcommand. The right loop is: notice the gap, add (or extend) a `cm.py` subcommand that fits the operation cleanly, then use it. Over time the script grows to cover the real operations callers care about, and ad-hoc SQL becomes rare on its own.

See `references/schema.md` for the full database schema if you need to write a custom query the CLI doesn't cover. Raw SQL via `sqlite3` is fine for one-off reads; prefer the CLI for writes so invariants (depth cap, unique constraints, flavor strings) are enforced consistently.

## Actions

These are the canonical workflows the skill supports. They map closely to what the user asked for; each is described in terms of the right CLI calls and the right judgment.

### 1. Initialize

Run `cm.py init --db <path>` once per project. This creates the SQLite file and schema. Ask the user where the DB should live before running this. Optionally record `--project-root` (so paths can be stored relative to it) and `--docs-root` (a default base for document files).

A **project** may be an umbrella directory containing multiple sibling repos — for example `~/projects/starkeep/` holds `starkeep-core/`, `starkeep-org/`, `starkeep-apps/`. Set `--project-root` to the umbrella, not any single git repo; the index then spans them coherently. The `repo_root` key from earlier schema versions has been renamed to `project_root`; run `cm.py migrate --db <path>` on any existing DB to apply the rename.

### 2. Establish topics

The most important action and the one with the highest leverage. The topic list defines the backbone of everything else.

- Ask the user for an initial proposal. If they don't have one, **read enough of the codebase to suggest one** (entry points, top-level dirs, README, package manifests). Don't guess from filenames alone.
- Keep topics human-meaningful. A topic should be something a teammate could explain in a sentence: "the checkout UI", "background job runner", "Pulumi networking stack". Avoid topics that are just folder names with no semantic content.
- Aim for coverage of "a large portion" of the codebase, not every line. Leftover modules being unassigned is fine and informative.
- Hierarchy is allowed but capped at depth 3 (root → child → grandchild). Push back gently if the user wants to go deeper; it usually means a topic should be split or a sibling should be promoted.
- After the user confirms, add each topic via `cm.py topic add` (parents first). Then `cm.py topic list --format tree` to show them the result.

### 3. Assign modules to topics

After topics exist, decide which code belongs to each.

- Prefer **directory** modules when a whole subtree clearly belongs to one topic — it's cheaper to maintain than enumerating every file. Use file granularity when a single directory mixes concerns. Use symbol granularity only when the situation actually demands it (a key function in a shared utility file, say); over-using symbol modules creates bookkeeping burden.
- Add modules with `cm.py module add`, then assign with `cm.py module assign`.
- It's fine — sometimes correct — for a module to belong to multiple topics. But if it's happening often, that's a hint the topics aren't well separated; surface it to the user.

### 4. Review topics

Use `cm.py topic list --format tree` and `cm.py module list --unassigned` to show the current state. Then read enough of the codebase to form a judgment, and propose changes: topics to add/split/merge/rename, modules to reassign, unassigned modules that deserve a home. Show your reasoning before making any writes.

### 5. Update topics

Apply the changes from step 4 via `cm.py topic update`, `topic delete`, `module assign`, `module unassign`. Reparenting a topic preserves its slug; renaming is purely cosmetic. Deleting a topic cascades to its module assignments and document links (but not the modules or documents themselves).

### 6. Select modules

The "give me a working set" action. Used to feed other prompts/skills.

- `cm.py select --topic auth --missing-flavor security-review --format paths` → list of paths suitable for piping into a code-review prompt.
- `cm.py select --no-topic` → modules with no topic assignment.
- `cm.py select --has-flavor functional-review --format json` → everything that's already been reviewed.

The `paths` format prints one path per line (file/dir/symbol path), suitable for `xargs` or copying into another prompt. `json` and `table` give richer output.

**Greedy (transitive-down) matching.** By default, `select` treats higher-level topics and modules as equivalent to enumerating their descendants:

- A document linked to a parent **topic** covers modules assigned to any descendant topic. (Doc on `auth` covers modules under `login-form`.)
- A document linked to a parent **module** covers descendant modules in the path hierarchy. (Doc on `dir src/auth` covers `file src/auth/login.ts` and `symbol src/auth/login.ts::validateCredentials`.)
- The `--topic X` filter likewise expands: matches modules assigned anywhere in X's subtree, including via module-ancestor assignments (a file inherits its parent dir's topic).

This matches the user's mental model: "the auth topic is documented" implies "the things under auth are documented". Use `--strict` to disable greedy matching for explicit-link semantics (useful when auditing exactly which links are recorded).

`module list` and `doc list` always use strict matching — they're intended for auditing direct assignments. `select` is where greedy matching matters because it answers "what still needs work?".

### 7. Add document

Once a meta-doc has been written (by the user, by another skill, by you in a different turn), register it:

```
cm.py doc add --db ... \
  --flavor code-review/security \
  --title "Auth module security review — 2026-05-12" \
  --doc-path docs/reviews/auth-security-2026-05.md \
  --summary "Focused on session token storage and CSRF posture" \
  --created-by claude \
  --source-ref $(git rev-parse HEAD) \
  --topics auth \
  --modules 12,17,29
```

Notes:
- `--flavor` is freeform. Use the user's existing flavors when possible — `cm.py doc list --format table` near the top will show what's been used. Slash-separated subtypes (`code-review/security`) are a useful convention but not required.
- `--created-by` is freeform too. `human`, `claude`, `mixed`, or a tool name are all reasonable.
- `--source-ref` is typically a git SHA at the time of authoring. Optional but valuable for staleness reasoning later.
- A document should link to **either** topics, modules, or both. Linking to a topic implicitly covers its modules at the time the doc was written; linking to specific modules is more precise. Use both when a document is scoped to a topic but specifically focused on certain modules.

### 8. Update document

For when the document file itself changes, or its coverage shifts:

```
cm.py doc update --db ... --id 42 --summary "..." --add-modules 33 --remove-topics legacy-auth
```

If the underlying file is moved, pass `--doc-path` with the new location. The skill does not move the file for you — do that separately.

## Conventions and judgment

- **Slugs** are kebab-case, ASCII-only, derived from the topic name if not given. They are the stable identifier; names can change freely.
- **Paths** in the DB should be consistent within a project — either all relative to `project_root`, or all absolute. Pick one when you initialize and stick with it. Relative is more portable but only works when every covered file lives under one root; for multi-repo projects (sibling repos under an umbrella), absolute is usually simpler.
- **Module identity** is `(kind, path, symbol)`. A `file` module and a `dir` module with the same path are different rows.
- **Don't auto-create modules from filesystem walks** unless the user asks. The point of this index is to record *intentional* assignments, not to mirror the file tree.
- **Don't invent flavors**. Use what the user uses, and if they need a new one, suggest it explicitly before recording.
- **Show before writing**. For multi-step changes (establishing topics, bulk reassignments), draft the plan in chat and let the user confirm before running the CLI.

## What this skill is not

- Not a doc generator. Composing the actual review / manual / test plan is out of scope; do it in a separate prompt or skill, then register the result here.
- Not a code analyzer. It doesn't compute coverage metrics, parse ASTs, or run linters. It tracks what humans have said about the code.
- Not a replacement for a wiki. Documents live as files; this is the index over them.

## Reference

- `references/schema.md` — full SQLite schema, with field-level notes and example queries. Read this when you need to write custom SQL or understand a constraint the CLI enforces.
