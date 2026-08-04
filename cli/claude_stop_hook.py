"""Claude Code completion adapter — a Stop hook that refuses a report-less stop.

Root cause this addresses. A ``claude -p`` assignee increasingly launches
completion-critical work (a test suite, a build) as a *background* task, then
emits ``stop_reason: "end_turn"`` while saying the run is still going and the
report will be written afterwards. In print mode that final result **is** the
end of the process: Claude exits 0 and stops its background shells shortly
after, so the promised report is never written. The wrapper propagates exit 0,
and ``cli/handoff_observer.py`` correctly classifies the absent report as
``exited-without-report``. Detection, however, happens after the damage; the
handoff is already dead.

This hook moves the intervention to the only moment where it can still be
repaired — the Stop event itself. Claude Code officially supports *blocking*
Stop: emitting ``{"decision": "block", "reason": ...}`` prevents the agent
from ending the turn and feeds ``reason`` back to it as the instruction for
what to do next. So while the required report is absent or unparseable, the
agent is told to finish in the foreground and write the report, rather than
being allowed to exit on a promise.

Scope (deliberately narrow):

1. **Active only for a Cartopian handoff.** ``CARTOPIAN_EXPECTED_REPORT_PATH``
   is exported by ``cartopian dispatch`` and names the exact bounded report
   slot this launch owns. Unset — every interactive session, and any
   non-Cartopian use of the same settings file — means zero footprint: no
   output, exit 0, nothing observed.
2. **The report is the only question asked.** Completeness is delegated to
   ``cli/handoff_observer.observe_report``, the same canonical observer the
   wait primitives use, so "complete" here means exactly what it means to
   ``cartopian wait-handoff``. A ``blocked`` or ``failed`` report is a
   *finished* handoff and is allowed to stop; only absent, partial, or
   permanently invalid publication blocks.
3. **Bounded intervention.** Blocking Stop indefinitely would turn an agent
   that genuinely cannot write a report (API error loop, an exhausted context)
   into an unkillable session. Each block is counted per session; after
   ``CARTOPIAN_STOP_GUARD_MAX_BLOCKS`` (default 3) the guard stops
   intervening and lets the process exit. If it exits cleanly without a
   report, the observer records the completion classification
   ``exited-without-report``.
4. **Fail open, always.** An unreadable payload, an unwritable counter, an
   unexpected exception — every failure path allows the stop with a stderr
   note. This hook is completion *discipline*, not a security boundary
   (unlike ``cli/claude_hook.py``, which fails closed): it must never be able
   to strand a session. The observer's terminal classification remains the
   authoritative record either way.

It imposes **no timer**. The single ``CARTOPIAN_TIMEOUT`` deadline enforced by
the wrapper stays the only clock; a blocked stop simply spends more of the
budget that was already allocated to the handoff.

Counter state lives in the OS temp directory, keyed by a digest of the session
id and the report path — never inside the governed project, so it adds no
report-slot companion file and needs no lifecycle cleanup. A stale file from an
earlier launch carries a different session id and resets to zero.

Activation is process-scoped. The shipped Claude wrappers add a Stop entry as
an inline ``--settings`` value whenever
``CARTOPIAN_EXPECTED_REPORT_PATH`` is present::

    {
      "hooks": {
        "Stop": [
          {
            "hooks": [
              {
                "type": "command",
                "command": "python /installed/root/cli/claude_stop_hook.py"
              }
            ]
          }
        ]
      }
    }

The actual command contains fully serialized installed paths; no settings file
is written. Claude continues to load user, project, and local settings
normally. ``CARTOPIAN_CLAUDE_BARE=true`` still passes ``--bare``;
auto-discovered hooks stay skipped, while this explicitly supplied per-launch
settings entry remains active. The same settings object may independently
carry the capability-refusal PreToolUse hook when the dispatched project's
resolved grants activate containment. The legacy ``scripts/install.py
--claude-hook <project-dir>`` operation removes obsolete project-level
Cartopian hook registrations as a bounded compatibility cleanup.

Hook I/O contract: the Stop payload arrives as JSON on stdin; a block is the
documented ``{"decision": "block", "reason": ...}`` object on stdout with exit
0; an allow produces no stdout at all. Standard library only.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

if __package__ in (None, ""):  # invoked as a script: `python .../cli/claude_stop_hook.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli.handoff_observer import ReportObservation, observe_report  # noqa: E402

# Exported by `cartopian dispatch` (cli/commands/dispatch.py). Its presence is
# the sole activation signal: no variable, no guard.
REPORT_ENV = "CARTOPIAN_EXPECTED_REPORT_PATH"
VARIANT_ENV = "CARTOPIAN_EXPECTED_REPORT_VARIANT"
MAX_BLOCKS_ENV = "CARTOPIAN_STOP_GUARD_MAX_BLOCKS"

# Enough nudges to recover a genuinely-recoverable handoff (re-run in the
# foreground, then write the report) without pinning a broken session open.
DEFAULT_MAX_BLOCKS = 3

VALID_VARIANTS = ("task", "review", "planning-review")


@dataclass(frozen=True)
class Decision:
    """The hook's verdict for one Stop event."""

    action: str  # "allow" | "block"
    reason: Optional[str] = None  # fed back to the agent when blocking
    note: Optional[str] = None  # stderr diagnostic; never gates anything


