"""`cartopian update-config <project-path> [--local] <operations…>`.

The mediated, validated editor for a project's **existing** ``cartopian.toml``
(and, with ``--local``, its per-machine ``cartopian.local.toml``). This is how
the PM manages config on the operator's behalf — only on the operator's explicit
request (a procedural rule the skills enforce; the CLI cannot know who asked) —
and how PM-owned migration performs its config edits.

Design:

- **Closed schema.** Only the dotted keys in :data:`SCHEMA` are settable, each
  with an explicit type/validator — types come from the schema, never inferred
  from value text (so a numeric-looking branch pattern stays a string). Role and
  handoff structure is edited through dedicated ``--set-role`` / ``--set-role-
  grants`` / ``--set-handoff`` / ``--remove-*`` flags.
- **Comment-preserving surgical edits.** The file is edited as a line model, not
  re-serialized through a TOML dumper: untouched keys, comments, and formatting
  survive byte-for-byte. A targeted edit that would require lexing a construct
  the surgical editor cannot handle safely — a multiline string, an inline
  table, a dotted-key form, or an array-of-tables — **fails closed**.
- **Existence.** The project ``cartopian.toml`` must already exist (use
  ``generate-config`` / ``init project`` to create it). ``--local`` may create
  ``cartopian.local.toml``, but only when at least one mapping is supplied — it
  never creates an empty local file.
- **Two-layer validation before write.** (1) the edited file parses and every
  touched key matches the closed schema; (2) the resulting *effective* project +
  global (+ local) configuration validates via the shared resolution functions
  in :mod:`cli.commands.resolve_config`, so inherited global roles/handoffs are
  respected. Only a result valid at both layers is written.
- **Guarded atomic write.** The same TOCTOU-hardened primitive the mediated
  ``.md`` writer uses (:mod:`cli.atomic_write`), with must-be-regular /
  single-link / no-symlink / exec-mask guards and preserved permissions.

A no-op edit (every value already equal) writes nothing and reports
``changed: []`` — byte-identical.
"""
import argparse
import os
import re
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cli.atomic_write import (
    DIR_FD_SUPPORTED,
    GuardRefusal,
    _atomic_write_via_dir_fd,
    _atomic_write_via_path,
    _snapshot_chain,
    make_tmp_name,
)
from cli.capabilities import PRESETS, is_known_grant_name
from cli.commands._registry import is_kebab_case
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_handoffs,
    _resolve_roles,
    resolve_grants,
    role_description,
    validate_effective_config,
)
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HANDOFF_FIELDS = ("agent", "model", "auto_start", "timeout")


class _Usage(Exception):
    pass


class _Guard(Exception):
    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail
        super().__init__(f"{rule}: {detail}")


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


# ---------------------------------------------------------------------------
# Value validators / serializers. Each takes the raw CLI string and returns a
# TOML-serialized token, or raises _Usage. Types come from the schema, never
# inferred from the text.
# ---------------------------------------------------------------------------
def _toml_str(raw: str) -> str:
    # Strip optional surrounding double quotes for shell convenience.
    if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if raw == "":
        raise _Usage("value must be non-empty")
    escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _v_nonempty(raw: str) -> str:
    return _toml_str(raw)


def _v_kebab(raw: str) -> str:
    val = raw[1:-1] if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"') else raw
    if not is_kebab_case(val):
        raise _Usage(f"must be kebab-case [a-z0-9][a-z0-9-]*; got: {val!r}")
    return _toml_str(val)


_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _v_version(raw: str) -> str:
    val = raw[1:-1] if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"') else raw
    if not _VERSION_RE.match(val):
        raise _Usage(f"must be a vX.Y.Z protocol version; got: {val!r}")
    return _toml_str(val)


def _v_bool(raw: str) -> str:
    if raw in ("true", "false"):
        return raw
    raise _Usage(f"expects true|false; got: {raw!r}")


def _v_posint(raw: str) -> str:
    try:
        n = int(raw)
    except ValueError:
        raise _Usage(f"must be a positive integer; got: {raw!r}")
    if n <= 0:
        raise _Usage(f"must be a positive integer; got: {n}")
    return str(n)


