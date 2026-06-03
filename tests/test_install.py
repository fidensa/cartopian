"""Tests for the install/upgrade flow (TASK-01-016, FR-002, DEC-009, DEC-012).

End-to-end install on a clean home dir produces the FR-002 minimum
layout; simulated upgrade preserves the operator-authored
``cartopian.toml`` and a registered ``projects.json``. Tests cover both
symlink (canonical per DEC-012) and copy modes.
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
    "CHANGELOG.md",
)


class _InstallTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.install_root = Path(self.tmp.name) / ".cartopian"


class InstallSymlinkTests(_InstallTestBase):
    def test_first_install_creates_full_layout(self) -> None:
        actions = install_mod.install(REPO_ROOT, self.install_root, mode="symlink")

        # Tool-shipped paths present and (except CHANGELOG.md) are symlinks
        # pointing back at the source repo.
        for target_rel in TOOL_SHIPPED_TARGETS:
            target = self.install_root / target_rel
            self.assertTrue(target.exists(), f"missing {target_rel}")
            if target_rel == "CHANGELOG.md":
                self.assertFalse(target.is_symlink(), "CHANGELOG.md must be a real copy")
            else:
                self.assertTrue(target.is_symlink(), f"{target_rel} must be a symlink")

        # bin/cartopian resolves to the repo entrypoint.
        bin_link = self.install_root / "bin" / "cartopian"
        self.assertEqual(
            Path(os.readlink(bin_link)).resolve(),
            (REPO_ROOT / "bin" / "cartopian").resolve(),
        )

        # cli symlink covers the _vendor/tomli_w.py file required by DEC-001.
        vendor = self.install_root / "cli" / "_vendor" / "tomli_w.py"
        self.assertTrue(vendor.exists(), "cli/_vendor/tomli_w.py must resolve via symlink")

        # CHANGELOG.md is a real copy of repo protocol/CHANGELOG.md.
        installed_changelog = (self.install_root / "CHANGELOG.md").read_text()
        repo_changelog = (REPO_ROOT / "protocol" / "CHANGELOG.md").read_text()
        self.assertEqual(installed_changelog, repo_changelog)

        # Operator-owned files: cartopian.toml seeded from global template.
        installed_toml = (self.install_root / "cartopian.toml").read_text()
        template_toml = (REPO_ROOT / "templates" / "global.cartopian.toml").read_text()
        self.assertEqual(installed_toml, template_toml)

        # Registry seeded as the empty top-level array per DEC-009.
        registry_text = (self.install_root / "projects.json").read_text()
        self.assertEqual(registry_text, "[]\n")
        self.assertEqual(json.loads(registry_text), [])

        # Action log records seeded operator paths.
        self.assertTrue(any("seeded     cartopian.toml" in a for a in actions))
        self.assertTrue(any("seeded     projects.json" in a for a in actions))

    def test_upgrade_preserves_operator_owned_files(self) -> None:
        # First install creates the layout.
        install_mod.install(REPO_ROOT, self.install_root, mode="symlink")

        # Operator edits cartopian.toml (e.g., uncomments a key) and registers
        # a project in projects.json.
        operator_toml = self.install_root / "cartopian.toml"
        operator_toml.write_text(
            '[automation]\nconfirmation = "until-blocked"\n',
            encoding="utf-8",
        )
        registry = self.install_root / "projects.json"
        registry.write_text(
            json.dumps(
                [{"id": "demo", "path": "/abs/path/to/demo"}],
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # Simulated upgrade (re-run the installer; equivalent to `git pull`
        # then a no-op re-link).
        actions = install_mod.install(REPO_ROOT, self.install_root, mode="symlink")

        # Operator-owned files are byte-identical to what the operator wrote.
        self.assertEqual(
            operator_toml.read_text(),
            '[automation]\nconfirmation = "until-blocked"\n',
        )
        self.assertEqual(
            json.loads(registry.read_text()),
            [{"id": "demo", "path": "/abs/path/to/demo"}],
        )

        # Action log says "preserved" for both, not "seeded".
        self.assertTrue(any("preserved  cartopian.toml" in a for a in actions))
        self.assertTrue(any("preserved  projects.json" in a for a in actions))

        # Tool-shipped symlinks still resolve to the source repo.
        for target_rel in TOOL_SHIPPED_TARGETS:
            if target_rel == "CHANGELOG.md":
                continue
            target = self.install_root / target_rel
            self.assertTrue(target.is_symlink())
            resolved = Path(os.readlink(target)).resolve()
            expected = (REPO_ROOT / target_rel).resolve()
            # Special case: ``bin/cartopian`` source rel matches its target rel.
            self.assertEqual(resolved, expected)

    def test_upgrade_repairs_stale_tool_shipped_symlink(self) -> None:
        install_mod.install(REPO_ROOT, self.install_root, mode="symlink")

        # Operator (or a corrupted file system) replaces the cli/ symlink with
        # a stale one pointing somewhere else.
        cli_link = self.install_root / "cli"
        cli_link.unlink()
        stale_target = Path(self.tmp.name) / "stale-cli"
        stale_target.mkdir()
        os.symlink(str(stale_target), str(cli_link), target_is_directory=True)

        actions = install_mod.install(REPO_ROOT, self.install_root, mode="symlink")
        # Now points back at the repo cli/.
        self.assertEqual(
            Path(os.readlink(cli_link)).resolve(),
            (REPO_ROOT / "cli").resolve(),
        )
        self.assertTrue(any("linked     cli ->" in a for a in actions))


class InstallCopyTests(_InstallTestBase):
    def test_first_install_copy_mode(self) -> None:
        install_mod.install(REPO_ROOT, self.install_root, mode="copy")

        for target_rel in TOOL_SHIPPED_TARGETS:
            target = self.install_root / target_rel
            self.assertTrue(target.exists())
            self.assertFalse(
                target.is_symlink(),
                f"{target_rel} must be a real copy in copy mode",
            )

        # cli/_vendor/tomli_w.py copied into place per DEC-001.
        vendor = self.install_root / "cli" / "_vendor" / "tomli_w.py"
        self.assertTrue(vendor.is_file())
        self.assertFalse(vendor.is_symlink())

        # Operator-owned seeds still applied.
        self.assertEqual((self.install_root / "projects.json").read_text(), "[]\n")

    def test_upgrade_copy_replaces_tool_shipped_preserves_operator(self) -> None:
        install_mod.install(REPO_ROOT, self.install_root, mode="copy")

        # Simulate operator state.
        operator_toml = self.install_root / "cartopian.toml"
        operator_toml.write_text("# operator override\n", encoding="utf-8")
        registry = self.install_root / "projects.json"
        registry.write_text('[{"id":"x","path":"/p"}]\n', encoding="utf-8")

        # Operator scribbles inside a tool-shipped copy. Upgrade must replace.
        drifted = self.install_root / "skills" / "DRIFT.md"
        drifted.write_text("drift", encoding="utf-8")

        actions = install_mod.install(REPO_ROOT, self.install_root, mode="copy")

        # Tool-shipped copy refreshed: drift file gone, replaced by upstream.
        self.assertFalse(drifted.exists(), "tool-shipped path must be replaced on upgrade")

        # Operator-owned files preserved.
        self.assertEqual(operator_toml.read_text(), "# operator override\n")
        self.assertEqual(registry.read_text(), '[{"id":"x","path":"/p"}]\n')
        self.assertTrue(any("preserved  cartopian.toml" in a for a in actions))
        self.assertTrue(any("preserved  projects.json" in a for a in actions))


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
        # FR-002 minimum layout — every path under ~/.cartopian/ named in the
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
