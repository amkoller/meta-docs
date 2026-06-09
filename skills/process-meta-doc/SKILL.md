---
name: process-meta-doc
description: Use registered meta-documentation (from meta-doc-manager) to answer questions about a codebase or to drive action on the review/suggestion sections of those docs. Two modes — (a) Q&A mode looks up the relevant docs by topic, reads them, and answers, with explicit citation to the source doc and its date when the answer is doc-only; (b) review-processing mode walks the user through the doc's open questions, suggested changes, and recommendations (whatever shape they take — e.g. for functional-doc the Part 2 subsections of questionable purposes / missing behaviors / behavioral bugs / potential gaps; for code-reviews a Findings list; for test-plans a Gaps section) and helps decide what to do about each. Use this skill whenever the user asks a question about a documented part of the codebase, references existing meta-docs/reviews/audits, or wants to "process / triage / address / work through" the findings in a registered review. Pairs with meta-doc-manager (which indexes the docs) and write-meta-doc (which produces them) — this skill is the consumer-side counterpart.
---

# process-meta-doc

Consume meta-documentation that has been registered via meta-doc-manager. Two modes:

1. **Q&A mode** — answer a question about the codebase by looking up the relevant docs, reading them, and synthesizing an answer.
2. **Review-processing mode** — walk the user through the "review and evaluation" parts of one or more docs and help decide what to do about each item.

This skill does not author new documents (use `write-meta-doc` for that) and does not modify the meta-doc-manager index beyond updating registered docs and appending action records.

## Prerequisites

Locate the meta-doc-manager DB, in order:
1. `$META_DOC_MANAGER_DB` if set.
2. A path the user has already mentioned in this session.
3. Ask the user.

**Delegate index reads and writes to the `meta-doc-manager` skill.** This skill is the *consumer* of the index; meta-doc-manager is the *owner*. Whenever you need to look up topics, list docs, read a doc record (with topics/modules/timestamps), update a doc's registration after marking it resolved, etc., invoke `meta-doc-manager` (via the Skill tool) with a concrete request — *e.g. "list docs for topic X", "show doc id N", "update doc N's summary to ..."* — rather than running its CLI yourself. meta-doc-manager knows its own surface and will pick the right `cm.py` subcommand (and extend that surface when something is missing).

In the examples below, when you see `cm <subcommand> ...`, that's shorthand for "ask meta-doc-manager to do this", not "run `python3 ~/.claude/skills/meta-doc-manager/scripts/cm.py` yourself". The CLI lines are there so a reader can see what the effective operation is.

If meta-doc-manager reports that no documents are registered for the topics in scope, tell the user — neither mode has anything to work with until documents exist.

## Which mode?

Pick by reading the user's intent:

- **"What does X do?" / "How does Y work?" / "Where is Z handled?"** → Q&A.
- **"Look up what we've documented about A."** → Q&A.
- **"Process / triage / address / work through the review for X."** → review-processing.
- **"What did the functional review say to fix?" → could be either**: ask the user whether they want a one-shot summary (Q&A) or a walkthrough (review-processing).

When ambiguous, ask.

---

# Q&A mode

Goal: answer the user's question using registered meta-docs as the primary source. Be explicit about provenance so the user knows whether they're getting "the doc says" vs. "the code says".

## 1. Map the question to topics

Read the topic tree once:

```
cm topic list --db <DB> --format tree
```

Pick topic(s) that semantically match the question. If the question names a topic explicitly ("the auth functional doc"), use it. If it names code areas, map them to topics (e.g. "the Cognito wizard" → `admin-web-cloud-auth` or `admin-web` parent). When the mapping is non-obvious, propose 1–3 candidate topics to the user and confirm before pulling docs.

## 2. Pull the candidate doc set

For each chosen topic:

```
cm doc list --db <DB> --topic <slug> --format table
```

This is a strict-match list; broaden by also pulling parent-topic docs if the question crosses sub-topics. Show the candidate list to the user when it's more than ~3 docs and ask which to consult — there's no point reading 10 docs to answer a question that only needs one.

