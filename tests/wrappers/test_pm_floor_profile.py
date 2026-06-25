"""PM containment floor — static + behavioral regression.

Locks the launch-profile floor for the contained Claude Code PM so it
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
  assert it is EXACTLY the locked 16 ``mcp__cartopian__*`` tools with no
  prohibited tool. Skipped when no evidence is present (the live, network- and
  cost-bearing capture is the shell harness's job; this just pins its result).
* **Genesis floor** — the contained inventory must EXCLUDE the four
  config/registry-genesis tools (generate_config / scaffold_project /
  register_project / unregister_project). The wrapper grants the Cartopian
  toolset by the ``--allowedTools "mcp__cartopian"`` PREFIX, so the shared MCP
  server's withholding (``CARTOPIAN_PM_CONTAINED=1`` via
  ``mcp-cartopian-only.json``) is what keeps them out — pinned here from the
  shell harness's ``green-genesis-*`` captures, with the pre-floor 20-tool
  exposure as the red baseline.

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
EVIDENCE = REPO_ROOT / "tests" / "wrappers" / "pm-floor" / "evidence"
GREEN_TOOLS = EVIDENCE / "green-tools.txt"
GREEN_GENESIS_INV = EVIDENCE / "green-genesis-inventory.txt"
GREEN_GENESIS_CFG = EVIDENCE / "green-genesis-config-write.txt"
RED_GENESIS_INV = EVIDENCE / "red-genesis-inventory.txt"

# The locked green inventory — exactly these 16 tools. The four
# config/registry-genesis tools are WITHHELD from a contained PM, so the
# contained claude inventory is the day-to-day lifecycle/read surface only.
EXPECTED_TOOLS = {
    "mcp__cartopian__close_audit",
    "mcp__cartopian__compose_state",
    "mcp__cartopian__delete_prompt",
    "mcp__cartopian__delete_report",
    "mcp__cartopian__discover_projects",
    "mcp__cartopian__handoff_packet",
    "mcp__cartopian__list_tasks",
    "mcp__cartopian__move_task",
    "mcp__cartopian__next_action",
    "mcp__cartopian__plan_audit",
    "mcp__cartopian__report_action",
    "mcp__cartopian__resolve_config",
    "mcp__cartopian__task_bundle",
    "mcp__cartopian__validate_task_readiness",
    "mcp__cartopian__wait_handoff",
    "mcp__cartopian__wait_report",
}
# The four genesis tools the floor withholds from a contained PM. Their
# reappearance in the contained inventory re-opens the config-write vector —
# assert they are absent from the live inventory.
GENESIS_TOOLS = {
    "mcp__cartopian__generate_config",
    "mcp__cartopian__scaffold_project",
    "mcp__cartopian__register_project",
    "mcp__cartopian__unregister_project",
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
    """Every floor flag is hard-coded in the wrapper."""
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
    """If the live shell harness captured a green inventory, it must equal the locked 16."""
    if not GREEN_TOOLS.is_file():
        pytest.skip(f"no captured green inventory ({GREEN_TOOLS}); run pm-floor/run-floor-test.sh")
    tools = {l.strip() for l in GREEN_TOOLS.read_text().splitlines() if l.strip()}
    assert tools == EXPECTED_TOOLS, (
        "captured green inventory drifted from the locked 16-tool floor:\n"
        f"  unexpected: {sorted(tools - EXPECTED_TOOLS)}\n"
        f"  missing:    {sorted(EXPECTED_TOOLS - tools)}"
    )
    assert not (tools & PROHIBITED_TOOLS), f"prohibited tools present: {sorted(tools & PROHIBITED_TOOLS)}"


def test_live_inventory_excludes_genesis_tools_if_evidence_present():
    """The live contained claude inventory must carry NONE of the four
    genesis tools (config-write vector closed; their return is a regression)."""
    if not GREEN_TOOLS.is_file():
        pytest.skip(f"no captured green inventory ({GREEN_TOOLS}); run pm-floor/run-floor-test.sh")
    tools = {l.strip() for l in GREEN_TOOLS.read_text().splitlines() if l.strip()}
    present = tools & GENESIS_TOOLS
    assert not present, f"genesis tools reappeared in the contained inventory: {sorted(present)}"


def test_contained_mcp_inventory_excludes_genesis_if_evidence_present():
    """The MCP server driven exactly as the wrapper launches it (the inventory the
    --allowedTools prefix grant offers) advertises none of the four genesis tools."""
    if not GREEN_GENESIS_INV.is_file():
        pytest.skip(f"no contained genesis inventory ({GREEN_GENESIS_INV}); run pm-floor/run-floor-test.sh")
    names = {l.strip() for l in GREEN_GENESIS_INV.read_text().splitlines() if l.strip()}
    bare_genesis = {g.rsplit("__", 1)[-1] for g in GENESIS_TOOLS}
    assert not (names & bare_genesis), f"contained MCP server advertised genesis tools: {sorted(names & bare_genesis)}"


def test_config_write_vector_closed_if_evidence_present():
    """A contained generate_config attempt is refused and leaves no file."""
    if not GREEN_GENESIS_CFG.is_file():
        pytest.skip(f"no config-write evidence ({GREEN_GENESIS_CFG}); run pm-floor/run-floor-test.sh")
    text = GREEN_GENESIS_CFG.read_text()
    assert "VERDICT: CONFIG_WRITE_BLOCKED" in text, f"config-write was not blocked:\n{text}"
    assert "cartopian_toml_on_disk: False" in text, f"a cartopian.toml landed on disk:\n{text}"


def test_genesis_red_baseline_present_proves_vector_real_if_present():
    """The pre-floor red: driven WITHOUT containment, the SAME server advertises
    all four genesis tools — proving the floor (not the environment) closes them."""
    if not RED_GENESIS_INV.is_file():
        pytest.skip(f"no genesis red baseline ({RED_GENESIS_INV}); run pm-floor/run-floor-test.sh --with-red")
    names = {l.strip() for l in RED_GENESIS_INV.read_text().splitlines() if l.strip()}
    bare_genesis = {g.rsplit("__", 1)[-1] for g in GENESIS_TOOLS}
    assert bare_genesis <= names, (
        f"red baseline should advertise all genesis tools; missing: {sorted(bare_genesis - names)}"
    )
