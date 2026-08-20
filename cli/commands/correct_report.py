"""`cartopian correct-report <report-path> --expected-identity sha256:... --corrected-file|--corrected-content ...`.

Hash-bound mechanical correction of one handoff report, applied in place by
the PM through the mediated writer — the minimal containment-safe alternative
to a correction *handoff* for validator-classified ``mechanical`` defects.

Why this exists: a mechanical schema defect (a malformed evidence row, a bad
status token, a missing heading) is a bounded fix, but routing it through a
new assignee handoff forces the whole assignment surface back into a prompt —
report bytes, deliverable preflights, dependency inputs — and consumes a
handoff-budget unit for a one-line repair. This command instead:

- **identifies the exact current report bytes**: ``--expected-identity`` must
  match the on-disk content, and the mediated write re-verifies those bytes at
  replace time, so a stale hash or concurrent mutation refuses fail-closed;
- **permits only validator-classified mechanical correction**: the current
  report must fail at least one ``mechanical`` check from ``validate-report``
  and no ``substantive`` or ``missing-input`` check — those route to the
  review loop or input repair, never to an edit;
- **prevents substantive evidence edits**: every failed check resolves to an
  exact edit operation — replace this header line or Identity bullet in
  place (never duplicated), replace the verdict token line, replace this
  defective unverified-claim row one-for-one, delete this contradictory
  ``- none`` claim row — or the command refuses. The unverified-claims
  grammar is the only editable evidence surface: authoritative-source and
  conflict-resolution rows are recorded producer evidence, their blockers
  are classified substantive or missing-input
  (``validate_report.source_blocker_class``), and no operation for them
  exists here. Comparison is positional over raw byte spans: section order,
  heading bytes, prose, row order, and every unaffected line must be
  identical, and a missing required heading may be repaired only as a
  body-identical in-place rename. Absent substantive content — a missing
  disposition, source, conflict record, or section body — is never supplied
  by a correction; it routes back to rework;
- **remains auditable**: one NDJSON record names the before/after content
  identities, the corrected grains, and the checks resolved;
- **avoids retransmitting anything**: no prompt, no deliverable copies, no
  governance inputs, no report-slot clearing, no dispatch.

The corrected body must fully validate (``validate-report`` semantics) before
a byte lands. A defect this command refuses is by definition not mechanical
correction — route it per `skills/run-handoff.md` § Failure routing instead
of widening this surface. Stdlib only (NF-001).
"""
import argparse
import re
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from cli import mediated_write, report_identity, request_trace, source_guidance
from cli.commands import parse_report, validate_report
from cli.commands._writers import resolve_content
from cli.emit import emit_record
from cli.main import (
    EXIT_FAIL,
    EXIT_OK,
    EXIT_USAGE,
    stderr_error,
    stderr_guard,
    stderr_usage,
)

_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_IDENTITY_BULLET_RE = re.compile(r"^\s*-\s*([^:]+?)\s*:")
_CLAIM_ROW_INDEX_RE = re.compile(r"unverified claim (\d+)")

