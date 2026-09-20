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

`protocol/RISK_AND_PRACTICE.md` explains the risk, judgment, and practice-pack extension contracts and the active source-guidance extension; `protocol/risk-and-practice-contract.json` is the single authority for their machine values. Risk classification is active through the task, prompt, report, CLI/MCP, and handoff projections described below. Practice-pack selection is active through the task envelope, the assignment-prompt projection, and the CLI/MCP surface: a task declaring an envelope resolves exactly one optional pack or none, and a selected outcome contributes exactly one body while the unmatched bodies contribute zero bytes. Selection never changes the risk band, activates a judgment card, alters review policy, or requires review. Judgment guidance is active through the task's judgment envelope, the run-task procedure, the assignment-prompt projection, and the CLI/MCP surface: a task declaring the two envelope facts activates a card only where the work crosses that card's lifecycle boundary and names that card's non-enforceable failure as still open, an active card is a hold at that boundary until the named decision authority or observable proof it reports is satisfied, and an active outcome contributes exactly one central guidance body while every unactivated boundary contributes zero bytes. Activation never changes the risk band, selects a practice pack, alters review policy, or requires review; the band and the pack outcome are rejected as activation inputs rather than quietly ignored. Source guidance remains active through the existing task, spec, readiness, handoff, and evidence surfaces and does not activate a judgment card. This file remains the invariant layer, and it continues to own review policy, evidence-gate discipline, and every other lifecycle rule. Risk classification never rewrites review policy, roles, capability grants, or launch configuration.

Skill invocation names are derived from skill filenames by dropping `.md` and replacing hyphens with spaces. For example, `run-task.md` maps to `run task`.

`use cartopian` starts Cartopian project management. Resolve the operator's instruction through the named skill and tools, loading only the relevant definitions. Do not load complete tool, resource, or skill catalogs to map vocabulary. The shared `read_context` MCP tool reads a named `cartopian://...` resource through the same reader as `resources/read`; both surfaces retain complete content and the same path validation. Hosts that must enumerate resources should filter to the named entries before returning the listing to the PM when local filtering is supported.

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
2. If exactly one project is registered and the operator did not name it, name it and ask whether to open it or start a new project; select only on explicit confirmation.
3. If more than one project is registered and none was selected, ask the operator which project to use. Do not read or mutate project-specific lifecycle artifacts until the project is selected.
4. If no projects are registered, start with `skills/init-project.md`, which scaffolds a new project at an operator-supplied path and registers it via `cartopian register-project`.

Selection ends by binding the session's host-captured request evidence to the project: `cartopian select-project <project-root> --handle <handle>` (MCP `select_project`), where the handle is the opaque `cartopian-session:` routing reference the host intake adapter injected on the session's first prompt. The command resolves the handle against adapter state and takes host and session identity from that record; no argument supplies them. It promotes the session's preselection buffer to the project's captured set, records the binding in the session and in `requests/bindings.json` (written only by this command), closes the previous project's range on a switch, and refuses an unknown or stale handle with `session-unbound` naming the one missing item. A session with no routing line has no capture; the PM reports that and never substitutes records of its own.

After project selection, the PM reads the selected project's `cartopian.toml` and the global `~/.cartopian/cartopian.toml` along the FR-011 resolution chain and resolves the effective PM role. If the agent is the PM for the selected project, session startup duty is:

1. Read `STATE.md` before taking lifecycle action.
2. Reconcile `STATE.md` against the filesystem when it names task state that disagrees with task directories.
3. Tell the operator the current phase, active work, and next protocol action from `STATE.md`.
4. Act on the operator's request per its intent class (see [Request Intent](#request-intent)). Execution begins only when that classification — or the resolved `[automation] initiation` policy — authorizes it.

**One authoritative startup result.** `cartopian next-action <project-root>` is the single startup read. Its `startup` record carries exactly one verdict, and the PM relays that verdict rather than re-deriving it from artifacts:

- `planning-incomplete` — the current phase is not fully planned; `action` names the exact remaining step (the checkpoint awaiting a verdict or report, or the phase whose tasks and specs must be generated). "Planning approved" and "task 1 can be dispatched" are different states, and this verdict is what separates them.
- `ready` — `task` names the exact next task (or the active task to continue) and `action` names the dispatch action. A ready verdict for an open task is proven, not assumed: the readiness checks passed and the assignment path was rehearsed read-only (role resolution, prompt composition, launch prerequisites — `cartopian validate-task-readiness --rehearse-dispatch`).
- `blocked` — `detail` names the concrete failure, `owner` names the responsible party (`pm`, `operator`, `assignee`, or `host`), and `action` names the recovery.
- `plan-complete` — nothing remains but closeout.

Startup uses `next-action --compact --audit`: the same orientation and complete `plan-audit` evaluation in one call. An audit failure makes the combined command fail and prevents a ready verdict. Compact output retains request policy, PM effective grants, state disagreement, every blocker, and provenance guards. Nonblocking audit findings are grouped by kind; other roles' configuration is deferred. Returned `details` command/arguments retrieve the full records without these flags. Read full role records before assigning new work, and full audit findings when diagnosing or remediating them. Compact projections never skip checks, relax gates, or authorize mutations. The original detailed commands and resource URIs remain supported.

`--reconcile` refreshes a stale composed `STATE.md` body through the mediated writer before the verdict is computed, preserving undelivered Situation notes; the filesystem is authoritative either way. Because it writes, it is used only when the request's intent class authorizes a write (an execution or scoped directive); an informational request runs `next-action` without it and reports the disagreement instead (see [Request Intent](#request-intent)). The `planning` record beside the verdict names the planning stage, the checkpoint in flight, and the status of every checkpoint that has left a trace on disk. A legacy tasks checkpoint that declares no `Plan ref:` covers only the earliest task-bearing phase; tasks generated later for another phase need a checkpoint whose `Plan ref:` covers them.

## Request Intent

Operator requests fall into four classes. Classifying intent is the PM's first interpretive duty, and a request never changes class because automation is configured aggressively.