def _v_enum(*allowed: str) -> Callable[[str], str]:
    def check(raw: str) -> str:
        val = raw[1:-1] if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"') else raw
        if val not in allowed:
            raise _Usage(f"must be one of {', '.join(allowed)}; got: {val!r}")
        return _toml_str(val)
    return check


def _v_name_list(raw: str) -> str:
    names = [] if raw == "" else [n.strip() for n in raw.split(",")]
    seen: set = set()
    for name in names:
        if not _NAME_RE.match(name):
            raise _Usage(f"work-root name must match [A-Za-z0-9_-]+; got: {name!r}")
        if name in seen:
            raise _Usage(f"work-root name {name!r} listed more than once")
        seen.add(name)
    inner = ", ".join(_toml_str(n) for n in names)
    return f"[{inner}]"


# dotted-key -> (table_path, key, validator). Closed and explicit.
SCHEMA: Dict[str, Tuple[Tuple[str, ...], str, Callable[[str], str]]] = {
    "project.name": (("project",), "name", _v_nonempty),
    "project.id": (("project",), "id", _v_kebab),
    "project.protocol_version": (("project",), "protocol_version", _v_version),
    "project.work_roots": (("project",), "work_roots", _v_name_list),
    "automation.initiation": (("automation",), "initiation", _v_enum("operator", "auto")),
    "automation.confirmation": (
        ("automation",), "confirmation", _v_enum("each-handoff", "until-blocked"),
    ),
    "automation.max_handoffs_per_run": (("automation",), "max_handoffs_per_run", _v_posint),
    "defaults.git_versioning": (("defaults",), "git_versioning", _v_bool),
    "git.pm_owns_product_branches": (("git",), "pm_owns_product_branches", _v_bool),
    "git.default_branch_pattern": (("git",), "default_branch_pattern", _v_nonempty),
    "git.default_merge_strategy": (
        ("git",), "default_merge_strategy", _v_enum("merge", "squash", "rebase"),
    ),
}

# The table heads the surgical editor manages; a dotted-key or inline-table form
# for any of these (e.g. `automation.initiation = ...` at top level, or
# `automation = { ... }`) is a construct we refuse to edit blindly.
_MANAGED_HEADS = frozenset({"project", "automation", "defaults", "git", "roles", "handoffs"})


# ---------------------------------------------------------------------------
# Line-model TOML editor. A block is either the preamble (path=None) or a table
# (path=tuple of names). Editing operates on block bodies so untouched bytes are
# preserved exactly.
# ---------------------------------------------------------------------------
class _Block:
    __slots__ = ("path", "header", "body")

    def __init__(self, path: Optional[Tuple[str, ...]], header: Optional[str]) -> None:
        self.path = path
        self.header = header  # header line (no newline), or None for preamble
        self.body: List[str] = []


