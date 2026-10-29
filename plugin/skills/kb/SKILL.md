---
name: kb
description: Read and write the personal knowledge base with the `kb` CLI (markdown notes in ~/agent-kb). Use at the start of unfamiliar work, before re-researching a fact, when the user mentions proj:// or kb:// ids, and to save durable facts, decisions and how-tos.
---

# Knowledge base (`kb`)

The kb is plain markdown notes, shared across all projects and sessions. The CLI is `kb`
(location: `--kb PATH` > `$KB_DIR` > `~/agent-kb`).

## Hard rule: local only

The kb is private. Never add a git remote to it, never push it, and never send its contents
to an external service (web tools, APIs, paste sites, remote models). Reading it into this session is fine.

## When to read

- At the start of unfamiliar work in a project: `kb project find` (card for the current directory), then search.
- Before re-researching a fact, a tool quirk or a past decision: search first.
- Whenever the user writes `proj://<name>` (= note `proj-<name>`) or `kb://<id>`: read that note.

```bash
kb search "sqlite wal" --type howto      # ranked hits: id, type, title, snippet
kb read proj://myapp --refs              # note + referenced notes + backlinks
kb project find                          # project card for the cwd (exit 1 if none)
kb project list
```

## When to write

Write only durable, reusable knowledge: a verified fact, a decision with its reason, a repeatable
how-to, a project card. Do not write session chatter, guesses, secrets or credentials.
Search first to avoid duplicates; link related notes.

Types: `note`, `project`, `idea`, `decision`, `howto`.

```bash
kb new howto "Rebuild the search index" --project myapp --tags search,sqlite --body "Run kb index ..."
kb new decision "Use FTS5 over vectors" --refs proj://myapp --body - <<'EOF'
Why: keyword search is enough for now. Revisit if recall is poor.
EOF
kb link kb://rebuild-the-search-index proj://myapp   # add a ref (idempotent)
kb project add myapp --path ~/code/myapp --stack "python" --commands "make test" --status active
kb check                                             # validate; run after bulk edits
```

## Session journal and friction

Hooks track each session (start, turns, last reply) and append one entry to
`<kb>/journal/YYYY-MM-DD.md` when it ends; SessionStart injects the project card, open task
handoffs and recent journal entries. You do not write those entries.

Log friction as it happens, one line, when the user corrects you or rejects your approach,
a tool or command fails unexpectedly, or you hit a repeated papercut worth fixing later:

```bash
kb journal friction "tests need KB_DIR unset or they touch the real kb"
kb journal tail --project proj-name -n 5     # recent entries, newest last
```

The note attaches to the live session (newest whose cwd equals, contains or is inside the
current dir) and lands in its journal entry; with no live session it becomes a standalone line.
The journal is local-only like the rest of the kb: it is never pushed or sent anywhere.

## Note format

`notes/<id>.md`: frontmatter between `---` lines (`id, type, title, project, tags, refs, status,
created, updated`; project cards add `path, stack, commands`), then a markdown body. Link other
notes in the body with `[[id]]`. ids are lowercase slugs. Edit notes with `kb` or directly;
`kb check` reports broken ones.
