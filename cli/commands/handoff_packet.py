"""`cartopian handoff-packet <task-path> --role <role>` aggregator.

Folds the handoff-packet assembly chain (resolved roles, handoff block, review
policy, work-root absolute paths, expected report path, git policy) into a
single NDJSON call. Read-only; no file writes, moves, renames, or deletes.
"""
import argparse
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cli import request_trace
from cli.commands.resolve_config import (
    _CliError,
    _load_toml,
    _resolve_deliverable,
    _resolve_work_roots,
    resolve_project_configuration,
)
from cli.emit import emit_record
from cli.config_schema import MACHINE_RECORD_SCHEMA_VERSION
from cli.main import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)

_TASK_ID_RE = re.compile(r"^(TASK-\d{2}-\d{3})(?:-[^/]*)?$")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    """Add arguments for handoff-packet."""
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file",
    )
    subparser.add_argument(
        "--role",
        required=True,
        help="Role identifier being dispatched (must have roles.<role>.agent)",
    )


def _first_heading(content: str) -> str:
    """Return the text after `# ` from the first top-level heading."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _deliverable_value(content: str) -> str:
    """Return the raw `Deliverable:` header value, or "" when absent.

    Scans the top-of-file header block only (stops at the first `## ` section),
    matching how `_parse_headers` reads declarative task fields.
    """
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("Deliverable:"):
            return stripped[len("Deliverable:"):].strip()
    return ""


def _extract_task_id(task_path: Path) -> Optional[str]:
    """Extract `TASK-NN-NNN` from the task filename stem, or None."""
    match = _TASK_ID_RE.match(task_path.stem)
    if match is None:
        return None
    return match.group(1)


def _find_project_root(task_path: Path) -> Optional[Path]:
    """Walk up from the task file to find the project root.

    A project root has a ``cartopian.toml`` plus either a ``phases/``
    directory or an ``IMPLEMENTATION_PLAN.md`` file.
    """
    for candidate in [task_path.parent] + list(task_path.parents):
        if (candidate / "cartopian.toml").is_file() and (
            (candidate / "phases").is_dir()
            or (candidate / "IMPLEMENTATION_PLAN.md").is_file()
        ):
            return candidate
    # Fall back to any ancestor carrying a cartopian.toml so a missing
    # config can still surface as EXIT_ENV rather than EXIT_FAIL.
    for candidate in [task_path.parent] + list(task_path.parents):
        if (candidate / "cartopian.toml").is_file():
            return candidate
    return None


def _build_work_roots(
    project_root: Path,
    project_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return an ordered list of ``{name, absolute_path}`` for all declared work roots.

    Names with no per-machine mapping resolve to ``absolute_path = null``.
    Re-raises ``_CliError`` with ``EXIT_ENV`` (unreadable local config).
    """
    project_table = project_cfg.get("project", {}) or {}
    names = project_table.get("work_roots", []) or []
    try:
        resolved = _resolve_work_roots(project_cfg, project_root)
    except _CliError as err:
        if err.exit_code == EXIT_ENV:
            raise
        resolved = {}
    return [{"name": name, "absolute_path": resolved.get(name)} for name in names]


def _expected_report_path(project_root: Path, task_id: str) -> Path:
    """Return the protocol-derived expected report path for a task.

    The report path is task-derived (``reports/REPORT-NN-NNN.md``), not
    role-derived. Shared with ``wait-handoff`` so both commands resolve the
    expected report path identically.
    """
    nn_nnn = task_id.removeprefix("TASK-") if task_id.startswith("TASK-") else task_id
    return (project_root / "reports" / f"REPORT-{nn_nnn}.md").resolve()


def _build_git_policy(git_block: Dict[str, Any]) -> Dict[str, Any]:
    """Project the resolved git block down to the git_policy shape."""
    return {
        "pm_owns_product_branches": bool(
            git_block.get("pm_owns_product_branches", False)
        ),
        "default_branch_pattern": git_block.get(
            "default_branch_pattern", "task/{task_id}-{slug}"
        ),
        "default_merge_strategy": git_block.get(
            "default_merge_strategy", "merge"
        ),
    }


