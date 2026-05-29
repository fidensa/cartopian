#!/usr/bin/env python3
"""Real-transport verification for MCP server stdio dual framing (TASK-01-005).

Unlike ``tests/mcp_server/test_server.py`` — which drives ``server.run()`` over
in-memory ``BytesIO`` streams — this harness launches the real ``bin/cartopian-mcp``
entrypoint as a subprocess and speaks JSON-RPC to it over genuine OS stdio pipes,
the same channel a real MCP client (Claude Code / Claude Desktop / Codex / Gemini
CLI) uses. It exercises a full client session (initialize → initialized
notification → tools/list → prompts/list → resources/list → ping) in:

  1. Content-Length-header framing (``Content-Length: <n>\\r\\n\\r\\n<payload>``)
  2. Newline-delimited JSON-RPC
  3. Content-Length framing with LF-only (``\\n``) header terminators
  4. Mixed framing in one session (framed request then newline request)
  5. A framed payload carrying an embedded ``\\n`` (byte-exact read check)

Each scenario asserts: the process exits 0, every request gets exactly one
correctly-framed response, the notification gets none, and no parse/framing
error response is emitted. Run with ``python3 scripts/verify_stdio_framing.py``;
exit code 0 means all scenarios passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRY = REPO_ROOT / "bin" / "cartopian-mcp"


# ---------------------------------------------------------------------------
# Framing encoders
# ---------------------------------------------------------------------------

def frame(message: Dict[str, Any], eol: bytes = b"\r\n") -> bytes:
    body = json.dumps(message).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + eol + eol + body


def newline(message: Dict[str, Any]) -> bytes:
    return json.dumps(message).encode("utf-8") + b"\n"


# ---------------------------------------------------------------------------
# Response parser — handles a stream that may interleave both framings
# ---------------------------------------------------------------------------

def parse_responses(out: bytes) -> List[Dict[str, Any]]:
    """Parse a byte stream of mixed Content-Length / newline-delimited messages."""
    messages: List[Dict[str, Any]] = []
    rest = out
    while rest:
        # Skip stray blank lines between messages.
        if rest[:2] == b"\r\n":
            rest = rest[2:]
            continue
        if rest[:1] == b"\n":
            rest = rest[1:]
            continue
        if rest.startswith(b"Content-Length:"):
            # Locate the header/body separator (accept CRLFCRLF or LFLF).
            sep_crlf = rest.find(b"\r\n\r\n")
            sep_lf = rest.find(b"\n\n")
            candidates = [s for s in (sep_crlf, sep_lf) if s != -1]
            sep_idx = min(candidates)
            sep_len = 4 if sep_idx == sep_crlf and sep_crlf != -1 else 2
            header_blob = rest[:sep_idx]
            length = None
            for hline in header_blob.replace(b"\r\n", b"\n").split(b"\n"):
                name, _, value = hline.partition(b":")
                if name.strip().lower() == b"content-length":
                    length = int(value.strip())
            assert length is not None, f"no Content-Length in header: {header_blob!r}"
            body_start = sep_idx + sep_len
            body = rest[body_start:body_start + length]
            messages.append(json.loads(body.decode("utf-8")))
            rest = rest[body_start + length:]
        else:
            line, _, rest = rest.partition(b"\n")
            if line.strip():
                messages.append(json.loads(line.decode("utf-8")))
    return messages


# ---------------------------------------------------------------------------
# Subprocess driver
# ---------------------------------------------------------------------------

def drive(raw_stdin: bytes) -> Tuple[int, bytes, bytes]:
    """Launch the real entrypoint over OS pipes; return (exit_code, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, str(ENTRY)],
        input=raw_stdin,
        cwd=str(REPO_ROOT),
        capture_output=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# A representative MCP client session
