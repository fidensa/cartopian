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
    launch_preflight,
    output_safety,
    report_identity,
    request_trace,
    source_guidance,
)
from cli.commands import handoff_packet
from cli.commands._writers import PROMPT_ID_RE
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_deliverable,
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
WINDOWS_AGENT_ENV = "CARTOPIAN_WINDOWS_AGENT_EXECUTABLE"
WINDOWS_PROMPT_ENV = "CARTOPIAN_WINDOWS_PROMPT_PATH"

# The detached launch chain starts Python and, for the POSIX Claude wrapper,
# Bash and Node before Claude's sandbox exists.  These inherited controls can
# execute startup code in those host processes (BASH_ENV, exported functions,
# LD_PRELOAD/DYLD_*, PYTHONPATH/sitecustomize, NODE_OPTIONS=--require, etc.).
# Strip them in the trusted dispatch parent rather than attempting a check in
# the child after its interpreter/loader has already consumed them.
_PRECONTAINMENT_ENV_KEYS = frozenset(
    {
        "BASHOPTS",
        "BASH_ENV",
        "BASH_XTRACEFD",
        "ENV",
        "GCONV_PATH",
        "GLIBC_TUNABLES",
        "__PYVENV_LAUNCHER__",
        "NODE_OPTIONS",
        "NODE_PATH",
        "OPENSSL_CONF",
        "OPENSSL_MODULES",
        "PS4",
        "SHELLOPTS",
    }
)
_PRECONTAINMENT_ENV_PREFIXES = (
    "BASH_FUNC_",
    "BUN_",
    "DYLD_",
    "LD_",
    "PYTHON",
)


def _sanitized_launch_environment(source: Dict[str, str]) -> Dict[str, str]:
    """Copy ``source`` without interpreter/loader startup injection knobs."""
    return {
        key: value
        for key, value in source.items()
        if key not in _PRECONTAINMENT_ENV_KEYS
        and not key.startswith(_PRECONTAINMENT_ENV_PREFIXES)
    }


