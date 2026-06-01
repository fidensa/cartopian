"""Negative-test suite for the mediated-write primitive (SPEC-01-002, P01-BUILD-002).

Every SPEC-01-002 test vector is covered red→green and fail-closed:

- **RED** — each prohibited case first drives a *naive sole writer*
  (``_naive_write``: a plain ``open(path, "w")``, i.e. what an uncontained PM
  would have) against the identical malicious setup and asserts the escape /
  unsafe write actually happens. This proves the vector is real and that the
  attack scaffolding works. Green can never be reached on a bogus setup — the
  red assertion gates it (fail-closed harness).
- **GREEN** — the same setup is then handed to :func:`mediated_write`, which
  must raise :class:`GuardRefusal` naming the violated rule and write nothing.

The internal CLI shim (``python -m cli.mediated_write``) is exercised for the
FR-014 surface: non-zero exit + ``[guard]`` stderr on refusal, NDJSON stdout on
success. A separate test asserts the primitive is absent from the PM tool
surface (CLI ``SUBCOMMANDS`` and the MCP ``list_tools`` registry).
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from cli.mediated_write import (  # noqa: E402
    DEST_KINDS,
    GuardRefusal,
    mediated_write,
)
import cli.mediated_write as mw  # noqa: E402


def _naive_write(path, content):
    """The uncontained baseline sole writer: follows symlinks, no allowlist."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _ProjectFixture(unittest.TestCase):
    """A throwaway cartopian project root with the lifecycle dirs created."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        # Minimal project shape.
        (Path(self.root) / "cartopian.toml").write_text(
            "[defaults]\ngit_versioning = false\n", encoding="utf-8"
        )
        for sub in ("tasks/open", "specs", "phases", "prompts", "reports",
                    "reviews", "decisions"):
            (Path(self.root) / sub).mkdir(parents=True, exist_ok=True)
        # An out-of-subtree area to use as escape targets.
        self.outside = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        mw._concurrent_swap_hook = None  # never leak the test seam


class TestSuccessPath(_ProjectFixture):
    def test_valid_write_lands_and_is_not_executable(self):
        rel = "open/TASK-01-002-demo.md"
        result = mediated_write(self.root, "task", rel, "hello\n")
        dest = Path(self.root) / "tasks" / rel
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(encoding="utf-8"), "hello\n")
        st = dest.lstat()
        self.assertTrue(stat.S_ISREG(st.st_mode))
        self.assertEqual(st.st_mode & 0o111, 0, "exec bits must be clear")
        self.assertEqual(st.st_mode & 0o777, 0o644)
        self.assertEqual(st.st_nlink, 1)
        self.assertEqual(result["bytes"], len(b"hello\n"))

    def test_overwrite_of_existing_regular_file_is_atomic(self):
        rel = "open/TASK-01-002-demo.md"
        dest = Path(self.root) / "tasks" / rel
        dest.write_text("old\n", encoding="utf-8")
        mediated_write(self.root, "task", rel, "new\n")
        self.assertEqual(dest.read_text(encoding="utf-8"), "new\n")
        self.assertEqual(dest.lstat().st_nlink, 1)


class TestSymlinkFinalComponent(_ProjectFixture):
    def test_symlink_final_component_refused(self):
        secret = Path(self.outside) / "secret.txt"
        secret.write_text("ORIGINAL", encoding="utf-8")
        link = Path(self.root) / "tasks" / "open" / "evil.md"
        os.symlink(secret, link)

        # RED: naive write follows the symlink and clobbers the outside file.
        _naive_write(link, "PWNED")
        self.assertEqual(secret.read_text(encoding="utf-8"), "PWNED",
                         "red: naive write should have escaped via symlink")

        secret.write_text("ORIGINAL", encoding="utf-8")  # reset; link survives

        # GREEN: mediated_write refuses on the symlink rule, nothing escapes.
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "task", "open/evil.md", "SAFE")
        self.assertEqual(ctx.exception.rule, "symlink")
        self.assertEqual(secret.read_text(encoding="utf-8"), "ORIGINAL")


class TestDotDotTraversal(_ProjectFixture):
    def test_dotdot_escaping_subtree_refused(self):
        base = Path(self.root) / "tasks"

        # RED: naive join + write lands a file outside the allowlisted subtree.
        escaped = os.path.join(str(base), "..", "escaped-red.md")
        _naive_write(escaped, "x")
        self.assertTrue((Path(self.root) / "escaped-red.md").is_file(),
                        "red: naive write escaped the tasks/ subtree")

        # GREEN: mediated_write refuses; no file is created at the escape target.
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "task", "../escaped-green.md", "x")
        self.assertEqual(ctx.exception.rule, "outside-allowlist")
        self.assertFalse((Path(self.root) / "escaped-green.md").exists())


class TestToctouParentSwap(_ProjectFixture):
    def test_parent_swapped_to_symlink_refused(self):
        evildir = Path(self.outside) / "evildir"
        evildir.mkdir()
        parent = Path(self.root) / "tasks" / "open"

        # RED: parent swapped to a symlink, then a path-based write lands the
        # file inside the attacker directory.
        backup = Path(self.root) / "tasks" / "_open_backup"
        os.rename(parent, backup)
        os.symlink(evildir, parent)
        _naive_write(str(parent / "file.md"), "PWNED")
        self.assertTrue((evildir / "file.md").is_file(),
                        "red: naive write escaped via swapped parent symlink")
        # Restore a real parent dir for the green run.
        os.unlink(parent)
        os.rename(backup, parent)
        (evildir / "file.md").unlink()

        # GREEN: the swap is injected *after* canonicalization/snapshot and
        # *before* the no-follow parent open, exactly the TOCTOU window.
        def _swap():
            os.rename(parent, backup)
            os.symlink(evildir, parent)

        mw._concurrent_swap_hook = _swap
        try:
            with self.assertRaises(GuardRefusal) as ctx:
                mediated_write(self.root, "task", "open/file.md", "SAFE")
        finally:
            mw._concurrent_swap_hook = None
        self.assertEqual(ctx.exception.rule, "toctou")
        self.assertFalse((evildir / "file.md").exists(),
                         "green: nothing may be written through the swapped parent")


class TestExecBit(_ProjectFixture):
    def test_exec_bit_mode_refused(self):
        # RED: a naive writer honoring mode=0o755 produces an executable file.
        red = Path(self.root) / "tasks" / "open" / "red.sh"
        fd = os.open(str(red), os.O_WRONLY | os.O_CREAT, 0o755)
        os.fchmod(fd, 0o755)
        os.close(fd)
        self.assertTrue(red.lstat().st_mode & 0o111,
                        "red: naive write set an executable bit")

        # GREEN: mediated_write refuses any exec bit; nothing written.
        dest = Path(self.root) / "tasks" / "open" / "TASK-01-002-x.md"
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "task", "open/TASK-01-002-x.md", "x", mode=0o755)
        self.assertEqual(ctx.exception.rule, "exec-bit")
        self.assertFalse(dest.exists())


class TestConfigFileDestination(_ProjectFixture):
    def test_cartopian_toml_refused(self):
        cfg = Path(self.root) / "cartopian.toml"
        original = cfg.read_text(encoding="utf-8")

        # RED: naive write clobbers the project config.
        _naive_write(cfg, "git_versioning = true  # PWNED")
        self.assertIn("PWNED", cfg.read_text(encoding="utf-8"),
                      "red: naive write clobbered cartopian.toml")
        cfg.write_text(original, encoding="utf-8")

        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "state", "cartopian.toml", "x")
        self.assertEqual(ctx.exception.rule, "config-file")
        self.assertEqual(cfg.read_text(encoding="utf-8"), original)

    def test_local_config_and_dotfile_refused(self):
        for target in ("cartopian.local.toml", ".secret-token"):
            with self.assertRaises(GuardRefusal) as ctx:
                mediated_write(self.root, "state", target, "x")
            self.assertEqual(ctx.exception.rule, "config-file", msg=target)
            self.assertFalse((Path(self.root) / target).exists())


class TestHardlink(_ProjectFixture):
    def test_hardlink_to_out_of_subtree_inode_refused(self):
        outside_inode = Path(self.outside) / "shared-inode.txt"
        outside_inode.write_text("ORIG", encoding="utf-8")
        link = Path(self.root) / "tasks" / "open" / "TASK-01-002-h.md"
        os.link(outside_inode, link)  # hardlink: same inode, st_nlink == 2
        self.assertEqual(link.lstat().st_nlink, 2)

        # RED: naive write through the hardlink mutates the shared inode.
        _naive_write(link, "PWNED")
        self.assertEqual(outside_inode.read_text(encoding="utf-8"), "PWNED",
                         "red: naive write mutated the out-of-subtree inode")
        outside_inode.write_text("ORIG", encoding="utf-8")  # reset; link persists

        # GREEN: mediated_write refuses the hardlink, inode untouched.
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "task", "open/TASK-01-002-h.md", "SAFE")
        self.assertEqual(ctx.exception.rule, "hardlink")
        self.assertEqual(outside_inode.read_text(encoding="utf-8"), "ORIG")


class TestNonAllowlistedDestKind(_ProjectFixture):
    def test_unknown_dest_kind_refused(self):
        bogus_kind = "totally-bogus"
        rel = "x.md"

        # Precondition: the vector's category must lie outside the closed set.
        self.assertNotIn(bogus_kind, DEST_KINDS,
                         "precondition: vector kind must be outside the closed set")

        # The only locations any write may legitimately land: the realpath of
        # every allowlisted category subtree (root itself for the ""-kinds).
        named_subtrees = {
            os.path.realpath(os.path.join(self.root, sub))
            for sub in DEST_KINDS.values() if sub != ""
        }

        # RED: a naive/uncontained sole writer (no closed allowlist) keys the
        # destination off the caller-supplied kind name, materializing a
        # brand-new `totally-bogus/` category directory that corresponds to no
        # allowlisted dest_kind and landing the file there. The escape — a write
        # under a non-allowlisted category — actually happens.
        naive_category = os.path.realpath(os.path.join(self.root, bogus_kind))
        naive_dest = os.path.join(naive_category, rel)
        os.makedirs(naive_category, exist_ok=True)
        _naive_write(naive_dest, "PWNED")
        self.assertTrue(os.path.isfile(naive_dest),
                        "red: naive write created a file under a non-allowlisted category")
        self.assertNotIn(naive_category, named_subtrees,
                         "red: the escape category is none of the named allowlisted subtrees")

        # Reset so the green run starts clean and can prove nothing is written.
        os.remove(naive_dest)
        os.rmdir(naive_category)

        # GREEN: the closed allowlist refuses the unknown category outright; no
        # category directory and no file are created.
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, bogus_kind, rel, "SAFE")
        self.assertEqual(ctx.exception.rule, "unknown-dest-kind")
        self.assertFalse(os.path.exists(naive_category),
                         "green: no non-allowlisted category directory may be created")
        self.assertFalse(os.path.exists(naive_dest),
                         "green: nothing may be written for an unknown dest_kind")


class TestNonRegularDestination(_ProjectFixture):
    def test_directory_destination_refused(self):
        # A pre-existing directory where the file should go is non-regular.
        os.mkdir(os.path.join(self.root, "specs", "SPEC-01-002-x.md"))
        with self.assertRaises(GuardRefusal) as ctx:
            mediated_write(self.root, "spec", "SPEC-01-002-x.md", "x")
        self.assertEqual(ctx.exception.rule, "non-regular")


# --------------------------------------------------------------------------
# FR-014 machine contract via the internal CLI shim, and surface-exclusion.
# --------------------------------------------------------------------------
class TestCliShimContract(_ProjectFixture):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "cli.mediated_write", *args],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )

    def test_success_emits_ndjson_and_exit_zero(self):
        res = self._run(self.root, "task", "open/TASK-01-002-cli.md",
                        "--content", "body\n")
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        self.assertEqual(res.stderr, "")
        record = json.loads(res.stdout.strip())
        self.assertEqual(record["action"], "mediated-write")
        self.assertEqual(record["details"]["dest_kind"], "task")
        dest = Path(self.root) / "tasks" / "open" / "TASK-01-002-cli.md"
        self.assertEqual(dest.read_text(encoding="utf-8"), "body\n")

    def test_refusal_emits_guard_line_and_nonzero_exit(self):
        res = self._run(self.root, "task", "../escaped.md", "--content", "x")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.stdout, "")
        self.assertTrue(res.stderr.startswith("[guard] outside-allowlist:"),
                        msg=f"stderr was: {res.stderr!r}")
        self.assertFalse((Path(self.root) / "escaped.md").exists())

    def test_config_refusal_via_shim(self):
        res = self._run(self.root, "state", "cartopian.local.toml",
                        "--content", "x")
        self.assertEqual(res.returncode, 1)
        self.assertTrue(res.stderr.startswith("[guard] config-file:"))


class TestNotExposedOnPmSurface(unittest.TestCase):
    def test_absent_from_cli_subcommands(self):
        from cli import main as cli_main
        for name in ("mediated-write", "mediated_write", "write"):
            self.assertNotIn(name, cli_main.SUBCOMMANDS)
        self.assertNotIn("mediated-write", cli_main._real_handlers())

    def test_absent_from_mcp_tool_registry(self):
        from mcp_server import server
        names = {t["name"] for t in server.list_tools()}
        self.assertNotIn("mediated_write", names)
        self.assertNotIn("mediated-write", names)

    def test_dest_kinds_is_a_closed_set(self):
        # Sanity: the allowlist is a non-empty, string-keyed closed mapping.
        self.assertTrue(DEST_KINDS)
        self.assertTrue(all(isinstance(k, str) for k in DEST_KINDS))


if __name__ == "__main__":
    unittest.main()
