"""Contract-quality audit: the review step that precedes implementation review.

This module implements the accepted separated-contract-review contract. It
adds exactly one step to the existing task-closure review: before recording
implementation findings, the reviewer judges whether the operator request, the
task, and the specification form a good contract, and writes that judgment
down first.

What it is, precisely:

* A **quality, accuracy, and completeness audit** against seven checks
  (:data:`CHECKS`). It is not a security audit, and it makes no isolation
  claim of any kind.
* An **ordering rule with a written outcome**. The audit is recorded in the
  existing review file under ``## Contract quality``, placed after
  ``## Request comparison`` and before ``## Implementation evidence``. No new
  file, artifact, handoff, role, panel, or budget is introduced.
* A **separation of concerns**. A contract defect is ``C<n>`` under
  ``## Contract quality``; an implementation defect stays ``F<n>`` under
  ``## Findings``. Neither excuses the other, and the reviewer records both.

What it deliberately is not: a score. The contract forbids computing an
aggregate over the rubric, so nothing here counts, weights, or ranks the seven
checks — it validates that each gap names a check, a severity, and a locus,
and that the outcome is coherent with the gaps recorded beneath it.

Ordering is a reasoning aid, not an isolation guarantee: writing the contract
audit before the implementation findings reduces the chance that
implementation framing colours the contract judgment. It does not prevent it,
and this module claims nothing about what the reviewer could read or when.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

#: The seven checks, in rubric order: ``(kebab code, display name)``.
CHECKS: Tuple[Tuple[str, str], ...] = (
    ("request-fidelity", "Request fidelity"),
    ("completeness", "Completeness"),
    ("factual-and-source-accuracy", "Factual and source accuracy"),
    ("internal-coherence", "Internal coherence"),
    ("upstream-alignment", "Upstream alignment"),
    ("acceptance-clarity", "Acceptance clarity"),
    ("testability", "Testability"),
)

CHECK_CODES: Tuple[str, ...] = tuple(code for code, _ in CHECKS)

#: The check whose gaps are omitted-requirement evidence rather than
#: rejection-reason evidence (§ 12.3 of the effectiveness contract).
UPSTREAM_ALIGNMENT = "upstream-alignment"

#: The review's existing severity vocabulary, consumed verbatim.
SEVERITIES: Tuple[str, ...] = ("blocker", "major", "minor", "nit")

#: The two outcome values. There is no third, and no numeric equivalent.
OUTCOMES: Tuple[str, ...] = ("adequate", "needs changes")

SECTION_HEADING = "## Contract quality"
PRECEDING_HEADING = "## Request comparison"
FOLLOWING_HEADING = "## Implementation evidence"
FINDINGS_HEADING = "## Findings"

_H2_RE = re.compile(r"^##\s+(.*?)\s*$")
_OUTCOME_RE = re.compile(r"^Outcome:\s*(.+?)\s*$")
_GAP_RE = re.compile(
    r"^-\s+C(\d{1,2})\.\s*\[(?P<severity>[a-z]+)\]\s*(?P<rest>.*\S)\s*$"
)
_FINDING_RE = re.compile(
    r"^-\s+F(\d{1,2})\.\s*\[(?P<severity>[a-z]+)\]\s*(?P<rest>.*)$"
)
_PLACEHOLDER_RE = re.compile(r"^\s*<.*>\s*$")

_DISPLAY_TO_CODE: Dict[str, str] = {
    display.casefold(): code for code, display in CHECKS
}
_DISPLAY_TO_CODE.update({code: code for code in CHECK_CODES})


@dataclass(frozen=True)
class Gap:
    """One contract-quality gap: ``C<n>``, a severity, a check, and a locus."""

    ordinal: str
    severity: str
    check: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "ordinal": self.ordinal,
            "severity": self.severity,
            "check": self.check,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Finding:
    """One implementation finding: ``F<n>`` and a severity."""

    ordinal: str
    severity: str
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "ordinal": self.ordinal,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass
class ContractQuality:
    """The parsed ``## Contract quality`` section and its intake verdict."""

    present: bool
    outcome: Optional[str]
    gaps: List[Gap]
    findings: List[Finding]
    violations: List[Dict[str, str]]

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_record(self) -> Dict[str, object]:
        return {
            "present": self.present,
            "outcome": self.outcome,
            "gaps": [gap.as_dict() for gap in self.gaps],
            "findings": [f.as_dict() for f in self.findings],
            "violations": list(self.violations),
            "ok": self.ok,
        }


