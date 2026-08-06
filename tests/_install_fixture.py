"""Shared copy-install fixture for tests that need a populated install root.

Builds the tool-shipped layout through the same production primitives the
coordinated workflow applies (``_replace_tool_path`` staging plus operator-file
seeding), without running the full plan/apply workflow with its progress
records and leases.
"""
from __future__ import annotations

from pathlib import Path

from cli.install_workflow import (
    TOOL_SHIPPED,
    _replace_tool_path,
    _seed_operator_files,
)


def install_copy_fixture(source_root: Path, install_root: Path) -> None:
    install_root.mkdir(parents=True, exist_ok=True)
    for target_rel, source_rel in TOOL_SHIPPED:
        _replace_tool_path(source_root / source_rel, install_root / target_rel)
    _seed_operator_files(source_root, install_root)
