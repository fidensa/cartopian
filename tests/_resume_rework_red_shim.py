"""Red-evidence harness for the resumable-update rework blockers.

``tests/_resume_red_shim.py`` proves the original bounded-progress feature.
This shim proves the *rework*: it restores, one blocker at a time, the
pre-rework behavior that review found defective, then runs the matching class
from ``tests/test_resume_rework_regressions.py`` against it.  Each restoration
must produce failures; the same class against the real implementation must pass.

The restorations are faithful reconstructions of the defective shape named in
the review, not the original bytes — the rework was delivered in place, so no
pre-rework tree survives to run against.  Each one is documented against the
line it replaces.

Not part of the canonical suite; ``unittest discover`` only collects ``test*``.
Run explicitly:

    python3 tests/_resume_rework_red_shim.py
"""
from __future__ import annotations

import sys
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli.install_workflow as workflow  # noqa: E402
import cli.resume_state as resume_state  # noqa: E402


# --- Blocker 1: orphan-lease takeover -------------------------------------
# Replaces resume_state._remove_exact_lease.  The pre-rework form checked the
# lease and then unlinked the *pathname*, so two recoverers inspecting one
# orphan could both succeed and both believe they owned the root.
def _unconditional_remove(install_root: Path, expected: bytes) -> bool:
    path = resume_state.lease_path(install_root)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


# --- Blocker 2: source-mismatch preservation ------------------------------
# The pre-rework apply gate acted on the intrinsic progress-read classification
# only.  Emptying _PRESERVE_BEFORE_REPLACEMENT and blinding the source-identity
# probe reproduces that: a changed source finds nothing to preserve and writes
# straight over its predecessor's recovery evidence.
def _blind_source_identity(_prior: Mapping[str, Any]) -> str:
    return ""


# --- Blocker 3: portable-evidence authority -------------------------------
# The pre-rework classifier blended a persisted envelope's source authority
# with the current run's remaining work instead of failing closed.
def _merging_predecessor(
    run: Mapping[str, Any],
    source: Mapping[str, Any],
    assessment: Optional[Mapping[str, Any]],
) -> "OrderedDict[str, Any]":
    if assessment is None:
        return OrderedDict(
            (
                ("classification", "not-assessed"),
                ("authority", "none"),
                ("reusable", False),
                ("superseded_source_identity", ""),
            )
        )
    compatibility = str(assessment.get("compatibility") or "")
    prior = assessment.get("prior_run")
    prior = prior if isinstance(prior, Mapping) else {}
    current = assessment.get("current_run")
    current = current if isinstance(current, Mapping) else {}
    return OrderedDict(
        (
            ("classification", compatibility or "not-assessed"),
            ("authority", "same-source" if prior else "none"),
            ("reusable", bool(prior)),
            (
                "superseded_source_identity",
                str(prior.get("source_identity") or ""),
            ),
        )
    )


# --- Blocker 4: migration-deferral binding --------------------------------
# The pre-rework planner validated the offer but not the source identity that
# produced it, and reused the envelope on schema compatibility alone.
def _unbound_envelope(
    prior: Mapping[str, Any], _source_identity: str
) -> Optional[Mapping[str, Any]]:
    if prior.get("classification") != "compatible":
        return None
    envelope = prior.get("envelope")
    return envelope if isinstance(envelope, Mapping) else None


def _offer_only_deferrals(
    migrations,
    *,
    prior_deferrals,
    prior_context,
    decision_context,
    explicit_defer,
) -> None:
    for offer in migrations:
        if explicit_defer:
            offer["choice_state"] = "deferred"
            continue
        prior = prior_deferrals.get(str(offer.get("project_identity")))
        if prior is None:
            continue
        if all(
            str(prior.get(field)) == str(offer.get(field))
            for field in (
                "current_schema",
                "target_schema",
                "applicability",
                "supported_workflow",
            )
        ):
            offer["choice_state"] = "deferred"


# --- Blocker 5: stale-plan resume race ------------------------------------
# The pre-remediation apply re-read progress under the lease but recomputed
# only intrinsic schema usability and source binding.  Returning an empty
# assessment (so the plan-time compatibility stands) and disarming the gate
# restores exactly that: the stale plan takes over the orphan lease and writes
# over the open mutation boundary another run had just published.
def _plan_time_assessment_only(
    _plan: Mapping[str, Any],
    _held: Mapping[str, Any],
    **_facts: Any,
) -> "OrderedDict[str, Any]":
    return OrderedDict()


def _ungated(
    _assessment: Mapping[str, Any], _held: Mapping[str, Any], **_gates: Any
) -> None:
    return None


BLOCKERS = (
    (
        "1 atomic orphan-lease takeover",
        "AtomicOrphanTakeoverTests",
        lambda: setattr(
            resume_state, "_remove_exact_lease", _unconditional_remove
        ),
    ),
    (
        "2 source-mismatch preservation",
        "SourceMismatchPreservationTests",
        lambda: (
            setattr(workflow, "_PRESERVE_BEFORE_REPLACEMENT", ()),
            setattr(
                workflow, "_persisted_source_identity", _blind_source_identity
            ),
        ),
    ),
    (
        "3 portable-evidence authority",
        "PortableEvidenceAuthorityTests",
        lambda: setattr(
            resume_state, "_authoritative_predecessor", _merging_predecessor
        ),
    ),
    (
        "4 source-bound migration deferrals",
        "SourceBoundMigrationDeferralTests",
        lambda: (
            setattr(workflow, "_source_bound_envelope", _unbound_envelope),
            setattr(
                workflow, "_apply_migration_deferrals", _offer_only_deferrals
            ),
        ),
    ),
    (
        "5 stale-plan resume race",
        "StalePlanResumeRaceTests",
        lambda: (
            setattr(
                workflow,
                "_post_lease_assessment",
                _plan_time_assessment_only,
            ),
            setattr(workflow, "_gate_post_lease_resume", _ungated),
        ),
    ),
)

_ORIGINALS = {
    (resume_state, "_remove_exact_lease"),
    (resume_state, "_authoritative_predecessor"),
    (workflow, "_PRESERVE_BEFORE_REPLACEMENT"),
    (workflow, "_persisted_source_identity"),
    (workflow, "_source_bound_envelope"),
    (workflow, "_apply_migration_deferrals"),
    (workflow, "_post_lease_assessment"),
    (workflow, "_gate_post_lease_resume"),
}


def main() -> int:
    saved = {(mod, name): getattr(mod, name) for mod, name in _ORIGINALS}
    loader = unittest.TestLoader()
    verdicts = []
    for label, class_name, install in BLOCKERS:
        for (mod, name), value in saved.items():
            setattr(mod, name, value)
        install()
        suite = loader.loadTestsFromName(
            f"tests.test_resume_rework_regressions.{class_name}"
        )
        print(f"\n=== blocker {label}: pre-rework behavior restored ===")
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        broken = len(result.failures) + len(result.errors)
        verdicts.append((label, result.testsRun, broken))
    for (mod, name), value in saved.items():
        setattr(mod, name, value)
    print("\nrework red-shim summary")
    for label, run, broken in verdicts:
        state = "RED (expected)" if broken else "NOT RED — no evidence"
        print(f"  blocker {label}: run={run} failed={broken} -> {state}")
    return 0 if all(broken for _, _, broken in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
