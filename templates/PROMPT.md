# TASK-NN-NNN: <title>

Plan ref: PNN-KIND-NNN Work root: <name | name, name | n/a> Branch: <branch or n/a> Spec: <SPEC-NN-NNN-slug.md or none>

## Paths

- **Prompt path**: <absolute path to this prompt file>
- **Project root**: <absolute path to the cartopian project directory; the assignee CLI's launch cwd>
- **Work root paths**: <comma-separated absolute paths resolved from `Work root:`, or n/a>
- **Task path**: <absolute path to the task file>
- **Spec path**: <absolute path to the spec file, or n/a>
- **Report path**: <absolute path to the expected completion report>
- **Review path**: <absolute path to the expected review file, if applicable>
- **Report template path**: <absolute path to templates/REPORT.md>

The assignee CLI is launched with cwd set to **Project root** (the registered absolute path from the cartopian project registry). Any locations the task touches outside the cartopian project root are declared as work-root names in `Work root:` above; the launcher resolves each name to an absolute path via `cartopian resolve-config` (which merges `<project-root>/cartopian.toml` and the per-machine `<project-root>/cartopian.local.toml`) and grants the agent read/write access to the union of the project root and the resolved work-root paths. The launcher fails closed if any name is unmapped.

## Pull request

- **PR URL**: <URL or n/a>
- **Preview URL**: <URL or n/a>

For review prompts in projects using PM-owned product-repo git, the PM populates `Branch`, `PR URL`, and `Preview URL` when available. If no preview URL exists, write `n/a`. Coder prompts may leave `PR URL` and `Preview URL` as `n/a` or omit them entirely.

## Your task

<Imperative, directed at the assignee.>

## Context

<Self-contained. No "go read the PM system." All referenced file paths must be absolute.>

## What to produce

<File paths, interfaces, test targets.>

## Evidence gate

<Red before code, or n/a with reason.>

## What not to do

<Scope boundaries. Non-goals.>

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
