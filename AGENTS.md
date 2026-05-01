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
- **No runtime dependencies.** This is a specification repo, not a software project.

## Project Structure

> **Note to AI agents:** List the project root to discover current layout.

- **protocol/** — Baseline protocol specification (`CONVENTIONS.md`)
- **templates/** — Default file templates (TASK, SPEC, PROMPT, REVIEW, DECISION)
- **skills/** — Agent-executable guided workflows (init-workspace, init-project,
  plan-project). Read the skill file and follow its steps.
- **projects/** — Gitignored; its own git repo. Each child directory is an
  independent project with its own config, state, phases, tasks, and decisions.
  Only `projects/sample-project/` ships with the protocol repo.

## Code Conventions

- **Naming is load-bearing.** Every artifact follows strict naming patterns
  defined in `protocol/CONVENTIONS.md`. Phase-scoped prefixes (`NN-NNN`)
  create a trace chain from `IMPLEMENTATION_PLAN.md` → Phase → Task → Spec/Prompt/Review.
- **Status is a directory.** Task status is `open/`, `in-progress/`,
  `in-review/`, or `done/`. Moving the file is the status update. Never add a
  `status:` field to a task file.
- **Specs are mutable.** Update in place. No version suffixes, no supersedes chains.
- **Decisions are immutable.** A new decision supersedes the old one; old files
  are never edited.
- **`STATE.md` ceiling:** 5KB hard limit per project.

## Formatting & Linting

- No automated linting or formatting tools. Conventions are enforced by
  protocol discipline at review time.
- Refer to `protocol/CONVENTIONS.md` for all structural rules.

## Testing

- **Test gate discipline** is per-task, not per-repo. Tasks declare
  `Test gate: required` or `Test gate: n/a` in their files.
- Reviews of `required` tasks must record red-before-green test evidence.

## Commit Conventions

- Git operations are optional (`git_versioning` in `cartopian.toml`).
- When enabled, commits describe the change at the unit-of-work grain.
- Auto-commit and auto-push happen at session close — invisible to operator.
- **Never run `git add`, `git commit`, or `git push` in this repo.** The
  human developer handles all git operations for the protocol repo.

## PM Workflow

- The PM produces assignee-directed prompts (`PROMPT-NN-NNN.md`).
- Operator must explicitly confirm assignment before a task moves to
  `in-progress/`.
- Session open: read `STATE.md` → current phase → active tasks → go.
- Session close: move changed tasks, record decisions, refresh state, name
  the next action.

## Roles

Four basic roles: PM, Operator, Coder, Reviewer. Configured in
`cartopian.toml` at workspace and project levels. Same agent can fill
multiple roles. Operator is currently expected to be human. See
`skills/README.md` for how roles interact with the guided workflows.
