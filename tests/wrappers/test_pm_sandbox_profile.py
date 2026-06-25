"""PM native-sandbox depth profile — static + behavioral regression.

Locks the Tier-2 native-sandbox DEPTH profile that the contained Claude Code PM
launch path applies beneath the tool-removal floor, so the depth layer
cannot silently weaken or disappear. Three complementary layers (same posture as
``test_pm_floor_profile.py``):

* **Profile static** — ``wrappers/etc/sandbox-pm-depth.json`` enables Claude
  Code's native OS sandbox, refuses the unsandboxed escape hatch, fails closed
  when the sandbox is unavailable, and denies BOTH the product repo and the
  work root for read and write (sandbox filesystem rules + the matching native
  permission deny rules).
* **Wrapper wiring** — the shipping wrapper hard-codes ``--settings`` pointing at
  that profile (so the floor launch path applies it), and *refuses*
  ``--settings`` / ``--setting-sources`` overrides so the depth profile is not
  overridable from the command line. The refusal runs live and cheaply (the
  guard precedes the ``claude`` precondition check).
* **Evidence lock** — if the live shell harness
  (``pm-sandbox/run-sandbox-test.sh``) has captured green transcripts, assert
  each shows the native OS sandbox denial (``Operation not permitted``). Skipped
  when no evidence is present (the live, cost-bearing capture is the shell
  harness's job; this just pins its result).

The authoritative red->green harness-level evidence is captured by the shell
harness; this module is the always-on CI anti-drift guard. It must not weaken or
duplicate the floor guard in ``test_pm_floor_profile.py``.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "wrappers" / "bin" / "cartopian-claude-pm"
PROFILE = REPO_ROOT / "wrappers" / "etc" / "sandbox-pm-depth.json"
EVID = REPO_ROOT / "tests" / "wrappers" / "pm-sandbox" / "evidence"

# The paths the depth profile must deny — product repo + tool-repo work root.
DENIED_ROOTS = {
    "/Users/scott/Projects/cartopian-manager",
    "/Users/scott/Projects/cartopian",
}


@pytest.fixture(scope="module")
def profile() -> dict:
    assert PROFILE.is_file(), f"depth profile missing: {PROFILE}"
    return json.loads(PROFILE.read_text())


@pytest.fixture(scope="module")
def wrapper_src() -> str:
    assert WRAPPER.is_file(), f"PM wrapper missing: {WRAPPER}"
    return WRAPPER.read_text()


def test_sandbox_enabled_and_fail_closed(profile):
    sb = profile.get("sandbox", {})
    assert sb.get("enabled") is True, "sandbox.enabled must be true (native sandbox active)"
    assert sb.get("failIfUnavailable") is True, (
        "sandbox.failIfUnavailable must be true — fail closed if the OS sandbox is unavailable"
    )
    assert sb.get("allowUnsandboxedCommands") is False, (
        "sandbox.allowUnsandboxedCommands must be false — the dangerouslyDisableSandbox escape is ignored"
    )


def test_sandbox_denies_product_repo_and_work_root(profile):
    fs = profile.get("sandbox", {}).get("filesystem", {})
    deny_read = set(fs.get("denyRead") or [])
    deny_write = set(fs.get("denyWrite") or [])
    missing_read = DENIED_ROOTS - deny_read
    missing_write = DENIED_ROOTS - deny_write
    assert not missing_read, f"sandbox.filesystem.denyRead missing: {sorted(missing_read)}"
    assert not missing_write, f"sandbox.filesystem.denyWrite missing: {sorted(missing_write)}"


def test_permission_deny_rules_reinforce_paths(profile):
    """The native permission layer also denies Read/Edit/Write on the same roots."""
    deny = profile.get("permissions", {}).get("deny") or []
    blob = "\n".join(deny)
    for root in DENIED_ROOTS:
        for tool in ("Read", "Edit", "Write"):
            assert f"{tool}({root}" in blob, f"permissions.deny missing a {tool}(...) rule for {root}"


def test_wrapper_applies_depth_profile(wrapper_src):
    """The floor launch path hard-codes --settings pointing at the depth profile."""
    assert "--settings" in wrapper_src, "wrapper must apply the depth profile via --settings"
    assert "sandbox-pm-depth.json" in wrapper_src, (
        "wrapper must reference wrappers/etc/sandbox-pm-depth.json"
    )
    # The profile is part of the hard-coded FLOOR array, not an overridable default.
    assert '--settings "$SANDBOX_PROFILE"' in wrapper_src, (
        "wrapper must pass --settings \"$SANDBOX_PROFILE\" in the hard-coded FLOOR"
    )


@pytest.mark.parametrize("flag", ["--settings", "--setting-sources"])
def test_wrapper_refuses_settings_overrides(flag):
    """The depth profile is not overridable from the command line."""
    proc = subprocess.run(
        [str(WRAPPER), flag, "/tmp/whatever"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0, f"wrapper accepted override flag {flag!r} (rc=0)"
    assert "refusing" in (proc.stderr + proc.stdout).lower(), (
        f"wrapper did not report refusal for {flag!r}: {proc.stderr!r}"
    )


@pytest.mark.parametrize("name", ["green-read.jsonl", "green-write.jsonl"])
def test_green_evidence_shows_native_sandbox_denial(name):
    """If the live harness captured green transcripts, each must show the OS sandbox denial."""
    path = EVID / name
    if not path.is_file():
        pytest.skip(f"no captured green evidence ({path}); run pm-sandbox/run-sandbox-test.sh")
    assert "Operation not permitted" in path.read_text(), (
        f"green transcript {path} does not show the native OS sandbox denial 'Operation not permitted'"
    )
