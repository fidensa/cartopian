"""CARTOPIAN_CODEX_DAYBREAK wrapper-translation contract tests.

`roles.<role>.daybreak = true` is exported by `cartopian dispatch` as
`CARTOPIAN_CODEX_DAYBREAK=true`. cartopian-codex turns it into
`--enable cli_daybreak --cyber-access-program daybreak_blue`; anything other
than `true` (including unset) adds neither flag. Other wrappers ignore it.

The bash wrapper is exercised live with a fake `codex` capturing argv (reusing
the test_effort_flag.py harness); the PowerShell mirror is asserted
statically, the project's standing posture for PS1 wrappers.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.wrappers.test_effort_flag import _captured, bash

REPO_ROOT = Path(__file__).resolve().parents[2]
PS1_CODEX = REPO_ROOT / "wrappers" / "ps1" / "cartopian-codex.ps1"

DAYBREAK_ARGV = ["--enable", "cli_daybreak", "--cyber-access-program", "daybreak_blue"]
DAYBREAK_TOKENS = ("cli_daybreak", "--cyber-access-program", "daybreak_blue")

needs_bash = pytest.mark.skipif(bash is None, reason="bash not available")


def _contains_run(received: list[str], run: list[str]) -> bool:
    return any(received[i:i + len(run)] == run for i in range(len(received)))


@needs_bash
def test_codex_daybreak_true_adds_feature_and_access_program(tmp_path):
    received, stderr = _captured(
        tmp_path, "cartopian-codex", None, {"CARTOPIAN_CODEX_DAYBREAK": "true"}
    )
    assert _contains_run(received, DAYBREAK_ARGV), f"argv={received!r}"
    # Flags precede the trailing prompt path positional.
    assert received.index("--enable") < len(received) - 1
    assert "daybreak=on" in stderr, f"stderr={stderr!r}"
    assert "Trusted Access for Cyber" in stderr, f"stderr={stderr!r}"


@needs_bash
@pytest.mark.parametrize("value", [None, "false", "TRUE", "1"])
def test_codex_daybreak_off_adds_nothing(tmp_path, value):
    extra = None if value is None else {"CARTOPIAN_CODEX_DAYBREAK": value}
    received, stderr = _captured(tmp_path, "cartopian-codex", None, extra)
    for token in DAYBREAK_TOKENS:
        assert not any(token in arg for arg in received), f"argv={received!r}"
    assert "daybreak=on" not in stderr, f"stderr={stderr!r}"


@needs_bash
@pytest.mark.parametrize("wrapper", ["cartopian-claude", "cartopian-devin"])
def test_other_wrappers_ignore_daybreak(tmp_path, wrapper):
    received, _ = _captured(
        tmp_path, wrapper, None, {"CARTOPIAN_CODEX_DAYBREAK": "true"}
    )
    for token in DAYBREAK_TOKENS:
        assert not any(token in arg for arg in received), f"argv={received!r}"


def test_ps1_codex_daybreak_parity():
    text = PS1_CODEX.read_text(encoding="utf-8")
    assert (
        "$Daybreak = if ($env:CARTOPIAN_CODEX_DAYBREAK -eq 'true') "
        "{ $true } else { $false }"
    ) in text
    guard = text.find("if ($Daybreak) {")
    assert guard != -1, "missing if ($Daybreak) guard"
    append = (
        "$Args += @('--enable', 'cli_daybreak', "
        "'--cyber-access-program', 'daybreak_blue')"
    )
    assert text.count(append) == 1
    block_end = text.find("}", text.find(append))
    assert guard < text.find(append) < block_end
    notice = text.find("[Console]::Error.WriteLine", guard)
    assert guard < notice < block_end, "daybreak notice must go to stderr"
    assert "Trusted Access for Cyber" in text[guard:block_end]
    assert text.find("$Args += $PromptPathAbs") > block_end, (
        "daybreak flags must precede the trailing prompt positional"
    )
