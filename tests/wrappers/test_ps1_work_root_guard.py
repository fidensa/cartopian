"""Regression: PS1 work-root guards must fail closed, not be swallowed.

Background
----------
The PowerShell wrappers read resolved work-root paths via
``cartopian resolve-config`` and enforce two security-critical guards before
launching the assignee:

* **resolved-work-root-missing** — a configured work-root directory that does
  not exist on disk (``[work-root] missing: <path>``); and
* **unrestricted-required** — a multi-root config the tool cannot scope
  (``[work-root] tool cannot scope multi-root access ...``).

Both must fail closed with ``exit 1``. An earlier deterministic-emission change
wrapped the parsed validation in ``try { ... } catch {}``. Because the wrappers
set ``$ErrorActionPreference = 'Stop'``, the guards' ``Write-Error`` calls
raise *terminating* errors that the broad empty ``catch {}`` swallowed *before*
``exit 1`` ran — so the wrapper launched the assignee anyway.

``pwsh`` is not available on this host, so this is a *static* parity assertion
(the project's standing posture for PS1 wrappers — see the static checks in
``test_wrapper_status_file.py``). It brace-matches each wrapper and asserts the
two guard messages are NOT enclosed in a ``try`` block whose paired ``catch`` is
empty (a swallow-all). It also asserts the intended parse tolerance is
preserved: a ``ConvertFrom-Json`` failure for an unregistered/ad-hoc layout is
still caught so the ``<report>.status`` file is emitted deterministically.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PS1_DIR = REPO_ROOT / "wrappers" / "ps1"

PS1_WRAPPERS = [
    "cartopian-codex.ps1",
    "cartopian-claude.ps1",
    "cartopian-gemini.ps1",
    "cartopian-devin.ps1",
]


def _effective_text(wrapper: str) -> str:
    """The wrapper's source plus the shared helper it dot-sources at runtime.

    The work-root guards (resolve-config fallback, missing-root and
    cannot-scope fail-closed exits, ConvertFrom-Json parse tolerance) are
    factored into ``CartopianStatus.ps1 :: Get-CartopianScopeArgs`` for the
    wrappers that delegate to it, so the static guard assertions must read the
    effective source (wrapper + helper), not the wrapper file alone. A wrapper
    that still inlines the guard keeps its own copy first in the concatenation.
    """
    text = (PS1_DIR / wrapper).read_text(encoding="utf-8")
    helper = (PS1_DIR / "CartopianStatus.ps1").read_text(encoding="utf-8")
    return text + "\n" + helper


# The two security-critical guard messages every wrapper must fail closed on.
MISSING_GUARD = "[work-root] missing:"
UNRESTRICTED_GUARD = "[work-root] tool cannot scope multi-root access"


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


def _swallowing_try_body_spans(text: str):
    """Spans ``(start, end)`` of every ``try`` body paired with an empty ``catch``.

    A ``catch`` whose body is whitespace-only is a swallow-all: any terminating
    error raised inside the matching ``try`` (e.g. ``Write-Error`` under
    ``$ErrorActionPreference = 'Stop'``) is silently discarded.
    """
    spans = []
    for m in re.finditer(r"\btry\b", text):
        try_open = text.find("{", m.end())
        if try_open == -1:
            continue
        try_close = _matching_brace(text, try_open)
        if try_close == -1:
            continue
        # Require a `catch` immediately after the try body (optionally typed).
        tail = text[try_close + 1:]
        if not re.match(r"\s*catch\b", tail):
            continue
        catch_open = text.find("{", try_close + 1)
        if catch_open == -1:
            continue
        catch_close = _matching_brace(text, catch_open)
        if catch_close == -1:
            continue
        catch_body = text[catch_open + 1:catch_close]
        if catch_body.strip() == "":
            spans.append((try_open, try_close))
    return spans


def _is_swallowed(text: str, marker: str) -> bool:
    idx = text.find(marker)
    assert idx != -1, f"guard marker not found: {marker!r}"
    return any(start < idx < end for start, end in _swallowing_try_body_spans(text))


@pytest.mark.parametrize("wrapper", PS1_WRAPPERS)
def test_missing_work_root_guard_not_swallowed(wrapper):
    text = _effective_text(wrapper)
    assert not _is_swallowed(text, MISSING_GUARD), (
        f"{wrapper}: resolved-work-root-missing guard is inside a try block whose "
        f"catch is empty; the terminating Write-Error would be swallowed before "
        f"exit 1 (fail-closed contract broken)."
    )


@pytest.mark.parametrize("wrapper", PS1_WRAPPERS)
def test_unrestricted_required_guard_not_swallowed(wrapper):
    text = _effective_text(wrapper)
    assert not _is_swallowed(text, UNRESTRICTED_GUARD), (
        f"{wrapper}: unrestricted-required guard is inside a try block whose "
        f"catch is empty; the terminating Write-Error would be swallowed before "
        f"exit 1 (fail-closed contract broken)."
    )


@pytest.mark.parametrize("wrapper", PS1_WRAPPERS)
def test_guard_exit_paths_present(wrapper):
    """Both guards still exist and still fail closed with exit 1."""
    text = _effective_text(wrapper)
    for marker in (MISSING_GUARD, UNRESTRICTED_GUARD):
        idx = text.find(marker)
        assert idx != -1, f"{wrapper}: missing guard {marker!r}"
        # An `exit 1` must follow the guard message before the next guard/block.
        assert re.search(r"exit\s+1", text[idx:idx + 400]), (
            f"{wrapper}: no `exit 1` follows guard {marker!r}"
        )


@pytest.mark.parametrize("wrapper", PS1_WRAPPERS)
def test_resolve_config_parse_tolerance_preserved(wrapper):
    """A ConvertFrom-Json failure (ad-hoc/unregistered layout) is still tolerated.

    The reason the catch was introduced: deterministic ``<report>.status``
    emission for handoffs whose ``cartopian resolve-config`` is missing/non-zero
    or returns non-JSON. That tolerance must remain — only the security guards
    must escape the catch.
    """
    text = _effective_text(wrapper)
    assert "ConvertFrom-Json" in text
    # ConvertFrom-Json must sit inside a try (so malformed config is tolerated).
    cfj = text.find("ConvertFrom-Json")
    spans = []
    for m in re.finditer(r"\btry\b", text):
        try_open = text.find("{", m.end())
        if try_open == -1:
            continue
        try_close = _matching_brace(text, try_open)
        if try_close == -1:
            continue
        spans.append((try_open, try_close))
    assert any(start < cfj < end for start, end in spans), (
        f"{wrapper}: ConvertFrom-Json is no longer guarded; a malformed "
        f"resolve-config would crash before the .status file is emitted."
    )


# PowerShell scope/namespace qualifiers that legitimately follow `$name:`.
_PS_SCOPES = {
    "env", "global", "script", "local", "using", "private", "variable",
    "function", "workflow",
}
_VAR_COLON_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*):")


def test_no_unguarded_variable_colon_in_ps1():
    """`"$Name:..."` parses `Name:` as a scope/drive qualifier in PowerShell, not
    `$Name` + literal `:` — a parse error that breaks dot-sourcing of the file
    (and every wrapper that sources it). The fix is `"${Name}:..."`. This static
    guard catches the class because `pwsh` is unavailable on this host to parse
    the scripts directly. (Found in the wild on a Windows acceptance run.)"""
    offenders = []
    for path in sorted(PS1_DIR.glob("*.ps1")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in _VAR_COLON_RE.finditer(line):
                if m.group(1).lower() not in _PS_SCOPES:
                    offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, (
        "Unguarded `$Name:` — PowerShell reads `Name:` as a scope qualifier; "
        "use `${Name}:` instead:\n" + "\n".join(offenders)
    )
