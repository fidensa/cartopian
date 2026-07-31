"""Red-evidence harness: restore the fail-open restart paths in place.

Each defect was the absence of one gate, so reinstating it is exactly a
permissive gate:

1. the MCP server read persisted restart facts from any parseable JSON at
   ``<root>/install-update-state.json``, without requiring supported schema
   identity, an actual non-boolean integer schema version, or a complete
   positive installed-content row — and never treated an uninterpretable
   record as an affected surface;
2. the public restart-verification command read only file and JSON shape
   before letting a pending restart row or a persisted surface proof
   strengthen its verdict;
3. all three consumers of a persisted restart row — the installer workflow's
   prior-restart reader, the MCP restart projection, and the public restart
   verifier — selected that row on record positivity alone, discarding the
   recorded MCP identity the shared gate returns, so a record naming other MCP
   content still donated its sibling restart row as prior-process evidence.

Every "cannot strengthen", "cannot attribute", and refused-evidence assertion
in these regression classes must fail here, and the positive cases — valid record,
absent record, independent non-MCP drift, stale process, CLI/MCP parity, and
valid fresh-process proof — must still pass.

Not part of the canonical suite; ``unittest discover`` only collects ``test*``.
Run explicitly:

    python3 tests/_restart_authority_red_shim.py
"""
from __future__ import annotations

import json
import sys
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli.commands.verify_restart_state as verify  # noqa: E402
import cli.install_workflow as install_workflow  # noqa: E402
import cli.version_identities as identities  # noqa: E402
from mcp_server import server  # noqa: E402

RED_CASES = (
    "tests.test_restart_record_authority.McpRestartProjectionAuthorityTests",
    "tests.test_restart_record_authority."
    "PublicRestartVerificationAuthorityTests",
    "tests.test_restart_record_authority."
    "InstallerWorkflowRestartAuthorityTests",
)
EXPECTED_RED = (
    "test_unusable_records_cannot_strengthen_the_projection",
    "test_unreadable_record_cannot_strengthen_the_projection",
    "test_unusable_records_cannot_strengthen_pending_restart",
    "test_unusable_records_cannot_strengthen_persisted_surface_proof",
    "test_record_naming_other_mcp_content_cannot_strengthen",
    "test_record_naming_other_mcp_content_cannot_attribute_prior_process",
    # The refused-evidence classes are a strict superset of the substituted
    # one, so the shape-only readers here fail them for the same reasons: they
    # expose a row from a record no gate accepted, and never classify anything
    # as withheld. The absent-versus-refused distinction has its own harness
    # (``tests/_restart_evidence_class_red_shim.py``), which isolates the
    # collapse without also removing record authority and content binding.
    "test_refused_restart_evidence_is_restart_relevant_in_planning",
    "test_refused_restart_evidence_still_fails_closed",
)


def _shape_only_evidence(root: Path) -> Dict[str, Any]:
    """The pre-correction reader: any parseable JSON is usable evidence."""
    path = root / identities._INSTALL_STATE_FILE
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > identities._INSTALL_STATE_MAX_BYTES
        ):
            return identities._install_evidence("absent", None, None, None)
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return identities._install_evidence("absent", None, None, None)
    if not isinstance(record, dict):
        return identities._install_evidence("absent", None, None, None)
    # No schema identity, schema version, or installed-content row is
    # consulted, and no recorded MCP identity is carried back for comparison.
    return identities._install_evidence("present", record, None, None)


def _shape_only_record_evidence(record: Any) -> Dict[str, Any]:
    """The pre-correction command: file and JSON shape were the whole gate."""
    if not isinstance(record, Mapping):
        return identities._install_evidence("absent", None, None, None)
    return identities._install_evidence("present", record, None, None)


def _ungated_authority(record: Any) -> Tuple[bool, Optional[str]]:
    """The pre-correction command's own record authority."""
    return True, None


def _unbound_restart_candidate(
    evidence: Mapping[str, Any],
    *,
    observed_mcp_identity: Optional[str],
    client_id: str,
    states: Sequence[str] = ("required", "pending"),
) -> "OrderedDict[str, Any]":
    """The pre-correction rule: record positivity alone selected the row.

    The recorded MCP identity the shared gate returns is discarded, so a row
    attesting other content is still exposed as prior-process evidence.
    """
    record = evidence.get("record") if isinstance(evidence, Mapping) else None
    if not isinstance(record, Mapping):
        return OrderedDict((("status", "absent"), ("row", None)))
    rows = record.get("restarts")
    if not isinstance(rows, list):
        return OrderedDict((("status", "absent"), ("row", None)))
    allowed = tuple(states)
    candidates = [
        dict(item)
        for item in rows
        if isinstance(item, Mapping)
        and item.get("state") in allowed
        and (
            item.get("client") == client_id
            or client_id == "unsupported"
            or len(rows) == 1
        )
    ]
    if len(candidates) != 1:
        return OrderedDict((("status", "absent"), ("row", None)))
    return OrderedDict((("status", "bound"), ("row", candidates[0])))


def _install_shim() -> None:
    server._install_record_evidence = _shape_only_evidence
    verify.install_record_evidence = _shape_only_record_evidence
    verify._record_authority = _ungated_authority
    for module in (server, verify, install_workflow):
        module.content_bound_restart_candidate = _unbound_restart_candidate


def main() -> int:
    _install_shim()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        loader.loadTestsFromName(name) for name in RED_CASES
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = {
        # Subtest ids carry a trailing " (record='...')" parameter label.
        test.id().split(" ", 1)[0].rsplit(".", 1)[-1]
        for test, _trace in list(result.failures) + list(result.errors)
    }
    missing = sorted(set(EXPECTED_RED) - failed)
    unexpected = sorted(failed - set(EXPECTED_RED))
    print(
        "red-shim summary: "
        f"run={result.testsRun} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"expected-red-not-failing={missing or 'none'} "
        f"positives-failing={unexpected or 'none'}"
    )
    return 0 if (not missing and not unexpected) else 1


if __name__ == "__main__":
    sys.exit(main())
