#!/usr/bin/env bash
# ============================================================================
# Acceptance: assignee launch scope excludes the governing project.
#
# Operator-executed acceptance for the launch-scope containment fix. It drives
# the REAL dispatch + cartopian-claude wrapper end to end, with a fake `claude`
# shim on PATH so NO live model and NO tokens are needed:
#
#     cartopian dispatch  ->  cartopian-claude  ->  (fake claude shim)
#
# It asserts, deterministically:
#   1. dispatch launches with cwd = the work root (not the governing project).
#   2. The scope handed to the agent = work roots + the report dir ONLY; the
#      governing project root is ABSENT.
#   3. The wrapper actually ran claude in the work root and passed --add-dir
#      flags matching that scope (work root + report dir, never the project).
#   4. The comment-volume directive + management-id ban were injected into the
#      prompt the agent received.
#   5. The completion report still landed in the governing project's reports/.
#
# A SEPARATE manual step (printed at the end) covers the real-agent tool-scope
# denial, which only a live harness can demonstrate. Everything here runs in a
# throwaway temp project under an isolated HOME and touches nothing real.
#
# Usage:   scripts/acceptance/launch_scope_containment.sh
# Exit:    0 = all assertions passed; 1 = at least one failed.
# ============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CARTOPIAN="$REPO/bin/cartopian"
WRAPPER="$REPO/wrappers/bin/cartopian-claude"

[ -x "$CARTOPIAN" ] || { echo "missing dev CLI: $CARTOPIAN" >&2; exit 1; }
[ -x "$WRAPPER" ]   || { echo "missing wrapper: $WRAPPER" >&2; exit 1; }

WORK="$(mktemp -d "${TMPDIR:-/tmp}/cartopian-acc.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
PROJ="$WORK/proj"
WR="$PROJ/wr"
HOME_DIR="$WORK/home"
SHIMBIN="$WORK/shimbin"
CAP="$WORK/claude-capture.json"
REPORT="$PROJ/reports/REPORT-01-001.md"
mkdir -p "$PROJ/prompts" "$PROJ/tasks/in-progress" "$PROJ/decisions" "$WR" "$HOME_DIR" "$SHIMBIN"

# --- governing-project artifacts (must end up OUT of the agent's scope) ------
printf '# Plan\n'                          > "$PROJ/IMPLEMENTATION_PLAN.md"
printf '# Requirements\nSECRET-PM-BODY\n'  > "$PROJ/REQUIREMENTS.md"
printf '# Backlog\n'                       > "$PROJ/BACKLOG.md"
printf '# State\n'                         > "$PROJ/STATE.md"
printf '# A decision\n'                    > "$PROJ/decisions/DEC-001-x.md"
printf 'product source\n'                  > "$WR/product.txt"

cat > "$PROJ/cartopian.toml" <<TOML
[project]
id = "acc-proj"
name = "Acceptance Project"
protocol_version = "v0.3.0"
work_roots = ["wr"]

[roles]
coder = "Implements tasks per spec."

[handoffs.coder]
agent = "$WRAPPER"
auto_start = true
timeout = "5m"
code_comments = "minimal"
TOML

cat > "$PROJ/cartopian.local.toml" <<TOML
[work_roots]
wr = "$WR"
TOML

cat > "$PROJ/tasks/in-progress/TASK-01-001-probe.md" <<MD
# TASK-01-001: probe
Work root: wr
Assignee: coder
MD

cat > "$PROJ/prompts/PROMPT-01-001.md" <<MD
# Probe
Report path: $REPORT

## Your task
Probe the launch scope.
MD

# --- fake claude shim: record cwd + argv, write a minimal complete report ----
# Stands in for `claude` on PATH. The wrapper invokes it as
#   claude -p [--add-dir D ...] --dangerously-skip-permissions [--model M] "<prompt>"
# so the prompt content is the final argument. The shim records everything it
# was handed, then writes the completion report the wrapper supervises for.
cat > "$SHIMBIN/claude" <<SHIM
#!/usr/bin/env bash
PROBE_CAPTURE="$CAP" python3 - "\$@" <<'PY'
import json, os, sys
json.dump({"cwd": os.getcwd(), "argv": sys.argv[1:]},
          open(os.environ["PROBE_CAPTURE"], "w"))
