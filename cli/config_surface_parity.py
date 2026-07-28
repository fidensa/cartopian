"""Deterministic structural checks for configuration/version consumers.

The JSON registry describes consumers and projections only.  Accepted values,
scope rules, defaults, resolution, migration behavior, capabilities, and
identities remain owned by their executable authorities.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from cli.capabilities import is_known_grant_name
from cli.config_schema import CONFIG_SCHEMA

REGISTRY_NAME = "config-surfaces.json"

_REQUIRED_ENTRY_KEYS = frozenset(
    {
        "id",
        "paths",
        "owner_contract",
        "mode",
        "facts_consumed",
        "output_projection",
        "test_anchor",
        "legacy_policy",
    }
)
_VALID_MODES = frozenset({"authority", "compatibility", "generated", "parity-validated"})
_VALID_LEGACY_POLICIES = frozenset(
    {"forbidden", "compatibility-labeled", "compatibility-implementation"}
)
_WRAPPER_RAW_AUTHORITY_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("raw-review-policy", re.compile(r"\[reviews]")),
    ("raw-automation-policy", re.compile(r"\[automation]")),
    ("raw-role-schema", re.compile(r"\[roles(?:\.|])|roles\.<role>")),
    ("schema-identity", re.compile(r"\bproject_schema_version\b|\bCONFIG_SCHEMA\b")),
    ("launch-policy", re.compile(r"\bauto_launch\b")),
)
_MACHINE_LOCAL_PATH_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("macos-user-path", re.compile(r"/Users/(?P<user>[^/\s'\"`]+)")),
    ("linux-user-path", re.compile(r"/home/(?P<user>[^/\s'\"`]+)")),
    (
        "windows-user-path",
        re.compile(r"(?i)\b[A-Z]:\\Users\\(?P<user>[^\\\s'\"`]+)"),
    ),
)
_SAFE_EXAMPLE_USERS = frozenset({"...", "me", "user", "username"})
_HOSTED_CI_PATH_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "linux-user-path": re.compile(
        r"/home/runner/work/[^/\s'\"`]+(?:/[^/\s'\"`]+)*"
    ),
    "windows-user-path": re.compile(
        r"(?i)\b[A-Z]:\\Users\\runneradmin\\work\\"
        r"[^\\\s'\"`]+(?:\\[^\\\s'\"`]+)*"
    ),
}
_OPERATOR_IDENTIFIER_RE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
_SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("openai-secret", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("github-secret", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
    (
        "assigned-secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|password|secret|access[_-]?token)"
            r"\s*[:=]\s*['\"]?(?!<|redacted\b|example\b|placeholder\b)"
            r"[A-Za-z0-9/+_.-]{12,}"
        ),
    ),
)
_MARKDOWN_FIELD_ROW_RE = re.compile(
    r"^\|\s*`(?P<field>[^`]+)`\s*\|\s*(?P<domain>.*?)\s*\|",
    re.MULTILINE,
)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_ACCEPTED_VALUE_CLAIM_RE = re.compile(
    r"\b(?:accepts?|accepted values?|allowed values?|values are|modes are|one of)\b",
    re.IGNORECASE,
)
_BARE_CLOSED_VALUE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b")
_ASSIGNED_CLOSED_VALUE_RE = re.compile(
    r"(?P<field>[A-Za-z][A-Za-z0-9_.<>*]*)\s*=\s*"
    r"(?P<value>\"[^\"\n]*\"|\[[^\]\n]*\])"
)
_SUPPORTED_VALUES_HEADER_RE = re.compile(
    r"Supported\s+`(?P<field>[^`]+)`\s+values\s+are:\s*$",
    re.IGNORECASE,
)
_PROSE_VALUE_CLAIM_RE = re.compile(
    r"\b(?:accepts?\s+only|supported\s+values?\s+are|allowed\s+values?\s+are"
    r"|values?\s+are|modes?\s+are|one\s+of|closed\s+unique\s+list[^.]*?\bfrom)\b",
    re.IGNORECASE,
)
_GRANT_ASSIGNMENT_RE = re.compile(r"\bgrants\s*=\s*\[(?P<values>[^\]]*)\]")
_QUOTED_GRANT_VALUE_RE = re.compile(r"^[\"'](?P<value>[^\"']+)[\"']$")
_BARE_GRANT_VALUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9:_-]*$")
_SENTENCE_END_RE = re.compile(r"[.!?](?P<separator>\s+)")
_PARAGRAPH_BOUNDARY_RE = re.compile(r"\n\s*\n")
_MARKDOWN_ITEM_BOUNDARY_RE = re.compile(
    r"(?m)^[ \t]*(?:[-+*]|\d+[.)])[ \t]+"
)
_MARKDOWN_TABLE_ROW_BOUNDARY_RE = re.compile(r"(?m)^[ \t]*\|[ \t]*")
_PROTECTED_PERIOD_ABBREVIATIONS = frozenset(
    {
        "cf.",
        "dr.",
        "e.g.",
        "etc.",
        "i.e.",
        "jr.",
        "mr.",
        "mrs.",
        "ms.",
        "no.",
        "prof.",
        "sr.",
        "st.",
        "vs.",
    }
)
_PERIOD_TOKEN_RE = re.compile(r"(?i)([a-z][a-z.]*)\.$")
_DOTTED_INITIALISM_RE = re.compile(r"(?:[A-Za-z]\.){2,}$")
_SINGLE_INITIAL_RE = re.compile(r"\b[A-Z]\.$")
_EXPLICIT_COMPATIBILITY_SENTENCE_RE = re.compile(
    r"(?:"
    r"^\s*Legacy compatibility only:"
    r"|^\s*Migration (?:input|source) only:"
    r"|\bmigration-source paths?\b[\s\S]*?\b(?:cover|include|recognize|accept)\b"
    r"|\bmigration tooling\b[\s\S]*?\b(?:recognizes?|accepts?|reads?|converts?)\b"
    r"|\bsuperseded\b[\s\S]*?\bmigration input only\b"
    r")",
    re.IGNORECASE,
)
_CAPABILITY_DIRECT_MODIFIER_RE = re.compile(
    r"\b(?:invalid|retired|superseded|deprecated|unsupported|unknown)"
    r"(?:\s+(?:capability|grant|preset))?"
    r"\s+(?:value|name|example|entry)\s*[`*_:'\"(\[]*$",
    re.IGNORECASE,
)
_CAPABILITY_NEGATIVE_BEFORE_RE = re.compile(
    r"(?:"
    r"\b(?:must|should|do|does)\s+not\s+(?:use|select|author|accept)"
    r"|\bnever\s+(?:use|select|author|accept)s?"
    r"|\b(?:rejects?|forbids?)"
    r")\s*[`*_:'\"(\[]*$",
    re.IGNORECASE,
)
_CAPABILITY_HISTORICAL_ATTRIBUTION_RE = re.compile(
    r"\b(?:DEC-\d+(?:'s)?|prior decision|earlier example|historical decision)"
    r"[\s\S]{0,160}?\b(?:used|contained|cited|showed|selected|authored)"
    r"\s*[`*_:'\"(\[]*$",
    re.IGNORECASE,
)
_CAPABILITY_NEGATIVE_AFTER_RE = re.compile(
    r"^\s*[`*_)\]}'\",.;:-]*\s*(?:"
    r"(?:must|should)\s+not\s+be\s+(?:used|selected|authored|accepted)"
    r"|(?:is|was|remains)\s+(?:an?\s+)?"
    r"(?:invalid|retired|superseded|deprecated|unsupported|unknown|"
    r"rejected|forbidden)(?:\s+(?:capability|grant|preset|value|name|example))?"
    r"|(?:is|was)\s+outside\s+(?:the\s+)?"
    r"(?:closed\s+)?(?:capability|grant|preset)\s+vocabulary"
    r")\b",
    re.IGNORECASE,
)
_DIRECT_PROJECTION_QUALIFIER_RE = re.compile(
    r"(?:\*{0,2})(?:resolved|derived)(?:\*{0,2})\s+$",
    re.IGNORECASE,
)
_DIRECT_NONAUTHORING_QUALIFIER_RE = re.compile(
    r"(?:"
    r"\b(?:contains?|has|emits?|authors?|uses?|permits?|allows?)\s+no"
    r"|\b(?:does|do|must|may|can)\s+not\s+"
    r"(?:author|emit|use|contain|accept)"
    r"|\bnever\s+(?:author|emit|use|contain|accept)s?"
    r"|\b(?:rejects?|forbids?)"
    r")\s+$",
    re.IGNORECASE,
)
_CANONICAL_SUITE_KEYS = frozenset(
    {
        "id",
        "runner",
        "collection_command",
        "execution_command",
        "collection_executes",
        "minimum_collected",
        "required_tests",
    }
)
_FORBIDDEN_REGISTRY_SEMANTIC_KEYS = frozenset(
    {
        "accepted_values",
        "allowed_values",
        "capability_grants",
        "defaults",
        "merge_semantics",
        "precedence",
        "scope_rules",
        "values",
    }
)


@dataclass(frozen=True, order=True)
class SurfaceDiagnostic:
    code: str
    surface: str
    detail: str

    def as_record(self) -> Dict[str, str]:
        return {"code": self.code, "surface": self.surface, "detail": self.detail}


def load_registry(path: Path) -> Dict[str, Any]:
    """Load the machine-readable registry without deriving product semantics."""
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("surface registry root must be an object")
    return raw


def _files_for_patterns(root: Path, patterns: Sequence[str]) -> List[Path]:
    files = set()
    for pattern in patterns:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _registered_files(root: Path, registry: Mapping[str, Any]) -> Dict[str, Tuple[str, ...]]:
    owners: Dict[str, List[str]] = {}
    for entry in registry.get("surfaces", []):
        for path in _files_for_patterns(root, entry.get("paths", [])):
            relative = path.relative_to(root).as_posix()
            owners.setdefault(relative, []).append(str(entry.get("id", "")))
    return {path: tuple(sorted(ids)) for path, ids in sorted(owners.items())}


def _legacy_authority_tokens() -> Tuple[str, ...]:
    """Flatten every explicitly shaped legacy vocabulary class from authority."""
    vocabulary = CONFIG_SCHEMA["legacy_vocabulary"]
    return tuple(
        token
        for tokens in vocabulary.values()
        for token in tokens
    )


def discover_consumers(root: Path, registry: Mapping[str, Any]) -> Tuple[str, ...]:
    """Discover governed consumers from registry-owned structural rules."""
    discovered = set()
    for rule in registry.get("discovery", []):
        needles = tuple(rule.get("contains_any", ()))
        for path in _files_for_patterns(root, rule.get("globs", ())):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if not needles or any(needle in text for needle in needles):
                discovered.add(path.relative_to(root).as_posix())
    return tuple(sorted(discovered))


def _legacy_pattern(alias: str) -> re.Pattern[str]:
    """Compile one migration-source alias from the authoritative schema."""
    if alias == "project.protocol_version":
        return re.compile(r"\bproject\.protocol_version\b")
    if alias == "protocol_version":
        return re.compile(r"(?<!\.)\bprotocol_version\b")
    if alias == "handoffs":
        return re.compile(r"\[handoffs(?:\.|])")
    if alias == "handoffs.*":
        return re.compile(r"\bhandoffs\.<[^>]+>")
    if alias.startswith("handoffs.*."):
        field = alias.rsplit(".", 1)[-1]
        return re.compile(rf"\b{re.escape(field)}\b")
    if alias.startswith("--"):
        return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(alias)}\b")
    return re.compile(rf"\b{re.escape(alias)}\b")


def legacy_vocabulary(text: str) -> Tuple[str, ...]:
    """Return deterministic names for migration-source vocabulary in text."""
    aliases = _legacy_authority_tokens()
    return tuple(alias for alias in aliases if _legacy_pattern(alias).search(text))


def _retired_nested_role_fields() -> Tuple[str, ...]:
    """Return retired nested role leaves that now have direct authored fields."""
    preferred = set(CONFIG_SCHEMA["fields"])
    leaves = []
    prefix = "roles.*.launch."
    for path in CONFIG_SCHEMA["legacy_vocabulary"]["authored_config_paths"]:
        if not path.startswith(prefix):
            continue
        leaf = path[len(prefix) :]
        preferred_leaf = "agent" if leaf == "target" else leaf
        if f"roles.*.{preferred_leaf}" in preferred:
            leaves.append(leaf)
    return tuple(sorted(set(leaves)))


def _period_ends_sentence(text: str, period_index: int) -> bool:
    """Return false only for explicit abbreviation shapes ending at a period."""
    prefix = text[: period_index + 1]
    token = _PERIOD_TOKEN_RE.search(prefix)
    if (
        token is not None
        and token.group(1).lower() + "." in _PROTECTED_PERIOD_ABBREVIATIONS
    ):
        return False
    if _DOTTED_INITIALISM_RE.search(prefix):
        return False
    if _SINGLE_INITIAL_RE.search(prefix):
        return False
    return True


def _sentence_boundaries(text: str) -> Tuple[Tuple[int, int], ...]:
    """Return punctuation-end and following-content offsets for sentences."""
    boundaries = []
    for match in _SENTENCE_END_RE.finditer(text):
        if match.group(0).startswith(".") and not _period_ends_sentence(
            text,
            match.start(),
        ):
            continue
        boundaries.append((match.start() + 1, match.end()))
    return tuple(boundaries)


def _prose_unit(text: str, start: int, end: int) -> Tuple[int, int]:
    """Return the exact sentence, paragraph, list item, or table row around a span."""
    sentence_boundaries = _sentence_boundaries(text)
    paragraph_boundaries = tuple(_PARAGRAPH_BOUNDARY_RE.finditer(text))
    item_boundaries = tuple(_MARKDOWN_ITEM_BOUNDARY_RE.finditer(text))
    table_boundaries = tuple(_MARKDOWN_TABLE_ROW_BOUNDARY_RE.finditer(text))
    unit_start = max(
        (
            *(
                content_start
                for _, content_start in sentence_boundaries
                if content_start <= start
            ),
            *(
                boundary.end()
                for boundary in paragraph_boundaries
                if boundary.end() <= start
            ),
            *(
                boundary.end()
                for boundary in item_boundaries
                if boundary.end() <= start
            ),
            *(
                boundary.end()
                for boundary in table_boundaries
                if boundary.end() <= start
            ),
        ),
        default=0,
    )
    unit_end = min(
        (
            *(
                punctuation_end
                for punctuation_end, _ in sentence_boundaries
                if punctuation_end >= end
            ),
            *(
                boundary.start()
                for boundary in paragraph_boundaries
                if boundary.start() >= end
            ),
            *(
                boundary.start()
                for boundary in item_boundaries
                if boundary.start() >= end
            ),
            *(
                boundary.start()
                for boundary in table_boundaries
                if boundary.start() >= end
            ),
        ),
        default=len(text),
    )
    return unit_start, unit_end


def _grant_values(raw_values: str) -> Tuple[str, ...]:
    """Parse quoted grants and retain bare invalid tokens for negative bite."""
    without_comments = "\n".join(
        line.split("#", 1)[0] for line in raw_values.splitlines()
    )
    values = []
    for raw_value in without_comments.split(","):
        token = raw_value.strip()
        if not token:
            continue
        quoted = _QUOTED_GRANT_VALUE_RE.fullmatch(token)
        if quoted is not None:
            values.append(quoted.group("value"))
        elif _BARE_GRANT_VALUE_RE.fullmatch(token):
            values.append(token)
    return tuple(values)


def _capability_citation_is_explanatory(
    text: str,
    *,
    unit_start: int,
    unit_end: int,
    assignment_start: int,
    assignment_end: int,
) -> bool:
    """Require negative or historical language to bind to this assignment."""
    preceding = text[unit_start:assignment_start]
    following = text[assignment_end:unit_end]
    return any(
        pattern.search(preceding) is not None
        for pattern in (
            _CAPABILITY_DIRECT_MODIFIER_RE,
            _CAPABILITY_NEGATIVE_BEFORE_RE,
            _CAPABILITY_HISTORICAL_ATTRIBUTION_RE,
        )
    ) or _CAPABILITY_NEGATIVE_AFTER_RE.search(following) is not None


def authored_field_prose_parity(
    text: str,
    *,
    surface: str,
) -> Tuple[SurfaceDiagnostic, ...]:
    """Reject retired authored role paths used as active configuration guidance.

    The checked spellings are derived from the authoritative preferred field
    catalog and its separately declared legacy vocabulary. Compatibility prose
    is allowed only when the sentence containing the spelling declares an
    explicit migration-only boundary. Derived-record prose is allowed only
    when ``resolved`` or ``derived`` directly qualifies the retired-looking
    projection spelling.
    """
    leaves = _retired_nested_role_fields()
    if not leaves:
        return ()
    leaf_group = "|".join(re.escape(leaf) for leaf in leaves)
    role = r"(?:\*|<[^>\n]+>|[A-Za-z0-9_-]+)"
    retired = re.compile(
        rf"`?(?:roles\.{role}\.)?launch\.(?:{leaf_group})`?"
        rf"|`?\[roles\.{role}\.launch\]`?"
        rf"|`?roles\.{role}\.launch`?(?!\.)"
    )
    diagnostics: List[SurfaceDiagnostic] = []
    for match in retired.finditer(text):
        sentence_start, sentence_end = _prose_unit(
            text,
            match.start(),
            match.end(),
        )
        sentence = text[sentence_start:sentence_end]
        if _EXPLICIT_COMPATIBILITY_SENTENCE_RE.search(sentence):
            continue
        preceding = text[sentence_start:match.start()]
        if _DIRECT_PROJECTION_QUALIFIER_RE.search(preceding):
            continue
        if _DIRECT_NONAUTHORING_QUALIFIER_RE.search(preceding):
            continue
        line_number = text.count("\n", 0, match.start()) + 1
        diagnostics.append(
            SurfaceDiagnostic(
                "stale-authored-field",
                surface,
                f"line {line_number}: {match.group(0).strip('`')}",
            )
        )
    return tuple(sorted(set(diagnostics)))


def capability_example_parity(
    text: str,
    *,
    surface: str,
) -> Tuple[SurfaceDiagnostic, ...]:
    """Validate authored ``grants = [...]`` examples against live vocabulary.

    Negative and explanatory prose may cite a retired value. The exemption is
    bounded to the exact sentence, paragraph, or Markdown item containing the
    citation; current authored examples remain subject to the live vocabulary.
    """
    diagnostics: List[SurfaceDiagnostic] = []
    for match in _GRANT_ASSIGNMENT_RE.finditer(text):
        unit_start, unit_end = _prose_unit(
            text,
            match.start(),
            match.end(),
        )
        explanatory = _capability_citation_is_explanatory(
            text,
            unit_start=unit_start,
            unit_end=unit_end,
            assignment_start=match.start(),
            assignment_end=match.end(),
        )
        values = _grant_values(match.group("values"))
        for value in values:
            if is_known_grant_name(value) or explanatory:
                continue
            line_number = text.count("\n", 0, match.start()) + 1
            diagnostics.append(
                SurfaceDiagnostic(
                    "unknown-capability-example",
                    surface,
                    f"line {line_number}: {value}",
                )
            )
    return tuple(sorted(set(diagnostics)))


def wrapper_authority_vocabulary(text: str) -> Tuple[str, ...]:
    """Return raw configuration/policy terms a neutral wrapper must not parse."""
    return tuple(
        name for name, pattern in _WRAPPER_RAW_AUTHORITY_PATTERNS if pattern.search(text)
    )


def _unlabeled_legacy_vocabulary(text: str) -> Tuple[str, ...]:
    markers = re.compile(
        r"\b(?:compatibility|legacy|historical|migration-source|deprecated|removed"
        r"|fixture|probe|test-only)\b",
        re.IGNORECASE,
    )
    found = set()
    for paragraph in re.split(r"\n\s*\n", text):
        legacy = legacy_vocabulary(paragraph)
        if legacy and not markers.search(paragraph):
            found.update(legacy)
    return tuple(
        alias
        for alias in _legacy_authority_tokens()
        if alias in found
    )


def _rendered_field_name(field: str) -> str:
    """Render wildcard role paths with the human-reference placeholder."""
    return field.replace("roles.*", "roles.<role>")


def schema_field_parity(
    text: str,
    *,
    surface: str,
) -> Tuple[SurfaceDiagnostic, ...]:
    """Compare one canonical Markdown field reference with schema authority.

    The document supplies representation only. Field names and every closed
    accepted-value domain come from ``CONFIG_SCHEMA["fields"]`` at check time.
    """
    rows: Dict[str, str] = {}
    for match in _MARKDOWN_FIELD_ROW_RE.finditer(text):
        rows[match.group("field")] = match.group("domain")

    diagnostics: List[SurfaceDiagnostic] = []
    for field, contract in CONFIG_SCHEMA["fields"].items():
        rendered = _rendered_field_name(field)
        if rendered not in rows:
            diagnostics.append(
                SurfaceDiagnostic(
                    "schema-field-missing",
                    surface,
                    f"expected field representation: {rendered}",
                )
            )
            continue
        if "values" not in contract:
            continue
        expected = tuple(str(value) for value in contract["values"])
        observed = tuple(_INLINE_CODE_RE.findall(rows[rendered]))
        if set(observed) != set(expected) or len(observed) != len(expected):
            diagnostics.append(
                SurfaceDiagnostic(
                    "schema-value-parity",
                    surface,
                    f"{rendered}: expected {expected!r}; observed {observed!r}",
                )
            )

    rendered_fields = {
        _rendered_field_name(field): tuple(str(value) for value in contract["values"])
        for field, contract in CONFIG_SCHEMA["fields"].items()
        if "values" in contract
    }
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not _ACCEPTED_VALUE_CLAIM_RE.search(line):
            continue
        for rendered, expected in rendered_fields.items():
            if f"`{rendered}`" not in line:
                continue
            candidates = {
                token
                for token in _INLINE_CODE_RE.findall(line)
                if token != rendered
            }
            claim = _ACCEPTED_VALUE_CLAIM_RE.search(line)
            if claim is not None:
                candidates.update(
                    _BARE_CLOSED_VALUE_RE.findall(line[claim.end():])
                )
            invented = sorted(candidates - set(expected))
            if invented:
                diagnostics.append(
                    SurfaceDiagnostic(
                        "schema-value-invented",
                        surface,
                        f"line {line_number}, {rendered}: {', '.join(invented)}",
                    )
                )
    return tuple(sorted(set(diagnostics)))


def _closed_value_domains() -> Dict[str, Tuple[str, Tuple[str, ...]]]:
    """Return unambiguous prose aliases for schema-owned closed domains."""
    fields = CONFIG_SCHEMA["fields"]
    leaf_counts: Dict[str, int] = {}
    for field, contract in fields.items():
        if "values" in contract:
            leaf = field.rsplit(".", 1)[-1]
            leaf_counts[leaf] = leaf_counts.get(leaf, 0) + 1

    domains: Dict[str, Tuple[str, Tuple[str, ...]]] = {}
    for field, contract in fields.items():
        if "values" not in contract:
            continue
        expected = tuple(str(value) for value in contract["values"])
        aliases = {field, _rendered_field_name(field)}
        leaf = field.rsplit(".", 1)[-1]
        if leaf_counts[leaf] == 1:
            aliases.add(leaf)
        for alias in aliases:
            domains[alias] = (field, expected)
    return domains


def _closed_domain(
    authored_name: str,
) -> Tuple[str, Tuple[str, ...]] | None:
    normalized = authored_name.strip().strip("`")
    return _closed_value_domains().get(normalized)


def _closed_value_diagnostic(
    *,
    surface: str,
    line_number: int,
    field: str,
    expected: Sequence[str],
    observed: Sequence[str],
) -> SurfaceDiagnostic | None:
    invented = tuple(sorted(set(observed) - set(expected)))
    if not invented:
        return None
    return SurfaceDiagnostic(
        "schema-value-invented",
        surface,
        f"line {line_number}, {_rendered_field_name(field)}: {', '.join(invented)}",
    )


def closed_value_parity(
    text: str,
    *,
    surface: str,
) -> Tuple[SurfaceDiagnostic, ...]:
    """Reject invented values in prose without requiring a complete field table.

    Assignments and explicit accepted-value claims provide representation and
    context. Every accepted value is obtained from ``CONFIG_SCHEMA["fields"]``
    at check time; prose is never required to republish every field or value.
    """
    diagnostics: List[SurfaceDiagnostic] = []
    lines = text.splitlines()

    for line_number, line in enumerate(lines, start=1):
        for match in _ASSIGNED_CLOSED_VALUE_RE.finditer(line):
            domain = _closed_domain(match.group("field"))
            if domain is None:
                continue
            field, expected = domain
            observed = tuple(
                re.findall(r"[\"']([^\"']+)[\"']", match.group("value"))
            )
            diagnostic = _closed_value_diagnostic(
                surface=surface,
                line_number=line_number,
                field=field,
                expected=expected,
                observed=observed,
            )
            if diagnostic is not None:
                diagnostics.append(diagnostic)

        header = _SUPPORTED_VALUES_HEADER_RE.search(line)
        if header is not None:
            domain = _closed_domain(header.group("field"))
            if domain is not None:
                field, expected = domain
                observed: List[str] = []
                cursor = line_number
                while cursor < len(lines) and not lines[cursor].strip():
                    cursor += 1
                while cursor < len(lines) and lines[cursor].lstrip().startswith("-"):
                    tokens = _INLINE_CODE_RE.findall(lines[cursor])
                    if tokens:
                        observed.append(tokens[0])
                    cursor += 1
                diagnostic = _closed_value_diagnostic(
                    surface=surface,
                    line_number=line_number,
                    field=field,
                    expected=expected,
                    observed=observed,
                )
                if diagnostic is not None:
                    diagnostics.append(diagnostic)

        claim = _PROSE_VALUE_CLAIM_RE.search(line)
        if claim is None:
            continue
        preceding = line[:claim.start()]
        located_domains: List[Tuple[int, Tuple[str, Tuple[str, ...]]]] = []
        for alias, value in _closed_value_domains().items():
            matches = list(
                re.finditer(
                    rf"(?<![\w.])`?{re.escape(alias)}`?(?![\w.])",
                    preceding,
                )
            )
            if matches:
                located_domains.append((matches[-1].end(), value))
        line_domains = (
            {max(located_domains, key=lambda item: item[0])[1]}
            if located_domains
            else set()
        )
        for field, expected in sorted(line_domains):
            claim_text = line[claim.end():]
            claim_text = re.split(r"\.\s|,\s+mapping\b", claim_text, maxsplit=1)[0]
            observed = tuple(_INLINE_CODE_RE.findall(claim_text))
            diagnostic = _closed_value_diagnostic(
                surface=surface,
                line_number=line_number,
                field=field,
                expected=expected,
                observed=observed,
            )
            if diagnostic is not None:
                diagnostics.append(diagnostic)

    return tuple(sorted(set(diagnostics)))


def registry_inventory_evidence(
    registry: Mapping[str, Any],
) -> Dict[str, Any]:
    """Compute evidence that the registry inventories, but does not define, semantics."""
    forbidden_paths: List[str] = []

    def walk(value: Any, path: Tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                child = path + (str(key),)
                if key in _FORBIDDEN_REGISTRY_SEMANTIC_KEYS:
                    forbidden_paths.append(".".join(child))
                walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + (str(index),))

    walk(registry)
    authority_reference = (
        registry.get("authority") == "cli/config_schema.py::CONFIG_SCHEMA"
    )
    return {
        "authority_reference": authority_reference,
        "forbidden_semantic_declarations": tuple(sorted(forbidden_paths)),
        "inventory_only": authority_reference and not forbidden_paths,
    }


def canonical_suite_manifest_diagnostics(
    root: Path,
    registry: Mapping[str, Any],
) -> Tuple[SurfaceDiagnostic, ...]:
    """Validate that every canonical suite has one runnable, closed declaration."""
    diagnostics: List[SurfaceDiagnostic] = []
    suites = registry.get("canonical_test_suites")
    if not isinstance(suites, list) or not suites:
        return (
            SurfaceDiagnostic(
                "canonical-suite-registration",
                REGISTRY_NAME,
                "at least one canonical test suite must be declared",
            ),
        )

    seen = set()
    for index, suite in enumerate(suites):
        surface = str(suite.get("id", f"<suite-{index}>")) if isinstance(suite, dict) else f"<suite-{index}>"
        if not isinstance(suite, dict):
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    "suite declaration must be an object",
                )
            )
            continue
        missing = sorted(_CANONICAL_SUITE_KEYS - set(suite))
        if missing:
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    f"missing keys: {', '.join(missing)}",
                )
            )
            continue
        extra = sorted(set(suite) - _CANONICAL_SUITE_KEYS)
        if extra:
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    f"unknown keys: {', '.join(extra)}",
                )
            )
        if surface in seen:
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    "suite id is repeated",
                )
            )
        seen.add(surface)
        if suite["runner"] not in {"unittest", "pytest"}:
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    f"unsupported runner: {suite['runner']!r}",
                )
            )
        if not isinstance(suite["collection_executes"], bool):
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    "collection_executes must be boolean",
                )
            )
        for command_name in ("collection_command", "execution_command"):
            command = suite[command_name]
            if (
                not isinstance(command, list)
                or not command
                or any(not isinstance(token, str) or not token for token in command)
                or command[0] != "{python}"
                or len(command) < 3
                or command[1] != "-m"
                or command[2] != suite["runner"]
            ):
                diagnostics.append(
                    SurfaceDiagnostic(
                        "canonical-suite-registration",
                        surface,
                        f"{command_name} is not a direct {suite['runner']} module invocation",
                    )
                )
        if (
            not isinstance(suite["minimum_collected"], int)
            or isinstance(suite["minimum_collected"], bool)
            or suite["minimum_collected"] < 2
        ):
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    "minimum_collected must be a nontrivial integer of at least 2",
                )
            )
        required_tests = suite["required_tests"]
        if not isinstance(required_tests, list) or not required_tests:
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    "required_tests must be a non-empty list",
                )
            )
            continue
        if (
            suite["runner"] == "unittest"
            and not any(
                token in {"-v", "--verbose"}
                for token in suite["execution_command"]
            )
        ):
            diagnostics.append(
                SurfaceDiagnostic(
                    "canonical-suite-registration",
                    surface,
                    "unittest required_tests need verbose execution output",
                )
            )
        for required in required_tests:
            if (
                not isinstance(required, dict)
                or set(required) != {"path", "count"}
            ):
                diagnostics.append(
                    SurfaceDiagnostic(
                        "canonical-suite-registration",
                        surface,
                        f"invalid required test declaration: {required!r}",
                    )
                )
                continue
            relative = str(required["path"])
            path = root / relative
            count = required.get("count")
            if (
                not relative.startswith("tests/")
                or not relative.endswith(".py")
                or Path(relative).is_absolute()
                or ".." in Path(relative).parts
                or not path.is_file()
                or not isinstance(count, int)
                or isinstance(count, bool)
                or count < 1
            ):
                diagnostics.append(
                    SurfaceDiagnostic(
                        "canonical-suite-registration",
                        surface,
                        f"invalid required test declaration: {required!r}",
                    )
                )
    return tuple(sorted(set(diagnostics)))


def canonical_suite_observation(
    suite: Mapping[str, Any],
    *,
    collection_exit_code: int,
    execution_exit_code: int,
    collected_total: int,
    collected_by_path: Mapping[str, int],
) -> Dict[str, Any]:
    """Fail closed when a declared suite fails or omits required collection."""
    issues: List[str] = []
    if collection_exit_code != 0:
        issues.append(f"collection-exit-{collection_exit_code}")
    if execution_exit_code != 0:
        issues.append(f"execution-exit-{execution_exit_code}")
    minimum = suite.get("minimum_collected")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 2
    ):
        issues.append("invalid-minimum-collected")
    elif collected_total < minimum:
        issues.append(
            f"collected-{collected_total}-below-minimum-{minimum}"
        )
    for required in suite.get("required_tests", ()):
        path = str(required["path"])
        expected = int(required["count"])
        observed = int(collected_by_path.get(path, 0))
        if observed != expected:
            issues.append(f"{path}:collected-{observed}-expected-{expected}")
    return {
        "green": not issues,
        "issues": tuple(issues),
    }


def _test_anchor_exists(root: Path, anchor: str) -> bool:
    path_text, separator, symbol = anchor.partition("::")
    path = root / path_text
    if not separator or not symbol or not path.is_file():
        return False
    leaf = symbol.rsplit("::", 1)[-1]
    text = path.read_text(encoding="utf-8")
    return re.search(rf"^\s*(?:def|class)\s+{re.escape(leaf)}\b", text, re.MULTILINE) is not None


def _approved_test_compatibility_files(
    root: Path,
    registry: Mapping[str, Any],
) -> Tuple[str, ...]:
    approved = set()
    for pattern in registry.get("test_legacy_compatibility_allowlist", []):
        approved.update(
            path.relative_to(root).as_posix()
            for path in _files_for_patterns(root, (pattern,))
        )
    return tuple(sorted(approved))


def guidance_hygiene(
    text: str,
    *,
    target_context: str = "host-neutral",
    allow_labeled_fixture: bool = False,
) -> Tuple[str, ...]:
    """Detect host/operator/secret data in current generated guidance.

    Placeholders such as ``/Users/<name>`` and ``/Users/me`` are safe. A
    concrete host path is allowed only in a paragraph explicitly labeled
    ``Host-specific example (<target>)`` and checked with that target context.
    Test fixtures may opt out only with the exact first-line classification
    ``Fixture classification: output-hygiene``.
    """
    if allow_labeled_fixture:
        first = text.splitlines()[0].strip() if text.splitlines() else ""
        if first == "Fixture classification: output-hygiene":
            return ()

    issues = set()
    for name, pattern in _MACHINE_LOCAL_PATH_PATTERNS:
        for match in pattern.finditer(text):
            hosted_pattern = _HOSTED_CI_PATH_PATTERNS.get(name)
            if (
                hosted_pattern is not None
                and hosted_pattern.match(text, match.start()) is not None
            ):
                continue
            user = match.group("user").strip("<>").lower()
            if user in _SAFE_EXAMPLE_USERS or match.group("user").startswith("<"):
                continue
            separator = text.rfind("\n\n", 0, match.start())
            paragraph_start = 0 if separator < 0 else separator + 2
            paragraph_end = text.find("\n\n", match.end())
            if paragraph_end < 0:
                paragraph_end = len(text)
            paragraph = text[paragraph_start:paragraph_end]
            label = re.search(
                r"Host-specific example \(([^)]+)\):", paragraph, re.IGNORECASE
            )
            if label and label.group(1).strip().lower() == target_context.lower():
                continue
            issues.add(name)

    for match in _OPERATOR_IDENTIFIER_RE.finditer(text):
        if match.group(0).lower().endswith(("@example.com", ".example")):
            continue
        issues.add("operator-email")
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            issues.add(name)
    return tuple(sorted(issues))


def check_surface_registry(
    root: Path, registry_path: Path | None = None
) -> Tuple[SurfaceDiagnostic, ...]:
    """Validate inventory completeness, metadata, vocabulary, and neutrality."""
    path = registry_path or root / REGISTRY_NAME
    registry = load_registry(path)
    diagnostics: List[SurfaceDiagnostic] = []

    if registry.get("registry_version") != 1:
        diagnostics.append(
            SurfaceDiagnostic(
                "registry-version", REGISTRY_NAME, "registry_version must equal 1"
            )
        )
    if registry.get("authority") != "cli/config_schema.py::CONFIG_SCHEMA":
        diagnostics.append(
            SurfaceDiagnostic(
                "competing-authority",
                REGISTRY_NAME,
                "authority must point to cli/config_schema.py::CONFIG_SCHEMA",
            )
        )
    diagnostics.extend(canonical_suite_manifest_diagnostics(root, registry))

    seen_ids = set()
    approved_test_compatibility = set(
        _approved_test_compatibility_files(root, registry)
    )
    for index, entry in enumerate(registry.get("surfaces", [])):
        surface = str(entry.get("id", f"<entry-{index}>"))
        missing = sorted(_REQUIRED_ENTRY_KEYS - set(entry))
        if missing:
            diagnostics.append(
                SurfaceDiagnostic(
                    "registry-shape", surface, f"missing keys: {', '.join(missing)}"
                )
            )
            continue
        if surface in seen_ids:
            diagnostics.append(
                SurfaceDiagnostic("duplicate-surface", surface, "surface id is repeated")
            )
        seen_ids.add(surface)
        if entry["mode"] not in _VALID_MODES:
            diagnostics.append(
                SurfaceDiagnostic("registry-mode", surface, f"unknown mode {entry['mode']!r}")
            )
        if entry["legacy_policy"] not in _VALID_LEGACY_POLICIES:
            diagnostics.append(
                SurfaceDiagnostic(
                    "legacy-policy",
                    surface,
                    f"unknown legacy policy {entry['legacy_policy']!r}",
                )
            )
        matched = _files_for_patterns(root, entry["paths"])
        if not matched:
            diagnostics.append(
                SurfaceDiagnostic(
                    "missing-surface", surface, "no repository path matches the entry"
                )
            )
        if entry["legacy_policy"] in {"forbidden", "compatibility-labeled"}:
            for matched_path in matched:
                relative = matched_path.relative_to(root).as_posix()
                if relative in approved_test_compatibility:
                    continue
                text = matched_path.read_text(encoding="utf-8")
                legacy = (
                    legacy_vocabulary(text)
                    if entry["legacy_policy"] == "forbidden"
                    else _unlabeled_legacy_vocabulary(text)
                )
                if legacy:
                    diagnostics.append(
                        SurfaceDiagnostic(
                            "stale-vocabulary",
                            surface,
                            f"{relative}: {', '.join(legacy)}",
                        )
                    )

    for pattern in registry.get("test_legacy_compatibility_allowlist", []):
        if not pattern.startswith("tests/") or "**" in pattern:
            diagnostics.append(
                SurfaceDiagnostic(
                    "compatibility-allowlist",
                    REGISTRY_NAME,
                    f"test compatibility path must be narrow and explicit: {pattern}",
                )
            )
        elif not _files_for_patterns(root, (pattern,)):
            diagnostics.append(
                SurfaceDiagnostic(
                    "compatibility-allowlist",
                    REGISTRY_NAME,
                    f"test compatibility path does not match a file: {pattern}",
                )
            )

    entries_by_id = {
        str(entry.get("id")): entry
        for entry in registry.get("surfaces", [])
        if isinstance(entry, dict)
    }
    schema_rules = registry.get("schema_field_parity", [])
    if not schema_rules:
        diagnostics.append(
            SurfaceDiagnostic(
                "schema-parity-registration",
                REGISTRY_NAME,
                "at least one canonical CONFIG_SCHEMA.fields parity rule is required",
            )
        )
    for rule in schema_rules:
        owner = str(rule.get("surface", ""))
        entry = entries_by_id.get(owner)
        mechanism = entry.get("parity_mechanism", {}) if entry is not None else {}
        if (
            entry is None
            or entry.get("mode") != "parity-validated"
            or "CONFIG_SCHEMA.fields" not in entry.get("facts_consumed", [])
            or mechanism.get("kind") != "schema-field-table"
            or mechanism.get("check")
            != "cli/config_surface_parity.py::schema_field_parity"
        ):
            diagnostics.append(
                SurfaceDiagnostic(
                    "schema-parity-registration",
                    owner or REGISTRY_NAME,
                    "schema field parity owner must be parity-validated against CONFIG_SCHEMA.fields",
                )
            )
            continue
        matched = _files_for_patterns(root, rule.get("paths", []))
        if not matched:
            diagnostics.append(
                SurfaceDiagnostic(
                    "schema-parity-registration",
                    owner,
                    "schema field parity rule matches no repository path",
                )
            )
        for matched_path in matched:
            relative = matched_path.relative_to(root).as_posix()
            owner_paths = {
                path.relative_to(root).as_posix()
                for path in _files_for_patterns(root, entry.get("paths", []))
            }
            if relative not in owner_paths:
                diagnostics.append(
                    SurfaceDiagnostic(
                        "schema-parity-registration",
                        owner,
                        f"{relative} is not registered to the declared parity owner",
                    )
                )
                continue
            text = matched_path.read_text(encoding="utf-8")
            if rule.get("strip_comment_prefix") is True:
                text = "\n".join(
                    line[2:] if line.startswith("# ") else line
                    for line in text.splitlines()
                )
            diagnostics.extend(schema_field_parity(text, surface=relative))

    closed_rules = registry.get("closed_value_parity", [])
    for rule in closed_rules:
        owner = str(rule.get("surface", ""))
        entry = entries_by_id.get(owner)
        mechanism = entry.get("parity_mechanism", {}) if entry is not None else {}
        if (
            entry is None
            or entry.get("mode") != "parity-validated"
            or "CONFIG_SCHEMA.fields[].values" not in entry.get("facts_consumed", [])
            or mechanism.get("kind") != "closed-value-prose"
            or mechanism.get("check")
            != "cli/config_surface_parity.py::closed_value_parity"
        ):
            diagnostics.append(
                SurfaceDiagnostic(
                    "closed-value-parity-registration",
                    owner or REGISTRY_NAME,
                    "closed-value prose owner must declare its schema-derived covering check",
                )
            )
            continue
        owner_paths = {
            path.relative_to(root).as_posix()
            for path in _files_for_patterns(root, entry.get("paths", []))
        }
        matched = _files_for_patterns(root, rule.get("paths", []))
        if not matched:
            diagnostics.append(
                SurfaceDiagnostic(
                    "closed-value-parity-registration",
                    owner,
                    "closed-value parity rule matches no repository path",
                )
            )
        for matched_path in matched:
            relative = matched_path.relative_to(root).as_posix()
            if relative not in owner_paths:
                diagnostics.append(
                    SurfaceDiagnostic(
                        "closed-value-parity-registration",
                        owner,
                        f"{relative} is not registered to the declared parity owner",
                    )
                )
                continue
            diagnostics.extend(
                closed_value_parity(
                    matched_path.read_text(encoding="utf-8"),
                    surface=relative,
                )
            )

    authored_field_rules = registry.get("authored_field_prose_parity", [])
    if not authored_field_rules:
        diagnostics.append(
            SurfaceDiagnostic(
                "authored-field-parity-registration",
                REGISTRY_NAME,
                "active guidance must be checked against authored field authority",
            )
        )
    for rule in authored_field_rules:
        matched = _files_for_patterns(root, rule.get("paths", []))
        if not matched:
            diagnostics.append(
                SurfaceDiagnostic(
                    "authored-field-parity-registration",
                    REGISTRY_NAME,
                    "authored field prose parity rule matches no repository path",
                )
            )
        for matched_path in matched:
            relative = matched_path.relative_to(root).as_posix()
            diagnostics.extend(
                authored_field_prose_parity(
                    matched_path.read_text(encoding="utf-8"),
                    surface=relative,
                )
            )

    capability_rules = registry.get("capability_example_parity", [])
    if not capability_rules:
        diagnostics.append(
            SurfaceDiagnostic(
                "capability-example-parity-registration",
                REGISTRY_NAME,
                "current guidance examples must use the live capability vocabulary",
            )
        )
    for rule in capability_rules:
        matched = _files_for_patterns(root, rule.get("paths", []))
        if not matched:
            diagnostics.append(
                SurfaceDiagnostic(
                    "capability-example-parity-registration",
                    REGISTRY_NAME,
                    "capability example parity rule matches no repository path",
                )
            )
        for matched_path in matched:
            relative = matched_path.relative_to(root).as_posix()
            diagnostics.extend(
                capability_example_parity(
                    matched_path.read_text(encoding="utf-8"),
                    surface=relative,
                )
            )

    schema_rule_owners = {
        str(rule.get("surface", "")) for rule in schema_rules
    }
    closed_rule_owners = {
        str(rule.get("surface", "")) for rule in closed_rules
    }
    for entry in registry.get("surfaces", []):
        facts = entry.get("facts_consumed", [])
        if (
            "CONFIG_SCHEMA.fields" not in facts
            and "CONFIG_SCHEMA.fields[].values" not in facts
        ):
            continue
        surface = str(entry.get("id", ""))
        mechanism = entry.get("parity_mechanism")
        if not isinstance(mechanism, dict):
            diagnostics.append(
                SurfaceDiagnostic(
                    "parity-mechanism",
                    surface,
                    "schema parity claim has no declared parity_mechanism",
                )
            )
            continue
        kind = mechanism.get("kind")
        if kind == "schema-field-table":
            covered = surface in schema_rule_owners
        elif kind == "closed-value-prose":
            covered = surface in closed_rule_owners
        elif kind == "executable-contract":
            check = str(mechanism.get("check", ""))
            covered = (
                entry.get("test_anchor") == check
                and _test_anchor_exists(root, check)
            )
        else:
            covered = False
        if not covered:
            diagnostics.append(
                SurfaceDiagnostic(
                    "parity-mechanism",
                    surface,
                    f"declared mechanism {kind!r} has no real covering check",
                )
            )

    registered = _registered_files(root, registry)
    for relative in discover_consumers(root, registry):
        if relative not in registered:
            diagnostics.append(
                SurfaceDiagnostic(
                    "unregistered-surface",
                    relative,
                    "discovered configuration/version consumer has no registry owner",
                )
            )

    for relative in sorted(registered):
        if not (
            relative == "wrappers/README.md"
            or relative.startswith("wrappers/bin/")
            or relative.startswith("wrappers/ps1/")
        ):
            continue
        text = (root / relative).read_text(encoding="utf-8")
        raw_terms = wrapper_authority_vocabulary(text)
        if raw_terms:
            diagnostics.append(
                SurfaceDiagnostic(
                    "wrapper-authority-drift",
                    relative,
                    f"neutral wrapper surface references raw authority: {', '.join(raw_terms)}",
                )
            )

    for rule in registry.get("guidance_hygiene", []):
        target_context = str(rule.get("target_context", "host-neutral"))
        for matched_path in _files_for_patterns(root, rule.get("paths", [])):
            relative = matched_path.relative_to(root).as_posix()
            text = matched_path.read_text(encoding="utf-8")
            issues = guidance_hygiene(
                text,
                target_context=target_context,
                allow_labeled_fixture=relative.startswith("tests/fixtures/"),
            )
            if issues:
                diagnostics.append(
                    SurfaceDiagnostic(
                        "output-hygiene",
                        relative,
                        "generated/current guidance contains: " + ", ".join(issues),
                    )
                )

    return tuple(sorted(set(diagnostics)))


def registry_paths(registry: Mapping[str, Any]) -> Tuple[str, ...]:
    """Expose deterministic declared patterns for inspection/reporting."""
    return tuple(
        pattern
        for entry in registry.get("surfaces", [])
        for pattern in entry.get("paths", [])
    )
