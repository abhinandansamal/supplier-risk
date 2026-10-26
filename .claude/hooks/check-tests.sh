#!/bin/bash
# Stop hook: when Claude finishes a turn, run fast checks if Python files changed.
# Exit 2 sends Claude back to fix the problem. Exit 0 lets it stop.
input=$(cat)
active=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))' 2>/dev/null)
[ "$active" = "True" ] && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

changed=$( { git diff --name-only HEAD -- '*.py'; git ls-files --others --exclude-standard -- '*.py'; } 2>/dev/null )
[ -z "$changed" ] && exit 0

if ! out=$(uv run ruff check . 2>&1); then
  printf 'ruff found problems. Fix them before finishing:\n%s\n' "$out" | tail -n 40 >&2
  exit 2
fi
out=$(uv run pytest -q -m "not integration" -x 2>&1); code=$?
if [ $code -ne 0 ] && [ $code -ne 5 ]; then   # 5 = no tests collected yet
  printf 'Unit tests are failing. Fix them before finishing:\n%s\n' "$out" | tail -n 40 >&2
  exit 2
fi
exit 0
