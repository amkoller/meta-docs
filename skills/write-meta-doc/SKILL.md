---
name: write-meta-doc
description: Author a meta-document about a codebase — a functional review, user manual, code review, test plan, etc. — by scoping it to topics and modules from the meta-doc-manager index, drafting it with the user in the loop, writing it to disk, and registering it back into the index. Use this skill whenever the user wants to *produce* (not just track) documentation about parts of a codebase: "write a functional review of the auth topic", "draft a user manual for the ingest pipeline", "document what these modules actually do", "write up how the sync engine behaves", or any request that combines "write/draft/produce" with "documentation/review/manual/notes" scoped to a topic or set of modules. Pairs with the meta-doc-manager skill, which tracks documents but does not author them — this skill is the authoring counterpart.
---

# write-meta-doc

Author a meta-document about a codebase and register it in the meta-doc-manager index. The skill drives a four-phase workflow: **topic scope → module scope → drafting (by flavor) → registration**. Topic scope is fixed once set; module scope is provisional and may grow as the work reveals more in-scope code.

This skill *writes* documents. The companion skill `meta-doc-manager` *tracks* them. Use meta-doc-manager's CLI for every index read/write — do not query or mutate the SQLite DB by hand except for the small recipes listed below.

## When to use

Triggered when the user wants to produce or formally place documentation about a codebase, not write or change the code itself. Two modes:

**Authoring mode (default).** Drive the full four-phase workflow below. Typical asks:
- "Write a functional doc for the auth topic."
- "Document what the sync engine actually does, user-facing and internal."
- "Draft a manual for the photos sample app."
- "Produce a code review of the admin-installer modules."

**Register-existing mode.** The user already has a finished document and wants it placed under `docs_root` and registered in the index. Typical asks:
- "Register this doc against the auth topic." (with inline content)
- "Take the file at ~/Desktop/foo.md and add it as a code-review for the billing topic."
- "Fetch this URL and register it as a user-manual for photos."

In register-existing mode, skip phases 1–3's authoring loops: collect the doc content, ask the user for topic scope + flavor + title, write to `docs_root`, then run the same phase 4 registration. See "Register-existing mode" below for the procedure.

If the user is only asking to *look up* existing docs, enumerate topics, or assign modules to topics — that's meta-doc-manager's job, not this one.

## Prerequisites

Before starting, you need a meta-doc-manager database for the project. If none exists, stop and tell the user to run meta-doc-manager's `init` and establish topics + modules first — this skill assumes that index already reflects the project.

Locate the DB by, in order:
1. `$META_DOC_MANAGER_DB` if set.
2. A path the user has already mentioned in this session (e.g. from memory, recent commands).
3. Ask the user.

**Delegate index reads and writes to the `meta-doc-manager` skill.** This skill owns the authoring loop; meta-doc-manager owns the index. When you need to read the topic tree, list docs, register a new doc, update a registration, set a config value, etc., invoke `meta-doc-manager` (via the Skill tool) with a concrete request — *e.g. "show the topic tree", "register this doc against topics X, Y", "set docs_root to <path>"* — rather than running its CLI yourself. meta-doc-manager knows its own surface and will pick the right `cm.py` subcommand (and grow that surface when something is missing).

In the examples below, when you see `cm <subcommand> ...`, that's shorthand for "ask meta-doc-manager to do this", not "run `python ~/.claude/skills/meta-doc-manager/scripts/cm.py` yourself". The CLI lines are there so a reader can see what the effective operation is.

## Phase 1 — Topic scope (fixed)

Goal: agree with the user on the exact set of topics the document will cover. Once agreed, the topic scope does not change for the rest of the run.

1. Show the user the current topic tree:
   ```
   cm topic list --db <DB> --format tree
   ```
2. Ask which topics this document should cover. Push back gently on scopes that span unrelated parts of the codebase — one document trying to cover too many topics usually produces something shallow. Suggest splitting into multiple documents instead.
3. Read back the final list to the user and get explicit confirmation. Record it (in your head / a TodoList) as the **fixed topic scope**.

If the user proposes a topic that doesn't exist yet, that's a meta-doc-manager task — stop and ask them to add it first (or to invoke meta-doc-manager to do so), then resume.

## Phase 2 — Module scope (provisional)

Goal: assemble the initial set of modules the document will cover, and keep the door open to expand it.

1. For the fixed topic scope, get the modules already assigned. Greedy matching is what you want — a doc on a parent topic should pull in modules of descendant topics:
   ```
   cm select --db <DB> --topic <slug> --format json
   ```
   Repeat per topic (or, if topics share a parent, query the parent once). Union the results.
2. Show the user the provisional module list. Note any topics in scope that have *no* modules assigned — those are either concept-only topics or a gap in the index; flag both cases.
3. Tell the user explicitly: "Module scope is provisional. If we find more in-scope code while drafting, I'll propose adding it as a module and assigning it to the relevant topic before continuing."

### Expanding module scope mid-draft

When drafting surfaces code that clearly belongs to the topic but isn't indexed:

