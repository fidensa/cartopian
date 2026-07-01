"""Tests for capability-based access grants (vocabulary, resolution, presets).

Covers `cli.capabilities` (closed vocabulary, role→grant resolution with
explicit activation state, presets) and the CLI surfaces that consume it:
`resolve-config` (capabilities block in the emitted record) and
`generate-config` (`--role-grants`).
"""
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from cli.capabilities import (
    ALL_CAPABILITIES,
    PRESETS,
    resolve_grants,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"

FULL_SET = frozenset(ALL_CAPABILITIES)

EXPECTED_CAPABILITIES = frozenset(
    {
        "read:governance",
        "read:reports",
        "read:prompts",
        "read:work-roots",
        "write:plan",
        "write:lifecycle",
        "write:decisions",
        "write:reports",
        "write:worktree",
        "dispatch",
    }
)

EXPECTED_PRESETS = {
    "coder-like": frozenset({"read:prompts", "read:work-roots", "write:worktree"}),
    "reviewer-like": frozenset({"read:prompts", "read:work-roots", "write:reports"}),
    "planner-like": frozenset(
        {"read:governance", "read:reports", "read:prompts", "write:plan"}
    ),
    "pm-with-planner": frozenset(
        {"read:governance", "read:reports", "read:prompts", "write:lifecycle", "dispatch"}
    ),
    "pm-solo": frozenset(
        {
            "read:governance",
            "read:reports",
            "read:prompts",
            "write:plan",
            "write:lifecycle",
            "dispatch",
        }
    ),
}


class TestVocabulary(unittest.TestCase):
    def test_vocabulary_is_exactly_the_closed_set(self):
        self.assertEqual(frozenset(ALL_CAPABILITIES), EXPECTED_CAPABILITIES)

    def test_preset_names_do_not_shadow_capabilities(self):
        self.assertEqual(frozenset(PRESETS) & EXPECTED_CAPABILITIES, frozenset())


class TestPresets(unittest.TestCase):
    def test_preset_contents_resolve_exactly_as_specified(self):
        self.assertEqual(frozenset(PRESETS), frozenset(EXPECTED_PRESETS))
        for name, expected in EXPECTED_PRESETS.items():
            with self.subTest(preset=name):
                res = resolve_grants({"r": {"grants": [name]}})
                self.assertTrue(res.activated)
                self.assertEqual(res.role_grants["r"], expected)
                self.assertEqual(frozenset(PRESETS[name]), expected)

    def test_preset_composes_with_extra_grant(self):
        res = resolve_grants({"reviewer": {"grants": ["reviewer-like", "read:reports"]}})
        self.assertEqual(
            res.role_grants["reviewer"],
            EXPECTED_PRESETS["reviewer-like"] | {"read:reports"},
        )


class TestUngatedMode(unittest.TestCase):
    def test_no_grants_declared_anywhere_means_gating_inactive(self):
        roles = {"pm": "Plans the work.", "coder": "Writes code."}
        res = resolve_grants(roles)
        self.assertFalse(res.activated)
        # All read and write grants behave as held, for any role.
        self.assertEqual(res.grants_for(["coder"]), FULL_SET)
        self.assertEqual(res.grants_for(["pm", "coder"]), FULL_SET)
        for role in roles:
            self.assertEqual(res.role_grants[role], FULL_SET)

    def test_table_form_role_without_grants_key_does_not_activate(self):
        roles = {"pm": {"description": "Plans the work."}}
        res = resolve_grants(roles)
        self.assertFalse(res.activated)
        self.assertEqual(res.grants_for(["pm"]), FULL_SET)


class TestActivation(unittest.TestCase):
    def test_single_role_declaring_grants_activates_project_wide(self):
        roles = {
            "pm": "Plans the work.",
            "coder": {"grants": ["coder-like"]},
        }
        res = resolve_grants(roles)
        self.assertTrue(res.activated)

    def test_explicitly_empty_grant_list_still_activates(self):
        res = resolve_grants({"coder": {"grants": []}})
        self.assertTrue(res.activated)

    def test_malformed_grants_value_still_activates(self):
        # A declaration that fails validation must never flip gating back off.
        res = resolve_grants({"coder": {"grants": "read:prompts"}})
        self.assertTrue(res.activated)


class TestFailClosed(unittest.TestCase):
    def test_unknown_capability_name_resolves_to_no_grants(self):
        # Typo in one entry: the whole role fails closed, valid entries included.
        res = resolve_grants(
            {"coder": {"grants": ["read:prompts", "write:worktre"]}}
        )
        self.assertTrue(res.activated)
        self.assertEqual(res.role_grants["coder"], frozenset())
        self.assertEqual(res.grants_for(["coder"]), frozenset())
        self.assertIn("write:worktre", res.invalid["coder"])

    def test_explicitly_empty_grant_list_resolves_to_no_grants(self):
        res = resolve_grants({"coder": {"grants": []}})
        self.assertEqual(res.role_grants["coder"], frozenset())
        self.assertEqual(res.grants_for(["coder"]), frozenset())

    def test_role_with_no_grant_set_in_activated_config_fails_closed(self):
        roles = {
            "coder": {"grants": ["coder-like"]},
            "pm": "Plans the work.",
            "operator": {"description": "Approves things."},
        }
        res = resolve_grants(roles)
        self.assertTrue(res.activated)
        self.assertEqual(res.role_grants["pm"], frozenset())
        self.assertEqual(res.role_grants["operator"], frozenset())
        self.assertEqual(res.grants_for(["pm"]), frozenset())

    def test_grants_not_a_list_fails_closed(self):
        res = resolve_grants({"coder": {"grants": "read:prompts"}})
        self.assertEqual(res.role_grants["coder"], frozenset())

    def test_non_string_grant_entry_fails_closed(self):
        res = resolve_grants({"coder": {"grants": ["read:prompts", 7]}})
        self.assertEqual(res.role_grants["coder"], frozenset())

    def test_unknown_role_name_holds_nothing_when_activated(self):
        res = resolve_grants({"coder": {"grants": ["coder-like"]}})
        self.assertEqual(res.grants_for(["ghost"]), frozenset())


class TestUnionSemantics(unittest.TestCase):
    def test_session_holding_several_roles_gets_the_union(self):
        roles = {
            "coder": {"grants": ["coder-like"]},
            "planner": {"grants": ["planner-like"]},
        }
        res = resolve_grants(roles)
        self.assertEqual(
            res.grants_for(["coder", "planner"]),
            EXPECTED_PRESETS["coder-like"] | EXPECTED_PRESETS["planner-like"],
        )

    def test_union_with_a_failed_closed_role_adds_nothing(self):
        roles = {
            "coder": {"grants": ["coder-like"]},
            "broken": {"grants": ["not-a-grant"]},
        }
        res = resolve_grants(roles)
        self.assertEqual(
            res.grants_for(["coder", "broken"]), EXPECTED_PRESETS["coder-like"]
        )


def _run_cli(*argv, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *argv],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _Sandbox:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.project = self.root / "proj"
        self.home.mkdir()
        self.project.mkdir()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._tmp.cleanup()


_PROJECT_HEADER = (
    '[project]\n'
    'id = "demo"\n'
    'name = "Demo Project"\n'
    'protocol_version = "v0.2.0"\n'
    '\n'
)


class TestResolveConfigCapabilities(unittest.TestCase):
    def test_activated_config_emits_capabilities_block(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT_HEADER
                + '[roles]\n'
                'pm = "Plans the work."\n'
                '\n'
                '[roles.coder]\n'
                'description = "Writes code."\n'
                'grants = ["coder-like"]\n',
            )
            result = _run_cli("resolve-config", str(sb.project), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        # Role descriptions keep their existing shape (name → description).
        self.assertEqual(record["roles"]["coder"], "Writes code.")
        self.assertEqual(record["roles"]["pm"], "Plans the work.")
        caps = record["capabilities"]
        self.assertTrue(caps["activated"])
        self.assertEqual(
            frozenset(caps["role_grants"]["coder"]),
            EXPECTED_PRESETS["coder-like"],
        )
        # pm declared no grant set in an activated config: fails closed.
        self.assertEqual(caps["role_grants"]["pm"], [])

    def test_ungated_config_emits_inactive_capabilities_block(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT_HEADER
                + '[roles]\n'
                'pm = "Plans the work."\n'
                'coder = "Writes code."\n',
            )
            result = _run_cli("resolve-config", str(sb.project), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        caps = record["capabilities"]
        self.assertFalse(caps["activated"])
        self.assertEqual(frozenset(caps["role_grants"]["coder"]), FULL_SET)

    def test_unknown_capability_warns_and_fails_closed(self):
        with _Sandbox() as sb:
            _write(
                sb.project / "cartopian.toml",
                _PROJECT_HEADER
                + '[roles.coder]\n'
                'description = "Writes code."\n'
                'grants = ["write:worktre"]\n',
            )
            result = _run_cli("resolve-config", str(sb.project), home=sb.home)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        record = json.loads(result.stdout.splitlines()[0])
        caps = record["capabilities"]
        self.assertTrue(caps["activated"])
        self.assertEqual(caps["role_grants"]["coder"], [])
        self.assertIn("write:worktre", result.stderr)


class TestGenerateConfigRoleGrants(unittest.TestCase):
    def test_role_grants_written_and_resolvable(self):
        with _Sandbox() as sb:
            result = _run_cli(
                "generate-config",
                str(sb.project),
                "--name", "Demo",
                "--id", "demo",
                "--role", 'pm=Plans the work.',
                "--role", 'coder=Writes code.',
                "--role-grants", "pm=pm-solo",
                "--role-grants", "coder=coder-like,read:reports",
                home=sb.home,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            with (sb.project / "cartopian.toml").open("rb") as fh:
                cfg = tomllib.load(fh)
        self.assertEqual(
            cfg["roles"]["pm"],
            {"description": "Plans the work.", "grants": ["pm-solo"]},
        )
        self.assertEqual(
            cfg["roles"]["coder"],
            {
                "description": "Writes code.",
                "grants": ["coder-like", "read:reports"],
            },
        )
        res = resolve_grants(cfg["roles"])
        self.assertTrue(res.activated)
        self.assertEqual(res.role_grants["pm"], EXPECTED_PRESETS["pm-solo"])
        self.assertEqual(
            res.role_grants["coder"],
            EXPECTED_PRESETS["coder-like"] | {"read:reports"},
        )

    def test_unknown_grant_name_is_rejected_at_generation(self):
        with _Sandbox() as sb:
            result = _run_cli(
                "generate-config",
                str(sb.project),
                "--name", "Demo",
                "--id", "demo",
                "--role", 'coder=Writes code.',
                "--role-grants", "coder=write:worktre",
                home=sb.home,
            )
            config_written = (sb.project / "cartopian.toml").exists()
        self.assertEqual(result.returncode, 2, msg=result.stdout)
        self.assertIn("write:worktre", result.stderr)
        self.assertFalse(config_written)

    def test_role_grants_for_undeclared_role_is_rejected(self):
        with _Sandbox() as sb:
            result = _run_cli(
                "generate-config",
                str(sb.project),
                "--name", "Demo",
                "--id", "demo",
                "--role-grants", "coder=coder-like",
                home=sb.home,
            )
        self.assertEqual(result.returncode, 2, msg=result.stdout)


if __name__ == "__main__":
    unittest.main()
