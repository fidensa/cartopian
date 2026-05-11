"""FR-013 protocol-version sequence test (TASK-01-012, P01-BUILD-012).

Asserts the scaffold-project → generate-config sequence produces a
project ``cartopian.toml`` whose ``[project] protocol_version`` matches
the current protocol version, read dynamically rather than hard-coded.

The current version is sourced from ``protocol/CHANGELOG.md`` — the
single repo-local source of truth that the implementation reads in
``cli/commands/generate_config.py``. Neither the workspace
``cartopian.toml`` nor ``templates/global.cartopian.toml`` carries a
``[project] protocol_version`` field, so they cannot drive this
assertion; CHANGELOG.md is the authoritative dynamic anchor.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"
CHANGELOG = REPO_ROOT / "protocol" / "CHANGELOG.md"


def _current_protocol_version() -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    _, _, body = text.partition("\n## Entries\n")
    match = re.search(r"^###\s+(v\d+\.\d+\.\d+)\b", body, flags=re.MULTILINE)
    assert match is not None, f"no protocol version entry under ## Entries in {CHANGELOG}"
    return match.group(1)


def _run(*cli_args, home):
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *cli_args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


class TestScaffoldThenGenerateConfigCarriesProtocolVersion(unittest.TestCase):
    def test_protocol_version_lands_in_generated_cartopian_toml(self):
        expected_version = _current_protocol_version()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project = tmp_path / "proj"

            scaffold = _run("scaffold-project", str(project), home=tmp_path)
            self.assertEqual(scaffold.returncode, 0, msg=scaffold.stderr)
            scaffold_record = json.loads(scaffold.stdout.strip())
            self.assertEqual(scaffold_record["action"], "scaffold-project")
            self.assertEqual(scaffold_record["details"]["outcome"], "scaffolded")
            self.assertTrue(project.is_dir())

            generate = _run(
                "generate-config", str(project),
                "--name", "Demo",
                "--id", "demo",
                home=tmp_path,
            )
            self.assertEqual(generate.returncode, 0, msg=generate.stderr)
            generate_record = json.loads(generate.stdout.strip())
            self.assertEqual(generate_record["action"], "generate-config")
            self.assertEqual(
                generate_record["details"]["protocol_version"],
                expected_version,
            )

            config_path = project / "cartopian.toml"
            self.assertTrue(config_path.is_file())
            with config_path.open("rb") as fh:
                data = tomllib.load(fh)
            self.assertEqual(
                data["project"]["protocol_version"],
                expected_version,
            )
            self.assertEqual(data["project"]["name"], "Demo")
            self.assertEqual(data["project"]["id"], "demo")


if __name__ == "__main__":
    unittest.main()
