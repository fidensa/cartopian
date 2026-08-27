"""Typed machine-created assignment-input payloads.

An assignment prompt carries two kinds of content with different trust
semantics:

- the *instruction channel* — PM-authored and composer-assembled prose that
  directs the assignee, which contamination and deidentification validation
  must inspect; and
- *input payloads* — the exact current bytes of an assignment input the
  assignee cannot read at its governance-scoped location (the existing
  project deliverable this assignment updates, or an upstream dependency's
  deliverable it builds on). Payload contents are data the assignee consumes,
  not authored instructions: they may legitimately contain fenced JSON,
  Cartopian identifiers, governance-looking headings, or instruction-like
  prose, and they must round-trip byte-for-byte.

A payload is a fenced block whose info string carries the ``cartopian-input``
marker and the payload's machine binding::

    ````cartopian-input channel=existing-deliverable logical=<pct-encoded> bytes=<n> sha256=<hex>
    <exact resource text>
    ````

Only the composer and the mediated writer create these blocks. The marker
alone grants nothing: hand-authored text cannot declare itself a trusted
payload, because every surface that honors the exemption verifies the block
against its binding — composition verifies against the machine-built payload
manifest, the mediated writer refuses authored declarations outright, and
handoff preflight structurally extracts each expected payload and verifies
its SHA-256 digest and byte count against the resource on disk. A block that
does not verify is a validation failure, never a trusted payload.

Stdlib only. Read-only: nothing here touches project files.
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Sequence

from cli.markdown_fences import FenceTracker, opening_info

MARKER = "cartopian-input"

CHANNEL_EXISTING = "existing-deliverable"
CHANNEL_DEPENDENCY = "dependency-deliverable"

#: The one prompt section each payload channel may appear in.
CHANNEL_SECTIONS = {
    CHANNEL_EXISTING: "Existing deliverable input",
    CHANNEL_DEPENDENCY: "Upstream contract input",
}

_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_INFO_FIELDS = ("channel", "logical", "bytes", "sha256")


def payload_binding(channel: str, logical: str, text: str) -> Dict[str, Any]:
    """The machine binding for one payload: channel, logical, bytes, digest."""
    data = text.encode("utf-8")
    return {
        "channel": channel,
        "logical": logical,
        "content_bytes": len(data),
        "content_sha256": hashlib.sha256(data).hexdigest(),
    }


def render_payload_block(channel: str, logical: str, text: str) -> str:
    """Render one machine-created payload block for a prompt section.

    The fence is longer than any backtick run in the payload, so embedded
    fenced blocks nest as content under the corrected fence rules. When the
    payload does not end with a newline the encoder adds one before the
    closing fence; the declared byte count disambiguates on extraction, so
    trailing whitespace and the final newline round-trip exactly.
    """
    if channel not in CHANNEL_SECTIONS:
        raise ValueError(f"unknown payload channel: {channel!r}")
    binding = payload_binding(channel, logical, text)
    longest = max(
        (len(run.group(0)) for run in _BACKTICK_RUN_RE.finditer(text)),
        default=0,
    )
    fence = "`" * max(4, longest + 1)
    info = (
        f"{MARKER} channel={channel} "
        f"logical={urllib.parse.quote(logical, safe='')} "
        f"bytes={binding['content_bytes']} "
        f"sha256={binding['content_sha256']}"
    )
    body = text if text.endswith("\n") else text + "\n"
    return f"{fence}{info}\n{body}{fence}"


def _parse_info(info: str) -> Optional[Dict[str, Any]]:
    """Parse a payload info string into its binding, or None when malformed."""
    tokens = info.split()
    if not tokens or tokens[0] != MARKER:
        return None
    fields: Dict[str, str] = {}
    for token in tokens[1:]:
        key, sep, value = token.partition("=")
        if not sep or key in fields:
            return None
        fields[key] = value
    if set(fields) != set(_INFO_FIELDS):
        return None
    if fields["channel"] not in CHANNEL_SECTIONS:
        return None
    if not fields["bytes"].isdigit():
        return None
    if not _SHA256_HEX_RE.fullmatch(fields["sha256"]):
        return None
    return {
        "channel": fields["channel"],
        "logical": urllib.parse.unquote(fields["logical"]),
        "declared_bytes": int(fields["bytes"]),
        "declared_sha256": fields["sha256"],
    }


def extract_payload_blocks(prompt_text: str) -> List[Dict[str, Any]]:
    """Structurally extract every declared payload block from a prompt.

    Each entry carries the declared binding, the enclosing H2 section name,
    the opener line number, the exact extracted ``content`` when the block
    verifies against its declared byte count and SHA-256 digest, ``verified``
    (self-consistency of content against the declared binding), and
    ``error`` (``malformed-declaration``, ``unterminated``, or
    ``binding-mismatch``) when it does not.
    """
    entries: List[Dict[str, Any]] = []
    tracker = FenceTracker()
    section = "(title)"
    lines = prompt_text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped_line = line.rstrip("\r\n")
        was_in_fence = tracker.in_fence
        is_delimiter = tracker.feed(stripped_line)
        if not tracker.in_fence and not was_in_fence:
            heading = _H2_RE.match(stripped_line)
            if heading:
                section = heading.group(1).strip()
        if not (is_delimiter and tracker.in_fence and not was_in_fence):
            index += 1
            continue
        info = opening_info(stripped_line) or ""
        if not info.startswith(MARKER):
            index += 1
            continue
        entry: Dict[str, Any] = {
            "channel": None,
            "logical": None,
            "declared_bytes": None,
            "declared_sha256": None,
            "content": None,
            "verified": False,
            "section": section,
            "line_number": index + 1,
            "error": None,
            "_span": None,
        }
        binding = _parse_info(info)
        if binding is not None:
            entry.update(binding)
        else:
            entry["error"] = "malformed-declaration"
        body_parts: List[str] = []
        closed = False
        scan = index + 1
        while scan < len(lines):
            candidate = lines[scan].rstrip("\r\n")
            if tracker.feed(candidate) and not tracker.in_fence:
                closed = True
                break
            body_parts.append(lines[scan])
            scan += 1
        if not closed:
            entry["error"] = entry["error"] or "unterminated"
            entries.append(entry)
            break
        entry["_span"] = (index, scan + 1)
        if entry["error"] is None:
            region = "".join(body_parts)
            content = _resolve_content(region, entry["declared_bytes"])
            if content is not None and (
                hashlib.sha256(content.encode("utf-8")).hexdigest()
                == entry["declared_sha256"]
            ):
                entry["content"] = content
                entry["verified"] = True
            else:
                entry["error"] = "binding-mismatch"
        entries.append(entry)
        index = scan + 1
    return entries


def _resolve_content(region: str, declared_bytes: int) -> Optional[str]:
    """Resolve the exact payload from the fenced region via its byte count.

    The encoder appends one newline when the payload lacks a trailing one, so
    the region is either the payload itself or the payload plus ``\\n``.
    """
    if len(region.encode("utf-8")) == declared_bytes:
        return region
    if region.endswith("\n"):
        trimmed = region[:-1]
        if len(trimmed.encode("utf-8")) == declared_bytes:
            return trimmed
    return None


def strip_payload_blocks(prompt_text: str) -> str:
    """The prompt's instruction channel: text with verified payloads removed.

    Only blocks that verify against their declared binding are removed — an
    unverifiable declaration stays in place so instruction-channel validation
    still sees (and fails on) its content.
    """
    spans = [
        entry["_span"]
        for entry in extract_payload_blocks(prompt_text)
        if entry["verified"] and entry["_span"] is not None
    ]
    if not spans:
        return prompt_text
    lines = prompt_text.splitlines(keepends=True)
    kept: List[str] = []
    removed = set()
    for start, end in spans:
        removed.update(range(start, end))
    for index, line in enumerate(lines):
        if index not in removed:
            kept.append(line)
    return "".join(kept)


def verify_bound_payload(
    prompt_text: str, channel: str, logical: str, data: bytes
) -> Dict[str, str]:
    """Verify that the prompt carries exactly one payload bound to a resource.

    ``data`` is the resource's current on-disk bytes. Returns ``state``
    ``bound`` when exactly one payload declares this channel and logical and
    its content verifies byte-for-byte against ``data``; otherwise
    ``missing``, ``duplicate``, or ``mismatch`` with a diagnostic detail.
    """
    matches = [
        entry
        for entry in extract_payload_blocks(prompt_text)
        if entry["channel"] == channel and entry["logical"] == logical
    ]
    if not matches:
        return {
            "state": "missing",
            "detail": f"no {channel} payload bound to {logical}",
        }
    if len(matches) > 1:
        return {
            "state": "duplicate",
            "detail": f"{len(matches)} {channel} payloads bound to {logical}",
        }
    entry = matches[0]
    if not entry["verified"]:
        return {
            "state": "mismatch",
            "detail": (
                f"the {channel} payload for {logical} does not verify against "
                f"its declared binding ({entry['error']})"
            ),
        }
    expected_sha = hashlib.sha256(data).hexdigest()
    if (
        entry["declared_sha256"] != expected_sha
        or entry["declared_bytes"] != len(data)
        or entry["content"].encode("utf-8") != data
    ):
        return {
            "state": "mismatch",
            "detail": (
                f"the {channel} payload for {logical} does not match the "
                "current resource content"
            ),
        }
    return {"state": "bound", "detail": "payload verifies against the resource"}


def audit_payloads(
    prompt_text: str, expected: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Audit every payload declaration in a prompt against the expected set.

    ``expected`` entries carry ``channel``, ``logical``, ``content_sha256``,
    and ``content_bytes`` for each machine-resolved assignment input. Every
    declared payload must verify self-consistently and bind to exactly one
    expected input with matching digest and byte count; anything else — a
    hand-authored declaration, a payload for a resource that is not an
    assignment input, a mutated or duplicated payload — is a problem.
    """
    problems: List[str] = []
    by_key = {
        (item["channel"], item["logical"]): item for item in expected
    }
    seen: set = set()
    for entry in extract_payload_blocks(prompt_text):
        where = f"line {entry['line_number']}"
        if entry["error"] is not None:
            problems.append(
                f"payload declaration at {where} is invalid: {entry['error']}"
            )
            continue
        key = (entry["channel"], entry["logical"])
        expected_item = by_key.get(key)
        if expected_item is None:
            problems.append(
                f"payload at {where} is bound to {entry['logical']!r}, which "
                "is not a machine-resolved assignment input"
            )
            continue
        if key in seen:
            problems.append(
                f"payload at {where} duplicates the {entry['channel']} "
                f"payload for {entry['logical']!r}"
            )
            continue
        seen.add(key)
        if (
            entry["declared_sha256"] != expected_item["content_sha256"]
            or entry["declared_bytes"] != expected_item["content_bytes"]
        ):
            problems.append(
                f"payload at {where} for {entry['logical']!r} does not match "
                "the current resource content"
            )
    return {"ok": not problems, "problems": problems}
