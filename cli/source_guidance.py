"""Shared source-guidance parser and fail-closed validator.

The machine vocabulary lives in ``protocol/risk-and-practice-contract.json``.
This module projects that contract into existing task, specification, handoff,
and report surfaces. It creates no lifecycle state and uses only the Python
standard library.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from cli import deidentify


_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT_PATH = _REPO_ROOT / "protocol" / "risk-and-practice-contract.json"
_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _/-]*?):\s*(.*)$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_CONTEXT_IDENTITY_RE = re.compile(r"\d")
_PLACEHOLDER_RE = re.compile(r"^\s*(?:<.*>|n/?a|none|unknown|tbd|\.\.\.|…)?\s*$", re.I)


@lru_cache(maxsize=1)
def contract() -> Dict[str, Any]:
    """Load the one authoritative source-guidance vocabulary."""
    registry = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    return registry["source_guidance"]


def _header(content: str, name: str) -> Optional[str]:
    for line in content.splitlines():
        if line.startswith("## "):
            break
        match = _HEADER_RE.match(line.strip())
        if match and match.group(1).strip() == name:
            return match.group(2).strip()
    return None


def _section(content: str, heading: str) -> Optional[str]:
    lines = content.splitlines()
    start: Optional[int] = None
    body: List[str] = []
    for index, line in enumerate(lines):
        match = _H2_RE.match(line)
        if match and match.group(1).strip().lower() == heading.lower():
            if start is not None:
                return None
            start = index + 1
            continue
        if start is not None:
            if _H2_RE.match(line):
                break
            body.append(line)
    if start is None:
        return None
    return "\n".join(body).strip()


def _subsections(body: str) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in body.splitlines():
        match = _H3_RE.match(line)
        if match:
            current = match.group(1).strip()
            result.setdefault(current, [])
            continue
        if current is not None:
            result[current].append(line)
    return result


def _rows(lines: Sequence[str]) -> List[str]:
    """Return Markdown list rows with indented continuation lines folded in.

    Source records are semicolon-delimited list items, but Markdown authors and
    agents commonly wrap a long item across physical lines.  Treat a non-empty,
    indented line as part of the preceding bullet.  Unindented prose and blank
    lines terminate the current row so unrelated section text is never consumed.
    """
    rows: List[str] = []
    current: Optional[str] = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            if current is not None:
                rows.append(current)
            current = stripped[2:].strip()
        elif current is not None and stripped and line[:1].isspace():
            current = f"{current} {stripped}"
        else:
            if current is not None:
                rows.append(current)
                current = None
    if current is not None:
        rows.append(current)
    return rows


def _parse_labeled_row(row: str, fields: Sequence[Dict[str, str]]) -> Dict[str, str]:
    labels = {item["label"].lower(): item["id"] for item in fields}
    parsed: Dict[str, str] = {}
    for part in row.split(";"):
        label, sep, value = part.partition(":")
        if not sep:
            continue
        field_id = labels.get(label.strip().lower())
        if field_id is not None and field_id not in parsed:
            parsed[field_id] = value.strip()
    return parsed


def _placeholder(value: Optional[str], *, allow_na: bool = False) -> bool:
    if value is None:
        return True
    if allow_na and value.strip().lower() in {"n/a", "na"}:
        return False
    return bool(_PLACEHOLDER_RE.fullmatch(value))


def _blocker(code: str, detail: str, recovery: str) -> Dict[str, str]:
    return {"code": code, "detail": detail, "recovery": recovery}


def _base_record(declaration: str, owner_kind: Optional[str], owner_path: Optional[Path]) -> Dict[str, Any]:
    spec = contract()
    return {
        "contract_id": spec["contract_id"],
        "contract_version": spec["contract_version"],
        "declaration": declaration,
        "owner_kind": owner_kind,
        "owner_path": str(owner_path.resolve()) if owner_path is not None else None,
        "outcome": "invalid",
        "authoritative_sources": [],
        "conflict_resolution": None,
        "unverified_claims": [],
        "blockers": [],
        "blocker_codes": [],
        "deidentified_guidance": None,
    }


def _deidentified_source_identity(identity: str) -> str:
    """Return a stable assignee-facing alias for PM-scoped source identities."""
    if not deidentify.list_identifiers(identity):
        return identity
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"project-management-source sha256:{digest}"


def _deidentified_authoritative_sources(record: Dict[str, Any]) -> List[Dict[str, str]]:
    return [
        {
            **source,
            "identity": _deidentified_source_identity(source.get("identity", "")),
        }
        for source in record["authoritative_sources"]
    ]


def _finalize(record: Dict[str, Any]) -> Dict[str, Any]:
    record["blocker_codes"] = [item["code"] for item in record["blockers"]]
    if record["outcome"] not in {"not-declared", "not-applicable"}:
        record["outcome"] = "invalid" if record["blockers"] else "valid"
    if record["outcome"] == "valid":
        projected = {
            **record,
            "authoritative_sources": _deidentified_authoritative_sources(record),
        }
        rendered = render_guidance(projected)
        record["deidentified_guidance"] = deidentify.deidentify_spec(rendered)[0]
    return record


def evaluate_record(
    content: str,
    *,
    heading: str,
    declaration: str,
    owner_kind: str,
    owner_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Parse and validate one declared source-guidance/evidence section."""
    spec = contract()
    record = _base_record(declaration, owner_kind, owner_path)
    body = _section(content, heading)
    if body is None:
        record["blockers"].append(_blocker(
            "missing-source-guidance-section",
            f"declared source-backed work has no ## {heading} section",
            f"add ## {heading} with authoritative sources, conflict resolution, and unverified claims",
        ))
        return _finalize(record)

    sections = _subsections(body)
    source_lines = sections.get(spec["subsections"]["sources"], [])
    source_rows = _rows(source_lines)
    if not source_rows or source_rows == ["none"]:
        record["blockers"].append(_blocker(
            "missing-authoritative-source",
            "source-backed work names no authoritative source",
            "name at least one authoritative source identity and its applicable date, edition, revision, or version",
        ))
    else:
        for index, row in enumerate(source_rows, start=1):
            parsed = _parse_labeled_row(row, spec["source_fields"])
            record["authoritative_sources"].append(parsed)
            if _placeholder(parsed.get("identity")):
                record["blockers"].append(_blocker(
                    "missing-authoritative-source",
                    f"authoritative source {index} has no usable identity",
                    "name a stable source title, locator, publication, policy, or record identity",
                ))
            context_value = parsed.get("applicable_context")
            if _placeholder(context_value) or not _CONTEXT_IDENTITY_RE.search(context_value or ""):
                record["blockers"].append(_blocker(
                    "missing-applicable-context",
                    f"authoritative source {index} has no identifiable date or version context",
                    "record its effective date, publication date, edition, revision, or version",
                ))
            status = parsed.get("status", "").lower()
            if status not in spec["source_statuses"]:
                record["blockers"].append(_blocker(
                    "missing-applicable-context",
                    f"authoritative source {index} has invalid status {status!r}",
                    "set Status to current, stale, or unknown after checking the applicable context",
                ))
            elif status == "stale":
                record["blockers"].append(_blocker(
                    "stale-applicable-context",
                    f"authoritative source {index} is explicitly stale",
                    "replace it with a current source or obtain named authority to resolve the stale context",
                ))
            elif status == "unknown":
                record["blockers"].append(_blocker(
                    "missing-applicable-context",
                    f"authoritative source {index} has unknown applicability",
                    "verify the applicable date or version before proceeding",
                ))
            if _placeholder(parsed.get("scope")):
                record["blockers"].append(_blocker(
                    "missing-source-scope",
                    f"authoritative source {index} does not name the claims or decisions it governs",
                    "state the bounded scope for which this source is authoritative",
                ))

    conflict_rows = _rows(sections.get(spec["subsections"]["conflicts"], []))
    if len(conflict_rows) != 1 or conflict_rows == ["none"]:
        record["blockers"].append(_blocker(
            "missing-conflict-resolution",
            "source-backed work must carry exactly one conflict-resolution record",
            "record Status: none, resolved, or unresolved plus the governing rule or decision authority",
        ))
    else:
        conflict = _parse_labeled_row(conflict_rows[0], spec["conflict_fields"])
        record["conflict_resolution"] = conflict
        status = conflict.get("status", "").lower()
        if status not in spec["conflict_statuses"]:
            record["blockers"].append(_blocker(
                "missing-conflict-resolution",
                f"conflict status {status!r} is not declared",
                "set conflict Status to none, resolved, or unresolved",
            ))
        if _placeholder(conflict.get("rule")):
            record["blockers"].append(_blocker(
                "missing-conflict-resolution",
                "the conflict record names no precedence rule or decision authority",
                "name the rule or authority that decides which source governs",
            ))
        if status == "resolved" and _placeholder(conflict.get("decision")):
            record["blockers"].append(_blocker(
                "missing-conflict-resolution",
                "a resolved conflict names no decision",
                "record the applied resolution",
            ))
        if status == "unresolved":
            record["blockers"].append(_blocker(
                "unresolved-source-conflict",
                "authoritative sources remain in unresolved conflict",
                "obtain the named decision or apply the named precedence rule before proceeding",
            ))

    claim_rows = _rows(sections.get(spec["subsections"]["claims"], []))
    if not claim_rows:
        record["blockers"].append(_blocker(
            "unhandled-unverified-claim",
            "the unverified-claims disposition is absent",
            "write '- none' or record each remaining claim with all failure-signal fields",
        ))
    elif "none" in {row.lower() for row in claim_rows} and len(claim_rows) != 1:
        record["blockers"].append(_blocker(
            "unhandled-unverified-claim",
            "the claims list mixes 'none' with claim records",
            "use either '- none' or explicit claim records, not both",
        ))
    elif [row.lower() for row in claim_rows] != ["none"]:
        for index, row in enumerate(claim_rows, start=1):
            parsed = _parse_labeled_row(row, spec["claim_fields"])
            record["unverified_claims"].append(parsed)
            missing_fields = [
                item["id"] for item in spec["claim_fields"]
                if _placeholder(parsed.get(item["id"]))
            ]
            if missing_fields:
                record["blockers"].append(_blocker(
                    "unhandled-unverified-claim",
                    f"unverified claim {index} is missing: {', '.join(missing_fields)}",
                    "name the claim, decisiveness, missing authority or evidence, consequence, and next decision or proof",
                ))
            decisiveness = parsed.get("decisiveness", "").lower()
            if decisiveness not in spec["claim_decisiveness"]:
                record["blockers"].append(_blocker(
                    "unhandled-unverified-claim",
                    f"unverified claim {index} has invalid decisiveness {decisiveness!r}",
                    "set Decisiveness to decisive or non-decisive",
                ))
            elif decisiveness == "decisive":
                record["blockers"].append(_blocker(
                    "decisive-claim-unverified",
                    f"decisive claim remains unverified: {parsed.get('claim', '')}",
                    parsed.get("next") or "obtain the missing decision or proof before proceeding",
                ))
    return _finalize(record)


