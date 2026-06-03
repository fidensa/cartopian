#!/usr/bin/env bash
# determine-cascade-tier.sh — captured FORCING EVIDENCE for the cascade harness
# (TASK-03-003 / FR-010 + FR-007 + FR-011 + FR-009).
#
# Cascade (the Windsurf agent, now Cognition/Devin) is the one Phase-03 harness
# that proves UNPROMOTABLE: Cartopian cannot ship a genuine Tier-1 floor launch
# profile or a Tier-2 native-sandbox depth profile for it, because the harness
# exposes neither mechanism. The promotion model every other harness uses is:
#
#     exec <first-party-harness-binary> <hard-coded, non-overridable floor flags>
#       beneath the harness's OWN native OS sandbox (seatbelt / Landlock / …).
#
# Cascade satisfies NONE of the three preconditions that model needs, so instead
# of shipping sham assets (which would make `_harness_tier` falsely report
# `tier-1-2` and would break the suite's no-regression guarantee), this harness
# CAPTURES the forcing evidence that records WHY cascade stays `tier-3` and is
# classified `not-recommended-as-PM-host`. This is the FR-011 evidence the
# evidence gate's "unpromotable" branch requires — a finding, not a silent
# deferral.
#
# It is deterministic and environment-independent: the forcing facts are
# architectural (true whether or not any cascade/windsurf binary is on PATH), so
# the determination does not depend on a contained cascade runtime existing to
# probe — there is none to probe, which is precisely facet F-C1.
#
# Fail-closed: the harness REFUSES to emit a promotable verdict. If a future
# checkout ever ships a `cartopian-cascade-pm` floor + `sandbox-cascade-pm-depth`
# depth profile (i.e. a real mechanism was found), this harness exits non-zero so
# the not-recommended finding can never silently mask a real promotion.
#
# Usage:   ./determine-cascade-tier.sh
# Output:  evidence/cascade-tier-determination.txt   (the captured artifact)
# Exit:    0 = forcing evidence captured AND verified on disk (cascade is
#              unpromotable, as recorded)
#          1 = INCONSISTENT (assets unexpectedly present) — re-evaluate, do not trust
#          3 = EVIDENCE WRITE FAILURE — the artifact could not be written/updated
#              (unwritable destination, failed write, empty/missing after publish).
#              This is a HARD failure for an evidence-gate-required task: a "captured"
#              marker is NEVER printed unless the artifact was actually written, so a
#              stale or unwritten capture can never masquerade as a clean run.
#
# stdlib only (bash + python3); no third-party packages (NF-001).

set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HARNESS_DIR}/../../.." && pwd)"   # tool-repo work root: .../cartopian
EVID_DIR="${HARNESS_DIR}/evidence"
OUT="${EVID_DIR}/cascade-tier-determination.txt"

# Fail closed BEFORE any work if the evidence destination cannot be established.
# An evidence-gate-required task must never report a capture without a writable
# place to land it.
if ! mkdir -p "$EVID_DIR" 2>/dev/null; then
  echo "determine-cascade-tier.sh: FATAL — cannot create evidence dir ${EVID_DIR}" >&2
  echo "  Evidence destination is not writable; refusing to report a capture (fail-closed)." >&2
  exit 3
fi

FLOOR="${REPO_ROOT}/wrappers/bin/cartopian-cascade-pm"
DEPTH="${REPO_ROOT}/wrappers/etc/sandbox-cascade-pm-depth.json"

# --- Asset presence (the asset-driven detection contract, TASK-02-001) --------
floor_present=no; [[ -f "$FLOOR" ]] && floor_present=yes
depth_present=no; [[ -f "$DEPTH" ]] && depth_present=yes

# --- First-party headless cascade runtime on PATH? ----------------------------
# A genuine floor wrapper must `exec` a first-party, scriptable harness binary.
# Cascade ships none; the only headless options are third-party (NF-001-barred).
cascade_bin="absent"; command -v cascade  >/dev/null 2>&1 && cascade_bin="present:$(command -v cascade)"
windsurf_bin="absent"; command -v windsurf >/dev/null 2>&1 && windsurf_bin="present:$(command -v windsurf)"

