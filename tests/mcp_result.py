"""Helpers for reading MCP ``tools/call`` results in tests.

The server emits a tool call's NDJSON records exactly once, in
``content[0].text`` (the MCP-baseline text channel every host supports).
Results carry no ``structuredContent``: hosts that honor the structured-output
convention treat that key as the whole result and ignore ``content``, so
publishing anything but the records there drops them. Small call metadata
(``exit_code``, ``stderr_lines``) rides in ``_meta`` instead.
"""
import json
from typing import Any, Dict, List

STDERR_MARKER = "--- stderr ---"


def tool_records(result: Dict[str, Any]) -> List[Any]:
    """Parse the NDJSON records from a tools/call result's text content."""
    text = result["content"][0]["text"]
    records: List[Any] = []
    for line in text.splitlines():
        if line == STDERR_MARKER:
            break
        stripped = line.strip()
        if not stripped:
            continue
        try:
            records.append(json.loads(stripped))
        except json.JSONDecodeError:
            # Non-NDJSON stdout (e.g. generate-config TOML) is not a record.
            pass
    return records


def tool_meta(result: Dict[str, Any]) -> Dict[str, Any]:
    """Return a tools/call result's call metadata (``exit_code``, ``stderr_lines``)."""
    return result["_meta"]
