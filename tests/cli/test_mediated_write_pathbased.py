"""Exercise the mediated-write *path-based* branch (the native-Windows write
path) on this POSIX host by forcing the test seam.

Native Windows has no directory file descriptors / openat, so the writer falls
back to a path-based atomic write (tmp-by-path + os.replace) that keeps every
platform-agnostic guard plus a final TOCTOU re-verification. That branch never
runs on POSIX by default, so without this module it would ship untested. Here we
re-run the existing negative suite's in-process guard scenarios against the
forced fallback (so the Windows path gets the same coverage), plus a binary/LF
fidelity check that pins the O_BINARY no-translation contract.
"""
import unittest
from pathlib import Path

import cli.mediated_write as mw
from cli.mediated_write import mediated_write

from tests.cli import test_p01_build_002_mediated_write as base


class _ForcePathBased:
    """Mixin: force the path-based branch for the duration of each test.

    Asserts the host's default is the dir-fd path so the override is meaningful
    (otherwise we would be testing the same branch twice).
    """

    def setUp(self):
        super().setUp()
        assert mw._DIR_FD_SUPPORTED, "expected dir-fd to be the default on this host"
        self._saved_force_path_based = mw._force_path_based
        mw._force_path_based = True

    def tearDown(self):
        mw._force_path_based = self._saved_force_path_based
        super().tearDown()


# Re-run the in-process guard/success scenarios on the path-based branch. The
# subprocess-shim tests are intentionally excluded — a child process never sees
# this in-process flag, so forcing it there would be a no-op.
class SuccessPathBased(_ForcePathBased, base.TestSuccessPath):
    pass


class SymlinkFinalComponentPathBased(_ForcePathBased, base.TestSymlinkFinalComponent):
    pass


class DotDotTraversalPathBased(_ForcePathBased, base.TestDotDotTraversal):
    pass


class ToctouParentSwapPathBased(_ForcePathBased, base.TestToctouParentSwap):
    pass


class ExecBitPathBased(_ForcePathBased, base.TestExecBit):
    pass


class ConfigFileDestinationPathBased(_ForcePathBased, base.TestConfigFileDestination):
    pass


class TestPathBasedContentFidelity(_ForcePathBased, base._ProjectFixture):
    def test_lf_and_binary_bytes_round_trip_exact(self):
        # The Windows fidelity contract: O_BINARY keeps the low-level write from
        # translating LF->CRLF in text mode, and nothing truncates at a NUL. On
        # POSIX O_BINARY is 0 (no-op) so this also documents/guards the intent.
        payload = b"alpha\nbeta\r\ngamma\n\x00\x01\x02tail\n"
        mediated_write(self.root, "task", "open/TASK-01-002-bin.md", payload)
        dest = Path(self.root) / "tasks" / "open" / "TASK-01-002-bin.md"
        self.assertEqual(dest.read_bytes(), payload)

    def test_str_content_round_trips(self):
        mediated_write(self.root, "task", "open/TASK-01-002-text.md", "hello\nworld\n")
        dest = Path(self.root) / "tasks" / "open" / "TASK-01-002-text.md"
        self.assertEqual(dest.read_text(encoding="utf-8"), "hello\nworld\n")


if __name__ == "__main__":
    unittest.main()
