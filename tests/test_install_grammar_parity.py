"""Writer/reader grammar parity across the install layer.

Each vocabulary or grammar that is asked about in more than one place is
asserted equal here, mechanically. The release-version bug shipped because a
writer and a reader answered the same question with two different grammars;
this suite converts "remember to keep these in sync" into a build failure.
"""
from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli import protocol_gate  # noqa: E402
from cli.config_schema import identity_contract  # noqa: E402
from cli.install_state import (  # noqa: E402
    PEER_IDENTITY_KINDS,
    _validate_versions,
    identity_state_vocabulary,
)
from cli.install_workflow import (  # noqa: E402
    _CLIENTS,
    INSTALLED_TARGETS,
    MCP_TARGETS,
    SUPPORTED_CLIENTS,
    _surface_digest,
    _target_schema,
)
from cli.provenance import PM_IDENTIFIER_RE  # noqa: E402
from cli.restart_state import (  # noqa: E402
    _CLIENT_ALIASES,
    CLIENT_RESTART_INSTRUCTIONS,
)
from cli.resume_state import _GOVERNANCE_TEXT  # noqa: E402
from cli.version_identities import (  # noqa: E402
    INSTALLED_CONTENT_PATHS,
    MCP_CONTENT_PATHS,
    _content_digest,
    _install_receipt,
    is_receipt_ref,
    is_release_tag,
    release_version,
)

_INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.py"


