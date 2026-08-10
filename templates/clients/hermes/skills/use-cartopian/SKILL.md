---
name: use-cartopian
description: Enter Cartopian PM mode. Use when the operator says "use cartopian" or asks to start, resume, or manage a Cartopian-governed project session.
version: 1.0.0
author: Cartopian
license: MIT
platforms: [linux, macos, windows]
---

# Use Cartopian

**Startup outcome:** Enter Cartopian PM mode through registry-first project selection.
**Startup action:** Read `cartopian://skills/use_cartopian` with your host's MCP resource reader and follow it.

Read every step before acting. The resource carries the authoritative install
context, including truthful installed-versus-running restart state, startup
boundaries, and registry-first runbook. Follow its one Hermes restart
instruction when present; do not claim activation until the resource reports
fresh-process matching-content proof.

Every `cartopian://...` URI named by that runbook is another Cartopian MCP
resource. Read it with the Cartopian MCP resource reader. Never translate a
Cartopian resource identity such as `cartopian://skills/start_session` into a
Hermes native skill name or pass it to Hermes's `skill_view` tool.
