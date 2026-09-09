"""Cartopian request-evidence intake for Hermes Agent.

Installed by ``scripts/install.py --intake-hooks`` into
``~/.hermes/plugins/cartopian-intake/`` and enabled by the operator with
``hermes plugins enable cartopian-intake``. It forwards four lifecycle
events to the Cartopian intake adapter (``cli/intake_adapter.py``) as the
same canonical payloads the Claude Code and Codex hooks send:

- ``on_session_start`` -> ``SessionStart``
- ``pre_llm_call``     -> ``UserPromptSubmit`` (the operator's prompt text)
- ``post_llm_call``    -> ``Stop`` (the assistant's final reply)
- ``on_session_end``   -> ``SessionEnd``

On the first prompt of a session the adapter answers with the one-line
``cartopian-session: <handle>`` routing note, which this plugin returns from
``pre_llm_call`` as ``{"context": ...}`` so Hermes appends it to the turn.
Nothing else is read or injected. A dispatched Cartopian role session
(``CARTOPIAN_ROLE`` set) records nothing. Failures never interrupt Hermes.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, Optional

CARTOPIAN_PYTHON = "__CARTOPIAN_PYTHON__"
CARTOPIAN_ADAPTER = "__CARTOPIAN_ADAPTER__"
_TIMEOUT_SECONDS = 15


def _send(event: str, session_id: Optional[str], **fields: Any) -> Optional[Dict[str, Any]]:
    if os.environ.get("CARTOPIAN_ROLE"):
        return None
    payload = {
        "hook_event_name": event,
        "session_id": session_id or "",
        "cwd": os.getcwd(),
        **fields,
    }
    try:
        completed = subprocess.run(
            [CARTOPIAN_PYTHON, CARTOPIAN_ADAPTER, "--host", "hermes"],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception:
        return None
    line = (completed.stdout or "").strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _on_session_start(session_id: Optional[str] = None, **_: Any) -> None:
    _send("SessionStart", session_id, source="startup")


def _pre_llm_call(session_id: Optional[str] = None, user_message: str = "", **_: Any) -> Optional[Dict[str, Any]]:
    return _send("UserPromptSubmit", session_id, prompt=user_message or "")


def _post_llm_call(session_id: Optional[str] = None, assistant_response: str = "", **_: Any) -> None:
    _send("Stop", session_id, last_assistant_message=assistant_response or "")


def _on_session_end(session_id: Optional[str] = None, interrupted: bool = False, **_: Any) -> None:
    _send("SessionEnd", session_id, reason="interrupted" if interrupted else "completed")


def register(ctx) -> None:
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_llm_call", _post_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
