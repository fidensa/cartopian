# Cartopian Protocol Conventions

Rules for keeping a project coherent over many sessions. This file defines durable protocol contracts: what artifacts exist, what they mean, and why the constraints exist. Procedural runbooks belong in `skills/`.

## Core Principle

Cartopian is filesystem-first. Directories and filenames carry the project's state, so the protocol can work without a database, SaaS control plane, or external services. Cartopian is self-contained — the agent is the software — and runs on the Python standard library alone with no third-party dependencies. Because it is a security tool that governs other systems, containment is security-first: dependencies are attack surface, so Cartopian adds none.

Git is optional. When git versioning is enabled, it records the same filesystem state; it is not the source of protocol authority.

Reviews are optional and explicit. `[reviews]` independently controls planning checkpoints and task closure; role names and descriptions never imply review policy. A required loop names the ordinary resolved role assigned to perform it, while an `off` loop proceeds from accepted completion evidence without that review stage.

AI agents come pre-trained to "be helpful and proactive". That training causes project drift and failure to follow governance verbatim. Cartopian aims to correct this training by producing a rigid framework for agentic behavior that defines exactly what helpful and proactive mean. Agents should not guess, make assumptions, or behave in any way contrary to the conventions or pronciples held by the Cartopian project mangement framework.

## Protocol And Skills

`protocol/CONVENTIONS.md` is the invariant layer. It defines naming, lifecycle authority, artifact meaning, and cross-session constraints.

`templates/*.md` files are the canonical field-schema layer. They define the required headings, frontmatter-style fields, and variant sections for protocol artifacts.

`skills/*.md` files are executable runbooks. They define operational procedure for initialization, planning, task execution, handoff automation, and plan closeout.

`protocol/RISK_AND_PRACTICE.md` explains the risk, judgment, and practice-pack extension contracts and the active source-guidance extension; `protocol/risk-and-practice-contract.json` is the single authority for their machine values. Risk classification is active through the task, prompt, report, CLI/MCP, and handoff projections described below. Judgment cards and practice-pack selection remain defined but inactive: no lifecycle surface activates a card or selects a pack today. Source guidance remains active through the existing task, spec, readiness, handoff, and evidence surfaces and does not activate either pending mechanism. This file remains the invariant layer, and it continues to own review policy, evidence-gate discipline, and every other lifecycle rule. Risk classification never rewrites review policy, roles, capability grants, or launch configuration.

Skill invocation names are derived from skill filenames by dropping `.md` and replacing hyphens with spaces. For example, `run-task.md` maps to `run task`.

`use cartopian` is a common phrase used to start the cartopian project management system. This and other commands correlate to Cartopian MCP server tools and dialogs and other Cartopian skills. Map available skill and MCP server volcabulary before making assumptions about the Operator's instruction meaning.

## Project Scope

A Cartopian project directory is a governance container, not a product codebase.

It tracks phase progress against `IMPLEMENTATION_PLAN.md`, holds specs, tasks, reviews, prompts, reports, and decisions, and keeps one short state file (`STATE.md`) so each project session starts with current context.

It is not a source repository for product code, a workspace shell for product repos, a chat log, journal, or prompt archive.

## Session Startup And Project Selection

A PM session starts only after the project is unambiguous.

