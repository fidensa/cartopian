"""`cartopian generate-config <project-path>`.

Writes ``<project-path>/cartopian.toml`` from the supplied flags. Always stamps
``[project] protocol_version`` to the current protocol version (the topmost
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
from cli.commands._registry import is_kebab_case
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

_ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_WORK_ROOT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PROTOCOL_VERSION_RE = re.compile(r"^###\s+(v\d+\.\d+\.\d+)\b", re.MULTILINE)

# Repo root is two parents up from this file: cli/commands/generate_config.py → repo.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHANGELOG_PATH = _REPO_ROOT / "protocol" / "CHANGELOG.md"


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


class _Usage(Exception):
    pass


class _SingleValuedAction(argparse.Action):
    """Store action that rejects a repeated occurrence with `[usage]` exit 2.

    The flags `--name`, `--id`, `--automation-confirmation`,
    `--automation-max-handoffs`, and `--git-versioning` are single-valued.
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
    subparser.add_argument("--handoff", action="append", default=[], metavar="ROLE=WRAPPER",
                           help="Repeatable handoff agent assignment")
    subparser.add_argument("--handoff-model", action="append", default=[],
                           metavar="ROLE=MODEL", help="Repeatable handoff model")
    subparser.add_argument("--handoff-auto-start", action="append", default=[],
                           metavar="ROLE=BOOL", help="Repeatable handoff auto_start")
    subparser.add_argument("--handoff-timeout", action="append", default=[],
                           metavar="ROLE=DURATION", help="Repeatable handoff timeout")
    subparser.add_argument("--automation-confirmation", default=None,
                           action=_SingleValuedAction,
                           choices=["each-handoff", "until-blocked"],
                           help="[automation] confirmation")
    subparser.add_argument("--automation-max-handoffs", default=None,
                           action=_SingleValuedAction,
                           metavar="N", help="[automation] max_handoffs_per_run (positive int)")
    subparser.add_argument("--work-root", action="append", default=[], metavar="NAME",
                           help="Repeatable work-root name")
    subparser.add_argument("--git-versioning", default=None,
                           action=_SingleValuedAction,
                           choices=["true", "false"], help="[defaults] git_versioning")
    subparser.add_argument("--git-key", action="append", default=[], metavar="KEY=VALUE",
                           help="Repeatable [git] entry (primitive type preserved)")


def _read_protocol_version() -> str:
    text = _CHANGELOG_PATH.read_text(encoding="utf-8")
    _, _, body = text.partition("\n## Entries\n")
    m = _PROTOCOL_VERSION_RE.search(body)
    if not m:
        raise RuntimeError(
            f"could not locate a protocol version entry in {_CHANGELOG_PATH}"
        )
    return m.group(1)


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


def _collect_handoff_field(
    raw_args: List[str], flag: str, declared_roles: Dict[str, str],
    seen: Dict[str, set], field: str, parser
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw in raw_args:
        role, value = _parse_kv(raw, flag)
        if not _ROLE_NAME_RE.match(role):
            raise _Usage(f"{flag} role must match [A-Za-z0-9_-]+; got: {role!r}")
        if role not in declared_roles:
            raise _Usage(f"orphan-handoff: {role} — declare with --role first")
        if role in seen[field]:
            raise _Usage(f"{flag} {role!r} declared more than once")
        seen[field].add(role)
        out[role] = parser(value, f"{flag} {role}")
    return out


def _build_handoffs(
    handoff_args: List[str], model_args: List[str], auto_args: List[str],
    timeout_args: List[str], declared_roles: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    seen = {"agent": set(), "model": set(), "auto_start": set(), "timeout": set()}
    agents = _collect_handoff_field(
        handoff_args, "--handoff", declared_roles, seen, "agent",
        lambda v, ctx: v if v != "" else (_ for _ in ()).throw(_Usage(f"{ctx}: wrapper must be non-empty")),
    )
    models = _collect_handoff_field(
        model_args, "--handoff-model", declared_roles, seen, "model",
        lambda v, ctx: v if v != "" else (_ for _ in ()).throw(_Usage(f"{ctx}: model must be non-empty")),
    )
    auto_starts = _collect_handoff_field(
        auto_args, "--handoff-auto-start", declared_roles, seen, "auto_start",
        _parse_bool,
    )
    timeouts = _collect_handoff_field(
        timeout_args, "--handoff-timeout", declared_roles, seen, "timeout",
        lambda v, ctx: v if v != "" else (_ for _ in ()).throw(_Usage(f"{ctx}: duration must be non-empty")),
    )
    handoffs: Dict[str, Dict[str, Any]] = {}
    for role in declared_roles:
        block: Dict[str, Any] = {}
        if role in agents:
            block["agent"] = agents[role]
        if role in models:
            block["model"] = models[role]
        if role in auto_starts:
            block["auto_start"] = auto_starts[role]
        if role in timeouts:
            block["timeout"] = timeouts[role]
        if block:
            handoffs[role] = block
    return handoffs


def _build_config(args: argparse.Namespace, protocol_version: str) -> Dict[str, Any]:
    # [project] required fields
    if args.name == "":
        raise _Usage("--name must be non-empty")
    if not is_kebab_case(args.proj_id):
        raise _Usage(f"--id must be kebab-case [a-z0-9][a-z0-9-]*; got: {args.proj_id!r}")

    roles = _collect_roles(args.role)

    handoffs = _build_handoffs(
        args.handoff, args.handoff_model, args.handoff_auto_start,
        args.handoff_timeout, roles
    )

    project_block: Dict[str, Any] = {
        "name": args.name,
        "id": args.proj_id,
        "protocol_version": protocol_version,
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

    cfg: Dict[str, Any] = {"project": project_block}
    if roles:
        cfg["roles"] = roles
    if handoffs:
        cfg["handoffs"] = handoffs
    if automation:
        cfg["automation"] = automation
    if defaults:
        cfg["defaults"] = defaults
    if git_block:
        cfg["git"] = git_block
    return cfg


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    project_path = Path(raw_path)
    config_path = project_path / "cartopian.toml"

    try:
        protocol_version = _read_protocol_version()
    except (OSError, RuntimeError) as exc:
        _stderr("error", str(exc))
        return EXIT_FAIL

    try:
        cfg = _build_config(args, protocol_version)
    except _Usage as exc:
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
                "protocol_version": protocol_version,
            },
        }
    )
    return EXIT_OK
