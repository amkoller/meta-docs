# Flavor: functional-doc

A functional doc is about **functionality**, not code. It describes what the system *does* and why — behaviors, flows, purposes, guarantees, surprises — in terms a reader can understand without needing to open a single source file.

Modules (files, directories, classes) are *implementations* of functionality. They are how the system happens to be organized today; they could be reorganized tomorrow without the functionality changing. A functional doc is therefore organized **by functional concept**, not by module. Modules appear inline as implementation notes — "this is realized in `apps/admin-web/src/lib/cognito-auth.ts`" — when grounding the claim helps a reader navigate, not as section headings.

This split makes the doc useful long after a refactor that moves the code around.

## Two top-level sections

1. **Current state** — what the system does today.
   - **Purposes / goals** of each functional area, inferred from the code's behavior and shape, the topic it belongs to, and any explicit documentation. Describe these as what the system *is for*, not what specific files do.
   - **Behaviors**, split into:
     - **User-facing behavior** — anything triggered from or presented in a UI, CLI, or other surface a human directly touches (screens, commands, log lines a human reads, error messages they see).
     - **Internal behavior** — invariants, side effects, state machines, ordering guarantees, retries, caching, failure modes — things that don't show up in the UI but matter for understanding what the system *actually does*.

2. **Review and evaluation** — a critical pass over what section 1 documented.
   - **Questionable purposes** — areas whose reason-for-existing is unclear or seems misaligned with the topic.
   - **Behavior apparently inconsistent with purpose** — places where the system does something different from (or beyond) what its purpose suggests.
   - **Missing behaviors** — things you'd expect the system to do given its purpose, but doesn't.
   - **Behavioral bugs** — incorrect logic, race conditions, broken error handling, etc. that you observed while reading.
   - **Potential gaps** — broader holes in the topic's coverage. **High bar:** only include a gap if you're highly confident it's real, not a "huh, I wonder if…". Gaps are the noisiest category and the easiest to fill with speculation; one well-grounded gap is worth ten hedged ones. When unsure, drop it or move it to "Open questions" in section 1.

Keep the two sections cleanly separated. Section 1 is descriptive; if you catch yourself making judgments, move them to section 2.

## Drafting procedure

This runs after `write-meta-doc` has fixed the topic scope and assembled the provisional module list. You have: the DB path, the topic slugs, and the module list.

### 1. Read the code

Read the actual code for each module in scope. You're reading it for **what the system does**, not to summarize the code itself. Look for: entry points (where users touch the system), persistent state (what survives a restart), data flows (what calls what, in what order), invariants the code is trying to maintain, surprising behavior, failure modes.

For directory modules, read enough to identify the meaningful behavior — you don't need every line. Files you keep returning to are the ones to ground claims in; throwaway helpers usually don't make the doc.

**Also read any relevant automated test coverage** — unit tests, integration tests, end-to-end tests that exercise the in-scope modules. Tests are a second source of truth for *intended* behavior: they encode the invariants and edge cases the author thought mattered, often more explicitly than the production code does. Use them to:

- Confirm or correct behaviors inferred from reading the code (if a test asserts X but you thought the code did Y, dig in).
- Surface behaviors you might otherwise miss — error paths, retry semantics, boundary conditions.
- Distinguish "the code happens to do this" from "the system is required to do this".

When tests and code disagree, note it as an open question or — if you're confident — as a Part 2 behavioral bug. Don't treat tests as gospel; they can be stale too. But they're high-signal evidence about what the author *meant*.

While reading, keep a running list of:
- Functional concepts emerging from what you're reading — bootstrap, install lifecycle, authentication, sync, persistence, etc. These are your future section headings.
- Things you don't understand and need to ask the user about.
- Code that seems in-scope for the topic but isn't in the module list (candidates for scope expansion — see SKILL.md phase 2).

### 2. Ask clarifying questions

Before drafting, batch the open questions. Useful angles:

