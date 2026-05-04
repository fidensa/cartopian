# TASK-NN-NNN: <title>

Plan ref: PNN-KIND-NNN
Repo subpath: <subpath>
Branch: <branch or n/a>
Spec: <SPEC-NN-NNN-slug.md or none>

## Paths

- **Prompt path**: <absolute path to this prompt file>
- **Project root**: <absolute path to the project directory>
- **Repo path**: <absolute path to the target repository>
- **Task path**: <absolute path to the task file>
- **Spec path**: <absolute path to the spec file, or n/a>
- **Report path**: <absolute path to the expected completion report>
- **Review path**: <absolute path to the expected review file, if applicable>
- **Report template path**: <absolute path to templates/REPORT.md>

## Your task
<Imperative, directed at the assignee.>

## Context
<Self-contained. No "go read the PM system." All referenced file paths
must be absolute.>

## What to produce
<File paths, interfaces, test targets.>

## Test gate
<Red before code, or n/a with reason.>

## What not to do
<Scope boundaries. Non-goals.>

- Do not move Cartopian task files between status directories.
- Do not delete prompt files.
- Do not rewrite `STATE.md` or perform PM lifecycle cleanup.

## Done criteria
<Checkable. Boolean-verifiable.>

## Completion report

When you are done, write a completion report to the report path listed
above. Use the report template at the report template path listed above.

Use the task handoff, review handoff, or planning-review handoff variant
from `templates/REPORT.md`, matching the type of work this prompt
assigns.

**Redact secrets.** Do not include API keys, credentials, tokens, private
connection strings, or comparable sensitive values in the report.
