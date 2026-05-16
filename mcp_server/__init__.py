"""Cartopian MCP server.

Exposes Cartopian skills, CLI subcommands, and protocol/template/project
content over the Model Context Protocol so any MCP-aware agent (Claude
Code, Claude Desktop, Codex, Gemini CLI, etc.) can enter Cartopian PM
mode without per-agent configuration of user-owned files.

Transport: newline-delimited JSON-RPC over stdin/stdout.
Protocol: MCP 2024-11-05.
"""

__all__ = ["server"]