def handler(args: argparse.Namespace) -> int:
    """Handle handoff-packet command.

    Reads the task file and its enclosing project config, resolves the
    handoff block for ``--role``, and emits a single NDJSON record with
    the fields PMs need to compose a handoff prompt.
    """
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
    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError as exc:
        stderr_error(f"task file unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    project_root = _find_project_root(task_path)
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

    try:
        resolved = resolve_project_configuration(project_root)
    except _CliError as err:
        if err.prefix == "guard":
            stderr_guard(err.message)
        elif err.prefix == "usage":
            stderr_usage(err.message)
        else:
            stderr_error(err.message)
        return err.exit_code

    roles = resolved["roles"]
    if role not in roles:
        stderr_guard(f"role {role!r} is not declared")
        return EXIT_FAIL
    role_record = roles[role]
    if role_record["launch"]["agent"] is None:
        stderr_guard(
            f"roles.{role}.agent is not configured — "
            f"dispatch this role manually"
        )
        return EXIT_FAIL

    git_versioning = resolved["git_versioning"]
    git_policy = (
        _build_git_policy(resolved["git"] or {}) if git_versioning else None
    )

    try:
        work_roots = _build_work_roots(project_root, project_cfg)
        deliverable = _resolve_deliverable(
            project_cfg, project_root, _deliverable_value(content)
        )
    except _CliError as err:
        stderr_error(err.message)
        return err.exit_code

    task_id = _extract_task_id(task_path) or task_path.stem
    task_title = _first_heading(content) or task_path.stem
    expected_report_path = _expected_report_path(project_root, task_id)

    # Manual review handoffs consume exactly the artifact automatic dispatch
    # does: the same resolved review context and the same binding preflight.
    # Bypassing `cartopian dispatch` therefore cannot bypass intent resolution.
    request_trace_record: Optional[Dict[str, Any]] = None
    if task_path.parent.name == "in-review":
        nn_nnn = task_id.removeprefix("TASK-")
        review_prompt = project_root / "prompts" / f"PROMPT-{nn_nnn}.md"
        try:
            context = request_trace.context_for_task(
                project_root,
                task_path,
                require_completion_evidence=True,
            )
        except request_trace.RequestRefusal as refusal:
            stderr_guard(f"{refusal.rule}: {refusal.detail}")
            if refusal.recovery:
                stderr_guard(f"recovery: {refusal.recovery}")
            return EXIT_FAIL
        preflight: Optional[Dict[str, Any]] = None
        if review_prompt.is_file():
            try:
                prompt_text = request_trace.read_contained_text(
                    project_root, review_prompt, what="review prompt"
                )
                prompt_context = request_trace.context_for_task(
                    project_root,
                    task_path,
                    prompt_text=prompt_text,
                    require_completion_evidence=True,
                )
            except request_trace.RequestRefusal as refusal:
                stderr_guard(f"{refusal.rule}: {refusal.detail}")
                if refusal.recovery:
                    stderr_guard(f"recovery: {refusal.recovery}")
                return EXIT_FAIL
            preflight = request_trace.preflight_prompt_binding(
                prompt_context, prompt_text
            )
            preflight["prompt_path"] = str(review_prompt)
            context = prompt_context
        else:
            preflight = {
                "ok": False,
                "rule": "missing-prompt",
                "detail": f"review prompt not found: {review_prompt}",
                "recovery": "prepare the bound review prompt before manual handoff",
                "context_identity": context.context_identity,
                "prompt_path": str(review_prompt),
            }
        request_trace_record = context.as_record()
        request_trace_record["preflight"] = preflight

    record: Dict[str, Any] = {
        "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
        "schema_identity": resolved["schema_identity"],
        "project_schema_version": resolved["project_schema_version"],
        "task_id": task_id,
        "task_title": task_title,
        "task_path": str(task_path),
        "role": role,
        "role_description": role_record["description"],
        "effective_grants": role_record["effective_grants"],
        "assigned_work_types": role_record["assigned_work_types"],
        "launch": role_record["launch"],
        "auto_launch": role_record["auto_launch"],
        "attribution": role_record["attribution"],
        "work_roots": work_roots,
        "deliverable": deliverable,
        "expected_report_path": str(expected_report_path),
        "git_versioning": git_versioning,
        "git_policy": git_policy,
        "automation_policy": resolved["automation"],
        "reviews": resolved["reviews"],
        "request_trace": request_trace_record,
    }
    emit_record(record)
    if (
        request_trace_record is not None
        and request_trace_record["preflight"] is not None
        and not request_trace_record["preflight"]["ok"]
    ):
        failure = request_trace_record["preflight"]
        stderr_guard(f"{failure['rule']}: {failure['detail']}")
        if failure.get("recovery"):
            stderr_guard(f"recovery: {failure['recovery']}")
        return EXIT_FAIL
    return EXIT_OK
