"""Process-tree bounds for the streaming-capture subprocess helper.

`run_bounded` must bound the child's whole tree, not just the immediate
process: a grandchild that inherited the stdout/stderr pipes keeps their
write ends open, so an immediate-process kill leaves the reader threads
blocked until the descendant exits on its own — a one-second timeout that
takes twenty seconds, or a persistent daemon that hangs the caller forever.
These tests pin the tree kill on timeout and overflow, and the bounded
reader joins after a clean exit with a leaked pipe-holder.
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from cli.bounded_run import CaptureOverflow, run_bounded

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX process-group semantics"
)


def _script(tmp_path: Path, body: str) -> str:
    script = tmp_path / "child.sh"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(
        script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
    )
    return str(script)


def test_plain_capture_round_trips(tmp_path):
    argv = [_script(tmp_path, "echo out\necho err >&2\nexit 7\n")]
    code, stdout, stderr = run_bounded(argv, timeout=10, max_bytes=65536)
    assert code == 7
    assert stdout == b"out\n"
    assert stderr == b"err\n"


def test_timeout_kills_the_pipe_holding_descendant_too(tmp_path):
    """Regression: with an immediate-process kill, the shell died at the
    one-second timeout but its `sleep` child retained the pipes, so the
    reader joins blocked for the full sleep (observed 20.51s for a 1s
    timeout). The tree kill must end the whole group at the timeout."""
    argv = [_script(tmp_path, "sleep 20 &\nsleep 20\n")]
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_bounded(argv, timeout=1, max_bytes=65536)
    assert time.monotonic() - started < 10


def test_clean_exit_with_a_leaked_pipe_holder_returns_bounded(tmp_path):
    """A child that exits cleanly but leaves a descendant holding the pipes
    must not stall the caller: everything the child wrote is returned after
    a bounded grace, and the leftover process is not waited on (it may be a
    service the CLI started on purpose, so it is not killed either)."""
    argv = [_script(tmp_path, "echo hello\nsleep 30 &\nexit 0\n")]
    started = time.monotonic()
    code, stdout, _stderr = run_bounded(argv, timeout=10, max_bytes=65536)
    assert time.monotonic() - started < 8
    assert code == 0
    assert stdout == b"hello\n"


def test_flooding_grandchild_is_killed_at_the_capture_bound(tmp_path):
    """The overflow kill must reach a flooding descendant: killing only the
    immediate shell would leave the flood running and the drain threads
    spinning on it until some other limit fired."""
    flood = (
        "( while :; do printf "
        "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; done ) &\n"
        "wait\n"
    )
    argv = [_script(tmp_path, flood)]
    started = time.monotonic()
    with pytest.raises(CaptureOverflow):
        run_bounded(argv, timeout=30, max_bytes=4096)
    assert time.monotonic() - started < 10
