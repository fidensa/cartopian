"""`cartopian dispatch <task-path> --role <role>` — mediated handoff launch.

The delegation counterpart to the mediated writer. A contained PM has no
shell or process-exec tool, so it cannot launch an assignee wrapper itself. This
command performs the launch on the PM's behalf as *per-invocation* Cartopian code
(no daemon, no broker): it composes the existing ``handoff-packet`` /
``resolve-config`` aggregators to prepare the packet, fails closed on unmapped or
missing work roots / a missing ``[handoffs.<role>]`` block / a missing prompt,
exports ``CARTOPIAN_TIMEOUT`` from the resolved ``[handoffs.<role>].timeout`` and
``CARTOPIAN_MODEL`` from the resolved ``[handoffs.<role>].model`` (when set), and
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
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

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

# Agent-neutral, role-level reviewer live-evidence recapture signal.
# When the operator opts a reviewer handoff into recapture (`--recapture`)
# AND the task declares live/harness evidence (`Evidence gate: required`), dispatch
# exports this to the wrapper environment. Every shipped wrapper honors it
# identically via the shared `cartopian_review_recapture_active` launch helper —
# the env var carries NO agent name, so the capability attaches to the reviewer
# role, not to any one agent. Default: never exported (opt-in).
RECAPTURE_ENV = "CARTOPIAN_REVIEW_RECAPTURE"

# Agent-neutral model selection. Exported from the resolved
# ``[handoffs.<role>].model`` so the wrapper can translate it into the
# tool-specific model flag; never exported when the handoff sets no model
# (the tool's own default model applies).
MODEL_ENV = "CARTOPIAN_MODEL"

# Match the task-file header `Evidence gate: required | n/a` (case-insensitive
# field; value compared case-insensitively). Mirrors templates/TASK.md and
# cli/commands/validate_task_readiness.py.
_EVIDENCE_GATE_RE = re.compile(r"(?im)^Evidence gate:\s*(\S+)\s*$")


def _task_declares_live_evidence(task_path: Path) -> bool:
    """True when the task file declares a live/harness evidence gate.

    The structured, machine-readable declaration is the `Evidence gate: required`
    header (templates/TASK.md). A task with `n/a` or no gate — the common shape of
    research / ops / creative reviews — is NOT evidence-gated, so recapture must
    never be enabled for it (the domain-neutral guard). Any read error degrades to
    False (fail-closed: no recapture)."""
    try:
        text = task_path.read_text(encoding="utf-8")
    except OSError:
        return False
    m = _EVIDENCE_GATE_RE.search(text)
    return bool(m) and m.group(1).strip().lower() == "required"


def _stderr_work_root(msg: str) -> None:
    """Emit the fail-closed ``[work-root]`` stderr line the wrappers also use."""
    import sys

    sys.stderr.write(f"[work-root] {msg}\n")


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
    subparser.add_argument(
        "--recapture",
        action="store_true",
        help=(
            "Opt this reviewer handoff into agent-agnostic live-evidence recapture: "
            "the assignee may re-run the task's probe harness to "
            "reproduce the live evidence instead of trusting the pinned artifacts. "
            "Only valid for a task that declares live/harness evidence "
            "(Evidence gate: required); exports CARTOPIAN_REVIEW_RECAPTURE=1 so the "
            "wrapper keeps the reviewed source read-only while granting probe egress."
        ),
    )


def handler(args: argparse.Namespace) -> int:
    """Prepare the packet, validate fail-closed, launch the wrapper, emit NDJSON."""
    raw_path: str = args.task_path
    role: str = args.role
    recapture: bool = getattr(args, "recapture", False)

    if not Path(raw_path).is_absolute():
        stderr_usage(f"task_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    task_path = Path(raw_path)
    if not task_path.is_file():
        stderr_error(f"task file not found: {raw_path}")
        return EXIT_FAIL
    task_path = task_path.resolve()

    # --- Fail-closed: recapture is opt-in AND evidence-gated -----------------
    # `--recapture` is only meaningful for a reviewer handoff on a task that
    # declares live/harness evidence. Refuse it on a task with no such gate so a
    # research / ops / creative review (Evidence gate: n/a or absent) can never
    # be silently granted probe egress (domain-neutral guard).
    if recapture and not _task_declares_live_evidence(task_path):
        stderr_guard(
            "--recapture requires a task that declares live/harness evidence "
            "(Evidence gate: required); this task does not, so recapture is "
            "refused — dispatch it without --recapture"
        )
        return EXIT_FAIL

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

    # --- Assignee launch scope excludes the governing project ---------------
    # The assignee's filesystem world is the work root, not this management
    # project, so it cannot read management artifacts (requirements, decisions,
    # tasks, backlog, state). Launch cwd = the primary (first declared) work
    # root; the scoped union is the resolved work roots plus only the
    # report-target directory. This launcher computes the scope and passes it to
    # the wrapper explicitly via CARTOPIAN_SCOPE_DIRS — once cwd is the work root
    # the wrapper can no longer re-derive the right scope from its own cwd. The
    # report still lands at the explicit absolute report path the prompt names;
    # only the browse scope narrows (the report dir is the lone in-scope path
    # inside the management project).
    report_dir = str(Path(expected_report_path).parent)
    work_root_dirs = [wr["absolute_path"] for wr in work_roots]
    primary_work_root = work_root_dirs[0] if work_root_dirs else None

    # Fail closed: an assignee must run inside a work root, never the governing
    # project. With no resolved work root there is no contained workspace, so we
    # refuse rather than launch in the project root and expose its PM artifacts.
    if primary_work_root is None:
        stderr_guard(
            "no work root resolved for this dispatch — an assignee must run "
            "inside a work root, not the governing project. Declare "
            "[project].work_roots and map them in cartopian.local.toml, or "
            "dispatch this role manually."
        )
        return EXIT_FAIL

    # Defense in depth: refuse if any granted work root is the governing project
    # root or an ancestor of it. A work root misconfigured to the project (or a
    # parent) would make the project's management artifacts readable. The report
    # dir is a descendant of the project root, not an ancestor, so it never trips.
    proj_real = os.path.realpath(str(project_root))
    for d in work_root_dirs:
        d_real = os.path.realpath(str(d))
        if d_real == proj_real or proj_real.startswith(d_real + os.sep):
            _stderr_work_root(
                f"work root {d_real} is the governing project root, or an "
                f"ancestor of it — that would expose the project's management "
                f"artifacts. Point the work root at the product tree instead."
            )
            return EXIT_FAIL

    launch_cwd = primary_work_root
    # The report dir is the assignee's output target; ensure it exists so the
    # wrapper's fail-closed on-disk scope check does not refuse a first report.
    try:
        Path(report_dir).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # The effective writable grant the wrapper applies: the work roots plus the
    # report dir (recorded for the NDJSON; the env split below keeps the report
    # dir separable so recapture can withhold the work roots yet still grant it).
    grant_dirs = list(work_root_dirs)
    if report_dir not in grant_dirs:
        grant_dirs.append(report_dir)

    # --- Launch (per-invocation; non-blocking) -------------------------------
    # The launch contract: `<agent> <absolute prompt path>` as a single argv
    # argument, cwd = the primary work root, CARTOPIAN_TIMEOUT exported.
    # `start_new_session` detaches the wrapper so it runs in the background and
    # survives this short-lived invocation; we never wait() — the PM observes
    # completion via wait-handoff / wait-report.
    env = dict(os.environ)
    env["CARTOPIAN_TIMEOUT"] = str(timeout)
    # The work roots are the assignee's primary scope; the report dir is exported
    # separately so it stays writable even when the work roots are withheld as
    # read-only source under reviewer recapture.
    env["CARTOPIAN_LAUNCH_CWD"] = launch_cwd
    env["CARTOPIAN_SCOPE_DIRS"] = "\n".join(work_root_dirs)
    env["CARTOPIAN_REPORT_DIR"] = report_dir
    # Agent-neutral model selection from the resolved [handoffs.<role>].model.
    # A stale value inherited from the parent environment is cleared when the
    # handoff sets no model, so the signal reflects this dispatch alone.
    if model:
        env[MODEL_ENV] = str(model)
    else:
        env.pop(MODEL_ENV, None)
    # Agent-neutral, opt-in, evidence-gated reviewer-recapture signal. Exported
    # ONLY when both gates above held (recapture requested AND the task declares
    # live/harness evidence); every wrapper honors it identically. A stale value
    # inherited from the parent environment is cleared when recapture is off, so
    # the signal reflects this dispatch alone.
    if recapture:
        env[RECAPTURE_ENV] = "1"
    else:
        env.pop(RECAPTURE_ENV, None)
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
        "scope_dirs": grant_dirs,
        "report_dir": report_dir,
        "work_roots": work_roots,
        "recapture": recapture,
        "pid": proc.pid,
        "status": "dispatched",
    }
    emit_record(record)
    return EXIT_OK