def _not_declared(path: Path) -> Dict[str, Any]:
    record = _base_record(contract()["legacy_declaration"], None, None)
    record["outcome"] = "not-declared"
    return _finalize(record)


def _not_applicable(declaration: str, path: Path) -> Dict[str, Any]:
    record = _base_record(declaration, None, None)
    record["outcome"] = "not-applicable"
    return _finalize(record)


def _find_project_root(path: Path) -> Optional[Path]:
    for candidate in path.parents:
        if (candidate / "cartopian.toml").is_file() or (candidate / "phases").is_dir():
            return candidate
    return None


def resolve_task_guidance(task_path: Path, *, content: Optional[str] = None) -> Dict[str, Any]:
    """Resolve the source-guidance owner declared by one task."""
    task_path = Path(task_path)
    if content is None:
        content = task_path.read_text(encoding="utf-8")
    declaration = _header(content, "Source guidance")
    if declaration is None:
        return _not_declared(task_path)
    declaration = declaration.lower()
    spec = contract()
    if declaration not in spec["task_declarations"]:
        record = _base_record(declaration, None, None)
        record["blockers"].append(_blocker(
            "source-guidance-owner-mismatch",
            f"task Source guidance value {declaration!r} is invalid",
            "set Source guidance to task, spec, or n/a",
        ))
        return _finalize(record)
    if declaration == "n/a":
        if _section(content, spec["section_headings"]["guidance"]) is not None:
            record = _base_record(declaration, None, None)
            record["blockers"].append(_blocker(
                "source-guidance-owner-mismatch",
                "task declares Source guidance: n/a but also carries a source-guidance section",
                "remove the section or declare its authoritative owner",
            ))
            return _finalize(record)
        return _not_applicable(declaration, task_path)
    if declaration == "task":
        return evaluate_record(
            content,
            heading=spec["section_headings"]["guidance"],
            declaration=declaration,
            owner_kind="task",
            owner_path=task_path,
        )

    project_root = _find_project_root(task_path)
    raw_spec = _header(content, "Spec") or ""
    if project_root is None or raw_spec.lower() in {"", "none", "n/a"}:
        record = _base_record(declaration, "spec", None)
        record["blockers"].append(_blocker(
            "source-guidance-owner-mismatch",
            "task delegates source guidance to a spec but names no resolvable spec",
            "name the governing spec or move the source record into the task",
        ))
        return _finalize(record)
    candidate = Path(raw_spec)
    if candidate.is_absolute():
        spec_path = candidate.resolve()
    else:
        spec_path = (project_root / (candidate if candidate.parts and candidate.parts[0] == "specs" else Path("specs") / candidate)).resolve()
    try:
        spec_path.relative_to((project_root / "specs").resolve())
    except ValueError:
        record = _base_record(declaration, "spec", spec_path)
        record["blockers"].append(_blocker(
            "source-guidance-owner-mismatch",
            "delegated spec path escapes the project's specs directory",
            "name a contained canonical spec",
        ))
        return _finalize(record)
    try:
        spec_content = spec_path.read_text(encoding="utf-8")
    except OSError as exc:
        record = _base_record(declaration, "spec", spec_path)
        record["blockers"].append(_blocker(
            "source-guidance-owner-mismatch",
            f"delegated source-guidance spec is unreadable: {exc}",
            "restore the spec or move the source record into the task",
        ))
        return _finalize(record)
    spec_declaration = (_header(spec_content, "Source guidance") or "").lower()
    if spec_declaration != "required":
        record = _base_record(declaration, "spec", spec_path)
        record["blockers"].append(_blocker(
            "source-guidance-owner-mismatch",
            "task delegates source guidance to a spec that does not declare Source guidance: required",
            "make the spec the declared source owner or move the record into the task",
        ))
        return _finalize(record)
    return evaluate_record(
        spec_content,
        heading=spec["section_headings"]["guidance"],
        declaration=declaration,
        owner_kind="spec",
        owner_path=spec_path,
    )