- **Execution directives** — "continue", "resume", "start working", "run the next task", "keep going", "pick up where we left off". These initiate (or resume) linear execution: the PM continues the active task — or starts the next sequential task when none is active — via `skills/run-task.md` without asking the operator to choose or approve the selection. Pace is governed by the `[automation]` policy; selection is never an operator question. The PM still stops for blockers, for decisions the protocol reserves to the operator, and at the plan-level forks named in `skills/start-session.md` (no plan exists, plan complete).
- **Informational requests** — "what's next?", "check `STATE.md`", "give me status", "where are we?". These are read-only: answer from `STATE.md` and the `next-action` record, name the next protocol action, and stop. An informational request never initiates execution — even under `[automation] initiation = "auto"` — because a question must not acquire side effects.
- **Scoped directives** — "generate PHASE-04's tasks", "write the spec", "revise the plan". These authorize exactly the named operation. When it completes: under `initiation = "operator"` (the protocol default), the PM reports completion, names the next protocol action, and stops; under `initiation = "auto"`, the newly ready open queue may initiate a run (see [Task Execution Order](#task-execution-order)).
- **Session-boundary requests** — "let's continue in a new session", "pick this up in a new chat", "that's enough for today". The request names a *session* boundary, so "continue" says *where* work resumes, not *start work*; a bare "continue" naming no boundary stays an execution directive. The one authorized operation is session closeout: refresh `STATE.md` (`skills/run-task.md` § Stage 8) and end the run, as an explicit stop does. Persisted project state is the whole continuity mechanism, so closeout produces no carry-forward artifact of any kind — no new task, no plan edit, no handoff document — and the next session resumes through registry selection and the `next-action` startup verdict.

Authorization is literal. A directive of any class does not implicitly authorize creating a task, plan item, decision, prompt, request capture, review, handoff, or other governance artifact merely to make the request fit a preferred workflow. If the named operation cannot be completed without a materially different mutation or scope expansion, the PM stops and asks before writing it. The PM also does not transfer authorized Cartopian file manipulation to the operator when a mediated writer can perform it.

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

The record is the PM's private completeness checklist, not the shape of the
conversation. The PM never presents the six fields as a form, never tells the
operator what a plan requires, and never enumerates the fields while gaps
remain. The operator sees the record exactly once: as the compact summary
offered for confirmation after every field is present.

Each field has one resolution state: `present`, `missing`, or `conflicting`.
A fact is `present` only when the operator stated it in this or a prior
exchange or an approved artifact records it. The PM reuses such facts and
never asks the operator to repeat one. Equivalent phrasing does not create a
conflict, and an existing confirmed fact is not discarded merely because later
input phrases it differently. Multiple beneficiaries are `present` when their
priority is explicit. An unobservable success signal is unresolved. An
exclusion that contradicts requested scope is `conflicting`.

The PM never supplies an operator-owned fact itself. It does not derive a
value from the project name, directory or file names, domain conventions, or
its own expectations of what such a project usually wants, and it does not
fill a `missing` or `conflicting` field with a working assumption, default,
placeholder, or provisional reading offered for the operator to accept. A
gap is closed only by an operator answer. When operator words are ambiguous,
the PM quotes them and asks what was meant rather than choosing a reading.
When two supplied facts conflict, the PM shows both and asks which governs.

Gaps are resolved through an interview, one question per turn. Each question
targets the single unresolved item whose answer most changes the plan, is
grounded in what the operator has already said, and may carry concrete
examples or options so it is easy to answer; examples are never recorded as
the answer. A question may be preceded by one brief insight — a gap, risk,
contradiction, or alternative the operator has not raised — when that insight
changes what the operator should decide. Between questions the PM does not
restate confirmed facts, summarize the conversation, or list what remains.
When a supplied fact is vague, the next question sharpens it. When the
operator ends the interview early, unresolved detail becomes recorded open
questions, but the six facts still gate the lock.

Requirements and implementation planning must not lock until all six fields
are `present` and the operator has confirmed the complete record. That
confirmation is one exchange over the whole record; the operator may correct
any field, and the corrected record is the confirmed one. It does not
require repeated cross-model confirmation. That single exchange is also the
project's request evidence: the host intake adapter captures the summary and
the reply in the operator's session, and the requirements/plan writer binds
the pair at lock (§ Up-front Operator Request Evidence). No further
confirmation is asked for evidence purposes. The six-fact record itself stays
PM-derived guidance; what review receives is the captured exchange, never the
PM's record of it.

The contract has no numerical confidence field. The PM never requests a
confidence percentage, model agreement score, or repeated cross-model
confirmation. Uncertainty is represented only by the resolution states and
the open question that resolves them.

The compact record stores only the six normalized facts and their resolution
states. It carries no PM assumptions, secrets, unnecessary conversation
transcript, or unrelated future-phase detail; normal containment and
deidentification rules continue to apply.

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
- Project summary: `CONTINUITY.md` at the project root. Optional plain Markdown summary a plan close may leave behind, read only on an explicit operator request (see Project Summary).

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

`in-progress → done` is disallowed when task-closure review is required, and `in-progress → in-review` is disallowed when it is off. A task already stranded in `in-review/` after policy is changed to off may move out without a verdict guard. `open → done` is an administrative exception only and requires `--administrative --reason`; ordinary execution never uses it. Administrative recovery also requires an explicit operator directive and a non-empty reason: `in-progress → open` is allowed only with no task prompt, review, completion/review report, status, or launch-log evidence; `done → in-review` requires configured task-closure review, a preserved complete completion report and review artifact, and no handoff status markers. Recovery preserves evidence and task identity, does not certify completion, and requires regenerating the review prompt against the restored path before dispatch. Ordinary terminal transitions remain refused.

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

The machine vocabulary and field labels live once under `source_guidance` in `protocol/risk-and-practice-contract.json`. `cli/source_guidance.py` reads that authority and projects one deterministic record through `task-bundle`, `validate-task-readiness`, `handoff-packet`, `dispatch`, `parse-report`, `report-action`, `report-skeleton`, `validate-report`, and `correct-report`. Because every CLI command is exposed through the shared MCP registry, the tool surface returns the identical record and diagnostics rather than reimplementing them.

A source record contains:

- at least one authoritative source identity;
- the effective date, publication date, edition, revision, or version that makes each source applicable, plus `current | stale | unknown` status and its governed scope;
- exactly one conflict disposition: `none | resolved | unresolved`, with a precedence rule or named decision authority and the applied decision when resolved; and
- either `none` or every claim that remains unverified, each naming whether it is decisive, the missing authority or evidence, the consequence of proceeding, and the next decision or proof required.

The rule is dominance, not averaging. Missing decisive authority, missing or stale applicable context, an unresolved conflict, or a decisive unverified claim fails readiness and blocks handoff. Favorable observations from other sources cannot offset that condition. The failure record names the exact claim or source condition, why proceeding matters, and the next authority or proof required. It never emits a numeric score.

Non-decisive unverified claims may remain only when the full failure signal is explicit. They are not converted into verified claims and do not silently grant authority. A complete task report for source-backed work carries `## Source evidence` in the same shape and names the non-empty subset of governing sources and applicable contexts actually used. It does not repeat sources that were not applied, and it may not introduce a source or context absent from the governing guidance. A complete report cannot close with a decisive unverified claim. `report-action` fails such a purported completion report closed as `failed-to-parse`; a `blocked` report may still truthfully report that the required authority or proof could not be obtained.

Source identities and scopes carried into coder prompts remain subject to normal deidentification. There is exactly one canonical assignee-facing projection of a resolved source record: every field of every source, conflict, and claim is deidentified at field granularity, and both the rendered `deidentified_guidance` and report-evidence validation consume that same structure. An identifier appearing in any field — Applicable context and Scope, not only Identity — therefore projects identically in the prompt and in the validator, and a faithful transcription of the supplied guidance always validates. `handoff-packet.source_guidance.deidentified_guidance` is that assignee-facing rendering; the PM does not paste raw task or spec identifiers into a prompt. Containment is unchanged: delegated spec guidance must resolve inside the selected project's `specs/` directory, and source guidance grants no filesystem, lifecycle, request-intent, publication, or operator authority of its own.

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

Critical work uses `cartopian adversarial-review-context` when its independent challenge is authorized. The command validates the supplied result against the current registry, reads a delivered artifact file and governing-contract file afresh from the project or configured work roots, complete and whatever their size, and returns their content identities. An operator may impose an explicit combined byte ceiling with `--max-context-bytes`; no default ceiling exists. The context payload contains exactly the artifact and governing contract; it has no author-conclusion input and admits no unrelated history. Required evidence and derived expectations remain top-level contract metadata. An unreadable, stale, or out-of-root input — or one exceeding an operator-supplied ceiling — fails closed before any partial context is emitted. Independence means the challenger did not produce the decisive work; it does not prescribe a fixed panel, role name, model, or reviewer count.

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

A spec is surfaced to an assignee **deidentified**, never as the raw file. The canonical spec keeps its full traceability (the `SPEC-NN-NNN` title, singular `Plan ref:`, and the `## References` section) for the PM; `cartopian render-spec <spec-path>` produces the assignee-facing rendering, which strips that scaffolding and any inline identifier while preserving the work-contract prose. For coder handoffs, `render-spec --projection assignment` (and the assignment-prompt composer) additionally drops author and reviewer metadata, planning status, review checklists, open-question sections, and the source-guidance record — the implementation contract only: observable goals, requirements, interfaces and data contracts, edge cases and failure behavior, and acceptance. That projection lands in the composed prompt's `## Implementation contract` section, so PM identifiers and planning material stay inside PM artifacts and never reach product code via the spec the coder reads. A spec with unresolved open questions is not a settled contract and refuses composition.

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

A review file carries a two-line `## Summary` and one self-contained `F<n>.` row per finding (`templates/REVIEW.md`). Those rows are what `cartopian report-action` projects to the PM as the bounded `review_projection`, so the PM applies a verdict without re-reading the whole review; the unbounded review body stays on disk as the durable evidence, and the PM opens it only when a projected finding requires the surrounding detail.

Planning-checkpoint reviews use `reviews/REVIEW-PLAN-NNN.md`. They follow the canonical field schema in `templates/REVIEW.md` but attach to planning stages, not tasks.

An approved planning-checkpoint review is a **retained** durable record for the life of the plan: task assignment and task-closure review inherit checkpoint-bound request evidence from it (see § Up-front Operator Request Evidence), and session startup reads it to know which checkpoint is complete. It is cleared only by plan closeout, with the rest of `reviews/`. The checkpoint's prompt (`prompts/PROMPT-PLAN-NNN.md`) and report (`reports/REPORT-PLAN-NNN.md`) are the temporary artifacts: they are deleted when the checkpoint is approved or superseded. A rerun checkpoint overwrites its review file in place. A retained review's references to its consumed prompt or report are historical by construction and are not dangling-reference defects.

Review verdicts are:

- `approve`: task moves to `done/`.
- `request-changes`: task moves to `in-progress/`.
- `reject`: task moves to `open/`.

A review prompt is consumed by its verdict — on all three verdicts, not only
`approve`. After the verdict is applied (the task moved per the verdict and
the durable findings preserved in `reviews/REVIEW-NN-NNN.md`), the PM retires
the consumed review prompt with `cartopian delete-prompt`; a rework dispatch
that regenerates the same prompt slot satisfies the same requirement. This is
normative lifecycle behavior, not workflow-specific housekeeping: the prompt's
bound artifact snapshot names the task's former `tasks/in-review/` path, so a
retained consumed prompt correctly reports `stale-request-context` in
`cartopian plan-audit` and at the dispatch preflight until it is retired or
regenerated. Retirement never precedes the verdict: a `blocked`, `failed`, or
`failed-to-parse` review outcome preserves the prompt for inspection.

## Up-front Operator Request Evidence

Before a task assignment, planning review, or task-closure review, Cartopian
resolves the operator's exact words for the governed unit from one source of
confirmed evidence: operator turns captured by the host intake adapter in the
operator's own interactive session and bound to the project through
`select_project` (§ Session Startup And Project Selection). The adapter is the
host's own prompt and stop hooks (Claude Code, Codex, and Antigravity
hooks; a Hermes plugin; an opencode plugin); it records each
operator prompt under a receipt ordinal and pairs it with the assistant
message it answered. Antigravity's hooks carry no text, so there the adapter
reads the transcript the hook payload names, at the hook moment. Nothing
the PM writes is evidence. Resolution is infrastructure behavior, not a later
confirmation, restatement, review step, scope choice, or requiredness choice.

Applicable evidence for a unit is selected, never searched for:

1. **The confirmation exchange.** The compact intent summary the PM presents
   under § Planning Intent Contract is the proposal; the operator's confirming
   or correcting reply is the assent. When requirements and the plan lock, the
   writer resolves that pair from adapter state in code and binds it into the
   project's binding record as the `project:project` original. Nothing is
   asked again: a resumed session, a new task, or a new agent inherits it.
2. **Referenced turns.** A decision names captured turns with the structural
   marker `Operator request evidence for: <unit>: <capture-id>[, ...]`, where
   the unit is exactly one of `project:project`, `planning:PLAN-NNN`, or
   `task:TASK-NN-NNN`; requirements and task files may name capture identities
   under `## Operator intent`, `## Original request evidence`, or `## Request
   evidence`. Text and provenance always come from the capture. An optional
   block quote directly under the marker must equal the captured text whole,
   modulo whitespace, or the reference is `unconfirmed`. A reference inherits
   the capture's unit, pairing state, and revocation; a turn referenced under
   two units is `cross-unit` for both. A malformed marker fails closed.
3. **Corrections.** Referenced turns later than the confirmation, in receipt
   order. Where a reply differs from the proposal it answered, the reply
   governs; both are kept whole.

A capture identity is `<handle>/turn-<ordinal>`; its paired proposal is
`<handle>/proposal-<ordinal>`. `cartopian lookup-evidence <project-root>
--unit <unit>` (MCP `lookup_evidence`) lists the applicable identities for a
unit one line each, or exactly one missing item with its operator-facing
remedy; it never returns captured text. The PM takes identities from the
lookup (`--recent` for turns not yet selected), never from memory.

Everything else is unconfirmed and satisfies no gate: block quotes under the
retired `Operator request quote for:` marker, the historical DEC-007..DEC-009
attribution wording, any JSON under `requests/chat/` (no supported intake
writes it), a reference to a turn that no receipted binding of the project
contains, a partial quotation, a bare assent whose proposal was not captured,
and a revoked identity. Unconfirmed items stay readable and are reported by
the lookup tool and `plan-audit`; ordinary decision, plan, phase, spec, task,
prompt, and report prose remains PM-derived. A conversation range never
establishes applicability; it only supplies candidates, and unreferenced
candidates are omitted and counted.

Pairing is decided by the adapter when the prompt arrives and then frozen. A
reply is paired with the latest non-empty assistant message of a turn that
was not interrupted, provided no other submission intervened; otherwise it is
`unpaired`. A late or repeated stop never rewrites a pair; when its text
differs from the paired proposal the pair is `inconsistent`, which blocks every
gate (`inconsistent-pair`) until the operator states the scope afresh in their
own session. An unpaired low-information reply ("yes", "proceed", the closed
grammar in `protocol/assignment-prompt-contract.json`) authorizes nothing
(`unpaired-assent`); an unpaired content-bearing statement is evidence on its
own. Events evicted from a session's bounded preselection buffer before
`select_project` are gone: a reference to one is `evicted`, the packet and the
lookup report the eviction, and the remedy is to ask the operator to restate
it. The PM never reconstructs evicted text as captured evidence.

Assurance is procedural traceability. Under an intact, correctly configured
installation, every selected excerpt is established to have arrived through
the configured adapter from the operator's own session, with its pairing and
order fixed at capture. It does not prove human authorship against an agent
using unrestricted same-user access: the intake root, `requests/bindings.json`,
`requests/revocations.json`, and the validator are same-user writable, and
modifying any of them defeats the traceability. That is the stated property of
the design, not a warning attached to an otherwise ordinary approval.

The PM must not manufacture evidence. It must not create, copy, edit, move, or
delete anything under `requests/` or the intake directory, invoke operator-only
intake, modify trust material (the inbox, receipts, bindings, revocations, or
the validator), or use shell, escalation, or any other tool to bypass an
evidence protection. The prohibition is narrow: ordinary mediated or escalated
writes elsewhere are not affected. Every not-captured refusal says the same
thing: stop, report the missing evidence to the operator, and name the
supported intake for this host (the intake hooks installed by
`scripts/install.py --intake-hooks`, trusted in `/hooks` on Codex, enabled
with `hermes plugins enable cartopian-intake` on Hermes, and bound with
`select_project`). Capture and disclosure are separate: the inbox never
enters the project, and only applicable evidence reaches a reviewer prompt. On
hosts without enforcement hooks this guidance is the only enforcement, and it
applies there exactly as written.

A host with no hook or plugin surface at all (the local Devin CLI today;
Devin Cloud is not a supported PM host) has no capture: a PM session there receives no routing line, `select_project`
refuses `session-unbound`, and every evidence gate refuses until the operator
records the request through the operator-only `capture-request` path or runs
the PM on a host with intake. Cartopian reads a host's transcript only at
that host's own hook boundary, from the path the hook supplies; it never
polls a transcript or session database in place of a hook.

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

`cartopian capture-request` remains as an operator-only manual intake path:
the operator runs it in their own terminal to record a message the host hooks
could not capture. It is absent from the managed-agent MCP registry and refuses
whenever `CARTOPIAN_ROLE` (a dispatched role) or `CARTOPIAN_MCP_TOOL_CALL` (an
in-process managed tool call) is set. It is never the PM's remedy for a
missing-evidence refusal. Its records keep their existing original/correction
ordering and immutable exact-text identity. The local CLI does not
cryptographically authenticate a human or prove the authorship of bytes
supplied by an otherwise unmarked process; content identity proves exact
preservation *after intake*, not human authorship by itself. A PM-transcribed
or paraphrased file is never valid proof of verbatim operator origin.

Revocation is operator-only as well. `cartopian revoke-evidence <project-root>
--evidence <id> [...]` (absent from the MCP registry; refuses under
`CARTOPIAN_ROLE` or `CARTOPIAN_MCP_TOOL_CALL`) first records a durable entry in
`requests/revocations.json` naming the evidence identities and every
review-context identity that bound them, never filenames; then moves the
affected project-side records byte-for-byte to `requests/quarantine/`. The
two steps are idempotent and retry-safe after a crash between them. From the
first step on, the shared resolver refuses every review bound to a revoked
identity (`revoked-evidence`) regardless of prompt deletion or regeneration,
and a revoked original no longer counts toward the single-original rule.
Nothing is re-certified: a fresh operator scope statement captured through
the adapter binds as the unit's original at the next lock with
`supersedes: <revoked id>`. Quarantined files are auditable, never resolved.

`cartopian plan-audit` audits the request store itself and inventories every
JSON record under `requests/`, quarantine included, each with a status.
Blocking findings: a binding with no adapter receipt, a binding that names
another project, an unconfirmed record bound by an active review, a revoked
identity or context still in use, a capture identity referenced under two
units, an inconsistent pair, and a referenced unpaired assent. Readable
legacy quotations and hand-written chat files that nothing binds are
warnings. Timestamp patterns are never findings.

A low-information response — "yes", "continue", "proceed", and the rest of the
closed grammar in `protocol/assignment-prompt-contract.json` — states no intent
of its own and is never standalone evidence. It is admitted only when it is
retained and evaluated together with the complete immediately preceding
question or proposal and that proposal's exact scope, and it authorizes nothing
absent from that proposal. `capture-request` takes the antecedent through
`--antecedent-file`, `--antecedent-scope`, `--antecedent-host`,
`--antecedent-conversation`, `--antecedent-message`, `--antecedent-order`,
`--response-host`, `--response-conversation`, `--response-message`, and
`--response-order` — all given together — and refuses `detached-assent`
without it; trace resolution refuses the same shape again for every channel,
including a decision that quotes a bare assent.

The retained binding is one integrity-bound value, not a text plus loose
annotations, and it retains *both* messages. Its `content_identity` covers the
proposal text, the exact scope, the proposal provenance (`source`: `host`,
`conversation_id`, `message_id`), the response provenance (`response`: the same
three fields), and the two positions (`order`, `response_order`) together, so
editing any one of them after capture is `changed-antecedent`.

Both sides are required because one side proves nothing: a proposal that names
only itself may be a faithful quotation from another conversation entirely.
*Immediately preceding* is therefore decided from the pair — the same host, the
same conversation, two distinct message identities, and `response_order`
exactly one after `order`. A pair that is cross-host, cross-conversation,
self-referential, or non-adjacent is `non-adjacent-antecedent`, as is a binding
whose retained response contradicts a channel that records its own source, such
as a host chat turn. Provenance that is absent or unusable on either side is
`missing-antecedent-provenance`; it is never inferred.

The exact scope is a quotation, not a summary: it must appear verbatim in the
retained proposal, modulo whitespace and case, or the record is
`scope-exceeds-antecedent`. A scope written in words the proposal never used
asserts something the operator was never asked, and that is precisely the
detail an assent may not authorize. Resolution also refuses
`ambiguous-antecedent` when the retained antecedent is itself content-free or
carries no scope. Every refusal names one remedy: a scope quoted from the
proposal, or a new, self-contained operator instruction. Every channel renders
such a record as the proposal, its scope, the provenance of both messages, and
then the assent — never the assent alone.

Every selected excerpt retains its capture identity, source path and
full-event SHA-256 identity, exact-text SHA-256 identity, governed unit, kind
(`instruction`, `confirmation`, or `correction`; `original` and `correction`
for `capture-request` records), and deterministic order: binding order, then
receipt ordinal. Deduplication is by presentation, not content: two turns are
one presentation only when their words, the proposal they answered, and their
position relative to the latest correction all match, so identical words
repeated after an intervening correction are kept. Assistant messages are
context, never evidence; unattributed quotations and unrelated conversation
are never promoted into the trace.

Planning, task-assignment, and task-closure prompts carry two generated
channels. `## Original operator request (verbatim)` is the **intent packet**:
the applicable original operator instructions verbatim and in order; then the
confirmation exchange (the proposal the operator answered, then the operator's
confirming or correcting words); then subsequent corrections in order,
constraints and exclusions intact. Each captured turn is preceded by the
assistant message it answered, labeled as context and not evidence, and a
content-free assent is followed by the statement that it authorizes exactly
that message and nothing absent from it. The packet ends with a trailer:
`Request evidence:` (the ordered identities), `Request-context identity:`,
and one line `Omitted candidates: N across M sessions`, with an eviction
notice when any candidate was evicted. It contains no transcripts, ledgers,
inbox contents, or per-excerpt metadata blocks; original words appear first,
summary second. `## PM-derived guidance and delivered outcome` names the
requirements, plan, task, spec, prompt, report, and other delivery evidence
prepared later; PM interpretation lives only there.

`cartopian review-context` is the common read-only projection used by prompt
generation, dispatch, manual handoff, report parsing, lifecycle guards, and
audit. The context identity covers the review target, ordered evidence and
source identities, legacy state, and PM artifact paths. The omitted-candidates
and eviction lines are telemetry outside that identity and outside the
preflight comparison, so unrelated conversation after a prompt is written
never makes it stale. The PM/delivery channel contains only artifacts that
exist when the prompt snapshot is generated, including canonical specs and
applicable phase and prior-review artifacts. Later lifecycle outputs do not
retroactively alter that snapshot; regenerating a review prompt takes a new
snapshot. Any selected-source mutation or prompt omission makes the binding
stale. Exact content carries no byte ceiling and is never truncated: an
excerpt is bound by SHA-256 content identity, never by size.

`cartopian lookup-evidence ... --recent` adds the last five captured
operator turns as identity rows: capture identity, session handle, receipt
ordinal, pairing state, the kind each was selected as (or none), and a fixed
120-character preview, never the whole text. It is how the PM finds the
identity of a correction the operator stated after lock so a decision can
reference it. Code establishes where selected evidence came from; the
reviewer judges whether the packet represents intent.

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
applicable task-bound capture reference or optional task record resolves
for such a task, `unit-request-not-captured` fails closed. Semantic
scope widening is still detected by comparing the exact request channel with
the plan, task, spec, prompt, and delivered outcome; inheritance does not turn
PM-authored scope into operator intent.

The configured review role compares the two channels. The operator supplies the
request but is not the reviewer. Review files and completion reports record:

```text
Request alignment: aligned | drifted | unavailable-for-legacy
Request evidence: <ordered evidence identities> | none
```

Coverage of inherited evidence is **scoped, not blanket**. A planned task
inherits every applicable operator excerpt and authoritative source, but a
task is governed only by the ones that bear on its outcome: an inventory task
is not governed by a pricing statement. For a task that declares `Upstream
trace: required`, the PM records an applicability map in the task's trace
block (`templates/TASK.md` § Upstream trace): an identity a criterion traces
to is *applicable*; an identity that constrains how the work is done without
yielding a criterion is an `A|` `governing-constraint`; an identity
mechanically unrelated to the task is an `A|` `outside-scope`. Both `A|`
classes are routine PM decisions that the configured reviewer confirms at
closure (D2); neither requires operator authority. A `W|` waiver remains the
record for an actual departure from operator intent and still requires
attributable operator authority. Coverage is preserved at the plan level:
`cartopian plan-audit` warns on an excerpt that every task scopes out and no
task claims or waives, and `cartopian close-audit` blocks closeout on it,
unless a current, locked decision (not `open`, not named by a later decision's
`Supersedes:`) records the authorized plan-level disposition `Out-of-plan
request: sha256:<content identity>`. Scoping therefore never silently drops
operator intent. `A|` records are part of the hashed trace identity, so an
applicability decision added or changed after a review was recorded makes
that review's `Trace-identity` stale. `cartopian acceptance-trace
--enumerate` / `--compose-from` derive the mechanical record syntax from a
structured mapping so the PM never hand-computes digests, ordinals, or sort
order.

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

## Standards

`STANDARDS.md` is the project's durable execution-standards document: the settled rules that govern *how* the project's work is performed. It is highly recommended but optional. It is not a governance contract — protocol conventions are tool-owned (`cartopian://protocol/CONVENTIONS`) and are never restated or overridden by a project file — and it is domain-neutral: an engineering project's standards look nothing like a research or writing project's, so an admission test, not a topic list, decides what belongs.

A statement is admitted to `STANDARDS.md` only when all three hold:

1. **Execution-binding** — it governs how work is performed (practices, conventions, tooling and dependency policy, quality bars, boundaries on method), not what is built, when it is delivered, or how the project is managed.
2. **Assignee-actionable** — whoever performs an assignment can follow it directly while doing the work.
3. **Settled** — it is a decided rule, not a preference under discussion or a question awaiting an answer.

Content that fails the test is routed to its owning artifact, never written here:

- Product behavior and scope → `REQUIREMENTS.md` or the governing spec.
- Phase deliverables, exclusions, and sequencing → the implementation plan and phase files.
- Lifecycle and PM behavior → tool-owned protocol and skills; never restated in a project file.
- Unresolved design or standards choices → the plan's open questions, or a decision (`DEC-NNN`) once ruled.
- Design requirements for an artifact the project is itself producing → that artifact's governing requirement or specification.

Because every admitted statement is execution-binding, assignee-actionable, and settled, the whole document is safe to project to the audience that performs work: prompt composition selects the applicable excerpt (see § Prompts) without needing to decontaminate it. The planning checkpoint that reviews `REQUIREMENTS.md` and `STANDARDS.md` verifies this admission discipline; content that fails the test is a review finding routed to its owning artifact before the plan locks.

Selection is deterministic, not judged. A standards section may open with an `Applies to: <identity, ...>` line naming the declared task facts it binds — practice-pack envelope identities (primary outcomes, artifact kinds, domain scopes) or an authorized profile hint. An untagged section, or `Applies to: all`, applies to every assignment. The assignment-prompt composer includes exactly the sections whose tags intersect the task's declared facts; the tag line itself never reaches the assignee.

## Prompts

Prompts are temporary, assignee-directed handoff artifacts in `prompts/`. They restate the requirements, acceptance criteria, context, output expectations, scope boundaries, done criteria, and completion report requirements.

Prompt files follow the canonical field schema in `templates/PROMPT.md`.

Task assignment prompts are **composed, not authored**. `cartopian compose-assignment-prompt <task-path> --role <role>` deterministically renders the audience-scoped assignee prompt from authoritative inputs — task facts, role packet, the spec's assignment projection, tag-selected standards, the selector results re-derived from the task's declared observations and envelopes, source guidance, request evidence, and the report skeleton — together with a machine-readable **trace receipt** and a content identity binding the two. The prompt is an execution interface: raw classifier or selector JSON, routing diagnostics, rejected candidates, inactive guidance, hashes, byte measurements, context receipts, and lifecycle bookkeeping live only in the trace receipt. Composition validation fails closed (the section contract and forbidden-content rules are owned by `protocol/assignment-prompt-contract.json`; section byte sizes are measured into the trace receipt as nonblocking telemetry and never reject composition), and `cartopian write-prompt --composed-file` verifies the record's identities and validation state before writing; a hand-authored assignment body is held to the same contamination rules.

Review prompts are produced with `cartopian write-prompt --review-kind ...`.
The writer resolves the intake trace and owns both generated review-context
sections; authored copies are replaced. Automatic dispatch, manual
`review-context --prompt` preflight, report parsing, and audit recompute the
same target.

Prompts must include complete absolute paths for every resource the assignee is expected to use or produce. They must not rely on relative path interpretation, current working directory assumptions, or vague instructions such as "read the PM system."

A prompt opens its prose with the assignee's role. The role preface is sourced from the resolved role record's one-line description (`handoff-packet.role_description`), addresses the assignee directly, and states what the role does for this assignment. It is orientation, not capability: it grants no authority beyond the role's configured grants and carries no PM identifiers.

Prompt volume is proportional to the assignment. A prompt carries the exact applicable inputs — the spec's assignment projection when a spec governs the work (implementation contract only: no author or reviewer metadata, planning status, review checklists, open-question sections, or PM identifiers), the applicable report skeleton from `cartopian report-skeleton` rather than the full report template, only the assignee projections of the guidance the selectors admitted for this task, and the tag-selected standards excerpt rather than the whole `STANDARDS.md` — never a full template, an unrelated guidance body, or a whole document the assignee needs one section of. Contract content appears once: source guidance is rendered exactly once and referenced elsewhere, and a task goal or acceptance criterion the specification already states is not restated. A rework prompt (after `request-changes`) is a correction, not a re-assignment: it carries the specific findings with their recovery, the affected excerpts, and the applicable skeleton, not the original assignment's full inputs again. A validator-classified mechanical defect never becomes a prompt at all — it is corrected in place through the hash-bound `cartopian correct-report` (see § Reports), so no report bytes, deliverable copies, or governance inputs are ever retransmitted for a schema repair.

Prompt content is audience-scoped as well as proportional. A statement belongs in a prompt only when it changes how the assignee performs, evidences, bounds, or reports the work. Downstream lifecycle facts — which role reviews the work, how a review is launched, what the PM does after the report lands — are resolved by the handoff packet for the PM's own routing and stay out of the prompt body: they change nothing the assignee does and invite hedging, such as deferring completeness to a reviewer or reporting work not ready pending a review that has not been assigned. Each classifier and selector therefore yields two projections: a concise, human-readable **assignee projection** (the risk band with its observable reasons, evidence expectation, operator gate, and contingency; the active judgment holds with the release requirement and the compact central instructions; the selected practice profile's execution capsule with its applicable source identities; the rendered source-guidance record) and a complete **trace record** for validation and audit. Only the assignee projections enter the prompt, rendered as prose by the composer and never paraphrased into additional prose; the trace records stay in the bound receipt.

