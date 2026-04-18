# AGENTS.md

Shared working-style rules for any AI coding agent operating in this repository (Claude Code, Gemini CLI, OpenAI Codex, Cursor, etc.). These are project-wide conventions, not agent-specific ones. Agent-specific entry points (`CLAUDE.md`, `GEMINI.md`, etc.) should import or reference this file.

## Working Style

- **Never commit or bump the version automatically.** Make code edits freely, but stop at the staging line. The user decides when to `git commit` and when to `bumpversion`, and they'll ask explicitly. This applies even in "auto" / "YOLO" / unattended modes.
- **Commit messages must include three sections:**
  1. **Problem** — what was broken, missing, or unsatisfactory, and why it mattered.
  2. **Design rationale** — the approach chosen and why (including alternatives considered or invariants preserved when the choice is non-obvious).
  3. **Solution** — the concrete changes made, at a level of detail a reviewer can use to audit the diff without reading it twice.

  A one-line subject plus a single terse bullet is not enough. Be specific about file/module boundaries and direction of dependencies where relevant.
