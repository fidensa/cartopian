"""`cartopian dispatch` — mediated handoff launch.

Two keying modes:

- ``cartopian dispatch <task-path> --role <role>`` — task-scoped handoffs
  (task assignment, task review). The prompt path is derived from the task id
  (``prompts/PROMPT-NN-NNN.md``), so prompt, report, and review paths agree.
- ``cartopian dispatch --prompt <prompt-path> --role <role>`` — report-path-only
  handoffs (planning-checkpoint reviews; no task file exists during planning).
  ``--prompt`` accepts only an allowlisted planning-checkpoint prompt slot
  (``<project-root>/prompts/PROMPT-PLAN-NNN.md``).

Each mode fails closed unless its exact activity appears in the role's
``auto_launch`` list: ``task_run``, ``task_review``, or ``planning_review``.
Review policy remains independent under ``[reviews]``.

The delegation counterpart to the mediated writer. A contained PM has no
shell or process-exec tool, so it cannot launch an assignee wrapper itself. This
command performs the launch on the PM's behalf as *per-invocation* Cartopian code
(no daemon, no broker): it consumes canonical resolution, fails closed on a
missing role handoff agent or prompt, exports ``CARTOPIAN_TIMEOUT`` from
``roles.<role>.timeout``, ``CARTOPIAN_MODEL`` from
``roles.<role>.model`` (when set), ``CARTOPIAN_EFFORT`` from
``roles.<role>.effort`` (when set), and
``CARTOPIAN_ROLE`` from the dispatched role (the session-role marker capability
enforcement points such as ``cli/claude_hook.py`` read), and
launches the configured wrapper with the single absolute-prompt-path argv from the
cartopian project-root cwd (the launch contract fixed by
``protocol/CONVENTIONS.md`` § Handoffs / Launch Directory). Capability grant
decisions remain the harness hook's job. The Claude wrapper uses the exported
role only to resolve whether that process-scoped hook must be loaded; it never
derives authorization from a role or wrapper name.

It returns once the wrapper is launched — it does **not** block to completion. The
wrapper owns its own background/timeout semantics (it kills the assignee at the
``CARTOPIAN_TIMEOUT`` deadline, exit ``124``). The PM then observes the result
through ``cartopian wait-handoff`` / ``cartopian wait-report``; this command never
adds a waiting mechanism and never reaps the child.

The launched executable is always the operator-configured role handoff agent.
There is no caller-supplied command or executable argument, so the PM cannot use
dispatch to launch an arbitrary process — the mediated, config-bound path is the
only route a contained PM has. Standard library only (NF-001).
"""
import argparse
import os
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli import (
    host_capability,
    output_safety,
    report_identity,
    request_trace,
    source_guidance,
)
from cli.commands import handoff_packet
from cli.commands._writers import PROMPT_ID_RE
from cli.commands.resolve_config import (
    _CliError,
    resolve_project_configuration,
)
from cli.config_schema import MACHINE_RECORD_SCHEMA_VERSION
from cli.emit import emit_record
from cli.main import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)

# Protocol default handoff timeout (CONVENTIONS.md § Handoffs). Exported to the
# wrapper as CARTOPIAN_TIMEOUT when the role block omits an explicit timeout.
DEFAULT_TIMEOUT = "60m"
DEFAULT_TIMEOUT_SECONDS = 3600

# Agent-neutral model selection. Exported from the resolved
# ``roles.<role>.model`` so the wrapper can translate it into the
# tool-specific model flag; never exported when the handoff sets no model
# (the tool's own default model applies).
MODEL_ENV = "CARTOPIAN_MODEL"

# Agent-neutral effort/thinking-level selection. Exported from the resolved
# ``roles.<role>.effort`` so the wrapper can translate it into the
# tool-specific effort flag; never exported when the handoff sets no effort
# (the tool's own default effort applies). Value validation is the wrapper's
# job — effort vocabularies differ per agent CLI.
EFFORT_ENV = "CARTOPIAN_EFFORT"

