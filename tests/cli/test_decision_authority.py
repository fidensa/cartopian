"""A decision says who it speaks for, and the PM cannot speak for the operator.

RED: every locked decision was a ruling in force whoever wrote it. A PM
decision could drop an operator request from the plan with an
``Out-of-plan request:`` line, overturn an operator ruling by superseding it,
or rewrite a locked ruling in place, and nothing distinguished a PM call from
a ruling the operator made.
GREEN: ``Authority: operator`` must name captured operator turns, which the
writer confirms; ``Authority: pm`` may not dispose of operator requests,
supersede or amend an operator ruling, or ratify. The operator adopts PM
decisions with ``Ratifies:``, and ``plan-audit`` lists those still unratified.
"""
from __future__ import annotations

from cli import trace_binding
from cli.commands import plan_audit, write_decision
from tests.cli.test_evidence_resolver import ResolverCase
from tests.cli.test_fail_open_defects import invoke

DIGEST = "sha256:" + "b" * 64


def body(
    dec_id: str,
    *,
    headers: str = "",
    decision: str = "Text.",
    ruling: str = "The ruling.",
) -> str:
    return (
        f"# {dec_id}: Title\n\nDate: 2026-10-13\nRuling: {ruling}\n{headers}\n"
        f"## Decision\n\n{decision}\n"
    )


class AuthorityFixture(ResolverCase):
    def setUp(self) -> None:
        super().setUp()
        self.operator = self.session()
        self.bind(self.operator)

    def turn(self, text: str = "Leave the pricing statement out of this plan.") -> str:
        return self.operator.exchange(text, "Noted.")

    def write(self, dec_id: str, content: str, **flags):
        return invoke(
            write_decision.handler, project_root=str(self.root), dec_id=dec_id,
            title=f"{dec_id} title", date="2026-10-13", status=flags.get("status"),
            supersedes=flags.get("supersedes"), content=content, content_file=None,
        )

    def written(self, dec_id: str) -> str:
        return (self.root / "decisions" / f"{dec_id}.md").read_text(encoding="utf-8")

    def evidence(self, cid: str, unit: str = "project:project") -> str:
        return f"Operator request evidence for: {unit}: {cid}"

    def operator_ruling(self, dec_id: str, headers: str = "", decision: str = "Text.") -> None:
        content = body(dec_id, headers=headers, decision=f"{decision}\n\n{self.evidence(self.turn())}")
        code, _records, err = self.write(dec_id, content)
        self.assertEqual(code, 0, err)


