---
name: use-cartopian
description: Enter Cartopian PM mode. Use when the operator says "use cartopian" or asks to start, resume, or manage a Cartopian-governed project session.
triggers:
  - user
  - model
---

# Use Cartopian

**Startup outcome:** Enter Cartopian PM mode through registry-first project selection.
**Startup action:** Read `cartopian://skills/use_cartopian` using the `read_context` MCP tool (uri argument), or your host's MCP resource reader, and follow it. Discover only the named tool or URI; do not dump tool or resource catalogs.

Read every step before acting. The resource carries the authoritative install
context, startup boundaries, and registry-first runbook.
