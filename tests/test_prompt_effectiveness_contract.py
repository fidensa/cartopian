"""Prompt-effectiveness evidence: the accepted ledger, measured.

The contract's conformance clause asks what two implementers must both
produce. These tests are that list, run against this one: byte-identical
records, closed domains, width caps, family assignment, `U` derivation, five
states never collapsing into four, cap behaviour, the two summaries, plan
retention, every row and announcement shape, the query bounds, zero routine
bytes, and fail-closed-never-fail-blocking emission.

Nothing here computes a score, and no assertion below implies that a prompt
caused an outcome.
"""
import argparse
import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from cli import prompt_evidence as pe
from cli.commands import prompt_evidence as command
from tests.scaffold import project_scaffold

PLAN = "PLAN-001"
UNIT = "TASK-05-010"
DATE = "2026-08-19"


class SpecimenTests(unittest.TestCase):
    """The twelve normative record specimens, byte for byte."""

    SPECIMENS = {
        "U": (
            243,
            '{"v":1,"k":"U","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":{"CLR":[0,"observed"],"OMR":[1,"observed"],"RRR":[2,"observed"],'
            '"RRG":[2,"observed"],"PCC":[29184,"observed"],'
            '"PAD":[0,"not-yet-observable"]},"q":{"OMR":12,"RRR":3,"PCC":6}}',
        ),
        "E-CLR": (
            96,
            '{"v":1,"k":"E","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"CLR","a":"REPORT-05-010"}',
        ),
        "E-CLR-dec": (
            90,
            '{"v":1,"k":"E","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"CLR","a":"DEC-067"}',
        ),
        "E-RRR": (
            124,
            '{"v":1,"k":"E","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"RRR","o":2,"x":"request-changes","a":"REVIEW-05-010"}',
        ),
        "E-RRR-app": (
            116,
            '{"v":1,"k":"E","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"RRR","o":3,"x":"approve","a":"REVIEW-05-010"}',
        ),
        "E-RRG": (
            110,
            '{"v":1,"k":"E","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"RRG","o":2,"x":"in-review>in-progress"}',
        ),
        "E-PCC": (
            91,
            '{"v":1,"k":"E","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"PCC","o":3,"n":4812}',
        ),
        "E-PAD": (
            89,
            '{"v":1,"k":"E","p":"PLAN-005","u":"TASK-05-010","d":"2026-09-02",'
            '"f":"PAD","a":"BL-031"}',
        ),
        "D-det": (
            157,
            '{"v":1,"k":"D","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"OMR","t":"det","n":"D2","c":"C03",'
            '"r":"upstream-intent-uncovered","a":"REVIEW-05-010"}',
        ),
        "D-cq": (
            151,
            '{"v":1,"k":"D","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"OMR","t":"cq","g":"C1","h":"upstream-alignment","s":"major",'
            '"a":"REVIEW-05-010"}',
        ),
        "D-fnd": (
            127,
            '{"v":1,"k":"D","p":"PLAN-005","u":"TASK-05-010","d":"2026-08-19",'
            '"f":"RRR","t":"fnd","g":"F2","s":"major","a":"REVIEW-05-010"}',
        ),
        "U-late": (
            233,
            '{"v":1,"k":"U","p":"PLAN-005","u":"TASK-05-010","d":"2026-09-30",'
            '"f":{"CLR":[0,"observed"],"OMR":[1,"observed"],"RRR":[2,"observed"],'
            '"RRG":[2,"observed"],"PCC":[29184,"observed"],"PAD":[1,"observed"]},'
            '"q":{"OMR":12,"RRR":3,"PCC":6}}',
        ),
    }

    def build(self, name):
        p, u, d = "PLAN-005", "TASK-05-010", "2026-08-19"
        summary_families = {
            "CLR": (0, "observed"),
            "OMR": (1, "observed"),
            "RRR": (2, "observed"),
            "RRG": (2, "observed"),
            "PCC": (29184, "observed"),
            "PAD": (0, "not-yet-observable"),
        }
        denominators = {"OMR": 12, "RRR": 3, "PCC": 6}
        if name == "U":
            return pe.summary(
                plan=p, unit=u, date=d, families=summary_families,
                denominators=denominators,
            )
        if name == "U-late":
            return pe.summary(
                plan=p, unit=u, date="2026-09-30",
                families={**summary_families, "PAD": (1, "observed")},
                denominators=denominators,
            )
        if name == "E-CLR":
            return pe.event(plan=p, unit=u, date=d, family="CLR", artifact="REPORT-05-010")
        if name == "E-CLR-dec":
            return pe.event(plan=p, unit=u, date=d, family="CLR", artifact="DEC-067")
        if name == "E-RRR":
            return pe.event(plan=p, unit=u, date=d, family="RRR", ordinal=2,
                            marker="request-changes", artifact="REVIEW-05-010")
        if name == "E-RRR-app":
            return pe.event(plan=p, unit=u, date=d, family="RRR", ordinal=3,
                            marker="approve", artifact="REVIEW-05-010")
        if name == "E-RRG":
            return pe.event(plan=p, unit=u, date=d, family="RRG", ordinal=2,
                            marker="in-review>in-progress")
        if name == "E-PCC":
            return pe.event(plan=p, unit=u, date=d, family="PCC", ordinal=3, size=4812)
        if name == "E-PAD":
            return pe.event(plan=p, unit=u, date="2026-09-02", family="PAD",
                            artifact="BL-031")
        if name == "D-det":
            return pe.determination_address(
                plan=p, unit=u, date=d, family="OMR", kind="det",
                determination="D2", criterion="C03",
                reason="upstream-intent-uncovered", artifact="REVIEW-05-010")
        if name == "D-cq":
            return pe.determination_address(
                plan=p, unit=u, date=d, family="OMR", kind="cq", gap="C1",
                check="upstream-alignment", severity="major",
                artifact="REVIEW-05-010")
        return pe.determination_address(
            plan=p, unit=u, date=d, family="RRR", kind="fnd", gap="F2",
            severity="major", artifact="REVIEW-05-010")

    def test_every_specimen_serializes_byte_identically(self):
        for name, (size, literal) in self.SPECIMENS.items():
            with self.subTest(specimen=name):
                line = pe.serialize(self.build(name))
                self.assertEqual(line, literal + "\n")
                self.assertEqual(len(line.encode("utf-8")), size)

    def test_every_specimen_is_a_valid_emission(self):
        for name in self.SPECIMENS:
            with self.subTest(specimen=name):
                self.assertIsNone(pe.validate(self.build(name)))

    def test_no_specimen_persists_a_body_path_hash_or_time_of_day(self):
        for name, (_, literal) in self.SPECIMENS.items():
            with self.subTest(specimen=name):
                for forbidden in ("sha256", "/", "T0", ":0", "prompt", "@"):
                    self.assertNotIn(forbidden, literal.replace('"d":"', ""))

    def test_the_two_summaries_differ_only_in_date_and_pad(self):
        first = json.loads(pe.serialize(self.build("U")))
        late = json.loads(pe.serialize(self.build("U-late")))
        self.assertNotEqual(first["d"], late["d"])
        self.assertEqual(first["f"]["PAD"], [0, "not-yet-observable"])
        self.assertEqual(late["f"]["PAD"], [1, "observed"])
        for family in ("CLR", "OMR", "RRR", "RRG", "PCC"):
            self.assertEqual(first["f"][family], late["f"][family])


