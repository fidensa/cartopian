#!/usr/bin/env bash
# determine-devin-tier.sh — captured FORCING EVIDENCE for the devin harness
# (TASK-03-004 / FR-010 + FR-007 + FR-011 + FR-009). Resolves OQ-004 for devin.
#
# "Devin for Terminal" (Cognition) is a LOCAL-first / cloud-hybrid coding agent.
# Unlike cascade — which has NO containment mechanism at all (no first-party
# runtime, no floor flag, no native sandbox) — devin DOES ship partial local
# mechanisms: a config-driven `permissions` allow/deny/ask system and a
# fail-closed OS-level `--sandbox`. The honest question this harness answers is
# therefore NOT "does any mechanism exist?" but "do devin's partial mechanisms
# combine into a GENUINE, VERIFIABLE, NON-ESCAPABLE Tier-1+2 — the layered shape
#
#     exec <first-party harness binary> <hard-coded, non-overridable floor flags>
#          beneath the harness's OWN native OS sandbox (seatbelt / Landlock / …)
#
# that claude / codex / gemini all instantiate?" The answer is NO: five forcing
# facets (F-D1..F-D5) each independently block that shape. So rather than shipping
# floor + depth assets that would make the asset-driven `_harness_tier` falsely
# report `tier-1-2` (a containment guarantee that cannot be verified and does not
# hold), this harness CAPTURES the forcing evidence that records WHY devin stays
# `tier-3` and is classified `not-recommended-as-PM-host`. This is the FR-011
# evidence the evidence gate's "unpromotable" branch requires — a finding, not a
# silent deferral.
#
# It is deterministic and environment-independent: the forcing facts are
# architectural / documented product behaviour (true whether or not any `devin`
# binary is on PATH and without an authenticated Cognition session), so the
# determination does not depend on a contained devin runtime existing to probe —
# there is none to probe locally, which is precisely facet F-D5.
#
# Fail-closed: the harness REFUSES to emit a promotable verdict. If a future
# checkout ever ships a `cartopian-devin-pm` floor + `sandbox-devin-pm-depth`
# depth profile (i.e. a real, verifiable mechanism was found), this harness exits
# non-zero so the not-recommended finding can never silently mask a real promotion.
#
# Usage:   ./determine-devin-tier.sh
# Output:  evidence/devin-tier-determination.txt   (the captured artifact)
# Exit:    0 = forcing evidence captured AND verified on disk (devin is
#              unpromotable as a contained PM host, as recorded)
#          1 = INCONSISTENT (assets unexpectedly present / tier no longer tier-3)
#              — re-evaluate, do not trust
#          3 = EVIDENCE WRITE FAILURE — the artifact could not be written/updated
#              (unwritable destination, failed write, empty/missing after publish).
#              This is a HARD failure for an evidence-gate-required task: a "captured"
#              marker is NEVER printed unless the artifact was actually written, so a
#              stale or unwritten capture can never masquerade as a clean run.
#
# stdlib only (bash + python3); no third-party packages, no bundled sandbox (NF-001).

set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HARNESS_DIR}/../../.." && pwd)"   # tool-repo work root: .../cartopian
EVID_DIR="${HARNESS_DIR}/evidence"
OUT="${EVID_DIR}/devin-tier-determination.txt"

# Fail closed BEFORE any work if the evidence destination cannot be established.
# An evidence-gate-required task must never report a capture without a writable
# place to land it.
if ! mkdir -p "$EVID_DIR" 2>/dev/null; then
  echo "determine-devin-tier.sh: FATAL — cannot create evidence dir ${EVID_DIR}" >&2
  echo "  Evidence destination is not writable; refusing to report a capture (fail-closed)." >&2
  exit 3
fi

FLOOR="${REPO_ROOT}/wrappers/bin/cartopian-devin-pm"
DEPTH="${REPO_ROOT}/wrappers/etc/sandbox-devin-pm-depth.json"

# --- Asset presence (the asset-driven detection contract, TASK-02-001) --------
floor_present=no; [[ -f "$FLOOR" ]] && floor_present=yes
depth_present=no; [[ -f "$DEPTH" ]] && depth_present=yes

