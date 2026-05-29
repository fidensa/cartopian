"""Tests for the Cartopian MCP server.

Covers:
- JSON-RPC dispatch shape (initialize handshake, error codes,
  notifications produce no response, malformed JSON returns parse error).
- Prompt surface (use_cartopian present, every skill registers, the
  skill prompt body includes the skill markdown).
- Tool surface (every cli.main.SUBCOMMANDS entry registers; argv rebuild
  preserves required arg semantics; tool execution captures NDJSON +
  stderr; FR-014 stderr prefixes survive round-trip).
- Resource surface (skills, protocol, templates, per-project artifacts;
  underscore identifier shape is consistent with prompt names).
- Subprocess-level smoke (bin/cartopian-mcp boots and responds).
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcp_server import server  # noqa: E402


# ---------------------------------------------------------------------------
# In-process JSON-RPC harness
# ---------------------------------------------------------------------------

def rpc(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drive server.run() with a list of JSON-RPC messages; return responses."""
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    stdout = io.StringIO()
    server.run(stdin=stdin, stdout=stdout)
    return [
        json.loads(line)
        for line in stdout.getvalue().splitlines()
        if line.strip()
    ]


def single(method: str, params: Optional[Dict[str, Any]] = None, rpc_id: int = 1) -> Dict[str, Any]:
    responses = rpc([{"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}])
    assert len(responses) == 1, f"expected one response, got: {responses}"
    return responses[0]


# ---------------------------------------------------------------------------
# Byte-exact stdio harness (Content-Length framing + newline-delimited)
# ---------------------------------------------------------------------------

def run_bytes(raw: bytes) -> bytes:
    """Drive server.run() over genuine byte streams; return raw stdout bytes."""
    stdin = io.BytesIO(raw)
    stdout = io.BytesIO()
    server.run(stdin=stdin, stdout=stdout)
    return stdout.getvalue()


def frame(message: Dict[str, Any], eol: bytes = b"\r\n") -> bytes:
    """Encode a message with a Content-Length header block."""
    body = json.dumps(message).encode("utf-8")
    header = b"Content-Length: " + str(len(body)).encode("ascii")
    return header + eol + eol + body


def newline(message: Dict[str, Any]) -> bytes:
    """Encode a message as newline-delimited JSON."""
    return json.dumps(message).encode("utf-8") + b"\n"


def parse_framed(out: bytes) -> List[Dict[str, Any]]:
    """Parse one-or-more Content-Length-framed messages from a byte stream."""
    messages: List[Dict[str, Any]] = []
    rest = out
    while rest:
        header_blob, sep, after = rest.partition(b"\r\n\r\n")
        assert sep, f"no header/body separator in: {rest!r}"
        length = None
        for hline in header_blob.split(b"\r\n"):
            name, _, value = hline.partition(b":")
            if name.strip().lower() == b"content-length":
                length = int(value.strip())
        assert length is not None, f"no Content-Length in: {header_blob!r}"
        body = after[:length]
        messages.append(json.loads(body.decode("utf-8")))
        rest = after[length:]
    return messages


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------

class TestInitializeHandshake(unittest.TestCase):
    def test_initialize_returns_protocol_and_server_info(self):
        response = single("initialize")
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "cartopian")
        self.assertEqual(result["serverInfo"]["version"], server._server_version())
        self.assertIn("prompts", result["capabilities"])
        self.assertIn("tools", result["capabilities"])
        self.assertIn("resources", result["capabilities"])


class TestServerVersionResolution(unittest.TestCase):
    def test_version_marker_is_preferred_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("v1.2.4\n", encoding="utf-8")
            with patch.object(server, "ROOT", root):
                with patch.object(server, "_read_git_version") as git_version:
                    self.assertEqual(server._server_version(), "v1.2.4")
            git_version.assert_not_called()

    def test_git_describe_fallback_used_without_version_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(server, "ROOT", root):
                with patch.object(server, "_read_git_version", return_value="v1.2.4-1-gcc16f01"):
                    self.assertEqual(server._server_version(), "v1.2.4-1-gcc16f01")


class TestRpcErrorContract(unittest.TestCase):
    def test_unknown_method_returns_method_not_found(self):
        response = single("does/not/exist")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_METHOD_NOT_FOUND)

    def test_notification_produces_no_response(self):
        # notifications/initialized has no id field; server must not respond.
        responses = rpc([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
        self.assertEqual(responses, [])

    def test_malformed_json_returns_parse_error(self):
        stdin = io.StringIO("not-json-at-all\n")
        stdout = io.StringIO()
        server.run(stdin=stdin, stdout=stdout)
        response = json.loads(stdout.getvalue().splitlines()[0])
        self.assertEqual(response["error"]["code"], server.ERR_PARSE)
        self.assertIsNone(response["id"])

    def test_missing_method_returns_invalid_request(self):
        responses = rpc([{"jsonrpc": "2.0", "id": 7, "params": {}}])
        self.assertEqual(responses[0]["error"]["code"], server.ERR_INVALID_REQUEST)

    def test_ping_returns_empty_result(self):
        response = single("ping")
        self.assertEqual(response["result"], {})


# ---------------------------------------------------------------------------
# Prompt surface
# ---------------------------------------------------------------------------

class TestPromptSurface(unittest.TestCase):
    def test_use_cartopian_is_first_and_present(self):
        response = single("prompts/list")
        names = [p["name"] for p in response["result"]["prompts"]]
        self.assertEqual(names[0], "use_cartopian")

    def test_every_skill_registers_as_prompt(self):
        response = single("prompts/list")
        names = {p["name"] for p in response["result"]["prompts"]}
        expected_skills = {
            "adopt_plan",
            "adopt_requirements",
            "check_for_updates",
            "close_plan",
            "init_project",
            "init_workspace",
            "plan_project",
            "run_handoff",
            "run_task",
            "start_session",
        }
        missing = expected_skills - names
        self.assertEqual(missing, set(), f"missing skill prompts: {missing}")

    def test_use_cartopian_briefing_mentions_default_first_move(self):
        response = single("prompts/get", {"name": "use_cartopian"})
        text = response["result"]["messages"][0]["content"]["text"]
        self.assertIn("start_session", text)
        self.assertIn("init_project", text)
        self.assertIn("cartopian://protocol/CONVENTIONS", text)

    def test_skill_prompt_returns_skill_body(self):
        response = single("prompts/get", {"name": "start_session"})
        text = response["result"]["messages"][0]["content"]["text"]
        # Body of skills/start-session.md must appear in the prompt content.
        self.assertIn("Stage 0", text)
        self.assertIn("Skill: Start Session", text)
        # Header must instruct the agent to use MCP tools rather than shell.
        self.assertIn("MCP tool", text)
        self.assertIn("rather than shelling out", text)

    def test_unknown_prompt_returns_invalid_params(self):
        response = single("prompts/get", {"name": "does_not_exist"})
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

class TestToolSurface(unittest.TestCase):
    def test_every_cli_subcommand_is_a_tool(self):
        from cli.main import SUBCOMMANDS
        response = single("tools/list")
        tool_names = {t["name"] for t in response["result"]["tools"]}
        for sub in SUBCOMMANDS:
            self.assertIn(sub.replace("-", "_"), tool_names)

    def test_move_task_schema_lists_required_positionals(self):
        response = single("tools/list")
        tools = {t["name"]: t for t in response["result"]["tools"]}
        schema = tools["move_task"]["inputSchema"]
        self.assertIn("task_path", schema["properties"])
        self.assertIn("to_status", schema["properties"])
        self.assertIn("task_path", schema["required"])
        self.assertIn("to_status", schema["required"])
        # Choices propagate as JSON-schema enum.
        self.assertIn("open", schema["properties"]["to_status"]["enum"])

    def test_register_project_label_is_optional(self):
        response = single("tools/list")
        tools = {t["name"]: t for t in response["result"]["tools"]}
        schema = tools["register_project"]["inputSchema"]
        self.assertIn("label", schema["properties"])
        self.assertNotIn("label", schema.get("required", []))

    def test_unknown_tool_returns_invalid_params(self):
        response = single("tools/call", {"name": "does_not_exist", "arguments": {}})
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_missing_required_argument_returns_invalid_params(self):
        response = single("tools/call", {"name": "move_task", "arguments": {}})
        # No exception leaks; either the in-process invoke returns isError
        # (argparse usage error captured), or the wrapper raises invalid params
        # before invocation. Either is acceptable; both surface the failure.
        if "error" in response:
            self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)
        else:
            self.assertTrue(response["result"]["isError"])

    def test_discover_projects_runs_in_process(self):
        # discover_projects reads ~/.cartopian/projects.json. We can't assume
        # contents, but the call must succeed (exit 0) and return a list.
        response = single("tools/call", {"name": "discover_projects", "arguments": {}})
        self.assertNotIn("error", response)
        sc = response["result"]["structuredContent"]
        self.assertEqual(sc["exit_code"], 0)
        self.assertIsInstance(sc["records"], list)

    def test_move_task_invalid_status_preserves_fr014_usage_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "project" / "tasks" / "open" / "TASK-01-001-demo.md"
            task.parent.mkdir(parents=True, exist_ok=True)
            task.write_text("# task\n", encoding="utf-8")
            response = single("tools/call", {
                "name": "move_task",
                "arguments": {"task_path": str(task), "to_status": "archived"},
            })
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertTrue(result["isError"])
        joined = "\n".join(result["structuredContent"]["stderr_lines"])
        self.assertTrue(joined.startswith("[usage]"), msg=f"stderr was: {joined!r}")
        self.assertIn("invalid to_status", joined)


# ---------------------------------------------------------------------------
# Resource surface
# ---------------------------------------------------------------------------

class TestResourceSurface(unittest.TestCase):
    def test_skills_and_protocol_listed_with_underscore_identifiers(self):
        response = single("resources/list")
        uris = {r["uri"] for r in response["result"]["resources"]}
        self.assertIn("cartopian://skills/start_session", uris)
        self.assertIn("cartopian://skills/init_project", uris)
        # Hyphen variant must NOT be present — identifier shape is uniform.
        self.assertNotIn("cartopian://skills/start-session", uris)

    def test_read_skill_returns_markdown(self):
        response = single("resources/read", {"uri": "cartopian://skills/start_session"})
        text = response["result"]["contents"][0]["text"]
        self.assertIn("# Skill: Start Session", text)
        self.assertEqual(response["result"]["contents"][0]["mimeType"], "text/markdown")

    def test_read_protocol_conventions(self):
        response = single("resources/read", {"uri": "cartopian://protocol/CONVENTIONS"})
        # If protocol/CONVENTIONS.md doesn't exist in this checkout the test
        # should be skipped rather than fail — the resource is environment-
        # provided.
        if "error" in response:
            self.skipTest("protocol/CONVENTIONS.md not present in this checkout")
        text = response["result"]["contents"][0]["text"]
        self.assertGreater(len(text), 200)

    def test_unknown_namespace_returns_invalid_params(self):
        response = single("resources/read", {"uri": "cartopian://nope/whatever"})
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_unsupported_scheme_returns_invalid_params(self):
        response = single("resources/read", {"uri": "file:///etc/passwd"})
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_project_resource_lists_when_registry_has_entry(self):
        fake_entries = [{
            "id": "demo-project",
            "path": "/tmp/cartopian-mcp-test-demo",
            "label": "Demo",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "STATE.md").write_text("# STATE\n", encoding="utf-8")
            fake_entries[0]["path"] = str(proj)
            with patch.object(server, "_registry_entries", return_value=fake_entries):
                response = single("resources/list")
                uris = {r["uri"] for r in response["result"]["resources"]}
                self.assertIn("cartopian://project/demo-project/STATE", uris)

                read_response = single(
                    "resources/read",
                    {"uri": "cartopian://project/demo-project/STATE"},
                )
                text = read_response["result"]["contents"][0]["text"]
                self.assertIn("# STATE", text)


# ---------------------------------------------------------------------------
# Security remediations: path traversal, resource limits, error scrubbing
# ---------------------------------------------------------------------------

class TestResourceSafety(unittest.TestCase):
    """Regression tests for the MCP resource read security findings.

    Covers:
    - SSS-03: project / protocol / template path traversal must be blocked.
    - SSS-03: project `kind` must be allowlisted, not user-controlled.
    - SSS-08: oversize files must be refused before being loaded.
    - SSS-02: internal exceptions must not leak tracebacks/paths to callers.
    """

    def _read(self, uri: str) -> Dict[str, Any]:
        return single("resources/read", {"uri": uri})

    def test_project_kind_traversal_blocked(self):
        # Even if the project entry is valid, a `kind` containing path
        # separators must be rejected before any filesystem access.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "STATE.md").write_text("# STATE\n", encoding="utf-8")
            # Plant a "sensitive" file outside the project to prove we don't
            # reach it.
            sibling = Path(tmp).parent / "cartopian-mcp-sensitive.md"
            try:
                sibling.write_text("SECRET", encoding="utf-8")
                fake_entries = [{"id": "demo", "path": str(proj), "label": "Demo"}]
                with patch.object(server, "_registry_entries", return_value=fake_entries):
                    response = self._read(
                        f"cartopian://project/demo/../../{sibling.stem}"
                    )
            finally:
                if sibling.exists():
                    sibling.unlink()
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_project_kind_must_be_allowlisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            # File exists, but the kind is not in PROJECT_KINDS.
            (proj / "SECRET.md").write_text("nope", encoding="utf-8")
            fake_entries = [{"id": "demo", "path": str(proj), "label": "Demo"}]
            with patch.object(server, "_registry_entries", return_value=fake_entries):
                response = self._read("cartopian://project/demo/SECRET")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_project_id_traversal_blocked(self):
        # `..` as a project id must be rejected by segment validation.
        response = self._read("cartopian://project/../STATE")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_protocol_traversal_blocked(self):
        response = self._read("cartopian://protocol/../README")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_templates_traversal_blocked(self):
        response = self._read("cartopian://templates/../README.md")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_oversize_resource_rejected(self):
        # Force the size cap below any real resource so the next read trips it.
        with patch.object(server, "MAX_RESOURCE_BYTES", 10):
            response = self._read("cartopian://skills/start_session")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INTERNAL)
        self.assertIn("size limit", response["error"]["message"])

    def test_oversize_skill_prompt_rejected(self):
        with patch.object(server, "MAX_RESOURCE_BYTES", 10):
            response = single("prompts/get", {"name": "start_session"})
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INTERNAL)
        # Message must not contain an absolute filesystem path.
        self.assertNotIn(str(server.ROOT), response["error"]["message"])

    def test_resource_read_error_does_not_leak_paths(self):
        # If the resolved file disappears between resolve and read, the
        # caller-visible error must reference only the URI — never the
        # internal absolute path.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            state = proj / "STATE.md"
            state.write_text("# STATE\n", encoding="utf-8")
            fake_entries = [{"id": "demo", "path": str(proj), "label": "Demo"}]
            with patch.object(server, "_registry_entries", return_value=fake_entries):
                # Patch read_text to simulate an OSError after resolution.
                original_read_text = Path.read_text

                def fail_read_text(self, *args, **kwargs):
                    if self == state.resolve():
                        raise OSError("simulated I/O failure on " + str(self))
                    return original_read_text(self, *args, **kwargs)

                with patch.object(Path, "read_text", fail_read_text):
                    response = self._read("cartopian://project/demo/STATE")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INTERNAL)
        msg = response["error"]["message"]
        self.assertNotIn(str(state.resolve()), msg)
        self.assertNotIn("simulated I/O failure", msg)


