"""Tests for the Claude Code refusal adapter (``cli/claude_hook.py``).

The adapter is a Claude Code PreToolUse hook that denies raw ``Write`` /
``Edit`` / ``MultiEdit`` / ``NotebookEdit`` calls against a registered
Cartopian project's governed path-classes (and declared work roots) when the
active session lacks the corresponding capability grant.

Evidence gate (red-before-green): the deny cases, ungated passthrough, and
zero-footprint tests below were written and run before ``cli/claude_hook.py``
existed — the red run is recorded in the completion report.
"""
import argparse
import contextlib
import io
import json
import ntpath
import os
import posixpath
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / "cli" / "claude_hook.py"


def _payload(tool_name: str, file_path: str, cwd: str = "/tmp") -> dict:
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    return {
        "tool_name": tool_name,
        "tool_input": {key: file_path},
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
    }


_PROJECT_TABLE = (
    "[project]\n"
    'id = "guard-proj"\n'
    'name = "Guard Project"\n'
    'protocol_version = "v0.3.0"\n'
    'work_roots = ["tool-repo"]\n'
    "\n"
)

# Activated config: `warden` declares a grants key (activation is project-wide)
# but holds only a read grant, so every raw write must be denied for it. The
# protocol-default `pm` role declares nothing and therefore fails closed too.
_ACTIVATED_ROLES = (
    "[roles.warden]\n"
    'description = "Holds only read grants."\n'
    'grants = ["read:prompts"]\n'
)

# Ungated config: roles exist but none declares a grants key.
_UNGATED_ROLES = '[roles]\ncoder = "Implements tasks per spec."\n'


class _HookFixture:
    """A throwaway registered Cartopian project + fake ~/.cartopian home."""

    def __init__(self, tmp: Path, roles_toml: str, *, extra_registry=()) -> None:
        self.home = tmp / "carthome"
        self.home.mkdir(parents=True, exist_ok=True)
        self.project_root = tmp / "gov-project"
        for sub in (
            "specs",
            "phases",
            "tasks/open",
            "prompts",
            "decisions",
            "reports",
            "reviews",
        ):
            (self.project_root / sub).mkdir(parents=True, exist_ok=True)
        self.work_root = tmp / "tool-repo"
        self.work_root.mkdir(parents=True, exist_ok=True)
        (self.project_root / "cartopian.toml").write_text(
            _PROJECT_TABLE + roles_toml, encoding="utf-8"
        )
        (self.project_root / "cartopian.local.toml").write_text(
            f'[work_roots]\ntool-repo = "{self.work_root}"\n', encoding="utf-8"
        )
        entries = [{"id": "guard-proj", "path": str(self.project_root)}]
        entries.extend(extra_registry)
        (self.home / "projects.json").write_text(
            json.dumps(entries), encoding="utf-8"
        )

    def evaluate(self, payload: dict, environ=None):
        from cli import claude_hook

        return claude_hook.evaluate(
            payload,
            environ=environ if environ is not None else {},
            cartopian_home=self.home,
        )


# Governed targets by path-class: (relative path, class fragment, grant).
_CLASS_MATRIX = (
    ("specs/SPEC-01-001-x.md", "plan", "write:plan"),
    ("phases/PHASE-01-x.md", "plan", "write:plan"),
    ("IMPLEMENTATION_PLAN.md", "plan", "write:plan"),
    ("tasks/open/TASK-01-001-x.md", "lifecycle", "write:lifecycle"),
    ("STATE.md", "lifecycle", "write:lifecycle"),
    ("BACKLOG.md", "lifecycle", "write:lifecycle"),
    ("prompts/PROMPT-01-001.md", "lifecycle", "write:lifecycle"),
    ("decisions/DECISION-001.md", "decisions", "write:decisions"),
    ("reports/REPORT-01-001.md", "reports", "write:reports"),
    ("reviews/REVIEW-01-001.md", "reports", "write:reports"),
)


