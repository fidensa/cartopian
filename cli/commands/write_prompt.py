"""`cartopian write-prompt <project-root> --prompt-id PROMPT-...`.

Structured writer for prompt files ``prompts/PROMPT-*.md``, covering both
variants:

- **task** prompts — ``PROMPT-NN-NNN.md``
- **planning** prompts — ``PROMPT-PLAN-NNN[-slug].md``

The grammar mirrors ``delete-prompt`` so the writer and the deleter agree on
what a valid prompt filename is. The PM supplies the id, not a path; the
destination subtree is the allowlisted ``prompt`` dest_kind. Re-issuing
overwrites in place (assign/re-handoff revision).
"""
import argparse

from cli.commands import _writers


def configure_parser(subparser: argparse.ArgumentParser) -> None:
    _writers.add_content_args(subparser)
    subparser.add_argument(
        "--prompt-id",
        required=True,
        help=(
            "Prompt id without extension, e.g. PROMPT-NN-NNN (task) or "
            "PROMPT-PLAN-NNN-some-slug (planning checkpoint)"
        ),
    )


def handler(args: argparse.Namespace) -> int:
    prompt_id = args.prompt_id
    if not _writers.PROMPT_ID_RE.match(prompt_id):
        _writers.stderr(
            "usage",
            "--prompt-id must match PROMPT-NN-NNN or PROMPT-PLAN-NNN[-slug]; "
            f"got: {prompt_id!r}",
        )
        return _writers.EXIT_USAGE
    variant = "planning" if prompt_id.startswith("PROMPT-PLAN-") else "task"
    return _writers.perform_write(
        args,
        action="write-prompt",
        dest_kind="prompt",
        relative_target=f"{prompt_id}.md",
        extra_details={"prompt_id": prompt_id, "variant": variant},
    )
