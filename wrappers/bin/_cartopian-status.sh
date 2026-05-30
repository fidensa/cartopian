#!/usr/bin/env bash
# _cartopian-status.sh — shared status-file helper for the Bash agent wrappers.
#
# Named with a leading underscore so it reads as a *library* sourced by the
# cartopian-* wrappers, not a wrapper itself (it carries no FR-012 launch-cwd
# or OQ-009 access-grant logic). Mirrors ps1/CartopianStatus.ps1.
#
# Sourced by every cartopian-* wrapper. Emits the optional early-crash
# detection signal that `cartopian wait-handoff` consumes: a status file at
# the deterministic path `<report-path>.status` capturing the assignee
# process exit outcome.
#
# CONSUMER CONTRACT (cli/commands/wait_handoff.py :: _status_exit_code):
#   - Path:   <project-root>/reports/REPORT-<NN-NNN>.md.status
#             (i.e. the expected report path with a ".status" suffix).
#   - Format: newline-separated `key=value` lines. The consumer parses
#             `state` and `exit_code`; it treats `state=exited` with a
#             NON-ZERO `exit_code` as the crash signal (status `failed`) and
#             ignores every other key. A zero exit_code is NOT a crash.
#   - Absence is valid: wait-handoff falls back to the report-only path, so
#     this helper NEVER fails the wrapper — all errors degrade to a no-op.
#
# Fields written (see wrappers/README.md for the full schema):
#   state=exited           always, once the assignee process has exited
#   exit_code=<int>        the assignee exit code (124 for a timeout kill)
#   reason=clean|error|timeout
#                          human/diagnostic distinction; ignored by the
#                          consumer, which keys off exit_code alone
#
# No secrets or environment data are ever written — only the three fields
# above, all derived from the exit outcome.

# Derive the status-file path wait-handoff expects from the prompt path.
# Echoes the absolute "<report-path>.status" path, or an empty string when
# the path cannot be derived (prompt outside a Cartopian project layout, or
# no NN-NNN id in the filename). Pure: runs in a subshell, never cd's the
# caller. Args: $1 = prompt path (absolute or relative).
cartopian_status_path() {
  local prompt_path="$1"
  [ -n "$prompt_path" ] || { echo ""; return 0; }

  local prompt_dir prompt_abs prompts_dir project_dir base id
  prompt_dir="$(cd "$(dirname "$prompt_path")" 2>/dev/null && pwd -P)" || { echo ""; return 0; }
  prompt_abs="$prompt_dir/$(basename "$prompt_path")"
  prompts_dir="$(dirname "$prompt_abs")"
  project_dir="$(dirname "$prompts_dir")"

  # Prompts always live at <project>/prompts/PROMPT-*.md; the sibling
  # reports/ dir is where wait-handoff derives the report (and status) path.
  [ "$(basename "$prompts_dir")" = "prompts" ] || { echo ""; return 0; }

  base="$(basename "$prompt_abs")"
  if [[ "$base" =~ ([0-9]{2}-[0-9]{3}) ]]; then
    id="${BASH_REMATCH[1]}"
  else
    echo ""
    return 0
  fi

  echo "${project_dir}/reports/REPORT-${id}.md.status"
}

# Write the status file capturing the assignee exit outcome. A best-effort,
# fail-open producer: any error (unwritable dir, missing path) degrades to a
# no-op so the wrapper's own exit code is never disturbed.
# Args:
#   $1 = status path (from cartopian_status_path; empty => skip)
#   $2 = assignee exit code (integer)
#   $3 = "true" when an OS-level timeout wrapped the command, else "false"
cartopian_write_status() {
  local status_path="$1" code="$2" timeout_applied="$3"
  [ -n "$status_path" ] || return 0
  [ -n "$code" ] || return 0

  local reason
  if [ "$code" -eq 0 ] 2>/dev/null; then
    reason="clean"
  elif [ "$timeout_applied" = "true" ] && [ "$code" -eq 124 ] 2>/dev/null; then
    # coreutils `timeout` returns 124 when it kills the child at the
    # deadline (CONVENTIONS.md § Handoffs). Distinguish it from a plain
    # non-zero exit while keeping the consumer-visible exit_code non-zero.
    reason="timeout"
  else
    reason="error"
  fi

  local dir tmp
  dir="$(dirname "$status_path")"
  mkdir -p "$dir" 2>/dev/null || return 0
  tmp="${status_path}.tmp.$$"
  {
    printf 'state=exited\n'
    printf 'exit_code=%s\n' "$code"
    printf 'reason=%s\n' "$reason"
  } >"$tmp" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 0; }
  # Atomic publish so wait-handoff never observes a half-written file.
  mv -f "$tmp" "$status_path" 2>/dev/null || { rm -f "$tmp" 2>/dev/null; return 0; }
  return 0
}