class WidthTests(unittest.TestCase):
    """Caps make the growth ceiling a bound rather than an estimate."""

    def at_cap(self, kind, **fields):
        base = {"v": 99, "k": kind, "p": "P" * 12, "u": "U" * 16, "d": "D" * 10}
        base.update(fields)
        return pe.serialize({k: base[k] for k in pe.KEY_ORDER[kind] if k in base})

    def test_the_summary_maximum_is_397_bytes(self):
        line = self.at_cap(
            "U",
            f={name: [10 ** 12 - 1, "not-yet-observable"] for name in pe.FAMILIES},
            q={name: 10 ** 12 - 1 for name in pe.DENOMINATOR_FAMILIES},
        )
        self.assertEqual(len(line.encode("utf-8")), pe.MAX_RECORD_BYTES["U"])

    def test_no_event_or_address_variant_exceeds_its_declared_ceiling(self):
        variants = {
            "E-CLR": self.at_cap("E", f="CLR", a="A" * 24),
            "E-PAD": self.at_cap("E", f="PAD", a="A" * 24),
            "E-RRR": self.at_cap("E", f="RRR", o=999, x="X" * 24, a="A" * 24),
            "E-RRG": self.at_cap("E", f="RRG", o=999, x="X" * 24),
            "E-PCC": self.at_cap("E", f="PCC", o=999, n=10 ** 12 - 1),
            "D-det": self.at_cap(
                "D", f="OMR", t="det", n="D1", c="C" * 3, r="R" * 26, a="A" * 24
            ),
            # `t` is capped at 3 B but `cq` is only 2, so the widest conformant
            # record is one byte under the mechanically derived 186 B ceiling.
            "D-cq": self.at_cap(
                "D", f="OMR", t="cq", g="G" * 4, h="H" * 27, s="S" * 7, a="A" * 24
            ),
            "D-fnd": self.at_cap("D", f="RRR", t="fnd", g="G" * 4, s="S" * 7, a="A" * 24),
        }
        for name, line in variants.items():
            with self.subTest(variant=name):
                self.assertLessEqual(
                    len(line.encode("utf-8")), pe.MAX_RECORD_BYTES[name]
                )

    def test_the_hard_per_unit_ceiling_is_12326_bytes(self):
        self.assertEqual(pe.UNIT_CEILING_BYTES, 12326)
        self.assertEqual(pe.ORDINARY_ALLOWANCE, 62)
        self.assertEqual(pe.RECORD_CAP, 64)

    def test_an_over_width_value_is_rejected_not_truncated(self):
        record = pe.event(
            plan=PLAN, unit=UNIT, date=DATE, family="CLR", artifact="REPORT-05-010"
        )
        record["a"] = "REPORT-05-010" * 3
        self.assertIsNotNone(pe.validate(record))


class ClosedDomainTests(unittest.TestCase):
    def test_a_reason_code_outside_the_consumed_set_is_rejected(self):
        record = pe.determination_address(
            plan=PLAN, unit=UNIT, date=DATE, family="OMR", kind="det",
            determination="D2", criterion="C03", reason="looks-wrong",
            artifact="REVIEW-05-010",
        )
        self.assertIn("reason code", pe.validate(record))

    def test_a_check_name_outside_the_seven_is_rejected(self):
        record = pe.determination_address(
            plan=PLAN, unit=UNIT, date=DATE, family="OMR", kind="cq", gap="C1",
            check="vibes", severity="major", artifact="REVIEW-05-010",
        )
        self.assertIn("check name", pe.validate(record))

    def test_a_severity_outside_the_four_is_rejected(self):
        record = pe.determination_address(
            plan=PLAN, unit=UNIT, date=DATE, family="RRR", kind="fnd", gap="F1",
            severity="critical", artifact="REVIEW-05-010",
        )
        self.assertIn("severity", pe.validate(record))

    def test_a_state_outside_the_five_is_rejected(self):
        record = pe.summary(
            plan=PLAN, unit=UNIT, date=DATE,
            families={name: (0, "maybe") for name in pe.FAMILIES},
            denominators={name: 0 for name in pe.DENOMINATOR_FAMILIES},
        )
        self.assertIn("outside the closed set", pe.validate(record))

    def test_a_non_task_unit_is_rejected(self):
        record = pe.event(
            plan=PLAN, unit="REVIEW-PLAN-001", date=DATE, family="CLR",
            artifact="DEC-067",
        )
        self.assertIn("TASK-NN-NNN", pe.validate(record))

    def test_omr_emits_no_event_record(self):
        record = pe.event(
            plan=PLAN, unit=UNIT, date=DATE, family="OMR", artifact="REVIEW-05-010"
        )
        self.assertIn("OMR emits no E record", pe.validate(record))

    def test_a_record_with_a_key_outside_its_kind_is_rejected(self):
        record = dict(
            pe.event(plan=PLAN, unit=UNIT, date=DATE, family="CLR", artifact="DEC-067")
        )
        record["t"] = "det"
        self.assertIsNotNone(pe.validate(record))


