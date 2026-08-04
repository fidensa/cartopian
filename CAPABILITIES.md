# Capabilities

Capability-based access grants for Cartopian-governed projects. Roles are user-named bundles of grants declared in `[roles.<name>]` tables in `cartopian.toml` (`grants = [...]`); enforcement keys on grants only — never on role names or prose role descriptions. **The vocabulary is closed and append-only**: names may be added in later protocol versions but are never renamed or removed, and unknown names are never silently accepted.

Review assignment is a separate concern. `[reviews].planning_role` and `[reviews].task_role` may point to any defined role name. The conventional `reviewer` label, a review-oriented description, and the `reviewer-like` preset do not by themselves assign that role to a review checkpoint. Conversely, assigning a role in `[reviews]` does not grant access. When containment is active, the operator must grant that role the capabilities its handoff needs.

## Vocabulary

Read grants (deliberately coarse; may be split finer later, append-only):

- `read:governance` — read management/strategy artifacts plus specs.
- `read:reports` — read reports and reviews.
- `read:prompts` — read the `prompts/` directory (the assignee's handoff).
- `read:work-roots` — read the product tree.

Write/act grants:

- `write:plan` — author plan artifacts.
- `write:lifecycle` — perform lifecycle mutations (task status, state, protocol files).
- `write:decisions` — record decisions.
- `write:reports` — write reports and reviews.
- `write:worktree` — mutate the product tree.
- `dispatch` — dispatch handoffs.

## Activation

- **Activation rule:** the first role in the resolved config that declares a `grants` key activates containment project-wide, all-or-nothing — there is no per-role mix of gated and ungated.
- **Ungated mode:** no role in the resolved config declares grants — gating is inactive and every session behaves as if all read and write grants were held (configs that predate the vocabulary work unchanged).
- **Activated mode:** resolution fails closed — a role with an unknown capability name, an explicitly empty grant list, or no declared grant set holds no grants (a typo never widens access); a session holding several roles gets the union of their grants.

## Presets

Preset names are valid anywhere a capability name is and expand to their grants at resolution time; the operator composes them per role (e.g. `grants = ["reviewer-like", "write:plan"]`). Preset names describe access shapes only; they do not select lifecycle policy or review assignment.

| Preset | Grants |
| --- | --- |
| `coder-like` | `read:prompts`, `read:work-roots`, `write:worktree`, `write:reports` |
| `reviewer-like` | `read:governance`, `read:reports`, `read:prompts`, `read:work-roots`, `write:reports` |
| `planner-like` | `read:governance`, `read:reports`, `read:prompts`, `write:plan` |
| `pm-with-planner` | `read:governance`, `read:reports`, `read:prompts`, `write:lifecycle`, `dispatch` |
| `pm-solo` | `read:governance`, `read:reports`, `read:prompts`, `write:plan`, `write:lifecycle`, `dispatch` |

`reviewer-like` includes the two direct-evidence reads used by both review workflows: `read:governance` covers governed task evidence and planning artifacts, while `read:reports` covers the preserved task-completion report and prior review output. It still grants no plan, lifecycle, decision, configuration, prompt, or worktree mutation authority. `coder-like` deliberately carries neither evidence-read grant because the PM curates an implementation assignment into its prompt. The PM presets stay out of `read:work-roots` and `write:worktree`.

## Enforcement

Both boundaries are enforced at the harness's native interception point — the Claude Code PreToolUse refusal adapter (`cli/claude_hook.py`) — keyed on the session's resolved grants only, never on role names or descriptions. For a mediated Claude handoff, `cartopian dispatch` exports the session role and current Python interpreter; the Claude wrapper resolves the project's activation state and adds the refusal adapter through process-scoped `--settings` only when grants are active. No project registration is required. `Bash`/shell tool calls are deliberately never gated.

**Write boundary** (the mutation tools `Write`/`Edit`/`MultiEdit`/`NotebookEdit`): governed path-classes require the matching `write:*` grant; a declared work root requires `write:worktree`.

**Read boundary** (the read tools `Read`/`NotebookRead` and the search tools `Glob`/`Grep`; a pathless search gates on the session cwd it would sweep):

| Path-class | Paths | Required grant |
| --- | --- | --- |
| governance | `specs/`, `phases/`, `tasks/`, `decisions/`, `STATE.md`, `BACKLOG.md`, `IMPLEMENTATION_PLAN.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STANDARDS.md`, `cartopian.toml`, `cartopian.local.toml`, and any unclassified project file | `read:governance` |
| reports | `reports/`, `reviews/` | `read:reports` |
| prompts | `prompts/` | `read:prompts` |
| work root | each declared work root | `read:work-roots` |

With the default assignee grants (`coder-like`), a dispatched session can read its own handoff prompt and the product work tree, and is refused reads of governance artifacts, specs, reports, and reviews.

With `reviewer-like`, a dispatched review session can read its handoff, the product tree, governed review evidence, and reports, and can write reports. The resolved bundle is independent of the role's operator-chosen name and of whether the handoff is manual or automatically launched; point-of-use enforcement still depends on the host boundary described above.

**Honest tiering.** `cartopian containment-matrix <project-path>` verifies the installed refusal hook, settings helper, and platform wrapper chain, then exercises the helper's process-scoped settings construction. It does not require `.claude/settings.json`. A missing or invalid link downgrades the result. Healthy Claude evidence is `contained-partial` on both axes because the hook intercepts the structured read and mutation tools but not `Bash`. Governed writes that bypass point-of-use refusal can be detected after the fact by `plan-audit` provenance. There is no equivalent reliable record of unauthorized shell reads, so the matrix never presents read provenance detection as a fallback.

Older projects may contain a project-level Cartopian `PreToolUse` entry. If it exactly targets the current launch interpreter and installed hook, the wrapper reuses the entry in its per-launch layer so Claude de-duplicates it and executes it once. A stale or incompatible entry refuses launch rather than executing twice or trusting an old interpreter. `scripts/install.py --claude-hook <project-dir>` is retained as an explicit cleanup operation: it removes old Cartopian `PreToolUse` and `Stop` handlers while preserving unrelated Claude settings and hooks. It does not create a registration. Install, update, reconciliation, and dispatch never mutate project Claude settings.

**Not a capability boundary.** The same settings helper independently adds `cli/claude_stop_hook.py` only when dispatch exports `CARTOPIAN_EXPECTED_REPORT_PATH`. This **Stop** hook refuses a report-less end-of-turn (see `protocol/CONVENTIONS.md` § Foreground Completion), grants and denies nothing, and contributes no containment-matrix evidence. `exited-without-report` is the after-exit completion classification when a cleanly exited handoff produced no report; it is not capability enforcement or a capability fallback.
