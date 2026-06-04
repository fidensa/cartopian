"""Static parity: PS1 wrappers translate CARTOPIAN_MODEL like the bash wrappers.

``pwsh`` is not available on this host, so this is a *static* parity assertion
(the project's standing posture for PS1 wrappers — see
``test_ps1_work_root_guard.py``). The bash wrappers' CARTOPIAN_MODEL contract is
exercised live by ``test_model_flag.py``; this file asserts the PowerShell
mirrors hold the same argv invariants:

* every PS1 wrapper appends ``--model $env:CARTOPIAN_MODEL`` ONLY inside an
  ``if ($env:CARTOPIAN_MODEL)`` guard (unset → no model flag, tool default);
* the model block precedes the wrapper's trailing positional/prompt append, so
  the flag-value pair can never be split by the positional argument; and
* ``cartopian-devin.ps1``'s restructured argv keeps the unconditional
  ``--prompt-file`` append after both the permission-mode branch and the model
  block — the argv with CARTOPIAN_MODEL unset is identical to the
  pre-restructure single-line arrays.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PS1_DIR = REPO_ROOT / "wrappers" / "ps1"

MODEL_GUARD = "if ($env:CARTOPIAN_MODEL) {"
MODEL_APPEND = "$Args += @('--model', $env:CARTOPIAN_MODEL)"

# Each wrapper -> the trailing append that must come AFTER the model block so
# the underlying CLI receives `--model <value>` before its positional/prompt.
PS1_TAIL_APPEND = {
    "cartopian-claude.ps1": "$Args += $PromptContent",
    "cartopian-codex.ps1": "$Args += $PromptContent",
    "cartopian-gemini.ps1": "$Args += @('-p', $PromptContent)",
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


def _model_block_span(text: str, wrapper: str):
    """Return the (start, end) span of the guarded CARTOPIAN_MODEL block."""
    guard_idx = text.find(MODEL_GUARD)
    assert guard_idx != -1, f"{wrapper}: missing {MODEL_GUARD!r}"
    open_idx = text.find("{", guard_idx)
    close_idx = _matching_brace(text, open_idx)
    assert close_idx != -1, f"{wrapper}: unbalanced braces in CARTOPIAN_MODEL block"
    return open_idx, close_idx


@pytest.mark.parametrize("wrapper", sorted(PS1_TAIL_APPEND))
def test_model_append_only_inside_env_guard(wrapper):
    """`--model` is appended exactly once, inside the CARTOPIAN_MODEL guard —
    unset means no model flag reaches the underlying CLI."""
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    start, end = _model_block_span(text, wrapper)
    appends = [m.start() for m in re.finditer(re.escape(MODEL_APPEND), text)]
    assert len(appends) == 1, (
        f"{wrapper}: expected exactly one {MODEL_APPEND!r}; found {len(appends)}"
    )
    assert start < appends[0] < end, (
        f"{wrapper}: the --model append is outside the if "
        f"($env:CARTOPIAN_MODEL) guard; an unset model would still inject a flag"
    )


@pytest.mark.parametrize("wrapper", sorted(PS1_TAIL_APPEND))
def test_model_block_precedes_trailing_append(wrapper):
    """The model block sits before the wrapper's trailing positional/prompt
    append, matching the bash wrappers' flag-before-positional ordering."""
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    _, block_end = _model_block_span(text, wrapper)
    tail = PS1_TAIL_APPEND[wrapper]
    tail_idx = text.find(tail)
    assert tail_idx != -1, f"{wrapper}: missing trailing append {tail!r}"
    assert tail_idx > block_end, (
        f"{wrapper}: trailing append {tail!r} precedes the CARTOPIAN_MODEL "
        f"block; --model would land after the positional argument"
    )


def test_devin_prompt_file_append_is_unconditional():
    """cartopian-devin.ps1 argv identity: --prompt-file is appended on every
    path (top-level statement, not inside any if/else), so with
    CARTOPIAN_MODEL unset both permission-mode branches reproduce the
    pre-restructure argv exactly."""
    text = (PS1_DIR / "cartopian-devin.ps1").read_text(encoding="utf-8")
    tail = PS1_TAIL_APPEND["cartopian-devin.ps1"]
    lines = text.splitlines()
    matches = [ln for ln in lines if tail in ln]
    assert len(matches) == 1, f"expected exactly one --prompt-file append; got {matches!r}"
    assert matches[0] == tail, (
        f"--prompt-file append is indented (inside a block), so one branch "
        f"could skip it: {matches[0]!r}"
    )
    # Both permission-mode branches must still seed $Args before it.
    assert "'--permission-mode', 'autonomous')" in text
    assert "'--permission-mode', $PermissionMode)" in text
