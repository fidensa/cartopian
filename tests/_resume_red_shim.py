"""Red-evidence harness: run the resume suite with the new boundary removed.

This restores the pre-implementation behavior in place — apply mutates and
writes only the visible state mirror, nothing persists a progress envelope, no
lease is claimed, and no uncertain boundary is refused — then runs
``tests/test_resumable_update_evidence.py`` against it.  Every assertion that
depends on bounded resumable progress must fail here and pass against the real
implementation.

Not part of the canonical suite; ``unittest discover`` only collects ``test*``.
Run explicitly:

    python3 tests/_resume_red_shim.py
"""
from __future__ import annotations

import sys
import unittest
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli.install_workflow as workflow  # noqa: E402
import cli.resume_state as resume_state  # noqa: E402


def _absent_progress(_install_root):
    return OrderedDict(
        (
            ("classification", "absent"),
            ("detail", "progress persistence is disabled in this shim"),
            ("envelope", None),
            ("lease", None),
            ("lease_state", "absent"),
        )
    )


def _empty_assessment(*_args, **_kwargs):
    return OrderedDict(
        (
            ("assessment_schema", resume_state.RESUME_ASSESSMENT_SCHEMA),
            ("compatibility", "absent"),
            ("compatibility_detail", "resume assessment is disabled"),
            ("prior_run", None),
            ("current_run", OrderedDict()),
            ("reusable", []),
            ("uncertain", []),
            ("incompatible", []),
            ("preserved_choices", []),
            ("preserved_migrations", []),
            ("surface_diagnosis", []),
            ("remaining_plan", []),
            ("restart", OrderedDict()),
            ("recovery", resume_state.recovery_note("absent", "disabled")),
            ("byte_stable_noop", False),
        )
    )


def _no_envelope(*_args, **_kwargs):
    return OrderedDict()


def _no_lease(*_args, **_kwargs):
    return OrderedDict((("owner", ""), ("state", "held"), ("takeover", "")))


def _install_shim() -> None:
    workflow.read_progress = _absent_progress
    workflow.assess_resume = _empty_assessment
    workflow.begin_progress = _no_envelope
    workflow.open_boundary = _no_envelope
    workflow.commit_checkpoint = _no_envelope
    workflow.advance_completion = _no_envelope
    workflow.advance_cleanup = _no_envelope
    workflow.record_failure = _no_envelope
    workflow.acquire_lease = _no_lease
    workflow.release_lease = lambda *_args, **_kwargs: None
    workflow.quarantine_progress = lambda *_a, **_k: resume_state.recovery_note(
        "absent", "disabled"
    )
    workflow.preserve_progress = lambda *_a, **_k: resume_state.recovery_note(
        "absent", "disabled"
    )
    workflow.carry_preserved_evidence = lambda _root, note, **_k: note
    workflow._uncertain_boundaries = lambda _plan: []


def main() -> int:
    _install_shim()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_resumable_update_evidence")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(
        f"red-shim summary: run={result.testsRun} "
        f"failures={len(result.failures)} errors={len(result.errors)}"
    )
    return 0 if (result.failures or result.errors) else 1


if __name__ == "__main__":
    sys.exit(main())
