# pipeline-lite

A personal agent setup for Claude Code that works across projects:

- **Continuity**: each new session starts with the project card, open work and last handoff, so you don't re-explain.
- **Persistence**: knowledge lives as plain markdown in a separate, local-only git repo (default `~/agent-kb`).
- **Journal**: every session leaves a short entry (changed files, commits, summary, friction).
- **Self-improvement**: `/pipeline:retro` turns the journal into *proposed* instruction edits; nothing changes without approval.
- **Backlog**: `kb backlog` lists open tasks and ideas across all registered projects.

It pairs with the [lead workflow](https://github.com/hawt-cid/lead-workflow): the session-start hook reads the Handoff from the Lead's `.tasks/<slug>/TASK.md`. It also works without it.

Status: in use. A dogfood week on a real project is running; expect small changes from its retro.

## Install

Requires `claude`, `git` and `python3` (stdlib only; `rg` is an optional search fallback).

```bash
git clone https://github.com/hawt-cid/pipeline-lite ~/pipeline-lite
cd ~/pipeline-lite && ./install.sh     # optional: --kb /path/to/kb, --dry-run to preview
```

This registers the repo as the local marketplace `agentic-pipeline-lite`, installs and enables `pipeline@agentic-pipeline-lite` (user scope), symlinks `bin/*` into `~/.local/bin`, and creates the knowledge base at `${KB_DIR:-~/agent-kb}` from `kb-template/` if it does not exist. The kb is **local only**: no git remote and a `pre-push` hook that refuses every push. Existing files at link targets are moved to `<path>.bak.<timestamp>`; re-running changes nothing.

The plugin is read live from `plugin/` in this checkout, so `git pull` updates it. Changes take effect at the next session start or `/reload-plugins`. Switching branches in this checkout changes the hooks of every session.

Then register each project you want a card for:

```bash
kb project add <name> --path ~/work/repo --stack "python, postgres" --commands "make test"
```

## How a session works

| Hook | What it does |
|---|---|
| SessionStart | Injects the project card for the cwd, open `.tasks/*/TASK.md` handoffs (full one for the task on the current branch) and the last 5 journal entries; ≤ 8k chars. A git repo without a card gets a one-line `kb project add` hint. |
| Stop | Counts turns and keeps the last reply (≤ 500 chars) in a session state file. Never reads the transcript. |
| SessionEnd | Appends a journal entry to `<kb>/journal/YYYY-MM-DD.md`: cwd, reason, turns, duration, changed files, commits, summary, friction. No LLM, no commit. |

When you correct Claude or a tool fails, Claude logs it with `kb journal friction "<text>"`; you can too. `kb journal tail [--project P] [-n N]` shows recent entries.

The journal stores file paths, commit subjects and short summaries. It stays on your machine, but keep that in mind for work repos.

## Retro

Run `/pipeline:retro` every few days. It reads the journal and friction since the last retro (`kb retro gather`) and writes up to 5 proposals to `<kb>/inbox/retro-<date>.md`. Each one is a small edit to `CLAUDE.md`, memory or a skill, backed by at least two journal occurrences or one explicit correction. You accept or reject each one; only accepted ones are applied. `kb retro record` saves the outcome and commits the journal locally.

## Backlog and ideas

```bash
kb backlog [--project P] [--all]       # open tasks, their next step, ideas per project, inbox count
kb idea add "<text>" [--project P] [--tags a,b]   # default project = card for the cwd
kb idea done <id>
```

## kb

`bin/kb` is a stdlib-only Python CLI (package in `kb/`) over the knowledge base. Location: `--kb PATH` > `$KB_DIR` > `~/agent-kb`. The kb is local only; never push it or send its contents to external services.

```bash
kb new <type> <title> [--id ID] [--project NAME] [--tags a,b] [--refs a,b] [--body TEXT | --body -]
kb read <id> [--refs]                  # --refs: referenced notes (one level) + backlinks
kb search <query> [--type T] [--project P] [--limit N]
kb link <from> <to>                    # add a ref, idempotent
kb check                               # frontmatter, required fields, types, ids, duplicates, dangling refs
kb index                               # force an index rebuild
kb project add <name> --path DIR [--stack S] [--commands CMD ...] [--status active|paused|done]
kb project list
kb project find [--path DIR]           # card for DIR or its closest ancestor; exit 1 if none
```

`read`, `search`, `project list` and `project find` accept `--json`. `--commands` is repeatable (one command each). `proj://<name>` means id `proj-<name>` and `kb://<id>` means `<id>`; both work wherever an id is taken.

**Notes** are `notes/<id>.md`: frontmatter between `---` lines, then a markdown body. Types: `note`, `project`, `idea`, `decision`, `howto`, `retro`. Fields: `id, type, title, project, tags, refs, status, created, updated`; project cards also `path, stack, commands`. Frontmatter is a restricted YAML subset: `key: value` scalars (double-quoted JSON strings when escaping is needed), inline lists `[a, b]`, and `commands` written as a `- item` block list (an inline list is accepted too). References are the `refs` field plus `[[id]]` links in the body.

Search uses a SQLite FTS5 index at `<kb>/.index/kb.sqlite` (git-ignored), rebuilt automatically when any note file changes. Without FTS5 (or with `KB_NO_FTS=1`) it falls back to ripgrep with the same output. The plugin skill `kb` teaches Claude when to read and write.

## Tests

```bash
python3 -m unittest discover -s tests
bash tests/install_test.sh
claude plugin validate plugin && claude plugin validate .
```

## Uninstall

```bash
./install.sh --uninstall
```

Removes the plugin, the marketplace registration and only the symlinks that point into this repo. The kb is never deleted; its location is printed.
