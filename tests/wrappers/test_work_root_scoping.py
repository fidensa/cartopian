"""Native per-tool work-root union scoping in the wrappers (TASK-03-009, P03-FIX-005).

TASK-03-008 made the work-root guard *fire* (fail closed on a non-empty resolved
work-root set). This task makes it *scope*: a tool whose sandbox can scope a
multi-directory union natively is launched **scoped to the union** (launch cwd +
each declared work-root absolute path) instead of failing closed — so a normal
coder work-root task no longer needs the blanket ``CARTOPIAN_<AGENT>_UNRESTRICTED``
bypass (DEC-006). A tool that genuinely cannot scope still fails closed.

The scoping is driven by the shared helper (``cartopian_enforce_work_roots``):
each wrapper opts in by defining a ``cartopian_tool_scope_union`` hook that maps
the union onto the tool's native multi-directory flags, which the wrapper injects
into its command. A new wrapper inherits the mechanism by defining that hook; a
wrapper with no hook (Devin) stays fail-closed-or-bypass.

No live model is needed. A fake ``cartopian`` shim stubs ``resolve-config`` and a
fake assignee binary records the exact argv it is launched with, so the test can
assert the native scoping flags carry the resolved union. ("Writes inside the
union succeed; writes outside are refused" is the documented semantics of those
native flags — ``--add-dir`` / ``--include-directories`` confine the writable
scope to the launch cwd plus the named roots; this test pins that the wrapper
passes exactly that union to the tool, with no bypass.)
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "wrappers" / "bin"

# Tools whose sandbox can scope a multi-directory union natively, and the native
# flag the wrapper must emit to carry each resolved work root.
#   (wrapper, upstream CLI, scope-flag, joins-roots-comma-separated?)
SCOPABLE = [
    ("cartopian-claude", "claude", "--add-dir", False),
    ("cartopian-codex", "codex", "--add-dir", False),
    ("cartopian-gemini", "gemini", "--include-directories", True),
]

# Tools with no local path-scoping mechanism: they must stay fail-closed-or-bypass.
UNSCOPABLE = [
    ("cartopian-devin", "devin", "CARTOPIAN_DEVIN_UNRESTRICTED"),
]

CONFIG_BODY = """[project]
work_roots = ["product", "design"]

[roles]
coder = "Implements tasks per spec."
"""


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "prompts").mkdir(parents=True)
    (project / "cartopian.toml").write_text(CONFIG_BODY, encoding="utf-8")
    prompt = project / "prompts" / "PROMPT-03-009.md"
    prompt.write_text("do the thing\n", encoding="utf-8")
    return prompt


def _shim(dir_path: Path, name: str, body: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(wrapper, tool, prompt, roots, *, argv_file=None, extra_env=None):
    """Run a wrapper with a fake ``cartopian`` resolving ``roots`` (a dict
    name->abs path) and a fake assignee that records its argv to ``argv_file``.
    PATH carries only the fakes + core utils (+ a real timeout if available).
    """
    fakebin = prompt.parent.parent.parent / "fakebin"
    if argv_file is not None:
        # The fake assignee writes one argv token per line, then exits 0.
        _shim(fakebin, tool, f'for a in "$@"; do printf "%s\\n" "$a"; done > "{argv_file}"\nexit 0')
    else:
        _shim(fakebin, tool, "exit 0")

    # resolve-config emits a single JSON line declaring the work_roots mapping.
    pairs = ", ".join(f'"{n}": "{p}"' for n, p in roots.items())
    _shim(
        fakebin,
        "cartopian",
        f'if [ "$1" = "resolve-config" ]; then '
        f"printf '%s\\n' '{{\"work_roots\": {{{pairs}}}}}'; fi",
    )

    path_parts = [str(fakebin), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    tbin = shutil.which("timeout") or shutil.which("gtimeout")
    if tbin:
        path_parts.insert(1, str(Path(tbin).parent))

    env = {
        "PATH": os.pathsep.join(path_parts),
        "HOME": os.environ.get("HOME", "/tmp"),
        "CARTOPIAN_TIMEOUT": "60m",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(BIN_DIR / wrapper), str(prompt)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# --- GREEN: a scopable tool launches scoped to the union, with NO bypass ------


@pytest.mark.parametrize("wrapper,tool,flag,comma_join", SCOPABLE)
def test_scopable_tool_launches_scoped_without_bypass(tmp_path, wrapper, tool, flag, comma_join):
    """The wrapper grants the resolved union to the tool natively and launches —
    no ``UNRESTRICTED`` bypass — passing the launch cwd + each declared work root
    via the tool's native multi-directory flag."""
    prompt = _make_project(tmp_path)
    product = tmp_path / "product"
    design = tmp_path / "design"
    product.mkdir()
    design.mkdir()
    argv_file = tmp_path / "argv.txt"

    proc = _run(
        wrapper, tool, prompt,
        {"product": str(product), "design": str(design)},
        argv_file=argv_file,
    )

    # It launched (exit 0) rather than failing closed, and used NO bypass.
    assert proc.returncode == 0, (
        f"{wrapper}: did not launch scoped (rc={proc.returncode})\nstderr:\n{proc.stderr}"
    )
    assert "tool cannot scope multi-root access" not in proc.stderr, (
        f"{wrapper}: still fails closed despite native scoping\nstderr:\n{proc.stderr}"
    )
    assert "unrestricted mode enabled" not in proc.stderr, (
        f"{wrapper}: launched via the bypass, not native scoping\nstderr:\n{proc.stderr}"
    )

    argv = argv_file.read_text(encoding="utf-8").splitlines() if argv_file.exists() else []
    assert flag in argv, f"{wrapper}: native scope flag {flag} not passed\nargv:\n{argv}"

    # The union (each declared work root) is carried by the native flag.
    if comma_join:
        # Gemini: one --include-directories with the roots comma-joined.
        idx = argv.index(flag)
        value = argv[idx + 1]
        assert str(product) in value and str(design) in value, (
            f"{wrapper}: roots not in {flag} value: {value!r}"
        )
    else:
        # Claude / Codex: each root carried by its own --add-dir occurrence.
        for root in (str(product), str(design)):
            assert root in argv, f"{wrapper}: work root {root} not scoped\nargv:\n{argv}"


