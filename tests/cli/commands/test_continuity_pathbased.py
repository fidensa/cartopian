"""Re-run the continuity write paths on the *path-based* atomic branch.

Native Windows has no directory file descriptors, so the shared mechanism falls
back to a path-based atomic write. That branch never runs on POSIX by default,
so without this module the reservation's two publications and the artifact's own
would ship untested on the platform this project supports.
"""
from __future__ import annotations

import unittest

import cli.mediated_write as mw

from tests.cli.commands import test_continuity_publication as publication
from tests.cli.commands import test_release_reservation as release_tests
from tests.cli.commands import test_write_continuity as write_tests


class _ForcePathBased:
    """Force the path-based branch for the duration of each test.

    Asserts the host's default is the dir-fd path, so the override is
    meaningful rather than testing the same branch twice.
    """

    def setUp(self):
        assert mw._DIR_FD_SUPPORTED, "expected dir-fd to be the default on this host"
        self._saved = mw._force_path_based
        mw._force_path_based = True
        super().setUp()

    def tearDown(self):
        mw._force_path_based = self._saved
        super().tearDown()


class ArchivePlusIndexPathBased(_ForcePathBased, write_tests.TestArchivePlusIndex):
    pass


class LedgerOutcomePathBased(_ForcePathBased, write_tests.TestLedgerOutcome):
    pass


class RetryAndRecoveryPathBased(_ForcePathBased, write_tests.TestRetryAndRecovery):
    pass


class ReleaseStateMachinePathBased(_ForcePathBased, release_tests.TestReleaseStateMachine):
    pass


class ArtifactPostCommitCleanupPathBased(
    _ForcePathBased, publication.TestArtifactPostCommitCleanup
):
    pass


class ArtifactPreCommitBoundariesPathBased(
    _ForcePathBased, publication.TestArtifactPreCommitBoundaries
):
    pass


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
