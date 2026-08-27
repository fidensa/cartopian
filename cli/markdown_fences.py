"""Correct fenced-code-block tracking for the prompt pipeline.

The prompt pipeline embeds machine-generated fenced blocks (report skeletons,
typed assignment-input payloads) whose contents may themselves contain fenced
blocks. A boolean "inside a fence" toggle mis-parses that nesting: a shorter
inner ```` ``` ```` line would incorrectly close a longer generated
````` ```` ````` fence. This tracker follows the CommonMark rules that matter
for that case:

- An opening fence is a run of at least three backticks or tildes; a
  backtick-opened fence's info string may not contain a backtick.
- A fence is closed only by a run of the *same* character, at least as long
  as the opener, with nothing but whitespace after it.
- Every other line inside an open fence — including shorter or
  different-character fence-like lines — is content.

Leading whitespace is accepted loosely (``^\\s*``) to match the pipeline's
historical behavior rather than CommonMark's 0-3 space rule.

Stdlib only.
"""
from __future__ import annotations

import re
from typing import Optional

_DELIMITER_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")


class FenceTracker:
    """Line-by-line fence state for one document scan.

    Feed every line in order. ``feed`` returns True exactly when the line is
    a fence delimiter (an opener or the matching closer); on True the caller
    should usually treat the line as structure, not content. ``in_fence`` is
    the state *after* the most recent ``feed``.
    """

    __slots__ = ("_char", "_length")

    def __init__(self) -> None:
        self._char = ""
        self._length = 0

    @property
    def in_fence(self) -> bool:
        return self._length > 0

    def feed(self, line: str) -> bool:
        match = _DELIMITER_RE.match(line)
        if match is None:
            return False
        run, rest = match.group(1), match.group(2)
        char = run[0]
        if self.in_fence:
            if (
                char == self._char
                and len(run) >= self._length
                and not rest.strip()
            ):
                self._char, self._length = "", 0
                return True
            return False
        if char == "`" and "`" in rest:
            return False
        self._char, self._length = char, len(run)
        return True


def opening_info(line: str) -> Optional[str]:
    """The info string of a line that would *open* a fence, else None.

    State-free helper for callers that have just observed ``feed`` return
    True with ``in_fence`` becoming true: the returned info string is the
    text after the delimiter run, stripped.
    """
    match = _DELIMITER_RE.match(line)
    if match is None:
        return None
    run, rest = match.group(1), match.group(2)
    if run[0] == "`" and "`" in rest:
        return None
    return rest.strip()
