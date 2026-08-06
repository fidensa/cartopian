"""Install-version projection through the CLI and the MCP surface.

The primary end-user install is copy mode: ``scripts/install.py`` copies the
tool-shipped surfaces into ``~/.cartopian`` and records the resolved release
ref at ``<root>/VERSION`` (``protocol/INSTALL_VERIFICATION.md`` section 7).
Such an install carries no ``.git``, so a runtime identity resolver that reads
only ``git rev-parse`` and a ``RELEASE_VERSION`` marker that no installer ever
writes collapses every peer identity to unknown/unverified — release version,
installed revision, installed verification, and, by derivation, the connected
process's running state.

These fixtures pin the repaired projection at both boundaries (CLI helpers and
the MCP prelude), the truthful-unknown negatives that must survive the repair,
and fresh-process evidence from a genuinely new server process started from an
installed copy.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli.version_identities import (  # noqa: E402
    connected_running_server,
    installed_content,
    mcp_content_identity,
    release_version,
    running_server,
    set_connected_running_server,
    version_identities,
)
from mcp_server import server  # noqa: E402

FIXTURE_REF = "v9.9.9"

_TMP: Optional[tempfile.TemporaryDirectory] = None
_PRISTINE: Optional[Path] = None


def setUpModule() -> None:
    """Build one real copy install; tests clone it per scenario."""
    global _TMP, _PRISTINE
    from tests._install_fixture import install_copy_fixture

    _TMP = tempfile.TemporaryDirectory()
    _PRISTINE = Path(_TMP.name) / "pristine"
    install_copy_fixture(REPO_ROOT, _PRISTINE)


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def copy_install(case: unittest.TestCase, *, ref: Optional[str] = FIXTURE_REF) -> Path:
    """Return an isolated copy-mode install root for one test."""
    tmp = tempfile.TemporaryDirectory()
    case.addCleanup(tmp.cleanup)
    root = Path(tmp.name) / ".cartopian"
    assert _PRISTINE is not None
    shutil.copytree(_PRISTINE, root)
    if ref is not None:
        (root / "VERSION").write_text(f"{ref}\n", encoding="utf-8")
    return root


def running_fact(root: Path, *, process_id: int = 424242) -> Dict[str, Any]:
    """A connected-process fact for ``root`` that is not this test process."""
    return running_server(
        installed_content(root),
        process_id=process_id,
        instance_id=f"process:{process_id}:started-ns:1",
    )


def shipped_targets() -> tuple:
    """The installer's own install-root-relative shipped surface set."""
    from cli.install_workflow import TOOL_SHIPPED

    return tuple(target for target, _source in TOOL_SHIPPED)


def recorded_row(root: Path, **overrides: Any) -> Dict[str, Any]:
    """The installed-content version row a coordinated install records."""
    from cli.config_schema import identity_contract
    from cli.install_workflow import MCP_TARGETS, _surface_digest

    row = {
        "kind": "installed_content",
        "value": _surface_digest(root, shipped_targets()),
        "state": "verified",
        "authority": identity_contract()["installed_content"]["authority"],
        "verification": "verified",
        "installed_identity": _surface_digest(root, shipped_targets()),
        "mcp_identity": _surface_digest(root, MCP_TARGETS),
    }
    row.update(overrides)
    return row


def installed_content_states() -> tuple:
    """The contract's closed state vocabulary for the installed identity."""
    from cli.install_state import contract_projection

    return tuple(
        contract_projection()["vocabularies"]["version_states"]["installed_content"]
    )


def verification_states() -> tuple:
    """The contract's closed verification vocabulary."""
    from cli.install_state import contract_projection

    return tuple(contract_projection()["vocabularies"]["verification_states"])