- **Purpose ambiguity.** "Module M and module N both seem to do X — is one the canonical path?"
- **Audience.** "Who reads this doc — new contributors, ops, end users? It changes how much background to assume."
- **Boundary calls.** "The flow extends into topic Y which is out of scope — describe Y's relevant surface as a black box, or stop at the boundary?"
- **Intent vs. implementation.** "The code does X but the comment says Y — which is the truth?"

If you have no real questions, don't manufacture any.

### 3. Draft

Structure the document by functional concept. The default skeleton:

```markdown
# <Title — topic name(s), flavor, date>

# Part 1 — Current state

## Overview
One-to-three paragraphs: what this topic *is* — what it lets users do, where it sits in the broader system, the two-or-three concepts that organize the rest of Part 1.

## <Concept 1: e.g. "Cloud bootstrap">
What this part of the system is for, what a user (or caller) sees, what happens internally to make it work, the key invariants. Cite specific files/functions inline (`apps/admin-web/src/components/CloudSetupWizard.tsx`) when grounding a claim — sparingly, only when it helps a reader navigate.

## <Concept 2: e.g. "App install lifecycle">
...

## <Concept 3, …>
...

## Open questions
Things the code didn't answer that the user didn't either. Leave these in — a doc that pretends to have all the answers is less useful than one that says "this isn't clear". (Distinct from Part 2's "potential gaps": open questions are uncertainties; gaps are confidently-identified holes.)

# Part 2 — Review and evaluation

## Questionable purposes
Per-concept or topic-level observations about purpose being unclear or misaligned. Reference Part 1's concept sections. Omit the subsection if there's nothing to say.

## Behavior inconsistent with purpose
Where the system does something different from or beyond what its stated/implied purpose suggests.

## Missing behaviors
Things a purpose implies the system should do, but it doesn't.

## Behavioral bugs
Incorrect logic, races, broken error handling, etc., observed while reading.

## Potential gaps
Broader holes in the topic's coverage. Include sparingly and only when confident. If you have nothing high-confidence to say, write "None identified with high confidence." rather than speculating.
```

### Style — keep it functional, keep it light

- **Concepts as headings, not modules.** "Cloud bootstrap" is a heading; `packages/admin-core` is not. If a concept genuinely maps 1:1 to a module, the heading can match the concept's plain name (e.g. "Manifest validation") and the module is cited inline.
- **Concepts must be facets of *this* topic, not of adjacent topics the code happens to live in.** When the in-scope topic set includes a cross-cutting child (`<topic>-cloud`, `<topic>-local`, `<topic>-sync`), the corresponding section is "how the topic appears in that environment" — *not* a general tour of the environment. If you find yourself explaining how the cloud Lambda authenticates, how the local server routes requests, how the sync supervisor schedules ticks: that's the adjacent topic, not yours. Replace the detail with a one-sentence pointer (e.g. "Mechanics of how the Lambda reaches DSQL — STS assumption, per-app PG roles — live with the `cloud-server-auth` topic, not here"). The litmus: if a paragraph would read essentially the same were your topic swapped for a sibling topic in the same tree, it belongs in the environment's own doc.
- **Technical detail is light by default.** Include code structure, types, file paths only when they clarify the *functionality* — e.g. naming the state-machine ledger table because a reader needs to understand idempotency; not listing every helper function in a module. A reader who wants the implementation can open the file you cited.
- **Prefer declarative prose to bullet salad.** A functional doc that reads like a colleague explaining the system is more useful than one that reads like an outline. Use bullets for genuine lists (steps in a flow, error codes, parameters), not for every sentence.
- **Describe what a behavior accomplishes for the user (or for the system as a whole) before describing how it's wired up.** "When the operator clicks Install, the orchestrator…" is a better opening than "`installApp()` is called with…".
- **Empty Part 2 subsections are omitted, not padded.** If you have nothing to say about behavioral bugs, drop the subsection.

### 4. Hand back to write-meta-doc

When the draft is ready, return to phase 4 in SKILL.md: write to disk, iterate with the user, register.

If the module list grew during drafting (you added modules via `cm module add`/`assign`), make sure write-meta-doc's `--modules` argument at registration time reflects the expanded set — though if the doc is fundamentally topic-scoped, linking only via `--topics` is usually cleaner.