def validate_spec_content(content: str, *, owner_path: Optional[Path] = None) -> Dict[str, Any]:
    declaration = _header(content, "Source guidance")
    if declaration is None:
        return _not_declared(owner_path or Path("SPEC.md"))
    declaration = declaration.lower()
    if declaration == "n/a":
        if _section(content, contract()["section_headings"]["guidance"]) is not None:
            record = _base_record(declaration, "spec", owner_path)
            record["blockers"].append(_blocker(
                "source-guidance-owner-mismatch",
                "spec declares Source guidance: n/a but also carries a source-guidance section",
                "remove the section or declare Source guidance: required",
            ))
            return _finalize(record)
        return _not_applicable(declaration, owner_path or Path("SPEC.md"))
    if declaration != "required":
        record = _base_record(declaration, "spec", owner_path)
        record["blockers"].append(_blocker(
            "source-guidance-owner-mismatch",
            f"spec Source guidance value {declaration!r} is invalid",
            "set Source guidance to required or n/a",
        ))
        return _finalize(record)
    return evaluate_record(
        content,
        heading=contract()["section_headings"]["guidance"],
        declaration=declaration,
        owner_kind="spec",
        owner_path=owner_path,
    )


def readiness_check(record: Dict[str, Any]) -> Dict[str, Any]:
    passed = record["outcome"] in {"valid", "not-applicable", "not-declared"}
    reason = None
    if not passed:
        reason = "; ".join(
            f"{item['code']}: {item['detail']} — {item['recovery']}"
            for item in record["blockers"]
        )
    return {"name": "source-guidance-valid", "pass": passed, "reason": reason}


