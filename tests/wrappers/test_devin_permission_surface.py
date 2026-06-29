"""Devin wrapper permission-surface composition tests,
re-baselined after the 2026-06-04 live-binary re-probe.

Two devin permission surfaces are known (`tests/wrappers/pm-devin/FINDINGS.md`):

* the **four-mode** surface (cli.devin.ai docs, captured 2026-06-02):
  ``--permission-mode normal|accept-edits|bypass|autonomous`` plus the separate
  ``--sandbox`` flag (which couples to ``autonomous``);
* the **two-mode** surface (live binary ``devin 2026.5.26-3``, probed
  2026-06-04): ``--permission-mode`` accepts ONLY ``normal`` (alias ``auto``)
  and ``dangerous`` (aliases ``yolo``, ``bypass``); ``autonomous`` and
  ``accept-edits`` are rejected at argv parse (exit 2).

The wrapper detects the surface by **parser acceptance** — it probes
``devin --permission-mode autonomous --help`` and keys off the exit code (0 →
four-mode; anything else → two-mode), immune to help-text wording drift — and
maps the abstract ``CARTOPIAN_DEVIN_PERMISSION`` modes onto the DETECTED
surface. These are COMPOSITION-level assertions only: a fake ``devin``
emulates each surface's parser (rejecting/accepting the probe value) and
records the argv the wrapper composes for the real run. This is NOT a
live-launch verification.

Independently of the permission surface, the wrapper also probes whether the
binary accepts ``--sandbox`` at all (``devin --sandbox --help``). Older builds
predate the flag and reject it at argv parse; on those, the default
``autonomous`` mode — which would compose ``--sandbox`` — instead DEGRADES to
``--permission-mode bypass`` (the same auto-approve-all posture minus the OS
sandbox) with a warning, so the unattended handoff still runs as it always has
rather than emitting a flag the binary rejects. A sandbox probe that cannot
positively confirm the flag (non-zero for any reason, incl. a fully-broken
``--help``) is treated as UNSUPPORTED so the wrapper never composes
``--sandbox`` against a binary that would reject it.

The wrapper-side harness (fake CLI on a restricted PATH that records argv)
mirrors ``tests/wrappers/test_timeout_ssot.py``.
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

# Abstract modes the wrapper accepts (plus the legacy aliases below).
ABSTRACT_MODES = ("normal", "accept-edits", "bypass", "autonomous")

# Per-surface valid `--permission-mode` values. Anything else is a value the
# respective binary would reject at launch.
FOUR_MODE_VALID = {"normal", "accept-edits", "bypass", "autonomous"}
TWO_MODE_VALID = {"normal", "auto", "dangerous", "yolo", "bypass"}

# Fake-devin surface behaviors. Each emulates the corresponding parser:
#   two-mode  — rejects `--permission-mode autonomous|accept-edits` at parse
#               (exit 2, mirroring the live `devin 2026.5.26-3` error), so the
#               wrapper's acceptance probe fails and detects two-mode. Accepts
#               `--sandbox` (the live 2026.5.26-3 binary has the flag).
#   four-mode — accepts every documented mode AND `--sandbox`; the probe (which
#               carries --help) exits 0, so the wrapper detects four-mode.
#   no-sandbox— a two-mode parser that ALSO predates `--sandbox` and rejects it
#               at parse (exit 2). Models the operator's work binary: the
#               default `autonomous` mode must degrade to `--permission-mode
#               bypass` rather than emit a flag the binary rejects.
#   broken    — any --help invocation fails (exit 1) for an unrelated reason;
#               the wrapper must degrade to two-mode for the permission VALUE,
#               and — unable to confirm `--sandbox` — degrade the
#               sandbox-dependent autonomous default to `--permission-mode
#               bypass`.
SURFACES = ("two-mode", "four-mode", "no-sandbox", "broken")

_FAKE_BODIES = {
    "two-mode": """\
