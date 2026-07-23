# Cartopian product-contract inventory

Observed on 2026-07-23 in `/Users/scott/Projects/cartopian` at `git describe --tags --always --dirty` = `v1.5.10`, using Python 3.14.5.

## Purpose and method

This document inventories existing product contracts. It does not create a new source of truth or reconcile conflicts. “Documented intent” means an explicit statement in a protocol document, runbook, template, or user-facing guide. “Observed behavior” means executable source, a deterministic local command, or a test assertion. When those disagree, both are recorded and the explicit ownership statement is used only to identify the intended authority.

Repository paths below are relative to `/Users/scott/Projects/cartopian` unless an absolute path is shown. Line references are sample anchors, not substitutes for reading the named contract. No backlog entries were used as requirements, evidence, or work sources.

The inventory uses these ownership rules, each stated by the product:

- Lifecycle semantics: `README.md:242`, `AGENTS.md:21,30-32`, and `protocol/CONVENTIONS.md` identify `protocol/CONVENTIONS.md` as the authoritative lifecycle reference.
- Operational procedures: `protocol/CONVENTIONS.md:21-23` identifies `skills/*.md` as executable runbooks whose invocation names derive from filenames.
- Report shape: `templates/REPORT.md:3` identifies that template as the canonical report field schema.
- Executable behavior: CLI and MCP behavior is observed in the Python implementations and their tests. Documentation remains intent when it differs from those implementations.
- Installed artifacts: `scripts/install.py:72-98,204-244` owns which paths are tool-shipped versus operator-owned and how they are materialized.

## Executive findings

All requested contract families have identifiable sources and test or validation anchors. The highest-confidence contradictions and duplications are:

1. **Assignment working directory conflict.** `templates/PROMPT.md:14` says the assignee starts in the primary work root and cannot write the governing project except for the report directory. The authoritative protocol says cwd is the Cartopian project root and grants the union of project root plus declared work roots (`protocol/CONVENTIONS.md:372-380`). Observed `dispatch` sets both `cwd` and `CARTOPIAN_LAUNCH_CWD` to the project root (`cli/commands/dispatch.py:420-457`), and every shipped wrapper follows that value or derives the project root. Tests pin the project-root behavior (`tests/cli/commands/test_dispatch.py:271-312`). The prompt template is therefore a conflicting representation of current behavior.
2. **Skill-summary metadata is structurally valid but not descriptive.** MCP prompt/resource descriptions for ordinary skills are derived by `_first_line_summary`, which selects the first non-empty Markdown heading (`mcp_server/server.py:212-225`). Because skill files begin with headings such as `# Skill: Start Session`, the observed description is `Skill: Start Session`, not the purpose paragraph or the curated purpose in `skills/README.md`. This affects discovery quality, not skill-body authority.
3. **Startup-action duplication and ordering conflict.** `README.md:117`, `install-cartopian.md:138`, and `skills/register-mcp.md:375` summarize the entry point as routing first to `start_session` when projects exist. The actual entry runbook performs update checking, discovery, and project selection itself before loading `start_session` (`skills/use-cartopian.md:13-49`). It then instructs a standalone `resolve_config` call, while the active startup runbook says a standalone call is not part of the flow because `next_action` resolves config internally (`skills/start-session.md:33-52`). Tests assert that startup surfaces exist and mention the expected runbooks, but do not assert this action ordering (`tests/mcp_server/test_server.py:213-228`).
4. **Version identity has three representations and a verified symlink-mode split.** Runtime CLI/MCP version logic uses `VERSION`, then `git describe`, then `unknown` (`cli/main.py:217-244`, `mcp_server/server.py:156-184`); packaging metadata independently says `0.1.0` (`pyproject.toml:7`). On this machine, `/Users/scott/.cartopian/VERSION` contains `v1.3.11`, while its tool paths are symlinks to the `v1.5.10` checkout. The PATH CLI reports `v1.3.11`; the PATH MCP server reports root `/Users/scott/Projects/cartopian` and version `v1.5.10`. The divergence follows from CLI using an unresolved `__file__` path while MCP resolves symlinks before locating its root.
5. **Install verification contains a stale ownership pointer.** `protocol/INSTALL_VERIFICATION.md:5,147` says install/upgrade behavior is documented in a root `STANDARDS.md`; the repository has only `templates/STANDARDS.md`. The actual behavior is defined in `scripts/install.py`, with user-facing flows in `README.md`, `install-cartopian.md`, and `skills/check-for-updates.md`. This is a documentation contradiction, not an observed installer failure.
6. **Client bridges are manually duplicated but sampled installed copies match.** Claude Code and Codex skill templates are byte-identical; Devin has a narrower trigger description; Gemini and Windsurf have client-native formats. `skills/register-mcp.md` copies these variants verbatim and contains the registration recipes. There is no repository generator linking the variants. On this machine, sampled installed Codex, Claude Code, Gemini, and Devin bridges have the same SHA-256 as their current source templates, while their MCP registrations point to the symlink install root.