class SummaryDerivationTests(unittest.TestCase):
    """`U` is a derivation of the unit's own records plus boundary availability."""

    def records(self):
        return [
            pe.event(plan=PLAN, unit=UNIT, date=DATE, family="PCC", ordinal=1, size=4000),
            pe.event(plan=PLAN, unit=UNIT, date=DATE, family="PCC", ordinal=2, size=812),
            pe.event(plan=PLAN, unit=UNIT, date=DATE, family="RRR", ordinal=1,
                     marker="request-changes", artifact="REVIEW-05-010"),
            pe.event(plan=PLAN, unit=UNIT, date=DATE, family="RRR", ordinal=2,
                     marker="approve", artifact="REVIEW-05-010"),
            pe.determination_address(
                plan=PLAN, unit=UNIT, date=DATE, family="OMR", kind="det",
                determination="D2", criterion="C03",
                reason="upstream-intent-uncovered", artifact="REVIEW-05-010"),
        ]

    def expectations(self, **overrides):
        base = {
            "CLR": pe.Expectation(available=True, expected=None),
            "OMR": pe.Expectation(available=True, denominator=12),
            "RRR": pe.Expectation(available=True, expected=2, denominator=2),
            "RRG": pe.Expectation(available=True, expected=None),
            "PCC": pe.Expectation(available=True, expected=2, denominator=2),
            "PAD": pe.Expectation(available=True),
        }
        base.update(overrides)
        return base

    def test_pcc_counts_summed_bytes_and_every_other_family_counts_events(self):
        families, denominators = pe.derive_summary_values(
            self.records(), self.expectations(), post_approval_closed=False
        )
        self.assertEqual(families["PCC"], (4812, "observed"))
        self.assertEqual(families["RRR"], (1, "observed"))
        self.assertEqual(families["OMR"], (1, "observed"))
        self.assertEqual(denominators, {"OMR": 12, "RRR": 2, "PCC": 2})

    def test_rrr_counts_only_non_approving_passes(self):
        families, _ = pe.derive_summary_values(
            self.records(), self.expectations(), post_approval_closed=False
        )
        self.assertEqual(families["RRR"][0], 1)

    def test_a_true_zero_is_observed_not_missing(self):
        families, _ = pe.derive_summary_values(
            self.records(), self.expectations(), post_approval_closed=False
        )
        self.assertEqual(families["CLR"], (0, "observed"))

    def test_an_unavailable_boundary_never_reads_zero(self):
        families, _ = pe.derive_summary_values(
            self.records(),
            self.expectations(CLR=pe.Expectation(available=False)),
            post_approval_closed=False,
        )
        self.assertEqual(families["CLR"], (0, "unavailable"))

    def test_a_missing_required_emission_reads_omitted(self):
        families, _ = pe.derive_summary_values(
            self.records(),
            self.expectations(PCC=pe.Expectation(available=True, expected=6,
                                                 denominator=6)),
            post_approval_closed=False,
        )
        self.assertEqual(families["PCC"][1], "omitted")

    def test_pad_is_not_yet_observable_until_its_window_closes(self):
        open_window, _ = pe.derive_summary_values(
            self.records(), self.expectations(), post_approval_closed=False
        )
        closed, _ = pe.derive_summary_values(
            self.records(), self.expectations(), post_approval_closed=True
        )
        self.assertEqual(open_window["PAD"], (0, "not-yet-observable"))
        self.assertEqual(closed["PAD"], (0, "observed"))

    def test_a_family_that_cannot_apply_reads_not_applicable(self):
        families, _ = pe.derive_summary_values(
            self.records(),
            self.expectations(
                PAD=pe.Expectation(available=False, forced_state="not-applicable")
            ),
            post_approval_closed=True,
        )
        self.assertEqual(families["PAD"], (0, "not-applicable"))

    def test_the_five_states_never_collapse_into_one_another(self):
        seen = set()
        for state in pe.STATES:
            families, _ = pe.derive_summary_values(
                [],
                self.expectations(
                    CLR=pe.Expectation(available=True, forced_state=state)
                ),
                post_approval_closed=True,
            )
            seen.add(families["CLR"][1])
        self.assertEqual(seen, set(pe.STATES))


