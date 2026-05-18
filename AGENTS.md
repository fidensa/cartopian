# AGENTS.md — Cartopian

This file is for contributors (human or agent) working **on Cartopian itself** — the protocol spec, templates, CLI, MCP server, wrappers, and skills in this repository. It is not loaded during Cartopian PM mode and does not describe how to manage a Cartopian-governed project. For protocol semantics (lifecycle, naming, roles, status, git), read `protocol/CONVENTIONS.md`.

## Project Overview

Cartopian is a filesystem-first project governance protocol for AI-native development. It tracks phases, tasks, specs, decisions, and reviews using plain markdown files and directory-as-status conventions. No database, no SaaS dependency, no mandatory tooling. This repository contains the protocol specification, default templates, the Core CLI, the MCP server, agent wrappers, and skills.

## Tech Stack

- **Language:** Markdown (protocol documents, templates)
- **Configuration:** TOML (`cartopian.toml` at workspace and project levels)
- **Versioning:** Git-optional — controlled per project via `cartopian.toml`
- **Runtime:** Python 3.11+ — `cli/` is the Core CLI dispatcher; `mcp_server/` is the JSON-RPC MCP server. Both use stdlib only; no third-party packages.
- **Dependency manifests:** `pyproject.toml` at the repo root — declares Python ≥ 3.11 and no third-party dependencies. Supports `pip install -e .` for contributors; the standard install uses the file-copy flow in `scripts/install.py`.

## Project Structure

> **Note to AI agents:** List the project root to discover current layout.

- **protocol/** — Baseline protocol specification (`CONVENTIONS.md`). Authoritative for all lifecycle behavior.
- **templates/** — Default file templates (TASK, SPEC, PROMPT, REVIEW, REPORT, DECISION, REQUIREMENTS, STANDARDS, IMPLEMENTATION_PLAN, PLAN_CLOSEOUT).
- **cli/** — Core CLI dispatcher (`cartopian` entry point).
- **mcp_server/** — JSON-RPC MCP server exposing Cartopian operations to MCP clients.
- **wrappers/** — Cross-platform agent CLI wrappers (`bin/` for bash, `ps1/` for PowerShell). Pre-built for Codex, Claude Code, Gemini, and Devin.
- **skills/** — Agent-executable guided workflows. Skill invocation names are derived from filenames at runtime by dropping `.md` and replacing hyphens with spaces (e.g. `init-project.md` → `init project`). The mapping is dynamic, not a maintained static list.
- **scripts/** — Installer and tooling support (`install.py` is the standard install path).
- **projects/** — Gitignored; its own git repo. Each child directory is an independent Cartopian-governed project. Useful for local end-to-end testing of the protocol; not part of this repo's source.

## Protocol Conventions

All protocol semantics — naming, status-as-directory, lifecycle authority, roles, handoffs, specs, decisions, prompts, reports, plan lifecycle, sizing limits, git behavior, session state — live in `protocol/CONVENTIONS.md`. Do not re-state those rules here; update the protocol doc and let this file stay developer-focused.

## Formatting & Linting

- No automated linting or formatting tools.
- Markdown style follows the surrounding files (sentence-case headings, fenced code blocks with language tags, ASCII punctuation).
- Python in `cli/` and `mcp_server/` uses stdlib only; keep imports sorted and avoid introducing third-party dependencies.

## Testing

- The protocol itself has no test suite; correctness is enforced by review and by the lifecycle audit (`cartopian plan-audit`).
- Python code under `cli/` and `mcp_server/` should be exercised against `projects/` fixtures when changing CLI or MCP behavior.
- Protocol-level evidence-gate discipline (`required` vs `n/a`) is a *project* concern, not a repo concern — see `protocol/CONVENTIONS.md`.

## Commit Conventions

- Git staging, commits, and pushes for **this** repository are human-owned; agents do not run `git add`, `git commit`, or `git push` here.
- Commit messages describe the change at the unit-of-work grain. Recent history is the best style reference (`git log --oneline -20`).
- Cartopian's protocol-level git automation (`auto_commit`, `auto_push`) applies to Cartopian-governed *projects*, not to this repo. See `protocol/CONVENTIONS.md § Git`.
