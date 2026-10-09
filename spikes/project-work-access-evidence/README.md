# Native macOS work-root enforcement evidence

Validation host: native macOS Darwin 25.6; authenticated Claude Code 2.1.295.
The JSON files record actual filesystem operations in the real Claude Bash
sandbox, using `scripts/probe_project_work_access.py` and the shipped adapter's
validation/settings builder. They are observations, not policy-only assertions.

| Probe | Result | Launch |
| --- | --- | --- |
| [Complete shipped wrapper](wrapper/native-macos.json) | 39 checks passed | Shipped wrapper and file handoff |
| [Configured subtree](native-macos.json) | 36 checks passed | Real CLI, shipped settings |
| [Default resources through wrapper](default-wrapper/native-macos.json) | 34 checks passed | Shipped wrapper and file handoff |
| [Default resources](default/native-macos.json) | 36 checks passed | Real CLI, shipped settings |
| [Read-only role](reader/native-macos.json) | Input read allowed; direct and child writes denied | Real CLI, shipped settings |
| [Empty-grant role](no-access/native-macos.json) | Input read, direct write and child write denied | Real CLI, shipped settings |

Writer checks created and modified source, built bytecode, ran a child process,
and removed files/directories inside the root. The latest configured-wrapper
probe also created a Python environment and executed its interpreter as a child. Protected governance, sibling
resources for the narrower mapping, other projects, runtime, host executables
and settings resisted writes. Traversal, symlink and hard-link escapes and
ancestor rename/removal/replacement attempts were denied. Opening real runtime
and executable files for append was tested without writing any bytes; actual
mutations targeted disposable fixtures only. For the default root, all resources
content is authorized, so a sibling within resources is correctly writable.

The original default probe repeated one ancestor and also checked the fixture's
parent boundary. The harness now walks each ancestor from the work root through
the project exactly once. The recorded operations remain valid observations.

The complete-wrapper probe passed 39 checks through the shipped wrapper
and its file handoff, using a verified source snapshot inside the approved
subtree. A prior rerun correctly refused to inspect the original harness in
another registered project; that run produced no accepted enforcement evidence. Direct-CLI probes separately submit the task directly. Local transcripts are
retained by the probe but excluded from source to avoid account/session metadata.
No native Windows or Linux contained-root enforcement is claimed. Those launches
refuse; PowerShell execution tests skip when its interpreter is unavailable.


Validation completed on 2026-10-09:

- Shared contract: 87 passed, 6 skipped (PowerShell unavailable).
- Focused contract, composition and containment reporting: 175 passed,
  6 skipped, 67 subtests passed; focused unittest: 91 tests, OK.
- Full pytest: 3,914 passed, 14 skipped, 5,073 subtests passed, one failure.
- Full unittest discovery: 3,118 tests, one failure, one skipped.
- Both full-suite failures name the existing `test_startup_transport_budgets`:
  the session runbook is 7,635 bytes against a 7,000-byte budget. An independent
  clean `git archive HEAD` run confirmed the same pre-existing overrun. The
  unrelated budget was left unchanged. Focused checks cover the final prompt
  and containment-reporting changes.
- Final boundary regression suite: 251 passed, 6 skipped, 42 subtests
  passed, including reserved-path file/directory/symlink refusal. The real
  wrapper also denied creation of that reserved path in its Bash sandbox.
- Configuration surface registry checks and shell syntax checks pass.

Do not run the native CLI probes concurrently with wrapper/matrix regression
suites. Their complete writable-set scans deliberately refuse if a live Claude
filesystem-watcher probe disappears during inspection. This fail-closed behavior
was retained; serial validation removes that unrelated source of test races.