# --- First-party devin runtime on PATH? ---------------------------------------
# Informational only: devin DOES ship a first-party headless CLI (`devin -p`),
# so unlike cascade the verdict is NOT "no runtime exists". The forcing facts are
# the SHAPE of that runtime (cloud-hybrid, approval-only floor, no settings-file
# flag), not the binary's absence. The contained-PM probe still cannot run here:
# devin is a cloud-authenticated agent whose model + handoff/subagents execute in
# Cognition's cloud, so there is no offline locally-contained runtime to exercise.
devin_bin="absent"; command -v devin >/dev/null 2>&1 && devin_bin="present:$(command -v devin)"

# --- The asset-driven tier as `_harness_tier` reports it (no classifier edit) --
tier_report="$(cd "$REPO_ROOT" && python3 - <<'PY'
from cli.commands import _harness_tier as ht
r = ht.classify_harness_tier("devin")
print(f"{r.tier}|constrained={r.constrained}|floor={r.floor_profile_present}|depth={r.depth_profile_present}")
PY
)"
tier="${tier_report%%|*}"

# --- Write the captured forcing-evidence artifact (atomic + fail-closed) ------
# Write to a temp file in the SAME directory and publish with an atomic `mv`
# only after a verified successful, non-empty write. Any failure to write/update
# the artifact (unwritable destination, short write, empty result) is a HARD
# failure (exit 3) — the "captured" marker below is unreachable unless the file
# actually landed on disk, so a stale or unwritten capture can never pass.
TMP="$(mktemp "${EVID_DIR}/.devin-tier-determination.XXXXXX" 2>/dev/null)" || {
  echo "determine-devin-tier.sh: FATAL — cannot create temp file in ${EVID_DIR}" >&2
  echo "  Evidence destination is not writable; refusing to report a capture (fail-closed)." >&2
  exit 3
}
# Always clean up the temp file unless we successfully publish it (trap cleared below).
trap 'rm -f "$TMP"' EXIT

