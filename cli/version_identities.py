"""Peer Cartopian version identities with explicit authority and state."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cli.config_schema import identity_contract
from cli.install_state import (
    SCHEMA_IDENTITY,
    positive_identity_fact,
    supported_record_schema_version,
)

# The MCP-affected content surface (mirrors ``cli.install_workflow.MCP_TARGETS``).
# ``cli`` is a member because the MCP server runs every tool call through the
# in-process ``cli`` package: content that changes CLI behavior changes the
# behavior a connected MCP client observes, so it participates in MCP identity
# and fresh-process proof.
MCP_CONTENT_PATHS: Tuple[str, ...] = (
    "mcp_server",
    "cli",
    "bin/cartopian-mcp",
    "bin/cartopian-mcp.cmd",
)
# The closed tool-shipped surface set an install materializes; it mirrors
# ``cli.install_workflow.TOOL_SHIPPED`` targets (parity is asserted in
# ``tests/test_install_version_projection.py``) so the runtime identifies the
# same content the installer wrote.
INSTALLED_CONTENT_PATHS: Tuple[str, ...] = (
    "protocol",
    "templates",
    "skills",
    "wrappers",
    "cli",
    "mcp_server",
    "bin/cartopian",
    "bin/cartopian.cmd",
    "bin/cartopian-mcp",
    "bin/cartopian-mcp.cmd",
    "install-cartopian.md",
    "scripts/install.py",
    "CHANGELOG.md",
)
_TRANSIENT_NAMES = frozenset((".DS_Store", "__pycache__"))
_INSTALL_STATE_FILE = "install-update-state.json"
_INSTALL_STATE_MAX_BYTES = 2 * 1024 * 1024
_MARKER_MAX_BYTES = 4096
_REF_MAX_LENGTH = 200
# A release tag as the installer resolves it (``v1.6.6``, ``v2.0.0-rc.1``).
# Branch refs, commit ids, and malformed markers are deliberately excluded.
_RELEASE_TAG = re.compile(r"^v\d+(?:\.\d+)*(?:[-+][0-9A-Za-z][0-9A-Za-z.+-]*)?$")
# The only non-tag ref ``scripts/install.py`` resolves and records: the branch
# it falls back to when no release has been published.
_RECEIPT_BRANCH_REFS = frozenset(("main",))
_CONTENT_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_INSTALLED_CONTENT_KIND = "installed_content"
# Fields the state contract requires on every peer identity row. A row that
# omits one is not the record this runtime knows how to read.
_STATE_ROW_FIELDS: Tuple[str, ...] = (
    "kind",
    "value",
    "state",
    "authority",
    "verification",
)
# Install-state evidence classes the runtime distinguishes.
INSTALL_STATE_ABSENT = "absent"
INSTALL_STATE_UNUSABLE = "unusable"
INSTALL_STATE_PRESENT = "present"
_STATE_ABSENT = INSTALL_STATE_ABSENT
_STATE_UNUSABLE = INSTALL_STATE_UNUSABLE
_STATE_PRESENT = INSTALL_STATE_PRESENT
# Verdicts for a persisted restart candidate read through the shared
# content-binding rule (:func:`content_bound_restart_candidate`).
RESTART_EVIDENCE_ABSENT = "absent"
RESTART_EVIDENCE_UNUSABLE = "unusable"
RESTART_EVIDENCE_UNBOUND = "unbound"
RESTART_EVIDENCE_BOUND = "bound"
# The verdicts that mean persisted evidence exists but was refused. They are
# not absence: something was written here, this runtime declined to read it,
# and whatever wrote it may have changed the MCP surface. Every consumer asks
# this one question through :func:`restart_evidence_withheld` so no surface can
# re-derive the distinction from its own string comparison.
RESTART_EVIDENCE_WITHHELD: Tuple[str, ...] = (
    RESTART_EVIDENCE_UNUSABLE,
    RESTART_EVIDENCE_UNBOUND,
)


def _read_marker(path: Path) -> Optional[str]:
    try:
        if not path.is_file() or path.stat().st_size > _MARKER_MAX_BYTES:
            return None
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return value or None


def _ref_marker(root: Path, name: str) -> Optional[str]:
    """Return a single-token ref marker, or ``None`` when it is unusable.

    ``VERSION`` and ``RELEASE_VERSION`` are single-line, single-token files
    (``protocol/INSTALL_VERIFICATION.md`` section 7). Empty, multi-line,
    multi-token, oversized, or undecodable content is malformed input and must
    stay unknown rather than be guessed at.
    """
    value = _read_marker(root / name)
    if value is None or len(value.splitlines()) != 1:
        return None
    token = value.strip()
    if not token or len(token) > _REF_MAX_LENGTH:
        return None
    if not token.isprintable() or any(char.isspace() for char in token):
        return None
    return token


def is_release_tag(value: Optional[str]) -> bool:
    """Return whether ``value`` is a release tag as the installer records one.

    Single source of truth for the release-ref grammar. Every writer that
    records a release claim and every reader that honors one must agree on it;
    when they diverge, a persisted row asserts a release version that no reader
    will accept, and the disagreement surfaces to the operator as an
    unexplained ``unknown``.
    """
    return value is not None and _RELEASE_TAG.match(value) is not None


def is_receipt_ref(value: Optional[str]) -> bool:
    """Return whether ``value`` is a ref the ``VERSION`` receipt may carry.

    Single source of truth for the receipt grammar: release tags plus the
    literal ``main`` fallback. ``scripts/install.py`` writes the marker only
    through this predicate and ``_install_receipt`` reads it through the same
    one, so a token outside the grammar genuinely cannot be an installer
    receipt.
    """
    return value is not None and (
        value in _RECEIPT_BRANCH_REFS or is_release_tag(value)
    )


def observed_release_marker(root: Path) -> Tuple[Optional[str], str]:
    """Report the release marker observed at ``root``, for explanation only.

    ``release_version`` withholds a claim for anything outside the release-tag
    grammar, which is correct but says nothing about *what* was installed. This
    returns the token actually on disk together with why it was not honored, so
    an operator reading ``unknown`` can see the cause without reading this
    module. The token is provenance for a withheld claim, never a claim itself:
    callers must not promote it to a release version.
    """
    for name in ("RELEASE_VERSION", "VERSION"):
        path = root / name
        if not path.is_symlink() and not path.exists():
            continue
        token = _ref_marker(root, name)
        if token is None:
            return None, "malformed"
        if is_release_tag(token):
            return token, "release-tag"
        if token in _RECEIPT_BRANCH_REFS:
            return token, "branch-ref"
        return token, "non-release-ref"
    return None, "absent"


def _git(root: Path, *args: str) -> Optional[str]:
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value or None


def _content_entries(path: Path) -> Tuple[List[Tuple[str, bytes]], bool]:
    entries: List[Tuple[str, bytes]] = []
    if path.is_symlink():
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError):
            return entries, False
    if path.is_file():
        try:
            entries.append(("@file", path.read_bytes()))
        except OSError:
            return entries, False
        return entries, True
    if not path.is_dir():
        return entries, False
    try:
        children = sorted(path.rglob("*"), key=lambda item: item.as_posix())
    except OSError:
        return entries, False
    for child in children:
        relative = child.relative_to(path)
        if any(part in _TRANSIENT_NAMES for part in relative.parts):
            continue
        if child.suffix in (".pyc", ".pyo"):
            continue
        try:
            if not child.is_file():
                continue
            payload = child.read_bytes()
        except OSError:
            return entries, False
        entries.append((relative.as_posix(), payload))
    return entries, True


def _content_digest(
    root: Path, paths: Sequence[str]
) -> Tuple[Optional[str], bool]:
    """Digest a closed surface set; identity is ``None`` unless it is complete."""
    digest = hashlib.sha256()
    found = False
    complete = True
    for relative in paths:
        entries, path_complete = _content_entries(root / relative)
        if not path_complete or not entries:
            complete = False
        for nested, payload in entries:
            found = True
            name = f"{relative}/{nested}".encode("utf-8")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    identity = "sha256:" + digest.hexdigest() if found and complete else None
    return identity, complete


def _digest_field(row: Mapping[str, Any], name: str) -> Tuple[Optional[str], bool]:
    """Return ``(identity, well_formed)`` for one recorded digest field."""
    value = row.get(name)
    if value is None:
        return None, True
    if isinstance(value, str) and _CONTENT_DIGEST.match(value):
        return value, True
    return None, False


def install_record_identities(
    record: Any,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Apply the record-compatibility and installed-content authority.

    This is the single gate every consumer that can strengthen installed,
    runtime, or restart state reads persisted install evidence through, so one
    record cannot be read as proof by one consumer and refused by another.

    Returns ``(status, installed_identity, mcp_identity)``:

    ``unusable``
        The record is not a mapping, carries an unsupported or future schema
        identity/version, does not account for the installed-content identity
        exactly once, attributes it to another identity's authority, records a
        digest outside the closed grammar, or carries a row whose own state and
        verification do not positively attest the content it names. Such a
        record is evidence that this runtime cannot interpret the install
        state, or evidence that the recorded install was never proven; either
        way it fails closed: it can never strengthen a verdict, and no weaker
        evidence class — a sibling restart row, a release receipt, or a
        surface observation — may be substituted for it.
    ``present``
        A compatible record attesting one well-formed installed-content
        identity (and, when recorded, its MCP subset identity).
    """
    if not isinstance(record, Mapping):
        return INSTALL_STATE_UNUSABLE, None, None
    if record.get("schema_identity") != SCHEMA_IDENTITY or (
        not supported_record_schema_version(record.get("record_schema_version"))
    ):
        return INSTALL_STATE_UNUSABLE, None, None
    versions = record.get("versions")
    if not isinstance(versions, list) or not all(
        isinstance(item, Mapping) for item in versions
    ):
        return INSTALL_STATE_UNUSABLE, None, None
    rows = [
        item for item in versions if item.get("kind") == _INSTALLED_CONTENT_KIND
    ]
    if len(rows) != 1:
        return INSTALL_STATE_UNUSABLE, None, None
    row = rows[0]
    if any(field not in row for field in _STATE_ROW_FIELDS):
        return INSTALL_STATE_UNUSABLE, None, None
    if row.get("authority") != identity_contract()["installed_content"]["authority"]:
        return INSTALL_STATE_UNUSABLE, None, None
    if not positive_identity_fact(
        _INSTALLED_CONTENT_KIND, row.get("state"), row.get("verification")
    ):
        # The row says of itself that the recorded content is unknown, drifted,
        # unproven, or described outside the closed vocabulary. Matching such a
        # row proves only that the content still is what was never verified.
        return INSTALL_STATE_UNUSABLE, None, None
    installed_identity, installed_ok = _digest_field(row, "installed_identity")
    mcp_identity, mcp_ok = _digest_field(row, "mcp_identity")
    value, value_ok = _digest_field(row, "value")
    if not (installed_ok and mcp_ok and value_ok):
        return INSTALL_STATE_UNUSABLE, None, None
    if installed_identity is None or value is None:
        # A record that attests only a subset cannot support a claim about the
        # whole shipped surface set the runtime reports as its revision, and a
        # positive row must carry the identity value it claims to have proven.
        return INSTALL_STATE_UNUSABLE, None, None
    return INSTALL_STATE_PRESENT, installed_identity, mcp_identity