## Contract inventory

### 1. Skill corpus, names, and discovery metadata

- **Family:** Skill metadata and discovery.
- **Authoritative source:** Each workflow body lives in `skills/*.md`; `protocol/CONVENTIONS.md:21-23` makes those files executable runbooks. Filename-to-invocation naming is documented as dropping `.md` and replacing hyphens with spaces. MCP’s machine name uses underscores via `_skill_name` in `mcp_server/server.py:207-210`.
- **Derived consumers:** `skills/README.md` is a curated human catalog. MCP prompts and resources are built by `_skill_paths`, `list_prompts`, and `list_resources` in `mcp_server/server.py:191-240,767-834`. Client trigger bridges lead models to `cartopian://skills/use_cartopian`.
- **Generation/synchronization path:** MCP enumeration is runtime-derived from the directory; no maintained MCP name list exists. The human table in `skills/README.md` is maintained separately. The installer ships the entire `skills/` directory (`scripts/install.py:72-96`).
- **Validation anchors:** `tests/mcp_server/test_server.py:189-235` verifies entry-point ordering, expected prompts, and skill-body delivery. `tests/mcp_server/test_server.py:467-491` verifies resource naming and entry-resource install context.
- **Conflicts/duplication:** MCP discovery descriptions come from the first heading, while `skills/README.md` carries actual purpose summaries. Deterministic observation on this checkout reports 14 prompts, including `{'name': 'start_session', 'description': 'Skill: Start Session'}`. The catalog and the derived directory surface can therefore drift independently.
- **Uncertainty:** No contract states whether MCP descriptions are intended to be semantic summaries rather than labels. The discoverability concern is observed; the desired replacement policy is not inferred.

### 2. Entry-trigger skill metadata

- **Family:** Skill metadata and discovery; bridges and client registrations.
- **Authoritative source:** `skills/use-cartopian.md` owns the startup workflow. Client trigger metadata and bridge instructions live in `templates/clients/<client>/...`.
- **Derived consumers:** Installed native skill, command, prompt, or workflow files for Codex, Claude Code, Gemini, Devin, and Windsurf; MCP-only clients consume the `use_cartopian` prompt directly.
- **Generation/synchronization path:** `skills/register-mcp.md:95-375` provides per-client MCP registration plus verbatim-copy steps for bridge templates. `install-cartopian.md:123-138` delegates registration to that runbook. Upgrade checking offers re-registration separately (`skills/check-for-updates.md:76-86`).
- **Validation anchors:** `tests/mcp_server/test_server.py:189-228` validates the MCP entry prompt and its references. Source-to-installed SHA-256 comparisons on this machine matched for Codex, Claude Code skill and command, Gemini, and Devin bridge files.
- **Conflicts/duplication:** Codex and Claude Code skill templates are identical copies. Devin’s description triggers “use cartopian” or starting a session, while Codex/Claude Code additionally say resume/manage. These are format-specific maintained copies, not generated views. Registration is not completed merely by registering MCP; the bridge is a second artifact, explicitly documented by `skills/register-mcp.md:95-97`.
- **Uncertainty:** The repository does not provide a cross-client behavioral test that invokes each native client’s trigger matching. File-copy fidelity is verified; client interpretation remains external.

### 3. Lifecycle protocol documents

- **Family:** Protocol documents and templates.
- **Authoritative source:** `protocol/CONVENTIONS.md` for project structure, lifecycle, roles, handoffs, configuration semantics, task ordering, closeout, and state. `protocol/CHANGELOG.md` owns shipped project-schema migration entries. `protocol/INSTALL_VERIFICATION.md` is a post-install checklist, not the installer.
- **Derived consumers:** Skills cite whole-document or section-scoped protocol resources. MCP publishes whole documents, per-section reads, and a curated startup slice (`mcp_server/server.py:617-738,767-834`).
- **Generation/synchronization path:** Whole and section resources are runtime reads of the Markdown files. The curated startup slice is assembled from a fixed heading allowlist and fails closed when headings drift (`mcp_server/server.py:625-738`). Installer copy/symlink mode ships the `protocol/` directory and copies `protocol/CHANGELOG.md` to install-root `CHANGELOG.md`.
- **Validation anchors:** `tests/mcp_server/test_server.py:566-655` verifies whole, section, and startup resources, size reduction, traversal rejection, and fail-closed heading drift. Protocol gate tests are in `tests/test_protocol_gate.py`.
- **Conflicts/duplication:** Skills often restate the protocol section they execute. They consistently say the full protocol remains authoritative, so those restatements are derived operational copies. `protocol/INSTALL_VERIFICATION.md` points to an absent root `STANDARDS.md` for install behavior; executable ownership is elsewhere.
- **Uncertainty:** Markdown lifecycle semantics not enforced by a CLI guard rely on review. `AGENTS.md:40-44` says the protocol itself has no test suite, while the repository does contain static and behavioral tests for many protocol representations; the exact untested semantic remainder is not enumerated here.

