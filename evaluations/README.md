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

To extend the runner, implement the two-method evaluator contract in
`evaluations/categories.py` and add it to `default_registry()` under a stable
label such as `routing`, `migration`, `privacy`, or
`context-output-budget`. Category validation should close its input and fixture
schemas; evaluation should return only normalized outcomes and safe,
deterministic diagnostics. Renderers consume normalized records and require no
category-specific changes.