class TestDenyUngrantedGovernedWrites(unittest.TestCase):
    """Activated config, role without the matching grant → deny every class."""

    def test_deny_each_governed_path_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            for rel, klass, grant in _CLASS_MATRIX:
                target = str(fx.project_root / rel)
                for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                    decision = fx.evaluate(
                        _payload(tool, target),
                        environ={"CARTOPIAN_ROLE": "warden"},
                    )
                    self.assertEqual(
                        decision.action, "deny", msg=f"{tool} {rel} must deny"
                    )
                    self.assertIn("[guard]", decision.reason)
                    self.assertIn(target, decision.reason)
                    self.assertIn(klass, decision.reason)
                    self.assertIn(grant, decision.reason)

    def test_deny_work_root_write_without_worktree_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            target = str(fx.work_root / "src" / "main.py")
            decision = fx.evaluate(
                _payload("Write", target), environ={"CARTOPIAN_ROLE": "warden"}
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("[guard]", decision.reason)
            self.assertIn(target, decision.reason)
            self.assertIn("work-root", decision.reason)
            self.assertIn("write:worktree", decision.reason)

    def test_interactive_session_defaults_to_pm_role(self) -> None:
        # No CARTOPIAN_ROLE marker → the session resolves to the project's PM
        # role. In this activated config `pm` declares no grants, so it fails
        # closed and the write is denied.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            target = str(fx.project_root / "STATE.md")
            decision = fx.evaluate(_payload("Write", target), environ={})
            self.assertEqual(decision.action, "deny")
            self.assertIn("[guard]", decision.reason)
            self.assertIn("pm", decision.reason)

    def test_unclassified_project_file_gates_on_lifecycle(self) -> None:
        # Anything else inside an activated project directory falls to the PM
        # lifecycle surface rather than passing through ungated.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            target = str(fx.project_root / "notes.txt")
            decision = fx.evaluate(
                _payload("Write", target), environ={"CARTOPIAN_ROLE": "warden"}
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("write:lifecycle", decision.reason)


class TestUngatedPassthrough(unittest.TestCase):
    """A project declaring no grants anywhere is ungated: never denied."""

    def test_ungated_config_passes_all_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _UNGATED_ROLES)
            targets = [str(fx.project_root / rel) for rel, _, _ in _CLASS_MATRIX]
            targets.append(str(fx.work_root / "src" / "main.py"))
            for target in targets:
                decision = fx.evaluate(_payload("Write", target), environ={})
                self.assertEqual(
                    decision.action, "allow", msg=f"{target} must pass ungated"
                )


class TestZeroFootprint(unittest.TestCase):
    """Paths outside every registered project are never touched."""

    def test_outside_paths_allowed_even_with_broken_registry_entries(self) -> None:
        broken = (
            {"id": "no-path-entry"},
            {"path": "relative/not-absolute"},
            "not-an-object",
        )
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(
                Path(tmp), _ACTIVATED_ROLES, extra_registry=broken
            )
            outside = str(Path(tmp) / "elsewhere" / "notes.md")
            decision = fx.evaluate(
                _payload("Write", outside), environ={"CARTOPIAN_ROLE": "warden"}
            )
            self.assertEqual(decision.action, "allow")
            self.assertIsNone(decision.reason)

    def test_outside_paths_allowed_when_other_project_config_is_broken(self) -> None:
        # A registered project with a corrupt config must not block writes to
        # unrelated paths — its error stays inside its own boundary.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            (fx.project_root / "cartopian.toml").write_text(
                "not valid toml [[[", encoding="utf-8"
            )
            outside = str(Path(tmp) / "elsewhere" / "notes.md")
            decision = fx.evaluate(_payload("Write", outside), environ={})
            self.assertEqual(decision.action, "allow")

    def test_empty_registry_allows_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "carthome"
            home.mkdir()
            (home / "projects.json").write_text("[]", encoding="utf-8")
            from cli import claude_hook

            decision = claude_hook.evaluate(
                _payload("Write", str(Path(tmp) / "anywhere.md")),
                environ={},
                cartopian_home=home,
            )
            self.assertEqual(decision.action, "allow")

    def test_bash_tool_is_never_gated(self) -> None:
        # Shell-routed writes are the detection floor's residual, not this
        # hook's: Bash calls pass untouched even against governed artifacts.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            payload = {
                "tool_name": "Bash",
                "tool_input": {
                    "command": f'echo x > "{fx.project_root / "STATE.md"}"'
                },
                "cwd": str(fx.project_root),
            }
            decision = fx.evaluate(payload, environ={"CARTOPIAN_ROLE": "warden"})
            self.assertEqual(decision.action, "allow")