class ProjectionTests(unittest.TestCase):
    """Nine row shapes and two announcements, at specimen and maximum domain."""

    def test_the_three_determination_rows_match_their_specimens(self):
        rows = {
            "det": pe.determination_row(
                pe.determination_address(
                    plan="PLAN-005", unit=UNIT, date=DATE, family="OMR", kind="det",
                    determination="D2", criterion="C03",
                    reason="upstream-intent-uncovered", artifact="REVIEW-05-010")),
            "cq": pe.determination_row(
                pe.determination_address(
                    plan="PLAN-005", unit=UNIT, date=DATE, family="OMR", kind="cq",
                    gap="C1", check="upstream-alignment", severity="major",
                    artifact="REVIEW-05-010")),
            "fnd": pe.determination_row(
                pe.determination_address(
                    plan="PLAN-005", unit=UNIT, date=DATE, family="RRR", kind="fnd",
                    gap="F2", severity="major", artifact="REVIEW-05-010")),
        }
        self.assertEqual(
            rows["det"],
            "D|TASK-05-010|D2 C03|upstream-intent-uncovered|REVIEW-05-010",
        )
        self.assertEqual(
            rows["cq"], "D|TASK-05-010|CQ C1|upstream-alignment major|REVIEW-05-010"
        )
        self.assertEqual(rows["fnd"], "D|TASK-05-010|F2|major|REVIEW-05-010")
        self.assertEqual(
            [len(row.encode()) + 1 for row in rows.values()], [61, 59, 37]
        )

    def test_the_cq_shape_can_never_be_read_as_a_criterion_ordinal(self):
        row = pe.determination_row(
            pe.determination_address(
                plan="PLAN-005", unit=UNIT, date=DATE, family="OMR", kind="cq",
                gap="C1", check="upstream-alignment", severity="major",
                artifact="REVIEW-05-010")
        )
        self.assertIn("|CQ C1|", row)

    def test_all_five_event_rows_match_their_specimens(self):
        cases = [
            (pe.event(plan="PLAN-005", unit=UNIT, date=DATE, family="CLR",
                      artifact="REPORT-05-010"),
             "E|TASK-05-010|2026-08-19|CLR|-|-|REPORT-05-010", 47),
            (pe.event(plan="PLAN-005", unit=UNIT, date=DATE, family="CLR",
                      artifact="DEC-067"),
             "E|TASK-05-010|2026-08-19|CLR|-|-|DEC-067", 41),
            (pe.event(plan="PLAN-005", unit=UNIT, date=DATE, family="RRR", ordinal=2,
                      marker="request-changes", artifact="REVIEW-05-010"),
             "E|TASK-05-010|2026-08-19|RRR|pass 2|request-changes|REVIEW-05-010", 66),
            (pe.event(plan="PLAN-005", unit=UNIT, date=DATE, family="RRG", ordinal=2,
                      marker="in-review>in-progress"),
             "E|TASK-05-010|2026-08-19|RRG|pass 2|in-review>in-progress|-", 60),
            (pe.event(plan="PLAN-005", unit=UNIT, date=DATE, family="PCC", ordinal=3,
                      size=4812),
             "E|TASK-05-010|2026-08-19|PCC|write 3|4812 B|-", 46),
            (pe.event(plan="PLAN-005", unit=UNIT, date="2026-09-02", family="PAD",
                      artifact="BL-031"),
             "E|TASK-05-010|2026-09-02|PAD|-|-|BL-031", 40),
        ]
        for record, expected, size in cases:
            with self.subTest(family=record["f"], artifact=record.get("a")):
                row = pe.event_row(record)
                self.assertEqual(row, expected)
                self.assertEqual(len(row.encode()) + 1, size)
                self.assertEqual(len(row.split("|")), 7)

    def test_the_announcement_shapes_match_their_specimens(self):
        self.assertEqual(
            pe.truncation_line("units", 50, 137, 50),
            "TRUNCATED|units|50 of 137 matched|cap 50",
        )
        self.assertEqual(
            pe.truncation_line("determinations", 200, 431, 200),
            "TRUNCATED|determinations|200 of 431 matched|cap 200",
        )
        self.assertEqual(
            pe.capped_line("TASK-05-006", 64, ["PCC", "RRR"]),
            "CAPPED|TASK-05-006|64 of 64|PCC RRR",
        )

    def test_a_unit_row_always_carries_six_families_with_their_states(self):
        record = pe.summary(
            plan="PLAN-005", unit=UNIT, date=DATE,
            families={
                "CLR": (0, "observed"), "OMR": (1, "observed"),
                "RRR": (2, "observed"), "RRG": (2, "observed"),
                "PCC": (29184, "observed"), "PAD": (0, "not-yet-observable"),
            },
            denominators={"OMR": 12, "RRR": 3, "PCC": 6},
        )
        row = pe.unit_row(record)
        self.assertEqual(len(row.split("|")), 9)
        for family in pe.FAMILIES:
            self.assertIn(f"{family} ", row)
        self.assertIn("PCC 29184 B observed of 6", row)
        self.assertIn("PAD 0 not-yet-observable", row)

    def test_every_family_carrying_a_denominator_renders_it(self):
        """Denominators travel with numerators.

        § 15.1 states that `of <n>` renders for the three families that carry
        one, and its own maximum-domain arithmetic prices `OMR` at 51 B — the
        35 B base plus ` of ` and a 12 B denominator — which is what makes the
        291 B row and the 17,220 B answer bound reproduce. The printed
        `proj-U` specimen omits `OMR`'s denominator and therefore measures
        141 B rather than 147 B; the rule and the bound agree with each other
        and the specimen does not, so the rule governs here.
        """
        record = pe.summary(
            plan="PLAN-005", unit=UNIT, date=DATE,
            families={name: (1, "observed") for name in pe.FAMILIES},
            denominators={"OMR": 12, "RRR": 3, "PCC": 6},
        )
        row = pe.unit_row(record)
        self.assertIn("OMR 1 observed of 12", row)
        self.assertIn("RRR 1 observed of 3", row)
        self.assertIn("PCC 1 B observed of 6", row)
        self.assertNotIn("CLR 1 observed of", row)


