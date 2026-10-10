"""`review-context --prompt` is a preflight, so its default record is compact.

Evidence gate (red-before-green):

- RED: the runbooks call ``review-context --prompt`` only to read
  ``preflight.ok``, yet every call returned the full projection -- each
  operator-request record verbatim, the delivery-contract section, and every
  decision-proximity pair. On a mature project that was ~100 KB, past the MCP
  host's tool-result limit, so the preflight verdict was only reachable by
  spilling the result to a file.
- GREEN: with ``--prompt`` the record keeps the verdict, every identity, and
  each record's size, drops the bodies, and names them in ``omitted_fields``.
  ``--full`` restores the complete projection, and a call without
  ``--prompt`` is unchanged.
"""
from __future__ import annotations

import unittest

from tests.cli.test_generated_artifact_parity import _invoke
from tests.cli.test_review_prompt_skeletons import _PlanningFixture
from cli.commands import review_context
from tests.test_review_bootstrap_parity import BootstrapFixture

_BODIES = ("text", "context", "source")


class PlanningPreflightProjectionTests(_PlanningFixture):
    def run_context(self, **overrides):
        args = dict(
            project_root=str(self.root), review_kind="planning", task=None,
            checkpoint=self.checkpoint, phase=None, plan_ref=None,
            prompt=str(self.prompt),
        )
        args.update(overrides)
        code, records, err = _invoke(review_context.handler, **args)
        self.assertEqual(code, 0, err)
        return records[0]

    def test_preflight_is_compact_and_keeps_every_identity(self) -> None:
        self.write()
        compact = self.run_context()
        full = self.run_context(full=True)

        self.assertEqual(compact["projection"], "compact")
        self.assertEqual(full["projection"], "full")
        self.assertNotIn("omitted_fields", full)
        self.assertIn("request_trace.records[].text/context/source", compact["omitted_fields"])
        # The verdict and every identity it was computed from are unchanged.
        self.assertTrue(compact["preflight"]["ok"])
        self.assertEqual(compact["preflight"], full["preflight"])
        self.assertEqual(compact["context_identity"], full["context_identity"])
        self.assertEqual(compact["measures"], full["measures"])

        full_records = full["request_trace"]["records"]
        compact_records = compact["request_trace"]["records"]
        self.assertTrue(full_records)
        self.assertEqual(len(compact_records), len(full_records))
        for slim, whole in zip(compact_records, full_records):
            for key in _BODIES:
                self.assertNotIn(key, slim)
            self.assertEqual(slim["record_id"], whole["record_id"])
            self.assertEqual(slim["content_identity"], whole["content_identity"])
            self.assertEqual(slim["text_bytes"], len(whole["text"].encode("utf-8")))

        delivery = compact["delivery_contract"]
        if isinstance(full["delivery_contract"].get("section"), str):
            self.assertNotIn("section", delivery)
            self.assertEqual(
                delivery["section_bytes"],
                len(full["delivery_contract"]["section"].encode("utf-8")),
            )
        self.assertNotIn("pairs", compact["decision_proximity"])
        self.assertEqual(
            compact["decision_proximity"]["pair_count"],
            len(full["decision_proximity"]["pairs"]),
        )

    def test_projection_without_a_prompt_is_always_full(self) -> None:
        self.write()
        record = self.run_context(prompt=None)
        self.assertEqual(record["projection"], "full")
        self.assertIn("text", record["request_trace"]["records"][0])
        self.assertIn("pairs", record["decision_proximity"])


class TaskClosurePreflightProjectionTests(BootstrapFixture):
    def setUp(self) -> None:
        super().setUp()
        self.move_to_review()
        self.prompt = self.root / "prompts/PROMPT-07-001.md"
        code, _records, err = self.run_cli(
            "write-prompt", str(self.root), "--prompt-id", "PROMPT-07-001",
            "--review-kind", "task-closure", "--task", str(self.task),
            "--content", "# Review task completion\n",
        )
        self.assertEqual(code, 0, err)

    def run_context(self, *extra: str):
        code, records, err = self.run_cli(
            "review-context", str(self.root), "--review-kind", "task-closure",
            "--task", str(self.task), "--prompt", str(self.prompt), *extra,
        )
        self.assertEqual(code, 0, err)
        return records[0]

    def test_cli_flag_restores_the_full_projection(self) -> None:
        compact = self.run_context()
        full = self.run_context("--full")
        self.assertEqual(compact["projection"], "compact")
        self.assertEqual(full["projection"], "full")
        self.assertEqual(compact["preflight"], full["preflight"])
        self.assertEqual(compact["context_identity"], full["context_identity"])
        self.assertIsNone(compact["decision_proximity"])
        projection = (full.get("upstream_trace") or {}).get("reviewer_projection")
        if projection is not None:
            slim = compact["upstream_trace"]["reviewer_projection"]
            self.assertNotIn("body", slim)
            self.assertEqual(slim["identity"], projection["identity"])
            self.assertEqual(slim["bytes"], projection["bytes"])


if __name__ == "__main__":
    unittest.main()