_ALLOW = Decision("allow")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _max_blocks(environ: Mapping[str, str]) -> int:
    """Resolve the per-session block ceiling.

    ``0`` disables the guard outright (an operator escape hatch). A malformed
    or negative value is not honored as "disable" — that would turn a typo
    into a silently absent guard — so it falls back to the default.
    """
    raw = (environ.get(MAX_BLOCKS_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_BLOCKS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_BLOCKS
    if value < 0:
        return DEFAULT_MAX_BLOCKS
    return value


def _expected_variant(environ: Mapping[str, str]) -> Optional[str]:
    variant = (environ.get(VARIANT_ENV) or "").strip()
    return variant if variant in VALID_VARIANTS else None


# ---------------------------------------------------------------------------
# Per-session block counter (OS temp dir; never inside the governed project)
# ---------------------------------------------------------------------------
def counter_path(session_id: str, report_path: str, tmpdir: Optional[str] = None) -> Path:
    """Deterministic counter file for one (session, report slot) pair."""
    digest = hashlib.sha256(
        f"{session_id}\0{report_path}".encode("utf-8")
    ).hexdigest()[:32]
    base = Path(tmpdir) if tmpdir else Path(tempfile.gettempdir())
    return base / f"cartopian-stop-guard-{digest}.json"


def _read_blocks(path: Path, session_id: str) -> int:
    """Blocks already issued for this session, or 0 when unknown.

    A file from an earlier launch carries a different session id and reads as
    0 — a stale counter can never pre-exhaust a fresh handoff's guard.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict) or data.get("session_id") != session_id:
        return 0
    blocks = data.get("blocks")
    return blocks if isinstance(blocks, int) and blocks > 0 else 0


def _write_blocks(path: Path, session_id: str, blocks: int) -> bool:
    """Persist the block count. Returns False when it could not be recorded."""
    payload = json.dumps({"session_id": session_id, "blocks": blocks})
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def clear_counter(path: Path) -> None:
    """Best-effort removal once the handoff has published a complete report."""
    try:
        path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Refusal text
# ---------------------------------------------------------------------------
def _missing_elements(report_path: Path, variant: Optional[str]) -> List[str]:
    """Name what a present-but-unparseable report is missing, when knowable.

    Reuses the canonical schema constants so the guidance can never drift from
    what ``cartopian parse-report`` actually requires. Returns an empty list
    when the variant is unknown or the file cannot be read — an empty list
    simply means the generic instruction stands on its own.
    """
    if variant not in VALID_VARIANTS:
        return []
    try:
        content = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    from cli.commands import parse_report  # local: keeps import cost off the allow path

    missing: List[str] = []
    for section in parse_report.REQUIRED_SECTIONS[variant]:
        if not _has_heading(content, section.removeprefix("## ")):
            missing.append(f"the `{section}` section")
    for alternatives in parse_report.REQUIRED_ANY_SECTIONS[variant]:
        if not any(
            _has_heading(content, s.removeprefix("## ")) for s in alternatives
        ):
            missing.append("one of " + " / ".join(f"`{s}`" for s in alternatives))
    for key in parse_report.REQUIRED_IDENTITY_KEYS[variant]:
        if key not in content:
            missing.append(f"the `- {key}` identity line")
    if parse_report._extract_status(content) not in parse_report.STATUS_VERDICT:
        missing.append("a top-level `Status: complete | blocked | failed` line")
    elif variant in parse_report.REVIEW_VARIANTS:
        if parse_report._extract_review_verdict(content) is None:
            missing.append("a `## Verdict` value of approve / request-changes / reject")
    return missing


def _has_heading(content: str, heading: str) -> bool:
    return bool(
        re.search(rf"^##\s+{re.escape(heading)}\s*$", content, re.MULTILINE)
    )


def _block_reason(
    report_path: Path,
    observation: ReportObservation,
    variant: Optional[str],
    attempt: int,
    max_blocks: int,
) -> str:
    variant_label = variant or "inferred from the report content"

    if observation.publication_state == "absent":
        problem = "the required handoff report has not been written."
    elif observation.permanently_invalid:
        problem = (
            "the required handoff report path is a symlink, which is never "
            "valid completion evidence. Write a regular file at that exact path."
        )
    else:
        problem = (
            "the required handoff report exists but does not parse as a "
            "complete report, so it is not completion evidence."
        )

    lines = [
        f"[cartopian] Stop blocked: {problem}",
        "",
        f"Report path: {report_path}",
        f"Expected variant: {variant_label}",
    ]

    missing = (
        _missing_elements(report_path, variant)
        if observation.present and not observation.permanently_invalid
        else []
    )
    if missing:
        lines.append("Missing from the current file: " + "; ".join(missing) + ".")

    lines += [
        "",
        "This session is a `claude -p` handoff. Your final result IS process",
        "exit: nothing runs after you stop. Background shells are terminated",
        "shortly after the final result, and background-task notifications",
        "cannot resume this session — there is no later turn in which to write",
        "the report. Ending the turn with work 'still running' loses that work.",
        "",
        "Do this now, before stopping again:",
        "1. Re-run every completion-critical command in the FOREGROUND and wait",
        "   for it to finish. Do not background the test suite, build, or any",
        "   command whose result the report depends on.",
        "2. Write the complete report to the exact path above, using the report",
        "   template named in your prompt.",
        "3. If the work genuinely cannot be finished, still write the report",
        "   with `Status: blocked` and record why. A blocked report is a",
        "   finished handoff; an absent report is a lost one.",
        "",
        f"(guard attempt {attempt} of {max_blocks}; after {max_blocks} the guard "
        "stops intervening and this handoff is recorded as exited-without-report.)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate(
    payload: Dict[str, Any],
    *,
    environ: Optional[Mapping[str, str]] = None,
    tmpdir: Optional[str] = None,
) -> Decision:
    """Decide allow/block for one Stop payload.

    ``environ`` and ``tmpdir`` are injectable for tests; live runs use the
    process environment and the OS temp directory.
    """
    if environ is None:
        environ = os.environ

    report_raw = (environ.get(REPORT_ENV) or "").strip()
    if not report_raw:
        # Not a Cartopian handoff: zero footprint.
        return _ALLOW

    report_path = Path(report_raw)
    variant = _expected_variant(environ)
    session_id = str(payload.get("session_id") or "unknown-session")
    stop_hook_active = bool(payload.get("stop_hook_active"))
    state_path = counter_path(session_id, str(report_path), tmpdir)

    observation = observe_report(report_path, variant)
    if observation.publication_state == "complete":
        clear_counter(state_path)
        return _ALLOW

    max_blocks = _max_blocks(environ)
    if max_blocks == 0:
        return Decision(
            "allow",
            note=(
                f"[cartopian] stop guard disabled via {MAX_BLOCKS_ENV}=0; "
                f"allowing stop with no report at {report_path}"
            ),
        )

    blocks = _read_blocks(state_path, session_id)
    if blocks >= max_blocks:
        clear_counter(state_path)
        return Decision(
            "allow",
            note=(
                f"[cartopian] stop guard exhausted after {blocks} block(s); "
                f"allowing stop with no complete report at {report_path} — "
                f"the handoff will be recorded as exited-without-report"
            ),
        )

    attempt = blocks + 1
    if not _write_blocks(state_path, session_id, attempt) and stop_hook_active:
        # The count cannot be persisted, so a further block cannot be bounded.
        # One unbounded loop is worse than one lost report: allow and let the
        # backstop classify it.
        return Decision(
            "allow",
            note=(
                f"[cartopian] stop guard cannot persist its counter at "
                f"{state_path}; allowing stop rather than risking an unbounded "
                f"block loop"
            ),
        )

    return Decision(
        "block",
        reason=_block_reason(report_path, observation, variant, attempt, max_blocks),
    )


def main() -> int:
    """Hook entry point: Stop payload on stdin; structured block on stdout.

    Allows are silent on stdout (a diagnostic may go to stderr). Every failure
    mode allows: this guard must never be able to strand a session.
    """
    try:
        raw = sys.stdin.buffer.read()
        payload = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
    except Exception as exc:
        sys.stderr.write(
            f"[cartopian] claude_stop_hook: unreadable hook payload ({exc}); "
            f"not interfering\n"
        )
        return 0

    try:
        decision = evaluate(payload)
    except Exception as exc:  # fail open: completion discipline, not a boundary
        sys.stderr.write(
            f"[cartopian] claude_stop_hook: evaluation failed ({exc}); "
            f"allowing stop\n"
        )
        return 0

    if decision.action == "block":
        sys.stdout.write(
            json.dumps({"decision": "block", "reason": decision.reason}) + "\n"
        )
        sys.stderr.write(decision.reason + "\n")
    elif decision.note:
        sys.stderr.write(decision.note + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
