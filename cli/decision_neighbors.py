"""Topical proximity between one decision and the live locked decision set.

Cartopian binds a task or spec to the sources its author *declared*. That is
deliberate — declared sources are what `acceptance-trace` can verify. But it
leaves one gap the protocol has no other mechanical answer for: a decision the
author never thought to name is never checked against, so a new ruling can
contradict a locked one and every structural gate still passes. The backstop
today is the independent planning review, which is judgment, not detection.

This module is the detection half. Given a decision body, it ranks the live
locked decisions by topical proximity and returns a **fixed, small** number of
them. The PM reads the titles at the moment it writes a decision, which is the
only moment the answer is actionable, and opens a file only when a title looks
live. Nothing here is loaded at session startup.

Why ranking and not a threshold
-------------------------------
Measured over the ``Supersedes:`` links in real Cartopian projects — where the
superseded decision is, by construction, one the author had to consider — the
similarity scores of known-related pairs sit inside the score distribution of
unrelated ones (median 0.040 against a top-candidate median of 0.036). No
absolute cut separates them: a threshold high enough to stay quiet drops most
true positives, and one low enough to keep them fires on a third of all writes.

Rank, however, separates cleanly. Over those same links the related decision
placed in the top three 23 times out of 24, and widening the window to eight
added nothing (the single miss sits at rank 16). So the budget is a constant
three. The cost of the surface is therefore fixed and predictable rather than
scaling with corpus size or with how alarming the text happens to look.

Scoring is a cosine over length-normalized tf-idf across three weighted zones
of each decision — title, ruling, and context — using single words and adjacent
word pairs. Word pairs carry more weight than single words because a phrase is
far more discriminating than either of its halves. Standard library only.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from cli import trace_binding

#: How many neighbors one decision surfaces. See the module docstring: this is
#: a measured constant, not a tunable — recall plateaus at three.
NEIGHBOR_BUDGET = 3

#: Upper bound on the pair list handed to a planning reviewer, so the
#: projection stays predictable on a large decision set.
REVIEW_PAIR_CAP = 20

#: Longest title carried in a projection. A decision title is a signal, not the
#: decision; past this length the id is the cheaper way to read the rest, and
#: the cap is what keeps a projection's size predictable on any corpus.
TITLE_CAP = 120

#: Longest shared term carried in a row. A term is a word or an adjacent word
#: pair drawn from a decision body, so nothing structurally bounds its length;
#: this cap is what makes a projection's worst-case size computable rather than
#: merely typical.
TERM_CAP = 48

#: Shared terms reported per neighbor row. Enough to show *why* a decision
#: surfaced without opening it; few enough to keep a row on one line.
SHARED_TERMS_PER_ROW = 3

_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_WORD_RE = re.compile(r"[a-z][a-z0-9_]{2,}")
_DEC_REF_RE = re.compile(r"DEC-\d{3}")

# Protocol identifiers and content digests are addresses, not subject matter.
# Two decisions that both cite TASK-03-026 are not thereby about the same
# thing, so they are stripped before any term is counted.
_IDENTIFIER_RE = re.compile(
    r"\b(?:DEC|TASK|SPEC|PHASE|PLAN|REQ|BL|FR|NF|REPORT|REVIEW|PROMPT"
    r"|BUILD|DESIGN|RESEARCH|TEST|RELEASE|VERIFY|CORRECTIVE)-[0-9A-Za-z-]+",
    re.I,
)
_DIGEST_RE = re.compile(r"\b[0-9a-f]{8,}\b")

#: Zone weights. A title is a decision's own one-line summary of its subject,
#: so it is the densest signal; context is history and argues around the ruling
#: rather than stating it, so it is discounted but not discarded.
ZONE_WEIGHT: Dict[str, float] = {"title": 3.0, "ruling": 1.0, "context": 0.4}

#: Multiplier applied to adjacent word pairs relative to single words.
BIGRAM_WEIGHT = 2.5

#: Ordinary English glue plus the vocabulary every Cartopian decision shares by
#: virtue of being a Cartopian decision. Neither kind distinguishes subjects, so
#: both would otherwise make every decision look related to every other.
_STOPWORDS = frozenset("""
the and for that with this from are was were not but its has have had you your
they them their there then than when which who whom whose what where how why
all any both each few more most other some such only own same too very can will
just should now also into onto over under above below between during before
after again further once here does did doing done being been because while
about against through upon per via out off down one two three four five six
seven eight nine ten first second third new old use used using make made must
may might shall would could within without across toward towards already still
ever never always
decision decisions decided record records recorded status locked open
supersedes date context consequences project projects cartopian operator work
working note notes item items file files path paths line lines change changes
changed keep keeps kept name names named set sets setting stay stays remain
remains
""".split())


def _sections(text: str) -> Tuple[str, str, str]:
    """Split a decision into ``(title, ruling, context)``.

    The ruling is ``## Decision`` plus ``## Consequences`` — what was actually
    ruled. A decision whose headings do not match the template falls back to
    its whole body rather than scoring as empty.
    """
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    # Drop the "DEC-NNN: " prefix so the identifier does not become a term.
    if ":" in title:
        title = title.split(":", 1)[1].strip()

    current: Optional[str] = None
    ruling: List[str] = []
    context: List[str] = []
    for line in text.splitlines():
        heading = _H2_RE.match(line)
        if heading:
            current = heading.group(1).strip().lower()
            continue
        if current in ("decision", "consequences"):
            ruling.append(line)
        elif current == "context":
            context.append(line)

    body = "\n".join(ruling).strip()
    if not body:
        body = text
    return title, body, "\n".join(context).strip()


def _terms(sections: Tuple[str, str, str]) -> Dict[Tuple[str, str], float]:
    """Weighted ``{(kind, term): zone weight}`` map for one decision.

    A term appearing in several zones keeps its strongest zone weight rather
    than accumulating, so a long decision cannot outweigh a focused one simply
    by repeating itself.
    """
    accumulated: Dict[Tuple[str, str], float] = {}
    for zone, chunk in zip(("title", "ruling", "context"), sections):
        if not chunk:
            continue
        weight = ZONE_WEIGHT[zone]
        cleaned = _DIGEST_RE.sub(" ", _IDENTIFIER_RE.sub(" ", chunk.lower()))
        words = [w for w in _WORD_RE.findall(cleaned) if w not in _STOPWORDS]
        for word in words:
            key = ("u", word)
            if weight > accumulated.get(key, 0.0):
                accumulated[key] = weight
        for left, right in zip(words, words[1:]):
            key = ("b", f"{left} {right}")
            if weight > accumulated.get(key, 0.0):
                accumulated[key] = weight
    return accumulated


def corpus(project_root: Path) -> Dict[str, str]:
    """``{DEC-NNN: body}`` for every decision worth reconciling against.

    Liveness here is **retrieval** liveness, which is deliberately wider than
    the **authorization** liveness `trace_binding.out_of_plan_dispositions`
    applies. That reader must refuse to act on anything but an explicit
    ``locked`` ruling, so an absent or unrecognized ``Status:`` correctly
    authorizes nothing. This reader is answering a different question: which
    existing rulings might the one being written contradict. Dropping a
    governing decision because its header is malformed is pure loss, with no
    safety gained — nothing is authorized by surfacing it for a human to read.

    So the rule is: exclude a decision a later one retires through
    ``Supersedes:``, and exclude a decision that declares itself ``open``,
    because a proposal is not yet a ruling. Everything else is included,
    malformed header and all, and `plan-audit` reports the malformed ones
    separately so they get fixed rather than silently tolerated.
    """
    texts = trace_binding.decision_bodies(project_root)
    retired = trace_binding.superseded_ids(texts)
    return {
        stem: text
        for stem, text in texts.items()
        if stem not in retired
        and trace_binding.decision_header(text, "Status").lower() != "open"
    }


def _idf(vectors: Iterable[Dict[Tuple[str, str], float]], size: int) -> Dict[Tuple[str, str], float]:
    """Smoothed inverse document frequency.

    The ``+1`` / ``+0.5`` smoothing matters at small corpus sizes: with a plain
    ``log(n/df)`` every term in a two-decision project scores exactly zero, and
    the first few decisions of every project would surface nothing at all.
    """
    frequency: Counter = Counter()
    for vector in vectors:
        frequency.update(vector.keys())
    return {key: math.log((size + 1.0) / (count + 0.5)) for key, count in frequency.items()}


def _unit_vector(
    accumulated: Dict[Tuple[str, str], float],
    idf: Dict[Tuple[str, str], float],
    default: float,
) -> Dict[Tuple[str, str], float]:
    vector = {
        key: zone_weight * (BIGRAM_WEIGHT * idf.get(key, default) if key[0] == "b" else idf.get(key, default))
        for key, zone_weight in accumulated.items()
    }
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    return {key: value / norm for key, value in vector.items()}


def _rank(
    target: Dict[Tuple[str, str], float],
    candidates: Dict[str, Dict[Tuple[str, str], float]],
    idf: Dict[Tuple[str, str], float],
    size: int,
    *,
    exclude: Optional[str] = None,
) -> List[Tuple[float, str, List[str]]]:
    default = math.log((size + 1.0) / 0.5)
    target_vector = _unit_vector(target, idf, default)
    ranked: List[Tuple[float, str, List[str]]] = []
    for name, accumulated in candidates.items():
        if name == exclude:
            continue
        candidate_vector = _unit_vector(accumulated, idf, default)
        smaller, larger = (
            (target_vector, candidate_vector)
            if len(target_vector) <= len(candidate_vector)
            else (candidate_vector, target_vector)
        )
        score = 0.0
        contributions: List[Tuple[float, str]] = []
        for key, value in smaller.items():
            other = larger.get(key)
            if other:
                product = value * other
                score += product
                contributions.append((product, key[1]))
        if score > 0.0:
            contributions.sort(reverse=True)
            ranked.append((
                score,
                name,
                [term[:TERM_CAP] for _, term in contributions[:SHARED_TERMS_PER_ROW]],
            ))
    # Sort by descending score, then by id, so equal scores order deterministically.
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return ranked


def _title(text: str) -> str:
    title = _sections(text)[0]
    return title if len(title) <= TITLE_CAP else title[: TITLE_CAP - 1].rstrip() + "\u2026"


def neighbors(
    project_root: Path,
    body: str,
    *,
    exclude_id: Optional[str] = None,
    budget: int = NEIGHBOR_BUDGET,
) -> List[Dict[str, object]]:
    """The ``budget`` live locked decisions nearest ``body``.

    ``exclude_id`` drops the decision being written from its own candidate
    list; it still contributes to the corpus statistics, because the ruling
    that was just recorded is part of what the project now says.

    A decision the body already names is reported with ``named_in_body`` set
    rather than dropped. Naming a decision in passing is not reconciling with
    it, and dropping those rows measurably worsened the result on the case this
    surface exists to catch.
    """
    live = corpus(project_root)
    if not live:
        return []
    vectors = {stem: _terms(_sections(text)) for stem, text in live.items()}
    size = max(len(vectors), 1)
    idf = _idf(vectors.values(), size)
    target = _terms(_sections(body))
    if not target:
        return []
    referenced = set(_DEC_REF_RE.findall(body))
    rows: List[Dict[str, object]] = []
    for score, stem, shared in _rank(target, vectors, idf, size, exclude=exclude_id)[:budget]:
        rows.append({
            "id": stem,
            "title": _title(live[stem]),
            "score": round(score, 4),
            "shared_terms": shared,
            "named_in_body": stem in referenced,
        })
    return rows


def unreferenced_pairs(
    project_root: Path,
    *,
    cap: int = REVIEW_PAIR_CAP,
) -> List[Dict[str, object]]:
    """Mutually near locked decisions that have never cross-referenced.

    The planning reviewer's job includes noticing that a new ruling contradicts
    an older locked one. Today that depends entirely on how widely the reviewer
    happens to read. This makes the candidate set mechanical: every pair where
    one decision ranks among the other's nearest neighbors and *neither* names
    the other anywhere in its body. A pair that already cross-references has
    been considered, whatever the author concluded.

    The list is evidence for a reader, never a verdict. Proximity is not
    contradiction, and the reviewer still decides.
    """
    live = corpus(project_root)
    if len(live) < 2:
        return []
    vectors = {stem: _terms(_sections(text)) for stem, text in live.items()}
    size = len(vectors)
    idf = _idf(vectors.values(), size)
    pairs: Dict[Tuple[str, str], Tuple[float, List[str]]] = {}
    for stem, accumulated in vectors.items():
        for score, other, shared in _rank(accumulated, vectors, idf, size, exclude=stem)[:NEIGHBOR_BUDGET]:
            if other in live[stem] or stem in live[other]:
                continue
            key = (stem, other) if stem < other else (other, stem)
            if score > pairs.get(key, (0.0, []))[0]:
                pairs[key] = (score, shared)
    ordered = sorted(pairs.items(), key=lambda item: (-item[1][0], item[0]))
    return [
        {
            "decisions": [left, right],
            "titles": [_title(live[left]), _title(live[right])],
            "score": round(score, 4),
            "shared_terms": shared,
        }
        for (left, right), (score, shared) in ordered[:cap]
    ]
