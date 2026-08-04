"""Build the fresh bounded context for a critical independent challenge."""
import argparse
import json
from pathlib import Path

from cli.commands.resolve_config import _CliError, resolve_project_configuration
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard, stderr_usage
from cli.risk_contract import (
    DEFAULT_MAX_REVIEW_CONTEXT_BYTES,
    RiskContractError,
    build_adversarial_review_context,
)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Build fresh bounded critical-review context from only the artifact and "
        "governing contract; the author's conclusion has no input channel."
    )
    parser.add_argument("project_root", help="Absolute Cartopian project root")
    parser.add_argument("--artifact", required=True, help="Absolute artifact path")
    parser.add_argument(
        "--governing-contract", required=True, help="Absolute governing-contract path"
    )
    parser.add_argument(
        "--risk-result",
        required=True,
        help="JSON object returned by classify-risk for a critical result",
    )
    parser.add_argument(
        "--max-context-bytes",
        type=int,
        default=DEFAULT_MAX_REVIEW_CONTEXT_BYTES,
        help="Positive combined UTF-8 payload limit",
    )


def handler(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    artifact = Path(args.artifact)
    contract = Path(args.governing_contract)
    if not root.is_absolute():
        stderr_usage("project_root must be absolute")
        return EXIT_USAGE
    if not artifact.is_absolute() or not contract.is_absolute():
        stderr_usage("--artifact and --governing-contract must be absolute")
        return EXIT_USAGE
    try:
        risk_result = json.loads(args.risk_result)
    except json.JSONDecodeError:
        stderr_usage("--risk-result must be a JSON object")
        return EXIT_USAGE
    if not isinstance(risk_result, dict):
        stderr_usage("--risk-result must be a JSON object")
        return EXIT_USAGE
    try:
        resolved = resolve_project_configuration(root.resolve())
        allowed_roots = [root.resolve(), *map(Path, resolved["work_roots"].values())]
        context = build_adversarial_review_context(
            artifact,
            contract,
            allowed_roots=allowed_roots,
            risk_result=risk_result,
            max_context_bytes=args.max_context_bytes,
        )
    except _CliError as exc:
        stderr_error(exc.message)
        return exc.exit_code
    except RiskContractError as exc:
        stderr_guard(str(exc))
        return EXIT_FAIL
    emit_record({"action": "adversarial-review-context", **context})
    return EXIT_OK
