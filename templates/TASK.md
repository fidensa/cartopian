# TASK-NN-NNN: <short imperative title>

Phase: PHASE-NN-slug
Plan ref: PNN-KIND-NNN
Source: <BL-NNN | n/a>
Work root: <name | name, name | n/a>
Deliverable: <root:relative/path | project:resources/relative/path | n/a>
Assignee: <free text; decided per task>
Spec: <SPEC-NN-NNN-slug.md | none>
Depends on: <TASK-NN-NNN, TASK-NN-NNN | none>
Blocked by: <TASK-NN-NNN, TASK-NN-NNN | none>
Created: YYYY-MM-DD
Evidence gate: <required | n/a>

## Goal

One or two sentences. What does done look like?

## Plan ref

One primary plan item from `IMPLEMENTATION_PLAN.md`, for example `P01-BUILD-001`. The matching phase file must carry the same plan ref. A task that truly advances multiple plan refs should usually be split; use References for secondary context.

## Source

The backlog entry (`BL-NNN`) this task was promoted from, or `n/a` when the task was not born from a backlog item. Do not hand-type this line to satisfy the promotion guard — stamp it with `cartopian write-task … --source BL-NNN`, which verifies the entry is live before writing it. `delete-backlog` reads this stamp to confirm the promotion is recorded before removing the entry.

## Work root

Optional, multi-valued, name-only. Each value is a work-root **name** drawn from the project's `[project].work_roots` list in `cartopian.toml` (see `protocol/CONVENTIONS.md` → Work Roots). Multiple names are comma-separated:

```
Work root: product, design
```

Names only. Absolute paths, project-relative paths, and `<owner>/<repo>` slugs are rejected. Operator-machine path mapping lives in `<project-root>/cartopian.local.toml` and is resolved by `cartopian resolve-config`; the launcher consumes the resolved absolute paths and fails closed on unmapped names.

Use `n/a` (or omit the line) when the task touches nothing outside the cartopian project root. Within-root subdirectory scope belongs in the task body, not in this field.

## Deliverable

Set this when the task's work product is a durable document — research findings, a design or evaluation, an analysis — rather than code. It names where that document lives, so the report can stay a thin summary and the reviewer reviews the real artifact. Name-only and deidentified (no task, plan, spec, or requirement identifiers), same discipline as `Work root:`. Two forms, routed by intent:

- `root:relative/path` — the work product is intended to become part of the product. It lives in the work root named `root` (drawn from `[project].work_roots`); the assignee writes it there directly, exactly as it writes code. The path is operator-chosen — captured from the operator at authoring or assignment, never invented by the PM.
- `project:resources/relative/path` — the work product is a supporting artifact of the project itself. It lives under the project's `resources/` directory (a project-mode path outside `resources/` fails `validate-task-readiness`). The assignee returns the document inline in its completion report and the PM persists it with `cartopian write-resource`, because the assignee is not granted write access inside the project.

Use `n/a` (or omit the line) for code tasks and any task with no durable document deliverable. See `protocol/CONVENTIONS.md` → Project Resources and Document Deliverables.

## Dependencies

- **Depends on** names tasks whose output this task reads or builds on. Informational; does not block start.
- **Blocked by** names tasks that must be in `done/` before this task can start.

## Evidence gate

If `required`, name the concrete before-and-after acceptance evidence (test target, fixture check, validation run, fact-check, approval checklist, inspection, rehearsal, or similar) that demonstrates the outcome. If `n/a`, say why.

## Acceptance

- [ ] Checkable, specific, boolean-verifiable things.
- [ ] Each item should be something an independent observer can mark true or false.

## References

- `IMPLEMENTATION_PLAN.md` section(s) by heading.
- The matching `phases/PHASE-NN-slug.md` roll-up row.
- Prior specs or tasks this depends on.

## Notes

Anything a future reader or reviewer would thank you for.
