"""Authoritative compact discovery metadata for shipped Cartopian skills.

The closed JSON contract at ``skills/skill-metadata.json`` owns skill
identity, compact outcome, routing applicability, runbook reference, surface
policy, bounded host qualification, and lifecycle. MCP listings consume the
records directly. Client bridge templates are deterministic projections that
this module generates or validates; they are never alternate prose sources.

Run from the repository or installed root with Python 3.11+::

    python3 -m mcp_server.skill_metadata validate
    python3 -m mcp_server.skill_metadata generate

Both commands use only the standard library and require no network, provider
credential, or model invocation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


METADATA_PATH = Path("skills/skill-metadata.json")
SCHEMA_VERSION = 1
MAX_DESCRIPTION_CHARS = 96
MAX_APPLICABILITY_CHARS = 140
MAX_DISCOVERY_CHARS = 220

TOP_LEVEL_FIELDS = frozenset({"schema_version", "skills"})
REQUIRED_RECORD_FIELDS = frozenset({
    "identity",
    "description",
    "applicability",
    "runbook",
    "surfaces",
    "lifecycle",
})
OPTIONAL_RECORD_FIELDS = frozenset({"host_qualifications"})
SURFACE_FIELDS = frozenset({"mcp_prompt", "mcp_resource", "client_bridges"})
HOST_QUALIFICATIONS = frozenset({"direct_command"})
SUPPORTED_LIFECYCLES = frozenset({"shipped"})
IDENTITY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class BridgeTarget:
    """A repository-native installed-bridge template projection."""

    identity: str
    path: Path
    syntax: str
    qualification: Optional[str] = None


BRIDGE_TARGETS: Mapping[str, BridgeTarget] = {
    "claude_command": BridgeTarget(
        "use_cartopian",
        Path("templates/clients/claude-code/commands/use-cartopian.md"),
        "yaml",
        "direct_command",
    ),
    "claude_skill": BridgeTarget(
        "use_cartopian",
        Path("templates/clients/claude-code/skills/use-cartopian/SKILL.md"),
        "yaml",
    ),
    "codex_skill": BridgeTarget(
        "use_cartopian",
        Path("templates/clients/codex/skills/use-cartopian/SKILL.md"),
        "yaml",
    ),
    "devin_skill": BridgeTarget(
        "use_cartopian",
        Path("templates/clients/devin/skills/use-cartopian/SKILL.md"),
        "yaml",
    ),
    "gemini_command": BridgeTarget(
        "use_cartopian",
        Path("templates/clients/gemini/use-cartopian.toml"),
        "toml",
        "direct_command",
    ),
    "windsurf_command": BridgeTarget(
        "use_cartopian",
        Path("templates/clients/windsurf/use-cartopian.md"),
        "yaml",
        "direct_command",
    ),
}


class MetadataValidationError(ValueError):
    """Raised when authoritative metadata or a derived projection is invalid."""

    def __init__(self, diagnostics: Sequence[str]) -> None:
        self.diagnostics = tuple(sorted(diagnostics))
        super().__init__("\n".join(self.diagnostics))


def discovery_description(record: Mapping[str, Any]) -> str:
    """Render the common MCP/routing description from one metadata record."""
    return f"{record['description']} {record['applicability']}"


def bridge_description(record: Mapping[str, Any], bridge_id: str) -> str:
    """Render a bridge description with only its declared host qualification."""
    target = BRIDGE_TARGETS[bridge_id]
    if target.qualification is None:
        return discovery_description(record)
    qualifications = record.get("host_qualifications", {})
    return f"{record['description']} {qualifications[target.qualification]}"


def _read_data(root: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    path = root / METADATA_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, [f"{METADATA_PATH.as_posix()}: metadata file is missing or unreadable"]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [
            f"{METADATA_PATH.as_posix()}: invalid JSON at line {exc.lineno}, "
            f"column {exc.colno}"
        ]
    if not isinstance(data, dict):
        return None, [f"{METADATA_PATH.as_posix()}: top level must be an object"]
    return data, []


def _nonempty_string(
    record: Mapping[str, Any],
    field: str,
    location: str,
    diagnostics: List[str],
) -> Optional[str]:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        diagnostics.append(f"{location}.{field}: required non-empty string")
        return None
    if value != value.strip() or "\n" in value or "\r" in value:
        diagnostics.append(f"{location}.{field}: must be one trimmed line")
        return None
    return value


def _identity_for_runbook(path: str) -> str:
    return Path(path).stem.replace("-", "_")


def _shipped_runbooks(root: Path) -> set[str]:
    runbooks = {
        path.relative_to(root).as_posix()
        for path in (root / "skills").glob("*.md")
        if path.name.lower() != "readme.md"
    }
    install = root / "install-cartopian.md"
    if install.is_file():
        runbooks.add("install-cartopian.md")
    return runbooks


def _validate_record(
    root: Path,
    record: Any,
    index: int,
    diagnostics: List[str],
) -> Optional[Dict[str, Any]]:
    location = f"skills[{index}]"
    if not isinstance(record, dict):
        diagnostics.append(f"{location}: record must be an object")
        return None

    for field in sorted(set(record) - REQUIRED_RECORD_FIELDS - OPTIONAL_RECORD_FIELDS):
        diagnostics.append(f"{location}: unknown field '{field}'")
    for field in sorted(REQUIRED_RECORD_FIELDS - set(record)):
        diagnostics.append(f"{location}.{field}: missing required field")

    identity = _nonempty_string(record, "identity", location, diagnostics)
    description = _nonempty_string(record, "description", location, diagnostics)
    applicability = _nonempty_string(record, "applicability", location, diagnostics)
    runbook = _nonempty_string(record, "runbook", location, diagnostics)
    lifecycle = _nonempty_string(record, "lifecycle", location, diagnostics)

    if identity is not None and not IDENTITY_RE.fullmatch(identity):
        diagnostics.append(
            f"{location}.identity: must match {IDENTITY_RE.pattern}"
        )
    if description is not None and len(description) > MAX_DESCRIPTION_CHARS:
        diagnostics.append(
            f"{location}.description: exceeds {MAX_DESCRIPTION_CHARS} characters"
        )
    if applicability is not None and len(applicability) > MAX_APPLICABILITY_CHARS:
        diagnostics.append(
            f"{location}.applicability: exceeds {MAX_APPLICABILITY_CHARS} characters"
        )
    if description is not None and applicability is not None:
        rendered = f"{description} {applicability}"
        if len(rendered) > MAX_DISCOVERY_CHARS:
            diagnostics.append(
                f"{location}: discovery description exceeds "
                f"{MAX_DISCOVERY_CHARS} characters"
            )

    if runbook is not None:
        candidate = Path(runbook)
        if candidate.is_absolute() or ".." in candidate.parts:
            diagnostics.append(f"{location}.runbook: must be a repository-relative path")
        elif not (root / candidate).is_file():
            diagnostics.append(
                f"{location}.runbook: runbook target does not exist: {runbook}"
            )
        if identity is not None and _identity_for_runbook(runbook) != identity:
            diagnostics.append(
                f"{location}.runbook: target identity does not match '{identity}'"
            )

    if lifecycle is not None and lifecycle not in SUPPORTED_LIFECYCLES:
        diagnostics.append(
            f"{location}.lifecycle: unsupported lifecycle '{lifecycle}'"
        )

    surfaces = record.get("surfaces")
    bridge_ids: List[str] = []
    if not isinstance(surfaces, dict):
        diagnostics.append(f"{location}.surfaces: required object")
    else:
        for field in sorted(set(surfaces) - SURFACE_FIELDS):
            diagnostics.append(f"{location}.surfaces: unknown field '{field}'")
        for field in sorted(SURFACE_FIELDS - set(surfaces)):
            diagnostics.append(f"{location}.surfaces.{field}: missing required field")
        for field in ("mcp_prompt", "mcp_resource"):
            if surfaces.get(field) is not True:
                diagnostics.append(f"{location}.surfaces.{field}: must be true")
        raw_bridges = surfaces.get("client_bridges")
        if not isinstance(raw_bridges, list):
            diagnostics.append(
                f"{location}.surfaces.client_bridges: required array"
            )
        else:
            seen_bridges: set[str] = set()
            for bridge_index, bridge_id in enumerate(raw_bridges):
                bridge_location = (
                    f"{location}.surfaces.client_bridges[{bridge_index}]"
                )
                if not isinstance(bridge_id, str) or not bridge_id:
                    diagnostics.append(
                        f"{bridge_location}: required non-empty string"
                    )
                    continue
                if bridge_id in seen_bridges:
                    diagnostics.append(
                        f"{bridge_location}: duplicate client bridge '{bridge_id}'"
                    )
                seen_bridges.add(bridge_id)
                bridge_ids.append(bridge_id)
                target = BRIDGE_TARGETS.get(bridge_id)
                if target is None:
                    diagnostics.append(
                        f"{bridge_location}: unsupported client bridge '{bridge_id}'"
                    )
                elif identity is not None and target.identity != identity:
                    diagnostics.append(
                        f"{bridge_location}: bridge belongs to '{target.identity}'"
                    )
            if raw_bridges != sorted(raw_bridges):
                diagnostics.append(
                    f"{location}.surfaces.client_bridges: must use canonical order"
                )

    qualifications = record.get("host_qualifications", {})
    if not isinstance(qualifications, dict):
        diagnostics.append(f"{location}.host_qualifications: must be an object")
        qualifications = {}
    else:
        for field in sorted(set(qualifications) - HOST_QUALIFICATIONS):
            diagnostics.append(
                f"{location}.host_qualifications: unknown field '{field}'"
            )
        for field, value in sorted(qualifications.items()):
            if field in HOST_QUALIFICATIONS:
                if not isinstance(value, str) or not value.strip():
                    diagnostics.append(
                        f"{location}.host_qualifications.{field}: "
                        "required non-empty string"
                    )
                elif value != value.strip() or "\n" in value or "\r" in value:
                    diagnostics.append(
                        f"{location}.host_qualifications.{field}: "
                        "must be one trimmed line"
                    )
                elif len(value) > MAX_APPLICABILITY_CHARS:
                    diagnostics.append(
                        f"{location}.host_qualifications.{field}: exceeds "
                        f"{MAX_APPLICABILITY_CHARS} characters"
                    )

    required_qualifications = {
        BRIDGE_TARGETS[bridge_id].qualification
        for bridge_id in bridge_ids
        if bridge_id in BRIDGE_TARGETS
        and BRIDGE_TARGETS[bridge_id].qualification is not None
    }
    for qualification in sorted(required_qualifications - set(qualifications)):
        diagnostics.append(
            f"{location}.host_qualifications.{qualification}: "
            "required by selected client bridge"
        )
    for qualification in sorted(set(qualifications) - required_qualifications):
        if qualification in HOST_QUALIFICATIONS:
            diagnostics.append(
                f"{location}.host_qualifications.{qualification}: "
                "not used by a selected client bridge"
            )

    return record


def _expected_projection_line(
    record: Mapping[str, Any],
    bridge_id: str,
) -> str:
    description = bridge_description(record, bridge_id)
    target = BRIDGE_TARGETS[bridge_id]
    if target.syntax == "yaml":
        return f"description: {description}"
    if target.syntax == "toml":
        return f"description = {json.dumps(description, ensure_ascii=False)}"
    raise AssertionError(f"unsupported bridge syntax: {target.syntax}")


def _projection_pattern(target: BridgeTarget) -> re.Pattern[str]:
    if target.syntax == "yaml":
        return re.compile(r"^description:[^\r\n]*(?:\r?\n|$)", re.MULTILINE)
    if target.syntax == "toml":
        return re.compile(r"^description\s*=[^\r\n]*(?:\r?\n|$)", re.MULTILINE)
    raise AssertionError(f"unsupported bridge syntax: {target.syntax}")


def _render_projection(
    text: str,
    record: Mapping[str, Any],
    bridge_id: str,
) -> Tuple[Optional[str], Optional[str]]:
    target = BRIDGE_TARGETS[bridge_id]
    matches = list(_projection_pattern(target).finditer(text))
    if len(matches) != 1:
        return None, (
            f"bridge[{bridge_id}].description: expected exactly one "
            f"description field in {target.path.as_posix()}"
        )
    match = matches[0]
    newline = "\r\n" if match.group(0).endswith("\r\n") else "\n"
    if not match.group(0).endswith(("\n", "\r")):
        newline = ""
    replacement = _expected_projection_line(record, bridge_id) + newline
    return text[:match.start()] + replacement + text[match.end():], None


def _validated_records(
    root: Path,
    data: Dict[str, Any],
    diagnostics: List[str],
) -> List[Dict[str, Any]]:
    for field in sorted(set(data) - TOP_LEVEL_FIELDS):
        diagnostics.append(f"metadata: unknown field '{field}'")
    if data.get("schema_version") != SCHEMA_VERSION:
        diagnostics.append(
            f"metadata.schema_version: expected {SCHEMA_VERSION}"
        )
    raw_records = data.get("skills")
    if not isinstance(raw_records, list):
        diagnostics.append("metadata.skills: required array")
        return []

    records: List[Dict[str, Any]] = []
    first_identity_index: Dict[str, int] = {}
    for index, raw_record in enumerate(raw_records):
        record = _validate_record(root, raw_record, index, diagnostics)
        if record is None:
            continue
        records.append(record)
        identity = record.get("identity")
        if isinstance(identity, str) and identity:
            if identity in first_identity_index:
                diagnostics.append(
                    f"skills[{index}].identity: duplicate identity '{identity}' "
                    f"(first at skills[{first_identity_index[identity]}])"
                )
            else:
                first_identity_index[identity] = index

    identities = [
        record.get("identity")
        for record in records
        if isinstance(record.get("identity"), str)
    ]
    if identities != sorted(identities):
        diagnostics.append("metadata.skills: identities must use canonical order")

    declared_runbooks = {
        record["runbook"]
        for record in records
        if isinstance(record.get("runbook"), str)
    }
    shipped_runbooks = _shipped_runbooks(root)
    for runbook in sorted(shipped_runbooks - declared_runbooks):
        diagnostics.append(f"metadata.skills: missing shipped runbook '{runbook}'")
    for runbook in sorted(declared_runbooks - shipped_runbooks):
        if (root / runbook).is_file():
            diagnostics.append(f"metadata.skills: unshipped runbook '{runbook}'")

    declared_bridges: Dict[str, str] = {}
    for record in records:
        identity = record.get("identity")
        surfaces = record.get("surfaces")
        if not isinstance(identity, str) or not isinstance(surfaces, dict):
            continue
        bridge_ids = surfaces.get("client_bridges", [])
        if not isinstance(bridge_ids, list):
            continue
        for bridge_id in bridge_ids:
            if not isinstance(bridge_id, str) or bridge_id not in BRIDGE_TARGETS:
                continue
            if bridge_id in declared_bridges:
                diagnostics.append(
                    f"bridge[{bridge_id}]: selected by both "
                    f"'{declared_bridges[bridge_id]}' and '{identity}'"
                )
            else:
                declared_bridges[bridge_id] = identity
    for bridge_id in sorted(set(BRIDGE_TARGETS) - set(declared_bridges)):
        diagnostics.append(
            f"bridge[{bridge_id}]: missing from authoritative surface policy"
        )
    return sorted(
        records,
        key=lambda record: str(record.get("identity", "")),
    )


def validate_repository(root: Path) -> List[str]:
    """Return stable repository-relative diagnostics; an empty list is valid."""
    root = Path(root)
    data, diagnostics = _read_data(root)
    if data is None:
        return sorted(diagnostics)
    records = _validated_records(root, data, diagnostics)
    by_identity = {
        record["identity"]: record
        for record in records
        if isinstance(record.get("identity"), str)
    }
    declared_bridges = {
        bridge_id
        for record in records
        if isinstance(record.get("surfaces"), dict)
        for bridge_ids in [record["surfaces"].get("client_bridges", [])]
        if isinstance(bridge_ids, list)
        for bridge_id in bridge_ids
        if isinstance(bridge_id, str) and bridge_id in BRIDGE_TARGETS
    }
    for bridge_id in sorted(declared_bridges):
        target = BRIDGE_TARGETS[bridge_id]
        record = by_identity.get(target.identity)
        if record is None:
            continue
        path = root / target.path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            diagnostics.append(
                f"bridge[{bridge_id}].description: projection is missing or "
                f"unreadable at {target.path.as_posix()}"
            )
            continue
        rendered, error = _render_projection(text, record, bridge_id)
        if error is not None:
            diagnostics.append(error)
        elif rendered != text:
            diagnostics.append(
                f"bridge[{bridge_id}].description: derived surface drift at "
                f"{target.path.as_posix()}"
            )
    return sorted(set(diagnostics))


def load_metadata(root: Path) -> List[Dict[str, Any]]:
    """Load validated records in canonical identity order."""
    diagnostics = validate_repository(root)
    if diagnostics:
        raise MetadataValidationError(diagnostics)
    data, read_diagnostics = _read_data(Path(root))
    if data is None:
        raise MetadataValidationError(read_diagnostics)
    return sorted(data["skills"], key=lambda record: record["identity"])


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def generate_surfaces(root: Path) -> Tuple[str, ...]:
    """Generate all bridge descriptions after validating every input first."""
    root = Path(root)
    data, diagnostics = _read_data(root)
    if data is None:
        raise MetadataValidationError(diagnostics)
    records = _validated_records(root, data, diagnostics)
    by_identity = {
        record["identity"]: record
        for record in records
        if isinstance(record.get("identity"), str)
    }
    rendered_by_path: Dict[Path, str] = {}
    for bridge_id, target in sorted(BRIDGE_TARGETS.items()):
        record = by_identity.get(target.identity)
        if record is None:
            continue
        surfaces = record.get("surfaces", {})
        bridge_ids = (
            surfaces.get("client_bridges", [])
            if isinstance(surfaces, dict)
            else []
        )
        if not isinstance(bridge_ids, list) or bridge_id not in bridge_ids:
            continue
        path = root / target.path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            diagnostics.append(
                f"bridge[{bridge_id}].description: projection is missing or "
                f"unreadable at {target.path.as_posix()}"
            )
            continue
        rendered, error = _render_projection(text, record, bridge_id)
        if error is not None:
            diagnostics.append(error)
        elif rendered is not None:
            rendered_by_path[path] = rendered
    if diagnostics:
        raise MetadataValidationError(diagnostics)
    for path in sorted(rendered_by_path, key=lambda item: item.as_posix()):
        _atomic_write(path, rendered_by_path[path])
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(rendered_by_path, key=lambda item: item.as_posix())
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or generate authoritative Cartopian skill metadata surfaces."
    )
    parser.add_argument("action", choices=("validate", "generate"))
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Cartopian repository or install root.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.action == "generate":
            generated = generate_surfaces(args.root)
            print(f"generated skill metadata surfaces: {len(generated)} projections")
        diagnostics = validate_repository(args.root)
        if diagnostics:
            raise MetadataValidationError(diagnostics)
        records = load_metadata(args.root)
        if args.action == "validate":
            print(
                f"skill metadata valid: {len(records)} records, "
                f"{len(BRIDGE_TARGETS)} bridge projections"
            )
    except MetadataValidationError as exc:
        for diagnostic in exc.diagnostics:
            print(diagnostic, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
