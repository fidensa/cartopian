"""FR-004 base test-suite consolidation gate (TASK-01-012, P01-BUILD-012).

Asserts that every FR-004 command listed in SPEC-01-001 carries, in its
per-command test module under ``tests/cli/commands/``:

1. at least one happy-path test class (class name contains ``Happy``);
2. at least one guard- or failure-mode test class whose name signals
   the contract being asserted (matches the GUARD_PATTERN below).

The gate is naming-convention driven so it stays cheap to enforce as
the suite grows. It does not introspect test bodies — that work belongs
to the per-command suites themselves. The gate's job is to make
"this command lost its happy-path or its guard test" a loud failure
rather than a silent regression.

If you add an FR-004 command (or rename a guard class), update either
``FR004_COMMANDS`` or ``GUARD_PATTERN`` so the gate keeps matching.
"""
import re
import unittest
from pathlib import Path


FR004_COMMANDS = (
    "resolve_config",
    "validate_task_readiness",
    "move_task",
    "discover_projects",
    "register_project",
    "unregister_project",
    "scaffold_project",
    "generate_config",
    # FR-005 stretch commands retained by DEC-010.
    "list_tasks",
    "delete_prompt",
    "delete_report",
)

COMMANDS_DIR = Path(__file__).resolve().parent / "commands"

HAPPY_PATTERN = re.compile(r"^class\s+\w*Happy\w*\(", re.MULTILINE)

# Guard/failure marker vocabulary. Covers every per-command failure-mode
# test class name in use today: explicit guards, usage errors, missing
# inputs, refusals, rejections, malformed/corrupt inputs, ambiguous or
# unresolvable parses, schema/collision/non-kebab violations.
GUARD_PATTERN = re.compile(
    r"^class\s+\w*("
    r"Guard|Usage|Missing|Refus|Reject|Malform|Fail|Corrupt|"
    r"Ambig|Unresolv|Unknown|EmptyAnd|Schema|Collision|Bad|"
    r"Orphan|Unmapped|Relative"
    r")\w*\(",
    re.MULTILINE,
)


class TestFr004BaseGate(unittest.TestCase):
    def test_per_command_module_exists_for_every_fr004_command(self):
        missing = [
            cmd for cmd in FR004_COMMANDS
            if not (COMMANDS_DIR / f"test_{cmd}.py").is_file()
        ]
        self.assertEqual(
            missing, [],
            msg=(
                "FR-004 commands missing a per-command test module "
                f"under {COMMANDS_DIR}: {missing}"
            ),
        )

    def test_every_fr004_command_has_a_happy_path_test_class(self):
        gaps = []
        for cmd in FR004_COMMANDS:
            text = (COMMANDS_DIR / f"test_{cmd}.py").read_text(encoding="utf-8")
            if not HAPPY_PATTERN.search(text):
                gaps.append(cmd)
        self.assertEqual(
            gaps, [],
            msg=(
                "FR-004 commands missing a happy-path test class "
                "(class name should contain 'Happy'): "
                f"{gaps}"
            ),
        )

    def test_every_fr004_command_has_a_guard_or_failure_test_class(self):
        gaps = []
        for cmd in FR004_COMMANDS:
            text = (COMMANDS_DIR / f"test_{cmd}.py").read_text(encoding="utf-8")
            if not GUARD_PATTERN.search(text):
                gaps.append(cmd)
        self.assertEqual(
            gaps, [],
            msg=(
                "FR-004 commands missing a guard/failure test class "
                "(class name should signal the failure mode — Guard, "
                "Usage, Missing, Refus, Reject, Malform, Fail, etc.): "
                f"{gaps}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
