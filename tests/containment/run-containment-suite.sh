#!/usr/bin/env bash
# run-containment-suite.sh — the consolidated FR-011 containment verification
# suite (P01-BUILD-007). One documented run that makes "the Phase 01 containment
# suite is green" a single mechanical check.
#
# It aggregates the per-feature negative tests (it does NOT reimplement them):
# the prohibited-operation → negative-test mapping lives in
# tests/containment/manifest.py (the single source of truth), and the always-on
# aggregator tests/containment/test_fr011_containment_suite.py keeps that mapping
# complete and pins the captured Claude Code harness-level evidence.
#
# Two layers:
#
#   DEFAULT (always-on, no cost, stdlib-only) — runs, green in one shot:
#     * every prohibited-operation + lifecycle negative test named in the
#       manifest (red→green: each is red before its guard exists, green after);
#     * the FR-011 aggregator (coverage completeness, red-baseline recording,
#       harness-evidence pins, deferrals-noted, this entrypoint present).
#     The captured live evidence is pinned when present and skipped (with a
#     reproduction pointer) when absent — exactly the floor/sandbox pin posture.
#
#   --with-harness (live, cost-bearing) — additionally drives the live Claude
#   Code harness-level evidence BEFORE the default layer, so the pins above bind
#   to fresh captures:
#     * the PM-floor harness (exposed tool set is Cartopian-only + product/work
#       roots unreachable):      tests/wrappers/pm-floor/run-floor-test.sh
#     * the native-sandbox depth harness (floor-bypassed reach denied at the OS
#       layer):                  tests/wrappers/pm-sandbox/run-sandbox-test.sh
#     * the FR-001 spike probes (in-runtime prohibited attempts + still-
#       functional):             tests/wrappers/pm-runtime/run-probes.sh
#   Pass --with-red to also (re)capture the red baselines in the floor/sandbox
#   harnesses. Each underlying harness is itself fail-closed (it proves stale
#   evidence gone and refuses to PASS on an unproduced/empty transcript), so a
#   failed live capture aborts the run rather than passing on stale evidence.
#
# Usage:
#   ./run-containment-suite.sh                 # default always-on suite (green)
#   ./run-containment-suite.sh --with-harness  # capture live evidence, then suite
#   ./run-containment-suite.sh --with-harness --with-red
#
# Exit codes: 0 = green; non-zero = a negative test failed or a live capture
# could not be trusted. stdlib-only (bash + python3 + pytest, NF-001).

set -uo pipefail

SUITE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SUITE_DIR}/../.." && pwd)"     # tool-repo work root: .../cartopian

WITH_HARNESS=0
WITH_RED=0
for arg in "$@"; do
  case "$arg" in
    --with-harness) WITH_HARNESS=1 ;;
    --with-red)     WITH_RED=1 ;;
    -h|--help)
      sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "run-containment-suite.sh: unknown argument '$arg' (see --help)" >&2
      exit 2 ;;
  esac
done

cd "$REPO_ROOT"

die () { echo "FATAL: $*" >&2; echo "=== CONTAINMENT SUITE ABORTED (fail-closed) ===" >&2; exit 2; }

PYTHON="${PYTHON:-python3}"

echo "=== FR-011 containment verification suite ==="
echo "work root: $REPO_ROOT"
echo "python:    $("$PYTHON" --version 2>&1)"
echo