def install_state_evidence(root: Path) -> "OrderedDict[str, Any]":
    """Read persisted install evidence for ``root`` through the same gate.

    ``status`` is ``absent`` when no coordinated install or update left a
    record here — other evidence (the installer receipt plus a complete surface
    digest) may still apply — and otherwise the verdict
    :func:`install_record_identities` reaches for the parsed record. ``record``
    is exposed only for a ``present`` verdict, so a consumer cannot read
    sibling facts out of a record this runtime has refused.
    """
    path = root / _INSTALL_STATE_FILE
    try:
        if not path.is_symlink() and not path.exists():
            return _install_evidence(INSTALL_STATE_ABSENT, None, None, None)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > _INSTALL_STATE_MAX_BYTES
        ):
            return _install_evidence(INSTALL_STATE_UNUSABLE, None, None, None)
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _install_evidence(INSTALL_STATE_UNUSABLE, None, None, None)
    return install_record_evidence(record)


def install_record_evidence(record: Any) -> "OrderedDict[str, Any]":
    """Apply the record gate to an already-parsed record.

    Consumers that read the state file themselves reach the same verdict, and
    the same exposed facts, as :func:`install_state_evidence`. ``absent`` is a
    file-level fact, so it is never reached from a record that was parsed.
    """
    status, installed_identity, mcp_identity = install_record_identities(record)
    return _install_evidence(
        status,
        record if status == INSTALL_STATE_PRESENT else None,
        installed_identity,
        mcp_identity,
    )


