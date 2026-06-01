"""FR-008 / FR-003 allowlist-integrity after the COMPATIBILITY.md extension (TASK-02-002).

SPEC-02-002 extends the mediated writer's fixed named-root-files allowlist by
**exactly one** entry — ``COMPATIBILITY.md`` — and relaxes no existing guard.
This suite proves the extension is surgical: the writer now permits that one new
destination, and *every other* fail-closed refusal still holds — a non-allowlisted
root file, ``cartopian.toml`` / a ``*.local.toml``, a symlinked
``COMPATIBILITY.md``, and any path whose real path escapes the allowlist.

Red-before-green
----------------
Each refusal carries an in-module red baseline: a *naive* sole writer (a plain
``open(path, "w")`` — what an uncontained PM would have) runs against the same
malicious setup and the escape/clobber actually happens (red). The same setup is
then handed to :func:`mediated_write`, which must refuse fail-closed and write
nothing (green). The "permit exactly one new destination" assertions pin that
the named-root-files allowlist grew by exactly ``COMPATIBILITY.md``.
"""
import os
import tempfile
import unittest
from pathlib import Path

from cli.mediated_write import (
    DEST_KINDS,
    ROOT_FILES,
    GuardRefusal,
    mediated_write,
)

# The FR-003 named project-root files that existed *before* this task. The
# allowlist-integrity contract is: exactly one new entry, COMPATIBILITY.md.
_FR003_ORIGINAL_ROOT_FILES = frozenset({
    "REQUIREMENTS.md",
    "IMPLEMENTATION_PLAN.md",
    "STANDARDS.md",
    "CONVENTIONS.md",
    "STATE.md",
    "ROADMAP.md",
    "BACKLOG.md",
})
_NEW_ROOT_FILE = "COMPATIBILITY.md"


def _naive_write(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _ProjectFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        (Path(self.root) / "cartopian.toml").write_text(
            "[project]\nid = \"demo\"\n", encoding="utf-8"
        )
        self.outside = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(self._tmp.cleanup)


class TestAllowlistGrewByExactlyOne(unittest.TestCase):
    """The named-root-files allowlist gained exactly COMPATIBILITY.md."""

    def test_root_files_allowlist_is_original_plus_one(self):
        permitted = set(ROOT_FILES.values())
        new_entries = permitted - _FR003_ORIGINAL_ROOT_FILES
        self.assertEqual(
            new_entries, {_NEW_ROOT_FILE},
            msg=f"named-root-files allowlist must grow by exactly {_NEW_ROOT_FILE}; "
                f"unexpected new entries: {sorted(new_entries - {_NEW_ROOT_FILE})}",
        )
        # No original named root file was dropped.
        self.assertTrue(
            _FR003_ORIGINAL_ROOT_FILES <= permitted,
            msg=f"an original named root file was dropped: "
                f"{sorted(_FR003_ORIGINAL_ROOT_FILES - permitted)}",
        )

    def test_compatibility_dest_kind_is_a_root_kind(self):
        self.assertIn("compatibility", DEST_KINDS)
        self.assertEqual(DEST_KINDS["compatibility"], "",
                         msg="COMPATIBILITY.md is a root file, not a directory entry")
        self.assertEqual(ROOT_FILES["compatibility"], _NEW_ROOT_FILE)


class TestPermitsCompatibility(_ProjectFixture):
    """The one new destination is now writable through the mediated writer."""

    def test_writes_compatibility_md(self):
        result = mediated_write(self.root, "compatibility", "COMPATIBILITY.md", "x\n")
        dest = Path(self.root) / "COMPATIBILITY.md"
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(encoding="utf-8"), "x\n")
        self.assertEqual(dest.lstat().st_mode & 0o111, 0, "must not be executable")
        self.assertEqual(result["path"], str(dest))


class TestRefusesNonAllowlistedRootFile(_ProjectFixture):
    """A root dest_kind may write only its one bound basename — nothing else."""

    def test_compatibility_kind_refuses_other_basename(self):
        # RED: a naive writer keyed off a caller-supplied name lands an arbitrary
        # file at the project root.
        naive_dest = os.path.join(self.root, "EVIL.md")
        _naive_write(naive_dest, "PWNED")
        self.assertTrue(os.path.isfile(naive_dest),
                        "red: naive write created a non-allowlisted root file")
        os.remove(naive_dest)

        # GREEN: the writer refuses any basename other than COMPATIBILITY.md.
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "compatibility", "EVIL.md", "SAFE")
        self.assertEqual(ctx.exception.rule, "non-allowlisted-root-file")
        self.assertFalse(os.path.exists(naive_dest))

    def test_other_root_kind_refuses_compatibility_basename(self):
        # The compatibility ledger cannot be authored by repurposing another
        # root kind (e.g. 'state' is bound to STATE.md only).
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "state", "COMPATIBILITY.md", "SAFE")
        self.assertEqual(ctx.exception.rule, "non-allowlisted-root-file")
        self.assertFalse((Path(self.root) / "COMPATIBILITY.md").exists())


class TestRefusesConfigViaCompatibilityKind(_ProjectFixture):
    """cartopian.toml / *.local.toml stay refused through the new kind too."""

    def test_cartopian_toml_refused(self):
        cfg = Path(self.root) / "cartopian.toml"
        original = cfg.read_text(encoding="utf-8")

        # RED: naive write clobbers the project config.
        _naive_write(cfg, "id = \"PWNED\"\n")
        self.assertIn("PWNED", cfg.read_text(encoding="utf-8"),
                      "red: naive write clobbered cartopian.toml")
        cfg.write_text(original, encoding="utf-8")

        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "compatibility", "cartopian.toml", "x")
        self.assertEqual(ctx.exception.rule, "config-file")
        self.assertEqual(cfg.read_text(encoding="utf-8"), original)

    def test_local_toml_refused(self):
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "compatibility", "cartopian.local.toml", "x")
        self.assertEqual(ctx.exception.rule, "config-file")
        self.assertFalse((Path(self.root) / "cartopian.local.toml").exists())


class TestRefusesSymlinkedCompatibility(_ProjectFixture):
    """A symlinked COMPATIBILITY.md is refused (no-follow)."""

    def test_symlink_final_component_refused(self):
        secret = Path(self.outside) / "secret.txt"
        secret.write_text("ORIGINAL", encoding="utf-8")
        link = Path(self.root) / "COMPATIBILITY.md"
        os.symlink(secret, link)

        # RED: naive write follows the symlink and clobbers the outside file.
        _naive_write(link, "PWNED")
        self.assertEqual(secret.read_text(encoding="utf-8"), "PWNED",
                         "red: naive write escaped via symlink")
        secret.write_text("ORIGINAL", encoding="utf-8")  # reset; link survives

        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "compatibility", "COMPATIBILITY.md", "SAFE")
        self.assertEqual(ctx.exception.rule, "symlink")
        self.assertEqual(secret.read_text(encoding="utf-8"), "ORIGINAL")


class TestRefusesRealPathEscape(_ProjectFixture):
    """A `..` traversal whose real path escapes the project root is refused."""

    def test_dotdot_escape_refused(self):
        escaped = os.path.join(self.root, "..", "escaped-red.md")
        _naive_write(escaped, "x")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "..", "escaped-red.md")),
                        "red: naive write escaped the project root")
        os.remove(escaped)

        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "compatibility", "../escaped-green.md", "x")
        self.assertIn(ctx.exception.rule, ("outside-allowlist", "non-allowlisted-root-file"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "..", "escaped-green.md")))


if __name__ == "__main__":
    unittest.main()