class MaximumDomainTests(unittest.TestCase):
    """Every answer bound is arithmetic over declared widths, not a specimen."""

    def test_the_maximum_domain_rows_measure_what_the_bounds_assume(self):
        unit = "U" * 11
        date = "D" * 10
        summary = {
            "v": 1, "k": "U", "p": "p", "u": unit, "d": date,
            "f": {name: [10 ** 12 - 1, "not-yet-observable"] for name in pe.FAMILIES},
            "q": {name: 10 ** 12 - 1 for name in pe.DENOMINATOR_FAMILIES},
        }
        self.assertEqual(len(pe.unit_row(summary).encode()) + 1, 291)

        addresses = {
            "det": {"t": "det", "n": "D1", "c": "C99",
                    "r": "unresolved-source-conflict", "a": "REVIEW-NN-NNN"},
            "cq": {"t": "cq", "g": "C99", "h": "factual-and-source-accuracy",
                   "s": "blocker", "a": "REVIEW-NN-NNN"},
            "fnd": {"t": "fnd", "g": "F99", "s": "blocker", "a": "REVIEW-NN-NNN"},
        }
        expected = {"det": 62, "cq": 71, "fnd": 40}
        for kind, fields in addresses.items():
            with self.subTest(shape=kind):
                row = pe.determination_row({"u": unit, **fields})
                self.assertEqual(len(row.encode()) + 1, expected[kind])

        events = {
            "CLR": ({"f": "CLR", "a": "REVIEW-NN-NNN"}, 47),
            "RRR": ({"f": "RRR", "o": 999, "x": "request-changes",
                     "a": "REVIEW-NN-NNN"}, 68),
            "RRG": ({"f": "RRG", "o": 999, "x": "in-review>in-progress"}, 62),
            "PCC": ({"f": "PCC", "o": 999, "n": 10 ** 12 - 1}, 56),
            "PAD": ({"f": "PAD", "a": "BL-999"}, 40),
        }
        for family, (fields, size) in events.items():
            with self.subTest(variant=family):
                row = pe.event_row({"u": unit, "d": date, **fields})
                self.assertEqual(len(row.encode()) + 1, size)

        self.assertEqual(
            len(pe.truncation_line("determinations", 10 ** 12 - 1, 10 ** 12 - 1,
                                   200).encode()) + 1,
            70,
        )
        self.assertEqual(
            len(pe.capped_line(unit, 64, list(pe.FAMILIES)).encode()) + 1, 52
        )

    def test_the_published_answer_bounds_reproduce(self):
        self.assertEqual(
            pe.ANSWER_BOUNDS,
            {"units": 17220, "determinations": 24670, "events": 24070},
        )
        self.assertEqual(max(pe.ANSWER_BOUNDS.values()), 24670)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.plan = pe.current_plan_id(self.root)

    def event(self, family="PCC", ordinal=1, **kw):
        return pe.event(
            plan=self.plan, unit=UNIT, date=DATE, family=family, ordinal=ordinal, **kw
        )

    def test_the_plan_window_is_one_past_the_highest_closed_archive(self):
        self.assertEqual(self.plan, "PLAN-001")
        (self.root / "archive" / "PLAN-001").mkdir(parents=True)
        (self.root / "archive" / "PLAN-002").mkdir(parents=True)
        self.assertEqual(pe.current_plan_id(self.root), "PLAN-003")

    def test_an_append_is_readable_and_scoped_to_the_current_window(self):
        outcome = pe.emit(self.root, self.event(size=100))
        self.assertEqual(outcome["result"], pe.WRITTEN)
        ledger = pe.read_ledger(self.root)
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0]["n"], 100)

    def test_a_foreign_plan_record_is_a_detected_inconsistency(self):
        pe.emit(self.root, self.event(size=100))
        path = pe.log_path(self.root)
        foreign = dict(self.event(ordinal=2, size=200))
        foreign["p"] = "PLAN-099"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(pe.serialize(foreign))
        ledger = pe.read_ledger(self.root)
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(
            [e["rule"] for e in ledger.errors], ["foreign-plan-window"]
        )
        self.assertEqual(ledger.unavailable_units, [UNIT])

    def test_an_unknown_schema_version_names_the_error_and_never_guesses(self):
        record = dict(self.event(size=100))
        record["v"] = 2
        path = pe.log_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["unknown-schema-version"])
        self.assertEqual(ledger.unavailable_units, [UNIT])

    def test_a_torn_final_line_is_discarded_on_read(self):
        pe.emit(self.root, self.event(size=100))
        with pe.log_path(self.root).open("a", encoding="utf-8") as handle:
            handle.write('{"v":1,"k":"E","p":"PLAN-001","u":"TASK-05-0')
        ledger = pe.read_ledger(self.root)
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual([e["rule"] for e in ledger.errors], ["torn-final-record"])

    def test_a_rejected_emission_writes_nothing_and_never_raises(self):
        record = dict(self.event(size=100))
        record["u"] = "not-a-unit"
        outcome = pe.emit(self.root, record)
        self.assertEqual(outcome["result"], pe.REJECTED)
        self.assertFalse(pe.log_path(self.root).exists())

    def test_a_foreign_plan_id_is_refused_at_write(self):
        record = dict(self.event(size=100))
        record["p"] = "PLAN-099"
        self.assertEqual(pe.emit(self.root, record)["result"], pe.REJECTED)