_REWORK_HINT = (
    "supplying it would be PM-authored producer evidence — route this to "
    "rework, not correction"
)


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    subparser.description = (
        "Apply one hash-bound mechanical correction to a handoff report in "
        "place, without a correction handoff. Requires the exact "
        "`report_content_identity` of the current bytes (from a wait or "
        "`validate_report`). Every failed mechanical check resolves to an "
        "exact edit operation — replace the defective header line, Identity "
        "bullet, verdict token, or unverified-claim row, or delete a "
        "contradictory `- none` claim row (source and conflict rows are "
        "producer evidence and are never editable) — and everything else, "
        "including section order, "
        "heading bytes, prose, and unaffected rows, must stay byte-"
        "identical; a missing heading is repairable only as a body-identical "
        "in-place rename. Refuses substantive or missing-input findings, "
        "stale identities, absent substantive content, and any correction "
        "that does not fully validate. Emits one audit record with "
        "before/after identities."
    )
    subparser.add_argument(
        "report_path",
        help="Absolute path to the report file to correct",
    )
    subparser.add_argument(
        "--expected-identity",
        dest="expected_identity",
        required=True,
        help=(
            "sha256:<hex> identity of the exact current report bytes being "
            "corrected (from wait-handoff/wait-report/validate-report)"
        ),
    )
    subparser.add_argument(
        "--corrected-content",
        dest="corrected_content",
        default=None,
        help=(
            "The complete corrected report body (UTF-8). Mutually exclusive "
            "with --corrected-file"
        ),
    )
    subparser.add_argument(
        "--corrected-file",
        dest="corrected_file",
        default=None,
        help="Path to a file holding the complete corrected report body",
    )
    subparser.add_argument(
        "--variant",
        choices=list(parse_report.VARIANTS),
        default=None,
        help=(
            "Explicit variant; replaces content inference but must agree "
            "with a grammar-matching report filename"
        ),
    )


# ---------------------------------------------------------------------------
# Report structure (raw spans preserved)
# ---------------------------------------------------------------------------


def _split_sections(
    content: str,
) -> Optional[Tuple[List[str], List[Tuple[str, str, List[str]]]]]:
    """Split a report into preamble lines and ordered raw ``## `` sections.

    Returns ``(preamble_lines, [(raw_heading_line, name, body_lines), ...])``
    preserving document order and the heading's exact bytes, or None when a
    section name appears more than once — an ambiguous structure this surface
    refuses to reason about.
    """
    preamble: List[str] = []
    sections: List[Tuple[str, str, List[str]]] = []
    seen: Set[str] = set()
    body: Optional[List[str]] = None
    for line in content.splitlines():
        match = _H2_RE.match(line)
        if match:
            name = match.group(1)
            if name in seen:
                return None
            seen.add(name)
            body = []
            sections.append((line, name, body))
            continue
        if body is None:
            preamble.append(line)
        else:
            body.append(line)
    return preamble, sections


def _split_subsections(
    lines: Sequence[str],
) -> Tuple[List[str], List[Tuple[str, str, List[str]]]]:
    """Split a section body into pre-subsection prose and raw ``### `` blocks."""
    pre: List[str] = []
    blocks: List[Tuple[str, str, List[str]]] = []
    current: Optional[List[str]] = None
    for line in lines:
        match = _H3_RE.match(line)
        if match:
            current = []
            blocks.append((line, match.group(1), current))
            continue
        if current is None:
            pre.append(line)
        else:
            current.append(line)
    return pre, blocks


def _row_groups(
    lines: Sequence[str],
) -> List[Tuple[str, Union[str, Tuple[str, ...]]]]:
    """Partition lines into ``("prose", line)`` and ``("row", lines)`` items.

    Mirrors the evidence parser's row-folding rule: a ``- `` bullet starts a
    row, an indented non-empty line continues it, and anything else — prose
    or a blank line — terminates it.
    """
    items: List[Tuple[str, Union[str, Tuple[str, ...]]]] = []
    current: Optional[List[str]] = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            if current is not None:
                items.append(("row", tuple(current)))
            current = [line]
        elif current is not None and stripped and line[:1].isspace():
            current.append(line)
        else:
            if current is not None:
                items.append(("row", tuple(current)))
                current = None
            items.append(("prose", line))
    if current is not None:
        items.append(("row", tuple(current)))
    return items


def _missing_required_sections(variant: str, content: str) -> List[str]:
    """The bare section names a body-identical rename may introduce."""

    def has_heading(section: str) -> bool:
        heading = section.removeprefix("## ")
        return bool(
            re.search(rf"^##\s+{re.escape(heading)}\s*$", content, re.MULTILINE)
        )

    allowed: List[str] = []
    for section in parse_report.REQUIRED_SECTIONS[variant]:
        if not has_heading(section):
            allowed.append(section.removeprefix("## "))
    for alternatives in parse_report.REQUIRED_ANY_SECTIONS[variant]:
        if not any(has_heading(section) for section in alternatives):
            allowed.extend(
                section.removeprefix("## ") for section in alternatives
            )
    return allowed