# Resolved work-root grant. Exported as an ``os.pathsep``-joined list of the
# project's resolved work-root absolute paths. The launch contract grants the
# assignee write access to the union of the cartopian project root and the
# declared work roots; some agent CLIs impose their own filesystem sandbox
# rooted at the launch cwd (e.g. codex ``--sandbox workspace-write``), so the
# wrapper needs the resolved paths to widen that sandbox to cover them. Never
# exported when the project declares no work roots.
WORK_ROOTS_ENV = "CARTOPIAN_WORK_ROOTS"
HANDOFF_ID_ENV = "CARTOPIAN_HANDOFF_ID"
EXPECTED_VARIANT_ENV = "CARTOPIAN_EXPECTED_REPORT_VARIANT"
EXPECTED_REPORT_ENV = "CARTOPIAN_EXPECTED_REPORT_PATH"
PYTHON_ENV = "CARTOPIAN_PYTHON"

def _running_on_windows() -> bool:
    """Platform seam for the two native-Windows launch branches (argv routing
    and output policy), patchable in tests without disturbing ``os.name``
    globally (pathlib and friends key on it)."""
    return os.name == "nt"


def _resolve_comspec() -> str:
    """Absolute path to the Windows command interpreter (``cmd.exe``).

    ``COMSPEC`` is the canonical source, but it is *not* guaranteed to be set:
    a curated environment — e.g. the MCP server process the harness spawns, in
    which ``dispatch`` runs in-process — can drop it. A bare ``"cmd.exe"`` then
    rides on the executable search succeeding, which is fragile when a custom
    ``env`` is handed to ``CreateProcess``. Resolve to an absolute path instead,
    falling back through ``%SystemRoot%`` (set by the kernel for essentially
    every process) and finally a PATH lookup before a last-resort bare name.
    """
    comspec = os.environ.get("COMSPEC")
    if comspec:
        return comspec
    system_root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if system_root:
        candidate = os.path.join(system_root, "System32", "cmd.exe")
        if os.path.isfile(candidate):
            return candidate
    which = shutil.which("cmd.exe")
    if which:
        return which
    return "cmd.exe"


def _build_launch_argv(resolved_agent: str, prompt_path: str, is_windows: bool) -> List[str]:
    """Argv to launch the resolved agent with the prompt path.

    A native-Windows ``.cmd``/``.bat`` is not a PE executable, so CreateProcess
    (which backs ``subprocess.Popen`` on Windows) cannot run it directly — route
    it through the command interpreter (``cmd.exe``), resolved to an absolute
    path so an absent ``COMSPEC`` cannot strand the launch. POSIX wrappers are
    executable scripts and launch directly.
    """
    if is_windows and resolved_agent.lower().endswith((".cmd", ".bat")):
        return [_resolve_comspec(), "/c", resolved_agent, prompt_path]
    return [resolved_agent, prompt_path]


def _preflight_request_trace(
    project_root: Path,
    activity: str,
    task_path: Optional[Path],
    prompt_path: Path,
) -> Tuple[bool, Dict[str, Any]]:
    """Recompute review context and validate the prompt's binding.

    Refuses omitted evidence, a changed request record, a stale prompt binding, and an
    absent request-trace section. Every refusal is fail-closed: no launch
    happens, and the reason names the operator-actionable recovery.
    """
    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, {
            "ok": False,
            "rule": "missing-request-trace-section",
            "detail": f"review prompt is unreadable: {prompt_path} — {exc}",
            "recovery": "regenerate the review prompt",
            "context_identity": None,
        }
    try:
        if activity == "task_review":
            context = request_trace.context_for_task(
                project_root,
                task_path,
                prompt_text=prompt_text,
                require_completion_evidence=True,
            )
        elif activity == "task_run":
            context = request_trace.context_for_task_assignment(
                project_root,
                task_path,
                prompt_text=prompt_text,
            )
        else:
            checkpoint_id = prompt_path.stem.removeprefix("PROMPT-")
            context = request_trace.context_for_checkpoint(
                project_root,
                checkpoint_id,
                checkpoint_text=prompt_text,
            )
    except request_trace.RequestRefusal as refusal:
        return False, {
            "ok": False,
            "rule": refusal.rule,
            "detail": refusal.detail,
            "recovery": refusal.recovery,
            "context_identity": None,
        }
    result = request_trace.preflight_prompt_binding(context, prompt_text)
    result["evidence"] = [
        item.record_id for item in context.evidence
    ]
    result["legacy_unavailable"] = context.legacy
    result["measures"] = context.measures
    result["captured_completion_evidence"] = (
        context.captured_completion.as_record()
        if context.captured_completion is not None
        else None
    )
    return bool(result["ok"]), result


