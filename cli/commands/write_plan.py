"""`cartopian write-plan <project-root>` (G2).

Structured writer for ``IMPLEMENTATION_PLAN.md``. Front-end over the
mediated-write primitive; destination implied by the verb.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)


def handler(args: argparse.Namespace) -> int:
    from cli import numbering_contract

    if numbering_contract.activation_state()["active"]:
        _root, error = _writers.validated_root(args.project_root)
        content, content_error = _writers.resolve_content(args)
        if error is not None or content_error is not None:
            _writers.stderr("usage", error or content_error or "invalid input")
            return _writers.EXIT_USAGE
        if isinstance(content, bytes):
            try:
                content = content.decode("utf-8")
            except UnicodeDecodeError:
                _writers.stderr("guard", "implementation plan must be valid UTF-8 text")
                return _writers.EXIT_FAIL
        findings = numbering_contract.validate_plan_allocations(content)
        if findings:
            finding = findings[0]
            _writers.stderr(
                "guard", f"{finding['classification']}: {finding['detail']}"
            )
            return _writers.EXIT_FAIL
    return _writers.perform_write(
        args,
        action="write-plan",
        dest_kind="plan",
        relative_target="IMPLEMENTATION_PLAN.md",
    )
