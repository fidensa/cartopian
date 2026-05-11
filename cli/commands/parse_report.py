"""`cartopian parse-report <report-path>` (FR-014, SPEC-01-001)."""
import argparse
import re
from pathlib import Path
from typing import Optional, Tuple

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_usage

VARIANTS = ("task", "review", "planning-review")

STATUS_VERDICT = {
    "complete": "accepted",
    "blocked": "blocked",
    "failed": "failed",
}

REQUIRED_SECTIONS = {
    "task": (
        "## Identity",
        "## Files changed",
        "## Test evidence",
        "## Commit / PR",
        "## Remaining risks",
        "## Ready for review",
    ),
    "review": (
        "## Identity",
        "## Evidence reviewed",
        "## Verdict",
        "## Blocking findings",
    ),
    "planning-review": (
        "## Identity",
        "## Evidence reviewed",
        "## Verdict",
        "## Blocking findings",
    ),
}

REQUIRED_IDENTITY_KEYS = {
    "task": ("Task ID:", "Prompt path:", "Task path:"),
    "review": ("Review ID:", "Prompt path:", "Review file path:"),
    "planning-review": ("Review ID:", "Prompt path:", "Review file path:"),
}


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "report_path",
        help="Path to the report file to parse",
    )
    subparser.add_argument(
        "--variant",
        choices=list(VARIANTS),
        default=None,
        help="Explicit variant; overrides filename/content inference",
    )


def _infer_variant(report_path: Path, content: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (variant, error_message). variant is None on conflict or unresolvable."""
    filename_is_plan = report_path.name.startswith("REPORT-PLAN-")
    has_review_id = "Review ID:" in content
    has_task_id = "Task ID:" in content

    if filename_is_plan:
        if has_task_id and not has_review_id:
            return None, (
                "ambiguous variant: filename and content disagree; "
                "pass --variant explicitly"
            )
        return "planning-review", None

    if has_review_id and has_task_id:
        return None, (
            "ambiguous variant: filename and content disagree; "
            "pass --variant explicitly"
        )
    if has_review_id:
        return "review", None
    if has_task_id:
        return "task", None
    return None, "cannot infer variant; pass --variant {task|review|planning-review}"


def _extract_status(content: str) -> Optional[str]:
    for match in re.finditer(r"^Status:\s*(.*)$", content, re.MULTILINE):
        value = match.group(1).strip()
        if value:
            return value
    return None


def _schema_ok(variant: str, content: str) -> bool:
    for section in REQUIRED_SECTIONS[variant]:
        if section not in content:
            return False
    for key in REQUIRED_IDENTITY_KEYS[variant]:
        if key not in content:
            return False
    return True


def handler(args: argparse.Namespace) -> int:
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE
    report_path = Path(raw_path)
    if not report_path.exists():
        stderr_error(f"report not found: {raw_path}")
        return EXIT_FAIL
    report_path = report_path.resolve()

    try:
        content = report_path.read_text(encoding="utf-8")
    except OSError:
        content = ""

    if args.variant:
        variant = args.variant
    else:
        variant, err = _infer_variant(report_path, content)
        if variant is None:
            stderr_usage(err)
            return EXIT_USAGE

    if not _schema_ok(variant, content):
        verdict = "failed-to-parse"
        status_value: Optional[str] = None
    else:
        raw_status = _extract_status(content)
        if raw_status is None or raw_status not in STATUS_VERDICT:
            verdict = "failed-to-parse"
            status_value = None
        else:
            verdict = STATUS_VERDICT[raw_status]
            status_value = raw_status

    record = {
        "verdict": verdict,
        "variant": variant,
        "report_path": str(report_path),
        "status": status_value,
    }
    emit_record(record)
    return EXIT_OK
