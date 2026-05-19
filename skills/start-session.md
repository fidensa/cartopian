# Skill: Start Session

Open or resume a Cartopian PM session by selecting the project, reading `STATE.md`, and asking whether to begin the current or next protocol action.

Use this skill when the operator gives a project-agnostic startup direction such as "start working", "continue", "check `STATE.md`", "what's next", "pick up where we left off", or "resume" without naming another lifecycle skill.

**Output:** The selected project is named to the operator, `STATE.md` is summarized, and the PM asks whether to begin or continue the current or next task. No handoff is launched and no task is moved before operator confirmation.

---

## Orientation — PM scope

- **PM scope** — Cartopian assigns PMs per project, plus there may be a protocol-level PM named in the root cartopian.toml. If a protocol-level PM is named, they have authority over all projects in the protocol. If no protocol-level PM is named, the project-level PM is the default and you should assume you will act as the PM for at least one project during this session.

- **Protocol reference** — Before proceeding, read `protocol/CONVENTIONS.md` and follow it for all lifecycle actions.

## Stage 0 - Select Project

Project selection is registry-only. The registry is authoritative — do not consult cwd or local config files (`cartopian.toml`, `AGENTS.md`, `CLAUDE.md`, `README.md`) to confirm, override, or filter the registry result. A registered project whose `path` differs from cwd is still the correct selection; cwd mismatch is not a reason to skip it or offer alternatives.

Use the Core CLI to enumerate and resolve the target project:

1. Enumerate registered projects via `cartopian discover-projects`. This emits NDJSON records with `id`, `path`, and `label`.
2. If the operator named a registered `id` or absolute `path`, select that project.
3. If exactly one project is registered, select it and name it to the operator.
4. If more than one project is registered and none was selected, list the registered IDs and ask the operator to choose one; pause until a choice is made.
5. If no projects are registered, stop and run `init project` to scaffold, generate config, and register a project. Only in this case may cwd be proposed as a candidate scaffold location.

Do not read or mutate project-specific lifecycle artifacts, and do not call `resolve-config` or `plan-audit`, until a registered project is selected.

---

## Stage 1 - Resolve PM Role

Resolve effective roles, handoff targets, automation policy, and relevant `[git]` keys via the Core CLI for the selected project id or path:

```
cartopian resolve-config <project>
```

Determine PM availability and dispatch path from the resolved config:

- If `pm` is not declared in the resolved `[roles]` table, surface a blocker: the project does not declare a PM role. Ask the operator how to proceed (declare `pm` in `[roles]`, name a different role to act as PM for this session, or stop) before taking any PM lifecycle action.
- If `pm` is declared in `[roles]` and a `[handoffs.pm]` block is configured, PM dispatch is automated via that block's wrapper.
- If `pm` is declared in `[roles]` and no `[handoffs.pm]` block is configured, PM dispatch is manual: this agent may summarize state and propose the next action, but lifecycle execution requires explicit operator confirmation per stage.

---

## Stage 2 - Read Session State

Read `STATE.md` and keep the summary short:

- Selected project.
- Current phase.
- Active work.
- Open or queued work.
- The "What to do next" instruction.

Check task directories when `STATE.md` names a task state. The filesystem is authoritative if `STATE.md` disagrees with task directory placement. Surface the mismatch and refresh `STATE.md` before starting work if the correction is mechanical; otherwise ask the operator how to resolve the inconsistency.

Run a lifecycle and provenance audit using the Core CLI:

```
cartopian plan-audit <project-path>
```

If the audit exits non-zero, surface each blocker to the operator and stop. Do not proceed to Stage 3 while blockers exist.

- **Missing artifact chain**: a task in `tasks/in-progress/` has no matching `prompts/PROMPT-NN-NNN.md`, or a task in `tasks/in-review/` has no matching `reviews/REVIEW-NN-NNN.md`. This indicates the task was moved without following the proper workflow.

Surface any audit warnings to the operator before proceeding. In particular, `unattributed-work-root-changes` means Cartopian cannot link the current dirty work-root state to an active prompt chain; it is informational and does not by itself block lifecycle action.

Resolve blockers with the operator before taking any lifecycle action.

---

## Stage 3 - Propose The Next Action

Convert `STATE.md` into one proposed PM action:

- If a task is active, ask whether to continue it with `run task`.
- If a task is in review, ask whether to process the review path with `run task`.
- If no plan exists, ask whether to begin planning with `plan project`.
- If the current plan is complete, ask whether to close it with `close plan`.
- If `STATE.md` names the next open task, ask whether to start that task with `run task`.
- If `STATE.md` says the PM should author or revise the next task, spec, decision, or plan artifact, ask whether to perform that PM-owned authoring action now.

Ask the operator for confirmation before launching a handoff, moving a task, creating an assignment prompt, or otherwise advancing lifecycle state.

If the operator confirms, continue with the relevant skill or PM-owned authoring procedure. If the operator declines, stop after the state summary.