class CapTests(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.plan = pe.current_plan_id(self.root)

    def fill_ordinary(self, count):
        ledger = pe.read_ledger(self.root)
        for index in range(1, count + 1):
            pe.emit(
                self.root,
                pe.event(plan=self.plan, unit=UNIT, date=DATE, family="PCC",
                         ordinal=index, size=100),
                ledger=ledger,
            )
        return ledger

    def summary_record(self, date=DATE, pad=(0, "not-yet-observable")):
        return pe.summary(
            plan=self.plan, unit=UNIT, date=date,
            families={
                "CLR": (0, "observed"), "OMR": (0, "observed"),
                "RRR": (0, "observed"), "RRG": (0, "observed"),
                "PCC": (100, "omitted"), "PAD": pad,
            },
            denominators={"OMR": 0, "RRR": 0, "PCC": 0},
        )

    def test_the_ordinary_allowance_is_62_records(self):
        ledger = self.fill_ordinary(62)
        used = pe.allowance(ledger, UNIT)
        self.assertEqual(used.ordinary_used, 62)
        self.assertEqual(used.ordinary_remaining, 0)
        self.assertTrue(used.at_cap)

    def test_the_63rd_ordinary_record_is_suppressed_not_written(self):
        ledger = self.fill_ordinary(62)
        outcome = pe.emit(
            self.root,
            pe.event(plan=self.plan, unit=UNIT, date=DATE, family="PCC",
                     ordinal=63, size=100),
            ledger=ledger,
        )
        self.assertEqual(outcome["result"], pe.SUPPRESSED)
        self.assertEqual(outcome["family"], "PCC")
        self.assertEqual(len(pe.read_ledger(self.root).records), 62)

    def test_both_summaries_still_write_from_their_reserved_slots(self):
        ledger = self.fill_ordinary(62)
        first = pe.emit(self.root, self.summary_record(), ledger=ledger)
        second = pe.emit(
            self.root,
            self.summary_record(date="2026-09-30", pad=(0, "omitted")),
            ledger=ledger,
        )
        self.assertEqual(first["result"], pe.WRITTEN)
        self.assertEqual(second["result"], pe.WRITTEN)
        self.assertEqual(len(pe.read_ledger(self.root).records), 64)

    def test_a_third_summary_is_rejected(self):
        ledger = pe.read_ledger(self.root)
        pe.emit(self.root, self.summary_record(), ledger=ledger)
        pe.emit(self.root, self.summary_record(date="2026-09-30"), ledger=ledger)
        third = pe.emit(
            self.root, self.summary_record(date="2026-10-31"), ledger=ledger
        )
        self.assertEqual(third["result"], pe.REJECTED)
        self.assertIn("third U", third["reason"])

    def test_the_reservation_is_never_reallocated(self):
        # A unit that is never approved simply leaves its second reserved
        # record unwritten; the ordinary allowance does not grow to 63.
        ledger = self.fill_ordinary(62)
        pe.emit(self.root, self.summary_record(), ledger=ledger)
        outcome = pe.emit(
            self.root,
            pe.event(plan=self.plan, unit=UNIT, date=DATE, family="PCC",
                     ordinal=63, size=100),
            ledger=ledger,
        )
        self.assertEqual(outcome["result"], pe.SUPPRESSED)

    def test_a_capped_unit_is_announced_in_every_answer_that_names_it(self):
        ledger = self.fill_ordinary(62)
        pe.emit(self.root, self.summary_record(), ledger=ledger)
        answer = pe.project_units(pe.read_ledger(self.root))
        self.assertEqual(len(answer.rows), 1)
        self.assertEqual(answer.capped, [pe.capped_line(UNIT, 63, ["PCC"])])

    def test_families_with_no_suppressed_record_keep_their_true_state(self):
        records = [
            pe.event(plan=self.plan, unit=UNIT, date=DATE, family="PCC",
                     ordinal=index, size=1)
            for index in range(1, 63)
        ]
        families = {
            "CLR": (0, "observed"), "OMR": (0, "observed"), "RRR": (0, "observed"),
            "RRG": (0, "observed"), "PCC": (62, "omitted"),
            "PAD": (0, "not-yet-observable"),
        }
        self.assertEqual(pe.suppressed_families(records, families), ["PCC"])


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.plan = pe.current_plan_id(self.root)

    def seed_units(self, count):
        ledger = pe.read_ledger(self.root)
        for index in range(1, count + 1):
            unit = f"TASK-05-{index:03d}"
            pe.emit(
                self.root,
                pe.summary(
                    plan=self.plan, unit=unit, date=DATE,
                    families={name: (0, "observed") for name in pe.FAMILIES},
                    denominators={name: 0 for name in pe.DENOMINATOR_FAMILIES},
                ),
                ledger=ledger,
            )
        return ledger

    def test_an_untruncated_answer_emits_no_announcement(self):
        self.seed_units(3)
        answer = pe.project_units(pe.read_ledger(self.root))
        self.assertEqual(len(answer.rows), 3)
        self.assertIsNone(answer.truncated)
        self.assertEqual(answer.capped, [])

    def test_a_truncated_answer_always_says_so(self):
        self.seed_units(52)
        answer = pe.project_units(pe.read_ledger(self.root))
        self.assertEqual(len(answer.rows), 50)
        self.assertEqual(
            answer.truncated, "TRUNCATED|units|50 of 52 matched|cap 50"
        )

    def test_truncation_follows_the_callers_stated_order(self):
        self.seed_units(52)
        ledger = pe.read_ledger(self.root)
        wanted = [f"TASK-05-{index:03d}" for index in range(52, 0, -1)]
        answer = pe.project_units(ledger, units=wanted)
        self.assertTrue(answer.rows[0].startswith("U|TASK-05-052|"))

    def test_a_unit_with_no_summary_is_reported_unavailable_not_dropped(self):
        ledger = pe.read_ledger(self.root)
        answer = pe.project_units(ledger, units=["TASK-05-999"])
        self.assertEqual(answer.rows, [])
        self.assertEqual(answer.unavailable_units, ["TASK-05-999"])

    def test_a_unit_with_two_summaries_renders_only_the_later_one(self):
        ledger = pe.read_ledger(self.root)
        for date, pad in ((DATE, (0, "not-yet-observable")), ("2026-09-30", (1, "observed"))):
            pe.emit(
                self.root,
                pe.summary(
                    plan=self.plan, unit=UNIT, date=date,
                    families={
                        **{name: (0, "observed") for name in pe.FAMILIES},
                        "PAD": pad,
                    },
                    denominators={name: 0 for name in pe.DENOMINATOR_FAMILIES},
                ),
                ledger=ledger,
            )
        answer = pe.project_units(pe.read_ledger(self.root))
        self.assertEqual(len(answer.rows), 1)
        self.assertIn("PAD 1 observed", answer.rows[0])

    def test_no_answer_can_exceed_its_published_bound(self):
        self.seed_units(52)
        answer = pe.project_units(pe.read_ledger(self.root))
        record = answer.as_record()
        self.assertLessEqual(record["bytes"], pe.ANSWER_BOUNDS["units"])
        self.assertEqual(record["bound_bytes"], 17220)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def test_deletion_is_mediated_so_the_absence_is_explainable(self):
        plan = pe.current_plan_id(self.root)
        pe.emit(
            self.root,
            pe.event(plan=plan, unit=UNIT, date=DATE, family="PCC", ordinal=1, size=1),
        )
        self.assertTrue(pe.log_path(self.root).exists())
        self.assertTrue(pe.delete_log(self.root))
        self.assertFalse(pe.log_path(self.root).exists())
        journal = (self.root / ".cartopian" / "provenance.log").read_text(encoding="utf-8")
        self.assertIn(pe.LOG_RELPATH, journal)
        self.assertIn("mediated-delete", journal)

    def test_every_family_of_an_expired_unit_reads_unavailable(self):
        # A plan reset must not turn a unit that had three rejections into a
        # unit that had none: the fresh window simply cannot answer for it.
        plan = pe.current_plan_id(self.root)
        pe.emit(
            self.root,
            pe.summary(
                plan=plan, unit=UNIT, date=DATE,
                families={name: (3, "observed") for name in pe.FAMILIES},
                denominators={name: 3 for name in pe.DENOMINATOR_FAMILIES},
            ),
        )
        pe.delete_log(self.root)
        answer = pe.project_units(pe.read_ledger(self.root), units=[UNIT])
        self.assertEqual(answer.rows, [])
        self.assertEqual(answer.unavailable_units, [UNIT])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root

    def run_command(self, **overrides):
        args = argparse.Namespace(
            project_root=str(self.root), projection=None, unit=None,
            summarize=False, post_approval_closed=False, record_event=False,
            family=None, artifact=None, close_plan=False, date=DATE,
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = command.handler(args)
        payload = out.getvalue().strip()
        return code, (json.loads(payload) if payload else None), err.getvalue()

    def test_the_routine_context_budget_is_reported_as_zero(self):
        code, record, _ = self.run_command(projection="U")
        self.assertEqual(code, 0)
        self.assertEqual(record["routine_context_bytes"], 0)

    def test_exactly_one_mode_is_required(self):
        code, _, err = self.run_command()
        self.assertEqual(code, 2)
        self.assertIn("exactly one", err)

    def test_a_clarification_event_records_its_decision_source(self):
        code, record, _ = self.run_command(
            record_event=True, family="CLR", unit=[UNIT], artifact="DEC-067"
        )
        self.assertEqual(code, 0)
        self.assertEqual(record["result"], pe.WRITTEN)
        rows = pe.project_events(pe.read_ledger(self.root)).rows
        self.assertEqual(rows, [f"E|{UNIT}|{DATE}|CLR|-|-|DEC-067"])

    def test_a_rejected_event_is_reported_and_never_blocks(self):
        code, record, err = self.run_command(
            record_event=True, family="CLR", unit=[UNIT], artifact="not-an-id"
        )
        self.assertEqual(code, 0)
        self.assertEqual(record["result"], pe.REJECTED)
        self.assertIn("rejected-emission", err)

    def test_close_plan_writes_the_superseding_summary_before_deleting(self):
        plan = pe.current_plan_id(self.root)
        ledger = pe.read_ledger(self.root)
        pe.emit(
            self.root,
            pe.event(plan=plan, unit=UNIT, date=DATE, family="RRR", ordinal=1,
                     marker="approve", artifact="REVIEW-05-010"),
            ledger=ledger,
        )
        code, record, _ = self.run_command(close_plan=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(record["superseding_summaries"]), 1)
        summary = record["superseding_summaries"][0]
        self.assertEqual(summary["result"], pe.WRITTEN)
        self.assertEqual(summary["record"]["f"]["PAD"][1], "observed")
        self.assertIn("PAD 0 observed", summary["row"])
        self.assertEqual(len(record["closing_projection"]["rows"]), 1)
        self.assertTrue(record["log_deleted"])
        self.assertFalse(record["retained"])
        self.assertFalse(pe.log_path(self.root).exists())


class ContainmentTests(unittest.TestCase):
    """The ledger is read from its own path, or not at all.

    A reader that follows a link out of ``.cartopian`` reports another file's
    contents as this project's evidence. Every refusal below names its rule
    and returns an empty ledger rather than a guess.
    """

    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.plan = pe.current_plan_id(self.root)
        self.log = pe.log_path(self.root)
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def a_record_line(self):
        return pe.serialize(
            pe.event(
                plan=self.plan, unit=UNIT, date=DATE, family="PCC", ordinal=1, size=7
            )
        )

    def test_a_symlinked_ledger_is_refused_not_followed(self):
        elsewhere = self.scaffold.root / "elsewhere.log"
        elsewhere.write_text(self.a_record_line(), encoding="utf-8")
        self.log.symlink_to(elsewhere)
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["ledger-uncontained"])
        self.assertEqual(pe.project_units(ledger).rows, [])

    def test_a_symlinked_evidence_directory_is_refused(self):
        outside = self.scaffold.root / "outside"
        outside.mkdir()
        (outside / pe.LOG_BASENAME).write_text(self.a_record_line(), encoding="utf-8")
        for child in sorted(self.log.parent.iterdir()):
            child.unlink()
        self.log.parent.rmdir()
        self.log.parent.symlink_to(outside, target_is_directory=True)
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["ledger-uncontained"])

    def test_a_non_regular_ledger_is_named_never_read(self):
        self.log.mkdir()
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["ledger-uncontained"])

    def test_a_hardlinked_ledger_is_refused(self):
        self.log.write_text(self.a_record_line(), encoding="utf-8")
        os.link(self.log, self.scaffold.root / "second-name.log")
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["ledger-uncontained"])

    def test_a_ledger_that_is_not_utf_8_is_reported_not_decoded(self):
        self.log.write_bytes(b'{"v":1,"k":"E","p":"PLAN-001","u":"\xff\xfe"}\n')
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["ledger-unreadable"])


