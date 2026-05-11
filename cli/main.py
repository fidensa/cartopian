"""Cartopian Core CLI dispatcher (FR-014 contract scaffolding).

Subcommand handlers are placeholders for TASK-01-005..011. This module
defines the exit-code contract, stderr-prefix helpers, and the argparse
surface every later command builds on.
"""
import argparse
import sys
from typing import List, Optional, Sequence

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_ENV = 3

SUBCOMMANDS: List[str] = [
    # FR-004
    "discover-projects",
    "generate-config",
    "move-task",
    "parse-report",
    "register-project",
    "resolve-config",
    "scaffold-project",
    "unregister-project",
    "validate-task-readiness",
    # FR-005
    "delete-prompt",
    "delete-report",
    "list-tasks",
]


def stderr_error(msg: str) -> None:
    sys.stderr.write(f"[error] {msg}\n")


def stderr_guard(msg: str) -> None:
    sys.stderr.write(f"[guard] {msg}\n")


def stderr_usage(msg: str) -> None:
    sys.stderr.write(f"[usage] {msg}\n")


class _UsageParser(argparse.ArgumentParser):
    """ArgumentParser that emits the [usage] stderr prefix on errors."""

    def error(self, message: str) -> None:  # pragma: no cover - exercised via subprocess
        if message.startswith("argument ") and "invalid choice" in message:
            # extract the offending subcommand name from argparse's message
            try:
                bad = message.split("invalid choice: ", 1)[1].split(" ", 1)[0].strip("'\"")
                stderr_usage(f"unknown subcommand: {bad}")
            except Exception:
                stderr_usage(message)
        else:
            stderr_usage(message)
        sys.exit(EXIT_USAGE)


def _placeholder(name: str):
    def handler(_args: argparse.Namespace) -> int:
        stderr_error(f"not yet implemented: {name}")
        return EXIT_FAIL

    return handler


def _real_handlers():
    """Map of subcommand name → (configure_parser, handler) for implemented commands.

    Imported lazily to avoid circular imports (command modules import EXIT_*
    constants from this module).
    """
    from cli.commands import resolve_config

    return {
        "resolve-config": (resolve_config.configure_parser, resolve_config.handler),
    }


def build_parser() -> _UsageParser:
    parser = _UsageParser(
        prog="cartopian",
        description="Cartopian Core CLI (scaffolding — subcommands not yet implemented)",
        add_help=True,
    )
    subparsers = parser.add_subparsers(dest="cmd", metavar="<subcommand>")
    real = _real_handlers()
    for name in SUBCOMMANDS:
        if name in real:
            sub = subparsers.add_parser(name, help=name)
            configure, handler = real[name]
            configure(sub)
            sub.set_defaults(_handler=handler)
        else:
            sub = subparsers.add_parser(name, help=f"{name} (not yet implemented)")
            sub.set_defaults(_handler=_placeholder(name))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(list(argv))
    if not getattr(args, "cmd", None):
        stderr_usage("no subcommand given; try 'cartopian --help'")
        return EXIT_USAGE
    handler = getattr(args, "_handler", None)
    if handler is None:
        stderr_usage(f"unknown subcommand: {args.cmd}")
        return EXIT_USAGE
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