def active_projection(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return active/invalid guidance, or ``None`` for no-context outcomes.

    Legacy and explicitly not-applicable work loads no source-guidance body.
    Keeping those outcomes as JSON ``null`` preserves a stable aggregator key
    without charging active context for an empty record.
    """
    if record["outcome"] in {"not-declared", "not-applicable"}:
        return None
    return record


def render_guidance(record: Dict[str, Any], *, heading: str = "Source guidance") -> str:
    """Render the canonical human projection from a resolved record."""
    lines = [f"## {heading}", "", "### Authoritative sources", ""]
    for source in record["authoritative_sources"]:
        lines.append(
            "- Identity: {identity}; Applicable context: {applicable_context}; "
            "Status: {status}; Scope: {scope}".format(**source)
        )
    lines.extend(["", "### Conflict resolution", ""])
    conflict = record["conflict_resolution"]
    lines.append(
        "- Status: {status}; Rule: {rule}; Decision: {decision}".format(**conflict)
    )
    lines.extend(["", "### Unverified claims", ""])
    if not record["unverified_claims"]:
        lines.append("- none")
    else:
        for claim in record["unverified_claims"]:
            lines.append(
                "- Claim: {claim}; Decisiveness: {decisiveness}; Missing: {missing}; "
                "Consequence: {consequence}; Next: {next}".format(**claim)
            )
    return "\n".join(lines) + "\n"


def resolve_report_evidence(task_path: Path, report_content: str) -> Dict[str, Any]:
    """Validate completion evidence against a source-backed task contract."""
    guidance = resolve_task_guidance(task_path)
    if guidance["outcome"] in {"not-declared", "not-applicable"}:
        return {
            "required": False,
            "outcome": "not-required",
            "guidance": guidance,
            "evidence": None,
            "blockers": [],
            "blocker_codes": [],
        }
    if guidance["outcome"] != "valid":
        blocker = _blocker(
            "governing-source-guidance-invalid",
            "the task's governing source record is invalid",
            "repair the task/spec source guidance before accepting completion evidence",
        )
        return {
            "required": True,
            "outcome": "invalid",
            "guidance": guidance,
            "evidence": None,
            "blockers": [blocker],
            "blocker_codes": [blocker["code"]],
        }
    evidence = evaluate_record(
        report_content,
        heading=contract()["section_headings"]["evidence"],
        declaration="required",
        owner_kind="report",
        owner_path=None,
    )
    blockers = list(evidence["blockers"])
    governed = {
        (item.get("identity"), item.get("applicable_context"))
        for item in _deidentified_authoritative_sources(guidance)
    }
    for source in evidence["authoritative_sources"]:
        key = (source.get("identity"), source.get("applicable_context"))
        if key not in governed:
            blockers.append(_blocker(
                "source-evidence-not-in-guidance",
                f"completion evidence names source {source.get('identity')!r} in context {source.get('applicable_context')!r}, which is not present in the governing guidance",
                "record only sources actually applied from the governing guidance, or amend the guidance before completing the task",
            ))
    codes = [item["code"] for item in blockers]
    return {
        "required": True,
        "outcome": "invalid" if blockers else "valid",
        "guidance": guidance,
        "evidence": evidence,
        "blockers": blockers,
        "blocker_codes": codes,
    }
