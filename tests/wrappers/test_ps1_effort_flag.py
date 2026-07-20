"""Static parity: PS1 wrappers translate CARTOPIAN_EFFORT like the bash wrappers.

``pwsh`` is not available on this host, so this is a *static* parity assertion
(the project's standing posture for PS1 wrappers — see
``test_ps1_model_flag.py``). The bash wrappers' CARTOPIAN_EFFORT contract is
exercised live by ``test_effort_flag.py``; this file asserts the PowerShell
mirrors hold the same invariants:

* claude/codex append their effort flag ONLY inside an
  ``if ($env:CARTOPIAN_EFFORT)`` guard, after lowercasing and checking the
  CLI-wide vocabulary (unset or out-of-vocabulary → no flag, tool default);
* the effort block precedes the wrapper's trailing positional/prompt append,
  so the flag-value pair can never be split by the positional argument;
* gemini/devin never append an effort flag — inside their guard there is only
  the ignore notice; and
* every fallback/ignore path emits a stderr notice naming CARTOPIAN_EFFORT.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PS1_DIR = REPO_ROOT / "wrappers" / "ps1"

EFFORT_GUARD = "if ($env:CARTOPIAN_EFFORT) {"
LOWERCASE = "$env:CARTOPIAN_EFFORT.ToLowerInvariant()"

# Wrapper -> (effort append inside the guard, CLI-wide vocabulary literal).
TRANSLATING = {
    "cartopian-claude.ps1": (
        "$Args += @('--effort', $EffortLc)",
        "@('low', 'medium', 'high', 'xhigh', 'max')",
    ),
    "cartopian-codex.ps1": (
        "$Args += @('-c', \"model_reasoning_effort=$EffortLc\")",
        "@('low', 'medium', 'high', 'xhigh', 'max', 'ultra')",
    ),
}
IGNORING = ["cartopian-gemini.ps1", "cartopian-devin.ps1"]

# Each wrapper -> the trailing append that must come AFTER the effort block so
# the underlying CLI receives the effort flag before its positional/prompt.
PS1_TAIL_APPEND = {
    "cartopian-claude.ps1": "$Args += $PromptPathAbs",
    "cartopian-codex.ps1": "$Args += $PromptPathAbs",
    "cartopian-gemini.ps1": "$Args += @('-p', $PromptPathAbs)",
    "cartopian-devin.ps1": "$Args += @('--prompt-file', $PromptPathAbs)",
}


def _matching_brace(text: str, open_idx: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at ``open_idx`` (or -1)."""
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _effort_block_span(text: str, wrapper: str):
    """Return the (start, end) span of the guarded CARTOPIAN_EFFORT block."""
    guard_idx = text.find(EFFORT_GUARD)
    assert guard_idx != -1, f"{wrapper}: missing {EFFORT_GUARD!r}"
    open_idx = text.find("{", guard_idx)
    close_idx = _matching_brace(text, open_idx)
    assert close_idx != -1, f"{wrapper}: unbalanced braces in CARTOPIAN_EFFORT block"
    return open_idx, close_idx


@pytest.mark.parametrize("wrapper", sorted(TRANSLATING))
def test_effort_append_only_inside_env_guard(wrapper):
    """The effort flag is appended exactly once, inside the CARTOPIAN_EFFORT
    guard — unset means no effort flag reaches the underlying CLI."""
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    start, end = _effort_block_span(text, wrapper)
    append, _vocab = TRANSLATING[wrapper]
    appends = [m.start() for m in re.finditer(re.escape(append), text)]
    assert len(appends) == 1, (
        f"{wrapper}: expected exactly one {append!r}; found {len(appends)}"
    )
    assert start < appends[0] < end, (
        f"{wrapper}: the effort append is outside the if "
        f"($env:CARTOPIAN_EFFORT) guard; an unset effort would still inject a flag"
    )


@pytest.mark.parametrize("wrapper", sorted(TRANSLATING))
def test_effort_is_lowercased_and_vocabulary_checked(wrapper):
    """The value is case-normalized and checked against the CLI-wide
    vocabulary inside the guard, with a fallback notice on the reject path."""
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    start, end = _effort_block_span(text, wrapper)
    block = text[start:end]
    _append, vocab = TRANSLATING[wrapper]
    assert LOWERCASE in block, f"{wrapper}: effort value is not lowercased"
    assert vocab in block, (
        f"{wrapper}: expected CLI-wide vocabulary {vocab!r} in the effort block"
    )
    assert "launching with the default effort" in block, (
        f"{wrapper}: missing the warn-and-omit fallback notice"
    )
    assert "[Console]::Error.WriteLine" in block, (
        f"{wrapper}: fallback notice must go to stderr"
    )


@pytest.mark.parametrize("wrapper", sorted(PS1_TAIL_APPEND))
def test_effort_block_precedes_trailing_append(wrapper):
    """The effort block sits before the wrapper's trailing positional/prompt
    append, matching the bash wrappers' flag-before-positional ordering."""
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    _, block_end = _effort_block_span(text, wrapper)
    tail = PS1_TAIL_APPEND[wrapper]
    tail_idx = text.find(tail)
    assert tail_idx != -1, f"{wrapper}: missing trailing append {tail!r}"
    assert tail_idx > block_end, (
        f"{wrapper}: trailing append {tail!r} precedes the CARTOPIAN_EFFORT "
        f"block; the effort flag would land after the positional argument"
    )


@pytest.mark.parametrize("wrapper", IGNORING)
def test_unsupported_cli_never_appends_effort(wrapper):
    """gemini/devin have no effort/thinking flag: their guard contains only
    the stderr ignore notice, never an $Args append."""
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    start, end = _effort_block_span(text, wrapper)
    block = text[start:end]
    assert "$Args +=" not in block, (
        f"{wrapper}: appends argv inside the CARTOPIAN_EFFORT guard, but the "
        f"underlying CLI has no effort flag"
    )
    assert "ignoring CARTOPIAN_EFFORT" in block, (
        f"{wrapper}: missing the ignore notice"
    )
    assert "[Console]::Error.WriteLine" in block, (
        f"{wrapper}: ignore notice must go to stderr"
    )
