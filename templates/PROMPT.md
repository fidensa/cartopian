# <title>

Work root: <name | name, name | n/a>
Branch: <branch or n/a>

## Paths

- **Project root**: <absolute path to the cartopian project directory; the assignee CLI's launch cwd>
- **Work root paths**: <comma-separated absolute paths resolved from `Work root:`, or n/a>
- **Report path**: <absolute path to the expected completion report>
- **Report template path**: <absolute path to templates/REPORT.md>

The assignee CLI is launched with cwd set to **Project root** (the registered absolute path from the cartopian project registry). Any locations this work touches outside the cartopian project root are declared as work-root names in `Work root:` above; the launcher resolves each name to an absolute path via `cartopian resolve-config` (which merges `<project-root>/cartopian.toml` and the per-machine `<project-root>/cartopian.local.toml`) and grants the agent read/write access to the union of the project root and the resolved work-root paths. The launcher fails closed if any name is unmapped.

## Pull request

- **PR URL**: <URL or n/a>
- **Preview URL**: <URL or n/a>

For review prompts in projects using PM-owned product-repo git, the PM populates `Branch`, `PR URL`, and `Preview URL` when available. If no preview URL exists, write `n/a`. Coder prompts may leave `PR URL` and `Preview URL` as `n/a` or omit them entirely.

## Your task

<Imperative, directed at the assignee.>

## Context

<Self-contained. No "go read the PM system." All referenced file paths must be absolute.>

## Specification

<When the work has a spec, paste the **deidentified** spec body here — the `deidentified_spec` field from `cartopian render-spec <spec-path>`. Do not link or hand over the raw spec file; it carries PM identifiers the assignee must not copy into product code. Omit this section when the task has no spec.>

## What to produce

<File paths, interfaces, test targets.>

## Evidence gate

<Red before code, or n/a with reason.>

## What not to do

<Scope boundaries. Non-goals.>

- Do not modify spec, task, phase, or prompt files. Only the PM edits Cartopian protocol files. If the spec, task, or this prompt is wrong, ambiguous, or insufficient, stop and report it as a blocker in the completion report rather than rewriting the protocol file to match what you built.
- Do not move Cartopian task files between status directories.
- Do not delete prompt files.
- Do not rewrite `STATE.md` or perform PM lifecycle cleanup.
- When the project uses PM-owned product-repo git, do not stage, commit, push, create branches, open PRs, merge, or otherwise perform product-repo git plumbing.

## Done criteria

<Checkable. Boolean-verifiable.>

## Completion report

When you are done, write a completion report to the report path listed above. Use the report template at the report template path listed above.

Use the task handoff, review handoff, or planning-review handoff variant from `templates/REPORT.md`, matching the type of work this prompt assigns.

**Redact secrets.** Do not include API keys, credentials, tokens, private connection strings, or comparable sensitive values in the report.
