"""`cartopian dispatch <task-path> --role <role>` — mediated handoff launch.

The delegation counterpart to the mediated writer. A contained PM has no
shell or process-exec tool, so it cannot launch an assignee wrapper itself. This
command performs the launch on the PM's behalf as *per-invocation* Cartopian code
(no daemon, no broker): it composes the existing ``handoff-packet`` /
``resolve-config`` aggregators to prepare the packet, fails closed on a missing
``[handoffs.<role>]`` block / a missing agent / a missing prompt, exports
``CARTOPIAN_TIMEOUT`` from the resolved ``[handoffs.<role>].timeout``,
``CARTOPIAN_MODEL`` from the resolved ``[handoffs.<role>].model`` (when set), and
``CARTOPIAN_ROLE`` from the dispatched role (the session-role marker capability
enforcement points such as ``cli/claude_hook.py`` read), and
launches the configured wrapper with the single absolute-prompt-path argv from the
cartopian project-root cwd (the launch contract fixed by
``protocol/CONVENTIONS.md`` § Handoffs / Launch Directory). Capability gating of
the launched agent is the harness's job, not the launcher's.

It returns once the wrapper is launched — it does **not** block to completion. The
wrapper owns its own background/timeout semantics (it kills the assignee at the
``CARTOPIAN_TIMEOUT`` deadline, exit ``124``). The PM then observes the result
through ``cartopian wait-handoff`` / ``cartopian wait-report``; this command never
adds a waiting mechanism and never reaps the child.

The launched executable is always the operator-configured ``[handoffs.<role>].agent``.
There is no caller-supplied command or executable argument, so the PM cannot use
dispatch to launch an arbitrary process — the mediated, config-bound path is the
only route a contained PM has. Standard library only (NF-001).
"""
import argparse
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from cli.commands import handoff_packet
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_handoffs,
)
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

