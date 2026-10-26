---
name: finish-feature
description: Run the end-of-feature checklist - tests, lint, types, security review, test logs, devlog, push, PR and merge only after GitHub Actions passes.
---
Stop at the first failure and explain it. Never run these steps on main.
1. Confirm the current branch is not main (git branch --show-current).
2. uv run ruff check . && uv run ruff format --check .
3. uv run mypy
4. uv run pytest -q -m "not integration"   (writes reports/junit.xml and coverage)
   Then: uv run python scripts/test_report.py reports/junit.xml --title "Unit tests" > /tmp/unit.md
5. Ask me whether Zetaris, Nightshift and the context service are running. If yes:
   uv run pytest -q -m integration -k <area> --junitxml=reports/junit-integration.xml
   Then: uv run python scripts/test_report.py reports/junit-integration.xml --title "Integration tests" > /tmp/int.md
   If no, write "Integration tests: not run (reason)".
6. Use the security-breaker subagent on the files changed on this branch (git diff main --name-only).
   Fix Critical/High findings with tests first, then repeat steps 2-4.
7. Check logs/errors.jsonl for unexpected ERROR/CRITICAL lines produced during the tests and explain them.
8. Test log: create docs/test-runs/<YYYY-MM-DD>-<branch-with-slashes-replaced>.md from /tmp/unit.md and
   /tmp/int.md (plus coverage %), and append one line to docs/test-log.md.
9. Append a devlog entry to docs/devlog.md: date, feature, tests added, results, security finding ids, decisions.
10. Show me the summary and the proposed commit message. After my OK: commit, git push -u origin <branch>,
    gh pr create --fill.
11. gh pr checks --watch. If all checks pass, ask me, then gh pr merge --merge --delete-branch and
    git checkout main && git pull. If a check fails, show the failing step and fix it on the branch.
