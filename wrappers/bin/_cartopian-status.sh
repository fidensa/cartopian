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

# --- Reviewer live-evidence re-capture (TASK-03-007) ------------------------
# Agent-agnostic, opt-in, evidence-gated reviewer-role launch contract. The
# recapture capability attaches to the REVIEWER ROLE via a role-level signal
# with NO agent name in it: CARTOPIAN_REVIEW_RECAPTURE. Every shipped wrapper
# (cartopian-claude / -codex / -gemini / -devin) honors it identically by
# sourcing this helper and calling these two functions, so adding a new agent
# wrapper inherits the behavior for free.
#
# WHEN it is enabled (opt-in + evidence-gated): the agent-neutral launcher
# `cartopian dispatch --recapture` exports CARTOPIAN_REVIEW_RECAPTURE=1 ONLY for
# a reviewer handoff on a task that declares live/harness evidence
# (`Evidence gate: required`). A reviewer handoff for a task with no such gate
# (research / ops / creative reviews included) never carries the signal, so these
# helpers are a no-op and the review is completely unaffected — no network, no
# scratch-scope change. Default unset = off.
#
# WHAT it grants (narrow, documented): the reviewed source work root is treated
# READ-ONLY — it is never added to the agent's writable scope — so a reviewer
# cannot edit the implementation it reviews (the review-integrity boundary that
# is the whole point). The writable scope stays exactly the launch cwd plus
# $TMPDIR/tmp, where the probe harness relocates its runtime home and writes the
# fresh evidence. Network egress is granted so model-backed probes can be
# re-run; egress is added ONLY and never widens the writable filesystem scope.

# Return 0 (active) when the agent-neutral reviewer-recapture signal is set
# truthy (1/true/yes/on, case-insensitive), else 1. Pure; reads only the env.
cartopian_review_recapture_active() {
  case "$(printf '%s' "${CARTOPIAN_REVIEW_RECAPTURE:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *)             return 1 ;;
  esac
}

# Print the exact recapture scope contract to stderr (operator-visible launch
# documentation and audit trail). Identical wording for every wrapper.
# Args: $1 = wrapper name, $2 = launch cwd, $3 = newline-separated read-only
# work roots (may be empty).
cartopian_review_recapture_banner() {
  local wrapper="$1" cwd="$2" roots="$3"
  echo "${wrapper}: reviewer live-evidence recapture mode (CARTOPIAN_REVIEW_RECAPTURE) — agent-neutral, opt-in." >&2
  echo "  Writable scope: the launch cwd (${cwd}) PLUS \$TMPDIR/tmp only — the probe" >&2
  echo "  harness scratch (relocated runtime home + fresh evidence). The reviewed" >&2
  echo "  source work root is READ-ONLY and is NOT added to the writable scope, so a" >&2
  echo "  reviewer cannot edit the implementation it reviews. Network egress is" >&2
  echo "  granted for live probe re-capture; it adds egress ONLY and does not widen" >&2
  echo "  the writable filesystem scope." >&2
  if [[ -n "$roots" ]]; then
    while IFS= read -r root; do
      [[ -z "$root" ]] && continue
      echo "  read-only source work root: $root" >&2
    done <<< "$roots"
  fi
}

# --- Work-root access guard (OQ-009 / FR-002 / NF-002) ----------------------
# The per-agent work-root scoping guard, factored HERE (not inlined per wrapper)
# so it cannot rot in one wrapper and a newly added wrapper inherits it by
# sourcing this helper and calling cartopian_enforce_work_roots. Stdlib-only.
#
# THE BUG THIS REPLACES: the per-wrapper inline version extracted the resolved
# roots with
#     WORK_ROOTS=$(python3 - <<'PY' ... PY <<<"$RC_JSON")
# a DOUBLE stdin redirect. The here-string ($RC_JSON) wins over the heredoc, so
# python's *program* became the JSON (a syntax error), WORK_ROOTS came back
# blank, and the fail-closed guard below never fired — the work-root containment
# guarantee was decorative. Passing RC_JSON through the ENVIRONMENT keeps the
# program (heredoc) and the data (env) on separate channels so extraction works.

# Echo the resolved work-root absolute paths (newline-separated) parsed from the
# resolve-config JSON passed as $1. Empty output when the JSON is absent,
# unparseable, or declares no work roots. Pure; reads RC_JSON from the env it
# sets for the child, never from a second stdin redirect.
cartopian_extract_work_roots() {
  local rc_json="$1"
  [ -n "$rc_json" ] || { echo ""; return 0; }
  CARTOPIAN_RC_JSON="$rc_json" python3 - <<'PY'
import os, json
try:
    rec = json.loads(os.environ.get("CARTOPIAN_RC_JSON", ""))
except Exception:
    print("")
    raise SystemExit(0)
wr = rec.get("work_roots") or {}
if isinstance(wr, dict) and wr:
    print("\n".join(str(p) for p in wr.values()))
else:
    print("")
PY
}

