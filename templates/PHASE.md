# PHASE-NN-slug: <short noun-phrase title>

Plan ref section: `## Phase NN: <name>` in `IMPLEMENTATION_PLAN.md`
Source: <BL-NNN | n/a>
Created: YYYY-MM-DD

## Goal

One or two sentences. What does this phase exist to accomplish?

## Plan refs covered

| Plan ref         | Kind     | Description |
| ---------------- | -------- | ----------- |
| PNN-BUILD-001    | build    | …           |
| PNN-RESEARCH-002 | research | …           |

Copied from the matching phase row in `IMPLEMENTATION_PLAN.md`. The two-digit phase number (`NN`) must match the plan section number. Every plan ref listed here must resolve to either a build or research item below. Plan-ref suffixes are drawn from one phase-wide sequence across all work kinds, and each ref's `TASK-NN-NNN` file shares its final suffix.

## Build items

Delivery/execution tasks that produce an outcome or artifact. `BUILD` is a compatibility identifier, not a claim that the project is software. Other supported kinds (`DESIGN`, `TEST`, `RELEASE`, `VERIFY`, `CORRECTIVE`) are listed under whichever section fits their outcome; kind is classification only. List by plan ref; the corresponding `TASK-NN-NNN-slug.md` files are generated when this phase becomes active.

- `PNN-BUILD-001` — …
- `PNN-BUILD-003` — …

## Research items

Tasks that produce knowledge, decisions, investigations, or designs. Other supported kinds belong here when their outcome is knowledge or a decision; kind is classification only. List by plan ref.

- `PNN-RESEARCH-002` — …

## Dependencies on prior phases

What must be complete before this phase can start. Cite by phase identifier and the exit-criterion or artifact being relied on.

- `PHASE-NN-slug`: …

Use `none` for the first phase or a bootstrap phase.

## Exit criteria

Copied from the plan's per-phase exit criteria. The phase is complete when all of these are satisfied by completed tasks, decisions, specs, or documented operator acceptance.

- …
- …

## References

- `IMPLEMENTATION_PLAN.md` → `## Phase NN: <name>`
- Prior phase files this phase depends on.
- Locked specs or decisions this phase consumes as inputs.

## Notes

Anything a future reader or reviewer would thank you for. Optional.