def _install_evidence(
    status: str,
    record: Any,
    installed_identity: Optional[str],
    mcp_identity: Optional[str],
) -> "OrderedDict[str, Any]":
    return OrderedDict(
        (
            ("status", status),
            ("record", copy.deepcopy(record) if record is not None else None),
            ("installed_identity", installed_identity),
            ("mcp_identity", mcp_identity),
        )
    )


def mcp_identity_binds(
    recorded: Optional[str], observed: Optional[str]
) -> bool:
    """Report whether a recorded MCP identity answers for observed content.

    Persisted restart facts describe one MCP content identity. They may be read
    as evidence about content only when the record says which MCP content it
    attests, says it in the closed digest grammar, and names exactly the
    content the caller is projecting. A missing, malformed, substituted, or
    otherwise different identity leaves the record attesting something else,
    and an unobservable content identity leaves nothing to bind it to; both
    fail closed.
    """
    if not isinstance(recorded, str) or not _CONTENT_DIGEST.match(recorded):
        return False
    if not isinstance(observed, str) or not _CONTENT_DIGEST.match(observed):
        return False
    return recorded == observed


def content_bound_restart_candidate(
    evidence: Mapping[str, Any],
    *,
    observed_mcp_identity: Optional[str],
    client_id: str,
    states: Sequence[str] = ("required", "pending"),
) -> "OrderedDict[str, Any]":
    """Select the one persisted restart row that may speak about this content.

    This is the single content-binding rule every consumer of a persisted
    restart candidate reads through, so no surface can expose a process,
    instance, or freshness fact that the record's own MCP identity does not
    account for. A restart row is a sibling of the installed-content row: it
    carries neither record authority nor content authority of its own.

    Absence and refusal are separate evidence classes, and the difference is
    decided here rather than by each consumer: collapsing them lets a refused
    record be read as though nothing had ever been persisted, which is a
    fail-open claim. ``status`` is one of:

    ``absent``
        No record was persisted, or a compatible record holds no single restart
        row for this client. Nothing was written here for this caller to read,
        so this class stays benign: other evidence may still apply.
    ``unusable``
        Persisted evidence exists that this runtime refuses to read: the record
        did not pass the shared record gate, or its restart section cannot be
        resolved to one candidate. It is withheld entirely, and the caller must
        treat the MCP surface as restart-relevant — whatever wrote the record
        may have changed that surface — rather than as evidence of absence.
    ``unbound``
        Exactly one restart row exists, but the record's MCP identity does not
        name the content being projected. The row is withheld entirely — no
        prior process, instance, or freshness fact may be derived from it — and
        the caller must treat the MCP surface as restart-relevant rather than
        as evidence that nothing changed.
    ``bound``
        The row may be read as prior-process evidence about this content.

    ``row`` carries the selected restart row only for ``bound``.
    """
    status = evidence.get("status") if isinstance(evidence, Mapping) else None
    record = evidence.get("record") if isinstance(evidence, Mapping) else None
    if status == INSTALL_STATE_ABSENT:
        return _restart_candidate(RESTART_EVIDENCE_ABSENT, None)
    if status != INSTALL_STATE_PRESENT or not isinstance(record, Mapping):
        # A record the shared gate refused — or a ``present`` verdict without
        # the record such a verdict must expose, which this runtime cannot
        # interpret either. Persisted evidence exists; it is not absence.
        return _restart_candidate(RESTART_EVIDENCE_UNUSABLE, None)
    rows = record.get("restarts")
    if rows is None:
        return _restart_candidate(RESTART_EVIDENCE_ABSENT, None)
    if not isinstance(rows, list):
        # A restart section outside the contract's shape is persisted evidence
        # this runtime cannot resolve, not evidence that none was recorded.
        return _restart_candidate(RESTART_EVIDENCE_UNUSABLE, None)
    allowed = tuple(states)
    candidates = [
        dict(item)
        for item in rows
        if isinstance(item, Mapping)
        and item.get("state") in allowed
        and (
            item.get("client") == client_id
            or client_id == "unsupported"
            or len(rows) == 1
        )
    ]
    if not candidates:
        # A compatible record that persisted no restart candidate for this
        # caller: the ordinary steady state once a restart has been proven.
        return _restart_candidate(RESTART_EVIDENCE_ABSENT, None)
    if len(candidates) != 1:
        # Several rows claim this caller. The record does not say which prior
        # process a fresh-process proof would be measured against, so none of
        # them may be read — and the ambiguity is present evidence, not none.
        return _restart_candidate(RESTART_EVIDENCE_UNUSABLE, None)
    if not mcp_identity_binds(
        evidence.get("mcp_identity"), observed_mcp_identity
    ):
        return _restart_candidate(RESTART_EVIDENCE_UNBOUND, None)
    return _restart_candidate(RESTART_EVIDENCE_BOUND, candidates[0])


