# PM Action Gap Register (FR-004)

**Plan ref:** P00-RESEARCH-002 · **Requirement:** FR-004 · **Feeds:** FR-005 (P01-BUILD-003), OQ-002
**Audit scope (tool-repo work root `/Users/scott/Projects/cartopian`):** `skills/`, `cli/`, `mcp_server/`
**Date:** 2026-05-31

This register inventories **every PM-reachable action that today requires a raw write, directory operation, or process exec**, classifies each as **critical-path-for-V1** or **deferrable**, and names the mediated Cartopian-command replacement FR-005 must build (critical) or defer. Per DEC-001 the PM is contained to the fixed Cartopian MCP toolset — no shell, no raw `Write`/`Edit`, no product-repo/work-root reach — so any lifecycle step relying on a raw operation must have a mediated replacement or be explicitly deferred. An omitted gap becomes a PM deadlock the moment containment removes raw capability; this register therefore **prefers over-inclusion** — uncertain items are listed `deferrable` with a note rather than dropped.

The register is the auditable definition of "complete" for FR-005: a contained PM can run the full V1 lifecycle (**plan → assign → review → close**) with no step that requires a tool it lacks **iff** every critical-path-for-V1 row below has a first-class Cartopian command.

---

## 1. What counts as a "PM-reachable raw operation"

A row exists for an operation only when **the PM itself is instructed to perform it** as part of a lifecycle skill (`skills/*.md` the contained PM executes). Two categories are therefore **not** gaps and are excluded in §4:

- **Tool-layer internal operations** — raw writes/dir-ops/exec inside `cli/` and `mcp_server/`. These *are* the mediation: per FR-003, "Cartopian's own tool layer is the sole writer." When the PM calls `cartopian move-task`, the `mkdir`/rename happens inside the trusted CLI, not from a PM-issued shell. The PM reaches these only through the fixed tool surface, which is exactly the contained state.
- **Operator / install-time skills** — `register-mcp.md`, `check-for-updates.md`, `init-workspace.md`. These run during install/registration by the operator, write to user-config paths outside the project surface (`~/.claude`, `~/.codex`, `~/.gemini`, `~/.config/devin`), and are not part of the contained PM's plan→assign→review→close runtime. They are listed once as **G23 (deferrable / out-of-contained-PM-scope)** for completeness, not as a lifecycle deadlock.

---

## 2. The register (fixed schema)

Schema per row: `{ lifecycle step · current raw capability · critical-path-for-V1 | deferrable · replacement Cartopian command · evidence/test pointer · deferral target }`

### 2.1 Critical-path-for-V1 (must close in FR-005 — the minimum lifecycle set)

