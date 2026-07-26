"""Execute the canonical test suites declared by ``config-surfaces.json``."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from cli.config_surface_parity import (
    canonical_suite_manifest_diagnostics,
    canonical_suite_observation,
    load_registry,
)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config-surfaces.json"


def _command(tokens: Sequence[str]) -> list[str]:
    return [sys.executable if token == "{python}" else token for token in tokens]


def _run(tokens: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(tokens),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _unittest_counts(output: str) -> dict[str, int]:
    total_match = re.search(r"\bRan (\d+) tests?\b", output)
    total = int(total_match.group(1)) if total_match else 0
    skipped_match = re.search(r"\bskipped=(\d+)\b", output)
    failures_match = re.search(r"\bfailures=(\d+)\b", output)
    errors_match = re.search(r"\berrors=(\d+)\b", output)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    failed = (
        (int(failures_match.group(1)) if failures_match else 0)
        + (int(errors_match.group(1)) if errors_match else 0)
    )
    return {
        "collected": total,
        "passed": max(total - skipped - failed, 0),
        "failed": failed,
        "skipped": skipped,
    }


def _unittest_collection(
    output: str,
    required_paths: Sequence[str],
) -> dict[str, int]:
    """Count verbose unittest identities for each required module path."""
    collected_by_path: dict[str, int] = {}
    for path in required_paths:
        module = path.removesuffix(".py").replace("/", ".")
        identity = re.compile(rf"\({re.escape(module)}(?:\.|\))")
        collected_by_path[path] = sum(
            1 for line in output.splitlines() if identity.search(line)
        )
    return collected_by_path


def _pytest_collection(output: str) -> tuple[int, dict[str, int]]:
    node_ids = [
        line.strip()
        for line in output.splitlines()
        if "::" in line and not line.lstrip().startswith("<")
    ]
    collected_by_path: dict[str, int] = {}
    for node_id in node_ids:
        path = node_id.split("::", 1)[0]
        collected_by_path[path] = collected_by_path.get(path, 0) + 1
    total_match = re.search(r"\b(\d+) tests? collected\b", output)
    total = int(total_match.group(1)) if total_match else len(node_ids)
    return total, collected_by_path


def _pytest_execution_counts(output: str, collected: int) -> dict[str, int]:
    def count(label: str) -> int:
        match = re.search(rf"\b(\d+) {label}\b", output)
        return int(match.group(1)) if match else 0

    passed = count("passed")
    failed = count("failed") + count("errors?")
    skipped = count("skipped")
    if passed == failed == skipped == 0 and collected:
        failed = collected
    return {
        "collected": collected,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


def evaluate_suite(suite: Mapping[str, Any]) -> dict[str, Any]:
    """Run one declared suite and return a fail-closed machine observation."""
    collection = _run(suite["collection_command"])
    if suite["collection_executes"]:
        execution = collection
    else:
        execution = _run(suite["execution_command"])
    collection_output = collection.stdout + "\n" + collection.stderr
    execution_output = execution.stdout + "\n" + execution.stderr

    if suite["runner"] == "unittest":
        counts = _unittest_counts(execution_output)
        collected_by_path = _unittest_collection(
            execution_output,
            [str(item["path"]) for item in suite["required_tests"]],
        )
    else:
        collected, collected_by_path = _pytest_collection(collection_output)
        counts = _pytest_execution_counts(execution_output, collected)

    verdict = canonical_suite_observation(
        suite,
        collection_exit_code=collection.returncode,
        execution_exit_code=execution.returncode,
        collected_total=counts["collected"],
        collected_by_path=collected_by_path,
    )
    return {
        "id": suite["id"],
        "runner": suite["runner"],
        "collection_command": _command(suite["collection_command"]),
        "execution_command": _command(suite["execution_command"]),
        "collection_exit_code": collection.returncode,
        "execution_exit_code": execution.returncode,
        "counts": counts,
        "required_tests_collected": {
            item["path"]: collected_by_path.get(item["path"], 0)
            for item in suite["required_tests"]
        },
        **verdict,
    }


def run() -> dict[str, Any]:
    registry = load_registry(REGISTRY)
    diagnostics = canonical_suite_manifest_diagnostics(ROOT, registry)
    if diagnostics:
        return {
            "green": False,
            "manifest_diagnostics": [
                diagnostic.as_record() for diagnostic in diagnostics
            ],
            "suites": [],
        }
    suites = [evaluate_suite(suite) for suite in registry["canonical_test_suites"]]
    return {
        "green": all(suite["green"] for suite in suites),
        "manifest_diagnostics": [],
        "suites": suites,
    }


def main() -> int:
    result = run()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["green"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
