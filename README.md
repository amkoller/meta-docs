# meta-docs

Three composable Claude Code skills for managing **meta-documentation** about a codebase — the topics it's organized into, the modules (files, dirs, symbols) that belong to each topic, and the documents (functional reviews, user manuals, code reviews, test plans, todos, etc.) that describe, audit, or plan them.

The three skills form a producer / index / consumer trio:

| Skill | Role |
|---|---|
| **meta-doc-manager** | Index. Tracks topics, modules, and document registrations in a per-project SQLite database. Provides a `cm.py` CLI. Track-and-index only — does not author content. |
| **write-meta-doc** | Producer. Drives an authoring loop (topic scope → module scope → drafting → registration) for new meta-documents and registers them via meta-doc-manager. |
| **process-meta-doc** | Consumer. Uses registered docs to answer questions about the codebase or to walk the user through Part-2-style review findings (questionable purposes, missing behaviors, suggestions). |

## Installation

### As a Claude Code plugin

Add this repo as a marketplace and install the plugin:

```
/plugin marketplace add https://github.com/akoller/meta-docs
/plugin install meta-docs
```

All three skills become available together.

### Manual (symlink into ~/.claude/skills/)

```sh
git clone https://github.com/akoller/meta-docs.git
ln -s "$PWD/meta-docs/skills/meta-doc-manager" ~/.claude/skills/meta-doc-manager
ln -s "$PWD/meta-docs/skills/write-meta-doc"   ~/.claude/skills/write-meta-doc
ln -s "$PWD/meta-docs/skills/process-meta-doc" ~/.claude/skills/process-meta-doc
```

## Quick start

In a project you want to document, ask Claude:

> Set up meta-docs for this repo — establish topics for the major parts and assign modules to them.

That invokes **meta-doc-manager** to `init` the SQLite index and define topics/modules.

Then:

> Write a functional review of the `auth` topic.

…invokes **write-meta-doc**, which scopes the work, drafts the document with you in the loop, writes it to disk, and registers it back into the index.

Later:

> What does the auth review say about session token storage?

…invokes **process-meta-doc**, which looks up the relevant registered doc and answers with citation.

## Repository layout

```
.claude-plugin/plugin.json     ← plugin manifest
skills/
  meta-doc-manager/            ← SQLite index + cm.py CLI
  write-meta-doc/              ← authoring workflow
  process-meta-doc/            ← Q&A + review-processing workflow
```

## License

MIT. See [LICENSE](LICENSE).
