"""Regression: a terminal wait result must name final report bytes.

Reproduces the REPORT-05-010 race deterministically: the child publishes a
syntactically complete report and then rewrites it during the supervisor's
grace/reap period. The old contract opened the publication boundary the
moment a complete report was observed, so a wait could return a terminal
``accepted`` with a content identity that subsequently changed and immediate
post-wait parsing read different (even malformed) bytes. The fixed contract
holds a complete report nonterminal while the launch is ``state=running`` and
publishes the retained snapshot and ``state=exited`` only after the child is
gone, so every terminal observation binds immutable bytes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cli import handoff_observer, output_safety, report_identity


def _report_text(evidence: str) -> str:
    return (
        "# REPORT-01-001\n\n"
        "Status: complete\n\n"
        "## Identity\n\n"
        "- Work root: n/a\n\n"
        "## Completion evidence\n\n"
        f"{evidence}\n\n"
        "## Remaining risks\n\n"
        "none\n\n"
        "## Ready to close\n\n"
        "yes\n"
    )


FIRST_PUBLICATION = _report_text("first publication — still mutable")
FINAL_PUBLICATION = _report_text("final publication after rewrite")


class TerminalBytesAreFinalTests(unittest.TestCase):
    def test_child_rewrite_during_grace_never_leaks_into_terminal_result(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="cartopian-race-") as tmp:
            root = Path(tmp)
            report = root / "REPORT-01-001.md"
            status = Path(str(report) + ".status")
            log_path = Path(str(report) + ".launch.log")
            # The running marker dispatch publishes before launch.
            status.write_text(
                "state=running\n"
                "launch_id=race-launch\n"
                "expected_variant=task\n"
                "guarantee_scope=retained-launch-log\n"
                "retained_log_ready=false\n",
                encoding="utf-8",
            )
            # The child: publish a complete report, rewrite it inside the
            # supervisor's grace window (atomic replace, so no torn reads),
            # then hold stdout open until reaped.
            child = root / "child.py"
            child.write_text(
                "import os, time\n"
                f"report = {str(report)!r}\n"
                "def publish(text):\n"
                "    tmp = report + '.childtmp'\n"
                "    with open(tmp, 'w', encoding='utf-8') as fh:\n"
                "        fh.write(text)\n"
                "    os.replace(tmp, report)\n"
                f"publish({FIRST_PUBLICATION!r})\n"
                "time.sleep(0.3)\n"
                f"publish({FINAL_PUBLICATION!r})\n"
                "while True:\n"
                "    time.sleep(0.1)\n",
                encoding="utf-8",
            )

            observations = []
            stop = threading.Event()

            def observe_continuously() -> None:
                while not stop.is_set():
                    observation = handoff_observer.observe_once(
                        report, expected_variant="task"
                    )
                    observations.append(observation)
                    time.sleep(0.005)

            observer_thread = threading.Thread(target=observe_continuously)
            observer_thread.start()
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        output_safety.REPORT_POLL_ENV: "0.1",
                        output_safety.REPORT_GRACE_POLLS_ENV: "5",
                    },
                ):
                    result = output_safety.supervise(
                        [sys.executable, str(child)],
                        status_path=status,
                        report_path=report,
                        log_path=log_path,
                        launch_id="race-launch",
                        expected_variant="task",
                        limits=output_safety.OutputLimits(),
                    )
                # Give the observer a final pass over the exited state.
                time.sleep(0.05)
            finally:
                stop.set()
                observer_thread.join()

            final_identity = report_identity.content_identity(
                report.read_text(encoding="utf-8")
            )
            self.assertEqual(
                final_identity,
                report_identity.content_identity(FINAL_PUBLICATION),
            )
            self.assertEqual(result.exit_code, 0)

            terminal = [obs for obs in observations if obs.terminal]
            nonterminal_complete = [
                obs
                for obs in observations
                if not obs.terminal
                and obs.report.publication_state == "complete"
            ]
            # The barrier held: complete-but-mutable bytes were observed and
            # stayed nonterminal (on the old contract the very first of these
            # was terminal `accepted` with the first publication's identity).
            self.assertTrue(nonterminal_complete)
            self.assertIn(
                report_identity.content_identity(FIRST_PUBLICATION),
                {
                    obs.report.content_identity
                    for obs in nonterminal_complete
                },
            )
            # Every terminal observation names the final, immutable bytes.
            self.assertTrue(terminal)
            for obs in terminal:
                self.assertEqual(obs.classification, "accepted")
                self.assertEqual(obs.report.content_identity, final_identity)

            fields = dict(
                line.split("=", 1)
                for line in status.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            self.assertEqual(fields["state"], "exited")
            self.assertEqual(fields["retained_log_ready"], "true")
            self.assertTrue(log_path.is_file())


if __name__ == "__main__":
    unittest.main()