| # | Lifecycle step | Current raw capability | Class | Replacement Cartopian command | Evidence pointer | Deferral target |
|---|---|---|---|---|---|---|
| G1 | Plan / adopt-requirements: author `REQUIREMENTS.md` | raw `Write` | **critical** | structured `write-requirements` (mediated writer, allowlisted dest) | `skills/plan-project.md:90`, `skills/adopt-requirements.md:94` | n/a |
| G2 | Plan / adopt-plan: author `IMPLEMENTATION_PLAN.md` | raw `Write` | **critical** | structured `write-plan` | `skills/plan-project.md:119`, `skills/adopt-plan.md:101` | n/a |
| G3 | Plan / adopt-requirements: author/update `STANDARDS.md` | raw `Write`/`Edit` | **critical** | structured `write-standards` | `skills/plan-project.md:94`, `skills/adopt-requirements.md:123` | n/a |
| G4 | Plan / adopt-plan: author phase files `phases/PHASE-NN-slug.md` | raw `Write` | **critical** | structured `write-phase` | `skills/plan-project.md:152`, `skills/adopt-plan.md:131` | n/a |
| G5 | Plan / adopt-plan: author task files `tasks/open/TASK-NN-NNN-slug.md` | raw `Write` | **critical** | structured `write-task` | `skills/plan-project.md:183`, `skills/adopt-plan.md:150` | n/a |
| G6 | Plan / adopt-plan: author spec files `specs/SPEC-NN-NNN-slug.md` | raw `Write` | **critical** | structured `write-spec` | `skills/plan-project.md:189`, `skills/adopt-plan.md:160` | n/a |
| G7 | Assign / review / planning-checkpoint: author or overwrite prompt files `prompts/PROMPT-NN-NNN.md` & `prompts/PROMPT-PLAN-NNN-slug.md` | raw `Write`/`Edit` | **critical** | structured `write-prompt` | `skills/run-task.md:78-96`, `skills/run-task.md:191`; `skills/run-handoff.md:65`; `skills/plan-project.md:245`,`:232` | n/a |
| G8 | Review/planning `request-changes`: revise target artifacts in place (REQUIREMENTS / STANDARDS / IMPLEMENTATION_PLAN / phases / tasks / specs) | raw `Edit` | **critical** | re-issue of the matching structured writer (G1–G6) | `skills/plan-project.md:103`,`:139`,`:170`,`:200`; `skills/run-task.md` Stage 6 | n/a |
| G9 | Run-task Stage 7: record decisions `decisions/DEC-NNN-slug.md` | raw `Write` | **critical** | structured `write-decision` | `skills/run-task.md:272` | n/a |
| G10 | Run-task Stage 7: update `decisions/INDEX.md` | raw `Edit` | **critical** | structured `write-decision` index update (or folded into G9) | `skills/run-task.md:273` | n/a |
| G11 | Session state: author/refresh `STATE.md` — persist `compose-state` `rendered_body`, author no-plan STATE, record blockers | raw `Write`/`Edit` | **critical** | `write-state` (consumes the existing `compose-state` body; needs a mediated *writer* to persist it) | `skills/run-task.md:297`,`:156`; `skills/plan-project.md:209`; `skills/close-plan.md:259`; `skills/start-session.md:62` | n/a |
| G13 | Close Stage 4.1: remove live artifacts — `REQUIREMENTS.md`, `IMPLEMENTATION_PLAN.md`, all files in `phases/`, `tasks/{open,in-progress,in-review,done}/`, `specs/`, `reviews/`, `decisions/` | raw `rm` / `find` | **critical** | `reset-plan` (mediated surface reset; prompts/reports already mediated via `delete-prompt`/`delete-report`) | `skills/close-plan.md:185-198` | n/a |
| G14 | Close Stage 4.1: recreate empty lifecycle directories (`phases/`, `prompts/`, `reports/`, `tasks/*`, `specs/`, `reviews/`, `decisions/`) | raw `mkdir` | **critical** | folded into `reset-plan` (directory primitive — **OQ-003**) | `skills/close-plan.md:200-213` | n/a |
| G15 | Close Stage 4.2/4.3: replace `STANDARDS.md` / `CONVENTIONS.md` with fresh seed (conditional on carry-forward) | raw `Write` | **critical** | `reset-plan` carry-forward flags + structured seed-write (G3 / write-conventions) | `skills/close-plan.md:221`,`:227` | n/a |
| G20 | Handoff dispatch (Stage 2): launch assignee/reviewer CLI as a background subprocess | raw process exec (`subprocess`/wrapper launch) | **critical** | `dispatch` (FR-006 mediated dispatch; PM observes via existing `wait-handoff`/`wait-report`) | `skills/run-handoff.md:91`,`:99`,`:103`; `skills/run-task.md:108-116` | n/a |

**Critical-path-for-V1 set:** **G1, G2, G3, G4, G5, G6, G7, G8, G9, G10, G11, G13, G14, G15, G20** (15 rows). Closing exactly these gives the contained PM a complete plan→assign→review→close lifecycle with no missing-tool deadlock. Directory ops (G14) and seed replacement (G15) are folded into `reset-plan`; their mediation shape is **OQ-003** (separate, owned by FR-005 design).

### 2.2 Deferrable (recorded, not closed in FR-005 — never silently dropped)