case " $* " in
  *" --permission-mode autonomous "*|*" --permission-mode accept-edits "*)
    echo "error: invalid value for '--permission-mode <PERMISSION_MODE>': Valid options: normal (auto), dangerous (yolo, bypass)" >&2
    exit 2 ;;
esac
case " $* " in
  *" --help "*) exit 0 ;;
esac
""",
    "four-mode": """\
case " $* " in
  *" --help "*) exit 0 ;;
esac
""",
    "no-sandbox": """\
case " $* " in
  *" --permission-mode autonomous "*|*" --permission-mode accept-edits "*)
    echo "error: invalid value for '--permission-mode <PERMISSION_MODE>': Valid options: normal (auto), dangerous (yolo, bypass)" >&2
    exit 2 ;;
esac
case " $* " in
  *" --sandbox "*)
    echo "error: unexpected argument '--sandbox' found" >&2
    exit 2 ;;
esac
case " $* " in
  *" --help "*) exit 0 ;;
esac
""",
    "broken": """\
case " $* " in
  *" --help "*) exit 1 ;;
esac
""",
}

bash = shutil.which("bash")
pytestmark = pytest.mark.skipif(bash is None, reason="bash not available")


def _make_fake_devin(bin_dir: Path, args_out: Path, surface: str) -> None:
    """A fake devin emulating `surface`'s parser; records real-run argv."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / "devin"
    p.write_text(
        "#!/bin/sh\n"
        + _FAKE_BODIES[surface]
        + f'printf "%s\\n" "$@" > "{args_out}"\n'
        + "exit 0\n"
    )
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


def _compose(tmp_path: Path, rid: str, permission: str | None, surface: str):
    """Run the real wrapper against a fake `devin` and return (rc, argv_list, stderr).

    PATH excludes `cartopian` so the access-grants step is skipped, and contains a
    real timeout/gtimeout so the wrapper's OS-deadline branch is exercised normally.
    """
    root = _project(tmp_path)
    prompt = _prompt(root, rid)
    fake_bin = tmp_path / "fakebin"
    args_out = tmp_path / "argv.txt"
    _make_fake_devin(fake_bin, args_out, surface)
    if args_out.exists():
        args_out.unlink()  # never read a previous composition's argv

    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    if tbin:
        path_parts.insert(1, str(Path(tbin).parent))
    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": "30m",
        # Keep the report-completion supervisor's poll cadence tight so the
        # composition runs return promptly (the fake assignee exits at once).
        "CARTOPIAN_REPORT_POLL": "0.1",
        "CARTOPIAN_REPORT_GRACE_POLLS": "1",
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


# --- default posture, per surface -----------------------------------------

def test_default_four_mode_composes_sandboxed_autonomous(tmp_path):
    """No env, four-mode surface → `--sandbox --permission-mode autonomous`."""
    rc, argv, err = _compose(tmp_path, "03-400", None, "four-mode")
    assert rc == 0, err
    assert "--sandbox" in argv, f"default must engage the OS sandbox; argv={argv!r}"
    assert _mode_value(argv) == "autonomous", f"argv={argv!r}"
    # --sandbox must precede the mode value it pairs with (documented invocation).
    assert argv.index("--sandbox") < argv.index("--permission-mode"), argv


def test_default_two_mode_composes_sandboxed_dangerous(tmp_path):
    """No env, two-mode (live-verified) surface → the same posture spelled
    `--sandbox --permission-mode dangerous`; the binary-rejected `autonomous`
    value must never be composed."""
    rc, argv, err = _compose(tmp_path, "03-400", None, "two-mode")
    assert rc == 0, err
    assert "--sandbox" in argv, f"default must engage the OS sandbox; argv={argv!r}"
    assert _mode_value(argv) == "dangerous", f"argv={argv!r}"
    assert argv.index("--sandbox") < argv.index("--permission-mode"), argv


