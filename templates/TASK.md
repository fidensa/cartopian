# TASK-NN-NNN: <short imperative title>

Phase: PHASE-NN
Plan ref: KIND-NN-NNN
Source: <BL-NNN | n/a>
Work root: <name | name, name | n/a>
Deliverable: <root:relative/path | project:resources/relative/path | n/a>
Assignee: <free text; decided per task>
Spec: <SPEC-NN-NNN.md | none>
Depends on: <TASK-NN-NNN, TASK-NN-NNN | none>
Blocked by: <TASK-NN-NNN, TASK-NN-NNN | none>
Created: YYYY-MM-DD
Evidence gate: <required | n/a>
Source guidance: <task | spec | n/a>
Upstream trace: <required | n/a>

## Goal

One or two sentences. What does done look like?

## Plan ref

One primary plan item from `IMPLEMENTATION_PLAN.md`, for example `BUILD-01-001`. The matching phase file must carry the same plan ref. The plan ref allocates the suffix: `BUILD-01-001` binds `TASK-01-001`, and every task-scoped artifact carries `01-001`. Pre-activation tasks keep their existing mappings unchanged under the approved compatibility boundary. A task that truly advances multiple plan refs should usually be split; use References for secondary context.

## Source

The backlog entry (`BL-NNN`) this task was promoted from, or `n/a` when the task was not born from a backlog item. Do not hand-type this line to satisfy the promotion guard — stamp it with `cartopian write-task … --source BL-NNN`, which verifies the entry is live before writing it. `delete-backlog` reads this stamp to confirm the promotion is recorded before removing the entry.

## Work root

Optional, multi-valued, name-only. Each value is a work-root **name** drawn from the project's `[project].work_roots` list in `cartopian.toml` (see `protocol/CONVENTIONS.md` → Work Roots). Multiple names are comma-separated:

```
Work root: product, design
```

Names only. Absolute paths, project-relative paths, and `<owner>/<repo>` slugs are rejected. Operator-machine path mapping lives in `<project-root>/cartopian.local.toml` and is resolved by `cartopian resolve-config`; the launcher consumes the resolved absolute paths and fails closed on unmapped names.

Use `n/a` (or omit the line) when the task touches nothing outside the cartopian project root. Within-root subdirectory scope belongs in the task body, not in this field.

## Deliverable

Set this whenever the task's work product is a durable document — research findings, a design or evaluation, an analysis — rather than code. `DESIGN` and `RESEARCH` plan items may not use `n/a`. The field names where that document lives, so the report can stay a thin summary and the reviewer reviews the real artifact. Name-only and deidentified (no task, plan, spec, or requirement identifiers), same discipline as `Work root:`. Two forms, routed by intent:

- `root:relative/path` — the work product is intended to become part of the product. It lives in the work root named `root` (drawn from `[project].work_roots`); the assignee writes it there directly, exactly as it writes code. The path is operator-chosen — captured from the operator at authoring or assignment, never invented by the PM.
- `project:resources/relative/path` — the work product is a supporting artifact of the project itself. It lives under the project's `resources/` directory (a project-mode path outside `resources/` fails `validate-task-readiness`). The assignee returns the document inline in its completion report and the PM persists it with `cartopian write-resource`, because the assignee is not granted write access inside the project.

Use `n/a` (or omit the line) only for code tasks and other task kinds with no durable document deliverable. See `protocol/CONVENTIONS.md` → Project Resources and Document Deliverables.

## Dependencies

- **Depends on** names tasks whose output this task reads or builds on. Informational; does not block start.
- **Blocked by** names tasks that must be in `done/` before this task can start.

## Evidence gate

If `required`, name the concrete before-and-after acceptance evidence (test target, fixture check, validation run, fact-check, approval checklist, inspection, rehearsal, or similar) that demonstrates the outcome. If `n/a`, say why.

## Risk observations

Every new task records all five observable conditions below. Use one state from `protocol/RISK_AND_PRACTICE.md` and a bounded supporting fact identity for each; do not infer a favorable state from missing information. An observation that cannot be established uses `unknown`. Classification is performed by `cartopian classify-risk`, not by averaging prose.

- consequence-reach: <local-artifact | project-internal | external-or-material | broad-or-binding | unknown>; Fact: <task or current-state fact>
- reversibility: <direct-undo | bounded-recovery | recovery-dependent | unrecoverable | unknown>; Fact: <recovery observation>
- authority: <covered | new-commitment | unconfirmed | absent | unknown>; Fact: <authority or commitment observation>
- ambiguity: <confirmed | stated-assumption | material-assumption | contradictory | unknown>; Fact: <input, exclusion, or success-condition observation>
- evidence-coverage: <deterministic | direct-observation | indirect-or-qualitative | unavailable | unknown>; Fact: <decisive evidence observation>

## Judgment envelope

Declare only the two observable facts used by `cartopian select-judgment-guidance`. A card activates only when this task crosses its lifecycle boundary **and** names its non-enforceable failure as still open; crossing a boundary alone activates nothing, and an open failure outside its own boundary activates nothing. A failure that a deterministic guard already decides is not open — prefer the guard. The risk band and the selected pack are not inputs and are rejected if supplied. Use `none` for an empty list.

- lifecycle-boundaries: <requirements-and-intent | evidence-and-review-gate | migration-install-restart | delivery-and-closeout, ... | none>
- open-failure-conditions: <inferred-intent-not-confirmed | evidence-self-certified-or-missing | mixed-version-or-unproven-running-state | artifact-mistaken-for-outcome, ... | none>