_TABLE_HEADER_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(#.*)?$")
_ARRAY_TABLE_RE = re.compile(r"^\s*\[\[")
_KEY_RE = re.compile(r'^\s*("(?:[^"\\]|\\.)*"|[A-Za-z0-9_.\-]+)\s*=(.*)$')


def _parse_table_path(inner: str) -> Optional[Tuple[str, ...]]:
    parts = []
    for seg in inner.split("."):
        seg = seg.strip()
        if len(seg) >= 2 and seg[0] == '"' and seg[-1] == '"':
            seg = seg[1:-1]
        if not _NAME_RE.match(seg):
            return None
        parts.append(seg)
    return tuple(parts)


def _key_name(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


class _Model:
    """Parsed line model of a simple TOML file, with surgical editors."""

    def __init__(self, blocks: List[_Block], newline: str, final_nl: bool) -> None:
        self.blocks = blocks
        self.newline = newline
        self.final_nl = final_nl

    @classmethod
    def parse(cls, text: str) -> "_Model":
        newline = "\r\n" if "\r\n" in text else "\n"
        # A freshly authored file (empty input) still ends with a newline.
        final_nl = text.endswith("\n") or text == ""
        body = text
        if final_nl:
            # drop exactly one trailing newline for a clean round-trip
            body = text[:-len(newline)] if text.endswith(newline) else text[:-1]
        lines = body.split(newline) if body != "" else []

        # A multiline string anywhere breaks one-key-per-line accounting; refuse.
        if '"""' in text or "'''" in text:
            raise _Guard(
                "unsupported-toml",
                "file contains a multiline string; edit it manually or via generate-config",
            )

        blocks: List[_Block] = [_Block(None, None)]
        for line in lines:
            if _ARRAY_TABLE_RE.match(line):
                raise _Guard(
                    "unsupported-toml",
                    "file contains an array-of-tables ([[...]]); cannot edit safely",
                )
            m = _TABLE_HEADER_RE.match(line)
            if m and "=" not in line.split("#", 1)[0]:
                path = _parse_table_path(m.group(1))
                if path is None:
                    # An exotic header we can't model; keep it verbatim in the
                    # current block body rather than misinterpret it.
                    blocks[-1].body.append(line)
                    continue
                blk = _Block(path, line)
                blocks.append(blk)
                continue
            blocks[-1].body.append(line)
        return cls(blocks, newline, final_nl)

    def render(self) -> str:
        parts: List[str] = []
        for b in self.blocks:
            if b.header is not None:
                parts.append(b.header)
            parts.extend(b.body)
        text = self.newline.join(parts)
        if self.final_nl:
            text += self.newline
        return text

    def find_block(self, path: Tuple[str, ...]) -> Optional[_Block]:
        for b in self.blocks:
            if b.path == path:
                return b
        return None

    def _guard_dotted_or_inline(self, head: str) -> None:
        """Refuse when a managed table head appears as a top-level dotted key or
        inline table (constructs the surgical editor cannot target safely)."""
        for b in self.blocks:
            body = b.body if b.path is not None else b.body
            for line in body:
                km = _KEY_RE.match(line)
                if not km:
                    continue
                key_tok = km.group(1)
                rhs = km.group(2).strip()
                key = _key_name(key_tok)
                # top-level dotted key `head.x = ...` (only meaningful in preamble
                # or a table whose path is a prefix — we conservatively refuse any)
                if b.path is None and "." in key and key.split(".", 1)[0] == head:
                    raise _Guard(
                        "unsupported-toml",
                        f"'{key}' is a dotted key; edit [{head}] as a table or use generate-config",
                    )
                # inline-table value for the head itself, e.g. `automation = { ... }`
                if b.path is None and key == head and rhs.startswith("{"):
                    raise _Guard(
                        "unsupported-toml",
                        f"[{head}] is written as an inline table; cannot edit safely",
                    )

    def find_key(self, block: _Block, key: str) -> Optional[int]:
        for i, line in enumerate(block.body):
            km = _KEY_RE.match(line)
            if km and _key_name(km.group(1)) == key:
                return i
        return None

    def _value_span(self, block: _Block, idx: int) -> int:
        """Number of body lines the value at ``idx`` occupies (>=1).

        A value that opens a bracket/brace and does not close it on the same
        line (a multiline array, as ``tomli_w`` emits) spans until the matching
        close. An unterminated value fails closed."""
        km = _KEY_RE.match(block.body[idx])
        depth = _bracket_delta(km.group(2))
        if depth <= 0:
            return 1
        span = 1
        i = idx + 1
        while i < len(block.body):
            depth += _bracket_delta(block.body[i])
            span += 1
            if depth <= 0:
                return span
            i += 1
        raise _Guard("unsupported-toml", f"unterminated multiline value for key at line {idx}")

    def get_value_token(self, block: _Block, key: str) -> Optional[str]:
        idx = self.find_key(block, key)
        if idx is None:
            return None
        km = _KEY_RE.match(block.body[idx])
        rhs = km.group(2)
        val, _ = _split_value_comment(rhs)
        return val.strip()

    def set_key(self, path: Tuple[str, ...], key: str, value_token: str) -> None:
        head = path[0]
        self._guard_dotted_or_inline(head)
        block = self.find_block(path)
        new_line = f"{key} = {value_token}"
        if block is None:
            self._append_table(path, [new_line])
            return
        idx = self.find_key(block, key)
        if idx is None:
            self._insert_body_line(block, new_line)
        else:
            span = self._value_span(block, idx)
            km = _KEY_RE.match(block.body[idx])
            indent = block.body[idx][: len(block.body[idx]) - len(block.body[idx].lstrip())]
            # Preserve a trailing inline comment only for a single-line value.
            comment = ""
            if span == 1:
                _, comment = _split_value_comment(km.group(2))
            replacement = f"{indent}{key} = {value_token}" + (f"  {comment}" if comment else "")
            block.body[idx:idx + span] = [replacement]

    def unset_key(self, path: Tuple[str, ...], key: str) -> None:
        block = self.find_block(path)
        if block is None:
            return
        idx = self.find_key(block, key)
        if idx is not None:
            span = self._value_span(block, idx)
            del block.body[idx:idx + span]

    def _insert_body_line(self, block: _Block, line: str) -> None:
        last_content = -1
        for i, b in enumerate(block.body):
            if b.strip() != "":
                last_content = i
        block.body.insert(last_content + 1, line)

    def _append_table(self, path: Tuple[str, ...], body_lines: List[str]) -> None:
        # Separate from prior content with a blank line if needed.
        if self.blocks:
            tail = self.blocks[-1]
            if (tail.body and tail.body[-1].strip() != "") or (not tail.body and tail.header):
                tail.body.append("")
        header = "[" + ".".join(path) + "]"
        blk = _Block(path, header)
        blk.body = list(body_lines)
        self.blocks.append(blk)

    # -- role helpers (compose string <-> table forms, preserving description) --
    def role_form(self, name: str) -> str:
        if self.find_block(("roles", name)) is not None:
            return "table"
        roles = self.find_block(("roles",))
        if roles is not None and self.find_key(roles, name) is not None:
            return "string"
        return "absent"

    def role_description(self, name: str) -> Optional[str]:
        form = self.role_form(name)
        if form == "string":
            tok = self.get_value_token(self.find_block(("roles",)), name)
            return _parse_toml_value(tok) if tok is not None else None
        if form == "table":
            tok = self.get_value_token(self.find_block(("roles", name)), "description")
            return _parse_toml_value(tok) if tok is not None else None
        return None

    def set_role_description(self, name: str, desc_token: str) -> None:
        form = self.role_form(name)
        if form == "table":
            self.set_key(("roles", name), "description", desc_token)
        elif form == "string":
            roles = self.find_block(("roles",))
            idx = self.find_key(roles, name)
            indent = roles.body[idx][: len(roles.body[idx]) - len(roles.body[idx].lstrip())]
            roles.body[idx] = f"{indent}{name} = {desc_token}"
        else:
            self._guard_dotted_or_inline("roles")
            roles = self.find_block(("roles",))
            if roles is None:
                self._append_table(("roles",), [f"{name} = {desc_token}"])
            else:
                self._insert_body_line(roles, f"{name} = {desc_token}")

    def set_role_grants(self, name: str, grants_token: str) -> None:
        form = self.role_form(name)
        if form == "table":
            self.set_key(("roles", name), "grants", grants_token)
            return
        # string or absent -> table form, preserving any existing description
        desc = self.role_description(name)
        if form == "string":
            roles = self.find_block(("roles",))
            idx = self.find_key(roles, name)
            del roles.body[idx]
        desc_token = _serialize_desc(desc) if desc is not None else None
        body = []
        if desc_token is not None:
            body.append(f"description = {desc_token}")
        body.append(f"grants = {grants_token}")
        self._append_table(("roles", name), body)

    def remove_role(self, name: str) -> None:
        blk = self.find_block(("roles", name))
        if blk is not None:
            self.blocks.remove(blk)
            return
        roles = self.find_block(("roles",))
        if roles is not None:
            idx = self.find_key(roles, name)
            if idx is not None:
                del roles.body[idx]

    def set_handoff_field(self, role: str, field: str, value_token: str) -> None:
        self.set_key(("handoffs", role), field, value_token)

    def remove_handoff(self, role: str) -> None:
        blk = self.find_block(("handoffs", role))
        if blk is not None:
            self.blocks.remove(blk)


def _split_value_comment(rhs: str) -> Tuple[str, str]:
    """Split a value region (text after ``=``) into (value, trailing-comment).

    Scans outside quoted strings so a ``#`` inside a string is not mistaken for
    a comment. Returns the comment including its leading ``#`` (or "")."""
    in_str: Optional[str] = None
    i = 0
    while i < len(rhs):
        c = rhs[i]
        if in_str:
            if c == "\\" and in_str == '"':
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'"):
                in_str = c
            elif c == "#":
                return rhs[:i], rhs[i:].strip()
        i += 1
    return rhs, ""


def _bracket_delta(s: str) -> int:
    """Net bracket/brace depth change across ``s``, ignoring string contents and
    trailing ``#`` comments — used to measure how many lines a value spans."""
    depth = 0
    in_str: Optional[str] = None
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\" and in_str == '"':
                i += 2
                continue
            if c == in_str:
                in_str = None
        else:
            if c in ('"', "'"):
                in_str = c
            elif c == "#":
                break
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
        i += 1
    return depth


def _parse_toml_value(token: str) -> Any:
    try:
        return tomllib.loads(f"__x__ = {token}")["__x__"]
    except (tomllib.TOMLDecodeError, KeyError):
        return None


def _serialize_desc(desc: str) -> str:
    escaped = desc.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ---------------------------------------------------------------------------
# Argument parsing.
# ---------------------------------------------------------------------------
def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("project_path", help="Absolute path to the project root")
    subparser.add_argument(
        "--local", action="store_true",
        help="Target <project>/cartopian.local.toml (work-root mappings) instead of cartopian.toml",
    )
    subparser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                           help="Set a schema key (repeatable)")
    subparser.add_argument("--unset", action="append", default=[], metavar="KEY",
                           help="Remove a schema key (repeatable)")
    subparser.add_argument("--set-role", action="append", default=[], metavar='NAME="DESC"',
                           help="Set a role description (repeatable)")
    subparser.add_argument("--set-role-grants", action="append", default=[],
                           metavar="NAME=GRANT[,GRANT...]",
                           help="Set a role's capability grants (empty = explicit empty list)")
    subparser.add_argument("--set-handoff", action="append", default=[],
                           metavar="ROLE.FIELD=VALUE", help="Set a handoff field (repeatable)")
    subparser.add_argument("--remove-role", action="append", default=[], metavar="NAME",
                           help="Remove a role (repeatable)")
    subparser.add_argument("--remove-handoff", action="append", default=[], metavar="ROLE",
                           help="Remove a handoff block (repeatable)")
    subparser.add_argument("--set-work-root", action="append", default=[],
                           metavar="NAME=ABS_PATH",
                           help="[--local only] Map a work-root name to an absolute path")
    subparser.add_argument("--unset-work-root", action="append", default=[], metavar="NAME",
                           help="[--local only] Remove a work-root mapping")


def _parse_kv(raw: str, flag: str) -> Tuple[str, str]:
    if "=" not in raw:
        raise _Usage(f"{flag} expects <key>=<value>; got: {raw}")
    key, _, value = raw.partition("=")
    key = key.strip()
    if not key:
        raise _Usage(f"{flag} <key> is empty: {raw}")
    return key, value


# ---------------------------------------------------------------------------
# Operation planning: parse args into a validated op list, rejecting duplicates
# and conflicts up front (no silent last-wins).
# ---------------------------------------------------------------------------
def _plan_project_ops(args: argparse.Namespace) -> List[Tuple]:
    ops: List[Tuple] = []
    touched_keys: set = set()
    set_roles: set = set()
    grant_roles: set = set()
    removed_roles: set = set()
    handoff_fields: set = set()
    removed_handoffs: set = set()

    for raw in getattr(args, "set"):
        key, value = _parse_kv(raw, "--set")
        if key not in SCHEMA:
            raise _Usage(f"--set: unknown config key {key!r} (closed schema; see update-config --help)")
        if key in touched_keys:
            raise _Usage(f"--set/--unset {key!r} given more than once")
        touched_keys.add(key)
        path, tk, validator = SCHEMA[key]
        try:
            token = validator(value)
        except _Usage as exc:
            raise _Usage(f"--set {key}: {exc}")
        ops.append(("set", path, tk, token))

    for key in getattr(args, "unset"):
        key = key.strip()
        if key not in SCHEMA:
            raise _Usage(f"--unset: unknown config key {key!r} (closed schema)")
        if key in touched_keys:
            raise _Usage(f"--set/--unset {key!r} given more than once")
        touched_keys.add(key)
        path, tk, _ = SCHEMA[key]
        ops.append(("unset", path, tk))

    for raw in args.set_role:
        name, value = _parse_kv(raw, "--set-role")
        if not _NAME_RE.match(name):
            raise _Usage(f"--set-role name must match [A-Za-z0-9_-]+; got: {name!r}")
        if name in set_roles:
            raise _Usage(f"--set-role {name!r} given more than once")
        set_roles.add(name)
        try:
            token = _toml_str(value)
        except _Usage as exc:
            raise _Usage(f"--set-role {name}: {exc}")
        ops.append(("set-role", name, token))

    for raw in args.set_role_grants:
        name, value = _parse_kv(raw, "--set-role-grants")
        if not _NAME_RE.match(name):
            raise _Usage(f"--set-role-grants name must match [A-Za-z0-9_-]+; got: {name!r}")
        if name in grant_roles:
            raise _Usage(f"--set-role-grants {name!r} given more than once")
        grant_roles.add(name)
        names = [] if value == "" else [g.strip() for g in value.split(",")]
        seen: set = set()
        for g in names:
            if not is_known_grant_name(g):
                raise _Usage(
                    f"--set-role-grants {name}: unknown capability or preset {g!r} "
                    f"(closed vocabulary; presets: {', '.join(sorted(PRESETS))})"
                )
            if g in seen:
                raise _Usage(f"--set-role-grants {name}: {g!r} listed more than once")
            seen.add(g)
        token = "[" + ", ".join(_toml_str(g) for g in names) + "]"
        ops.append(("set-role-grants", name, token))

    for raw in args.set_handoff:
        lhs, value = _parse_kv(raw, "--set-handoff")
        if "." not in lhs:
            raise _Usage(f"--set-handoff expects ROLE.FIELD=VALUE; got: {raw}")
        role, field = lhs.split(".", 1)
        if not _NAME_RE.match(role):
            raise _Usage(f"--set-handoff role must match [A-Za-z0-9_-]+; got: {role!r}")
        if role == "pm":
            raise _Usage("--set-handoff: the pm role is never launched as a handoff; drop pm.*")
        if field not in _HANDOFF_FIELDS:
            raise _Usage(f"--set-handoff field must be one of {', '.join(_HANDOFF_FIELDS)}; got: {field!r}")
        if (role, field) in handoff_fields:
            raise _Usage(f"--set-handoff {role}.{field} given more than once")
        handoff_fields.add((role, field))
        if field == "auto_start":
            try:
                token = _v_bool(value)
            except _Usage as exc:
                raise _Usage(f"--set-handoff {role}.auto_start: {exc}")
        else:
            try:
                token = _toml_str(value)
            except _Usage as exc:
                raise _Usage(f"--set-handoff {role}.{field}: {exc}")
        ops.append(("set-handoff", role, field, token))

    for name in args.remove_role:
        name = name.strip()
        if not _NAME_RE.match(name):
            raise _Usage(f"--remove-role name must match [A-Za-z0-9_-]+; got: {name!r}")
        if name in removed_roles:
            raise _Usage(f"--remove-role {name!r} given more than once")
        removed_roles.add(name)
        ops.append(("remove-role", name))

    for role in args.remove_handoff:
        role = role.strip()
        if not _NAME_RE.match(role):
            raise _Usage(f"--remove-handoff role must match [A-Za-z0-9_-]+; got: {role!r}")
        if role in removed_handoffs:
            raise _Usage(f"--remove-handoff {role!r} given more than once")
        removed_handoffs.add(role)
        ops.append(("remove-handoff", role))

    # Cross-flag conflicts.
    conflict = (set_roles | grant_roles) & removed_roles
    if conflict:
        raise _Usage(f"role(s) both set and removed: {', '.join(sorted(conflict))}")
    handoff_set_roles = {r for r, _ in handoff_fields}
    hconflict = handoff_set_roles & removed_handoffs
    if hconflict:
        raise _Usage(f"handoff role(s) both set and removed: {', '.join(sorted(hconflict))}")
    return ops


def _plan_local_ops(args: argparse.Namespace) -> List[Tuple]:
    ops: List[Tuple] = []
    set_names: set = set()
    unset_names: set = set()
    for raw in args.set_work_root:
        name, value = _parse_kv(raw, "--set-work-root")
        if not _NAME_RE.match(name):
            raise _Usage(f"--set-work-root name must match [A-Za-z0-9_-]+; got: {name!r}")
        if name in set_names:
            raise _Usage(f"--set-work-root {name!r} given more than once")
        set_names.add(name)
        val = value[1:-1] if len(value) >= 2 and value.startswith('"') and value.endswith('"') else value
        if not val or not os.path.isabs(val):
            raise _Usage(f"--set-work-root {name}: path must be absolute; got: {val!r}")
        ops.append(("set-work-root", name, _toml_str(val)))
    for name in args.unset_work_root:
        name = name.strip()
        if not _NAME_RE.match(name):
            raise _Usage(f"--unset-work-root name must match [A-Za-z0-9_-]+; got: {name!r}")
        if name in unset_names:
            raise _Usage(f"--unset-work-root {name!r} given more than once")
        unset_names.add(name)
        ops.append(("unset-work-root", name))
    conflict = set_names & unset_names
    if conflict:
        raise _Usage(f"work-root(s) both set and unset: {', '.join(sorted(conflict))}")
    return ops


def _apply_project_ops(model: _Model, ops: List[Tuple]) -> None:
    # Order: role descriptions, then grants (so a set-role + set-role-grants pair
    # composes into a table form preserving the description), then handoffs,
    # scalars, then removals.
    for op in [o for o in ops if o[0] == "set-role"]:
        model.set_role_description(op[1], op[2])
    for op in [o for o in ops if o[0] == "set-role-grants"]:
        model.set_role_grants(op[1], op[2])
    for op in [o for o in ops if o[0] == "set-handoff"]:
        model.set_handoff_field(op[1], op[2], op[3])
    for op in [o for o in ops if o[0] == "set"]:
        model.set_key(op[1], op[2], op[3])
    for op in [o for o in ops if o[0] == "unset"]:
        model.unset_key(op[1], op[2])
    for op in [o for o in ops if o[0] == "remove-role"]:
        model.remove_role(op[1])
    for op in [o for o in ops if o[0] == "remove-handoff"]:
        model.remove_handoff(op[1])


def _apply_local_ops(model: _Model, ops: List[Tuple]) -> None:
    for op in ops:
        if op[0] == "set-work-root":
            model.set_key(("work_roots",), op[1], op[2])
        elif op[0] == "unset-work-root":
            model.unset_key(("work_roots",), op[1])


def _changed_labels(ops: List[Tuple]) -> List[str]:
    out: List[str] = []
    for op in ops:
        kind = op[0]
        if kind in ("set", "unset"):
            out.append(".".join(op[1]) + "." + op[2])
        elif kind in ("set-role", "set-role-grants", "remove-role"):
            out.append(f"roles.{op[1]}")
        elif kind in ("set-handoff",):
            out.append(f"handoffs.{op[1]}.{op[2]}")
        elif kind == "remove-handoff":
            out.append(f"handoffs.{op[1]}")
        elif kind in ("set-work-root", "unset-work-root"):
            out.append(f"work_roots.{op[1]}")
    return out


# ---------------------------------------------------------------------------
# Guarded atomic write (project file must exist; --local may create).
# ---------------------------------------------------------------------------
def _atomic_write_config(
    project_root: Path, config_path: Path, data: bytes, *, allow_create: bool
) -> None:
    canonical_parent = os.path.realpath(str(project_root))
    if not os.path.isdir(canonical_parent):
        raise _Guard("bad-root", f"project root is not a directory: {canonical_parent}")
    final_name = config_path.name
    candidate = os.path.join(canonical_parent, final_name)

    if os.path.islink(candidate):
        raise _Guard("symlink", f"config path is a symlink: {candidate}")
    mode = 0o644
    if os.path.lexists(candidate):
        st = os.lstat(candidate)
        if not stat.S_ISREG(st.st_mode):
            raise _Guard("non-regular", f"config path is not a regular file: {candidate}")
        if st.st_nlink > 1:
            raise _Guard("hardlink", f"config path is a hardlink (st_nlink={st.st_nlink})")
        mode = st.st_mode & 0o777
    elif not allow_create:
        raise _Guard("config-not-found", f"config file does not exist: {candidate}")

    safe_mode = (mode & 0o777) & ~0o111
    snapshot = _snapshot_chain(canonical_parent, canonical_parent)
    tmp_name = make_tmp_name(final_name)
    if DIR_FD_SUPPORTED:
        _atomic_write_via_dir_fd(canonical_parent, snapshot, final_name, tmp_name, data, safe_mode)
    else:
        _atomic_write_via_path(canonical_parent, snapshot, final_name, tmp_name, data, safe_mode)


def _validate_effective(project_root: Path, new_project_cfg: Dict[str, Any]) -> None:
    """Layer 2: validate the resolved effective (project + global) config."""
    global_cfg = _load_toml(Path.home() / ".cartopian" / "cartopian.toml", "global config") or {}
    roles_raw = _resolve_roles(global_cfg, new_project_cfg)
    roles = {name: role_description(value) for name, value in roles_raw.items()}
    handoffs = _resolve_handoffs(global_cfg, new_project_cfg)
    capabilities = resolve_grants(roles_raw)
    # Raises on a blocking violation (e.g. orphan handoff); warnings are advisory.
    try:
        warnings = validate_effective_config(roles, handoffs, capabilities)
    except _CliError as err:
        raise _Guard(err.prefix, err.message)
    for prefix, message in warnings:
        _stderr(prefix, message)


def handler(args: argparse.Namespace) -> int:
    raw_path = args.project_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"project_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE
    project_root = Path(raw_path)
    if not project_root.is_dir():
        _stderr("error", f"project path is not a directory: {raw_path}")
        return EXIT_FAIL

    local = bool(args.local)
    project_ops_present = any([
        getattr(args, "set"), getattr(args, "unset"), args.set_role, args.set_role_grants,
        args.set_handoff, args.remove_role, args.remove_handoff,
    ])
    local_ops_present = bool(args.set_work_root or args.unset_work_root)

    try:
        if local and project_ops_present:
            raise _Usage("--local accepts only --set-work-root / --unset-work-root")
        if not local and local_ops_present:
            raise _Usage("--set-work-root / --unset-work-root require --local")
        if local:
            ops = _plan_local_ops(args)
        else:
            ops = _plan_project_ops(args)
        if not ops:
            raise _Usage("no operations given — supply at least one --set/--unset/--set-role/…")
    except _Usage as exc:
        _stderr("usage", str(exc))
        return EXIT_USAGE

    config_path = project_root / ("cartopian.local.toml" if local else "cartopian.toml")

    try:
        if config_path.exists():
            original = config_path.read_text(encoding="utf-8")
        elif local:
            if not args.set_work_root:
                raise _Guard(
                    "config-not-found",
                    f"{config_path} does not exist and no --set-work-root mapping was supplied "
                    "(the local file is created only to hold a mapping)",
                )
            original = ""
        else:
            raise _Guard(
                "config-not-found",
                f"{config_path} does not exist — create it with `generate-config` / the `init project` skill",
            )

        model = _Model.parse(original)
        if local:
            _apply_local_ops(model, ops)
        else:
            _apply_project_ops(model, ops)
        new_text = model.render()

        # Layer 1: the edited file must parse.
        try:
            new_cfg = tomllib.loads(new_text)
        except tomllib.TOMLDecodeError as exc:
            raise _Guard("invalid-result", f"edit produced invalid TOML: {exc}")

        # Layer 2: effective-config validation (project file only).
        if not local:
            _validate_effective(project_root, new_cfg)

        if new_text == original:
            emit_record({
                "action": "update-config",
                "details": {
                    "target": "local" if local else "project",
                    "config_path": str(config_path),
                    "changed": [],
                },
            })
            return EXIT_OK

        allow_create = local
        _atomic_write_config(
            project_root, config_path, new_text.encode("utf-8"), allow_create=allow_create
        )
    except _Guard as g:
        _stderr("guard", f"{g.rule}: {g.detail}")
        return EXIT_FAIL
    except GuardRefusal as refusal:
        _stderr("guard", f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL

    emit_record({
        "action": "update-config",
        "details": {
            "target": "local" if local else "project",
            "config_path": str(config_path),
            "changed": _changed_labels(ops),
        },
    })
    return EXIT_OK
