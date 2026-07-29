"""Bounded launch-log retention for automated handoffs.

The supervisor continuously drains the configured wrapper's combined output
so the child cannot block on a full pipe.  Only the retained diagnostic is
bounded: output that does not fit is discarded without constraining or
reclassifying the wrapper process, its artifacts, or its completion report.
"""
from __future__ import annotations

import argparse
from collections import deque
import os
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence


DEFAULT_LOG_LINE_LIMIT = 400
DEFAULT_LOG_BYTE_LIMIT = 64 * 1024

GUARANTEE_SCOPE = "retained-launch-log"

LOG_BYTE_LIMIT_ENV = "CARTOPIAN_LOG_BYTE_LIMIT"
LOG_LINE_LIMIT_ENV = "CARTOPIAN_LOG_LINE_LIMIT"
LAUNCH_LOG_PATH_ENV = "CARTOPIAN_LAUNCH_LOG_PATH"

OUTPUT_SAFETY_ENV_VARS = (
    LOG_BYTE_LIMIT_ENV,
    LOG_LINE_LIMIT_ENV,
    LAUNCH_LOG_PATH_ENV,
)

# These names are no longer configuration surfaces. Scrub inherited values so
# a stale parent environment cannot present them to a custom wrapper as an
# active Cartopian contract.
_RETIRED_OUTPUT_SAFETY_ENV_VARS = (
    "CARTOPIAN_COMMAND_OUTPUT_GUIDANCE",
    "CARTOPIAN_STREAM_BYTE_LIMIT",
    "CARTOPIAN_STREAM_LINE_LIMIT",
)

_LOG_TRUNCATION_MARKER = (
    b"[cartopian: retained launch log truncated; bounded tail follows]\n"
)


class OutputSafetyError(ValueError):
    """A configured output-safety limit is invalid."""


@dataclass(frozen=True)
class OutputLimits:
    log_bytes: int = DEFAULT_LOG_BYTE_LIMIT
    log_lines: int = DEFAULT_LOG_LINE_LIMIT

    def __post_init__(self) -> None:
        for name, value in (
            ("log_bytes", self.log_bytes),
            ("log_lines", self.log_lines),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise OutputSafetyError(f"{name} must be a positive integer")

    def as_record(self) -> Dict[str, int]:
        return {
            "log_byte_limit": self.log_bytes,
            "log_line_limit": self.log_lines,
        }


@dataclass(frozen=True)
class SupervisionResult:
    exit_code: int
    retained_log_path: Optional[str]
    retained_bytes: int
    retained_lines: int
    retention_state: str
    report_present: bool
    guarantee_scope: str = GUARANTEE_SCOPE


def count_lines(payload: bytes) -> int:
    """Count LF-terminated lines plus one non-empty trailing fragment.

    CRLF therefore counts once, a final LF does not create an extra empty line,
    and invalid/multibyte bytes have no special treatment.  This same rule is
    used incrementally at the enforcement boundary.
    """
    if not payload:
        return 0
    return payload.count(b"\n") + (0 if payload.endswith(b"\n") else 1)


def _positive_environment_limit(
    environ: Dict[str, str],
    name: str,
    default: int,
) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw, 10)
    except (TypeError, ValueError) as exc:
        raise OutputSafetyError(f"{name} must be a positive integer; got {raw!r}") from exc
    if value <= 0:
        raise OutputSafetyError(f"{name} must be a positive integer; got {raw!r}")
    return value


def limits_from_environment(environ: Dict[str, str]) -> OutputLimits:
    """Resolve and validate operator overrides before child creation."""
    return OutputLimits(
        log_bytes=_positive_environment_limit(
            environ, LOG_BYTE_LIMIT_ENV, DEFAULT_LOG_BYTE_LIMIT
        ),
        log_lines=_positive_environment_limit(
            environ, LOG_LINE_LIMIT_ENV, DEFAULT_LOG_LINE_LIMIT
        ),
    )


def project_environment(
    environ: Dict[str, str],
    limits: OutputLimits,
    log_path: Optional[Path],
) -> None:
    """Project the normalized retention-only contract into the environment."""
    for retired_name in _RETIRED_OUTPUT_SAFETY_ENV_VARS:
        environ.pop(retired_name, None)
    environ[LOG_BYTE_LIMIT_ENV] = str(limits.log_bytes)
    environ[LOG_LINE_LIMIT_ENV] = str(limits.log_lines)
    if log_path is None:
        environ.pop(LAUNCH_LOG_PATH_ENV, None)
    else:
        environ[LAUNCH_LOG_PATH_ENV] = str(log_path)