### 4. Templates and document field schemas

- **Family:** Protocol documents and templates; lifecycle prompts; delivery.
- **Authoritative source:** Individual files in `templates/`. `templates/REPORT.md:3` explicitly owns report fields and variants. `templates/PROMPT.md`, `TASK.md`, `REVIEW.md`, and `PLAN_CLOSEOUT.md` describe assignment, work-root, review, and closeout shapes.
- **Derived consumers:** Structured writer commands under `cli/commands/write_*.py`, report parsing/action code, lifecycle skills, MCP template resources, and installed template copies.
- **Generation/synchronization path:** Templates are hand-maintained Markdown/TOML and shipped as a directory. MCP reads them directly. Structured writers validate their own closed schemas rather than generating schemas from the templates.
- **Validation anchors:** `tests/cli/commands/test_fr005_structured_writers.py`, `tests/cli/commands/test_report_action.py`, `tests/test_p02_build_010_static_coverage.py`, and MCP template-resource tests in `tests/mcp_server/test_server.py`.
- **Conflicts/duplication:** The verified cwd/scope contradiction is in `templates/PROMPT.md:14` versus protocol, dispatch, wrappers, and tests. Template prose and writer/parser code are separate representations; there is no generator tying them together. MCP lists template URIs with extensions but `read_resource` also accepts an unlisted extensionless alias, e.g. both `cartopian://templates/REPORT` and `cartopian://templates/REPORT.md` read the same file (`mcp_server/server.py:875-893`).
- **Uncertainty:** Except where a template declares itself canonical, authority between prose and closed-schema code is contextual: template for the authored artifact shape, code for accepted runtime behavior.

### 5. Report, review, and durable-deliverable contract

- **Family:** Protocol documents and templates; delivery; high-frequency context-bearing outputs.
- **Authoritative source:** `templates/REPORT.md` owns report variants. Document-deliverable intent is in `protocol/CONVENTIONS.md:261-283`, `templates/TASK.md:39-45`, and `templates/PROMPT.md`.
- **Derived consumers:** `cli/commands/parse_report.py`, `report_action.py`, `wait_report.py`, `wait_handoff.py`, `move_task.py`, `close_audit.py`, and the `run-handoff`/`run-task` skills.
- **Generation/synchronization path:** Assignments derive expected report paths from prompt/task identity. Assignees write the report; parsing and action commands turn it into structured state. A durable document is written directly to a resolved work-root path, or returned inline when its durable path is inside the governing project.
- **Validation anchors:** `tests/cli/commands/test_report_action.py`, `test_wait_report.py`, `test_wait_handoff.py`, `test_move_task.py`, and `test_close_audit.py`; report-action tests exercise the shared parser.
- **Conflicts/duplication:** Report fields appear in the template, parsers, lifecycle skills, and expected-path derivation. The code tolerates some legacy identity fields and heading variants, so accepted behavior is wider than the preferred template. No contradiction was observed in the rule that the report is the authoritative handoff completion signal.
- **Uncertainty:** External agents can author malformed reports. The contract specifies how Cartopian rejects or classifies them; it cannot guarantee compliant emission.

### 6. Core CLI command and argument schema

- **Family:** CLI and MCP schemas.
- **Authoritative source:** `cli/main.py:17-57` is the registered subcommand set; `_real_handlers` and `build_parser` at `cli/main.py:75-214` bind every name to each command module’s `configure_parser` and `handler`.
- **Derived consumers:** Shell entry points, skills, docs, tests, and MCP tools.
- **Generation/synchronization path:** Each command module defines argparse actions. The top-level CLI builds the parser at runtime. `pyproject.toml:12-14` and `bin/cartopian` expose it as an executable.
- **Validation anchors:** `tests/cli/test_main.py` validates top-level dispatch, help, exit codes, and version output. Command-specific suites under `tests/cli/commands/` validate argument and output behavior.
- **Conflicts/duplication:** The list is centralized, but command names are also repeated throughout runbooks and documentation. The current source exposes 38 subcommands. No difference was observed between `SUBCOMMANDS` length and MCP tool count.
- **Uncertainty:** A string reference in prose can still drift without a static test covering that particular reference.

### 7. CLI output and error envelope

- **Family:** CLI and MCP schemas; high-frequency context-bearing outputs.
- **Authoritative source:** `cli/emit.py` owns compact one-object-per-line NDJSON. `cli/main.py:59-72` owns exit-code constants and the primary `[error]`, `[guard]`, and `[usage]` prefixes; some commands add contract-specific prefixes.
- **Derived consumers:** Humans, skills, wrappers, MCP’s in-process invocation adapter, and tests.
- **Generation/synchronization path:** Command handlers call `emit_record`; MCP captures stdout/stderr, parses each NDJSON line, and returns `{exit_code, records, stderr_lines, stdout_raw}` internally (`mcp_server/server.py:506-589`).
- **Validation anchors:** `tests/cli/test_emit.py`, `tests/cli/test_main.py`, command output-schema tests, and `tests/mcp_server/test_server.py:242-466`.
- **Conflicts/duplication:** Output record keys are hand-authored per command, while MCP input schemas are generated. There is no common output JSON Schema registry. Consequently input drift is mechanically limited, but output drift is pinned only where tests assert keys.
- **Uncertainty:** “Compact” is a design intent (`README.md:81-90`), not a numeric size budget for most records.

