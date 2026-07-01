"""Capability-based access grants: vocabulary, presets, and role→grant resolution.

The capability vocabulary is closed and append-only: names may be added in
later protocol versions but never renamed or removed, and unknown names are
never silently accepted. Roles are user-named bundles of grants declared in
``[roles.<name>]`` tables in ``cartopian.toml``; enforcement keys on grants
only — never on role names or prose role descriptions.

Config shape (both forms may coexist in one ``[roles]`` table):

    [roles]
    pm = "Plans the work."                # legacy string form — no grant set

    [roles.coder]
    description = "Implements tasks per spec."
    grants = ["coder-like"]               # capability names and/or preset names

Resolution semantics (see ``resolve_grants``):

- **Ungated mode** — no role in the resolved config declares a ``grants``
  key: gating is inactive and every role behaves as if all read and write
  grants were held. Configs that predate the vocabulary work unchanged.
- **Activated mode** — at least one role declares a ``grants`` key:
  containment is active project-wide and resolution fails closed. A role
  whose grant list contains an unknown name, is explicitly empty, is
  malformed, or that declares no grant set at all resolves to no grants.
  A typo in a capability name never widens access.

Activation is all-or-nothing per project; there is no per-role mix of gated
and ungated. A malformed declaration still activates — failing validation
must never flip gating back off.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, Mapping, Tuple

# The closed capability vocabulary (append-only).
READ_CAPABILITIES: Tuple[str, ...] = (
    "read:governance",   # management/strategy artifacts plus specs
    "read:reports",      # reports and reviews
    "read:prompts",      # the prompts/ directory — the assignee's handoff
    "read:work-roots",   # the product tree
)

WRITE_CAPABILITIES: Tuple[str, ...] = (
    "write:plan",
    "write:lifecycle",
    "write:decisions",
    "write:reports",
    "write:worktree",
    "dispatch",
)

ALL_CAPABILITIES: Tuple[str, ...] = READ_CAPABILITIES + WRITE_CAPABILITIES

# Presets are sane default bundles the operator composes per role. A preset
# name is valid anywhere a capability name is, and expands to its grants at
# resolution time. coder-like and reviewer-like deliberately exclude
# read:governance and read:reports (the PM curates spec and feedback into
# the prompt); the PM presets deliberately exclude read:work-roots and
# write:worktree.
PRESETS: Dict[str, Tuple[str, ...]] = {
    "coder-like": ("read:prompts", "read:work-roots", "write:worktree"),
    "reviewer-like": ("read:prompts", "read:work-roots", "write:reports"),
    "planner-like": ("read:governance", "read:reports", "read:prompts", "write:plan"),
    "pm-with-planner": (
        "read:governance",
        "read:reports",
        "read:prompts",
        "write:lifecycle",
        "dispatch",
    ),
    "pm-solo": (
        "read:governance",
        "read:reports",
        "read:prompts",
        "write:plan",
        "write:lifecycle",
        "dispatch",
    ),
}

_FULL_SET: FrozenSet[str] = frozenset(ALL_CAPABILITIES)
_KNOWN_NAMES: FrozenSet[str] = _FULL_SET | frozenset(PRESETS)


def is_known_grant_name(name: Any) -> bool:
    """True iff `name` is a capability name or a preset name."""
    return isinstance(name, str) and name in _KNOWN_NAMES


def role_description(value: Any) -> str:
    """Extract the prose description from either role form.

    Legacy form is a bare string; table form carries an optional
    ``description`` key. Descriptions are prose only — no behavior may key
    on them.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        desc = value.get("description", "")
        return desc if isinstance(desc, str) else ""
    return ""


@dataclass(frozen=True)
class GrantResolution:
    """Resolved grant state for one project's ``[roles]`` config.

    ``activated`` is the explicit activation state. ``role_grants`` maps each
    configured role to its *effective* grant set: the full vocabulary when
    ungated, the validated expansion of its declaration when activated
    (empty on any fail-closed condition). ``invalid`` maps roles whose
    declaration failed validation to the offending entries.
    """

    activated: bool
    role_grants: Dict[str, FrozenSet[str]]
    invalid: Dict[str, Tuple[str, ...]] = field(default_factory=dict)

    def grants_for(self, role_names: Iterable[str]) -> FrozenSet[str]:
        """Effective grants for a session holding `role_names` (the union).

        Ungated: all grants behave as held. Activated: roles that fail
        closed — including role names absent from the config — contribute
        nothing.
        """
        if not self.activated:
            return _FULL_SET
        held: set = set()
        for name in role_names:
            held |= self.role_grants.get(name, frozenset())
        return frozenset(held)


def _declares_grants(value: Any) -> bool:
    return isinstance(value, dict) and "grants" in value


def _expand_declaration(raw: Any) -> Tuple[FrozenSet[str], Tuple[str, ...]]:
    """Validate and expand one role's ``grants`` value.

    Returns ``(effective_grants, invalid_entries)``. Any invalid entry —
    unknown name, non-string entry, or a non-list ``grants`` value — fails
    the whole role closed: effective grants are empty and the offenders are
    reported. An explicitly empty list is a valid declaration that grants
    nothing.
    """
    if not isinstance(raw, list):
        return frozenset(), (repr(raw),)
    expanded: set = set()
    invalid: list = []
    for entry in raw:
        if not is_known_grant_name(entry):
            invalid.append(entry if isinstance(entry, str) else repr(entry))
        elif entry in PRESETS:
            expanded.update(PRESETS[entry])
        else:
            expanded.add(entry)
    if invalid:
        return frozenset(), tuple(invalid)
    return frozenset(expanded), ()


def resolve_grants(roles_cfg: Mapping[str, Any]) -> GrantResolution:
    """Resolve the merged ``[roles]`` config to per-role grant sets.

    `roles_cfg` maps role names to either a legacy description string or a
    ``[roles.<name>]`` table. See the module docstring for the ungated /
    activated semantics.
    """
    activated = any(_declares_grants(v) for v in roles_cfg.values())
    if not activated:
        return GrantResolution(
            activated=False,
            role_grants={name: _FULL_SET for name in roles_cfg},
        )
    role_grants: Dict[str, FrozenSet[str]] = {}
    invalid: Dict[str, Tuple[str, ...]] = {}
    for name, value in roles_cfg.items():
        if not _declares_grants(value):
            role_grants[name] = frozenset()
            continue
        grants, bad = _expand_declaration(value["grants"])
        role_grants[name] = grants
        if bad:
            invalid[name] = bad
    return GrantResolution(
        activated=True, role_grants=role_grants, invalid=invalid
    )
