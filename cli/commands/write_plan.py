"""`cartopian write-plan <project-root>` (G2).

Structured writer for ``IMPLEMENTATION_PLAN.md``. Front-end over the
mediated-write primitive; destination implied by the verb.
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    _writers.add_handle_arg(subparser)


def handler(args: argparse.Namespace) -> int:
    from cli import numbering_contract

    lock_root, lock_err = _writers.validated_root(args.project_root)
    if lock_err is not None:
        _writers.stderr("usage", lock_err)
        return _writers.EXIT_USAGE
    # Plan lock reuses the confirmation bound at requirements lock, or binds
    # it now when requirements were never written through this path.
    lock, lock_code = _writers.lock_confirmation(lock_root, getattr(args, "handle", None))
    if lock_code is not None:
        return lock_code

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
        plan_path = _root / "IMPLEMENTATION_PLAN.md"
        if plan_path.is_file():
            try:
                existing_content = plan_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                _writers.stderr(
                    "guard", f"implementation-plan-unreadable: {exc}"
                )
                return _writers.EXIT_FAIL
        else:
            existing_content = ""
        findings = numbering_contract.validate_plan_revision(
            existing_content, content
        )
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
        request_gate_satisfied=lock is not None,
        post_write=lambda project_root, details: _writers.bind_lock_confirmation(
            project_root, lock, details
        ),
    )
