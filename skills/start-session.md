# Skill: Start Session

Open or resume a Cartopian PM session by selecting the project, reading `STATE.md`, and asking whether to begin the current or next protocol action.

Use this skill when the operator gives a project-agnostic startup direction such as "start working", "continue", "check `STATE.md`", "what's next", "pick up where we left off", or "resume" without naming another lifecycle skill.

**Output:** The selected project is named to the operator, `STATE.md` is summarized, and the PM asks whether to begin or continue the current or next task. No handoff is launched and no task is moved before operator confirmation.

---

## Orientation — PM scope

- **PM scope** — Cartopian assigns PMs per project, plus there may be a protocol-level PM named in the root cartopian.toml. If a protocol-level PM is named, they have authority over all projects in the protocol. If no protocol-level PM is named, the project-level PM is the default and you should assume you will act as the PM for at least one project during this session.

- **Protocol reference** — Before proceeding, read the startup slice `cartopian://protocol/CONVENTIONS/startup` if not already loaded. The full `protocol/CONVENTIONS.md` (`cartopian://protocol/CONVENTIONS`) remains the authoritative contract for all lifecycle actions; read its broader sections when a later lifecycle action needs them.

## Stage 0 - Select Project

Project selection is registry-only. The registry is authoritative — do not consult cwd or local config files (`cartopian.toml`, `AGENTS.md`, `CLAUDE.md`, `README.md`) to confirm, override, or filter the registry result; a path/cwd mismatch is not a reason to skip a registered project or offer alternatives.

Use the Core CLI to enumerate and resolve the target project:

1. Enumerate registered projects via `cartopian discover-projects`. This emits NDJSON records with `id`, `path`, and `label`.
2. If the operator named a registered `id` or absolute `path`, select that project.
3. If exactly one project is registered, name it to the operator and ask whether to open it or start a new project (`init project`) instead; pause until they choose, and select it only on explicit confirmation — do not auto-enter it. (A path/cwd mismatch is still not a reason to skip or filter it: the registry is authoritative; you are confirming the operator's choice, not second-guessing the registry.)
4. If more than one project is registered and none was selected, list the registered IDs and ask the operator to choose one (or to start a new project with `init project`); pause until a choice is made.
5. If no projects are registered, stop and run `init project` to scaffold, generate config, and register a project. Only in this case may cwd be proposed as a candidate scaffold location.

Do not read or mutate project-specific lifecycle artifacts, and do not call `next-action` or any other lifecycle command, until a registered project is selected.

---

## Stage 1 - Resolve PM Role

PM role and dispatch path are read from the `pm_role` and `pm_dispatch_kind` fields of the `cartopian next-action` record gathered in Stage 2 (the aggregator runs `cartopian resolve-config` internally on your behalf, so a standalone resolve-config call is not part of this flow). Apply these rules to the values returned:

- If `pm_role` is the default placeholder (`Manages the project lifecycle and orchestrates handoffs.`) because no `pm` entry is declared in the resolved `[roles]` table, surface a blocker: the project does not declare a PM role. Ask the operator how to proceed (declare `pm` in `[roles]`, name a different role to act as PM for this session, or stop) before taking any PM lifecycle action.
- If `pm_dispatch_kind` is `automated`, a `[handoffs.pm]` block is configured and PM dispatch is automated via that block's wrapper.
- If `pm_dispatch_kind` is `manual`, no `[handoffs.pm]` block is configured: this agent may summarize state and propose the next action, but lifecycle execution requires explicit operator confirmation per stage.

---

## Stage 2 - Read Session State

Run the orientation aggregator using the Core CLI for the selected project path:

```
cartopian next-action <project-path>
```

This emits a single NDJSON record carrying every field needed to orient the session: `project_id`, `project_path`, `phase_id`, `active_task`, `next_open_task`, `next_unstarted_phase`, `plan_complete`, `pm_role`, `pm_dispatch_kind`, `blockers`, and `state_filesystem_disagreement`. It internally resolves config (the same data `cartopian resolve-config` would emit), so `resolve-config` does not need to be invoked separately. Its `blockers` field covers phase and `STATE.md` open-question checks only — it does not perform the artifact-chain audit, so also run `cartopian plan-audit <project-path>` at session startup per `protocol/CONVENTIONS.md` and treat a non-zero exit as a blocker.

Present a short summary to the operator from the returned record:

- Selected project — `project_id` at `project_path`.
- Current phase — `phase_id`.
- Active work — `active_task` (id, title, status).
- Open or queued work — `next_open_task` (id, title).

Then check the disagreement and blocker fields before proposing any action:

- **`state_filesystem_disagreement`**: if non-null, the value describes a mismatch between a task status claimed in `STATE.md` and the directory the task file actually lives in. The filesystem is authoritative. Surface the mismatch to the operator and offer to refresh `STATE.md` before starting work if the correction is mechanical; otherwise ask the operator how to resolve the inconsistency. The refresh is **PM-performed** — write the corrected body through the mediated writer (`cartopian write-state <project-root> --content-file <body-path>`), never a raw `Edit`.
- **`blockers`**: any non-empty `blockers` array is a PM-level blocker (e.g. `no active phase detected but tasks are present`, `unresolved open question in STATE.md: …`). Surface each entry to the operator and stop. Do not proceed to Stage 3 while blockers exist.

Resolve blockers with the operator before taking any lifecycle action.

---

## Stage 3 - Propose The Next Action

Convert the `next-action` record's `active_task` and `next_open_task` fields into one proposed PM action:

- If `active_task` is non-null and its status is `in-progress`, ask whether to continue it with `run task`.
- If `active_task` is non-null and its status is `in-review`, ask whether to process the review path with `run task`.
- If `phase_id` is null and no plan exists for the project, ask whether to begin planning with `plan project`.
- If `next_unstarted_phase` is non-null — the open queue is empty but a later phase exists whose tasks have **not** been generated yet — the plan is **not** complete. Name that phase to the operator and ask whether to generate its tasks now (the planning skill's task-generation stage). Do **not** offer to close the plan in this case; an empty open queue with a later un-generated phase means "generate the next phase," not "plan done."
- If `plan_complete` is true (no `active_task`, no `next_open_task`, no `next_unstarted_phase`, and the plan actually had tasks), the plan is genuinely finished — ask whether to close it with `close plan`.
- If `active_task` is null and `next_open_task` is non-null, ask whether to start that task with `run task`.
- If `STATE.md` says the PM should author or revise the next task, spec, decision, or plan artifact, ask whether to perform that PM-owned authoring action now. Any such PM authoring routes through the mediated `cartopian write-*` commands (the contained PM has no raw `Write`/`Edit`); the owning lifecycle skill names the specific command.

Ask the operator for confirmation before launching a handoff, moving a task, creating an assignment prompt, or otherwise advancing lifecycle state.

If the operator confirms, continue with the relevant skill or PM-owned authoring procedure. If the operator declines, stop after the state summary.
