# TASK-NN-NNN: <short imperative title>

Phase: PHASE-NN-slug
Plan ref: PNN-KIND-NNN
Repo subpath: <subpath | n/a>
Assignee: <free text; decided per task>
Spec: <SPEC-NN-NNN-slug.md | none>
Depends on: <TASK-NN-NNN, TASK-NN-NNN | none>
Blocked by: <TASK-NN-NNN, TASK-NN-NNN | none>
Created: YYYY-MM-DD
Evidence gate: <required | n/a>

## Goal

One or two sentences. What does done look like?

## Plan ref

One primary plan item from `IMPLEMENTATION_PLAN.md`, for example
`P01-BUILD-001`. The matching phase file must carry the same plan ref.
A task that truly advances multiple plan refs should usually be split;
use References for secondary context.

## Repo subpath

Single-valued path fragment, resolved as `<launch cwd>/<repo subpath>`.
The launch cwd is the parent of the workspace root (see
`protocol/CONVENTIONS.md` → Handoffs → Launch Directory). Typically a
bare directory name (e.g., `cartopian-web`); multi-segment is allowed
for nested layouts (e.g., `team-a/cartopian-web`). Not absolute, not a
`<owner>/<repo>` GitHub slug.

Use `n/a` if the task does not touch a product repo. A task that
genuinely spans multiple repos is a sign the task should be split.

## Dependencies

- **Depends on** names tasks whose output this task reads or builds on.
  Informational; does not block start.
- **Blocked by** names tasks that must be in `done/` before this task
  can start.

## Evidence gate

If `required`, name the concrete acceptance evidence (test targets,
fixture checks, validation runs, etc.) that must exist and fail
before implementation starts. If `n/a`, say why.

## Acceptance

- [ ] Checkable, specific, boolean-verifiable things.
- [ ] Each item should be something a reviewer can mark true or false.

## References

- `IMPLEMENTATION_PLAN.md` section(s) by heading.
- The matching `phases/PHASE-NN-slug.md` roll-up row.
- Prior specs or tasks this depends on.

## Notes

Anything a future reader or reviewer would thank you for.
