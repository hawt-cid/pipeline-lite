#!/usr/bin/env bash
# Installer for agentic-pipeline-lite.
# Registers this repo as a local Claude Code marketplace, installs the
# `pipeline` plugin, links bin/* into ~/.local/bin and creates a local-only kb.
set -euo pipefail

MARKETPLACE="agentic-pipeline-lite"
PLUGIN="pipeline@${MARKETPLACE}"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
KB="${KB_DIR:-$HOME/agent-kb}"
LINK_DIR="$HOME/.local/bin"
DRY_RUN=0
UNINSTALL=0
CHANGES=0

usage() {
  cat <<EOF
Usage: install.sh [--kb <path>] [--dry-run] [--uninstall] [--help]

Installs the agentic-pipeline-lite setup:
  - registers this repo as the local marketplace '${MARKETPLACE}'
    and installs + enables '${PLUGIN}' at user scope
  - symlinks every bin/* into ~/.local/bin
  - creates the knowledge base from kb-template/ if absent
    (default \$HOME/agent-kb, or \$KB_DIR, or --kb <path>); local only, no remote

Options:
  --kb <path>   knowledge base location
  --dry-run     print every action, change nothing
  --uninstall   remove plugin, marketplace and symlinks into this repo;
                the kb is never deleted
  --help        show this help

Existing files at link targets are moved to <path>.bak.<timestamp>.
Re-running is a no-op when everything is already in place.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --kb)
      [ $# -ge 2 ] || { echo "install.sh: --kb needs a path" >&2; exit 2; }
      KB="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "install.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for tool in claude git; do
  command -v "$tool" >/dev/null 2>&1 || { echo "install.sh: required tool '$tool' not found in PATH" >&2; exit 1; }
done

say() { printf '%s\n' "$*"; }

# act <description> <command...>: print, and run unless --dry-run.
act() {
  local desc="$1"; shift
  CHANGES=$((CHANGES + 1))
  if [ "$DRY_RUN" -eq 1 ]; then
    say "[dry-run] $desc"
  else
    say "$desc"
    "$@"
  fi
}

marketplace_registered() {
  claude plugin marketplace list 2>/dev/null | grep -q "❯ ${MARKETPLACE}\$"
}

plugin_installed() {
  claude plugin list 2>/dev/null | grep -q "❯ ${PLUGIN}\$"
}

plugin_disabled() {
  claude plugin list 2>/dev/null | awk -v id="❯ ${PLUGIN}" '
    $0 ~ id"$" {found=1; next}
    found && /Status:/ {print; exit}' | grep -q "disabled"
}

# True if $1 is a symlink whose target lies inside this repo.
links_into_repo() {
  [ -L "$1" ] || return 1
  case "$(readlink -m "$1")" in
    "$REPO"/*) return 0 ;;
    *) return 1 ;;
  esac
}

backup() {
  local path="$1" dest
  dest="${path}.bak.$(date +%Y%m%d%H%M%S)"
  mv "$path" "$dest"
}

install_kb() {
  cp -R "$REPO/kb-template" "$KB"
  git -C "$KB" init -q
  cat > "$KB/.git/hooks/pre-push" <<'HOOK'
#!/bin/sh
echo "pre-push: this knowledge base is LOCAL ONLY and must never be pushed." >&2
exit 1
HOOK
  chmod +x "$KB/.git/hooks/pre-push"
  git -C "$KB" add -A
  git -C "$KB" -c user.name="agent-kb" -c user.email="agent-kb@localhost" \
    -c commit.gpgsign=false commit -q -m "chore: initialize knowledge base"
}

do_install() {
  # 1. marketplace
  if marketplace_registered; then
    say "ok: marketplace ${MARKETPLACE} already registered"
  else
    act "register marketplace ${MARKETPLACE} (${REPO})" claude plugin marketplace add "$REPO"
  fi

  # 2. plugin
  if plugin_installed; then
    if plugin_disabled; then
      act "enable plugin ${PLUGIN}" claude plugin enable "$PLUGIN" --scope user
    else
      say "ok: plugin ${PLUGIN} already installed and enabled"
    fi
  else
    act "install plugin ${PLUGIN} (user scope)" claude plugin install "$PLUGIN" --scope user
  fi

  # 3. links
  if [ ! -d "$LINK_DIR" ]; then
    act "create ${LINK_DIR}" mkdir -p "$LINK_DIR"
  fi
  local src name target
  for src in "$REPO"/bin/*; do
    [ -e "$src" ] || continue
    name="$(basename "$src")"
    target="$LINK_DIR/$name"
    if links_into_repo "$target"; then
      say "ok: ${target} already links into this repo"
      continue
    fi
    if [ -e "$target" ] || [ -L "$target" ]; then
      act "back up existing ${target} to ${target}.bak.<timestamp>" backup "$target"
    fi
    act "link ${target} -> ${src}" ln -s "$src" "$target"
  done

  # 4. kb
  if [ -e "$KB" ]; then
    say "ok: kb exists at ${KB} (left alone)"
  else
    act "create local-only kb at ${KB} (git init, no remote, pre-push blocked)" install_kb
  fi

  if [ "$CHANGES" -eq 0 ]; then
    say "Nothing to do: already installed."
  elif [ "$DRY_RUN" -eq 1 ]; then
    say "Dry run: ${CHANGES} change(s) would be made."
  else
    say "Done: ${CHANGES} change(s) made. Restart Claude Code sessions to load the plugin."
  fi
}

do_uninstall() {
  if plugin_installed; then
    act "uninstall plugin ${PLUGIN}" claude plugin uninstall "$PLUGIN" --scope user
  else
    say "ok: plugin ${PLUGIN} not installed"
  fi
  if marketplace_registered; then
    act "remove marketplace ${MARKETPLACE}" claude plugin marketplace remove "$MARKETPLACE"
  else
    say "ok: marketplace ${MARKETPLACE} not registered"
  fi

  local src name target
  for src in "$REPO"/bin/*; do
    [ -e "$src" ] || continue
    name="$(basename "$src")"
    target="$LINK_DIR/$name"
    if links_into_repo "$target"; then
      act "remove link ${target}" rm -f "$target"
    fi
  done

  say "kb NOT deleted; it stays at ${KB}"
  if [ "$CHANGES" -eq 0 ]; then
    say "Nothing to do: already uninstalled."
  elif [ "$DRY_RUN" -eq 1 ]; then
    say "Dry run: ${CHANGES} change(s) would be made."
  fi
}

if [ "$UNINSTALL" -eq 1 ]; then
  do_uninstall
else
  do_install
fi
