# Skill: Start Session

Open or resume a Cartopian PM session by selecting the project, reading
`STATE.md`, and asking whether to begin the current or next protocol
action.

Use this skill when the operator gives a project-agnostic startup
direction such as "start working", "continue", "check `STATE.md`",
"what's next", "pick up where we left off", or "resume" without naming
another lifecycle skill.

**Output:** The selected project is named to the operator, `STATE.md` is
summarized, and the PM asks whether to begin or continue the current or
next task. No handoff is launched and no task is moved before operator
confirmation.

---

## Stage 0 - Select Project

Resolve the Cartopian workspace root, then identify eligible project
directories under `projects/`. An eligible project contains both:

- `STATE.md`
- `cartopian.toml`

Select the project using this order:

1. If the operator named a project ID or project path, use that project.
2. If the current working directory is inside one eligible project, use
   that project.
3. If there is exactly one eligible project, use that project.
4. If there is more than one eligible project and none was selected,
   ask the operator which project to use. List the project IDs and stop
   until the operator chooses one.
5. If there are no eligible projects, stop and run `init project`.

Do not read or mutate project-specific lifecycle artifacts until the
project is selected.

---

## Stage 1 - Resolve PM Role

Read the workspace-level `cartopian.toml` and the selected project's
`cartopian.toml`. Resolve the effective `[roles]` table and the
effective `[handoffs.*]` blocks, with project config overriding
workspace config.

Determine PM availability and dispatch path from the resolved
config:

- If `pm` is not declared in the resolved `[roles]` table, surface
  a blocker: the project does not declare a PM role. Ask the
  operator how to proceed (declare `pm` in `[roles]`, name a
  different role to act as PM for this session, or stop) before
  taking any PM lifecycle action.
- If `pm` is declared in `[roles]` and a `[handoffs.pm]` block is
  configured, PM dispatch is automated via that block's wrapper.
- If `pm` is declared in `[roles]` and no `[handoffs.pm]` block is
  configured, PM dispatch is manual: this agent may summarize
  state and propose the next action, but lifecycle execution
  requires explicit operator confirmation per stage.

---

## Stage 2 - Read Session State

Read `STATE.md` and keep the summary short:

- Selected project.
- Current phase.
- Active work.
- Open or queued work.
- The "What to do next" instruction.

Check task directories when `STATE.md` names a task state. The
filesystem is authoritative if `STATE.md` disagrees with task directory
placement. Surface the mismatch and refresh `STATE.md` before starting
work if the correction is mechanical; otherwise ask the operator how to
resolve the inconsistency.

---

## Stage 3 - Propose The Next Action

Convert `STATE.md` into one proposed PM action:

- If a task is active, ask whether to continue it with `run task`.
- If a task is in review, ask whether to process the review path with
  `run task`.
- If no plan exists, ask whether to begin planning with `plan project`.
- If the current plan is complete, ask whether to close it with
  `close plan`.
- If `STATE.md` names the next open task, ask whether to start that task
  with `run task`.
- If `STATE.md` says the PM should author or revise the next task,
  spec, decision, or plan artifact, ask whether to perform that PM-owned
  authoring action now.

Ask the operator for confirmation before launching a handoff, moving a
task, creating an assignment prompt, or otherwise advancing lifecycle
state.

If the operator confirms, continue with the relevant skill or PM-owned
authoring procedure. If the operator declines, stop after the state
summary.
