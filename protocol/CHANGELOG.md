# Cartopian Protocol Changelog

This file is the durable, agent-followable record of every
protocol-breaking change to the Cartopian protocol. Each entry
below the header documents one breaking change and the concrete
migration steps required to bring an older project up to the new
protocol version.

Operators read this file (and follow its migration steps);
authoring is owned by maintainers and ships with each release.
The installed copy lives at `~/.cartopian/CHANGELOG.md` and is
replaced on upgrade.

## Per-entry schema

Every entry below the header is a self-contained migration
contract. An entry shall include each of the following fields:

- **Protocol version** — a monotonically-increasing identifier
  (e.g., `v0.2.0` or a date stamp) that names the version the
  entry advances the protocol to. The entry advances projects
  from the prior version to this one.
- **One-line summary** — a single-sentence description of the
  change suitable for a release-note bullet.
- **Breakage description** — what stopped working in older
  projects: which files, fields, or behaviors are no longer
  valid under the new protocol version.
- **Applies-when precondition** — an explicit, agent-evaluable
  rule for deciding whether this entry applies to a given
  project (e.g., "applies when the project's
  `protocol_version` is less than this entry's version").
- **Agent-followable migration steps** — the concrete file and
  text changes an agent performs to bring the project into
  conformance. Steps must be specific enough that a competent
  agent can execute them without further clarification.
- **Idempotence guarantee** — the migration steps shall be
  idempotent: re-applying them to an already-migrated project
  must be a no-op. The post-migration validation hint (below)
  is the safety check used before and after each application.
- **Post-migration validation hint** — how the agent confirms
  the migration succeeded (e.g., a grep, a file check, a
  command to run).

## Prepend-only rule

New entries are inserted at the **top** of the entries section
below, immediately under the `## Entries` heading. Existing
entries are never edited, reordered, or removed after they
land. This makes the file a stable, append-only history that
older projects can rely on for migration.

## `[project] protocol_version` marker

Every Cartopian project's `cartopian.toml` carries a
`[project] protocol_version` field. The applies-when
precondition on each entry is evaluated against this marker
to decide applicability during rollup or in-place migration.

- Projects updated in-place during a cycle bump the marker as
  each applicable entry is applied.
- Projects rolled up after a cycle bump the marker once, to
  the latest entry's version, after every applicable entry
  has been applied successfully.
- Newly-scaffolded projects start at the current protocol
  version (the version of the topmost entry below).

## Entries

### v0.2.0 — Domain-neutral vocabulary rewrite and `[roles]` reshape

- **Protocol version:** `v0.2.0`
- **One-line summary:** Renames `ENGINEERING.md` to `STANDARDS.md`,
  swaps the `Test gate:` task-file header for `Evidence gate:`,
  reshapes `[roles]` from kind values to a flat name → description
  table, and shrinks the protocol-default role roster to `pm` and
  `operator`.

#### Breakage description

Older projects no longer match the new protocol surface in three
specific tool-surface places. Body prose inside project artifacts
(plan narratives, decision rationale, review notes, etc.) that
mentions the old filename or header in passing is **not** part of
the breakage contract and is out of FR-001 scope; only the
load-bearing tool surfaces below break.

1. The project-root `ENGINEERING.md` filename is retired in favor
   of `STANDARDS.md`. Skills and templates that import the
   standards file by name read `STANDARDS.md` only; a project that
   still ships an `ENGINEERING.md` file at its root is not
   discoverable to the new surfaces.
2. The `Test gate:` task-file header is retired in favor of
   `Evidence gate:`. Field values (`required` / `n/a`) keep their
   semantics verbatim; only the field name changes. Readiness
   parsing keys on the line-anchored `Test gate:` /
   `Evidence gate:` header in task and review files; tasks that
   still carry the old header at the line anchor fail readiness
   checks under the new protocol.
3. The `[roles]` table no longer carries kind values
   (`pm = "agent"`, `operator = "human"`, `coder = "agent"`,
   `reviewer = "agent"`). It is now a flat name → description map.
   The `human` / `agent` / `none` / `""` kind set is removed from
   the protocol; dispatch path is inferred from the presence or
   absence of a `[handoffs.<role>]` block. Projects whose
   `cartopian.toml` carries kind values silently lose dispatch
   metadata and need description strings instead.

The protocol-default role roster also shrinks from
`pm` / `operator` / `coder` / `reviewer` to `pm` / `operator` only;
`coder` and `reviewer` are now common example labels operators
opt-in to, not defaults.

The protocol default for `[defaults] git_versioning` is **`false`**.
Source attribution: the explicit `git_versioning = false` value in
the repo-root `cartopian.toml` shipped with this protocol (Stage 1.5
round-14 carry-forward). Projects opt in to git versioning by
setting `git_versioning = true` in their own config; this default
does not change with this entry but is stated explicitly here so its
provenance is unambiguous.

#### Applies-when precondition

Applies when the project's `cartopian.toml` `[project] protocol_version`
is unset, missing, or lexically less than `v0.2.0`. Projects already at
`v0.2.0` (or any later entry's version) are skipped.

#### Agent-followable migration steps

Run these against the project root in order. Each step is idempotent;
re-applying a step that has already been applied is a no-op.

1. **Rename the standards file.** If
   `<project-root>/ENGINEERING.md` exists, rename it to
   `<project-root>/STANDARDS.md`. If `STANDARDS.md` already exists
   and `ENGINEERING.md` does not, no action.
2. **Swap the evidence-gate header.** In every file under
   `<project-root>/tasks/` and `<project-root>/reviews/`, replace
   the line-anchored header `Test gate:` with `Evidence gate:`
   (semantics of values `required` / `n/a` unchanged). Leave body
   prose mentions inside completed task files untouched if they are
   not the header field. Files that already carry `Evidence gate:`
   are skipped.
3. **Reshape `[roles]` if present.** If
   `<project-root>/cartopian.toml` declares a `[roles]` table whose
   values are kind strings (`"agent"`, `"human"`, `"none"`, or
   `""`), rewrite each entry to a one-line description string in
   the form `<role-name> = "<description>"`. Suggested descriptions:
   - `pm = "Plans phases, dispatches handoffs, integrates results."`
   - `operator = "Approves locks, unblocks, sets cadence."`
   - `coder = "Implements tasks per spec."`
   - `reviewer = "Reviews per acceptance evidence."`

   Remove any `kind` keys. If the table's values are already
   description strings, no action. If the project has no `[roles]`
   table (it inherits from a workspace or global config), no action
   here.
4. **Set the protocol-version marker.** In
   `<project-root>/cartopian.toml`, set or update
   `[project] protocol_version = "v0.2.0"`. Insert into the
   `[project]` table if absent. If the marker already equals
   `"v0.2.0"` (or a later version), no action.

#### Idempotence guarantee

Re-applying these steps to an already-migrated project is a no-op:
the rename precondition checks for `ENGINEERING.md`'s existence,
so once renamed the step is skipped; already-swapped headers stay
swapped (matched by exact line-anchored string, so re-application
finds nothing left to swap); reshaped `[roles]` tables stay
reshaped (values are already strings, so no rewrite triggers);
and `protocol_version` is set to exactly `v0.2.0` regardless of
how many times the step is applied.

#### Post-migration validation hint

After migration, run the following checks against the project root.
Each check matches exactly one migration step; an agent that
followed the migration steps successfully will see every check
pass. The checks are scoped to the tool surfaces the migration
changes — body prose elsewhere in the project is intentionally
out of scope.

```sh
PROJECT_ROOT=<project-root>

# 1. Rename succeeded: ENGINEERING.md no longer exists at the
#    project root.
test ! -e "$PROJECT_ROOT/ENGINEERING.md"
# expected: exit status 0

# 2. New standards file exists at the project root.
test -f "$PROJECT_ROOT/STANDARDS.md"
# expected: exit status 0

# 3. No retired Test gate: header remains at the line anchor in
#    tasks/ or reviews/. Body prose mentions elsewhere are not
#    flagged.
grep -RIln '^Test gate:' "$PROJECT_ROOT/tasks/" "$PROJECT_ROOT/reviews/"
# expected: no matches (grep exits non-zero)

# 4. protocol_version marker is v0.2.0 (or a later entry's
#    version) in the project cartopian.toml.
grep -E '^protocol_version *= *"v0\.2\.0"' \
  "$PROJECT_ROOT/cartopian.toml"
# expected: one match (or a later version line if a later entry
# has been applied on top)
```

A project is conformant when all four checks pass and the
project's `[roles]` table (if present) carries description
strings, not kind values.