class _LineCounter:
    def __init__(self) -> None:
        self.completed = 0
        self.trailing_fragment = False

    def add(self, byte: int) -> int:
        if byte == 0x0A:
            self.completed += 1
            self.trailing_fragment = False
        else:
            self.trailing_fragment = True
        return self.value

    @property
    def value(self) -> int:
        return self.completed + int(self.trailing_fragment)


class _BoundedLog:
    """Bounded beginning/tail retention with an explicit truncation marker."""

    def __init__(self, byte_limit: int, line_limit: int) -> None:
        self.byte_limit = byte_limit
        self.line_limit = line_limit
        self.payload = bytearray()
        self.tail_byte_limit = max(1, byte_limit // 3)
        self.tail: deque[int] = deque(maxlen=self.tail_byte_limit)
        self.counter = _LineCounter()
        self.truncated = False

    def add(self, byte: int) -> None:
        if self.truncated:
            self.tail.append(byte)
            return
        if len(self.payload) >= self.byte_limit:
            self.truncated = True
            return
        before_completed = self.counter.completed
        before_fragment = self.counter.trailing_fragment
        lines = self.counter.add(byte)
        if lines > self.line_limit:
            self.counter.completed = before_completed
            self.counter.trailing_fragment = before_fragment
            self.truncated = True
            return
        self.payload.append(byte)

    def representation(self) -> bytes:
        head = bytes(self.payload)
        if not self.truncated:
            return head
        marker = _LOG_TRUNCATION_MARKER[: self.byte_limit]
        tail = bytes(self.tail)
        while head or tail:
            before = b"" if not head or head.endswith(b"\n") else b"\n"
            after = b"" if not tail or marker.endswith(b"\n") else b"\n"
            candidate = head + before + marker + after + tail
            if (
                len(candidate) <= self.byte_limit
                and count_lines(candidate) <= self.line_limit
            ):
                return candidate
            if len(head) >= len(tail) and head:
                head = head[:-1]
            elif tail:
                tail = tail[1:]
        while marker and (
            len(marker) > self.byte_limit or count_lines(marker) > self.line_limit
        ):
            marker = marker[:-1]
        return marker


def _safe_log_target(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return (
        not path.is_symlink()
        and stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
    )


def usable_log_path(path: Path) -> Optional[Path]:
    """Return a safe bounded-log destination, or None for null retention."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if not path.parent.is_dir() or not _safe_log_target(path):
        return None
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return None
    probe = path.parent / f".{path.name}.probe.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(probe, flags, 0o600)
        os.close(fd)
        probe.unlink()
    except OSError:
        try:
            probe.unlink()
        except OSError:
            pass
        return None
    return path


def _publish_log(path: Optional[Path], payload: bytes) -> Optional[Path]:
    if path is None or not _safe_log_target(path):
        return None
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}.{time.monotonic_ns()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
        return path
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None


def _status_value(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ")


def _publish_exit_status(
    status_path: Path,
    *,
    launch_id: str,
    expected_variant: str,
    limits: OutputLimits,
    result: SupervisionResult,
) -> None:
    if result.exit_code == 0:
        reason = "clean"
    elif result.exit_code == 124:
        reason = "timeout"
    else:
        reason = "error"
    fields: Iterable[tuple[str, object]] = (
        ("state", "exited"),
        ("exit_code", result.exit_code),
        ("reason", reason),
        ("launch_id", launch_id),
        ("expected_variant", expected_variant),
        ("log_byte_limit", limits.log_bytes),
        ("log_line_limit", limits.log_lines),
        ("retained_log_path", result.retained_log_path or "unavailable"),
        ("retained_bytes", result.retained_bytes),
        ("retained_lines", result.retained_lines),
        ("retention_state", result.retention_state),
        ("report_present", str(result.report_present).lower()),
        ("guarantee_scope", result.guarantee_scope),
    )
    payload = "".join(
        f"{key}={_status_value(value)}\n" for key, value in fields
    )
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_path.parent / (
        f".{status_path.name}.tmp.{os.getpid()}.{time.monotonic_ns()}"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, status_path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _report_complete(report_path: Optional[Path], expected_variant: str) -> bool:
    if report_path is None:
        return False
    try:
        from cli import handoff_observer

        return (
            handoff_observer.observe_report(
                report_path, expected_variant
            ).publication_state
            == "complete"
        )
    except (ImportError, OSError):
        return False


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> tuple[bool, str]:
    if proc.poll() is not None:
        return False, "already-exited"
    graceful = False
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        graceful = True
    except (OSError, subprocess.SubprocessError):
        try:
            proc.terminate()
            graceful = True
        except OSError:
            pass
    try:
        proc.wait(timeout=1.0)
        return True, "terminated" if graceful else "exited"
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            return False, "termination-failed"
    try:
        proc.wait(timeout=2.0)
        return True, "killed"
    except subprocess.TimeoutExpired:
        return False, "termination-failed"


def supervise(
    command: Sequence[str],
    *,
    status_path: Path,
    report_path: Optional[Path],
    log_path: Optional[Path],
    launch_id: str,
    expected_variant: str,
    limits: OutputLimits,
) -> SupervisionResult:
    """Drain one wrapper continuously while retaining a bounded diagnostic."""
    if not command:
        raise ValueError("command must not be empty")
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if os.name == "nt"
        else 0
    )
    try:
        proc = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
    except OSError as exc:
        diagnostic = (
            f"[cartopian: wrapper launch failed: {type(exc).__name__}: {exc}]\n"
        ).encode("utf-8", errors="replace")
        retained = _BoundedLog(limits.log_bytes, limits.log_lines)
        for byte in diagnostic:
            retained.add(byte)
        payload = retained.representation()
        published = _publish_log(log_path, payload)
        result = SupervisionResult(
            exit_code=1,
            retained_log_path=str(published) if published else None,
            retained_bytes=len(payload) if published else 0,
            retained_lines=count_lines(payload) if published else 0,
            retention_state=(
                "truncated" if published and retained.truncated
                else "complete" if published
                else "unavailable"
            ),
            report_present=bool(report_path and report_path.is_file()),
        )
        _publish_exit_status(
            status_path,
            launch_id=launch_id,
            expected_variant=expected_variant,
            limits=limits,
            result=result,
        )
        return result
    assert proc.stdout is not None
    retained = _BoundedLog(limits.log_bytes, limits.log_lines)

    while True:
        chunk = os.read(proc.stdout.fileno(), 4096)
        if not chunk:
            break
        for byte in chunk:
            retained.add(byte)
        # Preserve report-completion supervision without coupling it to stream
        # volume. Shipped wrappers also observe quiet post-report processes;
        # this check covers wrappers that continue emitting after publication.
        if _report_complete(report_path, expected_variant):
            _terminate_process_tree(proc)
            try:
                proc.stdout.close()
            except OSError:
                pass
            payload = retained.representation()
            published = _publish_log(log_path, payload)
            result = SupervisionResult(
                exit_code=0,
                retained_log_path=str(published) if published else None,
                retained_bytes=len(payload) if published else 0,
                retained_lines=count_lines(payload) if published else 0,
                retention_state=(
                    "truncated" if published and retained.truncated
                    else "complete" if published
                    else "unavailable"
                ),
                report_present=True,
            )
            _publish_exit_status(
                status_path,
                launch_id=launch_id,
                expected_variant=expected_variant,
                limits=limits,
                result=result,
            )
            return result

    try:
        proc.stdout.close()
    except OSError:
        pass
    exit_code = proc.wait()
    payload = retained.representation()
    published = _publish_log(log_path, payload)
    result = SupervisionResult(
        exit_code=exit_code,
        retained_log_path=str(published) if published else None,
        retained_bytes=len(payload) if published else 0,
        retained_lines=count_lines(payload) if published else 0,
        retention_state=(
            "truncated" if published and retained.truncated
            else "complete" if published
            else "unavailable"
        ),
        report_present=bool(report_path and report_path.is_file()),
    )
    _publish_exit_status(
        status_path,
        launch_id=launch_id,
        expected_variant=expected_variant,
        limits=limits,
        result=result,
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--log-path")
    parser.add_argument("--launch-id", required=True)
    parser.add_argument("--expected-variant", required=True)
    parser.add_argument("--log-bytes", type=int, required=True)
    parser.add_argument("--log-lines", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    limits = OutputLimits(
        log_bytes=args.log_bytes,
        log_lines=args.log_lines,
    )
    result = supervise(
        command,
        status_path=Path(args.status_path),
        report_path=Path(args.report_path),
        log_path=Path(args.log_path) if args.log_path else None,
        launch_id=args.launch_id,
        expected_variant=args.expected_variant,
        limits=limits,
    )
    return result.exit_code


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
