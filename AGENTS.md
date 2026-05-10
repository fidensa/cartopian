# AGENTS.md — Cartopian

## Project Overview

Cartopian is a filesystem-first project governance protocol for AI-native
development. It tracks phases, tasks, specs, decisions, and reviews using
plain markdown files and directory-as-status conventions. No database, no
SaaS dependency, no mandatory tooling. This repository contains the
protocol specification, default templates, and a sample project.

## Tech Stack

- **Language:** Markdown (protocol documents, templates)
- **Configuration:** TOML (`cartopian.toml` at workspace and project levels)
- **Versioning:** Git-optional — controlled per project via `cartopian.toml`
- **Runtime:** None. This is a protocol specification repo, not a software project.
- **Dependency manifests:** None at the repo root.

## Project Structure

> **Note to AI agents:** List the project root to discover current layout.

- **protocol/** — Baseline protocol specification (`CONVENTIONS.md`)
- **templates/** — Default file templates (TASK, SPEC, PROMPT, REVIEW,
  REPORT, DECISION, REQUIREMENTS, STANDARDS, IMPLEMENTATION_PLAN,
  PLAN_CLOSEOUT)
- **wrappers/** — Cross-platform agent CLI wrappers (`bin/` for bash,
  `ps1/` for PowerShell). Pre-built for Codex, Claude Code, Gemini,
  and Devin.
- **skills/** — Agent-executable guided workflows, with workflow details
  in each skill file.
  Planning, task, and review workflows understand CLI handoff automation.
- **Skill invocation names:** Natural-language skill names are derived from
  `skills/*.md` filenames at runtime by dropping `.md` and replacing hyphens
  with spaces, e.g. `init-project.md` maps to `init project`. The mapping is
  dynamic, not a maintained static list.
- **projects/** — Gitignored; its own git repo. Each child directory is an
  independent project with its own config, state, phases, tasks, and decisions.
  Only `projects/sample-project/` ships with the protocol repo.

## Code Conventions

- **Naming is load-bearing.** Every artifact follows strict naming patterns
  defined in `protocol/CONVENTIONS.md`. Phase-scoped prefixes (`NN-NNN`)
  create a trace chain from `IMPLEMENTATION_PLAN.md` to Phase to Task to
  Spec/Prompt/Review.
- **Status is a directory.** Task status is represented solely by `open/`,
  `in-progress/`, `in-review/`, or `done/`; task files have no `status:` field.
- **Specs are mutable.** Specs are single-file contracts without version
  suffixes or supersession chains.
- **Decisions are immutable.** Superseding decisions are new files; old decision
  files remain unchanged.
- **Prompts are temporary.** Prompt files are assignee handoff artifacts in
  `prompts/`, not durable archives.
- **Reports are temporary.** Report files are handoff result artifacts in
  `reports/`, read by the PM and cleared during plan reset.
- **Roles are operator-chosen.** `[roles]` maps role names to one-line
  descriptions; whether a role dispatches automatically is inferred
  from the presence of a `[handoffs.<role>]` block.
- **One active plan.** A project has one live `IMPLEMENTATION_PLAN.md` at a
  time. Run `close plan` before starting a fresh plan for the same project.
- **Plan reset discipline.** Requirements and implementation plans always reset
  between plans. Project standards and conventions carry forward only by
  explicit operator choice.
- **`STATE.md` ceiling:** 5KB hard limit per project.
- **Lifecycle authority:** Task movement, review verdicts, session state, and
  Git behavior are governed by `protocol/CONVENTIONS.md`.
- **Session startup safety:** For vague PM startup requests such as "start
  working", "continue", or "check STATE.md", use `skills/start-session.md`.
  If more than one project exists and no project is specified, ask which
  project to use before reading or mutating project lifecycle artifacts.
  When this agent is PM for the selected project, read `STATE.md`, summarize
  the current/next protocol action, and ask whether to begin.

## Formatting & Linting

- No automated linting or formatting tools. Conventions are enforced by
  protocol discipline at review time.
- Structural rules live in `protocol/CONVENTIONS.md`.

## Testing

- **Evidence gate discipline** is per-task, not per-repo. Tasks declare
  `Evidence gate: required` or `Evidence gate: n/a` in their files.
- Reviews of `required` tasks must record red-before-green evidence.

## Commit Conventions

- Git operations are optional (`git_versioning` in `cartopian.toml`).
- When enabled, commits describe the change at the unit-of-work grain.
- Auto-commit and auto-push happen at session close — invisible to operator.
- Git staging, commits, and pushes for the protocol repo are human-owned; agents
  do not run `git add`, `git commit`, or `git push` here.

## Roles & Assignment

- Default roster: PM and Operator. Common example labels operators add
  are Coder and Reviewer, but role names are operator-chosen.
- `[roles]` in `cartopian.toml` maps each role name to a one-line
  description. There is no `kind` field; whether a role dispatches
  automatically is inferred from the presence of a `[handoffs.<role>]`
  block.
- Role defaults live in `cartopian.toml`; project-level configs may
  override them.
- The same agent can be assigned to multiple roles. The human operator
  may take on multiple roles as well.
- Guided workflows live in `skills/`.
- Roles serve as a communication aid between the AI Project Manager and
  the human operator. They can be renamed, removed, or extended since
  they don't map to any actual software executables.
