# Deterministic evaluations

Run the canonical free evaluation set from the repository root:

```sh
python3 -m evaluations
python3 -m evaluations --format json
```

Use repeatable `--category NAME` and `--case IDENTIFIER` filters for focused
runs. Filters are applied after the complete case set is validated and preserve
canonical identifier order. A matched run exits 0, an outcome mismatch exits 1,
and invalid cases or filters exit 2.

Each JSON file directly under `evaluations/cases/` is one case. Its closed
schema requires `identifier`, `category`, `input`, `expected_outcome`, and
`rationale`; `measurement_boundary` is optional. `input` contains exactly one
of `fixture` (a repository-relative file below `evaluations/fixtures/`) or
`value` (small declarative JSON). An expected failure may name a
`diagnostic_class`:

```json
{
  "identifier": "structural-example",
  "category": "structural",
  "input": {"fixture": "evaluations/fixtures/example.json"},
  "expected_outcome": {
    "outcome": "fail",
    "diagnostic_class": "example_mismatch"
  },
  "rationale": "Protects one deterministic behavior."
}
```

The shipped domain categories are:

- `structural`: the original text-match seam plus the
  `skill-metadata-surfaces` repository check. The repository check consumes the
  authoritative metadata validator and verifies identifiers, required fields,
  runbook references, client templates, the single `use_cartopian` entry
  command, and MCP prompt/resource projection parity.
- `routing`: realistic operator utterances with a closed `expectation` of
  `selection`, `none`, or `collision`. Optional `candidates` bound a deliberate
  collision probe; collision candidates must be canonical and explicit.
- `context-size`: the exact UTF-8 byte count of the labeled compact routing
  projection compared with a labeled baseline in
  `measurement_boundary.max_input_bytes`.

Routing is a regression probe over `skills/skill-metadata.json`, not a
production router. It normalizes words, scores overlap against the
authoritative description and applicability fields, requires at least two
shared meaningful words, and treats candidates within 75 percent of the best
score as a collision. The only deterministic early resolution is an explicit
entry phrase belonging to a metadata record with declared client bridges;
today that is `use_cartopian`. Results never depend on filesystem or candidate
iteration order.

Routing fixtures use this closed shape:

```json
{
  "utterance": "Please use Cartopian to resume the project session.",
  "expectation": {"type": "selection", "skill": "use_cartopian"},
  "candidates": null,
  "rationale": "Protects the single conversational entry."
}
```

A collision uses `{"type": "collision", "skills": ["first", "second"]}` and
the same canonical list in `candidates`. A negative uses `{"type": "none"}`.
Unknown fields, expectation types, skills, unordered candidates, and empty
utterances fail validation.

The context-size case names `compact-skill-routing-metadata-v1`: canonical JSON
containing only each skill's `identity`, `description`, and `applicability`.
The canonical case compares exact UTF-8 bytes with the established,
canonicalized Phase 00 `use_cartopian` resource baseline. Token precision is
not claimed. An increase fails unless the fixture carries both a positive byte
allowance and a non-empty justification.

To extend the runner, implement the two-method evaluator contract in
`evaluations/categories.py` and add it to `default_registry()` under a stable
label. Category validation should close its input and fixture schemas;
evaluation should return only normalized outcomes and safe, deterministic
diagnostics. Renderers consume normalized records and require no
category-specific changes.