### 8. MCP prompts, tools, resources, and initialization

- **Family:** CLI and MCP schemas; skill discovery; startup prompts.
- **Authoritative source:** `mcp_server/server.py`. It owns MCP protocol version, initialization instructions, server info, prompt/resource enumeration, generated tool schemas, argument reconstruction, and JSON-RPC dispatch.
- **Derived consumers:** Any registered MCP client. The entry bridges specifically consume `cartopian://skills/use_cartopian`.
- **Generation/synchronization path:** Prompts/resources enumerate source files at runtime. Tools are derived from the CLI’s argparse parser: hyphenated command names become underscored MCP names, action types/choices/defaults become input JSON Schema, and calls reconstruct argv.
- **Validation anchors:** `tests/mcp_server/test_server.py` covers initialization, version resolution, prompts, tools, resources, traversal/size bounds, framing, and in-process CLI invocation.
- **Conflicts/duplication:** Tool descriptions are generic `Run cartopian <subcommand>` labels even though parser descriptions may be richer. Prompt descriptions have the heading-summary issue. The current deterministic observation is 14 prompts, 38 tools, and 76 listed resources. `resolve_config` requires `project_path`; `task_bundle` requires `task_path`; `dispatch` requires only `role` at schema level because exactly-one-of task/prompt is enforced by the handler rather than representable by the generated schema.
- **Uncertainty:** MCP clients may or may not inject server `instructions` into model context; `mcp_server/server.py:264-290` explicitly notes the MCP mechanism is client-optional. Cartopian duplicates install context into the entry prompt/resource to mitigate that.

### 9. Configuration shape, resolution, and editing

- **Family:** Configuration and version sources.
- **Authoritative source:** Documented shape and precedence are in `CONFIG-MAPPING.md` and `protocol/CONVENTIONS.md:284-423`. Observed resolution is implemented in `cli/commands/resolve_config.py`; creation and closed-schema edits are in `generate_config.py` and `update_config.py`.
- **Derived consumers:** Startup/status aggregators, handoff packets, dispatch, wrappers, protocol gate, migration, lifecycle skills, and MCP’s generated `resolve_config`, `generate_config`, and `update_config` tools.
- **Generation/synchronization path:** Project/global configuration merges shallowly per documented table rules; project-local work-root paths come only from `cartopian.local.toml`. `generate-config` stamps the shipped project-schema version. `update-config` validates a closed key set and effective configuration before writing. Runtime defaults are constants in `resolve_config.py:13-30`; the global seed documents those defaults as commented examples.
- **Validation anchors:** `tests/cli/commands/test_resolve_config.py`, `test_generate_config.py`, and `test_update_config.py`; cross-surface aggregator checks are in `tests/mcp_server/test_server.py:358-465`.
- **Conflicts/duplication:** Defaults and accepted keys are represented in protocol prose, `CONFIG-MAPPING.md`, `templates/global.cartopian.toml`, resolver constants, generator flags, updater schema, and tests. They are synchronized by review/tests, not generated from one schema. Legacy handoff keys are accepted and normalized by the resolver but remain editable only for migration, making the accepted input shape deliberately wider than the preferred output shape.
- **Uncertainty:** `CONFIG-MAPPING.md` is the best consolidated schema reference, but it does not declare itself the sole authority; actual acceptance is executable code.

### 10. Project protocol-schema version

- **Family:** Configuration and version sources; installer/update behavior.
- **Authoritative source:** A governed project’s `[project].protocol_version` is the only project marker (`skills/migrate-project.md:21`). The shipped target version is the topmost version entry in `protocol/CHANGELOG.md`, read by `cli/protocol_gate.py:43-53`.
- **Derived consumers:** `generate-config`, `next-action`, `plan-audit`, installer reconciliation, and the migration skill/executor.
- **Generation/synchronization path:** New config is stamped from `read_shipped_protocol_version`; upgrades reconcile registered projects and migration advances markers only through mediated config edits after applicable entries are complete.
- **Validation anchors:** `tests/test_protocol_gate.py`, `tests/cli/commands/test_generate_config.py`, `test_apply_migration_entry.py`, migration harness tests, and installer reconciliation cases in `tests/test_install.py`.
- **Conflicts/duplication:** The project-schema version is intentionally distinct from the Cartopian application release. The same distinction is repeated in config docs, protocol-gate messages, install/update runbooks, and migration runbook. No contradictory project marker location was observed.
- **Uncertainty:** Version ordering uses the documented lexical `vX.Y.Z` rule. Behavior outside that accepted form is rejected or classified, not semantically version-compared.

