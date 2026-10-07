"""`write-task --content-file` and the upstream-trace syntax gate.

RED: ``--content-file`` yields bytes, and the follow-up-review guard ran a str
regex over them, so every ``--content-file`` write crashed with a TypeError.
Separately, a record block whose ``A|`` line exceeded its byte cap was written
without error; the parse failure then surfaced only as an unrelated
governing-decision refusal. GREEN: the body is decoded once up front, and a
record block that cannot parse is refused at write time, naming the record.
"""
from pathlib import Path

from tests.cli.commands.test_write_task_review_follow_up import _FAILED_REVIEW, _body
from tests.cli.commands.test_write_task_uniqueness import _Fixture, run_cli


def _traced(record: str) -> str:
    return (
        "# Fix export\n\nEvidence gate: n/a\nUpstream trace: required\n\n"
        "## Goal\n\nFix the export.\n\n## Acceptance\n\n- [ ] done\n\n"
        f"## Upstream trace\n\n```trace\n{record}\n```\n"
    )


class TestWriteTaskContentFile(_Fixture):
    def content_file(self, body: str) -> str:
        path = Path(self.scaffold.project_root).parent / "body.md"
        path.write_bytes(body.encode("utf-8"))
        return str(path)

    def test_content_file_writes_the_exact_bytes(self):
        body = _body(" Cites REVIEW-01-009 and naïve text.\r\n")
        code, records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002",
            "--content-file", self.content_file(body),
        )
        self.assertEqual(code, 0, err)
        written = Path(records[0]["details"]["path"]).read_bytes()
        self.assertEqual(written, body.encode("utf-8"))

    def test_rewriting_a_task_from_its_own_file_is_unchanged(self):
        code, records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002", "--content", _body()
        )
        self.assertEqual(code, 0, err)
        path = records[0]["details"]["path"]
        before = Path(path).read_bytes()
        code, _records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002", "--content-file", path
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(Path(path).read_bytes(), before)

    def test_follow_up_guard_still_applies_to_content_file(self):
        self.scaffold.write("tasks/in-progress/TASK-01-001.md", _body())
        self.scaffold.write(
            "reviews/REVIEW-01-001.md", _FAILED_REVIEW.format(verdict="request-changes")
        )
        code, _records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002",
            "--content-file", self.content_file(_body(" Addresses REVIEW-01-001 F1.")),
        )
        self.assertEqual(code, 1)
        self.assertIn("review-follow-up-task", err)

    def test_invalid_utf8_content_file_is_refused(self):
        path = Path(self.scaffold.project_root).parent / "bad.md"
        path.write_bytes(b"# Bad\n\xff\xfe\n")
        code, _records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002", "--content-file", str(path)
        )
        self.assertEqual(code, 1)
        self.assertIn("valid UTF-8", err)
        self.assertEqual(self.all_task_files(), [])


class TestWriteTaskTraceSyntax(_Fixture):
    def test_over_cap_applicability_record_is_refused_before_writing(self):
        record = "A|DEC-042|outside-scope|" + "x" * 200
        code, records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002", "--content", _traced(record)
        )
        self.assertEqual(code, 1)
        self.assertEqual(records, [])
        self.assertIn("trace-unparseable", err)
        self.assertIn("183 B cap", err)
        self.assertIn(f"is {len(record) + 1} B", err)
        self.assertEqual(self.all_task_files(), [])

    def test_well_formed_record_block_is_accepted_even_when_incomplete(self):
        code, _records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002",
            "--content", _traced("A|DEC-042|outside-scope|records history only"),
        )
        self.assertEqual(code, 0, err)

    def test_undeclared_trace_is_not_parsed(self):
        body = _traced("not a record").replace("Upstream trace: required\n", "")
        code, _records, err = run_cli(
            "write-task", self.root, "--task-id", "TASK-01-002", "--content", body
        )
        self.assertEqual(code, 0, err)