def restart_evidence_withheld(candidate: Any) -> bool:
    """Report whether persisted restart evidence exists but was refused.

    Every consumer of :func:`content_bound_restart_candidate` asks the shared
    authority this question instead of comparing status strings itself, so the
    absent-versus-refused distinction cannot drift between the installer
    workflow, the MCP projection, and the public verifier. A refused verdict
    exposes no row, but it is positive evidence that the MCP surface must be
    treated as restart-relevant.

    A candidate mapping or a bare status token is accepted, so a consumer that
    carried the verdict forward through a persisted record reaches the same
    answer as one still holding the candidate itself.
    """
    status = (
        candidate.get("status")
        if isinstance(candidate, Mapping)
        else candidate
    )
    return status in RESTART_EVIDENCE_WITHHELD


def _restart_candidate(
    status: str, row: Optional[Dict[str, Any]]
) -> "OrderedDict[str, Any]":
    return OrderedDict((("status", status), ("row", row)))


def _install_receipt(root: Path) -> Tuple[Optional[str], str]:
    """Return ``(ref, state)`` for the installer's ``VERSION`` receipt.

    ``scripts/install.py`` records exactly the ref it resolved and installed: a
    release tag, or the literal ``main`` it falls back to when no release has
    been published (``protocol/INSTALL_VERIFICATION.md`` section 7). A token
    outside that grammar is not a ref this installer can have written, so it is
    malformed input that carries no provenance rather than an unrecognized but
    trusted receipt.
    """
    path = root / "VERSION"
    if not path.is_symlink() and not path.exists():
        return None, "absent"
    token = _ref_marker(root, "VERSION")
    if token is None or not is_receipt_ref(token):
        return None, "malformed"
    return token, "known"


