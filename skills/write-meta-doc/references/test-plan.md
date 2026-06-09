# Flavor: test-plan

A test plan describes **what should be tested** for the in-scope modules, at a planning level — not the test code itself. It's a "Plan mode" artifact: enough detail to agree on coverage, shallow enough that implementation choices are still open when someone (or you, in a later turn) sits down to write the tests.

The plan answers three questions:

1. **What's already covered?** A summary of the existing automated test coverage relevant to the in-scope modules.
2. **What important behavior should be covered but isn't?** Important cases and edge cases drawn from the functional review, weighted toward what's actually risky or under-tested.
3. **What infrastructure does the testing need?** Brief notes on mocks, fixtures, integration setups, test harnesses, or other implementation gaps that have to exist before the assertions can be written.

A test plan is not a test spec. It does not list every assertion. It does not give pseudo-code. It says: "we should test that X behaves correctly when Y; this will need a fake Z."

## Prerequisites

A test plan leans heavily on a **functional review (Part 1)** for the same topic scope. If one doesn't exist or is stale, stop and ask the user whether to:

- Write the functional review first (recommended — invoke `write-meta-doc` for the functional-doc flavor), or
- Proceed without one, accepting that the plan will be shallower and may miss important behavior the review would have surfaced.

If a functional review exists, read it (via `meta-doc-manager` / `process-meta-doc`) before drafting. Part 1's concept sections and behaviors are your map of what *matters*; Part 2 is useful context but not the primary driver of test cases.

## Drafting procedure

Runs after `write-meta-doc` has fixed the topic scope and assembled the provisional module list. You have: the DB path, the topic slugs, and the module list.

### 1. Inventory existing test coverage

For each in-scope module, find the tests that exercise it. Look in the conventional locations for the project's languages/frameworks (e.g. `__tests__/`, `*_test.go`, `tests/`, `spec/`, sibling `*.test.ts` files, etc.). When the project's layout isn't obvious, ask the user or grep for the test runner's config.

For each relevant test file or suite, note briefly:

- **What it covers** — which behaviors, in plain language. Not a test-by-test enumeration; a one-or-two-line summary per suite is usually right.
- **What kind of test it is** — unit, integration, e2e, contract, snapshot, etc. — and what it mocks vs. exercises for real.
- **Obvious gaps in that suite** — behaviors the suite *almost* covers but stops short of.

This becomes the plan's opening section. It also tells you what *not* to re-propose later.

### 2. Read the functional review (Part 1)

Re-read Part 1 of the functional review with a tester's eye. For each concept section, ask:

- What are the user-facing behaviors that, if they broke, a user would immediately notice?
- What are the internal invariants the system is trying to maintain?
- Where are the boundaries (auth, persistence, retries, ordering) that tend to fail?
- What edge cases does Part 1 hint at — explicit failure modes, "if the user does X mid-flow" notes, race conditions, idempotency claims?

The review is a guide, **not exhaustive**. You're not trying to write a test for every sentence; you're picking the important behaviors and the risky edges.

Also glance at Part 2 (behavioral bugs, missing behaviors) — these often correspond to tests worth adding even if the bug itself is fixed separately, because a test pins down the correct behavior going forward.

### 3. Draft the plan

Structure the document with these sections:

```markdown
# <Title — topic name(s), test plan, date>

## Existing coverage

Summary of relevant automated tests for the in-scope modules.

### <Suite or area 1>
- What it covers: …
- Kind: unit / integration / e2e / …; mocks: …
- Gaps: …

### <Suite or area 2>
…

(If there's no existing coverage at all, say so plainly: "No automated tests cover these modules today.")

## Behaviors to cover

Organized by functional concept (mirroring the functional review's Part 1 concepts). For each concept, the important cases and edge cases worth asserting — focused on **what isn't already covered** by the existing suite.

### <Concept 1: e.g. "Cloud bootstrap">
- Important cases:
  - <one-line description of a behavior to assert>
  - …
- Edge cases:
  - <one-line description of an edge case>
  - …

### <Concept 2>
…

## Implementation notes

Brief notes on what the tests will need to *exist* before they can be written. This is the "Plan mode" half — gaps in test infrastructure, not gaps in coverage.

- Mocks / fakes needed (e.g. "fake S3 client with controllable failure injection", "in-process Postgres via testcontainers").
- Fixtures or seed data (e.g. "sample manifest files covering the three supported schemas").
- Test harness work (e.g. "no integration harness exists for the local server; needs one before the install-lifecycle suite is feasible").
- Boundary decisions (e.g. "should the auth tests use a real Cognito user pool in a test stage, or a mocked JWT issuer?") — flag as open questions if not decided.

## Open questions

Anything the plan needs the user to decide before implementation starts — scope calls, infra trade-offs, "is this behavior even intended".
```

### Style — keep it plan-shaped

- **No assertion-level detail.** "Assert that an expired token returns 401" is fine; "expect(response.status).toBe(401)" is too far. The implementation phase decides shape.
- **One-liners over paragraphs.** Each case is a bullet. If a case needs a paragraph to explain, it's probably actually two cases or belongs in the implementation-notes section.
- **Focus on *uncovered* important behavior.** If the existing suite already covers a case well, don't re-list it under "Behaviors to cover" — that's noise. The plan's value is in the delta.
- **Important > exhaustive.** A plan that lists 200 trivial cases is worse than one that lists 20 cases that actually matter. Bias toward behaviors a user would notice breaking, invariants the system depends on, and edges where failure modes hide.
- **Flag infra gaps loudly.** If writing the tests requires building a harness that doesn't exist, that's a bigger deal than any individual case — put it in implementation notes with a clear "this blocks the rest of the plan" if applicable.
- **Cite the functional review where useful.** "See functional review's 'Cloud bootstrap' section for the behavior under test" is fine; copying Part 1 prose into the plan is not.

### 4. Hand back to write-meta-doc

When the draft is ready, return to phase 4 in SKILL.md: write to disk, iterate with the user, register.
