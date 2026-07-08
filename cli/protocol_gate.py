"""Config-schema migration gate for ``[project].protocol_version``.

Compares a project config's declared ``[project].protocol_version`` against
the shipped protocol version — the topmost ``### vX.Y.Z`` entry under
``## Entries`` in ``protocol/CHANGELOG.md``, per the CHANGELOG's own
``[project] protocol_version`` marker semantics — and classifies:

- ``GATE_CURRENT``  — marker equals the shipped version; pass, no gate noise.
- ``GATE_MIGRATE``  — marker is unset, missing, or lexically less than the
  shipped version (the CHANGELOG entries' applies-when precondition), so the
  documented migration entries bring it current.
- ``GATE_BLOCKED``  — marker is malformed or lexically greater than the
  shipped version; no CHANGELOG migration path exists, so consumers fail
  closed with the named residual :data:`RESIDUAL_NAME`.

Detection only: the gate never writes ``cartopian.toml`` — config is
operator-owned.

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


def read_shipped_protocol_version(changelog_path: Optional[Union[str, Path]] = None) -> str:
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


def classify_protocol_version(declared: Any, shipped: str) -> Dict[str, str]:
    """Classify a declared ``[project].protocol_version`` against ``shipped``.

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

    # CHANGELOG applies-when semantics: a migration entry applies when the
    # marker is "unset, missing, or lexically less" than the entry's version.
    if not detected or (_VERSION_FORM_RE.match(detected) and detected < shipped):
        detected_label = detected or "unset"
        return {
            "status": GATE_MIGRATE,
            "detected_version": detected_label,
            "shipped_version": shipped,
            "detail": (
                f"config-schema migration required: [project].protocol_version "
                f"is {detected_label} but the shipped protocol is {shipped} — "
                f"apply the protocol/CHANGELOG.md migration entries whose "
                f"applies-when precondition matches {detected_label} (they end "
                f"by setting the marker to {shipped}); the gate does not edit "
                f"cartopian.toml"
            ),
        }

    return {
        "status": GATE_BLOCKED,
        "detected_version": detected,
        "shipped_version": shipped,
        "detail": (
            f"config-schema gate failed closed (residual: {RESIDUAL_NAME}): "
            f"[project].protocol_version is {detected!r}, which is unknown to "
            f"or newer than the shipped protocol {shipped}; no CHANGELOG "
            f"migration path exists, so this config cannot be validated "
            f"against the shipped schema. cartopian.toml is left unmodified — "
            f"upgrade Cartopian or correct the marker"
        ),
    }