def _clear_handoff_signal(path: Path) -> bool:
    """Remove one bounded report-slot signal, refusing unsafe leaf types."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _CliError(
            EXIT_FAIL,
            "guard",
            f"handoff signal cannot be inspected before launch: {path} — {exc}",
        )
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise _CliError(
            EXIT_FAIL,
            "guard",
            f"handoff signal is not a safe single-link regular file: {path}",
        )
    try:
        path.unlink()
    except OSError as exc:
        raise _CliError(
            EXIT_FAIL,
            "guard",
            f"handoff signal cannot be cleared before launch: {path} — {exc}",
        )
    return True


def _clear_handoff_slot(report_path: Path) -> Dict[str, bool]:
    """Clear authoritative and secondary signals before one new launch."""
    return {
        "report_deleted": _clear_handoff_signal(report_path),
        "status_deleted": _clear_handoff_signal(Path(str(report_path) + ".status")),
    }


def _publish_running_status(
    status_path: Path,
    *,
    launch_id: str,
    role: str,
    activity: str,
    expected_variant: str,
    launch_log_path: Optional[str],
) -> None:
    """Atomically publish the secondary running marker for the current launch."""
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.parent / (
        f"{status_path.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    )
    payload = (
        "state=running\n"
        f"launch_id={launch_id}\n"
        f"role={role}\n"
        f"activity={activity}\n"
        f"expected_variant={expected_variant}\n"
    )
    if launch_log_path is not None:
        payload += (
            f"guarantee_scope={output_safety.GUARANTEE_SCOPE}\n"
            "retained_log_ready=false\n"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, status_path)
    except OSError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise _CliError(
            EXIT_FAIL,
            "guard",
            f"cannot publish current handoff status before launch: {status_path} — {exc}",
        )


def _remove_own_running_status(status_path: Path, launch_id: str) -> None:
    """Remove a marker created for a launch that never started."""
    try:
        fields = {}
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        if (
            fields.get("state") == "running"
            and fields.get("launch_id") == launch_id
            and not status_path.is_symlink()
        ):
            status_path.unlink()
    except (OSError, UnicodeDecodeError):
        pass


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for dispatch.

    Deliberately minimal: a task path (or a planning-checkpoint prompt path)
    and a role. The executable launched is sourced exclusively from
    ``roles.<role>.agent`` in config — there is intentionally no flag to
    supply an arbitrary command, so the PM cannot turn dispatch into a raw
    exec primitive (containment invariant). ``--prompt`` names an allowlisted
    prompt slot to hand to the config-bound agent, never an executable.
    """
    subparser.add_argument(
        "task_path",
        nargs="?",
        default=None,
        help="Absolute path to the task file whose handoff to launch (task-scoped handoffs)",
    )
    subparser.add_argument(
        "--prompt",
        default=None,
        help=(
            "Absolute path to a planning-checkpoint prompt "
            "(<project-root>/prompts/PROMPT-PLAN-NNN.md) for a "
            "report-path-only handoff; requires planning_review in the "
            "role's auto_launch list"
        ),
    )
    subparser.add_argument(
        "--role",
        required=True,
        help="Role identifier being dispatched (must have a handoff agent)",
    )


