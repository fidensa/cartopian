"""Tests for the install/upgrade flow.

End-to-end install on a clean home dir produces the minimum layout;
simulated upgrade preserves the operator-authored ``cartopian.toml`` and
a registered ``projects.json``. Every install is a copy install.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install.py"


def _load_install_module():
    spec = importlib.util.spec_from_file_location("cartopian_install", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


install_mod = _load_install_module()


TOOL_SHIPPED_TARGETS = (
    "protocol",
    "templates",
    "skills",
    "wrappers",
    "cli",
    "mcp_server",
    "bin/cartopian",
    "bin/cartopian.cmd",
    "bin/cartopian-mcp",
    "bin/cartopian-mcp.cmd",
    "install-cartopian.md",
    "scripts/install.py",
    "CHANGELOG.md",
)


class _InstallTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install_root = Path(self.tmp.name) / ".cartopian"

    def run_script(self, *extra: str) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(REPO_ROOT),
                "--prefix",
                str(self.install_root),
                *extra,
            ],
            capture_output=True,
            text=True,
        )


class InstallCopyTests(_InstallTestBase):
    def test_first_install_copies_full_layout(self) -> None:
        result = self.run_script("--quiet")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        for target_rel in TOOL_SHIPPED_TARGETS:
            target = self.install_root / target_rel
            self.assertTrue(target.exists(), f"missing {target_rel}")
            self.assertFalse(
                target.is_symlink(),
                f"{target_rel} must be a real copy",
            )

        # cli/_vendor/tomli_w.py copied into place.
        vendor = self.install_root / "cli" / "_vendor" / "tomli_w.py"
        self.assertTrue(vendor.is_file())
        self.assertFalse(vendor.is_symlink())

        # CHANGELOG.md is a real copy of repo protocol/CHANGELOG.md.
        installed_changelog = (self.install_root / "CHANGELOG.md").read_text()
        repo_changelog = (REPO_ROOT / "protocol" / "CHANGELOG.md").read_text()
        self.assertEqual(installed_changelog, repo_changelog)

        # Operator-owned files: cartopian.toml seeded from global template.
        installed_toml = (self.install_root / "cartopian.toml").read_text()
        template_toml = (REPO_ROOT / "templates" / "global.cartopian.toml").read_text()
        self.assertEqual(installed_toml, template_toml)

        # Registry seeded as the empty top-level array.
        registry_text = (self.install_root / "projects.json").read_text()
        self.assertEqual(registry_text, "[]\n")
        self.assertEqual(json.loads(registry_text), [])

    def test_upgrade_replaces_tool_shipped_preserves_operator(self) -> None:
        result = self.run_script("--quiet")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        # Simulate operator state. The registered project must carry a real,
        # current config: the script's reconciliation gate reads every
        # registered project and fails closed on an unreadable one.
        import re

        changelog = (REPO_ROOT / "protocol" / "CHANGELOG.md").read_text(
            encoding="utf-8"
        )
        match = re.search(r"^### (v[0-9][^\s]*)\s", changelog, flags=re.MULTILINE)
        assert match is not None
        target_schema = match.group(1)
        project_dir = Path(self.tmp.name) / "project-x"
        project_dir.mkdir()
        (project_dir / "cartopian.toml").write_text(
            "[project]\n"
            'id = "x"\n'
            'name = "x"\n'
            f'project_schema_version = "{target_schema}"\n',
            encoding="utf-8",
        )
        operator_toml = self.install_root / "cartopian.toml"
        operator_toml.write_text("# operator override\n", encoding="utf-8")
        registry = self.install_root / "projects.json"
        registry_content = (
            json.dumps([{"id": "x", "path": str(project_dir)}]) + "\n"
        )
        registry.write_text(registry_content, encoding="utf-8")

        # Operator scribbles inside a tool-shipped copy. Upgrade must replace.
        drifted = self.install_root / "skills" / "DRIFT.md"
        drifted.write_text("drift", encoding="utf-8")

        result = self.run_script("--quiet")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        # Tool-shipped copy refreshed: drift file gone, replaced by upstream.
        self.assertFalse(drifted.exists(), "tool-shipped path must be replaced on upgrade")

        # Operator-owned files preserved.
        self.assertEqual(operator_toml.read_text(), "# operator override\n")
        self.assertEqual(registry.read_text(), registry_content)

    def test_mode_flag_is_removed(self) -> None:
        result = self.run_script("--mode", "copy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)


class InstallScriptInvocationTests(_InstallTestBase):
    """End-to-end: invoke the install script as a subprocess on a clean home."""

    def test_script_invocation_clean_home(self) -> None:
        fake_home = Path(self.tmp.name) / "home"
        fake_home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)  # cover the Windows branch too

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--source", str(REPO_ROOT)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        install_root = fake_home / ".cartopian"
        self.assertTrue(install_root.is_dir())
        # Minimum layout — every path under ~/.cartopian/ named in the
        # standards-table is present.
        for target_rel in TOOL_SHIPPED_TARGETS:
            self.assertTrue(
                (install_root / target_rel).exists(),
                f"missing {target_rel} after script invocation",
            )
        self.assertTrue((install_root / "cartopian.toml").is_file())
        self.assertTrue((install_root / "projects.json").is_file())
        self.assertEqual(
            (install_root / "projects.json").read_text(), "[]\n"
        )

        # Summary line printed on stdout.
        self.assertIn("cartopian installed at", result.stdout)

    def test_explicit_prefix_overrides_default_home(self) -> None:
        custom = Path(self.tmp.name) / "custom-prefix"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(REPO_ROOT),
                "--prefix",
                str(custom),
                "--quiet",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((custom / "bin" / "cartopian").exists())
        self.assertEqual((custom / "projects.json").read_text(), "[]\n")


class VersionMarkerTests(_InstallTestBase):
    def test_ref_records_version_marker(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(REPO_ROOT),
                "--prefix",
                str(self.install_root),
                "--ref",
                "v9.9.9",
                "--quiet",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            (self.install_root / "VERSION").read_text(), "v9.9.9\n"
        )

    def test_no_ref_leaves_version_untouched(self) -> None:
        result = self.run_script("--quiet")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse((self.install_root / "VERSION").exists())

    def test_non_receipt_ref_is_reported_and_not_recorded(self) -> None:
        # The receipt grammar is enforced at the writer: a branch ref other
        # than ``main`` must never persist a marker the reader rejects.
        result = self.run_script("--ref", "local-writer-fix")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertFalse((self.install_root / "VERSION").exists())
        self.assertIn("skipped    VERSION marker", result.stdout)

    def test_main_branch_ref_is_recorded(self) -> None:
        result = self.run_script("--ref", "main", "--quiet")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            (self.install_root / "VERSION").read_text(), "main\n"
        )


class GithubBootstrapTests(_InstallTestBase):
    """--from-github mechanics with the network layer mocked out."""

    def test_explicit_ref_needs_no_network(self) -> None:
        ref, url = install_mod.resolve_github_ref("v1.2.3")
        self.assertEqual(ref, "v1.2.3")
        self.assertEqual(
            url,
            "https://api.github.com/repos/fidensa/cartopian/tarball/v1.2.3",
        )

    def test_latest_release_resolves_tag(self) -> None:
        import io

        payload = io.BytesIO(
            json.dumps(
                {"tag_name": "v2.0.0", "tarball_url": "https://example/tarball"}
            ).encode()
        )
        with mock.patch.object(install_mod, "_github_open", return_value=payload):
            ref, url = install_mod.resolve_github_ref(None)
        self.assertEqual(ref, "v2.0.0")
        self.assertEqual(url, "https://example/tarball")

    def test_no_releases_falls_back_to_main(self) -> None:
        import urllib.error

        err = urllib.error.HTTPError("u", 404, "Not Found", None, None)
        with mock.patch.object(install_mod, "_github_open", side_effect=err):
            ref, url = install_mod.resolve_github_ref(None)
        self.assertEqual(ref, "main")
        self.assertEqual(
            url, "https://api.github.com/repos/fidensa/cartopian/tarball/main"
        )

    def test_fetch_extracts_single_top_level_dir(self) -> None:
        import tarfile

        # Build a minimal GitHub-shaped tarball: one top-level dir holding
        # bin/cartopian.
        src = Path(self.tmp.name) / "fidensa-cartopian-abc123"
        (src / "bin").mkdir(parents=True)
        (src / "bin" / "cartopian").write_text("#!/usr/bin/env python3\n")
        tarball = Path(self.tmp.name) / "fake.tar.gz"
        with tarfile.open(tarball, "w:gz") as archive:
            archive.add(src, arcname=src.name)

        workdir = Path(self.tmp.name) / "work"
        workdir.mkdir()
        with mock.patch.object(
            install_mod, "_github_open", return_value=tarball.open("rb")
        ):
            root = install_mod.fetch_github_source("https://example/tarball", workdir)
        self.assertEqual(root.name, "fidensa-cartopian-abc123")
        self.assertTrue((root / "bin" / "cartopian").is_file())

    def test_from_github_rejects_source(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--from-github",
                "--source",
                str(REPO_ROOT),
                "--prefix",
                str(self.install_root),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutually exclusive", result.stderr)


class PatchPathTests(_InstallTestBase):
    """Unix rc-file PATH patching (--patch-path); idempotent by content."""

    def setUp(self) -> None:
        super().setUp()
        if os.name == "nt":
            self.skipTest("Unix rc-file branch")
        self.fake_home = Path(self.tmp.name) / "home"
        self.fake_home.mkdir()

    def _patch(self, actions):
        with mock.patch.dict(
            os.environ, {"HOME": str(self.fake_home), "SHELL": "/bin/zsh"}
        ):
            install_mod._patch_unix_rc(
                self.install_root / "bin",
                self.install_root / "wrappers" / "bin",
                actions,
            )

    def test_appends_once_then_noop(self) -> None:
        actions: list = []
        self._patch(actions)
        rc = self.fake_home / ".zshrc"
        content = rc.read_text()
        self.assertIn(str(self.install_root / "bin"), content)
        self.assertIn(str(self.install_root / "wrappers" / "bin"), content)
        self.assertTrue(any("patched" in a for a in actions))

        again: list = []
        self._patch(again)
        self.assertEqual(rc.read_text(), content, "second run must not append")
        self.assertTrue(any("unchanged" in a for a in again))

    def test_unrecognized_shell_prints_manual_line(self) -> None:
        actions: list = []
        with mock.patch.dict(
            os.environ, {"HOME": str(self.fake_home), "SHELL": "/bin/fish"}
        ):
            install_mod._patch_unix_rc(
                self.install_root / "bin",
                self.install_root / "wrappers" / "bin",
                actions,
            )
        self.assertFalse((self.fake_home / ".zshrc").exists())
        self.assertTrue(any("add manually" in a for a in actions))


class UnregisterTests(_InstallTestBase):
    """`--unregister` is the user-facing path to the guarded registration
    removal (D4): standalone, bounded, and refusing rather than guessing."""

    def _hermes_stub(self, entry_command: str):
        bin_dir = Path(self.tmp.name) / "hermesbin"
        bin_dir.mkdir()
        hermes_home = Path(self.tmp.name) / "hermes-home"
        hermes_home.mkdir()
        log = Path(self.tmp.name) / "unset.log"
        entry = json.dumps({"command": entry_command, "enabled": True})
        stub = bin_dir / "hermes"
        stub.write_text(
            "#!/bin/sh\n"
            # The uninstall resolves the profile home first, then pins the
            # read and the unset to it (`-p default` for a root-like home).
            'if [ "$1" = "-p" ]; then shift 2; fi\n'
            'if [ "$1 $2" = "config path" ]; then\n'
            f"  printf '%s\\n' '{hermes_home / 'config.yaml'}'\n"
            "  exit 0\n"
            "fi\n"
            'if [ "$1 $2" = "config get" ]; then\n'
            f"  printf '%s' '{entry}'\n"
            "  exit 0\n"
            "fi\n"
            'if [ "$1 $2" = "config unset" ]; then\n'
            f'  echo "$3" >> "{log}"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        return bin_dir, log

    def _run_unregister(self, client: str, bin_dir: Path):
        env = dict(
            os.environ,
            PATH=os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        )
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(REPO_ROOT),
                "--prefix",
                str(self.install_root),
                "--unregister",
                client,
            ],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_unregister_hermes_removes_our_entry(self) -> None:
        # The script resolves --prefix, so the stub entry must carry the
        # resolved spelling (macOS tempdirs live behind a /var symlink).
        expected = str(
            self.install_root.resolve() / "bin" / "cartopian-mcp"
        )
        bin_dir, log = self._hermes_stub(expected)
        result = self._run_unregister("hermes", bin_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            log.read_text(encoding="utf-8").split(),
            ["mcp_servers.cartopian"],
        )

    def test_unregister_hermes_preserves_a_foreign_entry(self) -> None:
        bin_dir, log = self._hermes_stub("/somebody/elses/server")
        result = self._run_unregister("hermes", bin_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("preserved", result.stderr)
        self.assertFalse(log.exists())

    def test_unregister_without_an_automated_path_instructs_manually(self) -> None:
        bin_dir = Path(self.tmp.name) / "emptybin"
        bin_dir.mkdir()
        result = self._run_unregister("codex", bin_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("manually", result.stderr)

    def test_unregister_is_a_standalone_operation(self) -> None:
        result = self.run_script("--unregister", "hermes", "--plan-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("standalone cleanup", result.stderr)


class InstallRootPlatformTests(unittest.TestCase):
    """Per-platform install-path expansion per ENGINEERING.md."""

    def test_unix_install_root_uses_home(self) -> None:
        if os.name == "nt":
            self.skipTest("Unix-only branch")
        old_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = "/tmp/fakehome-unix"
            self.assertEqual(
                install_mod.default_install_root(),
                Path("/tmp/fakehome-unix/.cartopian"),
            )
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

    def test_windows_install_root_uses_userprofile(self) -> None:
        # Environment-independent (was skipped on every non-Windows run). Patch
        # ``os.name`` to ``"nt"`` so the native-Windows branch of
        # ``default_install_root()`` runs on any host. ``pathlib`` forbids
        # instantiating a concrete ``WindowsPath`` on POSIX, so we also swap the
        # module's ``Path`` for the host-independent ``PureWindowsPath`` flavour:
        # the real branch logic still runs — base read from ``%USERPROFILE%`` and
        # joined with ``.cartopian`` using Windows separators — only the concrete
        # filesystem flavour is replaced, which is exactly the part that cannot
        # exist on POSIX. Asserts the contract: the nt branch roots the install at
        # ``%USERPROFILE%``, not ``$HOME``.
        with mock.patch.object(os, "name", "nt"), mock.patch.dict(
            os.environ, {"USERPROFILE": r"C:\Users\fake"}, clear=False
        ), mock.patch.object(install_mod, "Path", PureWindowsPath):
            self.assertEqual(
                install_mod.default_install_root(),
                PureWindowsPath(r"C:\Users\fake\.cartopian"),
            )


if __name__ == "__main__":
    unittest.main()
