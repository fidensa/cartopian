"""Config-schema migration gate for ``[project].project_schema_version``.

Compares a project config's declared
``[project].project_schema_version`` against the shipped schema target — the
topmost ``### vX.Y.Z`` entry under ``## Entries`` in
``protocol/CHANGELOG.md`` — and classifies:

- ``GATE_CURRENT``  — marker equals the shipped version; pass, no gate noise.
- ``GATE_MIGRATE``  — marker is unset, missing, or numerically less than the
  shipped version (the CHANGELOG entries' applies-when precondition), so the
  documented migration entries bring it current.
- ``GATE_BLOCKED``  — marker is malformed or numerically greater than the
  shipped version; no CHANGELOG migration path exists, so consumers fail
  closed with the named residual :data:`RESIDUAL_NAME`.

Detection only: the gate never writes ``cartopian.toml``. Applying the
migration (including the marker bump) is PM-owned and goes through the mediated
``cartopian update-config`` command on operator approval.

Standard library only, with no intra-package imports, so
``scripts/install.py`` can load this file directly from a source tree via
``importlib`` during upgrade/install reconciliation.
"""
import re
from pathlib import Path
from typing import Any, Dict, Optional, Union

GATE_CURRENT = "current"
GATE_MIGRATE = "older-migratable"
GATE_BLOCKED = "unknown-or-newer"

# The named residual a fail-closed classification discloses.
RESIDUAL_NAME = "unverifiable-config-schema"

_ENTRY_VERSION_RE = re.compile(r"^###\s+(v\d+\.\d+\.\d+)\b", re.MULTILINE)
_VERSION_FORM_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# Repo root is one parent up from this file: cli/protocol_gate.py -> repo.
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANGELOG_PATH = _REPO_ROOT / "protocol" / "CHANGELOG.md"


def read_shipped_project_schema_version(
    changelog_path: Optional[Union[str, Path]] = None,
) -> str:
    """The shipped protocol version: the topmost ``### vX.Y.Z`` entry under
    ``## Entries`` in the protocol CHANGELOG (same rule ``generate-config``
    stamps new configs with)."""
    path = Path(changelog_path) if changelog_path is not None else DEFAULT_CHANGELOG_PATH
    text = path.read_text(encoding="utf-8")
    _, _, body = text.partition("\n## Entries\n")
    m = _ENTRY_VERSION_RE.search(body)
    if not m:
        raise RuntimeError(f"could not locate a protocol version entry in {path}")
    return m.group(1)


def classify_project_schema_version(declared: Any, shipped: str) -> Dict[str, str]:
    """Classify a declared project schema marker against ``shipped``.

    Returns ``{status, detected_version, shipped_version, detail}``. The
    ``detail`` string names the detected version, the shipped version, and —
    for :data:`GATE_MIGRATE` — the required migration; for
    :data:`GATE_BLOCKED` it names the :data:`RESIDUAL_NAME` residual.
    """
    detected = "" if declared is None else str(declared).strip()

    if detected == shipped:
        return {
            "status": GATE_CURRENT,
            "detected_version": detected,
            "shipped_version": shipped,
            "detail": "",
        }

    # CHANGELOG applies-when semantics compare numeric version components;
    # lexical ordering breaks as soon as a component reaches two digits
    # (v0.9.0 would incorrectly sort after v0.10.0).
    def version_tuple(value: str) -> tuple[int, int, int]:
        return tuple(int(part) for part in value.removeprefix("v").split("."))  # type: ignore[return-value]

    if not detected or (
        _VERSION_FORM_RE.match(detected)
        and version_tuple(detected) < version_tuple(shipped)
    ):
        detected_label = detected or "unset"
        return {
            "status": GATE_MIGRATE,
            "detected_version": detected_label,
            "shipped_version": shipped,
            "detail": (
                f"project schema migration required: project_schema_version is "
                f"{detected_label}, while the shipped schema target is {shipped} — "
                f"apply the protocol/CHANGELOG.md migration entries whose "
                f"applies-when precondition matches {detected_label} (they end "
                f"by setting the internal marker to {shipped}); the PM applies "
                f"the migration after operator approval"
            ),
        }

    return {
        "status": GATE_BLOCKED,
        "detected_version": detected,
        "shipped_version": shipped,
        "detail": (
            f"config-schema gate failed closed (residual: {RESIDUAL_NAME}): "
            f"project_schema_version is {detected!r}, which is unknown to or "
            f"newer than the shipped schema target {shipped}; no CHANGELOG "
            f"migration path exists, so this config cannot be validated "
            f"against the shipped schema. Project config is left unmodified — "
            f"upgrade Cartopian or let the PM repair the internal marker"
        ),
    }


# Reserved migration-only compatibility aliases for transforming historical
# markers. Normal config parsing and all preferred output use only
# project_schema_version.
read_shipped_protocol_version = read_shipped_project_schema_version
classify_protocol_version = classify_project_schema_version
