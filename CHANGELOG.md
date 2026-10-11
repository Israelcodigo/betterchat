# Changelog

## 1.3.0rc1 — 2026-10-11

First public BetterChat release candidate, powered by Nexo. The Python package
remains `nexo-agent-kit`; the command remains `nexo`.

### Added

- Explicit task-to-provider routing with named profiles and optional model IDs.
- Gemini CLI adapter alongside Codex, Claude Code and custom command execution.
- Per-run provider, requested model, profile and elapsed-time records.
- Opt-in live provider smoke with four coordinator-owned acceptance cases.
- English/Spanish repository entry points, contribution templates and CI.
- Reproducible source archive and public inventory checks.

### Fixed

- Multiline provider error envelopes cannot silently count as successful runs.
- Ambiguous CLI routing and incomplete route maps fail before task claims.
- Windows test fixtures normalize short paths and compare ACL inheritance
  against a native sibling, keeping the suite portable across runner hosts.

## 1.2.2 — imported foundation

Local Nexo runtime with project state, dependencies, leases, worktrees, evidence,
MCP, skills, roles, hooks, a dashboard and controlled learning workflows. This
foundation is retained under its original MIT license and attribution.