def write_state(root: Path, rows: List[Dict[str, Any]], **overrides: Any) -> None:
    """Write an install-state record carrying ``rows`` to ``root``."""
    from cli.install_state import RECORD_SCHEMA_VERSION, SCHEMA_IDENTITY

    record: Dict[str, Any] = {
        "schema_identity": SCHEMA_IDENTITY,
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "state": "complete",
        "versions": rows,
    }
    record.update(overrides)
    (root / "install-update-state.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# CLI-side identity resolution
# ---------------------------------------------------------------------------

class TestCopyInstallIdentities(unittest.TestCase):
    """A recorded copy install projects its facts instead of unknowns."""

    def test_recorded_release_marker_is_a_release_authority(self) -> None:
        root = copy_install(self)
        record = release_version(root)
        self.assertEqual(record["value"], FIXTURE_REF)
        self.assertEqual(record["state"], "known")
        self.assertEqual(record["verification"], "verified")
        self.assertEqual(record["attribution"], "installed-release-marker")

    def test_maintainer_marker_outranks_the_installed_marker(self) -> None:
        root = copy_install(self)
        (root / "RELEASE_VERSION").write_text("v1.2.4\n", encoding="utf-8")
        record = release_version(root)
        self.assertEqual(record["value"], "v1.2.4")
        self.assertEqual(record["attribution"], "release-metadata")

    def test_installed_content_reports_revision_and_verification(self) -> None:
        root = copy_install(self)
        content = installed_content(root)
        self.assertEqual(content["materialization"], "copy")
        self.assertEqual(content["recorded_ref"], FIXTURE_REF)
        self.assertIsNotNone(content["revision"])
        self.assertTrue(str(content["revision"]).startswith("sha256:"))
        self.assertEqual(content["revision_attribution"], "installed-content-digest")
        self.assertEqual(content["verification"], "verified")
        self.assertEqual(content["state"], "verified")
        self.assertEqual(content["verification_evidence"], "recorded-install-content")
        self.assertEqual(content["mcp_completeness"], "complete")
        self.assertEqual(content["mcp_verification"], "verified")

    def test_connected_process_state_is_current(self) -> None:
        root = copy_install(self)
        records = version_identities(
            root,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        self.assertEqual(records["release_version"]["value"], FIXTURE_REF)
        self.assertEqual(records["running_server"]["state"], "current")
        self.assertEqual(
            records["running_server"]["loaded_content"]["mcp_identity"],
            records["installed_content"]["mcp_identity"],
        )

    def test_release_and_revision_remain_distinct_facts(self) -> None:
        root = copy_install(self)
        records = version_identities(root)
        self.assertNotEqual(
            records["release_version"]["value"],
            records["installed_content"]["revision"],
        )

    def test_installed_cli_version_flag_reports_the_recorded_release(self) -> None:
        root = copy_install(self)
        proc = subprocess.run(
            [sys.executable, str(root / "bin" / "cartopian"), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"cartopian {FIXTURE_REF}")


class TestInstalledSurfaceParity(unittest.TestCase):
    """The runtime identifies the same surface set the installer writes."""

    def test_installed_content_paths_match_the_installer_targets(self) -> None:
        from cli.install_workflow import INSTALLED_TARGETS, TOOL_SHIPPED
        from cli.version_identities import INSTALLED_CONTENT_PATHS

        self.assertEqual(
            INSTALLED_TARGETS,
            tuple(target for target, _source in TOOL_SHIPPED),
        )
        # Order matters: both sides digest the surface set in sequence, so a
        # reordering would silently make every recorded identity mismatch.
        self.assertEqual(INSTALLED_CONTENT_PATHS, INSTALLED_TARGETS)

    def test_recorded_identity_covers_the_reported_revision_surface(self) -> None:
        """The installer records the identity the runtime reports as revision.

        A record that attested only the MCP subset could not contradict
        non-MCP drift, so the runtime would keep reporting a changed revision
        as verified.
        """
        from cli.install_workflow import _version_records

        root = copy_install(self)
        rows = _version_records(
            REPO_ROOT,
            root,
            "source-identity",
            "current",
            release_ref=FIXTURE_REF,
            running_fact=running_fact(root),
        )
        row = next(item for item in rows if item["kind"] == "installed_content")
        content = installed_content(root)
        self.assertEqual(row["installed_identity"], content["revision"])
        self.assertEqual(row["mcp_identity"], content["mcp_identity"])
        self.assertNotEqual(row["installed_identity"], row["mcp_identity"])


class TestCoordinatedInstallEvidence(unittest.TestCase):
    """The record a real coordinated install writes verifies its own content.

    No ``VERSION`` receipt exists on this path, so the state record is the only
    evidence available: writer and reader must agree byte for byte.
    """

    def install(self) -> Path:
        from cli.install_workflow import apply_workflow, plan_workflow

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        install_root = root / "install"
        client_home = root / "home"
        client_home.mkdir()
        apply_workflow(
            plan_workflow(
                source_root=REPO_ROOT,
                install_root=install_root,
                operation="fresh-install",
                client_home=client_home,
                clients=("codex",),
            )
        )
        return install_root

    def test_recorded_state_verifies_the_installed_content_it_wrote(self) -> None:
        install_root = self.install()
        record = json.loads(
            (install_root / "install-update-state.json").read_text(
                encoding="utf-8"
            )
        )
        row = next(
            item
            for item in record["versions"]
            if item["kind"] == "installed_content"
        )
        content = installed_content(install_root)
        self.assertFalse((install_root / "VERSION").exists())
        self.assertEqual(row["installed_identity"], content["revision"])
        self.assertEqual(content["verification"], "verified")
        self.assertEqual(content["verification_evidence"], "install-state-record")

    def test_non_mcp_drift_against_a_real_record_is_dirty(self) -> None:
        install_root = self.install()
        path = install_root / "templates" / "REPORT.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8"
        )
        content = installed_content(install_root)
        self.assertEqual(content["verification"], "dirty")
        self.assertEqual(content["verification_evidence"], "install-state-mismatch")


class TestInstallStateEvidenceFailsClosed(unittest.TestCase):
    """Only a compatible, unique, well-formed record can verify content.

    Each case observes content that genuinely matches what the record claims,
    so nothing but record validation itself can keep the projection truthful.
    """

    def assert_fails_closed(self, root: Path) -> Dict[str, Any]:
        records = version_identities(
            root,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        content = records["installed_content"]
        self.assertNotIn(content["verification"], ("verified", "current"))
        self.assertNotIn(content["state"], ("verified", "current"))
        self.assertEqual(content["verification_evidence"], "install-state-unusable")
        self.assertNotIn(content["mcp_verification"], ("verified", "current"))
        self.assertNotIn(content["mcp_state"], ("verified", "current"))
        self.assertNotEqual(records["running_server"]["state"], "current")
        return content

    def test_future_record_schema_cannot_confer_verification(self) -> None:
        root = copy_install(self)
        write_state(
            root,
            [recorded_row(root)],
            record_schema_version=2,
        )
        self.assert_fails_closed(root)

    def test_unsupported_schema_identity_cannot_confer_verification(self) -> None:
        root = copy_install(self)
        write_state(
            root,
            [recorded_row(root)],
            schema_identity="cartopian-install-update-state-v2",
        )
        self.assert_fails_closed(root)

    def test_missing_schema_fields_cannot_confer_verification(self) -> None:
        root = copy_install(self)
        (root / "install-update-state.json").write_text(
            json.dumps({"versions": [recorded_row(root)]}), encoding="utf-8"
        )
        self.assert_fails_closed(root)

    def test_duplicate_installed_content_rows_fail_closed(self) -> None:
        root = copy_install(self)
        write_state(root, [recorded_row(root), recorded_row(root)])
        self.assert_fails_closed(root)

    def test_missing_installed_content_row_fails_closed(self) -> None:
        root = copy_install(self)
        write_state(root, [{"kind": "release_version", "value": FIXTURE_REF}])
        self.assert_fails_closed(root)

    def test_partial_digest_grammar_fails_closed(self) -> None:
        root = copy_install(self)
        identity = installed_content(root)["revision"]
        write_state(
            root,
            [recorded_row(root, installed_identity=str(identity)[: len("sha256:") + 8])],
        )
        self.assert_fails_closed(root)

    def test_non_digest_identity_fails_closed(self) -> None:
        root = copy_install(self)
        write_state(root, [recorded_row(root, installed_identity="sha256:not-a-digest")])
        self.assert_fails_closed(root)

    def test_row_without_an_installed_identity_fails_closed(self) -> None:
        # The pre-repair record shape attested only the MCP subset; it cannot
        # support a claim about the whole shipped surface set.
        root = copy_install(self)
        row = recorded_row(root)
        row.pop("installed_identity")
        write_state(root, [row])
        self.assert_fails_closed(root)

    def test_substituted_authority_fails_closed(self) -> None:
        root = copy_install(self)
        write_state(root, [recorded_row(root, authority="maintainer-release-metadata")])
        self.assert_fails_closed(root)

    def test_versions_that_are_not_a_list_fail_closed(self) -> None:
        root = copy_install(self)
        (root / "install-update-state.json").write_text(
            json.dumps(
                {
                    "schema_identity": "cartopian-install-update-state-v1",
                    "record_schema_version": 1,
                    "versions": {"installed_content": recorded_row(root)},
                }
            ),
            encoding="utf-8",
        )
        self.assert_fails_closed(root)

    def test_unreadable_record_fails_closed(self) -> None:
        root = copy_install(self)
        (root / "install-update-state.json").write_text("{not json", encoding="utf-8")
        self.assert_fails_closed(root)

    def test_float_record_schema_version_is_not_the_supported_integer(self) -> None:
        # ``1.0 == 1`` in Python, but the schema declares an integer version;
        # a float is not a version a supported adapter can have written.
        root = copy_install(self)
        write_state(root, [recorded_row(root)], record_schema_version=1.0)
        self.assert_fails_closed(root)

    def test_boolean_record_schema_version_fails_closed(self) -> None:
        root = copy_install(self)
        write_state(root, [recorded_row(root)], record_schema_version=True)
        self.assert_fails_closed(root)

    def test_string_record_schema_version_fails_closed(self) -> None:
        root = copy_install(self)
        write_state(root, [recorded_row(root)], record_schema_version="1")
        self.assert_fails_closed(root)

    def test_row_state_outside_the_closed_vocabulary_fails_closed(self) -> None:
        root = copy_install(self)
        for state in (True, False, None, 1, "", "yes", "current", ["verified"]):
            with self.subTest(state=state):
                write_state(root, [recorded_row(root, state=state)])
                self.assert_fails_closed(root)

    def test_row_verification_outside_the_closed_vocabulary_fails_closed(self) -> None:
        root = copy_install(self)
        for verification in (True, False, None, 1, "", "ok", "current", ["verified"]):
            with self.subTest(verification=verification):
                write_state(root, [recorded_row(root, verification=verification)])
                self.assert_fails_closed(root)

    def test_only_the_positive_row_semantics_confer_verification(self) -> None:
        # Sweep the contract's own closed vocabularies: exactly one pair says
        # the recorded content was observed and proven.
        root = copy_install(self)
        for state in installed_content_states():
            for verification in verification_states():
                with self.subTest(state=state, verification=verification):
                    write_state(
                        root,
                        [recorded_row(root, state=state, verification=verification)],
                    )
                    if state == "verified" and verification == "verified":
                        content = installed_content(root)
                        self.assertEqual(content["verification"], "verified")
                        self.assertEqual(
                            content["verification_evidence"], "install-state-record"
                        )
                    else:
                        self.assert_fails_closed(root)

    def test_positive_row_without_an_identity_value_fails_closed(self) -> None:
        # A row claiming a verified identity must carry the identity it claims.
        root = copy_install(self)
        write_state(root, [recorded_row(root, value=None)])
        self.assert_fails_closed(root)

    def test_unusable_row_does_not_fall_back_to_the_version_receipt(self) -> None:
        # This root carries a well-formed installer receipt, which is the
        # weaker evidence class an ordinary install verifies with. A present
        # but unusable record must not be answered by substituting it.
        root = copy_install(self)
        write_state(root, [recorded_row(root, verification="unverified")])
        content = self.assert_fails_closed(root)
        self.assertEqual(content["recorded_ref"], FIXTURE_REF)
        self.assertNotEqual(
            content["verification_evidence"], "recorded-install-content"
        )

    def test_a_valid_record_still_verifies_matching_content(self) -> None:
        root = copy_install(self)
        write_state(root, [recorded_row(root)])
        content = installed_content(root)
        self.assertEqual(content["verification"], "verified")
        self.assertEqual(content["verification_evidence"], "install-state-record")


class TestVerificationCoversTheReportedSurface(unittest.TestCase):
    """Drift anywhere in the reported surface set is reported truthfully."""

    def drifted(self, relative: str) -> Dict[str, Any]:
        root = copy_install(self)
        write_state(root, [recorded_row(root)])
        before = installed_content(root)
        self.assertEqual(before["verification"], "verified")
        path = root / relative
        path.write_text(
            path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8"
        )
        after = installed_content(root)
        self.assertNotEqual(after["revision"], before["revision"])
        return after

    def test_non_mcp_drift_after_recording_state_is_dirty(self) -> None:
        content = self.drifted("templates/REPORT.md")
        self.assertEqual(content["verification"], "dirty")
        self.assertEqual(content["state"], "dirty")
        self.assertEqual(content["verification_evidence"], "install-state-mismatch")
        # The narrower MCP claim is still its own fact and stays truthful.
        self.assertEqual(content["mcp_verification"], "verified")

    def test_mcp_drift_after_recording_state_is_dirty(self) -> None:
        content = self.drifted("mcp_server/server.py")
        self.assertEqual(content["verification"], "dirty")
        self.assertEqual(content["mcp_verification"], "dirty")
        self.assertEqual(content["mcp_state"], "dirty")

    def test_non_mcp_drift_reaches_the_running_projection(self) -> None:
        # Restarting the server cannot repair a drifted template, so the
        # MCP-scoped restart facts stay verified while the connected process
        # still carries the truthful installed-content verdict.
        root = copy_install(self)
        write_state(root, [recorded_row(root)])
        path = root / "templates" / "REPORT.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\ndrift\n", encoding="utf-8"
        )
        records = version_identities(
            root,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        self.assertEqual(records["installed_content"]["verification"], "dirty")
        self.assertEqual(
            records["running_server"]["loaded_content"]["verification"], "dirty"
        )
        self.assertEqual(
            records["running_server"]["loaded_content"]["mcp_verification"],
            "verified",
        )


class TestInstallReceiptGrammar(unittest.TestCase):
    """Only a ref the installer can have written carries provenance."""

    def assert_no_provenance(self, root: Path) -> None:
        records = version_identities(
            root,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        content = records["installed_content"]
        self.assertIsNone(records["release_version"]["value"])
        self.assertEqual(records["release_version"]["state"], "unknown")
        self.assertIsNone(content["recorded_ref"])
        self.assertEqual(content["verification"], "unverified")
        self.assertEqual(content["state"], "unverified")
        self.assertEqual(
            content["verification_evidence"], "malformed-install-receipt"
        )
        self.assertNotEqual(records["running_server"]["state"], "current")

    def test_single_token_non_ref_confers_no_provenance(self) -> None:
        self.assert_no_provenance(copy_install(self, ref="not-a-ref"))

    def test_commit_id_is_not_an_installer_receipt(self) -> None:
        self.assert_no_provenance(copy_install(self, ref="a" * 40))

    def test_arbitrary_branch_name_is_not_an_installer_receipt(self) -> None:
        self.assert_no_provenance(copy_install(self, ref="feature/x"))

    def test_release_tag_and_main_remain_supported_receipts(self) -> None:
        for ref in ("v9.9.9", "v2.0.0-rc.1", "main"):
            with self.subTest(ref=ref):
                root = copy_install(self, ref=ref)
                content = installed_content(root)
                self.assertEqual(content["recorded_ref"], ref)
                self.assertEqual(content["verification"], "verified")
                self.assertEqual(
                    content["verification_evidence"], "recorded-install-content"
                )


class TestWithheldReleaseClaimNamesItsCause(unittest.TestCase):
    """A withheld release claim reports the ref that caused it to be withheld.

    Withholding is correct, but a bare ``unknown`` is indistinguishable from a
    defect: an operator installing from a branch has no way to tell an intended
    fail-closed refusal from a broken resolver without reading this module. The
    observed ref is provenance for the refusal and never becomes a claim.
    """

    def test_branch_receipt_is_named_without_becoming_a_claim(self) -> None:
        record = release_version(copy_install(self, ref="main"))
        self.assertIsNone(record["value"])
        self.assertEqual(record["state"], "unknown")
        self.assertEqual(record["observed_ref"], "main")
        self.assertEqual(record["observed_ref_state"], "branch-ref")

    def test_non_release_ref_is_named_without_becoming_a_claim(self) -> None:
        record = release_version(copy_install(self, ref="local-writer-fix"))
        self.assertIsNone(record["value"])
        self.assertEqual(record["observed_ref"], "local-writer-fix")
        self.assertEqual(record["observed_ref_state"], "non-release-ref")

    def test_malformed_marker_reports_no_ref(self) -> None:
        root = copy_install(self, ref="v9.9.9")
        (root / "VERSION").write_text("two tokens\n", encoding="utf-8")
        record = release_version(root)
        self.assertIsNone(record["value"])
        self.assertIsNone(record["observed_ref"])
        self.assertEqual(record["observed_ref_state"], "malformed")

    def test_absent_marker_reports_no_ref(self) -> None:
        root = copy_install(self, ref="v9.9.9")
        (root / "VERSION").unlink()
        record = release_version(root)
        self.assertIsNone(record["value"])
        self.assertIsNone(record["observed_ref"])
        self.assertEqual(record["observed_ref_state"], "absent")

    def test_release_tag_reports_itself_as_the_observed_ref(self) -> None:
        record = release_version(copy_install(self, ref=FIXTURE_REF))
        self.assertEqual(record["value"], FIXTURE_REF)
        self.assertEqual(record["observed_ref"], FIXTURE_REF)
        self.assertEqual(record["observed_ref_state"], "release-tag")

    def test_installed_cli_version_flag_names_a_non_release_ref(self) -> None:
        root = copy_install(self, ref="local-writer-fix")
        proc = subprocess.run(
            [sys.executable, str(root / "bin" / "cartopian"), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(
            proc.stdout.strip(),
            "cartopian unknown (installed from ref local-writer-fix)",
        )


class TestRecordedClaimMatchesReaderGrammar(unittest.TestCase):
    """The install record may not assert a release the reader will refuse.

    ``_version_records`` writes the ``release_version`` row and
    ``release_version`` reads it back. When the writer accepts any non-empty
    ref and the reader accepts only release tags, a branch install persists a
    ``known``/``verified`` row that every reader then reports as unknown — the
    state file and the runtime disagree about the same install.
    """

    def _row(self, ref: str) -> Dict[str, Any]:
        from cli.install_workflow import _version_records

        rows = _version_records(
            REPO_ROOT,
            copy_install(self, ref=ref),
            "sha256:" + "0" * 64,
            "current",
            release_ref=ref,
        )
        return next(row for row in rows if row["kind"] == "release_version")

    def test_non_release_ref_is_recorded_as_unknown(self) -> None:
        for ref in ("local-writer-fix", "main", "feature/x", "a" * 40):
            with self.subTest(ref=ref):
                row = self._row(ref)
                self.assertIsNone(row["value"])
                self.assertEqual(row["state"], "unknown")
                self.assertEqual(row["verification"], "unknown")

    def test_release_tag_is_recorded_as_a_verified_claim(self) -> None:
        row = self._row("v9.9.9")
        self.assertEqual(row["value"], "v9.9.9")
        self.assertEqual(row["state"], "known")
        self.assertEqual(row["verification"], "verified")

    def test_every_recorded_claim_reads_back_identically(self) -> None:
        for ref in ("v9.9.9", "v2.0.0-rc.1", "main", "local-writer-fix"):
            with self.subTest(ref=ref):
                root = copy_install(self, ref=ref)
                from cli.install_workflow import _version_records

                rows = _version_records(
                    REPO_ROOT, root, "sha256:" + "0" * 64, "current", release_ref=ref
                )
                recorded = next(
                    row for row in rows if row["kind"] == "release_version"
                )
                self.assertEqual(recorded["value"], release_version(root)["value"])


class TestSourceCheckoutIdentitiesUnchanged(unittest.TestCase):
    """Git provenance keeps precedence and never becomes a release claim."""

    def test_source_checkout_reports_git_revision_and_unknown_release(self) -> None:
        content = installed_content(REPO_ROOT)
        self.assertEqual(content["materialization"], "source-checkout")
        self.assertIsNotNone(content["revision"])
        self.assertRegex(str(content["revision"]), r"^[0-9a-f]{40}$")
        self.assertEqual(content["revision_attribution"], "git-revision")
        self.assertIn(content["verification"], ("verified", "dirty"))
        self.assertEqual(release_version(REPO_ROOT)["state"], "unknown")


# ---------------------------------------------------------------------------
# Truthful unknown / unverified negatives
# ---------------------------------------------------------------------------

class TestTruthfulUnknownStates(unittest.TestCase):
    """Absent, malformed, incomplete, or contradicted facts stay unknown."""

    def assert_unverified(self, root: Path) -> Dict[str, Any]:
        records = version_identities(
            root,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        self.assertIsNone(records["release_version"]["value"])
        self.assertEqual(records["release_version"]["state"], "unknown")
        self.assertEqual(records["release_version"]["attribution"], "unavailable")
        self.assertEqual(records["installed_content"]["verification"], "unverified")
        self.assertEqual(records["running_server"]["state"], "unknown")
        return records

    def test_missing_marker_stays_unknown(self) -> None:
        root = copy_install(self, ref=None)
        (root / "VERSION").unlink(missing_ok=True)
        self.assert_unverified(root)

    def test_empty_marker_stays_unknown(self) -> None:
        root = copy_install(self, ref=None)
        (root / "VERSION").write_text("\n", encoding="utf-8")
        self.assert_unverified(root)

    def test_multi_token_marker_is_malformed(self) -> None:
        root = copy_install(self, ref=None)
        (root / "VERSION").write_text("v1.2.3 extra\n", encoding="utf-8")
        self.assert_unverified(root)

    def test_multi_line_marker_is_malformed(self) -> None:
        root = copy_install(self, ref=None)
        (root / "VERSION").write_text("v1.2.3\nv1.2.4\n", encoding="utf-8")
        self.assert_unverified(root)

    def test_non_utf8_marker_is_malformed(self) -> None:
        root = copy_install(self, ref=None)
        (root / "VERSION").write_bytes(b"\xff\xfe\x00v1")
        self.assert_unverified(root)

    def test_non_release_ref_does_not_become_a_release_version(self) -> None:
        root = copy_install(self, ref="main")
        records = version_identities(
            root,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        self.assertIsNone(records["release_version"]["value"])
        self.assertEqual(records["release_version"]["state"], "unknown")
        # The install is still a recorded install: content facts stay knowable.
        self.assertEqual(records["installed_content"]["recorded_ref"], "main")
        self.assertEqual(records["installed_content"]["verification"], "verified")

    def test_incomplete_installed_content_is_unverified(self) -> None:
        root = copy_install(self)
        shutil.rmtree(root / "mcp_server")
        content = installed_content(root)
        self.assertEqual(content["mcp_completeness"], "incomplete")
        self.assertIsNone(content["revision"])
        self.assertEqual(content["verification"], "unverified")
        self.assertEqual(content["mcp_state"], "incomplete")
        records = version_identities(
            root,
            mcp_protocol_version="2024-11-05",
            include_running_server=True,
        )
        self.assertEqual(records["running_server"]["state"], "unknown")
        # The release marker is still its own authority and stays known.
        self.assertEqual(records["release_version"]["value"], FIXTURE_REF)

    def test_content_that_contradicts_install_evidence_is_dirty(self) -> None:
        root = copy_install(self)
        write_state(
            root,
            [
                recorded_row(
                    root,
                    value="sha256:" + "0" * 64,
                    installed_identity="sha256:" + "0" * 64,
                    mcp_identity="sha256:" + "1" * 64,
                )
            ],
        )
        content = installed_content(root)
        self.assertEqual(content["verification"], "dirty")
        self.assertEqual(content["verification_evidence"], "install-state-mismatch")

    def test_matching_install_evidence_verifies_content(self) -> None:
        root = copy_install(self)
        write_state(root, [recorded_row(root)])
        content = installed_content(root)
        self.assertEqual(content["verification"], "verified")
        self.assertEqual(content["verification_evidence"], "install-state-record")


# ---------------------------------------------------------------------------
# Connected-process attribution for in-process MCP tool calls
# ---------------------------------------------------------------------------

class TestConnectedProcessAttribution(unittest.TestCase):
    """Tools executed inside the server process report that process."""

    def setUp(self) -> None:
        self.addCleanup(set_connected_running_server, connected_running_server())

    def test_plain_cli_context_is_not_a_connected_process(self) -> None:
        set_connected_running_server(None)
        root = copy_install(self)
        record = version_identities(root)["running_server"]
        self.assertIsNone(record["process_id"])
        self.assertIsNone(record["loaded_content"])
        self.assertEqual(record["attribution"], "not-connected-context")

    def test_registered_connected_fact_reaches_tool_records(self) -> None:
        root = copy_install(self)
        set_connected_running_server(running_fact(root))
        record = version_identities(root)["running_server"]
        self.assertEqual(record["process_id"], 424242)
        self.assertEqual(record["attribution"], "connected-process")
        self.assertEqual(record["state"], "current")

    def test_serving_process_publishes_and_then_releases_its_own_fact(self) -> None:
        from tests.mcp_server.test_server import single

        set_connected_running_server(None)
        observed: List[Optional[Dict[str, Any]]] = []
        original = server._identity_records

        def record_and_resolve() -> Dict[str, Any]:
            observed.append(connected_running_server())
            return original()

        with patch.object(server, "_identity_records", record_and_resolve):
            single("initialize")
        self.assertTrue(observed)
        self.assertEqual(observed[0]["process_id"], os.getpid())
        self.assertEqual(observed[0]["attribution"], "connected-process")
        # A process that is not serving must not keep claiming to be connected.
        self.assertIsNone(connected_running_server())

    def test_resolve_config_tool_reports_the_connected_process(self) -> None:
        from tests.mcp_server.test_server import single

        root = copy_install(self)
        self.enterContext(
            patch.object(server, "_RUNNING_SERVER_FACT", running_fact(root))
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir(parents=True)
            (project / "cartopian.toml").write_text(
                "[project]\n"
                'id = "demo"\n'
                'name = "Demo"\n'
                'project_schema_version = "v0.9.0"\n'
                "\n[roles.coder]\n"
                'description = "Implements work."\n'
                'agent = "cartopian-claude"\n',
                encoding="utf-8",
            )
            response = single(
                "tools/call",
                {
                    "name": "resolve_config",
                    "arguments": {"project_path": str(project)},
                },
            )
        record = response["result"]["structuredContent"]["records"][0]
        running = record["version_identities"]["running_server"]
        self.assertEqual(running["attribution"], "connected-process")
        self.assertEqual(running["process_id"], 424242)


# ---------------------------------------------------------------------------
# MCP prelude projection
# ---------------------------------------------------------------------------

class TestMcpPreludeProjection(unittest.TestCase):
    """Tool/resource prelude text carries the same authoritative facts."""

    def install_context(self, root: Path) -> List[str]:
        with patch.object(server, "ROOT", root), patch.object(
            server, "_RUNNING_SERVER_FACT", running_fact(root)
        ):
            return server._install_context_lines()

    def test_prelude_reports_known_release_and_installed_facts(self) -> None:
        root = copy_install(self)
        lines = self.install_context(root)
        text = "\n".join(lines)
        self.assertIn(f"- Release version: `{FIXTURE_REF}` (known)", text)
        self.assertNotIn("revision `unknown`", text)
        self.assertNotIn("verification `unverified`", text)
        self.assertIn("materialization `copy`", text)
        self.assertIn("- Running server: process `424242`", text)
        self.assertIn("- Restart status: `no_restart_needed`", text)
        identity = mcp_content_identity(root)["identity"]
        self.assertEqual(text.count(str(identity)), 2)

    def test_prelude_preserves_unknowns_without_a_marker(self) -> None:
        root = copy_install(self, ref=None)
        (root / "VERSION").unlink(missing_ok=True)
        text = "\n".join(self.install_context(root))
        self.assertIn("- Release version: `unknown` (unknown)", text)
        self.assertIn("no release marker recorded", text)
        self.assertIn("verification `unverified`", text)

    def test_prelude_explains_a_withheld_claim_from_a_branch_install(self) -> None:
        root = copy_install(self, ref="local-writer-fix")
        text = "\n".join(self.install_context(root))
        self.assertIn("- Release version: `unknown` (unknown)", text)
        self.assertIn("installed from ref `local-writer-fix`", text)
        self.assertIn("not a release tag", text)

    def test_initialize_instructions_and_server_version(self) -> None:
        from tests.mcp_server.test_server import single

        root = copy_install(self)
        with patch.object(server, "ROOT", root), patch.object(
            server, "_RUNNING_SERVER_FACT", running_fact(root)
        ):
            result = single("initialize")["result"]
            identities = result["cartopianIdentities"]
        self.assertEqual(result["serverInfo"]["version"], FIXTURE_REF)
        self.assertIn(f"- Release version: `{FIXTURE_REF}` (known)", result["instructions"])
        self.assertNotIn("Release version: `unknown`", result["instructions"])
        self.assertEqual(identities["release_version"]["value"], FIXTURE_REF)
        self.assertEqual(identities["running_server"]["state"], "current")

    def test_use_cartopian_resource_and_prompt_carry_the_prelude(self) -> None:
        from tests.mcp_server.test_server import single

        root = copy_install(self)
        with patch.object(server, "ROOT", root), patch.object(
            server, "_RUNNING_SERVER_FACT", running_fact(root)
        ):
            resource = single(
                "resources/read",
                {"uri": "cartopian://skills/use_cartopian"},
            )["result"]["contents"][0]["text"]
            prompt = single("prompts/get", {"name": "use_cartopian"})["result"]
        message = prompt["messages"][0]["content"]["text"]
        for text in (resource, message):
            self.assertIn(f"- Release version: `{FIXTURE_REF}` (known)", text)
            self.assertNotIn("revision `unknown`", text)
            self.assertNotIn("verification `unverified`", text)


# ---------------------------------------------------------------------------
# Fresh-process evidence
# ---------------------------------------------------------------------------

class TestFreshProcessEvidence(unittest.TestCase):
    """A newly started server process proves matching, non-unknown facts."""

    def start_server(self, root: Path, requests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        home = root.parent / "home"
        home.mkdir(exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        proc = subprocess.run(
            [sys.executable, str(root / "bin" / "cartopian-mcp")],
            input="\n".join(json.dumps(item) for item in requests) + "\n",
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        return [
            json.loads(line)
            for line in proc.stdout.splitlines()
            if line.strip()
        ]

    def test_new_process_projects_matching_non_unknown_facts(self) -> None:
        root = copy_install(self)
        responses = self.start_server(
            root,
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "resources/read",
                    "params": {"uri": "cartopian://skills/use_cartopian"},
                },
            ],
        )
        initialize = responses[0]["result"]
        prelude = responses[1]["result"]["contents"][0]["text"]

        self.assertEqual(initialize["serverInfo"]["version"], FIXTURE_REF)
        identities = initialize["cartopianIdentities"]
        expected_identity = mcp_content_identity(root)["identity"]
        self.assertEqual(identities["release_version"]["value"], FIXTURE_REF)
        self.assertEqual(identities["installed_content"]["verification"], "verified")
        self.assertEqual(
            identities["installed_content"]["mcp_identity"], expected_identity
        )
        running = identities["running_server"]
        self.assertEqual(running["state"], "current")
        self.assertNotEqual(running["process_id"], os.getpid())
        self.assertEqual(
            running["loaded_content"]["mcp_identity"], expected_identity
        )
        self.assertIsNotNone(running["instance_id"])

        self.assertIn(f"- Release version: `{FIXTURE_REF}` (known)", prelude)
        self.assertNotIn("revision `unknown`", prelude)
        self.assertNotIn("verification `unverified`", prelude)
        match = re.search(r"- Running server: process `(\d+)`", prelude)
        self.assertIsNotNone(match)
        self.assertNotEqual(int(match.group(1)), os.getpid())
        self.assertIn("- Restart status: `no_restart_needed`", prelude)


if __name__ == "__main__":
    unittest.main()
