"""`cartopian dispatch <task-path> --role <role>` — mediated handoff launch (FR-006, G20).

The delegation counterpart to the mediated writer. A contained PM (FR-002) has no
shell or process-exec tool, so it cannot launch an assignee wrapper itself. This
command performs the launch on the PM's behalf as *per-invocation* Cartopian code
(no daemon, no broker — NF-002): it composes the existing ``handoff-packet`` /
``resolve-config`` aggregators to prepare the packet, fails closed on unmapped or
missing work roots / a missing ``[handoffs.<role>]`` block / a missing prompt,
exports ``CARTOPIAN_TIMEOUT`` from the resolved ``[handoffs.<role>].timeout``, and
launches the configured wrapper with the single absolute-prompt-path argv from the
cartopian project-root cwd (the launch contract fixed by
``protocol/CONVENTIONS.md`` § Handoffs / Launch Directory).

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
import subprocess
from pathlib import Path
from typing import Any, Dict

from cli.commands import handoff_packet
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_handoffs,
    _resolve_work_roots,
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


def _stderr_work_root(msg: str) -> None:
    """Emit the fail-closed ``[work-root]`` stderr line the wrappers also use."""
    import sys

    sys.stderr.write(f"[work-root] {msg}\n")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for dispatch.

    Deliberately minimal: a task path and a role. The executable launched is
    sourced exclusively from ``[handoffs.<role>].agent`` in config — there is
    intentionally no flag to supply an arbitrary command, so the PM cannot turn
    dispatch into a raw exec primitive (FR-002 containment).
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

    # --- Fail-closed: every declared work root must map and exist on disk ----
    # resolve-config raises a `[work-root]` _CliError on an unmapped or
    # non-absolute name; on-disk existence is the wrapper's additional
    # fail-closed gate, re-checked here so dispatch never launches against a
    # work root the wrapper would itself refuse.
    try:
        resolved_roots = _resolve_work_roots(project_cfg, project_root)
    except _CliError as err:
        if err.prefix == "work-root":
            _stderr_work_root(err.message)
        else:
            stderr_error(err.message)
        return err.exit_code

    project_table = project_cfg.get("project", {}) or {}
    declared_names = project_table.get("work_roots", []) or []
    work_roots = []
    for name in declared_names:
        abs_path = resolved_roots.get(name)
        if not abs_path or not Path(abs_path).exists():
            _stderr_work_root(
                f"does not exist: {name} -> {abs_path} — fix the mapping in "
                f"{project_root / 'cartopian.local.toml'} or remove the declaration"
            )
            return EXIT_FAIL
        work_roots.append({"name": name, "absolute_path": abs_path})

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
    # The launch contract (CONVENTIONS.md § Handoffs): `<agent> <absolute prompt
    # path>` as a single argv argument, cwd = the cartopian project root,
    # CARTOPIAN_TIMEOUT exported. `start_new_session` detaches the wrapper so it
    # runs in the background and survives this short-lived invocation; we never
    # wait() — the PM observes completion via wait-handoff / wait-report.
    env = dict(os.environ)
    env["CARTOPIAN_TIMEOUT"] = str(timeout)
    try:
        proc = subprocess.Popen(  # noqa: S603 — agent is operator-configured, not PM input
            [str(agent), str(prompt_path)],
            cwd=str(project_root),
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        stderr_error(
            f"handoff agent not found: {agent} — install the wrapper on PATH or "
            f"correct [handoffs.{role}].agent"
        )
        return EXIT_FAIL
    except OSError as exc:
        stderr_error(f"failed to launch handoff agent {agent}: {exc}")
        return EXIT_FAIL

    record: Dict[str, Any] = {
        "task_id": task_id,
        "role": role,
        "handoff_target": agent,
        "prompt_path": str(prompt_path),
        "expected_report_path": str(expected_report_path),
        "timeout": timeout,
        "cwd": str(project_root),
        "work_roots": work_roots,
        "pid": proc.pid,
        "status": "dispatched",
    }
    emit_record(record)
    return EXIT_OK
