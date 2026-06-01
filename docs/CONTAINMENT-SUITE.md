# FR-011 containment verification suite

The consolidated, single-run check that Phase 01's containment guarantees are
**proven, not asserted**. It aggregates the per-feature negative tests delivered
across TASK-01-001..006 + 008 + 009 (it does not reimplement them) and pins the
captured Claude Code harness-level evidence, so "the containment suite is green"
is one mechanical command.

## Entrypoint

```
tests/containment/run-containment-suite.sh                 # default: always-on suite (green, no cost)
tests/containment/run-containment-suite.sh --with-harness  # also (re)capture live harness evidence
tests/containment/run-containment-suite.sh --with-harness --with-red
```

* **Default layer** (stdlib-only, no network/cost): runs every prohibited-
  operation + lifecycle negative test named in the manifest, plus the FR-011
  aggregator. Captured harness evidence is **pinned when present** and **skipped
  with a reproduction pointer when absent** — the same posture as the existing
  `test_pm_floor_profile` / `test_pm_sandbox_profile` pins.
* **`--with-harness`** drives the live, cost-bearing Claude Code harnesses
  *first* (so the pins bind to fresh captures), then runs the default layer.
  Each underlying harness is itself fail-closed: it proves stale evidence gone
  and refuses to PASS on an unproduced/empty transcript, so a failed capture
  aborts the run rather than passing on stale evidence.

## Single source of truth

`tests/containment/manifest.py` maps every prohibited operation to its existing
red→green negative test(s), its red baseline, and any captured harness evidence.
`tests/containment/test_fr011_containment_suite.py` (always-on) keeps that map
honest: coverage completeness (no silent omission), each mapped test exists
(AST-verified), red baselines recorded, harness evidence pinned, deferrals
noted, this entrypoint present. The runner sources its pytest targets from the
manifest, so the documented run and the manifest cannot drift.

## Prohibited operations → red→green negative tests

Each row is **red before its guard exists, green after**. Red baselines are
either captured evidence (reused from the per-feature harnesses) or an in-module
naive/uncontained baseline asserted in the same test module.

| Prohibited operation | Negative test (green) | Red baseline |
| --- | --- | --- |
| shell / process-exec | `test_pm_floor_profile.py` (locked inventory: no `Bash`; `--tools ""`) | spike `red-01-shell.jsonl` (shell succeeded uncontained) |
| raw file write/edit | `test_pm_floor_profile.py` (no `Write`/`Edit`/`NotebookEdit`) | spike `red-02-write.ondisk.txt` (`RAW_WRITE_SUCCEEDED` on disk) |
| product-repo read | `test_pm_sandbox_profile.py` (denyRead + permission deny + native-sandbox denial) | sandbox `red-read.jsonl` (`cat` succeeded) |
| product-repo write | `test_pm_sandbox_profile.py` (denyWrite + permission deny) | sandbox `red-write.jsonl` (write created the file) |
| work-root read | `test_pm_sandbox_profile.py` (work root in denyRead) | floor `red-tools.txt` (`Read` present uncontained) |
| work-root write | `test_pm_sandbox_profile.py` (work root in denyWrite + native denial) | sandbox `red-write.jsonl` |
| non-allowlisted write | `test_p01_build_002_mediated_write.py::TestNonAllowlistedDestKind` | in-module naive writer |
| symlink write | `…::TestSymlinkFinalComponent` | in-module naive writer (clobbers via symlink) |
| hardlink write | `…::TestHardlink` | in-module naive writer (mutates shared inode) |
| exec-bit write | `…::TestExecBit` | in-module naive writer (sets exec bit) |
| config write | `…::TestConfigFileDestination` | in-module naive writer (clobbers `cartopian.toml`) |
| raw process launch (dispatch) | `test_dispatch.py::TestDispatchNoRawExec` | REPORT-01-004 (pre-command: unknown subcommand) |
| PM-owned-git under containment | `test_fr013_containment_git_guard.py::TestContainedPmOwnedGitBlocked` | uncontained control proceeds (no block) |
| (bonus) TOCTOU parent swap | `…::TestToctouParentSwap` | in-module swap in the TOCTOU window |

## Claude Code harness-level evidence (FR-011 standard)

Captured by extending the FR-001 spike pattern; pinned by the aggregator when
present. Reproduce with `--with-harness` (or the per-facet entrypoint).

| Facet | Captured artifacts | Reproduce |
| --- | --- | --- |
| exposed tool set (Cartopian-only) | `tests/wrappers/pm-floor/evidence/green-tools.txt` (locked 20 `mcp__cartopian__*`), `green-mcp.txt` (`cartopian` only) | `tests/wrappers/pm-floor/run-floor-test.sh` |
| reachable filesystem (outside) | floor `green-read-product.jsonl` / `green-read-work.jsonl` (`NO_READ_TOOL`); sandbox `green-read.jsonl` (`Operation not permitted`) | floor + `tests/wrappers/pm-sandbox/run-sandbox-test.sh` |
| in-runtime prohibited attempts | spike `green-01-shell.sentinel.txt`, `green-02-write.sentinel.txt` (+ `…ondisk.txt`: no file created), `green-03-read.sentinel.txt` | `tests/wrappers/pm-runtime/run-probes.sh` |
| still functional (no deadlock) | spike `green-04-positive.check.txt` (`CARTOPIAN_TOOL_OK`) | `tests/wrappers/pm-runtime/run-probes.sh` |

The authoritative tool inventory is the `system/init` event's `tools` array —
what the harness exposes to the model, not what the model claims.

## Full lifecycle under containment (no deadlock)

| Evidence | Test |
| --- | --- |
| plan → assign → review → close completes with only Cartopian commands; pre-FR-005 surface deadlocks (red) | `test_p01_build_003_lifecycle_completeness.py::TestGreenLifecycleCompletes` / `TestRedMissingCommandDeadlock` |
| the rewired lifecycle skills route every PM-performed step through a mediated command; no residual raw op | `test_p01_build_004_mediated_pm_actions.py::MediatedCommandPresenceTest` / `ResidualRawOpTest` |

## Out-of-Phase-01 FR-011 items (noted, not omitted)

Tracked in `manifest.DEFERRED_FR011`:

1. **Live-harness promotion (Phase 03).** The cost-bearing shell harnesses are
   run on demand and *pinned*; promoting them to always-on CI gates needs a
   runner with the `claude` CLI + network budget.
2. **Mediated-git negative suite (RM-004, deferred).** Once mediated-git lands,
   PM-owned-git under containment flips from a fail-closed refusal to a
   mediated-git negative suite (path/exec-scoped git guards).
3. **Linux bubblewrap parity.** The native-sandbox depth evidence is macOS
   seatbelt; Linux bubblewrap parity evidence is deferred to a Linux CI lane.
4. **Web / sub-agent attempt probes.** `WebFetch` / `WebSearch` / `Task` are
   proven absent structurally by the locked inventory, but have no dedicated
   live in-runtime attempt probe (unlike shell/write/read).
