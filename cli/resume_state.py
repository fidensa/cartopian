"""Bounded progress persistence and deterministic resume assessment.

``cli.install_state`` owns the install/update state contract.  This module owns
the *persistence boundary* around it: a closed, versioned progress envelope
bound to one source and one run context, recoverable writes, an explicit
pre-mutation boundary marker, lease-based duplicate-invocation safety,
deterministic recovery classification, resume assessment, and a portable
evidence rendering kept separate from internal recovery metadata.

Bounded sequence evidence replaces wall-clock timestamps throughout.  Two
equivalent runs over equivalent observations must serialize to identical bytes,
which a clock cannot do; the monotonic per-run sequence is the "equivalent
evidence" the contract allows in place of a timestamp.

The module never mutates an installation, never executes a recovery action, and
never derives a filesystem destination or an executable from persisted content.
Every path it touches is a fixed closed name under one caller-validated install
root.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import stat
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cli.install_state import (
    PORTABLE_EVIDENCE_FIELDS,
    RECORD_SCHEMA_VERSION,
    SCHEMA_IDENTITY,
    SURFACE_KINDS,
    supported_record_schema_version,
    validate_portable_evidence,
)
from cli.provenance import PM_IDENTIFIER_RE

PROGRESS_SCHEMA_IDENTITY = "cartopian-install-update-progress-v1"
PROGRESS_SCHEMA_VERSION = 1
RESUME_ASSESSMENT_SCHEMA = "cartopian-install-update-resume-v1"
PORTABLE_EVIDENCE_SCHEMA = "cartopian-portable-install-evidence-v1"

PROGRESS_FILE = "install-update-progress.json"
QUARANTINE_FILE = "install-update-progress.quarantine.json"
LEASE_FILE = "install-update-progress.lease"

# The envelope is recovery state, not an archive.  Anything larger than this is
# treated as corrupted rather than parsed.
MAX_PROGRESS_BYTES = 512 * 1024
MAX_LEASE_BYTES = 4 * 1024

ENVELOPE_FIELDS: Tuple[str, ...] = (
    "progress_schema_identity",
    "progress_schema_version",
    "status",
    "sequence",
    "run",
    "boundary",
    "surface_profiles",
    "terminal",
    "retention",
    "recovery",
    "progress",
)

# Exactly one progress record exists per install root, so supersession happens
# in place and needs no status of its own; every member below has a producer.
PROGRESS_STATUSES: Tuple[str, ...] = (
    "active",
    "terminal",
    "quarantined",
)
RETENTION_CLASSES: Tuple[str, ...] = (
    "active",
    "terminal",
    "quarantined",
)
MARKER_STATES: Tuple[str, ...] = ("pending", "advanced")
COMPATIBILITY_STATES: Tuple[str, ...] = (
    "compatible",
    "absent",
    "stale",
    "source-mismatch",
    "run-conflict",
    "lease-conflict",
    "orphaned",
    "evidence-missing",
    "corrupted",
    "unsupported-newer",
)
# Mixed-state diagnosis vocabulary, per surface.
SURFACE_DIAGNOSES: Tuple[str, ...] = (
    "current",
    "stale",
    "missing",
    "declined",
    "pending",
    "unverified",
    "blocked",
    "unsupported",
)
RESUME_DISPOSITIONS: Tuple[str, ...] = (
    "reuse-verified",
    "preserve-choice",
    "replan",
    "inspect-before-retry",
    "refuse-replay",
)
RECOVERY_ACTIONS: Tuple[str, ...] = (
    "resume-remaining-work",
    "replan-from-current-observations",
    "inspect-uncertain-boundary",
    "discard-quarantined-progress",
    "migrate-progress-with-newer-tool",
    "release-orphaned-lease",
    "await-active-run",
)
OBSERVATION_CAPABILITIES: Tuple[str, ...] = (
    "observable",
    "partially-observable",
    "unobservable",
)

_RECOVERY_BY_CLASSIFICATION: "OrderedDict[str, Tuple[str, ...]]" = OrderedDict(
    (
        ("compatible", ("resume-remaining-work",)),
        ("absent", ("replan-from-current-observations",)),
        (
            "stale",
            ("replan-from-current-observations", "resume-remaining-work"),
        ),
        ("source-mismatch", ("replan-from-current-observations",)),
        (
            "run-conflict",
            (
                "inspect-uncertain-boundary",
                "replan-from-current-observations",
            ),
        ),
        ("lease-conflict", ("await-active-run", "release-orphaned-lease")),
        (
            "orphaned",
            (
                "discard-quarantined-progress",
                "replan-from-current-observations",
            ),
        ),
        (
            "evidence-missing",
            (
                "inspect-uncertain-boundary",
                "replan-from-current-observations",
            ),
        ),
        (
            "corrupted",
            (
                "discard-quarantined-progress",
                "replan-from-current-observations",
            ),
        ),
        ("unsupported-newer", ("migrate-progress-with-newer-tool",)),
    )
)

_REUSABLE_CLASSIFICATIONS = frozenset(("compatible", "stale"))

_DIAGNOSIS_BY_SURFACE_STATE: "OrderedDict[str, str]" = OrderedDict(
    (
        ("current", "current"),
        ("verified", "current"),
        ("not-applicable", "current"),
        ("stale", "stale"),
        ("dirty", "stale"),
        ("missing", "missing"),
        ("declined", "declined"),
        ("deferred", "pending"),
        ("offered", "pending"),
        ("pending", "pending"),
        ("unverified", "unverified"),
        ("unknown", "unverified"),
        ("blocked", "blocked"),
        ("failed", "blocked"),
        ("malformed", "unsupported"),
        ("unsupported-newer", "unsupported"),
        ("contradictory", "unsupported"),
    )
)

_SAFE_SURFACE_STATES = frozenset(("current", "verified", "not-applicable"))
_RETRY_RANK = {
    "idempotent": 0,
    "inspect-before-retry": 1,
    "refuse-replay": 2,
}

# Project-management identifiers must never reach portable evidence.  The
# closed field allowlist in ``cli.install_state`` covers structure; this covers
# identifier text smuggled inside an otherwise-allowed scalar.  The grammar is
# owned by ``cli.provenance`` so this boundary and the product-code scan can
# never disagree about what counts as an identifier.
_GOVERNANCE_TEXT = PM_IDENTIFIER_RE
_PRIVATE_TEXT = re.compile(
    r"(?i)\b(?:api[_-]?key|secret|password|passwd|bearer|private[_-]?key|"
    r"authorization|credential)\b"
)

class ProgressRefusal(ValueError):
    """A bounded, fail-closed persistence or resume refusal."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# Closed paths and recoverable writes
# ---------------------------------------------------------------------------


def progress_path(install_root: Path) -> Path:
    """Return the fixed progress-record path under one install root."""
    return Path(install_root) / PROGRESS_FILE


def quarantine_path(install_root: Path) -> Path:
    return Path(install_root) / QUARANTINE_FILE


def lease_path(install_root: Path) -> Path:
    return Path(install_root) / LEASE_FILE


