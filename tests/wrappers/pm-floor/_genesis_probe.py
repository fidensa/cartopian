#!/usr/bin/env python3
"""Genesis-tool floor probe for the contained Claude Code PM.

The contained `claude` PM is granted the Cartopian MCP toolset via the
`--allowedTools "mcp__cartopian"` PREFIX grant in `cartopian-claude-pm`. That
prefix grants whatever the Cartopian MCP server *advertises* — so the PM's
genesis-tool exposure is decided entirely by the server's `tools/list` under the
exact launch the wrapper uses (`wrappers/etc/mcp-cartopian-only.json`, which sets
`CARTOPIAN_PM_CONTAINED=1`). This helper drives THAT server, the same way the
wrapper does, so the inventory it reports is precisely what the contained claude
PM is offered — independent of any live `claude` round-trip.

Modes (stdlib-only, NF-001):

  inventory   [--uncontained]   print the advertised tool names, one per line.
                                Default applies the wrapper's launch env
                                (CARTOPIAN_PM_CONTAINED=1 from the MCP config);
                                --uncontained drops that env to capture the RED
                                baseline (the genesis tools the prefix grant
                                exposed before the genesis floor).
  config-write <scratch_dir>    attempt the genesis tool `generate_config`
                                against <scratch_dir> under the contained launch
                                env; print a VERDICT line and whether a
                                cartopian.toml landed on disk. <scratch_dir> MUST
                                be a throwaway path (the harness uses $TMPDIR).

Exit status is 0 on a clean probe; non-zero (with a FATAL line on stderr) when
the server could not be driven, so the harness fails closed rather than passing
on an untrusted capture.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# wrappers/etc/mcp-cartopian-only.json — the EXACT MCP launch the PM wrapper uses.
MCP_CONFIG = HERE.parents[2] / "wrappers" / "etc" / "mcp-cartopian-only.json"

GENESIS = ("generate_config", "scaffold_project", "register_project", "unregister_project")


def _server_launch() -> tuple[list[str], dict]:
    """Return (argv, env) for the cartopian MCP server, per the wrapper's config."""
    cfg = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
    server = cfg["mcpServers"]["cartopian"]
    argv = [server["command"], *server.get("args", [])]
    env = dict(os.environ)
    env.update(server.get("env", {}))
    return argv, env


def _drive(messages, *, contained: bool):
    argv, env = _server_launch()
    if not contained:
        # RED baseline: same server binary, but WITHOUT the containment signal the
        # MCP config injects — proves the genesis tools are present absent the floor.
        env.pop("CARTOPIAN_PM_CONTAINED", None)
    if not Path(argv[0]).exists():
        sys.stderr.write(f"FATAL: MCP server command not found: {argv[0]}\n")
        sys.exit(2)
    stdin = "\n".join(json.dumps(m) for m in messages) + "\n"
    proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, timeout=60)
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out, proc.stderr


def _tool_names(contained: bool) -> list[str]:
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    resp, err = _drive(msgs, contained=contained)
    for r in resp:
        if r.get("id") == 2 and "result" in r:
            return sorted(t["name"] for t in r["result"]["tools"])
    sys.stderr.write(f"FATAL: no tools/list result from MCP server\n{err}\n")
    sys.exit(2)


def _cmd_inventory(argv: list[str]) -> int:
    contained = "--uncontained" not in argv
    for name in _tool_names(contained):
        print(name)
    return 0


def _cmd_config_write(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("FATAL: config-write requires a scratch_dir argument\n")
        return 2
    scratch = Path(argv[0])
    target = scratch / "scratch-proj"
    target.mkdir(parents=True, exist_ok=True)
    cfg = target / "cartopian.toml"
    if cfg.exists():
        cfg.unlink()  # ensure a clean before-state
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "generate_config",
            "arguments": {"project_path": str(target), "name": "Probe", "proj_id": "probe"},
        }},
    ]
    resp, err = _drive(msgs, contained=True)
    call = next((r for r in resp if r.get("id") == 3), None)
    if call is None:
        sys.stderr.write(f"FATAL: no response to contained generate_config call\n{err}\n")
        return 2
    refused = "error" in call and "withheld" in (call["error"].get("message") or "")
    on_disk = cfg.exists()
    print(f"scratch_dir: {target}")
    print(f"refused_with_withheld: {refused}")
    print(f"cartopian_toml_on_disk: {on_disk}")
    if refused and not on_disk:
        print("VERDICT: CONFIG_WRITE_BLOCKED (genesis tool withheld, no file on disk): PASS")
        return 0
    print("VERDICT: CONFIG_WRITE_NOT_BLOCKED: FAIL")
    return 1


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(__doc__ or "")
        return 2
    mode, rest = argv[0], argv[1:]
    if mode == "inventory":
        return _cmd_inventory(rest)
    if mode == "config-write":
        return _cmd_config_write(rest)
    sys.stderr.write(f"FATAL: unknown mode {mode!r}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