if ! {
  echo "DEVIN HARNESS TIER DETERMINATION (TASK-03-004 — captured forcing evidence)"
  echo "captured-by: tests/wrappers/pm-devin/determine-devin-tier.sh"
  echo "harness:     devin ('Devin for Terminal', Cognition — local-first/cloud-hybrid CLI)"
  echo
  echo "VERDICT: NOT_PROMOTABLE — classification = not-recommended-as-PM-host"
  echo "ENFORCEABLE TIER: ${tier} (advisory)   [asset-driven: ${tier_report}]"
  echo
  echo "ASSET STATE (TASK-02-001 asset-driven detection contract):"
  echo "  Tier-1 floor launch profile  ${FLOOR}"
  echo "    present: ${floor_present}   (expected: no — no genuine/verifiable mechanism to ship)"
  echo "  Tier-2 native-sandbox depth  ${DEPTH}"
  echo "    present: ${depth_present}   (expected: no — see F-D3/F-D5)"
  echo
  echo "FIRST-PARTY HEADLESS RUNTIME ON PATH (informational; verdict is architectural):"
  echo "  devin : ${devin_bin}"
  echo "  NOTE: devin DOES ship a first-party headless CLI ('devin -p'), so unlike"
  echo "  cascade the finding is NOT 'no runtime exists'. devin even ships PARTIAL"
  echo "  containment mechanisms (a config 'permissions' allow/deny/ask system and a"
  echo "  fail-closed OS-level '--sandbox'). It is recorded not-recommended because"
  echo "  those PARTIAL mechanisms cannot be combined into a genuine, verifiable,"
  echo "  non-escapable, LAYERED Tier-1+2 — for the five forcing reasons below."
  echo
  echo "FORCING FACETS (harness-level evidence; FR-011) — each independently blocks"
  echo "the layered 'floor beneath native sandbox' shape claude/codex/gemini use:"
  echo
  echo "  F-D1  CLOUD HANDOFF + CLOUD SUBAGENTS ESCAPE (the dominant residual)."
  echo "        The local Devin terminal agent exposes a '/handoff' command that"
  echo "        packages the conversation context + current git branch and CREATES A"
  echo "        CLOUD DEVIN SESSION 'with its own computer' that picks up the work —"
  echo "        and a subagent/delegation surface that runs work in foreground/"
  echo "        background. The cloud session runs 'in its own sandbox, not yours':"
  echo "        OUTSIDE the local OS '--sandbox' and OUTSIDE the local 'permissions'"
  echo "        floor. No documented config key disables /handoff or cloud delegation."
  echo "        The local '--sandbox' OS-enforced limits on 'what files and domains"
  echo "        the agent can touch' DO NOT extend to that cloud computer. This is a"
  echo "        config-irremovable, OS-unsandboxable execution + data-exfiltration"
  echo "        surface — analogous to (and broader than) codex's server-side"
  echo "        web_search residual (F1b): a full cloud machine with shell/write/net,"
  echo "        not just web search."
  echo
  echo "  F-D2  NO CAPABILITY-FLOOR MECHANISM (cannot remove built-in tools / scope MCP)."
  echo "        devin's ONLY Tier-1 control is the config 'permissions' allow/deny/ask"
  echo "        system — tool-level PATTERN MATCHING (Read()/Write()/Exec()/"
  echo "        mcp__server__tool). There is NO analogue of claude '--tools \"\"',"
  echo "        gemini system-settings 'tools.exclude', or codex"
  echo "        'features.shell_tool=false' that REMOVES devin's built-in"
  echo "        edit/write/shell/read tools from the model surface, and NO key that"
  echo "        restricts the agent to a single MCP server or disables built-in tools."
  echo "        The floor is therefore an APPROVAL GATE over an unbounded tool surface,"
  echo "        not a capability floor — it cannot reach the Cartopian-only /"
  echo "        no-shell-tool surface DEC-001/FR-002 requires."
  echo
  echo "  F-D3  TIER-1 FLOOR AND TIER-2 SANDBOX ARE MUTUALLY EXCLUSIVE (cannot layer)."
  echo "        '--sandbox' auto-selects — and ONLY permits — the 'autonomous'"
  echo "        permission mode, which auto-approves tool calls and grants 'the"
  echo "        ability to run ANY shell command within an OS-level sandbox'. So a"
  echo "        deny-shell / deny-write APPROVAL floor cannot be layered BENEATH the"
  echo "        native OS sandbox the way claude layers '--tools \"\"' beneath seatbelt"
  echo "        (or codex layers features-off beneath '-s read-only'). Enabling the OS"
  echo "        sandbox REPLACES the approval floor with 'auto-approve within the box'."
  echo "        Neither single posture is a genuine floor+depth: approval-only is"
  echo "        bypassable and NOT OS-enforced; sandbox-only auto-approves shell"
  echo "        WITHIN the box AND still leaves F-D1 (cloud handoff) wide open. (The"
  echo "        '--sandbox' feature is itself documented UNSTABLE.)"
  echo
  echo "  F-D4  NO NON-OVERRIDABLE INJECTION PATH (no settings-file flag)."
  echo "        devin exposes NO '--config'/'--settings' flag and no highest-precedence"
  echo "        settings env var — unlike claude '--settings <file>', gemini"
  echo "        GEMINI_CLI_SYSTEM_SETTINGS_PATH, or codex CODEX_HOME. Config is read"
  echo "        only from ~/.config/devin/config.json, .devin/config.json, and"
  echo "        .devin/config.local.json (precedence local > project > user). The only"
  echo "        'highest precedence' is the cwd-local .devin/config.local.json, and"
  echo "        'read_config_from' will IMPORT permissive cursor/windsurf/claude"
  echo "        configs unless each is explicitly disabled. A hard-coded,"
  echo "        non-overridable floor launch profile therefore cannot be guaranteed —"
  echo "        the floor is only as fixed as the launch cwd, which the invoker controls."
  echo
  echo "  F-D5  NO CONTAINED LOCAL RUNTIME TO CAPTURE FR-011 IN-RUNTIME EVIDENCE."
  echo "        Devin for Terminal is a CLOUD-AUTHENTICATED hybrid agent: its model"
  echo "        runs in Cognition's cloud, and /handoff + subagents execute in the"
  echo "        cloud. There is no offline, locally-contained devin PM runtime to run"
  echo "        the FR-011 in-runtime prohibited-attempt probes against (product-repo /"
  echo "        work-root / non-allowlisted write, ../symlink escape, shell spawn,"
  echo "        exec, exec-bit set, config write) and PROVE fail-closed refusals. The"
  echo "        codex and gemini tier-1-2 promotions were each GATED on captured LIVE"
  echo "        in-runtime evidence; devin cannot meet that gate here. Shipping"
  echo "        floor+depth assets would flip _harness_tier to tier-1-2 with ZERO"
  echo "        guaranteeing evidence — exactly the sham the cascade precedent forbids."
  echo
  echo "REACHABLE FILESYSTEM (FR-011 facet): NOT VERIFIABLY BOUNDED for a contained PM."
  echo "  Locally, '--sandbox' (autonomous) can OS-deny writes outside the workspace,"
  echo "  but it cannot be layered beneath the capability floor (F-D3), cannot be"
  echo "  injected non-overridably (F-D4), and is bypassed entirely by the cloud"
  echo "  handoff/subagents (F-D1) whose filesystem is a cloud machine outside any"
  echo "  Cartopian control. There is no contained runtime to demonstrate the bound"
  echo "  (F-D5)."
  echo "EXPOSED TOOL SET (FR-011 facet): UNBOUNDED at the floor — built-in"
  echo "  edit/write/shell/read tools cannot be REMOVED and the agent cannot be scoped"
  echo "  to the Cartopian MCP set; only deny rules + the (mutually-exclusive) OS"
  echo "  sandbox gate them (F-D2)."
  echo "IN-RUNTIME PROHIBITED ATTEMPTS (FR-011 facet): NOT EXERCISABLE as 'blocked' for"
  echo "  a CONTAINED devin PM — there is no offline contained runtime to run them"
  echo "  against (F-D5), and the cloud-handoff path escapes any local boundary"
  echo "  regardless (F-D1). That absence is itself the forcing evidence: the negative"
  echo "  test has no genuine, verifiable contained profile to exercise."
  echo
  echo "CONCLUSION: devin is recorded not-recommended-as-PM-host at ${tier}"
  echo "  (advisory). No floor/depth assets are shipped, so _harness_tier honestly"
  echo "  keeps devin at tier-3 with NO classifier edit (TASK-02-001 contract) and the"
  echo "  suite's no-regression guarantee (NF-004) holds. WHAT WOULD CHANGE THIS: an"
  echo "  upstream devin capability to (a) hard-disable cloud /handoff + cloud"
  echo "  subagents, (b) remove built-in tools / scope to a single MCP server, (c) layer"
  echo "  the OS sandbox beneath a non-auto-approving floor, and (d) a non-overridable"
  echo "  settings-file injection path — at which point live in-runtime evidence could"
  echo "  be captured and the floor+depth assets honestly shipped. See FINDINGS.md for"
  echo "  the cited sources and docs/COMPATIBILITY.md for the matrix entry."
} > "$TMP"; then
  echo "determine-devin-tier.sh: FATAL — failed to write evidence artifact to ${TMP}" >&2
  echo "  Refusing to report a capture without a successfully written artifact (fail-closed)." >&2
  exit 3
