"""Measure the portable startup payload, independently of model tokenizers.

Run ``python3 -m evaluations.startup_context``. Reads the shipped surfaces and
executes startup against an isolated project and capture store, never a live registry.
This is a transport-byte budget, not a claim about a host's context accounting
or whether an AI actually follows the runbook. Host conversation baselines,
reasoning, and repeated-input usage require separate session measurements.
"""
import argparse
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from cli import intake_adapter
from cli.commands import next_action
from cli.intake_adapter import INTAKE_ROOT_ENV, ROLE_ENV, SessionStore, handle_event
from cli.protocol_gate import read_shipped_project_schema_version
from mcp_server import server
from mcp_server.skill_metadata import BRIDGE_TARGETS


# Bound each independently so one growing surface cannot hide behind another
# shrinking surface. Full eager tool advertisement is measured separately:
# hosts that support deferred discovery need only the named startup tools.
BUDGETS = {
    "entry_bridge": 2_000,
    "server_instructions": 2_500,
    "startup_tools": 4_500,
    "entry_resource": 9_000,
    "protocol_slice": 14_000,
    "session_runbook": 7_000,
    "orientation_and_audit": 5_000,
    "selection": 2_000,
    "eager_tool_catalog": 44_000,
    "startup_total": 38_000,
}


def _bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _normalize_paths(value, paths):
    if isinstance(value, str):
        for path, replacement in paths:
            value = value.replace(str(path), replacement)
        return value
    if isinstance(value, list):
        return [_normalize_paths(item, paths) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_paths(item, paths) for key, item in value.items()}
    return value


def measure():
    with tempfile.TemporaryDirectory(prefix="cartopian-context-") as temporary:
        home = Path(temporary)
        root = home / "project"
        root.mkdir()
        (root / "cartopian.toml").write_text(
            '[project]\nid = "context-benchmark"\nname = "Context Benchmark"\n'
            f'project_schema_version = "{read_shipped_project_schema_version()}"\n'
            '[roles.worker]\ndescription = "Performs assigned work."\n',
            encoding="utf-8",
        )
        # Normalize installation and project paths in measurements; live
        # user configuration and registered projects must not affect results.
        intake = home / "intake"
        with mock.patch.object(Path, "home", return_value=home), mock.patch.object(
            intake_adapter, "default_intake_root", return_value=intake,
        ):
            registry = home / ".cartopian" / "projects.json"
            registry.parent.mkdir()
            registry.write_text(json.dumps([{
                "id": "context-benchmark", "path": str(root), "label": "Context Benchmark",
            }]), encoding="utf-8")
            store = SessionStore(intake)
            # Use a supported adapter for fixture capture; the measured MCP
            # contract and entry templates are shared across every PM host.
            handle_event(store, "claude", {
                "session_id": "context-benchmark", "hook_event_name": "SessionStart",
            })
            session = store.load_session("claude", "context-benchmark")
            with mock.patch.dict(os.environ, {INTAKE_ROOT_ENV: str(intake), ROLE_ENV: ""}):
                discovery = server.call_tool("discover_projects", {})
                binding = server.call_tool("select_project", {
                    "project_root": str(root), "handle": session["handle"],
                })
            if discovery["isError"] or binding["isError"]:
                raise RuntimeError("startup fixture selection failed")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = next_action.handler(argparse.Namespace(
                    project_path=str(root), compact=True, audit=True, reconcile=False,
                ))
            if code or err.getvalue():
                raise RuntimeError("startup context fixture failed: " + err.getvalue())
            orientation = json.loads(out.getvalue())
            if orientation["startup"]["verdict"] != "planning-incomplete":
                raise RuntimeError("startup context fixture lost its planning boundary")
            tools = server.list_tools()
            surfaces = {
                "entry_bridge": max(
                    ((server.ROOT / target.path).read_text(encoding="utf-8") for target in BRIDGE_TARGETS.values()),
                    key=lambda text: len(text.encode("utf-8")),
                ),
                "server_instructions": server._server_instructions(),
                "startup_tools": [t for t in tools if t["name"] in {
                    "read_context", "discover_projects", "select_project", "next_action",
                }],
                "entry_resource": server.call_tool("read_context", {"uri": "cartopian://skills/use_cartopian"}),
                "protocol_slice": server.call_tool("read_context", {"uri": "cartopian://protocol/CONVENTIONS/startup"}),
                "session_runbook": server.call_tool("read_context", {"uri": "cartopian://skills/start_session"}),
                "orientation_and_audit": {"content": [{"type": "text", "text": json.dumps(orientation)}]},
                "selection": [discovery, binding],
                "eager_tool_catalog": tools,
            }
            sizes = {}
            for name, value in surfaces.items():
                sizes[name] = _bytes(_normalize_paths(value, [
                    (home.resolve(), "/fixture"), (home, "/fixture"),
                    (server.ROOT, "/cartopian"),
                ]))
            sizes["startup_total"] = sum(size for name, size in sizes.items() if name != "eager_tool_catalog")
            return sizes


def main():
    sizes = measure()
    exceeded = {name: size for name, size in sizes.items() if size > BUDGETS[name]}
    print(json.dumps({"unit": "UTF-8 bytes", "measured": sizes, "budgets": BUDGETS, "exceeded": exceeded}, indent=2))
    return int(bool(exceeded))


if __name__ == "__main__":
    raise SystemExit(main())