class AuthorityResolutionTests(AuthorityFixture):
    def test_authority_is_resolved_from_the_body_and_stamped(self) -> None:
        code, records, err = self.write("DEC-001", body("DEC-001"))
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["details"]["authority"], "pm")
        self.assertEqual(trace_binding.decision_header(self.written("DEC-001"), "Authority"), "pm")

        code, records, err = self.write(
            "DEC-002", body("DEC-002", decision=self.evidence(self.turn()))
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(records[0]["details"]["authority"], "operator")
        self.assertEqual(trace_binding.decision_header(self.written("DEC-002"), "Authority"), "operator")

    def test_stamping_never_parts_a_marker_from_its_quote(self) -> None:
        text = "Keep the export provenance for every task."
        cid = self.turn(text)
        content = (
            "# DEC-001: Provenance\n\nDate: 2026-10-13\nRuling: Keep export provenance.\n"
            f"{self.evidence(cid)}\n\n> {text}\n"
        )
        code, _records, err = self.write("DEC-001", content)
        self.assertEqual(code, 0, err)
        lines = self.written("DEC-001").splitlines()
        self.assertLess(lines.index("Authority: operator"), lines.index(self.evidence(cid)))
        self.assertEqual(lines[lines.index(self.evidence(cid)) + 2], f"> {text}")

    def test_operator_authority_needs_confirmed_captured_turns(self) -> None:
        cases = {
            "decision-authority-unevidenced": body("DEC-001", headers="Authority: operator\n"),
            "decision-evidence-unconfirmed": body(
                "DEC-001", decision=self.evidence(f"{self.operator.handle}/turn-999")
            ),
            "decision-authority-unrecognized": body("DEC-001", headers="Authority: team\n"),
        }
        for code_name, content in cases.items():
            with self.subTest(code_name):
                code, _records, err = self.write("DEC-001", content)
                self.assertEqual(code, 1)
                self.assertIn(code_name, err)
                self.assertFalse((self.root / "decisions/DEC-001.md").exists())

    def test_a_revoked_turn_no_longer_carries_operator_authority(self) -> None:
        cid = self.turn()
        from cli import evidence_resolver

        evidence_resolver.write_revocations(self.root, [{"evidence": [cid]}])
        code, _records, err = self.write("DEC-001", body("DEC-001", decision=self.evidence(cid)))
        self.assertEqual(code, 1)
        self.assertIn("decision-evidence-unconfirmed", err)
        self.assertIn("revoked", err)


class PmLimitTests(AuthorityFixture):
    def test_a_pm_decision_cannot_supersede_or_amend_an_operator_ruling(self) -> None:
        self.operator_ruling("DEC-001")
        for name, headers in (("supersede", "Supersedes: DEC-001\n"), ("amend", "Amends: DEC-001\n")):
            with self.subTest(name):
                code, _records, err = self.write("DEC-002", body("DEC-002", headers=headers))
                self.assertEqual(code, 1)
                self.assertIn("decision-authority-insufficient", err)
        code, _records, err = self.write(
            "DEC-002", body("DEC-002", headers="Amends: DEC-001\n",
                            decision=self.evidence(self.turn("Narrow that ruling to exports.")))
        )
        self.assertEqual(code, 0, err)

    def test_a_pm_decision_may_supersede_a_pm_decision(self) -> None:
        self.assertEqual(self.write("DEC-001", body("DEC-001"))[0], 0)
        code, _records, err = self.write("DEC-002", body("DEC-002", headers="Supersedes: DEC-001\n"))
        self.assertEqual(code, 0, err)

    def test_a_pm_may_propose_a_disposition_but_only_the_operator_rules_it(self) -> None:
        disposition = f"Out-of-plan request: {DIGEST}"
        code, _records, err = self.write("DEC-001", body("DEC-001", decision=disposition))
        self.assertEqual(code, 1)
        self.assertIn("decision-authority-insufficient", err)

        code, _records, err = self.write(
            "DEC-001", body("DEC-001", headers="Status: open\n", decision=disposition)
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(trace_binding.out_of_plan_dispositions(self.root), {})

        # The operator agrees; the draft is re-issued as the operator's ruling.
        code, _records, err = self.write(
            "DEC-001",
            body("DEC-001", decision=f"{disposition}\n\n{self.evidence(self.turn())}"),
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(trace_binding.out_of_plan_dispositions(self.root), {DIGEST: "decisions/DEC-001.md"})

    def test_a_hand_written_pm_disposition_disposes_of_nothing(self) -> None:
        (self.root / "decisions/DEC-001.md").write_text(
            "# DEC-001: Out\n\nDate: 2026-10-13\nStatus: locked\nSupersedes: none\n\n"
            f"## Decision\n\nOut-of-plan request: {DIGEST}\n",
            encoding="utf-8",
        )
        self.assertEqual(trace_binding.out_of_plan_dispositions(self.root), {})
        kinds = [w["kind"] for w in plan_audit._check_decision_authority(self.root)]
        self.assertIn("decision-disposition-unauthorized", kinds)


class RatificationTests(AuthorityFixture):
    def test_the_operator_ratifies_pm_decisions_in_one_ruling(self) -> None:
        for dec_id in ("DEC-001", "DEC-002"):
            self.assertEqual(self.write(dec_id, body(dec_id))[0], 0)
        warning = plan_audit._check_decision_authority(self.root)[0]
        self.assertEqual(warning["kind"], "decision-pm-unratified")
        self.assertEqual(warning["decisions"], ["decisions/DEC-001.md", "decisions/DEC-002.md"])

        code, _records, err = self.write("DEC-003", body("DEC-003", headers="Ratifies: DEC-001\n"))
        self.assertEqual(code, 1)
        self.assertIn("decision-authority-insufficient", err)

        self.operator_ruling("DEC-003", headers="Ratifies: DEC-001\n")
        warning = plan_audit._check_decision_authority(self.root)[0]
        self.assertEqual(warning["decisions"], ["decisions/DEC-002.md"])
        index = (self.root / "decisions/INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| pm, ratified by DEC-003 |", index)

        # A ratified decision now speaks for the operator: the PM cannot overturn it.
        code, _records, err = self.write("DEC-004", body("DEC-004", headers="Supersedes: DEC-001\n"))
        self.assertEqual(code, 1)
        self.assertIn("decision-authority-insufficient", err)

    def test_ratification_names_only_live_pm_decisions(self) -> None:
        self.operator_ruling("DEC-001")
        self.assertEqual(self.write("DEC-002", body("DEC-002"))[0], 0)
        self.assertEqual(self.write("DEC-003", body("DEC-003", headers="Supersedes: DEC-002\n"))[0], 0)
        for target in ("DEC-001", "DEC-002", "DEC-009"):
            with self.subTest(target):
                content = body("DEC-004", headers=f"Ratifies: {target}\n",
                               decision=self.evidence(self.turn()))
                code, _records, err = self.write("DEC-004", content)
                self.assertEqual(code, 1)
                self.assertIn("decision-ratifies-invalid", err)

    def test_a_ratified_legacy_disposition_authorizes(self) -> None:
        (self.root / "decisions/DEC-001.md").write_text(
            "# DEC-001: Out\n\nDate: 2026-10-13\nStatus: locked\nSupersedes: none\n\n"
            f"## Decision\n\nOut-of-plan request: {DIGEST}\n",
            encoding="utf-8",
        )
        self.operator_ruling("DEC-002", headers="Ratifies: DEC-001\n")
        self.assertEqual(trace_binding.out_of_plan_dispositions(self.root), {DIGEST: "decisions/DEC-001.md"})


class AuditTests(AuthorityFixture):
    def test_an_unevidenced_operator_claim_is_read_as_pm_and_reported(self) -> None:
        (self.root / "decisions/DEC-001.md").write_text(
            "# DEC-001: Claim\n\nDate: 2026-10-13\nStatus: locked\nAuthority: operator\n"
            "Supersedes: none\n\n## Decision\n\nText.\n",
            encoding="utf-8",
        )
        texts = trace_binding.decision_bodies(self.root)
        self.assertEqual(trace_binding.effective_authorities(texts)["DEC-001"], "pm")
        unreadable = [
            w for w in plan_audit._check_decision_authority(self.root)
            if w["kind"] == "decision-authority-unreadable"
        ]
        self.assertEqual([w["decision"] for w in unreadable], ["decisions/DEC-001.md"])
