// Cartopian request-evidence intake for opencode.
//
// Installed by `scripts/install.py --intake-hooks` into
// `~/.config/opencode/plugins/cartopian-intake.js`; opencode loads it at
// startup. It forwards the operator's prompt text and the assistant's final
// reply per turn to the Cartopian intake adapter (`cli/intake_adapter.py`)
// as the same canonical payloads the Claude Code and Codex hooks send.
// On the first prompt of a session the adapter answers with the one-line
// `cartopian-session: <handle>` routing note, which is appended to that
// message as a synthetic text part so the model sees it. A dispatched
// Cartopian role session (CARTOPIAN_ROLE set) records nothing. Failures
// never interrupt opencode.
import { spawn } from "node:child_process";

const CARTOPIAN_PYTHON = "__CARTOPIAN_PYTHON__";
const CARTOPIAN_ADAPTER = "__CARTOPIAN_ADAPTER__";
const TIMEOUT_MS = 15000;

function send(payload) {
  return new Promise((resolve) => {
    if (process.env.CARTOPIAN_ROLE) return resolve("");
    let out = "";
    let done = false;
    const finish = (value) => { if (!done) { done = true; resolve(value); } };
    let child;
    try {
      child = spawn(CARTOPIAN_PYTHON, [CARTOPIAN_ADAPTER, "--host", "opencode"], { stdio: ["pipe", "pipe", "ignore"] });
    } catch (_) {
      return finish("");
    }
    const timer = setTimeout(() => { try { child.kill(); } catch (_) {} finish(""); }, TIMEOUT_MS);
    child.stdout.on("data", (chunk) => { out += chunk; });
    child.on("error", () => { clearTimeout(timer); finish(""); });
    child.on("close", () => { clearTimeout(timer); finish(out.trim()); });
    try { child.stdin.end(JSON.stringify(payload)); } catch (_) { finish(""); }
  });
}

function partId() {
  const time = Date.now().toString(16).padStart(12, "0");
  let tail = "";
  while (tail.length < 14) tail += Math.floor(Math.random() * 16).toString(16);
  return "prt_" + time + tail;
}

function textOf(parts) {
  return (parts || []).filter((p) => p && p.type === "text" && !p.synthetic).map((p) => p.text).join("\n");
}

export const CartopianIntake = async ({ client, directory }) => {
  const started = new Set();
  const ensureStart = async (sessionID) => {
    if (!sessionID || started.has(sessionID)) return;
    started.add(sessionID);
    await send({ hook_event_name: "SessionStart", session_id: sessionID, cwd: directory, source: "startup" });
  };
  return {
    "chat.message": async (input, output) => {
      const sessionID = input.sessionID;
      const messageID = (output.message && output.message.id) || input.messageID || "";
      await ensureStart(sessionID);
      const line = await send({
        hook_event_name: "UserPromptSubmit",
        session_id: sessionID,
        cwd: directory,
        prompt: textOf(output.parts),
        message_id: messageID,
      });
      if (line) {
        // A full part: opencode validates parts before saving the message
        // and requires a `prt`-prefixed, time-sortable id like its own.
        output.parts.push({
          id: partId(),
          sessionID,
          messageID,
          type: "text",
          text: line,
          synthetic: true,
        });
      }
    },
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        const id = event.properties.sessionID;
        let messages = [];
        try {
          const res = await client.session.messages({ path: { id } });
          messages = res.data || [];
        } catch (_) {
          return;
        }
        const last = [...messages].reverse().find((m) => m.info && m.info.role === "assistant");
        if (!last) return;
        await send({
          hook_event_name: "Stop",
          session_id: id,
          cwd: directory,
          last_assistant_message: (last.parts || []).filter((p) => p.type === "text").map((p) => p.text).join("\n"),
          message_id: last.info.parentID || "",
        });
      } else if (event.type === "session.deleted") {
        const info = event.properties.info || {};
        await send({ hook_event_name: "SessionEnd", session_id: info.id, cwd: directory, reason: "other" });
      }
    },
  };
};