### 11. Application release and runtime identity

- **Family:** Configuration and version sources.
- **Authoritative source:** For an installed copy, `<install-root>/VERSION` is the recorded ref written by the installer. Both CLI and MCP prefer a marker, fall back to `git describe`, then `unknown`. MCP initialization and entry surfaces expose the resolved server identity.
- **Derived consumers:** `cartopian --version`, MCP `serverInfo`, initialization install context, `use_cartopian` prompt/resource, update checks, and install verification.
- **Generation/synchronization path:** `scripts/install.py:342-344,707-732` writes `VERSION` when a ref is resolved. Copy-mode upgrades refresh code and the marker together. Contributor symlink mode can change running source independently of the previously written marker.
- **Validation anchors:** `tests/cli/test_main.py:47-58` and `tests/mcp_server/test_server.py:104-157`. Installer marker behavior is covered in `tests/test_install.py`.
- **Conflicts/duplication:** `pyproject.toml` contains `0.1.0`, but runtime commands do not read it. The verified symlink-mode local split is:
  - `/Users/scott/.cartopian/VERSION` → `v1.3.11`;
  - `cartopian --version` → `cartopian v1.3.11`;
  - `/Users/scott/.cartopian/bin/cartopian-mcp` initialize → install root `/Users/scott/Projects/cartopian`, server version `v1.5.10`;
  - source checkout `git describe` → `v1.5.10`.
  MCP resolves `Path(__file__)` before root detection; CLI uses `abspath(__file__)`. This makes “installed version” surface-dependent in this supported symlink layout.
- **Uncertainty:** The repository does not state whether `pyproject.toml`’s version is intentionally fixed packaging metadata or stale release metadata. It must not be treated as the runtime application identity without a product decision.

### 12. Installer materialization and update behavior

- **Family:** Installer and update behavior.
- **Authoritative source:** `scripts/install.py` owns executable install behavior. `install-cartopian.md` is the guided install runbook; `skills/check-for-updates.md` owns update comparison/approval flow; `protocol/INSTALL_VERIFICATION.md` owns the verification checklist.
- **Derived consumers:** Install roots, PATH entries, wrapper availability, MCP registration flow, operator-owned global config/registry, and installed `VERSION`.
- **Generation/synchronization path:** Local-source installs default to symlinks; `--from-github` implies copy mode. Tool-shipped paths are replaced/refreshed. Existing `cartopian.toml` and `projects.json` are preserved; first install seeds them from the global template and an empty JSON array. `CHANGELOG.md` is always copied. The installer ships itself for later upgrades.
- **Validation anchors:** `tests/test_install.py` covers first install and upgrades in symlink/copy modes, preservation, stale-link repair, shims, vendored dependency, download/ref handling, and registered-project reconciliation. `protocol/INSTALL_VERIFICATION.md` provides operator-repeatable checks.
- **Conflicts/duplication:** Install behavior is narrated in README, two skills, verification docs, and code. The stale `STANDARDS.md` pointer and the symlink version split are the verified conflicts. Update comparison reads `VERSION`, so in symlink mode it can assess the recorded install ref rather than the running server’s source ref.
- **Uncertainty:** Network release discovery was not executed for this inventory; remote “latest” is intentionally outside the stable local product-contract claim.

### 13. Wrapper launch contract

- **Family:** Wrappers; assignment working directory.
- **Authoritative source:** Lifecycle intent is `protocol/CONVENTIONS.md:346-382`; executable launch preparation is `cli/commands/dispatch.py`; client translation is in `wrappers/bin/*` and `wrappers/ps1/*`.
- **Derived consumers:** Automated assignee and reviewer handoffs, agent CLI argv, sandbox grants, launch diagnostics, and dispatch records.
- **Generation/synchronization path:** `dispatch` resolves the configured agent, timeout, model, effort, work roots, prompt, and report. It exports `CARTOPIAN_TIMEOUT`, `CARTOPIAN_LAUNCH_CWD`, `CARTOPIAN_ROLE`, optional model/effort, and optional work roots. Every wrapper translates shared environment to its client CLI and runs with one absolute prompt-path argument.
- **Validation anchors:** `tests/cli/commands/test_dispatch.py`; wrapper tests for model, effort, timeout, work roots, cwd, and PowerShell parity under `tests/wrappers/`.
- **Conflicts/duplication:** Bash and PowerShell plus four client variants duplicate common behavior. Shared status helpers reduce but do not eliminate that duplication. Protocol, dispatch, wrappers, README, and tests agree on project-root cwd. `templates/PROMPT.md:14` alone states primary-work-root cwd and a narrower write scope.
- **Uncertainty:** Gemini and Devin sandbox modes expose no per-path writable-root grant. Wrappers warn when declared work roots may be unwritable; the outcome then depends on the external client.

### 14. Wrapper completion, status, and launch-log contract

