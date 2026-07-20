"""Static parity: PS1 wrappers translate CARTOPIAN_WORK_ROOTS like the bash ones.

``pwsh`` is not available on this host, so this is a *static* parity assertion
(the project's standing posture for PS1 wrappers — see
``test_ps1_model_flag.py``). The bash wrappers' CARTOPIAN_WORK_ROOTS contract
is exercised live by ``test_work_roots_grant.py``; this file asserts the
PowerShell mirrors hold the same invariants:

* ``cartopian-codex.ps1`` widens the workspace-write sandbox with a
  ``sandbox_workspace_write.writable_roots`` -c override, only inside a guard
  requiring non-bypass + workspace-write + a set CARTOPIAN_WORK_ROOTS, and
  before the trailing prompt append;
* ``cartopian-claude.ps1`` appends ``--add-dir`` per work root only inside an
  ``if ($env:CARTOPIAN_WORK_ROOTS)`` guard, before the trailing prompt append;
* ``cartopian-gemini.ps1`` / ``cartopian-devin.ps1`` warn on stderr when their
  sandbox is active and work roots are declared (no per-path grant surface).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PS1_DIR = REPO_ROOT / "wrappers" / "ps1"

WARNING_TEXT = "declared work roots may not be writable inside the sandbox"


def test_codex_ps1_widens_workspace_write_sandbox():
    text = (PS1_DIR / "cartopian-codex.ps1").read_text(encoding="utf-8")
    guard = "if (-not $Bypass -and $Sandbox -eq 'workspace-write' -and $env:CARTOPIAN_WORK_ROOTS) {"
    assert guard in text, "codex.ps1: missing guarded work-root widening block"
    guard_idx = text.find(guard)
    override_idx = text.find("sandbox_workspace_write.writable_roots=")
    assert override_idx != -1, "codex.ps1: missing writable_roots -c override"
    assert override_idx > guard_idx, (
        "codex.ps1: writable_roots override must sit inside the guarded block"
    )
    tail_idx = text.find("$Args += $PromptPathAbs")
    assert tail_idx != -1 and tail_idx > override_idx, (
        "codex.ps1: the widening block must precede the trailing prompt append"
    )
    # TOML escaping of Windows paths (backslash, double-quote) is present.
    assert r".Replace('\', '\\')" in text
    assert text.count("sandbox_workspace_write.writable_roots=") == 1


def test_claude_ps1_adds_work_roots_as_add_dir():
    text = (PS1_DIR / "cartopian-claude.ps1").read_text(encoding="utf-8")
    guard = "if ($env:CARTOPIAN_WORK_ROOTS) {"
    append = "$Args += @('--add-dir', $root)"
    guard_idx = text.find(guard)
    append_idx = text.find(append)
    assert guard_idx != -1, "claude.ps1: missing CARTOPIAN_WORK_ROOTS guard"
    assert append_idx != -1, "claude.ps1: missing --add-dir append"
    assert append_idx > guard_idx, (
        "claude.ps1: --add-dir append must sit inside the CARTOPIAN_WORK_ROOTS "
        "guard; unset work roots would still inject a flag"
    )
    tail_idx = text.find("$Args += $PromptPathAbs")
    assert tail_idx != -1 and tail_idx > append_idx, (
        "claude.ps1: the --add-dir block must precede the trailing prompt append"
    )
    assert text.count(append) == 1


def test_gemini_ps1_warns_inside_sandbox_and_roots_guard():
    text = (PS1_DIR / "cartopian-gemini.ps1").read_text(encoding="utf-8")
    assert WARNING_TEXT in text, "gemini.ps1: missing work-root sandbox warning"
    warn_idx = text.find(WARNING_TEXT)
    sandbox_idx = text.find("if ($Sandbox) {")
    roots_idx = text.find("if ($env:CARTOPIAN_WORK_ROOTS) {")
    assert sandbox_idx != -1 and roots_idx != -1
    assert sandbox_idx < roots_idx < warn_idx, (
        "gemini.ps1: the warning must be guarded by sandbox-on AND work-roots-set"
    )


def test_devin_ps1_warns_inside_sandbox_and_roots_guard():
    text = (PS1_DIR / "cartopian-devin.ps1").read_text(encoding="utf-8")
    assert WARNING_TEXT in text, "devin.ps1: missing work-root sandbox warning"
    warn_idx = text.find(WARNING_TEXT)
    guard = "if ($DevinSandboxSupported -and $env:CARTOPIAN_WORK_ROOTS) {"
    guard_idx = text.find(guard)
    assert guard_idx != -1, "devin.ps1: missing sandbox+work-roots guard"
    assert guard_idx < warn_idx, (
        "devin.ps1: the warning must sit inside the sandbox+work-roots guard"
    )
