"""Persisted-record authority on every restart/runtime strengthening path.

The installed-content reader fails closed on a record it cannot interpret, but
restart evidence lives in the *same* record and used to be read on file and
JSON shape alone. Two public surfaces could therefore re-confer what the reader
had just refused:

* the MCP install-context/restart projection, whose persisted restart baseline
  overwrote a fail-closed MCP verdict from an unchecked sibling restart row;
* the public restart-verification command, whose pending-restart and
  persisted-installed-proof logic strengthened the same way.

Every case here observes content that genuinely matches what the record claims
and a genuinely fresh, verified connected process, so nothing but the record
authority itself decides the verdict. Each invalid record must withhold
installed/MCP verification, current state, activation permission, and a
successful complete-qualified outcome; the same record, uncorrupted, must still
permit them.

Record authority answers whether a record may be read at all. It does not
answer *what content* the record speaks for: a structurally valid, positive
record whose recorded ``mcp_identity`` names different MCP content used to keep
donating its sibling restart row as prior-process evidence, because the three
consumers of that row read it before — or without — comparing the recorded MCP
identity with the content being projected. The substituted-identity classes
below cover all three consumers directly (installer workflow, MCP install-
context/restart projection, public restart verification): a record that attests
other content must expose no prior process, no ``previous_instance_id``, no
verified fresh proof, no current/no-restart state, no activation permission,
and no successful complete-qualified outcome.

Refusing to read a record is also not the same as finding none. The shared
selector reported ``absent`` for every record it did not reach a bound
candidate from, so a refused record — a malformed recorded MCP identity makes
one — arrived at installer planning as the benign "nothing was persisted here"
class that planning is entitled to treat as unaffected. The two other consumers
kept their own record checks and stayed fail-closed; the installer reported
``no_restart_needed``, workflow state ``complete``, a complete-qualified
outcome, and ``restart_required = false`` for content whose recorded identity
it had just refused. The classes below therefore assert four distinct verdicts
of one shared authority — absent, unusable, unbound, bound — and hold every
consumer to the behaviour each verdict licenses, with the genuinely absent
record and candidate kept as the positive controls the distinction protects.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.config_schema import identity_contract  # noqa: E402
from cli.install_state import (  # noqa: E402
    RECORD_SCHEMA_VERSION,
    SCHEMA_IDENTITY,
)
from cli.install_workflow import (  # noqa: E402
    MCP_TARGETS,
    STATE_FILE,
    _surface_digest,
    apply_workflow,
    plan_workflow,
)
from cli import install_workflow  # noqa: E402
from cli.commands import verify_restart_state  # noqa: E402
from cli.restart_state import normalize_client_context  # noqa: E402
from cli.version_identities import (  # noqa: E402
    INSTALLED_CONTENT_PATHS,
    RESTART_EVIDENCE_ABSENT,
    RESTART_EVIDENCE_BOUND,
    RESTART_EVIDENCE_UNBOUND,
    RESTART_EVIDENCE_UNUSABLE,
    content_bound_restart_candidate,
    install_record_evidence,
    install_state_evidence,
    installed_content,
    mcp_content_identity,
    restart_evidence_withheld,
)
from mcp_server import server  # noqa: E402

FIXTURE_REF = "v9.9.9"
STALE_PROCESS = 7100
STALE_INSTANCE = "process:7100:old"
FRESH_PROCESS = 7200
FRESH_INSTANCE = "process:7200:new"

_TMP: Optional[tempfile.TemporaryDirectory] = None
_PRISTINE: Optional[Path] = None
_UPDATED: Optional[Path] = None
_WORKFLOW: Optional[Path] = None


# ---------------------------------------------------------------------------
# Module fixtures: one copy install, and one real restart-pending install
# ---------------------------------------------------------------------------

def _running(identity: Optional[str], process_id: int, instance_id: str):
    """A connected-process fact claiming verified, complete loaded content."""
    return {
        "process_id": process_id,
        "instance_id": instance_id,
        "loaded_content": {
            "mcp_identity": identity,
            "mcp_verification": "verified",
            "mcp_completeness": "complete",
        },
        "state": "current" if identity else "unknown",
        "verification": "verified",
        "completeness": "complete",
        "authority": "connected-mcp-process",
        "attribution": "connected-process",
    }


def _build_restart_pending_install(root: Path) -> Path:
    """Install, change the MCP surface, then update against a stale process.

    The result is a real coordinated record: a valid installed-content row, a
    verified mcp-server-files surface row, and one pending restart row for
    Codex — exactly the evidence the two strengthening paths read.
    """
    install_root = root / "install"
    client_home = root / "home"
    client_home.mkdir(parents=True)
    apply_workflow(
        plan_workflow(
            source_root=REPO_ROOT,
            install_root=install_root,
            operation="fresh-install",
            client_home=client_home,
            clients=("codex",),
        )
    )
    target = install_root / "mcp_server" / "server.py"
    target.write_text("# stale connected server fixture\n", encoding="utf-8")
    inventory = plan_workflow(
        source_root=REPO_ROOT,
        install_root=install_root,
        operation="update",
        client_home=client_home,
        clients=("codex",),
    )
    stale_identity = next(
        item
        for item in inventory["surfaces"]
        if item["kind"] == "mcp-server-files"
    )["observed_content_identity"]
    apply_workflow(
        plan_workflow(
            source_root=REPO_ROOT,
            install_root=install_root,
            operation="update",
            client_home=client_home,
            clients=("codex",),
            running_server_fact=_running(
                stale_identity, STALE_PROCESS, STALE_INSTANCE
            ),
            client_context=normalize_client_context("codex-mcp-client"),
        )
    )
    return install_root


def setUpModule() -> None:
    global _TMP, _PRISTINE, _UPDATED, _WORKFLOW
    _TMP = tempfile.TemporaryDirectory()
    base = Path(_TMP.name)
    _PRISTINE = base / "pristine"
    from tests._install_fixture import install_copy_fixture

    install_copy_fixture(REPO_ROOT, _PRISTINE)
    (_PRISTINE / "VERSION").write_text(f"{FIXTURE_REF}\n", encoding="utf-8")
    _WORKFLOW = base / "workflow"
    _UPDATED = _build_restart_pending_install(_WORKFLOW)


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def clone(case: unittest.TestCase, source: Optional[Path]) -> Path:
    """Return an isolated copy of a module fixture install root."""
    assert source is not None
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    root = Path(tmp.name) / ".cartopian"
    shutil.copytree(source, root, symlinks=True)
    return root


def clone_workflow(case: unittest.TestCase) -> Tuple[Path, Path]:
    """Return an isolated copy of the restart-pending install and its home.

    The installer workflow reads a client home as well as the install root, so
    both halves of the coordinated fixture are cloned together and no test
    touches a real registration, configuration, or install.
    """
    assert _WORKFLOW is not None
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    root = Path(tmp.name) / "workflow"
    shutil.copytree(_WORKFLOW, root, symlinks=True)
    return root / "install", root / "home"


# ---------------------------------------------------------------------------
# The record-authority case catalogue, shared by both public paths
# ---------------------------------------------------------------------------

def installed_row(record: Dict[str, Any]) -> Dict[str, Any]:
    return next(
        item
        for item in record["versions"]
        if item.get("kind") == "installed_content"
    )


def _set(name: str, value: Any) -> Callable[[Dict[str, Any]], None]:
    def mutate(record: Dict[str, Any]) -> None:
        record[name] = value

    return mutate


def _row_set(name: str, value: Any) -> Callable[[Dict[str, Any]], None]:
    def mutate(record: Dict[str, Any]) -> None:
        installed_row(record)[name] = value

    return mutate


def _row_pop(name: str) -> Callable[[Dict[str, Any]], None]:
    def mutate(record: Dict[str, Any]) -> None:
        installed_row(record).pop(name, None)

    return mutate


def _drop_schema(record: Dict[str, Any]) -> None:
    record.pop("schema_identity", None)
    record.pop("record_schema_version", None)


def _unknown_row(record: Dict[str, Any]) -> None:
    row = installed_row(record)
    row["state"] = "unknown"
    row["verification"] = "unknown"


def _truncate_identity(record: Dict[str, Any]) -> None:
    row = installed_row(record)
    row["installed_identity"] = str(row["installed_identity"])[
        : len("sha256:") + 8
    ]


def _duplicate_row(record: Dict[str, Any]) -> None:
    record["versions"].append(copy.deepcopy(installed_row(record)))


def _remove_row(record: Dict[str, Any]) -> None:
    record["versions"] = [
        item
        for item in record["versions"]
        if item.get("kind") != "installed_content"
    ]


# (label, mutation) pairs. Each leaves the restart/surface evidence intact and
# the observed content unchanged: only the record's own authority is broken.
INVALID_RECORDS: Tuple[Tuple[str, Callable[[Dict[str, Any]], None]], ...] = (
    # Schema identity and an actual non-boolean integer schema version.
    ("json-float-schema-version", _set("record_schema_version", 1.0)),
    ("boolean-schema-version", _set("record_schema_version", True)),
    ("unsupported-schema-version", _set("record_schema_version", 2)),
    ("string-schema-version", _set("record_schema_version", "1")),
    (
        "unsupported-schema-identity",
        _set("schema_identity", "cartopian-install-update-state-v2"),
    ),
    ("missing-schema-fields", _drop_schema),
    # Closed vocabularies for installed-content state and verification.
    ("out-of-vocabulary-row-state", _row_set("state", "current")),
    ("boolean-row-state", _row_set("state", True)),
    ("out-of-vocabulary-row-verification", _row_set("verification", "ok")),
    ("boolean-row-verification", _row_set("verification", True)),
    # Semantically negative rows inside the closed vocabularies.
    ("unknown-installed-row", _unknown_row),
    ("unverified-installed-row", _row_set("verification", "unverified")),
    ("dirty-installed-row", _row_set("state", "dirty")),
    ("failed-verification-row", _row_set("verification", "failed")),
    # Complete, well-formed full-surface identity values.
    ("missing-full-surface-identity", _row_pop("installed_identity")),
    (
        "malformed-full-surface-identity",
        _row_set("installed_identity", "sha256:not-a-digest"),
    ),
    ("truncated-full-surface-identity", _truncate_identity),
    ("missing-identity-value", _row_set("value", None)),
    (
        "substituted-row-authority",
        _row_set("authority", "maintainer-release-metadata"),
    ),
    ("duplicate-installed-rows", _duplicate_row),
    ("missing-installed-row", _remove_row),
)


SUBSTITUTED_MCP_IDENTITY = "sha256:" + "1" * 64


def _empty_restarts(record: Dict[str, Any]) -> None:
    record["restarts"] = []


# (label, mutation, shared classification). Each mutation leaves the restart
# row, the surface evidence, and the observed content intact: only the recorded
# MCP identity — or, for the last two, an unrelated record-compatibility fact —
# decides whether the persisted restart evidence may be read.
#
# The classes are deliberately different verdicts of the same shared authority.
# A malformed digest is refused by the record gate itself, while a missing or
# substituted one leaves a compatible record whose candidate cannot be bound to
# this content. Both are *refusals*: persisted evidence exists, this runtime
# declines to read it, and the MCP surface stays restart-relevant. Collapsing
# either into `absent` reports the benign "nothing was persisted" class for a
# record that was, which is the fail-open claim these cases exist to catch.
REFUSED_RESTART_EVIDENCE: Tuple[
    Tuple[str, Callable[[Dict[str, Any]], None], str], ...
] = (
    (
        "missing-recorded-mcp-identity",
        _row_pop("mcp_identity"),
        RESTART_EVIDENCE_UNBOUND,
    ),
    (
        "malformed-recorded-mcp-identity",
        _row_set("mcp_identity", "sha256:not-a-digest"),
        RESTART_EVIDENCE_UNUSABLE,
    ),
    (
        "null-recorded-mcp-identity",
        _row_set("mcp_identity", None),
        RESTART_EVIDENCE_UNBOUND,
    ),
    (
        "substituted-recorded-mcp-identity",
        _row_set("mcp_identity", SUBSTITUTED_MCP_IDENTITY),
        RESTART_EVIDENCE_UNBOUND,
    ),
    (
        "non-string-recorded-mcp-identity",
        _row_set("mcp_identity", 1),
        RESTART_EVIDENCE_UNUSABLE,
    ),
    # Unusable for a compatibility reason unrelated to the MCP identity: the
    # record gate refuses it before any restart row is considered.
    (
        "unusable-record-unsupported-schema-version",
        _set("record_schema_version", 2),
        RESTART_EVIDENCE_UNUSABLE,
    ),
    (
        "unusable-record-unproven-installed-row",
        _unknown_row,
        RESTART_EVIDENCE_UNUSABLE,
    ),
    (
        "unusable-record-missing-installed-row",
        _remove_row,
        RESTART_EVIDENCE_UNUSABLE,
    ),
)

# The classes that must stay benign. A record that persisted no restart
# candidate for this caller is not withheld evidence: nothing was written for
# this caller to read, so other evidence still applies and the MCP surface may
# still be reported as unchanged.
ABSENT_RESTART_EVIDENCE: Tuple[
    Tuple[str, Optional[Callable[[Dict[str, Any]], None]]], ...
] = (
    ("no-record-at-all", None),
    ("compatible-record-with-no-restart-rows", _empty_restarts),
    ("compatible-record-with-no-restart-section", _set("restarts", None)),
)


def read_record(root: Path) -> Dict[str, Any]:
    return json.loads((root / STATE_FILE).read_text(encoding="utf-8"))


def write_record(root: Path, record: Dict[str, Any]) -> None:
    (root / STATE_FILE).write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )


def corrupt(root: Path, mutate: Callable[[Dict[str, Any]], None]) -> None:
    record = read_record(root)
    mutate(record)
    write_record(root, record)


# ---------------------------------------------------------------------------
# The shared authority: absence and refusal are separate evidence classes
# ---------------------------------------------------------------------------

class SharedRestartEvidenceClassTests(unittest.TestCase):
    """One rule decides absent versus refused, for every consumer.

    The three consumers ask the same shared authority what a persisted restart
    candidate is worth. When that authority collapsed every non-present record
    into ``absent``, a consumer that (correctly) treats absence as benign could
    not tell "nothing was ever persisted here" from "a record was persisted and
    this runtime refused to read it" — so the second was reported as the first.
    """

    def candidate(self, root: Path) -> Dict[str, Any]:
        return content_bound_restart_candidate(
            install_state_evidence(root),
            observed_mcp_identity=mcp_content_identity(root)["identity"],
            client_id="codex",
        )

    def test_refused_evidence_is_never_classified_absent(self) -> None:
        for label, mutate, expected in REFUSED_RESTART_EVIDENCE:
            with self.subTest(record=label):
                root = clone(self, _UPDATED)
                corrupt(root, mutate)
                candidate = self.candidate(root)
                self.assertEqual(candidate["status"], expected)
                self.assertNotEqual(
                    candidate["status"], RESTART_EVIDENCE_ABSENT
                )
                self.assertIsNone(candidate["row"])
                self.assertTrue(restart_evidence_withheld(candidate))
                # The verdict answers the same question once carried forward
                # through a persisted record as a bare token.
                self.assertTrue(
                    restart_evidence_withheld(candidate["status"])
                )

    def test_absent_evidence_keeps_its_own_benign_class(self) -> None:
        for label, mutate in ABSENT_RESTART_EVIDENCE:
            with self.subTest(record=label):
                root = clone(self, _UPDATED)
                if mutate is None:
                    (root / STATE_FILE).unlink()
                else:
                    corrupt(root, mutate)
                candidate = self.candidate(root)
                self.assertEqual(candidate["status"], RESTART_EVIDENCE_ABSENT)
                self.assertIsNone(candidate["row"])
                self.assertFalse(restart_evidence_withheld(candidate))

    def test_compatible_bound_record_still_exposes_its_row(self) -> None:
        root = clone(self, _UPDATED)
        candidate = self.candidate(root)
        self.assertEqual(candidate["status"], RESTART_EVIDENCE_BOUND)
        self.assertEqual(candidate["row"]["instance_id"], STALE_INSTANCE)
        self.assertFalse(restart_evidence_withheld(candidate))

    def test_ambiguous_candidates_are_refused_rather_than_absent(self) -> None:
        # Two rows claim this caller: the record does not say which prior
        # process a fresh-process proof would be measured against. That is
        # persisted evidence this runtime cannot resolve, not none of it.
        root = clone(self, _UPDATED)
        record = read_record(root)
        record["restarts"].append(copy.deepcopy(record["restarts"][0]))
        write_record(root, record)
        candidate = self.candidate(root)
        self.assertEqual(candidate["status"], RESTART_EVIDENCE_UNUSABLE)
        self.assertIsNone(candidate["row"])
        self.assertTrue(restart_evidence_withheld(candidate))

    def test_malformed_restart_section_is_refused_rather_than_absent(
        self,
    ) -> None:
        root = clone(self, _UPDATED)
        corrupt(root, _set("restarts", "required"))
        candidate = self.candidate(root)
        self.assertEqual(candidate["status"], RESTART_EVIDENCE_UNUSABLE)
        self.assertIsNone(candidate["row"])

    def test_unreadable_record_is_refused_rather_than_absent(self) -> None:
        root = clone(self, _UPDATED)
        (root / STATE_FILE).write_text("{not json", encoding="utf-8")
        candidate = self.candidate(root)
        self.assertEqual(candidate["status"], RESTART_EVIDENCE_UNUSABLE)
        self.assertTrue(restart_evidence_withheld(candidate))

    def test_parsed_and_file_readers_reach_the_same_class(self) -> None:
        """One authority, whichever side of the file boundary reads it."""
        for label, mutate, expected in REFUSED_RESTART_EVIDENCE:
            with self.subTest(record=label):
                root = clone(self, _UPDATED)
                corrupt(root, mutate)
                observed = mcp_content_identity(root)["identity"]
                parsed = content_bound_restart_candidate(
                    install_record_evidence(read_record(root)),
                    observed_mcp_identity=observed,
                    client_id="codex",
                )
                self.assertEqual(parsed, self.candidate(root))
                self.assertEqual(parsed["status"], expected)

    def test_unbound_candidate_is_not_derived_from_observation_alone(
        self,
    ) -> None:
        # An unobservable MCP identity cannot bind a well-formed recorded one
        # either; that refusal is also not absence.
        root = clone(self, _UPDATED)
        candidate = content_bound_restart_candidate(
            install_state_evidence(root),
            observed_mcp_identity=None,
            client_id="codex",
        )
        self.assertEqual(candidate["status"], RESTART_EVIDENCE_UNBOUND)
        self.assertTrue(restart_evidence_withheld(candidate))


# ---------------------------------------------------------------------------
# Path 1: the MCP install-context / restart projection
# ---------------------------------------------------------------------------

class McpRestartProjectionAuthorityTests(unittest.TestCase):
    """A sibling restart row cannot re-confer what the record cannot prove."""

    def setUp(self) -> None:
        self.enterContext(
            patch.object(
                server,
                "_client_info",
                {"name": "codex-mcp-client", "title": "Codex"},
            )
        )

    def record(self, root: Path, **overrides: Any) -> Dict[str, Any]:
        """A coordinated record whose restart row attests the observed MCP content."""
        identity = mcp_content_identity(root)["identity"]
        record: Dict[str, Any] = {
            "schema_identity": SCHEMA_IDENTITY,
            "record_schema_version": RECORD_SCHEMA_VERSION,
            "state": "restart-required",
            "versions": [
                {
                    "kind": "installed_content",
                    "value": _surface_digest(root, INSTALLED_CONTENT_PATHS),
                    "state": "verified",
                    "authority": identity_contract()["installed_content"][
                        "authority"
                    ],
                    "verification": "verified",
                    "installed_identity": _surface_digest(
                        root, INSTALLED_CONTENT_PATHS
                    ),
                    "mcp_identity": identity,
                }
            ],
            "restarts": [
                {
                    "client": "codex",
                    "installed_identity": identity,
                    "installed_verification": "verified",
                    "installed_completeness": "complete",
                    "state": "required",
                    "instruction_class": "restart-client",
                    "proof_state": "unverified",
                    "status": "restart_required",
                    "reason_code": "running_content_stale",
                    "process_id": STALE_PROCESS,
                    "instance_id": STALE_INSTANCE,
                }
            ],
        }
        record.update(overrides)
        return record

    def project(self, root: Path) -> Tuple[Dict[str, Any], str]:
        """Project restart state for a genuinely fresh, verified process."""
        identity = mcp_content_identity(root)["identity"]
        with patch.object(server, "ROOT", root), patch.object(
            server,
            "_RUNNING_SERVER_FACT",
            _running(identity, FRESH_PROCESS, FRESH_INSTANCE),
        ):
            return (
                server._restart_projection(),
                "\n".join(server._install_context_lines()),
            )

    def assert_withheld(self, root: Path) -> Dict[str, Any]:
        restart, text = self.project(root)
        installed = restart["installed"]
        self.assertNotIn(installed["verification"], ("verified", "current"))
        self.assertNotIn(installed["state"], ("verified", "current"))
        self.assertNotIn(restart["status"], ("current", "no_restart_needed"))
        self.assertEqual(restart["reason_code"], "installed_content_unverified")
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertEqual(restart["activation_claim"], "none")
        self.assertNotEqual(restart["fresh_proof"]["verification"], "verified")
        # The prior process identity is evidence from the same refused record.
        self.assertIsNone(restart["fresh_proof"]["previous_instance_id"])
        self.assertNotIn("Activation: `active`", text)
        self.assertIn("Activation: `not claimed`", text)
        self.assertIn("Restart Codex.", text)
        return restart

    def test_unusable_records_cannot_strengthen_the_projection(self) -> None:
        for label, mutate in INVALID_RECORDS:
            with self.subTest(record=label):
                root = clone(self, _PRISTINE)
                record = self.record(root)
                mutate(record)
                write_record(root, record)
                self.assertNotEqual(
                    installed_content(root)["mcp_verification"], "verified"
                )
                self.assert_withheld(root)

    def test_unreadable_record_cannot_strengthen_the_projection(self) -> None:
        root = clone(self, _PRISTINE)
        (root / STATE_FILE).write_text("{not json", encoding="utf-8")
        self.assert_withheld(root)

    def test_record_naming_other_mcp_content_cannot_strengthen(self) -> None:
        # A compatible, positive record that attests a different MCP subset
        # than the one being projected proves nothing about this content: not
        # its installed verdict, and not the prior process a fresh-process
        # proof would be measured against.
        root = clone(self, _PRISTINE)
        record = self.record(root)
        installed_row(record)["mcp_identity"] = "sha256:" + "1" * 64
        write_record(root, record)
        evidence = server._install_record_evidence(root)
        self.assertIsNone(
            server._persisted_restart_baseline(
                evidence,
                "codex",
                observed_mcp_identity=mcp_content_identity(root)["identity"],
            )
        )
        restart, text = self.project(root)
        fresh = restart["fresh_proof"]
        self.assertNotEqual(restart["installed"]["verification"], "verified")
        self.assertNotIn(restart["status"], ("current", "no_restart_needed"))
        self.assertNotEqual(restart["legacy_state"], "verified")
        self.assertIsNone(fresh["previous_instance_id"])
        self.assertIsNone(fresh["previous_process_id"])
        self.assertNotEqual(fresh["verification"], "verified")
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertEqual(restart["activation_claim"], "none")
        self.assertIn("Activation: `not claimed`", text)
        self.assertIn("Restart Codex.", text)

    def assert_refused(self, root: Path) -> Dict[str, Any]:
        """The refused-evidence contract, as this projection must report it."""
        restart, text = self.project(root)
        fresh = restart["fresh_proof"]
        self.assertNotIn(restart["status"], ("current", "no_restart_needed"))
        self.assertNotEqual(restart["legacy_state"], "verified")
        self.assertIsNone(fresh["previous_instance_id"])
        self.assertIsNone(fresh["previous_process_id"])
        self.assertNotEqual(fresh["verification"], "verified")
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertEqual(restart["activation_claim"], "none")
        self.assertIn("Activation: `not claimed`", text)
        self.assertIn("Restart Codex.", text)
        return restart

    def test_refused_restart_evidence_still_fails_closed(self) -> None:
        """The classification change cannot weaken this consumer.

        This projection already refused an unusable record through its own
        record check; naming the refusal in the shared authority must leave
        every class — including the ones it did not check itself — refused.
        """
        for label, mutate, expected in REFUSED_RESTART_EVIDENCE:
            with self.subTest(record=label):
                root = clone(self, _PRISTINE)
                record = self.record(root)
                mutate(record)
                write_record(root, record)
                self.assertIsNone(
                    server._persisted_restart_baseline(
                        server._install_record_evidence(root),
                        "codex",
                        observed_mcp_identity=mcp_content_identity(root)[
                            "identity"
                        ],
                    )
                )
                self.assert_refused(root)
                # The same shared verdict this consumer's own record check
                # already acts on, asserted after the behaviour it governs.
                self.assertEqual(
                    server._persisted_restart_candidate(
                        server._install_record_evidence(root),
                        "codex",
                        mcp_content_identity(root)["identity"],
                    )["status"],
                    expected,
                )

    def test_valid_record_still_permits_the_current_activation_outcome(self) -> None:
        root = clone(self, _PRISTINE)
        write_record(root, self.record(root))
        restart, text = self.project(root)
        self.assertEqual(restart["installed"]["verification"], "verified")
        self.assertEqual(restart["status"], "current")
        self.assertEqual(restart["reason_code"], "fresh_process_current")
        self.assertTrue(restart["fresh_proof"]["new_process"])
        self.assertEqual(restart["fresh_proof"]["previous_instance_id"], STALE_INSTANCE)
        self.assertTrue(restart["activation_claim_allowed"])
        self.assertIn("Activation: `active`", text)
        self.assertNotIn("Required action:", text)

    def test_absent_record_keeps_the_observed_mcp_verdict(self) -> None:
        # No record at all is not the same evidence class as a refused one:
        # the receipt-backed install keeps its own observed verdict, and the
        # matching connected process still needs no restart.
        root = clone(self, _PRISTINE)
        (root / STATE_FILE).unlink(missing_ok=True)
        restart, _text = self.project(root)
        self.assertEqual(restart["status"], "no_restart_needed")
        self.assertEqual(restart["reason_code"], "mcp_surface_unaffected")
        self.assertFalse(restart["activation_claim_allowed"])

    def test_mcp_subset_verdict_is_not_global_content_drift(self) -> None:
        # Drift outside the MCP subset is a different authority: restarting
        # cannot repair a template, so the MCP-scoped verdict stays verified
        # while installed content reports the drift.
        root = clone(self, _PRISTINE)
        write_record(root, self.record(root))
        path = root / "templates" / "REPORT.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8"
        )
        content = installed_content(root)
        self.assertEqual(content["verification"], "dirty")
        self.assertEqual(content["mcp_verification"], "verified")
        restart, _text = self.project(root)
        self.assertEqual(restart["status"], "current")
        self.assertTrue(restart["activation_claim_allowed"])


# ---------------------------------------------------------------------------
# Path 2: the public restart-verification command
# ---------------------------------------------------------------------------

class PublicRestartVerificationAuthorityTests(unittest.TestCase):
    """Pending-restart and persisted-installed proofs need the same authority."""

    def setUp(self) -> None:
        self.enterContext(
            patch.object(
                server,
                "_client_info",
                {"name": "codex-mcp-client", "title": "Codex"},
            )
        )

    def verify(self, root: Path, **arguments: Any) -> Dict[str, Any]:
        """Run the public restart-verification command for a fresh process."""
        identity = mcp_content_identity(root)["identity"]
        with patch.object(
            server,
            "_RUNNING_SERVER_FACT",
            _running(identity, FRESH_PROCESS, FRESH_INSTANCE),
        ):
            response = server.call_tool(
                "verify_restart_state",
                {"install_root": str(root), **arguments},
            )
        structured = response["structuredContent"]
        self.assertTrue(structured["records"], msg=structured["stderr_lines"])
        record = structured["records"][0]
        record["exit_code"] = structured["exit_code"]
        return record

    def only_surface_proof(self, record: Dict[str, Any]) -> None:
        """Disarm the pending row's own claim, leaving the surface proof."""
        for item in record["restarts"]:
            item["installed_verification"] = "unverified"

    def assert_withheld(self, result: Dict[str, Any]) -> None:
        restart = result["restart_state"]
        workflow = result["workflow"]
        self.assertNotIn(
            restart["installed"]["verification"], ("verified", "current")
        )
        self.assertNotIn(restart["installed"]["state"], ("verified", "current"))
        self.assertNotIn(restart["status"], ("current", "no_restart_needed"))
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertEqual(restart["activation_claim"], "none")
        self.assertNotEqual(restart["fresh_proof"]["verification"], "verified")
        self.assertNotIn(
            workflow["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertNotEqual(workflow["outcome"]["claim"], "qualified-complete")
        self.assertFalse(workflow["outcome"]["fully_updated"])
        self.assertNotEqual(workflow["state"], "complete")
        self.assertTrue(workflow["outcome"]["restart_required"])
        self.assertEqual(
            [item["activation_claim_allowed"] for item in workflow["restarts"]],
            [False],
        )

    def test_unusable_records_cannot_strengthen_pending_restart(self) -> None:
        for label, mutate in INVALID_RECORDS:
            with self.subTest(record=label):
                root = clone(self, _UPDATED)
                corrupt(root, mutate)
                self.assert_withheld(self.verify(root))

    def test_unusable_records_cannot_strengthen_persisted_surface_proof(self) -> None:
        for label, mutate in INVALID_RECORDS:
            with self.subTest(record=label):
                root = clone(self, _UPDATED)
                record = read_record(root)
                self.only_surface_proof(record)
                mutate(record)
                write_record(root, record)
                self.assert_withheld(self.verify(root))

    def test_record_naming_other_mcp_content_cannot_strengthen(self) -> None:
        # This record is compatible and positive, but it attests other MCP
        # content: it cannot answer for what is installed here, and its restart
        # row cannot be substituted for the prior process either.
        root = clone(self, _UPDATED)
        record = read_record(root)
        installed_row(record)["mcp_identity"] = "sha256:" + "1" * 64
        write_record(root, record)
        self.assertIsNone(
            verify_restart_state._pending_restart(
                record, "codex", mcp_content_identity(root)["identity"]
            )["row"]
        )
        result = self.verify(root)
        restart = result["restart_state"]
        workflow = result["workflow"]
        fresh = restart["fresh_proof"]
        self.assertNotIn(
            restart["installed"]["verification"], ("verified", "current")
        )
        self.assertNotIn(restart["status"], ("current", "no_restart_needed"))
        self.assertIsNone(fresh["previous_instance_id"])
        self.assertIsNone(fresh["previous_process_id"])
        self.assertNotEqual(fresh["verification"], "verified")
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertEqual(
            [item["previous_instance_id"] for item in workflow["restarts"]],
            [None],
        )
        self.assertNotIn(
            workflow["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertNotEqual(workflow["outcome"]["claim"], "qualified-complete")
        self.assertNotEqual(workflow["state"], "complete")

    def test_legacy_digest_observation_cannot_poison_current_record(self) -> None:
        """An old in-process verifier must leave newer install proof intact."""
        root = clone(self, _UPDATED)
        state_path = root / STATE_FILE
        original = state_path.read_bytes()
        legacy_identity = "sha256:" + "4" * 64
        legacy_installed = {
            "mcp_identity": legacy_identity,
            "mcp_state": "dirty",
            "mcp_verification": "dirty",
            "mcp_completeness": "complete",
        }
        legacy_observation = {
            "identity": legacy_identity,
            "state": "unverified",
            "verification": "unverified",
            "completeness": "complete",
            "authority": "installed-or-materialized-content",
        }
        with (
            patch.object(
                server,
                "_RUNNING_SERVER_FACT",
                _running(legacy_identity, STALE_PROCESS, STALE_INSTANCE),
            ),
            patch.object(
                verify_restart_state,
                "mcp_content_identity",
                return_value=legacy_observation,
            ),
            patch.object(
                verify_restart_state,
                "installed_content",
                return_value=legacy_installed,
            ),
        ):
            response = server.call_tool(
                "verify_restart_state", {"install_root": str(root)}
            )

        result = response["structuredContent"]["records"][0]
        self.assertEqual(
            result["restart_state"]["reason_code"],
            "installed_content_unverified",
        )
        self.assertFalse(
            result["restart_state"]["activation_claim_allowed"]
        )
        self.assertEqual(state_path.read_bytes(), original)

    def test_refused_restart_evidence_still_fails_closed(self) -> None:
        """The classification change cannot weaken this consumer either.

        Whatever the shared authority now calls the refusal, this command must
        expose no prior process, prove no fresh process, and close no outcome.
        """
        for label, mutate, expected in REFUSED_RESTART_EVIDENCE:
            with self.subTest(record=label):
                root = clone(self, _UPDATED)
                corrupt(root, mutate)
                record = read_record(root)
                result = self.verify(root)
                restart = result["restart_state"]
                workflow = result["workflow"]
                fresh = restart["fresh_proof"]
                self.assertNotIn(
                    restart["status"], ("current", "no_restart_needed")
                )
                self.assertIsNone(fresh["previous_instance_id"])
                self.assertIsNone(fresh["previous_process_id"])
                self.assertNotEqual(fresh["verification"], "verified")
                self.assertFalse(restart["activation_claim_allowed"])
                self.assertEqual(restart["activation_claim"], "none")
                self.assertEqual(
                    [
                        item["previous_instance_id"]
                        for item in workflow["restarts"]
                    ],
                    [None],
                )
                self.assertEqual(workflow["state"], "restart-required")
                self.assertNotIn(
                    workflow["outcome"]["status"],
                    ("complete", "complete-qualified"),
                )
                self.assertNotEqual(
                    workflow["outcome"]["claim"], "qualified-complete"
                )
                self.assertTrue(workflow["outcome"]["restart_required"])
                # The same shared verdict this command's own record check
                # already acts on, asserted after the behaviour it governs.
                self.assertEqual(
                    verify_restart_state._pending_restart(
                        record,
                        "codex",
                        mcp_content_identity(root)["identity"],
                    )["status"],
                    expected,
                )

    def test_valid_pending_row_still_permits_activation_and_completion(self) -> None:
        root = clone(self, _UPDATED)
        result = self.verify(root)
        restart = result["restart_state"]
        workflow = result["workflow"]
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(restart["installed"]["verification"], "verified")
        self.assertEqual(restart["status"], "current")
        self.assertEqual(restart["reason_code"], "fresh_process_current")
        self.assertTrue(restart["activation_claim_allowed"])
        self.assertEqual(restart["fresh_proof"]["previous_instance_id"], STALE_INSTANCE)
        self.assertIn(
            workflow["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertEqual(workflow["state"], "complete")
        self.assertFalse(workflow["outcome"]["restart_required"])

    def test_valid_surface_proof_still_permits_activation(self) -> None:
        root = clone(self, _UPDATED)
        record = read_record(root)
        self.only_surface_proof(record)
        write_record(root, record)
        result = self.verify(root)
        restart = result["restart_state"]
        self.assertEqual(restart["installed"]["verification"], "verified")
        self.assertEqual(restart["status"], "current")
        self.assertTrue(restart["activation_claim_allowed"])

    def test_stale_process_still_reaches_restart_required(self) -> None:
        # The safe outcome the pending record exists to produce is unchanged.
        root = clone(self, _UPDATED)
        with patch.object(
            server,
            "_RUNNING_SERVER_FACT",
            _running("sha256:" + "2" * 64, STALE_PROCESS, STALE_INSTANCE),
        ):
            response = server.call_tool(
                "verify_restart_state", {"install_root": str(root)}
            )
        restart = response["structuredContent"]["records"][0]["restart_state"]
        self.assertEqual(restart["status"], "restart_required")
        self.assertEqual(restart["reason_code"], "running_content_stale")
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertEqual(restart["instruction"]["action"], "Restart Codex.")

    def test_cli_and_mcp_surfaces_reach_the_same_verified_record(self) -> None:
        """The repaired gate is one contract, not two implementations."""
        through_mcp = self.verify(clone(self, _UPDATED))
        through_mcp.pop("exit_code")

        root = clone(self, _UPDATED)
        identity = mcp_content_identity(root)["identity"]
        from cli import host_capability
        from cli.restart_state import RUNNING_SERVER_ENV

        env = dict(os.environ)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                host_capability.CLIENT_ENV: "codex-mcp-client",
                host_capability.CLIENT_TITLE_ENV: "Codex",
                RUNNING_SERVER_ENV: json.dumps(
                    _running(identity, FRESH_PROCESS, FRESH_INSTANCE),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(root / "bin" / "cartopian"),
                "verify-restart-state",
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        lines: List[Dict[str, Any]] = [
            json.loads(line) for line in proc.stdout.splitlines() if line.strip()
        ]
        self.assertEqual(lines[0], through_mcp)


# ---------------------------------------------------------------------------
# Path 3: the installer workflow's own prior-restart reader
# ---------------------------------------------------------------------------

class InstallerWorkflowRestartAuthorityTests(unittest.TestCase):
    """Planning may carry forward only a content-bound restart candidate.

    The planner turns the persisted restart row into the prior process, and the
    applied result measures fresh-process proof against it. Reading that row on
    record positivity alone let a record naming other MCP content donate a
    prior instance, so a genuinely distinct new process reported verified fresh
    proof, current state, and activation for content the record never attested.
    """

    def run_update(
        self, install_root: Path, client_home: Path
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Plan and apply an update against a genuinely fresh, verified process.

        The isolated clone moved the install and home paths, so the registration
        surfaces are authorized here; nothing outside the clone is written, and
        the run can reach a complete outcome when the restart facts permit one.
        """
        identity = mcp_content_identity(install_root)["identity"]
        plan = plan_workflow(
            source_root=REPO_ROOT,
            install_root=install_root,
            operation="update",
            client_home=client_home,
            clients=("codex",),
            decisions={
                "client-registrations": "accept",
                "client-configuration": "accept",
            },
            running_server_fact=_running(
                identity, FRESH_PROCESS, FRESH_INSTANCE
            ),
            client_context=normalize_client_context("codex-mcp-client"),
        )
        return plan, apply_workflow(plan)

    def assert_refused(
        self,
        plan: Dict[str, Any],
        result: Dict[str, Any],
        *,
        classification: str,
    ) -> None:
        """Assert the whole refused-evidence contract for one installer run.

        Planning must carry the refusal forward as a restart-relevant fact
        while exposing no prior process, and application must refuse every
        successful claim that would rest on a fresh-process proof it never had.
        """
        self.assertTrue(plan["internal"]["mcp_affecting_change"])
        self.assertIsNone(plan["internal"]["prior_process"])
        self.assertEqual(len(result["restarts"]), 1)
        restart = result["restarts"][0]
        self.assertIsNone(restart["previous_instance_id"])
        self.assertIsNone(restart["previous_process_id"])
        self.assertNotEqual(restart["proof_state"], "verified")
        self.assertNotEqual(restart["fresh_proof"]["verification"], "verified")
        self.assertIsNone(restart["fresh_proof"]["previous_instance_id"])
        self.assertNotIn(restart["status"], ("current", "no_restart_needed"))
        self.assertIn(
            restart["status"],
            (
                "restart_required",
                "restart_instructed",
                "verification_pending",
                "unverified",
            ),
        )
        self.assertFalse(restart["activation_claim_allowed"])
        projection = result["internal"]["restart_projection"]
        self.assertEqual(projection["activation_claim"], "none")
        self.assertFalse(projection["activation_claim_allowed"])
        self.assertEqual(result["state"], "restart-required")
        self.assertNotIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertNotEqual(result["outcome"]["claim"], "qualified-complete")
        self.assertFalse(result["outcome"]["fully_updated"])
        self.assertTrue(result["outcome"]["restart_required"])
        # Asserted last, so a regression that collapses the class shows up as
        # the fail-open behaviour above rather than only as a changed label.
        self.assertEqual(plan["internal"]["restart_evidence"], classification)
        self.assertNotEqual(
            plan["internal"]["restart_evidence"], RESTART_EVIDENCE_ABSENT
        )

    def test_refused_restart_evidence_is_restart_relevant_in_planning(
        self,
    ) -> None:
        """The installer's own path, for every refused evidence class.

        A malformed recorded MCP identity makes the record unusable, so the
        shared authority withholds it. Planning that treated only ``unbound``
        as restart-relevant read the collapsed ``absent`` verdict as though no
        persisted evidence existed and reported ``no_restart_needed``, workflow
        state ``complete``, and a complete-qualified outcome for content whose
        recorded identity it had just refused.
        """
        for label, mutate, expected in REFUSED_RESTART_EVIDENCE:
            with self.subTest(record=label):
                install_root, client_home = clone_workflow(self)
                corrupt(install_root, mutate)
                evidence = install_workflow._prior_restart(
                    install_root, "codex"
                )
                self.assertIsNone(evidence["row"])
                plan, result = self.run_update(install_root, client_home)
                self.assert_refused(plan, result, classification=expected)

    def test_absent_record_keeps_its_benign_installer_behavior(self) -> None:
        """The positive control the distinction exists to protect.

        No record at all is a different evidence class from a refused one: the
        MCP surface is genuinely unchanged, so the run stays benign and still
        reaches a complete outcome.
        """
        install_root, client_home = clone_workflow(self)
        (install_root / STATE_FILE).unlink()
        evidence = install_workflow._prior_restart(install_root, "codex")
        self.assertEqual(evidence["status"], RESTART_EVIDENCE_ABSENT)
        self.assertIsNone(evidence["row"])
        plan, result = self.run_update(install_root, client_home)
        self.assertEqual(
            plan["internal"]["restart_evidence"], RESTART_EVIDENCE_ABSENT
        )
        self.assertFalse(plan["internal"]["mcp_affecting_change"])
        self.assertIsNone(plan["internal"]["prior_process"])
        restart = result["restarts"][0]
        self.assertEqual(restart["status"], "no_restart_needed")
        self.assertEqual(restart["reason_code"], "mcp_surface_unaffected")
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertFalse(result["outcome"]["restart_required"])

    def test_absent_candidate_keeps_its_benign_installer_behavior(
        self,
    ) -> None:
        # A compatible record that persisted no restart candidate — the steady
        # state after a restart was proven — is absence, not refusal.
        install_root, client_home = clone_workflow(self)
        corrupt(install_root, _empty_restarts)
        evidence = install_workflow._prior_restart(install_root, "codex")
        self.assertEqual(evidence["status"], RESTART_EVIDENCE_ABSENT)
        plan, result = self.run_update(install_root, client_home)
        self.assertFalse(plan["internal"]["mcp_affecting_change"])
        self.assertEqual(
            result["restarts"][0]["status"], "no_restart_needed"
        )
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertFalse(result["outcome"]["restart_required"])

    def test_record_naming_other_mcp_content_cannot_attribute_prior_process(
        self,
    ) -> None:
        install_root, client_home = clone_workflow(self)
        record = read_record(install_root)
        installed_row(record)["mcp_identity"] = "sha256:" + "1" * 64
        write_record(install_root, record)
        self.assertIsNone(
            install_workflow._prior_restart(install_root, "codex")["row"]
        )

        plan, result = self.run_update(install_root, client_home)
        self.assertIsNone(plan["internal"]["prior_process"])
        self.assertEqual(len(result["restarts"]), 1)
        restart = result["restarts"][0]
        self.assertIsNone(restart["previous_instance_id"])
        self.assertIsNone(restart["previous_process_id"])
        self.assertNotEqual(restart["proof_state"], "verified")
        self.assertNotEqual(
            restart["fresh_proof"]["verification"], "verified"
        )
        self.assertNotIn(
            restart["status"], ("current", "no_restart_needed")
        )
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertNotIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertNotEqual(result["outcome"]["claim"], "qualified-complete")
        self.assertFalse(result["outcome"]["fully_updated"])
        self.assertNotEqual(result["state"], "complete")
        self.assertTrue(result["outcome"]["restart_required"])

    def test_valid_record_still_proves_the_fresh_process(self) -> None:
        install_root, client_home = clone_workflow(self)
        self.assertEqual(
            install_workflow._prior_restart(install_root, "codex")["status"],
            RESTART_EVIDENCE_BOUND,
        )
        plan, result = self.run_update(install_root, client_home)
        self.assertEqual(
            plan["internal"]["restart_evidence"], RESTART_EVIDENCE_BOUND
        )
        self.assertEqual(
            plan["internal"]["prior_process"],
            {"process_id": STALE_PROCESS, "instance_id": STALE_INSTANCE},
        )
        restart = result["restarts"][0]
        self.assertEqual(restart["status"], "current")
        self.assertEqual(restart["reason_code"], "fresh_process_current")
        self.assertEqual(restart["previous_instance_id"], STALE_INSTANCE)
        self.assertEqual(restart["proof_state"], "verified")
        self.assertTrue(restart["fresh_proof"]["new_process"])
        self.assertTrue(restart["activation_claim_allowed"])
        self.assertIn(
            result["outcome"]["status"], ("complete", "complete-qualified")
        )
        self.assertFalse(result["outcome"]["restart_required"])

    def test_stale_process_still_reaches_restart_required(self) -> None:
        # The bound record's own safe outcome is unchanged: old loaded content
        # on the same process stays restart-required with one instruction.
        install_root, client_home = clone_workflow(self)
        result = apply_workflow(
            plan_workflow(
                source_root=REPO_ROOT,
                install_root=install_root,
                operation="update",
                client_home=client_home,
                clients=("codex",),
                running_server_fact=_running(
                    "sha256:" + "2" * 64, STALE_PROCESS, STALE_INSTANCE
                ),
                client_context=normalize_client_context("codex-mcp-client"),
            )
        )
        restart = result["restarts"][0]
        self.assertEqual(restart["status"], "restart_required")
        self.assertEqual(restart["reason_code"], "running_content_stale")
        self.assertFalse(restart["activation_claim_allowed"])
        self.assertEqual(restart["instruction"]["action"], "Restart Codex.")


if __name__ == "__main__":
    unittest.main()