fi

# The artifact must be non-empty before we trust or publish it.
if [[ ! -s "$TMP" ]]; then
  echo "determine-devin-tier.sh: FATAL — evidence artifact wrote empty to ${TMP}" >&2
  echo "  Refusing to publish an empty capture (fail-closed)." >&2
  exit 3
fi
chmod 0644 "$TMP" 2>/dev/null || true   # mktemp defaults to 0600; keep the artifact world-readable

# --- Fail-closed guard: never emit a promotable verdict ----------------------
# Checked BEFORE publishing so an inconsistent state never lands a NOT_PROMOTABLE
# artifact on disk.
if [[ "$floor_present" == "yes" || "$depth_present" == "yes" || "$tier" != "tier-3" ]]; then
  echo "determine-devin-tier.sh: INCONSISTENT — devin assets/tier no longer match the" >&2
  echo "  not-recommended finding (floor=${floor_present} depth=${depth_present} tier=${tier})." >&2
  echo "  A real mechanism may have appeared; re-evaluate the classification. Refusing to" >&2
  echo "  emit a NOT_PROMOTABLE pass on inconsistent state (fail-closed)." >&2
  exit 1
fi

# Atomically publish only after a verified successful, non-empty write.
if ! mv -f "$TMP" "$OUT"; then
  echo "determine-devin-tier.sh: FATAL — could not move evidence artifact into place (${OUT})" >&2
  echo "  Refusing to report a capture the destination did not accept (fail-closed)." >&2
  exit 3
fi
trap - EXIT   # published; do not remove the artifact on exit

# Confirm the published artifact actually exists and is non-empty on disk.
if [[ ! -s "$OUT" ]]; then
  echo "determine-devin-tier.sh: FATAL — published artifact missing or empty after write (${OUT})" >&2
  echo "  Refusing to report a capture that is not present on disk (fail-closed)." >&2
  exit 3
fi

echo "determine-devin-tier.sh: forcing evidence captured -> ${OUT}"
echo "determine-devin-tier.sh: devin = not-recommended-as-PM-host at ${tier} (NOT_PROMOTABLE)"
exit 0
