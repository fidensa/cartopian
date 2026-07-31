"""Red-evidence harness: restore the three fail-open identity paths in place.

Each reviewer-reported defect is reinstated exactly as it behaved before the
correction, then the regressions that close it are run against that behavior:

1. the install-state reader accepts any JSON carrying a versions list, an
   installed-content row, and a string beginning with ``sha256``, without
   validating schema identity, record schema version, row uniqueness, or the
   digest grammar;
2. verification compares only the MCP subset while the reported revision
   covers the whole shipped surface set, and the installer records no
   full-surface identity to compare against;
3. any printable single token in ``VERSION`` is accepted as install
   provenance.

Every assertion in the four regression classes must fail here and pass against
the real implementation.

Not part of the canonical suite; ``unittest discover`` only collects ``test*``.
Run explicitly:

    python3 tests/_install_identity_red_shim.py
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli.install_workflow as workflow  # noqa: E402
import cli.version_identities as identities  # noqa: E402

RED_CASES = (
    "tests.test_install_version_projection.TestInstallStateEvidenceFailsClosed",
    "tests.test_install_version_projection.TestVerificationCoversTheReportedSurface",
    "tests.test_install_version_projection.TestInstallReceiptGrammar",
    "tests.test_install_version_projection.TestCoordinatedInstallEvidence",
)


def _lax_recorded_mcp_identity(root: Path) -> Optional[str]:
    """The pre-correction reader: any sha256-ish string in any JSON shape."""
    path = root / identities._INSTALL_STATE_FILE
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > identities._INSTALL_STATE_MAX_BYTES
        ):
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    versions = record.get("versions") if isinstance(record, dict) else None
    if not isinstance(versions, list):
        return None
    for item in versions:
        if not isinstance(item, dict) or item.get("kind") != "installed_content":
            continue
        identity = item.get("mcp_identity")
        if isinstance(identity, str) and identity.startswith("sha256:"):
            return identity
    return None


def _mcp_only_evidence(root: Path) -> Dict[str, Any]:
    """Bind verification to the MCP subset only, as before the correction.

    Reporting the observed full-surface digest as the recorded one is what
    "the record verifies the whole install" amounted to: the wider comparison
    could never contradict anything.
    """
    recorded = _lax_recorded_mcp_identity(root)
    if recorded is None:
        return identities._install_evidence("absent", None, None, None)
    observed, _complete = identities._content_digest(
        root, identities.INSTALLED_CONTENT_PATHS
    )
    return identities._install_evidence("present", None, observed, recorded)


def _lax_receipt(root: Path) -> Tuple[Optional[str], str]:
    """Any single printable token counts as installer provenance."""
    token = identities._ref_marker(root, "VERSION")
    return token, "known" if token is not None else "absent"


def _version_records_without_installed_identity(*args: Any, **kwargs: Any):
    rows = _ORIGINAL_VERSION_RECORDS(*args, **kwargs)
    for row in rows:
        if isinstance(row, dict) and row.get("kind") == "installed_content":
            row.pop("installed_identity", None)
    return rows


_ORIGINAL_VERSION_RECORDS = workflow._version_records


def _install_shim() -> None:
    identities.install_state_evidence = _mcp_only_evidence
    identities._install_receipt = _lax_receipt
    identities._mcp_verification = lambda verification, **_kwargs: verification
    workflow._version_records = _version_records_without_installed_identity


def main() -> int:
    _install_shim()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromName(name) for name in RED_CASES
    )
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    summary: Dict[str, int] = {
        "run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
    }
    print(
        "red-shim summary: "
        + " ".join(f"{key}={value}" for key, value in summary.items())
    )
    return 0 if (result.failures or result.errors) else 1


if __name__ == "__main__":
    sys.exit(main())