@pytest.mark.parametrize("wrapper,tool,flag,comma_join", SCOPABLE)
def test_scoped_launch_notes_the_union(tmp_path, wrapper, tool, flag, comma_join):
    """The scoped launch is operator-visible: it names each scoped work root."""
    prompt = _make_project(tmp_path)
    product = tmp_path / "product"
    design = tmp_path / "design"
    product.mkdir()
    design.mkdir()

    proc = _run(wrapper, tool, prompt, {"product": str(product), "design": str(design)})

    assert "scoped work root:" in proc.stderr, (
        f"{wrapper}: no scoped-union note\nstderr:\n{proc.stderr}"
    )
    assert str(product) in proc.stderr and str(design) in proc.stderr


# --- Genuine fail-closed preserved for a tool that cannot scope ---------------


@pytest.mark.parametrize("wrapper,tool,var", UNSCOPABLE)
def test_unscopable_tool_still_fails_closed(tmp_path, wrapper, tool, var):
    prompt = _make_project(tmp_path)
    product = tmp_path / "product"
    design = tmp_path / "design"
    product.mkdir()
    design.mkdir()

    proc = _run(wrapper, tool, prompt, {"product": str(product), "design": str(design)})

    assert proc.returncode != 0, (
        f"{wrapper}: did NOT fail closed despite no native scoping\nstderr:\n{proc.stderr}"
    )
    assert "[work-root] tool cannot scope multi-root access" in proc.stderr


@pytest.mark.parametrize("wrapper,tool,var", UNSCOPABLE)
def test_unscopable_tool_unrestricted_bypass_still_works(tmp_path, wrapper, tool, var):
    """The documented per-tool opt-out is preserved for an unscopable tool."""
    prompt = _make_project(tmp_path)
    product = tmp_path / "product"
    product.mkdir()

    proc = _run(
        wrapper, tool, prompt, {"product": str(product), "design": str(product)},
        extra_env={var: "true"},
    )

    assert proc.returncode == 0, f"{wrapper}: bypass did not proceed\nstderr:\n{proc.stderr}"
    assert "unrestricted mode enabled" in proc.stderr


# --- A scopable tool's bypass and recapture paths are preserved unchanged -----


@pytest.mark.parametrize("wrapper,tool,flag,comma_join", SCOPABLE)
def test_scopable_tool_bypass_still_takes_precedence(tmp_path, wrapper, tool, flag, comma_join):
    """Setting the per-tool UNRESTRICTED bypass remains the documented opt-out:
    it takes precedence over native scoping (explicit full-access override)."""
    prompt = _make_project(tmp_path)
    product = tmp_path / "product"
    product.mkdir()
    var = f"CARTOPIAN_{tool.upper()}_UNRESTRICTED"

    proc = _run(
        wrapper, tool, prompt, {"product": str(product), "design": str(product)},
        extra_env={var: "true"},
    )

    assert proc.returncode == 0, f"{wrapper}: bypass did not proceed\nstderr:\n{proc.stderr}"
    assert "unrestricted mode enabled" in proc.stderr


@pytest.mark.parametrize("wrapper,tool,flag,comma_join", SCOPABLE)
def test_scopable_tool_recapture_stays_readonly(tmp_path, wrapper, tool, flag, comma_join):
    """Under reviewer recapture the source work root stays READ-ONLY: scoping must
    NOT widen it — no native writable-scope flag is emitted for the source root."""
    prompt = _make_project(tmp_path)
    product = tmp_path / "product"
    product.mkdir()
    argv_file = tmp_path / "argv.txt"

    proc = _run(
        wrapper, tool, prompt, {"product": str(product), "design": str(product)},
        argv_file=argv_file,
        extra_env={"CARTOPIAN_REVIEW_RECAPTURE": "1"},
    )

    assert proc.returncode == 0, f"{wrapper}: recapture path regressed\nstderr:\n{proc.stderr}"
    assert "read-only source work root:" in proc.stderr
    argv = argv_file.read_text(encoding="utf-8").splitlines() if argv_file.exists() else []
    # The source root is NOT granted as a writable scope under recapture.
    assert flag not in argv, (
        f"{wrapper}: recapture widened writable scope with {flag}\nargv:\n{argv}"
    )
