# agent-kb

Personal knowledge base for the agentic-pipeline-lite setup: plain markdown notes, session journals and an inbox for captured items.

- `notes/` curated knowledge
- `journal/` session journals
- `inbox/` raw captures awaiting review

Notes are `notes/<id>.md` with `---` delimited frontmatter (`id, type, title, project, tags, refs, status, created, updated`; project cards add `path, stack, commands`) and a markdown body. Use the `kb` CLI to create and search them; `kb check` validates. See the repo README for details.

**LOCAL ONLY. Never push this repo anywhere.** It may contain sensitive data. It has no git remote and a `pre-push` hook that refuses every push; do not remove either.