# --- The asset-driven tier as `_harness_tier` reports it (no classifier edit) --
tier_report="$(cd "$REPO_ROOT" && python3 - <<'PY'
from cli.commands import _harness_tier as ht
r = ht.classify_harness_tier("cascade")
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
TMP="$(mktemp "${EVID_DIR}/.cascade-tier-determination.XXXXXX" 2>/dev/null)" || {
  echo "determine-cascade-tier.sh: FATAL — cannot create temp file in ${EVID_DIR}" >&2
  echo "  Evidence destination is not writable; refusing to report a capture (fail-closed)." >&2
  exit 3
}
# Always clean up the temp file unless we successfully publish it (trap cleared below).
trap 'rm -f "$TMP"' EXIT

if ! {
  echo "CASCADE HARNESS TIER DETERMINATION (TASK-03-003 — captured forcing evidence)"
  echo "captured-by: tests/wrappers/pm-cascade/determine-cascade-tier.sh"
  echo "harness:     cascade (Windsurf agent; vendor Codeium → Cognition/Devin)"
  echo
  echo "VERDICT: NOT_PROMOTABLE — classification = not-recommended-as-PM-host"
  echo "ENFORCEABLE TIER: ${tier} (advisory)   [asset-driven: ${tier_report}]"
  echo
  echo "ASSET STATE (TASK-02-001 asset-driven detection contract):"
  echo "  Tier-1 floor launch profile  ${FLOOR}"
  echo "    present: ${floor_present}   (expected: no — no genuine mechanism to ship)"
  echo "  Tier-2 native-sandbox depth  ${DEPTH}"
  echo "    present: ${depth_present}   (expected: no — cascade has no native sandbox)"
  echo
  echo "FIRST-PARTY HEADLESS RUNTIME ON PATH (informational; verdict is architectural):"
  echo "  cascade  : ${cascade_bin}"
  echo "  windsurf : ${windsurf_bin}"
  echo
  echo "FORCING FACETS (harness-level evidence; FR-011) — each is a structural"
  echo "reason cascade cannot reach the Tier-1+2 the promotion model requires:"
  echo
  echo "  F-C1  NO FIRST-PARTY CONTAINABLE RUNTIME."
  echo "        Cascade is the agent embedded in the Windsurf Electron IDE. There is"
  echo "        no first-party, scriptable cascade binary Cartopian can wrap with a"
  echo "        hard-coded, non-overridable floor launch profile (the 'exec <harness>"
  echo "        <floor flags>' shape cartopian-claude-pm / -codex-pm / -gemini-pm all"
  echo "        use). The only headless options are THIRD-PARTY and barred by NF-001:"
  echo "          * staronelabs/windsurf-cli ('wsc') — drives the Windsurf GUI via"
  echo "            AppleScript (macOS-only); a bridge to the FULL-capability agent,"
  echo "            not a containment surface."
  echo "          * pfcoperez/windsurfinabox — packages Windsurf in a Docker image;"
  echo "            a BUNDLED sandbox, explicitly disallowed by NF-001."
  echo "        The official first-party Windsurf terminal CLI is 'Devin for Terminal'"
  echo "        — that is the SEPARATE 'devin' harness (TASK-03-004), not cascade."
  echo
  echo "  F-C2  NO TIER-1 FLOOR MECHANISM (cannot scope the toolset / deny shell+write)."
  echo "        Cascade exposes no launch-time flag, env var, or config file that"
  echo "        removes its built-in file-edit and shell tools and restricts the agent"
  echo "        to a single MCP server — the analogue of claude '--tools \"\"', codex"
  echo "        features.shell_tool=false, or gemini system-settings tools.exclude."
  echo "        Per-tool toggling exists ONLY as an interactive GUI panel and only"
  echo "        filters MCP-SERVER tools; it cannot remove cascade's built-in"
  echo "        edit/write/shell tools. The DEC-001/FR-002 capability floor therefore"
  echo "        cannot be hard-coded for cascade."
  echo
  echo "  F-C3  NO TIER-2 NATIVE SANDBOX (FR-007 has no mechanism to drive)."
  echo "        Cascade's only command-execution control is the allow/deny-list +"
  echo "        auto-execution-level model (Disabled / Allowlist / Auto / Turbo) —"
  echo "        application-layer command-STRING matching, NOT an OS sandbox. There is"
  echo "        no seatbelt/sandbox-exec, Landlock, or container layer to drive."
  echo "        Cascade and its MCP servers run with the FULL PERMISSIONS of the"
  echo "        launching process; there is no documented filesystem write boundary or"
  echo "        workspace restriction, so the product repo and work roots are fully"
  echo "        reachable. The control is not fail-closed: 'Auto' mode defers to the"
  echo "        model's own safety judgement (the advisory posture Tier-1/2 replaces),"
  echo "        and a string-prefix denylist is bypassable (quoting, path indirection,"
  echo "        alternate binaries)."
  echo
  echo "REACHABLE FILESYSTEM (FR-011 facet): UNBOUNDED — full user-privilege reach,"
  echo "  including ${REPO_ROOT} (work root) and the product repo. No floor removes"
  echo "  the write tools; no native sandbox denies the paths."
  echo "EXPOSED TOOL SET (FR-011 facet): UNBOUNDED — built-in edit/write/shell tools"
  echo "  cannot be withheld; the Cartopian-only scoping every other floor enforces"
  echo "  has no cascade mechanism."
  echo "IN-RUNTIME PROHIBITED ATTEMPTS (FR-011 facet): NOT EXERCISABLE as 'blocked' —"
  echo "  there is no contained cascade runtime to run them against (facet F-C1), and"
  echo "  uncontained they would all SUCCEED (no floor, no sandbox). That absence is"
  echo "  itself the forcing evidence: the negative test has no profile to exercise."
  echo
  echo "CONCLUSION: cascade is recorded not-recommended-as-PM-host at ${tier}"
  echo "  (advisory). No floor/depth assets are shipped, so _harness_tier honestly"
  echo "  keeps cascade at tier-3 with NO classifier edit (TASK-02-001 contract) and"
  echo "  the suite's no-regression guarantee (NF-004) holds. See FINDINGS.md for the"
  echo "  cited sources and docs/COMPATIBILITY.md for the matrix entry."
} > "$TMP"; then
  echo "determine-cascade-tier.sh: FATAL — failed to write evidence artifact to ${TMP}" >&2
  echo "  Refusing to report a capture without a successfully written artifact (fail-closed)." >&2
  exit 3