# ---------------------------------------------------------------------------
# Authorization: exact edit operations per failed mechanical check
# ---------------------------------------------------------------------------


class _RowOps:
    """Exact row operations for one evidence subsection."""

    def __init__(self) -> None:
        self.replace: Set[int] = set()
        self.delete: Set[int] = set()


class _Authorization:
    def __init__(self) -> None:
        # Lower-cased top-of-file header names whose single line may be
        # replaced in place (or inserted once when currently absent).
        self.preamble_headers: Set[str] = set()
        # Identity bullet keys whose single bullet may be replaced in place
        # (or inserted once when currently absent).
        self.identity_keys: Set[str] = set()
        # Whether the ## Verdict token (first non-blank) line may change.
        self.verdict_token = False
        # Per-subsection exact row operations inside ## Source evidence.
        self.source_rows: Dict[str, _RowOps] = {}
        # Missing required section names a body-identical in-place rename
        # may claim.
        self.rename_targets: Set[str] = set()


def _defective_source_rows(
    project_root: Path,
    report_path: Path,
    content: str,
    variant: str,
) -> Tuple[Optional[Dict[str, _RowOps]], Optional[str]]:
    """Resolve mechanical source-evidence blockers to exact row operations.

    Returns ``(ops, None)`` or ``(None, refusal_detail)``. Only the
    unverified-claims subsection is ever editable: authoritative-source and
    conflict-resolution rows are recorded producer evidence, and every
    blocker touching them is classified `substantive` or `missing-input`
    (``validate_report.source_blocker_class``) and refused before this
    resolver runs — no swap operation for them exists here even in depth.
    A claims defect with no exact, content-free operation — an absent
    disposition — refuses: absent substantive rows route to rework.
    """
    spec = source_guidance.contract()
    claims_name = spec["subsections"]["claims"]
    task_id = validate_report._report_task_id(report_path, variant)
    if task_id is None:
        return None, "the report filename carries no task identity"
    task_path = validate_report._find_task_for_report(project_root, task_id)
    if task_path is None:
        return None, f"no task on disk matches {task_id}"
    try:
        record = source_guidance.resolve_report_evidence(task_path, content)
    except (OSError, UnicodeError, ValueError) as exc:
        return None, f"source evidence could not be resolved: {exc}"

    section_body = source_guidance._section(
        content, spec["section_headings"]["evidence"]
    )
    if section_body is None:
        return None, (
            "the report has no source-evidence section; " + _REWORK_HINT
        )
    subsections = source_guidance._subsections(section_body)
    claim_rows = source_guidance._rows(subsections.get(claims_name, []))

    ops: Dict[str, _RowOps] = {}

    def _claims_op() -> _RowOps:
        return ops.setdefault(claims_name, _RowOps())

    for blocker in record["blockers"]:
        code = blocker["code"]
        if validate_report.source_blocker_class(code) != "mechanical":
            # Substantive / missing-input blockers are refused before this
            # resolver runs; nothing here may authorize an edit for them.
            continue
        claim_match = _CLAIM_ROW_INDEX_RE.search(blocker["detail"])
        if claim_match:
            _claims_op().replace.add(int(claim_match.group(1)))
        elif code == "unhandled-unverified-claim":
            lowered = [row.lower() for row in claim_rows]
            if len(claim_rows) > 1 and "none" in lowered:
                # Mixed `- none` with claim records: the only content-free
                # repair is deleting the contradictory `none` row(s); every
                # claim record stays byte-identical.
                for index, value in enumerate(lowered, start=1):
                    if value == "none":
                        _claims_op().delete.add(index)
            else:
                return None, (
                    "the unverified-claims disposition is absent; "
                    + _REWORK_HINT
                )
        else:
            return None, (
                f"no exact edit operation is defined for blocker {code!r}"
            )
    if not ops:
        return None, "no defective unverified-claim row could be resolved"
    return ops, None