def mcp_content_identity(root: Path) -> Dict[str, Any]:
    """Observe the MCP digest without strengthening provenance verification."""
    logical_root = Path(os.path.abspath(root))
    identity, complete = _content_digest(logical_root, MCP_CONTENT_PATHS)
    return {
        "identity": identity,
        "state": "unverified" if complete else "incomplete",
        "verification": "unverified",
        "completeness": "complete" if complete else "incomplete",
        "authority": identity_contract()["installed_content"]["authority"],
        "paths": list(MCP_CONTENT_PATHS),
    }


def release_version(root: Path) -> Dict[str, Any]:
    """Inspect release metadata recorded for this root, in authority order.

    Two markers carry maintainer release metadata: ``RELEASE_VERSION``, authored
    in a maintained tree, and ``VERSION``, which ``scripts/install.py`` writes
    with the release ref it resolved for the install (``protocol/
    INSTALL_VERIFICATION.md`` section 7) — the only release metadata a copy-mode
    install carries. Only a release-tag-shaped ref answers this question: a
    branch ref such as ``main``, a commit id, or a malformed marker leaves the
    release version unknown. Content identity is a separate authority and never
    substitutes for a release claim.
    """
    for name, attribution in (
        ("RELEASE_VERSION", "release-metadata"),
        ("VERSION", "installed-release-marker"),
    ):
        value = _ref_marker(root, name)
        if not is_release_tag(value):
            continue
        return {
            "value": value,
            "state": "known",
            "authority": identity_contract()["release_version"]["authority"],
            "verification": "verified",
            "attribution": attribution,
            "observed_ref": value,
            "observed_ref_state": "release-tag",
        }
    observed_ref, observed_ref_state = observed_release_marker(root)
    return {
        "value": None,
        "state": "unknown",
        "authority": identity_contract()["release_version"]["authority"],
        "verification": "unknown",
        "attribution": "unavailable",
        "observed_ref": observed_ref,
        "observed_ref_state": observed_ref_state,
    }