def _violation(rule: str, detail: str) -> Dict[str, str]:
    return {"rule": rule, "detail": detail}


def _headings(text: str) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for index, line in enumerate(text.splitlines()):
        match = _H2_RE.match(line)
        if match:
            out.append((index, match.group(1).strip()))
    return out


def _section_lines(text: str, heading: str) -> Optional[List[str]]:
    want = heading[3:].strip().casefold()
    collecting = False
    out: List[str] = []
    for line in text.splitlines():
        match = _H2_RE.match(line)
        if match:
            if collecting:
                break
            collecting = match.group(1).strip().casefold() == want
            continue
        if collecting:
            out.append(line)
    return out if collecting else None


def _split_check(rest: str) -> Tuple[Optional[str], str]:
    """Split ``<Check name> — <detail>`` into its kebab code and detail.

    Both the display spelling used in the rubric and the kebab spelling used
    by the effectiveness ledger's ``h`` field are accepted, so a reviewer
    writing either is recorded against the same check.
    """
    for separator in ("—", "--", "–", " - ", ":"):
        head, found, tail = rest.partition(separator)
        if found:
            code = _DISPLAY_TO_CODE.get(head.strip().casefold())
            if code is not None:
                return code, tail.strip()
    code = _DISPLAY_TO_CODE.get(rest.strip().casefold())
    if code is not None:
        return code, ""
    return None, rest.strip()


def parse_findings(text: str) -> List[Finding]:
    """Read the implementation findings under ``## Findings``.

    Template placeholder rows (``- F1. [blocker | major | …]``) are not
    findings and are skipped: an unfilled template must not read as a recorded
    defect on either channel.
    """
    lines = _section_lines(text, FINDINGS_HEADING)
    if lines is None:
        return []
    out: List[Finding] = []
    for line in lines:
        match = _FINDING_RE.match(line.strip())
        if not match:
            continue
        severity = match.group("severity")
        if severity not in SEVERITIES:
            continue
        out.append(
            Finding(
                ordinal=f"F{int(match.group(1))}",
                severity=severity,
                detail=match.group("rest").lstrip("—-– ").strip(),
            )
        )
    return out


def parse(text: str) -> ContractQuality:
    """Parse and validate a review file's contract-quality audit.

    Validation is structural and fail-closed: a missing section, a missing or
    unknown outcome, a gap naming no check or an unknown severity, and an
    outcome incoherent with the gaps beneath it are all recorded as
    violations. Whether a stated gap is *correct* is the reviewer's judgment
    and is not evaluated here.
    """
    violations: List[Dict[str, str]] = []
    lines = _section_lines(text, SECTION_HEADING)
    findings = parse_findings(text)
    if lines is None:
        violations.append(
            _violation(
                "contract-quality-missing",
                "the review records no `## Contract quality` section; the contract "
                "audit is made and written down before implementation findings",
            )
        )
        return ContractQuality(False, None, [], findings, violations)

    outcome: Optional[str] = None
    gaps: List[Gap] = []
    seen_ordinals: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        outcome_match = _OUTCOME_RE.match(line)
        if outcome_match and outcome is None:
            value = outcome_match.group(1).strip()
            if _PLACEHOLDER_RE.match(value) or "|" in value:
                violations.append(
                    _violation(
                        "contract-quality-outcome-unset",
                        "`Outcome:` still carries the template placeholder; record "
                        "`adequate` or `needs changes`",
                    )
                )
                continue
            if value.casefold() not in OUTCOMES:
                violations.append(
                    _violation(
                        "contract-quality-outcome-invalid",
                        f"`Outcome: {value}` is outside the closed set {OUTCOMES}",
                    )
                )
                continue
            outcome = value.casefold()
            continue
        gap_match = _GAP_RE.match(line)
        if not gap_match:
            continue
        severity = gap_match.group("severity")
        rest = gap_match.group("rest")
        if severity not in SEVERITIES:
            violations.append(
                _violation(
                    "contract-quality-severity-invalid",
                    f"gap C{int(gap_match.group(1))} carries severity "
                    f"{severity!r}, outside {SEVERITIES}",
                )
            )
            continue
        check, detail = _split_check(rest)
        ordinal = f"C{int(gap_match.group(1))}"
        if check is None:
            violations.append(
                _violation(
                    "contract-quality-check-unnamed",
                    f"gap {ordinal} names no rubric check; name one of "
                    + ", ".join(CHECK_CODES),
                )
            )
            continue
        if not detail:
            violations.append(
                _violation(
                    "contract-quality-gap-unlocated",
                    f"gap {ordinal} names the check but not the offending clause "
                    "or what would resolve it",
                )
            )
            continue
        if ordinal in seen_ordinals:
            violations.append(
                _violation(
                    "contract-quality-ordinal-duplicated",
                    f"gap ordinal {ordinal} is used more than once",
                )
            )
            continue
        seen_ordinals.append(ordinal)
        gaps.append(Gap(ordinal=ordinal, severity=severity, check=check, detail=detail))

    if outcome is None and not any(
        v["rule"].startswith("contract-quality-outcome") for v in violations
    ):
        violations.append(
            _violation(
                "contract-quality-outcome-missing",
                "the section carries no `Outcome:` line",
            )
        )

    blocking = [gap for gap in gaps if gap.severity != "nit"]
    if outcome == "adequate" and blocking:
        violations.append(
            _violation(
                "contract-quality-outcome-incoherent",
                "`Outcome: adequate` means all seven checks pass, but the section "
                "records "
                + ", ".join(f"{g.ordinal} [{g.severity}]" for g in blocking),
            )
        )
    if outcome == "needs changes" and not gaps:
        violations.append(
            _violation(
                "contract-quality-outcome-incoherent",
                "`Outcome: needs changes` requires at least one named gap",
            )
        )
    return ContractQuality(True, outcome, gaps, findings, violations)