When the project's `STANDARDS.md` declares standards that govern how an assignment's work is performed — style and formatting conventions, required development practices, mandated validation tooling — the composer selects the applicable sections into the prompt's `## Applicable project standards` section by applicability-tag intersection with the task's declared facts (see § Standards). The excerpt is proportional: only the standards that bind this assignment, never the whole document. Every statement in `STANDARDS.md` is assignee-actionable by the admission discipline in § Standards, so selection is by applicability alone. Standards excerpts are subject to normal deidentification. A task with no applicable standards omits the section.

For source-backed work, the prompt includes the resolved record's deidentified rendering — once — and requires the corresponding source evidence at completion. Prompt composition does not repair or reinterpret an invalid record: readiness and handoff fail first with the record's actionable blockers. Historical provenance and current authority are represented distinctly: an applicable-context value ("authored against version X") is a historical fact about the source and is not by itself invalid; a claim that a source *remains* current is a dynamic claim the governing record must establish. Composition fails only when the work requires current behavior and compatibility with the historical source has not been established — never merely because a version reference is old.

Coder (task) handoffs are **deidentified**. Project-management identifiers — `TASK-NN-NNN`, `SPEC-NN-NNN`, plan refs `KIND-NN-NNN`, requirement refs (`FR-`/`NF-`), decision refs (`DEC-`), and the like — exist only inside PM artifacts; they are not surfaced to the assignee. A coder prompt names the work by its title and addresses every resource by file path, and the coder writes its report to the given report path without recording any identifier. Cartopian links the report back to its task by the report *filename* (`REPORT-NN-NNN.md`), so the assignee never needs — and is never given — a task identifier to copy into product code.

