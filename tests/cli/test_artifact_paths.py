"""Tests for `cli.artifact_paths` contained artifact reads."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import artifact_paths


class TestPinnedParentRead(unittest.TestCase):
    """The read goes through the directory the containment check approved."""

    @staticmethod
    def _project(tmp: str) -> Path:
        root = Path(tmp) / "proj"
        (root / "reviews").mkdir(parents=True)
        return root

    def test_regular_review_file_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            slot = root / "reviews" / "REVIEW-01-001.md"
            slot.write_text("Verdict: approve\n", encoding="utf-8")
            path, content = artifact_paths.review(root, slot)
        self.assertEqual(content, "Verdict: approve\n")
        self.assertEqual(path.name, "REVIEW-01-001.md")

    def test_symlinked_leaf_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            outside = Path(tmp) / "outside.md"
            outside.write_text("SECRET\n", encoding="utf-8")
            slot = root / "reviews" / "REVIEW-01-001.md"
            slot.symlink_to(outside)
            with self.assertRaises(artifact_paths.ArtifactRefusal) as ctx:
                artifact_paths.review(root, slot)
        self.assertEqual(ctx.exception.rule, "symlink")

    @unittest.skipUnless(
        artifact_paths._DIR_FD_SUPPORTED, "platform has no dir_fd support"
    )
    def test_parent_swapped_for_symlink_after_validation_is_refused(self) -> None:
        """The parent-directory TOCTOU window is closed by the pinned dir fd.

        `resolve` approves the real reviews/ directory; a racing writer then
        replaces reviews/ with a symlink to an outside directory holding a
        file of the same name. O_NOFOLLOW on the leaf alone would follow the
        new parent and read the outside file; the pinned-parent open must
        refuse instead.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "REVIEW-01-001.md").write_text(
                "## Summary\n\nSECRET\n", encoding="utf-8"
            )
            slot = root / "reviews" / "REVIEW-01-001.md"
            slot.write_text("legit\n", encoding="utf-8")
            validated = artifact_paths.resolve(
                root,
                slot,
                subdirs=artifact_paths.REVIEW_SUBDIRS,
                label="--review",
            )
            # Simulate the race deterministically: swap the validated parent
            # after validation, then run the read with validation pinned to
            # its pre-swap result.
            slot.unlink()
            (root / "reviews").rmdir()
            (root / "reviews").symlink_to(outside)
            with patch.object(
                artifact_paths, "resolve", return_value=validated
            ):
                with self.assertRaises(
                    artifact_paths.ArtifactRefusal
                ) as ctx:
                    artifact_paths.review(root, slot)
        self.assertEqual(ctx.exception.rule, "unreadable")
        self.assertIn("cannot pin", str(ctx.exception))


class TestNoUnverifiedFallback(unittest.TestCase):
    """Without dir_fd (and off Windows) the helper fails closed.

    A full-path open with no pinned parent and no handle verification would
    silently retain the parent-swap race, so it must refuse rather than read.
    """

    @unittest.skipUnless(
        __import__("os").name == "posix", "exercises the posix fallback branch"
    )
    def test_read_refuses_when_dir_fd_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = TestPinnedParentRead._project(tmp)
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "REVIEW-01-001.md").write_text(
                "## Summary\n\nSECRET\n", encoding="utf-8"
            )
            slot = root / "reviews" / "REVIEW-01-001.md"
            slot.write_text("legit\n", encoding="utf-8")
            with patch.object(artifact_paths, "_DIR_FD_SUPPORTED", False):
                # Even a legitimate slot refuses: containment cannot be
                # guaranteed, so nothing is read at all.
                with self.assertRaises(artifact_paths.ArtifactRefusal) as ctx:
                    artifact_paths.review(root, slot)
                self.assertEqual(ctx.exception.rule, "uncontainable")
                # The reviewer's forced reproduction: the parent swapped for
                # a symlink after validation must not return SECRET either.
                validated = artifact_paths.resolve(
                    root,
                    slot,
                    subdirs=artifact_paths.REVIEW_SUBDIRS,
                    label="--review",
                )
                slot.unlink()
                (root / "reviews").rmdir()
                (root / "reviews").symlink_to(outside)
                with patch.object(
                    artifact_paths, "resolve", return_value=validated
                ):
                    with self.assertRaises(
                        artifact_paths.ArtifactRefusal
                    ) as swapped:
                        artifact_paths.review(root, slot)
                self.assertEqual(swapped.exception.rule, "uncontainable")


if __name__ == "__main__":
    unittest.main()