def _occurrences(text: str, heading: str) -> List[int]:
    want = heading[3:].strip().casefold()
    return [index for index, name in _headings(text) if name.casefold() == want]


def placement_violations(text: str) -> List[Dict[str, str]]:
    """Check that the audit is written down before implementation evidence.

    The ordering is the whole mechanism this contract adds, so both ends of it
    are required, not merely respected when present. Each of the two anchors
    and the section itself must appear **exactly once**, and the section must
    sit strictly between them. A review that carries neither anchor has not
    placed the judgment anywhere; a review that carries an anchor twice has
    not placed it unambiguously; and a review that records the contract
    judgment after the implementation evidence has not made the judgment the
    contract asks for.
    """
    out: List[Dict[str, str]] = []
    found: Dict[str, List[int]] = {}
    for heading in (PRECEDING_HEADING, SECTION_HEADING, FOLLOWING_HEADING):
        positions = _occurrences(text, heading)
        found[heading] = positions
        if heading == SECTION_HEADING and not positions:
            # `parse()` already names the missing section; restating it here
            # would report one defect twice.
            continue
        if not positions:
            out.append(
                _violation(
                    "review-anchor-missing",
                    f"the review carries no `{heading}` heading; `{SECTION_HEADING}` "
                    "is placed between `" + PRECEDING_HEADING + "` and `"
                    + FOLLOWING_HEADING + "`",
                )
            )
        elif len(positions) > 1:
            out.append(
                _violation(
                    "review-anchor-duplicated",
                    f"`{heading}` appears {len(positions)} times; the placement "
                    "rule needs exactly one of each heading to be unambiguous",
                )
            )

    preceding, section, following = (
        found[PRECEDING_HEADING],
        found[SECTION_HEADING],
        found[FOLLOWING_HEADING],
    )
    if len(section) != 1:
        return out
    if len(preceding) == 1 and section[0] < preceding[0]:
        out.append(
            _violation(
                "review-order-violation",
                "`## Contract quality` must follow `## Request comparison`",
            )
        )
    if len(following) == 1 and section[0] > following[0]:
        out.append(
            _violation(
                "review-order-violation",
                "`## Contract quality` must precede `## Implementation evidence`; "
                "the contract audit is recorded before implementation framing is "
                "admitted",
            )
        )
    return out


def section_template(outcome: str = "", gaps: Sequence[Gap] = ()) -> str:
    """Render the section in its normative shape: an outcome, then the gaps."""
    lines = [SECTION_HEADING, "", f"Outcome: {outcome or 'adequate | needs changes'}"]
    if gaps:
        lines.append("")
        for gap in gaps:
            display = dict(CHECKS)[gap.check]
            lines.append(f"- {gap.ordinal}. [{gap.severity}] {display} — {gap.detail}")
    return "\n".join(lines) + "\n"


def evaluate(text: str) -> ContractQuality:
    """Parse the section and fold placement into the same violation list."""
    parsed = parse(text)
    parsed.violations.extend(placement_violations(text))
    return parsed
