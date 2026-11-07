"""Safe whitespace rules: trailing whitespace and repeated spaces."""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterator

from md_ocr_cleaner.inline import InlineSpan, mask_inline
from md_ocr_cleaner.rules.base import Rule, RuleContext, RuleResult, Safety, Structure, TextEdit
from md_ocr_cleaner.segmenter import Segment, SegmentKind

_REPEATED = re.compile(r"(?<=\S) {2,}(?=\S)")


def _masked(spans: tuple[InlineSpan, ...], start: int, end: int) -> bool:
    # Spans are sorted and non-overlapping, so only the last one starting
    # before ``end`` can overlap [start, end).
    k = bisect.bisect_right(spans, end - 1, key=lambda span: span.start) - 1
    return k >= 0 and spans[k].end > start


def _trailing_whitespace(text: str) -> Iterator[tuple[int, str]]:
    """(offset, suffix) for every line of ``text`` ending in spaces or tabs.

    A plain per-line scan: a regex like ``[ \\t]+$`` is quadratic on long runs
    of spaces that are followed by other text.
    """
    offset = 0
    for line in text.split("\n"):
        content = line.rstrip(" \t")
        if len(content) < len(line):
            yield offset + len(content), line[len(content) :]
        offset += len(line) + 1


def _unsafe_to_strip(text: str, start: int, end: int) -> bool:
    """Whether removing ``text[start:end]`` would change line structure.

    - After a lone carriage return the removal would create ``\\r\\n``, which
      the document layer treats as a line ending.
    - Emptying a whitespace-only last line without a final newline would give
      the document a final newline it did not have, or (for a whitespace-only
      document) leave no content at all.
    """
    if start > 0 and text[start - 1] == "\r":
        return True
    is_last_line = end == len(text)
    starts_line = start == 0 or text[start - 1] == "\n"
    return is_last_line and starts_line


def _spans(segment: Segment) -> tuple[InlineSpan, ...]:
    return mask_inline(segment.text, segment.start)


class TrailingWhitespace(Rule):
    """Remove trailing spaces and tabs, keeping Markdown hard breaks.

    If a line's trailing whitespace ends with two or more ASCII spaces (a hard
    break), that final run of spaces is kept and anything before it in the
    trailing whitespace is removed. Otherwise all trailing spaces and tabs are
    removed. Other Unicode spaces are never removed. Trailing whitespace inside
    inline code or HTML spans is left alone.
    """

    id = "trailing-whitespace"
    description = "Remove trailing spaces and tabs, keeping Markdown hard breaks."
    safety = Safety.SAFE
    default_enabled = True
    edit_kinds = frozenset(
        {
            SegmentKind.PARAGRAPH,
            SegmentKind.ATX_HEADING,
            SegmentKind.SETEXT_HEADING,
            SegmentKind.LIST_ITEM,
            SegmentKind.TASK_ITEM,
            SegmentKind.BLOCKQUOTE,
            SegmentKind.BLANK,
        }
    )
    structure = Structure.PRESERVED

    def apply(self, ctx: RuleContext) -> RuleResult:
        return RuleResult(edits=tuple(self._edits(ctx)))

    def _edits(self, ctx: RuleContext) -> Iterator[TextEdit]:
        for segment in ctx.segments_for(self.edit_kinds):
            spans = None
            for position, suffix in _trailing_whitespace(segment.text):
                spaces = len(suffix) - len(suffix.rstrip(" "))
                keep = spaces if spaces >= 2 else 0  # an existing hard break
                remove = suffix[: len(suffix) - keep]
                if not remove:
                    continue
                start = segment.start + position
                end = start + len(remove)
                if _unsafe_to_strip(ctx.text, start, start + len(suffix)):
                    continue
                if spans is None:
                    spans = _spans(segment)
                if _masked(spans, start, end):
                    continue
                yield TextEdit(start, end, remove, "", "removed trailing whitespace")


class RepeatedSpaces(Rule):
    """Collapse runs of ASCII spaces between two non-whitespace characters in prose.

    Only paragraphs are edited. Leading indentation, trailing spaces, tabs,
    non-breaking and other Unicode spaces, and anything inside inline code,
    links, URLs, HTML, footnote references, or escapes are left alone.
    """

    id = "repeated-spaces"
    description = "Collapse repeated spaces inside prose to one space (not code, URLs, or HTML)."
    safety = Safety.SAFE
    default_enabled = True
    edit_kinds = frozenset({SegmentKind.PARAGRAPH})
    structure = Structure.PRESERVED

    def apply(self, ctx: RuleContext) -> RuleResult:
        return RuleResult(edits=tuple(self._edits(ctx)))

    def _edits(self, ctx: RuleContext) -> Iterator[TextEdit]:
        for segment in ctx.segments_for(self.edit_kinds):
            spans = None
            for m in _REPEATED.finditer(segment.text):
                start = segment.start + m.start()
                end = segment.start + m.end()
                if spans is None:
                    spans = _spans(segment)
                if _masked(spans, start, end):
                    continue
                yield TextEdit(start, end, m.group(), " ", "collapsed repeated spaces")