Task prompts are deleted when the task reaches `done/` or when the prompt is superseded before assignment. Planning-checkpoint prompts are deleted when the checkpoint is approved or superseded. Prompts are never archived as durable records.

## Reports

Reports are protocol-defined handoff result artifacts in `reports/`. They are evidence for the PM, not replacements for task, review, decision, or backlog records.

Report files follow the canonical field schema and variants in `templates/REPORT.md`.

The neutral task-report core is `## Identity`, `## Completion evidence`, `## Remaining risks`, and `## Ready to close`. Specialized software and document sections (`## Files changed`, `## Deliverable`, `## Test evidence`, `## Commit / PR`) are optional evidence shapes. For compatibility, an exact `## Files changed` or `## Deliverable` heading may stand in for `## Completion evidence`, and `## Ready for review` may stand in for `## Ready to close` (the two headings parse identically).

Reports carry a capped PM-facing `## Summary` section — at most 10 short lines naming what was done, where the evidence and work product live, and anything the PM must act on. Generated skeletons include it; legacy reports without it remain valid. Storage and projection are separate concerns: the report body is architecturally unbounded on disk as durable evidence, while `cartopian report-action` returns the bounded `pm_summary` projection (and, for review variants, the bounded `review_projection` of the durable review file's verdict, summary, and findings rows) so routing never requires the PM to load the whole artifact into context. The PM routes on the projection and opens the full report or review only when a projected finding requires it.

The readiness value is the producer's declaration about **its own work**, never a certification of anyone else's future verdict. Its first line is a `yes`/`no` token, optionally followed by a short rationale on the same line. Under required task-closure review, `yes` means "my work is complete — route it into the required independent review"; it does not approve closure, and the producer is never asked to self-certify a review it cannot perform. With task review off, `yes` routes the accepted task toward direct closure. `no` is only for genuinely incomplete or blocked work and returns the task to `in-progress`. Skeletons generated for review-required projects use the `## Ready for review` heading so the question the producer answers is the one being asked.

`## Source evidence` is conditionally required when the governing task resolves to valid source guidance. It repeats the shared record shape as completion evidence, not as a second authority: it names the non-empty subset actually applied and what remains unverified. `parse-report` and `report-action` project the validated record; a complete source-backed report with no applied source or with a source identity/context absent from the governing guidance fails closed. Sources in the broader guidance that were not applied are not completion-report requirements.

Task completion reports use `reports/REPORT-NN-NNN.md`. Task review completion reports use the independent `reports/REPORT-NN-NNN-review.md`. Planning-checkpoint review completion reports use `reports/REPORT-PLAN-NNN.md`. The task-completion report is preserved unchanged throughout task review — the reviewer reads it directly from its compatibility path — and neither task-scoped artifact can satisfy the other's completion signal.

Task review completion reports declare the absolute `Task path:` in `## Identity`. The path must name the task implied by the report filename's `NN-NNN` identity in its current lifecycle directory; a missing, stale, or wrong task path is invalid completion evidence. This requirement does not apply to deidentified task completion reports or to planning-review completion reports.

Machine-owned report content is generated, not transcribed. `cartopian report-skeleton` emits the exact applicable report skeleton for one task handoff — only this task's applicable sections, with identities, paths, request-evidence tokens, work-root names, and the assignee-facing source-evidence rows already filled — and, for a review handoff, the durable review file's skeleton. For source-backed work the skeleton also states the exact non-empty unverified-claim row grammar (`- Claim: …; Decisiveness: decisive|non-decisive; Missing: …; Consequence: …; Next: …`) alongside the valid `- none` default, as plain instructional prose that the evidence parser never consumes as a row — so the grammar is discoverable before the report is written, not first at validation. The PM includes that skeleton in the prompt instead of the full template; the assignee supplies substantive evidence, findings, and verdicts only. `cartopian validate-report` validates a written report against the full acceptance contract and enumerates every defect with an actionable recovery and a failure class (`mechanical`, `missing-input`, `substantive`), so a schema or transcription defect resolves as a bounded correction rather than a full rework handoff, a missing input is repaired before any re-dispatch, and a recorded judgment routes to the review loop or the operator instead of being edited into compliance.

A validator-classified `mechanical` defect is corrected in place through the mediated `cartopian correct-report` command, not through a correction handoff. The command is hash-bound and fail-closed: it requires the exact current bytes' `sha256:` identity (a stale identity refuses), and it resolves every failed mechanical check to an exact edit **operation** — replace the `Status:` header line or a bound request header in place (one line, never duplicated, never relocated), replace the specific mismatched Identity bullet in place (duplicate or contradictory bullets refuse), replace the verdict token line (never the rationale beneath it), replace the individual defective unverified-claim row one for one, or delete a contradictory `- none` row from a mixed claims list — the only deletion a correction may perform. The claims grammar is the only editable evidence surface: authoritative-source and conflict-resolution rows are recorded producer evidence, and every blocker touching them — an unresolved conflict, a stale applicability, an applied source outside the governing guidance, an absent record — is classified `substantive` (or `missing-input` for governing-artifact defects) by an explicit, fail-closed audit of every source blocker code; an unaudited code never defaults into the PM-editable class. Comparison is positional over raw spans: section order, heading bytes, subsection structure, prose, row order, and every unaffected line must remain byte-identical; a passing `Status:` token may not change; and a missing required heading may be repaired only as a body-identical in-place rename. A correction never supplies substantive content the producer did not publish — an absent disposition, source, conflict record, or section body refuses and routes back through the failure classes above as rework. The command refuses when any `substantive` or `missing-input` finding is present, requires the corrected body to fully validate before a byte lands, and emits one audit record naming before/after content identities, the corrected grains (including deletions), and the checks resolved. A correction it refuses is by definition not a mechanical correction — it routes per the failure classes above, and a mechanical correction therefore never silently becomes a full reassignment, never requires report-slot clearing, and never requires granting a role broad report or governance read access.

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
- `project:resources/<relative/path>` (project-resource deliverable) — for a **supporting artifact** of the project itself. The document lands under the project's `resources/` directory; a project-mode path outside `resources/` is invalid (`validate-task-readiness` blocks the task). The assignee is not granted write access to the project, so it returns the document inline in its completion report and the PM persists it via `cartopian write-resource`. Its path has a **protocol default**: `project:resources/<kind>/<title-slug>.md`, where `<kind>` is the lower-cased plan-item kind and `<title-slug>` is derived from the task title with every identifier removed. `cartopian write-task` stamps that default whenever a `DESIGN` or `RESEARCH` task omits the field or writes `Deliverable: default`, appending the first free ordinal suffix (`-2`, `-3`, …) when another task already declares the path or the resource already exists on disk, so a default never overwrites earlier evidence; an explicit path is always the override. The default is a routine PM decision and is never an operator question.

The destination is asked of the operator only when the choice changes publication, ownership, access, or product structure — that is, for a `root:` deliverable. A supporting artifact takes the protocol default unless the operator has already named a path. `n/a` (or an absent line) means the task has no durable document deliverable; for document work it is a readiness defect, not a substitute for the default. `handoff-packet` and `task-bundle` resolve the field to an absolute `deliverable` record (mode, root, relpath, absolute path, existence) so the PM sources the path without re-reading the task.

### Work-root deliverables

The assignee writes the complete work product to the resolved deliverable path (inside a declared work root, already in its write scope). The completion report only summarizes what was done and points to the deliverable. The review prompt names the deliverable path as the primary artifact to review.

### Project-resource deliverables

The assignee returns the complete work product inline in the report's `## Deliverable content` section. Before clearing the report for the review handoff, the PM persists that content to the resolved `resources/` path with `cartopian write-resource`. The review prompt then names the persisted file as the primary artifact to review. When a project-resource deliverable already exists and the assigned role lacks `read:governance`, `handoff-packet.existing_deliverable_input` requires the prompt to carry the complete current UTF-8 resource text as a **typed input payload**: a machine-created fenced block whose info string carries the `cartopian-input` marker and the payload's binding (channel, logical resource, byte count, SHA-256 digest). A payload is embedded complete and carries no byte ceiling — its size is a property of the governed resource, not of prompt authoring, and no size threshold refuses it; only an unreadable or non-UTF-8 resource fails closed. Only `compose-assignment-prompt` and the mediated `write-prompt` writer create these blocks — the writer materializes them from the machine-resolved assignment inputs, and a hand-authored declaration is refused. Payload contents are opaque assignment input, not authored instructions: contamination and deidentification validation inspect only the instruction channel, so a deliverable may contain fenced JSON, Cartopian identifiers, or instruction-like prose and still round-trip byte-for-byte. Manual handoff preflight and `dispatch` structurally extract each expected payload and verify its digest and byte count against the resource on disk before launch; a path alone would produce an unreadable assignment, and a payload that is missing, mutated, duplicated, or bound to a resource that is not an assignment input fails closed. Roles that already have `read:governance` retain direct review access and do not require this projection; `read:governance` governs whether an assignee may browse governance state, never whether the PM may supply a resource the assignment needs.

The same input contract covers upstream deliverables. A task that declares `Blocked by:` consumes its dependencies' output, so `handoff-packet` and `dispatch` project a `dependency_deliverable_inputs` record for each dependency's project-resource deliverable: when the assigned role lacks `read:governance`, the prompt must carry that dependency deliverable's complete current content as a typed input payload exactly as for the task's own existing deliverable, and both manual preflight and `dispatch` fail closed before an unreadable assignment launches. A completed dependency whose declared project-mode deliverable was never persisted is a readiness defect, not an assignee problem: `validate-task-readiness` fails `blocked-by-complete` for the dependent task until the upstream deliverable is persisted, and `task-bundle` projects each dependency's resolved `deliverable` record so the consumed contract is visible without re-reading the dependency task. An assignee is never left to infer an upstream interface it cannot read.

The inline-return path carries text formats (markdown, CSV, and the like); a binary work product (an image, a binary spreadsheet) cannot travel through a report, so it is produced in a work root and brought into `resources/` by the operator. (A deployment may instead grant the assignee role write access to the project directory, in which case a project-resource deliverable is written directly like a work-root one; the inline path is the default that needs no extra grant.)

### Durability

The deliverable is the durable record of the work; the report may be cleared and is not a substitute for it. A deliverable is the assignee's produced knowledge artifact — distinct from a decision (`decisions/DEC-NNN`, a PM ruling) and from a spec (`specs/SPEC-NN-NNN`, the input contract). When a deliverable's findings warrant a durable protocol ruling, the PM still records that as a decision.

`plan-audit` enforces this durability: a task in `in-review` or `done` that declares a `Deliverable:` whose file is missing is a `missing-deliverable` blocker (skipped only when a work-root deliverable's name is unmapped on the auditing machine, since existence cannot be verified there). Placement is guarded at the transition that consumes it: `validate-task-readiness` blocks a task whose project-mode deliverable escapes `resources/`, and `plan-audit` emits a `deliverable-outside-resources` warning for a legacy artifact that predates the rule.

## Roles

Each `[roles.<name>]` table in `cartopian.toml` carries a required one-line `description` and may carry capability grants, role-local launch facts, and automatic-launch permissions. Role names are operator-chosen identifiers; names and descriptions explain responsibility but confer no review, launch, selection, capability, or identity authority.

Roles exist to be assigned, which means a PM who takes on the work rather than assigning it is undermining the system. Assign work to role(s) with appropriate descriptions/permissions.

### PM Scope

The PM role is bounded to project-management authoring:

- **Directory scope.** The PM may only read or mutate files inside the project directory currently being managed. It may not modify files outside that project — including sibling Cartopian-governed projects, the Cartopian protocol repository itself, or any unrelated repository the operator happens to have on disk.
- **File-type scope.** Within the managed project, the PM authors markdown (`.md`) files — CREATE, READ, UPDATE, DELETE. There are two non-markdown exceptions. The project's own config files (`cartopian.toml`, `cartopian.local.toml`): the PM may change them only through the mediated `cartopian update-config` command and only on the operator's explicit request (see **Config management** below); an activated native macOS/Linux Claude handoff reports the exact operation for execution outside that handoff because its project sandbox intentionally blocks the writer. And `resources/`: the PM may persist a file there, but only through the mediated `cartopian write-resource` command, and only as transcription — persisting an assignee-returned deliverable or operator-supplied content verbatim, never producing the substantive content itself. All other non-markdown work — source code, data files, build artifacts, executables — must be dispatched to another role via a handoff.
- **Authoring discipline.** A PM that implements work rather than assigning it is a protocol violation, regardless of which file types are involved.

These limits apply to every PM. The PM is always the interactive orchestrator of a session — it is never itself launched as a handoff (there would be no PM to launch it), so `roles.pm.agent` and `roles.pm.auto_launch` must not be configured.

Before config edits or migration, read `cartopian://protocol/CONVENTIONS/roles/config-management-and-migration`. Before assigning a role, read `cartopian://protocol/CONVENTIONS/roles/role-configuration`.

### Config management and migration

- **Config management.** The PM manages the project's config on the operator's behalf, so a non-technical operator never has to hand-edit `cartopian.toml`. Config edits are operator-*requested*, never proactive or routine: the PM does not offer or solicit config changes during ordinary lifecycle flow, and applies them only when the operator explicitly asks (or approves a migration). Every config edit goes through `cartopian update-config`, which validates the closed key schema and the resulting effective config and writes atomically; the PM still reads effective config via `cartopian resolve-config`. This scope covers only config files *inside the managed project directory*; the global `~/.cartopian/cartopian.toml` lives outside every project and is authored by the workspace-setup flow (`skills/init-workspace.md`), not by a per-project PM. Enforcement is precise: a structured raw-edit tool aimed at a config file is denied regardless of grants (the mediated command is the only edit path). Activated Claude handoffs on native macOS/Linux additionally make the entire project directory shell-read-only, and strict MCP exclusion removes the out-of-sandbox MCP writer; such a handoff must report the exact `update-config` operation for the operator or another trusted host to execute outside the handoff. It must not claim the config change completed. Activated WSL2 handoffs refuse preflight until Cartopian can attest Claude's optional interop-blocking seccomp layer. Activated native-Windows Claude handoffs also refuse preflight until Cartopian can attest both a shell sandbox and an exact native Claude executable chain; they are not a runnable residual path. Advisory-tier hosts retain their documented residuals.
- **Migration is PM-owned.** A project's internal protocol-schema version is separate from the installed Cartopian application's release version. Bringing that project schema current is PM-owned orchestration performed on operator approval: the PM applies each applicable `protocol/CHANGELOG.md` entry, doing config edits via `cartopian update-config`, ordinary project authoring through the structured writers, and shipped deterministic filesystem transforms through `cartopian apply-migration-entry`. An activated native macOS/Linux Claude PM records any blocked `update-config` operation for execution outside the handoff and cannot complete the migration until that evidence returns. The migration executor accepts only a registered project root and shipped entry version; its closed registry owns all paths and transformations. Judgment-dependent values return as structured pending PM actions and block the marker bump until resolved. Operators are never expected to hand-edit the version marker or perform file surgery. See `skills/migrate-project.md`.

### Role configuration

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

agent = "cartopian-agy"
timeout = "30m"
```

Role launch and permission fields are:

- `agent`: agent or Cartopian agent wrapper name.
- `model`: optional model identifier, exported to the wrapper as the `CARTOPIAN_MODEL` environment variable; the wrapper translates it into the tool-specific model-selection flag. When unset, no variable is exported and the tool's own default model applies.
- `effort`: optional effort/thinking level for the assigned agent, exported to the wrapper as the `CARTOPIAN_EFFORT` environment variable; the wrapper translates it into the tool-specific effort flag. When unset, no variable is exported and the tool's own default effort applies. A value outside the wrapper's CLI-wide vocabulary makes the wrapper warn on stderr and launch at the default; whether a specific model supports a vocabulary-valid level is the tool's own behavior.
- `auto_launch`: a closed unique list containing applicable assigned work types from `task_run`, `task_review`, and `planning_review`. The list chooses launch mode only after `[automation].initiation` has allowed the run to begin and `run_boundary` permits the handoff; it never initiates a run. It does not assign review, control pace, select a task, or grant capabilities. `cartopian dispatch` enforces the applicable permission fail-closed.
- `timeout`: optional maximum wall-clock duration for PM-launched handoffs. The protocol default is `60m`.

Legacy compatibility only: migration tooling recognizes `project.protocol_version`, `[roles.<role>.launch]`, `[handoffs.<role>]`, `auto_start`, `auto_start_tasks`, `auto_start_reviews`, and `planning_reviews` as migration-source vocabulary. Preferred validation rejects them, and current generation, editing, examples, CLI/MCP authored schemas, and canonical TOML never emit them. Resolved machine records may expose a derived `launch` projection.

`roles.<role>.timeout` — resolved along the project → global chain, defaulting to `60m` — is the single source of truth for the handoff deadline. The launcher exports it to the wrapper as the `CARTOPIAN_TIMEOUT` environment variable (see `skills/run-handoff.md`), and the wrapper is the sole enforcer: it kills the assignee at that deadline (exit `124`); no per-tool CLI timeout flag is set independently — a required tool-native timer is derived from the same value — and the PM runs no concurrent timer or watchdog, so no second timer can kill a legitimate long-running handoff before the SSOT deadline. The PM observes completion through the wait primitives in [Waiting For Completion](#waiting-for-completion).

Every automated handoff follows this argument contract:

```text
<agent> <absolute prompt path>
```

The prompt path is passed as one argument. Tool-specific non-interactive flags, sandbox settings, approval settings, and environment variables belong in a wrapper executable, not in `cartopian.toml`.

Pre-built wrappers for common CLIs (Codex, Claude Code, Antigravity, Devin, opencode, Hermes) are in `wrappers/`. See `wrappers/README.md` for installation.

### Foreground Completion

An automated handoff is one non-interactive session, and the assignee's final result is process exit. Nothing the assignee started survives that exit, and no completion notification can resume it — the session is not suspended between turns, it is over. Two rules follow, and every prompt states them (`templates/PROMPT.md` § Completion report):

- **Completion-critical work runs in the foreground.** Any command whose outcome the completion report depends on — test suite, build, validation script, fixture run, evidence-gate command — must be run in the foreground and waited for before the report is written. Backgrounding it and ending the turn on the expectation of a later notification discards the run and the report with it. A run that cannot finish inside `roles.<role>.timeout` is a blocker to report, not work to leave running.
- **The report is the last action, unconditionally.** Work that succeeded but was never reported is not completion evidence. When the work cannot be finished, the assignee still publishes the report with `Status: blocked` and records what stopped it: a blocked report is a finished handoff, an absent one is a lost handoff.

Claude Code has two logically independent process-scoped hooks. `cartopian dispatch` exports the role/config boundary, exact bound project root, current Python interpreter, and exact resolved Claude executable. Before it starts the detached output supervisor, dispatch removes inherited Bash/function/tracing, Bun/Node, loader, Python, and OpenSSL startup controls; the supervisor runs with `-I -S`, and the POSIX wrapper's Bash uses privileged startup mode. A hook-enabled wrapper requires the absolute Python/Claude bindings, validates containment with the bound Python before probing Claude's version, and launches that same Claude path; neither executable is re-resolved through a potentially writable `PATH`. On native Windows, a hook-bound launch additionally requires the underlying Claude executable itself to be native: `.cmd` and `.bat` shims are refused because the extra command-processor hop cannot preserve the exact settings argv boundary. The shipped POSIX and PowerShell wrappers use the installed `cli/claude_launch_settings.py` helper to resolve capability activation from the canonical Cartopian project/global/local configuration. When any role declares grants, the helper adds `cli/claude_hook.py` as a **PreToolUse** refusal adapter for the structured read and mutation tools; no role declaration means it adds no capability hook. Its shell-free command carries the resolved role, project root, canonical Cartopian config home, loaded legacy-global settings path, and each work root's canonical path plus launch-time device/inode as immutable, separate exec arguments and runs Python with `-I -S`. The hook revalidates those root identities for every call and resolves the authored target spelling before lexical `..` normalization. For an activated launch the helper validates the inherited environment and the top-level `env` Claude still loads from legacy global `~/.claude.json` (or `.claude.json` under an absolute shell-level `CLAUDE_CONFIG_DIR`), refusing loader/interpreter/native-library controls (`PYTHON*`, `LD_*`, `DYLD_*`, `OPENSSL_*`, `BUN_*`, `NODE_OPTIONS`, `NODE_PATH`, `__PYVENV_LAUNCHER__`, `GLIBC_TUNABLES`, `GCONV_PATH`), config or temp-root redirection, `CLAUDE_CODE_PROCESS_WRAPPER`, and Claude startup modes that suppress hooks or cap Stop blocking. The hook resolves project and work-root access against the dispatched project first, not registry order. Another project's duplicate or overlapping work-root mapping therefore cannot widen or narrow the active session, and an unregistered dispatched project remains enforced. An activated bound session also denies structured targets outside that project and its launch-captured work roots; an unbound legacy invocation retains zero footprint outside registered boundaries and refuses equally specific duplicate claims as ambiguous. Because the macOS path-only hook cannot atomically bind Claude's later file open, activated structured mutation tools also refuse external work roots even with `write:worktree`; those authorized mutations use Bash, where the OS sandbox enforces the boundary at the actual filesystem operation. Effective access still resolves from grants only, never from role or wrapper names. Malformed, unknown, missing, and explicitly empty grant sets retain the fail-closed semantics in `CAPABILITIES.md`.

Every accepted activated Claude launch passes an empty `--setting-sources` value. Normal user, project, and local settings—and therefore their hooks, plugins, `env`, permissions, and sandbox additions—are excluded for the whole process, including after cwd changes. Claude still loads the top-level `env` from its legacy global `.claude.json`, so the helper validates that effective environment together with the inherited host environment; other fields in the legacy file are inert on this path. The process settings also disable Claude auto-memory so the dispatched session cannot persist instructions under `~/.claude/projects`. Administrator-managed settings remain trusted policy overrides.

On native macOS and Linux, the helper additionally emits a strict process-scoped OS-sandbox policy for shell commands and their child processes. It forces filesystem isolation on, refuses launch when isolation is unavailable, disables unsandboxed retry and Apple Events, and adds no excluded-command or violation exception. The Cartopian project root, its checkout `.git` marker, its resolved Git directory/common directory (including external linked-worktree metadata), and the active Cartopian install/config/runtime roots are always in `denyWrite`; each declared work root is in `allowWrite` only when the dispatched role holds `write:worktree`, otherwise it is in `denyWrite`. Git metadata inside an authorized external product work root remains writable with that root. Before launch, the shared preflight rejects symlink or hard-link aliases in the activated project/config/registry boundary and scans the complete effective shell-writable set: authorized work roots and Claude/Sandbox Runtime's existing fixed and per-UID temp, npm-log, and debug directories. Protected paths and writable roots must be disjoint in both directions; nested writable roots and protected paths overlapping an implicit write exception refuse before launch so an ancestor rename cannot move a protected inode around a path-based deny. A regular inode with multiple names is accepted only when every name is observed inside the complete writable set; a pre-existing cross-boundary hard link refuses launch. Fresh cross-boundary links are blocked by the attested deny-default sandbox. `CLAUDE_CODE_TMPDIR` and `CLAUDE_TMPDIR` are refused; inherited `TMPDIR`/`TMP`/`TEMP` are replaced before the version probe with a direct user-owned mode-0700 `~/.cartopian/claude-host-tmp` under the protected config root. That directory contains the unsandboxed host SRT mux socket, while attested Claude 2.1.278 assigns sandboxed Bash its separate writable per-user temp. On Linux, mountinfo coordinate validation also rejects nested mounts and differently spelled bind-mount aliases of a protected boundary. A policy path containing `*`, `?`, `[`, `]`, or a control character also refuses because Claude/Git could treat it as syntax, skip it, or split it rather than enforce the literal path. The helper refuses unsafe host-helper controls from the still-effective inherited or legacy-global environment, passes an explicit sanitized environment to each pre-containment Git/version probe, protects the selected `rg` PATH chain on macOS and the `rg`/`bwrap`/`socat` chains on Linux, and refuses any intersection between those search paths and an authorized writable work root. The structured hook denies writes to the legacy config, governed-project Git metadata, Cartopian enforcement/configuration/runtime paths, bound runtimes, and protected host-helper paths. Activated WSL2 handoffs fail preflight until Cartopian can attest Claude's optional interop-blocking seccomp layer. Activated native-Windows handoffs fail preflight until Cartopian can attest both a shell sandbox and an exact native Claude executable chain; no partial capability session is launched on that host. Project/governance/report mutations therefore use the structured capability surface or a mediated Cartopian command, while authorized external-product mutations and build/test commands use sandboxed Bash. Arbitrary shell command text is never parsed as an authorization mechanism. Shell reads remain outside the write-only sandbox boundary on accepted native macOS/Linux launches. Completion-only hook launches require Claude Code 2.1.139 or newer for shell-free exec arguments and, on native Windows, a direct native Claude executable rather than a `.cmd`/`.bat` shim. Every activated capability launch requires Claude Code 2.1.278 or newer—the first build behaviorally attested for this exact deny-default policy, strict MCP, and settings-source isolation—and the wrapper refuses an older or unidentifiable version before launch.

Every activated Claude launch also passes `--strict-mcp-config` without an explicit MCP config. User/project/plugin MCP tools execute outside the shell sandbox and emit `mcp__...` events beyond the built-in-tool matcher, so Cartopian excludes them rather than representing them as capability-scoped. The wrapper also disallows delegated-agent/worktree tools, team lifecycle tools, durable cron tools, peer-session message/file routing, and remote agent triggers (`Agent`/legacy `Task`, `EnterWorktree`, `ExitWorktree`, `TeamCreate`, `TeamDelete`, `CronCreate`, `CronDelete`, `CronList`, `SendMessage`, `SendFile`, and `RemoteTrigger`); the refusal hook independently denies any such call that still reaches PreToolUse. Those tools can move or defer execution, route mutations through another session, or mutate shared Git metadata outside the captured project/work-root boundary. Normal user/project/local hooks and plugins are not loaded in the activated process. Administrator-managed extensions remain trusted operator policy.

Independently, `cli/claude_stop_hook.py` is a **Stop** hook that refuses to end the turn while the report slot named by `CARTOPIAN_EXPECTED_REPORT_PATH` is absent or unparseable, feeding the assignee the instruction to finish in the foreground and publish. The helper adds it whenever that variable is present, whether capability gating is active or not, and captures the report path, variant, and block ceiling in direct hook arguments so a settings `env` block cannot redirect or disable the slot. For an activated launch it also binds a direct, user-private counter directory below the canonical protected Cartopian config root; the counter does not depend on temp variables, and its I/O does not follow planted symlinks. Both entries travel in one inline Claude `--settings` object but remain separate event entries, and the process layer asserts `disableAllHooks:false`. Completion-only and ungated launches retain all normal settings sources, which therefore remain hot-loadable; that is an accepted residual for this bounded, fail-open completion mechanism. Activated launches instead use the empty-source isolation described above. Hook-enabled launches refuse `CARTOPIAN_CLAUDE_BARE=true` and inherited Claude startup modes such as `CLAUDE_CODE_SIMPLE` or `CLAUDE_CODE_SAFE_MODE`, because current Claude releases can suppress even explicit process-scoped hooks under those modes. A missing/invalid helper or required hook refuses launch instead of silently weakening a dispatched handoff. Managed `disableAllHooks`, `allowManagedHooksOnly`, or managed environment settings remain administrator overrides above command-line settings.

The completion hook delegates the completeness question to the same canonical observer the wait primitives use, imposes no timer of its own, and bounds itself to `CARTOPIAN_STOP_GUARD_MAX_BLOCKS` interventions (default 3) so a session that genuinely cannot report is never pinned open. It fails open on every error path. Hosts with no comparable completion interception point rely on the prompt instruction alone. Completion enforcement grants and denies no capability and contributes no containment evidence.

Older projects may still contain Cartopian `claude_hook.py` PreToolUse or `claude_stop_hook.py` Stop entries written by an earlier installer. Activated dispatch excludes those normal-scope files, so their presence neither executes a duplicate hook nor requires cleanup before an activated handoff. Completion-only and ungated launches still load normal settings; where a persistent Stop registration would collide with the report-bound process hook, shared preflight refuses it rather than executing twice or depending on mutable environment identity. The explicitly requested `scripts/install.py --claude-hook <project-dir>` compatibility operation remains available to remove obsolete Cartopian handlers while preserving unrelated settings and hooks; it creates no registration. Ordinary install, update, and project reconciliation never mutate registered projects for this migration.

Stop refusal is completion intervention, not capability prevention or detection. The terminal classification in [Waiting For Completion](#waiting-for-completion) is unchanged and remains authoritative: a clean exit with no report is still classified `exited-without-report` whenever the guard is absent, disabled, exhausted, or bypassed. That classification says nothing about capability containment.

### Automated output safety

`cli/output_safety.py` is the runtime source of truth for automated-dispatch launch-log retention. Every configured wrapper runs through this agent-neutral standard-library supervisor on POSIX and native PowerShell/CMD launch paths. The supervisor continuously drains combined wrapper output so the child cannot block on a full pipe, but retains only the bounded `<report-path>.launch.log` diagnostic. Bytes outside the retained representation are discarded; retained-log growth never signals, terminates, fails, or otherwise constrains the assignee, its source files and deliverables, or its completion report.

The shipped retained-log defaults are **400 lines / 64 KiB**. Operators may set positive-integer `CARTOPIAN_LOG_LINE_LIMIT` and `CARTOPIAN_LOG_BYTE_LIMIT` overrides in the dispatch environment; malformed, zero, or negative values refuse before launch. Accounting for the retained representation uses raw bytes. Line count is the number of LF bytes plus one when the representation ends in a non-empty trailing fragment: CRLF counts once, a final LF adds no empty line, and multibyte or invalid UTF-8 bytes receive no special treatment. Truncation is marked explicitly, and the stored representation never exceeds either configured limit. Unsafe, unwritable, symlinked, hard-linked, or non-regular destinations degrade to unavailable retention and never redirect output into the PM-visible stream.

The outer supervisor preloads canonical report parsing before child creation and throttles report observation by elapsed time, independently of output chunk volume. Its pipe-readiness wait times out at the next report poll or grace deadline, so a wrapper that publishes a complete report and then holds stdout open silently cannot stall retained publication or reap. Once the report is complete, the supervisor atomically publishes the current retained representation before beginning the wrapper-compatible post-report grace/reap path, continues draining during that grace, and atomically replaces the log with the final bounded representation afterward. The grace reuses `CARTOPIAN_REPORT_POLL` and `CARTOPIAN_REPORT_GRACE_POLLS`; it reacts only to a complete report and never replaces or extends the role timeout.

Dispatch and status records expose only retained-log limits, path, retained size/line count, truncation state, report presence, and **`guarantee_scope=retained-launch-log`**. This is a storage-retention guarantee, not an execution-output, artifact-size, completion-report-size, pre-model interception, model-context, or provider-private-context guarantee. The wait commands consume report/status metadata plus the launch-log companion's safe file-shape metadata only; they never open, read, summarize, or use launch-log contents as progress evidence. `delete-report` removes both status and log companions on slot clear and close.

### Launch Directory

Assignee CLIs run with cwd set to the **cartopian project root** — the absolute path recorded for the selected project in the registry (FR-003). The shipped wrappers resolve and `cd` to that path automatically; the prompt path passed to the wrapper carries the project root in its prefix (`<project-root>/prompts/PROMPT-NN-NNN.md`) so derivation is unambiguous. `cartopian dispatch` sets `CARTOPIAN_LAUNCH_CWD` to the same project root. No "parent" or "shared workspace" directory is involved in the launch contract.

Wrappers translate env → CLI flags, set the cwd, run the agent **autonomously** (so the unattended handoff completes), enforce the `CARTOPIAN_TIMEOUT` deadline, and emit the status signal. The Claude wrapper additionally attaches its native process-scoped structured-tool hook and, on native macOS/Linux, its shell-write sandbox when the dispatch role/config boundary activates grants. Activated WSL2 dispatches refuse pending seccomp attestation; activated native-Windows dispatches refuse pending both shell-sandbox and exact-native-executable-chain attestation. Authorization resolves from the project's grants, never from a role name. The same wrapper may back any operator-defined role. Locations outside the project root that a task needs (declared as **work roots**, below) are referenced by absolute path/URI inside the prompt the PM authors.

**Work-root write grant.** The launched agent's tool surface must include the Cartopian project root and the project's declared work roots; actual writes remain capability-scoped. `cartopian dispatch` resolves the declared work roots fail-closed (an unmapped name, a mapped path missing on this machine, or a path containing a control character or the platform path-list separator refuses the launch) and exports the resolved absolute paths to the wrapper as the `CARTOPIAN_WORK_ROOTS` environment variable (`os.pathsep`-joined: `:` on POSIX, `;` on Windows; not exported when the project declares none, and a stale inherited value is cleared). Control- or separator-bearing paths cannot be represented losslessly by this line-oriented transport and must be relocated. A wrapper whose agent CLI imposes its own filesystem sandbox rooted at the launch cwd must make these paths visible — the shipped codex wrapper adds them as `sandbox_workspace_write.writable_roots`, and the Claude and agy wrappers pass each as `--add-dir`. On activated native macOS/Linux Claude handoffs, the stricter process-scoped `denyWrite`/`allowWrite` policy then removes shell write access from every root the dispatched role is not authorized to mutate and always denies shell writes to the Cartopian project root. Activated WSL2 and native-Windows Claude handoffs refuse at their respective unattested host boundaries instead of receiving a work-root grant. Other wrappers retain their documented containment tier. Where a tool's sandbox exposes no per-path grant surface (devin `--sandbox`), the wrapper warns on stderr that declared work roots may be unwritable inside that sandbox.

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
run_boundary = "handoff-complete"
```

Supported `initiation` values are:

- `operator`: execution begins only from an operator execution directive (see [Request Intent](#request-intent)). After informational requests and scoped directives the PM reports and stops.
- `auto`: the PM may initiate a run without a directive — at session startup once startup duty completes with no blockers, and when a scoped directive leaves the open queue ready. Informational requests remain read-only, and explicit "stop"/"pause" language still suspends initiation until the operator directs execution again.

`run_boundary` names the unit that ends one initiated run. Supported `run_boundary` values are:

- `handoff-complete`: the run ends after one handoff reaches a terminal publication outcome and that result is processed.
- `handoff-budget`: the run continues through sequential handoffs whose applicable role-local `auto_launch` permission is present until blocked, failed, rejected, missing evidence, requiring operator judgment, reaching a phase boundary, or exhausting `max_handoffs_per_run`.
- `task-complete`: the run binds one task at initiation and continues every configured and authorized assignment or lifecycle activity that same task needs to reach `done`, then returns control.

Defaults are `initiation = "operator"` and `run_boundary = "handoff-complete"`. `max_handoffs_per_run` is a positive integer that is required and valid only under `run_boundary = "handoff-budget"`; authoring it beside either other boundary, omitting it under `handoff-budget`, or giving it a non-positive or non-integer value fails configuration validation, as does any value outside the closed domains. Outside `handoff-budget` the resolved record reports it as `null`.

The automation authorities are disjoint, and each gates a different question:

- `initiation` gates **whether a run begins** when no execution directive was given.
- `run_boundary` gates **how far an initiated run continues**: under `handoff-complete` the PM stops after the handoff reaches a terminal result and that result is processed, then resumes with the next sequential step when the operator says to continue; under `handoff-budget` the initiated run stays active while a launched handoff is working and chains through sequential tasks within the run budget; under `task-complete` the run stays active for one bound task and ends when that task reaches `done`. No value authorizes initiation — `run_boundary` describes how far an initiated run continues, not whether one starts.
- **Selection** is never gated and never an operator question: task order is deterministic per [Task Execution Order](#task-execution-order). Within an initiated run, evidence-supported lifecycle moves (starting the next sequential task, moving a task per a parsed report or review verdict) are applied without a confirmation prompt; the operator is consulted only at the stop conditions named there.
- `roles.<role>.auto_launch` gates **launch mode** for each listed assigned work type; it participates in neither initiation nor pace.

Full unattended operation is therefore a stack of explicit opt-ins, each an operator choice and none a protocol default: `initiation = "auto"` (runs may begin without a directive), a `run_boundary` that continues past one handoff (`handoff-budget` with `max_handoffs_per_run` sized to the desired batch, or `task-complete`), and the applicable work types present in each launched role's `auto_launch` list.

`max_handoffs_per_run` is a launch budget and exists only under `handoff-budget`. Only launches consume a handoff budget unit. Re-invoking a wait primitive, receiving a nonterminal observation, or automatically waking/resuming the host to continue observing the same launched assignee consumes no unit and cannot authorize or cause another launch.

#### The `task-complete` run unit

Under `task-complete` the run binds exactly one task identity at initiation: the active task, or the task deterministic selection names under [Task Execution Order](#task-execution-order). Binding invents nothing — when the existing initiation and selection rules bind no task, no run starts. It is ephemeral run state: not a task field, not a status directory, and never written to the filesystem.

While the run is active, the PM continues any next action that is already configured, already authorized by an existing authority, and applicable to the bound task identity. The contract names no activity list, no artifact kind, and no role names: assignment, review, evidence collection, rework, reassignment, delivery, or any other lifecycle activity a Cartopian-governed workflow configures continues on exactly the terms that already govern it. `task-complete` widens no authority — it decides only when the run ends, and a governance, research, marketing, operations, or policy workflow reads it the same way a software one does.

The run ends the moment the bound task reaches `done`, before any selection, lifecycle move, prompt composition, or dispatch that belongs to another task. Other open tasks stay open and the operator regains control.

Every existing fail-closed condition remains terminal for the run and leaves governed state as it was: a blocker, an unavailable or refused launch, a failed or absent report, a malformed or identity-mismatched publication, a timeout, missing or invalid evidence, an unavailable assignee, a host-capability mismatch, a review verdict that routes to an operator-owned decision, a reserved decision, an operator gate, or an explicit stop.

`task-complete` neither consumes nor honors a handoff budget; `max_handoffs_per_run` is invalid under it. How many handoffs one bound task needs is a property of that task, not a number the operator has to predict in advance.

Handoffs are sequential. Concurrent child agents are out of scope.

### Waiting For Completion

The PM detects handoff completion by observing the filesystem through two canonical read-only wait primitives, which replace all ad-hoc polling, hand-rolled timing loops, manual "tell me when it's done" prompts, and PM-side watchdog timers:

- `cartopian wait-handoff <task-path> --role <role> [--max-block <duration>]` — for task-scoped handoffs (task assignment, task review). It resolves the task's expected report path and honors the configured `roles.<role>.timeout` value as the absolute ceiling.
- `cartopian wait-report <report-path> [--role <role>] [--max-block <duration>]` — the lower-level primitive for a known report path, including planning-checkpoint reviews that have no task file. With `--role` it honors the same resolved role launch timeout; otherwise the protocol default applies.

The completion contract is:

- **The report file is the authoritative completion signal, and a terminal observation binds final bytes.** Path appearance alone is not terminal. A complete report that parses as the expected handoff variant routes its actual verdict (`accepted`, `blocked`, `failed`, `changes-requested`, or `rejected`) — but only once its bytes can no longer change. While a matching automated launch is still `state=running`, the report writer is alive and may still rewrite even a syntactically complete report (the supervisor's post-report grace window is exactly such a period), so the canonical observer holds a complete report nonterminal until the launch publishes `state=exited`. The supervisor publishes that status — and, for retained-log launches, the atomic launch-log snapshot immediately before it — only after the child process is provably gone; because the prior slot's log was removed before the running marker, a safe single-link regular `<report-path>.launch.log` therefore also proves the writer is gone if the following status replacement is lost or raced (supervisor loss after final publication). Each wait's terminal record names the accepted bytes as `report_content_identity`; downstream parsing and routing accept that identity back (`report-action --expected-identity`, `validate-report --expected-identity`) and refuse fail-closed on a mismatch instead of acting on different bytes. This live-launch barrier never changes the report verdict and never opens or reads the log body. Manual/report-only observation has no status requirement, and a matching `state=exited` status is the normal publication boundary: a dead supervisor cannot strand an already complete authoritative report. Incomplete or temporarily malformed bytes remain nonterminal while the current wrapper can still finish publication. After wrapper exit, stable malformed bytes classify `failed-to-parse`; a non-zero exit with no report classifies `failed`; a clean exit with no report classifies `exited-without-report`. A `timeout`, hard process stop, crash, or missing/late/permanently invalid report is not successful completion evidence.
- **Wrapper status is current, secondary evidence.** Automatic dispatch clears the launch's own expected report and `<report-path>.status` after all preflights (for task review that is the independent `REPORT-NN-NNN-review.md` slot — the preserved completion report is never cleared by a review launch), removes any prior launch log while establishing a safe destination, publishes a fresh `state=running` status carrying the launch identity and expected variant before child creation, and removes that marker if supervisor creation fails. When bounded retention is available, that marker also carries `guarantee_scope=retained-launch-log` and `retained_log_ready=false`. Once a complete report is observed, the outer supervisor grants the child a short grace to exit, reaps it if it lingers, and only then — with the writer provably gone — atomically publishes the retained snapshot followed by the final `state=exited` clean/error/timeout status with retained-log facts, so a custom wrapper or wrapper-launch failure cannot strand `running` and no publication signal ever precedes final bytes. If that status replacement is lost after the atomic snapshot publication, waits recognize the safe deterministic launch-log companion as publication metadata without opening its body. Retained-log truncation is nonterminal metadata and never changes that lifecycle result. Manual launches may omit running identity; absence remains valid and waits use report-only observation. A stale or variant-mismatched status cannot terminate or delay a new handoff. The `.status` file remains transient and is removed through `cartopian delete-report`. Both wait commands are read-only — they never write project state, move tasks, launch processes, or read `.launch.log` bodies.
- **Coder completion evidence and reviewer completion are separate artifacts.** The accepted coder report stays preserved at `reports/REPORT-NN-NNN.md` throughout task-closure review, and the reviewer publishes independently to `reports/REPORT-NN-NNN-review.md`. The review-prompt writer binds the preserved completion report by absolute path, outcome facts, and SHA-256 content identity inside the generated review context — the reviewer reads the artifact directly and the prompt never reproduces the report body. That binding also covers exact operator evidence, PM-derived artifact paths, task, prompt, review target, and the expected review-report path. Review preflight re-verifies the preserved artifact against the bound identity: a missing completion report blocks the review launch, and a mutated one is a stale binding. Task review expects the `review` report variant at the review path, so completion-shaped content in the review slot — or a review report in the completion slot — is a path/variant mismatch and cannot satisfy the other signal. Review retries clear only the review slot's transient state; the completion artifact stays byte-identical.
- **Waiting is terminal by default: one launch, one wait call, one result.** Called without `--max-block`, a wait primitive blocks until a terminal observation, bounded by the resolved handoff timeout as the absolute ceiling. This is the only supported shape. Cartopian has no wake, resume, or callback mechanism, and no host is assumed to supply one — a blocking call that survives to the report is the entire completion mechanism. **The silence while that call is outstanding is correct.** No model turn is in progress during a pending tool call, so a host instruction requiring periodic commentary during ongoing work does not govern it: such instructions govern turns the model holds, and a pending call holds none. A PM that slices a wait into short `--max-block` observations to create opportunities to speak has converted a correct silence into per-slice context cost and nonterminal records that decide nothing. Slice only for a host ceiling that cannot be raised, never for narration.
- **The host's tools/call ceiling is a hard constraint, and it is checked before launch.** Every MCP host may cap a single `tools/call` by fixed wall clock, by silence between messages, or both. The resolver distinguishes direct CLI execution, a recognized connected host, and an unrecognized connected host even when `clientInfo` is absent; unknown connected capability fails the gate. It reports raw wall/idle facts, evidence, progress-reset behavior, and the sustainable effective budget. Documented progress may maintain a resettable idle channel; it never extends a fixed wall-clock ceiling. `cartopian dispatch` refuses to launch, before child creation, when `roles.<role>.timeout` exceeds that sustainable budget or connected capability is unknown, naming measured facts and bounded remedies. `cartopian host-capability` exposes the same read-only record, and both wait primitives echo it as `host_wait_budget`. The remedies are to raise the host ceiling, lower the role timeout, or dispatch manually and monitor the report path; recurring operator wakeups are not a completion mechanism. Host-specific keys and defaults live in `skills/register-mcp.md`.
- **A blocking wait occupies the session for its duration.** The MCP server processes one message at a time, so no other Cartopian tool is serviced while a wait is blocked. This is a property of the waiting model, not a fault to route around: dispatch one child handoff at a time and let the wait run to its terminal result.
- **`still-running` is a nonterminal internal observation boundary, reachable only when `--max-block` was explicitly supplied.** `--max-block` bounds a single observation slice and exists for one purpose: to fit a wait inside a host ceiling that cannot be raised. When that explicitly requested budget elapses before the configured timeout, the assignee may still be working. It is not a blocker, completion result, budget-consuming event, or operator-confirmation boundary. Routine `still-running` / `still_running` slices are silent and context-neutral: the PM keeps the same initiated run active and re-invokes the same canonical wait primitive in bounded slices without user-facing text or repeated state when no material state changed. User-facing output is allowed only for a terminal result, blocker, timeout/failure, meaningful new progress evidence, or a deliberately throttled long-running threshold. A re-wait is read-only and does not launch or dispatch an assignee; the single launch remains the active handoff. Under `handoff-budget` and `task-complete`, the run therefore remains active across every nonterminal observation. Under `handoff-complete`, control returns only after the handoff reaches a terminal result and that result is processed, never between observation slices. Slicing a wait to fit a host ceiling costs context on every slice, so prefer raising the ceiling; where the host cannot be raised and slices are unacceptable, declare manual monitoring instead of pretending the wait is automatic.

The wrapper enforces the wall-clock deadline (at the OS level when available; through agy's aligned internal print timer in the Antigravity bash wrapper's no-coreutils fallback). The wait commands observe the result rather than imposing a separate PM-side deadline.

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

## Delivery Gate

A plan that produces an outcome reaching anything outside itself — delivered, launched, published, transitioned, or adopted — carries one `## Delivery contract` section in `IMPLEMENTATION_PLAN.md` naming owner, target, acceptance evidence, success signals, contingency, immediate verification, and follow-up timing. `protocol/DELIVERY.md` explains the contract; `protocol/delivery-contract.json` is the single authority for its machine values.

The gate exists for one failure: artifact creation mistaken for outcome completion. It keeps three states apart and never collapses them. **Artifact complete** means the work product exists and passed its artifact-level check. **Outcome verified** means the target's own state was observed and the observation passed. **Follow-up** is the remaining question, its owner, and when it is answered. A complete artifact never establishes a verified outcome, and a verified outcome never discharges a follow-up.

A plan that reaches no target outside itself declares `Delivery: not-applicable` with a justification. Missing and justified not-applicable are distinct states: a plan carrying no delivery-contract section at all is undeclared, and undeclared fails closed.

Missing, placeholder, contradictory, unavailable-owner, changed-target, and self-certified-evidence records fail closed with an ordered finding and a named recovery. `cartopian validate-delivery <project-root>` is the detail surface and exits non-zero when the gate is blocked; `close-audit` folds every finding into its closeout blockers; `compose-state` and `next-action` carry bounded status; `review-context --review-kind planning` projects the contract and its evidence to the reviewer complete, with its measured size as nonblocking telemetry. One validator serves all of them, so CLI and MCP never disagree.

The requirement is breaking for every plan authored before it, so the project schema marker gates it. It landed as protocol version `v0.12.0`; a project still marked below that has not adopted it, and Cartopian refuses to represent such a project as current: readiness fails its `project-schema-current` check, startup raises a migration blocker, and `migrate-config` refuses to advance the marker while the section is undeclared. The migration itself is PM-performed authoring through `write-plan` on operator approval — no shipped transform writes a delivery record on the operator's behalf.

The gate validates a delivery record. It never performs, schedules, transmits, or authorizes a delivery. Whether an external action happens remains the operator's decision, recorded as an authority state: an unauthorized action stays `pending-authority` and a declined one stays `not-delivered`, never `verified`. An unattended run may prepare and validate the record and may not cross that boundary.

## Plan Lifecycle

A Cartopian project has one active implementation plan at a time. The live `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, `phases/`, `tasks/`, `specs/`, `reviews/`, `decisions/`, `prompts/`, and `reports/` describe the current plan only.

**Readiness is the exit condition of planning.** Task-and-spec generation for a phase is not finished when its checkpoint is approved; it is finished when the first ready task of that phase passes `cartopian validate-task-readiness --rehearse-dispatch` after the final approval. Later tasks may retain explicitly declared dependencies, but the first task carries no unresolved planning input — dependencies, deliverable destination, sources, acceptance trace, prompt composition, role configuration, and launch prerequisites are all settled before planning reports complete, and `cartopian next-action` then reports `ready` with that task's dispatch action.

When a plan completes, close it before starting a new plan. The canonical closeout workflow is `skills/close-plan.md`.

Plan closeout requires:

- No task files in `tasks/open/`, `tasks/in-progress/`, or `tasks/in-review/`.
- No active or ambiguous prompts.
- No unresolved or ambiguous reports.
- Phase exit criteria satisfied by completed tasks, decisions, specs, or documented operator acceptance.
- A delivery contract that passes the delivery gate (see § Delivery Gate). A declined archive does not erase the delivery obligation, and closeout never reports success while a delivery obligation is unmet.
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

`STANDARDS.md` — the project's durable execution standards (see § Standards) — may carry forward only when the operator explicitly chooses to keep it as seed context for the next plan. Otherwise, it resets to a seed file. Protocol conventions are tool-owned and read through `cartopian://protocol/CONVENTIONS`; projects do not carry a local `CONVENTIONS.md`.

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

## Project Summary

A plan close may leave behind one **plain Markdown** summary of the project so that a later session can be pointed at it. The artifact is `<project-root>/CONTINUITY.md`, and there is exactly one per project: it is not per-plan, not per-phase, and not resolved from anywhere else. It survives `reset-plan` (whose allowlist never names it) and is never copied into an archive (`ARCHIVE_ROOT_FILES` never gains it). It is a mediated artifact: hand edits are out of band, exactly as they are for `BACKLOG.md` and `STATE.md`.

It is plain Markdown and nothing more. There is no format header, no schema, no row or table grammar, no index, no counter, and no size ceiling beyond the mediated-write primitive's ordinary rules. Cartopian does not parse the body, derive fields from it, or project any part of it.

**Writing.** One mediated command writes it, and it composes nothing — the body it is handed is the artifact:

```
cartopian write-continuity <project-root> [--content <summary> | --content-file <path>]
```

The summary is independent of the ordinary plan-archive choice. Archiving does not require a summary, a summary does not require an archive, and running neither, either, or both leaves `archive-plan`, `reset-plan`, and `archive/INDEX.md` behaving exactly as they do without this artifact. Re-issuing the command replaces the summary in place.

**Reading is explicit only.** One command reads it, and it runs only when the operator asks for the summary:

```
cartopian read-continuity <project-root>
```

No other command opens, stats, or parses the artifact. `next-action` and `compose-state` emit no field, projection, index, or rendered line derived from it, in any mode and whether or not it exists; `write-state` never reads it, so `STATE.md` never restates it; and startup, status, prompt/state composition, task assignment, and task execution perform no summary load of any kind. Nothing derived from the summary is ever injected automatically, and nothing replaces that removed injection with another automatic context channel.

**Absence is success.** A project with no summary starts, reports status, composes state, and executes tasks exactly as one that never closed a plan: zero added context, zero cost, no failure. Explicit retrieval on such a project reports the absence and exits successfully. A present artifact that cannot be read as text — a symlink or other non-regular file, or bytes that are not UTF-8 — is refused by name on that explicit path only.

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
