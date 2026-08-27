"""Deterministic blanket governance-read rule, shared across surfaces.

The rule itself — the marker set naming whole governance documents, the
scopes an occurrence is forbidden in, the one permitted provenance form, and
the recovery — is owned by ``protocol/assignment-prompt-contract.json``
(``blanket_governance_reads``). This module is the one evaluator of that
rule: assignment-prompt composition (component and section aware), the
v0.11.0 standards migration readiness gate, and plan-audit all call it, so a
marker occurrence can never be classified differently by different surfaces.

Semantics: a listed marker names an entire governance document. Directing an
assignee at it is forbidden wherever text functions as instruction content —
project standards, task instruction prose, and assignee-facing prompt
sections. The single permitted form is a source identity or provenance value:
a structured provenance row (``- Identity: ...``) inside a source-guidance
section. Fenced code blocks carry curated data the assignee consumes (report
skeletons, complete deliverable inputs), not prompt structure, so matches
inside fences are never findings.

Stdlib only. Read-only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from cli.markdown_fences import FenceTracker

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "protocol"
    / "assignment-prompt-contract.json"
)

_H2_RE = re.compile(r"^##\s+(.+?)\s*$")


class GovernanceReadRuleError(ValueError):
    """The shipped blanket governance-read rule is missing or malformed."""


def load_rule(contract: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Parse the contract's ``blanket_governance_reads`` rule; fail closed.

    Accepts an already-loaded contract mapping to keep one contract read per
    composition; with no argument it reads the shipped contract itself.
    """
    if contract is None:
        try:
            contract = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GovernanceReadRuleError(
                "the assignment-prompt contract is unreadable"
            ) from exc
    raw = contract.get("blanket_governance_reads")
    if (
        not isinstance(raw, Mapping)
        or not isinstance(raw.get("markers"), list)
        or not isinstance(raw.get("provenance_sections"), list)
        or not isinstance(raw.get("provenance_row_prefixes"), list)
        or not isinstance(raw.get("recovery"), str)
    ):
        raise GovernanceReadRuleError(
            "the blanket governance-read rule is missing or malformed in the "
            "assignment-prompt contract"
        )
    markers = tuple(str(item) for item in raw["markers"] if str(item).strip())
    prefixes = tuple(
        str(item) for item in raw["provenance_row_prefixes"] if str(item).strip()
    )
    if not markers or not prefixes:
        raise GovernanceReadRuleError(
            "the blanket governance-read rule declares no markers or no "
            "provenance row form"
        )
    return {
        "markers": markers,
        "provenance_sections": frozenset(
            str(item).strip().casefold() for item in raw["provenance_sections"]
        ),
        "provenance_row_prefixes": prefixes,
        "recovery": str(raw["recovery"]),
    }


def _scan(
    text: str, rule: Mapping[str, Any], *, section_aware: bool
) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    tracker = FenceTracker()
    section = "(title)"
    for number, line in enumerate(text.splitlines(), start=1):
        if tracker.feed(line):
            continue
        if tracker.in_fence:
            continue
        heading = _H2_RE.match(line)
        if heading:
            # The heading opens its section before the line is judged: a
            # marker inside a heading is never a provenance row.
            section = heading.group(1).strip()
        marker = next((item for item in rule["markers"] if item in line), None)
        if marker is None:
            continue
        stripped = line.strip()
        permitted = (
            section_aware
            and section.casefold() in rule["provenance_sections"]
            and any(
                stripped.startswith(prefix)
                for prefix in rule["provenance_row_prefixes"]
            )
        )
        if not permitted:
            violations.append(
                {
                    "marker": marker,
                    "section": section,
                    "line_number": number,
                    "statement": stripped,
                }
            )
    return violations


def instruction_violations(
    text: str, rule: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Marker occurrences in pure instruction content, outside fences.

    For text with no sanctioned provenance form — STANDARDS.md, task
    instruction prose, spec and standards projections — every unfenced
    occurrence is a violation.
    """
    return _scan(text, rule, section_aware=False)


def markdown_violations(
    text: str, rule: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Section-aware scan of an assignee-facing markdown body.

    A marker is permitted only as a provenance row (a declared row prefix)
    inside a declared provenance section; the exemption is per-line, so
    imperative prose elsewhere in that same section still fails.
    """
    return _scan(text, rule, section_aware=True)
