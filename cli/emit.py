"""NDJSON record emitter, plus the optional progress channel for long waits.

Records go to stdout as NDJSON — the CLI's only result surface. Progress is
deliberately *not* stdout: it is a side channel a host may install, used by the
blocking wait primitives to prove liveness against a host idle ceiling (see
``cli/host_capability.py``). With no sink installed — every plain shell
invocation — emitting progress is a no-op, so the CLI's stdout contract is
identical whether or not a host is listening.
"""
import json
import sys
from typing import Callable, Optional

# Installed by mcp_server.server for the duration of one tool call that carries
# an MCP progress token. Module-level because the server invokes CLI handlers
# in-process on a single thread; there is never more than one call in flight.
_progress_sink: Optional[Callable[[float, Optional[float], Optional[str]], None]] = None


def emit_record(record: dict, *, out=None) -> None:
    """Emit one NDJSON record to stdout.

    One compact JSON object per line, UTF-8, trailing newline. Bare scalars
    and top-level arrays are code defects and raise TypeError.
    """
    if not isinstance(record, dict):
        raise TypeError(
            f"emit_record requires a dict; got {type(record).__name__}"
        )
    if out is None:
        out = sys.stdout
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    out.write(line + "\n")


def set_progress_sink(
    sink: Optional[Callable[[float, Optional[float], Optional[str]], None]]
) -> Optional[Callable[[float, Optional[float], Optional[str]], None]]:
    """Install (or clear with ``None``) the progress sink; returns the prior one.

    Callers restore the prior sink in a ``finally`` block so a nested or
    subsequent invocation never inherits a stale channel.
    """
    global _progress_sink
    previous = _progress_sink
    _progress_sink = sink
    return previous


def progress_available() -> bool:
    """True when a host has installed a progress channel for this invocation."""
    return _progress_sink is not None


def emit_progress(
    progress: float,
    total: Optional[float] = None,
    message: Optional[str] = None,
) -> None:
    """Report liveness to the host, if one is listening.

    A no-op without a sink. A sink that raises is swallowed: progress is a
    courtesy to the host's idle timer, and a broken side channel must never
    turn a successful wait into a failed command.
    """
    sink = _progress_sink
    if sink is None:
        return
    try:
        sink(progress, total, message)
    except Exception:  # pragma: no cover — defensive; see docstring
        pass