class TestHandleMessageScrubsTraceback(unittest.TestCase):
    def test_unhandled_exception_does_not_return_traceback(self):
        def boom(method, params):
            raise RuntimeError("internal path /etc/passwd should not leak")

        with patch.object(server, "handle_request", side_effect=boom):
            response = single("initialize")
        self.assertIn("error", response)
        self.assertEqual(response["error"]["code"], server.ERR_INTERNAL)
        # No traceback data block should appear, and the message must be
        # generic — no internal paths, exception text, or stack frames.
        self.assertNotIn("data", response["error"])
        msg = response["error"]["message"]
        self.assertNotIn("/etc/passwd", msg)
        self.assertNotIn("Traceback", msg)
        self.assertNotIn("RuntimeError", msg)


# ---------------------------------------------------------------------------
# Dual stdio framing (Content-Length headers + newline-delimited)
# ---------------------------------------------------------------------------

class TestStdioFraming(unittest.TestCase):
    INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}

    def test_content_length_framed_request_is_parsed(self):
        # Read/parse loop must consume exactly Content-Length bytes and
        # dispatch the request, rather than treating the header line as JSON.
        out = run_bytes(frame(self.INIT))
        messages = parse_framed(out)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["id"], 1)
        self.assertEqual(messages[0]["result"]["protocolVersion"], "2024-11-05")

    def test_framed_request_gets_framed_response(self):
        # Symmetry: a Content-Length request gets a Content-Length response.
        out = run_bytes(frame(self.INIT))
        self.assertTrue(
            out.startswith(b"Content-Length:"),
            msg=f"expected framed response, got: {out!r}",
        )
        self.assertIn(b"\r\n\r\n", out)

    def test_newline_request_gets_newline_response(self):
        # Symmetry: a newline-delimited request gets a newline-delimited
        # response — no Content-Length header is emitted.
        out = run_bytes(newline(self.INIT))
        self.assertFalse(
            out.startswith(b"Content-Length:"),
            msg=f"expected newline response, got: {out!r}",
        )
        self.assertTrue(out.endswith(b"\n"))
        message = json.loads(out.splitlines()[0].decode("utf-8"))
        self.assertEqual(message["id"], 1)
        self.assertEqual(message["result"]["protocolVersion"], "2024-11-05")

    def test_newline_response_body_has_no_embedded_framing(self):
        out = run_bytes(newline(self.INIT))
        self.assertNotIn(b"Content-Length", out)

    def test_multiple_framed_messages_back_to_back(self):
        ping = {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}
        out = run_bytes(frame(self.INIT) + frame(ping))
        messages = parse_framed(out)
        self.assertEqual([m["id"] for m in messages], [1, 2])
        self.assertEqual(messages[1]["result"], {})

    def test_framed_payload_is_read_byte_exact(self):
        # A payload whose JSON contains an embedded newline must still be read
        # in full by byte count — line iteration would truncate it.
        msg = {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "does_not_exist\ninjected", "arguments": {}},
        }
        out = run_bytes(frame(msg))
        messages = parse_framed(out)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["id"], 9)
        # Whole payload parsed: the bogus tool name reaches dispatch and errors.
        self.assertEqual(messages[0]["error"]["code"], server.ERR_INVALID_PARAMS)

    def test_lf_only_header_terminator_is_accepted(self):
        # Some clients emit LF rather than CRLF around the header block.
        out = run_bytes(frame(self.INIT, eol=b"\n"))
        self.assertTrue(out.startswith(b"Content-Length:"))
        messages = parse_framed(out)
        self.assertEqual(messages[0]["id"], 1)

    def test_mixed_framing_in_one_stream(self):
        ping = {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}}
        out = run_bytes(frame(self.INIT) + newline(ping))
        # First response framed, second newline-delimited.
        self.assertTrue(out.startswith(b"Content-Length:"))
        first_blob, _, after = out.partition(b"\r\n\r\n")
        length = int(first_blob.split(b": ")[1])
        first = json.loads(after[:length].decode("utf-8"))
        self.assertEqual(first["id"], 1)
        tail = after[length:]
        second = json.loads(tail.splitlines()[0].decode("utf-8"))
        self.assertEqual(second["id"], 3)
        self.assertNotIn(b"Content-Length", tail)

    def test_framed_notification_produces_no_response(self):
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        out = run_bytes(frame(note))
        self.assertEqual(out, b"")


# ---------------------------------------------------------------------------
# Subprocess-level smoke (bin/cartopian-mcp via stdio)
# ---------------------------------------------------------------------------

class TestSubprocessSmoke(unittest.TestCase):
    def test_bin_entry_responds_to_initialize(self):
        entry = REPO_ROOT / "bin" / "cartopian-mcp"
        self.assertTrue(entry.exists(), f"missing entry: {entry}")
        proc = subprocess.run(
            [sys.executable, str(entry)],
            input=json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            }) + "\n",
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        response = json.loads(proc.stdout.splitlines()[0])
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(response["result"]["serverInfo"]["name"], "cartopian")


if __name__ == "__main__":
    unittest.main()
