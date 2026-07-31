"""Red-evidence harness: collapse refused restart evidence back into absence.

The record gate and the content-binding rule are both left intact here. The
single defect reinstated is the shared restart selector's *classification*: it
reported ``absent`` for every record it did not reach a bound candidate from,
so "nothing was ever persisted here" and "a record was persisted and this
runtime refused to read it" arrived at consumers as the same benign fact.

Installer planning treated only ``unbound`` evidence as restart-relevant, so a
record refused by the record gate — a malformed recorded MCP identity, for
instance — fell through planning as though no relevant persisted evidence
existed. The run then reported ``no_restart_needed``, workflow state
``complete``, outcome ``complete-qualified``, and ``restart_required = false``
for content whose recorded identity it had just refused.

Under this shim:

* the classification assertions in the shared-authority class are red, along
  with the two consumer rechecks that assert the same classification;
* the installer planning/application regression is red *behaviourally* — the
  probe below reproduces the reported fail-open outcome directly;
* every positive control stays green: the genuinely absent record, the absent
  candidate, the compatible bound record, the stale process, independent
  non-MCP drift, CLI/MCP parity, and both consumers' own fail-closed handling
  of an unusable record (each keeps its own record check, which is why only
  the installer path fell open).

Not part of the canonical suite; ``unittest discover`` only collects ``test*``.
Run explicitly:

    python3 tests/_restart_evidence_class_red_shim.py
"""
from __future__ import annotations

import sys
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli.commands.verify_restart_state as verify  # noqa: E402
import cli.install_workflow as install_workflow  # noqa: E402
import cli.version_identities as identities  # noqa: E402
from mcp_server import server  # noqa: E402
from tests import test_restart_record_authority as authority  # noqa: E402

RED_CASES = (
    "tests.test_restart_record_authority.SharedRestartEvidenceClassTests",
    "tests.test_restart_record_authority.McpRestartProjectionAuthorityTests",
    "tests.test_restart_record_authority."
    "PublicRestartVerificationAuthorityTests",
    "tests.test_restart_record_authority."
    "InstallerWorkflowRestartAuthorityTests",
)
EXPECTED_RED = (
    # The classification itself.
    "test_refused_evidence_is_never_classified_absent",
    "test_ambiguous_candidates_are_refused_rather_than_absent",
    "test_malformed_restart_section_is_refused_rather_than_absent",
    "test_unreadable_record_is_refused_rather_than_absent",
    "test_parsed_and_file_readers_reach_the_same_class",
    # The installer path, which read the collapsed verdict as absence.
    "test_refused_restart_evidence_is_restart_relevant_in_planning",
    # The two consumer rechecks assert the shared classification as well; their
    # behavioural assertions stay green here, because each consumer keeps its
    # own record check.
    "test_refused_restart_evidence_still_fails_closed",
)
# The reported fail-open shape: a malformed recorded MCP identity that planning
# never treated as restart-relevant.
FAIL_OPEN = {
    "restart_status": "no_restart_needed",
    "workflow_state": "complete",
    "outcome_status": "complete-qualified",
    "restart_required": False,
}


def _collapsed_restart_candidate(
    evidence: Mapping[str, Any],
    *,
    observed_mcp_identity: Optional[str],
    client_id: str,
    states: Sequence[str] = ("required", "pending"),
) -> "OrderedDict[str, Any]":
    """The pre-correction selector: every non-bound record collapses to absent.

    Record authority and content binding are unchanged — a refused record still
    exposes no row. Only the reported evidence class is collapsed, which is
    exactly the fact installer planning consumed.
    """
    def absent() -> "OrderedDict[str, Any]":
        return OrderedDict(
            (("status", identities.RESTART_EVIDENCE_ABSENT), ("row", None))
        )

    status = evidence.get("status") if isinstance(evidence, Mapping) else None
    record = evidence.get("record") if isinstance(evidence, Mapping) else None
    if status != identities.INSTALL_STATE_PRESENT or not isinstance(
        record, Mapping
    ):
        return absent()
    rows = record.get("restarts")
    if not isinstance(rows, list):
        return absent()
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
        return absent()
    if not identities.mcp_identity_binds(
        evidence.get("mcp_identity"), observed_mcp_identity
    ):
        return OrderedDict(
            (("status", identities.RESTART_EVIDENCE_UNBOUND), ("row", None))
        )
    return OrderedDict(
        (("status", identities.RESTART_EVIDENCE_BOUND), ("row", candidates[0]))
    )


def _install_shim() -> None:
    for module in (server, verify, install_workflow, authority):
        module.content_bound_restart_candidate = _collapsed_restart_candidate


class _Probe(unittest.TestCase):
    """A test case used only for its temporary-directory cleanup registry."""

    def runTest(self) -> None:  # pragma: no cover - never executed
        raise AssertionError("probe is not a test")


def _fail_open_probe() -> Dict[str, Any]:
    """Reproduce the reported installer outcome for a malformed identity."""
    probe = _Probe()
    try:
        install_root, client_home = authority.clone_workflow(probe)
        authority.corrupt(
            install_root,
            authority._row_set("mcp_identity", "sha256:not-a-digest"),
        )
        evidence = install_workflow._prior_restart(install_root, "codex")
        _plan, result = (
            authority.InstallerWorkflowRestartAuthorityTests.run_update(
                probe, install_root, client_home
            )
        )
        restart = result["restarts"][0]
        return {
            "evidence_class": evidence["status"],
            "restart_status": restart["status"],
            "workflow_state": result["state"],
            "outcome_status": result["outcome"]["status"],
            "restart_required": result["outcome"]["restart_required"],
        }
    finally:
        probe.doCleanups()


def main() -> int:
    _install_shim()
    authority.setUpModule()
    try:
        observed = _fail_open_probe()
        loader = unittest.TestLoader()
        suite = unittest.TestSuite(
            loader.loadTestsFromName(name) for name in RED_CASES
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        authority.tearDownModule()
    failed = {
        # Subtest ids carry a trailing " (record='...')" parameter label.
        test.id().split(" ", 1)[0].rsplit(".", 1)[-1]
        for test, _trace in list(result.failures) + list(result.errors)
    }
    missing = sorted(set(EXPECTED_RED) - failed)
    unexpected = sorted(failed - set(EXPECTED_RED))
    reproduced = all(
        observed[field] == value for field, value in FAIL_OPEN.items()
    )
    print(
        "evidence-class red-shim summary: "
        f"run={result.testsRun} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"expected-red-not-failing={missing or 'none'} "
        f"positives-failing={unexpected or 'none'} "
        f"fail-open-probe={observed} "
        f"fail-open-reproduced={reproduced}"
    )
    return 0 if (not missing and not unexpected and reproduced) else 1


if __name__ == "__main__":
    sys.exit(main())
