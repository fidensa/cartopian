"""`cartopian acceptance-trace <project-root> --task <path>`.

Derive, validate, and project one task's acceptance-to-source trace.

The command is the single surface both the CLI and the MCP tool registry
expose for the traceability contract, so the two carry identical identities,
ordering, fields, exit semantics, and byte receipts. It never writes: the
record set is PM-authored in the task's ``## Upstream trace`` section, and
this command reads, binds, validates, and measures it.

Exit semantics follow the contract's fail-closed routing: a structural code
blocks at the boundary that detected it (``EXIT_FAIL``, with the code and the
offending identity on stderr), and a task that does not declare the contract
reports its declaration and exits ``EXIT_OK`` without inventing a trace.
"""
import argparse
from pathlib import Path

from cli import acceptance_trace as mechanism
from cli import artifact_paths, trace_binding
from cli.emit import emit_record
from cli.main import EXIT_FAIL, EXIT_OK, EXIT_USAGE, stderr_error, stderr_guard, stderr_usage

PROJECTIONS = ("coder", "reviewer", "trace", "criteria", "determinations", "diagnostic")


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.description = (
        "Derive and validate the bounded acceptance-to-source trace for one "
        "task, and project it for the coder or the reviewer. Structural errors "
        "fail closed at readiness; the D1/D2 determinations are recorded at "
        "closure by `review-intake`."
    )
    parser.add_argument(
        "project_root", help="Absolute path to the Cartopian project root"
    )
    parser.add_argument(
        "--task", required=True, help="Absolute path to the governing task file"
    )
    parser.add_argument(
        "--projection",
        default=None,
        choices=list(PROJECTIONS),
        help=(
            "Include one exact serialized body in the record. `coder` and "
            "`reviewer` are the two routine projections; `trace` is the hashed "
            "body; `diagnostic` is the on-demand failure body."
        ),
    )
    parser.add_argument(
        "--anchor",
        action="store_true",
        help="Report the reference-shape conformance anchor and exit.",
    )


def _body(binding: trace_binding.Binding, projection: str) -> str:
    trace = binding.trace
    assert trace is not None
    if projection == "coder":
        return trace.coder_projection()
    if projection == "reviewer":
        return trace.reviewer_projection()
    if projection == "trace":
        return trace.trace_body()
    if projection == "criteria":
        return trace.criterion_body()
    if projection == "determinations":
        return trace.completion_evidence(trace.determination_template())
    findings = trace.closure_findings()
    finding = (
        findings[0]
        if findings
        else mechanism.Finding("trace-identity-mismatch", "closure", "no finding")
    )
    return mechanism.diagnostic_body(trace, finding)


def handler(args: argparse.Namespace) -> int:
    if args.anchor:
        anchor = mechanism.conformance_anchor()
        emit_record(
            {
                "action": "acceptance-trace",
                "mode": "conformance-anchor",
                **{
                    key: (list(value) if isinstance(value, tuple) else value)
                    for key, value in anchor.items()
                },
            }
        )
        if not anchor["conforms"]:
            stderr_guard(
                "trace-unparseable: the reference-shape conformance anchor does "
                "not reproduce the accepted 143 + 2438 = 2581 byte measurement"
            )
            return EXIT_FAIL
        return EXIT_OK

    root = Path(args.project_root)
    if not root.is_absolute():
        stderr_usage("project_root must be absolute")
        return EXIT_USAGE
    root = root.resolve()
    if not (root / "cartopian.toml").is_file():
        stderr_error(f"project config not found: {root / 'cartopian.toml'}")
        return EXIT_FAIL
    # The task is a project artifact, not any readable file: an unrelated
    # absolute path, a traversal, or a symlink planted in `tasks/` would put a
    # foreign document behind every identity this command then reports.
    try:
        task, task_text = artifact_paths.task(root, args.task)
    except artifact_paths.ArtifactRefusal as refusal:
        stderr_guard(f"{refusal.rule}: {refusal.detail}")
        return EXIT_FAIL

    binding = trace_binding.bind(root, task, task_text=task_text)
    record = {
        "action": "acceptance-trace",
        "project_path": str(root),
        **binding.as_record(),
    }
    if binding.trace is not None and args.projection:
        body = _body(binding, args.projection)
        record["projection"] = {
            "name": args.projection,
            "bytes": mechanism.measure(body),
            "identity": mechanism.body_identity(body),
            "est_tokens": mechanism.est_tokens(mechanism.measure(body)),
            "body": body,
        }
    emit_record(record)
    if binding.refusal is not None:
        identity = f" [{binding.refusal.identity}]" if binding.refusal.identity else ""
        stderr_guard(f"{binding.refusal.code}: {binding.refusal.detail}{identity}")
        return EXIT_FAIL
    return EXIT_OK