- **Family:** Wrappers; handoff outputs.
- **Authoritative source:** `protocol/CONVENTIONS.md:427-440`, `wrappers/README.md:135-208`, shared shell/PowerShell status helpers, `wait_handoff.py`, and `wait_report.py`.
- **Derived consumers:** Handoff wait primitives, lifecycle skills, report cleanup, operator diagnostics, and closeout audits.
- **Generation/synchronization path:** The report file is authoritative. Wrappers supervise for a complete report, then emit a best-effort `.status` sidecar when the process exits. POSIX `dispatch` may create a hardened `.launch.log`; native Windows intentionally uses null output. Wait commands observe but do not launch or mutate. Cleanup uses report deletion paths.
- **Validation anchors:** `tests/wrappers/test_handoff_exit_contract.py`, `test_ps1_handoff_exit_contract.py`, `test_wrapper_status_file.py`, `tests/cli/commands/test_wait_handoff.py`, and `test_wait_report.py`.
- **Conflicts/duplication:** Status shape/path exists in producer helpers, wrapper variants, consumer code, README, and tests. Tests explicitly pin producer-consumer agreement. No contradiction was observed; `.status` and `.launch.log` are secondary context, never completion authority.
- **Uncertainty:** PowerShell behavioral cases are conditional on `pwsh` availability; the test file distinguishes static parity from host-executed parity.

### 15. MCP registration and client bridges

- **Family:** Bridges and client registrations.
- **Authoritative source:** Per-client recipes are in `skills/register-mcp.md`; bridge payloads are in `templates/clients/`; install and update runbooks decide when to invoke registration.
- **Derived consumers:** Client-owned MCP config files and native trigger locations.
- **Generation/synchronization path:** The skill detects clients, asks which registrations to change, registers an absolute `cartopian-mcp` command, and copies bridge files verbatim. Upgrades preserve client configs and offer repair/re-registration rather than silently rewriting them.
- **Validation anchors:** MCP server initialization can be verified with the one-line JSON-RPC command in `README.md:106-113`. Template-to-installed hashes are repeatable. MCP source tests validate the target server surface, not external config mutation.
- **Conflicts/duplication:** Registration and bridge installation are two separate required pieces for named native clients. Current sampled configs for Codex, Claude, and Devin point to `/Users/scott/.cartopian/bin/cartopian-mcp`; that install is symlinked to the source checkout. Codex’s local approval table also contains a `write_conventions` tool entry that is absent from the current 38-tool MCP surface, demonstrating that client-owned per-tool metadata can outlive server schemas.
- **Uncertainty:** The sample is one machine and not a statement about all supported clients. No secrets or unrelated client configuration were inspected or recorded.

### 16. Startup and session-orientation contract

- **Family:** Lifecycle and startup prompts; high-frequency context-bearing outputs.
- **Authoritative source:** Entry procedure is `skills/use-cartopian.md`; selected-project orientation is `skills/start-session.md`; lifecycle intent is the startup slice of `protocol/CONVENTIONS.md`. MCP initialization and entry delivery are built in `mcp_server/server.py`.
- **Derived consumers:** MCP client context, native bridge triggers, project discovery, `next_action`, `plan_audit`, operator summaries, and subsequent lifecycle routing.
- **Generation/synchronization path:** Initialization provides install context and directs models to the entry resource. Both prompt and resource delivery prepend the same install block. The entry runbook performs update check, registry discovery/selection, loads the startup protocol slice and start-session runbook, then continues into orientation.
- **Validation anchors:** `tests/mcp_server/test_server.py:104-228,467-491,566-655`; `tests/cli/commands/test_next_action.py`; `tests/cli/commands/test_plan_audit.py`.
- **Conflicts/duplication:** Project selection exists in both `use-cartopian` Step 1 and `start-session` Stage 0. Human summaries say the entry prompt routes first to start-session, but executable instructions put discovery/selection before loading it. The standalone `resolve_config` instruction in `use-cartopian` conflicts with start-session’s `next_action` aggregator contract. Startup context is intentionally duplicated in initialization, prompt, and resource because clients may ignore initialization instructions.
- **Uncertainty:** Which duplicated startup action should be removed or reordered is a design decision. This inventory records the conflict without choosing a new workflow.

### 17. Task bundle, prompt, handoff, and delivery flow

- **Family:** Lifecycle prompts; high-frequency context-bearing outputs.
- **Authoritative source:** `skills/run-task.md` and `skills/run-handoff.md` own procedure; protocol sections own lifecycle/handoff semantics; `task_bundle.py`, `handoff_packet.py`, `render_spec.py`, `dispatch.py`, wait commands, and `report_action.py` own observed records.
- **Derived consumers:** Prompt authors, assignees, reviewers, task movement, delivery references, and state refresh.
- **Generation/synchronization path:** `task_bundle` resolves readiness, work roots, deliverable, spec, and expected prompt/report paths. `render_spec` deidentifies the spec. `handoff_packet` resolves role/config policy. The prompt is written through the structured writer, dispatched, observed, parsed, and converted to a recommended lifecycle action.
- **Validation anchors:** Corresponding command tests under `tests/cli/commands/`, end-to-end MCP aggregator checks at `tests/mcp_server/test_server.py:358-465`, and lifecycle static tests.
- **Conflicts/duplication:** Expected paths and task identity are derived in multiple commands, with shared helpers only in some paths. Tests pin key agreement. The prompt-template cwd contradiction directly affects this flow’s assignment context.
- **Uncertainty:** These outputs carry enough context to support later measurement, but the product does not currently define a single receipt schema spanning all stages.