def _build_authorization(
    failed: List[Dict[str, Any]],
    variant: str,
    current: str,
    project_root: Path,
    report_path: Path,
) -> Tuple[Optional[_Authorization], Optional[str]]:
    """Resolve the failed mechanical checks into exact edit operations.

    Returns ``(authorization, refusal_detail)``; a check whose defect cannot
    be pinned to an exact operation refuses fail-closed.
    """
    auth = _Authorization()
    source_implicated = False
    for item in failed:
        name = item["name"]
        if name == "status-valid":
            auth.preamble_headers.add("status")
        elif name == "request-alignment-valid":
            auth.preamble_headers.update(
                ("request alignment", "request evidence")
            )
        elif name == "required-sections-present":
            auth.rename_targets.update(
                _missing_required_sections(variant, current)
            )
        elif name == "identity-keys-present":
            auth.identity_keys.update(
                key.rstrip(":")
                for key in parse_report.REQUIRED_IDENTITY_KEYS[variant]
                if key not in current
            )
        elif name == "identity-values-aligned":
            mismatches = validate_report._identity_alignment_mismatches(
                project_root, report_path, current, variant
            )
            auth.identity_keys.update(
                key for key, _message in mismatches if key is not None
            )
        elif name == "review-verdict-valid":
            auth.verdict_token = True
        elif name.startswith("source-evidence:"):
            source_implicated = True
        else:
            return None, (
                f"no bounded correction operation is defined for failed "
                f"check {name!r}; route this defect per run-handoff failure "
                "routing instead"
            )
    if source_implicated:
        ops, reason = _defective_source_rows(
            project_root, report_path, current, variant
        )
        if ops is None:
            return None, (
                f"{reason}; route this defect per run-handoff failure "
                "routing instead"
            )
        auth.source_rows = ops
    return auth, None


# ---------------------------------------------------------------------------
# Confinement: positional, cardinality-preserving raw-span comparison
# ---------------------------------------------------------------------------


def _positional_line_repair(
    current: Sequence[str],
    corrected: Sequence[str],
    classify: Callable[[str], Optional[str]],
    allowed: Set[str],
    where: str,
) -> Optional[str]:
    """Allow only in-place single-line repairs of the authorized keys.

    Each allowed key may occur at most once on either side, may be replaced
    only at its current position, may be inserted only when currently
    absent (and then exactly once), and may never be deleted. Every other
    line must match positionally, byte for byte. Returns a violation
    description or None.
    """
    current_counts = {key: 0 for key in allowed}
    corrected_counts = {key: 0 for key in allowed}
    for line in current:
        key = classify(line)
        if key in current_counts:
            current_counts[key] += 1
    for line in corrected:
        key = classify(line)
        if key in corrected_counts:
            corrected_counts[key] += 1
    for key in allowed:
        if current_counts[key] > 1:
            return (
                f"{where} already carries duplicate {key!r} lines; the "
                "defect is ambiguous"
            )
        if corrected_counts[key] > 1:
            return (
                f"{where} would carry duplicate, potentially contradictory "
                f"{key!r} lines"
            )
        if corrected_counts[key] < current_counts[key]:
            return f"the {key!r} line in {where} may not be deleted"

    i = j = 0
    while i < len(current) and j < len(corrected):
        if current[i] == corrected[j]:
            i += 1
            j += 1
            continue
        current_key = classify(current[i])
        corrected_key = classify(corrected[j])
        if (
            current_key is not None
            and current_key in allowed
            and corrected_key == current_key
        ):
            i += 1
            j += 1
            continue
        if (
            corrected_key is not None
            and corrected_key in allowed
            and current_counts[corrected_key] == 0
        ):
            j += 1
            continue
        return f"{where} outside the defective line(s)"
    while j < len(corrected):
        corrected_key = classify(corrected[j])
        if (
            corrected_key is not None
            and corrected_key in allowed
            and current_counts[corrected_key] == 0
        ):
            j += 1
            continue
        return f"inserted content in {where}"
    if i < len(current):
        return f"removed content in {where}"
    return None


