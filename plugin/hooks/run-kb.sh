#!/usr/bin/env bash
# Runs `kb <args>` for a plugin hook. Exits 0 silently if kb is unavailable:
# hooks must never break a session.
if command -v kb >/dev/null 2>&1; then
  kb "$@"
elif [ -x "${CLAUDE_PLUGIN_ROOT:-}/../bin/kb" ]; then
  "${CLAUDE_PLUGIN_ROOT}/../bin/kb" "$@"
fi
exit 0
