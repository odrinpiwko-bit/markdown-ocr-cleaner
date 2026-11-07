"""Minimal inline masking: find spans that later rules must not touch.

This is not a Markdown inline parser. It scans left to right and reports
non-overlapping, half-open spans for:

- code spans (matching backtick-run lengths; an unmatched run masks the rest
  of its line),
- inline link and image destinations ``](...)`` (unbalanced parentheses mask
  the rest of the line),
- URI autolinks ``<scheme:...>``, email autolinks ``<user@host>``, and bare
  ``http://``, ``https://`` and ``ftp://`` URLs,
- inline HTML tags and comments (an unclosed comment masks to the end),
- footnote references ``[^label]``,
- backslash-escaped ASCII punctuation.

Call ``mask_inline`` on a whole segment's text so that spans crossing line
breaks (such as multi-line code spans) are found. The function never changes
text and performs no I/O.
"""

from __future__ import annotations

import bisect
import re
import string
from dataclasses import dataclass
from enum import Enum


class SpanKind(Enum):
    CODE = "code"
    LINK_DESTINATION = "link-destination"
    IMAGE_DESTINATION = "image-destination"
    AUTOLINK = "autolink"
    EMAIL_AUTOLINK = "email-autolink"
    BARE_URL = "bare-url"
    HTML = "html"
    FOOTNOTE_REFERENCE = "footnote-reference"
    ESCAPE = "escape"


@dataclass(frozen=True)
class InlineSpan:
    kind: SpanKind
    start: int
    """Absolute start offset (``base_offset`` included), inclusive."""
    end: int
    """Absolute end offset, exclusive."""

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"invalid span offsets {self.start}..{self.end}")


_ASCII_PUNCTUATION = frozenset(string.punctuation)

_AUTOLINK = re.compile(r"<[A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\s<>]*>")
_EMAIL_AUTOLINK = re.compile(
    r"<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*>"
)
_TAG_NAME = r"[A-Za-z][A-Za-z0-9\-]*"
_ATTRIBUTE = (
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:\-]*"
    r"(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)"
)
_HTML_TAG = re.compile(rf"<{_TAG_NAME}{_ATTRIBUTE}*\s*/?>|</{_TAG_NAME}\s*>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FOOTNOTE_REFERENCE = re.compile(r"\[\^[^\]\s]+\]")
_BARE_URL = re.compile(r"(?:https?|ftp)://[^\s<>\[\]]+", re.IGNORECASE)
_BACKTICK_RUN = re.compile(r"`+")


def _line_end(text: str, pos: int) -> int:
    newline = text.find("\n", pos)
    return len(text) if newline == -1 else newline


def _destination_end(text: str, start: int) -> int:
    """End of ``(...)`` starting at ``start``; the rest of the line if unbalanced."""
    depth = 0
    quote: str | None = None
    j = start
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "\n" and text.startswith("\n", j + 1):
            break  # a blank line always ends inline content
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in "\"'" and text[j - 1] in " \t\n":
            quote = ch
        elif ch == "<" and j == start + 1:
            close = text.find(">", j)
            line_end = _line_end(text, j)
            if close == -1 or close > line_end:
                return line_end
            j = close
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return max(_line_end(text, start), start + 1)


def mask_inline(text: str, base_offset: int = 0) -> tuple[InlineSpan, ...]:
    """Return sorted, non-overlapping spans of ``text`` that must stay unchanged."""
    runs_by_length: dict[int, list[int]] = {}
    for run in _BACKTICK_RUN.finditer(text):
        runs_by_length.setdefault(len(run.group()), []).append(run.start())

    spans: list[InlineSpan] = []
    brackets: list[bool] = []  # one entry per open "[", True if it opens an image

    def add(kind: SpanKind, start: int, end: int) -> int:
        spans.append(InlineSpan(kind, start + base_offset, end + base_offset))
        return end

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 < n and text[i + 1] in _ASCII_PUNCTUATION:
                i = add(SpanKind.ESCAPE, i, i + 2)
            else:
                i += 1
        elif ch == "`":
            length = 1
            while i + length < n and text[i + length] == "`":
                length += 1
            starts = runs_by_length.get(length, [])
            k = bisect.bisect_left(starts, i + length)
            end = starts[k] + length if k < len(starts) else _line_end(text, i)
            i = add(SpanKind.CODE, i, max(end, i + length))
        elif ch == "<":
            if text.startswith("<!--", i):
                m = _HTML_COMMENT.match(text, i)
                i = add(SpanKind.HTML, i, m.end() if m else n)
                continue
            for pattern, kind in (
                (_AUTOLINK, SpanKind.AUTOLINK),
                (_EMAIL_AUTOLINK, SpanKind.EMAIL_AUTOLINK),
                (_HTML_TAG, SpanKind.HTML),
            ):
                m = pattern.match(text, i)
                if m:
                    i = add(kind, i, m.end())
                    break
            else:
                i += 1
        elif ch == "[":
            m = _FOOTNOTE_REFERENCE.match(text, i)
            if m:
                i = add(SpanKind.FOOTNOTE_REFERENCE, i, m.end())
            else:
                brackets.append(i > 0 and text[i - 1] == "!")
                i += 1
        elif ch == "]":
            is_image = brackets.pop() if brackets else False
            if i + 1 < n and text[i + 1] == "(":
                kind = SpanKind.IMAGE_DESTINATION if is_image else SpanKind.LINK_DESTINATION
                i = add(kind, i + 1, _destination_end(text, i + 1))
            else:
                i += 1
        elif ch in "hHfF" and (i == 0 or not text[i - 1].isalnum()):
            m = _BARE_URL.match(text, i)
            i = add(SpanKind.BARE_URL, i, m.end()) if m else i + 1
        else:
            i += 1
    return tuple(spans)


def unmasked_ranges(
    text: str, spans: tuple[InlineSpan, ...], base_offset: int = 0
) -> list[tuple[int, int]]:
    """Half-open absolute ranges of ``text`` not covered by ``spans``."""
    ranges = []
    pos = base_offset
    for span in spans:
        if span.start > pos:
            ranges.append((pos, span.start))
        pos = max(pos, span.end)
    end = base_offset + len(text)
    if pos < end:
        ranges.append((pos, end))
    return ranges