If `cm doc list` returns nothing for the topics in scope, say so and ask whether the user wants:
- a code-only answer (no docs to ground it), or
- to first author one via `write-meta-doc`.

## 3. Read and answer

Read the selected docs' `doc_path` files. Synthesize an answer focused on the user's question — don't dump the doc.

### Citation rules

Every answer that draws on a doc must make the source legible:

- **Doc-only answer** (no code read): lead with `Based on <doc title>, written <created_at date>:` and end with `Want a fresh look at the current code? — I haven't checked it against the doc.` so the user knows they can ask for verification.
- **Doc + code verification**: say which parts came from where, e.g. `The doc describes X (functional-doc-admin-web, 2026-06-01); I verified against <file>:<line> and the current code still does this.`
- **Code-only fallback** (no relevant docs found): say so explicitly: `No registered doc covers this; the answer below is read directly from <file>.`

### When to verify against current code

Default policy (registered docs are trusted):

- Questions about **purpose / design intent / why** → trust the doc; do not verify. Cite as doc-only.
- Questions about **current state / "what does the code do now?" / "is X still true?"** → also open the cited files and verify. Cite both.
- Any time the user asks for "a fresh look" or "check the code" → verify regardless of question type.

When a verified check disagrees with the doc: report the divergence explicitly ("doc says X; current code does Y at `<file>:<line>`"). Suggest the user invoke `write-meta-doc` to refresh the doc, but don't auto-fix it.

## 4. Optional follow-up

After answering, briefly note other doc-linked topics that might be relevant ("There's also a `code-review/security` doc on this topic from 2026-04-12 if you want to go deeper") so the user can chain the next question.

---

# Review-processing mode

Goal: take the "review and evaluation" parts of one or more registered docs and drive action on each item with the user.

## 0. Read the `keep-action-log` setting

Before starting, read the meta-doc-manager config key `keep-action-log` for the project's DB:

```
cm config get --db <DB> --key keep-action-log
```

- Missing key, or value `false`/`0`/`off` → **OFF** (default). Lightweight policy: items are *removed* from the doc's review section as they're resolved, and deferred work becomes a separate `todo` meta-doc.
- Value `true`/`1`/`on` → **ON**. Heavyweight policy: items are *preserved* in place with a `**Resolved (...)**` marker, and the doc gets a `## Review action log` appended.

