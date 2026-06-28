#!/usr/bin/env bash
# _cartopian-status.sh — shared status-file helper for the Bash agent wrappers.
#
# Named with a leading underscore so it reads as a *library* sourced by the
# cartopian-* wrappers, not a wrapper itself (it carries no launch-cwd
# or access-grant logic). Mirrors ps1/CartopianStatus.ps1.
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

# Return 0 (success) when the file at $1 looks like a *complete* handoff report:
# present, non-empty, and carrying a top-level `Status: <complete|blocked|failed>`
# line — the authoritative completion signal `wait-handoff` keys off (it parses
# the same report). This is the producer-side proxy the wrappers use to decide a
# handoff is done; it deliberately accepts blocked/failed reports (a written
# report is a finished handoff regardless of verdict — the PM reads the verdict).
# The `<` of the template placeholder (`Status: <complete | ...>`) does NOT match,
# so an unfilled template is not mistaken for a finished report. Pure, no side
# effects; any error degrades to "not complete" (return 1).
cartopian_report_complete() {
  local report_path="$1"
  [ -n "$report_path" ] || return 1
  [ -s "$report_path" ] || return 1
  grep -Eqi '^Status:[[:space:]]*(complete|blocked|failed)([[:space:]]|$)' \
    "$report_path" 2>/dev/null
}

# Run the assignee command under the SSOT timeout, supervising for the
# authoritative report-completion signal so a *finished* assignee exits promptly
# instead of lingering until the deadline.
#
# ROOT CAUSE this addresses: `claude -p` / `codex exec` can keep running after
# they have written the report — MCP stdio servers not torn down, an inherited
# open stdin, or a trailing turn leave the process alive with no more work to do.
# The wrapper used to only `wait` for that process, so a finished handoff sat
# idle until `timeout` killed it (exit 124, reason=timeout) — a success that
# always read as a deadline failure.
#
# The fix is event-driven, NOT a second timer: the single CARTOPIAN_TIMEOUT
# deadline (enforced by `timeout`, which still wraps the command) remains the
# ONLY clock. This supervisor reacts to the report *file appearing* and, once the
# work is provably done, grants a brief grace for the child to tear itself down
# before reaping it. A genuine hang never writes a report, so it is never reaped
# early — it hits the deadline and reports `timeout` exactly as before.
#
# stdin is redirected from /dev/null so the child can never block waiting on
# inherited terminal input (one of the lingering modes).
#
# Args:  $1 = report path (empty => no supervision; run inline as before)
#        $2.. = the full command vector to run (e.g. timeout 60m claude -p ...)
# Sets:  CARTOPIAN_ASSIGNEE_EXIT — the exit code the wrapper should surface
#        CARTOPIAN_REPORT_DONE   — "true" when a complete report was observed
# Tunables (env): CARTOPIAN_REPORT_POLL (seconds between polls; default 2)
#                 CARTOPIAN_REPORT_GRACE_POLLS (post-report grace polls; default 3)
cartopian_run_supervised() {
  local report_path="$1"; shift
  local poll="${CARTOPIAN_REPORT_POLL:-2}"
  local grace_polls="${CARTOPIAN_REPORT_GRACE_POLLS:-3}"

  CARTOPIAN_REPORT_DONE=false
  CARTOPIAN_ASSIGNEE_EXIT=0

  # No derivable report path (prompt outside a project layout): run inline with
  # a closed stdin — behaviorally identical to the historical wrapper, minus the
  # inherited-stdin hang. There is nothing to supervise against.
  if [ -z "$report_path" ]; then
    "$@" </dev/null
    CARTOPIAN_ASSIGNEE_EXIT=$?
    return 0
  fi

  "$@" </dev/null &
  local child=$!

  # Report watcher (subshell): once the report is complete, give the child a
  # brief grace to exit on its own, then reap it. It reacts to the report event
  # only — it imposes no deadline of its own and self-terminates when the child
  # is gone. Its kill of a *lingering* child is what turns a deadline kill into a
  # prompt clean exit.
  (
    while kill -0 "$child" 2>/dev/null; do
      if cartopian_report_complete "$report_path"; then
        i=0
        while [ "$i" -lt "$grace_polls" ] && kill -0 "$child" 2>/dev/null; do
          sleep "$poll"
          i=$((i + 1))
        done
        kill -TERM "$child" 2>/dev/null
        sleep "$poll"
        kill -KILL "$child" 2>/dev/null
        break
      fi
      sleep "$poll"
    done
  ) &
  local watcher=$!

  # Blocks until the child exits — on its own, by the watcher's reap, or by the
  # `timeout` deadline. `wait` returns the child's status either way.
  wait "$child" 2>/dev/null
  local raw=$?

  # The watcher may still be polling (child exited on its own with no report);
  # stop it so it never outlives the handoff.
  kill -TERM "$watcher" 2>/dev/null
  wait "$watcher" 2>/dev/null

  # The report file is authoritative: if it is complete, the handoff succeeded,
  # regardless of how the lingering child was ultimately reaped (a SIGTERM/SIGKILL
  # from the watcher would otherwise surface as a non-zero/142/137 exit).
  if cartopian_report_complete "$report_path"; then
    CARTOPIAN_REPORT_DONE=true
    CARTOPIAN_ASSIGNEE_EXIT=0
  else
    CARTOPIAN_ASSIGNEE_EXIT=$raw
  fi
  return 0
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