def _preamble_classify(line: str) -> Optional[str]:
    name, sep, _value = line.partition(":")
    if not sep:
        return None
    return name.strip().lower()


def _identity_classify(line: str) -> Optional[str]:
    match = _IDENTITY_BULLET_RE.match(line)
    return match.group(1) if match else None


def _minus_first_nonblank(lines: Sequence[str]) -> List[str]:
    remainder = list(lines)
    for index, line in enumerate(remainder):
        if line.strip():
            del remainder[index]
            break
    return remainder


def _compare_evidence_subsection(
    label: str,
    current: Sequence[str],
    corrected: Sequence[str],
    ops: Optional[_RowOps],
    violations: List[str],
    changed: List[str],
) -> None:
    if list(current) == list(corrected):
        return
    if ops is None:
        violations.append(f"evidence subsection ### {label}")
        return
    current_items = _row_groups(current)
    corrected_items = _row_groups(corrected)
    i = j = 0
    row_index = 0
    while i < len(current_items):
        item = current_items[i]
        if item[0] == "row":
            row_index += 1
        corrected_item = (
            corrected_items[j] if j < len(corrected_items) else None
        )
        if corrected_item is not None and item == corrected_item:
            i += 1
            j += 1
            continue
        if item[0] == "row" and row_index in ops.delete:
            changed.append(
                f"Source evidence/{label} row {row_index} (deleted)"
            )
            i += 1
            continue
        if (
            item[0] == "row"
            and row_index in ops.replace
            and corrected_item is not None
            and corrected_item[0] == "row"
        ):
            changed.append(f"Source evidence/{label} row {row_index}")
            i += 1
            j += 1
            continue
        violations.append(
            f"content outside the defective row(s) in ### {label}"
        )
        return
    if j != len(corrected_items):
        violations.append(f"inserted content in ### {label}")


def _confine(
    current_split,
    corrected_split,
    auth: _Authorization,
) -> Tuple[List[str], List[str]]:
    """Enumerate scope violations and the authorized grains that changed."""
    violations: List[str] = []
    changed: List[str] = []
    evidence_heading = source_guidance.contract()["section_headings"]["evidence"]

    current_pre, current_sections = current_split
    corrected_pre, corrected_sections = corrected_split

    if corrected_pre != current_pre:
        if not auth.preamble_headers:
            violations.append(
                "the header preamble outside the defective header line(s)"
            )
        else:
            violation = _positional_line_repair(
                current_pre,
                corrected_pre,
                _preamble_classify,
                auth.preamble_headers,
                "the header preamble",
            )
            if violation is None:
                changed.append("<preamble>")
            else:
                violations.append(violation)

    if len(current_sections) != len(corrected_sections):
        violations.append(
            "sections were added or removed; a missing heading may only be "
            "repaired as a body-identical rename in place — absent "
            "substantive content is not a mechanical correction"
        )
        return violations, changed

    for (cur_raw, cur_name, cur_body), (cor_raw, cor_name, cor_body) in zip(
        current_sections, corrected_sections
    ):
        if cur_raw != cor_raw:
            # An in-place heading change is legal only as a body-identical
            # rename onto a missing required section name — a correction
            # never reorders sections or supplies content the producer did
            # not publish.
            if cor_name in auth.rename_targets and cor_body == cur_body:
                changed.append(cor_name)
                continue
            violations.append(
                f"section heading {cur_raw!r} changed to {cor_raw!r} "
                "(section order and heading bytes are fixed; a missing "
                "heading may only be repaired as a body-identical rename "
                "in place)"
            )
            continue
        if cur_body == cor_body:
            continue
        if cur_name == "Identity" and auth.identity_keys:
            violation = _positional_line_repair(
                cur_body,
                cor_body,
                _identity_classify,
                auth.identity_keys,
                "the Identity section",
            )
            if violation is None:
                changed.append("Identity")
            else:
                violations.append(violation)
        elif cur_name == "Verdict" and auth.verdict_token:
            if _minus_first_nonblank(cur_body) == _minus_first_nonblank(
                cor_body
            ):
                changed.append("Verdict")
            else:
                violations.append(
                    "the Verdict body beyond its first (token) line"
                )
        elif cur_name == evidence_heading and auth.source_rows:
            cur_sub_pre, cur_blocks = _split_subsections(cur_body)
            cor_sub_pre, cor_blocks = _split_subsections(cor_body)
            if cur_sub_pre != cor_sub_pre or [
                raw for raw, _name, _lines in cur_blocks
            ] != [raw for raw, _name, _lines in cor_blocks]:
                violations.append(
                    f"the ## {evidence_heading} structure (subsection "
                    "order, heading bytes, and prose are fixed)"
                )
            else:
                for (
                    (_cur_sub_raw, label, cur_lines),
                    (_cor_sub_raw, _cor_label, cor_lines),
                ) in zip(cur_blocks, cor_blocks):
                    _compare_evidence_subsection(
                        label,
                        cur_lines,
                        cor_lines,
                        auth.source_rows.get(label),
                        violations,
                        changed,
                    )
        else:
            violations.append(f"section ## {cur_name}")
    return violations, changed


