"""Structural parity checks for every configuration/version consumer."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli.config_schema import CONFIG_SCHEMA, MACHINE_RECORD_SCHEMA_VERSION
from cli.config_surface_parity import (
    authored_field_prose_parity,
    canonical_suite_manifest_diagnostics,
    canonical_suite_observation,
    capability_example_parity,
    check_surface_registry,
    closed_value_parity,
    guidance_hygiene,
    legacy_vocabulary,
    load_registry,
    registry_inventory_evidence,
    schema_field_parity,
    wrapper_authority_vocabulary,
)
from cli.main import OPERATOR_ONLY_SUBCOMMANDS, build_parser
from cli.protocol_gate import read_shipped_project_schema_version
from mcp_server import server

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config-surfaces.json"
ENTRYPOINT = ROOT / "bin" / "cartopian"


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    action = next(
        item
        for item in parser._actions  # noqa: SLF001
        if isinstance(item, argparse._SubParsersAction)  # noqa: SLF001
    )
    return dict(action.choices)


def _run_cli(home: Path, *args: str) -> tuple[int, dict, str]:
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, str(ENTRYPOINT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(records) != 1:
        raise AssertionError(
            f"{args[0]} emitted {len(records)} records; stderr={result.stderr!r}"
        )
    return result.returncode, records[0], result.stderr


def _normalized_projection_payload(
    record: object,
    fixture_root: Path,
    repository_root: Path = ROOT,
) -> bytes:
    replacements = {
        str(fixture_root): "<fixture-root>",
        str(fixture_root.resolve()): "<fixture-root>",
        str(repository_root): "<install-root>",
        str(repository_root.resolve()): "<install-root>",
    }

    def normalize(value: object) -> object:
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, str):
            for source, marker in sorted(
                replacements.items(), key=lambda item: len(item[0]), reverse=True
            ):
                value = value.replace(source, marker)
            return value
        return value

    payload = json.dumps(
        normalize(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return payload.encode("utf-8")


def _normalized_projection_bytes(
    record: object,
    fixture_root: Path,
    repository_root: Path = ROOT,
) -> int:
    return len(
        _normalized_projection_payload(
            record,
            fixture_root,
            repository_root=repository_root,
        )
    )


def _additive_identity_delta(project_schema_version: str | None = None) -> int:
    target = (
        read_shipped_project_schema_version()
        if project_schema_version is None
        else project_schema_version
    )
    additive_identity_fields = {
        "record_schema_version": MACHINE_RECORD_SCHEMA_VERSION,
        "schema_identity": CONFIG_SCHEMA["schema_identity"],
        "project_schema_version": target,
    }
    # Adding these fields to an existing non-empty compact JSON object
    # replaces its final "}" with "," plus the three field encodings.
    return len(
        json.dumps(
            additive_identity_fields,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) - 1


def _run_cli_from_checkout(
    checkout: Path,
    home: Path,
    *args: str,
) -> tuple[int, dict, str]:
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, str(checkout / "bin" / "cartopian"), *args],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(records) != 1:
        raise AssertionError(
            f"{args[0]} emitted {len(records)} records; stderr={result.stderr!r}"
        )
    return result.returncode, records[0], result.stderr


class TestSurfaceRegistry(unittest.TestCase):
    def test_registry_is_complete_and_aligned(self) -> None:
        diagnostics = check_surface_registry(ROOT, REGISTRY)
        self.assertEqual(
            diagnostics,
            (),
            "\n" + "\n".join(json.dumps(item.as_record()) for item in diagnostics),
        )

    def test_deliberate_stale_preferred_example_is_detected(self) -> None:
        fixture = (
            ROOT / "tests" / "fixtures" / "config_surfaces" / "stale-preferred-example.md"
        )
        self.assertEqual(
            legacy_vocabulary(fixture.read_text(encoding="utf-8")),
            ("handoffs", "handoffs.*.auto_start_tasks"),
        )

    def test_deliberate_unregistered_consumer_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "cli").mkdir(parents=True)
            (root / "cli" / "launch_policy.py").write_text(
                "from cli.config_schema import CONFIG_SCHEMA\n", encoding="utf-8"
            )
            registry = {
                "registry_version": 1,
                "authority": "cli/config_schema.py::CONFIG_SCHEMA",
                "migration_authority": "cli/config_migration.py",
                "discovery": [
                    {
                        "globs": ["cli/**/*.py"],
                        "contains_any": ["CONFIG_SCHEMA"],
                    }
                ],
                "surfaces": [],
            }
            path = root / "config-surfaces.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            diagnostics = check_surface_registry(root, path)
        unregistered = [
            item for item in diagnostics if item.code == "unregistered-surface"
        ]
        self.assertEqual(len(unregistered), 1)
        self.assertEqual(unregistered[0].surface, "cli/launch_policy.py")

    def test_legacy_vocabulary_follows_any_shaped_authority_change(self) -> None:
        vocabulary = CONFIG_SCHEMA["legacy_vocabulary"]
        vocabulary["probe_category"] = ("retired_setting",)
        try:
            self.assertIn(
                "retired_setting",
                legacy_vocabulary("retired_setting = true"),
            )
        finally:
            del vocabulary["probe_category"]

    def test_preferred_contract_tests_are_registered_fail_closed(self) -> None:
        registry = load_registry(REGISTRY)
        preferred = next(
            item
            for item in registry["surfaces"]
            if item["id"] == "preferred-configuration-contract-tests"
        )
        self.assertEqual(preferred["legacy_policy"], "forbidden")
        self.assertIn("tests/wrappers/test_timeout_ssot.py", preferred["paths"])
        self.assertIn("tests/test_protocol_gate.py", preferred["paths"])

    def test_test_vocabulary_is_forbidden_by_default_with_narrow_allowlist(
        self,
    ) -> None:
        registry = load_registry(REGISTRY)
        tests_surface = next(
            item
            for item in registry["surfaces"]
            if item["id"] == "configuration-test-surfaces"
        )
        self.assertEqual(tests_surface["legacy_policy"], "forbidden")
        allowlist = registry["test_legacy_compatibility_allowlist"]
        self.assertTrue(allowlist)
        self.assertTrue(all(path.startswith("tests/") for path in allowlist))
        self.assertTrue(all("**" not in path for path in allowlist))
        self.assertIn(
            "tests/fixtures/config_surfaces/stale-preferred-example.md",
            allowlist,
        )

    def test_new_ordinary_test_and_fixture_fail_on_stale_vocabulary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-test-policy-") as raw:
            root = Path(raw) / "repo"
            shutil.copytree(
                ROOT,
                root,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            (root / "tests" / "test_new_surface.py").write_text(
                'CONFIG = "[handoffs.coder] auto_start_tasks = true"\n',
                encoding="utf-8",
            )
            fixture = root / "tests" / "fixtures" / "ordinary" / "legacy.toml"
            fixture.parent.mkdir()
            fixture.write_text(
                "[handoffs.coder]\nauto_start_tasks = true\n",
                encoding="utf-8",
            )
            diagnostics = check_surface_registry(root)
        stale_details = {
            item.detail
            for item in diagnostics
            if item.code == "stale-vocabulary"
        }
        self.assertTrue(
            any("tests/test_new_surface.py" in detail for detail in stale_details)
        )
        self.assertTrue(
            any(
                "tests/fixtures/ordinary/legacy.toml" in detail
                for detail in stale_details
            )
        )

    def test_schema_field_reference_rejects_missing_field_and_invented_value(
        self,
    ) -> None:
        reference = (ROOT / "CONFIG-MAPPING.md").read_text(encoding="utf-8")
        missing = schema_field_parity(
            reference.replace("max_handoffs_per_run", "REMOVED_FIELD"),
            surface="CONFIG-MAPPING.md",
        )
        self.assertTrue(
            any(
                item.code == "schema-field-missing"
                and "automation.max_handoffs_per_run" in item.detail
                for item in missing
            )
        )
        invented = schema_field_parity(
            reference
            + "\n`automation.initiation` also accepts `semi-auto`.\n",
            surface="CONFIG-MAPPING.md",
        )
        self.assertTrue(
            any(
                item.code == "schema-value-invented"
                and "semi-auto" in item.detail
                for item in invented
            )
        )

    def test_authored_field_prose_rejects_stale_active_paths(self) -> None:
        source = (ROOT / "protocol" / "CONVENTIONS.md").read_text(
            encoding="utf-8"
        )
        mutations = (
            (
                "with `target` has a resolved target/options record",
                "with `launch.target` has a resolved target/options record",
            ),
            (
                "configured `roles.<role>.timeout` value",
                "configured `launch.timeout`",
            ),
        )
        for preferred, stale in mutations:
            with self.subTest(stale=stale):
                self.assertIn(preferred, source)
                probe = source.replace(preferred, stale, 1)
                first = authored_field_prose_parity(
                    probe, surface="protocol/CONVENTIONS.md"
                )
                second = authored_field_prose_parity(
                    probe, surface="protocol/CONVENTIONS.md"
                )
                self.assertEqual(first, second)
                stale_diagnostics = [
                    item for item in first if item.code == "stale-authored-field"
                ]
                self.assertEqual(len(stale_diagnostics), 1)

    def test_authored_field_prose_rejects_legacy_adjacent_false_negatives(
        self,
    ) -> None:
        probes = (
            (
                "Migration tooling recognizes the nested legacy form. "
                "**Author** `roles.coder.launch.target` here.",
                "Neutral current guidance. "
                "**Author** `roles.coder.launch.target` here.",
            ),
            (
                "Migration tooling recognizes the nested legacy form. "
                "*Author* `roles.coder.launch.model` here.",
                "Neutral current guidance. "
                "*Author* `roles.coder.launch.model` here.",
            ),
            (
                "Migration tooling recognizes the nested legacy form. "
                '"Author `roles.coder.launch.effort` here."',
                "Neutral current guidance. "
                '"Author `roles.coder.launch.effort` here."',
            ),
            (
                "Migration tooling recognizes the nested legacy form. "
                "'Author `roles.coder.launch.timeout` here.'",
                "Neutral current guidance. "
                "'Author `roles.coder.launch.timeout` here.'",
            ),
            (
                "Migration tooling recognizes the nested legacy form. "
                "(Author `roles.coder.launch.target` here.)",
                "Neutral current guidance. "
                "(Author `roles.coder.launch.target` here.)",
            ),
            (
                "Migration tooling recognizes the nested legacy form. "
                "configure `roles.coder.launch.model` here.",
                "Neutral current guidance. "
                "configure `roles.coder.launch.model` here.",
            ),
            (
                "| Legacy form | Migration tooling recognizes "
                "`[roles.<role>.launch]` as migration input only. |\n"
                "| Current form | Author `roles.<role>.launch.target` here. |",
                "| Legacy form | Neutral current guidance. |\n"
                "| Current form | Author `roles.<role>.launch.target` here. |",
            ),
            (
                "- Migration tooling recognizes `[roles.coder.launch]` "
                "as migration input only.\n"
                "  Author `roles.coder.launch.target` here.",
                "- Neutral current guidance.\n"
                "  Author `roles.coder.launch.target` here.",
            ),
            (
                "Legacy compatibility only: old forms follow.\n\n"
                "Author `launch.timeout` in current TOML.",
                "Neutral current guidance.\n\n"
                "Author `launch.timeout` in current TOML.",
            ),
        )
        for compatibility_probe, neutral_control in probes:
            with self.subTest(probe=compatibility_probe):
                for text in (compatibility_probe, neutral_control):
                    diagnostics = authored_field_prose_parity(
                        text,
                        surface="probe.md",
                    )
                    self.assertEqual(len(diagnostics), 1)
                    self.assertEqual(
                        diagnostics[0].code,
                        "stale-authored-field",
                    )

    def test_authored_field_prose_allows_bounded_legacy_and_projection_paths(
        self,
    ) -> None:
        probes = (
            "Legacy compatibility only: migration tooling recognizes "
            "`[roles.<role>.launch]` and `launch.timeout` as migration input.",
            "Authored migration-source paths cover `[roles.<role>.launch]`.",
            "The superseded `[roles.coder.launch]` table is migration input only.",
            "Migration tooling, e.g. the compatibility reader, recognizes "
            "`[roles.<role>.launch]` as migration input only.",
            "Migration tooling recognizes the U.S. spelling "
            "`[roles.<role>.launch]` as migration input only.",
            "The resolved `launch.target` is consumed by handoff code.",
            "The derived `roles.<role>.launch.timeout` projection is read-only.",
        )
        for probe in probes:
            with self.subTest(probe=probe):
                self.assertEqual(
                    authored_field_prose_parity(probe, surface="probe.md"),
                    (),
                )

    def test_authored_field_prose_covers_every_declared_guidance_file(
        self,
    ) -> None:
        registry = load_registry(REGISTRY)
        rules = registry["authored_field_prose_parity"]
        self.assertEqual(len(rules), 1)
        paths = rules[0]["paths"]
        for required in (
            "AGENTS.md",
            "CAPABILITIES.md",
            "evaluations/README.md",
            "install-cartopian.md",
            "mcp_server/**/*.md",
            "mcp_server/**/*.py",
            "scripts/**/*.md",
            "scripts/**/*.py",
            "wrappers/*.md",
        ):
            self.assertIn(required, paths)

        matched = {
            path
            for pattern in paths
            for path in ROOT.glob(pattern)
            if path.is_file()
        }
        self.assertTrue(matched)
        for path in sorted(matched):
            relative = path.relative_to(ROOT).as_posix()
            source = path.read_text(encoding="utf-8")
            with self.subTest(surface=relative):
                self.assertEqual(
                    authored_field_prose_parity(source, surface=relative),
                    (),
                )
                diagnostics = authored_field_prose_parity(
                    source + "\n\nAuthor `roles.<role>.launch.target` here.\n",
                    surface=relative,
                )
                self.assertTrue(
                    any(
                        item.code == "stale-authored-field"
                        for item in diagnostics
                    )
                )
                for lead in ("**Author**", '"Author', "(Author", "configure"):
                    sentence_mutation = (
                        source
                        + "\n\nMigration tooling recognizes the nested "
                        "legacy form. "
                        + lead
                        + " `roles.<role>.launch.target` here.\n"
                    )
                    sentence_diagnostics = authored_field_prose_parity(
                        sentence_mutation,
                        surface=relative,
                    )
                    self.assertTrue(
                        any(
                            item.code == "stale-authored-field"
                            for item in sentence_diagnostics
                        ),
                        msg=f"{relative} did not bite {lead!r} sentence",
                    )
                list_item = re.search(
                    r"(?m)^(?P<indent>[ \t]*)(?:[-+*]|\d+[.)])[ \t]+",
                    source,
                )
                if list_item is not None:
                    indent = list_item.group("indent")
                    insertion = (
                        f"{indent}- Migration tooling recognizes "
                        "`[roles.<role>.launch]` as migration input only.\n"
                        f"{indent}  Author "
                        "`roles.<role>.launch.target` here.\n"
                    )
                    list_mutation = (
                        source[: list_item.start()]
                        + insertion
                        + source[list_item.start() :]
                    )
                    list_diagnostics = authored_field_prose_parity(
                        list_mutation,
                        surface=relative,
                    )
                    self.assertTrue(
                        any(
                            item.code == "stale-authored-field"
                            for item in list_diagnostics
                        ),
                        msg=f"{relative} did not bite indented continuation",
                    )
                table_row = re.search(r"(?m)^[ \t]*\|", source)
                if table_row is not None:
                    table_insertion = (
                        "| Legacy form | Migration tooling recognizes "
                        "`[roles.<role>.launch]` as migration input only. |\n"
                        "| Current form | Author "
                        "`roles.<role>.launch.target` here. |\n"
                    )
                    table_mutation = (
                        source[: table_row.start()]
                        + table_insertion
                        + source[table_row.start() :]
                    )
                    table_diagnostics = authored_field_prose_parity(
                        table_mutation,
                        surface=relative,
                    )
                    self.assertTrue(
                        any(
                            item.code == "stale-authored-field"
                            for item in table_diagnostics
                        ),
                        msg=f"{relative} did not bite table row",
                    )

    def test_capability_examples_follow_live_vocabulary_and_bite_unknowns(
        self,
    ) -> None:
        registry = load_registry(REGISTRY)
        rules = registry["capability_example_parity"]
        matched = {
            path
            for rule in rules
            for pattern in rule["paths"]
            for path in ROOT.glob(pattern)
            if path.is_file()
        }
        self.assertTrue(matched)
        for path in sorted(matched):
            relative = path.relative_to(ROOT).as_posix()
            self.assertEqual(
                capability_example_parity(
                    path.read_text(encoding="utf-8"),
                    surface=relative,
                ),
                (),
            )
        diagnostics = capability_example_parity(
            'grants = ["preset:standard"]\n',
            surface="current-successor.md",
        )
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(
            diagnostics[0].code,
            "unknown-capability-example",
        )
        multiline = capability_example_parity(
            "grants = [\n"
            '  "coder-like",\n'
            '  "read:reports",\n'
            "]\n",
            surface="current-successor.md",
        )
        self.assertEqual(multiline, ())
        multiline_invalid = capability_example_parity(
            "grants = [\n"
            '  "coder-like",\n'
            '  "preset:standard",\n'
            "]\n",
            surface="current-successor.md",
        )
        self.assertEqual(len(multiline_invalid), 1)
        self.assertIn("preset:standard", multiline_invalid[0].detail)

    def test_capability_examples_allow_bounded_explanation_not_authored_use(
        self,
    ) -> None:
        explanatory = (
            "DEC-002's earlier example used "
            '`grants = ["preset:standard"]`. That value is outside the '
            "closed capability vocabulary and is rejected.\n\n"
            "The retired value `grants = [\n"
            '  "preset:standard",\n'
            "]` must not be used.\n\n"
            "```toml\n"
            "grants = [\n"
            '  "coder-like",\n'
            "]\n"
            "```\n"
        )
        self.assertEqual(
            capability_example_parity(
                explanatory,
                surface="current-successor.md",
            ),
            (),
        )
        active = (
            "Use this current authored example:\n\n"
            "```toml\n"
            "grants = [\n"
            '  "preset:standard",\n'
            "]\n"
            "```\n"
        )
        diagnostics = capability_example_parity(
            active,
            surface="current-successor.md",
        )
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(
            diagnostics[0].code,
            "unknown-capability-example",
        )
        masking_probes = (
            "Nothing invalid here: set "
            "grants = [retired-invalid-value] in your role table.",
            "Retired handoff fields are removed; author "
            'grants = ["preset:standard"] in each role.',
            "```toml\n"
            "# unsupported keys are ignored\n"
            'grants = ["preset:standard"]\n'
            "```\n",
        )
        for probe in masking_probes:
            with self.subTest(probe=probe):
                diagnostics = capability_example_parity(
                    probe,
                    surface="current-successor.md",
                )
                self.assertEqual(len(diagnostics), 1)
                self.assertEqual(
                    diagnostics[0].code,
                    "unknown-capability-example",
                )

        bound_explanations = (
            "DEC-002's earlier example used "
            '`grants = ["preset:standard"]`.',
            "The invalid capability value "
            '`grants = ["preset:standard"]` is rejected.',
            "Do not use `grants = [\"preset:standard\"]`.",
            "`grants = [\"preset:standard\"]` must not be used.",
        )
        for probe in bound_explanations:
            with self.subTest(probe=probe):
                self.assertEqual(
                    capability_example_parity(
                        probe,
                        surface="current-successor.md",
                    ),
                    (),
                )

    def test_canonical_suite_manifest_and_observations_fail_closed(self) -> None:
        registry = load_registry(REGISTRY)
        self.assertEqual(
            canonical_suite_manifest_diagnostics(ROOT, registry),
            (),
        )
        pytest_suite = next(
            suite
            for suite in registry["canonical_test_suites"]
            if suite["id"] == "pytest"
        )
        green = canonical_suite_observation(
            pytest_suite,
            collection_exit_code=0,
            execution_exit_code=0,
            collected_total=pytest_suite["minimum_collected"],
            collected_by_path={
                "tests/cli/commands/test_wait_handoff.py": 18,
            },
        )
        self.assertTrue(green["green"])
        uncollected = canonical_suite_observation(
            pytest_suite,
            collection_exit_code=0,
            execution_exit_code=0,
            collected_total=pytest_suite["minimum_collected"],
            collected_by_path={
                "tests/cli/commands/test_wait_handoff.py": 17,
            },
        )
        self.assertFalse(uncollected["green"])
        failing = canonical_suite_observation(
            pytest_suite,
            collection_exit_code=0,
            execution_exit_code=1,
            collected_total=pytest_suite["minimum_collected"],
            collected_by_path={
                "tests/cli/commands/test_wait_handoff.py": 18,
            },
        )
        self.assertFalse(failing["green"])
        partial_collapse = canonical_suite_observation(
            pytest_suite,
            collection_exit_code=0,
            execution_exit_code=0,
            collected_total=pytest_suite["minimum_collected"] - 100,
            collected_by_path={
                "tests/cli/commands/test_wait_handoff.py": 18,
            },
        )
        self.assertFalse(partial_collapse["green"])
        for invalid_suite in (
            {"required_tests": []},
            {"minimum_collected": 0, "required_tests": []},
            {"minimum_collected": 1, "required_tests": []},
        ):
            with self.subTest(invalid_suite=invalid_suite):
                bypass = canonical_suite_observation(
                    invalid_suite,
                    collection_exit_code=0,
                    execution_exit_code=0,
                    collected_total=1,
                    collected_by_path={},
                )
                self.assertFalse(bypass["green"])
                self.assertIn(
                    "invalid-minimum-collected",
                    bypass["issues"],
                )

    def test_authored_field_prose_follows_both_authoritative_catalogs(self) -> None:
        fields = CONFIG_SCHEMA["fields"]
        vocabulary = CONFIG_SCHEMA["legacy_vocabulary"]
        original_paths = vocabulary["authored_config_paths"]
        fields["roles.*.probe"] = {
            "scopes": ("global", "project"),
            "type": "non-empty-string",
        }
        vocabulary["authored_config_paths"] = (
            *original_paths,
            "roles.*.launch.probe",
        )
        try:
            diagnostics = authored_field_prose_parity(
                "Set the active `launch.probe` field.",
                surface="probe.md",
            )
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].code, "stale-authored-field")
        finally:
            vocabulary["authored_config_paths"] = original_paths
            del fields["roles.*.probe"]

    def test_guidance_hygiene_rejects_host_operator_and_secret_values(self) -> None:
        issues = guidance_hygiene(
            "path=/Users/alice/work\n"
            "owner=alice@example.org\n"
            "api_key=sk-probe0123456789012345\n"
        )
        self.assertIn("macos-user-path", issues)
        self.assertIn("operator-email", issues)
        self.assertIn("openai-secret", issues)

    def test_guidance_hygiene_allows_placeholders_and_labeled_context(self) -> None:
        self.assertEqual(
            guidance_hygiene(
                "Use /Users/<name>/work or /Users/me/work; "
                "contact maintainer@example.com."
            ),
            (),
        )

    def test_registry_hygiene_rejects_wrapper_and_installer_leaks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-hygiene-policy-") as raw:
            root = Path(raw) / "repo"
            shutil.copytree(
                ROOT,
                root,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            wrapper = root / "wrappers" / "bin" / "cartopian-codex"
            wrapper.write_text(
                wrapper.read_text(encoding="utf-8")
                + "\n# /Users/alice/work alice@operator.invalid "
                "sk-probe0123456789012345\n",
                encoding="utf-8",
            )
            installer = root / "install-cartopian.md"
            installer.write_text(
                installer.read_text(encoding="utf-8")
                + "\n/home/alice/work owner@operator.invalid "
                "ghp_012345678901234567890123\n",
                encoding="utf-8",
            )
            diagnostics = check_surface_registry(root)
        hygiene = {
            item.surface: item.detail
            for item in diagnostics
            if item.code == "output-hygiene"
        }
        self.assertIn("wrappers/bin/cartopian-codex", hygiene)
        self.assertIn("macos-user-path", hygiene["wrappers/bin/cartopian-codex"])
        self.assertIn("operator-email", hygiene["wrappers/bin/cartopian-codex"])
        self.assertIn("openai-secret", hygiene["wrappers/bin/cartopian-codex"])
        self.assertIn("install-cartopian.md", hygiene)
        self.assertIn("linux-user-path", hygiene["install-cartopian.md"])
        self.assertIn("operator-email", hygiene["install-cartopian.md"])
        self.assertIn("github-secret", hygiene["install-cartopian.md"])
        self.assertEqual(
            guidance_hygiene(
                "Host-specific example (macos): /Users/alice/work",
                target_context="macos",
            ),
            (),
        )
        self.assertEqual(
            guidance_hygiene(
                "Fixture classification: output-hygiene\n"
                "/Users/alice/work\nsk-probe0123456789012345\n",
                allow_labeled_fixture=True,
            ),
            (),
        )

    def test_context_ceilings_reconcile_phase_00_and_task_deltas(self) -> None:
        registry = load_registry(REGISTRY)
        budgets = {
            item["surface"]: item for item in registry["context_budgets"]
        }
        derived_identity_delta = _additive_identity_delta()
        self.assertEqual(
            budgets["next-action"]["phase_00_baseline"],
            {"label": "Phase 00 status/next-action stdout NDJSON", "bytes": 1150},
        )
        self.assertEqual(
            budgets["task-bundle"]["phase_00_baseline"]["bytes"], 1099
        )
        self.assertEqual(
            budgets["handoff-packet"]["phase_00_baseline"]["bytes"], 1197
        )
        self.assertEqual(
            {name: item["task_delta_bytes"] for name, item in budgets.items()},
            {
                "resolve-config": 26,
                "next-action": derived_identity_delta,
                "task-bundle": derived_identity_delta,
                "handoff-packet": derived_identity_delta,
                "containment-matrix": derived_identity_delta,
                "plan-audit": derived_identity_delta,
                # review-context postdates the identity-delta task, so it has
                # no Phase 00 baseline and no task delta to reconcile. Its
                # ceiling is set by the exact request-record contract
                # instead, and it declares that explicitly rather than
                # borrowing a baseline it never had.
                "review-context": None,
            },
        )
        self.assertIsNone(budgets["review-context"]["phase_00_baseline"])
        self.assertIn(
            "24 KiB", budgets["review-context"]["request_trace_channel_note"]
        )
        self.assertGreaterEqual(
            budgets["review-context"]["max_output_bytes"],
            24 * 1024,
        )
        for item in budgets.values():
            benefit = item["record_versioning_benefit"]
            self.assertIn("record_schema_version", benefit)
            self.assertIn("project_schema_version", benefit)
            self.assertNotIn("aligned canonical fixture", json.dumps(item))

    def test_identity_delta_tracks_authoritative_marker_length(self) -> None:
        current_target = read_shipped_project_schema_version()
        longer_future_target = "v100.200.300"
        self.assertNotEqual(
            len(current_target),
            len(longer_future_target),
        )
        self.assertNotEqual(
            _additive_identity_delta(current_target),
            _additive_identity_delta(longer_future_target),
        )

    def test_every_schema_claim_declares_a_real_parity_mechanism(self) -> None:
        registry = load_registry(REGISTRY)
        table_owners = {
            rule["surface"] for rule in registry["schema_field_parity"]
        }
        prose_owners = {
            rule["surface"] for rule in registry["closed_value_parity"]
        }
        claiming = [
            entry
            for entry in registry["surfaces"]
            if (
                "CONFIG_SCHEMA.fields" in entry["facts_consumed"]
                or "CONFIG_SCHEMA.fields[].values" in entry["facts_consumed"]
            )
        ]
        self.assertTrue(claiming)
        for entry in claiming:
            with self.subTest(surface=entry["id"]):
                mechanism = entry["parity_mechanism"]
                if mechanism["kind"] == "schema-field-table":
                    self.assertIn(entry["id"], table_owners)
                elif mechanism["kind"] == "closed-value-prose":
                    self.assertIn(entry["id"], prose_owners)
                else:
                    self.assertEqual(mechanism["kind"], "executable-contract")
                    anchor_path, anchor_symbol = mechanism["check"].split("::", 1)
                    self.assertIn(
                        f"def {anchor_symbol}(",
                        (ROOT / anchor_path).read_text(encoding="utf-8"),
                    )

    def test_real_six_surface_pipeline_is_checkout_root_invariant(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-checkout-invariance-") as raw:
            root = Path(raw)
            short_checkout = root / "r"
            long_checkout = root / ("repository-" + ("x" * 96))
            self.assertGreater(
                len(str(long_checkout)) - len(str(short_checkout)),
                80,
            )
            ignore = shutil.ignore_patterns(
                ".git",
                "tests",
                "__pycache__",
                ".pytest_cache",
            )
            for checkout in (short_checkout, long_checkout):
                shutil.copytree(ROOT, checkout, ignore=ignore)

            fixture = root / "fixture"
            home = fixture / "home"
            project = fixture / "project"
            work_root = fixture / "tool-repo"
            for path in (
                home,
                project / "phases",
                project / "tasks" / "open",
                project / "tasks" / "in-progress",
                project / "tasks" / "in-review",
                project / "tasks" / "done",
                project / "prompts",
                project / "reports",
                project / "specs",
                project / "decisions",
                project / "reviews",
                project / "resources",
                work_root,
            ):
                path.mkdir(parents=True, exist_ok=True)
            (project / "cartopian.toml").write_text(
                TestProjectionParity._CONFIG,
                encoding="utf-8",
            )
            (project / "cartopian.local.toml").write_text(
                f'[work_roots]\ntool-repo = "{work_root}"\n',
                encoding="utf-8",
            )
            (project / "STATE.md").write_text(
                "# State\n\n## Situation\n\nNone.\n",
                encoding="utf-8",
            )
            (project / "STANDARDS.md").write_text(
                "# Standards\n",
                encoding="utf-8",
            )
            (project / "phases" / "PHASE-01-build.md").write_text(
                "# PHASE-01: Build\n",
                encoding="utf-8",
            )
            task = project / "tasks" / "open" / "TASK-01-001-build.md"
            task.write_text(
                "# TASK-01-001: Build\n\n"
                "Phase: PHASE-01-build\n"
                "Plan ref: n/a\n"
                "Work root: tool-repo\n"
                "Assignee: coder\n"
                "Spec: none\n"
                "Depends on: n/a\n"
                "Blocked by: n/a\n"
                "Created: 2026-07-25\n"
                "Evidence gate: n/a\n\n"
                "## Goal\n\nBuild the fixture.\n",
                encoding="utf-8",
            )
            request_source = fixture / "operator-message.txt"
            request_source.write_text("Build the fixture.", encoding="utf-8")
            capture_code, _, capture_stderr = _run_cli(
                home,
                "capture-request",
                str(project),
                "--request-id",
                "REQUEST-001",
                "--unit",
                "task:TASK-01-001",
                "--content-file",
                str(request_source),
                "--captured-at",
                "2026-07-27T12:00:00Z",
            )
            self.assertEqual(capture_code, 0, capture_stderr)
            commands = {
                "resolve-config": ("resolve-config", str(project)),
                "next-action": ("next-action", str(project)),
                "task-bundle": ("task-bundle", str(task)),
                "handoff-packet": (
                    "handoff-packet",
                    str(task),
                    "--role",
                    "coder",
                ),
                "containment-matrix": ("containment-matrix", str(project)),
                "plan-audit": ("plan-audit", str(project)),
                "review-context": (
                    "review-context",
                    str(project),
                    "--review-kind",
                    "task-closure",
                    "--task",
                    str(task),
                ),
            }
            pipelines = {}
            for checkout in (short_checkout, long_checkout):
                checkout_records = {}
                for name, args in commands.items():
                    code, record, stderr = _run_cli_from_checkout(
                        checkout,
                        home,
                        *args,
                    )
                    self.assertIn(code, (0, 1), msg=f"{name}: {stderr}")
                    checkout_records[name] = _normalized_projection_payload(
                        record,
                        fixture,
                        repository_root=checkout,
                    )
                pipelines[checkout.name] = checkout_records
            self.assertEqual(
                pipelines[short_checkout.name],
                pipelines[long_checkout.name],
            )

    def test_guidance_hygiene_scopes_hosted_ci_placeholders_to_workspaces(self) -> None:
        self.assertEqual(
            guidance_hygiene(
                "Use $HOME/project, ${HOME}/project, "
                "/home/runner/work/repository/repository, and "
                "C:\\Users\\runneradmin\\work\\repository."
            ),
            (),
        )
        self.assertEqual(
            guidance_hygiene(
                "/home/runner/private /Users/runner/private "
                "C:\\Users\\runneradmin\\private"
            ),
            ("linux-user-path", "macos-user-path", "windows-user-path"),
        )

    def test_registry_inventory_evidence_fails_on_semantic_declaration(self) -> None:
        registry = load_registry(REGISTRY)
        self.assertTrue(registry_inventory_evidence(registry)["inventory_only"])
        mutated = json.loads(json.dumps(registry))
        mutated["accepted_values"] = {"automation.initiation": ["invented"]}
        evidence = registry_inventory_evidence(mutated)
        self.assertFalse(evidence["inventory_only"])
        self.assertEqual(
            evidence["forbidden_semantic_declarations"],
            ("accepted_values",),
        )

    def test_closed_value_prose_mutations_are_deterministically_rejected(self) -> None:
        probes = (
            ("README.md", "`task_run`", "`teleport_run`"),
            (
                "README.md",
                'initiation = "operator"',
                'initiation = "instant"',
            ),
            (
                "README.md",
                'planning = "required"',
                'planning = "mandatory"',
            ),
            (
                "protocol/CONVENTIONS.md",
                "`task_run`, `task_review`, and `planning_review`",
                "`teleport_run`, `task_review`, and `planning_review`",
            ),
            (
                "protocol/CONVENTIONS.md",
                'confirmation = "each-handoff"',
                'confirmation = "sometimes"',
            ),
            (
                "protocol/CONVENTIONS.md",
                'planning = "required"',
                'planning = "mandatory"',
            ),
            ("protocol/CONVENTIONS.md", "`squash`", "`octopus`"),
        )
        for relative, old, new in probes:
            with self.subTest(surface=relative, invented=new):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn(old, source)
                mutated = source.replace(old, new, 1)
                first = closed_value_parity(mutated, surface=relative)
                second = closed_value_parity(mutated, surface=relative)
                self.assertEqual(first, second)
                self.assertTrue(first)
                self.assertTrue(
                    all(item.code == "schema-value-invented" for item in first)
                )


class TestCliMcpContractParity(unittest.TestCase):
    def test_generator_and_editor_represent_every_schema_field(self) -> None:
        from cli.commands import update_config

        generator_actions = {
            "project.id": "proj_id",
            "project.name": "name",
            "project.project_schema_version": "<derived-shipped-target>",
            "project.work_roots": "work_root",
            "roles.*.description": "role",
            "roles.*.grants": "role_grants",
            "roles.*.target": "role_launch_target",
            "roles.*.model": "role_launch_model",
            "roles.*.effort": "role_launch_effort",
            "roles.*.timeout": "role_launch_timeout",
            "roles.*.auto_launch": "role_auto_launch",
            "reviews.planning": "review_planning",
            "reviews.planning_role": "review_planning_role",
            "reviews.task_closure": "review_task_closure",
            "reviews.task_role": "review_task_role",
            "automation.initiation": "automation_initiation",
            "automation.confirmation": "automation_confirmation",
            "automation.max_handoffs_per_run": "automation_max_handoffs",
            "defaults.git_versioning": "git_versioning",
            "git.pm_owns_product_branches": "git_key",
            "git.default_branch_pattern": "git_key",
            "git.default_merge_strategy": "git_key",
        }
        expected_generator_fields = set(CONFIG_SCHEMA["fields"]) - {"work_roots.*"}
        self.assertEqual(set(generator_actions), expected_generator_fields)
        actions = {
            action.dest
            for action in _subparsers()["generate-config"]._actions  # noqa: SLF001
        }
        self.assertTrue(
            {
                action
                for action in generator_actions.values()
                if not action.startswith("<")
            }.issubset(actions)
        )

        editor_fields = set(update_config.SCHEMA)
        editor_fields.update(
            {
                "roles.*.description",
                "roles.*.grants",
                "roles.*.target",
                "roles.*.model",
                "roles.*.effort",
                "roles.*.timeout",
                "roles.*.auto_launch",
                "work_roots.*",
            }
        )
        self.assertEqual(editor_fields, set(CONFIG_SCHEMA["fields"]))

    def test_generate_config_closed_values_derive_from_authoritative_contract(self) -> None:
        generate = _subparsers()["generate-config"]
        actions = {action.dest: action for action in generate._actions}  # noqa: SLF001
        fields = CONFIG_SCHEMA["fields"]
        self.assertEqual(
            tuple(actions["automation_initiation"].choices),
            fields["automation.initiation"]["values"],
        )
        self.assertEqual(
            tuple(actions["automation_confirmation"].choices),
            fields["automation.confirmation"]["values"],
        )
        self.assertEqual(
            tuple(actions["review_planning"].choices),
            fields["reviews.planning"]["values"],
        )
        self.assertEqual(
            tuple(actions["review_task_closure"].choices),
            fields["reviews.task_closure"]["values"],
        )

    def test_mcp_schema_independently_matches_argparse_actions(self) -> None:
        server._TOOL_CACHE = None
        subparsers = _subparsers()
        listed = {item["name"]: item for item in server.list_tools()}
        # Operator-only subcommands are deliberately absent from the tool
        # surface: host intake capture must not become a dispatched handoff
        # management-callable MCP writer.
        agent_facing = {
            name: sub
            for name, sub in subparsers.items()
            if name not in OPERATOR_ONLY_SUBCOMMANDS
        }
        self.assertEqual(
            set(listed),
            {name.replace("-", "_") for name in agent_facing},
        )
        for cli_name, sub in agent_facing.items():
            schema = listed[cli_name.replace("-", "_")]["inputSchema"]
            actions = [
                action
                for action in sub._actions  # noqa: SLF001
                if not isinstance(action, argparse._HelpAction)  # noqa: SLF001
                and action.dest not in (None, argparse.SUPPRESS)
            ]
            self.assertEqual(set(schema["properties"]), {a.dest for a in actions})
            expected_required = []
            for action in actions:
                observed = schema["properties"][action.dest]
                if isinstance(action, argparse._AppendAction):  # noqa: SLF001
                    self.assertEqual(observed["type"], "array")
                elif isinstance(
                    action,
                    (argparse._StoreTrueAction, argparse._StoreFalseAction),  # noqa: SLF001
                ):
                    self.assertEqual(observed["type"], "boolean")
                elif action.type is int:
                    self.assertEqual(observed["type"], "integer")
                elif action.type is float:
                    self.assertEqual(observed["type"], "number")
                else:
                    self.assertEqual(observed["type"], "string")
                if action.choices:
                    self.assertEqual(observed["enum"], list(action.choices))
                if action.option_strings:
                    if action.required:
                        expected_required.append(action.dest)
                elif action.nargs in (None, 1) or (
                    isinstance(action.nargs, int) and action.nargs >= 1
                ):
                    expected_required.append(action.dest)
            self.assertEqual(schema.get("required", []), expected_required)

    def test_update_config_enum_domains_match_authoritative_contract(self) -> None:
        from cli.commands import update_config

        scalar_fields = {
            name
            for name in CONFIG_SCHEMA["fields"]
            if not name.startswith("roles.") and name != "work_roots.*"
        }
        self.assertEqual(set(update_config.SCHEMA), scalar_fields)
        for field in (
            "automation.initiation",
            "automation.confirmation",
            "reviews.planning",
            "reviews.task_closure",
            "git.default_merge_strategy",
        ):
            values = CONFIG_SCHEMA["fields"][field]["values"]
            validator = update_config.SCHEMA[field][2]
            for value in values:
                self.assertEqual(validator(value), json.dumps(value))
            with self.assertRaises(Exception):
                validator("__outside_contract__")

    def test_cli_and_mcp_return_the_same_validation_diagnostic(self) -> None:
        argv = [
            "/tmp/config-parity-probe",
            "--name",
            "Probe",
            "--id",
            "probe",
            "--automation-initiation",
            "__outside_contract__",
        ]
        direct = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "generate-config", *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        via_mcp = server._invoke_cli("generate-config", argv)
        self.assertEqual(via_mcp["exit_code"], direct.returncode)
        self.assertEqual(via_mcp["stderr_lines"], direct.stderr.splitlines())


class TestWrapperNeutrality(unittest.TestCase):
    def test_deliberate_raw_policy_probe_is_detected(self) -> None:
        self.assertEqual(
            wrapper_authority_vocabulary(
                "read [reviews] and roles.<role>.auto_launch before launch"
            ),
            ("raw-review-policy", "raw-role-schema", "launch-policy"),
        )


class TestProjectionParity(unittest.TestCase):
    _CONFIG = (
        "[project]\n"
        'id = "surface-parity"\n'
        'name = "Surface Parity"\n'
        'project_schema_version = "v0.9.0"\n'
        'work_roots = ["tool-repo"]\n'
        "\n"
        "[roles.coder]\n"
        'description = "Implements tasks per spec."\n'
        'grants = ["coder-like"]\n'
        'auto_launch = ["task_run"]\n'
        'target = "cartopian-codex"\n'
        'model = "gpt-5-codex"\n'
        'effort = "high"\n'
        'timeout = "30m"\n'
        "\n"
        "[reviews]\n"
        'planning = "off"\n'
        'task_closure = "off"\n'
        "\n"
        "[automation]\n"
        'initiation = "operator"\n'
        'confirmation = "each-handoff"\n'
        "max_handoffs_per_run = 1\n"
    )

    def test_bounded_projections_preserve_canonical_facts_and_budgets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-surface-parity-") as raw:
            fixture = Path(raw)
            home = fixture / "home"
            project = fixture / "project"
            work_root = fixture / "tool-repo"
            home.mkdir()
            project.mkdir()
            work_root.mkdir()
            for relative in (
                "phases",
                "tasks/open",
                "tasks/in-progress",
                "tasks/in-review",
                "tasks/done",
                "prompts",
                "reports",
                "specs",
                "decisions",
                "reviews",
                "resources",
            ):
                (project / relative).mkdir(parents=True, exist_ok=True)
            (project / "cartopian.toml").write_text(self._CONFIG, encoding="utf-8")
            (project / "cartopian.local.toml").write_text(
                f'[work_roots]\ntool-repo = "{work_root}"\n', encoding="utf-8"
            )
            (project / "STATE.md").write_text(
                "# State\n\n## Situation\n\nNone.\n", encoding="utf-8"
            )
            (project / "STANDARDS.md").write_text("# Standards\n", encoding="utf-8")
            (project / "phases" / "PHASE-01-build.md").write_text(
                "# PHASE-01: Build\n", encoding="utf-8"
            )
            task = project / "tasks" / "open" / "TASK-01-001-build.md"
            task.write_text(
                "# TASK-01-001: Build\n\n"
                "Phase: PHASE-01-build\n"
                "Plan ref: n/a\n"
                "Work root: tool-repo\n"
                "Assignee: coder\n"
                "Spec: none\n"
                "Depends on: n/a\n"
                "Blocked by: n/a\n"
                "Created: 2026-07-25\n"
                "Evidence gate: n/a\n\n"
                "## Goal\n\nBuild the fixture.\n\n"
                "## Acceptance\n\n- [ ] Fixture is built.\n",
                encoding="utf-8",
            )
            request_source = fixture / "operator-message.txt"
            request_source.write_text("Build the fixture.", encoding="utf-8")
            capture_code, _, capture_stderr = _run_cli(
                home,
                "capture-request",
                str(project),
                "--request-id",
                "REQUEST-001",
                "--unit",
                "task:TASK-01-001",
                "--content-file",
                str(request_source),
                "--captured-at",
                "2026-07-27T12:00:00Z",
            )
            self.assertEqual(capture_code, 0, capture_stderr)

            commands = {
                "resolve-config": ("resolve-config", str(project)),
                "next-action": ("next-action", str(project)),
                "task-bundle": ("task-bundle", str(task)),
                "handoff-packet": ("handoff-packet", str(task), "--role", "coder"),
                "containment-matrix": ("containment-matrix", str(project)),
                "plan-audit": ("plan-audit", str(project)),
                "review-context": (
                    "review-context",
                    str(project),
                    "--review-kind",
                    "task-closure",
                    "--task",
                    str(task),
                ),
            }
            records = {}
            for name, args in commands.items():
                code, record, stderr = _run_cli(home, *args)
                self.assertIn(code, (0, 1), msg=f"{name}: {stderr}")
                records[name] = record

            canonical = records["resolve-config"]
            for name, record in records.items():
                with self.subTest(surface=name):
                    self.assertEqual(record["record_schema_version"], 1)
                    self.assertEqual(record["schema_identity"], canonical["schema_identity"])
                    self.assertEqual(
                        record["project_schema_version"],
                        canonical["project_schema_version"],
                    )
            self.assertEqual(records["next-action"]["roles"], canonical["roles"])
            self.assertEqual(records["next-action"]["reviews"], canonical["reviews"])
            self.assertEqual(records["next-action"]["automation"], canonical["automation"])
            coder = canonical["roles"]["coder"]
            packet = records["handoff-packet"]
            self.assertEqual(packet["launch"], coder["launch"])
            self.assertEqual(packet["auto_launch"], coder["auto_launch"])
            self.assertEqual(packet["effective_grants"], coder["effective_grants"])
            self.assertEqual(packet["reviews"], canonical["reviews"])
            self.assertEqual(packet["automation_policy"], canonical["automation"])
            self.assertEqual(
                records["task-bundle"]["work_roots_resolved"][0]["absolute_path"],
                canonical["work_roots"]["tool-repo"],
            )
            self.assertEqual(
                records["containment-matrix"]["activated"],
                canonical["capabilities"]["activated"],
            )
            # The two review channels stay separate and task provenance is
            # explicit rather than silently inherited from project intake.
            trace = records["review-context"]["request_trace"]
            self.assertEqual(trace["state"], "resolved")
            self.assertEqual(trace["records"][0]["unit"]["kind"], "task")
            self.assertIn(
                "management_guidance", records["review-context"]
            )

            registry = load_registry(REGISTRY)
            budgets = {
                item["surface"]: item for item in registry["context_budgets"]
            }
            self.assertEqual(set(records), set(budgets))
            for name, record in records.items():
                measured = _normalized_projection_bytes(record, fixture)
                with self.subTest(context_budget=name):
                    self.assertLessEqual(measured, budgets[name]["max_output_bytes"])

    def test_lifecycle_paths_resolve_while_authored_work_root_spelling_is_stable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="cartopian-path-spelling-", dir="/tmp"
        ) as raw:
            authored_root = Path(raw)
            project = authored_root / "project"
            work_root = authored_root / "tool-repo"
            home = authored_root / "home"
            for path in (
                project / "phases",
                project / "tasks" / "open",
                project / "tasks" / "in-progress",
                project / "tasks" / "in-review",
                project / "tasks" / "done",
                project / "prompts",
                project / "reports",
                project / "specs",
                project / "decisions",
                project / "reviews",
                project / "resources",
                work_root,
                home,
            ):
                path.mkdir(parents=True, exist_ok=True)
            (project / "cartopian.toml").write_text(
                self._CONFIG, encoding="utf-8"
            )
            (project / "cartopian.local.toml").write_text(
                f'[work_roots]\ntool-repo = "{work_root}"\n', encoding="utf-8"
            )
            (project / "STATE.md").write_text(
                "# State\n\n## Situation\n\nNone.\n", encoding="utf-8"
            )
            (project / "STANDARDS.md").write_text(
                "# Standards\n", encoding="utf-8"
            )
            (project / "phases" / "PHASE-01-build.md").write_text(
                "# PHASE-01: Build\n", encoding="utf-8"
            )
            task = project / "tasks" / "open" / "TASK-01-001-build.md"
            task.write_text(
                "# TASK-01-001: Build\n\n"
                "Phase: PHASE-01-build\nPlan ref: n/a\nWork root: tool-repo\n"
                "Assignee: coder\nSpec: none\nDepends on: n/a\nBlocked by: n/a\n"
                "Created: 2026-07-25\nEvidence gate: n/a\n\n"
                "## Goal\n\nBuild.\n\n## Acceptance\n\n- [ ] Built.\n",
                encoding="utf-8",
            )
            commands = {
                "resolve-config": ("resolve-config", str(project)),
                "next-action": ("next-action", str(project)),
                "task-bundle": ("task-bundle", str(task)),
                "handoff-packet": (
                    "handoff-packet",
                    str(task),
                    "--role",
                    "coder",
                ),
                "containment-matrix": ("containment-matrix", str(project)),
                "plan-audit": ("plan-audit", str(project)),
            }
            records = {}
            for name, args in commands.items():
                code, record, stderr = _run_cli(home, *args)
                self.assertIn(code, (0, 1), f"{name}: {stderr}")
                records[name] = record

            resolved_project = str(project.resolve())
            resolved_task = str(task.resolve())
            for name in (
                "resolve-config",
                "next-action",
                "containment-matrix",
                "plan-audit",
            ):
                with self.subTest(surface=name, path="project_path"):
                    self.assertEqual(
                        records[name]["project_path"],
                        resolved_project,
                    )
            for name in ("task-bundle", "handoff-packet"):
                with self.subTest(surface=name, path="task_path"):
                    self.assertEqual(records[name]["task_path"], resolved_task)

            record = records["task-bundle"]
            self.assertEqual(
                record["work_roots_resolved"][0]["absolute_path"],
                str(work_root),
            )
            self.assertEqual(
                records["resolve-config"]["work_roots"]["tool-repo"],
                str(work_root),
            )
            expected_report = str(
                (project / "reports" / "REPORT-01-001.md").resolve()
            )
            self.assertEqual(record["expected_report_path"], expected_report)
            self.assertEqual(
                records["handoff-packet"]["expected_report_path"],
                expected_report,
            )
            if str(authored_root) != str(authored_root.resolve()):
                self.assertTrue(resolved_project.startswith("/private/tmp/"))
                self.assertTrue(resolved_task.startswith("/private/tmp/"))
                self.assertTrue(
                    record["work_roots_resolved"][0]["absolute_path"].startswith(
                        "/tmp/"
                    )
                )


if __name__ == "__main__":
    unittest.main()