PY
prompt="\${@: -1}"
rp="\$(printf '%s\n' "\$prompt" | sed -n 's/^Report path:[[:space:]]*//p' | head -1)"
if [ -n "\$rp" ]; then
  mkdir -p "\$(dirname "\$rp")"
  printf 'Status: complete\n\nProbe report.\n' > "\$rp"
fi
exit 0
SHIM
chmod +x "$SHIMBIN/claude"

# --- run the REAL dispatch under an isolated HOME ----------------------------
echo "== Dispatching (real cartopian dispatch -> cartopian-claude -> claude shim) =="
NDJSON="$(PATH="$SHIMBIN:$PATH" HOME="$HOME_DIR" \
  "$CARTOPIAN" dispatch "$PROJ/tasks/in-progress/TASK-01-001-probe.md" --role coder)"
echo "$NDJSON"
echo

# dispatch is non-blocking; wait for the detached wrapper -> shim to land.
for _ in $(seq 1 100); do
  [ -f "$CAP" ] && [ -f "$REPORT" ] && break
  sleep 0.1
done

echo "== Assertions =="
set +e
NDJSON="$NDJSON" PROJ="$PROJ" WR="$WR" CAP="$CAP" REPORT="$REPORT" python3 - <<'PY'
import json, os, sys

proj = os.path.realpath(os.environ["PROJ"])
wr   = os.path.realpath(os.environ["WR"])
reports = os.path.realpath(os.path.join(proj, "reports"))
fails = []

def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)

rec = json.loads(os.environ["NDJSON"].strip().splitlines()[-1])

# (1) dispatch contract -----------------------------------------------------
check(os.path.realpath(rec["cwd"]) == wr, "dispatch cwd is the work root")
sd = [os.path.realpath(d) for d in rec.get("scope_dirs", [])]
check(wr in sd, "scope includes the work root")
check(reports in sd, "scope includes the report dir")
check(proj not in sd, "scope EXCLUDES the governing project root")
check(rec.get("code_comments") == "minimal", "code_comments resolved to minimal")

# (3,4) wrapper + agent actuals, from the shim capture ----------------------
cap_path = os.environ["CAP"]
if not os.path.isfile(cap_path):
    check(False, "claude shim ran (capture file present)")
else:
    cap = json.load(open(cap_path))
    check(os.path.realpath(cap["cwd"]) == wr, "wrapper ran claude with cwd = work root")
    argv = cap.get("argv", [])
    adds = [os.path.realpath(argv[i + 1]) for i, a in enumerate(argv)
            if a == "--add-dir" and i + 1 < len(argv)]
    check(wr in adds, "claude --add-dir includes the work root")
    check(reports in adds, "claude --add-dir includes the report dir")
    check(proj not in adds, "claude --add-dir EXCLUDES the governing project root")
    prompt = argv[-1] if argv else ""
    check(prompt.startswith("Code comments:"), "comment-volume directive injected into prompt")
    check("Never write" in prompt and "identifiers" in prompt,
          "management-identifier ban injected into prompt")

# (5) report landed ---------------------------------------------------------
check(os.path.isfile(os.environ["REPORT"]),
      "completion report landed in the governing project's reports/")

print()
print(f"  {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
PY
rc=$?
set -e

cat <<MANUAL

== Operator recording =====================================================
Automated launch-contract result: $([ $rc -eq 0 ] && echo PASS || echo FAIL)

Manual real-agent step (tool-scope denial — needs a live Claude Code CLI):
  1. In the throwaway layout this script builds (or a scratch project), set
     [handoffs.coder].agent back to the real cartopian-claude and dispatch a
     task whose prompt asks the coder to read ../REQUIREMENTS.md.
  2. Confirm the coder reports it CANNOT access REQUIREMENTS.md / decisions/ /
     tasks/ / BACKLOG.md (out of --add-dir scope) while it CAN read the work
     root and WRITE its report.
  Operator: ____ pass / ____ fail   date: __________  notes: ______________
===========================================================================
MANUAL

exit $rc