# --------------------------------------------------------------------------- #
# Optional live harness-level evidence capture (cost-bearing). Each harness is
# fail-closed; if one returns non-zero we abort the whole suite (no green on an
# untrusted/unproduced capture).
# --------------------------------------------------------------------------- #
if [[ "$WITH_HARNESS" -eq 1 ]]; then
  if ! command -v claude >/dev/null 2>&1; then
    die "--with-harness requires the 'claude' CLI on PATH (live capture); it was not found."
  fi
  RED_FLAG=()
  [[ "$WITH_RED" -eq 1 ]] && RED_FLAG=(--with-red)

  echo "[harness] capturing live Claude Code harness-level evidence (this calls claude)…"

  echo "[harness] 1/3 PM-floor (exposed tool set + reachable filesystem)"
  "${REPO_ROOT}/tests/wrappers/pm-floor/run-floor-test.sh" ${RED_FLAG[@]+"${RED_FLAG[@]}"} \
    || die "PM-floor harness failed — its captured evidence is untrusted"

  echo "[harness] 2/3 native-sandbox depth (floor-bypassed reach denied at OS layer)"
  "${REPO_ROOT}/tests/wrappers/pm-sandbox/run-sandbox-test.sh" ${RED_FLAG[@]+"${RED_FLAG[@]}"} \
    || die "native-sandbox depth harness failed — its captured evidence is untrusted"

  echo "[harness] 3/3 FR-001 spike (in-runtime prohibited attempts + still-functional)"
  "${REPO_ROOT}/tests/wrappers/pm-runtime/run-probes.sh" \
    || die "FR-001 spike harness failed — its captured evidence is untrusted"

  # Promoted non-reference harnesses (Phase 03). Each is captured only when its
  # CLI is present (the runner stays portable); a present-but-failing capture is
  # fatal so the pins never bind to untrusted evidence.
  if command -v codex >/dev/null 2>&1; then
    echo "[harness] +codex (TASK-03-001): exposed tool set + in-runtime prohibited attempts"
    "${REPO_ROOT}/tests/wrappers/pm-codex/run-codex-probes.sh" ${RED_FLAG[@]+"${RED_FLAG[@]}"} \
      || die "codex PM harness failed — its captured evidence is untrusted"
  else
    echo "[harness] +codex: skipped (no 'codex' CLI on PATH); codex pins skip-when-absent"
  fi

  if command -v gemini >/dev/null 2>&1; then
    echo "[harness] +gemini (TASK-03-002): exposed tool set + in-runtime prohibited attempts"
    "${REPO_ROOT}/tests/wrappers/pm-gemini/run-gemini-probes.sh" ${RED_FLAG[@]+"${RED_FLAG[@]}"} \
      || die "gemini PM harness failed — its captured evidence is untrusted"
  else
    echo "[harness] +gemini: skipped (no 'gemini' CLI on PATH); gemini pins skip-when-absent"
  fi

  echo "[harness] live capture complete; the pins below now bind to fresh evidence."
  echo
fi

# --------------------------------------------------------------------------- #
# The mechanical check: every prohibited-operation + lifecycle negative test
# named in the manifest, plus the FR-011 aggregator, green in one pytest run.
# Targets are sourced from the manifest (SSOT) so this list cannot drift.
# --------------------------------------------------------------------------- #
# Capture targets via command substitution (status-checked) then split on
# newlines — bash 3.2 compatible (no mapfile). Node ids never contain spaces.
TARGETS_RAW="$("$PYTHON" -m tests.containment.manifest)" \
  || die "could not enumerate negative-test targets from the manifest"
TARGETS=()
while IFS= read -r _line; do
  [[ -n "$_line" ]] && TARGETS+=("$_line")
done <<< "$TARGETS_RAW"
[[ "${#TARGETS[@]}" -gt 0 ]] || die "manifest produced no negative-test targets"

echo "[suite] running ${#TARGETS[@]} prohibited-operation/lifecycle negative tests + the FR-011 aggregator + per-harness promotions"
"$PYTHON" -m pytest -q \
  "${TARGETS[@]}" \
  "tests/containment/test_fr011_containment_suite.py" \
  "tests/containment/test_harness_tier_detection.py" \
  "tests/containment/test_codex_harness_promotion.py" \
  "tests/containment/test_gemini_harness_promotion.py" \
  "tests/containment/test_cascade_harness_promotion.py" \
  "tests/containment/test_devin_harness_promotion.py"
rc=$?

echo
if [[ "$rc" -eq 0 ]]; then
  echo "=== CONTAINMENT SUITE GREEN ==="
  echo "manifest:  tests/containment/manifest.py"
  echo "aggregator: tests/containment/test_fr011_containment_suite.py"
  echo "docs:      docs/CONTAINMENT-SUITE.md"
  if [[ "$WITH_HARNESS" -eq 0 ]]; then
    echo "note: captured harness-level evidence was PINNED if present, else SKIPPED."
    echo "      re-run with --with-harness to (re)capture it live."
  fi
else
  echo "=== CONTAINMENT SUITE FAILED (rc=$rc) ==="
fi
exit "$rc"
