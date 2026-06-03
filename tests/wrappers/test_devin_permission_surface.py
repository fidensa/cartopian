"""Devin wrapper permission-surface composition tests — TASK-03-012 / P03-FIX-008.

The `cartopian-devin` wrapper previously passed `--permission-mode auto|dangerous`,
which is NOT a valid value on the current documented "Devin for Terminal" CLI
surface (cli.devin.ai/docs, captured in `tests/wrappers/pm-devin/FINDINGS.md`):
`--permission-mode` takes one of `normal | accept-edits | bypass | autonomous`,
and OS isolation is engaged with the separate `--sandbox` flag (which auto-selects,
and only permits, `autonomous`). `dangerous` exists only as an *interactive*
`/bypass` alias, never as a `--permission-mode` value.

These are COMPOSITION-level assertions only: there is no live `devin` CLI here, so
a fake `devin` records the argv the wrapper composes and we assert it matches the
documented surface. This is NOT a live-launch verification.

The wrapper-side harness (fake CLI on a restricted PATH that records argv) mirrors
`tests/wrappers/test_timeout_ssot.py`.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "wrappers" / "bin" / "cartopian-devin"

# The valid `--permission-mode` values on the documented surface, and the values
# the stale wrapper used to pass that the surface does NOT accept.
VALID_MODES = ("normal", "accept-edits", "bypass", "autonomous")
STALE_VALUES = ("auto", "dangerous")

bash = shutil.which("bash")
pytestmark = pytest.mark.skipif(bash is None, reason="bash not available")


def _make_fake_devin(bin_dir: Path, args_out: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / "devin"
    p.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{args_out}"\nexit 0\n')
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    return root


def _prompt(root: Path, rid: str) -> Path:
    p = root / "prompts" / f"PROMPT-{rid}.md"
    # Free of any "--permission-mode"/"--sandbox" substring so the argv scan
    # cannot be fooled by the prompt content itself.
    p.write_text("do the thing")
    return p


def _compose(tmp_path: Path, rid: str, permission: str | None):
    """Run the real wrapper against a fake `devin` and return (rc, argv_list, stderr).

    PATH excludes `cartopian` so the access-grants step is skipped, and contains a
    real timeout/gtimeout so the wrapper's OS-deadline branch is exercised normally.
    """
    root = _project(tmp_path)
    prompt = _prompt(root, rid)
    fake_bin = tmp_path / "fakebin"
    args_out = tmp_path / "argv.txt"
    _make_fake_devin(fake_bin, args_out)

    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    if tbin:
        path_parts.insert(1, str(Path(tbin).parent))
    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": "30m",
    }
    if permission is not None:
        env["CARTOPIAN_DEVIN_PERMISSION"] = permission

    res = subprocess.run(
        [bash, str(WRAPPER), str(prompt)],
        capture_output=True, text=True, env=env, timeout=60,
    )
    argv = args_out.read_text().splitlines() if args_out.exists() else []
    return res.returncode, argv, res.stderr


def _mode_value(argv: list[str]) -> str:
    """The value passed after --permission-mode (or '' if the flag is absent)."""
    if "--permission-mode" not in argv:
        return ""
    return argv[argv.index("--permission-mode") + 1]


# --- default posture ------------------------------------------------------

def test_default_composes_sandboxed_autonomous(tmp_path):
    """No env → the conservative default: `--sandbox --permission-mode autonomous`."""
    rc, argv, err = _compose(tmp_path, "03-400", None)
    assert rc == 0, err
    assert "--sandbox" in argv, f"default must engage the OS sandbox; argv={argv!r}"
    assert _mode_value(argv) == "autonomous", f"argv={argv!r}"
    # --sandbox must precede the mode value it pairs with (documented invocation).
    assert argv.index("--sandbox") < argv.index("--permission-mode"), argv


def test_no_stale_permission_value_ever_composed(tmp_path):
    """The wrapper must never pass the stale `auto`/`dangerous` --permission-mode
    values (the bug this task fixes), in any mode including the default."""
    for permission in (None, "normal", "accept-edits", "bypass", "autonomous",
                       "auto", "dangerous"):
        rc, argv, err = _compose(tmp_path, "03-401", permission)
        assert rc == 0, f"{permission}: {err}"
        assert _mode_value(argv) not in STALE_VALUES, (
            f"permission={permission!r} composed a stale value "
            f"{_mode_value(argv)!r}; argv={argv!r}"
        )
        assert _mode_value(argv) in VALID_MODES, (
            f"permission={permission!r} composed a non-surface value "
            f"{_mode_value(argv)!r}; argv={argv!r}"
        )


# --- explicit modes map to the documented surface ------------------------

@pytest.mark.parametrize(
    "permission,expect_sandbox",
    [("normal", False), ("accept-edits", False), ("bypass", False), ("autonomous", True)],
)
def test_explicit_mode_maps_to_surface(tmp_path, permission, expect_sandbox):
    rc, argv, err = _compose(tmp_path, "03-402", permission)
    assert rc == 0, err
    assert _mode_value(argv) == permission, f"argv={argv!r}"
    assert ("--sandbox" in argv) is expect_sandbox, (
        f"{permission}: --sandbox presence wrong; argv={argv!r}"
    )


def test_only_autonomous_engages_sandbox(tmp_path):
    """`--sandbox` (which Devin couples to autonomous) is composed only for
    autonomous — never for the approval-gate modes."""
    for permission in ("normal", "accept-edits", "bypass"):
        _rc, argv, _err = _compose(tmp_path, "03-403", permission)
        assert "--sandbox" not in argv, f"{permission} must not engage --sandbox: {argv!r}"


# --- legacy compatibility (interface stability) --------------------------

@pytest.mark.parametrize("legacy,mapped", [("auto", "normal"), ("dangerous", "bypass")])
def test_legacy_values_map_onto_surface(tmp_path, legacy, mapped):
    rc, argv, err = _compose(tmp_path, "03-404", legacy)
    assert rc == 0, err
    assert _mode_value(argv) == mapped, (
        f"legacy {legacy!r} must map to {mapped!r}; argv={argv!r}"
    )
    # legacy 'dangerous' (=bypass) must NOT silently re-enable the OS sandbox.
    assert "--sandbox" not in argv, argv


# --- fail-closed on unknown input ----------------------------------------

def test_unknown_permission_fails_closed(tmp_path):
    """An unrecognised value is rejected before launch rather than passed through
    as a flag value devin would reject."""
    rc, argv, err = _compose(tmp_path, "03-405", "garbage")
    assert rc != 0, f"expected non-zero exit; argv={argv!r}"
    assert "unknown CARTOPIAN_DEVIN_PERMISSION" in err, err
    assert argv == [], f"fake devin should never have been invoked; argv={argv!r}"


# --- the prompt is always passed by file path ----------------------------

def test_prompt_passed_by_file_path(tmp_path):
    rc, argv, err = _compose(tmp_path, "03-406", None)
    assert rc == 0, err
    assert "--prompt-file" in argv, argv
    assert argv[0] == "-p", f"headless print flag must lead; argv={argv!r}"
    assert argv[argv.index("--prompt-file") + 1].endswith("PROMPT-03-406.md"), argv