fi

# The artifact must be non-empty before we trust or publish it.
if [[ ! -s "$TMP" ]]; then
  echo "determine-cascade-tier.sh: FATAL — evidence artifact wrote empty to ${TMP}" >&2
  echo "  Refusing to publish an empty capture (fail-closed)." >&2
  exit 3
fi
chmod 0644 "$TMP" 2>/dev/null || true   # mktemp defaults to 0600; keep the artifact world-readable

# --- Fail-closed guard: never emit a promotable verdict ----------------------
# Checked BEFORE publishing so an inconsistent state never lands a NOT_PROMOTABLE
# artifact on disk.
if [[ "$floor_present" == "yes" || "$depth_present" == "yes" || "$tier" != "tier-3" ]]; then
  echo "determine-cascade-tier.sh: INCONSISTENT — cascade assets/tier no longer match the" >&2
  echo "  not-recommended finding (floor=${floor_present} depth=${depth_present} tier=${tier})." >&2
  echo "  A real mechanism may have appeared; re-evaluate the classification. Refusing to" >&2
  echo "  emit a NOT_PROMOTABLE pass on inconsistent state (fail-closed)." >&2
  exit 1
fi

# Atomically publish only after a verified successful, non-empty write.
if ! mv -f "$TMP" "$OUT"; then
  echo "determine-cascade-tier.sh: FATAL — could not move evidence artifact into place (${OUT})" >&2
  echo "  Refusing to report a capture the destination did not accept (fail-closed)." >&2
  exit 3
fi
trap - EXIT   # published; do not remove the artifact on exit

# Confirm the published artifact actually exists and is non-empty on disk.
if [[ ! -s "$OUT" ]]; then
  echo "determine-cascade-tier.sh: FATAL — published artifact missing or empty after write (${OUT})" >&2
  echo "  Refusing to report a capture that is not present on disk (fail-closed)." >&2
  exit 3
fi

echo "determine-cascade-tier.sh: forcing evidence captured -> ${OUT}"
echo "determine-cascade-tier.sh: cascade = not-recommended-as-PM-host at ${tier} (NOT_PROMOTABLE)"
exit 0