# Per-tool native union scoping (TASK-03-009 / P03-FIX-005) --------------------
# The fail-closed guard above (TASK-03-008) refused to launch whenever any work
# root was declared. That is correct ONLY for a tool whose sandbox cannot scope a
# multi-directory union; for a tool that CAN (Claude Code --add-dir, codex exec
# --add-dir, gemini --include-directories), the guard should instead GRANT the
# resolved union (launch cwd + declared work-root absolute paths) to the tool
# natively and launch scoped — no blanket UNRESTRICTED bypass (DEC-006).
#
# The native mechanism differs per tool and does NOT transfer, so each is
# expressed as a per-tool HOOK the wrapper defines: a function named
# `cartopian_tool_scope_union`. A wrapper OPTS IN by defining that hook before
# calling cartopian_enforce_work_roots. The hook receives the launch cwd as $1
# and each declared work-root absolute path as $2.., and APPENDS the tool's
# native multi-directory flags to the CARTOPIAN_SCOPE_ARGS array, which the
# wrapper injects into the command it launches. The launch cwd is already the
# tool's primary writable scope (it is the cwd), so a hook typically only needs
# to add the declared roots ($2..) — the union is cwd ∪ roots.
#
# A wrapper that defines NO such hook has no native path-scoping mechanism
# (Devin), so the guard keeps failing closed (or honoring the documented
# UNRESTRICTED bypass) for it — the contract is precise, not all-or-nothing, and
# a new wrapper inherits scoping for free simply by defining the hook.

# Enforce the work-root scoping guard for a wrapper. Agent-agnostic: identical
# control flow for every wrapper. Resolves the declared work roots for the
# current launch cwd ($PWD) via `cartopian resolve-config` and sets the
# WORK_ROOTS global (newline-separated, possibly empty) so the caller's recapture
# banner can list the read-only source roots. Behavior:
#   * No resolved work roots             -> return 0 (nothing to scope).
#   * A declared root is missing on disk -> "[work-root] missing: <p>" + exit 1.
#   * Reviewer-recapture active          -> return 0. The roots are the READ-ONLY
#     source under review (TASK-03-007); the documented bypass. The guard does
#     NOT fail closed and does NOT widen writable scope (the wrapper's banner
#     documents the contract).
#   * Per-tool unrestricted bypass=true  -> proceed with an operator-visible note
#     (the documented full-access opt-out; takes precedence over native scoping).
#   * Tool can scope natively (wrapper defines cartopian_tool_scope_union) ->
#     grant the union via the hook (populates CARTOPIAN_SCOPE_ARGS) + return 0,
#     launching scoped with NO bypass.
#   * Otherwise (a non-empty multi-root set the tool cannot scope) -> FAIL CLOSED:
#     "[work-root] tool cannot scope multi-root access; set <VAR>=true ..." + exit 1.
# Sets: CARTOPIAN_SCOPE_ARGS — the native scoping flags the wrapper injects into
#       its command (empty unless the native-scoping path ran).
# Args: $1 = wrapper name, $2 = unrestricted bypass value ("true" bypasses),
#       $3 = the bypass env-var NAME (shown in the operator-facing message).
# Exits the (sourced) wrapper with status 1 on any fail-closed path.
cartopian_enforce_work_roots() {
  local wrapper="$1" unrestricted="$2" var_name="$3"
  CARTOPIAN_SCOPE_ARGS=()
  local rc_json
  rc_json="$(cartopian resolve-config "$PWD" | head -n 1)"
  WORK_ROOTS="$(cartopian_extract_work_roots "$rc_json")"
  [ -n "$WORK_ROOTS" ] || return 0

  local root
  local -a roots=()
  while IFS= read -r root; do
    [ -z "$root" ] && continue
    if [ ! -d "$root" ]; then
      echo "[work-root] missing: $root" >&2
      exit 1
    fi
    roots+=("$root")
  done <<< "$WORK_ROOTS"

  if cartopian_review_recapture_active; then
    # Reviewer recapture: declared roots are the read-only source under review.
    # Do NOT fail closed and do NOT widen writable scope (no scope args added);
    # the wrapper's banner documents the scope contract.
    return 0
  fi

  if [ "$unrestricted" = "true" ]; then
    # Documented full-access opt-out; takes precedence over native scoping.
    echo "${wrapper}: unrestricted mode enabled; proceeding without scoped grants" >&2
    return 0
  fi

  # Native union scoping: if the wrapper provides a per-tool scoping hook, grant
  # the resolved union (launch cwd + declared roots) to the tool natively and
  # launch scoped — no bypass needed.
  if declare -F cartopian_tool_scope_union >/dev/null 2>&1; then
    if cartopian_tool_scope_union "$PWD" "${roots[@]}"; then
      echo "${wrapper}: work-root union scoped natively (launch cwd + ${#roots[@]} declared work root(s)); writes confined to the union, outside-union writes refused" >&2
      for root in "${roots[@]}"; do
        echo "  scoped work root: $root" >&2
      done
      return 0
    fi
    # Hook declined (the tool cannot scope this particular union): fall through
    # to the fail-closed contract below.
  fi

  echo "[work-root] tool cannot scope multi-root access; set ${var_name}=true to bypass (dangerous)" >&2
  exit 1
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