1. Pause drafting. Describe the code (path, what it is, why it's relevant) to the user.
2. Ask which topic in the fixed scope it belongs to. (If none fit, that's a sign the topic scope is wrong — surface it, but remember topic scope is fixed for *this run*; the user may choose to abort and re-scope.)
3. On confirmation, register it with meta-doc-manager:
   ```
   cm module add --db <DB> --kind <file|dir|symbol> --path <path> [--symbol <name>]
   cm module assign --db <DB> --kind <...> --path <...> --topic <slug>
   ```
4. Add it to your working module list and continue.

Do not silently expand. The point of routing through meta-doc-manager is that the index reflects intentional decisions; pinging the user keeps that property.

## Phase 3 — Draft the document (by flavor)

The skill supports multiple **flavors** of meta-document. Each flavor has its own drafting procedure. Today, one flavor is fully inlined here; others will be added as separate skills invoked from this phase with the same contract.

### Choosing the flavor

Ask the user which flavor they want unless it's obvious from their initial request. Common flavors (freeform — use what the project already uses; check with `cm doc list --db <DB> --format table`):

- `functional-doc` — what each module is for and how it actually behaves (user-facing + internal). **Inlined below.**
- `test-plan` — what should be tested for the in-scope modules, at a planning level (existing coverage + gaps + infra notes). **Inlined below.**
- `user-manual` — end-user-facing operation guide. *(Not yet inlined.)*
- `code-review/<subtype>` — security, consistency, performance, etc. *(Not yet inlined.)*
- `test-coverage`, `todos` — *(Not yet inlined.)*

For not-yet-inlined flavors, tell the user the flavor isn't implemented yet and either (a) ask permission to draft it inline ad-hoc using the same scoping/registration scaffolding, or (b) stop and suggest extending this skill.

### Flavor: functional-doc (inlined)

See `references/functional-doc.md` for the full drafting procedure. Read it now if the user picked this flavor.

### Flavor: test-plan (inlined)

See `references/test-plan.md` for the full drafting procedure. Read it now if the user picked this flavor. Note: a test plan leans on the functional review for the same topic scope; if one doesn't exist, that reference explains how to handle it.

### Future-flavor contract (for when flavors become their own skills)

When a flavor moves out into its own skill, this skill invokes it after phases 1–2 and passes:

- The fixed topic scope (list of slugs).
- The provisional module list (cm `select --format json` output, or equivalent).
- The DB path.
- The contract: *"If you discover in-scope code outside the module list, do not silently include it. Propose it to the user; on confirmation, register via meta-doc-manager (`module add` + `module assign`) and report back the updated module list before returning."*

The flavor skill returns a draft (and the final module list, if it grew). This skill then handles the human review loop and registration in phase 4.

## Phase 4 — Review and register

### Decide the output path

1. Read `docs_root` via meta-doc-manager:
   ```
   cm config get --db <DB> --key docs_root
   ```
2. If unset (the command exits non-zero with "no config key 'docs_root'"), ask the user where the file should live and offer to record it for next time:
   ```
   cm config set --db <DB> --key docs_root --value <path> --path
   ```
   (`--path` stores the absolute form of the path.)
3. Pick a filename: `<flavor>-<topic-slugs-joined>-<YYYY-MM-DD>.md`. If multiple topics, pick the most representative two and trust the user to rename if they want something else.

(Reads/writes go through meta-doc-manager, not direct CLI invocation or raw SQL — see the Prerequisites note above.)

### Write the file, then iterate

1. Write the draft to disk at the chosen path. The file on disk is the source of truth from this point on — refer to it by path in subsequent turns, not by re-pasting the contents.
2. Tell the user the path, summarize what's in it (a sentence or two — not a recap of the document), and ask for changes.
3. Loop: accept edit requests (in chat, or by reading the file after the user edits it directly) and revise the file in place. Keep going until the user signals done ("looks good", "ship it", "register it", etc.).

This dual mode — file on disk + inline iteration — is intentional. The file lets the user open the doc in their editor and the index point to something real; the chat loop lets you and the user revise without ping-ponging through saves.

### Register with meta-doc-manager

Once the user signals done:

1. Capture a source ref if the project is in git:
   ```
   git -C <project-root> rev-parse HEAD
   ```
   The `project_root` is in the same config (read with `cm config get --db <DB> --key project_root`). A project may be an umbrella over multiple sibling repos — in that case `project_root` is the umbrella and is NOT itself a git repo. Probe its subdirectories for the actual git repo(s) and choose the one whose code the doc is about; record that repo's SHA. If no git repo applies, omit `--source-ref`.
2. Register:
   ```
   cm doc add --db <DB> \
     --flavor <flavor> \
     --title "<human title with topic + date>" \
     --doc-path <path-relative-to-project-root-if-set-else-absolute> \
     --summary "<one-to-two-sentence summary>" \
     --created-by claude \
     [--source-ref <sha>] \
     --topics <comma-joined topic slugs from the fixed scope> \
     [--modules <comma-joined module ids if the doc focuses on specific modules>]
   ```
3. Confirm to the user: the file path, the document ID returned by `cm doc add`, and the topics it was linked to. The timestamp is recorded automatically by meta-doc-manager (`created_at`); no extra step needed.

Prefer linking to **topics** when the document covers a topic's full module set; add specific `--modules` only when the doc is narrowly focused within the topic. The skill's greedy matching handles topic-level coverage correctly downstream.

## Register-existing mode

Use when the user hands you a finished document instead of asking you to write one. The goal is to put the file under `docs_root` and register it in the index — no drafting, no review loop.

### Collect the content

The source can be any of:

1. **Inline content in the prompt** — the user pasted the document text directly. No fetch needed.
2. **Filesystem path** — `/path/to/foo.md` or similar. Read the file. Default to **copying** it into `docs_root` so the index points at a stable, project-owned location; offer an "in-place register without copying" alternative if the user explicitly wants the canonical location to stay where it is.
3. **URL** — fetch with WebFetch. Strip any obvious nav/chrome if the fetch returns HTML; preserve formatting if it's already markdown. Warn the user if the source could change underneath the registration (e.g. a Notion or wiki URL) — content captured at registration time is what the index points to, not the live URL.

If the source isn't one of these and the user hasn't said, ask.

### Confirm metadata

Even in register-existing mode you need:

- **Flavor** — ask; suggest from `cm doc list --format table` if unsure.
- **Topics** — same as phase 1: show `cm topic list`, get explicit confirmation. Topic scope still has to be intentional; "register against any topic that sounds related" is not a valid scope.
- **Title** — propose one based on the content's heading and the topics; let the user override.
- **Summary** — propose a 1–2 sentence summary based on the content; let the user override.
- **Modules** — usually skip in this mode unless the user gives them; the doc was authored elsewhere and its scope is what the user says it is.

### Write to docs_root, then register

1. Resolve `docs_root` from the DB (same as phase 4 in authoring mode; ask + record if unset).
2. Pick a filename: prefer the doc's existing name when copying from a filesystem path; otherwise `<flavor>-<topic-slugs-joined>-<YYYY-MM-DD>.md`.
3. Write the content to disk at `<docs_root>/<filename>`.
4. Tell the user the final path and confirm everything before registering.
5. Run `cm doc add` as in phase 4. If the source was a URL, include the URL in `--source-ref` (it's a reasonable provenance pointer even if not a git SHA); for filesystem sources, use the git SHA if applicable, else omit.
6. Confirm the doc ID and topics to the user.

Skip phases 1–3's full procedure entirely in this mode. Phase 4's path-resolution and registration steps are reused as-is.

## Judgment notes

- **Do not invent flavors.** If the user names a new flavor, check `cm doc list` for the project's existing vocabulary first and suggest reusing one. Slash-separated subtypes (`code-review/security`) are conventional.
- **Don't silently expand topic scope.** A document covering "auth and billing" because billing seemed related drifts from what the user asked for. If you think the scope is wrong, stop and ask.
- **Stay in the topic's lens when adjacent topics are touched.** Cross-cutting subtopics (`<topic>-cloud`, `<topic>-local`, `<topic>-sync`) almost always overlap an adjacent topic tree (`cloud-server-*`, `local-server-*`, etc.) that has its own documents. Cover *only* the overlap — the facets specifically about the doc's topic as it appears in the adjacent environment. The adjacent topic's general architecture (auth flow, request routing, deployment shape, internal lifecycle) belongs in *its* doc; even when it's interesting and you just read it, describing it here is drift, not breadth. Concretely: if a paragraph reads the same whether the doc's topic is X or Y, it doesn't belong. Replace it with a one-sentence pointer to the adjacent topic. Re-read each cross-cutting section after drafting and ask "would this still make sense if my topic were swapped for any other?" — if yes, cut it.
- **Document content is the user's call.** When the user requests changes, apply them — don't argue style preferences. Do push back on factual changes you believe are wrong (e.g. "I read the code and behavior X actually happens, not Y") with the evidence.
- **One run, one document.** If the user wants multiple docs (e.g. one per topic), do them as separate invocations — the topic-scope-is-fixed invariant depends on this.

## Accuracy

Two failure modes have produced concrete, embarrassing errors in past meta-docs. Both apply to any flavor.

- **Treat structural code comments as hypotheses, not evidence.** Comments that make structural claims — *"runs inside the X Lambda"*, *"called from Y"*, *"kept for API compatibility"*, *"this function is dead"* — are the *most* likely things in a codebase to drift from reality, because the code they describe can change without the comment being touched.

- **Spot-check subagent summaries on sharp factual claims, especially negative ones.** When an Explore subagent (or any agent) returns notes for you to synthesize, those notes are good for *shape and orientation* and weaker for *sharp specifics*. Claims like *"there is no comment explaining X"*, *"field Y is missing"*, *"X only happens in Z"* are the easiest to get wrong by partial reading or generalization. Before placing any such claim in the document — particularly in a critical/Part-2-style section — open the cited file yourself and confirm. Trust subagent notes on what's there; double-check them on what's allegedly absent.