## Practice-pack envelope

Declare only observable task facts used by `cartopian select-practice-pack`; do not infer a primary outcome from prose, filenames, project history, or Cartopian's own lifecycle activity. Use `none` for an empty list. More than one primary outcome can produce a fail-closed ambiguity. An authorized profile hint may resolve only an otherwise eligible collision and cannot override an exclusion. Declared domain scopes decide only which conditional sources apply to whichever pack is selected; they never select, veto, or change a pack, and an undeclared scope yields no authority rather than a default one.

- primary-outcomes: <stable outcome identity, ... | none>
- artifact-kinds: <stable artifact or state identity, ... | none>
- incidental-terms: <stable subject identity, ... | none>
- exclusions: <stable guidance or outcome identity, ... | none>
- lifecycle-substrate-activities: <task-directory-movement | handoff-dispatch | review-routing | state-file-refresh | pm-cleanup | none>
- domain-scopes: <declared jurisdiction, platform, artifact, or method scope, ... | none>
- authorized-profile-hint: <software | research | marketing | operations | policy | pack identity | none>

## Source guidance

Use this existing task/spec contract when the outcome depends on external or internal source authority. Set the header to `task` when this task owns the record, `spec` when the named spec owns it, or `n/a` when source authority is not material to the work. A missing header remains readable only for legacy tasks; new tasks choose explicitly.

When the header is `task`, keep all three subsections below. When it is `spec`, omit this section and keep the same record only in the named spec. When it is `n/a`, omit this section.

### Authoritative sources

- Identity: <stable source title or locator>; Applicable context: <effective date, publication date, edition, revision, or version>; Status: <current | stale | unknown>; Scope: <claims or decisions governed>

### Conflict resolution

- Status: <none | resolved | unresolved>; Rule: <precedence rule or named decision authority>; Decision: <applied resolution | n/a>

### Unverified claims

- none

Or record each remaining claim using the shared failure-signal shape:

- Claim: <unverified claim>; Decisiveness: <decisive | non-decisive>; Missing: <authority or evidence>; Consequence: <consequence of proceeding>; Next: <decision or proof required>

Missing authority, a stale/unknown applicable context, an unresolved conflict, or a decisive unverified claim fails readiness. Favorable source observations do not offset one of those conditions.

## Acceptance

- [ ] Checkable, specific, boolean-verifiable things.
- [ ] Each item should be something an independent observer can mark true or false.

## Upstream trace

Keep this section only when `Upstream trace: required`. It is the PM-derived record set that binds every material acceptance criterion to the upstream authority that governs it. Omit both the header and this section for legacy tasks; a task that declares `n/a` must not carry this section.

**What is material.** The material set is the union of two enumerations and nothing else: the governing specification's `## Examples / acceptance` list and this task's `## Acceptance` checklist. `## Constraints` bullets, background prose, non-acceptance examples, and unrelated history are not material, even when they name an observable outcome. Ordinals `C01`, `C02`, … are assigned by position — specification items in document order, then task items in document order, with merged-away origins omitted — so two producers reading the same task and specification derive the same ordinals.

**The record block.** One fenced block; every line is a record and prose outside the fence is never parsed as one. Records must appear in the contract's total sort order, and byte-identical records collapse silently.

```trace
C01|<digest12>|<requirement|standard|plan-item|decision|spec|operator-request>|<source-identity>|<applicable-context>|<occurrence>
C02|<digest12>|none:<derived-mechanical|template-fixed|restates-parent>|-|-|1
X|C03|<precedence|narrowing|amendment>|<why these same-class sources do not fight>
O|C04|<merged-away task-acceptance origin's digest12>
W|<identity>|<procedural-authorization|background-scope>|<scope statement>
```

- `digest12` is the first 12 hex characters of `sha256(normalized criterion text)` — NFC, ends stripped, internal whitespace runs collapsed to one space, no trailing newline. An edited criterion no longer matches its recorded digest, which is the point.
- `source-identity` and `applicable-context` are copied **verbatim** from the resolved source guidance. A `spec` edge names `spec-clause sha256:<64 hex>`; an `operator-request` edge names `REQ-<evidence order> sha256:<content identity>`. Neither ever names a path.
- A criterion carries **either** at least one typed edge **or** exactly one exemption — never both, and never two exemptions with different reasons.
- Two or more edges naming distinct source identities of the same precedence class (`behavior`: requirement/standard; `boundary`: plan-item/decision; `contract`: spec; `intent`: operator-request) require an `X|` disposition. Cross-class pairs need none.
- An `O|` merge may name only a task-acceptance origin, only against a criterion whose governing text is a specification-acceptance item, only once per origin, and never onto an exempt criterion. It costs 19 B against the 242 B of carrying the origin as its own criterion.
- A `W|` waiver requires attributable operator authority for that exact identity and class. Neither the PM nor the assignee may grant one.
- Coverage records (`S|`, `R|`), the trace identity, and both projections are **derived**, never authored here. Run `cartopian acceptance-trace <root> --task <path>` to validate the block and read the measured bodies.

Structural errors — a missing record, an unparseable one, a drifted digest, an unknown type or waiver class, a self-referencing spec clause, an exemption conflict, or a routine body over its bound — fail `validate-task-readiness` and never reach a coder. The two closure determinations are the reviewer's and are recorded at closure, not here.

## References

- `IMPLEMENTATION_PLAN.md` section(s) by heading.
- The matching `phases/PHASE-NN.md` roll-up row.
- Prior specs or tasks this depends on.

## Notes

Anything a future reader or reviewer would thank you for.
