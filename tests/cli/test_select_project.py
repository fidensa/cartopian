"""Tests for `cartopian select-project` (plan section 5 item 3).

Handle resolution, persisted binding in the session record and in the
project's ``requests/bindings.json``, project switch by ordinal range,
unbind, and every refusal. The command is driven through the entrypoint with
an isolated HOME (registry) and intake root, exactly as a PM host would.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli import intake_adapter
from cli.intake_adapter import SessionStore, handle_event

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "bin" / "cartopian"


def _payload(event: str, host: str = "claude", session_id: str = "sess-1", **extra) -> dict:
    body = {"session_id": session_id, "hook_event_name": event, "cwd": "/tmp/work"}
    body.update(extra)
    return body


class SelectProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.home = base / "home"
        (self.home / ".cartopian").mkdir(parents=True)
        self.intake = base / "intake"
        self.store = SessionStore(self.intake)
        self.projects = {}
        for pid in ("alpha", "beta"):
            root = base / pid
            root.mkdir()
            (root / "cartopian.toml").write_text("[project]\nid = \"%s\"\n" % pid)
            self.projects[pid] = root
        (base / "unregistered").mkdir()
        (self.home / ".cartopian" / "projects.json").write_text(
            json.dumps([
                {"id": pid, "path": str(root), "label": pid}
                for pid, root in self.projects.items()
            ])
        )

    def run_cli(self, *argv: str, env_extra: dict | None = None):
        env = {k: v for k, v in os.environ.items() if k not in (intake_adapter.ROLE_ENV, "CARTOPIAN_MCP_TOOL_CALL")}
        env["HOME"] = str(self.home)
        env[intake_adapter.INTAKE_ROOT_ENV] = str(self.intake)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(ENTRYPOINT), "select-project", *argv],
            capture_output=True, text=True, env=env,
        )

    def start_session(self, host: str = "claude", session_id: str = "sess-1", prompts: int = 2) -> dict:
        handle_event(self.store, host, _payload("SessionStart", host=host, session_id=session_id))
        key = "prompt_id" if host == "claude" else "turn_id"
        for i in range(prompts):
            handle_event(self.store, host, _payload("UserPromptSubmit", host=host, session_id=session_id, prompt=f"p{i}", **{key: f"t{i}"}))
            handle_event(self.store, host, _payload("Stop", host=host, session_id=session_id, last_assistant_message=f"a{i}", **{key: f"t{i}"}))
        return self.store.load_session(host, session_id)

    def bindings(self, pid: str) -> list:
        path = self.projects[pid] / "requests" / "bindings.json"
        if not path.is_file():
            return []
        return json.loads(path.read_text())["bindings"]


class BindingTests(SelectProjectCase):
    def test_bind_promotes_buffer_and_persists_both_sides(self) -> None:
        record = self.start_session()
        result = self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"])
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["outcome"], "bound")
        self.assertEqual((out["host"], out["session_id"]), ("claude", "sess-1"))
        self.assertEqual(out["binding"]["from_ordinal"], 1)
        self.assertIsNone(out["binding"]["to_ordinal"])
        self.assertTrue(out["capture_active"])
        self.assertEqual(out["buffer"], {"events": 5, "evicted": None})

        session = self.store.load_session("claude", "sess-1")
        self.assertEqual(len(session["bindings"]), 1)
        self.assertEqual(session["bindings"][0]["project_id"], "alpha")
        project = self.bindings("alpha")
        self.assertEqual(len(project), 1)
        self.assertEqual(project[0]["binding_id"], session["bindings"][0]["binding_id"])
        self.assertEqual(project[0]["session_id"], "sess-1")
        self.assertIsNone(project[0]["confirmation"])

    def test_bound_session_stops_evicting_and_survives_session_end(self) -> None:
        record = self.start_session()
        self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"])
        for i in range(intake_adapter.PRESELECTION_MAX_EVENTS + 5):
            handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt=f"x{i}", prompt_id=f"x{i}"))
        events = self.store.read_events("claude", "sess-1")
        self.assertFalse(any(e["event"] == "evicted" for e in events))
        handle_event(self.store, "claude", _payload("SessionEnd", reason="other"))
        kept = self.store.load_session("claude", "sess-1")
        self.assertIsNotNone(kept)
        self.assertIsNotNone(kept["ended"])

    def test_rebind_same_project_is_idempotent(self) -> None:
        record = self.start_session()
        self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"])
        again = self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"])
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["outcome"], "already-bound")
        self.assertEqual(len(self.bindings("alpha")), 1)
        self.assertEqual(len(self.store.load_session("claude", "sess-1")["bindings"]), 1)

    def test_project_switch_closes_range_and_reannounces(self) -> None:
        record = self.start_session()  # ordinals 1..5
        self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"])
        handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt="switch", prompt_id="t9"))  # 6
        result = self.run_cli(str(self.projects["beta"]), "--handle", record["handle"])
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["outcome"], "switched")
        self.assertEqual(out["binding"]["from_ordinal"], 7)

        session = self.store.load_session("claude", "sess-1")
        self.assertEqual([(b["project_id"], b["from_ordinal"], b["to_ordinal"]) for b in session["bindings"]],
                         [("alpha", 1, 6), ("beta", 7, None)])
        self.assertTrue(session["announce_pending"])
        self.assertEqual(self.bindings("alpha")[0]["to_ordinal"], 6)
        self.assertIsNone(self.bindings("beta")[0]["to_ordinal"])
        # The adapter prints the routing line again on the next prompt.
        line, _ = handle_event(self.store, "claude", _payload("UserPromptSubmit", prompt="hi", prompt_id="t10"))
        self.assertEqual(line, intake_adapter.routing_line(record["handle"]))

    def test_unbind_ends_capture(self) -> None:
        record = self.start_session()
        self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"])
        result = self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"], "--unbind")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["outcome"], "unbound")
        self.assertFalse(out["capture_active"])
        self.assertEqual(self.bindings("alpha")[0]["to_ordinal"], 5)
        twice = self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"], "--unbind")
        self.assertEqual(twice.returncode, 1)
        self.assertIn("not-bound", twice.stderr)

    def test_two_sessions_bind_the_same_project_independently(self) -> None:
        a = self.start_session(host="codex", session_id="thread-a")
        b = self.start_session(host="codex", session_id="thread-b")
        self.assertEqual(self.run_cli(str(self.projects["alpha"]), "--handle", a["handle"]).returncode, 0)
        self.assertEqual(self.run_cli(str(self.projects["alpha"]), "--handle", b["handle"]).returncode, 0)
        self.assertEqual({x["session_id"] for x in self.bindings("alpha")}, {"thread-a", "thread-b"})


class RefusalTests(SelectProjectCase):
    def test_unknown_handle_is_session_unbound_with_operator_remedy(self) -> None:
        result = self.run_cli(str(self.projects["alpha"]), "--handle", "cs-0000000000000000")
        self.assertEqual(result.returncode, 1)
        self.assertIn("session-unbound", result.stderr)
        self.assertIn("Never create, copy, or edit records", result.stderr)
        self.assertEqual(self.bindings("alpha"), [])

    def test_stale_handle_after_discard_is_unbound(self) -> None:
        record = self.start_session()
        handle_event(self.store, "claude", _payload("SessionEnd", reason="other"))  # unbound: discarded
        result = self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("session-unbound", result.stderr)

    def test_forged_handle_index_is_ignored(self) -> None:
        self.start_session()
        forged = self.store.handle_path("cs-ffffffffffffffff")
        forged.write_text(json.dumps({"handle": "cs-ffffffffffffffff", "host": "claude", "session_id": "sess-1"}))
        result = self.run_cli(str(self.projects["alpha"]), "--handle", "cs-ffffffffffffffff")
        self.assertEqual(result.returncode, 1)
        self.assertIn("session-unbound", result.stderr)

    def test_dispatched_role_cannot_bind(self) -> None:
        record = self.start_session()
        result = self.run_cli(str(self.projects["alpha"]), "--handle", record["handle"],
                              env_extra={intake_adapter.ROLE_ENV: "coder"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("non-operator-session", result.stderr)
        self.assertEqual(self.bindings("alpha"), [])

    def test_unregistered_project_is_refused(self) -> None:
        record = self.start_session()
        result = self.run_cli(str(Path(self.tmp.name) / "unregistered"), "--handle", record["handle"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("project-unregistered", result.stderr)

    def test_session_identity_cannot_be_supplied(self) -> None:
        # There is no argument for it: the record is the only source.
        result = self.run_cli(str(self.projects["alpha"]), "--handle", "cs-0000000000000000", "--session-id", "sess-1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


class SurfaceTests(unittest.TestCase):
    def test_mcp_tool_exposes_no_session_identity_argument(self) -> None:
        from mcp_server import server

        server._TOOL_CACHE = None
        tools = {t["name"]: t for t in server.list_tools()}
        self.assertIn("select_project", tools)
        props = set(tools["select_project"]["inputSchema"]["properties"])
        self.assertEqual(props, {"project_root", "handle", "unbind"})


if __name__ == "__main__":
    unittest.main()