Project selection is **registry-only**. The project registry lives at `~/.cartopian/projects.<format>` (per FR-003) and maps project IDs to absolute filesystem paths. Projects may live anywhere on disk; the registry is the discovery mechanism. The PM reads it via `cartopian discover-projects` (FR-004 #5) and resolves a project by its registered `id` or `path`. There is no directory-scan, no working-directory inference, and no protocol-defined "workspace" directory whose children are projects.

A project is selected explicitly when the operator names a registered project ID or registered project path.

For project-agnostic startup requests of any intent class (see [Request Intent](#request-intent)) — "start working", "continue", "check `STATE.md`", "what's next", "pick up where we left off" — the PM resolves eligible projects through the registry:

1. Enumerate registered projects via `cartopian discover-projects`.
2. If exactly one project is registered, use it and name it to the operator.
3. If more than one project is registered and none was selected, ask the operator which project to use. Do not read or mutate project-specific lifecycle artifacts until the project is selected.
4. If no projects are registered, start with `skills/init-project.md`, which scaffolds a new project at an operator-supplied path and registers it via `cartopian register-project`.

After project selection, the PM reads the selected project's `cartopian.toml` and the global `~/.cartopian/cartopian.toml` along the FR-011 resolution chain and resolves the effective PM role. If the agent is the PM for the selected project, session startup duty is:

1. Read `STATE.md` before taking lifecycle action.
2. Reconcile `STATE.md` against the filesystem when it names task state that disagrees with task directories.
3. Tell the operator the current phase, active work, and next protocol action from `STATE.md`.
4. Act on the operator's request per its intent class (see [Request Intent](#request-intent)). Execution begins only when that classification — or the resolved `[automation] initiation` policy — authorizes it.

## Request Intent

Operator requests fall into three classes. Classifying intent is the PM's first interpretive duty, and a request never changes class because automation is configured aggressively.

- **Execution directives** — "continue", "resume", "start working", "run the next task", "keep going", "pick up where we left off". These initiate (or resume) linear execution: the PM continues the active task — or starts the next sequential task when none is active — via `skills/run-task.md` without asking the operator to choose or approve the selection. Pace is governed by the `[automation]` policy; selection is never an operator question. The PM still stops for blockers, for decisions the protocol reserves to the operator, and at the plan-level forks named in `skills/start-session.md` (no plan exists, plan complete).
- **Informational requests** — "what's next?", "check `STATE.md`", "give me status", "where are we?". These are read-only: answer from `STATE.md` and the `next-action` record, name the next protocol action, and stop. An informational request never initiates execution — even under `[automation] initiation = "auto"` — because a question must not acquire side effects.
- **Scoped directives** — "generate PHASE-04's tasks", "write the spec", "revise the plan". These authorize exactly the named operation. When it completes: under `initiation = "operator"` (the protocol default), the PM reports completion, names the next protocol action, and stops; under `initiation = "auto"`, the newly ready open queue may initiate a run (see [Task Execution Order](#task-execution-order)).

Authorization is literal. A scoped directive does not implicitly authorize creating a task, plan item, decision, prompt, request capture, review, handoff, or other governance artifact merely to make the request fit a preferred workflow. If the named operation cannot be completed without a materially different mutation or scope expansion, the PM stops and asks before writing it. The PM also does not transfer authorized Cartopian file manipulation to the operator when a mediated writer can perform it.

An explicit "stop", "pause", or "don't execute" always overrides configuration: it ends any run in progress at the next safe point and suspends automatic initiation until the operator directs execution again.

## Planning Intent Contract

Before requirements or an implementation plan can lock, the PM resolves a
compact record of six operator-owned facts:

- **outcome** — the observable change the project should produce;
- **beneficiary** — the primary person or group served;
- **why now** — the timing or urgency rationale;
- **success signal** — observable evidence that the outcome has been achieved;
- **binding constraint** — the most important non-negotiable boundary; and
- **explicit exclusions** — outcomes, users, or surfaces that are out of scope.

The PM consumes the operator's current input and already approved artifacts.
It reuses facts found there and never asks the operator to repeat a supplied
fact. Each field has one resolution state: `present`, `missing`, or
`conflicting`. Equivalent phrasing does not create a conflict, and an existing
confirmed fact is not discarded merely because later input phrases it
differently. Multiple beneficiaries are `present` when their priority is
explicit. An unobservable success signal is unresolved. An exclusion that
contradicts requested scope is `conflicting`.

For every unresolved field, the PM states one bounded, labeled working
assumption and asks only the focused question needed to resolve that missing
or conflicting fact. This is a conversation, not a blank form. A working
assumption remains provisional: it does not become operator intent until the
operator confirms or corrects it. Requirements and implementation planning
must not lock until all six fields are present and operator confirmation has
been obtained. The confirmation may cover the complete compact record in one
exchange; it does not require repeated cross-model confirmation. This
pre-existing planning-normalization check is PM-derived guidance: it neither
creates nor substitutes for independently resolved request evidence used by review.

The contract has no numerical confidence field. The PM never requests a
confidence percentage, model agreement score, or repeated cross-model
confirmation. Uncertainty is represented only by the resolution states and
the provisional working assumption.

The compact record stores only the six normalized facts, their resolution
states, and any provisional assumptions. It carries no secrets, unnecessary
conversation transcript, or unrelated future-phase detail; normal containment
and deidentification rules continue to apply.

Request Intent remains the separate side-effect authority. An informational
request stays read-only, a scoped directive authorizes only its named
planning operation, and an execution directive alone initiates or resumes
execution. Planning task generation expands only the current active phase and
does not preload future-phase task detail. Resolving or confirming planning
intent never changes the request's intent class.

## Naming

- Tasks: `TASK-NN-NNN.md`. `NN` is the two-digit phase; `NNN` is the three-digit counter within that phase.
- Specs: `SPEC-NN-NNN.md`. Spec numbering is locked to task numbering; specs do not have an independent counter.
- Reviews: `REVIEW-NN-NNN.md`. One task-closure review per task; overwritten on re-review.
- Planning-checkpoint reviews: `REVIEW-PLAN-NNN.md`. `NNN` is a per-project sequential counter independent of task numbering.
- Prompts: `PROMPT-NN-NNN.md`. Temporary task handoff artifacts in `prompts/`.
- Planning-checkpoint prompts: `PROMPT-PLAN-NNN.md`. Temporary review handoff artifacts in `prompts/`.
- Reports: `REPORT-NN-NNN.md`. Task-completion handoff result artifacts in `reports/`, preserved unchanged throughout any task-closure review.
- Task-review reports: `REPORT-NN-NNN-review.md`. Independent task-review completion result artifacts in `reports/`; they share the task's `NN-NNN` identity but never the completion report's slot.
- Planning-checkpoint reports: `REPORT-PLAN-NNN.md`. Temporary planning-review handoff result artifacts in `reports/`.
- Phases: `PHASE-NN.md`. `NN` matches the plan phase order.
- Implementation plan: `IMPLEMENTATION_PLAN.md`. One live plan per project.
- Plan archives: `archive/PLAN-NNN/`. Optional completed-plan snapshots created only during plan closeout.
- Plan closeout summary: `archive/PLAN-NNN/CLOSEOUT.md`.
- Archive index: `archive/INDEX.md`. One-line-per-archive summary table.
- Decisions: `DEC-NNN.md`. `NNN` is a project-local counter within `decisions/`.

Artifact names carry identity only. Human-readable descriptions belong in the
artifact heading and index metadata, never in a filename. Descriptive
filenames are invalid after the `v0.10.0` project migration; normal lifecycle
surfaces do not retain a legacy reader path.

### Trace Chain

The trace chain is identifier-based, not physical nesting. Related artifacts live in their protocol directories.

`IMPLEMENTATION_PLAN.md` defines phase sections and is the numbering authority. A plan ref such as `BUILD-01-003` allocates `01-003`; the matching phase file carries that ref, and the bound task, optional spec, prompt, completion report, review report, and review carry the same `01-003` unchanged. The task file carries the plan ref explicitly, so forward lookup from the plan and backward lookup from any task-scoped artifact are deterministic.

Planning-checkpoint prompts, reports, and reviews are not part of the task trace chain because they attach to planning stages, not tasks.

### Plan/Task Numbering Contract

A plan ref `KIND-NN-NNN` names its work kind, phase (`NN`), and the three-digit phase-wide allocation (`NNN`). Supported work kinds are `BUILD`, `DESIGN`, `RESEARCH`, `TEST`, `RELEASE`, `VERIFY`, and `CORRECTIVE`.

Within a phase, all work kinds draw from one sequence starting at `001`: for example, `DESIGN-04-001`, `BUILD-04-002`, `TEST-04-003`, and `CORRECTIVE-04-004`. Work-kind counters never restart. An allocated phase suffix is not allocated to another kind. One plan ref binds one task, and every corrective task receives its own distinct plan ref.

The plan allocates the suffix before downstream artifacts are authored. A task bound to `KIND-NN-NNN` is `TASK-NN-NNN`; its optional spec is `SPEC-NN-NNN`; and its task prompt, completion report, review report, and review are `PROMPT-NN-NNN`, `REPORT-NN-NNN`, `REPORT-NN-NNN-review`, and `REVIEW-NN-NNN`. A task may not point to a differently numbered umbrella spec, and multiple tasks may not share one task-scoped spec. Missing, malformed, duplicate, ambiguous, or suffix-divergent allocations fail closed with a diagnostic naming the observed and required identities.

The contract is prospective and its boundary is runtime-governed: the corrected rule applies only after the reviewed correction is carried by an operator-owned release tag, that release is installed, and the running process is proven to serve the installed content (`install-cartopian.md`, `protocol/INSTALL_UPDATE_STATE.md`). The boundary is observed from authoritative identity facts — the install root's release-tag receipt, verified installed content, and fresh-process proof for MCP-served calls; hand-typed task prose, caller-selected dates, and filename conventions are not a boundary and cannot claim early activation. Reviewed source alone activates nothing: a source checkout, an unreceipted tree, or content that fails install verification keeps the historical numbering behavior authoritative, and a stale running process keeps it until fresh-process proof succeeds.

The corrected rule governs only work authored after activation: when the mediated task writer creates a task under the active contract, it records that creation in the project's append-only provenance log, and exactly those task-scoped chains are re-verified downstream. This is the existing approved compatibility boundary; it is not an exemption for newly authored work. Every artifact that already exists remains valid, byte-stable, and accepted, with no migration, inventory, receipt, renumbering, rewrite, or reclassification.

Enforcement is mediated and shared. Plan and phase writers refuse conflicting phase-suffix allocations and projections; task and spec writers refuse missing, unallocated, reused, ambiguous, or suffix-divergent bindings. Prompt writing, report routing, review acceptance, and task movement re-verify the governed trace before proceeding. `validate-task-readiness` and `task-bundle` report the same verdict through `plan-ref-aligned`, including plan allocation, phase anchor, and task/spec identity. `cartopian plan-audit` reports the same conflicts as blockers while reporting the activation-boundary state. CLI and MCP surfaces resolve through `cli.numbering_contract`, so their verdicts and trace projections cannot drift.

### Filename Exclusions

Task, spec, prompt, and review filenames never include session numbers, dates, person names, or tool names.

## Status Through Directory

Task status is the directory the task file lives in:

- `tasks/open/`
- `tasks/in-progress/`
- `tasks/in-review/`
- `tasks/done/`

Task files never carry a `status:` field because duplicated status can go stale.

When task-closure review is required, tasks can move backward on failed review. `request-changes` returns the task to `in-progress/`; `reject` returns it to `open/`. The original task remains the unit of work, so failed reviews do not spawn replacement tasks or follow-up tasks.

## Lifecycle Authority

The PM owns Cartopian lifecycle movement: task directory changes, prompt cleanup, handoff result processing, review assignment, and `STATE.md` updates.

Assignees do not move Cartopian task files, delete prompts, rewrite `STATE.md`, or perform PM lifecycle cleanup.

Reviewers create or update review files and record verdicts. They do not move tasks between status directories.

Automated agents do not gain lifecycle authority by completing a handoff. Their reports are evidence for the PM to process.

When PM-owned product-repo git is enabled, PM lifecycle authority also includes product-repo staging, commits, branches, pushes, PRs, merges, and post-merge review-evidence updates for product repos only. See [PM-Owned Product-Repo Branches](#pm-owned-product-repo-branches).

## Lifecycle CLI Guards

`cartopian move-task` enforces artifact prerequisites before executing any status rename. No workaround, manual task-file move, or worktree edit bypasses these checks; the guard runs on every invocation of the CLI command.

Guarded transitions and their prerequisites:

| Transition | Required artifact | Validation |
| --- | --- | --- |
| `in-progress → in-review` (task review required) | `reports/REPORT-NN-NNN.md` | report exists at this task's `NN-NNN` filename; `Status: complete` |
| `in-review → done` (task review required) | `reviews/REVIEW-NN-NNN.md` | `Verdict: approve`; current request context resolves; alignment is non-blocking |
| `in-review → in-progress` (task review required) | `reviews/REVIEW-NN-NNN.md` | `Verdict: request-changes` |
| `in-review → open` (task review required) | `reviews/REVIEW-NN-NNN.md` | `Verdict: reject` |
| `in-progress → done` (task review off) | `reports/REPORT-NN-NNN.md` | report exists at this task's `NN-NNN` filename; `Status: complete` |

`open → in-progress` carries no artifact guard: the PM moves the task first, then authors `prompts/PROMPT-NN-NNN.md` against the `tasks/in-progress/` path, so prompt, report, and review paths agree. Prompt existence is enforced fail-closed at the mediated handoff boundary instead — `cartopian dispatch` refuses to launch when the prompt is missing. Manual (operator-performed) assignment paths do not pass through `dispatch`; there the operator is handed the prompt path directly, and `cartopian plan-audit` reports any in-progress task without a matching prompt as a blocker.

`in-progress → done` is disallowed when task-closure review is required, and `in-progress → in-review` is disallowed when it is off. A task already stranded in `in-review/` after policy is changed to off may move out without a verdict guard. `open → done` is an administrative exception only and requires `--administrative --reason`; ordinary execution never uses it.

Guards apply only to task files whose names match the canonical `TASK-NN-NNN` prefix. Tasks with non-canonical names skip artifact checks. On guarded transitions, a canonical task file with no findable project root is a hard block; the CLI cannot verify prerequisites and will not execute the rename. Unguarded transitions carry no prerequisites to verify, so they execute without requiring a project root.

`cartopian plan-audit <project-path>` is a companion audit that surfaces provenance gaps across the whole project:

- **Artifact chain integrity**: every `TASK-NN-NNN` file in `tasks/in-progress/` must have a matching `prompts/PROMPT-NN-NNN.md`; every file in `tasks/in-review/` must have a matching `reviews/REVIEW-NN-NNN.md` with a `Verdict:` field present.
- **Request-trace integrity**: active task and planning-review prompts carry
  the complete bound verbatim request and separately named PM-derived channel;
  approval agrees with the configured reviewer's comparison. Historical
  reviews without the v0.9 generated context are not rejudged.
- **Infrastructure-artifact scope guard**: assignees must not add `.github`, CI, or other infrastructure artifacts to a work root unless the task explicitly authorizes them. For every dirty work root, changed files under a top-level infrastructure marker (`.github/`, `.gitlab/`, `.gitlab-ci.yml`, `.circleci/`, `.buildkite/`, `.travis.yml`, `.drone.yml`, `azure-pipelines.yml`, `bitbucket-pipelines.yml`, `Jenkinsfile`) emit an `unauthorized-infra-artifacts` warning unless a task naming that work root carries the explicit task-file field `Infra authorized: <markers>` — a comma-separated list of the markers it authorizes (e.g. `Infra authorized: .github`), or the blanket `Infra authorized: yes`. Prefer the marker-scoped form. Prose mentions of a marker are not authorization, and attribution alone is not authorization. This is a warning for the operator, not a blocker.
- **Work-root provenance**: for each configured work root, if uncommitted git changes exist and no active task is assigned to that root (or no active prompt exists for the assigned task), the audit's behavior depends on the effective `git.pm_owns_product_branches` setting.
  - When `pm_owns_product_branches = true`, the PM owns product-repo plumbing, so dirty state without an active prompted task is anomalous and the audit emits an `unattributed-work-root-changes` warning.
  - When `pm_owns_product_branches = false` (the protocol default), product-repo state belongs to the assignee and dirty work roots are expected. The audit does not emit a warning; instead it emits an informational `work-root-attribution` entry naming the most-recently-modified task that targeted this work root and its assignee (or recording that attribution is unknown if no prior task names the root).

Run `plan-audit` at session startup and before plan closeout. A non-zero exit is a PM-level blocker; do not advance lifecycle state until all blockers are resolved. Warnings should be surfaced to the operator, but they do not block lifecycle movement by themselves.

## Tasks

Tasks are assignment-sized units of work derived from the current phase and implementation plan. The domain-neutral lifecycle is `Plan -> Contract -> Evidence -> Outcome`; for software work this is the familiar `Plan -> Spec -> Test -> Code`. Task execution procedure is defined in `skills/run-task.md`.

Task files follow the canonical field schema in `templates/TASK.md`.

Open task files should contain enough context to assign and review the work without becoming progress journals.

Every new task declares `Source guidance: task | spec | n/a`. `task` means the task owns the source record; `spec` means its named spec owns the one record; `n/a` means source authority is not material to the outcome. A missing declaration remains readable only for legacy tasks. The owner modes prevent a task and spec from maintaining duplicate records that can drift.

If completion evidence arrives before assignment/start was recorded, the PM may fast-forward the task to the status supported by that evidence.

### Task Execution Order

Task execution is **linear by default**. The next task is deterministic: the first file in `tasks/open/`, ordered by phase (plan order), then by task filename within the phase, skipping tasks whose `Blocked by:` dependencies are not yet in `tasks/done/`. This is the same selection `cartopian next-action` emits as `next_open_task`.

**Selection does not authorize execution.** Deterministic selection answers *which task would run next*; it does not answer *whether execution begins*. Execution begins only from an operator execution directive or from `[automation] initiation = "auto"` (see [Request Intent](#request-intent) and the `[automation]` policy under [Handoffs](#handoffs)). A populated open queue is a fact about the plan, not permission to run it.

Within an initiated run, choosing the next task is a computation, not a conversation:

- The PM does not ask the operator which task to run next or whether to continue an already in-progress task. It proceeds.
- When a task completes and automation budget remains (see the `[automation]` policy under [Handoffs](#handoffs)), the PM continues to the next sequential task in the same run.
- The operator may override the order at any time by naming a task; an explicit override applies to that task only and does not change the default for subsequent selections.
- Deviating from sequential order on the PM's own initiative is a protocol violation.

**Directive scope.** A scoped directive ("generate PHASE-04's tasks", "write the spec", "revise the plan") authorizes only the named operation; completing it never rolls into execution on its own. Under `initiation = "operator"` the PM reports completion and stops. Under `initiation = "auto"` the newly ready open queue may initiate a run, subject to the same stop conditions. An explicit "stop", "pause", or "don't execute" always wins over configuration.

Linear movement stops — and the operator is consulted — only at genuine stop conditions: a readiness or audit blocker, a failed/blocked/rejected handoff, evidence gates that cannot be satisfied, a decision the protocol or plan reserves to the operator, a plan-level fork (no plan, phase tasks not yet generated, plan complete), or exhaustion of the `[automation]` budget.

## Source-Backed Work

Source-backed work uses the existing planning, task, specification, prompt, evidence, handoff, validation, and report contracts. It introduces no new lifecycle artifact, score, approval loop, or specialist context.

The machine vocabulary and field labels live once under `source_guidance` in `protocol/risk-and-practice-contract.json`. `cli/source_guidance.py` reads that authority and projects one deterministic record through `task-bundle`, `validate-task-readiness`, `handoff-packet`, `dispatch`, `parse-report`, and `report-action`. Because every CLI command is exposed through the shared MCP registry, the tool surface returns the identical record and diagnostics rather than reimplementing them.

A source record contains:

- at least one authoritative source identity;
- the effective date, publication date, edition, revision, or version that makes each source applicable, plus `current | stale | unknown` status and its governed scope;
- exactly one conflict disposition: `none | resolved | unresolved`, with a precedence rule or named decision authority and the applied decision when resolved; and
- either `none` or every claim that remains unverified, each naming whether it is decisive, the missing authority or evidence, the consequence of proceeding, and the next decision or proof required.

The rule is dominance, not averaging. Missing decisive authority, missing or stale applicable context, an unresolved conflict, or a decisive unverified claim fails readiness and blocks handoff. Favorable observations from other sources cannot offset that condition. The failure record names the exact claim or source condition, why proceeding matters, and the next authority or proof required. It never emits a numeric score.

Non-decisive unverified claims may remain only when the full failure signal is explicit. They are not converted into verified claims and do not silently grant authority. A complete task report for source-backed work carries `## Source evidence` in the same shape and names the non-empty subset of governing sources and applicable contexts actually used. It does not repeat sources that were not applied, and it may not introduce a source or context absent from the governing guidance. A complete report cannot close with a decisive unverified claim. `report-action` fails such a purported completion report closed as `failed-to-parse`; a `blocked` report may still truthfully report that the required authority or proof could not be obtained.

Source identities and scopes carried into coder prompts remain subject to normal deidentification. `handoff-packet.source_guidance.deidentified_guidance` is the assignee-facing rendering; the PM does not paste raw task or spec identifiers into a prompt. Containment is unchanged: delegated spec guidance must resolve inside the selected project's `specs/` directory, and source guidance grants no filesystem, lifecycle, request-intent, publication, or operator authority of its own.

When a source identity itself contains a PM identifier, the assignee rendering
uses a deterministic `project-management-source sha256:...` alias instead of
leaving an unusable partial path such as `decisions/.md`. Completion evidence
is validated against the same projected identity. The raw owner record retains
its full PM identity; the alias exists only across the deidentified handoff
boundary.

## Risk Classification and Scaled Governance

Every new task records the five observable conditions defined in `protocol/risk-and-practice-contract.json`: consequence reach, reversibility, authority, ambiguity, and evidence coverage. Each record carries one declared state and a bounded supporting fact identity. Missing observations fail closed; an observation that cannot be established uses its declared `unknown` state. No numeric confidence or averaging is used.

`cartopian classify-risk` reads those bounded facts and the shared registry. The result's band is the highest declared state floor. Its ordered reasons are every observation at that floor in registry order, followed by the registry's one evidence, independent-review, operator-gate, and contingency expectation for that band. The CLI handler is automatically exposed as the `classify_risk` MCP tool, so both surfaces execute the same function and return the same structured record.

The result is projected into the assignment and completion report; handoff construction consumes it and does not reclassify from prose. Configured review policy remains authoritative and separate: risk does not edit whether a configured loop runs, who performs it, any capability grant, or any launch/automation permission. When a derived independent-review expectation exceeds configured policy, the difference is an operator gate. Cross-model or additional review happens only under configured policy or explicit scoped operator direction.

Critical work uses `cartopian adversarial-review-context` when its independent challenge is authorized. The command validates the supplied result against the current registry, reads a delivered artifact file and governing-contract file afresh from the project or configured work roots, applies a combined byte ceiling, and returns their content identities. The context payload contains exactly the artifact and governing contract; it has no author-conclusion input and admits no unrelated history. Required evidence and derived expectations remain top-level contract metadata. An unbounded, unreadable, stale, or out-of-root input fails closed before any partial context is emitted. Independence means the challenger did not produce the decisive work; it does not prescribe a fixed panel, role name, model, or reviewer count.

## Specs

Specs are mutable, single-file **work contracts** — a generic agreement between the PM and the assignee about what "done" looks like for the work the spec covers. The same artifact can carry a software requirements and design contract, operating procedure, creative brief, research plan, checklist, or similar domain-neutral work agreement. The `SPEC-NN-NNN` identifier prefix, the `templates/SPEC.md` filename, the `Spec:` task-file field, and the `specs/` project directory are compatibility labels, not a declaration that every project is a software project.

Every spec declares `Profile: software | general`. Profile selection follows the outcome governed by that spec, not the label applied to the overall project:

- **Software profile.** Required when the spec's end outcome is creating or changing executable software or a technical contract intended for software implementation. This includes applications, services, libraries, command-line tools, automation scripts, and implementable schemas, APIs, or integrations. A software project may still use the general profile for a genuinely non-software outcome such as a research report, launch procedure, or creative asset.
- **General profile.** Required when the spec governs a non-software outcome such as an operating procedure, creative brief, research plan, physical activity, or other work contract. A generally non-software project still uses the software profile for any spec whose outcome is software.

A software-profile spec defines requirements and technical design, not the implementation the assignee should type. Its **SRS** portion covers **Overview & Goals**, **Functional Requirements**, **Non-Functional Requirements**, and **User Stories & Use Cases**. Its **TDS** portion covers **Architecture & Structure**, **Data Models**, **APIs & Integrations**, and **Edge Cases & Error Handling**. The PM specifies observable behavior, design boundaries, externally imposed constraints, and acceptance conditions while leaving source-level implementation decisions to the assignee.

Software-profile specs must not contain source or executable code, pseudocode, step-by-step algorithms, function or class bodies, complete configuration or build files, or copy/paste-ready implementation snippets. Contract notation is allowed when it communicates a requirement rather than an implementation: diagrams, tables, field/type definitions, endpoint signatures, protocol grammar, and concise example payloads or input/output values. A required named algorithm, standard, framework, or platform may be recorded as a constraint when it comes from an approved requirement or decision; the PM does not turn that constraint into implementation code.

General-profile specs use the domain-neutral work-contract sections in `templates/SPEC.md`. The software-code prohibition does not prevent a general-profile spec from quoting source material needed for a non-software outcome, but the PM still may not use a general profile to evade software-profile rules. Each authored spec keeps exactly one body profile and removes the unused template profile and its instructional text.

The current file is the current version.

Spec files follow the applicable canonical profile schema in `templates/SPEC.md`.

A spec may carry `Status: draft | locked`. `locked` means the current contract has been approved; it does not make the file immutable forever.

Approved specs change in place after the project's required review or approval. Version-suffixed spec files (`-v1`, `-v2`) and spec supersession chains are not part of the protocol.

A spec is surfaced to an assignee **deidentified**, never as the raw file. The canonical spec keeps its full traceability (the `SPEC-NN-NNN` title, singular `Plan ref:`, and the `## References` section) for the PM; `cartopian render-spec <spec-path>` produces the assignee-facing rendering, which strips that scaffolding and any inline identifier while preserving the work-contract prose. The PM inlines that rendering into the coder prompt's `## Specification` section, so PM identifiers stay inside PM artifacts and never reach product code via the spec the coder reads.

A source-backed spec declares `Source guidance: required` and owns the one record used by tasks that declare `Source guidance: spec`. A non-source-backed spec declares `n/a`. `write-spec` refuses a declared required record that lacks current authority, applicable date/version context, conflict disposition, or complete unverified-claim handling.

## Reviews

Review policy is resolved project over global, key-by-key:

```toml
[reviews]
planning = "required"       # required | off
planning_role = "reviewer"  # any resolved role name
task_closure = "off"        # required | off
task_role = "reviewer"      # required only when task_closure is required
```

The protocol defaults both loops to `off`. A project can therefore override globally required review by setting its local mode to `off` without removing the inherited role. Policy answers whether review happens; the role field answers who performs it; capability grants answer what that role may access and do. No behavior keys on the literal role name `reviewer`, on description prose, or on a preset name.

Task-closure reviews use `reviews/REVIEW-NN-NNN.md`. There is one review file per task, overwritten on re-review. There is no round suffix and no closure sign-off section.

Planning-checkpoint reviews use `reviews/REVIEW-PLAN-NNN.md`. They follow the canonical field schema in `templates/REVIEW.md` but attach to planning stages, not tasks.

Planning-checkpoint reviews are temporary artifacts deleted when the checkpoint is approved or superseded.

Review verdicts are:

- `approve`: task moves to `done/`.
- `request-changes`: task moves to `in-progress/`.
- `reject`: task moves to `open/`.

## Up-front Operator Request Evidence

Before a task assignment, planning review, or task-closure review, Cartopian resolves exact
operator excerpts from three provenance-bearing sources: explicitly attributed
verbatim quotations in applicable decisions, supported host-provided chat
records, and optional immutable request-store records. A native host callback
is one possible intake adapter, not a completion gate when adequate exact
evidence already exists. Resolution is infrastructure behavior, not a later
confirmation, restatement, review step, scope choice, or requiredness choice.

New decision evidence uses one exact structural marker immediately before its
Markdown block quote:

```text
Operator request quote for: project:project

> <unmodified operator quotation>
```

The value is exactly one governed unit: `project:project`,
`planning:PLAN-NNN`, or `task:TASK-NN-NNN`. The unit-bearing marker makes
a decision self-selecting and lets a fresh project establish exact evidence
before requirements exist. Malformed or ambiguous markers fail closed.
Ordinary block quotes, loosely adjacent PM attribution, and ordinary plan,
phase, spec, task, prompt, report, or decision prose remain PM-derived. Bounded
backward compatibility recognizes the exact existing DEC-007, DEC-008, and
DEC-009 attribution wording only when an applicable artifact explicitly
selects those decisions through `## Operator intent`, `## Original request
evidence`, or `## Request evidence`; it is not a general prose heuristic.

Decisions are evidence containers, not PM derivatives that require earlier
request evidence. Writing one does not make later optional direct host capture
late. Decision-first and capture-first intake orderings are both valid. Later
requirements, plans, phases, tasks, specs, and prompts still fail closed when
neither an applicable exact decision/chat source nor an optional request record
resolves. There is no raw decision-deletion recovery path.

For task assignment, `write-prompt --task <absolute-task-path>` generates the
exact-request channel before the coder sees the prompt. The assignment context
binds the request to the PM-derived task and applicable spec. `dispatch`
recomputes that binding and fails closed before process launch when the section
is absent, edited, or stale. The assignee compares the exact request with all
PM-authored instructions before changing a work root. Added implementation,
destinations, features, conventions, or scope are blockers, not implicit
authority; permission to propose an option does not authorize implementing it.
Task-closure review applies the same authority rule to the delivered outcome,
so a PM-authored liberty is request drift even when the task and coder agree.

Supported host chat records are UTF-8 JSON files below `requests/chat/` with
schema `cartopian-host-chat-v1`. They identify the operator role, governed unit,
original-or-correction kind, contiguous order, exact text hash, host,
conversation, and message identities. Records for unrelated units are not
selected. Optional `cartopian capture-request` records keep their existing
original/correction ordering and immutable exact-text identity.

The enforceable CLI boundary is deliberately precise. `capture-request` is
absent from the managed-agent MCP registry and refuses whenever
`CARTOPIAN_ROLE` (a dispatched role) or `CARTOPIAN_MCP_TOOL_CALL` (an in-process
managed tool call) is set. The local CLI does not cryptographically authenticate
a human or prove the authorship of bytes supplied by an otherwise unmarked
process. Content identity proves exact preservation *after host intake*, not
human authorship by itself. Accordingly, a PM-transcribed or paraphrased file
is never valid proof of verbatim operator origin; a host integration that uses
direct intake must pass its raw operator-message payload rather than a
model-produced copy.

Multiple exact excerpts may express the initiating ask and later explicit
corrections. Each selected excerpt retains its source kind, stable source
identity and path, full-source SHA-256 identity, exact-text SHA-256 identity,
governed unit, and deterministic evidence order. Duplicate content is emitted
once. Assistant messages, unattributed quotations, and unrelated conversation
history are never promoted into the trace.

Planning and task-closure reviews carry two generated channels:

1. `## Original operator request (verbatim)` contains the resolved exact
   excerpts and ordered explicit corrections.
2. `## PM-derived guidance and delivered outcome` names the requirements, plan,
   task, spec, prompt, report, and other delivery evidence prepared later.

`cartopian review-context` is the common read-only projection used by prompt
generation, dispatch, manual handoff, report parsing, lifecycle guards, and
audit. The context identity covers the review target, ordered evidence and
source identities, legacy state, and PM artifact paths. The PM/delivery channel
contains only artifacts that exist when the prompt snapshot is generated,
including canonical specs and applicable phase and prior-review artifacts.
Later lifecycle outputs do not retroactively alter that snapshot; regenerating
a review prompt takes a new snapshot. Any selected-source mutation or prompt
omission makes the binding stale. Exact content is bounded to 24 KiB per
excerpt and is never truncated.

Task-assignment snapshots exclude completion-report and task-review slots.
Those are outputs of the handoff being prepared, so a stale retry artifact may
be cleared without invalidating the new assignment binding. Task-closure
snapshots continue to bind the preserved completion report and applicable
review evidence.

Generated text names both the review target and every excerpt's governed unit.
Planning checkpoints explicitly consume project-planning evidence. A planned
task inherits approved planning evidence without another operator restatement
only when Cartopian can verify the complete ancestry chain: the task ID, its
`Phase:` header, and its `Plan ref:` share one phase; the plan ref exists in
both `IMPLEMENTATION_PLAN.md` and the canonical phase file. Assignment and
task-closure review revalidate that chain, then select checkpoint-bound exact
evidence from every canonical planning review whose `Plan ref:` covers the
task, whose verdict is `approve`, and whose request alignment is `aligned`.
Canonical same-kind ranges written as `REF through REF` are supported. A stale
or missing evidence identity in an applicable approval fails closed. When no
applicable approved checkpoint carries exact evidence, the verified planned
task falls back to project-origin intake for compatibility. Direct task-bound
evidence still takes precedence, allowing an explicit correction or scope
addition to govern that task without mixing it with inherited evidence.

An ad-hoc task (`Plan ref: n/a`), a task with malformed or mismatched ancestry,
or a task whose plan anchors are missing never inherits project intent. When no
applicable task-bound decision quote, host chat turn, or optional task record
resolves for such a task, `unit-request-not-captured` fails closed. Semantic
scope widening is still detected by comparing the exact request channel with
the plan, task, spec, prompt, and delivered outcome; inheritance does not turn
PM-authored scope into operator intent.

The configured review role compares the two channels. The operator supplies the
request but is not the reviewer. Review files and completion reports record:

```text
Request alignment: aligned | drifted | unavailable-for-legacy
Request evidence: <ordered evidence identities> | none
```

Contradiction, narrowing, widening, omission, or substitution is `drifted` and
blocks approval even when every PM artifact agrees with the implementation.
`unavailable-for-legacy` is non-blocking only when the prompt itself proves the
unit predates v0.9 capture. Missing or malformed comparison evidence fails
closed for new work.

Migration from v0.8 fabricates no request and rewrites no review. Historical
reviews are evaluated according to the generated context present in their own
prompt; changing the project marker never retroactively invalidates them.
Legacy `intent/ATTEST-*.md` attestations and `intent/records/OIR-*.md` records
remain inert historical files and do not govern approval.

## Prompts

Prompts are temporary, assignee-directed handoff artifacts in `prompts/`. They restate the requirements, acceptance criteria, context, output expectations, scope boundaries, done criteria, and completion report requirements.

Prompt files follow the canonical field schema in `templates/PROMPT.md`.

Review prompts are produced with `cartopian write-prompt --review-kind ...`.
The writer resolves the intake trace and owns both generated review-context
sections; authored copies are replaced. Automatic dispatch, manual
`review-context --prompt` preflight, report parsing, and audit recompute the
same target.

Prompts must include complete absolute paths for every resource the assignee is expected to use or produce. They must not rely on relative path interpretation, current working directory assumptions, or vague instructions such as "read the PM system."

For source-backed work, the prompt includes the resolved record's deidentified rendering and requires the corresponding source evidence at completion. Prompt authoring does not repair or reinterpret an invalid record: readiness and handoff fail first with the record's actionable blockers.

Coder (task) handoffs are **deidentified**. Project-management identifiers — `TASK-NN-NNN`, `SPEC-NN-NNN`, plan refs `KIND-NN-NNN`, requirement refs (`FR-`/`NF-`), decision refs (`DEC-`), and the like — exist only inside PM artifacts; they are not surfaced to the assignee. A coder prompt names the work by its title and addresses every resource by file path, and the coder writes its report to the given report path without recording any identifier. Cartopian links the report back to its task by the report *filename* (`REPORT-NN-NNN.md`), so the assignee never needs — and is never given — a task identifier to copy into product code.

Task prompts are deleted when the task reaches `done/` or when the prompt is superseded before assignment. Planning-checkpoint prompts are deleted when the checkpoint is approved or superseded. Prompts are never archived as durable records.

## Reports

Reports are protocol-defined handoff result artifacts in `reports/`. They are evidence for the PM, not replacements for task, review, decision, or backlog records.

Report files follow the canonical field schema and variants in `templates/REPORT.md`.

The neutral task-report core is `## Identity`, `## Completion evidence`, `## Remaining risks`, and `## Ready to close`. Specialized software and document sections (`## Files changed`, `## Deliverable`, `## Test evidence`, `## Commit / PR`) are optional evidence shapes. For compatibility, an exact `## Files changed` or `## Deliverable` heading may stand in for `## Completion evidence`, and `## Ready for review` may stand in for `## Ready to close`.

`## Source evidence` is conditionally required when the governing task resolves to valid source guidance. It repeats the shared record shape as completion evidence, not as a second authority: it names the non-empty subset actually applied and what remains unverified. `parse-report` and `report-action` project the validated record; a complete source-backed report with no applied source or with a source identity/context absent from the governing guidance fails closed. Sources in the broader guidance that were not applied are not completion-report requirements.

Task completion reports use `reports/REPORT-NN-NNN.md`. Task review completion reports use the independent `reports/REPORT-NN-NNN-review.md`. Planning-checkpoint review completion reports use `reports/REPORT-PLAN-NNN.md`. The task-completion report is preserved unchanged throughout task review — the reviewer reads it directly from its compatibility path — and neither task-scoped artifact can satisfy the other's completion signal.

Task review completion reports declare the absolute `Task path:` in `## Identity`. The path must name the task implied by the report filename's `NN-NNN` identity in its current lifecycle directory; a missing, stale, or wrong task path is invalid completion evidence. This requirement does not apply to deidentified task completion reports or to planning-review completion reports.

Review and planning-review reports also carry the request alignment and
evidence fields from the bound prompt. Report parsing recomputes the binding at
completion time. An approving report with drifted, missing, malformed, or stale
evidence is `failed-to-parse`; genuine historical unavailability is explicit
and non-blocking.

Reports must not include secrets or unnecessary sensitive environment data such as API keys, credentials, tokens, or private connection strings.

Each handoff has one expected protocol-derived report path. A stale, missing, malformed, incomplete, internally inconsistent, unsupported, or path-mismatched report is not valid completion evidence.

Report parsing outcomes are:

- `accepted`: well-formed and actionable.
- `blocked`: explicitly blocked or operator judgment is required.
- `failed`: explicitly failed.
- `failed-to-parse`: missing, malformed, incomplete, inconsistent, unsupported, or contradicts expected paths.

`failed-to-parse` is a PM-level blocker. It preserves the prompt and invalid report for inspection and prevents lifecycle movement.

## Project Resources

`resources/` at the project root is the durable home for project supporting artifacts — research documents, user stories, reference papers, images, spreadsheets, datasets, or generally any format of material that supports planning and implementation but is not itself part of the product. The routing rule is intent-based: anything intended to become part of the product belongs in a work root at an operator-chosen path (see Document Deliverables); anything that exists to support the project's own planning and execution belongs in `resources/`. Supporting artifacts never land loose in a work root.

- Any file format is allowed, and subdirectories are allowed.
- The PM writes into `resources/` only through the mediated `cartopian write-resource` command (see PM Scope) — typically to persist an assignee-returned document deliverable. The operator may place files there directly.
- PM artifacts reference resources by project-relative path (`resources/<path>`); prompts surface them to assignees by absolute path, like any other referenced location.
- `resources/` is not part of the live plan surface. `reset-plan` never clears it, and it carries forward across plans by default. At plan closeout the operator explicitly decides its disposition: carry forward as-is, and/or snapshot it into the plan archive (`archive-plan` includes `resources/`). Pruning is operator-performed; no mediated command deletes resources.

## Document Deliverables

A document-deliverable task is one whose work product is a durable document — research findings, a design, an evaluation, an analysis — rather than code. Such a task declares a `Deliverable:` field so its work product is written to a durable file the reviewer reviews directly, and the completion report stays a thin summary. `DESIGN` and `RESEARCH` plan items are document work and therefore cannot launch with an absent or `n/a` deliverable. This is the same shape as a code task: code is written to the work root and the report summarizes it; a document is written to a deliverable and the report summarizes it. A report is never the home of the work product, and reports are not durable lifecycle records: both task-scoped reports (`reports/REPORT-NN-NNN.md` and `reports/REPORT-NN-NNN-review.md`) are removed at supported task closure, after their evidence has been consumed.

### The Deliverable field

`Deliverable:` is name-only and deidentified — it carries no task, plan, spec, or requirement identifier, the same discipline as `Work root:`. It takes one of two forms, routed by intent:

- `root:<relative/path>` (work-root deliverable) — for a work product intended to become **part of the product**. The assignee writes it into the named work root directly, exactly as it writes code. The path is **operator-chosen**: the PM captures it from the operator at task authoring or at assignment and never invents or assigns it itself.
- `project:resources/<relative/path>` (project-resource deliverable) — for a **supporting artifact** of the project itself. The document lands under the project's `resources/` directory; a project-mode path outside `resources/` is invalid (`validate-task-readiness` blocks the task). The assignee is not granted write access to the project, so it returns the document inline in its completion report and the PM persists it via `cartopian write-resource`.

The field is set at task authoring, or captured at assignment when the PM prompts the operator for the location; the deliverable's destination is operator authority — the protocol fixes the `resources/` home for supporting artifacts, and the operator supplies or confirms the relative path in either form. `n/a` (or an absent line) means the task has no durable document deliverable. `handoff-packet` and `task-bundle` resolve the field to an absolute `deliverable` record (mode, root, relpath, absolute path, existence) so the PM sources the path without re-reading the task.

### Work-root deliverables

The assignee writes the complete work product to the resolved deliverable path (inside a declared work root, already in its write scope). The completion report only summarizes what was done and points to the deliverable. The review prompt names the deliverable path as the primary artifact to review.

### Project-resource deliverables

The assignee returns the complete work product inline in the report's `## Deliverable content` section. Before clearing the report for the review handoff, the PM persists that content to the resolved `resources/` path with `cartopian write-resource`. The review prompt then names the persisted file as the primary artifact to review. The inline-return path carries text formats (markdown, CSV, and the like); a binary work product (an image, a binary spreadsheet) cannot travel through a report, so it is produced in a work root and brought into `resources/` by the operator. (A deployment may instead grant the assignee role write access to the project directory, in which case a project-resource deliverable is written directly like a work-root one; the inline path is the default that needs no extra grant.)

### Durability

The deliverable is the durable record of the work; the report may be cleared and is not a substitute for it. A deliverable is the assignee's produced knowledge artifact — distinct from a decision (`decisions/DEC-NNN`, a PM ruling) and from a spec (`specs/SPEC-NN-NNN`, the input contract). When a deliverable's findings warrant a durable protocol ruling, the PM still records that as a decision.

`plan-audit` enforces this durability: a task in `in-review` or `done` that declares a `Deliverable:` whose file is missing is a `missing-deliverable` blocker (skipped only when a work-root deliverable's name is unmapped on the auditing machine, since existence cannot be verified there). Placement is guarded at the transition that consumes it: `validate-task-readiness` blocks a task whose project-mode deliverable escapes `resources/`, and `plan-audit` emits a `deliverable-outside-resources` warning for a legacy artifact that predates the rule.

## Roles

Each `[roles.<name>]` table in `cartopian.toml` carries a required one-line `description` and may carry capability grants, role-local launch facts, and automatic-launch permissions. Role names are operator-chosen identifiers; names and descriptions explain responsibility but confer no review, launch, selection, capability, or identity authority.

Roles exist to be assigned, which means a PM who takes on the work rather than assigning it is undermining the system. Assign work to role(s) with appropriate descriptions/permissions.

### PM Scope

The PM role is bounded to project-management authoring:

- **Directory scope.** The PM may only read or mutate files inside the project directory currently being managed. It may not modify files outside that project — including sibling Cartopian-governed projects, the Cartopian protocol repository itself, or any unrelated repository the operator happens to have on disk.
- **File-type scope.** Within the managed project, the PM authors markdown (`.md`) files — CREATE, READ, UPDATE, DELETE. There are two non-markdown exceptions. The project's own config files (`cartopian.toml`, `cartopian.local.toml`): the PM may edit them, but only through the mediated `cartopian update-config` command and only on the operator's explicit request (see **Config management** below). And `resources/`: the PM may persist a file there, but only through the mediated `cartopian write-resource` command, and only as transcription — persisting an assignee-returned deliverable or operator-supplied content verbatim, never producing the substantive content itself. All other non-markdown work — source code, data files, build artifacts, executables — must be dispatched to another role via a handoff.
- **Config management.** The PM manages the project's config on the operator's behalf, so a non-technical operator never has to find or hand-edit `cartopian.toml`. Config edits are operator-*requested*, never proactive or routine: the PM does not offer or solicit config changes during ordinary lifecycle flow, and applies them only when the operator explicitly asks (or approves a migration). All PM config edits go through `cartopian update-config`, which validates the closed key schema and the resulting effective config and writes atomically; the PM still reads effective config via `cartopian resolve-config`. This scope covers only config files *inside the managed project directory*; the global `~/.cartopian/cartopian.toml` lives outside every project and is authored by the workspace-setup flow (`skills/init-workspace.md`), not by a per-project PM. Enforcement is precise: a structured raw-edit tool aimed at a config file is denied regardless of grants (the mediated command is the only edit path), while shell-routed edits and advisory-tier hosts remain documented residuals, exactly as for every other governed path-class.
- **Migration is PM-owned.** A project's internal protocol-schema version is separate from the installed Cartopian application's release version. Bringing that project schema current is PM-owned orchestration performed on operator approval: the PM applies each applicable `protocol/CHANGELOG.md` entry, doing config edits via `cartopian update-config`, ordinary project authoring through the structured writers, and shipped deterministic filesystem transforms through `cartopian apply-migration-entry`. The migration executor accepts only a registered project root and shipped entry version; its closed registry owns all paths and transformations. Judgment-dependent values return as structured pending PM actions and block the marker bump until resolved. Operators are not expected to edit the version marker or perform file surgery. See `skills/migrate-project.md`.
- **Authoring discipline.** A PM that implements work rather than assigning it is a protocol violation, regardless of which file types are involved.

These limits apply to every PM. The PM is always the interactive orchestrator of a session — it is never itself launched as a handoff (there would be no PM to launch it), so `roles.pm.agent` and `roles.pm.auto_launch` must not be configured.

```toml
[roles.pm]
description = "Plans phases, dispatches handoffs, integrates results."

[roles.operator]
description = "Approves locks, unblocks, sets cadence."
```

The protocol-default roster is **`pm` and `operator`**. Operators may add any further roles their project needs. Common example labels include `coder`, `reviewer`, `editor`, and `researcher`, but all are illustrative only. Review assignment is configured under `[reviews]`; role names and descriptions carry no protocol behavior, so an operator may use another label if desired.

Launch and permission remain distinct:

- A declared non-PM role with `agent` has a resolved agent/options record.
- A declared role without `agent` uses manual handoff; the PM surfaces the prompt and the operator acts.
- `auto_launch` independently grants automatic launch for listed assigned work types.
- A role omitted from `[roles]` does not exist in this project; tasks and review policy may not assign it.

## Handoffs

CLI handoff automation is optional. Manual handoff remains valid for every role.

The reusable handoff procedure is `skills/run-handoff.md`. Planning uses the same contract through `skills/plan-project.md`; task execution uses it through `skills/run-task.md`.

Use role-local launch facts only for roles that need a named agent or Cartopian agent wrapper:

```toml
[roles.coder]
description = "Implements tasks per spec."
grants = ["coder-like"]
auto_launch = ["task_run"]

agent = "cartopian-codex"
model = "gpt-5-codex"
effort = "high"
timeout = "60m"

[roles.reviewer]
description = "Reviews assigned checkpoints."
grants = ["reviewer-like"]
auto_launch = ["task_review", "planning_review"]

agent = "cartopian-gemini"
timeout = "30m"
```

Role launch and permission fields are:

- `agent`: agent or Cartopian agent wrapper name.
- `model`: optional model identifier, exported to the wrapper as the `CARTOPIAN_MODEL` environment variable; the wrapper translates it into the tool-specific model-selection flag. When unset, no variable is exported and the tool's own default model applies.
- `effort`: optional effort/thinking level for the assigned agent, exported to the wrapper as the `CARTOPIAN_EFFORT` environment variable; the wrapper translates it into the tool-specific effort flag. When unset, no variable is exported and the tool's own default effort applies. A value outside the wrapper's CLI-wide vocabulary makes the wrapper warn on stderr and launch at the default; whether a specific model supports a vocabulary-valid level is the tool's own behavior.
- `auto_launch`: a closed unique list containing applicable assigned work types from `task_run`, `task_review`, and `planning_review`. The list chooses launch mode only after `[automation].initiation` has allowed the run to begin and `confirmation` permits the handoff; it never initiates a run. It does not assign review, control pace, select a task, or grant capabilities. `cartopian dispatch` enforces the applicable permission fail-closed.
- `timeout`: optional maximum wall-clock duration for PM-launched handoffs. The protocol default is `60m`.

Legacy compatibility only: migration tooling recognizes `project.protocol_version`, `[roles.<role>.launch]`, `[handoffs.<role>]`, `auto_start`, `auto_start_tasks`, `auto_start_reviews`, and `planning_reviews` as migration-source vocabulary. Preferred validation rejects them, and current generation, editing, examples, CLI/MCP authored schemas, and canonical TOML never emit them. Resolved machine records may expose a derived `launch` projection.

`roles.<role>.timeout` — resolved along the project → global chain, defaulting to `60m` — is the single source of truth for the handoff deadline. The launcher exports it to the wrapper as the `CARTOPIAN_TIMEOUT` environment variable (see `skills/run-handoff.md`), and the wrapper is the sole enforcer: it kills the assignee at that deadline (exit `124`). No other timer exists — no per-tool CLI timeout flag is set independently, and the PM runs no concurrent timer or watchdog — so no second timer can kill a legitimate long-running handoff before the SSOT deadline. The PM observes completion through the wait primitives in [Waiting For Completion](#waiting-for-completion).

Every automated handoff follows this argument contract:

```text
<agent> <absolute prompt path>
```

The prompt path is passed as one argument. Tool-specific non-interactive flags, sandbox settings, approval settings, and environment variables belong in a wrapper executable, not in `cartopian.toml`.

Pre-built wrappers for common CLIs (Codex, Claude Code, Gemini, Devin, opencode, Hermes) are in `wrappers/`. See `wrappers/README.md` for installation.

### Foreground Completion

An automated handoff is one non-interactive session, and the assignee's final result is process exit. Nothing the assignee started survives that exit, and no completion notification can resume it — the session is not suspended between turns, it is over. Two rules follow, and every prompt states them (`templates/PROMPT.md` § Completion report):

- **Completion-critical work runs in the foreground.** Any command whose outcome the completion report depends on — test suite, build, validation script, fixture run, evidence-gate command — must be run in the foreground and waited for before the report is written. Backgrounding it and ending the turn on the expectation of a later notification discards the run and the report with it. A run that cannot finish inside `roles.<role>.timeout` is a blocker to report, not work to leave running.
- **The report is the last action, unconditionally.** Work that succeeded but was never reported is not completion evidence. When the work cannot be finished, the assignee still publishes the report with `Status: blocked` and records what stopped it: a blocked report is a finished handoff, an absent one is a lost handoff.

Claude Code has two logically independent process-scoped hooks. `cartopian dispatch` exports the role/config boundary and the current Python interpreter. The shipped POSIX and PowerShell Claude wrappers use the installed `cli/claude_launch_settings.py` helper to resolve capability activation from the canonical project/global/local configuration. When any role declares grants, the helper adds `cli/claude_hook.py` as a **PreToolUse** refusal adapter for the structured read and mutation tools; no role declaration means it adds no capability hook. Effective access still resolves from grants only, never from role or wrapper names. Malformed, unknown, missing, and explicitly empty grant sets retain the fail-closed semantics in `CAPABILITIES.md`.

Independently, `cli/claude_stop_hook.py` is a **Stop** hook that refuses to end the turn while the report slot named by `CARTOPIAN_EXPECTED_REPORT_PATH` is absent or unparseable, feeding the assignee the instruction to finish in the foreground and publish. The helper adds it whenever that variable is present, whether capability gating is active or not. Both entries travel in one inline Claude `--settings` object but remain separate event entries. The wrappers never write a user, project, or local settings file and never override `--setting-sources`, so Claude continues to load all normal settings sources. `CARTOPIAN_CLAUDE_BARE=true` suppresses auto-discovery but cannot suppress these explicit process-scoped entries. A missing/invalid helper or required hook refuses launch instead of silently weakening a dispatched handoff.

The completion hook delegates the completeness question to the same canonical observer the wait primitives use, imposes no timer of its own, and bounds itself to `CARTOPIAN_STOP_GUARD_MAX_BLOCKS` interventions (default 3) so a session that genuinely cannot report is never pinned open. It fails open on every error path. Hosts with no comparable completion interception point rely on the prompt instruction alone. Completion enforcement grants and denies no capability and contributes no containment evidence.

Older projects may still contain Cartopian `claude_hook.py` PreToolUse or `claude_stop_hook.py` Stop entries written by an earlier installer. When an entry already targets the current launch interpreter and installed hook, the helper reuses it verbatim in the per-launch settings so Claude's cross-scope array de-duplication runs it once. A stale or incompatible registration refuses launch rather than executing twice or depending on an old interpreter. The explicitly requested `scripts/install.py --claude-hook <project-dir>` compatibility operation removes both obsolete Cartopian handlers while preserving unrelated settings and hooks; it creates no registration. Ordinary install, update, project reconciliation, and dispatch never mutate registered projects for this migration.

Stop refusal is completion intervention, not capability prevention or detection. The terminal classification in [Waiting For Completion](#waiting-for-completion) is unchanged and remains authoritative: a clean exit with no report is still classified `exited-without-report` whenever the guard is absent, disabled, exhausted, or bypassed. That classification says nothing about capability containment.

### Automated output safety

`cli/output_safety.py` is the runtime source of truth for automated-dispatch launch-log retention. Every configured wrapper runs through this agent-neutral standard-library supervisor on POSIX and native PowerShell/CMD launch paths. The supervisor continuously drains combined wrapper output so the child cannot block on a full pipe, but retains only the bounded `<report-path>.launch.log` diagnostic. Bytes outside the retained representation are discarded; retained-log growth never signals, terminates, fails, or otherwise constrains the assignee, its source files and deliverables, or its completion report.

The shipped retained-log defaults are **400 lines / 64 KiB**. Operators may set positive-integer `CARTOPIAN_LOG_LINE_LIMIT` and `CARTOPIAN_LOG_BYTE_LIMIT` overrides in the dispatch environment; malformed, zero, or negative values refuse before launch. Accounting for the retained representation uses raw bytes. Line count is the number of LF bytes plus one when the representation ends in a non-empty trailing fragment: CRLF counts once, a final LF adds no empty line, and multibyte or invalid UTF-8 bytes receive no special treatment. Truncation is marked explicitly, and the stored representation never exceeds either configured limit. Unsafe, unwritable, symlinked, hard-linked, or non-regular destinations degrade to unavailable retention and never redirect output into the PM-visible stream.

The outer supervisor preloads canonical report parsing before child creation and throttles report observation by elapsed time, independently of output chunk volume. Its pipe-readiness wait times out at the next report poll or grace deadline, so a wrapper that publishes a complete report and then holds stdout open silently cannot stall retained publication or reap. Once the report is complete, the supervisor atomically publishes the current retained representation before beginning the wrapper-compatible post-report grace/reap path, continues draining during that grace, and atomically replaces the log with the final bounded representation afterward. The grace reuses `CARTOPIAN_REPORT_POLL` and `CARTOPIAN_REPORT_GRACE_POLLS`; it reacts only to a complete report and never replaces or extends the role timeout.

Dispatch and status records expose only retained-log limits, path, retained size/line count, truncation state, report presence, and **`guarantee_scope=retained-launch-log`**. This is a storage-retention guarantee, not an execution-output, artifact-size, completion-report-size, pre-model interception, model-context, or provider-private-context guarantee. The wait commands consume report/status metadata plus the launch-log companion's safe file-shape metadata only; they never open, read, summarize, or use launch-log contents as progress evidence. `delete-report` removes both status and log companions on slot clear and close.

### Launch Directory

Assignee CLIs run with cwd set to the **cartopian project root** — the absolute path recorded for the selected project in the registry (FR-003). The shipped wrappers resolve and `cd` to that path automatically; the prompt path passed to the wrapper carries the project root in its prefix (`<project-root>/prompts/PROMPT-NN-NNN.md`) so derivation is unambiguous. `cartopian dispatch` sets `CARTOPIAN_LAUNCH_CWD` to the same project root. No "parent" or "shared workspace" directory is involved in the launch contract.

Wrappers translate env → CLI flags, set the cwd, run the agent **autonomously** (so the unattended handoff completes), enforce the `CARTOPIAN_TIMEOUT` deadline, and emit the status signal. The Claude wrapper additionally attaches its native process-scoped hook when the dispatch role/config boundary activates grants; authorization decisions still occur inside that hook, and the wrapper never infers them from a role name. The same wrapper may back any operator-defined role. Locations outside the project root that a task needs (declared as **work roots**, below) are referenced by absolute path/URI inside the prompt the PM authors.

**Work-root write grant.** The launched agent must be able to write to the union of the cartopian project root and the project's declared work roots. `cartopian dispatch` resolves the declared work roots fail-closed (an unmapped name or a mapped path missing on this machine refuses the launch) and exports the resolved absolute paths to the wrapper as the `CARTOPIAN_WORK_ROOTS` environment variable (`os.pathsep`-joined: `:` on POSIX, `;` on Windows; not exported when the project declares none, and a stale inherited value is cleared). A wrapper whose agent CLI imposes its own filesystem sandbox rooted at the launch cwd must **widen** that sandbox to cover these paths — the shipped codex wrapper adds them as `sandbox_workspace_write.writable_roots`, and the claude wrapper passes each as `--add-dir`. Widening a tool-imposed sandbox to match the launch contract is not scoping; wrappers still never *confine* the agent below what its own CLI does. Where a tool's sandbox exposes no per-path grant surface (gemini `--sandbox`, devin `--sandbox`), the wrapper warns on stderr that declared work roots may be unwritable inside that sandbox.

Capability-based grant decisions remain the **harness's** responsibility. For Claude, the wrapper is responsible only for loading that harness interception point at the dispatched boundary. If approval-in-the-loop behavior is wanted for a role, omit the applicable work type from `auto_launch` and use the manual path rather than the wrapper — the wrapper path is the unattended-automation path, where there is no human to answer a prompt.

**Note for custom wrapper authors.** The cartopian project root is not automatically a git repository. Tools that refuse to run outside a git repo must be told to skip that check (the shipped wrappers do so unconditionally). The autonomy/permission flags a wrapper passes live at the wrapper layer; capability gating lives in the harness.

### Work Roots

Work roots are the protocol mechanism that lets a cartopian project reference filesystem locations outside its own root — typically a sibling product repository or any external location the project's tasks need to read or write.

- The committed `<project-root>/cartopian.toml` declares a **name set** under `[project].work_roots`: an inline list of operator-chosen, platform-independent identifiers (e.g., `["product", "design"]`). The committed file carries no paths, keeping multi-operator and multi-machine use viable.
- The per-machine `<project-root>/cartopian.local.toml` carries the **name → absolute-path mapping** for the current operator's machine, under a `[work_roots]` table. It is gitignored by `cartopian scaffold-project` and never committed.
- `cartopian resolve-config <project>` merges the two files and validates that every declared name has an absolute path mapping. Path spelling follows one rule across machine records: project, task, spec, dependency, prompt, and report paths are filesystem-resolved absolute paths; machine-local work-root mappings preserve the operator-authored absolute spelling verbatim. This prevents one record from rewriting an authored `/tmp/...` mapping to `/private/tmp/...` while its lifecycle paths use the filesystem-resolved spelling. Skills and the PM consume each emitted path verbatim. Unmapped names exit non-zero with a `[work-root]` stderr line.
- Tasks reference work roots by **name** in the `Work root:` task-file field (see `templates/TASK.md`). The field is optional, comma-separated multi-valued, and rejects absolute paths, project-relative paths, and `<owner>/<repo>` slugs. Names absent from `[project].work_roots` cause `cartopian validate-task-readiness` to block the task.

Optional automation policy:

```toml
[automation]
initiation = "operator"
confirmation = "each-handoff"
max_handoffs_per_run = 1
```

Supported `initiation` values are:

- `operator`: execution begins only from an operator execution directive (see [Request Intent](#request-intent)). After informational requests and scoped directives the PM reports and stops.
- `auto`: the PM may initiate a run without a directive — at session startup once startup duty completes with no blockers, and when a scoped directive leaves the open queue ready. Informational requests remain read-only, and explicit "stop"/"pause" language still suspends initiation until the operator directs execution again.

Supported `confirmation` values are:

- `each-handoff`: stop after each handoff reaches a terminal result and that result is processed.
- `until-blocked`: continue through handoffs whose applicable role-local `auto_launch` permission is present until blocked, failed, rejected, missing evidence, requiring operator judgment, reaching a phase boundary, or hitting `max_handoffs_per_run`.

Defaults are `initiation = "operator"`, `confirmation = "each-handoff"`, and `max_handoffs_per_run = 1`. Values outside the closed domains fail configuration validation.

The automation authorities are disjoint, and each gates a different question:

- `initiation` gates **whether a run begins** when no execution directive was given.
- `confirmation` gates **pace** within an initiated run: under `each-handoff` the PM stops after the handoff reaches a terminal result and that result is processed, then resumes with the next sequential step when the operator says to continue; under `until-blocked` the initiated run remains active while a launched handoff is working and chains through sequential tasks within the run budget. Neither value authorizes initiation — `until-blocked` describes how far an initiated run chains, not whether one starts.
- **Selection** is never gated and never an operator question: task order is deterministic per [Task Execution Order](#task-execution-order). Within an initiated run, evidence-supported lifecycle moves (starting the next sequential task, moving a task per a parsed report or review verdict) are applied without a confirmation prompt; the operator is consulted only at the stop conditions named there.
- `roles.<role>.auto_launch` gates **launch mode** for each listed assigned work type; it participates in neither initiation nor pace.

Full unattended operation is therefore a stack of explicit opt-ins, each an operator choice and none a protocol default: `initiation = "auto"` (runs may begin without a directive), `confirmation = "until-blocked"` (runs chain), `max_handoffs_per_run` sized to the desired batch, and the applicable work types present in each launched role's `auto_launch` list.

`max_handoffs_per_run` is a launch budget. Only launches consume a handoff budget unit. Re-invoking a wait primitive, receiving a nonterminal observation, or automatically waking/resuming the host to continue observing the same launched assignee consumes no unit and cannot authorize or cause another launch.

Handoffs are sequential. Concurrent child agents are out of scope.

### Waiting For Completion

The PM detects handoff completion by observing the filesystem through two canonical read-only wait primitives, which replace all ad-hoc polling, hand-rolled timing loops, manual "tell me when it's done" prompts, and PM-side watchdog timers:

- `cartopian wait-handoff <task-path> --role <role> [--max-block <duration>]` — for task-scoped handoffs (task assignment, task review). It resolves the task's expected report path and honors the configured `roles.<role>.timeout` value as the absolute ceiling.
- `cartopian wait-report <report-path> [--role <role>] [--max-block <duration>]` — the lower-level primitive for a known report path, including planning-checkpoint reviews that have no task file. With `--role` it honors the same resolved role launch timeout; otherwise the protocol default applies.

The completion contract is:

- **The report file is the authoritative completion signal and publication boundary.** Path appearance alone is not terminal. A complete report that parses as the expected handoff variant routes its actual verdict (`accepted`, `blocked`, `failed`, `changes-requested`, or `rejected`). For a matching automated launch whose status is still `state=running` with retained storage pending, the canonical observer exposes that terminal verdict only after the outer supervisor has atomically published the bounded retained snapshot. The normal proof is `retained_log_ready=true`; because the snapshot is published first and the prior slot's log was removed before the running marker, a safe single-link regular `<report-path>.launch.log` also proves that boundary if the following status replacement is lost or raced. This brief live-launch barrier never changes the report verdict and never opens or reads the log body. Manual/report-only observation has no status requirement, and a matching `state=exited` status fails the retention barrier open: a dead supervisor cannot strand an already complete authoritative report. Incomplete or temporarily malformed bytes remain nonterminal while the current wrapper can still finish publication. After wrapper exit, stable malformed bytes classify `failed-to-parse`; a non-zero exit with no report classifies `failed`; a clean exit with no report classifies `exited-without-report`. A `timeout`, hard process stop, crash, or missing/late/permanently invalid report is not successful completion evidence.
- **Wrapper status is current, secondary evidence.** Automatic dispatch clears the launch's own expected report and `<report-path>.status` after all preflights (for task review that is the independent `REPORT-NN-NNN-review.md` slot — the preserved completion report is never cleared by a review launch), removes any prior launch log while establishing a safe destination, publishes a fresh `state=running` status carrying the launch identity and expected variant before child creation, and removes that marker if supervisor creation fails. When bounded retention is available, that marker also carries `guarantee_scope=retained-launch-log` and `retained_log_ready=false`. The wrapper preserves the pending marker on exit; after retained publication, the outer supervisor atomically sets `retained_log_ready=true` with retained-log facts and later publishes the final clean/error/timeout result as a fallback so a custom wrapper or wrapper-launch failure cannot strand `running`. If that status replacement is lost after the atomic snapshot publication, waits recognize the safe deterministic launch-log companion as publication metadata without opening its body. If the wrapper reaches `state=exited` before publication, waits accept a complete report without requiring the pending marker to flip. Retained-log truncation is nonterminal metadata and never changes that lifecycle result. Manual launches may omit running identity; absence remains valid and waits use report-only observation. A stale or variant-mismatched status cannot terminate or delay a new handoff. The `.status` file remains transient and is removed through `cartopian delete-report`. Both wait commands are read-only — they never write project state, move tasks, launch processes, or read `.launch.log` bodies.
- **Coder completion evidence and reviewer completion are separate artifacts.** The accepted coder report stays preserved at `reports/REPORT-NN-NNN.md` throughout task-closure review, and the reviewer publishes independently to `reports/REPORT-NN-NNN-review.md`. The review-prompt writer binds the preserved completion report by absolute path, outcome facts, and SHA-256 content identity inside the generated review context — the reviewer reads the artifact directly and the prompt never reproduces the report body. That binding also covers exact operator evidence, PM-derived artifact paths, task, prompt, review target, and the expected review-report path. Review preflight re-verifies the preserved artifact against the bound identity: a missing completion report blocks the review launch, and a mutated one is a stale binding. Task review expects the `review` report variant at the review path, so completion-shaped content in the review slot — or a review report in the completion slot — is a path/variant mismatch and cannot satisfy the other signal. Review retries clear only the review slot's transient state; the completion artifact stays byte-identical.
- **Waiting is terminal by default: one launch, one wait call, one result.** Called without `--max-block`, a wait primitive blocks until a terminal observation, bounded by the resolved handoff timeout as the absolute ceiling. This is the only supported shape. Cartopian has no wake, resume, or callback mechanism, and no host is assumed to supply one — a blocking call that survives to the report is the entire completion mechanism. **The silence while that call is outstanding is correct.** No model turn is in progress during a pending tool call, so a host instruction requiring periodic commentary during ongoing work does not govern it: such instructions govern turns the model holds, and a pending call holds none. A PM that slices a wait into short `--max-block` observations to create opportunities to speak has converted a correct silence into per-slice context cost and nonterminal records that decide nothing. Slice only for a host ceiling that cannot be raised, never for narration.
- **The host's tools/call ceiling is a hard constraint, and it is checked before launch.** Every MCP host may cap a single `tools/call` by fixed wall clock, by silence between messages, or both. The resolver distinguishes direct CLI execution, a recognized connected host, and an unrecognized connected host even when `clientInfo` is absent; unknown connected capability fails the gate. It reports raw wall/idle facts, evidence, progress-reset behavior, and the sustainable effective budget. Documented progress may maintain a resettable idle channel; it never extends a fixed wall-clock ceiling. `cartopian dispatch` refuses to launch, before child creation, when `roles.<role>.timeout` exceeds that sustainable budget or connected capability is unknown, naming measured facts and bounded remedies. `cartopian host-capability` exposes the same read-only record, and both wait primitives echo it as `host_wait_budget`. The remedies are to raise the host ceiling, lower the role timeout, or dispatch manually and monitor the report path; recurring operator wakeups are not a completion mechanism. Host-specific keys and defaults live in `skills/register-mcp.md`.
- **A blocking wait occupies the session for its duration.** The MCP server processes one message at a time, so no other Cartopian tool is serviced while a wait is blocked. This is a property of the waiting model, not a fault to route around: dispatch one child handoff at a time and let the wait run to its terminal result.
- **`still-running` is a nonterminal internal observation boundary, reachable only when `--max-block` was explicitly supplied.** `--max-block` bounds a single observation slice and exists for one purpose: to fit a wait inside a host ceiling that cannot be raised. When that explicitly requested budget elapses before the configured timeout, the assignee may still be working. It is not a blocker, completion result, handoff-budget event, or operator-confirmation boundary. Routine `still-running` / `still_running` slices are silent and context-neutral: the PM keeps the same initiated run active and re-invokes the same canonical wait primitive in bounded slices without user-facing text or repeated state when no material state changed. User-facing output is allowed only for a terminal result, blocker, timeout/failure, meaningful new progress evidence, or a deliberately throttled long-running threshold. A re-wait is read-only and does not launch or dispatch an assignee; the single launch remains the active handoff. Under `until-blocked`, the run therefore remains active across every nonterminal observation. Under `each-handoff`, control returns only after the handoff reaches a terminal result and that result is processed, never between observation slices. Slicing a wait to fit a host ceiling costs context on every slice, so prefer raising the ceiling; where the host cannot be raised and slices are unacceptable, declare manual monitoring instead of pretending the wait is automatic.

The wrapper enforces the wall-clock deadline at the OS level (see the `timeout` field above); the wait commands observe the result rather than imposing a separate PM-side deadline.

## Dependencies

- `Depends on`: tasks whose output this task reads or builds on. Informational; does not block start.
- `Blocked by`: tasks that must be in `done/` before this task can start.

Both fields carry `TASK-NN-NNN` identifiers only.

## Evidence Gate Discipline

Every task declares `Evidence gate: required` or `Evidence gate: n/a`.

`required` tasks name concrete acceptance evidence. Software work often uses a test that fails before implementation; other work may use a fixture run, validation script, fact-check pass, approval checklist, inspection record, rehearsal, or another verifiable before-and-after check.

`n/a` is only for non-executable work and must say why.

When task-closure review is required, reviews of `required` tasks record the before-and-after evidence. When review is off, the completion report records it directly.

Source evidence is one domain-neutral evidence shape under this same discipline. It may be a fact-check, policy effective-date check, operating-authority check, campaign claim substantiation, software version/standard check, or another source-application observation; it is not limited to research or software work.

## Plan Lifecycle

A Cartopian project has one active implementation plan at a time. The live `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, `phases/`, `tasks/`, `specs/`, `reviews/`, `decisions/`, `prompts/`, and `reports/` describe the current plan only.

When a plan completes, close it before starting a new plan. The canonical closeout workflow is `skills/close-plan.md`.

Plan closeout requires:

- No task files in `tasks/open/`, `tasks/in-progress/`, or `tasks/in-review/`.
- No active or ambiguous prompts.
- No unresolved or ambiguous reports.
- Phase exit criteria satisfied by completed tasks, decisions, specs, or documented operator acceptance.
- Explicit operator confirmation.

Plan closeout resets the live plan surface:

- `REQUIREMENTS.md`
- `IMPLEMENTATION_PLAN.md`
- `decisions/`
- `phases/`
- `tasks/`
- `specs/`
- `reviews/`
- `prompts/`
- `reports/`

`REQUIREMENTS.md` and `IMPLEMENTATION_PLAN.md` never carry forward as live artifacts. A new planning cycle produces fresh requirements and a fresh implementation plan.

`STANDARDS.md` — project metadata: the chosen tools or stack, working standards, and cycle constraints — may carry forward only when the operator explicitly chooses to keep it as seed context for the next plan. Otherwise, it resets to a seed file. Protocol conventions are tool-owned and read through `cartopian://protocol/CONVENTIONS`; projects do not carry a local `CONVENTIONS.md`.

`resources/` is not part of the live plan surface: `reset-plan` never clears it, and its contents carry forward across plans by default. Closeout puts its disposition to the operator explicitly — carry forward, and/or snapshot into the plan archive (see Project Resources).

`cartopian.toml` remains live across plans.

## Plan Archives

Cartopian is anti-archival by default. Completed plan artifacts are archived only when the operator explicitly asks during closeout.

Plan archives use `archive/PLAN-NNN/` and may include snapshots of:

- `REQUIREMENTS.md`
- `STANDARDS.md`
- `IMPLEMENTATION_PLAN.md`
- `STATE.md`
- `decisions/`
- `phases/`
- `tasks/`
- `specs/`
- `reviews/`
- `reports/`
- `resources/`
- `CLOSEOUT.md`

Prompts are not archived. Archiving copies `resources/` — it never removes the live directory; whether the live `resources/` carries forward untouched is the operator's closeout decision (see Project Resources).

Archival is a PM lifecycle action. When the operator requests a snapshot, the PM runs the bounded `cartopian archive-plan` command before reset; the PM does not delegate raw archive creation or copying to the operator. The command owns archive numbering, copies only the fixed plan-artifact allowlist, writes `CLOSEOUT.md`, and updates `archive/INDEX.md`.

`archive/INDEX.md` is a one-line-per-archive summary table. It is created with the first archive and updated on each subsequent closeout that produces an archive.

After closeout, `STATE.md` says there is no active plan and names `skills/plan-project.md` as the next action.

## Decisions

Every non-trivial decision gets its own immutable file in `decisions/`, named `DEC-NNN.md`; its title lives in the file and decision index.

`decisions/INDEX.md` is a one-line-per-decision summary table.

A decision that changes a prior decision creates a new file with `Supersedes: DEC-NNN`. The superseded decision file remains unchanged.

## Backlog

`BACKLOG.md` at the project root is the durable home for PM/reviewer follow-up notes — actionable tech debt, process debt, and protocol-hardening items that are not yet promoted into a task or roadmap entry. Follow-up notes belong here, never in `STATE.md`, which stays canonical composed state under its 5KB ceiling; the mediated `write-state` enforces this by composing the body itself (see Session State). Protocol-compliance feedback (e.g. the operator points out a protocol or config rule the PM missed) is process debt and lands here the moment it arises — not in a `STATE.md` situation note.

Entries are written through `cartopian write-backlog` (one section per `BL-NNN` id) and removed through `cartopian delete-backlog <project-root> --bl-id BL-NNN` (which removes only that entry's section; the preamble and every other entry round-trip byte-for-byte). Both paths are mediated writes — hand-edits to `BACKLOG.md` remain out of band, the same as any other mediated artifact. The file survives plan closeout and is input to the next planning cycle.

### Ids are writer-allocated and never reused

`BACKLOG.md` carries a visible preamble field, `Highest id issued: BL-NNN`, owned exclusively by the mediated writers. New-entry ids are **allocated by the writer, never supplied by the caller**: omitting `--bl-id` mints the next id (mark + 1), bumps the field, and reports the allocated id in the command's NDJSON record. Supplying `--bl-id` is legal only to revise an entry that is currently live. Because the mark only ever ascends and `delete-backlog` never touches it, a deleted id is never reissued — so a stray reference a cleanup sweep missed can never collide with a freshly minted entry. The counter lives in the file itself (not a machine-local counter, a sidecar file, or git history) so it travels with the project and cannot split-brain from the entries it governs. On every mediated write the writer reconciles the field: a value **below** the highest live id can only come from a raw hand-edit and is refused fail-closed; an **absent** field (a legacy file predating this rule) is the one permitted self-heal, initialized to the highest live id on the next write. `plan-audit` asserts `mark ≥ max live id` as a portable detection floor.

### Promotion is a recorded move

When a backlog item is promoted into a task, spec, or phase, the durable artifact records where it came from with a `Source: BL-NNN` header line, and the backlog entry is deleted outright. Reference points from the durable artifact back to the ephemeral entry, never the reverse — the file that outlives the reference is the one that holds it, so nothing can dangle. This is enforced by an **interlocking pair of guards with the delete as the choke point**, not by a composite verb (sugar cannot hold the invariant while the primitive commands stay callable):

- **Stamping is an argument, not body text.** `cartopian write-task` / `write-spec` / `write-phase` take `--source BL-NNN`; the writer validates the grammar, verifies the entry is live in `BACKLOG.md` at stamp time, and renders the `Source:` line itself. A `Source:` line hand-typed into a content body is decoration the guard never saw. This is what separates it from a plain `Plan ref:` — the reference is created by a command that checked the referent existed.
- **`delete-backlog` refuses undocumented deletion.** Before removing a live entry it scans the governed durable surfaces (`tasks/` in all four status dirs, `specs/`, `phases/`, `IMPLEMENTATION_PLAN.md`, `decisions/`) for a matching `Source: BL-NNN` stamp and refuses without one.

Neither guard alone suffices — stamping without the delete guard still lets an unstamped entry be deleted (the dangle); the delete guard without mediated stamping is satisfied by a hand-typed line pointing at nothing. Together you can only stamp what exists and only delete what has been stamped. The ordering is **stamp-then-delete**: the filesystem offers no transaction, so promotion is not atomic — but stamp-first leaves a benign, mechanically recoverable duplicate (the entry is still live and already referenced; `plan-audit` flags it as an unfinished promotion), whereas delete-first would lose information irreversibly. The delete guard makes the safe ordering the only one that executes. The one legitimate exception — an entry **abandoned** rather than promoted — is an explicit `--discard` flag: loud, recorded in the NDJSON, never the default, mirroring the evidence gate's `required` vs `n/a` grammar where an exception is legal only when it is stated.

The general principle this settles: **every cross-artifact reference field is verified by a guard at the lifecycle transition that consumes it** — `validate-task-readiness` already checks `Plan ref:` at task start, and `move-task` checks the review `Verdict:` before a task reaches `done`. A reference that no transition ever verifies is exactly the kind this rule exists to forbid.

## Sizing

- `STATE.md` has a hard ceiling of 5KB; its `## Situation` section is capped at 5 notes, one line of ≤ 200 chars each, ≤ 1KB rendered (see Session State).
- Task files are assignment-sized, not running journals.
- Open task files should usually stay under 2KB.
- Completed tasks may be larger when they need closure evidence.
- Phase files are roll-ups of plan refs, task coverage, dependencies, and exit criteria.
- Specs have no fixed ceiling, but prefer specificity over comprehensiveness.

## Git

When git versioning is used, each cartopian project root is its own git repository, tracking that project's PM data (phases, tasks, specs, reviews, decisions, prompts, reports, `STATE.md`, and `cartopian.toml`) in a single history. Projects live anywhere on disk per FR-003, so git scope is per-project and never assumes a shared parent directory.

The protocol default for `[defaults] git_versioning` is **`false`**. Source attribution: the explicit `git_versioning = false` value in the global `~/.cartopian/cartopian.toml` shipped as the `templates/global.cartopian.toml` seed — projects opt in by setting `git_versioning = true` in their own `cartopian.toml`.

Optional `[git]` configuration resolves along the FR-011 resolution chain (project-level `cartopian.toml` → global `~/.cartopian/cartopian.toml` → these protocol defaults):

```toml
[git]
pm_owns_product_branches = false
default_branch_pattern = "task/{task_id}-{slug}"
default_merge_strategy = "merge"
```

`pm_owns_product_branches = false` is the legacy path. A project with no `[git]` section behaves exactly as before.

`default_branch_pattern` is used only when `pm_owns_product_branches = true`. It supports `{task_id}` and `{slug}`. `{task_id}` is the numeric task identifier without the `TASK-` prefix (`NN-NNN`), and `{slug}` is the task filename slug. For `TASK-02-001-page-templates.md`, the protocol default produces `task/02-001-page-templates`.

`default_merge_strategy` controls the PM merge command for opt-in product repos. Supported values are `merge`, `squash`, and `rebase`, mapping to `gh pr merge --merge`, `gh pr merge --squash`, and `gh pr merge --rebase`.

When `git_versioning = true` in the effective `cartopian.toml`:

- Session closeout includes auto-commit and auto-push by the PM.
- Commit messages describe the unit-of-work grain.
- Product-repo commits preserve red-then-green evidence-gate discipline.

When `git_versioning = false`:

- The filesystem is the only protocol record.
- `STATE.md` remains the current cross-session handoff.
- Product-repository branches are not PM-owned. In a verification-only task, an uncommitted work root containing deliverables from prior completed tasks is an expected steady state, not evidence that the verification handoff modified files. Assignment and review prompts for such tasks state this operating model explicitly, and reviewers distinguish pre-existing work-root state from changes attributable to the current handoff instead of treating `git status` alone as a defect.

Git staging, commits, and pushes for the protocol repository itself are human-owned.

### PM-Owned Product-Repo Branches

When `git.pm_owns_product_branches = true`, the PM owns product-repo git plumbing for tasks whose `Work root:` field names a work root that resolves to a product repository: staging, commits, branches, pushes, PRs, merges, and branch cleanup. The setting does not apply to tasks whose `Work root:` is `n/a` or omitted, and it never applies to the Cartopian protocol repository itself. Protocol-repo git staging, commits, pushes, and branch management remain human-owned regardless of any project setting.

On an accepted task completion report with `Ready to close: yes` (or the legacy `Ready for review: yes`), the assignee is responsible for completed worktree changes and completion evidence only. The assignee does not stage, commit, push, create a branch, or open a PR. The PM resolves the product repo, creates or updates the configured product-repo branch, stages and commits the task changes, captures the resulting implementation commit SHA, pushes with `git push -u origin <branch>`, and opens a pull request with `gh pr create`. The commit message, PR title, and PR body reference the task ID and completion report. With task-closure review required, merge follows approval; with review off, the PM merges after accepted completion evidence and then closes the task.

The protocol defaults are:

- Branch pattern: `task/{task_id}-{slug}`.
- Merge strategy: `merge`.
- Branch cleanup: delete the product branch on merge.

The PM resolves a deploy preview URL when one exists, such as from a deployment-bot PR comment. If no preview URL exists, the PM proceeds with the PR URL only and records the gap in `STATE.md`.

On reviewer `approve`, the PM merges the PR with `gh pr merge --<strategy> --delete-branch`, using the effective `git.default_merge_strategy`. On `request-changes` or `reject`, the PM moves the task per the verdict and leaves the branch and PR open for the next coder pass.

Review-evidence authorship follows the event boundary. Reviewers fill the pre-merge review fields: `Commit SHA`, findings, and verdict. For `Merge commit SHA`, reviewers write `pending` when PM-owned product-repo git is enabled, or `n/a` when it is not. After an approved PR is merged, the PM appends `Merge commit SHA` to the review file's existing `Implementation evidence` block and appends `PR URL` if the review file does not already contain it. Review reports remain assignee-to-PM evidence handoffs and are not PM-edited.

## Session State

After project selection, every PM session starts from that project's `STATE.md` and ends with `STATE.md` refreshed. The file remains short, current, and under 5KB.

### The body is composed, not authored

While a project has plan artifacts, the canonical `STATE.md` body — Current phase, Active work, Open work, What to do next — is derived entirely from the filesystem, so the PM never authors it. `cartopian write-state <project-root>` composes and persists the body in one step; it refuses `--content`/`--content-file` while plan artifacts exist. The PM decides *when* state is refreshed; the CLI renders *what* it says. This removes the round-trip of derivable text through the PM's context and closes `STATE.md` as a free-form note surface.

The one exception is the no-plan project (post-closeout, pre-plan): there is nothing to compose from, so the closeout body (closeout date, archive note, carry-forward choices, next-action pointer) is PM-authored via `--content`/`--content-file` — and only there.

### Situation notes

The single PM-authored input on a planned project is the `## Situation` section, supplied as `write-state --note` lines (bounded: max 5 notes, one line of ≤ 200 chars each, section ≤ 1KB). A note qualifies only if all three hold: it is about the current state of *this project*; it is **not derivable** from the filesystem, config, or protocol; and it **changes what the next session does**. Example: "coder deploy failed mid-handoff; operator is restarting the development machine." Protocol rules, config values, and task placement never qualify — they are already recorded. Protocol-compliance feedback and follow-up items route to `BACKLOG.md` as process debt at the moment they arise, never into a note.

Notes have a **one-delivery TTL** — a note exists to survive exactly one gap between sessions, then must be consumed:

- Every `write-state` starts from zero notes; nothing carries forward by inertia.
- A `--note` byte-identical to one already in `STATE.md` is refused fail-closed (`note-carry-forward`). A fact that outlives its delivery is promoted (`write-backlog`, `write-decision`), dropped, or — for a genuinely still-live transient — consciously restated, never repasted.
- `plan-audit` and the `next-action` session brief emit a **blocker** per note present: undelivered mail must be resolved before lifecycle movement. Resolving it (acting, promoting, dropping, then refreshing `STATE.md`) is PM work and does not itself require operator input. A healthy steady-state `STATE.md` has zero notes.

Session closeout leaves task directories, prompts, reports, decisions, and git state consistent with the lifecycle evidence processed during the session.

The final operator-facing message names the exact next protocol action.