class MalformedRecordRecoveryTests(unittest.TestCase):
    """A record this schema cannot interpret is named, and its unit is unavailable.

    Passing ``v``, ``p``, and ``k`` is not the same as being interpretable. A
    record admitted on those three alone moves the failure out of the reader
    and into a bounded query, where it is a crash instead of an answer.
    """

    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.plan = pe.current_plan_id(self.root)
        self.log = pe.log_path(self.root)
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def write(self, *records):
        self.log.write_text(
            "".join(
                json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
                for r in records
            ),
            encoding="utf-8",
        )

    def test_a_summary_missing_its_families_is_malformed_not_admitted(self):
        self.write({"v": 1, "k": "U", "p": self.plan, "u": UNIT})
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["record-malformed"])
        self.assertEqual(ledger.unavailable_units, [UNIT])

    def test_the_bounded_projection_answers_instead_of_crashing(self):
        self.write({"v": 1, "k": "U", "p": self.plan, "u": UNIT})
        answer = pe.project_units(pe.read_ledger(self.root), units=[UNIT])
        self.assertEqual(answer.rows, [])
        self.assertEqual(answer.unavailable_units, [UNIT])

    def test_an_event_outside_its_closed_domain_is_malformed(self):
        self.write(
            {
                "v": 1, "k": "E", "p": self.plan, "u": UNIT, "d": DATE,
                "f": "RRR", "o": 1, "x": "looks-fine", "a": "REVIEW-05-010",
            }
        )
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["record-malformed"])
        self.assertEqual(ledger.unavailable_units, [UNIT])

    def test_an_address_missing_its_artifact_is_malformed(self):
        self.write(
            {
                "v": 1, "k": "D", "p": self.plan, "u": UNIT, "d": DATE,
                "f": "OMR", "t": "det", "n": "D2", "c": "C03",
                "r": "upstream-intent-uncovered",
            }
        )
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual(pe.project_determinations(pe.read_ledger(self.root)).rows, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["record-malformed"])

    def test_a_well_formed_record_beside_a_malformed_one_still_reads(self):
        good = pe.event(
            plan=self.plan, unit=UNIT, date=DATE, family="PCC", ordinal=1, size=7
        )
        self.write(good, {"v": 1, "k": "U", "p": self.plan, "u": "TASK-05-011"})
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [good])
        self.assertEqual(ledger.unavailable_units, ["TASK-05-011"])


