# TASK-NN-NNN: <short imperative title>

Phase: PHASE-NN-slug
Plan ref: PNN-KIND-NNN
Work root: <name | name, name | n/a>
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

## Work root

Optional, multi-valued, name-only. Each value is a work-root **name** drawn from the project's `[project].work_roots` list in `cartopian.toml` (see `protocol/CONVENTIONS.md` → Work Roots). Multiple names are comma-separated:

```
Work root: product, design
```

Names only. Absolute paths, project-relative paths, and `<owner>/<repo>` slugs are rejected. Operator-machine path mapping lives in `<project-root>/cartopian.local.toml` and is resolved by `cartopian resolve-config`; the launcher consumes the resolved absolute paths and fails closed on unmapped names.

Use `n/a` (or omit the line) when the task touches nothing outside the cartopian project root. Within-root subdirectory scope belongs in the task body, not in this field.

## Dependencies

- **Depends on** names tasks whose output this task reads or builds on. Informational; does not block start.
- **Blocked by** names tasks that must be in `done/` before this task can start.

## Evidence gate

If `required`, name the concrete acceptance evidence (test targets, fixture checks, validation runs, etc.) that must exist and fail before implementation starts. If `n/a`, say why.

## Acceptance

- [ ] Checkable, specific, boolean-verifiable things.
- [ ] Each item should be something a reviewer can mark true or false.

## References

- `IMPLEMENTATION_PLAN.md` section(s) by heading.
- The matching `phases/PHASE-NN-slug.md` roll-up row.
- Prior specs or tasks this depends on.

## Notes

Anything a future reader or reviewer would thank you for.