| # | Lifecycle step | Current raw capability | Class | Replacement Cartopian command | Evidence pointer | Deferral target |
|---|---|---|---|---|---|---|
| G12 | Run-task Stage 6: append `Merge commit SHA` / `PR URL` into `reviews/REVIEW-NN-NNN.md` evidence block (PM edit of reviewer artifact) | raw `Edit` | deferrable | mediated review-evidence-append, or folded into mediated-git | `skills/run-task.md:253` | **FR-013** — fires only when `git.pm_owns_product_branches = true`; unsupported combo fails closed until mediated-git exists |
| G16 | Close Stage 3.1: create `archive/` and `archive/PLAN-NNN-slug/` directories | raw `mkdir` | deferrable | `archive-plan` (directory primitive — **OQ-003**) | `skills/close-plan.md:120` | named **archive follow-up** cycle — archive is operator-optional ("skip unless requested"), not on the minimum close path |
| G17 | Close Stage 3.2: write `archive/PLAN-NNN-slug/CLOSEOUT.md` | raw `Write` | deferrable | `archive-plan` | `skills/close-plan.md:134` | archive follow-up |
| G18 | Close Stage 3.3: copy live artifacts (`REQUIREMENTS.md`, `STANDARDS.md`, `CONVENTIONS.md`, `IMPLEMENTATION_PLAN.md`, `STATE.md`, `phases/`, `tasks/`, `specs/`, `reviews/`, `reports/`, `decisions/`) into archive dir | raw `cp` / recursive `cp -r` | deferrable | `archive-plan` | `skills/close-plan.md:147-159` | archive follow-up |
| G19 | Close Stage 3.4: create or append `archive/INDEX.md` | raw `Write`/`Edit` | deferrable | `archive-plan` | `skills/close-plan.md:165` | archive follow-up |
| G21 | Run-task Stage 4/6: product-repo git plumbing — branch create, stage, commit, push, `gh pr create`, `gh pr merge` | raw process exec (`git`/`gh`) | deferrable | mediated-git commands **or** delegated/human-owned | `skills/run-task.md:175-179`,`:253` | **FR-013** — `pm_owns_product_branches = true` + contained PM is an unsupported combo that fails closed until mediated-git lands |
| G22 | Run-task Stage 8: configured session-close git behavior for project PM data | raw process exec (`git`) | deferrable | mediated-git / human-owned | `skills/run-task.md:305` | **FR-013** |
| G23 | Install/registration (`register-mcp`, `check-for-updates`): `mkdir` + `cp` into `~/.claude`, `~/.codex`, `~/.gemini`, `~/.config/devin`; edit agent MCP config JSON/TOML | raw `mkdir`/`cp`/`Edit`/CLI exec | deferrable | none in contained-PM runtime | `skills/register-mcp.md:101-321`, `skills/check-for-updates.md:59` | **out of contained-PM scope** — operator/installer activity, outside the project surface; not part of plan→assign→review→close |

**Deferrable set:** G12, G16, G17, G18, G19, G21, G22, G23 (8 rows).

> **Note on `ROADMAP.md` / `BACKLOG.md`:** these appear in the FR-003 fixed write-allowlist but **no current skill instructs the PM to author them** (grep `ROADMAP|BACKLOG` over `skills/` → no matches). They are therefore reserved allowlisted destinations with no live raw-op today, so they get no gap row. If a future skill authors them, they fold into the generic mediated writer / a structured `write-roadmap`/`write-backlog` with zero new bypass surface.

---

## 3. Audit commands (reproducible evidence gate)

Run from the tool-repo root `/Users/scott/Projects/cartopian`. Layer 1 (CMD1–CMD2) enumerates raw ops in the **tool layer** (expected → all excluded as mediation). Layer 2 (CMD3–CMD6) enumerates **PM-instructed** raw ops in the lifecycle/install skills (→ gap rows).

```bash
# CMD1 — process exec in the tool layer
grep -rnE 'subprocess|os\.(exec|system|spawn)|Popen|check_call|check_output' cli mcp_server --include='*.py'

# CMD2 — file-write / dir-create sinks in the tool layer (excluding stderr/stdout diagnostics)
grep -rnE 'write_text|write_bytes|\.mkdir\(|shutil\.' cli mcp_server --include='*.py'

# CMD3 — raw shell-op tokens in skill fenced command blocks
grep -rnE '^\s*(cp|mv|rm|rmdir|mkdir|find|touch|ln)\b' skills --include='*.md'

# CMD4 — PM-instructed raw write / dir-op / copy / remove in lifecycle skills (line-leading imperatives)
grep -rniE '^\s*-?\s*(write|create|author|rewrite|replace|copy|remove|recreate|update|generate)\b.*(`[A-Z_]+\.md`|`?(REQUIREMENTS|IMPLEMENTATION_PLAN|STANDARDS|CONVENTIONS|STATE|ROADMAP|BACKLOG)|phases/|tasks/|specs/|reviews/|decisions/|prompts/|archive/|directories|live artifacts)' \
  skills/plan-project.md skills/adopt-plan.md skills/adopt-requirements.md skills/run-task.md skills/close-plan.md skills/run-handoff.md skills/start-session.md --include='*.md'

