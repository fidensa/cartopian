"""`cartopian write-phase <project-root> --phase-id PHASE-NN`.

Structured writer for phase files ``phases/PHASE-NN.md``. The filename
is derived from the validated ``--phase-id`` (the PM supplies an id, not a
path); the destination subtree is the allowlisted ``phase`` dest_kind.
"""
import argparse
from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--phase-id",
        required=True,
        help="Phase id matching the grammar PHASE-NN, e.g. PHASE-01",
    )
    _writers.add_source_arg(subparser)


def handler(args: argparse.Namespace) -> int:
    phase_id = args.phase_id
    if not _writers.PHASE_CANONICAL_ID_RE.match(phase_id):
        _writers.stderr(
            "usage",
            f"--phase-id must match PHASE-NN grammar; got: {phase_id!r}",
        )
        return _writers.EXIT_USAGE

    root, err = _writers.validated_root(args.project_root)
    if err is not None:
        _writers.stderr("usage", err)
        return _writers.EXIT_USAGE
    content, cerr = _writers.resolve_content(args)
    if cerr is not None:
        _writers.stderr("usage", cerr)
        return _writers.EXIT_USAGE
    content, source_id, serr = _writers.apply_source_stamp(args, root, content)
    if serr is not None:
        _writers.stderr(*serr)
        return _writers.EXIT_USAGE if serr[0] == "usage" else _writers.EXIT_FAIL

    from cli import numbering_contract

    if numbering_contract.activation_state()["active"]:
        if isinstance(content, bytes):
            try:
                phase_text = content.decode("utf-8")
            except UnicodeDecodeError:
                _writers.stderr("guard", "phase body must be valid UTF-8 text")
                return _writers.EXIT_FAIL
        else:
            phase_text = content
        try:
            plan_text = (root / "IMPLEMENTATION_PLAN.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _writers.stderr(
                "guard", f"implementation-plan-unreadable: {exc}"
            )
            return _writers.EXIT_FAIL
        findings = numbering_contract.validate_phase_projection(
            plan_text, phase_id, phase_text
        )
        if findings:
            finding = findings[0]
            _writers.stderr(
                "guard", f"{finding['classification']}: {finding['detail']}"
            )
            return _writers.EXIT_FAIL

    filename = f"{phase_id}.md"
    matches = _writers.identifier_files(root / "phases", phase_id)
    if len(matches) > 1:
        _writers.stderr(
            "guard",
            f"phase-id-collision: {phase_id} resolves to multiple files: "
            + ", ".join(str(path) for path in matches),
        )
        return _writers.EXIT_FAIL
    if matches and matches[0].name != filename:
        _writers.stderr(
            "guard",
            f"artifact-name-migration-required: {matches[0]} must be migrated to {filename}",
        )
        return _writers.EXIT_FAIL

    extra_details = {"phase_id": phase_id}
    if source_id is not None:
        extra_details["source"] = source_id
    return _writers.perform_write(
        args,
        action="write-phase",
        dest_kind="phase",
        relative_target=f"{phase_id}.md",
        content=content,
        extra_details=extra_details,
    )