def test_failed_probe_degrades_to_two_mode_value(tmp_path):
    """A surface probe that fails for an unrelated reason (broken install,
    hung-then-killed probe) must degrade to the two-mode VALUE surface — never
    guess a four-mode value. `accept-edits` (a four-mode-only value) must then
    fail closed exactly as on a positively-detected two-mode binary, proving
    the wrapper does not optimistically compose a four-mode value when the
    surface probe is inconclusive."""
    rc, argv, err = _compose(tmp_path, "03-400", "accept-edits", "broken")
    assert rc != 0, f"expected two-mode fail-closed; argv={argv!r}"
    assert "no 'accept-edits' permission mode" in err, err
    assert argv == [], f"fake devin should never have been invoked; argv={argv!r}"


def test_failed_sandbox_probe_degrades_autonomous_to_bypass(tmp_path):
    """When the binary's --help fails entirely the wrapper cannot positively
    confirm `--sandbox` parses; the sandbox-dependent `autonomous` default must
    degrade to `--permission-mode bypass` (no `--sandbox`) and still run, never
    composing an unconfirmed flag the binary might reject at launch (the
    original cryptic-failure bug)."""
    rc, argv, err = _compose(tmp_path, "03-400", None, "broken")
    assert rc == 0, err
    assert "--sandbox" not in argv, f"must not compose --sandbox; argv={argv!r}"
    assert _mode_value(argv) == "bypass", f"argv={argv!r}"


# --- only surface-valid values are ever composed ---------------------------

@pytest.mark.parametrize(
    "surface,valid",
    [("four-mode", FOUR_MODE_VALID), ("two-mode", TWO_MODE_VALID)],
)
def test_only_surface_valid_values_composed(tmp_path, surface, valid):
    """For every accepted input (incl. the default and legacy aliases), the
    composed --permission-mode value must be one the DETECTED surface parses —
    never a value that binary rejects at launch. Inputs the surface cannot
    express must fail closed instead (asserted separately)."""
    for permission in (None, "normal", "accept-edits", "bypass", "autonomous",
                       "auto", "dangerous"):
        rc, argv, err = _compose(tmp_path, "03-401", permission, surface)
        if rc != 0:
            # Fail-closed refusals compose nothing — the only acceptable
            # non-zero outcome (two-mode accept-edits; unknown values).
            assert argv == [], (
                f"{surface}: permission={permission!r} failed (rc={rc}) but "
                f"still invoked devin; argv={argv!r}; stderr={err}"
            )
            continue
        assert _mode_value(argv) in valid, (
            f"{surface}: permission={permission!r} composed a value the surface "
            f"rejects: {_mode_value(argv)!r}; argv={argv!r}"
        )


def test_four_mode_never_composes_legacy_values(tmp_path):
    """On the four-mode surface the legacy `auto`/`dangerous` spellings are not
    documented values; the wrapper must map them, never pass them through."""
    for permission in (None, "normal", "accept-edits", "bypass", "autonomous",
                       "auto", "dangerous"):
        rc, argv, err = _compose(tmp_path, "03-401", permission, "four-mode")
        assert rc == 0, f"{permission}: {err}"
        assert _mode_value(argv) not in ("auto", "dangerous"), (
            f"permission={permission!r} composed a non-four-mode value "
            f"{_mode_value(argv)!r}; argv={argv!r}"
        )


def test_two_mode_never_composes_rejected_values(tmp_path):
    """On the two-mode surface, `autonomous`/`accept-edits` are rejected at
    argv parse by the live binary — the wrapper must never compose them."""
    for permission in (None, "normal", "bypass", "autonomous", "auto", "dangerous"):
        rc, argv, err = _compose(tmp_path, "03-401", permission, "two-mode")
        assert rc == 0, f"{permission}: {err}"
        assert _mode_value(argv) not in ("autonomous", "accept-edits"), (
            f"permission={permission!r} composed a live-binary-rejected value "
            f"{_mode_value(argv)!r}; argv={argv!r}"
        )


