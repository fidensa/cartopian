# Capabilities

Capability-based access grants for Cartopian-governed projects. Roles are user-named bundles of grants declared in `[roles.<name>]` tables in `cartopian.toml` (`grants = [...]`); enforcement keys on grants only — never on role names or prose role descriptions. **The vocabulary is closed and append-only**: names may be added in later protocol versions but are never renamed or removed, and unknown names are never silently accepted.

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

Preset names are valid anywhere a capability name is and expand to their grants at resolution time; the operator composes them per role (e.g. `grants = ["reviewer-like", "read:reports"]`).

| Preset | Grants |
| --- | --- |
| `coder-like` | `read:prompts`, `read:work-roots`, `write:worktree` |
| `reviewer-like` | `read:prompts`, `read:work-roots`, `write:reports` |
| `planner-like` | `read:governance`, `read:reports`, `read:prompts`, `write:plan` |
| `pm-with-planner` | `read:governance`, `read:reports`, `read:prompts`, `write:lifecycle`, `dispatch` |
| `pm-solo` | `read:governance`, `read:reports`, `read:prompts`, `write:plan`, `write:lifecycle`, `dispatch` |

Deliberate exclusions: `coder-like` and `reviewer-like` carry neither `read:governance` nor `read:reports` — the PM curates spec and feedback into the prompt (an operator may add `read:reports` to a reviewer). The PM presets stay out of `read:work-roots` and `write:worktree`.