This is per-project (lives in the project's meta-doc-manager DB), so the setting reflects the maintainer's preference for *this* codebase, not a global Claude setting. If the user wants to flip it, run `cm config set --key keep-action-log --value true`.

The choice affects §4 only; everything else (selecting docs, picking an interaction shape, deciding outcomes) is identical.

## 1. Select the doc(s)

Same topic-mapping as Q&A. Then filter to docs that have a review/evaluation section — typically `functional-doc`, `code-review/*`, `test-plan`, etc. (the user's flavor vocabulary). When in doubt, list candidates and ask.

For each selected doc, **read it in full** and extract the review items. A "review item" is anything the doc surfaces as an **open question, a suggested change, or a recommendation** — the heading that holds them varies by flavor:

- `functional-doc` → "Part 2 — Review and evaluation" (sub-sections: Questionable purposes / Behavior inconsistent with purpose / Missing behaviors / Behavioral bugs / Potential gaps), plus the Part 1 "Open questions" subsection.
- `code-review/*` → a "Findings" or "Issues" list.
- `test-plan` / `test-coverage` → "Gaps", "Open questions", or "Recommendations".
- `user-manual` → typically none, but watch for an "Open questions" or "Known issues" footer.
- `todo` → the whole doc is one item.

If the doc doesn't have an obvious review section, scan it for paragraphs framed as questions, "should we…" prompts, or "TODO/FIXME/Note:" callouts and treat those as items. Treat each bullet/paragraph as one item.

Build a working list with a stable order (doc, subsection, position) so you can refer to items by number throughout the session.

## 2. Pick an interaction shape

Offer the user three shapes (use AskUserQuestion if unclear):

- **One-by-one walkthrough.** Present each item with full context; for each, ask what to do (address now / defer / dismiss / note for later / convert to external action). User paces.
- **Filter then process.** Show the full list first; user picks a subset (by category, by confidence, by area, or by explicit selection); then walkthrough only those.
- **Auto-address all (with checkpoints).** For each item, draft a proposed action (a fix, a clarification, a defer-with-reason, etc.) and present in batches. User approves/rejects per item; rejected items stay open.

Defaults: if the doc has ≤5 items, one-by-one. If >5, offer filter-then-process. Auto-address only on explicit user request — it consumes the most tokens and warrants the user actively opting in.

## 3. For each item, decide an outcome

Outcomes are not constrained to "fix the code". A review item can resolve as any of:

- **Address now (code change)** — make the code change in the codebase; record what was done.
- **Address now (code comment)** — the underlying concern is real but the right response is to encode the invariant / caveat as a comment in the relevant code (not as a code-behavior change and not as an edit to the review doc). Common when the item flags a "fragile invariant" or "surprising assumption" that future readers of the code need to see.
- **Address now (doc clarification)** — the item turns out to be a documentation bug; fix the doc. The target can be any doc *other than the review item itself* — including the descriptive part of the same review doc (e.g. Part 1 of a functional-doc), a different meta-doc, architecture-intent, a sibling functional doc, CLAUDE.md, a README, etc. What is **not** allowed is treating "edit the review-item bullet itself" as a resolution: in lightweight mode that bullet is deleted as the item is resolved, so editing it as the "fix" while also deleting it is incoherent. If the only meaningful clarification *is* the bullet itself, the item is effectively a no-op and should be dismissed with a reason. Updating Part 1 prose (or other descriptive sections) of the same doc to reflect post-resolution reality — or to clarify a misreading the item identified — is fine and often the right move.
- **Defer** — real issue, not now; record why + when to revisit.
- **Dismiss** — not actually an issue (e.g. the reviewer was wrong, or context has changed); record the reason.
- **Convert to external action** — file an issue, open a PR, post to a tracker; record the link.

Always record *why* the outcome was chosen, in one short line. Future-you reading the action log six months later needs the reason, not just the verdict.

When the outcome is a code change, make the change as you would normally (Edit/Write tools), keep it minimal and scoped to the item, and verify it works before marking the item addressed.

## 4. Update the doc in place

The mechanics depend on `keep-action-log` (see §0). In both modes, after code changes, update the doc's `summary` via `cm doc update` if the scope shifted (e.g. an item was resolved that the summary called out).

**Do not bump `source_ref` yourself.** The SHA only meaningfully changes when the user commits, and the user is the one who commits — so the user will request the bump. After the resolutions, *suggest* the next steps ("commit these changes, then ask me to bump doc N's `source_ref` to the new SHA"), but leave the actual `--source-ref` update to a follow-up turn the user initiates. Setting it to the current HEAD before the commit lands records a SHA that does not contain the resolutions and is actively misleading.

Effective ops:
```
cm doc update --db <DB> --id <doc-id> [--summary "<...>"]      # safe to run now
cm doc update --db <DB> --id <doc-id> --source-ref <new-sha>   # only when user asks, after their commit
cm doc show   --db <DB> --id <doc-id>
```

### 4a. Lightweight mode (`keep-action-log` OFF — default)

Items are *removed* from the doc as they're resolved. Deferred work becomes a separate `todo` meta-doc so it stays trackable without bloating the review.

Per outcome (in each case, "delete the item" means remove its whole bullet/paragraph from wherever it lives in the doc, including sub-bullets):

- **Address now (code change)** — make the code change. Then delete the item. If the fix is partial or you decide to defer follow-ups, create a `todo` meta-doc (see below) for the remainder before deleting the item.
- **Address now (code comment)** — add the comment to the relevant code file. Then delete the item.
- **Address now (doc clarification)** — fix the *related* doc (not the review doc itself — see §3). Then delete the item. If the clarification reveals follow-up work, file it as a `todo`.
- **Defer** — **do not** edit the doc to add a "deferred" marker in place. Instead, write a new `todo` meta-doc (see below), then delete the item.
- **Dismiss** — just delete the item. No todo, no marker.
- **Convert to external action** — file the issue / PR / tracker entry, then delete the item. The external system is the record; no todo needed.

If a subsection (e.g. "Behavioral bugs", "Findings", "Open questions") ends up empty after processing, you may leave the heading in place with a brief `_(no remaining items)_` placeholder or remove the heading — either is fine; pick whichever reads better.

**Creating a `todo` meta-doc.** Write a short markdown file (one screen) at `<docs-root>/todo-<topic-slug>-<short-name>-<YYYY-MM-DD>.md` containing:
- A one-line title.
- A 1–3 sentence description of what the deferred/follow-up item is and why it was deferred.
- A pointer back to the source review by doc id and section heading as it appears in the source doc (e.g. "From doc id 5, Part 2 — Behavioral bugs", or "From doc id 12, Findings #4").
- A revisit hint if there is one (a trigger, a date, a dependency).

Then register it:
```
cm doc add --db <DB> --flavor todo --title "<one-line title>" \
  --doc-path <path> --topics <same topic slug(s) as the source review> \
  --source-ref <SHA> --created-by claude \
  --summary "<one-line summary>"
```

Don't invoke `write-meta-doc` for todos — they're deliberately one-screen notes, not full meta-docs.

### 4b. Heavyweight mode (`keep-action-log` ON)

Preserve every item and append a per-item action-log entry.

1. **Mark the item resolved in the doc.** Edit the original `doc_path` file: add a `**Resolved (YYYY-MM-DD): <one-line outcome>**` line directly under the item, or wrap the item text in a way that survives markdown rendering (a `> Resolved: …` blockquote works well). Don't delete the original wording — readers should still see what was reviewed.
2. **Append an action-log section if not present.** At the bottom of the doc, under `## Review action log`, add a dated entry per processed item:
   ```markdown
   ## Review action log

   ### 2026-06-01
   - **Item:** Behavior inconsistent with purpose — admin-installer's two install paths
   - **Outcome:** Address now (doc clarification)
   - **Note:** Added a "Built-in installs vs. manifest-driven installs" subsection to Part 1 explaining the split; no code change.
   ```

If the user is processing many items in one session, batch the file edits (one big Edit per doc rather than one per item) but keep the action log entries individual.

## 5. End-of-session summary

At the end of a processing session, give the user a one-screen summary:

- Items processed: N
- Outcomes: M addressed (code), K addressed (doc), L deferred, P dismissed, Q external
- Items remaining open in this doc: R
- Doc(s) updated: paths
- Suggested next steps (if any)

---

## Judgment notes

- **Don't silently broaden scope.** If the user asks about topic X and you find that the answer also requires reading docs on topic Y, mention it before reading. Topic discipline is what makes the doc index useful.
- **Don't fabricate citations.** If you couldn't find a doc that covers something, say so. "Per the doc" is meaningful only when the doc actually says that.
- **Doc-only answers must be flagged.** This is the single most important habit — the user should never have to ask "is that from the doc or from the code?"
- **Resolution-edit policy follows the setting.** In heavyweight mode, preserve every review item with a resolution marker in place so a reader six months from now can see what was reviewed. In lightweight mode (default), remove resolved items from the doc and keep deferred work as separate `todo` meta-docs — the index of todos becomes the trail, not the review doc.
- **Stay in your lane.** This skill resolves items via outcomes that touch code, docs, or external trackers — it does not author new meta-docs. If a resolution genuinely requires a new doc (e.g. "this gap means we need a doc on topic Y"), record that as the outcome and hand off to `write-meta-doc` in a separate run.