# --- explicit modes map per surface ----------------------------------------

@pytest.mark.parametrize(
    "permission,expect_value,expect_sandbox",
    [("normal", "normal", False), ("accept-edits", "accept-edits", False),
     ("bypass", "bypass", False), ("autonomous", "autonomous", True)],
)
def test_explicit_mode_maps_to_four_mode_surface(tmp_path, permission, expect_value,
                                                 expect_sandbox):
    rc, argv, err = _compose(tmp_path, "03-402", permission, "four-mode")
    assert rc == 0, err
    assert _mode_value(argv) == expect_value, f"argv={argv!r}"
    assert ("--sandbox" in argv) is expect_sandbox, (
        f"{permission}: --sandbox presence wrong; argv={argv!r}"
    )


@pytest.mark.parametrize(
    "permission,expect_value,expect_sandbox",
    [("normal", "normal", False), ("bypass", "bypass", False),
     ("autonomous", "dangerous", True)],
)
def test_explicit_mode_maps_to_two_mode_surface(tmp_path, permission, expect_value,
                                                expect_sandbox):
    rc, argv, err = _compose(tmp_path, "03-402", permission, "two-mode")
    assert rc == 0, err
    assert _mode_value(argv) == expect_value, f"argv={argv!r}"
    assert ("--sandbox" in argv) is expect_sandbox, (
        f"{permission}: --sandbox presence wrong; argv={argv!r}"
    )


def test_accept_edits_fails_closed_on_two_mode_surface(tmp_path):
    """`accept-edits` has no two-mode equivalent: refuse before launch rather
    than passing a value the binary rejects."""
    rc, argv, err = _compose(tmp_path, "03-402", "accept-edits", "two-mode")
    assert rc != 0, f"expected fail-closed refusal; argv={argv!r}"
    assert "no 'accept-edits' permission mode" in err, err
    assert argv == [], f"fake devin should never have been invoked: argv={argv!r}"


# --- binary that predates --sandbox (operator's work CLI) ------------------

def test_no_sandbox_surface_autonomous_default_degrades_to_bypass(tmp_path):
    """On a binary that rejects `--sandbox` at parse, the default `autonomous`
    mode must degrade to `--permission-mode bypass` and still launch — never
    compose `--sandbox`, the flag the binary rejects (the reported failure:
    devin would not run). This restores the long-standing automated behavior:
    no env var required to run on a build that predates `--sandbox`."""
    rc, argv, err = _compose(tmp_path, "03-410", None, "no-sandbox")
    assert rc == 0, err
    assert "--sandbox" not in argv, f"must not compose --sandbox; argv={argv!r}"
    assert _mode_value(argv) == "bypass", f"argv={argv!r}"
    # The dropped OS boundary is surfaced, not silent.
    assert "running unsandboxed" in err, f"degrade must warn; stderr={err}"


@pytest.mark.parametrize(
    "permission,expect_value",
    [(None, "bypass"), ("autonomous", "bypass"),  # autonomous (incl. default) degrades
     ("bypass", "bypass"), ("dangerous", "bypass"),
     ("normal", "normal"), ("auto", "normal")],
)
def test_no_sandbox_surface_modes_run_unsandboxed(tmp_path, permission, expect_value):
    """Every mode runs on a no-sandbox binary: `autonomous` (and its default)
    degrades to `bypass`; the others compose unchanged. None composes
    `--sandbox` — the flag this binary would reject at launch."""
    rc, argv, err = _compose(tmp_path, "03-411", permission, "no-sandbox")
    assert rc == 0, err
    assert "--sandbox" not in argv, f"must not compose --sandbox; argv={argv!r}"
    assert _mode_value(argv) == expect_value, f"argv={argv!r}"


