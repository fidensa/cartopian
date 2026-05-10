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

_No entries yet. The first entry lands with the cycle's first
protocol-breaking change._
