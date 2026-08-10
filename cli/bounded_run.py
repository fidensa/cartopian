"""Run a subprocess with capture bounds enforced while it runs.

``subprocess.run(capture_output=True)`` buffers a child's complete stdout and
stderr before the caller can inspect their sizes, so a flooding process can
exhaust memory long before any post-hoc length check fires. This helper reads
both pipes incrementally, kills the child the moment either stream exceeds the
byte bound, and only then reports the overflow — the bound is enforced during
capture, not after it.

The bound covers the child's whole process tree, not just the immediate
process: the child runs as its own session leader (POSIX), so a timeout or
overflow kills the group, and a grandchild that inherited the pipes cannot
keep the reader threads blocked past the kill. Every reader join is bounded —
a descendant this process cannot kill delays the return by a grace period,
never hangs it.

Used by every Hermes CLI invocation (registration adapter and host-capability
resolver alike); stdin is always closed because under MCP-hosted execution
this process's stdin is the protocol pipe.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

_CHUNK_BYTES = 65536

# Once the child's tree is killed, its pipe write ends close and the readers
# hit EOF within milliseconds; the grace period only bounds the pathological
# unkillable-descendant case, after which the daemon readers are abandoned
# instead of joined forever.
_REAP_GRACE_SECONDS = 5.0

# After a clean child exit, readers that are still alive are blocked on a pipe
# a leaked descendant kept open — everything the child itself wrote was
# already readable without blocking. A short grace separates that state from
# ordinary scheduling delay before the readers are abandoned.
_ORPHAN_PIPE_GRACE_SECONDS = 1.0


class CaptureOverflow(Exception):
    """A stream exceeded the capture bound; the child was killed."""

    def __init__(self, stream: str, max_bytes: int) -> None:
        self.stream = stream
        self.max_bytes = max_bytes
        super().__init__(
            f"{stream} exceeded the {max_bytes}-byte capture bound"
        )


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill the child and every descendant sharing its pipes.

    Killing only the immediate process leaves a grandchild's inherited pipe
    write ends open, so the reader threads stay blocked until the descendant
    exits on its own. The child is launched as a session leader on POSIX, so
    its process group is exactly its tree; Windows walks the tree with
    ``taskkill /T`` instead.
    """
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    else:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_REAP_GRACE_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        process.kill()
    except OSError:
        pass


def _join_readers(readers: Sequence[threading.Thread], grace: float) -> bool:
    """Join every reader within one shared grace budget; True when all ended."""
    deadline = time.monotonic() + grace
    for reader in readers:
        reader.join(max(0.0, deadline - time.monotonic()))
    return all(not reader.is_alive() for reader in readers)


def run_bounded(
    argv: Sequence[str],
    *,
    timeout: float,
    max_bytes: int,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[int, bytes, bytes]:
    """Run ``argv`` shell-free with a hard timeout and streaming capture bound.

    Returns ``(returncode, stdout, stderr)``. Raises
    :class:`subprocess.TimeoutExpired` when the child outlives ``timeout``
    (its whole process tree is killed first), :class:`CaptureOverflow` when
    either stream passes ``max_bytes`` (likewise killed first), and
    :class:`OSError` when the child cannot be launched at all. When the child
    exits cleanly but a leaked descendant keeps the pipes open, the call
    returns after a bounded grace with everything captured so far rather than
    waiting on the descendant.
    """
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        # Own session, so the child's process group is exactly its tree and
        # _kill_tree reaches pipe-holding descendants, not just the child.
        start_new_session=(os.name == "posix"),
    )
    # Chunk lists are shared with the reader threads and published as they
    # arrive, so an abandoned reader's progress is still visible at return.
    captured: Dict[str, List[bytes]] = {"stdout": [], "stderr": []}
    overflowed: List[str] = []

    def _drain(name: str, pipe) -> None:
        chunks = captured[name]
        total = 0
        discarding = False
        while True:
            # read1, not read: read(n) blocks until n bytes or EOF, which
            # would hold the child's already-written output hostage to a
            # descendant keeping the pipe open past the child's exit.
            chunk = pipe.read1(_CHUNK_BYTES)
            if not chunk:
                break
            if discarding:
                continue
            total += len(chunk)
            if total > max_bytes:
                # Stop the flood at its source — the whole tree, so a
                # flooding grandchild dies too — then drain to EOF so
                # nothing blocks on a full pipe.
                overflowed.append(name)
                discarding = True
                _kill_tree(process)
                continue
            chunks.append(chunk)

    readers = [
        threading.Thread(target=_drain, args=("stdout", process.stdout)),
        threading.Thread(target=_drain, args=("stderr", process.stderr)),
    ]
    for reader in readers:
        reader.daemon = True
        reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        process.wait()
        _join_readers(readers, _REAP_GRACE_SECONDS)
        raise
    # A reader still alive past the grace means the child exited but a
    # descendant inherited its pipes and is keeping them open. The child's
    # own output was already consumed, so abandon the daemon readers instead
    # of blocking on a process this helper did not ask for. The tree is left
    # running — after a clean exit it may be a service the CLI started on
    # purpose — and the readers cap any future flood at the byte bound.
    _join_readers(readers, _ORPHAN_PIPE_GRACE_SECONDS)
    if overflowed:
        raise CaptureOverflow(overflowed[0], max_bytes)
    return (
        process.returncode,
        b"".join(captured["stdout"]),
        b"".join(captured["stderr"]),
    )