@pytest.mark.parametrize("surface", ["four-mode", "two-mode"])
def test_only_autonomous_engages_sandbox(tmp_path, surface):
    """`--sandbox` is composed only for the abstract autonomous mode — never
    for the approval-gate / bypass modes — on either surface."""
    for permission in ("normal", "bypass"):
        _rc, argv, _err = _compose(tmp_path, "03-403", permission, surface)
        assert "--sandbox" not in argv, f"{permission} must not engage --sandbox: {argv!r}"


# --- legacy compatibility (interface stability) --------------------------

@pytest.mark.parametrize("surface", ["four-mode", "two-mode"])
@pytest.mark.parametrize("legacy,mapped", [("auto", "normal"), ("dangerous", "bypass")])
def test_legacy_values_map_onto_surface(tmp_path, surface, legacy, mapped):
    rc, argv, err = _compose(tmp_path, "03-404", legacy, surface)
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
    rc, argv, err = _compose(tmp_path, "03-405", "garbage", "two-mode")
    assert rc != 0, f"expected non-zero exit; argv={argv!r}"
    assert "unknown CARTOPIAN_DEVIN_PERMISSION" in err, err
    assert argv == [], f"fake devin should never have been invoked; argv={argv!r}"


# --- the probe tests parser acceptance, not help prose --------------------

def test_probe_is_parser_acceptance_not_help_prose(tmp_path):
    """A two-mode parser whose --help PROSE mentions 'autonomous' (deprecation
    note, example) must still be detected as two-mode: detection keys off the
    probe's exit code, never off help-text tokens (review finding: a token
    grep was fooled by wording drift)."""
    root = _project(tmp_path)
    prompt = _prompt(root, "03-407")
    fake_bin = tmp_path / "fakebin"
    args_out = tmp_path / "argv.txt"
    fake_bin.mkdir(parents=True, exist_ok=True)
    p = fake_bin / "devin"
    # Two-mode parser + prose that would fool a token grep.
    p.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        '  *" --permission-mode autonomous "*|*" --permission-mode accept-edits "*)\n'
        '    echo "error: invalid value" >&2; exit 2 ;;\n'
        "esac\n"
        'case " $* " in\n'
        '  *" --help "*)\n'
        '    echo "NOTE: the legacy autonomous and accept-edits modes were removed."\n'
        "    exit 0 ;;\n"
        "esac\n"
        f'printf "%s\\n" "$@" > "{args_out}"\n'
        "exit 0\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    path_parts = [str(fake_bin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    if tbin:
        path_parts.insert(1, str(Path(tbin).parent))
    res = subprocess.run(
        [bash, str(WRAPPER), str(prompt)],
        capture_output=True, text=True, timeout=60,
        env={"PATH": os.pathsep.join(path_parts),
             "HOME": os.environ.get("HOME", "/tmp"),
             "CARTOPIAN_TIMEOUT": "30m",
             "CARTOPIAN_REPORT_POLL": "0.1",
             "CARTOPIAN_REPORT_GRACE_POLLS": "1"},
    )
    assert res.returncode == 0, res.stderr
    argv = args_out.read_text().splitlines() if args_out.exists() else []
    assert _mode_value(argv) == "dangerous", (
        f"prose mentioning 'autonomous' must not flip detection; argv={argv!r}"
    )
    assert "surface=two-mode" in res.stderr, res.stderr


# --- the prompt is always passed by file path ----------------------------

@pytest.mark.parametrize("surface", ["four-mode", "two-mode"])
def test_prompt_passed_by_file_path(tmp_path, surface):
    rc, argv, err = _compose(tmp_path, "03-406", None, surface)
    assert rc == 0, err
    assert "--prompt-file" in argv, argv
    assert argv[0] == "-p", f"headless print flag must lead; argv={argv!r}"
    assert argv[argv.index("--prompt-file") + 1].endswith("PROMPT-03-406.md"), argv
