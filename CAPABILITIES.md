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

Preset names are valid anywhere a capability name is and expand to their grants at resolution time; the operator composes them per role (e.g. `grants = ["reviewer-like", "read:reports"]`). Preset names describe access shapes only; they do not select lifecycle policy or review assignment.

| Preset | Grants |
| --- | --- |
| `coder-like` | `read:prompts`, `read:work-roots`, `write:worktree` |
| `reviewer-like` | `read:prompts`, `read:work-roots`, `write:reports` |
| `planner-like` | `read:governance`, `read:reports`, `read:prompts`, `write:plan` |
| `pm-with-planner` | `read:governance`, `read:reports`, `read:prompts`, `write:lifecycle`, `dispatch` |
| `pm-solo` | `read:governance`, `read:reports`, `read:prompts`, `write:plan`, `write:lifecycle`, `dispatch` |

Deliberate exclusions: `coder-like` and `reviewer-like` carry neither `read:governance` nor `read:reports` — the PM curates spec and feedback into the prompt (an operator may add `read:reports` to a reviewer). The PM presets stay out of `read:work-roots` and `write:worktree`.

## Enforcement

Both boundaries are enforced at the harness's native interception point — the Claude Code PreToolUse refusal adapter (`cli/claude_hook.py`) — keyed on the session's resolved grants only, never on role names, descriptions, or which launcher started the session. The launchers stay neutral. `Bash`/shell tool calls are deliberately never gated: the raw-edit/read detection floor owns that residual.

**Write boundary** (the mutation tools `Write`/`Edit`/`MultiEdit`/`NotebookEdit`): governed path-classes require the matching `write:*` grant; a declared work root requires `write:worktree`.

**Read boundary** (the read tools `Read`/`NotebookRead` and the search tools `Glob`/`Grep`; a pathless search gates on the session cwd it would sweep):

| Path-class | Paths | Required grant |
| --- | --- | --- |
| governance | `specs/`, `phases/`, `tasks/`, `decisions/`, `STATE.md`, `BACKLOG.md`, `IMPLEMENTATION_PLAN.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STANDARDS.md`, `cartopian.toml`, `cartopian.local.toml`, and any unclassified project file | `read:governance` |
| reports | `reports/`, `reviews/` | `read:reports` |
| prompts | `prompts/` | `read:prompts` |
| work root | each declared work root | `read:work-roots` |

With the default assignee grants (`coder-like`), a dispatched session can read its own handoff prompt and the product work tree, and is refused reads of governance artifacts, specs, reports, and reviews.

**Honest tiering.** The read boundary is *enforced* only on a host whose interception point actually intercepts the read tools — for Claude Code, only when the registered PreToolUse matcher covers them (a pre-read-boundary, write-only registration does not; re-run `scripts/install.py --claude-hook <project-dir>` to upgrade it). On any host without read interception, the read boundary is **advisory + detection**, never claimed as enforced; the detection floor remains the fail-safe. `cartopian containment-matrix <project-path>` renders the per-host tier for each boundary from real evidence and disclosures the residuals plainly.