# ---------------------------------------------------------------------------

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "verify-stdio-framing", "version": "0"},
}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
TOOLS = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
PROMPTS = {"jsonrpc": "2.0", "id": 3, "method": "prompts/list", "params": {}}
RESOURCES = {"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}}
PING = {"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}}

# Five request ids that must each produce exactly one response; the
# notification (no id) must produce none.
SESSION = [INIT, INITIALIZED, TOOLS, PROMPTS, RESOURCES, PING]
EXPECTED_IDS = [1, 2, 3, 4, 5]


def assert_session(label: str, exit_code: int, stdout: bytes, stderr: bytes) -> None:
    errors: List[str] = []
    if exit_code != 0:
        errors.append(f"exit code {exit_code} (expected 0); stderr={stderr.decode('utf-8', 'replace')!r}")
    try:
        responses = parse_responses(stdout)
    except Exception as exc:  # noqa: BLE001 — surface any framing/parse break
        raise AssertionError(f"[{label}] response stream did not parse: {exc}\nraw={stdout!r}")
    ids = [r.get("id") for r in responses]
    if ids != EXPECTED_IDS:
        errors.append(f"response ids {ids} != expected {EXPECTED_IDS}")
    for r in responses:
        if "error" in r:
            errors.append(f"unexpected JSON-RPC error in response id={r.get('id')}: {r['error']}")
    # initialize result sanity
    init = next((r for r in responses if r.get("id") == 1), None)
    if init is None or init.get("result", {}).get("protocolVersion") != "2024-11-05":
        errors.append(f"initialize result malformed: {init}")
    if errors:
        raise AssertionError(f"[{label}] FAILED:\n  - " + "\n  - ".join(errors))
    print(f"[PASS] {label}: {len(responses)} responses, ids={ids}, no framing/parse errors")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_content_length_crlf() -> None:
    raw = b"".join(frame(m) for m in SESSION)
    code, out, err = drive(raw)
    assert out.startswith(b"Content-Length:"), f"expected framed responses, got {out[:40]!r}"
    assert_session("Content-Length framing (CRLF headers)", code, out, err)


def scenario_content_length_lf() -> None:
    raw = b"".join(frame(m, eol=b"\n") for m in SESSION)
    code, out, err = drive(raw)
    assert out.startswith(b"Content-Length:"), f"expected framed responses, got {out[:40]!r}"
    assert_session("Content-Length framing (LF-only headers)", code, out, err)


def scenario_newline() -> None:
    raw = b"".join(newline(m) for m in SESSION)
    code, out, err = drive(raw)
    assert not out.startswith(b"Content-Length:"), "newline session must not emit Content-Length"
    assert_session("Newline-delimited framing", code, out, err)


def scenario_mixed() -> None:
    # Alternate framings request-by-request; responses must mirror each request.
    raw = (
        frame(INIT)
        + newline(INITIALIZED)
        + frame(TOOLS)
        + newline(PROMPTS)
        + frame(RESOURCES)
        + newline(PING)
    )
    code, out, err = drive(raw)
    assert_session("Mixed framing in one session", code, out, err)


def scenario_embedded_newline_byte_exact() -> None:
    # A framed payload whose JSON value contains a literal newline must be read
    # by exact byte count — line iteration would truncate it and corrupt framing.
    msg = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"clientInfo": {"name": "line1\nline2", "version": "0"}},
    }
    raw = frame(msg)
    code, out, err = drive(raw)
    responses = parse_responses(out)
    assert code == 0, f"exit {code}: {err!r}"
    assert len(responses) == 1 and responses[0].get("id") == 1, responses
    assert "error" not in responses[0], responses[0]
    print("[PASS] Byte-exact framed read (embedded newline in payload)")


def main() -> int:
    if not ENTRY.exists():
        print(f"[FATAL] entrypoint not found: {ENTRY}", file=sys.stderr)
        return 2
    scenarios = [
        scenario_content_length_crlf,
        scenario_content_length_lf,
        scenario_newline,
        scenario_mixed,
        scenario_embedded_newline_byte_exact,
    ]
    failed = 0
    for fn in scenarios:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(str(exc), file=sys.stderr)
    print()
    if failed:
        print(f"RESULT: {failed}/{len(scenarios)} scenarios FAILED")
        return 1
    print(f"RESULT: all {len(scenarios)} scenarios PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
