#!/usr/bin/env bash
# _cartopian-toolchain.sh — PM toolchain pinning / identity audit.
#
# Named with a leading underscore so it reads as a *library* sourced by the
# cartopian-*-pm containment wrappers, not a wrapper itself. Stdlib-only
# (bash + python3 json) — NF-001.
#
# THE GAP THIS CLOSES: the Cartopian CLI/MCP
# the PM runs on can resolve to the editable work-root source tree — the very
# tree coders mutate during handoffs — so a coder edit to cli/ or mcp_server/
# lands directly on the PM's own toolchain path. Whether the PM then runs the
# old code (stale in-memory MCP modules) or the new code (a fresh subprocess /
# server restart) is *incidental*, making "which code did the PM actually run?"
# ambiguous. A contained PM's toolchain must be a pinned/installed artifact
# (the scripts/install.py file-copy root, e.g. ~/.cartopian), distinct from any
# editable checkout.
#
# WHAT THE AUDIT ENFORCES at PM launch (fail closed, before the harness runs):
#   1. The cartopian MCP command resolves from the shared MCP config
#      (wrappers/etc/mcp-cartopian-only.json) and is executable.
#   2. The toolchain root it implies (<root>/bin/<cmd> => <root>) is NOT a git
#      work tree. A git work tree is an editable checkout — the tree assignees
#      mutate — and is refused unless the operator explicitly opts in with
#      CARTOPIAN_PM_TOOLCHAIN_DEV=1 (a loud, per-invocation dev-only bypass).
#   3. The resolved toolchain identity is printed to stderr — command, root,
#      and <root>/VERSION — so "which code did the PM run?" is explicit and
#      auditable at launch, not reconstructed after the fact. The MCP server
#      independently reports the same root/version in its serverInfo and
#      use_cartopian install-context block; this banner covers the launch path.

# Audit the PM toolchain identity and pinning. Fail-closed: exits the
# (sourced) wrapper with status 1 on an unresolvable/unexecutable command or
# an editable-checkout toolchain root without the dev opt-in.
# Args: $1 = wrapper name (for messages)
#       $2 = path to the cartopian-only MCP config (JSON)
#       $3 = optional already-resolved cartopian-mcp command (skips re-parse)
cartopian_pm_toolchain_audit() {
  local wrapper="$1" mcp_config="$2" cmd="${3:-}"

  if [[ -z "$cmd" ]]; then
    cmd="$(python3 - "$mcp_config" <<'PY'
import json, sys
try:
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    srv = (cfg.get("mcpServers") or {}).get("cartopian") or {}
    print(srv.get("command") or "")
except Exception:
    print("")
PY
)"
  fi

  if [[ -z "$cmd" ]]; then
    echo "${wrapper}: error: cannot resolve the cartopian MCP command from ${mcp_config}" >&2
    exit 1
  fi
  if [[ ! -x "$cmd" ]]; then
    echo "${wrapper}: error: resolved cartopian MCP command is not executable: ${cmd}" >&2
    echo "${wrapper}: install the pinned toolchain (scripts/install.py) or fix ${mcp_config}" >&2
    exit 1
  fi

  local root version
  root="$(cd "$(dirname "$cmd")/.." && pwd -P)"
  version="$(cat "${root}/VERSION" 2>/dev/null || true)"
  [[ -n "$version" ]] || version="unknown"

  if [[ -e "${root}/.git" ]]; then
    # The toolchain root is a git work tree => an editable checkout, i.e. the
    # tree assignees mutate during handoffs. Refuse: the PM's toolchain must be
    # the pinned install (scripts/install.py copy), not the work root.
    case "$(printf '%s' "${CARTOPIAN_PM_TOOLCHAIN_DEV:-}" | tr '[:upper:]' '[:lower:]')" in
      1|true|yes|on)
        echo "${wrapper}: WARNING: PM toolchain resolves to an EDITABLE checkout (${root})" >&2
        echo "${wrapper}: WARNING: CARTOPIAN_PM_TOOLCHAIN_DEV is set — proceeding for development ONLY." >&2
        echo "${wrapper}: WARNING: coder edits to cli/ or mcp_server/ in that tree can alter this PM mid-session." >&2
        ;;
      *)
        echo "${wrapper}: error: PM toolchain resolves to an EDITABLE checkout: ${root}" >&2
        echo "${wrapper}: a contained PM must run a pinned/installed toolchain, not the work root assignees mutate." >&2
        echo "${wrapper}: fix: point ${mcp_config} at the installed root (scripts/install.py, e.g. ~/.cartopian/bin/cartopian-mcp)," >&2
        echo "${wrapper}: or set CARTOPIAN_PM_TOOLCHAIN_DEV=1 to opt in for development (dangerous)." >&2
        exit 1
        ;;
    esac
  fi

  echo "${wrapper}: PM toolchain = ${cmd} (root=${root}, version=${version})" >&2
}
