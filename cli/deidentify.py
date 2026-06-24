"""Deidentify PM artifacts before they are surfaced to an assignee (coder).

Cartopian project-management identifiers — ``TASK-NN-NNN``, ``SPEC-NN-NNN``,
plan refs ``PNN-KIND-NNN``, requirement refs (``FR-`` / ``NF-`` / ``RM-``),
decisions (``DEC-``), backlog (``BL-``), and prompt / report / review ids — are
bookkeeping that maps only to ephemeral PM data. When an assignee reads them
they get copied into product code (most often into code comments), where they
outlive the PM data that is archived and re-issued and decay into meaningless
litter.

A coder handoff is therefore deidentified: the prompt carries no identifiers
(see ``templates/PROMPT.md``), and any spec surfaced to the coder is rendered
through :func:`deidentify_spec`, which removes the traceability scaffolding (the
title id, the ``Plan refs:`` field, and the ``## References`` section) and strips
any inline identifier token, while leaving the work-contract prose (Problem,
Goal, Interface, Constraints, Test vectors / acceptance) intact.

Stdlib only.
"""
import re
from typing import List, Tuple

# Bare identifier alternation (no anchors). Kept in sync with the per-artifact
# grammars in ``cli/commands/_writers.py``. Longest-prefix-first where prefixes
# overlap (``PROMPT-PLAN`` before ``PROMPT`` etc.) so the alternation is
# unambiguous.
_ID = (
    r"(?:"
    r"TASK-\d{2}-\d{3}"
    r"|SPEC-\d{2}-\d{3}"
    r"|PHASE-\d{2}-[a-z0-9][a-z0-9-]*"
    r"|PROMPT-PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?"
    r"|PROMPT-\d{2}-\d{3}"
    r"|REVIEW-PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?"
    r"|REVIEW-\d{2}-\d{3}"
    r"|REPORT-PLAN-\d{3}(?:-[a-z0-9][a-z0-9-]*)?"
    r"|REPORT-\d{2}-\d{3}"
    r"|DEC-\d{3}"
    r"|BL-\d{3}"
    r"|RM-\d{3}"
    r"|FR-\d{3}"
    r"|NF-\d{3}"
    r"|P\d{2}-[A-Z]+-\d{3}"
    r")"
)

# A leading ``\b`` and a trailing ``(?![-\w])`` stop substring matches:
# ``TASK-NN-MMMM`` (too many digits) and ``xFR-NNN`` (non-word prefix) never match.
IDENTIFIER_RE = re.compile(r"\b" + _ID + r"(?![-\w])")

# Inline reference forms, removed whole so no dangling "(see )" / "per ," remains.
# A parenthetical group of ids, optionally introduced by see/per/cf/ref:
_PAREN_REF_RE = re.compile(
    r"\s*\((?:see\s+|per\s+|cf\.?\s+|ref\.?\s+)?"
    + _ID
    + r"(?:\s*,\s*" + _ID + r")*\s*\)",
    re.IGNORECASE,
)
# A bare "see/per/cf/ref ID[, ID]" phrase (no parens):
_PHRASE_REF_RE = re.compile(
    r"\b(?:see|per|cf\.?|ref\.?)\s+" + _ID + r"(?:\s*,\s*" + _ID + r")*",
    re.IGNORECASE,
)
# A leading "ID:" label after a bullet / numbered marker, e.g. "- FR-NNN: ...".
_LABEL_RE = re.compile(r"^(\s*(?:[-*]|\d+\.)\s+)" + _ID + r"\s*:\s*")

_TITLE_RE = re.compile(r"^#\s+(.*)$")
_TITLE_ID_PREFIX_RE = re.compile(r"^" + _ID + r"\s*:\s*")
_PLAN_REFS_LINE_RE = re.compile(r"^\s*Plan\s+refs?\s*:.*$", re.IGNORECASE)
_SECTION_RE = re.compile(r"^##\s+")
_REFERENCES_HEADING_RE = re.compile(r"^##\s+References\s*$", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

# Cleanup applied only to lines an inline substitution actually changed, so
# code/interface blocks (which we never touch — see fence tracking) keep their
# spacing.
_EMPTY_BRACKETS_RE = re.compile(r"\(\s*\)|\[\s*\]")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:])")
_MULTISPACE_RE = re.compile(r"  +")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _tidy(line: str) -> str:
    line = _EMPTY_BRACKETS_RE.sub("", line)
    line = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", line)
    line = _MULTISPACE_RE.sub(" ", line)
    return line.rstrip()


def scrub_identifiers(text: str) -> str:
    """Remove inline identifier references from a single run of prose.

    Strips parenthetical and ``see ...`` reference forms whole, then any
    remaining bare identifier token. Returns the cleaned text (callers tidy
    whitespace per line when they know the line is not inside a code fence).
    """
    text = _PAREN_REF_RE.sub("", text)
    text = _PHRASE_REF_RE.sub("", text)
    text = IDENTIFIER_RE.sub("", text)
    return text


def list_identifiers(text: str) -> List[str]:
    """Return the sorted, unique identifier tokens present in ``text``."""
    return sorted(set(IDENTIFIER_RE.findall(text)))


def deidentify_spec(text: str) -> Tuple[str, List[str]]:
    """Return ``(deidentified_text, redactions)`` for a spec body.

    Removes the traceability scaffolding and inline identifiers an assignee
    must not see, preserving the work-contract prose. ``redactions`` is the
    sorted unique list of identifier tokens that were present in the input.

    Operates line by line and never rewrites content inside fenced code blocks
    (```` ``` ```` / ``~~~``), so an interface example that happens to contain a
    token is left byte-for-byte intact.
    """
    redactions = list_identifiers(text)

    out: List[str] = []
    in_fence = False
    skip_section = False
    title_done = False

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            # Strip identifier tokens from code too (a token in an example
            # comment/string is the verbatim text a coder would copy), but never
            # tidy whitespace inside a fence — code indentation is significant.
            out.append(scrub_identifiers(line))
            continue

        # Section skipping: drop the whole "## References" section until the
        # next "## " heading (or end of input).
        if skip_section:
            if _SECTION_RE.match(line):
                skip_section = False  # fall through to handle this heading
            else:
                continue
        if _REFERENCES_HEADING_RE.match(line):
            skip_section = True
            continue

        # Title: strip a leading "SPEC-NN-NNN:" id from the first H1.
        if not title_done:
            m = _TITLE_RE.match(line)
            if m:
                title_done = True
                heading = _TITLE_ID_PREFIX_RE.sub("", m.group(1)).strip()
                if not heading or IDENTIFIER_RE.fullmatch(heading):
                    heading = "Specification"
                out.append(f"# {heading}")
                continue

        # Drop the "Plan refs:" metadata line entirely.
        if _PLAN_REFS_LINE_RE.match(line):
            continue

        original = line
        # Strip a leading "- ID: " / "1. ID: " label, keeping the marker.
        line = _LABEL_RE.sub(r"\1", line)
        line = scrub_identifiers(line)
        if line != original:
            line = _tidy(line)
        out.append(line)

    result = "\n".join(out)
    # Collapse blank runs created by removed lines; preserve a trailing newline.
    result = _BLANK_RUN_RE.sub("\n\n", result)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result, redactions