def _refuse(rule: str, detail: str, record: Dict[str, Any]) -> int:
    record.update({"ok": False, "rule": rule, "detail": detail})
    emit_record(record)
    stderr_guard(f"{rule}: {detail}")
    return EXIT_FAIL


def handler(args: argparse.Namespace) -> int:
    raw_path = args.report_path
    if not Path(raw_path).is_absolute():
        stderr_usage(f"report_path must be an absolute path; got: {raw_path}")
        return EXIT_USAGE
    if not report_identity.CONTENT_IDENTITY_RE.match(args.expected_identity):
        stderr_usage(
            f"invalid --expected-identity {args.expected_identity!r}; "
            "expected sha256:<64 lowercase hex digits>"
        )
        return EXIT_USAGE

    corrected, err = resolve_content(
        argparse.Namespace(
            content=args.corrected_content,
            content_file=args.corrected_file,
        )
    )
    if err is not None:
        stderr_usage(err.replace("--content", "--corrected-content").replace(
            "--corrected-content-file", "--corrected-file"
        ))
        return EXIT_USAGE
    if isinstance(corrected, bytes):
        try:
            corrected = corrected.decode("utf-8")
        except UnicodeDecodeError as exc:
            stderr_usage(f"corrected content is not valid UTF-8: {exc}")
            return EXIT_USAGE

    report_path = Path(raw_path)
    if not report_path.is_file():
        stderr_error(f"report not found: {raw_path}")
        return EXIT_FAIL
    report_path = report_path.resolve()
    try:
        current = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        stderr_error(f"report unreadable: {raw_path} — {exc}")
        return EXIT_FAIL

    observed_identity = report_identity.content_identity(current)
    corrected_identity = report_identity.content_identity(corrected)
    record: Dict[str, Any] = {
        "report_path": str(report_path),
        "expected_content_identity": args.expected_identity,
        "previous_content_identity": observed_identity,
        "corrected_content_identity": corrected_identity,
    }

    if args.expected_identity != observed_identity:
        return _refuse(
            "stale-report-identity",
            f"{report_path} no longer matches the bound publication "
            f"(expected {args.expected_identity}, observed "
            f"{observed_identity}) — re-observe the report and rebind the "
            "correction to its current identity",
            record,
        )
    if corrected == current:
        return _refuse(
            "correction-identical",
            "the corrected content is byte-identical to the current report",
            record,
        )

    variant, variant_err = validate_report.resolve_variant(
        report_path, current, args.variant
    )
    if variant is None:
        stderr_usage(variant_err)
        return EXIT_USAGE
    record["variant"] = variant

    project_root = request_trace.find_project_root(report_path)
    if project_root is None or report_path.parent.name != "reports":
        return _refuse(
            "report-outside-project",
            f"{report_path} is not a reports/ artifact inside a Cartopian "
            "project; corrections apply only to project report slots",
            record,
        )

    checks = validate_report.collect_checks(
        project_root, report_path, current, variant
    )
    failed = [item for item in checks if not item["pass"]]
    record["failed_checks"] = [item["name"] for item in failed]
    if not failed:
        return _refuse(
            "no-defect-to-correct",
            "the current report already passes every acceptance check; "
            "nothing mechanical remains to correct",
            record,
        )
    blocking = [
        item for item in failed
        if item["failure_class"] in ("substantive", "missing-input")
    ]
    if blocking:
        names = ", ".join(
            f"{item['name']} [{item['failure_class']}]" for item in blocking
        )
        return _refuse(
            "non-mechanical-findings-present",
            f"the report carries non-mechanical findings ({names}); a "
            "substantive judgment routes to the configured review loop or "
            "the operator, and a missing input is repaired before any "
            "re-dispatch — neither may be edited into compliance",
            record,
        )

    auth, auth_refusal = _build_authorization(
        failed, variant, current, project_root, report_path
    )
    if auth is None:
        return _refuse("unroutable-mechanical-check", auth_refusal, record)

    current_split = _split_sections(current)
    corrected_split = _split_sections(corrected)
    if current_split is None or corrected_split is None:
        return _refuse(
            "ambiguous-section-structure",
            "a section heading appears more than once; the correction "
            "cannot be confined to the defective grains",
            record,
        )

    violations, changed_regions = _confine(
        current_split, corrected_split, auth
    )
    if violations:
        return _refuse(
            "correction-outside-defect-scope",
            "the correction changes content outside the exact defective "
            "token, field, or row: "
            + "; ".join(sorted(set(violations)))
            + " — a mechanical correction preserves every unaffected span "
            "byte-for-byte, and a larger change is a rework handoff, not a "
            "correction",
            record,
        )

    # A passing routing token may not change under the cover of an unrelated
    # mechanical fix: the Status verdict drives lifecycle routing.
    current_status = parse_report._extract_status(current)
    if current_status in parse_report.STATUS_VERDICT:
        if parse_report._extract_status(corrected) != current_status:
            return _refuse(
                "status-token-changed",
                f"the valid Status token {current_status!r} may not change "
                "during a mechanical correction",
                record,
            )

    corrected_variant, corrected_err = validate_report.resolve_variant(
        report_path, corrected, args.variant
    )
    if corrected_variant != variant:
        return _refuse(
            "variant-changed",
            "the corrected content no longer resolves to the report's "
            f"variant ({variant}): {corrected_err or corrected_variant}",
            record,
        )
    corrected_checks = validate_report.collect_checks(
        project_root, report_path, corrected, variant
    )
    still_failing = [item for item in corrected_checks if not item["pass"]]
    if still_failing:
        names = ", ".join(
            f"{item['name']} [{item['failure_class']}]: {item['reason']}"
            for item in still_failing
        )
        return _refuse(
            "correction-does-not-validate",
            f"the corrected content still fails acceptance checks: {names}",
            record,
        )

    try:
        write_result = mediated_write.mediated_write(
            project_root,
            "report",
            report_path.name,
            corrected,
            expected_data=current.encode("utf-8"),
        )
    except mediated_write.GuardRefusal as refusal:
        return _refuse(refusal.rule, refusal.detail, record)

    record.update(
        {
            "ok": True,
            "report_content_identity": corrected_identity,
            "corrected_regions": sorted(set(changed_regions)),
            "resolved_checks": [item["name"] for item in failed],
            "revalidated": True,
            "bytes": write_result["bytes"],
        }
    )
    emit_record(record)
    return EXIT_OK
