"""PM containment floor — static + behavioral regression (TASK-01-001 / FR-002).

Locks the DEC-001 launch-profile floor for the contained Claude Code PM so it
cannot silently weaken. Two complementary layers:

* **Static parity** — the shipping wrapper ``wrappers/bin/cartopian-claude-pm``
  hard-codes the exact floor flags, references the Cartopian-only MCP config,
  launches from an isolated cwd, and NEVER passes ``--dangerously-skip-permissions``
  / ``--add-dir``. (Same standing posture as the PS1 static checks in
  ``test_ps1_work_root_guard.py``, which run without the live tool.)
* **Behavioral** — the wrapper *refuses* surface-reopening flags before it ever
  launches ``claude`` (exit 1, no process spawned). This runs live and cheaply
  because the refusal guard precedes the ``claude`` precondition check.
* **Inventory lock** — if the live shell harness
  (``pm-floor/run-floor-test.sh``) has been run and captured a green inventory,
  assert it is EXACTLY the locked 20 ``mcp__cartopian__*`` tools with no
  prohibited tool. Skipped when no evidence is present (the live, network- and
  cost-bearing capture is the shell harness's job; this just pins its result).

The authoritative red→green harness-level evidence is captured by the shell
harness; this module is the always-on CI anti-drift guard.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "wrappers" / "bin" / "cartopian-claude-pm"
MCP_CONFIG = REPO_ROOT / "wrappers" / "etc" / "mcp-cartopian-only.json"
GREEN_TOOLS = REPO_ROOT / "tests" / "wrappers" / "pm-floor" / "evidence" / "green-tools.txt"

# The locked green inventory (DEC-001 §a) — exactly these 20 tools.
EXPECTED_TOOLS = {
    "mcp__cartopian__close_audit",
    "mcp__cartopian__compose_state",
    "mcp__cartopian__delete_prompt",
    "mcp__cartopian__delete_report",
    "mcp__cartopian__discover_projects",
    "mcp__cartopian__generate_config",
    "mcp__cartopian__handoff_packet",
    "mcp__cartopian__list_tasks",
    "mcp__cartopian__move_task",
    "mcp__cartopian__next_action",
    "mcp__cartopian__plan_audit",
    "mcp__cartopian__register_project",
    "mcp__cartopian__report_action",
    "mcp__cartopian__resolve_config",
    "mcp__cartopian__scaffold_project",
    "mcp__cartopian__task_bundle",
    "mcp__cartopian__unregister_project",
    "mcp__cartopian__validate_task_readiness",
    "mcp__cartopian__wait_handoff",
    "mcp__cartopian__wait_report",
}
PROHIBITED_TOOLS = {
    "Bash", "Write", "Edit", "NotebookEdit", "Read", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task",
    "mcp__claude_ai_Gmail__authenticate",
    "mcp__claude_ai_Google_Drive__authenticate",
    "mcp__claude_ai_Google_Calendar__authenticate",
}


@pytest.fixture(scope="module")
def wrapper_src() -> str:
    assert WRAPPER.is_file(), f"PM wrapper missing: {WRAPPER}"
    return WRAPPER.read_text()


def test_wrapper_is_executable():
    import os
    assert WRAPPER.is_file(), f"PM wrapper missing: {WRAPPER}"
    assert os.access(WRAPPER, os.X_OK), f"PM wrapper not executable: {WRAPPER}"


@pytest.mark.parametrize(
    "flag",
    ['--tools ""', "--strict-mcp-config", "--mcp-config", '--allowedTools "mcp__cartopian"', "--disable-slash-commands"],
)
def test_floor_flag_present(wrapper_src, flag):
    """Every DEC-001 floor flag is hard-coded in the wrapper."""
    assert flag in wrapper_src, f"floor flag '{flag}' missing from {WRAPPER}"


def test_no_permission_bypass_in_wrapper(wrapper_src):
    """The wrapper must never grant the permission bypass.

    It may *name* the flag in the refusal guard / comments, but must not pass
    it to claude. Assert it never appears as a bare token on a line that is not
    a comment or the refusal `case` pattern.
    """
    for raw in wrapper_src.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "--dangerously-skip-permissions" in line:
            # Allowed only inside the refusal guard (a `case` pattern / echo).
            assert ("refus" in line.lower()) or ("| --" in line) or line.startswith("--dangerously-skip-permissions \\") or "echo" in line.lower(), (
                f"--dangerously-skip-permissions used outside the refusal guard: {line!r}"
            )


def test_mcp_config_is_cartopian_only():
    assert MCP_CONFIG.is_file(), f"MCP config missing: {MCP_CONFIG}"
    cfg = json.loads(MCP_CONFIG.read_text())
    servers = cfg.get("mcpServers", {})
    assert set(servers) == {"cartopian"}, f"MCP config must expose only 'cartopian', got {set(servers)}"


@pytest.mark.parametrize("flag", ["--add-dir", "--dangerously-skip-permissions", "--permission-mode"])
def test_wrapper_refuses_surface_reopening_flags(flag):
    """The floor is not overridable: the wrapper exits non-zero and does not launch."""
    proc = subprocess.run(
        [str(WRAPPER), flag, "/tmp"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0, f"wrapper accepted surface-reopening flag {flag!r} (rc=0)"
    assert "refusing" in (proc.stderr + proc.stdout).lower(), (
        f"wrapper did not report refusal for {flag!r}: {proc.stderr!r}"
    )


def test_green_inventory_locked_if_evidence_present():
    """If the live shell harness captured a green inventory, it must equal the locked 20."""
    if not GREEN_TOOLS.is_file():
        pytest.skip(f"no captured green inventory ({GREEN_TOOLS}); run pm-floor/run-floor-test.sh")
    tools = {l.strip() for l in GREEN_TOOLS.read_text().splitlines() if l.strip()}
    assert tools == EXPECTED_TOOLS, (
        "captured green inventory drifted from the locked 20-tool floor:\n"
        f"  unexpected: {sorted(tools - EXPECTED_TOOLS)}\n"
        f"  missing:    {sorted(EXPECTED_TOOLS - tools)}"
    )
    assert not (tools & PROHIBITED_TOOLS), f"prohibited tools present: {sorted(tools & PROHIBITED_TOOLS)}"
