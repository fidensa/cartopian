"""Shared registry primitives for registry CLI commands.

Centralizes:
- ``~/.cartopian/projects.json`` path resolution.
- Kebab-case id grammar.
- Per-entry registry schema validation for the ``discover-projects``
  minimum schema.
- Read / write primitives with atomic temp-file rename.

Used by ``discover_projects``, ``register_project``, ``unregister_project``.
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List


# Kebab-case id grammar:
#   lowercase ASCII letters, digits, hyphens; must start with a letter;
#   no leading/trailing/consecutive hyphens.
_KEBAB_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

_ALLOWED_ENTRY_KEYS = frozenset({"id", "path", "label"})


def registry_path() -> Path:
    return Path.home() / ".cartopian" / "projects.json"


def is_kebab_case(value: Any) -> bool:
    return isinstance(value, str) and bool(_KEBAB_RE.match(value))


class MalformedRegistry(Exception):
    """Registry file or entry violates the expected schema."""

    def __init__(self, path: Path, detail: str = "") -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"{path}{(' — ' + detail) if detail else ''}")


def _validate_entry(entry: Any, path: Path) -> None:
    if not isinstance(entry, dict):
        raise MalformedRegistry(path, "entry is not a JSON object")
    extra = set(entry.keys()) - _ALLOWED_ENTRY_KEYS
    if extra:
        raise MalformedRegistry(
            path, f"entry has unknown keys: {sorted(extra)}"
        )
    eid = entry.get("id")
    if not isinstance(eid, str) or eid == "":
        raise MalformedRegistry(path, "entry id missing or empty")
    if not is_kebab_case(eid):
        raise MalformedRegistry(
            path, f"entry id is not kebab-case: {eid!r}"
        )
    epath = entry.get("path")
    if not isinstance(epath, str) or epath == "":
        raise MalformedRegistry(path, "entry path missing or empty")
    if not Path(epath).is_absolute():
        raise MalformedRegistry(
            path, f"entry path is not absolute: {epath!r}"
        )
    if "label" in entry:
        elabel = entry["label"]
        if elabel is not None and (not isinstance(elabel, str) or elabel == ""):
            raise MalformedRegistry(
                path, "entry label must be a non-empty string or null"
            )


def read_registry(path: Path) -> List[Dict[str, Any]]:
    """Read and validate the registry file.

    Missing or empty file → empty list.
    Corrupt JSON / non-array root / invalid entry → MalformedRegistry.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MalformedRegistry(path, str(exc)) from exc
    if raw.strip() == "":
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedRegistry(path, "invalid JSON") from exc
    if not isinstance(data, list):
        raise MalformedRegistry(path, "top-level is not a JSON array")
    for entry in data:
        _validate_entry(entry, path)
    return data


def write_registry(path: Path, entries: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
