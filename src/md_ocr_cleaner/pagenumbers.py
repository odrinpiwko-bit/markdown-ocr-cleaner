"""Page-number candidate analysis shared by detection (and later removal).

Internal module: the API may change between releases. It only analyses text;
it never decides to remove anything.

Candidates
----------
A candidate is one line of an unprotected PARAGRAPH segment that is isolated
above and below. A side is isolated by the start or end of the document, by a
blank line (the neighboring segment is blank), or by a form-feed boundary
between two lines of the same paragraph: the earlier line ends with a form
feed, or the later line starts with one. A one-line paragraph between blank
lines is therefore a candidate; a number inside a longer line never is.

After removing one boundary form feed at either end and surrounding spaces or
tabs, the line must fully match one of these forms (the label is case-insensitive):

- ``12`` (1 to 4 digits)
- ``- 12 -``, or the same with an en dash or em dash on both sides
- ``[12]``
- ``12/340`` (current page / total pages; the current page is used)
- ``Page 12`` (the English label ``Page`` and the number)

Bare, dashed, and bracketed four-digit values from 1500 through 2100 are
treated as likely years and are not candidates. Labelled and current/total
forms are explicit enough to be kept.

Probable sequences
------------------
Candidates are scanned in document order and grouped greedily into runs: a
candidate continues the current run if it has the same form (and the same
total for current/total forms) and its value is 1 to 3 greater than the
previous candidate's value; otherwise it starts a new run. A run of at least
three candidates in which at least one step equals 1 is *probable*; every other
candidate is *possible*.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass

from md_ocr_cleaner.segmenter import Segment, SegmentKind

_FORMS = (
    ("bare", re.compile(r"(\d{1,4})")),
    ("dashed", re.compile(r"([-\u2013\u2014])[ \t]*(\d{1,4})[ \t]*\1")),
    ("bracketed", re.compile(r"\[[ \t]*(\d{1,4})[ \t]*\]")),
    ("current-total", re.compile(r"(\d{1,4})[ \t]*/[ \t]*(\d{1,4})")),
    ("labelled", re.compile(r"(?i:page)[ \t]+(\d{1,4})")),
)
_YEAR_FORMS = frozenset({"bare", "dashed", "bracketed"})
_YEARS = range(1500, 2101)


@dataclass(frozen=True)
class PageNumberCandidate:
    start: int
    """Offset of the candidate text (after boundary form feed and spaces)."""
    end: int
    """Offset after the candidate text (before trailing spaces, form feed, newline)."""
    line_start: int
    """Offset of the start of the whole line."""
    line_end: int
    """Offset after the line, including its newline if any."""
    value: int
    """Page number (the current page for current/total forms)."""
    form: str
    total: int | None = None
    probable: bool = False
    segment_index: int = -1
    """Index of the PARAGRAPH segment containing the candidate."""
    run_index: int = -1
    """Index of the run (see module docs) the candidate belongs to."""
    form_feed_boundary: bool = False
    """True unless the candidate is a whole one-line paragraph isolated only by
    blank lines or document edges, with no form feed on its line."""


def _value(form: str, m: re.Match[str]) -> tuple[int, int | None]:
    if form == "dashed":
        return int(m.group(2)), None
    if form == "current-total":
        return int(m.group(1)), int(m.group(2))
    return int(m.group(1)), None


def _match_line(body: str, line_start: int, line_end: int) -> PageNumberCandidate | None:
    left = 1 if body.startswith("\f") else 0
    right = len(body) - (1 if body.endswith("\f") and len(body) > left else 0)
    inner = body[left:right]
    stripped = inner.strip(" \t")
    if not stripped:
        return None
    start = line_start + left + (len(inner) - len(inner.lstrip(" \t")))
    for form, pattern in _FORMS:
        m = pattern.fullmatch(stripped)
        if not m:
            continue
        value, total = _value(form, m)
        if value < 1 or (total is not None and total < value):
            return None
        if form in _YEAR_FORMS and len(str(value)) == 4 and value in _YEARS:
            return None
        return PageNumberCandidate(
            start, start + len(stripped), line_start, line_end, value, form, total
        )
    return None


def _segment_candidates(
    segment: Segment, index: int, isolated_above: bool, isolated_below: bool
) -> list[PageNumberCandidate]:
    lines = segment.text.split("\n")
    if segment.text.endswith("\n"):
        lines.pop()
    found = []
    single = len(lines) == 1
    offset = segment.start
    for k, body in enumerate(lines):
        line_end = offset + len(body) + (1 if offset + len(body) < segment.end else 0)
        above = isolated_above if k == 0 else (lines[k - 1].endswith("\f") or body.startswith("\f"))
        below = (
            isolated_below
            if k == len(lines) - 1
            else (body.endswith("\f") or lines[k + 1].startswith("\f"))
        )
        if above and below:
            candidate = _match_line(body, offset, line_end)
            if candidate is not None:
                form_feed = not single or "\f" in (body[:1], body[-1:])
                found.append(
                    dataclasses.replace(
                        candidate, segment_index=index, form_feed_boundary=form_feed
                    )
                )
        offset = line_end
    return found


def _mark_probable(candidates: list[PageNumberCandidate]) -> list[PageNumberCandidate]:
    runs: list[list[PageNumberCandidate]] = []
    for candidate in candidates:
        if runs:
            last = runs[-1][-1]
            step = candidate.value - last.value
            if candidate.form == last.form and candidate.total == last.total and 1 <= step <= 3:
                runs[-1].append(candidate)
                continue
        runs.append([candidate])
    result = []
    for run_index, run in enumerate(runs):
        steps = [b.value - a.value for a, b in itertools.pairwise(run)]
        probable = len(run) >= 3 and 1 in steps
        result.extend(dataclasses.replace(c, probable=probable, run_index=run_index) for c in run)
    return result


def find_page_number_candidates(
    segments: Sequence[Segment],
) -> tuple[PageNumberCandidate, ...]:
    """Analyse segments and return candidates in document order."""
    candidates: list[PageNumberCandidate] = []
    for k, segment in enumerate(segments):
        if segment.kind is not SegmentKind.PARAGRAPH or segment.is_protected:
            continue
        previous = segments[k - 1] if k > 0 else None
        following = segments[k + 1] if k + 1 < len(segments) else None
        above = previous is None or previous.kind is SegmentKind.BLANK
        below = following is None or following.kind is SegmentKind.BLANK
        candidates.extend(_segment_candidates(segment, k, above, below))
    return tuple(_mark_probable(candidates))


def content_between(
    segments: Sequence[Segment], first: PageNumberCandidate, second: PageNumberCandidate
) -> bool:
    """Whether real text lies between two candidates.

    That is a non-blank segment strictly between their segments, or other text
    inside their own segments on the side facing each other (possible when a
    form feed separates a candidate from text in the same paragraph).
    """
    earlier, later = sorted((first, second), key=lambda c: c.start)
    if any(
        s.kind is not SegmentKind.BLANK
        for s in segments[earlier.segment_index + 1 : later.segment_index]
    ):
        return True
    after_earlier = segments[earlier.segment_index].text[
        earlier.line_end - segments[earlier.segment_index].start :
    ]
    before_later = segments[later.segment_index].text[
        : later.line_start - segments[later.segment_index].start
    ]
    return bool(after_earlier.strip() or before_later.strip())