### 18. Plan closeout contract

- **Family:** Lifecycle and startup prompts; high-frequency context-bearing outputs.
- **Authoritative source:** `protocol/CONVENTIONS.md:459-515` owns closeout semantics; `skills/close-plan.md` is the canonical workflow; `close_audit.py`, `archive_plan.py`, `reset_plan.py`, `compose_state.py`, and `write_state.py` own executable steps; `templates/PLAN_CLOSEOUT.md` owns summary shape.
- **Derived consumers:** Closeout readiness summaries, optional archive, reset live surfaces, post-closeout state, and the next planning entry.
- **Generation/synchronization path:** Plan audit and close audit must clear. Operator chooses archive/carry-forward behavior. Archive is optional; reset is mediated; no-plan state is verified; the PM writes the closeout state body because it cannot be composed from an active plan.
- **Validation anchors:** `tests/cli/commands/test_close_audit.py`, `test_archive_plan.py`, `test_fr005_reset_plan.py`, `test_compose_state.py`, `test_write_state.py`, and lifecycle static coverage.
- **Conflicts/duplication:** Closeout rules are repeated across protocol, skill, template, and command schemas. No verified contradiction was found in the core sequence. The closeout skill explicitly treats the no-plan `compose_state` null record as a signal rather than a complete closeout body.
- **Uncertainty:** Optional archival and carry-forward content require operator judgment, so those values cannot be generated deterministically from repository state.

## Context-bearing outputs and measurement candidates

These are candidates for later measurement because they repeatedly enter model or operator context. This list does **not** assert that each surface will or should emit a receipt.

| Surface class | Current context-bearing surfaces | Authoritative/observed source and representative fields | Primary consumers | Validation anchor | Duplication or caveat |
| --- | --- | --- | --- | --- | --- |
| Startup | MCP `initialize.instructions`; `use_cartopian` prompt/resource; `discover_projects`; startup protocol slice | `mcp_server/server.py`; install root/version/upgrade skill; registry `id/path/label` | Client model and operator | MCP initialization, prompt, resource, and discovery tests | Install context appears on three delivery paths deliberately; client use of initialization instructions is optional |
| Status | `next_action`; `plan_audit`; `list_tasks`; `compose_state` / rendered `STATE.md` | `next_action.py:549-565` includes project, phase, active/next work, policy, blockers, disagreement; `plan_audit.py:806-815` includes clean/blockers/warnings/attribution/provenance | Startup and lifecycle routing | Per-command schema tests | Overlapping summaries serve different scopes; start-session must call both next action and plan audit |
| Task bundle | `task_bundle`; `handoff_packet`; `render_spec` | `task_bundle.py:243-257`; `handoff_packet.py:238-260`; `render_spec.py:43-50` | Prompt author and dispatcher | Command tests plus MCP aggregator test | Work roots, deliverable, report path, and policy repeat across records |
| Prompt | MCP skill prompts; protocol/template resources; assignment prompt file | `mcp_server/server.py:293-347`; `templates/PROMPT.md`; structured prompt writer | Model, assignee, reviewer | Prompt/resource and writer tests | Skill descriptions are weak summaries; assignment template carries the cwd conflict |
| Handoff | `dispatch`; `.launch.log`; `.status`; `wait_handoff`; `wait_report`; `report_action` | `dispatch.py:554-570`; `wait_handoff.py:338-350`; `wait_report.py:123-148`; `report_action.py:509-535` | Lifecycle orchestrator and operator diagnostics | Dispatch/wait/report/wrapper tests | Report is authoritative; sidecars are secondary; still-running is nonterminal |
| Delivery | Durable deliverable path/content; completion report; review result | `templates/TASK.md`, `PROMPT.md`, `REPORT.md`; parser/action code | Reviewer, lifecycle movement, later users of the artifact | Report/action/move tests | Preferred template and legacy accepted shapes differ; direct versus inline persistence depends on resolved path |
| Closeout | `plan_audit`; `close_audit`; optional closeout summary; archive/reset records; no-plan `compose_state`; post-closeout `STATE.md` | `close_audit.py:261-274`; `skills/close-plan.md`; `templates/PLAN_CLOSEOUT.md` | Operator and next session | Closeout command tests | Operator choices prevent a fully deterministic single record |

