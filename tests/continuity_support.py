"""Shared fixtures for the optional continuity artifact's coverage.

Not collected by ``unittest discover`` (its pattern is ``test*.py``); the
continuity test modules import from it the way ``test_archive_plan`` imports
its CLI driver from ``test_fr005_structured_writers``.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List, Optional, Tuple

from cli import prompt_evidence
from cli.main import build_parser
from tests.scaffold import ProjectScaffold, project_scaffold

TOML = (
    "[project]\n"
    'id = "continuity-demo"\n'
    'name = "Continuity Demo"\n'
    'project_schema_version = "v0.9.0"\n'
)

#: A six-group ledger section body, the only part of the artifact a PM authors.
LEDGER_BODY = (
    "- Outcome: Published the delivery approval rule; terminal state: closed; "
    "verification: outcome-verified\n"
    "- Evidence: Approval recorded by the plan owner; state: verified\n"
    "- Decisions: 1 governing; 0 superseded; 0 expired\n"
    "- Risks: none open\n"
    "- Delivery: Delivery approval rule; target: publication owners; state: "
    "accepted 2026-08-14\n"
    "- Follow-up: none"
)


def run_cli(*argv: str) -> Tuple[int, List[dict], str]:
    """Drive the real CLI parser in-process; return (exit_code, records, stderr)."""
    parser = build_parser()
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            args = parser.parse_args(list(argv))
            handler = getattr(args, "_handler", None)
            code = handler(args) if handler is not None else 2
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    records = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, records, err.getvalue()


def continuity_scaffold(**kwargs) -> ProjectScaffold:
    """A project with a live plan surface, ready for a closeout."""
    scaffold = project_scaffold(
        cartopian_toml=TOML, extra_dirs=("resources",), **kwargs
    )
    scaffold.write("REQUIREMENTS.md", "# Requirements\n")
    scaffold.write("IMPLEMENTATION_PLAN.md", "# Implementation Plan\n")
    scaffold.write("STANDARDS.md", "# Standards\n")
    return scaffold


def decision(
    scaffold: ProjectScaffold,
    dec_id: str,
    *,
    scope: Optional[str] = None,
    ruling: Optional[str] = None,
    supersedes: Optional[str] = None,
    expires: Optional[str] = None,
    status: str = "locked",
) -> Path:
    """Write one decision file with the headers the continuity write reads."""
    lines = [f"# {dec_id}: fixture decision", "", "Date: 2026-08-14", f"Status: {status}"]
    if supersedes is not None:
        lines.append(f"Supersedes: {supersedes}")
    if expires is not None:
        lines.append(f"Expires: {expires}")
    if scope is not None:
        lines.append(f"Scope: {scope}")
    if ruling is not None:
        lines.append(f"Ruling: {ruling}")
    lines += ["", "## Context", "", "Fixture.", ""]
    return scaffold.write(f"decisions/{dec_id}.md", "\n".join(lines))


def seed_evidence(scaffold: ProjectScaffold, plan_id: str, unit: str = "TASK-02-001") -> Path:
    """Append one well-formed ``U`` record carrying ``plan_id`` as its window.

    The window witness is derived from this log through
    ``prompt_evidence.read_ledger`` and nothing else.
    """
    record = prompt_evidence.summary(
        plan=plan_id,
        unit=unit,
        date="2026-08-14",
        families={name: (0, "observed") for name in prompt_evidence.FAMILIES},
        denominators={name: 0 for name in prompt_evidence.DENOMINATOR_FAMILIES},
    )
    path = prompt_evidence.log_path(scaffold.project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prompt_evidence.serialize(record))
    return path


def archive(scaffold: ProjectScaffold, closed: str, summary: str = "fixture") -> str:
    code, records, err = run_cli(
        "archive-plan",
        str(scaffold.project_root),
        "--closed", closed,
        "--summary", summary,
        "--content", "# Plan Closeout\n",
    )
    assert code == 0, err
    return records[0]["details"]["archive_name"]


def write_continuity(
    scaffold: ProjectScaffold,
    mode: str,
    closed: str,
    *,
    plan: Optional[str] = None,
    content: str = LEDGER_BODY,
) -> Tuple[int, List[dict], str]:
    argv = ["write-continuity", str(scaffold.project_root), "--mode", mode,
            "--closed", closed, "--content", content]
    if plan is not None:
        argv[4:4] = ["--plan", plan]
    return run_cli(*argv)


def release(scaffold: ProjectScaffold, plan: str, closed: str = "2026-08-26"):
    return run_cli(
        "release-reservation", str(scaffold.project_root), "--plan", plan,
        "--closed", closed,
    )
