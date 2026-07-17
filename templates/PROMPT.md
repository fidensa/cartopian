# <title>

Work root: <name | name, name | n/a>
Branch: <branch or n/a; include only for a git workflow>

## Paths

- **Project root**: <absolute path to the governing cartopian project directory; NOT in the assignee's scope except the report target below>
- **Work root paths**: <comma-separated absolute paths resolved from `Work root:`, or n/a>
- **Deliverable path**: <absolute path where the durable work product is written, or n/a; when the deliverable must land inside the governing project, this is n/a and the work product is returned inline in the report instead — see the Deliverable section below>
- **Report path**: <absolute path to the expected completion report>
- **Report template path**: <absolute path to templates/REPORT.md>

The assignee CLI is launched with cwd set to the **primary work root** (the first name in `Work root:`), not the governing project. Its filesystem scope is the union of the resolved work-root paths plus only the directory of the **Report path** above — so the governing project's PM artifacts (requirements, decisions, tasks, backlog, STATE, sibling prompts/reports) are out of scope. The launcher (`cartopian dispatch`) resolves each work-root name to an absolute path via `cartopian resolve-config` (merging `<project-root>/cartopian.toml` and the per-machine `<project-root>/cartopian.local.toml`) and passes the scope set to the wrapper; it fails closed if any name is unmapped. The prompt is self-contained — paste the deidentified spec and the relevant report-template variant inline rather than pointing the assignee at protocol files; the assignee's only interaction with the governing project is writing its completion report to the report target.

## Pull request

- **PR URL**: <URL or n/a>
- **Preview URL**: <URL or n/a>

Omit this section when the work has no pull-request workflow.

For review prompts in projects using PM-owned product-repo git, the PM populates `Branch`, `PR URL`, and `Preview URL` when available. If no preview URL exists, write `n/a`. Coder prompts may leave `PR URL` and `Preview URL` as `n/a` or omit them entirely.

## Your task

<Imperative, directed at the assignee.>

## Context

<Self-contained. No "go read the PM system." All referenced file paths must be absolute.>

## Specification

<When the work has a spec, paste the **deidentified** spec body here — the `deidentified_spec` field from `cartopian render-spec <spec-path>`. Do not link or hand over the raw spec file; it carries PM identifiers the assignee must not copy into product code. Omit this section when the task has no spec.>

## Deliverable

<Include this section only when the task produces a durable document (research findings, a design, an evaluation, an analysis) rather than code.

- When a **Deliverable path** is given above, write the complete work product to that file. Treat it like code: it is the artifact the reviewer reviews, not the report. Your completion report then only summarizes what you did and points to the deliverable — do not paste the full work product into the report.
- When the **Deliverable path** is n/a because the durable copy must live inside the governing project (outside your write scope), put the complete work product in the report's `## Deliverable content` section instead. The PM persists it to its durable location and the reviewer reviews that copy.

Omit this section for code-only tasks.>

## What to produce

<File paths, interfaces, confirmation sources, checklists, validation targets, or other concrete evidence locations.>

## Evidence gate

<The before-and-after evidence required: test target, fixture run, validation script, fact-check pass, approval checklist, inspection, rehearsal, or n/a with reason.>

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
