#!/usr/bin/env bash
# Tests for install.sh. Every scenario runs with a throwaway HOME and
# CLAUDE_CONFIG_DIR so the real ~/.claude, ~/.local/bin and ~/agent-kb are untouched.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
INSTALL="$REPO/install.sh"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/aplite-test.XXXXXX")"
PASS=0
FAIL=0
trap 'rm -rf "$ROOT"' EXIT

ok()   { PASS=$((PASS + 1)); echo "  pass: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
check() { # check <description> <command...>
  local d="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$d"; else fail "$d"; fi
}
check_not() {
  local d="$1"; shift
  if "$@" >/dev/null 2>&1; then fail "$d"; else ok "$d"; fi
}

# new_env <name>: fresh sandbox, exports HOME / CLAUDE_CONFIG_DIR / KB_DIR.
new_env() {
  local d="$ROOT/$1"
  mkdir -p "$d/home" "$d/claude"
  export HOME="$d/home" CLAUDE_CONFIG_DIR="$d/claude"
  export KB_DIR="$d/home/agent-kb"
  # Safety: never run against anything that is not under our temp root.
  case "$HOME:$CLAUDE_CONFIG_DIR" in
    "$ROOT"/*:"$ROOT"/*) ;;
    *) echo "refusing to run outside sandbox" >&2; exit 99 ;;
  esac
}

snapshot() { find "$HOME" "$CLAUDE_CONFIG_DIR" 2>/dev/null | LC_ALL=C sort; }
inst() { bash "$INSTALL" "$@"; }

echo "== fresh install"
new_env fresh
OUT="$(inst 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && ok "install exits 0" || { fail "install exits 0"; echo "$OUT"; }
check "plugin listed by claude plugin list" bash -c 'claude plugin list | grep -q "pipeline@agentic-pipeline-lite"'
check "plugin enabled" bash -c 'claude plugin list | grep -q "enabled"'
check "marketplace registered" bash -c 'claude plugin marketplace list | grep -q "agentic-pipeline-lite"'
check "kb link exists" test -L "$HOME/.local/bin/kb"
check "kb link points into repo" bash -c '[ "$(readlink "$HOME/.local/bin/kb")" = "'"$REPO"'/bin/kb" ]'
check "kb dir exists" test -d "$KB_DIR"
for d in notes journal inbox; do check "kb has $d/" test -d "$KB_DIR/$d"; done
check "kb is a git repo with a commit" git -C "$KB_DIR" rev-parse --verify HEAD
check "kb working tree clean" bash -c '[ -z "$(git -C "$KB_DIR" status --porcelain)" ]'
check "kb has no remote" bash -c '[ -z "$(git -C "$KB_DIR" remote)" ]'
check "kb gitignore covers indexes" grep -q '\.sqlite' "$KB_DIR/.gitignore"
check "pre-push hook is executable" test -x "$KB_DIR/.git/hooks/pre-push"
check_not "pre-push refuses" "$KB_DIR/.git/hooks/pre-push"
check "pre-push message says local-only" bash -c '"$KB_DIR/.git/hooks/pre-push" 2>&1 | grep -qi "local only"'

echo "== re-run is a no-op"
BEFORE="$(snapshot)"
OUT="$(inst 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && ok "second run exits 0" || fail "second run exits 0"
echo "$OUT" | grep -q "Nothing to do" && ok "second run reports nothing to do" || { fail "second run reports nothing to do"; echo "$OUT"; }
[ "$BEFORE" = "$(snapshot)" ] && ok "second run changes no files" || fail "second run changes no files"

echo "== dry-run on fresh HOME"
new_env dry
BEFORE="$(snapshot)"
OUT="$(inst --dry-run 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && ok "dry-run exits 0" || { fail "dry-run exits 0"; echo "$OUT"; }
echo "$OUT" | grep -q "dry-run" && ok "dry-run prints actions" || fail "dry-run prints actions"
check_not "dry-run created no kb" test -e "$KB_DIR"
check_not "dry-run created no link" test -e "$HOME/.local/bin/kb"
check_not "dry-run installed no plugin" bash -c 'claude plugin list 2>/dev/null | grep -q "pipeline@"'
check_not "dry-run registered no marketplace" bash -c 'claude plugin marketplace list 2>/dev/null | grep -q "agentic-pipeline-lite"'
# claude itself may write its own config files when queried; user-visible paths must be untouched.
[ ! -e "$HOME/.local" ] && ok "dry-run left HOME without ~/.local" || fail "dry-run left HOME without ~/.local"
echo "   (snapshot diff, informational)"; diff <(echo "$BEFORE") <(snapshot) | sed 's/^/   /' || true

echo "== pre-existing user file at link target"
new_env backup
mkdir -p "$HOME/.local/bin"
echo "my precious script" > "$HOME/.local/bin/kb"
OUT="$(inst 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && ok "install exits 0" || { fail "install exits 0"; echo "$OUT"; }
check "link now points into repo" test -L "$HOME/.local/bin/kb"
BAK="$(find "$HOME/.local/bin" -name 'kb.bak.*' | head -1)"
check "backup file exists" test -n "$BAK"
check "backup content preserved" bash -c '[ "$(cat "'"$BAK"'")" = "my precious script" ]'
echo "$OUT" | grep -q "back up" && ok "backup is announced" || fail "backup is announced"

echo "== uninstall"
OUT="$(inst --uninstall 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && ok "uninstall exits 0" || { fail "uninstall exits 0"; echo "$OUT"; }
check_not "plugin gone" bash -c 'claude plugin list 2>/dev/null | grep -q "pipeline@"'
check_not "marketplace gone" bash -c 'claude plugin marketplace list 2>/dev/null | grep -q "agentic-pipeline-lite"'
check_not "kb link removed" test -e "$HOME/.local/bin/kb"
check "backup file untouched by uninstall" test -f "$BAK"
check "kb kept" test -d "$KB_DIR/.git"
echo "$OUT" | grep -q "$KB_DIR" && ok "uninstall prints kb location" || fail "uninstall prints kb location"

echo "== uninstall leaves foreign links alone"
new_env foreign
mkdir -p "$HOME/.local/bin"
echo other > "$ROOT/foreign/other"
ln -s "$ROOT/foreign/other" "$HOME/.local/bin/kb"
inst --uninstall >/dev/null 2>&1
check "foreign symlink kept" test -L "$HOME/.local/bin/kb"

echo "== existing kb is left alone"
new_env keepkb
mkdir -p "$KB_DIR"
echo mine > "$KB_DIR/mine.txt"
inst >/dev/null 2>&1
check "existing kb content kept" test -f "$KB_DIR/mine.txt"
check_not "existing kb not git-initialised" test -e "$KB_DIR/.git"

echo "== --kb flag"
new_env kbflag
unset KB_DIR
inst --kb "$HOME/custom-kb" >/dev/null 2>&1
check "kb created at --kb path" test -d "$HOME/custom-kb/.git"

echo
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