def recoverable_write_text(path: Path, content: str) -> None:
    """Write ``content`` so a crash cannot expose a partial file.

    The payload is staged in the destination directory, flushed, fsynced, and
    then renamed over the target.  ``os.replace`` is atomic within a directory
    on POSIX and on Windows for an existing destination, so a reader observes
    either the previous complete file or the new complete file.

    Residual limitation: the parent-directory fsync that makes the rename
    itself durable across a power loss is unavailable on Windows and on some
    network filesystems.  It is attempted and skipped where unsupported, so on
    those platforms an abrupt power loss can lose the *last* write while still
    never exposing a truncated one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp = Path(raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and not path.is_symlink():
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    try:
        directory = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory)
    except OSError:
        pass
    finally:
        os.close(directory)


def serialize_envelope(envelope: Mapping[str, Any]) -> str:
    """Return the deterministic on-disk form of one progress envelope."""
    return (
        json.dumps(
            envelope_projection(envelope), indent=2, ensure_ascii=False
        )
        + "\n"
    )


def envelope_projection(envelope: Mapping[str, Any]) -> "OrderedDict[str, Any]":
    """Return only closed envelope fields, in contract order."""
    return OrderedDict(
        (field, copy.deepcopy(envelope.get(field)))
        for field in ENVELOPE_FIELDS
    )


def _digest_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _digest_text(content: str) -> str:
    return _digest_bytes(content.encode("utf-8"))


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def build_envelope(
    *,
    record: Mapping[str, Any],
    surface_profiles: Sequence[Mapping[str, Any]],
    projection: Mapping[str, Any],
    sequence: int = 1,
    status: str = "active",
    boundary: Optional[Mapping[str, Any]] = None,
    recovery: Optional[Mapping[str, Any]] = None,
) -> "OrderedDict[str, Any]":
    """Build one closed progress envelope bound to a single source and run."""
    if status not in PROGRESS_STATUSES:
        raise ProgressRefusal(
            "invalid-progress-status", f"unknown progress status: {status!r}"
        )
    run = record.get("run")
    if not isinstance(run, Mapping):
        raise ProgressRefusal(
            "progress-run-missing", "progress requires a bound run context"
        )
    source = run.get("source")
    if not isinstance(source, Mapping) or not source.get("value"):
        raise ProgressRefusal(
            "progress-source-missing",
            "progress requires a resolved source identity",
        )
    marker = str(run.get("marker") or "")
    if not marker:
        raise ProgressRefusal(
            "progress-run-missing", "progress requires a run marker"
        )
    return OrderedDict(
        (
            ("progress_schema_identity", PROGRESS_SCHEMA_IDENTITY),
            ("progress_schema_version", PROGRESS_SCHEMA_VERSION),
            ("status", status),
            ("sequence", int(sequence)),
            (
                "run",
                OrderedDict(
                    (
                        ("operation", str(run.get("operation"))),
                        ("marker", marker),
                        (
                            "source",
                            OrderedDict(
                                (
                                    ("kind", str(source.get("kind"))),
                                    ("value", str(source.get("value"))),
                                    (
                                        "authority",
                                        str(source.get("authority")),
                                    ),
                                )
                            ),
                        ),
                    )
                ),
            ),
            (
                "boundary",
                copy.deepcopy(dict(boundary)) if boundary else None,
            ),
            (
                "surface_profiles",
                [
                    copy.deepcopy(dict(item))
                    for item in surface_profiles
                ],
            ),
            (
                "terminal",
                OrderedDict(
                    (
                        ("schema", "pending"),
                        ("completion", "pending"),
                        ("cleanup", "pending"),
                    )
                ),
            ),
            (
                "retention",
                OrderedDict(
                    (
                        ("class", "active"),
                        (
                            "reason",
                            "run in progress; evidence retained for recovery",
                        ),
                        ("evidence_retained", True),
                    )
                ),
            ),
            (
                "recovery",
                copy.deepcopy(dict(recovery))
                if recovery
                else _recovery_note("compatible", "run started from a clean boundary"),
            ),
            ("progress", copy.deepcopy(dict(projection))),
        )
    )


def _recovery_note(
    classification: str,
    detail: str,
    *,
    quarantine: str = "",
    quarantined_identity: str = "",
    preserved_classification: str = "",
) -> "OrderedDict[str, Any]":
    if classification not in COMPATIBILITY_STATES:
        raise ProgressRefusal(
            "invalid-recovery-classification",
            f"unknown recovery classification: {classification!r}",
        )
    if (
        preserved_classification
        and preserved_classification not in COMPATIBILITY_STATES
    ):
        raise ProgressRefusal(
            "invalid-recovery-classification",
            "unknown preserved classification: "
            f"{preserved_classification!r}",
        )
    return OrderedDict(
        (
            ("classification", classification),
            (
                "actions",
                list(_RECOVERY_BY_CLASSIFICATION.get(classification, ())),
            ),
            ("detail", detail),
            ("quarantine", quarantine),
            ("quarantined_identity", quarantined_identity),
            # ``classification`` describes this run; this describes the record
            # this run had to set aside before it could start.  They are
            # different facts and the second one has to survive, or the outcome
            # stops being explainable once the run recovers.
            ("preserved_classification", preserved_classification),
        )
    )


def recovery_note(classification: str, detail: str) -> "OrderedDict[str, Any]":
    """Public deterministic recovery note for one classification."""
    return _recovery_note(classification, detail)


def _carry_recovery(
    envelope: Mapping[str, Any],
    classification: str,
    detail: str,
    *,
    drop_quarantine_file: bool = False,
) -> "OrderedDict[str, Any]":
    """Rewrite the recovery note while retaining quarantine provenance.

    A run that recovered from a quarantined predecessor keeps that fact for the
    life of the envelope: the quarantine file may be superseded once terminal
    proof exists, but its content identity is what explains the outcome later.
    """
    prior = envelope.get("recovery")
    prior = prior if isinstance(prior, Mapping) else {}
    return _recovery_note(
        classification,
        detail,
        quarantine=(
            "" if drop_quarantine_file else str(prior.get("quarantine", ""))
        ),
        quarantined_identity=str(prior.get("quarantined_identity", "")),
        preserved_classification=str(
            prior.get("preserved_classification", "")
        ),
    )


# ---------------------------------------------------------------------------
# Reading, classification, quarantine
# ---------------------------------------------------------------------------


def _validate_envelope_shape(raw: Mapping[str, Any]) -> Optional[str]:
    """Return a corruption reason, or ``None`` when the shape is usable."""
    if raw.get("progress_schema_identity") != PROGRESS_SCHEMA_IDENTITY:
        return "progress schema identity is missing or unrecognized"
    version = raw.get("progress_schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        return "progress schema version is missing or not an integer"
    if raw.get("status") not in PROGRESS_STATUSES:
        return "progress status is outside the closed vocabulary"
    if not isinstance(raw.get("sequence"), int) or isinstance(
        raw.get("sequence"), bool
    ):
        return "progress sequence evidence is missing or not an integer"
    run = raw.get("run")
    if not isinstance(run, Mapping) or not run.get("marker"):
        return "progress record has no bound run marker"
    source = run.get("source")
    if not isinstance(source, Mapping) or not source.get("value"):
        return "progress record has no bound source identity"
    progress = raw.get("progress")
    if not isinstance(progress, Mapping):
        return "progress record carries no state projection"
    if progress.get("schema_identity") != SCHEMA_IDENTITY:
        return "state projection schema identity is missing or unrecognized"
    if not isinstance(progress.get("checkpoints"), list):
        return "state projection has no checkpoint accounting"
    if not isinstance(progress.get("surfaces"), list):
        return "state projection has no surface accounting"
    terminal = raw.get("terminal")
    if not isinstance(terminal, Mapping) or any(
        terminal.get(name) not in MARKER_STATES
        for name in ("schema", "completion", "cleanup")
    ):
        return "terminal marker accounting is missing or unsupported"
    return None


def _missing_checkpoint_evidence(progress: Mapping[str, Any]) -> List[str]:
    missing: List[str] = []
    for item in progress.get("checkpoints", []):
        if not isinstance(item, Mapping):
            continue
        if item.get("status") != "completed":
            continue
        evidence = item.get("evidence")
        if (
            not isinstance(evidence, Mapping)
            or not evidence
            or not evidence.get("identity")
            or evidence.get("verification") != "verified"
        ):
            missing.append(str(item.get("id", "")))
    return sorted(missing)


def _lease_bytes(install_root: Path) -> Optional[bytes]:
    """Return the exact bytes of the lease object, or ``None`` when absent.

    Ownership decisions compare *these bytes*, not the pathname.  Every lease
    payload carries a fresh per-invocation owner token, so byte equality is
    equivalent to "this is still the object I inspected".
    """
    path = lease_path(install_root)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > MAX_LEASE_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def read_lease(install_root: Path) -> Optional[Dict[str, Any]]:
    """Read the bounded lease fact, or ``None`` when no lease is present."""
    path = lease_path(install_root)
    try:
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size > MAX_LEASE_BYTES
        ):
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        # An unreadable lease still means "someone claimed this root"; it fails
        # closed as an unidentified holder rather than being ignored.
        return {"owner": "", "run": "", "pid": None, "node": ""}
    if not isinstance(raw, Mapping):
        return {"owner": "", "run": "", "pid": None, "node": ""}
    return {
        "owner": str(raw.get("owner", "")),
        "run": str(raw.get("run", "")),
        "pid": raw.get("pid") if isinstance(raw.get("pid"), int) else None,
        "node": str(raw.get("node", "")),
    }


def lease_is_orphaned(lease: Mapping[str, Any]) -> bool:
    """True only when the holder is *provably* gone on this host.

    Residual limitation: a reliable liveness probe exists on POSIX only.  On
    Windows an abandoned lease is reported as a conflict requiring explicit
    operator recovery rather than being silently taken over.
    """
    pid = lease.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    if str(lease.get("node", "")) != platform.node():
        return False
    if os.name != "posix":
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        # Alive but owned by another user, or otherwise unprobeable: not
        # provably gone, so the lease stands.
        return False
    return False


def read_progress(install_root: Path) -> "OrderedDict[str, Any]":
    """Read and classify persisted progress without mutating anything.

    Returns ``{classification, detail, envelope, lease, lease_state}``.  The
    classification here covers only facts intrinsic to the stored record;
    comparison against current observations happens in :func:`assess_resume`.
    """
    path = progress_path(install_root)
    lease = read_lease(install_root)
    lease_state = "absent"
    if lease is not None:
        lease_state = "orphaned" if lease_is_orphaned(lease) else "held"

    def result(
        classification: str,
        detail: str,
        envelope: Optional[Mapping[str, Any]] = None,
    ) -> "OrderedDict[str, Any]":
        return OrderedDict(
            (
                ("classification", classification),
                ("detail", detail),
                (
                    "envelope",
                    copy.deepcopy(dict(envelope))
                    if envelope is not None
                    else None,
                ),
                ("lease", copy.deepcopy(lease) if lease else None),
                ("lease_state", lease_state),
            )
        )

    try:
        if path.is_symlink():
            return result(
                "corrupted", "progress record is a symlink and was not followed"
            )
        if not path.is_file():
            return result("absent", "no prior progress record is present")
        if path.stat().st_size > MAX_PROGRESS_BYTES:
            return result(
                "corrupted",
                "progress record exceeds the bounded recovery size",
            )
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return result(
            "corrupted", f"progress record is unreadable: {type(exc).__name__}"
        )
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return result(
            "corrupted",
            "progress record is truncated or malformed JSON",
        )
    if not isinstance(raw, Mapping):
        return result("corrupted", "progress record is not an object")
    version = raw.get("progress_schema_version")
    if (
        raw.get("progress_schema_identity") == PROGRESS_SCHEMA_IDENTITY
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version > PROGRESS_SCHEMA_VERSION
    ):
        return result(
            "unsupported-newer",
            (
                f"progress record schema version {version} is newer than the "
                f"supported version {PROGRESS_SCHEMA_VERSION}"
            ),
            raw,
        )
    reason = _validate_envelope_shape(raw)
    if reason is not None:
        return result("corrupted", reason)
    projection = raw.get("progress")
    if not isinstance(projection, Mapping):  # pragma: no cover - shape checked
        return result("corrupted", "state projection is missing")
    if not supported_record_schema_version(projection.get("record_schema_version")):
        return result(
            "unsupported-newer",
            (
                "state projection record schema version "
                f"{projection.get('record_schema_version')!r} is not the "
                f"supported version {RECORD_SCHEMA_VERSION}"
            ),
            raw,
        )
    missing = _missing_checkpoint_evidence(projection)
    if missing:
        return result(
            "evidence-missing",
            "completed checkpoints without verified evidence: "
            + ", ".join(missing),
            raw,
        )
    if raw.get("status") == "quarantined":
        return result(
            "corrupted",
            "progress record is already marked quarantined",
            raw,
        )
    return result("compatible", "progress record is schema-compatible", raw)


def quarantine_progress(
    install_root: Path, *, classification: str, detail: str
) -> "OrderedDict[str, Any]":
    """Preserve an unusable progress record and return its recovery note.

    The first quarantined record is retained verbatim; a later unusable record
    is reduced to its content identity so quarantine cannot grow without bound
    and cannot overwrite the earliest recovery evidence.
    """
    source = progress_path(install_root)
    target = quarantine_path(install_root)
    identity = ""
    try:
        if source.is_file() and not source.is_symlink():
            identity = _digest_text(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        identity = "unreadable"
    retained = ""
    try:
        if source.is_file() or source.is_symlink():
            if target.exists():
                # The earliest failure is the useful one; do not erase it.
                source.unlink()
                retained = QUARANTINE_FILE
            else:
                os.replace(source, target)
                retained = QUARANTINE_FILE
                _mark_quarantined(target)
    except OSError as exc:
        raise ProgressRefusal(
            "quarantine-unavailable",
            f"unable to quarantine unusable progress: {exc.__class__.__name__}",
        ) from exc
    return _recovery_note(
        classification,
        detail,
        quarantine=retained,
        quarantined_identity=identity,
        preserved_classification=classification,
    )


def preserve_progress(
    install_root: Path, *, classification: str, detail: str
) -> "OrderedDict[str, Any]":
    """Preserve an intact-but-incompatible record before anything replaces it.

    :func:`quarantine_progress` handles records that are unusable *in
    themselves* — truncated, malformed, missing evidence — where the earliest
    failure is the useful one and a later duplicate may be dropped.  This
    handles the opposite case: a record that reads perfectly and is the only
    remaining recovery evidence for a different source, a different run, or an
    installation that is no longer present.  A new envelope may not replace one
    of those until it has been preserved.

    Three rules follow from that:

    * The record is retained byte-for-byte and is never relabelled.  Its content
      identity *is* the evidence, so rewriting it would destroy what it proves.
    * A preservation slot already holding a different record may roll forward
      only when the current progress envelope commits to that exact record's
      content identity in its recovery note.  Replacing the slot with the
      current envelope then preserves a bounded, hash-linked lineage instead
      of deadlocking every later source update.
    * A different occupant that is not committed by the current envelope is a
      refusal.  An unrelated or tampered record is never overwritten.
    """
    source = progress_path(install_root)
    target = quarantine_path(install_root)
    try:
        if source.is_symlink() or not source.is_file():
            return _recovery_note(classification, detail)
        content = source.read_bytes()
    except OSError as exc:
        raise ProgressRefusal(
            "preserve-unavailable",
            "unable to read the progress record that must be preserved: "
            f"{exc.__class__.__name__}",
        ) from exc
    identity = _digest_bytes(content)
    try:
        if target.exists() or target.is_symlink():
            retained = (
                target.read_bytes()
                if target.is_file() and not target.is_symlink()
                else b""
            )
            retained_identity = _digest_bytes(retained)
            if retained_identity == identity:
                # The identical record is already preserved; drop the duplicate.
                source.unlink()
            else:
                try:
                    current = json.loads(content.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    current = None
                recovery = (
                    current.get("recovery")
                    if isinstance(current, Mapping)
                    else None
                )
                carries_retained_identity = (
                    isinstance(recovery, Mapping)
                    and recovery.get("quarantine") == QUARANTINE_FILE
                    and recovery.get("quarantined_identity")
                    == retained_identity
                )
                if not carries_retained_identity:
                    raise ProgressRefusal(
                        "preserved-evidence-occupied",
                        (
                            "earlier recovery evidence is already preserved at "
                            f"{QUARANTINE_FILE}, but the current progress record "
                            "does not commit to its content identity; inspect "
                            "the preserved record before retrying"
                        ),
                    )
                # The current envelope contains the exact digest of the prior
                # occupant.  Preserve it as the new head of a bounded,
                # hash-linked lineage; os.replace keeps the rollover atomic.
                os.replace(source, target)
        else:
            os.replace(source, target)
    except OSError as exc:
        raise ProgressRefusal(
            "preserve-unavailable",
            "unable to preserve incompatible progress: "
            f"{exc.__class__.__name__}",
        ) from exc
    return _recovery_note(
        classification,
        detail,
        quarantine=QUARANTINE_FILE,
        quarantined_identity=identity,
        preserved_classification=classification,
    )


def carry_preserved_evidence(
    install_root: Path,
    recovery: Optional[Mapping[str, Any]],
    *,
    prior_recovery: Optional[Mapping[str, Any]] = None,
) -> Optional["OrderedDict[str, Any]"]:
    """Carry preserved-evidence provenance across a run boundary.

    Preserved evidence outlives the run that set it aside: it is superseded only
    by terminal proof, and the run that reaches that proof is often a later one.
    Without this, a preserved record would sit on disk with nothing on record
    explaining it, and its content identity would already be gone by the time
    cleanup superseded it — leaving an outcome that cannot be explained.

    ``recovery`` is whatever note this run has already produced, if any; a note
    that already names a preserved record is returned untouched, because it is
    describing the record it just set aside.
    """
    if isinstance(recovery, Mapping) and recovery.get("quarantine"):
        return OrderedDict(recovery)
    target = quarantine_path(install_root)
    try:
        if target.is_symlink() or not target.is_file():
            return OrderedDict(recovery) if recovery is not None else None
        identity = _digest_bytes(target.read_bytes())
    except OSError:
        return OrderedDict(recovery) if recovery is not None else None
    prior_recovery = (
        prior_recovery if isinstance(prior_recovery, Mapping) else {}
    )
    base = recovery if isinstance(recovery, Mapping) else {}
    return _recovery_note(
        str(base.get("classification") or "compatible"),
        str(base.get("detail") or "")
        or "earlier preserved recovery evidence is retained for this root",
        quarantine=QUARANTINE_FILE,
        quarantined_identity=identity,
        preserved_classification=str(
            prior_recovery.get("preserved_classification", "")
        ),
    )


def _mark_quarantined(target: Path) -> None:
    """Label a quarantined record when — and only when — it is parseable.

    A record that failed for a reason other than corruption (a completed
    checkpoint with no verified evidence, say) is still valid JSON, so it can
    carry an explicit quarantine status for whoever inspects it later.  A
    truncated or malformed record is left byte-for-byte as found: it cannot be
    rewritten without destroying the evidence that explains the failure.
    """
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(raw, Mapping) or raw.get(
        "progress_schema_identity"
    ) != PROGRESS_SCHEMA_IDENTITY:
        return
    labelled = OrderedDict(raw)
    labelled["status"] = "quarantined"
    retention = labelled.get("retention")
    labelled["retention"] = OrderedDict(
        (
            ("class", "quarantined"),
            (
                "reason",
                str(
                    retention.get("reason", "")
                    if isinstance(retention, Mapping)
                    else ""
                )
                or "record was unusable for resume",
            ),
            ("evidence_retained", True),
        )
    )
    try:
        recoverable_write_text(
            target, json.dumps(labelled, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError:  # pragma: no cover - best effort labelling
        pass


# ---------------------------------------------------------------------------
# Lease: duplicate and concurrent invocation safety
# ---------------------------------------------------------------------------


def new_owner_token() -> str:
    """Return a bounded, non-identifying owner token for one invocation."""
    return f"owner-{uuid.uuid4().hex[:16]}"


def _remove_exact_lease(install_root: Path, expected: bytes) -> bool:
    """Remove the lease only if it is still byte-identical to ``expected``.

    A check followed by an unconditional ``unlink`` is not a removal of the
    object that was checked — it is a removal of whatever occupies the pathname
    at the moment the syscall runs.  Two recoverers inspecting one orphan could
    therefore both "succeed": the second removes the first's freshly created
    lease and claims the root, leaving two live owners.

    This is the compare-and-remove that closes it, built from the one atomic
    primitive the standard library gives on every supported platform:

    1. ``os.rename`` moves the object out of the pathname.  It is atomic within
       a directory, so of two concurrent recoverers exactly one succeeds and the
       loser sees ``FileNotFoundError`` — the winner is decided by the kernel,
       not by an interleaving.
    2. The moved object is then read and compared against the inspected bytes.
       Only a match proves the winner removed the orphan it inspected rather
       than someone else's live lease.
    3. On a mismatch the object is put back through ``os.link``, which fails
       closed if a lease already exists again, so a third party's claim is never
       clobbered by the restore.

    Returns ``True`` when the inspected object is gone from the lease path and
    the caller may attempt its own exclusive claim.

    Residual limitation: ``os.link`` is unavailable on FAT/exFAT volumes and on
    some network filesystems.  Where the restore cannot run, the mismatched
    object is left under its private name and the caller refuses rather than
    claiming, so the failure mode is a refusal requiring operator recovery, not
    two owners.
    """
    path = lease_path(install_root)
    private = path.with_name(f".{LEASE_FILE}.takeover-{uuid.uuid4().hex}")
    # Cheap pre-condition: never even attempt to move an object that already
    # differs from the one that was inspected.
    if _lease_bytes(install_root) != expected:
        return False
    try:
        os.rename(path, private)
    except FileNotFoundError:
        # Someone else already removed it; the pathname is free either way.
        return True
    except OSError:
        return False
    try:
        moved = private.read_bytes()
    except OSError:
        moved = None
    if moved == expected:
        try:
            private.unlink()
        except OSError:  # pragma: no cover - best effort
            pass
        return True
    try:
        os.link(private, path)
    except OSError:
        # A lease exists again, or hard links are unsupported here.  The moved
        # object stays under its private name rather than being destroyed, and
        # the caller refuses: it has no proof of what it would be replacing.
        return False
    try:
        private.unlink()
    except OSError:  # pragma: no cover - best effort
        pass
    return False


def acquire_lease(
    install_root: Path, *, run_marker: str, owner: str
) -> "OrderedDict[str, Any]":
    """Claim exclusive persistence authority for one install root.

    Refuses when another owner holds the lease.  A lease whose holder is
    provably gone on this host is taken over and reported, so a crashed run
    does not permanently block recovery — but the takeover removes the exact
    object it inspected (see :func:`_remove_exact_lease`), so two recoverers
    racing on one orphan produce exactly one live owner and one refusal.
    """
    path = lease_path(install_root)
    payload = json.dumps(
        {
            "owner": owner,
            "run": run_marker,
            "pid": os.getpid(),
            "node": platform.node(),
        },
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    ) + "\n"
    Path(install_root).mkdir(parents=True, exist_ok=True)
    takeover = ""
    for _attempt in (0, 1):
        try:
            descriptor = os.open(
                str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            # Capture the bytes *before* the read that judges liveness.  That
            # ordering is what makes the takeover safe: the authorized bytes can
            # then only be older than or equal to the object judged orphaned,
            # never newer, so a lease installed in between produces a mismatch
            # and a refusal rather than an unauthorized removal.
            inspected = _lease_bytes(install_root)
            existing = read_lease(install_root) or {}
            if str(existing.get("owner", "")) == owner:
                return OrderedDict(
                    (("owner", owner), ("state", "held"), ("takeover", ""))
                )
            if lease_is_orphaned(existing):
                if inspected is None or not _remove_exact_lease(
                    install_root, inspected
                ):
                    raise ProgressRefusal(
                        "lease-conflict",
                        "the orphaned progress lease was replaced while it was "
                        "being recovered; another invocation now holds this "
                        "install root",
                    )
                takeover = "released-orphaned-lease"
                continue
            raise ProgressRefusal(
                "lease-conflict",
                (
                    "another invocation holds the install progress lease for "
                    f"run {str(existing.get('run', '')) or 'unknown'}; wait "
                    "for it to finish or recover the orphaned lease before "
                    "retrying"
                ),
            )
        except OSError as exc:
            raise ProgressRefusal(
                "lease-unavailable",
                f"progress lease is unavailable: {exc.__class__.__name__}",
            ) from exc
        else:
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError as exc:
                try:
                    path.unlink()
                except OSError:  # pragma: no cover - best effort
                    pass
                raise ProgressRefusal(
                    "lease-unavailable",
                    f"progress lease could not be written: "
                    f"{exc.__class__.__name__}",
                ) from exc
            # Read back before reporting authority: an exclusive create proves
            # nobody else *created* this object, and this proves nobody has
            # replaced it since.  A caller only ever learns it holds the root
            # from a lease that still names it.
            confirmed = read_lease(install_root)
            if confirmed is None or str(confirmed.get("owner", "")) != owner:
                raise ProgressRefusal(
                    "lease-conflict",
                    "the progress lease was replaced immediately after it was "
                    "claimed; another invocation now holds this install root",
                )
            return OrderedDict(
                (
                    ("owner", owner),
                    ("state", "held"),
                    ("takeover", takeover),
                )
            )
    raise ProgressRefusal(  # pragma: no cover - loop always returns or raises
        "lease-conflict", "progress lease could not be claimed"
    )


def release_lease(install_root: Path, owner: str) -> None:
    """Release the lease only when this owner still holds it.

    Release is identity-bound for the same reason takeover is: a teardown that
    unlinks a pathname can drop a lease that a different invocation installed
    between the check and the syscall.
    """
    inspected = _lease_bytes(install_root)
    existing = read_lease(install_root)
    if (
        inspected is None
        or existing is None
        or str(existing.get("owner", "")) != owner
    ):
        return
    _remove_exact_lease(install_root, inspected)


# ---------------------------------------------------------------------------
# Commit boundaries
# ---------------------------------------------------------------------------


def _guard_owner(envelope: Mapping[str, Any], install_root: Path, owner: str) -> None:
    lease = read_lease(install_root)
    if lease is None or str(lease.get("owner", "")) != owner:
        raise ProgressRefusal(
            "lease-conflict",
            "this invocation no longer holds the install progress lease; "
            "another run may have taken it over",
        )
    run = envelope.get("run")
    marker = str(run.get("marker")) if isinstance(run, Mapping) else ""
    if str(lease.get("run", "")) != marker:
        raise ProgressRefusal(
            "run-conflict",
            "the progress lease is bound to a different run identity",
        )


def _persist(
    install_root: Path, envelope: Mapping[str, Any], owner: str
) -> "OrderedDict[str, Any]":
    """Persist one envelope revision, then return the committed value.

    The caller only ever sees a revision that reached disk.  A permission or
    space failure therefore cannot advance in-memory completion state either.
    """
    _guard_owner(envelope, install_root, owner)
    projected = envelope_projection(envelope)
    recoverable_write_text(
        progress_path(install_root), serialize_envelope(projected)
    )
    return projected


def begin_progress(
    install_root: Path,
    *,
    record: Mapping[str, Any],
    projection: Mapping[str, Any],
    surface_profiles: Sequence[Mapping[str, Any]],
    owner: str,
    recovery: Optional[Mapping[str, Any]] = None,
) -> "OrderedDict[str, Any]":
    """Open a run: persist the plan projection with no marker advanced."""
    envelope = build_envelope(
        record=record,
        surface_profiles=surface_profiles,
        projection=projection,
        sequence=1,
        status="active",
        recovery=recovery,
    )
    return _persist(install_root, envelope, owner)


def open_boundary(
    install_root: Path,
    envelope: Mapping[str, Any],
    *,
    surface: str,
    action: str,
    phase: str = "apply",
    owner: str,
) -> "OrderedDict[str, Any]":
    """Persist mutation intent *before* the mutation happens.

    A crash after this point and before :func:`commit_checkpoint` leaves an
    open boundary and an ``in-progress`` checkpoint on disk, which resume
    reports as uncertain rather than complete.
    """
    if surface not in SURFACE_KINDS:
        raise ProgressRefusal(
            "unknown-surface", f"unknown surface kind: {surface!r}"
        )
    updated = envelope_projection(envelope)
    updated["sequence"] = int(updated.get("sequence") or 0) + 1
    profile = _profile_for(updated.get("surface_profiles"), surface)
    updated["boundary"] = OrderedDict(
        (
            ("phase", phase),
            ("surface", surface),
            ("action", action),
            ("mutation_status", "intended"),
            ("retry_safety", profile["retry_safety"]),
            ("observation", profile["observation"]),
        )
    )
    progress = updated.get("progress")
    if isinstance(progress, dict):
        for checkpoint in progress.get("checkpoints", []):
            if (
                isinstance(checkpoint, dict)
                and checkpoint.get("surface") == surface
            ):
                checkpoint["status"] = "in-progress"
                checkpoint["verification"] = "unknown"
                checkpoint["attempted_action"] = action
                checkpoint["mutation_status"] = "intended"
                checkpoint["retry_safety"] = _escalate_retry(
                    profile["retry_safety"], "inspect-before-retry"
                )
    return _persist(install_root, updated, owner)


def commit_checkpoint(
    install_root: Path,
    envelope: Mapping[str, Any],
    *,
    checkpoint: Mapping[str, Any],
    owner: str,
) -> "OrderedDict[str, Any]":
    """Persist one verified checkpoint and close its boundary."""
    evidence = checkpoint.get("evidence")
    if checkpoint.get("status") == "completed" and (
        not isinstance(evidence, Mapping)
        or not evidence
        or not evidence.get("identity")
        or evidence.get("verification") != "verified"
    ):
        raise ProgressRefusal(
            "checkpoint-evidence-missing",
            "a completed checkpoint requires verified portable evidence",
        )
    if isinstance(evidence, Mapping):
        offending = validate_portable_evidence(evidence)
        if offending:
            raise ProgressRefusal(
                "checkpoint-evidence-forbidden",
                "; ".join(sorted(item["detail"] for item in offending)),
            )
    updated = envelope_projection(envelope)
    updated["sequence"] = int(updated.get("sequence") or 0) + 1
    surface = str(checkpoint.get("surface"))
    boundary = updated.get("boundary")
    if isinstance(boundary, Mapping) and boundary.get("surface") == surface:
        updated["boundary"] = None
    progress = updated.get("progress")
    if isinstance(progress, dict):
        rows = progress.get("checkpoints")
        if isinstance(rows, list):
            replaced = False
            for index, existing in enumerate(rows):
                if (
                    isinstance(existing, Mapping)
                    and existing.get("id") == checkpoint.get("id")
                ):
                    rows[index] = copy.deepcopy(dict(checkpoint))
                    replaced = True
                    break
            if not replaced:
                rows.append(copy.deepcopy(dict(checkpoint)))
            rows.sort(key=lambda item: str(item.get("id", "")))
    return _persist(install_root, updated, owner)


def record_failure(
    install_root: Path,
    envelope: Mapping[str, Any],
    *,
    projection: Mapping[str, Any],
    owner: str,
    detail: str,
    mutation_status: str = "refused-preserved",
) -> "OrderedDict[str, Any]":
    """Persist a refused or failed run without advancing any marker.

    A boundary whose content is demonstrably preserved is closed: nothing
    changed, so nothing is uncertain.  A boundary that failed part-way through
    a replacement stays open, so the next run treats it as uncertain and
    inspects it instead of replaying it.
    """
    updated = envelope_projection(envelope)
    prior_recovery = updated.get("recovery")
    prior_recovery = prior_recovery if isinstance(prior_recovery, Mapping) else {}
    updated["sequence"] = int(updated.get("sequence") or 0) + 1
    updated["status"] = "active"
    updated["progress"] = copy.deepcopy(dict(projection))
    preserved = mutation_status.endswith("-preserved")
    boundary = updated.get("boundary")
    if preserved:
        updated["boundary"] = None
    elif isinstance(boundary, dict):
        boundary["mutation_status"] = mutation_status
    updated["retention"] = OrderedDict(
        (
            ("class", "active"),
            (
                "reason",
                "run did not reach terminal proof; recovery evidence retained",
            ),
            ("evidence_retained", True),
        )
    )
    updated["recovery"] = _recovery_note(
        "compatible" if preserved or not boundary else "evidence-missing",
        detail,
        quarantine=str(prior_recovery.get("quarantine", "")),
        quarantined_identity=str(
            prior_recovery.get("quarantined_identity", "")
        ),
        preserved_classification=str(
            prior_recovery.get("preserved_classification", "")
        ),
    )
    return _persist(install_root, updated, owner)


def advance_completion(
    install_root: Path,
    envelope: Mapping[str, Any],
    *,
    projection: Mapping[str, Any],
    owner: str,
) -> "OrderedDict[str, Any]":
    """Advance the schema and completion markers — after all verified work.

    Each marker is a separate recoverable write, so a crash or a disk failure
    between them can only ever leave a *weaker* claim on disk than the work
    actually performed.  When required work or verification is missing, no
    marker advances at all and the projection is persisted as still-active.
    The cleanup marker is deliberately not touched here: :func:`advance_cleanup`
    runs after the caller has published its visible mirror, so the record is
    never fully terminal while a downstream write is still outstanding.
    """
    outcome = projection.get("outcome")
    status = (
        str(outcome.get("status")) if isinstance(outcome, Mapping) else "unknown"
    )
    unresolved = _unresolved_checkpoints(projection)
    committed = envelope_projection(envelope)
    committed["sequence"] = int(committed.get("sequence") or 0) + 1
    committed["progress"] = copy.deepcopy(dict(projection))
    if status not in ("complete", "complete-qualified") or unresolved:
        committed["boundary"] = (
            None if not unresolved else committed.get("boundary")
        )
        committed["retention"] = OrderedDict(
            (
                ("class", "active"),
                (
                    "reason",
                    "terminal proof is absent; recovery evidence retained",
                ),
                ("evidence_retained", True),
            )
        )
        detail = (
            "unresolved work remains: " + ", ".join(unresolved)
            if unresolved
            else f"run outcome is {status}; no terminal marker advanced"
        )
        committed["recovery"] = _carry_recovery(
            committed, "compatible", detail
        )
        return _persist(install_root, committed, owner)

    # Marker-last ordering, one durable write per marker.
    committed["boundary"] = None
    terminal = OrderedDict(committed.get("terminal") or {})
    terminal["schema"] = "advanced"
    committed["terminal"] = terminal
    committed["recovery"] = _carry_recovery(
        committed, "compatible", "schema identity advanced after verified work"
    )
    committed = _persist(install_root, committed, owner)

    committed = envelope_projection(committed)
    committed["sequence"] = int(committed.get("sequence") or 0) + 1
    terminal = OrderedDict(committed.get("terminal") or {})
    terminal["completion"] = "advanced"
    committed["terminal"] = terminal
    committed["status"] = "terminal"
    committed["recovery"] = _carry_recovery(
        committed,
        "compatible",
        "completion advanced after every checkpoint verified",
    )
    return _persist(install_root, committed, owner)


def advance_cleanup(
    install_root: Path, envelope: Mapping[str, Any], *, owner: str
) -> "OrderedDict[str, Any]":
    """Advance the cleanup marker last, once terminal proof is published."""
    terminal_state = envelope.get("terminal")
    terminal_state = (
        terminal_state if isinstance(terminal_state, Mapping) else {}
    )
    if terminal_state.get("completion") != "advanced":
        raise ProgressRefusal(
            "cleanup-before-completion",
            "cleanup cannot advance before the completion marker",
        )
    committed = envelope_projection(envelope)

    # Only now that terminal proof exists is a quarantined predecessor no
    # longer the only recovery evidence for this root.  Its content identity
    # stays in the record so the outcome remains explainable.
    superseded = ""
    target = quarantine_path(install_root)
    try:
        if target.is_file() and not target.is_symlink():
            target.unlink()
            superseded = QUARANTINE_FILE
    except OSError:  # pragma: no cover - best effort
        pass

    committed = envelope_projection(committed)
    committed["sequence"] = int(committed.get("sequence") or 0) + 1
    terminal = OrderedDict(committed.get("terminal") or {})
    terminal["cleanup"] = "advanced"
    committed["terminal"] = terminal
    committed["retention"] = OrderedDict(
        (
            ("class", "terminal"),
            (
                "reason",
                "terminal proof recorded; evidence retained to explain the "
                "outcome",
            ),
            ("evidence_retained", True),
        )
    )
    committed["recovery"] = _carry_recovery(
        committed,
        "compatible",
        (
            "cleanup advanced last, after terminal proof; superseded "
            f"{superseded}"
            if superseded
            else "cleanup advanced last, after terminal proof"
        ),
        drop_quarantine_file=bool(superseded),
    )
    return _persist(install_root, committed, owner)


def _unresolved_checkpoints(projection: Mapping[str, Any]) -> List[str]:
    unresolved: List[str] = []
    for item in projection.get("checkpoints", []):
        if not isinstance(item, Mapping):
            continue
        if item.get("status") == "completed" and item.get(
            "verification"
        ) == "verified":
            continue
        surface = str(item.get("surface", ""))
        state = _surface_state(projection, surface)
        if state in ("declined", "deferred") or (
            surface == "project-schema-migration-offers"
            and state in ("offered", "not-applicable")
        ):
            continue
        unresolved.append(str(item.get("id", "")))
    return sorted(unresolved)


def _surface_state(projection: Mapping[str, Any], surface: str) -> str:
    for item in projection.get("surfaces", []):
        if isinstance(item, Mapping) and item.get("kind") == surface:
            return str(item.get("state", "unknown"))
    return "unknown"


def _profile_for(
    profiles: Optional[Sequence[Mapping[str, Any]]], surface: str
) -> Dict[str, str]:
    for item in profiles or ():
        if isinstance(item, Mapping) and item.get("surface") == surface:
            return {
                "surface": surface,
                "retry_safety": str(
                    item.get("retry_safety", "inspect-before-retry")
                ),
                "observation": str(item.get("observation", "unobservable")),
            }
    return {
        "surface": surface,
        "retry_safety": "inspect-before-retry",
        "observation": "unobservable",
    }


def _escalate_retry(*values: str) -> str:
    return max(values, key=lambda value: _RETRY_RANK.get(value, 1))


# ---------------------------------------------------------------------------
# Resume assessment
# ---------------------------------------------------------------------------


def _material_context(context: Any) -> str:
    """Return a stable comparison form for one decision context."""
    return json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)


def _classify_compatibility(
    prior: Mapping[str, Any], current: Mapping[str, Any]
) -> Tuple[str, str]:
    classification = str(prior.get("classification"))
    lease_state = str(prior.get("lease_state", "absent"))
    lease = prior.get("lease")
    if lease_state == "held" and isinstance(lease, Mapping):
        holder_run = str(lease.get("run", ""))
        if holder_run != str(current.get("marker", "")):
            return (
                "lease-conflict",
                (
                    "another invocation holds the progress lease for run "
                    f"{holder_run or 'unknown'}; its evidence must not be "
                    "consumed by this run"
                ),
            )
    envelope = prior.get("envelope")
    if classification != "compatible" or not isinstance(envelope, Mapping):
        if classification == "absent" and lease_state in ("held", "orphaned"):
            return (
                "orphaned",
                "a progress lease exists with no progress record",
            )
        return classification, str(prior.get("detail", ""))
    run = envelope.get("run")
    run = run if isinstance(run, Mapping) else {}
    source = run.get("source")
    source = source if isinstance(source, Mapping) else {}
    if str(source.get("value", "")) != str(current.get("source_identity", "")):
        return (
            "source-mismatch",
            (
                "persisted progress is bound to a different source identity "
                "and cannot drive new mutations"
            ),
        )
    projection = envelope.get("progress")
    projection = projection if isinstance(projection, Mapping) else {}
    claims_install = any(
        isinstance(item, Mapping)
        and item.get("kind") == "installed_content"
        and item.get("value")
        for item in projection.get("versions", [])
    )
    if claims_install and not current.get("installed_identity"):
        return (
            "orphaned",
            (
                "persisted progress claims installed content that is no "
                "longer present under the install root"
            ),
        )
    same_marker = str(run.get("marker", "")) == str(current.get("marker", ""))
    if not same_marker:
        if envelope.get("status") == "active" and envelope.get("boundary"):
            return (
                "run-conflict",
                (
                    "an interrupted run left an open mutation boundary under a "
                    "different run identity; inspect it before reusing work"
                ),
            )
        return (
            "stale",
            (
                "persisted progress describes a different run identity for the "
                "same source; only independently valid evidence is reusable"
            ),
        )
    return "compatible", "persisted progress matches the current run identity"


def assess_resume(
    *,
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
) -> "OrderedDict[str, Any]":
    """Return a deterministic, byte-stable resume plan.

    ``current`` supplies the freshly observed facts: ``marker``,
    ``operation``, ``source_identity``, ``installed_identity``, ``surfaces``,
    ``choices``, ``migrations``, ``restarts``, and ``plan_actions``.  Nothing
    here reads the filesystem or carries a clock, so equivalent inputs
    serialize identically.
    """
    classification, detail = _classify_compatibility(prior, current)
    envelope = prior.get("envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    projection = envelope.get("progress")
    projection = projection if isinstance(projection, Mapping) else {}
    prior_run = envelope.get("run")
    prior_run = prior_run if isinstance(prior_run, Mapping) else {}
    prior_source = prior_run.get("source")
    prior_source = prior_source if isinstance(prior_source, Mapping) else {}

    observed = {
        str(item.get("kind")): item
        for item in current.get("surfaces", [])
        if isinstance(item, Mapping)
    }
    boundary = envelope.get("boundary")
    boundary_surface = (
        str(boundary.get("surface"))
        if isinstance(boundary, Mapping) and boundary.get("surface")
        else ""
    )
    reuse_allowed = classification in _REUSABLE_CLASSIFICATIONS

    reusable: List[Dict[str, Any]] = []
    uncertain: List[Dict[str, Any]] = []
    incompatible: List[Dict[str, Any]] = []
    for item in sorted(
        (
            row
            for row in projection.get("checkpoints", [])
            if isinstance(row, Mapping)
        ),
        key=lambda row: str(row.get("id", "")),
    ):
        surface = str(item.get("surface", ""))
        profile = _profile_for(profiles, surface)
        evidence = item.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        verified = (
            item.get("status") == "completed"
            and item.get("verification") == "verified"
            and bool(evidence.get("identity"))
            and evidence.get("verification") == "verified"
        )
        current_identity = str(
            observed.get(surface, {}).get("observed_identity", "")
        )
        current_state = str(observed.get(surface, {}).get("state", "unknown"))
        open_here = surface == boundary_surface or item.get(
            "status"
        ) == "in-progress"
        if not reuse_allowed:
            incompatible.append(
                OrderedDict(
                    (
                        ("checkpoint", str(item.get("id", ""))),
                        ("surface", surface),
                        ("reason", classification),
                        ("detail", detail),
                    )
                )
            )
            if open_here:
                uncertain.append(
                    _uncertain_entry(item, profile, "open mutation boundary")
                )
            continue
        if open_here:
            uncertain.append(
                _uncertain_entry(
                    item,
                    profile,
                    "mutation was persisted as intended but no verified "
                    "checkpoint followed",
                )
            )
            continue
        if item.get("status") in ("blocked", "failed"):
            # A bounded refusal or an OS error that demonstrably preserved its
            # target changed nothing, so it is remaining work, not uncertain
            # work.  Only a failure part-way through a replacement — or one
            # that never recorded what it attempted — needs inspection.
            mutation_status = str(item.get("mutation_status", ""))
            if mutation_status and not mutation_status.endswith("-preserved"):
                uncertain.append(
                    _uncertain_entry(
                        item,
                        profile,
                        f"prior checkpoint is {item.get('status')} after "
                        f"{mutation_status}",
                    )
                )
                continue
            incompatible.append(
                OrderedDict(
                    (
                        ("checkpoint", str(item.get("id", ""))),
                        ("surface", surface),
                        ("reason", f"prior-{item.get('status')}"),
                        (
                            "detail",
                            "prior work did not complete; its target was "
                            "preserved and remains replannable",
                        ),
                    )
                )
            )
            continue
        if not verified:
            incompatible.append(
                OrderedDict(
                    (
                        ("checkpoint", str(item.get("id", ""))),
                        ("surface", surface),
                        ("reason", "unverified-checkpoint"),
                        (
                            "detail",
                            "prior checkpoint carries no verified evidence",
                        ),
                    )
                )
            )
            continue
        if (
            current_identity
            and str(evidence.get("observed_identity", evidence.get("identity")))
            == current_identity
            and current_state in _SAFE_SURFACE_STATES
        ):
            reusable.append(
                OrderedDict(
                    (
                        ("checkpoint", str(item.get("id", ""))),
                        ("surface", surface),
                        ("disposition", "reuse-verified"),
                        ("evidence_identity", str(evidence.get("identity"))),
                        ("retry_safety", profile["retry_safety"]),
                        ("observation", profile["observation"]),
                    )
                )
            )
            continue
        incompatible.append(
            OrderedDict(
                (
                    ("checkpoint", str(item.get("id", ""))),
                    ("surface", surface),
                    ("reason", "evidence-superseded"),
                    (
                        "detail",
                        "current observation differs from the persisted "
                        "verified evidence",
                    ),
                )
            )
        )

    reusable_surfaces = {str(item["surface"]) for item in reusable}
    uncertain_surfaces = {str(item["surface"]) for item in uncertain}

    preserved_choices = _preserved_choices(
        projection, current, reuse_allowed=reuse_allowed
    )
    preserved_migrations = _preserved_migrations(
        projection, current, reuse_allowed=reuse_allowed
    )
    preserved_surfaces = {
        str(item["surface"]) for item in preserved_choices
    }

    diagnosis: List[Dict[str, Any]] = []
    for kind in SURFACE_KINDS:
        surface = observed.get(kind)
        if surface is None:
            continue
        profile = _profile_for(profiles, kind)
        state = str(surface.get("state", "unknown"))
        value = (
            "unverified"
            if kind in uncertain_surfaces
            else _DIAGNOSIS_BY_SURFACE_STATE.get(state, "unverified")
        )
        diagnosis.append(
            OrderedDict(
                (
                    ("surface", kind),
                    ("diagnosis", value),
                    ("state", state),
                    ("retry_safety", profile["retry_safety"]),
                    ("observation", profile["observation"]),
                    ("reused", kind in reusable_surfaces),
                )
            )
        )

    remaining: List[Dict[str, Any]] = []
    actions = {
        str(item.get("surface")): item
        for item in current.get("plan_actions", [])
        if isinstance(item, Mapping)
    }
    for kind in SURFACE_KINDS:
        action = actions.get(kind)
        if action is None:
            continue
        profile = _profile_for(profiles, kind)
        state = str(observed.get(kind, {}).get("state", "unknown"))
        if kind in uncertain_surfaces:
            remaining.append(
                OrderedDict(
                    (
                        ("surface", kind),
                        ("action", "inspect"),
                        ("authorization", str(action.get("authorization"))),
                        (
                            "disposition",
                            _escalate_retry(
                                profile["retry_safety"],
                                "inspect-before-retry",
                            ),
                        ),
                        ("reason", "uncertain-boundary"),
                    )
                )
            )
            continue
        if kind in preserved_surfaces:
            continue
        if kind in reusable_surfaces and state in _SAFE_SURFACE_STATES:
            continue
        if state in _SAFE_SURFACE_STATES:
            continue
        if state in ("declined", "deferred"):
            continue
        if (
            kind == "project-schema-migration-offers"
            and state == "offered"
            and preserved_migrations
        ):
            continue
        remaining.append(
            OrderedDict(
                (
                    ("surface", kind),
                    ("action", str(action.get("action"))),
                    ("authorization", str(action.get("authorization"))),
                    (
                        "disposition",
                        "refuse-replay"
                        if profile["retry_safety"] == "refuse-replay"
                        else (
                            "replan"
                            if profile["retry_safety"] == "idempotent"
                            else "inspect-before-retry"
                        ),
                    ),
                    ("reason", state),
                )
            )
        )

    restart = current.get("restart")
    restart = restart if isinstance(restart, Mapping) else {}
    return OrderedDict(
        (
            ("assessment_schema", RESUME_ASSESSMENT_SCHEMA),
            ("compatibility", classification),
            ("compatibility_detail", detail),
            (
                "prior_run",
                None
                if not prior_run
                else OrderedDict(
                    (
                        ("operation", str(prior_run.get("operation"))),
                        ("marker", str(prior_run.get("marker"))),
                        ("source_identity", str(prior_source.get("value"))),
                        ("status", str(envelope.get("status"))),
                        (
                            "terminal",
                            copy.deepcopy(envelope.get("terminal")),
                        ),
                    )
                ),
            ),
            (
                "current_run",
                OrderedDict(
                    (
                        ("operation", str(current.get("operation"))),
                        ("marker", str(current.get("marker"))),
                        (
                            "source_identity",
                            str(current.get("source_identity")),
                        ),
                    )
                ),
            ),
            ("reusable", reusable),
            ("uncertain", uncertain),
            ("incompatible", incompatible),
            ("preserved_choices", preserved_choices),
            ("preserved_migrations", preserved_migrations),
            ("surface_diagnosis", diagnosis),
            ("remaining_plan", remaining),
            (
                "restart",
                OrderedDict(
                    (
                        ("state", str(restart.get("state", "unknown"))),
                        (
                            "instruction_class",
                            str(restart.get("instruction_class", "none")),
                        ),
                    )
                ),
            ),
            (
                "recovery",
                _recovery_note(classification, detail),
            ),
            (
                "byte_stable_noop",
                classification == "compatible"
                and not remaining
                and not uncertain,
            ),
        )
    )


def _uncertain_entry(
    checkpoint: Mapping[str, Any], profile: Mapping[str, str], detail: str
) -> "OrderedDict[str, Any]":
    return OrderedDict(
        (
            ("checkpoint", str(checkpoint.get("id", ""))),
            ("surface", str(checkpoint.get("surface", ""))),
            (
                "disposition",
                _escalate_retry(
                    str(profile.get("retry_safety", "inspect-before-retry")),
                    "inspect-before-retry",
                ),
            ),
            ("observation", str(profile.get("observation", "unobservable"))),
            ("attempted_action", str(checkpoint.get("attempted_action", ""))),
            ("detail", detail),
        )
    )


def _preserved_choices(
    projection: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    reuse_allowed: bool,
) -> List[Dict[str, Any]]:
    """Return declines still valid because their affected facts are unchanged."""
    if not reuse_allowed:
        return []
    current_choices = {
        str(item.get("surface")): item
        for item in current.get("choices", [])
        if isinstance(item, Mapping)
    }
    preserved: List[Dict[str, Any]] = []
    for item in projection.get("choices", []):
        if not isinstance(item, Mapping) or item.get("state") != "declined":
            continue
        surface = str(item.get("surface"))
        live = current_choices.get(surface)
        if live is None:
            continue
        if str(live.get("offered_action")) != str(item.get("offered_action")):
            continue
        if _material_context(item.get("decision_context")) != _material_context(
            live.get("decision_context")
        ):
            continue
        preserved.append(
            OrderedDict(
                (
                    ("surface", surface),
                    ("offered_action", str(item.get("offered_action"))),
                    ("state", "declined"),
                    ("disposition", "preserve-choice"),
                    ("provenance", str(item.get("provenance", ""))),
                )
            )
        )
    preserved.sort(key=lambda row: str(row["surface"]))
    return preserved


def _preserved_migrations(
    projection: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    reuse_allowed: bool,
) -> List[Dict[str, Any]]:
    """Return migration deferrals whose offered action is materially unchanged."""
    if not reuse_allowed:
        return []
    live = {
        str(item.get("project_identity")): item
        for item in current.get("migrations", [])
        if isinstance(item, Mapping)
    }
    preserved: List[Dict[str, Any]] = []
    for item in projection.get("migrations", []):
        if not isinstance(item, Mapping) or item.get("choice_state") != "deferred":
            continue
        identity = str(item.get("project_identity"))
        offer = live.get(identity)
        if offer is None:
            continue
        if any(
            str(offer.get(field)) != str(item.get(field))
            for field in (
                "current_schema",
                "target_schema",
                "applicability",
                "supported_workflow",
            )
        ):
            continue
        preserved.append(
            OrderedDict(
                (
                    ("project_identity", identity),
                    ("choice_state", "deferred"),
                    ("disposition", "preserve-choice"),
                    ("applicability", str(item.get("applicability"))),
                    ("target_schema", str(item.get("target_schema"))),
                )
            )
        )
    preserved.sort(key=lambda row: str(row["project_identity"]))
    return preserved


# ---------------------------------------------------------------------------
# Portable evidence
# ---------------------------------------------------------------------------

_PORTABLE_VERSION_KINDS = (
    "release_version",
    "installed_content",
    "project_schema_version",
    "running_server",
    "mcp_protocol_version",
)


def resume_is_reusable(compatibility: str) -> bool:
    """True when a persisted record may still speak for the current run."""
    return compatibility in _REUSABLE_CLASSIFICATIONS


def _authoritative_predecessor(
    run: Mapping[str, Any],
    source: Mapping[str, Any],
    assessment: Optional[Mapping[str, Any]],
) -> "OrderedDict[str, Any]":
    """Classify the predecessor, or refuse to blend it into this record.

    Only closed vocabulary and content identities cross this boundary.  The
    assessment's prose detail deliberately does not: it names internal recovery
    state, which portable evidence excludes by contract.
    """
    persisted_source = str(source.get("value") or "")
    persisted_marker = str(run.get("marker") or "")
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
    reusable = resume_is_reusable(compatibility)
    current = assessment.get("current_run")
    current = current if isinstance(current, Mapping) else {}
    current_source = str(current.get("source_identity") or "")
    current_marker = str(current.get("marker") or "")
    if (
        current_source
        and persisted_source
        and current_source != persisted_source
    ):
        raise ProgressRefusal(
            "portable-evidence-authority-conflict",
            (
                "the record's source identity is not the source identity this "
                "plan assessed; portable evidence must be built from one "
                "compatible observation set, not merged across two"
            ),
        )
    if (
        not reusable
        and current_marker
        and persisted_marker
        and current_marker != persisted_marker
    ):
        raise ProgressRefusal(
            "portable-evidence-authority-conflict",
            (
                f"a {compatibility or 'incompatible'} predecessor cannot supply "
                "the observations behind this plan's remaining work; render "
                "current observations instead"
            ),
        )
    prior = assessment.get("prior_run")
    prior = prior if isinstance(prior, Mapping) else {}
    prior_source = str(prior.get("source_identity") or "")
    return OrderedDict(
        (
            ("classification", compatibility or "not-assessed"),
            (
                "authority",
                "same-source"
                if reusable and prior
                else ("not-used" if prior else "none"),
            ),
            ("reusable", bool(reusable and prior)),
            (
                "superseded_source_identity",
                prior_source if prior_source != current_source else "",
            ),
        )
    )


def portable_evidence(
    envelope: Mapping[str, Any],
    *,
    assessment: Optional[Mapping[str, Any]] = None,
) -> "OrderedDict[str, Any]":
    """Render a portable, privacy-preserving evidence record.

    Deliberately excludes internal recovery metadata — the run marker,
    boundary, sequence, retention class, quarantine state, and recovery
    classification all stay on the internal side of the boundary.

    The record has exactly one authority.  Everything below the ``source``
    field — version identities, per-surface state, checkpoint status — is read
    from ``envelope``, while ``remaining_work`` is read from ``assessment``.
    Those two only describe the same installation while they agree on who
    produced it, so a disagreement is a refusal rather than a record: a reader
    cannot be handed one document that attributes a predecessor's source
    identity to the current run's remaining work.  Callers holding an
    incompatible predecessor must build the envelope from current observations
    instead; :func:`resume_is_reusable` is the test for which they hold.
    """
    projection = envelope.get("progress")
    projection = projection if isinstance(projection, Mapping) else {}
    run = envelope.get("run")
    run = run if isinstance(run, Mapping) else {}
    source = run.get("source")
    source = source if isinstance(source, Mapping) else {}
    predecessor = _authoritative_predecessor(run, source, assessment)

    versions: List[Dict[str, Any]] = []
    by_kind = {
        str(item.get("kind")): item
        for item in projection.get("versions", [])
        if isinstance(item, Mapping)
    }
    for kind in _PORTABLE_VERSION_KINDS:
        item = by_kind.get(kind)
        if item is None:
            continue
        versions.append(
            OrderedDict(
                (
                    ("kind", kind),
                    ("value", _scalar(item.get("value"))),
                    ("state", _scalar(item.get("state"))),
                    ("verification", _scalar(item.get("verification"))),
                    ("authority", _scalar(item.get("authority"))),
                )
            )
        )

    checkpoints = {
        str(item.get("surface")): item
        for item in projection.get("checkpoints", [])
        if isinstance(item, Mapping)
    }
    diagnosis = {
        str(item.get("surface")): item
        for item in (assessment or {}).get("surface_diagnosis", [])
        if isinstance(item, Mapping)
    }
    profiles = envelope.get("surface_profiles")
    surfaces: List[Dict[str, Any]] = []
    for kind in SURFACE_KINDS:
        surface = next(
            (
                item
                for item in projection.get("surfaces", [])
                if isinstance(item, Mapping) and item.get("kind") == kind
            ),
            None,
        )
        if surface is None:
            continue
        state = str(surface.get("state", "unknown"))
        checkpoint = checkpoints.get(kind, {})
        evidence = checkpoint.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        profile = _profile_for(profiles, kind)
        surfaces.append(
            OrderedDict(
                (
                    ("surface", kind),
                    ("state", state),
                    (
                        "diagnosis",
                        str(
                            diagnosis.get(kind, {}).get(
                                "diagnosis",
                                _DIAGNOSIS_BY_SURFACE_STATE.get(
                                    state, "unverified"
                                ),
                            )
                        ),
                    ),
                    ("checkpoint_status", _scalar(checkpoint.get("status"))),
                    ("retry_safety", profile["retry_safety"]),
                    ("observation", profile["observation"]),
                    (
                        "evidence",
                        OrderedDict(
                            (field, _scalar(evidence[field]))
                            for field in PORTABLE_EVIDENCE_FIELDS
                            if field in evidence
                            and isinstance(
                                evidence[field], (str, int, float, bool)
                            )
                        ),
                    ),
                )
            )
        )

    restarts = [
        OrderedDict(
            (
                ("client", _scalar(item.get("client"))),
                ("state", _scalar(item.get("state"))),
                (
                    "instruction_class",
                    _scalar(item.get("instruction_class")),
                ),
                ("expected_proof", _scalar(item.get("expected_proof"))),
            )
        )
        for item in projection.get("restarts", [])
        if isinstance(item, Mapping)
    ]
    outcome = projection.get("outcome")
    outcome = outcome if isinstance(outcome, Mapping) else {}
    remaining = [
        OrderedDict(
            (
                ("surface", _scalar(item.get("surface"))),
                ("action", _scalar(item.get("action"))),
                ("disposition", _scalar(item.get("disposition"))),
            )
        )
        for item in (assessment or {}).get("remaining_plan", [])
        if isinstance(item, Mapping)
    ]
    return OrderedDict(
        (
            ("evidence_schema", PORTABLE_EVIDENCE_SCHEMA),
            ("record_schema_identity", SCHEMA_IDENTITY),
            ("record_schema_version", RECORD_SCHEMA_VERSION),
            ("operation", _scalar(run.get("operation"))),
            (
                "source",
                OrderedDict(
                    (
                        ("kind", _scalar(source.get("kind"))),
                        ("identity", _scalar(source.get("value"))),
                        ("authority", _scalar(source.get("authority"))),
                    )
                ),
            ),
            ("predecessor", predecessor),
            ("version_identities", versions),
            ("surfaces", surfaces),
            ("restarts", restarts),
            (
                "restart_required",
                bool(outcome.get("restart_required", False)),
            ),
            ("remaining_work", remaining),
            (
                "outcome",
                OrderedDict(
                    (
                        ("status", _scalar(outcome.get("status"))),
                        ("claim", _scalar(outcome.get("claim"))),
                        (
                            "fully_updated",
                            bool(outcome.get("fully_updated", False)),
                        ),
                    )
                ),
            ),
            (
                "exclusions",
                [
                    "secrets-and-credentials",
                    "private-prompt-or-conversation-content",
                    "project-management-identifiers",
                    "unrelated-work-root-data",
                    "caller-selected-executables",
                    "caller-selected-destinations",
                    "internal-recovery-metadata",
                ],
            ),
        )
    )


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def portable_evidence_diagnostics(
    portable: Mapping[str, Any]
) -> List[Dict[str, str]]:
    """Return violations of the portable-evidence boundary, or an empty list."""
    diagnostics: List[Dict[str, str]] = []
    for surface in portable.get("surfaces", []):
        if not isinstance(surface, Mapping):
            continue
        evidence = surface.get("evidence")
        if isinstance(evidence, Mapping) and evidence:
            diagnostics.extend(
                validate_portable_evidence(
                    evidence, field=f"surfaces.{surface.get('surface')}.evidence"
                )
            )
    for path, text in sorted(_walk_strings(portable, "portable")):
        if _GOVERNANCE_TEXT.search(text):
            diagnostics.append(
                {
                    "code": "portable-evidence-governance-field",
                    "severity": "error",
                    "field": path,
                    "detail": "portable evidence contains a project-management identifier",
                    "recovery": "remove governance identifiers from portable evidence",
                }
            )
        if _PRIVATE_TEXT.search(text):
            diagnostics.append(
                {
                    "code": "portable-evidence-private-field",
                    "severity": "error",
                    "field": path,
                    "detail": "portable evidence contains secret-bearing text",
                    "recovery": "retain only verification facts",
                }
            )
    diagnostics.sort(
        key=lambda item: (str(item["field"]), str(item["code"]))
    )
    return diagnostics


def _walk_strings(value: Any, path: str) -> List[Tuple[str, str]]:
    found: List[Tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key in value:
            found.append((f"{path}.{key}", str(key)))
            found.extend(_walk_strings(value[key], f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_walk_strings(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        found.append((path, value))
    return found


def render_portable_evidence(portable: Mapping[str, Any]) -> str:
    """Render the portable evidence record as operator-readable text."""
    lines: List[str] = []
    lines.append("# Cartopian install/update evidence")
    lines.append("")
    lines.append(f"Evidence schema: {portable.get('evidence_schema')}")
    lines.append(
        "Record schema: "
        f"{portable.get('record_schema_identity')} "
        f"v{portable.get('record_schema_version')}"
    )
    lines.append(f"Operation: {portable.get('operation')}")
    source = portable.get("source")
    source = source if isinstance(source, Mapping) else {}
    lines.append(
        f"Source: {source.get('kind')} {source.get('identity')} "
        f"({source.get('authority')})"
    )
    lines.append("")
    lines.append("## Predecessor")
    lines.append("")
    predecessor = portable.get("predecessor")
    predecessor = predecessor if isinstance(predecessor, Mapping) else {}
    lines.append(
        f"Prior record: {predecessor.get('classification')} "
        f"(authority={predecessor.get('authority')}, "
        f"reusable={predecessor.get('reusable')})"
    )
    superseded = str(predecessor.get("superseded_source_identity") or "")
    lines.append(
        f"- superseded source identity: {superseded}"
        if superseded
        else "- no superseded source identity; one source produced this record"
    )
    lines.append("")
    lines.append("## Version identities")
    lines.append("")
    for item in portable.get("version_identities", []):
        if not isinstance(item, Mapping):
            continue
        lines.append(
            f"- {item.get('kind')}: {item.get('value')} "
            f"[state={item.get('state')} "
            f"verification={item.get('verification')}]"
        )
    lines.append("")
    lines.append("## Per-surface state")
    lines.append("")
    for item in portable.get("surfaces", []):
        if not isinstance(item, Mapping):
            continue
        evidence = item.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        lines.append(
            f"- {item.get('surface')}: {item.get('diagnosis')} "
            f"(state={item.get('state')}, "
            f"checkpoint={item.get('checkpoint_status')}, "
            f"retry={item.get('retry_safety')}, "
            f"observation={item.get('observation')})"
        )
        if evidence:
            lines.append(
                f"  evidence: {evidence.get('kind')} "
                f"{evidence.get('identity')} "
                f"[{evidence.get('verification')}]"
            )
    lines.append("")
    lines.append("## Restart")
    lines.append("")
    lines.append(f"Restart required: {portable.get('restart_required')}")
    restarts = portable.get("restarts") or []
    if not restarts:
        lines.append("- no client restart fact was observed")
    for item in restarts:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            f"- {item.get('client')}: {item.get('state')} "
            f"(instruction={item.get('instruction_class')}, "
            f"expected proof={item.get('expected_proof')})"
        )
    lines.append("")
    lines.append("## Remaining work")
    lines.append("")
    remaining = portable.get("remaining_work") or []
    if not remaining:
        lines.append("- none; no surface requires further action")
    for item in remaining:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            f"- {item.get('surface')}: {item.get('action')} "
            f"({item.get('disposition')})"
        )
    lines.append("")
    lines.append("## Outcome")
    lines.append("")
    outcome = portable.get("outcome")
    outcome = outcome if isinstance(outcome, Mapping) else {}
    lines.append(
        f"Status: {outcome.get('status')}; claim: {outcome.get('claim')}; "
        f"fully updated: {outcome.get('fully_updated')}"
    )
    lines.append("")
    lines.append("## Excluded content classes")
    lines.append("")
    for item in portable.get("exclusions", []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def progress_contract() -> "OrderedDict[str, Any]":
    """Deterministic machine-readable metadata for the persistence boundary."""
    return OrderedDict(
        (
            ("progress_schema_identity", PROGRESS_SCHEMA_IDENTITY),
            ("progress_schema_version", PROGRESS_SCHEMA_VERSION),
            ("assessment_schema", RESUME_ASSESSMENT_SCHEMA),
            ("portable_evidence_schema", PORTABLE_EVIDENCE_SCHEMA),
            ("envelope_fields", list(ENVELOPE_FIELDS)),
            (
                "files",
                OrderedDict(
                    (
                        ("progress", PROGRESS_FILE),
                        ("quarantine", QUARANTINE_FILE),
                        ("lease", LEASE_FILE),
                    )
                ),
            ),
            (
                "vocabularies",
                OrderedDict(
                    (
                        ("progress_statuses", list(PROGRESS_STATUSES)),
                        ("retention_classes", list(RETENTION_CLASSES)),
                        ("marker_states", list(MARKER_STATES)),
                        ("compatibility_states", list(COMPATIBILITY_STATES)),
                        ("surface_diagnoses", list(SURFACE_DIAGNOSES)),
                        ("resume_dispositions", list(RESUME_DISPOSITIONS)),
                        ("recovery_actions", list(RECOVERY_ACTIONS)),
                        (
                            "observation_capabilities",
                            list(OBSERVATION_CAPABILITIES),
                        ),
                    )
                ),
            ),
            (
                "recovery_by_classification",
                OrderedDict(
                    (key, list(value))
                    for key, value in _RECOVERY_BY_CLASSIFICATION.items()
                ),
            ),
        )
    )
