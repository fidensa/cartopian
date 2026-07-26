"""`cartopian generate-config <project-path>`.

Writes ``<project-path>/cartopian.toml`` from the supplied flags. Always stamps
``[project] project_schema_version`` to the current schema target (the topmost
``### vX.Y.Z`` entry under ``## Entries`` in ``protocol/CHANGELOG.md``).

Omitted optional flags MUST NOT write protocol defaults — the resolution
chain applies defaults at consumption time via ``resolve-config``.
"""
import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cli._vendor import tomli_w
from cli.capabilities import PRESETS, is_known_grant_name
from cli.commands._registry import is_kebab_case
from cli.commands.resolve_config import _load_toml
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from cli.config_schema import (
    AUTO_LAUNCH_ACTIVITIES,
    CONFIG_SCHEMA,
    ConfigDiagnostic,
    resolve_configuration,
    validate_authored_config,
)
from cli.protocol_gate import read_shipped_project_schema_version

_ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_WORK_ROOT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _closed_values(field: str) -> Tuple[str, ...]:
    """Read one accepted-value domain from the authoritative contract."""
    return tuple(CONFIG_SCHEMA["fields"][field]["values"])


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


class _Usage(Exception):
    pass


class _SingleValuedAction(argparse.Action):
    """Store action that rejects a repeated occurrence with `[usage]` exit 2.

    The flags `--name`, `--id`, `--automation-initiation`,
    `--automation-confirmation`, `--automation-max-handoffs`, and
    `--git-versioning` are single-valued.
    Choices validation runs before ``__call__`` so enum flags still reject
    bad values first; a second occurrence of any single-valued flag fails
    here regardless of value.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string}: repeated; single-valued only")
        setattr(namespace, self.dest, values)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("project_path", help="Absolute path to the project root")
    subparser.add_argument("--name", required=True, action=_SingleValuedAction,
                           help="[project] name (non-empty)")
    subparser.add_argument("--id", required=True, dest="proj_id",
                           action=_SingleValuedAction,
                           help="[project] id (kebab-case)")
    subparser.add_argument("--role", action="append", default=[], metavar='NAME="DESC"',
                           help="Repeatable role definition")
    subparser.add_argument("--role-grants", action="append", default=[],
                           metavar="ROLE=NAME[,NAME...]",
                           help="Repeatable capability grants for a declared role "
                                "(capability names and/or preset names; empty value "
                                "declares an explicitly empty grant list)")
    subparser.add_argument("--role-launch-target", action="append", default=[],
                           metavar="ROLE=TARGET",
                           help="Repeatable role launch target")
    subparser.add_argument("--role-launch-model", action="append", default=[],
                           metavar="ROLE=MODEL", help="Repeatable role launch model")
    subparser.add_argument("--role-launch-effort", action="append", default=[],
                           metavar="ROLE=EFFORT",
                           help="Repeatable role launch effort/thinking level")
    subparser.add_argument("--role-auto-launch", action="append", default=[],
                           metavar="ROLE=ACTIVITY[,ACTIVITY...]",
                           help="Repeatable closed automatic-launch permission list")
    subparser.add_argument("--role-launch-timeout", action="append", default=[],
                           metavar="ROLE=DURATION", help="Repeatable role launch timeout")
    subparser.add_argument("--automation-initiation", default=None,
                           action=_SingleValuedAction,
                           choices=_closed_values("automation.initiation"),
                           help="[automation] initiation")
    subparser.add_argument("--automation-confirmation", default=None,
                           action=_SingleValuedAction,
                           choices=_closed_values("automation.confirmation"),
                           help="[automation] confirmation")
    subparser.add_argument("--automation-max-handoffs", default=None,
                           action=_SingleValuedAction,
                           metavar="N", help="[automation] max_handoffs_per_run (positive int)")
    subparser.add_argument("--work-root", action="append", default=[], metavar="NAME",
                           help="Repeatable work-root name")
    subparser.add_argument("--review-planning", default=None,
                           action=_SingleValuedAction,
                           choices=_closed_values("reviews.planning"),
                           help="[reviews] planning policy")
    subparser.add_argument("--review-planning-role", default=None,
                           action=_SingleValuedAction, metavar="ROLE",
                           help="Role assigned to required planning reviews")
    subparser.add_argument("--review-task-closure", default=None,
                           action=_SingleValuedAction,
                           choices=_closed_values("reviews.task_closure"),
                           help="[reviews] task_closure policy")
    subparser.add_argument("--review-task-role", default=None,
                           action=_SingleValuedAction, metavar="ROLE",
                           help="Role assigned to required task-closure reviews")
    subparser.add_argument("--git-versioning", default=None,
                           action=_SingleValuedAction,
                           choices=["true", "false"], help="[defaults] git_versioning")
    subparser.add_argument("--git-key", action="append", default=[], metavar="KEY=VALUE",
                           help="Repeatable [git] entry (primitive type preserved)")


def _read_project_schema_version() -> str:
    # Shared with the protocol-version migration gate so the stamped version
    # and the gate's shipped version can never diverge.
    return read_shipped_project_schema_version()


def _parse_kv(raw: str, flag: str) -> Tuple[str, str]:
    if "=" not in raw:
        raise _Usage(f"{flag} expects <key>=<value>; got: {raw}")
    key, _, value = raw.partition("=")
    if not key:
        raise _Usage(f"{flag} <key> is empty: {raw}")
    return key, value


def _parse_bool(value: str, ctx: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise _Usage(f"{ctx} expects true|false; got: {value!r}")


def _parse_git_key_value(raw: str) -> Any:
    """Parse a --git-key value as a TOML primitive.

    Rule (locked in source):
      - ``true`` / ``false`` → bool
      - integer literal (optionally signed) → int
      - otherwise → string, stripping optional surrounding double quotes
    """
    if raw == "true":
        return True
    if raw == "false":
        return False
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    return raw


def _collect_roles(role_args: List[str]) -> Dict[str, str]:
    roles: Dict[str, str] = {}
    for raw in role_args:
        name, value = _parse_kv(raw, "--role")
        if not _ROLE_NAME_RE.match(name):
            raise _Usage(f"--role name must match [A-Za-z0-9_-]+; got: {name!r}")
        if value == "":
            raise _Usage(f"--role {name!r} description must not be empty")
        # Strip optional surrounding double quotes for shell convenience.
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if name in roles:
            raise _Usage(f"--role {name!r} declared more than once")
        roles[name] = value
    return roles


def _collect_role_grants(
    grant_args: List[str], declared_roles: Dict[str, str]
) -> Dict[str, List[str]]:
    """Parse --role-grants ROLE=NAME[,NAME...] entries.

    Names may be capability names or preset names; the vocabulary is closed,
    so an unknown name is a usage error — never silently accepted. An empty
    value declares an explicitly empty grant list (the role can write
    nothing once gating is active).
    """
    grants: Dict[str, List[str]] = {}
    for raw in grant_args:
        role, value = _parse_kv(raw, "--role-grants")
        if role not in declared_roles:
            raise _Usage(f"--role-grants {role!r}: declare with --role first")
        if role in grants:
            raise _Usage(f"--role-grants {role!r} declared more than once")
        names = [] if value == "" else value.split(",")
        seen: set = set()
        for name in names:
            if not is_known_grant_name(name):
                raise _Usage(
                    f"--role-grants {role}: unknown capability or preset "
                    f"name: {name!r} — the vocabulary is closed; see "
                    f"CAPABILITIES.md (presets: {', '.join(sorted(PRESETS))})"
                )
            if name in seen:
                raise _Usage(f"--role-grants {role}: {name!r} listed more than once")
            seen.add(name)
        grants[role] = names
    return grants


def _collect_role_launch_field(
    raw_args: List[str], flag: str, declared_roles: Dict[str, str],
    seen: Dict[str, set], field: str, parser
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in raw_args:
        role, value = _parse_kv(raw, flag)
        if not _ROLE_NAME_RE.match(role):
            raise _Usage(f"{flag} role must match [A-Za-z0-9_-]+; got: {role!r}")
        if role == "pm":
            raise _Usage(
                "pm-launch-forbidden: the `pm` role is the interactive session "
                f"orchestrator and cannot be launched; drop {flag} pm=…"
            )
        if role not in declared_roles:
            raise _Usage(f"orphan-handoff: {role} — declare with --role first")
        if role in seen[field]:
            raise _Usage(f"{flag} {role!r} declared more than once")
        seen[field].add(role)
        out[role] = parser(value, f"{flag} {role}")
    return out


def _build_role_execution(
    target_args: List[str],
    model_args: List[str],
    effort_args: List[str],
    auto_launch_args: List[str],
    timeout_args: List[str],
    declared_roles: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    """Build preferred flat role-local execution and permission fields."""
    seen = {
        "target": set(),
        "model": set(),
        "effort": set(),
        "timeout": set(),
    }
    targets = _collect_role_launch_field(
        target_args,
        "--role-launch-target",
        declared_roles,
        seen,
        "target",
        lambda value, ctx: value
        if value
        else (_ for _ in ()).throw(_Usage(f"{ctx}: target must be non-empty")),
    )
    models = _collect_role_launch_field(
        model_args,
        "--role-launch-model",
        declared_roles,
        seen,
        "model",
        lambda value, ctx: value
        if value
        else (_ for _ in ()).throw(_Usage(f"{ctx}: model must be non-empty")),
    )
    efforts = _collect_role_launch_field(
        effort_args,
        "--role-launch-effort",
        declared_roles,
        seen,
        "effort",
        lambda value, ctx: value
        if value
        else (_ for _ in ()).throw(_Usage(f"{ctx}: effort must be non-empty")),
    )
    timeouts = _collect_role_launch_field(
        timeout_args,
        "--role-launch-timeout",
        declared_roles,
        seen,
        "timeout",
        lambda value, ctx: value
        if re.fullmatch(r"[1-9]\d*[smh]", value)
        else (_ for _ in ()).throw(
            _Usage(f"{ctx}: duration must be positive with s, m, or h suffix")
        ),
    )
    auto_launch: Dict[str, List[str]] = {}
    for raw in auto_launch_args:
        role, value = _parse_kv(raw, "--role-auto-launch")
        if role not in declared_roles:
            raise _Usage(
                f"--role-auto-launch {role!r}: declare with --role first"
            )
        if role == "pm":
            raise _Usage("pm-launch-forbidden: the pm role cannot auto-launch")
        if role in auto_launch:
            raise _Usage(f"--role-auto-launch {role!r} declared more than once")
        activities = [] if value == "" else value.split(",")
        if len(set(activities)) != len(activities):
            raise _Usage(
                f"--role-auto-launch {role}: activities must be unique"
            )
        for activity in activities:
            if activity not in AUTO_LAUNCH_ACTIVITIES:
                raise _Usage(
                    f"--role-auto-launch {role}: unknown activity {activity!r}; "
                    f"expected {', '.join(AUTO_LAUNCH_ACTIVITIES)}"
                )
        auto_launch[role] = activities

    result: Dict[str, Dict[str, Any]] = {}
    for role in declared_roles:
        block: Dict[str, Any] = {}
        for key, values in (
            ("target", targets),
            ("model", models),
            ("effort", efforts),
            ("timeout", timeouts),
        ):
            if role in values:
                block[key] = values[role]
        if role in auto_launch:
            block["auto_launch"] = auto_launch[role]
        if block:
            result[role] = block
    return result


def _build_config(args: argparse.Namespace, project_schema_version: str) -> Dict[str, Any]:
    # [project] required fields
    if args.name == "":
        raise _Usage("--name must be non-empty")
    if not is_kebab_case(args.proj_id):
        raise _Usage(f"--id must be kebab-case [a-z0-9][a-z0-9-]*; got: {args.proj_id!r}")

    roles = _collect_roles(args.role)
    role_grants = _collect_role_grants(args.role_grants, roles)

    role_execution = _build_role_execution(
        args.role_launch_target,
        args.role_launch_model,
        args.role_launch_effort,
        args.role_auto_launch,
        args.role_launch_timeout,
        roles,
    )

    project_block: Dict[str, Any] = {
        "name": args.name,
        "id": args.proj_id,
        "project_schema_version": project_schema_version,
    }

    work_roots = list(args.work_root)
    seen_wr: set = set()
    for name in work_roots:
        if not _WORK_ROOT_RE.match(name):
            raise _Usage(f"--work-root name must match [A-Za-z0-9_-]+; got: {name!r}")
        if name in seen_wr:
            raise _Usage(f"--work-root {name!r} declared more than once")
        seen_wr.add(name)
    if work_roots:
        project_block["work_roots"] = work_roots

    automation: Dict[str, Any] = {}
    if args.automation_initiation is not None:
        automation["initiation"] = args.automation_initiation
    if args.automation_confirmation is not None:
        automation["confirmation"] = args.automation_confirmation
    if args.automation_max_handoffs is not None:
        try:
            n = int(args.automation_max_handoffs)
        except ValueError:
            raise _Usage(
                f"--automation-max-handoffs must be a positive integer; got: "
                f"{args.automation_max_handoffs!r}"
            )
        if n <= 0:
            raise _Usage(
                f"--automation-max-handoffs must be a positive integer; got: {n}"
            )
        automation["max_handoffs_per_run"] = n

    defaults: Dict[str, Any] = {}
    git_versioning_set = args.git_versioning is not None
    git_versioning_true = args.git_versioning == "true"
    if git_versioning_set:
        defaults["git_versioning"] = git_versioning_true

    git_block: Dict[str, Any] = {}
    if args.git_key:
        if not git_versioning_true:
            raise _Usage(
                "--git-key requires --git-versioning true"
            )
        seen_keys: set = set()
        for raw in args.git_key:
            key, value = _parse_kv(raw, "--git-key")
            if not _BARE_KEY_RE.match(key):
                raise _Usage(
                    f"--git-key key must be a TOML bare key [A-Za-z0-9_-]+; got: {key!r}"
                )
            if key in seen_keys:
                raise _Usage(f"--git-key {key!r} declared more than once")
            seen_keys.add(key)
            git_block[key] = _parse_git_key_value(value)

    roles_block: Dict[str, Any] = {}
    for role_name, description in roles.items():
        role_block: Dict[str, Any] = {"description": description}
        if role_name in role_grants:
            role_block["grants"] = role_grants[role_name]
        role_block.update(role_execution.get(role_name, {}))
        roles_block[role_name] = role_block

    reviews_block: Dict[str, Any] = {}
    review_values = (
        ("planning", getattr(args, "review_planning", None)),
        ("planning_role", getattr(args, "review_planning_role", None)),
        ("task_closure", getattr(args, "review_task_closure", None)),
        ("task_role", getattr(args, "review_task_role", None)),
    )
    for key, value in review_values:
        if value is not None:
            if key.endswith("_role") and not _ROLE_NAME_RE.match(value):
                raise _Usage(
                    f"--review-{key.replace('_', '-')} must match "
                    f"[A-Za-z0-9_-]+; got: {value!r}"
                )
            reviews_block[key] = value

    cfg: Dict[str, Any] = {"project": project_block}
    if roles_block:
        cfg["roles"] = roles_block
    if reviews_block:
        cfg["reviews"] = reviews_block
    if automation:
        cfg["automation"] = automation
    if defaults:
        cfg["defaults"] = defaults
    if git_block:
        cfg["git"] = git_block
    try:
        validate_authored_config(cfg, "project")
    except ConfigDiagnostic as exc:
        raise _Usage(str(exc)) from exc
    return cfg


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    project_path = Path(raw_path)
    config_path = project_path / "cartopian.toml"

    try:
        project_schema_version = _read_project_schema_version()
    except (OSError, RuntimeError) as exc:
        _stderr("error", str(exc))
        return EXIT_FAIL

    try:
        cfg = _build_config(args, project_schema_version)
    except _Usage as exc:
        _stderr("usage", str(exc))
        return EXIT_USAGE

    try:
        global_cfg = _load_toml(
            Path.home() / ".cartopian" / "cartopian.toml", "global config"
        ) or {}
        # A new project may declare work-root names before the operator adds
        # machine-specific paths. Use non-persisted absolute placeholders so
        # all other cross-field authority checks still run before writing.
        local_validation = {
            "work_roots": {
                name: str((project_path / name).resolve())
                for name in cfg["project"].get("work_roots", [])
            }
        }
        resolve_configuration(global_cfg, cfg, local_validation)
    except ConfigDiagnostic as exc:
        _stderr("usage", str(exc))
        return EXIT_USAGE

    if config_path.exists():
        _stderr(
            "guard",
            f"cartopian.toml already exists at {config_path} — refusing to overwrite",
        )
        return EXIT_FAIL

    if not project_path.is_dir():
        _stderr("error", f"project path is not a directory: {project_path}")
        return EXIT_FAIL

    payload = tomli_w.dumps(cfg)
    config_path.write_text(payload, encoding="utf-8")

    emit_record(
        {
            "action": "generate-config",
            "details": {
                "project_path": str(project_path),
                "config_path": str(config_path),
                "project_schema_version": project_schema_version,
            },
        }
    )
    return EXIT_OK
