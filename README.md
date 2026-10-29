# agentic-pipeline-lite

A personal agent setup for Claude Code that works across projects:

- **Continuity**: each new session picks up the project card, open work and last handoff.
- **Persistence**: knowledge lives as plain markdown in a separate git repo (default `~/agent-kb`).
- **Self-improvement**: session journals feed a `/retro` skill that *proposes* edits; nothing changes without approval.

Status: under construction.

## Install

Requires `claude` and `git`.

```bash
./install.sh            # optional: --kb /path/to/kb, --dry-run to preview
```

This registers the repo as the local marketplace `agentic-pipeline-lite`, installs and enables `pipeline@agentic-pipeline-lite` (user scope), symlinks `bin/*` into `~/.local/bin`, and creates the knowledge base at `${KB_DIR:-~/agent-kb}` from `kb-template/` if it does not exist. The kb is **local only**: no git remote and a `pre-push` hook that refuses every push. Existing files at link targets are moved to `<path>.bak.<timestamp>`; re-running changes nothing. The plugin is read live from `plugin/` in this checkout (no cache refresh or reinstall needed): edits take effect at the next session start or `/reload-plugins`.

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

**Notes** are `notes/<id>.md`: frontmatter between `---` lines, then a markdown body. Types: `note`, `project`, `idea`, `decision`, `howto`. Fields: `id, type, title, project, tags, refs, status, created, updated`; project cards also `path, stack, commands`. Frontmatter is a restricted YAML subset: `key: value` scalars (double-quoted JSON strings when escaping is needed), inline lists `[a, b]`, and `commands` written as a `- item` block list (an inline list is accepted too). References are the `refs` field plus `[[id]]` links in the body.

Search uses a SQLite FTS5 index at `<kb>/.index/kb.sqlite` (git-ignored), rebuilt automatically when any note file changes. Without FTS5 (or with `KB_NO_FTS=1`) it falls back to ripgrep with the same output. The plugin skill `kb` teaches Claude when to read and write.

## Uninstall

```bash
./install.sh --uninstall
```

Removes the plugin, the marketplace registration and only the symlinks that point into this repo. The kb is never deleted; its location is printed.