def _bind_activated_claude_host_temp(
    agent: object,
    *,
    capabilities_activated: bool,
    environ: Dict[str, str],
    create: bool = False,
) -> Optional[str]:
    """Replace inherited temp controls for one trusted activated Claude launch.

    Claude's host-side Sandbox Runtime mux opens its Unix socket before a Bash
    command enters the OS sandbox.  Its location must therefore be bound by
    dispatch, not supplied by the operator environment or by product content.
    """
    if not capabilities_activated or _running_on_windows():
        return None
    resolved_agent = shutil.which(str(agent), path=environ.get("PATH"))
    if (
        not launch_preflight._is_cartopian_claude_agent(agent, resolved_agent)
        or launch_preflight._cartopian_claude_install_root(resolved_agent) is None
    ):
        return None

    from cli.claude_launch_settings import (
        CLAUDE_HOST_TMPDIR_ENV,
        prepare_claude_host_tmpdir,
    )

    # This is a per-dispatch binding. Never accept a parent-provided value,
    # even if it happens to name a directory that currently looks safe.
    for temp_key in (CLAUDE_HOST_TMPDIR_ENV, "TMPDIR", "TMP", "TEMP"):
        environ.pop(temp_key, None)
    host_tmp = prepare_claude_host_tmpdir(
        environ,
        windows=False,
        create=create,
        allow_missing=not create,
    )
    environ[CLAUDE_HOST_TMPDIR_ENV] = host_tmp
    environ["TMPDIR"] = host_tmp
    prepare_claude_host_tmpdir(
        environ,
        windows=False,
        allow_missing=not create,
        require_binding=True,
    )
    return host_tmp

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
        # cmd.exe reparses metacharacters in ordinary argv after /c. Keep the
        # command text fixed and carry both operator-selected paths in quoted
        # environment expansions; Windows filenames cannot contain a quote,
        # and expansion values are not recursively percent-expanded.
        # The outer quote pair is cmd.exe's standard /s /c form for a quoted
        # executable. Do not use CALL: CALL performs a second percent-expansion
        # pass over values introduced by the first environment expansion.
        command = (
            f'""%{WINDOWS_AGENT_ENV}%" '
            f'"%{WINDOWS_PROMPT_ENV}%""'
        )
        # Disable delayed expansion explicitly so a literal ``!`` in either
        # transported path survives the outer command interpreter unchanged.
        return [_resolve_comspec(), "/d", "/v:off", "/s", "/c", command]
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
    # --- Fail-closed role prerequisites (shared with the dispatch rehearsal) --
    # Agent configured; set-but-empty model/effort (they would be reported in
    # the record yet never exported); the output contract; and the host wait
    # budget — launching is only half a handoff, and a host tools/call ceiling
    # shorter than the role timeout would orphan the assignee mid-run. The
    # rehearsal applies the identical checks, so readiness never claims a
    # launch this preflight would refuse.
    for finding in launch_preflight.role_checks(role, role_record, context="dispatch"):
        stderr_guard(finding["message"])
        return EXIT_FAIL
    agent = launch.get("agent")
    timeout = launch.get("timeout") or DEFAULT_TIMEOUT
    model = launch.get("model")
    effort = launch.get("effort")
    output_limits = output_safety.limits_from_environment(dict(os.environ))
    _host_ok, host_budget, _host_refusal = host_capability.check_wait_budget(
        role,
        host_capability.parse_duration(str(timeout)) or DEFAULT_TIMEOUT_SECONDS,
        context="dispatch",
    )

    task_id: Optional[str]
    source_guidance_record: Optional[Dict[str, Any]] = None
    existing_deliverable_input: Optional[Dict[str, Any]] = None
    dependency_deliverable_inputs: Optional[List[Dict[str, Any]]] = None
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
        try:
            task_content = task_path.read_text(encoding="utf-8")
            # newline="" keeps payload bytes exact for digest verification.
            with prompt_path.open("r", encoding="utf-8", newline="") as handle:
                prompt_text = handle.read()
            project_cfg = _load_toml(project_toml, "project config") or {}
            deliverable = _resolve_deliverable(
                project_cfg,
                project_root,
                handoff_packet._deliverable_value(task_content),
            )
        except (OSError, UnicodeDecodeError) as exc:
            stderr_guard(f"handoff input unreadable: {exc}")
            return EXIT_FAIL
        except _CliError as exc:
            stderr_guard(exc.message)
            return exc.exit_code
        existing_deliverable_input = handoff_packet._existing_deliverable_input(
            deliverable,
            role_record["effective_grants"],
            prompt_text=prompt_text,
        )
        if (
            existing_deliverable_input["required"]
            and existing_deliverable_input["ok"] is False
        ):
            stderr_guard(
                "existing-deliverable-input-unavailable: "
                + handoff_packet._existing_deliverable_refusal(
                    existing_deliverable_input
                )
            )
            return EXIT_FAIL
        # --- Fail-closed: upstream dependency deliverables are readable ------
        # A task that builds on a dependency's governance-scoped deliverable
        # must not launch unless that upstream contract exists and is either
        # role-readable or curated verbatim into the prompt.
        dependency_deliverable_inputs = (
            handoff_packet._dependency_deliverable_inputs(
                project_root,
                project_cfg,
                task_content,
                role_record["effective_grants"],
                prompt_text=prompt_text,
            )
        )
        failed_dependencies = [
            item
            for item in dependency_deliverable_inputs
            if item["required"] and item["ok"] is False
        ]
        if failed_dependencies:
            for item in failed_dependencies:
                stderr_guard(
                    "dependency-deliverable-input-unavailable: "
                    + handoff_packet._dependency_deliverable_refusal(item)
                )
            return EXIT_FAIL
        # --- Fail-closed: every declared payload is a real, current input ----
        # A payload declaration the machine did not resolve — hand-authored,
        # duplicated, malformed, or stale against the resource on disk —
        # refuses the launch; the typed channel must never become a bypass.
        payload_audit = handoff_packet.audit_prompt_payloads(
            project_root,
            project_cfg,
            task_content,
            deliverable,
            prompt_text,
        )
        if not payload_audit["ok"]:
            for problem in payload_audit["problems"]:
                stderr_guard(f"input-payload-audit: {problem}")
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
    # Shared with the rehearsal: mapped work roots exist and the agent resolves
    # on PATH (`shutil.which` honors PATHEXT, so the `.cmd` shim is found on
    # native Windows and the extensionless wrapper on POSIX).
    launch_bindings: Dict[str, str] = {}
    launch_environment = _sanitized_launch_environment(dict(os.environ))
    try:
        _bind_activated_claude_host_temp(
            agent,
            capabilities_activated=bool(resolved["capabilities"]["activated"]),
            environ=launch_environment,
        )
    except Exception as exc:
        # The helper raises SettingsError, but keep this launch boundary
        # fail-closed if filesystem inspection itself reports an unexpected
        # platform error. No assignee process has started at this point.
        stderr_guard(f"activated Claude host-temp binding failed: {exc}")
        return EXIT_FAIL
    for finding in launch_preflight.environment_checks(
        role,
        role_record,
        resolved_roots,
        project_root=project_root,
        capabilities_activated=bool(resolved["capabilities"]["activated"]),
        launch_bindings=launch_bindings,
        environ=launch_environment,
    ):
        if finding["prefix"] == "error":
            stderr_error(finding["message"])
        else:
            stderr_guard(finding["message"])
        return EXIT_FAIL
    try:
        _bind_activated_claude_host_temp(
            agent,
            capabilities_activated=bool(resolved["capabilities"]["activated"]),
            environ=launch_environment,
            create=True,
        )
    except Exception as exc:
        stderr_guard(f"activated Claude host-temp binding failed: {exc}")
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
    env = launch_environment
    # This is a per-dispatch binding, never an inherited operator/session
    # override. It is repopulated below after the underlying Claude binary is
    # resolved and checked against the governed roots.
    env.pop("CARTOPIAN_CLAUDE_EXECUTABLE", None)
    # Connected-host identity belongs to the MCP boundary.  It is evidence for
    # this preflight only and must not leak into the detached assignee. The
    # trusted host marker and its Hermes home companion override clientInfo,
    # so inheriting them would misclassify nested Cartopian processes and
    # restart context as the launching host.
    for private_host_marker in (
        host_capability.CONNECTED_ENV,
        host_capability.CLIENT_ENV,
        host_capability.CLIENT_VERSION_ENV,
        host_capability.CLIENT_TITLE_ENV,
        host_capability.HOST_MARKER_ENV,
        host_capability.HERMES_HOME_ENV,
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
    # Launch exactly the path resolved and validated by the shared preflight.
    # Repeating PATH resolution here would reopen a check/use race and, on
    # native Windows, could select a different PATHEXT shim.
    resolved_agent = launch_bindings.get("agent")
    if resolved_agent is None:  # pragma: no cover - environment_checks refused above
        stderr_error(f"handoff agent not found on PATH: {agent}")
        return EXIT_FAIL
    if (
        launch_preflight._is_cartopian_claude_agent(agent, resolved_agent)
        and launch_preflight._cartopian_claude_install_root(resolved_agent)
        is not None
    ):
        from cli.claude_launch_settings import (
            CLAUDE_EXECUTABLE_ENV,
        )

        claude_executable = launch_bindings.get("claude_executable")
        if claude_executable is None:  # pragma: no cover - preflight owns refusal
            stderr_guard("trusted Claude launch is missing its checked executable binding")
            return EXIT_FAIL
        # Freeze the exact executable selected at the trusted dispatch boundary.
        # The wrapper uses this path for both the version probe and the launch.
        env[CLAUDE_EXECUTABLE_ENV] = claude_executable
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
    if is_windows and resolved_agent.lower().endswith((".cmd", ".bat")):
        env[WINDOWS_AGENT_ENV] = resolved_agent
        env[WINDOWS_PROMPT_ENV] = str(prompt_path)
    else:
        env.pop(WINDOWS_AGENT_ENV, None)
        env.pop(WINDOWS_PROMPT_ENV, None)
    launch_argv = _build_launch_argv(resolved_agent, str(prompt_path), is_windows)
    # The detached supervisor continuously drains the configured wrapper
    # through a pipe and atomically publishes only the bounded retained log.
    # Bytes outside that representation are discarded without affecting the
    # wrapper process or its lifecycle result.
    output_safety.project_environment(env, output_limits, launch_log)
    supervisor_argv = [
        sys.executable,
        "-I",
        "-S",
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
        "existing_deliverable_input": existing_deliverable_input,
        "dependency_deliverable_inputs": dependency_deliverable_inputs,
        # The wait budget this launch was cleared against. `null` when the CLI
        # ran outside an MCP host, where no tools/call ceiling applies.
        "host_wait_budget": host_budget.record() if host_budget is not None else None,
    }
    emit_record(record)
    return EXIT_OK