class TestResolutionFailureDenies(unittest.TestCase):
    """Unreadable registry/config inside a registered boundary fails closed."""

    def test_corrupt_project_config_denies_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            (fx.project_root / "cartopian.toml").write_text(
                "not valid toml [[[", encoding="utf-8"
            )
            target = str(fx.project_root / "STATE.md")
            decision = fx.evaluate(_payload("Write", target), environ={})
            self.assertEqual(decision.action, "deny")
            self.assertIn("[guard]", decision.reason)
            self.assertIn(target, decision.reason)

    def test_missing_project_config_denies_inside_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            (fx.project_root / "cartopian.toml").unlink()
            decision = fx.evaluate(
                _payload("Write", str(fx.project_root / "STATE.md")), environ={}
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("[guard]", decision.reason)

    def test_unmapped_work_root_in_activated_config_denies(self) -> None:
        # Activated project whose declared work roots cannot be resolved: the
        # target cannot be classified safely, so the write is denied.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            (fx.project_root / "cartopian.local.toml").unlink()
            decision = fx.evaluate(
                _payload("Write", str(fx.project_root / "STATE.md")),
                environ={"CARTOPIAN_ROLE": "warden"},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("[guard]", decision.reason)

    def test_corrupt_registry_denies_fail_closed(self) -> None:
        # With the registry unreadable, project boundaries cannot be
        # established — the hook must not silently allow.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "carthome"
            home.mkdir()
            (home / "projects.json").write_text("{not json", encoding="utf-8")
            from cli import claude_hook

            decision = claude_hook.evaluate(
                _payload("Write", str(Path(tmp) / "anywhere.md")),
                environ={},
                cartopian_home=home,
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("[guard]", decision.reason)


class TestAllowGrantedWrites(unittest.TestCase):
    """Activated config, role holding the matching grant → allow per class."""

    # One role per grant, each holding exactly the grant under test.
    _GRANT_ROLES = (
        "[roles.planner]\ngrants = [\"write:plan\"]\n"
        "[roles.lifecycler]\ngrants = [\"write:lifecycle\"]\n"
        "[roles.decider]\ngrants = [\"write:decisions\"]\n"
        "[roles.reporter]\ngrants = [\"write:reports\"]\n"
        "[roles.coder]\ngrants = [\"write:worktree\"]\n"
    )

    _GRANT_TO_ROLE = {
        "write:plan": "planner",
        "write:lifecycle": "lifecycler",
        "write:decisions": "decider",
        "write:reports": "reporter",
        "write:worktree": "coder",
    }

    def test_allow_each_governed_class_with_matching_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), self._GRANT_ROLES)
            for rel, _, grant in _CLASS_MATRIX:
                target = str(fx.project_root / rel)
                decision = fx.evaluate(
                    _payload("Write", target),
                    environ={"CARTOPIAN_ROLE": self._GRANT_TO_ROLE[grant]},
                )
                self.assertEqual(
                    decision.action, "allow", msg=f"{rel} with {grant} must allow"
                )

    def test_allow_work_root_write_with_worktree_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), self._GRANT_ROLES)
            decision = fx.evaluate(
                _payload("Write", str(fx.work_root / "src" / "main.py")),
                environ={"CARTOPIAN_ROLE": "coder"},
            )
            self.assertEqual(decision.action, "allow")

    def test_coder_with_worktree_grant_still_denied_on_governance(self) -> None:
        # Both boundaries out of the one hook: write:worktree alone does not
        # open the governed artifacts.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), self._GRANT_ROLES)
            decision = fx.evaluate(
                _payload("Write", str(fx.project_root / "STATE.md")),
                environ={"CARTOPIAN_ROLE": "coder"},
            )
            self.assertEqual(decision.action, "deny")

    def test_pm_without_worktree_grant_denied_in_work_root(self) -> None:
        # ...and the governance grants alone do not open the product tree.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), self._GRANT_ROLES)
            decision = fx.evaluate(
                _payload("Write", str(fx.work_root / "src" / "main.py")),
                environ={"CARTOPIAN_ROLE": "lifecycler"},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("write:worktree", decision.reason)

    def test_comma_separated_roles_union_grants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), self._GRANT_ROLES)
            environ = {"CARTOPIAN_ROLE": "planner,coder"}
            for target in (
                str(fx.project_root / "specs" / "SPEC-01-001-x.md"),
                str(fx.work_root / "src" / "main.py"),
            ):
                decision = fx.evaluate(_payload("Write", target), environ=environ)
                self.assertEqual(decision.action, "allow", msg=target)