def handler(args: argparse.Namespace) -> int:
    """Prepare the packet, validate fail-closed, launch the wrapper, emit NDJSON."""
    raw_task: Optional[str] = args.task_path
    raw_prompt: Optional[str] = args.prompt
    role: str = args.role

    if (raw_task is None) == (raw_prompt is None):
        stderr_usage(
            "provide exactly one of <task-path> (task-scoped handoff) or "
            "--prompt <prompt-path> (planning-checkpoint handoff)"
        )
        return EXIT_USAGE

    task_path: Optional[Path] = None
    prompt_path: Optional[Path] = None
    if raw_task is not None:
        if not Path(raw_task).is_absolute():
            stderr_usage(f"task_path must be an absolute path; got: {raw_task}")
            return EXIT_USAGE
        task_path = Path(raw_task)
        if not task_path.is_file():
            stderr_error(f"task file not found: {raw_task}")
            return EXIT_FAIL
        task_path = task_path.resolve()
        anchor = task_path
    else:
        activity = "planning_review"
        if not Path(raw_prompt).is_absolute():
            stderr_usage(f"--prompt must be an absolute path; got: {raw_prompt}")
            return EXIT_USAGE
        prompt_path = Path(raw_prompt)
        if not prompt_path.is_file():
            stderr_guard(
                f"prompt not found: {raw_prompt} — prepare the handoff prompt "
                f"before dispatching (run-handoff Stage 1)"
            )
            return EXIT_FAIL
        prompt_path = prompt_path.resolve()
        anchor = prompt_path

    project_root = handoff_packet._find_project_root(anchor)
    if project_root is None:
        stderr_error(f"project config not found for: {anchor}")
        return EXIT_ENV

    project_toml = project_root / "cartopian.toml"
    if not project_toml.is_file():
        stderr_error(f"project config not found: {project_toml}")
        return EXIT_ENV

    try:
        resolved = resolve_project_configuration(project_root)
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    if role not in resolved["roles"]:
        stderr_guard(f"role {role!r} is not declared")
        return EXIT_FAIL
    role_record = resolved["roles"][role]
    launch = role_record["launch"]
    agent = launch.get("agent")
    if not agent:
        stderr_guard(
            f"roles.{role}.agent is not configured — "
            f"dispatch this role manually"
        )
        return EXIT_FAIL

    timeout = launch.get("timeout") or DEFAULT_TIMEOUT
    model = launch.get("model")
    # Fail closed on a set-but-falsy model ("" / 0 / false): it would be
    # reported in the record below yet never exported, silently launching the
    # tool's default model while the record claims otherwise.
    if model is not None and not model:
        stderr_guard(
            f"roles.{role}.model is set but empty — set a model "
            f"identifier or remove the key"
        )
        return EXIT_FAIL
    effort = launch.get("effort")
    # Same fail-closed guard as model: a set-but-falsy effort would be
    # reported in the record below yet never exported.
    if effort is not None and not effort:
        stderr_guard(
            f"roles.{role}.effort is set but empty — set an effort "
            f"level or remove the key"
        )
        return EXIT_FAIL
    try:
        output_limits = output_safety.limits_from_environment(dict(os.environ))
    except output_safety.OutputSafetyError as exc:
        stderr_guard(f"invalid automated-handoff output contract: {exc}")
        return EXIT_FAIL

    # --- Fail-closed: the host must be able to wait out this handoff ---------
    # Launching is only half a handoff; the PM has to stay attached until the
    # report lands (run-handoff Stage 3). Every MCP host caps a single
    # tools/call, and on some hosts that cap is shorter than the protocol's
    # default 60m role timeout — so the wait dies mid-handoff and the assignee
    # keeps running unobserved. Refuse here, before the launch, rather than
    # discover it partway through the wait: an unlaunched handoff is
    # recoverable, an orphaned one is not.
    host_ok, host_budget, host_refusal = host_capability.check_wait_budget(
        role, host_capability.parse_duration(str(timeout)) or DEFAULT_TIMEOUT_SECONDS
    )
    if not host_ok:
        stderr_guard(host_refusal)
        return EXIT_FAIL

    task_id: Optional[str]
    source_guidance_record: Optional[Dict[str, Any]] = None
    if task_path is not None:
        from cli import numbering_contract

        refusal = numbering_contract.guard_existing_task_trace(
            project_root, task_path
        )
        if refusal is not None:
            stderr_guard(f"numbering trace invalid ({refusal[0]}): {refusal[1]}")
            return EXIT_FAIL
        activity = (
            "task_review"
            if task_path.parent.name == "in-review"
            else "task_run"
        )
        if activity not in role_record["auto_launch"]:
            stderr_guard(
                f"automatic {activity} dispatch is not enabled for role {role} "
                f"— add {activity!r} to roles.{role}.auto_launch, or present "
                f"the launch command to the operator"
            )
            return EXIT_FAIL
        # --- Fail-closed: the assignee prompt must exist ---------------------
        task_id = handoff_packet._extract_task_id(task_path) or task_path.stem
        nn_nnn = task_id.removeprefix("TASK-") if task_id.startswith("TASK-") else task_id
        prompt_path = (project_root / "prompts" / f"PROMPT-{nn_nnn}.md").resolve()
        if not prompt_path.is_file():
            stderr_guard(
                f"prompt not found: {prompt_path} — prepare the handoff prompt before "
                f"dispatching (run-handoff Stage 1)"
            )
            return EXIT_FAIL
        # Task review publishes to the independent review-report slot
        # (REPORT-NN-NNN-review.md). The task-completion report keeps its
        # compatibility path and is *preserved* for direct reviewer access —
        # the slot clear below therefore never touches coder evidence.
        expected_variant = "review" if activity == "task_review" else "task"
        expected_report_path = (
            handoff_packet._expected_review_report_path(project_root, task_id)
            if activity == "task_review"
            else handoff_packet._expected_report_path(project_root, task_id)
        )
        source_guidance_record = source_guidance.resolve_task_guidance(task_path)
        if source_guidance_record["outcome"] == "invalid":
            for blocker in source_guidance_record["blockers"]:
                stderr_guard(
                    f"{blocker['code']}: {blocker['detail']} — {blocker['recovery']}"
                )
            return EXIT_FAIL
    else:
        # --- Fail-closed: --prompt names an allowlisted planning slot only ---
        # Task prompts (PROMPT-NN-NNN) must dispatch by task path, which
        # enforces task/prompt/report agreement; --prompt would be a second,
        # weaker route to the same launch.
        task_id = None
        prompt_id = prompt_path.stem
        if (
            prompt_path.parent != project_root / "prompts"
            or prompt_path.suffix != ".md"
            or not prompt_id.startswith("PROMPT-PLAN-")
            or not PROMPT_ID_RE.match(prompt_id)
        ):
            stderr_guard(
                f"--prompt must name a planning-checkpoint prompt slot "
                f"(<project-root>/prompts/PROMPT-PLAN-NNN.md); got: {prompt_path}. "
                f"Task-scoped handoffs dispatch by task path"
            )
            return EXIT_FAIL
        # --- Fail-closed: planning-review automatic launch is explicit -------
        if "planning_review" not in role_record["auto_launch"]:
            stderr_guard(
                f"automatic planning-review dispatch is not enabled for role {role} — "
                f"add 'planning_review' to roles.{role}.auto_launch, or present "
                f"the launch command to the operator"
            )
            return EXIT_FAIL
        expected_report_path = report_identity.planning_report_path(
            project_root, prompt_id.removeprefix("PROMPT-")
        ).resolve()
        expected_variant = "planning-review"

    # --- Fail-closed: request-trace evidence is present and current --------
    # Review handoffs recompute applicability and the review-context identity at
    # the handoff boundary. Bypassing automatic launch must not bypass intent
    # resolution, so the identical preflight runs on the manual path through
    # `cartopian handoff-packet` / `cartopian review-context --prompt`.
    request_record: Optional[Dict[str, Any]] = None
    if activity in ("task_run", "task_review", "planning_review"):
        ok, request_record = _preflight_request_trace(
            project_root, activity, task_path, prompt_path
        )
        if not ok:
            stderr_guard(
                f"{request_record['rule']}: {request_record['detail']}"
            )
            if request_record.get("recovery"):
                stderr_guard(f"recovery: {request_record['recovery']}")
            return EXIT_FAIL

    # --- Fail-closed: declared work roots resolve and exist ------------------
    # The launch contract grants the assignee write access to the union of the
    # project root and the declared work roots. An unmapped root, or one whose
    # mapped path is missing on this machine, would launch an agent whose
    # work-root writes are doomed to fail mid-run — refuse up front instead.
    resolved_roots = resolved["work_roots"]
    work_root_paths = list(resolved_roots.values())
    missing_roots = [p for p in work_root_paths if not Path(p).is_dir()]
    if missing_roots:
        stderr_guard(
            "work root path(s) do not exist on this machine: "
            + ", ".join(missing_roots)
            + " — fix the [work_roots] mapping in cartopian.local.toml"
        )
        return EXIT_FAIL

    # --- Launch (per-invocation; non-blocking) -------------------------------
    # The launch contract: `<agent> <absolute prompt path>` as a single argv
    # argument, cwd = the cartopian project root, CARTOPIAN_TIMEOUT exported.
    # `start_new_session` detaches the wrapper so it runs in the background and
    # survives this short-lived invocation; we never wait() — the PM observes
    # completion via wait-handoff / wait-report. Dispatch sets where to run,
    # the deadline, and the role/config boundary. The Claude wrapper uses that
    # boundary only to attach its native enforcement hook; grant decisions
    # remain inside the hook. Resolved work roots let wrappers widen an agent
    # CLI sandbox to cover the declared work roots.
    launch_cwd = str(project_root)
    env = dict(os.environ)
    # Connected-host identity belongs to the MCP boundary.  It is evidence for
    # this preflight only and must not leak into the detached assignee.
    for private_host_marker in (
        host_capability.CONNECTED_ENV,
        host_capability.CLIENT_ENV,
        host_capability.CLIENT_VERSION_ENV,
        host_capability.CLIENT_TITLE_ENV,
        "CARTOPIAN_MCP_TOOL_CALL",
    ):
        env.pop(private_host_marker, None)
    launch_id = secrets.token_hex(16)
    env["CARTOPIAN_TIMEOUT"] = str(timeout)
    env["CARTOPIAN_LAUNCH_CWD"] = launch_cwd
    # Session-role marker for capability enforcement points (e.g. the Claude
    # Code refusal adapter, cli/claude_hook.py). Carries identity only — the
    # wrapper stays a neutral launcher and never keys behavior on it; the
    # enforcement point maps the role to grants via the resolved config.
    env["CARTOPIAN_ROLE"] = role
    env[HANDOFF_ID_ENV] = launch_id
    env[EXPECTED_VARIANT_ENV] = expected_variant
    env[EXPECTED_REPORT_ENV] = str(expected_report_path)
    # Per-launch hook settings must use the same valid interpreter running
    # this dispatch, not a path captured by an earlier install and not an
    # arbitrary `python3` found later in the wrapper's PATH.
    env[PYTHON_ENV] = sys.executable
    # Agent-neutral model selection from the resolved role launch record.
    # A stale value inherited from the parent environment is cleared when the
    # handoff sets no model, so the signal reflects this dispatch alone.
    if model:
        env[MODEL_ENV] = str(model)
    else:
        env.pop(MODEL_ENV, None)
    # Agent-neutral effort selection from the resolved role launch record,
    # cleared the same way when unset.
    if effort:
        env[EFFORT_ENV] = str(effort)
    else:
        env.pop(EFFORT_ENV, None)
    # Resolved work-root grant (os.pathsep-joined absolute paths); a stale
    # inherited value is cleared when the project declares no work roots.
    if work_root_paths:
        env[WORK_ROOTS_ENV] = os.pathsep.join(work_root_paths)
    else:
        env.pop(WORK_ROOTS_ENV, None)
    # Resolve the agent to a full path before launching. `subprocess.Popen` with
    # a bare name uses CreateProcess on native Windows, which resolves only
    # `.exe` — not the `.cmd` shim that exposes a PowerShell wrapper (CreateProcess
    # ignores PATHEXT). `shutil.which` DOES honor PATHEXT, so it finds the `.cmd`
    # on Windows and the extensionless wrapper script on POSIX. An absolute
    # role handoff agent resolves through `shutil.which` unchanged.
    resolved_agent = shutil.which(str(agent))
    if resolved_agent is None:
        stderr_error(
            f"handoff agent not found on PATH: {agent} — install the wrapper "
            f"(on native Windows the `.cmd` shim in wrappers/ps1 must be on PATH), "
            f"or set roles.{role}.agent to an absolute path"
        )
        return EXIT_FAIL
    try:
        slot_clear = _clear_handoff_slot(expected_report_path)
        status_path = Path(str(expected_report_path) + ".status")
        launch_log = output_safety.usable_log_path(
            Path(str(expected_report_path) + ".launch.log")
        )
        launch_log_path = str(launch_log) if launch_log is not None else None
        _publish_running_status(
            status_path,
            launch_id=launch_id,
            role=role,
            activity=activity,
            expected_variant=expected_variant,
            launch_log_path=launch_log_path,
        )
    except _CliError as err:
        stderr_guard(err.message)
        return err.exit_code
    is_windows = _running_on_windows()
    launch_argv = _build_launch_argv(resolved_agent, str(prompt_path), is_windows)
    # The detached supervisor continuously drains the configured wrapper
    # through a pipe and atomically publishes only the bounded retained log.
    # Bytes outside that representation are discarded without affecting the
    # wrapper process or its lifecycle result.
    output_safety.project_environment(env, output_limits, launch_log)
    supervisor_argv = [
        sys.executable,
        str(Path(output_safety.__file__).resolve()),
        "--status-path",
        str(status_path),
        "--report-path",
        str(expected_report_path),
        "--launch-id",
        launch_id,
        "--expected-variant",
        expected_variant,
        "--log-bytes",
        str(output_limits.log_bytes),
        "--log-lines",
        str(output_limits.log_lines),
    ]
    if launch_log_path is not None:
        supervisor_argv.extend(["--log-path", launch_log_path])
    supervisor_argv.extend(["--", *launch_argv])
    proc = None
    try:
        proc = subprocess.Popen(  # noqa: S603 — agent is operator-configured, not PM input
            supervisor_argv,
            cwd=launch_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        _remove_own_running_status(status_path, launch_id)
        # `which` already resolved the agent, so a FileNotFoundError here points
        # at the *launch chain*, not the agent: most often the Windows command
        # interpreter (an absent COMSPEC / unreachable cmd.exe) when routing a
        # `.cmd` shim. Surface the missing file so the cause is unambiguous.
        missing = getattr(exc, "filename", None) or launch_argv[0]
        stderr_error(
            f"failed to launch handoff agent {agent}: could not start "
            f"{missing!r} (resolved agent: {resolved_agent}). On native Windows "
            f"this is usually the command interpreter — ensure cmd.exe is "
            f"reachable; otherwise correct roles.{role}.agent"
        )
        return EXIT_FAIL
    except OSError as exc:
        _remove_own_running_status(status_path, launch_id)
        stderr_error(f"failed to launch handoff agent {agent}: {exc}")
        return EXIT_FAIL

    record: Dict[str, Any] = {
        "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
        "schema_identity": resolved["schema_identity"],
        "project_schema_version": resolved["project_schema_version"],
        "task_id": task_id,
        "prompt_id": prompt_path.stem,
        "role": role,
        "activity": activity,
        "launch": {
            "agent": agent,
            "model": model,
            "effort": effort,
            "timeout": timeout,
        },
        "work_roots": work_root_paths,
        "prompt_path": str(prompt_path),
        "expected_report_path": str(expected_report_path),
        "timeout": timeout,
        "cwd": launch_cwd,
        "launch_log_path": launch_log_path,
        "output_safety": {
            **output_limits.as_record(),
            "guarantee_scope": output_safety.GUARANTEE_SCOPE,
        },
        "pid": proc.pid,
        "launch_id": launch_id,
        "expected_report_variant": expected_variant,
        "slot_clear": slot_clear,
        "status": "dispatched",
        "request_trace": request_record,
        "source_guidance": (
            source_guidance.active_projection(source_guidance_record)
            if source_guidance_record is not None
            else None
        ),
        # The wait budget this launch was cleared against. `null` when the CLI
        # ran outside an MCP host, where no tools/call ceiling applies.
        "host_wait_budget": host_budget.record() if host_budget is not None else None,
    }
    emit_record(record)
    return EXIT_OK