def _mcp_verification(
    content_verification: str,
    *,
    complete: bool,
    comparable: bool,
    recorded: Optional[str],
    observed: Optional[str],
) -> str:
    """Derive the MCP-scoped verdict without inheriting a wider one.

    The MCP subset carries its own claim: a recorded MCP identity proves or
    contradicts it directly, and drift proven somewhere else in the shipped
    surface set leaves it unproven rather than either verdict. Reporting a
    wider ``dirty`` here would demand a restart that cannot repair non-MCP
    content; reporting a wider ``verified`` would restate an unproven claim.
    """
    if not complete:
        return "unverified"
    if comparable and recorded is not None:
        return "verified" if recorded == observed else "dirty"
    if content_verification == "dirty":
        return "unverified"
    return content_verification


def installed_content(root: Path) -> Dict[str, Any]:
    """Identify exact loaded/materialized content without claiming a release.

    A source checkout answers with git provenance. A materialized install — the
    primary end-user shape — carries no ``.git``, so its identity is the digest
    of the closed tool-shipped surface set, and its verification rests on the
    installation evidence the installer left behind: the identity recorded in
    ``install-update-state.json`` when a coordinated install/update wrote one,
    otherwise the ``VERSION`` receipt plus a complete surface digest. Content
    that contradicts recorded install evidence is ``dirty``; content that is
    incomplete, that carries no installation evidence at all, or whose recorded
    evidence this runtime cannot interpret, stays ``unverified``. Verification
    never claims the content matches an upstream release — that is
    ``release_version``'s separate authority.

    The verified claim is bound to the same surface set the reported revision
    covers, so drift in any shipped surface — not only the MCP subset — is
    reported. The MCP-scoped facts stay separately attributed, because the
    restart projection asks the narrower question of whether the connected
    process loaded the installed MCP content.
    """
    logical_root = Path(os.path.abspath(root))
    loaded_root = logical_root.resolve()
    revision = _git(loaded_root, "rev-parse", "HEAD")
    status = _git(loaded_root, "status", "--porcelain")
    recorded_ref, receipt_state = _install_receipt(logical_root)
    if (loaded_root / ".git").exists():
        materialization = "source-checkout"
    else:
        materialization = "copy"

    mcp = mcp_content_identity(logical_root)
    mcp_complete = mcp["completeness"] == "complete"
    revision_attribution = "git-revision" if revision is not None else "unavailable"
    # A git verdict already speaks for the whole loaded tree; only the
    # digest path derives a separately attributed MCP verdict.
    mcp_verification: Optional[str] = None
    if revision is not None and status:
        verification = "dirty"
        evidence = "git-worktree-modified"
    elif revision is not None:
        verification = "verified"
        evidence = "git-clean-checkout"
    else:
        identity, complete = _content_digest(logical_root, INSTALLED_CONTENT_PATHS)
        evidence = install_state_evidence(logical_root)
        state = evidence["status"]
        recorded_identity = evidence["installed_identity"]
        recorded_mcp = evidence["mcp_identity"]
        comparable = state == _STATE_PRESENT
        mcp_comparable = state == _STATE_PRESENT and recorded_mcp is not None
        if identity is not None:
            revision = identity
            revision_attribution = "installed-content-digest"
        if not complete or not mcp_complete:
            verification = "unverified"
            evidence = "incomplete-installed-content"
        elif state == _STATE_UNUSABLE:
            verification = "unverified"
            evidence = "install-state-unusable"
        elif comparable and recorded_identity != identity:
            verification = "dirty"
            evidence = "install-state-mismatch"
        elif mcp_comparable and recorded_mcp != mcp["identity"]:
            verification = "dirty"
            evidence = "install-state-mcp-mismatch"
        elif comparable:
            verification = "verified"
            evidence = "install-state-record"
        elif recorded_ref is not None:
            # No digest record at all: the receipt plus a complete surface
            # set is the weaker evidence class an ordinary install carries.
            verification = "verified"
            evidence = "recorded-install-content"
        elif receipt_state == "malformed":
            verification = "unverified"
            evidence = "malformed-install-receipt"
        else:
            verification = "unverified"
            evidence = "no-install-record"
        mcp_verification = _mcp_verification(
            verification,
            complete=mcp_complete,
            comparable=mcp_comparable,
            recorded=recorded_mcp,
            observed=mcp["identity"],
        )
    if mcp_verification is None:
        mcp_verification = verification
    return {
        "revision": revision,
        "revision_attribution": revision_attribution,
        "recorded_ref": recorded_ref,
        "materialization": materialization,
        "verification": verification,
        "verification_evidence": evidence,
        "state": verification,
        "authority": identity_contract()["installed_content"]["authority"],
        "loaded_root": str(loaded_root),
        "attribution": "runtime-inspection",
        "mcp_identity": mcp["identity"],
        "mcp_state": mcp_verification if mcp_complete else "incomplete",
        "mcp_verification": mcp_verification,
        "mcp_completeness": mcp["completeness"],
    }