# CMD5 — PM-instructed process launch / git exec in lifecycle skills
grep -rniE 'subprocess|launch|background|gh pr|git (push|commit|branch|status|add)' skills/run-handoff.md skills/run-task.md --include='*.md'

# CMD6 — create/author of phase/task/spec/prompt/decision/review artifacts (mid-line imperatives)
grep -rniE '(create|write|update|overwrite|append|record).{0,30}(`?phases/PHASE|`?tasks/(open/)?TASK|`?specs/SPEC|`?prompts/PROMPT|`?reviews/REVIEW|`?decisions/(DEC|INDEX)|STANDARDS\.md|review file)' \
  skills/plan-project.md skills/adopt-plan.md skills/run-task.md --include='*.md'

# Reserved-destination check (expect: no matches → no live raw op)
grep -rnE 'ROADMAP|BACKLOG' skills --include='*.md'
```

**Acceptance evidence:** re-running CMD1–CMD6 yields zero PM-reachable raw operations absent from §2 — every hit below maps to a register row or an explicit §4 exclusion.

---

## 4. Line-by-line reconciliation (every audit hit → row or excluded-with-reason)

### 4.1 CMD1 — exec in `cli/` + `mcp_server/` → **all excluded (tool-layer internal)**

| Hit | Disposition |
|---|---|
| `cli/commands/plan_audit.py:4` `import subprocess` | Excluded — import for the line below. |
| `cli/commands/plan_audit.py:47` `subprocess.run([...git status --porcelain...])` | Excluded — **read-only** `git status` inside `cartopian plan-audit`; PM reaches it only via the mediated CLI tool, not a raw shell. |
| `cli/commands/plan_audit.py:54` `except (FileNotFoundError, TimeoutExpired)` | Excluded — error handling for the above. |
| `cli/main.py:61` `# pragma … exercised via subprocess` | Excluded — comment; no exec. |
| `mcp_server/server.py:38` `import subprocess` | Excluded — import for the line below. |
| `mcp_server/server.py:167` `subprocess.run([...git describe...])` | Excluded — **read-only** `git describe` for the version string; tool-layer internal. |
| `mcp_server/server.py:174` `except (OSError, SubprocessError)` | Excluded — error handling for the above. |

### 4.2 CMD2 — file-write / dir-create in `cli/` + `mcp_server/` → **all excluded (the mediation itself)**

