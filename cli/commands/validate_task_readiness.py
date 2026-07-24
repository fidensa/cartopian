"""`cartopian validate-task-readiness <task-path>`."""
import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli.commands.resolve_config import (
    _CliError,
    _DELIVERABLE_SKIP,
    _load_toml,
    _relpath_in_resources,
    _resolve_work_roots,
)
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE


CHECK_ORDER = (
    "phase-exists",
    "plan-ref-exists",
    "blocked-by-complete",
    "evidence-gate-valid",
    "acceptance-present",
    "work-root-names-valid",
    "deliverable-valid",
)

EVIDENCE_GATE_VALUES = ("required", "n/a")


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "task_path",
        help="Absolute path to the task file",
    )


def _stderr(prefix: str, msg: str) -> None:
    sys.stderr.write(f"[{prefix}] {msg}\n")


def _find_project_root(task_path: Path) -> Optional[Path]:
    for candidate in [task_path.parent] + list(task_path.parents):
        if (candidate / "cartopian.toml").is_file() and (
            (candidate / "phases").is_dir()
            or (candidate / "IMPLEMENTATION_PLAN.md").is_file()
        ):
            return candidate
    return None


_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _/-]*?):\s*(.*)$")


def _parse_headers(content: str) -> Tuple[Dict[str, str], Dict[str, bool]]:
    """Parse top-of-file `Field: value` headers.

    Stops at the first markdown section header (line starting with `## `).
    Returns (headers, presence) where `presence[name]` is True if the
    header line was present at all (even with an empty value).
    """
    headers: Dict[str, str] = {}
    presence: Dict[str, bool] = {}
    for line in content.splitlines():
        if line.startswith("## "):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _HEADER_RE.match(stripped)
        if not match:
            continue
        key = match.group(1).strip()
        value = match.group(2).strip()
        if key not in headers:
            headers[key] = value
            presence[key] = True
    return headers, presence


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _check_phase(project_root: Path, headers: Dict[str, str]) -> Dict[str, Any]:
    phase = headers.get("Phase", "").strip()
    if not phase:
        return {"name": "phase-exists", "pass": False, "reason": "missing Phase: header"}
    phase_file = project_root / "phases" / f"{phase}.md"
    if phase_file.is_file():
        return {"name": "phase-exists", "pass": True, "reason": None}
    return {
        "name": "phase-exists",
        "pass": False,
        "reason": f"phase file not found: phases/{phase}.md",
    }


def _check_plan_ref(project_root: Path, headers: Dict[str, str]) -> Dict[str, Any]:
    plan_ref = headers.get("Plan ref", "").strip()
    if not plan_ref:
        return {
            "name": "plan-ref-exists",
            "pass": False,
            "reason": "missing Plan ref: header",
        }
    plan_path = project_root / "IMPLEMENTATION_PLAN.md"
    if not plan_path.is_file():
        return {
            "name": "plan-ref-exists",
            "pass": False,
            "reason": "IMPLEMENTATION_PLAN.md not found",
        }
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "name": "plan-ref-exists",
            "pass": False,
            "reason": f"IMPLEMENTATION_PLAN.md unreadable: {exc}",
        }
    if plan_ref in plan_text:
        return {"name": "plan-ref-exists", "pass": True, "reason": None}
    return {
        "name": "plan-ref-exists",
        "pass": False,
        "reason": f"plan ref not found in IMPLEMENTATION_PLAN.md: {plan_ref}",
    }


def _check_blocked_by(project_root: Path, headers: Dict[str, str]) -> Dict[str, Any]:
    raw = headers.get("Blocked by", "").strip()
    if not raw or raw.lower() in {"n/a", "none"}:
        return {"name": "blocked-by-complete", "pass": True, "reason": None}
    items = _split_csv(raw)
    done_dir = project_root / "tasks" / "done"
    missing: List[str] = []
    for tid in items:
        matches = list(done_dir.glob(f"{tid}-*.md")) if done_dir.is_dir() else []
        direct = done_dir / f"{tid}.md"
        if not matches and not direct.is_file():
            missing.append(tid)
    if missing:
        return {
            "name": "blocked-by-complete",
            "pass": False,
            "reason": f"not in tasks/done/: {', '.join(missing)}",
        }
    return {"name": "blocked-by-complete", "pass": True, "reason": None}


def _check_evidence_gate(
    headers: Dict[str, str], presence: Dict[str, bool]
) -> Dict[str, Any]:
    if "Evidence gate" in headers:
        value = headers["Evidence gate"].strip()
        field = "Evidence gate"
    elif "Test gate" in headers:
        value = headers["Test gate"].strip()
        field = "Test gate"
    else:
        return {
            "name": "evidence-gate-valid",
            "pass": False,
            "reason": "missing Evidence gate: header",
        }
    if value in EVIDENCE_GATE_VALUES:
        return {"name": "evidence-gate-valid", "pass": True, "reason": None}
    return {
        "name": "evidence-gate-valid",
        "pass": False,
        "reason": f"{field}: {value!r} is not one of {{required, n/a}}",
    }