class SingleWriteAppendTests(unittest.TestCase):
    """§ 12.1: one record is one append of one complete line."""

    def setUp(self):
        self.scaffold = project_scaffold()
        self.addCleanup(self.scaffold.cleanup)
        self.root = self.scaffold.project_root
        self.plan = pe.current_plan_id(self.root)

    def record(self, ordinal=1):
        return pe.event(
            plan=self.plan, unit=UNIT, date=DATE, family="PCC",
            ordinal=ordinal, size=4812,
        )

    def test_each_record_is_written_by_exactly_one_write_call(self):
        writes = []
        real_write = os.write

        def counting_write(fd, payload):
            writes.append(payload)
            return real_write(fd, payload)

        with mock.patch.object(os, "write", counting_write):
            for ordinal in (1, 2):
                self.assertEqual(
                    pe.emit(self.root, self.record(ordinal))["result"], pe.WRITTEN
                )
        self.assertEqual(
            writes,
            [pe.serialize(self.record(1)).encode(), pe.serialize(self.record(2)).encode()],
        )

    def test_a_short_write_is_a_failed_emission_never_a_resumed_one(self):
        real_write = os.write

        def short_write(fd, payload):
            return real_write(fd, payload[:-1])

        with mock.patch.object(os, "write", short_write):
            outcome = pe.emit(self.root, self.record())
        self.assertEqual(outcome["result"], pe.REJECTED)
        ledger = pe.read_ledger(self.root)
        self.assertEqual(ledger.records, [])
        self.assertEqual([e["rule"] for e in ledger.errors], ["torn-final-record"])

    def test_an_append_never_follows_a_symlinked_ledger(self):
        elsewhere = self.scaffold.root / "elsewhere.log"
        elsewhere.write_text("", encoding="utf-8")
        log = pe.log_path(self.root)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.symlink_to(elsewhere)
        self.assertEqual(pe.emit(self.root, self.record())["result"], pe.REJECTED)
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "")

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