# Agent-neutral model selection. Exported from the resolved
# ``[handoffs.<role>].model`` so the wrapper can translate it into the
# tool-specific model flag; never exported when the handoff sets no model
# (the tool's own default model applies).
MODEL_ENV = "CARTOPIAN_MODEL"

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


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for dispatch.

    Deliberately minimal: a task path and a role. The executable launched is
    sourced exclusively from ``[handoffs.<role>].agent`` in config — there is
    intentionally no flag to supply an arbitrary command, so the PM cannot turn
    dispatch into a raw exec primitive (containment invariant).
    """
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file whose handoff to launch",
    )
    subparser.add_argument(
        "--role",
        required=True,
        help="Role identifier being dispatched (must have a [handoffs.<role>] block)",
    )


def handler(args: argparse.Namespace) -> int:
    """Prepare the packet, validate fail-closed, launch the wrapper, emit NDJSON."""
    raw_path: str = args.task_path
    role: str = args.role

    if not Path(raw_path).is_absolute():
        stderr_usage(f"task_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    task_path = Path(raw_path)
    if not task_path.is_file():
        stderr_error(f"task file not found: {raw_path}")
        return EXIT_FAIL
    task_path = task_path.resolve()

    project_root = handoff_packet._find_project_root(task_path)
    if project_root is None:
        stderr_error(f"project config not found for task: {raw_path}")
        return EXIT_ENV

    project_toml = project_root / "cartopian.toml"
    if not project_toml.is_file():
        stderr_error(f"project config not found: {project_toml}")
        return EXIT_ENV

    try:
        project_cfg = _load_toml(project_toml, "project config") or {}
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    global_toml = Path.home() / ".cartopian" / "cartopian.toml"
    try:
        global_cfg = _load_toml(global_toml, "global config") or {}
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    # --- Fail-closed: a configured [handoffs.<role>] block with an agent -----
    raw_handoffs_project = project_cfg.get("handoffs", {}) or {}
    raw_handoffs_global = global_cfg.get("handoffs", {}) or {}
    if role not in raw_handoffs_project and role not in raw_handoffs_global:
        stderr_guard(
            f"no [handoffs.{role}] block configured — declare it in the project "
            f"or global cartopian.toml, or dispatch this role manually"
        )
        return EXIT_FAIL

    handoffs = _resolve_handoffs(global_cfg, project_cfg)
    role_handoff = handoffs.get(role, {}) or {}
    agent = role_handoff.get("agent")
    if not agent:
        stderr_guard(
            f"[handoffs.{role}] has no agent configured — set agent in the "
            f"project or global cartopian.toml, or dispatch this role manually"
        )
        return EXIT_FAIL

    timeout = role_handoff.get("timeout") or DEFAULT_TIMEOUT
    model = role_handoff.get("model")
    # Fail closed on a set-but-falsy model ("" / 0 / false): it would be
    # reported in the record below yet never exported, silently launching the
    # tool's default model while the record claims otherwise.
    if model is not None and not model:
        stderr_guard(
            f"[handoffs.{role}].model is set but empty — set a model "
            f"identifier or remove the key"
        )
        return EXIT_FAIL

    # --- Fail-closed: the assignee prompt must exist -------------------------
    task_id = handoff_packet._extract_task_id(task_path) or task_path.stem
    nn_nnn = task_id.removeprefix("TASK-") if task_id.startswith("TASK-") else task_id
    prompt_path = (project_root / "prompts" / f"PROMPT-{nn_nnn}.md").resolve()
    if not prompt_path.is_file():
        stderr_guard(
            f"prompt not found: {prompt_path} — prepare the handoff prompt before "
            f"dispatching (run-handoff Stage 1)"
        )
        return EXIT_FAIL

    expected_report_path = handoff_packet._expected_report_path(project_root, task_id)

    # --- Launch (per-invocation; non-blocking) -------------------------------
    # The launch contract: `<agent> <absolute prompt path>` as a single argv
    # argument, cwd = the cartopian project root, CARTOPIAN_TIMEOUT exported.
    # `start_new_session` detaches the wrapper so it runs in the background and
    # survives this short-lived invocation; we never wait() — the PM observes
    # completion via wait-handoff / wait-report. The wrapper is a neutral
    # launcher: dispatch sets where to run and the deadline; it does not scope
    # the agent's filesystem access (capability gating is the harness's job).
    launch_cwd = str(project_root)
    env = dict(os.environ)
    env["CARTOPIAN_TIMEOUT"] = str(timeout)
    env["CARTOPIAN_LAUNCH_CWD"] = launch_cwd
    # Session-role marker for capability enforcement points (e.g. the Claude
    # Code refusal adapter, cli/claude_hook.py). Carries identity only — the
    # wrapper stays a neutral launcher and never keys behavior on it; the
    # enforcement point maps the role to grants via the resolved config.
    env["CARTOPIAN_ROLE"] = role
    # Agent-neutral model selection from the resolved [handoffs.<role>].model.
    # A stale value inherited from the parent environment is cleared when the
    # handoff sets no model, so the signal reflects this dispatch alone.
    if model:
        env[MODEL_ENV] = str(model)
    else:
        env.pop(MODEL_ENV, None)
    # Resolve the agent to a full path before launching. `subprocess.Popen` with
    # a bare name uses CreateProcess on native Windows, which resolves only
    # `.exe` — not the `.cmd` shim that exposes a PowerShell wrapper (CreateProcess
    # ignores PATHEXT). `shutil.which` DOES honor PATHEXT, so it finds the `.cmd`
    # on Windows and the extensionless wrapper script on POSIX. An absolute
    # `[handoffs.<role>].agent` resolves through `shutil.which` unchanged.
    resolved_agent = shutil.which(str(agent))
    if resolved_agent is None:
        stderr_error(
            f"handoff agent not found on PATH: {agent} — install the wrapper "
            f"(on native Windows the `.cmd` shim in wrappers/ps1 must be on PATH), "
            f"or set [handoffs.{role}].agent to an absolute path"
        )
        return EXIT_FAIL
    launch_argv = _build_launch_argv(resolved_agent, str(prompt_path), os.name == "nt")
    try:
        proc = subprocess.Popen(  # noqa: S603 — agent is operator-configured, not PM input
            launch_argv,
            cwd=launch_cwd,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        # `which` already resolved the agent, so a FileNotFoundError here points
        # at the *launch chain*, not the agent: most often the Windows command
        # interpreter (an absent COMSPEC / unreachable cmd.exe) when routing a
        # `.cmd` shim. Surface the missing file so the cause is unambiguous.
        missing = getattr(exc, "filename", None) or launch_argv[0]
        stderr_error(
            f"failed to launch handoff agent {agent}: could not start "
            f"{missing!r} (resolved agent: {resolved_agent}). On native Windows "
            f"this is usually the command interpreter — ensure cmd.exe is "
            f"reachable; otherwise correct [handoffs.{role}].agent"
        )
        return EXIT_FAIL
    except OSError as exc:
        stderr_error(f"failed to launch handoff agent {agent}: {exc}")
        return EXIT_FAIL

    record: Dict[str, Any] = {
        "task_id": task_id,
        "role": role,
        "handoff_target": agent,
        "model": model,
        "prompt_path": str(prompt_path),
        "expected_report_path": str(expected_report_path),
        "timeout": timeout,
        "cwd": launch_cwd,
        "pid": proc.pid,
        "status": "dispatched",
    }
    emit_record(record)
    return EXIT_OK