class TestWindowsPathLogic(unittest.TestCase):
    """Windows-shaped paths (backslash, drive letter, case variance) classify
    correctly — exercised on POSIX via the flavor-parameterized helpers."""

    def test_membership_case_and_separator_insensitive(self) -> None:
        from cli.claude_hook import _is_within

        self.assertTrue(
            _is_within("c:\\proj\\gov\\STATE.md", "C:\\Proj\\Gov", ntpath)
        )
        self.assertTrue(
            _is_within("C:/Proj/Gov/specs/SPEC-01.md", "C:\\proj\\gov", ntpath)
        )
        self.assertTrue(_is_within("C:\\proj\\gov", "c:/PROJ/GOV", ntpath))

    def test_membership_rejects_sibling_prefix_and_other_drive(self) -> None:
        from cli.claude_hook import _is_within

        self.assertFalse(
            _is_within("C:\\proj\\government\\x.md", "C:\\proj\\gov", ntpath)
        )
        self.assertFalse(
            _is_within("D:\\proj\\gov\\STATE.md", "C:\\proj\\gov", ntpath)
        )

    def test_windows_classification(self) -> None:
        from cli.claude_hook import classify_project_path

        root = "C:\\Proj\\Gov"
        cases = (
            ("C:/Proj/Gov/Specs/SPEC-01.md", "plan", "write:plan"),
            ("c:\\proj\\gov\\state.MD", "lifecycle", "write:lifecycle"),
            ("C:\\PROJ\\GOV\\Tasks\\open\\TASK-01-001.md", "lifecycle", "write:lifecycle"),
            ("C:\\proj\\gov\\DECISIONS\\DECISION-001.md", "decisions", "write:decisions"),
            ("C:/proj/gov/Reviews/REVIEW-01.md", "reports", "write:reports"),
            ("C:\\proj\\gov\\random\\notes.txt", "project-file", "write:lifecycle"),
        )
        for target, expect_class, expect_grant in cases:
            klass, grant = classify_project_path(target, root, ntpath)
            self.assertEqual((klass, grant), (expect_class, expect_grant), msg=target)

    def test_posix_classification_stays_case_sensitive(self) -> None:
        # On POSIX, `Specs/` and `state.md` are different filesystem objects
        # from the governed `specs/` / `STATE.md` — they classify as plain
        # project files, not as the governed class.
        from cli.claude_hook import _is_within, classify_project_path

        klass, grant = classify_project_path("/p/gov/Specs/x.md", "/p/gov", posixpath)
        self.assertEqual((klass, grant), ("project-file", "write:lifecycle"))
        klass, _ = classify_project_path("/p/gov/state.md", "/p/gov", posixpath)
        self.assertEqual(klass, "project-file")
        self.assertFalse(_is_within("/p/govx/file", "/p/gov", posixpath))
        self.assertFalse(_is_within("/p/GOV/file", "/p/gov", posixpath))


