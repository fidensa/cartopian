# AGENTS.md — Cartopian

## Project Overview

Cartopian is a filesystem-first project governance protocol for AI-native development. It tracks phases, tasks, specs, decisions, and reviews using plain markdown files and directory-as-status conventions. No database, no SaaS dependency, no mandatory tooling. This repository contains the protocol specification and default templates.

## Tech Stack

- **Language:** Markdown (protocol documents, templates)
- **Configuration:** TOML (`cartopian.toml` at workspace and project levels)
- **Versioning:** Git-optional — controlled per project via `cartopian.toml`
- **Runtime:** Python 3.11+ — `cli/` is the Core CLI dispatcher; `mcp_server/` is the JSON-RPC MCP server. Both use stdlib only; no third-party packages.
- **Dependency manifests:** `pyproject.toml` at the repo root — declares Python ≥ 3.11 and no third-party dependencies. Supports `pip install -e .` for contributors; the standard install uses the file-copy flow in `scripts/install.py`.

## Project Structure

> **Note to AI agents:** List the project root to discover current layout.

- **protocol/** — Baseline protocol specification (`CONVENTIONS.md`)
- **templates/** — Default file templates (TASK, SPEC, PROMPT, REVIEW, REPORT, DECISION, REQUIREMENTS, STANDARDS, IMPLEMENTATION_PLAN, PLAN_CLOSEOUT)
- **wrappers/** — Cross-platform agent CLI wrappers (`bin/` for bash, `ps1/` for PowerShell). Pre-built for Codex, Claude Code, Gemini, and Devin.
- **skills/** — Agent-executable guided workflows, with workflow details in each skill file. Planning, task, and review workflows understand CLI handoff automation.
- **Skill invocation names:** Natural-language skill names are derived from `skills/*.md` filenames at runtime by dropping `.md` and replacing hyphens with spaces, e.g. `init-project.md` maps to `init project`. The mapping is dynamic, not a maintained static list.
- **projects/** — Gitignored; its own git repo. Each child directory is an independent project with its own config, state, phases, tasks, and decisions.

## Code Conventions

- **Naming is load-bearing.** Every artifact follows strict naming patterns defined in `protocol/CONVENTIONS.md`. Phase-scoped prefixes (`NN-NNN`) create a trace chain from `IMPLEMENTATION_PLAN.md` to Phase to Task to Spec/Prompt/Review.
- **Status is a directory.** Task status is represented solely by `open/`, `in-progress/`, `in-review/`, or `done/`; task files have no `status:` field.
- **Specs are mutable.** Specs are single-file contracts without version suffixes or supersession chains.
- **Decisions are immutable.** Superseding decisions are new files; old decision files remain unchanged.
- **Prompts are temporary.** Prompt files are assignee handoff artifacts in `prompts/`, not durable archives.
- **Reports are temporary.** Report files are handoff result artifacts in `reports/`, read by the PM and cleared during plan reset.
- **Roles are operator-chosen.** `[roles]` maps role names to one-line descriptions; whether a role dispatches automatically is inferred from the presence of a `[handoffs.<role>]` block.
- **One active plan.** A project has one live `IMPLEMENTATION_PLAN.md` at a time. Run `close plan` before starting a fresh plan for the same project.
- **Plan reset discipline.** Requirements and implementation plans always reset between plans. Project standards and conventions carry forward only by explicit operator choice.
- **`STATE.md` ceiling:** 5KB hard limit per project.
- **Lifecycle authority:** Task movement, review verdicts, session state, and Git behavior are governed by `protocol/CONVENTIONS.md`.
- **Session startup safety:** For vague PM startup requests such as "start working", "continue", or "check STATE.md", use `skills/start-session.md`. If more than one project exists and no project is specified, ask which project to use before reading or mutating project lifecycle artifacts. When this agent is PM for the selected project, read `STATE.md`, briefly summarize the current/next protocol action, and suggest the most logical next step to move the project forward. Do NOT offer alternative work or introduce alternative steps; follow the plan.
- **Protocol protection:** The PM is strictly forbidden from making changes to the cartopian codebase. The PM role is intended to work within designated project management directories only and may only CREATE/READ/UPDATE/DELETE MARKDOWN files. Use roles for other operations.

## Formatting & Linting

- No automated linting or formatting tools. Conventions are enforced by protocol discipline at review time.
- Structural rules live in `protocol/CONVENTIONS.md`.

## Testing

- **Evidence gate discipline** is per-task, not per-repo. Tasks declare `Evidence gate: required` or `Evidence gate: n/a` in their files.
- Reviews of `required` tasks must record red-before-green evidence.

## Commit Conventions

- Git operations are optional (`git_versioning` in `cartopian.toml`).
- When enabled, commits describe the change at the unit-of-work grain.
- Auto-commit and auto-push happen at session close — invisible to operator.
- Git staging, commits, and pushes for the protocol repo are human-owned; agents do not run `git add`, `git commit`, or `git push` here.

## Roles & Assignment

- Roles exist to be assigned, which means a PM who takes on the work rather than assigning it is undermining the entire system. The PM is NEVER allowed to make changes outside the project currently being managed. Assign those tasks to the correct roles.
- Default roster: PM and Operator. Common example labels operators add are Coder and Reviewer, but role names are operator-chosen.
- `[roles]` in `cartopian.toml` maps each role name to a one-line description. There is no `kind` field; whether a role dispatches automatically is inferred from the presence of a `[handoffs.<role>]` block.
- Role defaults live in `cartopian.toml`; project-level configs may override them.
- The same agent can be assigned to multiple roles. The human operator may take on multiple roles as well.
- Guided workflows live in `skills/`.
- Roles serve as a communication aid between the AI Project Manager and the human operator. They can be renamed, removed, or extended since they don't map to any actual software executables.
