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

This registers the repo as the local marketplace `agentic-pipeline-lite`, installs and enables `pipeline@agentic-pipeline-lite` (user scope), symlinks `bin/*` into `~/.local/bin`, and creates the knowledge base at `${KB_DIR:-~/agent-kb}` from `kb-template/` if it does not exist. The kb is **local only**: no git remote and a `pre-push` hook that refuses every push. Existing files at link targets are moved to `<path>.bak.<timestamp>`; re-running changes nothing.

## Uninstall

```bash
./install.sh --uninstall
```

Removes the plugin, the marketplace registration and only the symlinks that point into this repo. The kb is never deleted; its location is printed.