class TestHookEndToEnd(unittest.TestCase):
    """Run the hook exactly as Claude Code does: script + stdin JSON."""

    def _run_hook(self, payload: dict, home: Path, role: str = "") -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)  # Path.home() source on native Windows
        env.pop("CARTOPIAN_ROLE", None)
        if role:
            env["CARTOPIAN_ROLE"] = role
        return subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=60,
        )

    def test_deny_emits_structured_guard_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            home = Path(tmp) / "home"
            home.mkdir()
            (home / ".cartopian").mkdir()
            (home / ".cartopian" / "projects.json").write_text(
                json.dumps([{"id": "guard-proj", "path": str(fx.project_root)}]),
                encoding="utf-8",
            )
            target = str(fx.project_root / "STATE.md")
            result = self._run_hook(_payload("Write", target), home, role="warden")
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            output = json.loads(result.stdout.decode("utf-8"))
            hso = output["hookSpecificOutput"]
            self.assertEqual(hso["hookEventName"], "PreToolUse")
            self.assertEqual(hso["permissionDecision"], "deny")
            self.assertIn("[guard]", hso["permissionDecisionReason"])
            self.assertIn(target, hso["permissionDecisionReason"])
            self.assertIn("write:lifecycle", hso["permissionDecisionReason"])

    def test_outside_path_is_perfectly_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            home = Path(tmp) / "home"
            home.mkdir()
            (home / ".cartopian").mkdir()
            (home / ".cartopian" / "projects.json").write_text(
                json.dumps([{"id": "guard-proj", "path": str(fx.project_root)}]),
                encoding="utf-8",
            )
            outside = str(Path(tmp) / "elsewhere" / "notes.md")
            result = self._run_hook(_payload("Write", outside), home, role="warden")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

    def test_no_registry_at_all_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            result = self._run_hook(
                _payload("Write", str(Path(tmp) / "x.md")), home
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")


class TestDispatchExportsRole(unittest.TestCase):
    """`cartopian dispatch` carries the role to the child via CARTOPIAN_ROLE —
    the env mechanism the hook reads for session-role identification."""

    def test_dispatch_exports_cartopian_role(self) -> None:
        from cli.commands import dispatch
        from tests.scaffold import project_scaffold

        toml = (
            "[project]\n"
            'id = "dispatch-proj"\n'
            'name = "Dispatch Project"\n'
            'protocol_version = "v0.3.0"\n'
            "\n"
            "[roles]\n"
            'coder = "Implements tasks per spec."\n'
            "\n"
            "[handoffs.coder]\n"
            'agent = "/bin/true"\n'
            'timeout = "30m"\n'
        )
        with project_scaffold(cartopian_toml=toml) as scaffold, \
                tempfile.TemporaryDirectory() as fake_home:
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-004-role-export.md",
                "# TASK-01-004: role export\n",
            )
            scaffold.write(
                "prompts/PROMPT-01-004.md", "# PROMPT-01-004\n\n## Your task\n\nx\n"
            )
            captured = {}

            def fake_popen(argv, cwd=None, env=None, **kwargs):
                captured["env"] = env
                return mock.Mock(pid=12345)

            args = argparse.Namespace(task_path=str(task_path), role="coder")
            out, err = io.StringIO(), io.StringIO()
            with mock.patch(
                "cli.commands.dispatch.Path.home", return_value=Path(fake_home)
            ), mock.patch(
                "cli.commands.dispatch.shutil.which", return_value="/bin/true"
            ), mock.patch(
                "cli.commands.dispatch.subprocess.Popen", side_effect=fake_popen
            ):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = dispatch.handler(args)
            self.assertEqual(rc, 0, msg=err.getvalue())
            self.assertEqual(captured["env"].get("CARTOPIAN_ROLE"), "coder")


class TestInstallerHookRegistration(unittest.TestCase):
    """`scripts/install.py --claude-hook <project-dir>` writes the project-level
    settings registration; it is operator-invoked and never global."""

    def test_registers_hook_in_project_settings(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            import install
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workdir"
            project_dir.mkdir()
            actions = []
            install.register_claude_hook(
                project_dir, Path(tmp) / "install-root", actions
            )
            settings_path = project_dir / ".claude" / "settings.json"
            self.assertTrue(settings_path.exists())
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            matchers = settings["hooks"]["PreToolUse"]
            self.assertEqual(len(matchers), 1)
            self.assertEqual(
                matchers[0]["matcher"], "Write|Edit|MultiEdit|NotebookEdit"
            )
            self.assertIn("claude_hook.py", matchers[0]["hooks"][0]["command"])

    def test_registration_is_idempotent_and_preserves_settings(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            import install
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workdir"
            (project_dir / ".claude").mkdir(parents=True)
            settings_path = project_dir / ".claude" / "settings.json"
            settings_path.write_text(
                json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}),
                encoding="utf-8",
            )
            actions = []
            install.register_claude_hook(project_dir, Path(tmp) / "root", actions)
            install.register_claude_hook(project_dir, Path(tmp) / "root", actions)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings["permissions"], {"allow": ["Bash(ls:*)"]})
            self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)


if __name__ == "__main__":
    unittest.main()
