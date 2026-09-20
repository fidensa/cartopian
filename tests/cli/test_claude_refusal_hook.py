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


def _read_payload(tool_name: str, path: str = None, cwd: str = "/tmp") -> dict:
    """A PreToolUse payload for a read tool, shaped as Claude Code sends it.

    ``Read``/``NotebookRead`` carry ``file_path``/``notebook_path``; the
    search tools ``Glob``/``Grep`` carry an optional ``path`` (absent → the
    search runs over the session cwd).
    """
    tool_input = {}
    if path is not None:
        if tool_name == "NotebookRead":
            tool_input["notebook_path"] = path
        elif tool_name in ("Glob", "Grep"):
            tool_input["path"] = path
        else:
            tool_input["file_path"] = path
    if tool_name == "Glob":
        tool_input["pattern"] = "**/*"
    elif tool_name == "Grep":
        tool_input["pattern"] = "needle"
    return {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
    }


_PROJECT_TABLE = (
    "[project]\n"
    'id = "guard-proj"\n'
    'name = "Guard Project"\n'
    'project_schema_version = "v0.13.0"\n'
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
_UNGATED_ROLES = (
    "[roles.coder]\n"
    'description = "Implements tasks per spec."\n'
)

# Read-axis config: `assignee` holds the default dispatched-assignee grants
# (`coder-like` = read:prompts + read:work-roots + write:worktree +
# write:reports); `curator`
# holds the two read grants the assignee deliberately lacks; `boaster` has a
# prose description claiming everything but an empty grant list — enforcement
# must key on the grants, never on the name or the description.
_READ_ROLES = (
    "[roles.assignee]\n"
    'description = "Default dispatched assignee."\n'
    'grants = ["coder-like"]\n'
    "[roles.curator]\n"
    'description = "Holds the governance/reports read grants."\n'
    'grants = ["read:governance", "read:reports"]\n'
    "[roles.boaster]\n"
    'description = "Cleared to read every artifact in the project."\n'
    "grants = []\n"
    "[roles.governance-only]\n"
    'description = "Reads only governance artifacts."\n'
    'grants = ["read:governance"]\n'
    "[roles.project-reader]\n"
    'description = "Reads ordinary project artifacts, but no work roots."\n'
    'grants = ["read:governance", "read:prompts", "read:reports"]\n'
)

_REVIEW_ASSIGNMENT_ROLES = (
    "[roles.quality-gate]\n"
    'description = "Reviews assigned evidence."\n'
    'grants = ["reviewer-like"]\n'
    "[roles.builder]\n"
    'description = "Implements assigned work."\n'
    'grants = ["coder-like"]\n'
    "[reviews]\n"
    'planning = "required"\n'
    'planning_role = "quality-gate"\n'
    'task_closure = "required"\n'
    'task_role = "quality-gate"\n'
)

# Governance-class read targets (management/strategy artifacts and specs; an
# unclassified project file also falls to governance on the read axis).
_GOVERNANCE_READ_TARGETS = (
    "specs/SPEC-01-001.md",
    "phases/PHASE-01.md",
    "IMPLEMENTATION_PLAN.md",
    "REQUIREMENTS.md",
    "tasks/open/TASK-01-001.md",
    "STATE.md",
    "BACKLOG.md",
    "decisions/DECISION-001.md",
    "cartopian.toml",
    "notes.txt",
)

_REPORTS_READ_TARGETS = (
    "reports/REPORT-01-001.md",
    "reviews/REVIEW-01-001.md",
)


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

    def evaluate(self, payload: dict, environ=None, **kwargs):
        from cli import claude_hook

        return claude_hook.evaluate(
            payload,
            environ=environ if environ is not None else {},
            cartopian_home=self.home,
            **kwargs,
        )


def _claiming_project(
    tmp: Path, project_id: str, work_root: Path, roles_toml: str
) -> tuple[Path, dict]:
    """Create another registered project that claims ``work_root``."""
    project_root = tmp / project_id
    project_root.mkdir(parents=True)
    project_root.joinpath("cartopian.toml").write_text(
        "[project]\n"
        f'id = "{project_id}"\n'
        f'name = "{project_id}"\n'
        'project_schema_version = "v0.13.0"\n'
        'work_roots = ["shared"]\n\n'
        + roles_toml,
        encoding="utf-8",
    )
    project_root.joinpath("cartopian.local.toml").write_text(
        f'[work_roots]\nshared = "{work_root}"\n', encoding="utf-8"
    )
    return project_root, {"id": project_id, "path": str(project_root)}


def _darwin_data_volume_alias(path: Path) -> str | None:
    """Return a live /System/Volumes/Data firmlink spelling when available."""
    resolved = os.path.realpath(path)
    candidate = "/System/Volumes/Data" + resolved
    try:
        if os.path.lexists(candidate) and os.path.samefile(resolved, candidate):
            return candidate
    except OSError:
        pass
    return None


# Governed targets by path-class: (relative path, class fragment, grant).
_CLASS_MATRIX = (
    ("specs/SPEC-01-001.md", "plan", "write:plan"),
    ("phases/PHASE-01.md", "plan", "write:plan"),
    ("IMPLEMENTATION_PLAN.md", "plan", "write:plan"),
    ("tasks/open/TASK-01-001.md", "lifecycle", "write:lifecycle"),
    ("STATE.md", "lifecycle", "write:lifecycle"),
    ("BACKLOG.md", "lifecycle", "write:lifecycle"),
    ("prompts/PROMPT-01-001.md", "prompts", "write:lifecycle"),
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
        # Command text is not a sound path-classification boundary. Bash calls
        # pass through this hook; activated POSIX launches contain their writes
        # with the separate process-scoped OS sandbox.
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


class TestDispatchedProjectBinding(unittest.TestCase):
    """A mediated handoff's project identity outranks registry ordering."""

    def _write_registry(self, fx: _HookFixture, entries) -> None:
        fx.home.joinpath("projects.json").write_text(
            json.dumps(list(entries)), encoding="utf-8"
        )

    def test_activated_bound_session_denies_delegation_and_worktree_tools(self) -> None:
        from cli import claude_hook

        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _ACTIVATED_ROLES)
            environ = {
                "CARTOPIAN_ROLE": "warden",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for tool_name in sorted(claude_hook.UNCONTAINED_SESSION_TOOLS):
                decision = fx.evaluate(
                    {"tool_name": tool_name, "tool_input": {}, "cwd": str(fx.project_root)},
                    environ=environ,
                )
                self.assertEqual(decision.action, "deny", msg=tool_name)
                self.assertIn("outside the captured", decision.reason)

    def test_unresolvable_unrelated_registry_paths_cannot_crash_bound_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _ACTIVATED_ROLES)
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            for poisoned in ("/tmp/\x00poison", "/tmp/\ud800poison"):
                self._write_registry(
                    fx,
                    ({"id": "poison", "path": poisoned}, active),
                )
                decision = fx.evaluate(
                    _payload("Write", str(fx.project_root / "STATE.md")),
                    environ={
                        "CARTOPIAN_ROLE": "warden",
                        "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                    },
                )
                self.assertEqual(decision.action, "deny", msg=repr(poisoned))
                self.assertIn("write:lifecycle", decision.reason)

    def test_ungated_bound_session_allows_session_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _UNGATED_ROLES)
            decision = fx.evaluate(
                {"tool_name": "Agent", "tool_input": {}, "cwd": str(fx.project_root)},
                environ={"CARTOPIAN_LAUNCH_CWD": str(fx.project_root)},
            )
            self.assertEqual(decision.action, "allow")

    def test_bound_project_grant_wins_duplicate_root_in_either_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            _foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", fx.work_root, _ACTIVATED_ROLES
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            target = str(fx.work_root / "src" / "main.py")
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for entries in ((foreign, active), (active, foreign)):
                self._write_registry(fx, entries)
                decision = fx.evaluate(
                    _read_payload("Read", target), environ=environ
                )
                self.assertEqual(decision.action, "allow", msg=entries)

    def test_bound_broad_search_denies_nested_foreign_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            foreign_root = fx.project_root / "vendor" / "foreign-project"
            foreign_work = tmp / "foreign-work"
            foreign_root.mkdir(parents=True)
            foreign_work.mkdir()
            foreign_root.joinpath("cartopian.toml").write_text(
                "[project]\n"
                'id = "foreign-project"\n'
                'name = "Foreign Project"\n'
                'project_schema_version = "v0.13.0"\n'
                'work_roots = ["product"]\n\n'
                + _READ_ROLES,
                encoding="utf-8",
            )
            foreign_root.joinpath("cartopian.local.toml").write_text(
                f'[work_roots]\nproduct = "{foreign_work}"\n',
                encoding="utf-8",
            )
            self._write_registry(
                fx,
                (
                    {"id": "guard-proj", "path": str(fx.project_root)},
                    {"id": "foreign-project", "path": str(foreign_root)},
                ),
            )
            environ = {
                "CARTOPIAN_ROLE": "assignee,curator",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for tool_name in ("Glob", "Grep"):
                decision = fx.evaluate(
                    _read_payload(tool_name, str(fx.project_root)),
                    environ=environ,
                )
                self.assertEqual(decision.action, "deny", msg=tool_name)
                self.assertIn("never cross project boundaries", decision.reason)
                self.assertIn("foreign-project", decision.reason)

    def test_ancestor_work_root_cannot_override_active_project_read_classes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            (fx.project_root / "cartopian.local.toml").write_text(
                f'[work_roots]\ntool-repo = "{tmp}"\n', encoding="utf-8"
            )
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            exact = fx.evaluate(
                _read_payload("Read", str(fx.project_root / "STATE.md")),
                environ=environ,
            )
            self.assertEqual(exact.action, "deny")
            self.assertIn("read:governance", exact.reason)

            for tool_name in ("Glob", "Grep"):
                broad = fx.evaluate(
                    _read_payload(tool_name, str(tmp)), environ=environ
                )
                self.assertEqual(broad.action, "deny", msg=tool_name)
                self.assertIn("read:governance", broad.reason)

    def test_foreign_grant_cannot_widen_bound_project(self) -> None:
        denied_coder = (
            "[roles.coder]\n"
            'description = "Bound coder without worktree authority."\n'
            "grants = []\n"
        )
        allowed_coder = (
            "[roles.coder]\n"
            'description = "Foreign coder."\n'
            'grants = ["coder-like"]\n'
        )
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, denied_coder)
            _foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", fx.work_root, allowed_coder
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            target = str(fx.work_root / "src" / "main.py")
            environ = {
                "CARTOPIAN_ROLE": "coder",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for entries in ((foreign, active), (active, foreign)):
                self._write_registry(fx, entries)
                decision = fx.evaluate(_payload("Write", target), environ=environ)
                self.assertEqual(decision.action, "deny", msg=entries)
                self.assertIn("guard-proj", decision.reason)
                self.assertNotIn("foreign-project' requires", decision.reason)

    def test_unbound_equal_claims_fail_closed_instead_of_using_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            _foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", fx.work_root, _READ_ROLES
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            target = str(fx.work_root / "src" / "main.py")
            for entries in ((foreign, active), (active, foreign)):
                self._write_registry(fx, entries)
                decision = fx.evaluate(
                    _payload("Write", target),
                    environ={"CARTOPIAN_ROLE": "assignee"},
                )
                self.assertEqual(decision.action, "deny", msg=entries)
                self.assertIn("equally specific", decision.reason)
                self.assertIn("foreign-project", decision.reason)
                self.assertIn("guard-proj", decision.reason)

    def test_unbound_unicode_alias_claims_remain_equally_specific(self) -> None:
        from cli import claude_hook

        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            composed = tmp / "caf\N{LATIN SMALL LETTER E WITH ACUTE}-product"
            decomposed = tmp / "cafe\N{COMBINING ACUTE ACCENT}-product"
            composed.mkdir()
            fx.project_root.joinpath("cartopian.local.toml").write_text(
                f'[work_roots]\ntool-repo = "{composed}"\n', encoding="utf-8"
            )
            _foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", decomposed, _READ_ROLES
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            self._write_registry(fx, (active, foreign))

            with (
                mock.patch.object(
                    claude_hook,
                    "_live_path_is_normalization_insensitive",
                    return_value=True,
                ),
                mock.patch.object(
                    claude_hook, "_live_path_is_case_insensitive", return_value=False
                ),
            ):
                decision = claude_hook.evaluate(
                    _payload("Write", str(composed / "src" / "main.py")),
                    environ={"CARTOPIAN_ROLE": "assignee"},
                    cartopian_home=fx.home,
                    resolve=lambda path: path,
                )

            self.assertEqual(decision.action, "deny")
            self.assertIn("equally specific", decision.reason)

    def test_nested_project_depth_uses_unicode_normalized_keys(self) -> None:
        from cli import claude_hook

        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            composed_name = "\N{LATIN SMALL LETTER E WITH ACUTE}" * 5
            decomposed_name = "e\N{COMBINING ACUTE ACCENT}" * 5
            outer = tmp / composed_name
            outer.mkdir()
            outer.joinpath("cartopian.toml").write_text(
                "[project]\n"
                'id = "outer"\n'
                'name = "Outer"\n'
                'project_schema_version = "v0.13.0"\n'
                "work_roots = []\n\n"
                + _READ_ROLES,
                encoding="utf-8",
            )
            inner = outer / "i"
            inner.mkdir()
            outer_alias = tmp / decomposed_name
            home = tmp / "carthome"
            home.mkdir()
            home.joinpath("projects.json").write_text(
                json.dumps(
                    [
                        {"id": "outer", "path": str(outer_alias)},
                        {"id": "inner", "path": str(inner)},
                    ]
                ),
                encoding="utf-8",
            )

            def resolve_alias(path):
                return str(outer) if str(path) == str(outer_alias) else str(path)

            with (
                mock.patch.object(
                    claude_hook,
                    "_live_path_is_normalization_insensitive",
                    return_value=True,
                ),
                mock.patch.object(
                    claude_hook, "_live_path_is_case_insensitive", return_value=False
                ),
            ):
                decision = claude_hook.evaluate(
                    _payload("Write", str(inner / "STATE.md")),
                    environ={
                        "CARTOPIAN_ROLE": "assignee",
                        "CARTOPIAN_LAUNCH_CWD": str(outer_alias),
                    },
                    cartopian_home=home,
                    resolve=resolve_alias,
                )

            self.assertEqual(decision.action, "deny")
            self.assertIn("Capability grants never cross project", decision.reason)
            self.assertIn("outer", decision.reason)
            self.assertIn("inner", decision.reason)

    def test_dispatched_unregistered_project_is_still_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _ACTIVATED_ROLES)
            self._write_registry(fx, ())
            environ = {
                "CARTOPIAN_ROLE": "warden",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for target in (
                fx.project_root / "STATE.md",
                fx.work_root / "src" / "main.py",
            ):
                decision = fx.evaluate(
                    _payload("Write", str(target)), environ=environ
                )
                self.assertEqual(decision.action, "deny", msg=str(target))

    def test_bound_session_cannot_inherit_foreign_project_grants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", tmp / "foreign-work", _READ_ROLES
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            self._write_registry(fx, (foreign, active))
            decision = fx.evaluate(
                _payload("Write", str(foreign_root / "notes.txt")),
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("Capability grants never cross project", decision.reason)

    def test_bound_session_denies_foreign_only_external_work_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            foreign_work = tmp / "foreign-work"
            foreign_work.mkdir()
            _foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", foreign_work, _READ_ROLES
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for entries in ((foreign, active), (active, foreign)):
                self._write_registry(fx, entries)
                decision = fx.evaluate(
                    _payload("Write", str(foreign_work / "notes.txt")),
                    environ=environ,
                )
                self.assertEqual(decision.action, "deny", msg=entries)
                self.assertIn("Capability grants never cross project", decision.reason)
                self.assertIn("foreign-project", decision.reason)

    def test_broken_foreign_config_cannot_erase_bound_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            foreign_work = tmp / "foreign-work"
            foreign_work.mkdir()
            foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", foreign_work, _READ_ROLES
            )
            foreign_root.joinpath("cartopian.toml").write_text(
                "not valid toml [[[", encoding="utf-8"
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            self._write_registry(fx, (foreign, active))
            decision = fx.evaluate(
                _payload("Write", str(foreign_work / "new.txt")),
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("outside dispatched Cartopian project", decision.reason)

    def test_bound_activated_session_cannot_write_arbitrary_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            outside = tmp / "unrelated-host-tree" / "new.txt"
            outside.parent.mkdir()
            decision = fx.evaluate(
                _payload("Write", str(outside)),
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("outside dispatched Cartopian project", decision.reason)

            # The legacy unbound adapter remains zero-footprint outside every
            # registered project/work-root claim.
            unbound = fx.evaluate(
                _payload("Write", str(outside)),
                environ={"CARTOPIAN_ROLE": "assignee"},
            )
            self.assertEqual(unbound.action, "allow")

    def test_bound_work_root_structured_write_routes_to_sandboxed_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _READ_ROLES)
            decision = fx.evaluate(
                _payload("Edit", str(fx.work_root / "src" / "main.py")),
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("role holds write:worktree", decision.reason)
            self.assertIn("Use Bash", decision.reason)

    def test_launch_captured_work_root_identity_blocks_directory_pivots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            child = fx.work_root / "child"
            child.mkdir()
            fx.project_root.joinpath("cartopian.toml").write_text(
                _PROJECT_TABLE.replace(
                    'work_roots = ["tool-repo"]',
                    'work_roots = ["tool-repo", "child"]',
                )
                + _READ_ROLES,
                encoding="utf-8",
            )
            fx.project_root.joinpath("cartopian.local.toml").write_text(
                "[work_roots]\n"
                f'tool-repo = "{fx.work_root}"\n'
                f'child = "{child}"\n',
                encoding="utf-8",
            )
            captured = {}
            for name, path in (("tool-repo", fx.work_root), ("child", child)):
                info = path.lstat()
                captured[name] = (str(path), info.st_dev, info.st_ino)
            original = fx.work_root / "child-original"
            child.rename(original)
            outside = tmp / "outside"
            outside.mkdir()
            try:
                child.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks unavailable on this platform")

            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            decision = fx.evaluate(
                _payload("Write", str(child / "escaped.txt")),
                environ=environ,
                bound_work_roots=captured,
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("changed filesystem identity", decision.reason)

            child.unlink()
            child.symlink_to(fx.project_root / "specs", target_is_directory=True)
            decision = fx.evaluate(
                _payload("Write", str(child / "plan.md")),
                environ=environ,
                bound_work_roots=captured,
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("changed filesystem identity", decision.reason)

    def test_firmlink_spelling_does_not_win_duplicate_work_root_specificity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            alias = _darwin_data_volume_alias(fx.work_root)
            if alias is None:
                self.skipTest("macOS Data-volume firmlink alias unavailable")
            denied_assignee = (
                "[roles.assignee]\n"
                'description = "No work-root grant."\n'
                "grants = []\n"
            )
            _foreign_root, foreign = _claiming_project(
                tmp, "foreign-project", Path(alias), denied_assignee
            )
            active = {"id": "guard-proj", "path": str(fx.project_root)}
            self._write_registry(fx, (foreign, active))
            decision = fx.evaluate(
                _payload("Write", str(fx.work_root / "new.txt")),
                environ={"CARTOPIAN_ROLE": "assignee"},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("equally specific", decision.reason)

    def test_firmlink_spelling_cannot_hide_physically_nested_project(self) -> None:
        inner_pm = (
            "[roles.pm]\n"
            'description = "Nested project denies lifecycle writes."\n'
            "grants = []\n"
        )
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _PM_LIFECYCLE_ROLES)
            outer_alias = _darwin_data_volume_alias(fx.project_root)
            if outer_alias is None:
                self.skipTest("macOS Data-volume firmlink alias unavailable")
            inner_work = tmp / "inner-work"
            inner_work.mkdir()
            inner_root, inner = _claiming_project(
                fx.project_root, "inner", inner_work, inner_pm
            )
            outer = {"id": "guard-proj", "path": outer_alias}
            self._write_registry(fx, (outer, inner))
            decision = fx.evaluate(
                _payload("Write", str(inner_root / "STATE.md")),
                environ={
                    "CARTOPIAN_ROLE": "pm",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("Capability grants never cross project", decision.reason)
            self.assertIn("inner", decision.reason)

    def test_dispatched_session_cannot_edit_loaded_claude_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _ACTIVATED_ROLES)
            user_home = tmp / "operator-home"
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                "HOME": str(user_home),
            }
            for target in (
                fx.project_root / ".claude" / "settings.json",
                fx.project_root / ".claude" / "settings.local.json",
                user_home / ".claude" / "settings.json",
            ):
                decision = fx.evaluate(
                    _payload("Edit", str(target)), environ=environ
                )
                self.assertEqual(decision.action, "deny", msg=str(target))
                self.assertIn("cannot mutate its own enforcement policy", decision.reason)

            # Claude has no user-scope settings.local.json source, so this is
            # not classified as a loaded-settings mutation. The activated
            # dispatch binding still denies it because it lies outside the
            # dispatched project and declared work roots.
            decision = fx.evaluate(
                _payload(
                    "Edit",
                    str(user_home / ".claude" / "settings.local.json"),
                ),
                environ=environ,
            )
            self.assertEqual(decision.action, "deny")
            self.assertNotIn("cannot mutate its own enforcement policy", decision.reason)
            self.assertIn("outside dispatched Cartopian project", decision.reason)

    def test_macos_filesystem_alias_probes_stop_at_mount_boundary(self) -> None:
        from cli import claude_hook

        with tempfile.TemporaryDirectory() as tmp_raw:
            mount_parent = Path(tmp_raw)
            numeric_root = mount_parent / "123"
            numeric_child = numeric_root / "456"
            numeric_child.mkdir(parents=True)
            real_device = claude_hook._path_device

            def device(path):
                if Path(path) == mount_parent:
                    return 1
                if Path(path) in (numeric_root, numeric_child):
                    return 2
                return real_device(path)

            for probe in (
                claude_hook._darwin_path_is_case_insensitive,
                claude_hook._darwin_path_is_normalization_insensitive,
            ):
                with self.subTest(probe=probe.__name__), mock.patch.object(
                    claude_hook, "_path_device", side_effect=device
                ), mock.patch.object(
                    claude_hook.os, "listdir", wraps=os.listdir
                ) as listdir:
                    probe.cache_clear()
                    self.assertFalse(probe(str(numeric_child)))
                    self.assertNotIn(
                        mock.call(str(mount_parent)), listdir.call_args_list
                    )
                probe.cache_clear()

    def test_mountpoint_parent_alias_membership_uses_existing_inode_identity(self) -> None:
        from cli import claude_hook

        root = "/Volumes/Foo/project"
        alias_root = "/volumes/foo/project"
        target = alias_root + "/specs/new.md"

        def lexists(path):
            return path in {root, alias_root}

        def samefile(left, right):
            return {left, right} == {root, alias_root}

        with mock.patch.object(
            claude_hook.os.path, "lexists", side_effect=lexists
        ), mock.patch.object(
            claude_hook.os.path, "samefile", side_effect=samefile
        ):
            self.assertEqual(
                claude_hook._filesystem_relative_path(target, root, os.path),
                os.path.join("specs", "new.md"),
            )
            self.assertTrue(claude_hook._is_within(target, root, os.path))


class TestFilesystemAliasBoundaries(unittest.TestCase):
    """One inode cannot inherit a weaker grant from another pathname."""

    def test_external_project_symlink_denies_lexical_and_backing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _READ_ROLES)
            (fx.project_root / "specs").rmdir()
            backing = fx.work_root / "specs-backing"
            backing.mkdir()
            (fx.project_root / "specs").symlink_to(backing, target_is_directory=True)
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            lexical = fx.project_root / "specs" / "new.md"
            direct = backing / "new.md"
            for payload in (
                _payload("Write", str(lexical)),
                _payload("Write", str(direct)),
                _read_payload("Read", str(lexical)),
            ):
                decision = fx.evaluate(payload, environ=environ)
                self.assertEqual(decision.action, "deny", msg=payload)

    def test_symlink_followed_by_dotdot_cannot_escape_captured_work_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            outside = tmp / "outside"
            destination = outside / "dir"
            destination.mkdir(parents=True)
            link = fx.work_root / "link"
            try:
                link.symlink_to(destination, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks unavailable on this platform")
            root_info = fx.work_root.lstat()
            captured = {
                "tool-repo": (
                    str(fx.work_root),
                    root_info.st_dev,
                    root_info.st_ino,
                )
            }
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            targets = (
                (str(link / ".." / "escaped-absolute.txt"), "/tmp"),
                ("link/../escaped-relative.txt", str(fx.work_root)),
            )
            for target, cwd in targets:
                decision = fx.evaluate(
                    _read_payload("Read", target, cwd=cwd),
                    environ=environ,
                    bound_work_roots=captured,
                )
                self.assertEqual(decision.action, "deny", msg=target)
                self.assertIn("outside dispatched Cartopian project", decision.reason)

    def test_request_directory_symlink_cannot_bypass_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _READ_ROLES)
            backing = fx.work_root / "request-backing"
            backing.mkdir()
            (fx.project_root / "requests").symlink_to(
                backing, target_is_directory=True
            )
            decision = fx.evaluate(
                _payload(
                    "Write", str(fx.project_root / "requests" / "REQUEST-1.md")
                ),
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")

    def test_internal_symlink_cannot_reverse_alias_across_path_classes(self) -> None:
        roles = (
            "[roles.reporter]\n"
            'description = "Writes reports."\n'
            'grants = ["write:reports"]\n'
        )
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), roles)
            report = fx.project_root / "reports" / "shared.md"
            report.write_text("protected\n", encoding="utf-8")
            request = fx.project_root / "requests" / "REQUEST.md"
            request.parent.mkdir(exist_ok=True)
            request.symlink_to(Path("..") / "reports" / "shared.md")

            # Addressing the permissive reports spelling directly must still
            # fail: activated projects reject every alternate project name
            # before a path-class grant is evaluated.
            decision = fx.evaluate(
                _payload("Write", str(report)),
                environ={
                    "CARTOPIAN_ROLE": "reporter",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("symbolic link", decision.reason)

    def test_symlinked_config_backing_is_raw_config_even_when_unbound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _READ_ROLES)
            config = fx.project_root / "cartopian.toml"
            backing = fx.work_root / "ordinary.toml"
            config.replace(backing)
            config.symlink_to(backing)
            for target in (config, backing):
                decision = fx.evaluate(
                    _payload("Edit", str(target)),
                    environ={"CARTOPIAN_ROLE": "assignee"},
                )
                self.assertEqual(decision.action, "deny", msg=str(target))
                self.assertIn("raw edits to Cartopian config", decision.reason)

    def test_hardlinked_project_files_cannot_be_written_via_work_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _READ_ROLES)
            requirement = fx.project_root / "REQUIREMENTS.md"
            requirement.write_text("protected\n", encoding="utf-8")
            aliases = (
                (fx.project_root / "cartopian.toml", fx.work_root / "config.txt"),
                (requirement, fx.work_root / "requirements.txt"),
            )
            try:
                for source, alias in aliases:
                    os.link(source, alias)
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for _source, alias in aliases:
                decision = fx.evaluate(
                    _payload("Write", str(alias)), environ=environ
                )
                self.assertEqual(decision.action, "deny", msg=str(alias))


class TestResolutionFailureDenies(unittest.TestCase):
    """Unreadable registry/config inside a registered boundary fails closed."""

    def test_corrupt_bound_project_config_denies_possible_external_work_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            (fx.project_root / "cartopian.toml").write_text(
                "not valid toml [[[", encoding="utf-8"
            )
            target = str(fx.work_root / "escape.txt")
            decision = fx.evaluate(
                _payload("Write", target),
                environ={
                    "CARTOPIAN_ROLE": "warden",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("dispatched Cartopian project", decision.reason)
            self.assertIn("failing closed", decision.reason)

    def test_unrepresentable_bound_work_root_denies_inside_and_external_targets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            (fx.project_root / "cartopian.local.toml").write_text(
                '[work_roots]\ntool-repo = "/tmp/\\u0000poison"\n',
                encoding="utf-8",
            )
            environ = {
                "CARTOPIAN_ROLE": "warden",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for target in (
                fx.project_root / "STATE.md",
                fx.work_root / "escape.txt",
            ):
                decision = fx.evaluate(
                    _payload("Write", str(target)), environ=environ
                )
                self.assertEqual(decision.action, "deny", msg=str(target))
                self.assertIn("not representable", decision.reason)

    def test_dangling_registry_symlink_is_unreadable_not_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _ACTIVATED_ROLES)
            registry = fx.home / "projects.json"
            registry.unlink()
            registry.symlink_to(fx.home / "missing-projects.json")
            decision = fx.evaluate(
                _payload("Write", str(fx.project_root / "STATE.md")),
                environ={},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("registry is unreadable", decision.reason)

    def test_registry_symlink_and_hardlink_aliases_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            for alias_kind in ("symlink", "hardlink"):
                fx = _HookFixture(tmp / alias_kind, _ACTIVATED_ROLES)
                registry = fx.home / "projects.json"
                backing = fx.work_root / "ordinary.json"
                if alias_kind == "symlink":
                    registry.replace(backing)
                    registry.symlink_to(backing)
                else:
                    try:
                        os.link(registry, backing)
                    except OSError as exc:
                        self.skipTest(f"hard links unavailable: {exc}")
                decision = fx.evaluate(
                    _payload("Write", str(backing)),
                    environ={
                        "CARTOPIAN_ROLE": "warden",
                        "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                    },
                )
                self.assertEqual(decision.action, "deny", msg=alias_kind)
                self.assertIn("registry is unreadable", decision.reason)

    def test_dangling_global_and_local_config_symlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            for config_kind in ("global", "local"):
                fx = _HookFixture(tmp / config_kind, _ACTIVATED_ROLES)
                path = (
                    fx.home / "cartopian.toml"
                    if config_kind == "global"
                    else fx.project_root / "cartopian.local.toml"
                )
                if path.exists():
                    path.unlink()
                path.symlink_to(path.with_name("missing.toml"))
                decision = fx.evaluate(
                    _payload("Write", str(fx.project_root / "STATE.md")),
                    environ={
                        "CARTOPIAN_ROLE": "warden",
                        "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                    },
                )
                self.assertEqual(decision.action, "deny", msg=config_kind)
                self.assertIn("dangling", decision.reason)

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
        "[roles.planner]\ndescription = \"Plans.\"\n"
        "grants = [\"write:plan\"]\n"
        "[roles.lifecycler]\ndescription = \"Runs lifecycle.\"\n"
        "grants = [\"write:lifecycle\"]\n"
        "[roles.decider]\ndescription = \"Records decisions.\"\n"
        "grants = [\"write:decisions\"]\n"
        "[roles.reporter]\ndescription = \"Writes reports.\"\n"
        "grants = [\"write:reports\"]\n"
        "[roles.coder]\ndescription = \"Writes product code.\"\n"
        "grants = [\"write:worktree\"]\n"
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


class TestDenyUngrantedReads(unittest.TestCase):
    """Activated config: a session without the matching read grant is denied
    reads of that path-class. The default assignee grants (`coder-like`)
    deliberately exclude `read:governance` and `read:reports`."""

    def test_assignee_denied_governance_class_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            environ = {"CARTOPIAN_ROLE": "assignee"}
            for rel in _GOVERNANCE_READ_TARGETS:
                target = str(fx.project_root / rel)
                decision = fx.evaluate(_read_payload("Read", target), environ=environ)
                self.assertEqual(
                    decision.action, "deny", msg=f"Read {rel} must deny"
                )
                self.assertIn("[guard]", decision.reason)
                self.assertIn(target, decision.reason)
                self.assertIn("read:governance", decision.reason)

    def test_assignee_denied_reports_class_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            environ = {"CARTOPIAN_ROLE": "assignee"}
            for rel in _REPORTS_READ_TARGETS:
                target = str(fx.project_root / rel)
                decision = fx.evaluate(_read_payload("Read", target), environ=environ)
                self.assertEqual(
                    decision.action, "deny", msg=f"Read {rel} must deny"
                )
                self.assertIn("[guard]", decision.reason)
                self.assertIn("read:reports", decision.reason)

    def test_notebook_read_gates_like_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            target = str(fx.project_root / "specs" / "SPEC-01-001-x.ipynb")
            decision = fx.evaluate(
                _read_payload("NotebookRead", target),
                environ={"CARTOPIAN_ROLE": "assignee"},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("read:governance", decision.reason)

    def test_search_tools_denied_on_ungranted_directories(self) -> None:
        # Glob/Grep pointed at a governed directory read its contents just as
        # surely as Read does — they gate on the same read path-classes.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            environ = {"CARTOPIAN_ROLE": "assignee"}
            for tool in ("Glob", "Grep"):
                for rel, grant in (
                    ("specs", "read:governance"),
                    ("reports", "read:reports"),
                ):
                    target = str(fx.project_root / rel)
                    decision = fx.evaluate(
                        _read_payload(tool, target), environ=environ
                    )
                    self.assertEqual(
                        decision.action, "deny", msg=f"{tool} {rel} must deny"
                    )
                    self.assertIn(grant, decision.reason)

    def test_search_without_path_gates_on_cwd(self) -> None:
        # A pathless Glob/Grep searches the session cwd; launched from the
        # project root that sweeps governed artifacts, so it gates there.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            decision = fx.evaluate(
                _read_payload("Grep", cwd=str(fx.project_root)),
                environ={"CARTOPIAN_ROLE": "assignee"},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("read:governance", decision.reason)

    def test_project_root_search_requires_every_reachable_read_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            root = str(fx.project_root)
            payloads = (
                {
                    "tool_name": "Glob",
                    "tool_input": {"path": root, "pattern": "prompts/**"},
                    "cwd": root,
                },
                {
                    "tool_name": "Grep",
                    "tool_input": {
                        "path": root,
                        "pattern": "secret",
                        "glob": "prompts/**",
                    },
                    "cwd": root,
                },
            )
            for payload in payloads:
                decision = fx.evaluate(
                    payload,
                    environ={"CARTOPIAN_ROLE": "governance-only"},
                )
                self.assertEqual(decision.action, "deny", msg=payload)
                self.assertIn("read:prompts", decision.reason)

    def test_absolute_brace_glob_uses_first_metacharacter_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            pattern = str(fx.project_root / "{prompts,reports}" / "**")
            for tool_input in (
                {"pattern": pattern},
                {"pattern": pattern, "path": str(fx.work_root)},
            ):
                decision = fx.evaluate(
                    {
                        "tool_name": "Glob",
                        "tool_input": tool_input,
                        "cwd": str(fx.work_root),
                    },
                    environ={"CARTOPIAN_ROLE": "governance-only"},
                )
                self.assertEqual(decision.action, "deny", msg=tool_input)
                self.assertIn("read:prompts", decision.reason)

    def test_absolute_glob_parent_traversal_widens_authorization_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            pattern = str(
                fx.project_root / "prompts" / "{foo,../reports}" / "**"
            )
            decision = fx.evaluate(
                {
                    "tool_name": "Glob",
                    "tool_input": {"pattern": pattern},
                    "cwd": str(fx.work_root),
                },
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("read:governance", decision.reason)

    def test_glob_validates_absolute_and_relative_pattern_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            prefixes = (str(fx.project_root / "prompts" / "**"), "**")
            for prefix in prefixes:
                for invalid in ("\ud800", "\x00"):
                    decision = fx.evaluate(
                        {
                            "tool_name": "Glob",
                            "tool_input": {
                                "path": str(fx.project_root / "prompts"),
                                "pattern": prefix + invalid,
                            },
                            "cwd": str(fx.project_root),
                        },
                        environ={"CARTOPIAN_ROLE": "assignee"},
                    )
                    self.assertEqual(
                        decision.action, "deny", msg=(prefix, repr(invalid))
                    )
                    self.assertIn("cannot be represented safely", decision.reason)

    def test_narrow_prompt_search_remains_authorized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            decision = fx.evaluate(
                {
                    "tool_name": "Glob",
                    "tool_input": {
                        "path": str(fx.project_root / "prompts"),
                        "pattern": "**/*",
                    },
                    "cwd": str(fx.project_root),
                },
                environ={"CARTOPIAN_ROLE": "assignee"},
            )
            self.assertEqual(decision.action, "allow")

    def test_union_of_read_roles_can_search_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            decision = fx.evaluate(
                _read_payload("Glob", str(fx.project_root)),
                environ={"CARTOPIAN_ROLE": "assignee,curator"},
            )
            self.assertEqual(decision.action, "allow")

    def test_broad_search_requires_reachable_nested_work_root_grant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            nested_work_root = fx.project_root / "prompts" / "tool-repo"
            nested_work_root.mkdir()
            (fx.project_root / "cartopian.local.toml").write_text(
                f'[work_roots]\ntool-repo = "{nested_work_root}"\n',
                encoding="utf-8",
            )
            decision = fx.evaluate(
                _read_payload("Glob", str(fx.project_root)),
                environ={"CARTOPIAN_ROLE": "project-reader"},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("read:work-roots", decision.reason)

    def test_search_refuses_cross_class_project_hardlink_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            secret = fx.project_root / "reports" / "secret.txt"
            alias = fx.project_root / "prompts" / "allowed.txt"
            secret.write_text("secret\n", encoding="utf-8")
            try:
                os.link(secret, alias)
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")
            decision = fx.evaluate(
                {
                    "tool_name": "Grep",
                    "tool_input": {
                        "path": str(fx.project_root / "prompts"),
                        "pattern": "secret",
                    },
                    "cwd": str(fx.project_root),
                },
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("multiply-linked file", decision.reason)

    def test_absolute_glob_pattern_overrides_safe_path_and_is_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            pattern = str(fx.project_root / "specs" / "**" / "*.md")
            environ = {
                "CARTOPIAN_ROLE": "assignee",
                "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
            }
            for tool_input in (
                {"pattern": pattern},
                {"pattern": pattern, "path": str(fx.work_root)},
            ):
                decision = fx.evaluate(
                    {
                        "tool_name": "Glob",
                        "tool_input": tool_input,
                        "cwd": str(fx.work_root),
                        "hook_event_name": "PreToolUse",
                    },
                    environ=environ,
                )
                self.assertEqual(decision.action, "deny", msg=tool_input)
                self.assertIn("read:governance", decision.reason)

    def test_enforcement_keys_on_grants_not_names_or_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            target = str(fx.project_root / "specs" / "SPEC-01-001-x.md")
            # A role whose description claims full clearance but whose grant
            # list is empty holds nothing.
            decision = fx.evaluate(
                _read_payload("Read", target),
                environ={"CARTOPIAN_ROLE": "boaster"},
            )
            self.assertEqual(decision.action, "deny")
            # The protocol-default role name grants nothing by itself: `pm`
            # is undeclared in this config, so it fails closed too.
            decision = fx.evaluate(_read_payload("Read", target), environ={})
            self.assertEqual(decision.action, "deny")

    def test_read_outside_registered_boundaries_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            outside = str(Path(tmp) / "elsewhere" / "notes.md")
            decision = fx.evaluate(
                _read_payload("Read", outside),
                environ={"CARTOPIAN_ROLE": "assignee"},
            )
            self.assertEqual(decision.action, "allow")
            self.assertIsNone(decision.reason)


class TestReviewerLikeWorkflowAccess(unittest.TestCase):
    def test_arbitrarily_named_review_role_reads_task_and_completion_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _REVIEW_ASSIGNMENT_ROLES)
            environ = {"CARTOPIAN_ROLE": "quality-gate"}
            for rel in (
                "tasks/in-review/TASK-01-001.md",
                "reports/REPORT-01-001.md",
            ):
                decision = fx.evaluate(
                    _read_payload("Read", str(fx.project_root / rel)),
                    environ=environ,
                )
                self.assertEqual(decision.action, "allow", msg=rel)

    def test_planning_review_retains_governance_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _REVIEW_ASSIGNMENT_ROLES)
            decision = fx.evaluate(
                _read_payload(
                    "Read", str(fx.project_root / "IMPLEMENTATION_PLAN.md")
                ),
                environ={"CARTOPIAN_ROLE": "quality-gate"},
            )
            self.assertEqual(decision.action, "allow")

    def test_unrelated_execution_role_still_cannot_read_governance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _REVIEW_ASSIGNMENT_ROLES)
            decision = fx.evaluate(
                _read_payload(
                    "Read",
                    str(fx.project_root / "tasks" / "in-review" / "TASK-01-001.md"),
                ),
                environ={"CARTOPIAN_ROLE": "builder"},
            )
            self.assertEqual(decision.action, "deny")
            self.assertIn("read:governance", decision.reason)

    def test_reviewer_like_does_not_gain_lifecycle_or_implementation_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _REVIEW_ASSIGNMENT_ROLES)
            environ = {"CARTOPIAN_ROLE": "quality-gate"}
            for target in (
                fx.project_root / "IMPLEMENTATION_PLAN.md",
                fx.project_root / "tasks" / "in-review" / "TASK-01-001.md",
                fx.project_root / "prompts" / "PROMPT-01-001.md",
                fx.project_root / "decisions" / "DECISION-001.md",
                fx.work_root / "src" / "main.py",
            ):
                decision = fx.evaluate(
                    _payload("Write", str(target)), environ=environ
                )
                self.assertEqual(decision.action, "deny", msg=str(target))


class TestAllowGrantedReads(unittest.TestCase):
    """The default assignee keeps its prompt + work tree; a session holding
    the governance/reports read grants reads those classes successfully."""

    def test_assignee_reads_own_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            decision = fx.evaluate(
                _read_payload("Read", str(fx.project_root / "prompts" / "PROMPT-01-005.md")),
                environ={"CARTOPIAN_ROLE": "assignee"},
            )
            self.assertEqual(decision.action, "allow")

    def test_assignee_reads_work_tree_with_all_read_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            environ = {"CARTOPIAN_ROLE": "assignee"}
            decision = fx.evaluate(
                _read_payload("Read", str(fx.work_root / "src" / "main.py")),
                environ=environ,
            )
            self.assertEqual(decision.action, "allow")
            for tool in ("Glob", "Grep"):
                decision = fx.evaluate(
                    _read_payload(tool, str(fx.work_root)), environ=environ
                )
                self.assertEqual(decision.action, "allow", msg=tool)

    def test_bound_read_rejects_hardlink_alias_from_foreign_work_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            foreign_work_root = tmp / "foreign-work-root"
            foreign_work_root.mkdir()
            foreign_project, foreign_entry = _claiming_project(
                tmp, "foreign-project", foreign_work_root, _READ_ROLES
            )
            fx = _HookFixture(tmp, _READ_ROLES, extra_registry=(foreign_entry,))
            secret = foreign_work_root / "secret.txt"
            alias = fx.work_root / "innocent.txt"
            secret.write_text("foreign secret\n", encoding="utf-8")
            try:
                os.link(secret, alias)
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")

            decision = fx.evaluate(
                _read_payload("Read", str(alias)),
                environ={
                    "CARTOPIAN_ROLE": "assignee",
                    "CARTOPIAN_LAUNCH_CWD": str(fx.project_root),
                },
            )

            self.assertEqual(decision.action, "deny")
            self.assertIn("multiple hard-link names", decision.reason)
            self.assertTrue(foreign_project.is_dir())

    def test_curator_reads_governance_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            environ = {"CARTOPIAN_ROLE": "curator"}
            for rel in _GOVERNANCE_READ_TARGETS + _REPORTS_READ_TARGETS:
                decision = fx.evaluate(
                    _read_payload("Read", str(fx.project_root / rel)),
                    environ=environ,
                )
                self.assertEqual(
                    decision.action, "allow", msg=f"Read {rel} must allow"
                )

    def test_read_and_write_boundaries_coexist_for_assignee(self) -> None:
        # coder-like carries write:worktree: the assignee still writes the
        # work tree but is denied a governed write — the read extension must
        # leave the write axis exactly as it was.
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            environ = {"CARTOPIAN_ROLE": "assignee"}
            decision = fx.evaluate(
                _payload("Write", str(fx.work_root / "src" / "main.py")),
                environ=environ,
            )
            self.assertEqual(decision.action, "allow")
            decision = fx.evaluate(
                _payload("Write", str(fx.project_root / "STATE.md")),
                environ=environ,
            )
            self.assertEqual(decision.action, "deny")

    def test_ungated_config_passes_all_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _UNGATED_ROLES)
            for rel in _GOVERNANCE_READ_TARGETS + _REPORTS_READ_TARGETS:
                decision = fx.evaluate(
                    _read_payload("Read", str(fx.project_root / rel)), environ={}
                )
                self.assertEqual(
                    decision.action, "allow", msg=f"{rel} must pass ungated"
                )


class TestWindowsPathLogic(unittest.TestCase):
    """Windows-shaped paths (backslash, drive letter, case variance) classify
    correctly — exercised on POSIX via the flavor-parameterized helpers."""

    def test_absolute_glob_search_root_matches_first_metacharacter_rules(
        self,
    ) -> None:
        from cli.claude_hook import _absolute_glob_search_root

        posix_cases = (
            ("/project/prompts/file.md", "/project/prompts"),
            ("/project/prompts/", "/project"),
            ("/project/prompts/**/*.md", "/project/prompts"),
            ("/project/pro?mpts/**/*.md", "/project"),
            ("/project/[pr]ompts/**/*.md", "/project"),
            ("/project/{prompts,reports}/**", "/project"),
            (
                "/project/@(prompts|reports)/**",
                "/project/@(prompts|reports)",
            ),
            ("/*.md", "/"),
            ("project/prompts/**", None),
        )
        for pattern, expected in posix_cases:
            self.assertEqual(
                _absolute_glob_search_root(pattern, posixpath),
                expected,
                msg=pattern,
            )

        windows_cases = (
            (r"C:\Project\Prompts\file.md", r"C:\Project\Prompts"),
            ("C:\\Project\\Prompts\\", "C:\\Project"),
            (r"C:\Project\Prompts\**", r"C:\Project\Prompts"),
            (r"C:\{Prompts,Reports}\**", "C:\\"),
            (r"C:/Project/Prompts/**", "C:/Project/Prompts"),
            (r"\\server\share\prompts\**", r"\\server\share\prompts"),
            (r"\Project\Prompts\**", r"\Project\Prompts"),
        )
        for pattern, expected in windows_cases:
            self.assertEqual(
                _absolute_glob_search_root(pattern, ntpath),
                expected,
                msg=pattern,
            )

    def test_absolute_glob_parent_suffix_widens_search_root(self) -> None:
        from cli.claude_hook import _absolute_glob_search_root

        self.assertEqual(
            _absolute_glob_search_root(
                "/project/prompts/{foo,../reports}/**", posixpath
            ),
            "/",
        )
        self.assertEqual(
            _absolute_glob_search_root(
                r"C:\Project\Prompts\{foo,..\Reports}\**", ntpath
            ),
            "C:\\",
        )

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

    def test_windows_classification_read_axis(self) -> None:
        # The read axis shares the same classification spine; only the
        # required grant differs per class.
        from cli.claude_hook import classify_project_path

        root = "C:\\Proj\\Gov"
        cases = (
            ("C:/Proj/Gov/Specs/SPEC-01.md", "plan", "read:governance"),
            ("c:\\proj\\gov\\state.MD", "lifecycle", "read:governance"),
            ("C:\\proj\\gov\\DECISIONS\\DECISION-001.md", "decisions", "read:governance"),
            ("C:\\proj\\gov\\Prompts\\PROMPT-01-001.md", "prompts", "read:prompts"),
            ("C:/proj/gov/Reviews/REVIEW-01.md", "reports", "read:reports"),
            ("C:\\proj\\gov\\random\\notes.txt", "project-file", "read:governance"),
        )
        for target, expect_class, expect_grant in cases:
            klass, grant = classify_project_path(target, root, ntpath, axis="read")
            self.assertEqual((klass, grant), (expect_class, expect_grant), msg=target)

    def test_posix_classification_stays_case_sensitive(self) -> None:
        # On case-sensitive Linux, `Specs/` and `state.md` are different objects
        # from the governed `specs/` / `STATE.md` — they classify as plain
        # project files, not as the governed class.
        from cli import claude_hook

        with mock.patch.object(claude_hook.sys, "platform", "linux"):
            klass, grant = claude_hook.classify_project_path(
                "/p/gov/Specs/x.md", "/p/gov", posixpath
            )
            self.assertEqual((klass, grant), ("project-file", "write:lifecycle"))
            klass, _ = claude_hook.classify_project_path(
                "/p/gov/state.md", "/p/gov", posixpath
            )
            self.assertEqual(klass, "project-file")
            self.assertFalse(
                claude_hook._is_within("/p/govx/file", "/p/gov", posixpath)
            )
            self.assertFalse(
                claude_hook._is_within("/p/GOV/file", "/p/gov", posixpath)
            )

    def test_darwin_unknown_volume_mode_conservatively_casefolds_governed_names(
        self,
    ) -> None:
        from cli import claude_hook

        with (
            mock.patch.object(claude_hook.sys, "platform", "darwin"),
            mock.patch.object(
                claude_hook, "_live_path_is_case_insensitive", return_value=False
            ),
        ):
            self.assertEqual(
                claude_hook.classify_project_path(
                    "/Volumes/Product/123/roadmap.md",
                    "/Volumes/Product/123",
                    os.path,
                ),
                ("plan", "write:plan"),
            )

    def test_case_insensitive_macos_aliases_remain_inside_guarded_roots(self) -> None:
        from cli import claude_hook

        with tempfile.TemporaryDirectory() as tmp_raw:
            fx = _HookFixture(Path(tmp_raw), _ACTIVATED_ROLES)
            project_alias = str(fx.project_root / "STATE.md").replace(
                "gov-project", "GOV-PROJECT"
            )
            work_alias = str(fx.work_root / "change.txt").replace(
                "tool-repo", "TOOL-REPO"
            )
            with (
                mock.patch.object(
                    claude_hook, "_live_path_is_case_insensitive", return_value=True
                ),
                mock.patch.object(
                    claude_hook,
                    "_live_path_is_normalization_insensitive",
                    return_value=True,
                ),
            ):
                self.assertTrue(
                    claude_hook._is_within(
                        "/tmp/caf\N{LATIN SMALL LETTER E WITH ACUTE}/file.txt",
                        "/tmp/cafe\N{COMBINING ACUTE ACCENT}",
                        os.path,
                    )
                )
                project_decision = fx.evaluate(
                    _payload("Edit", project_alias),
                    environ={"CARTOPIAN_ROLE": "warden"},
                )
                work_decision = fx.evaluate(
                    _payload("Write", work_alias),
                    environ={"CARTOPIAN_ROLE": "warden"},
                )

            self.assertEqual(project_decision.action, "deny")
            self.assertEqual(work_decision.action, "deny")

    def test_target_probe_case_mode_also_normalizes_root_file_names(self) -> None:
        from cli import claude_hook

        root = "/Volumes/Product/123"
        target = root + "/requirements.md"
        with (
            mock.patch.object(
                claude_hook,
                "_filesystem_relative_path",
                return_value="requirements.md",
            ),
            mock.patch.object(
                claude_hook,
                "_live_path_is_case_insensitive",
                side_effect=lambda path, _flavor: path == target,
            ),
            mock.patch.object(
                claude_hook,
                "_live_path_is_normalization_insensitive",
                return_value=False,
            ),
        ):
            self.assertEqual(
                claude_hook.classify_project_path(target, root, os.path),
                ("plan", "write:plan"),
            )

    def test_case_sensitive_macos_unicode_aliases_remain_inside_guarded_roots(
        self,
    ) -> None:
        from cli import claude_hook

        with (
            mock.patch.object(
                claude_hook, "_live_path_is_case_insensitive", return_value=False
            ),
            mock.patch.object(
                claude_hook,
                "_live_path_is_normalization_insensitive",
                return_value=True,
            ),
        ):
            self.assertTrue(
                claude_hook._is_within(
                    "/tmp/caf\N{LATIN SMALL LETTER E WITH ACUTE}/file.txt",
                    "/tmp/cafe\N{COMBINING ACUTE ACCENT}",
                    os.path,
                )
            )
            self.assertFalse(
                claude_hook._is_within(
                    "/tmp/CAF\N{LATIN SMALL LETTER E WITH ACUTE}/file.txt",
                    "/tmp/cafe\N{COMBINING ACUTE ACCENT}",
                    os.path,
                )
            )


class TestHookEndToEnd(unittest.TestCase):
    """Run the hook exactly as Claude Code does: script + stdin JSON."""

    def _run_hook(
        self,
        payload: dict,
        home: Path,
        role: str = "",
        arguments=(),
        env_overrides=None,
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)  # Path.home() source on native Windows
        env.pop("CARTOPIAN_ROLE", None)
        if role:
            env["CARTOPIAN_ROLE"] = role
        env.update(env_overrides or {})
        return subprocess.run(
            [sys.executable, str(HOOK_PATH), *arguments],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=60,
        )

    def test_bound_argv_identity_overrides_mutable_settings_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            home = tmp / "home"
            home.mkdir()
            protected = tmp / "actual-settings.json"
            arguments = (
                "--role",
                "assignee",
                "--project-root",
                str(fx.project_root),
                "--cartopian-home",
                str(fx.home),
                "--work-root",
                "tool-repo",
                str(fx.work_root),
                str(fx.work_root.lstat().st_dev),
                str(fx.work_root.lstat().st_ino),
                "--settings-path",
                str(protected),
            )

            # Claude settings may replace inherited env entries. The immutable
            # command argv still selects the dispatched role/project, so the
            # assignee's legitimate work-root read remains allowed.
            result = self._run_hook(
                _read_payload("Read", str(fx.work_root / "change.txt")),
                home,
                role="boaster",
                arguments=arguments,
                env_overrides={"CARTOPIAN_LAUNCH_CWD": str(tmp / "foreign")},
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertEqual(result.stdout, b"")

            # The protected settings list is bound the same way; changing HOME
            # cannot redirect structured self-protection to a decoy location.
            result = self._run_hook(
                _payload("Edit", str(protected)),
                home,
                role="boaster",
                arguments=arguments,
                env_overrides={"CARTOPIAN_LAUNCH_CWD": str(tmp / "foreign")},
            )
            self.assertIn(b'"permissionDecision": "deny"', result.stdout)

            result = self._run_hook(
                _payload("Edit", str(fx.home / "projects.json")),
                home,
                role="boaster",
                arguments=arguments,
                env_overrides={"CARTOPIAN_LAUNCH_CWD": str(tmp / "foreign")},
            )
            self.assertIn(b'"permissionDecision": "deny"', result.stdout)
            self.assertIn(b"active enforcement/configuration", result.stdout)

    def test_unrepresentable_or_nul_target_fails_closed_without_hook_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            fx = _HookFixture(tmp, _READ_ROLES)
            home = tmp / "home"
            home.mkdir()
            root_info = fx.work_root.lstat()
            arguments = (
                "--role",
                "assignee",
                "--project-root",
                str(fx.project_root),
                "--cartopian-home",
                str(fx.home),
                "--work-root",
                "tool-repo",
                str(fx.work_root),
                str(root_info.st_dev),
                str(root_info.st_ino),
            )
            for target in (
                str(fx.project_root / "specs") + "/\ud800escaped.txt",
                str(fx.project_root / "specs") + "/nul\x00escaped.txt",
            ):
                result = self._run_hook(
                    _payload("Write", target),
                    home,
                    arguments=arguments,
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                self.assertIn(b'"permissionDecision": "deny"', result.stdout)
                self.assertIn(b"cannot be represented safely", result.stdout)

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

    def test_deny_read_emits_structured_guard_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            home = Path(tmp) / "home"
            home.mkdir()
            (home / ".cartopian").mkdir()
            (home / ".cartopian" / "projects.json").write_text(
                json.dumps([{"id": "guard-proj", "path": str(fx.project_root)}]),
                encoding="utf-8",
            )
            target = str(fx.project_root / "STATE.md")
            result = self._run_hook(
                _read_payload("Read", target), home, role="assignee"
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            output = json.loads(result.stdout.decode("utf-8"))
            hso = output["hookSpecificOutput"]
            self.assertEqual(hso["permissionDecision"], "deny")
            self.assertIn("[guard]", hso["permissionDecisionReason"])
            self.assertIn(target, hso["permissionDecisionReason"])
            self.assertIn("read:governance", hso["permissionDecisionReason"])

    def test_allowed_read_is_perfectly_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _READ_ROLES)
            home = Path(tmp) / "home"
            home.mkdir()
            (home / ".cartopian").mkdir()
            (home / ".cartopian" / "projects.json").write_text(
                json.dumps([{"id": "guard-proj", "path": str(fx.project_root)}]),
                encoding="utf-8",
            )
            target = str(fx.project_root / "prompts" / "PROMPT-01-005.md")
            result = self._run_hook(
                _read_payload("Read", target), home, role="assignee"
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")

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
        from cli import request_trace
        from cli.commands import dispatch
        from tests.scaffold import project_scaffold

        toml = (
            "[project]\n"
            'id = "dispatch-proj"\n'
            'name = "Dispatch Project"\n'
            'project_schema_version = "v0.13.0"\n'
            "\n"
            "[roles.coder]\n"
            'description = "Implements tasks per spec."\n'
            'auto_launch = ["task_run"]\n'
            'agent = "/bin/true"\n'
            'timeout = "30m"\n'
        )
        with project_scaffold(cartopian_toml=toml) as scaffold, \
                tempfile.TemporaryDirectory() as fake_home:
            task_path = scaffold.write(
                "tasks/in-progress/TASK-01-004.md",
                "# TASK-01-004: role export\n",
            )
            scaffold.capture_request(
                request_id="REQUEST-001",
                unit="task:TASK-01-004",
                text="Run the role export task.",
            )
            context = request_trace.context_for_task_assignment(
                scaffold.project_root, task_path
            )
            scaffold.write(
                "prompts/PROMPT-01-004.md",
                request_trace.upsert_request_sections(
                    "# PROMPT-01-004\n\n## Your task\n\nx\n",
                    context.section,
                ),
            )
            captured = {}

            def fake_popen(argv, cwd=None, env=None, **kwargs):
                captured["env"] = env
                return mock.Mock(pid=12345)

            args = argparse.Namespace(task_path=str(task_path), prompt=None, role="coder")
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
            self.assertEqual(captured["env"].get("CARTOPIAN_PYTHON"), sys.executable)


class TestInstallerHookCleanup(unittest.TestCase):
    """The historical ``--claude-hook`` path removes old registrations only."""

    def test_missing_settings_remains_unmodified(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            import install
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workdir"
            project_dir.mkdir()
            install.cleanup_claude_hook_registrations(project_dir, [])
            settings_path = project_dir / ".claude" / "settings.json"
            self.assertFalse(settings_path.exists())

    def test_cleanup_is_idempotent_and_preserves_settings(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            import install
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workdir"
            (project_dir / ".claude").mkdir(parents=True)
            settings_path = project_dir / ".claude" / "settings.json"
            settings_path.write_text(json.dumps({
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {"PreToolUse": [{
                    "matcher": "Write",
                    "hooks": [
                        {"type": "command", "command": "python old/claude_hook.py"},
                        {
                            "type": "command",
                            "command": sys.executable,
                            "args": ["-I", "-S", "old/claude_hook.py"],
                        },
                        {"type": "command", "command": "audit-tool"},
                    ],
                }]},
            }), encoding="utf-8")
            actions = []
            install.cleanup_claude_hook_registrations(project_dir, actions)
            install.cleanup_claude_hook_registrations(project_dir, actions)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(settings["permissions"], {"allow": ["Bash(ls:*)"]})
            self.assertEqual(
                settings["hooks"]["PreToolUse"][0]["hooks"],
                [{"type": "command", "command": "audit-tool"}],
            )
            self.assertEqual(len(actions), 1)

    def test_cleanup_removes_old_write_only_matcher(self) -> None:
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            import install
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workdir"
            (project_dir / ".claude").mkdir(parents=True)
            settings_path = project_dir / ".claude" / "settings.json"
            stale = {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Write|Edit|MultiEdit|NotebookEdit",
                            "hooks": [
                                {"type": "command", "command": "python claude_hook.py"}
                            ],
                        }
                    ]
                }
            }
            settings_path.write_text(json.dumps(stale), encoding="utf-8")
            install.cleanup_claude_hook_registrations(project_dir, [])
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn("PreToolUse", settings["hooks"])

    def test_cli_cleanup_is_standalone_and_skips_install_source_validation(self) -> None:
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
                json.dumps({
                    "hooks": {
                        "PreToolUse": [{
                            "matcher": "Write",
                            "hooks": [{
                                "type": "command",
                                "command": "python old/claude_hook.py",
                            }],
                        }]
                    }
                }),
                encoding="utf-8",
            )
            with mock.patch.object(
                install,
                "_resolve_source_root",
                side_effect=AssertionError("cleanup must not resolve an install source"),
            ):
                rc = install.main(["--claude-hook", str(project_dir), "--quiet"])
            self.assertEqual(rc, 0)
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn("PreToolUse", settings["hooks"])


_PM_LIFECYCLE_ROLES = (
    "[roles.pm]\n"
    'description = "Plans the work."\n'
    'grants = ["pm-solo"]\n'  # pm-solo includes write:lifecycle and read:governance
)


class TestConfigWriteDeny(unittest.TestCase):
    """Structured raw-edit tools are denied for config files regardless of
    grants or activation. `update-config` is the only edit path; an activated
    POSIX handoff reports it for outside execution because shell writes are also
    denied by launch sandboxing."""

    def test_ungated_project_denies_raw_config_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _UNGATED_ROLES)
            for fname in ("cartopian.toml", "cartopian.local.toml"):
                target = str(fx.project_root / fname)
                for tool in ("Write", "Edit", "MultiEdit"):
                    decision = fx.evaluate(_payload(tool, target), environ={})
                    self.assertEqual(decision.action, "deny", f"{tool} {fname}")
                    self.assertIn("update-config", decision.reason)
                    self.assertIn("outside an activated Claude handoff", decision.reason)

    def test_target_case_probe_applies_to_raw_config_name(self):
        from cli import claude_hook

        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _UNGATED_ROLES)
            target = str(fx.project_root / "CARTOPIAN.TOML")
            with mock.patch.object(
                claude_hook,
                "_live_path_is_case_insensitive",
                return_value=False,
            ), mock.patch.object(claude_hook.sys, "platform", "darwin"):
                decision = fx.evaluate(_payload("Write", target), environ={})
            self.assertEqual(decision.action, "deny")
            self.assertIn("update-config", decision.reason)

    def test_activated_with_write_lifecycle_still_denies_config_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _PM_LIFECYCLE_ROLES)
            target = str(fx.project_root / "cartopian.toml")
            decision = fx.evaluate(_payload("Write", target), environ={"CARTOPIAN_ROLE": "pm"})
            self.assertEqual(decision.action, "deny")
            self.assertIn("update-config", decision.reason)

    def test_config_read_still_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _PM_LIFECYCLE_ROLES)
            target = str(fx.project_root / "cartopian.toml")
            decision = fx.evaluate(_read_payload("Read", target), environ={"CARTOPIAN_ROLE": "pm"})
            self.assertEqual(decision.action, "allow")

    def test_normal_md_write_path_unaffected(self):
        # A non-config lifecycle write still follows the capability path (allowed
        # here because pm-solo holds write:lifecycle).
        with tempfile.TemporaryDirectory() as tmp:
            fx = _HookFixture(Path(tmp), _PM_LIFECYCLE_ROLES)
            target = str(fx.project_root / "STATE.md")
            decision = fx.evaluate(_payload("Write", target), environ={"CARTOPIAN_ROLE": "pm"})
            self.assertEqual(decision.action, "allow")


if __name__ == "__main__":
    unittest.main()
