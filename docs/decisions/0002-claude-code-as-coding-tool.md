# 0002: Claude Code as the coding tool

Date: 25 Oct 2026. Status: accepted.

## Context
The organisers made Cursor optional on 22 Oct 2026 and removed Cursor credits; any AI coding tool may be used.

## Decision
Use Claude Code (existing subscription) in the terminal and the VS Code extension. Project instructions live in
AGENTS.md (tool-neutral) and CLAUDE.md; rules in .claude/rules/; subagents security-breaker and load-tester;
skill /finish-feature; a Stop hook runs lint and unit tests. Claude Code never receives the NVIDIA key or the
Nightshift token.

## Consequences
Roadmap references to .cursor/rules are replaced by .claude/rules. The sponsor technology explanation names
Claude Code as the build tool.
