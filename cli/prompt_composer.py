"""Deterministic assignee (coder) assignment-prompt composition.

The composer resolves one task's authoritative inputs — task bundle facts,
role packet, specification, applicable standards, the three selector results,
source guidance, request evidence, and the report skeleton — and produces:

- an audience-scoped assignee prompt (an execution interface, not an audit
  log: no raw JSON, no routing diagnostics, no diagnostic hashes, no inactive
  guidance, no lifecycle bookkeeping — the one machine binding the prompt
  carries is the typed input-payload declaration, see
  ``cli/assignment_inputs.py``),
- a machine-readable trace receipt carrying the complete selector results,
  raw records, projection receipts, and per-section measurements,
- a content identity binding the two.

The section set, validation rules, and measured budgets are owned by
``protocol/assignment-prompt-contract.json``. Composition fails closed: a
finding with severity ``fail`` refuses the prompt rather than issuing it.

Stdlib only. Read-only: nothing here writes project files.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli import (
    assignment_inputs,
    deidentify,
    governance_reads,
    judgment_guidance,
    practice_packs,
    report_identity,
    request_trace,
    risk_contract,
    source_guidance,
)
from cli.markdown_fences import FenceTracker

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "protocol"
    / "assignment-prompt-contract.json"
)

_TASK_TITLE_ID_RE = re.compile(r"^TASK-\d{2}-\d{3}\s*:\s*")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_ROW_RE = re.compile(r"^-\s+(.*)$")
_RISK_ROW_RE = re.compile(
    r"^([a-z][a-z-]*)\s*:\s*([a-z][a-z-]*)\s*;\s*Fact\s*:\s*(.+)$"
)
_ENVELOPE_ROW_RE = re.compile(r"^([a-z][a-z-]*)\s*:\s*(.*)$")
_APPLIES_TO_RE = re.compile(r"^Applies to\s*:\s*(.+)$", re.IGNORECASE)
_JSON_LINE_RE = re.compile(r"^\s*[\[{]\s*\"")
_CURRENCY_CLAIM_RE = re.compile(
    r"\b(?:remains|is|stays)\s+current\b", re.IGNORECASE
)

_JUDGMENT_ENVELOPE_FACTS = {
    "lifecycle-boundaries": "lifecycle_boundaries",
    "open-failure-conditions": "open_failure_conditions",
}
_PRACTICE_ENVELOPE_FACTS = {
    "primary-outcomes": "primary_outcomes",
    "artifact-kinds": "artifact_kinds",
    "incidental-terms": "incidental_terms",
    "exclusions": "exclusions",
    "lifecycle-substrate-activities": "lifecycle_substrate_activities",
    "domain-scopes": "domain_scopes",
}


class ComposeRefusal(Exception):
    """Stable fail-closed diagnostic for an unresolvable composition input."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def load_contract() -> Dict[str, Any]:
    """Read the one authoritative composition contract; fail closed."""
    try:
        contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        contract["sections"]["required"]
        contract["section_budgets"]["budgets"]
        governance_reads.load_rule(contract)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        governance_reads.GovernanceReadRuleError,
    ) as exc:
        raise ComposeRefusal(
            "assignment-contract-unavailable",
            "the assignment-prompt contract is unreadable",
        ) from exc
    return contract


def sha256_identity(data: bytes) -> str:
    """The composer's content-identity form for raw bytes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_identity(payload: Any) -> str:
    """The composer's content-identity form for a JSON-serializable record."""
    return sha256_identity(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )


# Backwards-compatible internal aliases.
_sha256 = sha256_identity
_canonical_identity = canonical_identity


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


_HEADING_LINE_RE = re.compile(r"^(#{1,4})\s")

# The completion-report identity is the one PM identifier a coder prompt is
# built to carry: the coder writes to the given report path, and the protocol
# links the report back by filename precisely so no *other* identifier is
# ever needed. Review-slot identities stay flagged.
_SANCTIONED_IDENTIFIER_RE = re.compile(r"^REPORT-\d{2}-\d{3}$")


def _embed(text: str, *, strip_h1: bool = False, demote: int = 1) -> str:
    """Prepare a standalone document for embedding under a prompt section.

    Optionally strips the document's own H1 title and demotes every remaining
    heading by ``demote`` levels (outside fences), so embedded content never
    fractures the prompt's top-level section structure.
    """
    lines: List[str] = []
    tracker = FenceTracker()
    h1_stripped = not strip_h1
    for line in text.splitlines():
        if tracker.feed(line):
            lines.append(line)
            continue
        if tracker.in_fence:
            lines.append(line)
            continue
        if not h1_stripped and line.startswith("# "):
            h1_stripped = True
            continue
        match = _HEADING_LINE_RE.match(line)
        if match:
            line = "#" * demote + line
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------------------
# Task-section parsing (deterministic; no PM interpretation)
# ---------------------------------------------------------------------------

def _section_body(content: str, heading: str) -> Optional[str]:
    lines = content.splitlines()
    start: Optional[int] = None
    body: List[str] = []
    tracker = FenceTracker()
    for line in lines:
        is_delimiter = tracker.feed(line)
        match = (
            None
            if tracker.in_fence or is_delimiter
            else _H2_RE.match(line)
        )
        if match and match.group(1).strip().lower() == heading.lower():
            start = 0
            body = []
            continue
        if start is not None:
            if match:
                break
            body.append(line)
    if start is None:
        return None
    return "\n".join(body).strip()


def _section_rows(body: str) -> List[str]:
    rows: List[str] = []
    current: Optional[str] = None
    for line in body.splitlines():
        stripped = line.strip()
        match = _ROW_RE.match(stripped)
        if match:
            if current is not None:
                rows.append(current)
            current = match.group(1).strip()
        elif current is not None and stripped and line[:1].isspace():
            current = f"{current} {stripped}"
        else:
            if current is not None:
                rows.append(current)
                current = None
    if current is not None:
        rows.append(current)
    return rows


def parse_risk_observations(content: str) -> List[Dict[str, str]]:
    """Read the five declared risk-observation records from the task body."""
    body = _section_body(content, "Risk observations")
    if body is None:
        raise ComposeRefusal(
            "missing-risk-observations",
            "the task declares no ## Risk observations section",
        )
    records: List[Dict[str, str]] = []
    for row in _section_rows(body):
        match = _RISK_ROW_RE.match(row)
        if match is None:
            raise ComposeRefusal(
                "invalid-risk-observation",
                f"unparseable risk-observation row: {row!r}",
            )
        records.append(
            {
                "observation": match.group(1),
                "state": match.group(2),
                "supporting_fact": match.group(3).strip(),
            }
        )
    return records


def _identity_list(raw: str) -> List[str]:
    values = [item.strip() for item in raw.split(",")]
    values = [item for item in values if item]
    if [item.lower() for item in values] == ["none"]:
        return []
    return values