| Hit | Disposition |
|---|---|
| `cli/commands/move_task.py:169` `dest.parent.mkdir(...)` | Excluded — internal to `cartopian move-task` (mediated lifecycle move; the sole-writer layer per FR-003). |
| `cli/commands/scaffold_project.py:92`,`:98` gitignore write | Excluded — internal to `cartopian scaffold-project` (mediated init). |
| `cli/commands/scaffold_project.py:103`,`:107`,`:154` `mkdir` | Excluded — mediated scaffold. |
| `cli/commands/scaffold_project.py:108` `target.write_text(seed)` | Excluded — mediated scaffold. |
| `cli/commands/generate_config.py:317` `config_path.write_text(...)` | Excluded — internal to `cartopian generate-config` (operator-driven init; config write is *not* a PM authoring action and is outside the FR-003 PM writer's surface by design). |
| `cli/commands/_registry.py:100` `mkdir`, `:103` `tmp.write_text(...)` | Excluded — internal to register/unregister/discover; the project registry lives outside the project surface and is managed only by the mediated CLI. |

**Class exclusion (diagnostics / streams):** every `sys.stderr.write(...)` (`register_project.py:27`, `plan_audit.py:31`, `move_task.py:32`, `scaffold_project.py:68`, `generate_config.py:32`, `unregister_project.py:26`, `discover_projects.py:21`, `delete_report.py:46`, `resolve_config.py:32`,`:276`, `list_tasks.py:34`, `validate_task_readiness.py:33`, `delete_prompt.py:29`, `main.py:47`,`:51`,`:55`), `cli/emit.py:19` `out.write` (NDJSON to stdout), `cli/_vendor/tomli_w.py:80` `fp.write` (in-memory TOML serialization), and `mcp_server/server.py` stream/protocol writes (`:92`,`:494`,`:505`,`:839`,`:951`,`:953`) are excluded — these are diagnostic stderr, stdout result emission, or MCP wire-protocol writes, **not artifact filesystem writes**. Docstring/comment hits (`unregister_project.py:4`, `handoff_packet.py:66`, `mcp_server/server.py:139`) are excluded as prose.

### 4.3 CMD3 — skill shell-op tokens → **G23**

| Hit | Disposition |
|---|---|
| `skills/register-mcp.md:101`,`:104`,`:141`,`:187`,`:320` `mkdir -p ~/...` | **G23** (deferrable / out-of-contained-PM-scope). |
| `skills/register-mcp.md:102`,`:105`,`:142`,`:188`,`:321` `cp "$install_root/..."` | **G23**. |

### 4.4 CMD4 + CMD6 — PM-instructed authoring / close ops → gap rows

| Hit | Row |
|---|---|
| `plan-project.md:90`, `adopt-requirements.md:94` Write `REQUIREMENTS.md` | **G1** |
| `plan-project.md:119`, `adopt-plan.md:101` Write `IMPLEMENTATION_PLAN.md` | **G2** |
| `plan-project.md:94`, `adopt-requirements.md:123` author/update `STANDARDS.md` | **G3** |
| `plan-project.md:152`, `adopt-plan.md:131` create `phases/PHASE-...` | **G4** |
| `plan-project.md:183`, `adopt-plan.md:150` create `tasks/open/TASK-...` | **G5** |
| `plan-project.md:189`, `adopt-plan.md:160` create `specs/SPEC-...` | **G6** |
| `plan-project.md:226`,`:232`,`:245`, `adopt-plan.md:187` create/prepare `prompts/PROMPT-...` | **G7** |
| `run-task.md:272` record `decisions/DEC-...` | **G9** |
| `run-task.md:273` update `decisions/INDEX.md` | **G10** |
| `plan-project.md:209`, `adopt-plan.md:168`, `close-plan.md:259` author/update/rewrite `STATE.md` | **G11** |
| `close-plan.md:120` create `archive/` | **G16** |
| `close-plan.md:134` create `archive/.../CLOSEOUT.md` | **G17** |
| `close-plan.md:147` copy live artifacts into archive | **G18** |
| `close-plan.md:165` create/append `archive/INDEX.md` | **G19** |
| `close-plan.md:185` remove live artifacts | **G13** |
| `close-plan.md:200` recreate directories | **G14** |
| `run-task.md:253` append merge SHA / PR URL into `reviews/REVIEW-...` | **G12** |
| `run-task.md:264` overwrite `reviews/REVIEW-NN-NNN.md` | Excluded — **reviewer-authored**, not a PM write (the reviewer role owns review files; the PM's only edit of a review file is the git-evidence append at `:253` → G12). |
| `run-task.md:209` "reviewers do not modify … records the finding in the review file" | Excluded — describes reviewer behavior, not a PM op. |
| `plan-project.md:252` "Require the reviewer to create `reviews/REVIEW-PLAN-...`" | Excluded — reviewer-authored. |

> `STANDARDS.md`/`CONVENTIONS.md` seed-replacement at `close-plan.md:221`,`:227` (caught by reading Stage 4.2/4.3, adjacent to the CMD4 `:200` hit) → **G15**. The conditional-reset wording sits just below the `Recreate the directories` block in the same Stage 4.

### 4.5 CMD5 — PM-instructed exec → gap rows

| Hit | Row |
|---|---|
| `run-handoff.md:91`,`:99`,`:103` launch configured executable / background subprocess | **G20** |
| `run-task.md:108-116` Assign Or Launch Work (delegates to run-handoff) | **G20** |
| `run-task.md:175-179` git branch/stage/commit/push, `gh pr create` | **G21** |
| `run-task.md:253` `gh pr merge` | **G21** (exec portion; the review-file append portion → G12) |
| `run-task.md:305` session-close git behavior | **G22** |

### 4.6 Reserved-destination check

`grep -rnE 'ROADMAP|BACKLOG' skills` → **no matches.** No live raw op; no row (see §2.2 note).

**Reconciliation result:** every CMD1–CMD6 hit is mapped to a register row (§2) or excluded with a stated reason (§4). Zero PM-reachable raw operations remain unaccounted.

---

## 5. OQ-002 recommendation — generic mediated-write vs structured-only

**OQ-002:** *For V1, is the generic validated mediated-write primitive exposed to the PM, or only structured per-artifact commands (with the generic primitive deferred)?*

**Recommendation: structured per-artifact commands only are exposed to the PM for V1. Build the generic validated writer as an internal tool-layer primitive, but do not expose it as a PM tool — defer PM-facing generic write to a named follow-up, opt-in and logged.**

**Rationale**

1. **It matches the thesis.** The containment thesis (REQUIREMENTS §Thesis) is *capability removal, not better rules*: "given the capability and an opportunity, the PM eventually defects." A PM-exposed generic write takes *(destination, content)* — the model chooses where to write. That is precisely the broad capability containment exists to remove. Structured commands let the PM choose *which artifact* (`write-task`, `write-state`, …); the **destination is implied by the command**, not supplied by the model. Smaller decision surface, smaller bypass surface.
2. **It matches FR-003's design stance.** FR-003 deliberately favors "a small, enumerable allowlist over a rule-and-exception engine — fewer rules, fewer bypass vectors, easier to audit." The set of PM-authored destinations is small, closed, and enumerable (the FR-003 allowlist). Each maps 1:1 to a structured command (G1–G11, G13–G15). A generic write verb re-introduces the rule-and-exception ergonomics FR-003 rejects at the *authoring* layer.
3. **No capability is lost.** The generic validated writer (real-path resolution, `O_NOFOLLOW`, fixed-allowlist enforcement, fail-closed) is still built — it is the **shared internal implementation** every structured command calls. FR-003's "Cartopian's own tool layer is the sole writer" is satisfied without exposing a generic write *tool* to the PM. The structured commands are thin typed front-ends over one audited writer.
4. **Auditability.** "FR-005 is complete" becomes "every critical-path row has its structured command," which is mechanically checkable against this register. A generic primitive makes completeness interpretive ("could the PM have written that?") rather than enumerable.
5. **Cost / mitigation.** The cost of structured-only is more commands and friction for a genuinely new artifact type (needs a new command). Mitigation: the closed allowlist means new types are rare; the deferrable archive/index rows (G17, G19) and any future doc type can be added incrementally, and the internal generic writer can be *promoted* to a PM-exposed tool later **if** a real need appears — opt-in, logged, never the default, with its own follow-up cycle as the deferral target.

**Consequence for FR-004/FR-005 classification:** all critical-path replacements in §2.1 are **structured commands** (`write-requirements`, `write-plan`, `write-standards`, `write-phase`, `write-task`, `write-spec`, `write-prompt`, `write-decision`, `write-state`, plus `reset-plan` for the close-surface ops). The directory-op shaping for `reset-plan`/`archive-plan` (G14, G16) is the open design question **OQ-003**, owned by FR-005 design — this register assumes those raw dir ops fold into the structured `reset-plan`/`archive-plan` commands rather than a separately-exposed generic directory primitive, consistent with the structured-only recommendation above.

---

## 6. Summary

- **23 gap rows.** Critical-path-for-V1: **15** (G1–G11, G13–G15, G20). Deferrable: **8** (G12, G16–G19, G21–G23).
- Closing the 15 critical rows gives a contained PM a deadlock-free plan→assign→review→close lifecycle.
- Deferrable rows split into: **archive** (G16–G19 → archive follow-up), **product-repo git** (G12, G21, G22 → FR-013), and **install/registration** (G23 → out of contained-PM scope).
- **OQ-002 resolved:** expose structured per-artifact commands only; keep the generic validated writer internal and defer PM-facing generic write.
- **OQ-003 flagged** (not owned here): directory ops (G14, G16) fold into structured `reset-plan`/`archive-plan`; final shaping is FR-005 design.
</content>
</invoke>
