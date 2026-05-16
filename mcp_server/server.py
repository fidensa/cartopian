"""Cartopian MCP server — JSON-RPC over stdio, MCP protocol 2024-11-05.

The server is the cross-agent entry point for Cartopian. Once an agent
(Claude Code, Claude Desktop, Codex, Gemini CLI, anything that speaks
MCP) is configured to launch ``cartopian-mcp``, the operator can say
"use cartopian" from any directory and the agent gets:

- Prompts — one per skill in ``skills/``, plus a ``use_cartopian``
  meta-prompt that orients the agent and routes it to the right skill.
- Tools — one per CLI subcommand in ``cli.main.SUBCOMMANDS``. Each tool
  invokes its handler in-process with stdout/stderr captured, parses
  NDJSON output back into structured records, and surfaces stderr
  prefixes verbatim so the FR-014 error contract is preserved.
- Resources — ``cartopian://skills/<name>``, ``cartopian://protocol/<name>``,
  ``cartopian://templates/<name>``, and ``cartopian://project/<id>/<file>``
  for registered projects.

Transport is newline-delimited JSON-RPC over stdin/stdout (no
Content-Length headers); this matches what current MCP clients
(Claude Code, Claude Desktop) negotiate by default on stdio.

The server depends on the rest of the install tree (``skills/``,
``protocol/``, ``templates/``, ``cli/``, ``projects.json``) but pulls
in no third-party Python packages — same posture as ``cli/``.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "cartopian"
SERVER_VERSION = "0.1.0"

# JSON-RPC error codes (per spec + MCP extensions).
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

URI_SCHEME = "cartopian"


class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


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
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                cleaned = stripped.lstrip("#").strip()
                return cleaned[:limit] if cleaned else path.name
            return stripped[:limit]
    except OSError:
        pass
    return path.name


def list_prompts() -> List[Dict[str, Any]]:
    prompts: List[Dict[str, Any]] = [{
        "name": "use_cartopian",
        "description": (
            "Enter Cartopian PM mode. Orients the agent, lists the skill / "
            "tool / resource surface, and routes to the right skill for the "
            "operator's intent."
        ),
    }]
    for path in _skill_paths():
        prompts.append({
            "name": _skill_name(path),
            "description": _first_line_summary(path),
        })
    return prompts


def _use_cartopian_messages() -> List[Dict[str, Any]]:
    skill_names = [_skill_name(p) for p in _skill_paths()]
    tool_names = sorted(_tool_registry().keys())
    text = (
        "You have entered **Cartopian PM mode**. Cartopian is a "
        "filesystem-first project governance protocol. For this conversation "
        "you are the Project Manager unless the operator says otherwise.\n\n"
        "## Surface available to you\n\n"
        "**Prompts** — invoke as MCP prompts when the operator's intent maps "
        "to a lifecycle skill:\n"
        + "\n".join(f"- `{name}`" for name in skill_names)
        + "\n\n**Tools** — invoke directly when you need structured data or "
        "to mutate lifecycle state. Each tool wraps a Cartopian CLI "
        "subcommand and returns NDJSON records:\n"
        + "\n".join(f"- `{name}`" for name in tool_names)
        + "\n\n**Resources** — read on demand:\n"
        "- `cartopian://protocol/CONVENTIONS` — protocol contract\n"
        "- `cartopian://skills/<name>` — every skill, readable\n"
        "- `cartopian://templates/<name>` — every template\n"
        "- `cartopian://project/<id>/STATE` / `REQUIREMENTS` / "
        "`IMPLEMENTATION_PLAN` — per-project artifacts\n\n"
        "## Default first move\n\n"
        "Invoke the **`start_session`** prompt to resolve the current "
        "project and propose the next PM action. If `discover_projects` "
        "returns no rows, invoke **`init_project`** instead — the operator "
        "has nothing to resume.\n\n"
        "## Lifecycle authority\n\n"
        "`cartopian://protocol/CONVENTIONS` is the contract for all "
        "lifecycle actions (task movement, review verdicts, session state, "
        "git behavior). Read it before any mutating action.\n"
    )
    return [{"role": "user", "content": {"type": "text", "text": text}}]


def _skill_messages(path: Path) -> List[Dict[str, Any]]:
    try:
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise McpError(ERR_INTERNAL, f"cannot read skill {path}: {exc}")
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
    items: List[Dict[str, Any]] = []
    for tool_name, entry in sorted(_tool_registry().items()):
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
    ``[error]`` / ``[guard]`` per FR-014).
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
        err_buf.write("[error] mcp_server caught unexpected exception:\n")
        err_buf.write(traceback.format_exc())
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

    # Protocol
    protocol_dir = ROOT / "protocol"
    if protocol_dir.is_dir():
        for path in sorted(protocol_dir.glob("*.md")):
            resources.append({
                "uri": f"{URI_SCHEME}://protocol/{path.stem}",
                "name": f"protocol: {path.stem}",
                "description": _first_line_summary(path),
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
        for path in _skill_paths():
            if _skill_name(path) == tail[0]:
                resolved_path = path
                break
    elif namespace == "protocol":
        candidate = ROOT / "protocol" / f"{tail[0]}.md"
        if candidate.exists():
            resolved_path = candidate
    elif namespace == "templates":
        candidate = ROOT / "templates" / tail[0]
        if candidate.exists():
            resolved_path = candidate
    elif namespace == "project":
        if len(tail) != 2:
            raise McpError(ERR_INVALID_PARAMS, f"project uri requires <id>/<file>: {uri}")
        project_id, kind = tail
        for entry in _registry_entries():
            if entry["id"] == project_id:
                candidate = Path(entry["path"]) / f"{kind}.md"
                if candidate.exists():
                    resolved_path = candidate
                break
    else:
        raise McpError(ERR_INVALID_PARAMS, f"unknown cartopian namespace: {namespace}")

    if resolved_path is None:
        raise McpError(ERR_INVALID_PARAMS, f"resource not found: {uri}")

    try:
        text = resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise McpError(ERR_INTERNAL, f"cannot read {resolved_path}: {exc}")
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
    return {"name": SERVER_NAME, "version": SERVER_VERSION}


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
    except Exception as exc:  # pragma: no cover — defensive
        return _error_response(
            rpc_id,
            ERR_INTERNAL,
            f"unhandled server exception: {exc}",
            {"traceback": traceback.format_exc()},
        )
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error_response(rpc_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


# ---------------------------------------------------------------------------
# Stdio loop
# ---------------------------------------------------------------------------

def run(stdin=None, stdout=None) -> int:
    """Serve MCP requests on stdin/stdout until EOF."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response = _error_response(None, ERR_PARSE, "invalid JSON")
            _write(stdout, response)
            continue
        if not isinstance(message, dict):
            response = _error_response(None, ERR_INVALID_REQUEST, "message must be a JSON object")
            _write(stdout, response)
            continue
        response = handle_message(message)
        if response is not None:
            _write(stdout, response)
    return 0


def _write(stream, payload: Dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