def _check_acceptance(content: str) -> Dict[str, Any]:
    lines = content.splitlines()
    in_section = False
    found_checkbox = False
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            if line.strip().lower() == "## acceptance":
                in_section = True
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            found_checkbox = True
            break
    if not in_section:
        return {
            "name": "acceptance-present",
            "pass": False,
            "reason": "## Acceptance section not found",
        }
    if not found_checkbox:
        return {
            "name": "acceptance-present",
            "pass": False,
            "reason": "## Acceptance section has no checkbox items",
        }
    return {"name": "acceptance-present", "pass": True, "reason": None}


def _check_work_root(
    project_root: Path,
    headers: Dict[str, str],
    presence: Dict[str, bool],
    warnings: List[str],
) -> Dict[str, Any]:
    raw = headers.get("Work root", "").strip() if "Work root" in headers else ""
    work_root_present = "Work root" in presence
    if not work_root_present or not raw or raw == "n/a":
        return {"name": "work-root-names-valid", "pass": True, "reason": None}

    names = _split_csv(raw)
    if len(names) > 1:
        warnings.append(
            f"warning: multi-valued Work root: {', '.join(names)}"
        )

    try:
        project_cfg = _load_toml(project_root / "cartopian.toml", "project config") or {}
    except _CliError as err:
        return {
            "name": "work-root-names-valid",
            "pass": False,
            "reason": err.message,
        }
    declared = (project_cfg.get("project", {}) or {}).get("work_roots", []) or []

    try:
        _resolve_work_roots(project_cfg, project_root)
    except _CliError as err:
        return {
            "name": "work-root-names-valid",
            "pass": False,
            "reason": err.message,
        }

    for name in names:
        if name not in declared:
            return {
                "name": "work-root-names-valid",
                "pass": False,
                "reason": f"unknown work-root name: {name}",
            }
    return {"name": "work-root-names-valid", "pass": True, "reason": None}


def _check_deliverable(
    project_root: Path, headers: Dict[str, str]
) -> Dict[str, Any]:
    """Validate the ``Deliverable:`` field's placement rules.

    A project-mode deliverable must land under ``resources/`` (CONVENTIONS
    § Project Resources); a work-root deliverable must name a declared work
    root. Absent / ``n/a`` deliverables pass — most tasks have none.
    """
    raw = headers.get("Deliverable", "").strip()
    if raw.lower() in _DELIVERABLE_SKIP:
        return {"name": "deliverable-valid", "pass": True, "reason": None}

    root, sep, relpath = raw.partition(":")
    root, relpath = root.strip(), relpath.strip()
    if not sep or not relpath:
        root, relpath = "project", raw

    if relpath.startswith(("/", "\\")) or (len(relpath) > 1 and relpath[1] == ":"):
        return {
            "name": "deliverable-valid",
            "pass": False,
            "reason": f"deliverable path must be relative: {raw!r}",
        }

    if root == "project":
        if _relpath_in_resources(relpath):
            return {"name": "deliverable-valid", "pass": True, "reason": None}
        return {
            "name": "deliverable-valid",
            "pass": False,
            "reason": (
                f"project-mode deliverable must live under resources/ "
                f"(project:resources/<path>); got: {raw!r}"
            ),
        }

    try:
        project_cfg = _load_toml(project_root / "cartopian.toml", "project config") or {}
    except _CliError as err:
        return {"name": "deliverable-valid", "pass": False, "reason": err.message}
    declared = (project_cfg.get("project", {}) or {}).get("work_roots", []) or []
    if root not in declared:
        return {
            "name": "deliverable-valid",
            "pass": False,
            "reason": f"deliverable names an undeclared work root: {root}",
        }
    return {"name": "deliverable-valid", "pass": True, "reason": None}


def handler(args: argparse.Namespace) -> int:
    raw_path = args.task_path
    if not Path(raw_path).is_absolute():
        _stderr("usage", f"task_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE

    task_path = Path(raw_path)
    if not task_path.is_file():
        _stderr("error", f"task file not found: {raw_path}")
        return EXIT_FAIL

    task_path = task_path.resolve()

    try:
        content = task_path.read_text(encoding="utf-8")
    except OSError as exc:
        _stderr("error", f"task file unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    project_root = _find_project_root(task_path)
    if project_root is None:
        _stderr("error", f"project root not found for task: {raw_path}")
        return EXIT_FAIL

    headers, presence = _parse_headers(content)
    warnings: List[str] = []

    checks_by_name = {
        "phase-exists": _check_phase(project_root, headers),
        "plan-ref-exists": _check_plan_ref(project_root, headers),
        "blocked-by-complete": _check_blocked_by(project_root, headers),
        "evidence-gate-valid": _check_evidence_gate(headers, presence),
        "acceptance-present": _check_acceptance(content),
        "work-root-names-valid": _check_work_root(
            project_root, headers, presence, warnings
        ),
        "deliverable-valid": _check_deliverable(project_root, headers),
    }
    checks = [checks_by_name[name] for name in CHECK_ORDER]
    ready = all(c["pass"] for c in checks)

    record = {
        "task_path": str(task_path),
        "ready": ready,
        "checks": checks,
    }
    emit_record(record)

    for msg in warnings:
        _stderr("work-root", msg)

    if not ready:
        for check in checks:
            if check["pass"]:
                continue
            prefix = (
                "work-root"
                if check["name"] == "work-root-names-valid"
                else "validation"
            )
            _stderr(prefix, f"{check['name']}: {check['reason']}")
        return EXIT_FAIL
    return EXIT_OK
