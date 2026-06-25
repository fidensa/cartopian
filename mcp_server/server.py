"""Cartopian MCP server — JSON-RPC over stdio, MCP protocol 2024-11-05.

The server is the cross-agent entry point for Cartopian. Once an agent
(Claude Code, Claude Desktop, Codex, Gemini CLI, anything that speaks
MCP) is configured to launch ``cartopian-mcp``, the operator can say
"use cartopian" from any directory and the agent gets:

- Prompts — one per skill in ``skills/``, plus a ``use_cartopian``
  entry-point prompt loaded from ``skills/use-cartopian.md``. It issues
  an imperative startup contract: read the protocol, load the startup
  runbook, select a project via the registry. It never inspects the
  current working directory.
- Tools — one per CLI subcommand in ``cli.main.SUBCOMMANDS``. Each tool
  invokes its handler in-process with stdout/stderr captured, parses
  NDJSON output back into structured records, and surfaces stderr
  prefixes verbatim so the error contract is preserved.
- Resources — ``cartopian://skills/<name>``, ``cartopian://protocol/<name>``
  (plus narrower ``cartopian://protocol/<name>/<section-slug>`` per-H2-section
  reads and the curated ``cartopian://protocol/CONVENTIONS/startup`` slice),
  ``cartopian://templates/<name>``, and ``cartopian://project/<id>/<file>``
  for registered projects.

Transport is JSON-RPC over stdin/stdout with dual framing: the server
reads (and replies in) either ``Content-Length``-header framing
(``Content-Length: <n>\r\n\r\n<payload>``, the standard MCP SDK framing)
or raw newline-delimited JSON-RPC, matching the framing each request
arrives in. Stdio is handled byte-exact via the binary buffer so no
``\n``↔``\r\n`` text-mode translation can corrupt a framed payload.

The server depends on the rest of the install tree (``skills/``,
``protocol/``, ``templates/``, ``cli/``, ``projects.json``) but pulls
in no third-party Python packages — same posture as ``cli/``.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "cartopian"

# JSON-RPC error codes (per spec + MCP extensions).
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

URI_SCHEME = "cartopian"

# Hard caps applied before any read_text() of operator/agent-facing files.
# Skills, protocol docs, templates, and project artifacts are all hand-authored
# markdown; 1 MiB is several orders of magnitude above any realistic file.
MAX_RESOURCE_BYTES = 1 * 1024 * 1024
# `_first_line_summary` only needs the first non-empty line for prompt/resource
# listings; reading a small head avoids loading large files just to format a
# directory listing.
SUMMARY_HEAD_BYTES = 4096

# Resource kinds we publish under `cartopian://project/<id>/<kind>`. Restricting
# read access to this allowlist mirrors what `_project_paths` lists and blocks
# user-controlled `kind` from constructing arbitrary paths under a project root.
PROJECT_KINDS = ("STATE", "REQUIREMENTS", "IMPLEMENTATION_PLAN")

# Tools a CONTAINED PM must NOT reach. These create or mutate project *config* /
# the project *registry*: generate_config and scaffold_project write
# `cartopian.toml` to an arbitrary `project_path` — a filesystem-write escape
# past the capability floor (the MCP server runs OUT of the per-tool native
# sandbox, so the depth profile's deny_write_roots cannot stop it), and
# register/unregister_project mutate the global registry. A contained PM operates an
# already-selected project through read + lifecycle tools only; project/config
# genesis is an uncontained setup operation. This is the SHARED floor: every *-pm
# wrapper launches this server with CARTOPIAN_PM_CONTAINED=1
# (wrappers/etc/mcp-cartopian-only.json), so withholding here covers gemini, codex,
# AND Claude Code uniformly — no harness-specific allowlist needed. Absent the env
# (operator shell / uncontained PM) the full toolset is exposed, so legacy behavior
# is unchanged (NF-004).
CONTAINED_DENIED_TOOLS = frozenset({
    "generate_config",
    "scaffold_project",
    "register_project",
    "unregister_project",
})


def _pm_is_contained() -> bool:
    """True iff this server runs under the containment launch profile.

    Reuses ``cli.commands._containment.pm_is_contained`` (the single source of
    truth for the ``CARTOPIAN_PM_CONTAINED`` signal) when importable, and falls
    back to reading the same env var directly so the gate is never silently
    dropped if that import surface changes. Read per-request (not cached) so the
    gate honors the process environment the launch wrapper set.
    """
    try:
        root_str = str(ROOT)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        from cli.commands._containment import pm_is_contained  # noqa: WPS433
        return pm_is_contained()
    except Exception:  # pragma: no cover — defensive; never let import shape drop the gate
        return os.environ.get("CARTOPIAN_PM_CONTAINED", "").strip().lower() in {
            "1", "true", "yes", "on",
        }


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def _log_internal(context: str) -> None:
    """Write a traceback to the real stderr for operator debugging.

    Used in defensive ``except Exception`` paths so the model-visible response
    can stay generic while operators still have a diagnosable trail in the MCP
    client's stderr capture (or wherever the host pipes it).
    """
    try:
        sys.__stderr__.write(f"[cartopian-mcp] {context}\n{traceback.format_exc()}")
    except Exception:  # pragma: no cover — never let logging mask the original error
        pass


def _safe_segment(name: str) -> bool:
    """True iff ``name`` is a single, traversal-free path component.

    Rejects empty strings, ``.`` / ``..``, any path separator, NUL, and
    anything longer than a typical filesystem name limit.
    """
    if not name or len(name) > 255:
        return False
    if name in (".", ".."):
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    return True


def _bounded_path(candidate: Path, root: Path) -> Optional[Path]:
    """Resolve ``candidate`` and return it iff it lives under ``root``.

    Uses ``Path.resolve(strict=True)`` so symlinks (and any traversal that
    survived ``_safe_segment``) cannot escape the intended root. Returns
    ``None`` for any failure — caller decides how to surface it.
    """
    try:
        resolved = candidate.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return None
    return resolved


# ---------------------------------------------------------------------------
# Install-root resolution
# ---------------------------------------------------------------------------

def _resolve_install_root() -> Path:
    """Find the Cartopian install root.

    The mcp_server package lives at ``<root>/mcp_server/``. Walk up from
    ``__file__`` until we find a directory containing both ``skills/``
    and ``protocol/``. Falls back to ``~/.cartopian`` if not found.
    """
    here = Path(__file__).resolve()
    for ancestor in [here.parent.parent, *here.parents]:
        if (ancestor / "skills").is_dir() and (ancestor / "protocol").is_dir():
            return ancestor
    return Path.home() / ".cartopian"


ROOT = _resolve_install_root()


def _read_installed_version(root: Path) -> Optional[str]:
    """Return the installed Cartopian ref from ``<root>/VERSION`` if present."""
    version_path = root / "VERSION"
    try:
        value = version_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _read_git_version(root: Path) -> Optional[str]:
    """Return ``git describe`` output for developer checkouts when available."""
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value or None


def _server_version() -> str:
    """Return the Cartopian release/ref this MCP server was loaded from."""
    return _read_installed_version(ROOT) or _read_git_version(ROOT) or "unknown"


# ---------------------------------------------------------------------------
# Skill enumeration & prompt construction
# ---------------------------------------------------------------------------

SKILL_DIR = ROOT / "skills"
INSTALL_SKILL = ROOT / "install-cartopian.md"


def _skill_paths() -> List[Path]:
    paths: List[Path] = []
    if SKILL_DIR.is_dir():
        paths.extend(sorted(
            p for p in SKILL_DIR.glob("*.md")
            if p.name.lower() != "readme.md"
        ))
    if INSTALL_SKILL.exists():
        paths.append(INSTALL_SKILL)
    return paths


def _skill_name(path: Path) -> str:
    """``skills/init-project.md`` → ``init_project``."""
    return path.stem.replace("-", "_")


def _first_line_summary(path: Path, limit: int = 160) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(SUMMARY_HEAD_BYTES)
    except OSError:
        return path.name
    for line in head.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            cleaned = stripped.lstrip("#").strip()
            return cleaned[:limit] if cleaned else path.name
        return stripped[:limit]
    return path.name


def list_prompts() -> List[Dict[str, Any]]:
    prompts: List[Dict[str, Any]] = [{
        "name": "use_cartopian",
        "description": "Enter Cartopian PM mode — the startup entry point.",
    }]
    for path in _skill_paths():
        if _skill_name(path) == "use_cartopian":
            continue  # already listed as entry point above
        prompts.append({
            "name": _skill_name(path),
            "description": _first_line_summary(path),
        })
    return prompts


def _install_context_block() -> str:
    """Authoritative install metadata to prepend to the use_cartopian prompt.

    Without this, the agent has no way to know where Cartopian is installed
    or which version is running, and cannot answer "where does cartopian
    live?" or run upgrade flows without scanning the filesystem.
    """
    version = _server_version()
    return (
        "**Cartopian install context** (authoritative — do not re-derive by "
        "scanning the filesystem):\n"
        f"- Install root: `{ROOT}`\n"
        f"- Installed version: `{version}`\n"
        f"- Upgrade skill: `cartopian://skills/check_for_updates`\n\n"
        "Use this whenever the operator asks about upgrading, updating, or "
        "where Cartopian is installed.\n\n---\n\n"
    )


def _server_instructions() -> str:
    """Server-level guidance returned in the ``initialize`` response's
    ``instructions`` field — the MCP mechanism a server uses to add context to
    the model (clients MAY add it to the system prompt).

    Without this, the install root + version are only visible if the agent first
    invokes the ``use_cartopian`` prompt; many clients never do, so the version
    appeared "missing" at session start. Surfacing it here makes it available at
    connect time, no prompt invocation required.
    """
    version = _server_version()
    return (
        "Cartopian — a filesystem-first project-governance protocol, served over "
        "MCP.\n\n"
        "**Cartopian install context** (authoritative — do not re-derive by "
        "scanning the filesystem):\n"
        f"- Install root: `{ROOT}`\n"
        f"- Installed version: `{version}`\n"
        "- Upgrade skill (MCP prompt/resource): `check_for_updates` / "
        "`cartopian://skills/check_for_updates`\n\n"
        "To act as a Cartopian project manager (e.g. when the operator says "
        "\"use cartopian\"), invoke the `use_cartopian` MCP prompt. Cartopian "
        "skills are MCP prompts/resources, not native client skills."
    )


def _use_cartopian_messages() -> List[Dict[str, Any]]:
    skill_path = SKILL_DIR / "use-cartopian.md"
    context = _install_context_block()
    if skill_path.exists():
        messages = _skill_messages(skill_path)
        # Prepend install context to the skill body so the agent sees it
        # before executing any step.
        first = messages[0]
        first["content"]["text"] = context + first["content"]["text"]
        return messages
    # Hard fallback if the skill file is missing
    text = (
        context
        + "You are in **Cartopian PM mode**. Execute in order:\n"
        "1. Read `cartopian://protocol/CONVENTIONS`.\n"
        "2. Read `cartopian://skills/start_session`.\n"
        "3. Do not inspect workspace or project files yet.\n"
        "4. Call `discover_projects` and run Stage 0 of `start_session`."
    )
    return [{"role": "user", "content": {"type": "text", "text": text}}]


def _skill_messages(path: Path) -> List[Dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError:
        raise McpError(ERR_INTERNAL, f"cannot read skill: {path.name}")
    if size > MAX_RESOURCE_BYTES:
        raise McpError(ERR_INTERNAL, f"skill exceeds size limit: {path.name}")
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        raise McpError(ERR_INTERNAL, f"cannot read skill: {path.name}")
    header = (
        f"Follow this Cartopian skill: **`{path.name}`**. The skill is the "
        f"authoritative runbook for this workflow — read every step before "
        f"acting. When the skill calls for `cartopian <subcommand>`, use the "
        f"corresponding MCP tool (`<subcommand>` with hyphens replaced by "
        f"underscores) rather than shelling out.\n\n---\n\n"
    )
    return [{"role": "user", "content": {"type": "text", "text": header + body}}]


def get_prompt(name: str, _args: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if name == "use_cartopian":
        return {
            "description": "Cartopian PM mode entry point.",
            "messages": _use_cartopian_messages(),
        }
    for path in _skill_paths():
        if _skill_name(path) == name:
            return {
                "description": _first_line_summary(path),
                "messages": _skill_messages(path),
            }
    raise McpError(ERR_INVALID_PARAMS, f"unknown prompt: {name}")


# ---------------------------------------------------------------------------
# Tool surface — derived from cli.main.build_parser()
# ---------------------------------------------------------------------------

def _import_cli_parser() -> argparse.ArgumentParser:
    """Build the CLI parser. ROOT must be on sys.path so `cli` imports."""
    root_str = str(ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    from cli.main import build_parser  # noqa: WPS433 — lazy import is correct here
    return build_parser()


def _subparsers_map(parser: argparse.ArgumentParser) -> Dict[str, argparse.ArgumentParser]:
    """Extract {name: sub-parser} from the top-level cartopian parser."""
    for action in parser._actions:  # noqa: SLF001 — argparse exposes no public API
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return dict(action.choices)
    return {}


def _action_is_positional(action: argparse.Action) -> bool:
    return not action.option_strings


def _action_json_type(action: argparse.Action) -> str:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):  # noqa: SLF001
        return "boolean"
    py_type = action.type or str
    if py_type is int:
        return "integer"
    if py_type is float:
        return "number"
    if py_type is bool:
        return "boolean"
    return "string"


def _action_to_schema(action: argparse.Action) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": _action_json_type(action)}
    if action.help:
        schema["description"] = action.help
    if action.choices:
        schema["enum"] = list(action.choices)
    if action.default is not None and action.default is not argparse.SUPPRESS:
        if not isinstance(action.default, (str, int, float, bool, list, dict)):
            pass  # skip non-JSON-friendly defaults
        else:
            schema["default"] = action.default
    return schema


def _command_input_schema(sub: argparse.ArgumentParser) -> Tuple[Dict[str, Any], List[argparse.Action]]:
    """Return (JSON schema, ordered list of actions for argv rebuild)."""
    properties: Dict[str, Any] = {}
    required: List[str] = []
    ordered: List[argparse.Action] = []
    for action in sub._actions:  # noqa: SLF001
        if isinstance(action, argparse._HelpAction):  # noqa: SLF001
            continue
        if action.dest == argparse.SUPPRESS or action.dest is None:
            continue
        ordered.append(action)
        properties[action.dest] = _action_to_schema(action)
        if _action_is_positional(action):
            # Positional with nargs="?" or "*" is optional; otherwise required.
            if action.nargs in (None, 1) or (isinstance(action.nargs, int) and action.nargs >= 1):
                required.append(action.dest)
        elif action.required:
            required.append(action.dest)
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema, ordered


_TOOL_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _tool_registry() -> Dict[str, Dict[str, Any]]:
    """Build (and cache) the tool registry.

    {tool_name: {"subcommand": "cli-name", "schema": {...},
                 "actions": [argparse.Action, ...], "description": "..."}}
    """
    global _TOOL_CACHE
    if _TOOL_CACHE is not None:
        return _TOOL_CACHE

    parser = _import_cli_parser()
    subs = _subparsers_map(parser)
    registry: Dict[str, Dict[str, Any]] = {}
    for cli_name, sub in subs.items():
        tool_name = cli_name.replace("-", "_")
        schema, actions = _command_input_schema(sub)
        registry[tool_name] = {
            "subcommand": cli_name,
            "schema": schema,
            "actions": actions,
            "description": (sub.description or sub.prog or cli_name).strip(),
        }
    _TOOL_CACHE = registry
    return registry


def list_tools() -> List[Dict[str, Any]]:
    contained = _pm_is_contained()
    items: List[Dict[str, Any]] = []
    for tool_name, entry in sorted(_tool_registry().items()):
        if contained and tool_name in CONTAINED_DENIED_TOOLS:
            continue  # config/registry-genesis tools are withheld from a contained PM
        items.append({
            "name": tool_name,
            "description": f"Run `cartopian {entry['subcommand']}`.",
            "inputSchema": entry["schema"],
        })
    return items


def _kwargs_to_argv(actions: List[argparse.Action], kwargs: Dict[str, Any]) -> List[str]:
    """Rebuild the argv argparse expects from a dict of kwargs."""
    positional_parts: List[str] = []
    optional_parts: List[str] = []
    for action in actions:
        if action.dest not in kwargs:
            if _action_is_positional(action) and action.nargs not in ("?", "*"):
                raise McpError(
                    ERR_INVALID_PARAMS,
                    f"missing required argument: {action.dest}",
                )
            continue
        value = kwargs[action.dest]
        if _action_is_positional(action):
            if action.nargs in ("*", "+") and isinstance(value, list):
                positional_parts.extend(str(v) for v in value)
            else:
                positional_parts.append(str(value))
            continue
        # Optional argument — pick the longest option string for clarity.
        flag = sorted(action.option_strings, key=len, reverse=True)[0]
        if isinstance(action, argparse._StoreTrueAction):  # noqa: SLF001
            if value:
                optional_parts.append(flag)
        elif isinstance(action, argparse._StoreFalseAction):  # noqa: SLF001
            if not value:
                optional_parts.append(flag)
        else:
            optional_parts.extend([flag, str(value)])
    # Optionals first, then positionals (works for argparse either way; this
    # keeps trailing positionals unambiguous against `--` if it appears).
    return optional_parts + positional_parts


def _invoke_cli(subcommand: str, argv: List[str]) -> Dict[str, Any]:
    """Run the CLI subcommand in-process and capture stdout/stderr.

    Returns ``{exit_code, records, stderr_lines, stdout_raw}``. ``records``
    is the list of NDJSON dicts parsed from stdout; ``stderr_lines`` is the
    list of stderr lines (each typically prefixed with ``[usage]`` /
    ``[error]`` / ``[guard]``).
    """
    parser = _import_cli_parser()
    full_argv = [subcommand, *argv]

    out_buf = io.StringIO()
    err_buf = io.StringIO()

    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                args = parser.parse_args(full_argv)
            except SystemExit as exc:
                # argparse exits via SystemExit(2) on usage error; we still
                # want to surface stderr.
                code = exc.code if isinstance(exc.code, int) else 2
                return _build_invoke_result(code, out_buf, err_buf)
            handler = getattr(args, "_handler", None)
            if handler is None:
                err_buf.write(f"[usage] unknown subcommand: {subcommand}\n")
                return _build_invoke_result(2, out_buf, err_buf)
            try:
                code = handler(args)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    except Exception:  # pragma: no cover — defensive
        # Internal details (paths, stack frames) go to the real stderr for
        # operator debugging — never into the captured stderr that gets
        # surfaced back to the model.
        _log_internal(f"_invoke_cli unexpected exception in {subcommand}:")
        err_buf.write("[error] mcp_server caught unexpected exception\n")
        return _build_invoke_result(1, out_buf, err_buf)

    return _build_invoke_result(code, out_buf, err_buf)


def _build_invoke_result(code: int, out_buf: io.StringIO, err_buf: io.StringIO) -> Dict[str, Any]:
    raw_out = out_buf.getvalue()
    records: List[Any] = []
    for line in raw_out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            # Non-NDJSON output is allowed (e.g., generate-config prints TOML);
            # callers can still see it in stdout_raw.
            pass
    stderr_lines = [line for line in err_buf.getvalue().splitlines() if line]
    return {
        "exit_code": code,
        "records": records,
        "stderr_lines": stderr_lines,
        "stdout_raw": raw_out,
    }


def call_tool(name: str, arguments: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    registry = _tool_registry()
    if name not in registry:
        raise McpError(ERR_INVALID_PARAMS, f"unknown tool: {name}")
    # Defense in depth: even if a contained PM names a withheld genesis tool
    # directly (bypassing the filtered tools/list), refuse it fail-closed.
    if name in CONTAINED_DENIED_TOOLS and _pm_is_contained():
        raise McpError(
            ERR_INVALID_PARAMS,
            f"tool '{name}' is withheld under PM containment "
            f"(CARTOPIAN_PM_CONTAINED): config/registry-genesis tools are not "
            f"available to a contained PM.",
        )
    entry = registry[name]
    argv = _kwargs_to_argv(entry["actions"], arguments or {})
    result = _invoke_cli(entry["subcommand"], argv)

    # MCP tool result: structuredContent + text fallback.
    text_lines: List[str] = []
    if result["records"]:
        for record in result["records"]:
            text_lines.append(json.dumps(record, ensure_ascii=False))
    elif result["stdout_raw"]:
        text_lines.append(result["stdout_raw"].rstrip("\n"))
    else:
        text_lines.append(f"(exit {result['exit_code']}, no stdout)")
    if result["stderr_lines"]:
        text_lines.append("--- stderr ---")
        text_lines.extend(result["stderr_lines"])

    is_error = result["exit_code"] != 0
    return {
        "content": [{"type": "text", "text": "\n".join(text_lines)}],
        "isError": is_error,
        "structuredContent": {
            "exit_code": result["exit_code"],
            "records": result["records"],
            "stderr_lines": result["stderr_lines"],
        },
    }


# ---------------------------------------------------------------------------
# Resource surface
# ---------------------------------------------------------------------------

# --- Section-scoped protocol resources (additive; whole-file URIs unchanged) -
#
# `cartopian://protocol/<doc>/<section-slug>` reads one H2 section of an
# allowlisted protocol doc, and `cartopian://protocol/CONVENTIONS/startup`
# reads a curated startup slice, so the PM can load only the slice of
# CONVENTIONS.md a given moment needs instead of the whole file. The full
# `cartopian://protocol/<doc>` resources remain available and authoritative.

# H2 heading line in a protocol markdown doc (H3+ stays inside its parent H2).
_H2_RE = re.compile(r"^## (.+?)\s*$")

# Reserved slug for the curated startup slice of CONVENTIONS.md. No H2 in the
# doc slugifies to a bare "startup", so the reservation cannot shadow a section.
STARTUP_SLUG = "startup"

# H2 headings concatenated (in document order) into
# `cartopian://protocol/CONVENTIONS/startup` — the sections a PM needs through
# session startup: project selection, role resolution, state read, and the
# next-action proposal. Fail-closed: if any heading disappears from
# CONVENTIONS.md the startup read errors instead of silently dropping a
# guardrail.
STARTUP_SECTIONS = (
    "Core Principle",
    "Protocol And Skills",
    "Project Scope",
    "Session Startup And Project Selection",
    "Status Through Directory",
    "Lifecycle Authority",
    "Roles",
    "Session State",
)

STARTUP_PREAMBLE = (
    "# Cartopian Protocol Conventions — startup slice\n\n"
    "Startup-scoped excerpt of `protocol/CONVENTIONS.md`: the sections a PM "
    "needs through session startup (project selection, role resolution, state "
    "read, next-action proposal). The full `cartopian://protocol/CONVENTIONS` "
    "remains the authoritative contract. When a later lifecycle action needs "
    "rules beyond this slice (task movement guards, handoffs, reviews, plan "
    "lifecycle, git), read the relevant section via "
    "`cartopian://protocol/CONVENTIONS/<section-slug>` (H2 title lowercased, "
    "spaces as hyphens — e.g. `lifecycle-cli-guards`) or the full document.\n"
)


def _section_slug(heading: str) -> str:
    """``Session Startup And Project Selection`` → ``session-startup-and-project-selection``."""
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def _split_h2_sections(text: str) -> Dict[str, Tuple[str, str]]:
    """Split a markdown doc into H2 sections: {slug: (heading, body)}.

    Each body runs from its ``## Heading`` line up to (excluding) the next H2,
    so H3 subsections stay inside their parent section. Insertion order follows
    document order (dicts preserve it).
    """
    sections: Dict[str, Tuple[str, str]] = {}
    current_slug: Optional[str] = None
    current_heading = ""
    current_lines: List[str] = []

    def flush() -> None:
        if current_slug is not None:
            sections[current_slug] = (
                current_heading,
                "\n".join(current_lines).rstrip() + "\n",
            )

    for line in text.splitlines():
        match = _H2_RE.match(line)
        if match:
            flush()
            current_heading = match.group(1)
            current_slug = _section_slug(current_heading)
            current_lines = [line]
        elif current_slug is not None:
            current_lines.append(line)
    flush()
    return sections


def _startup_slice_text(sections: Dict[str, Tuple[str, str]], uri: str) -> str:
    """Assemble the curated startup slice; fail closed on heading drift."""
    parts = [STARTUP_PREAMBLE]
    for heading in STARTUP_SECTIONS:
        entry = sections.get(_section_slug(heading))
        if entry is None:
            # A curated heading vanished from CONVENTIONS.md — surface loudly
            # rather than serving a slice that silently lost a guardrail.
            raise McpError(ERR_INTERNAL, f"startup section missing from protocol doc: {uri}")
        parts.append(entry[1])
    return "\n".join(parts)


def _read_protocol_section(doc: str, slug: str, uri: str) -> Dict[str, Any]:
    """Bounded read of one H2 section (or the startup slice) of a protocol doc.

    Same allowlist shape and size discipline as the whole-file branch: the only
    path ever constructed is ``protocol/<doc>.md`` under the protocol root, and
    the file is size-capped before being loaded. Malformed names, unknown docs,
    and unknown sections are all invalid-params (fail-closed).
    """
    if not _safe_segment(doc) or not _safe_segment(slug):
        raise McpError(ERR_INVALID_PARAMS, f"invalid protocol section uri: {uri}")
    candidate = ROOT / "protocol" / f"{doc}.md"
    resolved = _bounded_path(candidate, ROOT / "protocol")
    if resolved is None:
        raise McpError(ERR_INVALID_PARAMS, f"resource not found: {uri}")
    try:
        size = resolved.stat().st_size
    except OSError:
        raise McpError(ERR_INTERNAL, f"cannot read resource: {uri}")
    if size > MAX_RESOURCE_BYTES:
        raise McpError(ERR_INTERNAL, f"resource exceeds size limit: {uri}")
    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError:
        raise McpError(ERR_INTERNAL, f"cannot read resource: {uri}")
    sections = _split_h2_sections(text)
    if doc == "CONVENTIONS" and slug == STARTUP_SLUG:
        body = _startup_slice_text(sections, uri)
    else:
        entry = sections.get(slug)
        if entry is None:
            raise McpError(ERR_INVALID_PARAMS, f"unknown protocol section: {uri}")
        body = entry[1]
    return {
        "contents": [{
            "uri": uri,
            "mimeType": "text/markdown",
            "text": body,
        }]
    }


def _registry_entries() -> List[Dict[str, Any]]:
    try:
        sys.path.insert(0, str(ROOT))
        from cli.commands._registry import read_registry, registry_path
        return read_registry(registry_path())
    except Exception:
        return []


def _project_paths(entry: Dict[str, Any]) -> List[Tuple[str, Path]]:
    base = Path(entry["path"])
    items: List[Tuple[str, Path]] = []
    for name in ("STATE.md", "REQUIREMENTS.md", "IMPLEMENTATION_PLAN.md"):
        p = base / name
        if p.exists():
            items.append((name.replace(".md", ""), p))
    return items


def list_resources() -> List[Dict[str, Any]]:
    resources: List[Dict[str, Any]] = []

    # Skills — use the same underscore identifier as the matching prompt
    # so agents have one identifier shape across surfaces.
    for path in _skill_paths():
        resources.append({
            "uri": f"{URI_SCHEME}://skills/{_skill_name(path)}",
            "name": f"skill: {_skill_name(path)}",
            "description": _first_line_summary(path),
            "mimeType": "text/markdown",
        })

    # Protocol — whole-file resource plus the additive narrower surface:
    # the curated startup slice (CONVENTIONS only) and one resource per H2
    # section, so agents can read only the slice they need.
    protocol_dir = ROOT / "protocol"
    if protocol_dir.is_dir():
        for path in sorted(protocol_dir.glob("*.md")):
            resources.append({
                "uri": f"{URI_SCHEME}://protocol/{path.stem}",
                "name": f"protocol: {path.stem}",
                "description": _first_line_summary(path),
                "mimeType": "text/markdown",
            })
            try:
                if path.stat().st_size > MAX_RESOURCE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue  # whole-file entry stays listed; sections degrade
            if path.stem == "CONVENTIONS":
                resources.append({
                    "uri": f"{URI_SCHEME}://protocol/{path.stem}/{STARTUP_SLUG}",
                    "name": f"protocol: {path.stem} § startup slice",
                    "description": (
                        "Startup-scoped slice of CONVENTIONS.md — the smallest "
                        "sufficient protocol read for session startup."
                    ),
                    "mimeType": "text/markdown",
                })
            for slug, (heading, _body) in _split_h2_sections(text).items():
                resources.append({
                    "uri": f"{URI_SCHEME}://protocol/{path.stem}/{slug}",
                    "name": f"protocol: {path.stem} § {heading}",
                    "description": f"Single section `## {heading}` of {path.stem}.md.",
                    "mimeType": "text/markdown",
                })

    # Templates
    template_dir = ROOT / "templates"
    if template_dir.is_dir():
        for path in sorted(template_dir.iterdir()):
            if path.is_file() and path.suffix in (".md", ".toml"):
                resources.append({
                    "uri": f"{URI_SCHEME}://templates/{path.name}",
                    "name": f"template: {path.name}",
                    "description": _first_line_summary(path),
                    "mimeType": "text/markdown" if path.suffix == ".md" else "text/plain",
                })

    # Per-project lifecycle artifacts
    for entry in _registry_entries():
        for kind, path in _project_paths(entry):
            resources.append({
                "uri": f"{URI_SCHEME}://project/{entry['id']}/{kind}",
                "name": f"{entry['id']}: {kind}",
                "description": f"{kind}.md for project {entry['id']}",
                "mimeType": "text/markdown",
            })
    return resources


def read_resource(uri: str) -> Dict[str, Any]:
    if not uri.startswith(f"{URI_SCHEME}://"):
        raise McpError(ERR_INVALID_PARAMS, f"unsupported uri scheme: {uri}")
    rest = uri[len(URI_SCHEME) + 3:]  # strip "cartopian://"
    parts = rest.split("/", 2)
    if not parts or len(parts) < 2:
        raise McpError(ERR_INVALID_PARAMS, f"malformed cartopian uri: {uri}")
    namespace = parts[0]
    tail = parts[1:]

    resolved_path: Optional[Path] = None
    if namespace == "skills":
        # `_skill_paths()` returns a fixed enumeration of *.md files under
        # SKILL_DIR (and the install skill); matching by `_skill_name(path)`
        # against tail[0] cannot escape that allowlist.
        for path in _skill_paths():
            if _skill_name(path) == tail[0]:
                resolved_path = path
                break
    elif namespace == "protocol":
        if len(tail) == 2:
            # Additive section-scoped surface: `protocol/<doc>/<section-slug>`
            # plus the curated `CONVENTIONS/startup` slice. Whole-file reads
            # below are unchanged.
            return _read_protocol_section(tail[0], tail[1], uri)
        if not _safe_segment(tail[0]):
            raise McpError(ERR_INVALID_PARAMS, f"invalid protocol name: {uri}")
        candidate = ROOT / "protocol" / f"{tail[0]}.md"
        resolved_path = _bounded_path(candidate, ROOT / "protocol")
    elif namespace == "templates":
        if not _safe_segment(tail[0]):
            raise McpError(ERR_INVALID_PARAMS, f"invalid template name: {uri}")
        candidate = ROOT / "templates" / tail[0]
        resolved_path = _bounded_path(candidate, ROOT / "templates")
    elif namespace == "project":
        if len(tail) != 2:
            raise McpError(ERR_INVALID_PARAMS, f"project uri requires <id>/<file>: {uri}")
        project_id, kind = tail
        if not _safe_segment(project_id) or not _safe_segment(kind):
            raise McpError(ERR_INVALID_PARAMS, f"invalid project uri: {uri}")
        if kind not in PROJECT_KINDS:
            raise McpError(ERR_INVALID_PARAMS, f"unknown project kind: {kind}")
        for entry in _registry_entries():
            if entry.get("id") != project_id:
                continue
            base = Path(entry["path"])
            candidate = base / f"{kind}.md"
            resolved_path = _bounded_path(candidate, base)
            break
    else:
        raise McpError(ERR_INVALID_PARAMS, f"unknown cartopian namespace: {namespace}")

    if resolved_path is None:
        raise McpError(ERR_INVALID_PARAMS, f"resource not found: {uri}")

    try:
        size = resolved_path.stat().st_size
    except OSError:
        raise McpError(ERR_INTERNAL, f"cannot read resource: {uri}")
    if size > MAX_RESOURCE_BYTES:
        raise McpError(ERR_INTERNAL, f"resource exceeds size limit: {uri}")
    try:
        text = resolved_path.read_text(encoding="utf-8")
    except OSError:
        raise McpError(ERR_INTERNAL, f"cannot read resource: {uri}")
    # The use_cartopian entry-point skill must carry the install-context block on
    # EVERY delivery path. The prompt path (`_use_cartopian_messages`) prepends
    # it; a client that enters PM mode by READING the resource
    # (`cartopian://skills/use_cartopian`) rather than invoking the prompt would
    # otherwise get the runbook without the block its Step 0 depends on — so the
    # version appears "missing" and the update check is skipped.
    if namespace == "skills" and _skill_name(resolved_path) == "use_cartopian":
        text = _install_context_block() + text
    return {
        "contents": [{
            "uri": uri,
            "mimeType": "text/markdown",
            "text": text,
        }]
    }


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------

def _server_info() -> Dict[str, Any]:
    return {"name": SERVER_NAME, "version": _server_version()}


def _capabilities() -> Dict[str, Any]:
    return {
        "prompts": {"listChanged": False},
        "tools": {"listChanged": False},
        "resources": {"subscribe": False, "listChanged": False},
    }


def handle_request(method: str, params: Dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": _server_info(),
            "capabilities": _capabilities(),
            "instructions": _server_instructions(),
        }
    if method == "ping":
        return {}
    if method == "prompts/list":
        return {"prompts": list_prompts()}
    if method == "prompts/get":
        name = params.get("name")
        if not isinstance(name, str):
            raise McpError(ERR_INVALID_PARAMS, "params.name (string) required")
        return get_prompt(name, params.get("arguments"))
    if method == "tools/list":
        return {"tools": list_tools()}
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            raise McpError(ERR_INVALID_PARAMS, "params.name (string) required")
        return call_tool(name, params.get("arguments"))
    if method == "resources/list":
        return {"resources": list_resources()}
    if method == "resources/read":
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise McpError(ERR_INVALID_PARAMS, "params.uri (string) required")
        return read_resource(uri)
    raise McpError(ERR_METHOD_NOT_FOUND, f"method not found: {method}")


def _is_notification(method: str) -> bool:
    return method.startswith("notifications/")


def handle_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch one JSON-RPC message. Returns the response dict or None
    for notifications."""
    rpc_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if not isinstance(method, str):
        return _error_response(rpc_id, ERR_INVALID_REQUEST, "missing method")

    if _is_notification(method):
        # No response for notifications. We ignore unknown notifications
        # silently per JSON-RPC convention.
        return None

    try:
        result = handle_request(method, params if isinstance(params, dict) else {})
    except McpError as exc:
        return _error_response(rpc_id, exc.code, exc.message, exc.data)
    except Exception:  # pragma: no cover — defensive
        # Surface a generic message to the caller; the traceback (which can
        # carry filesystem paths and internal state) goes to the real stderr
        # only.
        _log_internal(f"unhandled exception in {method}:")
        return _error_response(rpc_id, ERR_INTERNAL, "internal server error")
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error_response(rpc_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


# ---------------------------------------------------------------------------
# Stdio loop — dual framing (Content-Length headers + newline-delimited)
# ---------------------------------------------------------------------------
#
# stdin/stdout are byte streams. To stay byte-exact for ``Content-Length``
# framing (and to dodge ``\n``↔``\r\n`` text-mode translation on Windows) the
# loop works on the binary buffer: it reads header/framing lines with
# ``readline()`` and framed payloads with an exact byte count. Each request's
# framing is remembered and the response is emitted in the same framing.

# Matches an RFC-822-style header field name followed by a colon, e.g.
# ``Content-Length:``. A JSON-RPC message always starts with ``{`` (or ``[``),
# never a bare ``token:``, so this cleanly distinguishes the two framings.
_HEADER_LINE = re.compile(rb"^[A-Za-z][A-Za-z0-9-]*:")

FRAMING_HEADER = "header"
FRAMING_NEWLINE = "newline"


class _TextReaderAdapter:
    """Expose a text stream (e.g. ``io.StringIO``) as a byte reader.

    Only the legacy in-process test harness drives the server with text
    streams; the real entry point uses ``sys.stdin.buffer``. ``read`` is
    char-based here (exact for the ASCII payloads the text harness uses);
    byte-exact framed reads come through the binary path.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def readline(self) -> bytes:
        return self._stream.readline().encode("utf-8")

    def read(self, size: int) -> bytes:
        return self._stream.read(size).encode("utf-8")


class _TextWriterAdapter:
    """Expose a text stream as a byte writer (mirror of the reader adapter)."""

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, data: bytes) -> None:
        self._stream.write(data.decode("utf-8"))

    def flush(self) -> None:
        self._stream.flush()


def _byte_reader(stdin):
    if stdin is None:
        return sys.stdin.buffer
    buffer = getattr(stdin, "buffer", None)
    if buffer is not None:  # real text stdio (TextIOWrapper) → use its binary buffer
        return buffer
    if isinstance(stdin, io.TextIOBase):  # in-memory text stream (StringIO)
        return _TextReaderAdapter(stdin)
    return stdin  # already a binary stream (BytesIO / BufferedReader)


def _byte_writer(stdout):
    if stdout is None:
        return sys.stdout.buffer
    buffer = getattr(stdout, "buffer", None)
    if buffer is not None:
        return buffer
    if isinstance(stdout, io.TextIOBase):
        return _TextWriterAdapter(stdout)
    return stdout


def _read_exact(reader, count: int) -> bytes:
    """Read exactly ``count`` bytes, or fewer only at EOF."""
    chunks: List[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            break  # EOF before the declared payload was complete
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_content_length(first_line: bytes, reader) -> Optional[int]:
    """Consume a header block (starting at ``first_line``) up to the blank
    separator line and return the parsed ``Content-Length``.

    Returns ``None`` if no valid ``Content-Length`` header is present. Handles
    both ``\r\n`` and ``\n`` line endings since ``strip`` drops either.
    """
    content_length: Optional[int] = None
    line = first_line
    while True:
        stripped = line.strip()
        if not stripped:
            break  # blank line terminates the header block
        name, sep, value = stripped.partition(b":")
        if sep and name.strip().lower() == b"content-length":
            try:
                content_length = int(value.strip())
            except ValueError:
                return None
        line = reader.readline()
        if not line:
            break  # EOF before the blank separator
    return content_length


def run(stdin=None, stdout=None) -> int:
    """Serve MCP requests on stdin/stdout until EOF, in either framing."""
    reader = _byte_reader(stdin)
    writer = _byte_writer(stdout)

    while True:
        line = reader.readline()
        if not line:
            break  # EOF
        stripped = line.strip()
        if not stripped:
            continue  # blank line between messages

        if _HEADER_LINE.match(stripped):
            framing = FRAMING_HEADER
            content_length = _read_content_length(stripped, reader)
            if content_length is None:
                _write(writer, framing, _error_response(None, ERR_PARSE, "invalid Content-Length header"))
                continue
            payload = _read_exact(reader, content_length)
            if len(payload) < content_length:
                _write(writer, framing, _error_response(None, ERR_PARSE, "unexpected EOF in framed payload"))
                break
        else:
            framing = FRAMING_NEWLINE
            payload = stripped

        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _write(writer, framing, _error_response(None, ERR_PARSE, "invalid JSON"))
            continue
        if not isinstance(message, dict):
            _write(writer, framing, _error_response(None, ERR_INVALID_REQUEST, "message must be a JSON object"))
            continue
        response = handle_message(message)
        if response is not None:
            _write(writer, framing, response)
    return 0


def _write(writer, framing: str, payload: Dict[str, Any]) -> None:
    """Serialize ``payload`` to ``writer`` in the requested framing."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if framing == FRAMING_HEADER:
        header = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n"
        writer.write(header + body)
    else:
        writer.write(body + b"\n")
    writer.flush()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