Potential later measures include context bytes/tokens, repeated fields, time-to-first-action, schema stability, contradictory field incidence, and whether a surface materially changed since its prior emission. These are examples only; no measurement policy is established here.

## Reproducible evidence queries

Run from `/Users/scott/Projects/cartopian`.

### Skill, MCP, and CLI surface counts

```bash
python3 - <<'PY'
from mcp_server import server
from cli.main import SUBCOMMANDS
prompts = server.list_prompts()
tools = server.list_tools()
resources = server.list_resources()
print("prompts", len(prompts))
print("tools", len(tools), "subcommands", len(SUBCOMMANDS))
print("resources", len(resources))
for name in ("use_cartopian", "start_session", "run_task", "install_cartopian"):
    print(next(p for p in prompts if p["name"] == name))
PY
```

Observed: 14 prompts, 38 tools, 38 subcommands, and 76 resources. Ordinary skill descriptions are heading labels.

### Generated MCP input-schema samples

```bash
python3 - <<'PY'
from mcp_server import server
tools = {item["name"]: item for item in server.list_tools()}
for name in ("resolve_config", "task_bundle", "dispatch", "wait_handoff"):
    schema = tools[name]["inputSchema"]
    print(name, schema.get("required", []), sorted(schema["properties"]))
PY
```

Observed required/property sets:

- `resolve_config`: required `project_path`; properties `project_path`.
- `task_bundle`: required `task_path`; properties `task_path`.
- `dispatch`: required `role`; properties `prompt`, `role`, `task_path`.
- `wait_handoff`: required `task_path`, `role`, `max_block`; properties add optional `poll_interval`.

### Working-directory contradiction

```bash
rg -n "primary work root|cartopian project root|CARTOPIAN_LAUNCH_CWD|launch_cwd" \
  templates/PROMPT.md protocol/CONVENTIONS.md skills/run-handoff.md \
  cli/commands/dispatch.py wrappers/README.md tests/cli/commands/test_dispatch.py
```

This samples the conflicting template claim and the agreeing protocol, implementation, documentation, and test claims.

### Runtime and recorded version identity

```bash
python3 -m cli --version
git describe --tags --always --dirty
cat /Users/scott/.cartopian/VERSION
cartopian --version
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | /Users/scott/.cartopian/bin/cartopian-mcp
ls -ld /Users/scott/.cartopian/bin/cartopian \
  /Users/scott/.cartopian/bin/cartopian-mcp \
  /Users/scott/.cartopian/cli /Users/scott/.cartopian/mcp_server
```

Observed source/MCP `v1.5.10`, recorded/PATH CLI `v1.3.11`, with installed tool paths symlinked to the source checkout.

### Bridge copy fidelity

```bash
shasum -a 256 \
  templates/clients/codex/skills/use-cartopian/SKILL.md \
  /Users/scott/.codex/skills/use-cartopian/SKILL.md
shasum -a 256 \
  templates/clients/claude-code/commands/use-cartopian.md \
  /Users/scott/.claude/commands/use-cartopian.md
shasum -a 256 \
  templates/clients/gemini/use-cartopian.toml \
  /Users/scott/.gemini/commands/use-cartopian.toml
shasum -a 256 \
  templates/clients/devin/skills/use-cartopian/SKILL.md \
  /Users/scott/.config/devin/skills/use-cartopian/SKILL.md
```

Each sampled source/installed pair had matching hashes on 2026-07-23.

### Installer ownership and preservation

```bash
rg -n "TOOL_SHIPPED|OPERATOR_TOML|OPERATOR_REGISTRY|preserved|seeded|write_version_marker" \
  scripts/install.py tests/test_install.py
```

This samples the code and test anchors for shipped paths, operator-owned files, and the version marker.

### Output record ownership

```bash
rg -n -A24 "record.*= \\{" \
  cli/commands/next_action.py cli/commands/task_bundle.py \
  cli/commands/handoff_packet.py cli/commands/dispatch.py \
  cli/commands/wait_handoff.py cli/commands/report_action.py \
  cli/commands/plan_audit.py cli/commands/close_audit.py
```

This gives independently sampleable record keys for the most frequent context-bearing outputs.

### Canonical test suite

```bash
python3 -m unittest discover -s tests -t .
```

The exact result for this inventory run is recorded in the completion report.

## Coverage checklist

- Skill metadata and discovery: contracts 1-2.
- Protocol documents and templates: contracts 3-5.
- CLI and MCP schemas: contracts 6-8.
- Configuration and version sources: contracts 9-11.
- Installer and update behavior: contract 12.
- Wrappers: contracts 13-14.
- Bridges and client registrations: contracts 2 and 15.
- Lifecycle and startup prompts: contracts 16-18.
- High-frequency context-bearing outputs: contracts 5, 7-8, 14, 16-18 and the measurement-candidate table.
- Named concerns: skill summary (1), startup action (16), assignment working directory (4, 13, 17), configuration shape (9), version identity (11), installed-versus-running server (11-12, 15).
