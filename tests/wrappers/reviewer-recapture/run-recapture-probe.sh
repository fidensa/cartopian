#!/usr/bin/env bash
# run-recapture-probe.sh — reviewer live-evidence recapture probe (TASK-03-007).
#
# Simulates a reviewer independently RE-RUNNING an evidence-gated task's probe
# harness instead of trusting the assignee's pinned artifacts. It models the two
# properties the agent-neutral, opt-in, evidence-gated reviewer-recapture launch
# contract grants (and only those):
#
#   * SCRATCH RELOCATION under $TMPDIR — the relocated runtime home and the fresh
#     evidence are written under a writable $TMPDIR/tmp scratch, never into the
#     reviewed source. The reviewed source (the pinned baseline) is READ-ONLY: the
#     probe reads it and never writes it.
#   * EGRESS — re-running a model-backed probe needs network. Without the
#     agent-neutral recapture grant (CARTOPIAN_REVIEW_RECAPTURE) there is no
#     egress, so the probe FAILS before producing any fresh evidence (the red
#     state the contract exists to clear).
#
# Stdlib/coreutils only (NF-001). Echoes the fresh-evidence path on success.
#
# Env:
#   RECAPTURE_BASELINE   (required) path to the pinned baseline evidence — the
#                        read-only source under review.
#   RECAPTURE_SCRATCH    (optional) scratch dir for the fresh evidence; defaults
#                        to a per-pid dir under $TMPDIR/tmp.
#   CARTOPIAN_REVIEW_RECAPTURE  the agent-neutral recapture grant (egress proxy).
# Args:
#   $1  (optional) a per-run nonce so successive re-captures are DISTINCT from
#       the pinned baseline and from each other.
set -euo pipefail

BASELINE="${RECAPTURE_BASELINE:?RECAPTURE_BASELINE (read-only pinned evidence) required}"
NONCE="${1:-fresh}"

if [[ ! -r "$BASELINE" ]]; then
  echo "recapture-probe: baseline not readable: $BASELINE" >&2
  exit 2
fi

# Egress gate. A genuine probe re-run calls the model over the network; without
# the recapture grant there is no egress, so it cannot produce fresh evidence.
case "$(printf '%s' "${CARTOPIAN_REVIEW_RECAPTURE:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on) : ;;
  *)
    echo "recapture-probe: no egress (CARTOPIAN_REVIEW_RECAPTURE not active) — cannot re-run the model-backed probe; no fresh evidence produced" >&2
    exit 3
    ;;
esac

SCRATCH="${RECAPTURE_SCRATCH:-${TMPDIR:-/tmp}/cartopian-recapture-$$}"
mkdir -p "$SCRATCH"

# Read the read-only baseline; write FRESH evidence to the relocated scratch.
base="$(cat "$BASELINE")"
fresh="$SCRATCH/fresh-evidence.txt"
{
  printf 'fresh-recapture nonce=%s\n' "$NONCE"
  printf 'reproduced-from-baseline: %s\n' "$base"
} > "$fresh"

echo "recapture-probe: wrote fresh evidence to $fresh (read-only source baseline left untouched)" >&2
echo "$fresh"
