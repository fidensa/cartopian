"""Deterministic structural, routing, and context-size evaluations.

The routing evaluator intentionally consumes the authoritative compact skill
metadata.  It is a model-free regression probe, not a production router and
not an alternate source of skill applicability.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from evaluations.runner import (
    AmbiguousJsonError,
    Diagnostic,
    EvaluationCase,
    JsonValue,
    ObservedResult,
    load_json_document,
)
from mcp_server.skill_metadata import (
    BRIDGE_TARGETS,
    MetadataValidationError,
    discovery_description,
    load_metadata,
    validate_repository,
)

ROUTING_SURFACE = "compact-skill-routing-metadata-v1"
BASELINE_RELATION = "not-greater-than"

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "use",
        "when",
        "with",
    }
)
_TOKEN_ALIASES = {
    "adopted": "adopt",
    "adopting": "adopt",
    "checked": "check",
    "checking": "check",
    "closed": "close",
    "closing": "close",
    "configured": "configure",
    "configuring": "configure",
    "created": "create",
    "creating": "create",
    "installed": "install",
    "installing": "install",
    "migrated": "migrate",
    "migrating": "migrate",
    "planned": "plan",
    "planning": "plan",
    "registered": "register",
    "registering": "register",
    "requirements": "requirement",
    "resumed": "resume",
    "resuming": "resume",
    "reviewed": "review",
    "reviewing": "review",
    "started": "start",
    "starting": "start",
    "tasks": "task",
    "updates": "update",
    "updating": "update",
    "upgraded": "upgrade",
    "upgrading": "upgrade",
}
_ROUTING_FIELDS = frozenset({"utterance", "expectation", "candidates", "rationale"})
_EXPECTATION_FIELDS = frozenset({"type", "skill", "skills"})
_EXPECTATION_TYPES = frozenset({"selection", "none", "collision"})
_CONTEXT_FIELDS = frozenset(
    {"surface", "baseline_label", "budget_relation", "allowance"}
)
_ALLOWANCE_FIELDS = frozenset({"bytes", "justification"})


@dataclass(frozen=True)
class RoutingExpectation:
    expectation_type: str
    skills: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingFixture:
    utterance: str
    expectation: RoutingExpectation
    candidates: tuple[str, ...] | None
    rationale: str


@dataclass(frozen=True)
class RoutingDecision:
    decision_type: str
    skills: tuple[str, ...]
    scores: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ContextFixture:
    surface: str
    baseline_label: str
    budget_relation: str
    allowance_bytes: int
    allowance_justification: str | None


def _fixture_value(case: EvaluationCase, repository_root: Path) -> JsonValue:
    if case.input.uses_value:
        return case.input.value
    assert case.input.fixture is not None
    path = repository_root.joinpath(*case.input.fixture.split("/"))
    return load_json_document(path.read_text(encoding="utf-8"))


def _fixture_error(exc: Exception) -> Diagnostic:
    if isinstance(exc, UnicodeDecodeError):
        return Diagnostic("malformed_fixture", "Fixture is not valid UTF-8.")
    if isinstance(exc, json.JSONDecodeError):
        return Diagnostic(
            "malformed_fixture",
            f"Fixture contains invalid JSON at line {exc.lineno}, column {exc.colno}.",
        )
    if isinstance(exc, AmbiguousJsonError):
        return Diagnostic(
            "malformed_fixture",
            f"Fixture contains ambiguous JSON: {exc}.",
        )
    return Diagnostic("malformed_fixture", "Fixture could not be read.")


def _unknown_field(
    value: Mapping[str, Any], allowed: frozenset[str], label: str
) -> Diagnostic | None:
    unknown = sorted(set(value) - allowed)
    if not unknown:
        return None
    return Diagnostic(
        "fixture_unknown_field",
        f"{label} field {unknown[0]!r} is not allowed.",
    )


def _canonical_skill_list(
    value: Any,
    *,
    label: str,
    minimum: int = 1,
) -> tuple[tuple[str, ...] | None, Diagnostic | None]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or not all(isinstance(item, str) and item for item in value)
    ):
        return None, Diagnostic(
            "fixture_invalid_type",
            f"{label} must be an array of at least {minimum} non-empty skill identifiers.",
        )
    skills = tuple(value)
    if len(set(skills)) != len(skills):
        return None, Diagnostic(
            "duplicate_skill",
            f"{label} must not contain duplicate skill identifiers.",
        )
    if list(skills) != sorted(skills):
        return None, Diagnostic(
            "unordered_candidates",
            f"{label} must use canonical skill-identifier order.",
        )
    return skills, None


def _parse_routing_fixture(
    raw: JsonValue,
    known_skills: frozenset[str],
) -> tuple[RoutingFixture | None, tuple[Diagnostic, ...]]:
    if not isinstance(raw, dict):
        return None, (
            Diagnostic("malformed_fixture", "Routing fixture must be a JSON object."),
        )
    unknown = _unknown_field(raw, _ROUTING_FIELDS, "Routing fixture")
    if unknown is not None:
        return None, (unknown,)
    missing = sorted(_ROUTING_FIELDS - set(raw))
    if missing:
        return None, (
            Diagnostic(
                "fixture_missing_field",
                f"Routing fixture field {missing[0]!r} is required.",
            ),
        )
    utterance = raw["utterance"]
    rationale = raw["rationale"]
    if not isinstance(utterance, str) or not utterance.strip():
        return None, (
            Diagnostic(
                "empty_utterance",
                "Routing fixture utterance must be a non-empty string.",
            ),
        )
    if utterance != utterance.strip() or "\n" in utterance or "\r" in utterance:
        return None, (
            Diagnostic(
                "invalid_utterance",
                "Routing fixture utterance must be one trimmed line.",
            ),
        )
    if not isinstance(rationale, str) or not rationale.strip():
        return None, (
            Diagnostic(
                "invalid_rationale",
                "Routing fixture rationale must be a non-empty string.",
            ),
        )

    raw_expectation = raw["expectation"]
    if not isinstance(raw_expectation, dict):
        return None, (
            Diagnostic(
                "fixture_invalid_type",
                "Routing expectation must be a JSON object.",
            ),
        )
    unknown = _unknown_field(
        raw_expectation, _EXPECTATION_FIELDS, "Routing expectation"
    )
    if unknown is not None:
        return None, (unknown,)
    expectation_type = raw_expectation.get("type")
    if expectation_type not in _EXPECTATION_TYPES:
        return None, (
            Diagnostic(
                "unsupported_expectation",
                "Routing expectation type must be 'selection', 'none', or 'collision'.",
            ),
        )

    expectation_skills: tuple[str, ...] = ()
    allowed_fields = {"type"}
    if expectation_type == "selection":
        allowed_fields.add("skill")
        skill = raw_expectation.get("skill")
        if not isinstance(skill, str) or not skill:
            return None, (
                Diagnostic(
                    "fixture_invalid_type",
                    "Selection expectation requires a non-empty 'skill' identifier.",
                ),
            )
        expectation_skills = (skill,)
    elif expectation_type == "collision":
        allowed_fields.add("skills")
        expectation_skills, error = _canonical_skill_list(
            raw_expectation.get("skills"),
            label="Collision expectation skills",
            minimum=2,
        )
        if error is not None:
            return None, (error,)
        assert expectation_skills is not None
    unexpected = sorted(set(raw_expectation) - allowed_fields)
    if unexpected:
        return None, (
            Diagnostic(
                "fixture_unknown_field",
                f"Routing expectation field {unexpected[0]!r} is not valid for "
                f"type {expectation_type!r}.",
            ),
        )

    candidates: tuple[str, ...] | None = None
    raw_candidates = raw["candidates"]
    if raw_candidates is not None:
        candidates, error = _canonical_skill_list(
            raw_candidates,
            label="Routing candidates",
        )
        if error is not None:
            return None, (error,)
        assert candidates is not None
    if expectation_type == "collision" and candidates is None:
        return None, (
            Diagnostic(
                "missing_collision_candidates",
                "Collision expectations require an explicit bounded candidate set.",
            ),
        )

    referenced = set(expectation_skills)
    if candidates is not None:
        referenced.update(candidates)
    unknown_skills = sorted(referenced - known_skills)
    if unknown_skills:
        return None, (
            Diagnostic(
                "unknown_skill",
                f"Routing fixture references unknown skill {unknown_skills[0]!r}.",
            ),
        )
    if candidates is not None and not set(expectation_skills).issubset(candidates):
        return None, (
            Diagnostic(
                "expectation_outside_candidates",
                "Expected skills must be included in the bounded candidate set.",
            ),
        )

    return (
        RoutingFixture(
            utterance=utterance,
            expectation=RoutingExpectation(
                expectation_type=expectation_type,
                skills=expectation_skills,
            ),
            candidates=candidates,
            rationale=rationale,
        ),
        (),
    )


def _normalized_tokens(text: str) -> frozenset[str]:
    normalized: set[str] = set()
    for raw in _TOKEN.findall(text.lower()):
        token = _TOKEN_ALIASES.get(raw, raw)
        if token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
            token = token[:-1]
        if token not in _STOP_WORDS:
            normalized.add(token)
    return frozenset(normalized)


def _entry_trigger(
    utterance: str,
    records: tuple[Mapping[str, Any], ...],
    candidate_ids: frozenset[str],
) -> str | None:
    """Resolve only declared client-bridge entries as explicit entry phrases."""

    lowered = utterance.lower()
    matches: list[str] = []
    for record in records:
        identity = record["identity"]
        if identity not in candidate_ids:
            continue
        surfaces = record.get("surfaces", {})
        if not surfaces.get("client_bridges"):
            continue
        words = identity.replace("_", " ")
        hyphenated = identity.replace("_", "-")
        if (
            re.search(rf"(?<![a-z0-9]){re.escape(words)}(?![a-z0-9])", lowered)
            or f"/{hyphenated}" in lowered
            or f"${hyphenated}" in lowered
        ):
            matches.append(identity)
    if len(matches) == 1:
        return matches[0]
    return None


def _route(
    utterance: str,
    records: tuple[Mapping[str, Any], ...],
    candidates: tuple[str, ...] | None,
) -> RoutingDecision:
    by_identity = {record["identity"]: record for record in records}
    candidate_ids = (
        frozenset(candidates) if candidates is not None else frozenset(by_identity)
    )
    entry = _entry_trigger(utterance, records, candidate_ids)
    if entry is not None:
        return RoutingDecision("selection", (entry,), ((entry, 1),))

    metadata_tokens = {
        identity: _normalized_tokens(
            f"{identity.replace('_', ' ')} {discovery_description(record)}"
        )
        for identity, record in by_identity.items()
        if identity in candidate_ids
    }
    document_frequency = Counter(
        token
        for tokens in metadata_tokens.values()
        for token in tokens
    )
    utterance_tokens = _normalized_tokens(utterance)
    scores: dict[str, int] = {}
    overlaps: dict[str, int] = {}
    document_count = len(metadata_tokens)
    for identity, tokens in metadata_tokens.items():
        shared = utterance_tokens & tokens
        overlaps[identity] = len(shared)
        scores[identity] = sum(
            1 + document_count - document_frequency[token] for token in shared
        )

    qualifying = {
        identity: score
        for identity, score in scores.items()
        if overlaps[identity] >= 2 and score > 0
    }
    ordered_scores = tuple(sorted(scores.items()))
    if not qualifying:
        return RoutingDecision("none", (), ordered_scores)
    highest = max(qualifying.values())
    close = tuple(
        sorted(
            identity
            for identity, score in qualifying.items()
            if score * 4 >= highest * 3
        )
    )
    if len(close) == 1:
        return RoutingDecision("selection", close, ordered_scores)
    return RoutingDecision("collision", close, ordered_scores)


def _decision_label(decision: RoutingDecision) -> str:
    if decision.decision_type == "none":
        return "no selection"
    if decision.decision_type == "selection":
        return f"selection {decision.skills[0]!r}"
    return f"collision [{', '.join(decision.skills)}]"


def _score_label(decision: RoutingDecision) -> str:
    relevant = [
        f"{identity}={score}"
        for identity, score in decision.scores
        if score > 0
    ]
    return ", ".join(relevant) if relevant else "none"


class RoutingEvaluator:
    """Evaluate realistic language against compact authoritative metadata."""

    def _records(
        self, repository_root: Path
    ) -> tuple[tuple[Mapping[str, Any], ...] | None, tuple[Diagnostic, ...]]:
        try:
            records = tuple(load_metadata(repository_root))
        except MetadataValidationError as exc:
            return None, tuple(
                Diagnostic("metadata_invalid", diagnostic)
                for diagnostic in exc.diagnostics
            )
        return records, ()

    def validate(
        self, case: EvaluationCase, repository_root: Path
    ) -> tuple[Diagnostic, ...]:
        if case.measurement_boundary is not None:
            return (
                Diagnostic(
                    "unsupported_measurement_boundary",
                    "Routing cases do not use a measurement boundary.",
                ),
            )
        records, errors = self._records(repository_root)
        if errors:
            return errors
        assert records is not None
        try:
            raw = _fixture_value(case, repository_root)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AmbiguousJsonError) as exc:
            return (_fixture_error(exc),)
        _, fixture_errors = _parse_routing_fixture(
            raw,
            frozenset(record["identity"] for record in records),
        )
        return fixture_errors

    def evaluate(
        self, case: EvaluationCase, repository_root: Path
    ) -> ObservedResult:
        records, errors = self._records(repository_root)
        if errors:
            return ObservedResult("fail", errors)
        assert records is not None
        raw = _fixture_value(case, repository_root)
        fixture, fixture_errors = _parse_routing_fixture(
            raw,
            frozenset(record["identity"] for record in records),
        )
        if fixture_errors:
            return ObservedResult("fail", fixture_errors)
        assert fixture is not None
        decision = _route(fixture.utterance, records, fixture.candidates)
        expected = fixture.expectation
        matched = (
            decision.decision_type == expected.expectation_type
            and decision.skills == expected.skills
        )
        if matched:
            return ObservedResult("pass")
        return ObservedResult(
            "fail",
            (
                Diagnostic(
                    "routing_expectation_mismatch",
                    f"Expected {expected.expectation_type} "
                    f"[{', '.join(expected.skills)}]; observed "
                    f"{_decision_label(decision)}. Scores: {_score_label(decision)}.",
                ),
            ),
        )


class RepositoryStructuralEvaluator:
    """Validate metadata, references, command names, and projected parity."""

    _CHECK_FIELDS = frozenset({"check"})
    _CHECK = "skill-metadata-surfaces"

    def _is_repository_check(
        self, case: EvaluationCase, repository_root: Path
    ) -> tuple[bool, tuple[Diagnostic, ...]]:
        try:
            raw = _fixture_value(case, repository_root)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AmbiguousJsonError) as exc:
            return False, (_fixture_error(exc),)
        if not isinstance(raw, dict) or "check" not in raw:
            return False, ()
        unknown = _unknown_field(raw, self._CHECK_FIELDS, "Structural fixture")
        if unknown is not None:
            return True, (unknown,)
        if raw.get("check") != self._CHECK:
            return True, (
                Diagnostic(
                    "unsupported_structural_check",
                    f"Structural check must be {self._CHECK!r}.",
                ),
            )
        return True, ()

    def validate(
        self, case: EvaluationCase, repository_root: Path
    ) -> tuple[Diagnostic, ...]:
        if case.measurement_boundary is not None:
            return (
                Diagnostic(
                    "unsupported_measurement_boundary",
                    "Structural repository checks do not use a measurement boundary.",
                ),
            )
        is_check, errors = self._is_repository_check(case, repository_root)
        if not is_check:
            return (
                Diagnostic(
                    "unsupported_structural_fixture",
                    "Structural repository evaluator requires a named repository check.",
                ),
            )
        return errors

    def evaluate(
        self, case: EvaluationCase, repository_root: Path
    ) -> ObservedResult:
        diagnostics = [
            Diagnostic("metadata_structure", message)
            for message in validate_repository(repository_root)
        ]
        if diagnostics:
            return ObservedResult("fail", tuple(diagnostics))

        records = tuple(load_metadata(repository_root))
        expected = {
            record["identity"]: discovery_description(record)
            for record in records
        }
        entry_records = [
            record
            for record in records
            if record["surfaces"]["client_bridges"]
        ]
        if [record["identity"] for record in entry_records] != ["use_cartopian"]:
            diagnostics.append(
                Diagnostic(
                    "entry_surface_mismatch",
                    "Exactly 'use_cartopian' must own client entry bridges.",
                )
            )
        elif entry_records[0]["runbook"] != "skills/use-cartopian.md":
            diagnostics.append(
                Diagnostic(
                    "entry_command_mismatch",
                    "The client entry must resolve to skills/use-cartopian.md.",
                )
            )

        declared_bridges = {
            bridge_id
            for record in records
            for bridge_id in record["surfaces"]["client_bridges"]
        }
        if declared_bridges != set(BRIDGE_TARGETS):
            diagnostics.append(
                Diagnostic(
                    "bridge_inventory_mismatch",
                    "Declared client bridges do not match registered bridge templates.",
                )
            )
        for bridge_id in sorted(declared_bridges & set(BRIDGE_TARGETS)):
            target = BRIDGE_TARGETS[bridge_id]
            if (
                target.identity != "use_cartopian"
                or "use-cartopian" not in target.path.as_posix()
            ):
                diagnostics.append(
                    Diagnostic(
                        "entry_command_mismatch",
                        f"Bridge {bridge_id!r} does not preserve the use-cartopian command name.",
                    )
                )

        try:
            from mcp_server import server

            if server.ROOT.resolve() == repository_root.resolve():
                prompts = {
                    item["name"]: item["description"]
                    for item in server.list_prompts()
                }
                resources = {
                    item["uri"].removeprefix("cartopian://skills/"): item[
                        "description"
                    ]
                    for item in server.list_resources()
                    if item["uri"].startswith("cartopian://skills/")
                }
                if prompts != expected:
                    diagnostics.append(
                        Diagnostic(
                            "mcp_prompt_parity",
                            "MCP prompt names or descriptions differ from authoritative metadata.",
                        )
                    )
                if resources != expected:
                    diagnostics.append(
                        Diagnostic(
                            "mcp_resource_parity",
                            "MCP skill resource names or descriptions differ from authoritative metadata.",
                        )
                    )
        except Exception:
            diagnostics.append(
                Diagnostic(
                    "mcp_surface_exception",
                    "MCP generated surfaces could not be inspected.",
                )
            )

        return ObservedResult("fail", tuple(diagnostics)) if diagnostics else ObservedResult("pass")


def compact_routing_surface_bytes(repository_root: Path) -> int:
    """Return exact UTF-8 bytes for the labeled tested routing projection."""

    records = load_metadata(repository_root)
    projection = [
        {
            "applicability": record["applicability"],
            "description": record["description"],
            "identity": record["identity"],
        }
        for record in records
    ]
    return len(
        json.dumps(
            projection,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def _parse_context_fixture(
    raw: JsonValue,
) -> tuple[ContextFixture | None, tuple[Diagnostic, ...]]:
    if not isinstance(raw, dict):
        return None, (
            Diagnostic("malformed_fixture", "Context-size fixture must be a JSON object."),
        )
    unknown = _unknown_field(raw, _CONTEXT_FIELDS, "Context-size fixture")
    if unknown is not None:
        return None, (unknown,)
    missing = sorted({"surface", "baseline_label", "budget_relation"} - set(raw))
    if missing:
        return None, (
            Diagnostic(
                "fixture_missing_field",
                f"Context-size fixture field {missing[0]!r} is required.",
            ),
        )
    surface = raw["surface"]
    baseline_label = raw["baseline_label"]
    budget_relation = raw["budget_relation"]
    if surface != ROUTING_SURFACE:
        return None, (
            Diagnostic(
                "unsupported_context_surface",
                f"Context surface must be {ROUTING_SURFACE!r}.",
            ),
        )
    if not isinstance(baseline_label, str) or not baseline_label.strip():
        return None, (
            Diagnostic(
                "missing_baseline_label",
                "Context-size fixture requires a non-empty baseline label.",
            ),
        )
    if budget_relation != BASELINE_RELATION:
        return None, (
            Diagnostic(
                "unsupported_budget_relation",
                f"Budget relation must be {BASELINE_RELATION!r}.",
            ),
        )
    allowance_bytes = 0
    allowance_justification: str | None = None
    allowance = raw.get("allowance")
    if allowance is not None:
        if not isinstance(allowance, dict):
            return None, (
                Diagnostic(
                    "fixture_invalid_type",
                    "Context-size allowance must be an object.",
                ),
            )
        unknown = _unknown_field(allowance, _ALLOWANCE_FIELDS, "Allowance")
        if unknown is not None:
            return None, (unknown,)
        if set(allowance) != _ALLOWANCE_FIELDS:
            return None, (
                Diagnostic(
                    "incomplete_allowance",
                    "Allowance requires both 'bytes' and 'justification'.",
                ),
            )
        allowance_bytes = allowance["bytes"]
        allowance_justification = allowance["justification"]
        if type(allowance_bytes) is not int or allowance_bytes <= 0:
            return None, (
                Diagnostic(
                    "invalid_allowance",
                    "Allowance bytes must be a positive integer.",
                ),
            )
        if (
            not isinstance(allowance_justification, str)
            or not allowance_justification.strip()
        ):
            return None, (
                Diagnostic(
                    "invalid_allowance",
                    "Allowance justification must be a non-empty string.",
                ),
            )
    return (
        ContextFixture(
            surface=surface,
            baseline_label=baseline_label,
            budget_relation=budget_relation,
            allowance_bytes=allowance_bytes,
            allowance_justification=allowance_justification,
        ),
        (),
    )


class ContextSizeEvaluator:
    """Compare the tested compact routing projection with a labeled baseline."""

    def validate(
        self, case: EvaluationCase, repository_root: Path
    ) -> tuple[Diagnostic, ...]:
        boundary = case.measurement_boundary
        if boundary is None or boundary.max_input_bytes is None:
            return (
                Diagnostic(
                    "missing_baseline_bytes",
                    "Context-size case requires measurement_boundary.max_input_bytes.",
                ),
            )
        if boundary.max_output_bytes is not None:
            return (
                Diagnostic(
                    "unsupported_output_boundary",
                    "Context-size routing checks do not measure output bytes.",
                ),
            )
        try:
            raw = _fixture_value(case, repository_root)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AmbiguousJsonError) as exc:
            return (_fixture_error(exc),)
        _, errors = _parse_context_fixture(raw)
        if errors:
            return errors
        try:
            load_metadata(repository_root)
        except MetadataValidationError as exc:
            return tuple(
                Diagnostic("metadata_invalid", diagnostic)
                for diagnostic in exc.diagnostics
            )
        return ()

    def evaluate(
        self, case: EvaluationCase, repository_root: Path
    ) -> ObservedResult:
        raw = _fixture_value(case, repository_root)
        fixture, errors = _parse_context_fixture(raw)
        if errors:
            return ObservedResult("fail", errors)
        assert fixture is not None
        assert case.measurement_boundary is not None
        assert case.measurement_boundary.max_input_bytes is not None
        baseline = case.measurement_boundary.max_input_bytes
        allowed = baseline + fixture.allowance_bytes
        measured = compact_routing_surface_bytes(repository_root)
        message = (
            f"Surface {fixture.surface!r} is {measured} exact UTF-8 bytes; "
            f"baseline {fixture.baseline_label!r} is {baseline} bytes"
        )
        if fixture.allowance_bytes:
            message += f" with a justified allowance of {fixture.allowance_bytes} bytes"
        message += "."
        if measured > allowed:
            return ObservedResult(
                "fail",
                (Diagnostic("context_size_increase", message),),
            )
        return ObservedResult(
            "pass",
            (Diagnostic("context_size_measurement", message),),
        )