def _parse_envelope(
    content: str, heading: str, facts: Dict[str, str]
) -> Optional[Dict[str, Any]]:
    """Parse one declared envelope section into its fact lists.

    Returns ``None`` for a legacy task with no section — the selectors treat
    an empty envelope as the valid ``none`` outcome.
    """
    body = _section_body(content, heading)
    if body is None:
        return None
    envelope: Dict[str, Any] = {field: [] for field in facts.values()}
    hint: Optional[str] = None
    for row in _section_rows(body):
        match = _ENVELOPE_ROW_RE.match(row)
        if match is None:
            raise ComposeRefusal(
                "invalid-envelope-row",
                f"unparseable {heading} row: {row!r}",
            )
        key, raw = match.group(1), match.group(2).strip()
        if key == "authorized-profile-hint":
            hint = None if raw.lower() in {"none", "n/a", ""} else raw
            continue
        if key not in facts:
            raise ComposeRefusal(
                "invalid-envelope-row",
                f"{heading} declares an unknown fact: {key}",
            )
        envelope[facts[key]] = _identity_list(raw)
    if hint is not None:
        envelope["authorized_profile_hint"] = hint
    return envelope


# ---------------------------------------------------------------------------
# Standards projection
# ---------------------------------------------------------------------------

def project_standards(
    standards_content: str, applicability_identities: List[str]
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Select the standards sections applicable to one assignment.

    A section may open with an ``Applies to: <identity, ...>`` line naming the
    task facts it binds; an untagged section (or ``Applies to: all``) applies
    to every assignment. Applicability identities are the task's declared
    practice-pack envelope facts plus the authorized profile hint — the same
    closed fact set that routes pack selection, so no free-form PM judgment
    enters the projection. The tag line itself never reaches the assignee.
    """
    declared = {value.casefold() for value in applicability_identities}
    declared.add("all")
    selected: List[str] = []
    excluded: List[Dict[str, str]] = []
    current_heading: Optional[str] = None
    current_lines: List[str] = []
    preamble_done = False

    def flush() -> None:
        nonlocal current_heading, current_lines
        if current_heading is None:
            current_lines = []
            return
        body_lines = list(current_lines)
        tags: Optional[List[str]] = None
        for index, line in enumerate(body_lines):
            if not line.strip():
                continue
            match = _APPLIES_TO_RE.match(line.strip())
            if match:
                tags = [item.strip() for item in match.group(1).split(",") if item.strip()]
                del body_lines[index]
            break
        applies = tags is None or bool(
            {tag.casefold() for tag in tags} & declared
        )
        if applies:
            section = "\n".join([current_heading, *body_lines]).strip()
            if section:
                selected.append(section)
        else:
            excluded.append(
                {
                    "heading": current_heading.lstrip("# ").strip(),
                    "reason": "applies-to:" + ",".join(tags or []),
                }
            )
        current_heading = None
        current_lines = []

    tracker = FenceTracker()
    for line in standards_content.splitlines():
        is_delimiter = tracker.feed(line)
        if not tracker.in_fence and not is_delimiter and _H2_RE.match(line):
            flush()
            preamble_done = True
            current_heading = line
            continue
        if not preamble_done:
            continue
        current_lines.append(line)
    flush()

    receipt = {
        "selected_headings": [
            section.splitlines()[0].lstrip("# ").strip() for section in selected
        ],
        "excluded_sections": excluded,
        "applicability_identities": sorted(declared),
    }
    if not selected:
        return None, receipt
    projected = deidentify.scrub_identifiers("\n\n".join(selected)).strip() + "\n"
    return projected, receipt


# ---------------------------------------------------------------------------
# Assignee-facing renderings (no raw JSON crosses this boundary)
# ---------------------------------------------------------------------------

def render_risk_guidance(projection: Dict[str, Any]) -> str:
    lines = [
        f"Risk band: {projection['band']}. The observations behind it:",
        "",
    ]
    for reason in projection["reasons"]:
        lines.append(
            f"- {reason['observation']}: {reason['state']} — "
            f"{reason['supporting_fact']}"
        )
    lines += [
        "",
        f"- Required evidence: {projection['evidence_expectation']} — answer "
        "it directly in the report's Risk-scaled evidence section.",
        f"- Operator gate: {projection['operator_gate']} — when a gate "
        "applies, record in the report how it was satisfied before the gated "
        "action.",
        f"- Contingency: {projection['contingency_expectation']} — record the "
        "recovery or stop-condition evidence the report section names.",
    ]
    return "\n".join(lines)


def render_judgment_guidance(projection: Dict[str, Any]) -> Optional[str]:
    if projection["outcome"] != "active":
        return None
    lines: List[str] = []
    for hold in projection["holds"]:
        lines.append(
            f"- Active hold at the **{hold['boundary_id']}** boundary: "
            f"{hold['lifecycle_boundary'] or hold['boundary_id']}"
        )
        lines.append(
            f"  Open failure ({hold['failure_id']}): "
            f"{hold['non_enforceable_failure'] or hold['failure_id']}"
        )
    if projection["instructions"]:
        lines += ["", _embed(projection["instructions"], strip_h1=True, demote=2)]
    return "\n".join(lines)


def render_practice_guidance(projection: Dict[str, Any]) -> Optional[str]:
    if projection["outcome"] != "selected":
        return None
    lines = [
        _embed(projection["capsule"], strip_h1=True, demote=2)
        if projection["capsule"]
        else ""
    ]
    if projection["applicable_sources"]:
        lines += ["", "Applicable sources for this profile:", ""]
        for source in projection["applicable_sources"]:
            entry = (
                f"- {source['title']} ({source['context']}) — "
                f"{source['governed_scope']}"
            )
            if source["applicability_boundary"]:
                entry += f"; applies when {source['applicability_boundary']}"
            lines.append(entry)
    return "\n".join(lines).strip()


def _source_guidance_section(record: Dict[str, Any], contract: Dict[str, Any]) -> str:
    """Render the one authoritative source-guidance body (heading stripped)."""
    rendered = record["deidentified_guidance"]
    _, _, body = rendered.partition("\n")
    note = contract.get("source_currency_note", "").strip()
    text = body.strip()
    if note:
        text += "\n\n" + note
    return text


def _reference_source_evidence_lines() -> List[str]:
    """The report-skeleton source-evidence contract, by reference.

    The full record is rendered once, in the prompt's ``## Source guidance``
    section; the report contract references it instead of reproducing it.
    """
    return [
        "## Source evidence",
        "",
        "Reproduce this section's three subsections by copying rows from the "
        "assignment prompt's ## Source guidance section — identities and "
        "applicable contexts unchanged. Include only the sources you actually "
        "applied and delete the rest; never introduce a source outside the "
        "supplied guidance.",
        "",
        "### Authoritative sources",
        "",
        "<the full row, copied unchanged, for each supplied source you "
        "actually applied>",
        "",
        "### Conflict resolution",
        "",
        "<the conflict-resolution row, copied unchanged>",
        "",
        "### Unverified claims",
        "",
        "- none",
        "",
        "Keep `- none` when no claim remains unverified. Otherwise replace it "
        "with one row per remaining claim, each carrying all five fields in "
        "exactly this form:",
        "`- Claim: <unverified claim>; Decisiveness: <decisive | "
        "non-decisive>; Missing: <authority or evidence>; Consequence: "
        "<consequence of proceeding>; Next: <decision or proof required>`",
        "A `decisive` claim may not remain unverified in a complete report.",
        "",
    ]


def _risk_scaled_evidence_lines(projection: Dict[str, Any]) -> List[str]:
    return [
        "## Risk-scaled evidence",
        "",
        f"- Band: {projection['band']}",
        f"- Evidence expectation: {projection['evidence_expectation']}; "
        "Evidence: <direct proof>",
        f"- Operator gate: {projection['operator_gate']}; Disposition: "
        "<authority or approval evidence, or n/a when no gate applies>",
        f"- Contingency expectation: {projection['contingency_expectation']}; "
        "Evidence: <recovery action, trigger/owner, or evidenced stop "
        "condition>",
        "",
    ]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def split_sections(prompt: str) -> List[Tuple[str, str]]:
    """Split a composed prompt into ``(section_name, section_text)`` pairs.

    The leading title block is returned under the name ``(title)``. Fenced
    content never opens or closes a section.
    """
    sections: List[Tuple[str, List[str]]] = [("(title)", [])]
    tracker = FenceTracker()
    for line in prompt.splitlines():
        is_delimiter = tracker.feed(line)
        match = (
            None
            if tracker.in_fence or is_delimiter
            else _H2_RE.match(line)
        )
        if match:
            sections.append((match.group(1).strip(), [line]))
            continue
        sections[-1][1].append(line)
    return [(name, "\n".join(lines)) for name, lines in sections]


def _finding(code: str, detail: str, recovery: str, severity: str = "fail") -> Dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "detail": detail,
        "recovery": recovery,
    }


def _outside_fences(text: str) -> str:
    """Return the prompt text with fenced content removed.

    Fenced blocks carry curated data — the embedded report skeleton and
    complete deliverable inputs — which is content the assignee consumes, not
    prompt structure, so structural duplicate checks never look inside them.
    """
    kept: List[str] = []
    tracker = FenceTracker()
    for line in text.splitlines():
        if tracker.feed(line):
            continue
        if not tracker.in_fence:
            kept.append(line)
    return "\n".join(kept)


def _raw_json_present(text: str) -> bool:
    """Detect machine JSON pasted as prompt content.

    A fence explicitly labeled ``json`` is a diagnostic paste; a bare JSON
    object line outside any fence is the same paste without the fence. Data
    inside a neutral fence (a curated deliverable that happens to be JSON) is
    assignment input, not diagnostics, and is not flagged.
    """
    tracker = FenceTracker()
    for line in text.splitlines():
        was_in_fence = tracker.in_fence
        if tracker.feed(line):
            if not was_in_fence and "json" in line.strip().lstrip("`~").lower():
                return True
            continue
        if not tracker.in_fence and _JSON_LINE_RE.match(line):
            return True
    return False


def _component_governance_findings(
    contract: Dict[str, Any], origins: List[Tuple[str, str]]
) -> Tuple[List[Dict[str, str]], frozenset]:
    """Blanket governance-read findings per originating prompt component.

    Runs before assembly on the components that carry project-authored
    instruction prose, so a violation is attributed to the artifact the
    operator must fix rather than to the assembled section it lands in. The
    returned marker set lets the final section-aware validation skip
    re-reporting the same contamination.
    """
    read_rule = governance_reads.load_rule(contract)
    findings: List[Dict[str, str]] = []
    markers: set = set()
    for origin, text in origins:
        if not text:
            continue
        for item in governance_reads.instruction_violations(text, read_rule):
            markers.add(item["marker"])
            findings.append(_finding(
                "blanket-governance-read",
                f"{origin} directs the assignee to read a governance "
                f"document: {item['marker']!r} — offending statement: "
                f"{item['statement'][:160]!r}",
                f"remove or bound the offending statement in {origin}; "
                + read_rule["recovery"],
            ))
    return findings, frozenset(markers)


def validate_authored_body(body: str) -> List[Dict[str, str]]:
    """Contamination findings for a hand-authored task-assignment body.

    The composed path runs the full contract validation; the authored path
    (``write-prompt --content*``) enforces the contamination subset named by
    the contract's ``authored_body_findings`` so a manually assembled body
    cannot reintroduce audit-log content, without imposing the composed
    structure on historical projects. Hand-authored text cannot declare
    itself a trusted input payload: the typed payload sections and marker are
    machine-owned, so their presence in an authored body is itself a finding.
    """
    contract = load_contract()
    allowed = set(contract.get("authored_body_findings", []))
    findings = validate_prompt(
        body, contract, structural=False, payload_origin="authored"
    )
    return [item for item in findings if item["code"] in allowed]


def _payload_findings(
    prompt: str,
    payload_entries: List[Dict[str, Any]],
    input_payloads: Optional[List[Dict[str, Any]]],
    payload_origin: str,
) -> List[Dict[str, str]]:
    """Findings over the typed input-payload channel.

    An authored body may not declare payloads or carry the machine-owned
    payload sections at all. A machine body's payloads must each verify
    against their declared binding, sit in their channel's section, and —
    when the composer's payload manifest is supplied — correspond one-to-one
    with the machine-resolved assignment inputs.
    """
    findings: List[Dict[str, str]] = []
    if payload_origin == "authored":
        for entry in payload_entries:
            findings.append(_finding(
                "unbound-input-payload",
                "a hand-authored body declares a typed input payload (line "
                f"{entry['line_number']}); payload sections are machine-created",
                "remove the declaration — the mediated writer materializes "
                "input payloads from the machine-resolved assignment inputs",
            ))
        for name, _text in split_sections(prompt):
            if name in assignment_inputs.CHANNEL_SECTIONS.values():
                findings.append(_finding(
                    "unbound-input-payload",
                    f"a hand-authored body carries the machine-owned section "
                    f"{name!r}",
                    "remove the section — the mediated writer materializes "
                    "input payloads from the machine-resolved assignment "
                    "inputs",
                ))
        return findings

    verified_keys: List[Tuple[str, str, str, int]] = []
    for entry in payload_entries:
        if entry["error"] is not None or not entry["verified"]:
            findings.append(_finding(
                "input-payload-mismatch",
                f"the input payload at line {entry['line_number']} does not "
                "verify against its declared binding "
                f"({entry['error'] or 'digest mismatch'})",
                "recompose the prompt so every payload is machine-created "
                "from the current resource content",
            ))
            continue
        if entry["section"] != assignment_inputs.CHANNEL_SECTIONS[entry["channel"]]:
            findings.append(_finding(
                "unbound-input-payload",
                f"the {entry['channel']} payload at line "
                f"{entry['line_number']} appears outside its machine-owned "
                f"section (found under {entry['section']!r})",
                "recompose the prompt; a payload block belongs only in its "
                "channel's declared input section",
            ))
            continue
        verified_keys.append((
            entry["channel"],
            entry["logical"],
            entry["declared_sha256"],
            entry["declared_bytes"],
        ))

    if input_payloads is not None:
        manifest_keys = [
            (
                item["channel"],
                item["logical"],
                item["content_sha256"],
                item["content_bytes"],
            )
            for item in input_payloads
        ]
        remaining = list(manifest_keys)
        for key in verified_keys:
            if key in remaining:
                remaining.remove(key)
            else:
                findings.append(_finding(
                    "unbound-input-payload",
                    f"the prompt declares a payload for {key[1]!r} that is "
                    "not among the machine-resolved assignment inputs",
                    "only the composer and mediated writer may create typed "
                    "payload sections; recompose the prompt",
                ))
        for key in remaining:
            findings.append(_finding(
                "input-payload-mismatch",
                f"the machine-resolved assignment input {key[1]!r} has no "
                "verified payload in the prompt",
                "recompose the prompt from the current resource content",
            ))
    return findings


def validate_prompt(
    prompt: str,
    contract: Dict[str, Any],
    *,
    structural: bool = True,
    budget_reasons: Optional[Dict[str, str]] = None,
    governance_markers_reported: Optional[frozenset] = None,
    input_payloads: Optional[List[Dict[str, Any]]] = None,
    payload_origin: str = "machine",
) -> List[Dict[str, str]]:
    """Validate one assignee-facing prompt body against the contract.

    ``governance_markers_reported`` names governance-read markers already
    reported by the component-level validation in :func:`compose`; the
    section-aware defense-in-depth check here skips them so one contamination
    surfaces once, attributed to its originating component.

    Contamination and deidentification checks inspect the *instruction
    channel* only: verified machine-created input payloads (see
    ``cli/assignment_inputs.py``) are exact assignment input data, so their
    contents — fenced JSON, Cartopian identifiers, instruction-like prose —
    are never validation findings. ``input_payloads`` is the composer's
    machine-built payload manifest; when supplied, the prompt's payloads must
    correspond to it one-to-one. ``payload_origin="authored"`` marks a
    hand-authored body, in which any payload declaration is itself a finding
    and no payload exemption exists.
    """
    findings: List[Dict[str, str]] = []
    sections = split_sections(prompt)
    by_name = {name: text for name, text in sections}

    payload_entries = assignment_inputs.extract_payload_blocks(prompt)
    findings.extend(_payload_findings(
        prompt, payload_entries, input_payloads, payload_origin
    ))
    instruction = (
        prompt
        if payload_origin == "authored"
        else assignment_inputs.strip_payload_blocks(prompt)
    )
    instruction_sections = split_sections(instruction)
    instruction_by_name = {name: text for name, text in instruction_sections}

    if _raw_json_present(instruction):
        findings.append(_finding(
            "raw-diagnostic-json",
            "a raw machine JSON payload appears in the assignee prompt",
            "carry machine results in the trace receipt and render only the "
            "assignee projection",
        ))

    unfenced = _outside_fences(instruction)
    source_renderings = unfenced.count("### Authoritative sources")
    if source_renderings > 1:
        findings.append(_finding(
            "duplicate-source-guidance",
            "the source-guidance record is rendered more than once",
            "render one authoritative ## Source guidance section and "
            "reference it elsewhere",
        ))

    for marker in contract.get("reviewer_only_markers", []):
        if marker in instruction:
            findings.append(_finding(
                "reviewer-only-content",
                f"reviewer-audience material present: {marker!r}",
                "remove review-audience sections from the coder prompt",
            ))
            break

    for marker in contract.get("pm_lifecycle_markers", []):
        if marker in instruction:
            findings.append(_finding(
                "pm-lifecycle-instruction",
                f"PM lifecycle instruction present: {marker!r}",
                "PM lifecycle operations stay out of assignee prompts",
            ))
            break

    identifiers = [
        token
        for token in deidentify.list_identifiers(instruction)
        if not _SANCTIONED_IDENTIFIER_RE.fullmatch(token)
    ]
    if identifiers:
        findings.append(_finding(
            "pm-identifier-present",
            "unredacted project-management identifiers present: "
            + ", ".join(identifiers[:5]),
            "deidentify every projected input before composition",
        ))

    # Section-aware: a source identity/provenance row inside Source guidance
    # is data about a source, not an instruction to read it; the exemption is
    # per-line, so imperative prose anywhere — including inside Source
    # guidance — still fails.
    read_rule = governance_reads.load_rule(contract)
    suppressed = governance_markers_reported or frozenset()
    for item in governance_reads.markdown_violations(instruction, read_rule):
        if item["marker"] in suppressed:
            continue
        findings.append(_finding(
            "blanket-governance-read",
            "the prompt directs the assignee to read a governance document: "
            f"{item['marker']!r} (section {item['section']!r}: "
            f"{item['statement'][:160]!r})",
            read_rule["recovery"],
        ))

    guidance_section = instruction_by_name.get("Source guidance", "")
    if guidance_section and _CURRENCY_CLAIM_RE.search(guidance_section):
        if "Status: current" not in guidance_section:
            findings.append(_finding(
                "stale-dynamic-source-claim",
                "the prompt claims source currency the governing record does "
                "not establish",
                "verify compatibility and update the governing record, or "
                "drop the currency claim",
            ))

    if not structural:
        return findings

    for required in contract["sections"]["required"]:
        text = by_name.get(required, "")
        body = "\n".join(text.splitlines()[1:]).strip() if text else ""
        if not body:
            findings.append(_finding(
                "missing-required-section",
                f"required section absent or empty: {required}",
                "compose the prompt from resolved authoritative inputs",
            ))

    outcome = by_name.get("Outcome and done criteria", "")
    if outcome and "- [ ]" not in outcome and "acceptance items in the" not in outcome:
        findings.append(_finding(
            "missing-acceptance-criteria",
            "the outcome section carries no checkable done criteria",
            "carry the task's acceptance checklist or the contract's "
            "acceptance items",
        ))

    seen_criteria: Dict[str, str] = {}
    for name, text in instruction_sections:
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- [ ]"):
                continue
            key = _normalized(stripped)
            if key in seen_criteria and seen_criteria[key] != name:
                findings.append(_finding(
                    "duplicate-contract-content",
                    f"acceptance criterion repeated across sections "
                    f"({seen_criteria[key]!r} and {name!r}): {stripped[:80]!r}",
                    "render one authoritative contract plus the task-specific "
                    "delta only",
                ))
                break
            seen_criteria.setdefault(key, name)

    budgets = contract["section_budgets"]["budgets"]
    reasons = budget_reasons or {}
    for name, text in sections:
        if name == "(title)":
            continue
        limit = budgets.get(name)
        size = len(text.encode("utf-8"))
        if limit is not None and size > limit and name not in reasons:
            findings.append(_finding(
                "section-over-budget",
                f"section {name!r} is {size} bytes; measured budget is "
                f"{limit} bytes and no explicit reason is recorded",
                "trim the section to its audience-scoped content or record "
                "an explicit oversize reason",
            ))
    return findings


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def _content_fence(text: str) -> str:
    longest = 0
    for match in re.finditer(r"`+", text):
        longest = max(longest, len(match.group(0)))
    return "`" * max(3, longest + 1)


def _fenced(text: str, info: str = "text") -> str:
    fence = _content_fence(text)
    return f"{fence}{info}\n{text.rstrip()}\n{fence}"


def _deidentified_title(task_title: str) -> str:
    title = _TASK_TITLE_ID_RE.sub("", task_title).strip()
    title = deidentify.scrub_identifiers(title).strip()
    return title or "Assignment"


def _resolve_selector_results(content: str) -> Dict[str, Any]:
    """Run the three deterministic selectors from the task's declared facts."""
    observations = parse_risk_observations(content)
    try:
        risk_result = risk_contract.classify_risk(observations)
    except risk_contract.RiskContractError as exc:
        raise ComposeRefusal(exc.code, exc.detail) from exc

    judgment_envelope = _parse_envelope(
        content, "Judgment envelope", _JUDGMENT_ENVELOPE_FACTS
    ) or {"lifecycle_boundaries": [], "open_failure_conditions": []}
    judgment_result = judgment_guidance.select_judgment_guidance(
        judgment_envelope
    )
    if judgment_result["outcome"] == "invalid":
        error = judgment_result["error"] or {}
        raise ComposeRefusal(
            error.get("code", "judgment-invalid"),
            error.get("detail", "judgment guidance did not resolve"),
        )

    practice_envelope = _parse_envelope(
        content, "Practice-pack envelope", _PRACTICE_ENVELOPE_FACTS
    ) or {field: [] for field in _PRACTICE_ENVELOPE_FACTS.values()}
    practice_result = practice_packs.select_practice_pack(practice_envelope)
    if practice_result["outcome"] in {"invalid", "ambiguous"}:
        error = practice_result["error"] or {}
        raise ComposeRefusal(
            error.get("code", "pack-selection-invalid"),
            error.get("detail", "practice-pack selection did not resolve"),
        )

    return {
        "observations": observations,
        "risk": risk_result,
        "judgment_envelope": judgment_envelope,
        "judgment": judgment_result,
        "practice_envelope": practice_envelope,
        "practice": practice_result,
    }


def _acceptance_rows(content: str) -> List[str]:
    body = _section_body(content, "Acceptance") or ""
    return [
        deidentify.scrub_field(line.strip())
        for line in body.splitlines()
        if line.strip().startswith("- [ ]")
    ]


def _governance_readable(effective_grants: List[str]) -> bool:
    return "read:governance" in effective_grants


def _read_resource(path: str, what: str) -> str:
    # newline="" keeps CRLF byte-exact: the payload binding is hashed over
    # these bytes and preflight compares against the raw resource on disk.
    try:
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ComposeRefusal(
            "deliverable-input-unreadable", f"{what} is not readable UTF-8: {exc}"
        ) from exc


def compose(task_path: Path, role: str) -> Dict[str, Any]:
    """Compose one task's assignee prompt and its bound trace receipt.

    Raises :class:`ComposeRefusal` when an authoritative input does not
    resolve. Validation findings on the composed output do not raise: they are
    returned in the record with ``outcome: "invalid"`` so the caller can
    surface every finding at once, and the caller fails closed on them.
    """
    from cli.commands.handoff_packet import (
        _blocked_by_ids,
        _deliverable_value,
        _find_dependency_task,
        _find_project_root,
    )
    from cli.commands.report_skeleton import _task_skeleton
    from cli.commands.resolve_config import (
        _CliError,
        _load_toml,
        _resolve_deliverable,
        _resolve_work_roots,
        resolve_project_configuration,
    )
    from cli.commands.task_bundle import _resolve_spec_path
    from cli.commands.validate_task_readiness import _parse_headers, _split_csv
    from cli import numbering_contract

    contract = load_contract()

    task_path = Path(task_path)
    if not task_path.is_absolute():
        raise ComposeRefusal("task-path-invalid", "task path must be absolute")
    if not task_path.is_file():
        raise ComposeRefusal("task-not-found", f"task file not found: {task_path}")
    task_path = task_path.resolve()
    if task_path.parent.name == "done":
        raise ComposeRefusal(
            "task-already-closed", "a done task takes no assignment prompt"
        )
    try:
        content = task_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ComposeRefusal("task-unreadable", str(exc)) from exc

    project_root = _find_project_root(task_path)
    if project_root is None:
        raise ComposeRefusal(
            "project-root-not-found", f"no project config found above {task_path}"
        )

    refusal = numbering_contract.guard_existing_task_trace(project_root, task_path)
    if refusal is not None:
        raise ComposeRefusal(refusal[0], refusal[1])

    try:
        project_cfg = _load_toml(project_root / "cartopian.toml", "project config") or {}
        resolved = resolve_project_configuration(project_root)
    except _CliError as err:
        raise ComposeRefusal("project-config-invalid", err.message) from err

    roles = resolved["roles"]
    if role not in roles:
        raise ComposeRefusal("role-not-declared", f"role {role!r} is not declared")
    role_record = roles[role]

    headers, _presence = _parse_headers(content)
    task_id = "-".join(task_path.stem.split("-")[:3])
    nn_nnn = task_id.removeprefix("TASK-")
    task_title = ""
    for line in content.splitlines():
        if line.startswith("# "):
            task_title = line[2:].strip()
            break
    task_title = task_title or task_path.stem

    # Work roots: declared names must resolve to absolute paths — an
    # unmapped name would put an unusable placeholder in front of a coder.
    raw_roots = headers.get("Work root", "").strip()
    root_names = (
        [] if not raw_roots or raw_roots.lower() in {"n/a", "none"}
        else _split_csv(raw_roots)
    )
    try:
        resolved_roots = _resolve_work_roots(project_cfg, project_root)
    except _CliError as err:
        raise ComposeRefusal("work-root-resolution-failed", err.message) from err
    work_roots: List[Dict[str, str]] = []
    for name in root_names:
        absolute = resolved_roots.get(name)
        if absolute is None:
            raise ComposeRefusal(
                "work-root-unmapped",
                f"declared work root {name!r} has no per-machine path mapping",
            )
        work_roots.append({"name": name, "absolute_path": str(absolute)})

    try:
        deliverable = _resolve_deliverable(
            project_cfg, project_root, _deliverable_value(content)
        )
    except _CliError as err:
        raise ComposeRefusal("deliverable-invalid", err.message) from err

    selectors = _resolve_selector_results(content)
    risk_projection = risk_contract.assignee_projection(selectors["risk"])
    judgment_projection = judgment_guidance.assignee_projection(
        selectors["judgment"]
    )
    practice_projection = practice_packs.assignee_projection(
        selectors["practice"]
    )

    guidance_record = source_guidance.resolve_task_guidance(
        task_path, content=content
    )
    if guidance_record["outcome"] == "invalid":
        first = guidance_record["blockers"][0]
        raise ComposeRefusal(first["code"], first["detail"])
    source_backed = guidance_record["outcome"] == "valid"

    findings: List[Dict[str, str]] = []
    spec_path = _resolve_spec_path(project_root, headers)
    spec_projection: Optional[str] = None
    spec_receipt: Optional[Dict[str, Any]] = None
    if spec_path is not None:
        try:
            spec_content = Path(spec_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ComposeRefusal(
                "spec-unreadable", f"{spec_path}: {exc}"
            ) from exc
        spec_projection, spec_receipt = deidentify.assignment_spec_projection(
            spec_content
        )
        if spec_receipt["open_question_lines"]:
            findings.append(_finding(
                "spec-open-questions-unresolved",
                "the governing specification still carries open questions",
                "close every open question (the contract must be settled) "
                "before assigning the work",
            ))

    standards_path = project_root / "STANDARDS.md"
    standards_section: Optional[str] = None
    standards_receipt: Optional[Dict[str, Any]] = None
    if standards_path.is_file():
        applicability = [
            *selectors["practice_envelope"].get("primary_outcomes", []),
            *selectors["practice_envelope"].get("artifact_kinds", []),
            *selectors["practice_envelope"].get("domain_scopes", []),
        ]
        hint = selectors["practice_envelope"].get("authorized_profile_hint")
        if hint:
            applicability.append(hint)
        try:
            standards_section, standards_receipt = project_standards(
                standards_path.read_text(encoding="utf-8"), applicability
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise ComposeRefusal(
                "standards-unreadable", f"{standards_path}: {exc}"
            ) from exc

    governance_findings, governance_markers = _component_governance_findings(
        contract,
        [
            ("the task's Goal section", _section_body(content, "Goal") or ""),
            ("the task's Notes section", _section_body(content, "Notes") or ""),
            (
                "the task's Evidence gate section",
                _section_body(content, "Evidence gate") or "",
            ),
            (
                "the task's Acceptance section",
                _section_body(content, "Acceptance") or "",
            ),
            (
                "the governing specification's assignment projection",
                spec_projection or "",
            ),
            ("the projected STANDARDS.md sections", standards_section or ""),
        ],
    )
    findings.extend(governance_findings)

    try:
        request_context = request_trace.context_for_task_assignment(
            project_root, task_path
        )
    except request_trace.RequestRefusal as refusal_exc:
        raise ComposeRefusal(refusal_exc.rule, refusal_exc.detail) from refusal_exc

    expected_report_path = report_identity.completion_report_path(
        project_root, nn_nnn
    ).resolve()
    expected_prompt_path = (
        project_root / "prompts" / f"PROMPT-{nn_nnn}.md"
    ).resolve()
    task_review_required = (
        resolved["reviews"]["task_closure"]["mode"] == "required"
    )

    skeleton = _task_skeleton(
        headers,
        guidance_record,
        deliverable,
        task_review_required,
        source_evidence_lines=(
            _reference_source_evidence_lines() if source_backed else None
        ),
        risk_scaled_lines=_risk_scaled_evidence_lines(risk_projection),
    )

    git_versioning = resolved["git_versioning"]
    git_block = resolved["git"] or {}
    pm_owns_branches = git_versioning and bool(
        git_block.get("pm_owns_product_branches", False)
    )

    grants = role_record["effective_grants"]
    existing_input: Optional[Dict[str, Any]] = None
    if (
        deliverable is not None
        and deliverable.get("mode") == "project"
        and deliverable.get("exists")
        and not _governance_readable(grants)
    ):
        text = _read_resource(
            deliverable.get("absolute_path") or "",
            deliverable.get("logical") or "the existing project deliverable",
        )
        existing_input = {
            **assignment_inputs.payload_binding(
                assignment_inputs.CHANNEL_EXISTING,
                deliverable.get("logical") or "",
                text,
            ),
            "content": text,
        }

    upstream_inputs: List[Dict[str, Any]] = []
    seen_upstream: set = set()
    for dependency_id in _blocked_by_ids(content):
        dependency_path = _find_dependency_task(project_root, dependency_id)
        if dependency_path is None:
            continue
        try:
            dependency_content = dependency_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ComposeRefusal(
                "dependency-task-unreadable", f"{dependency_path}: {exc}"
            ) from exc
        dependency_deliverable = _resolve_deliverable(
            project_cfg, project_root, _deliverable_value(dependency_content)
        )
        if (
            not dependency_deliverable
            or dependency_deliverable.get("mode") != "project"
        ):
            continue
        if not dependency_deliverable.get("exists"):
            raise ComposeRefusal(
                "dependency-deliverable-missing",
                f"dependency {dependency_id} declares "
                f"{dependency_deliverable.get('logical')} but it was never "
                "persisted",
            )
        # Two dependencies may declare the same deliverable; the payload
        # channel binds by (channel, logical), so it is rendered exactly once.
        if dependency_deliverable.get("logical") in seen_upstream:
            continue
        seen_upstream.add(dependency_deliverable.get("logical"))
        if _governance_readable(grants):
            continue
        text = _read_resource(
            dependency_deliverable.get("absolute_path") or "",
            dependency_deliverable.get("logical") or "a dependency deliverable",
        )
        upstream_inputs.append(
            {
                **assignment_inputs.payload_binding(
                    assignment_inputs.CHANNEL_DEPENDENCY,
                    dependency_deliverable.get("logical") or "",
                    text,
                ),
                "content": text,
            }
        )

    prompt = _render_prompt(
        contract=contract,
        title=_deidentified_title(task_title),
        role=role,
        role_description=role_record["description"],
        project_root=project_root,
        work_roots=work_roots,
        expected_report_path=expected_report_path,
        content=content,
        headers=headers,
        spec_projection=spec_projection,
        standards_section=standards_section,
        guidance_record=guidance_record,
        risk_projection=risk_projection,
        judgment_projection=judgment_projection,
        practice_projection=practice_projection,
        deliverable=deliverable,
        existing_input=existing_input,
        upstream_inputs=upstream_inputs,
        skeleton=skeleton,
        pm_owns_branches=pm_owns_branches,
        git_versioning=git_versioning,
    )

    input_payload_manifest = [
        {
            key: item[key]
            for key in ("channel", "logical", "content_bytes", "content_sha256")
        }
        for item in (
            ([existing_input] if existing_input is not None else [])
            + upstream_inputs
        )
    ]

    findings.extend(validate_prompt(
        prompt,
        contract,
        governance_markers_reported=governance_markers,
        input_payloads=input_payload_manifest,
    ))
    sections = measure_sections(prompt)
    prompt_identity = _sha256(prompt.encode("utf-8"))

    trace_receipt = {
        "contract_id": contract["contract_id"],
        "contract_version": contract["contract_version"],
        "task": {
            "task_id": task_id,
            "task_title": task_title,
            "task_path": str(task_path),
            "task_status": task_path.parent.name,
        },
        "role": {
            "role": role,
            "description": role_record["description"],
            "effective_grants": grants,
        },
        "risk": selectors["risk"],
        "judgment": {**selectors["judgment"], "body": None},
        "practice_pack": {**selectors["practice"], "body": None},
        "source_guidance": guidance_record,
        "spec": {"spec_path": spec_path, "projection_receipt": spec_receipt},
        "standards": {
            "path": str(standards_path) if standards_path.is_file() else None,
            "receipt": standards_receipt,
        },
        "request_context": request_context.as_record(),
        "report": {
            "expected_report_path": str(expected_report_path),
            "expected_prompt_path": str(expected_prompt_path),
            "skeleton_identity": _sha256(skeleton.encode("utf-8")),
        },
        "deliverable": deliverable,
        "work_roots": work_roots,
        "input_payloads": input_payload_manifest,
        "section_sizes": sections,
        "findings": findings,
        "prompt_content_identity": prompt_identity,
    }
    receipt_identity = _canonical_identity(trace_receipt)
    content_identity = _canonical_identity(
        {"prompt": prompt_identity, "trace_receipt": receipt_identity}
    )

    return {
        "task_id": task_id,
        "task_path": str(task_path),
        "role": role,
        "outcome": "invalid" if any(
            item["severity"] == "fail" for item in findings
        ) else "composed",
        "assignee_prompt": prompt,
        "prompt_content_identity": prompt_identity,
        "trace_receipt": trace_receipt,
        "receipt_content_identity": receipt_identity,
        "content_identity": content_identity,
        "section_sizes": sections,
        "findings": findings,
        "expected_prompt_path": str(expected_prompt_path),
        "expected_report_path": str(expected_report_path),
        "request_sections_appended_by": "write-prompt",
    }


def materialize_input_sections(
    project_root: Path, task_path: Path, body: str
) -> Tuple[str, List[Dict[str, Any]]]:
    """Machine-materialize the typed input sections for an authored prompt.

    The hand-authored path cannot declare trusted payloads, so the mediated
    writer creates them: it resolves the task's existing project deliverable
    and its dependencies' project deliverables (whichever exist), and appends
    one machine-created payload section per channel. Returns the extended
    body and the machine payload manifest. Refuses when the authored body
    already declares a payload or carries a machine-owned payload section —
    that text is hand-authored by definition and must not pass as machine
    input.

    Materialization is role-agnostic: whether the assignee's role could read
    the resource directly is a preflight concern; an embedded exact copy is
    correct input either way.
    """
    from cli.commands.handoff_packet import (
        _blocked_by_ids,
        _deliverable_value,
        _find_dependency_task,
    )
    from cli.commands.resolve_config import _CliError, _load_toml, _resolve_deliverable

    if assignment_inputs.extract_payload_blocks(body):
        raise ComposeRefusal(
            "unbound-input-payload",
            "the authored prompt body declares a typed input payload; "
            "payload sections are machine-created — remove the declaration",
        )
    for name, _text in split_sections(body):
        if name in assignment_inputs.CHANNEL_SECTIONS.values():
            raise ComposeRefusal(
                "unbound-input-payload",
                f"the authored prompt body carries the machine-owned section "
                f"{name!r} — remove it; the writer materializes input "
                "payloads from the machine-resolved assignment inputs",
            )

    try:
        content = Path(task_path).read_text(encoding="utf-8")
        project_cfg = _load_toml(
            Path(project_root) / "cartopian.toml", "project config"
        ) or {}
        deliverable = _resolve_deliverable(
            project_cfg, Path(project_root), _deliverable_value(content)
        )
    except (OSError, UnicodeDecodeError) as exc:
        raise ComposeRefusal("task-unreadable", str(exc)) from exc
    except _CliError as err:
        raise ComposeRefusal("project-config-invalid", err.message) from err

    manifest: List[Dict[str, Any]] = []
    parts: List[str] = []
    if (
        deliverable is not None
        and deliverable.get("mode") == "project"
        and deliverable.get("exists")
    ):
        text = _read_resource(
            deliverable.get("absolute_path") or "",
            deliverable.get("logical") or "the existing project deliverable",
        )
        logical = deliverable.get("logical") or ""
        manifest.append(assignment_inputs.payload_binding(
            assignment_inputs.CHANNEL_EXISTING, logical, text
        ))
        parts += [
            "## Existing deliverable input",
            "",
            "The complete current content of the document this assignment "
            "updates follows. Work from this copy.",
            "",
            assignment_inputs.render_payload_block(
                assignment_inputs.CHANNEL_EXISTING, logical, text
            ),
            "",
        ]

    upstream_parts: List[str] = []
    seen_upstream: set = set()
    for dependency_id in _blocked_by_ids(content):
        dependency_path = _find_dependency_task(
            Path(project_root), dependency_id
        )
        if dependency_path is None:
            continue
        try:
            dependency_content = dependency_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ComposeRefusal(
                "dependency-task-unreadable", f"{dependency_path}: {exc}"
            ) from exc
        dependency_deliverable = _resolve_deliverable(
            project_cfg, Path(project_root), _deliverable_value(dependency_content)
        )
        if (
            not dependency_deliverable
            or dependency_deliverable.get("mode") != "project"
            or not dependency_deliverable.get("exists")
        ):
            continue
        # Shared deliverables render once: the payload channel binds by
        # (channel, logical).
        if dependency_deliverable.get("logical") in seen_upstream:
            continue
        seen_upstream.add(dependency_deliverable.get("logical"))
        text = _read_resource(
            dependency_deliverable.get("absolute_path") or "",
            dependency_deliverable.get("logical") or "a dependency deliverable",
        )
        logical = dependency_deliverable.get("logical") or ""
        manifest.append(assignment_inputs.payload_binding(
            assignment_inputs.CHANNEL_DEPENDENCY, logical, text
        ))
        upstream_parts += [
            assignment_inputs.render_payload_block(
                assignment_inputs.CHANNEL_DEPENDENCY, logical, text
            ),
            "",
        ]
    if upstream_parts:
        parts += [
            "## Upstream contract input",
            "",
            "The upstream contract(s) this assignment builds on follow in "
            "full — read them here rather than inferring the interface.",
            "",
            *upstream_parts,
        ]

    if not parts:
        return body, manifest
    return body.rstrip() + "\n\n" + "\n".join(parts).rstrip() + "\n", manifest


def _render_prompt(
    *,
    contract: Dict[str, Any],
    title: str,
    role: str,
    role_description: str,
    project_root: Path,
    work_roots: List[Dict[str, str]],
    expected_report_path: Path,
    content: str,
    headers: Dict[str, str],
    spec_projection: Optional[str],
    standards_section: Optional[str],
    guidance_record: Dict[str, Any],
    risk_projection: Dict[str, Any],
    judgment_projection: Dict[str, Any],
    practice_projection: Dict[str, Any],
    deliverable: Optional[Dict[str, Any]],
    existing_input: Optional[Dict[str, Any]],
    upstream_inputs: List[Dict[str, Any]],
    skeleton: str,
    pm_owns_branches: bool,
    git_versioning: bool,
) -> str:
    parts: List[str] = [f"# {title}", ""]

    # Role and workspace -----------------------------------------------------
    roots_value = (
        ", ".join(
            f"{item['name']}: {item['absolute_path']}" for item in work_roots
        )
        or "n/a — this assignment touches nothing outside the project root"
    )
    parts += [
        "## Role and workspace",
        "",
        f"You are a {role} — {deidentify.scrub_field(role_description)} "
        "This preface is orientation only: it grants no authority beyond "
        "the role's configured grants.",
        "",
        f"- Project root (your launch working directory; not authority to "
        f"edit project-management files): {project_root}",
        f"- Work roots (product work happens only here): {roots_value}",
        f"- Report path (the only authorized write inside the governing "
        f"project unless a section below says otherwise): "
        f"{expected_report_path}",
        "",
    ]

    # Outcome and done criteria ----------------------------------------------
    goal = _section_body(content, "Goal") or ""
    goal = deidentify.scrub_field(goal)
    normalized_spec = _normalized(spec_projection or "")
    acceptance = _acceptance_rows(content)
    outcome_lines: List[str] = ["## Outcome and done criteria", ""]
    # With a spec, the goal appears here only as a task-specific delta; with
    # no spec, the goal is the implementation contract and is not restated.
    if goal and normalized_spec and _normalized(goal) not in normalized_spec:
        outcome_lines += [goal, ""]
    retained = [
        row
        for row in acceptance
        if not normalized_spec
        or _normalized(row.removeprefix("- [ ]").strip()) not in normalized_spec
    ]
    if retained:
        outcome_lines.append(
            "Done means every item below can be independently marked true"
            + (
                ", together with the acceptance items in the Implementation "
                "contract:"
                if spec_projection
                else ":"
            )
        )
        outcome_lines.append("")
        outcome_lines.extend(retained)
    elif spec_projection:
        outcome_lines.append(
            "Done criteria are the acceptance items in the Implementation "
            "contract below; this task adds no further criteria."
        )
    parts += [*outcome_lines, ""]

    # Implementation contract ------------------------------------------------
    parts += ["## Implementation contract", ""]
    if spec_projection:
        parts += [_embed(spec_projection, strip_h1=True, demote=1), ""]
    elif goal:
        parts += [goal, ""]
    notes = _section_body(content, "Notes")
    if notes and _normalized(notes) not in {
        "", _normalized("Anything a future reader or reviewer would thank you for."),
    }:
        parts += ["Task notes:", "", deidentify.scrub_field(notes), ""]

    # Applicable project standards -------------------------------------------
    if standards_section:
        parts += [
            "## Applicable project standards",
            "",
            _embed(standards_section, demote=1),
            "",
        ]

    # Source guidance ---------------------------------------------------------
    if guidance_record["outcome"] == "valid":
        parts += [
            "## Source guidance",
            "",
            _source_guidance_section(guidance_record, contract),
            "",
        ]

    # Scope and authority boundaries -----------------------------------------
    boundary_lines = [
        "## Scope and authority boundaries",
        "",
        "- Implement only what the Implementation contract and done criteria "
        "require. If any supplied input is wrong, ambiguous, or "
        "insufficient, stop and report it as a blocker in the completion "
        "report instead of adapting the input to what you built.",
        "- Do not create, edit, move, or delete project-management files "
        "(task, specification, prompt, phase, or state records) or perform "
        "lifecycle cleanup; your writes are the work roots above and the "
        "report path.",
    ]
    if pm_owns_branches and work_roots:
        boundary_lines.append(
            "- Product-repository git plumbing (stage, commit, push, branch, "
            "PR, merge) is owned by the project manager; do not perform it."
        )
    if not git_versioning and work_roots:
        boundary_lines.append(
            "- This project runs without git versioning: a work root may "
            "already contain uncommitted output from earlier completed "
            "work. That steady state is expected and is not itself a "
            "defect; evaluate and report only changes attributable to this "
            "assignment."
        )
    parts += [*boundary_lines, ""]

    # Verification -------------------------------------------------------------
    gate = headers.get("Evidence gate", headers.get("Test gate", "")).strip()
    gate_body = deidentify.scrub_field(
        _section_body(content, "Evidence gate") or ""
    )
    verification_lines = ["## Verification", ""]
    if gate == "required":
        verification_lines.append(
            "- Evidence gate: required — " + (
                gate_body or "produce the before-and-after acceptance "
                "evidence named in the done criteria."
            )
        )
    else:
        reason = re.sub(
            r"^\s*n/?a\s*[—–-]*\s*", "", gate_body, flags=re.IGNORECASE
        )
        verification_lines.append(
            "- Evidence gate: n/a" + (f" — {reason}" if reason else "")
        )
    verification_lines.append(
        "- Run every completion-critical check in the foreground and wait "
        "for it to finish before writing the report. A run too slow to "
        "finish inside this session is a blocker to report, not work to "
        "leave running."
    )
    if guidance_record["outcome"] == "valid":
        verification_lines.append(
            "- The report's Source evidence section must name the non-empty "
            "subset of supplied sources you actually applied — copied from "
            "Source guidance above, never extended with outside sources."
        )
    parts += [*verification_lines, ""]

    # Active guidance ----------------------------------------------------------
    parts += ["## Active guidance", "", "### Risk", "",
              render_risk_guidance(risk_projection), ""]
    judgment_text = render_judgment_guidance(judgment_projection)
    if judgment_text:
        parts += ["### Judgment holds", "", judgment_text, ""]
    practice_text = render_practice_guidance(practice_projection)
    if practice_text:
        parts += [
            f"### Practice profile: {practice_projection['pack_id']}",
            "",
            practice_text,
            "",
        ]

    # Existing deliverable input ----------------------------------------------
    if existing_input is not None:
        parts += [
            "## Existing deliverable input",
            "",
            "The complete current content of the document this assignment "
            "updates follows. Its durable location is not readable by your "
            "role; work from this copy.",
            "",
            assignment_inputs.render_payload_block(
                existing_input["channel"],
                existing_input["logical"],
                existing_input["content"],
            ),
            "",
        ]

    # Upstream contract input --------------------------------------------------
    if upstream_inputs:
        parts += ["## Upstream contract input", ""]
        parts += [
            "The upstream contract(s) this assignment builds on follow in "
            "full — read them here rather than inferring the interface.",
            "",
        ]
        for item in upstream_inputs:
            parts += [
                assignment_inputs.render_payload_block(
                    item["channel"], item["logical"], item["content"]
                ),
                "",
            ]

    # Deliverable ---------------------------------------------------------------
    if deliverable is not None:
        parts += ["## Deliverable", ""]
        if deliverable.get("mode") == "project":
            parts += [
                "Return the complete work product inline in the report's "
                "Deliverable content section. It is persisted to its durable "
                "location after acceptance; do not attempt to write it into "
                "the governing project yourself.",
                "",
            ]
        else:
            parts += [
                f"Write the complete work product to: "
                f"{deliverable.get('absolute_path')}",
                "It is the artifact under review — treat it like code, and "
                "keep the completion report a summary that points to it.",
                "",
            ]

    # Completion report ----------------------------------------------------------
    parts += [
        "## Completion report",
        "",
        "Write your completion report to the report path named in Role and "
        "workspace, filling in the skeleton below. Keep every "
        "machine-generated value (paths, names, prefilled rows) exactly as "
        "given and supply only substantive evidence, findings, and status.",
        "",
        "- Writing the report is the last thing you do. If the work cannot "
        "be finished, still write the report with `Status: blocked` and "
        "record what stopped you — a blocked report is a finished handoff; "
        "an absent one is not.",
        "- If the `cartopian` CLI is available, run `cartopian "
        "validate-report <report path>` after writing and apply the named "
        "recovery for any `mechanical` finding. Report — never edit away — "
        "a `substantive` or `missing-input` finding.",
        "- Do not include secrets: API keys, credentials, tokens, or "
        "private connection strings.",
        "",
        _fenced(skeleton),
        "",
    ]

    return "\n".join(parts).rstrip() + "\n"


def measure_sections(prompt: str) -> List[Dict[str, Any]]:
    total = len(prompt.encode("utf-8")) or 1
    measured = []
    for name, text in split_sections(prompt):
        size = len(text.encode("utf-8"))
        measured.append(
            {
                "section": name,
                "bytes": size,
                "share": round(size / total, 4),
            }
        )
    return measured