def running_server(
    content: Dict[str, Any],
    *,
    process_id: Optional[int] = None,
    instance_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Identify the connected process and the content it loaded."""
    verification = content.get("mcp_verification", "unknown")
    completeness = content.get("mcp_completeness", "unknown")
    if completeness != "complete":
        state = "unknown"
    elif verification == "verified":
        state = "current"
    else:
        state = "unknown"
    return {
        "process_id": process_id if process_id is not None else os.getpid(),
        "instance_id": instance_id,
        "loaded_content": {
            "revision": content["revision"],
            "loaded_root": content["loaded_root"],
            "verification": content["verification"],
            "mcp_identity": content.get("mcp_identity"),
            "mcp_verification": verification,
            "mcp_completeness": completeness,
        },
        "state": state,
        "authority": identity_contract()["running_server"]["authority"],
        "attribution": "connected-process",
    }


_CONNECTED_RUNNING_SERVER: Optional[Dict[str, Any]] = None


def set_connected_running_server(fact: Optional[Mapping[str, Any]]) -> None:
    """Register (or clear) the fact for the MCP process this code runs in.

    Cartopian's MCP tools execute inside the connected server process, so a
    tool that resolves peer identities can report that process instead of the
    ``not-connected-context`` projection. Only the serving process registers a
    fact; a plain CLI process registers nothing and keeps that projection.
    """
    global _CONNECTED_RUNNING_SERVER
    _CONNECTED_RUNNING_SERVER = (
        copy.deepcopy(dict(fact)) if fact is not None else None
    )


def connected_running_server() -> Optional[Dict[str, Any]]:
    """Return the registered connected-process fact, if this is one."""
    if _CONNECTED_RUNNING_SERVER is None:
        return None
    return copy.deepcopy(_CONNECTED_RUNNING_SERVER)


def version_identities(
    root: Path,
    *,
    project_schema: Optional[Dict[str, Any]] = None,
    mcp_protocol_version: Optional[str] = None,
    include_running_server: bool = False,
    running_server_fact: Optional[Dict[str, Any]] = None,
) -> "OrderedDict[str, Dict[str, Any]]":
    """Return deterministic structured peer identities for this context."""
    content = installed_content(root)
    installed_record = dict(content)
    if running_server_fact is None:
        running_server_fact = connected_running_server()
    if not include_running_server and running_server_fact is None:
        for field in (
            "mcp_identity",
            "mcp_state",
            "mcp_verification",
            "mcp_completeness",
        ):
            installed_record.pop(field, None)
    schema_record = project_schema or {
        "value": None,
        "target": None,
        "state": "unknown",
        "authority": identity_contract()["project_schema_version"]["authority"],
        "verification": "unknown",
        "attribution": "unavailable",
    }
    records: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    records["release_version"] = release_version(root)
    records["installed_content"] = installed_record
    records["project_schema_version"] = schema_record
    if include_running_server or running_server_fact is not None:
        records["running_server"] = (
            dict(running_server_fact)
            if running_server_fact is not None
            else running_server(content)
        )
    else:
        records["running_server"] = {
            "process_id": None,
            "loaded_content": None,
            "state": "unknown",
            "authority": identity_contract()["running_server"]["authority"],
            "attribution": "not-connected-context",
        }
    records["mcp_protocol_version"] = {
        "value": mcp_protocol_version,
        "state": "supported" if mcp_protocol_version is not None else "unknown",
        "authority": identity_contract()["mcp_protocol_version"]["authority"],
        "verification": (
            "verified" if mcp_protocol_version is not None else "unknown"
        ),
        "attribution": (
            "wire-handshake" if mcp_protocol_version is not None else "unavailable"
        ),
    }
    return records