def _load_install_module():
    spec = importlib.util.spec_from_file_location(
        "cartopian_install_parity", _INSTALL_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestIdentityStateVocabularyParity(unittest.TestCase):
    """`_validate_versions` accepts exactly `identity_state_vocabulary`.

    The validator once carried its own inline state list that omitted
    `malformed`; a record preserving a truthful malformed observation failed
    validation. Acceptance must track the vocabulary authority for every peer
    identity kind, in both directions.
    """

    def _state_diagnostics(self, kind: str, state: Any) -> List[Dict[str, str]]:
        contract = identity_contract()
        record = {
            "versions": [
                {
                    "kind": kind,
                    "value": "observed-value",
                    "state": state,
                    "authority": contract[kind]["authority"],
                    "verification": "verified",
                }
            ]
        }
        diagnostics: List[Dict[str, str]] = []
        _validate_versions(record, diagnostics)
        return [
            item
            for item in diagnostics
            if item.get("field") == "versions[0].state"
            and item.get("code") == "unknown-vocabulary"
        ]

    def test_every_vocabulary_state_validates_for_every_kind(self) -> None:
        for kind in PEER_IDENTITY_KINDS:
            vocabulary = identity_state_vocabulary(kind)
            self.assertTrue(vocabulary, msg=f"empty vocabulary for {kind}")
            for state in vocabulary:
                with self.subTest(kind=kind, state=state):
                    self.assertEqual(self._state_diagnostics(kind, state), [])

    def test_off_vocabulary_state_is_rejected_for_every_kind(self) -> None:
        for kind in PEER_IDENTITY_KINDS:
            with self.subTest(kind=kind):
                flagged = self._state_diagnostics(kind, "not-a-state")
                self.assertEqual(len(flagged), 1)


class TestSurfaceDigestParity(unittest.TestCase):
    """Installer and runtime digest the same surfaces to the same identity.

    `install_workflow._surface_digest` records the installed identity;
    `version_identities._content_digest` reads it back for verification. If
    either the surface lists or the digest recipes drift, every install
    reports content divergence that does not exist (or misses one that does).
    """

    _tmp: Optional[tempfile.TemporaryDirectory] = None
    _root: Optional[Path] = None

    @classmethod
    def setUpClass(cls) -> None:
        from tests._install_fixture import install_copy_fixture

        cls._tmp = tempfile.TemporaryDirectory()
        cls._root = Path(cls._tmp.name) / ".cartopian"
        install_copy_fixture(REPO_ROOT, cls._root)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._tmp is not None:
            cls._tmp.cleanup()

    def test_surface_lists_agree(self) -> None:
        self.assertEqual(tuple(INSTALLED_TARGETS), tuple(INSTALLED_CONTENT_PATHS))
        self.assertEqual(tuple(MCP_TARGETS), tuple(MCP_CONTENT_PATHS))

    def test_installed_content_digests_agree_on_a_copy_install(self) -> None:
        assert self._root is not None
        recorded = _surface_digest(self._root, INSTALLED_TARGETS)
        observed, complete = _content_digest(self._root, INSTALLED_CONTENT_PATHS)
        self.assertTrue(complete)
        self.assertTrue(recorded.startswith("sha256:"))
        self.assertEqual(recorded, observed)

    def test_mcp_content_digests_agree_on_a_copy_install(self) -> None:
        assert self._root is not None
        recorded = _surface_digest(self._root, MCP_TARGETS)
        observed, complete = _content_digest(self._root, MCP_CONTENT_PATHS)
        self.assertTrue(complete)
        self.assertTrue(recorded.startswith("sha256:"))
        self.assertEqual(recorded, observed)


class TestReceiptWriterReaderParity(unittest.TestCase):
    """A `VERSION` receipt the installer writes is never read as malformed.

    `scripts/install.py write_version_marker` and `_install_receipt` /
    `release_version` must share one receipt grammar: every recorded marker
    reads back as a known receipt, every refused ref leaves the receipt
    absent, and a release-tag marker reads back as the identical claim.
    """

    REFS = (
        "v9.9.9",
        "v1.6.27",
        "v2.0.0-rc.1",
        "v1.2",
        "main",
        "master",
        "feature/x",
        "local-writer-fix",
        "a" * 40,
        "V1.2.3",
    )

    def _marker_root(self, ref: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / ".cartopian"
        root.mkdir()
        install_mod = _load_install_module()
        install_mod.write_version_marker(root, ref, [])
        return root

    def test_writer_records_exactly_the_receipt_grammar(self) -> None:
        for ref in self.REFS:
            with self.subTest(ref=ref):
                root = self._marker_root(ref)
                self.assertEqual((root / "VERSION").exists(), is_receipt_ref(ref))

    def test_recorded_receipt_reads_back_known_never_malformed(self) -> None:
        for ref in self.REFS:
            with self.subTest(ref=ref):
                root = self._marker_root(ref)
                token, state = _install_receipt(root)
                if is_receipt_ref(ref):
                    self.assertEqual((token, state), (ref, "known"))
                else:
                    self.assertEqual((token, state), (None, "absent"))

    def test_release_claim_matches_release_tag_grammar(self) -> None:
        for ref in self.REFS:
            with self.subTest(ref=ref):
                claim = release_version(self._marker_root(ref))
                if is_release_tag(ref):
                    self.assertEqual(claim["value"], ref)
                    self.assertEqual(claim["state"], "known")
                else:
                    self.assertIsNone(claim["value"])
                    self.assertEqual(claim["state"], "unknown")
                    self.assertNotEqual(claim["observed_ref_state"], "malformed")


class TestChangelogSchemaGrammarParity(unittest.TestCase):
    """One CHANGELOG heading grammar for gate and migration planner.

    `protocol_gate` and `install_workflow._target_schema` both answer "what
    project-schema version does this release ship?" from the same heading.
    `_target_schema` reads through the gate's grammar, so a two-part,
    prerelease, or double-spaced heading can never make the installer's gate
    and the migration planner disagree about what is being shipped.
    """

    HEADINGS = (
        "### v0.10.0 — three-part form the live CHANGELOG uses",
        "### v0.10 — two-part version",
        "### v1.2.3-rc.1 — prerelease tag",
        "###  v1.2.3 — double space after the hashes",
        "### v12.34.56 — wide components",
    )

    def _source_root(self, heading: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source_root = Path(tmp.name) / "source"
        (source_root / "protocol").mkdir(parents=True)
        (source_root / "protocol" / "CHANGELOG.md").write_text(
            f"# CHANGELOG\n\n## Entries\n\n{heading}\n\n- a migration step\n",
            encoding="utf-8",
        )
        return source_root

    def _gate_answer(self, source_root: Path) -> Optional[str]:
        try:
            return protocol_gate.read_shipped_project_schema_version(
                source_root / "protocol" / "CHANGELOG.md"
            )
        except RuntimeError:
            return None

    def test_gate_and_migration_planner_read_the_same_version(self) -> None:
        for heading in self.HEADINGS:
            with self.subTest(heading=heading):
                source_root = self._source_root(heading)
                self.assertEqual(
                    self._gate_answer(source_root), _target_schema(source_root)
                )


class TestClientVocabularyParity(unittest.TestCase):
    """One supported-client vocabulary across workflow, restart, installer."""

    def test_workflow_tables_agree(self) -> None:
        self.assertEqual(tuple(SUPPORTED_CLIENTS), tuple(_CLIENTS))

    def test_restart_instructions_cover_exactly_the_supported_clients(self) -> None:
        self.assertEqual(
            tuple(SUPPORTED_CLIENTS), tuple(CLIENT_RESTART_INSTRUCTIONS)
        )

    def test_aliases_resolve_onto_exactly_the_supported_clients(self) -> None:
        self.assertEqual(
            set(target for _alias, target in _CLIENT_ALIASES),
            set(SUPPORTED_CLIENTS),
        )

    def test_installer_client_choices_match(self) -> None:
        parser = _load_install_module()._build_parser()
        choices = next(
            action.choices
            for action in parser._actions
            if "--client" in action.option_strings
        )
        self.assertEqual(tuple(choices), tuple(SUPPORTED_CLIENTS))


class TestPmIdentifierGrammarParity(unittest.TestCase):
    """One PM-identifier grammar for provenance scan and governance boundary.

    `provenance.PM_IDENTIFIER_RE` and `resume_state._GOVERNANCE_TEXT` enforce
    the same invariant — no project-management identifier may leak. Any token
    one matches and the other misses is an identifier that crosses exactly one
    of the two boundaries, so both must read the one shared grammar, and that
    grammar must cover the union of every identifier family either boundary
    ever caught on its own.
    """

    IDENTIFIERS = (
        "TASK-001",
        "SPEC-042",
        "DEC-012",
        "PHASE-01",
        "FR-002",
        "BUILD-01-001",
        "VERIFY-02-003",
        "REVIEW-PLAN-001",
        "P01-012",
        "P04-BUILD-005",
        # Families only one boundary caught before the grammars were unified.
        "BL-012",
        "OQ-003",
        "REQUEST-01",
        "NF-004",
        "PLAN-2",
        "REQ-7",
    )
    NON_IDENTIFIERS = (
        "USERTASK-999",
        "plain prose with no identifiers",
        "TIMEOUT = 60",
        "version = 'v0.4.0'",
        "path = 'a-1/b-2'",
    )

    def test_both_boundaries_share_one_grammar_object(self) -> None:
        self.assertIs(_GOVERNANCE_TEXT, PM_IDENTIFIER_RE)

    def test_every_identifier_family_matches_at_both_boundaries(self) -> None:
        for token in self.IDENTIFIERS:
            with self.subTest(token=token):
                self.assertTrue(PM_IDENTIFIER_RE.search(token))
                self.assertTrue(_GOVERNANCE_TEXT.search(token))

    def test_ordinary_text_matches_at_neither_boundary(self) -> None:
        for token in self.NON_IDENTIFIERS:
            with self.subTest(token=token):
                self.assertFalse(PM_IDENTIFIER_RE.search(token))
                self.assertFalse(_GOVERNANCE_TEXT.search(token))


if __name__ == "__main__":
    unittest.main()
