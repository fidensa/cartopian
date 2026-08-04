"""Classify bounded observable task facts through the risk authority."""
import argparse

from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, stderr_guard
from cli.risk_contract import RiskContractError, classify_risk, observation_choices


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Classify five bounded observable task facts by dominance and return "
        "the deterministic evidence, review, operator-gate, and contingency expectations."
    )
    for observation, states in observation_choices().items():
        parser.add_argument(f"--{observation}", required=True, choices=states)
        parser.add_argument(
            f"--{observation}-fact",
            required=True,
            help=f"Bounded supporting fact identity for {observation}",
        )


def handler(args: argparse.Namespace) -> int:
    records = []
    for observation in observation_choices():
        dest = observation.replace("-", "_")
        records.append(
            {
                "observation": observation,
                "state": getattr(args, dest),
                "supporting_fact": getattr(args, f"{dest}_fact"),
            }
        )
    try:
        result = classify_risk(records)
    except RiskContractError as exc:
        stderr_guard(str(exc))
        return EXIT_FAIL
    emit_record({"action": "classify-risk", **result})
    return EXIT_OK
