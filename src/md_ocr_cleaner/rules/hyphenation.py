"""Opt-in destructive rule: join words split by a hyphen at the end of a line.

Dictionary-free and deliberately conservative. Inside one editable paragraph,
a line ending in a word fragment and a hyphen is joined with the first token
of the next line by moving that token up; the rest of the next line stays on
its own line, so no general paragraph reconstruction takes place::

    infor-                 information
    mation is here   ->   is here

If the token is the whole next line, the two lines become one.

A soft hyphen (U+00AD) at a line end is explicit typesetter hyphenation and is
joined whenever the continuation starts with two lowercase letters. An ordinary
hyphen (U+002D) is joined only if the prefix is two or more lowercase letters
with no digit or other hyphen, the hyphen directly follows it, the next line
starts with two lowercase letters, and nothing touches a masked inline span.
Real compounds cannot be told apart without a dictionary, so these cases are
left unchanged and reported as ``ambiguous-hyphenation`` (INFO):

- the prefix ends in ``o``, a common shape of combining forms in compounds
  (``socio-``, ``electro-``); this deliberately also keeps ordinary splits
  like ``photo-``,
- the prefix is in the small, incomplete ``COMPOUND_PREFIXES`` list,
- the prefix itself follows another hyphen (``state-of-the-``).

Moving the fragment must not create Markdown structure. The lines around the
join, as they would read afterwards, are segmented on their own and must still
be one ordinary paragraph; otherwise (for example when the rest of the next
line would start a heading, list item, quote, fence, table, definition, or
``ocr-clean`` directive) the hyphen is kept and reported as
``ambiguous-hyphenation`` with the reason ``joining would create Markdown
structure``. The same happens, with its own reason, when the rest of the line
would start with masked inline content such as a URL or inline HTML.

En dashes, em dashes, a hyphen after a space, uppercase or digit
continuations, and anything outside one paragraph are never joined.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterator

from md_ocr_cleaner.inline import mask_inline
from md_ocr_cleaner.report import Finding, Severity
from md_ocr_cleaner.rules.base import Rule, RuleContext, RuleResult, Safety, Structure, TextEdit
from md_ocr_cleaner.segmenter import Segment, SegmentKind, segment_document

AMBIGUOUS = "ambiguous-hyphenation"
REASON_STRUCTURE = "joining would create Markdown structure"
REASON_INLINE = "joining would move protected inline content to the start of a line"
SOFT_HYPHEN = "\u00ad"
HYPHEN = "-"

COMPOUND_PREFIXES = frozenset(
    {"well", "self", "non", "co", "ex", "anti", "pro", "pre", "post", "multi"}
)
"""Lowercase prefixes that usually form real compounds. Not complete."""

_LINE_END = re.compile(r"([^\W\d_]+)([-\u00ad])[ \t]*$")
_CONTINUATION = re.compile(r"[ \t]*(\S+)[ \t]*")


def _is_letter(ch: str) -> bool:
    """Same characters as the regex class ``[^\\W\\d_]``."""
    return ch.isalnum() and not ch.isdecimal()


def _line_end(line: str) -> re.Match[str] | None:
    """``_LINE_END.search(line)`` in linear time.

    An unanchored search for a letter run before the hyphen restarts at every
    letter and is quadratic on very long words, so find where the final
    letter run starts first and match there.
    """
    hyphen = len(line.rstrip(" \t")) - 1
    if hyphen < 1 or line[hyphen] not in (HYPHEN, SOFT_HYPHEN):
        return None
    start = hyphen
    while start > 0 and _is_letter(line[start - 1]):
        start -= 1
    if start == hyphen:
        return None
    return _LINE_END.match(line, start)


def _single_paragraph(text: str) -> bool:
    """Whether ``text``, segmented on its own, is one ordinary paragraph.

    Used on the lines around a join as they would read afterwards. Taken out
    of context, a line can only look more like a block start than it did
    inside the paragraph, so this errs on the side of refusing.
    """
    segments = segment_document(text).segments
    return len(segments) == 1 and segments[0].kind is SegmentKind.PARAGRAPH


def _lowercase_start(token: str) -> bool:
    return (
        len(token) >= 2
        and token[0].isalpha()
        and token[0].islower()
        and (token[1].isalpha() and token[1].islower())
    )


class JoinHyphenatedWords(Rule):
    id = "join-hyphenated-words"
    description = "Join words split by a line-end hyphen (destructive; review with --diff)."
    safety = Safety.DESTRUCTIVE
    default_enabled = False
    edit_kinds = frozenset({SegmentKind.PARAGRAPH})
    structure = Structure.NO_NEW_BLOCKS

    def apply(self, ctx: RuleContext) -> RuleResult:
        edits: list[TextEdit] = []
        findings: list[Finding] = []
        for segment in ctx.segments_for(self.edit_kinds):
            for result in self._segment(ctx, segment):
                (edits if isinstance(result, TextEdit) else findings).append(result)
        return RuleResult(edits=tuple(edits), findings=tuple(findings))

    def _segment(self, ctx: RuleContext, segment: Segment) -> Iterator[TextEdit | Finding]:
        spans = mask_inline(segment.text, segment.start)
        starts = [s.start for s in spans]

        def masked(start: int, end: int) -> bool:
            k = bisect.bisect_right(starts, end - 1) - 1
            return k >= 0 and spans[k].end > start

        lines = segment.text.split("\n")
        offsets = [segment.start]
        for line in lines[:-1]:
            offsets.append(offsets[-1] + len(line) + 1)
        consumed_until = -1
        line_start = segment.start  # where the current line starts after earlier joins

        for k in range(len(lines) - 1):
            line, following = lines[k], lines[k + 1]
            m = _line_end(line)
            if m is None or offsets[k] + m.start() < consumed_until:
                continue
            prefix, mark = m.group(1), m.group(2)
            prefix_start = offsets[k] + m.start(1)
            hyphen = offsets[k] + m.start(2)
            c = _CONTINUATION.match(following)
            if c is None:
                continue
            token = c.group(1)
            if not _lowercase_start(token) or HYPHEN in token or SOFT_HYPHEN in token:
                continue
            token_end = offsets[k + 1] + c.end(1)
            if masked(prefix_start, token_end):
                continue

            before = line[m.start(1) - 1 : m.start(1)]
            if before.isdigit() or before == "_":
                continue  # the word contains a digit or is an identifier
            if mark == HYPHEN:
                if len(prefix) < 2 or prefix != prefix.lower():
                    continue
                if before in ("-", "\u2010", "\u2011", "\u2013", "\u2014"):
                    yield self._ambiguous(ctx, hyphen, prefix, "follows another hyphen")
                    continue
                if prefix.endswith("o"):
                    yield self._ambiguous(ctx, hyphen, prefix, "prefix ends in 'o'")
                    continue
                if prefix in COMPOUND_PREFIXES:
                    yield self._ambiguous(ctx, hyphen, prefix, "common compound prefix")
                    continue

            rest_starts = offsets[k + 1] + c.end()
            rest_of_line_empty = c.end() == len(following)
            if rest_of_line_empty:
                end, replacement = token_end, token
            else:
                end, replacement = rest_starts, token + "\n"
            # The joined line, the rest of the line, and the line after it, as
            # they would read after the edit.
            stop = end
            for _ in range(2):
                newline = ctx.text.find("\n", stop, segment.end)
                stop = segment.end if newline == -1 else newline + 1
            window = ctx.text[max(offsets[k], line_start) : hyphen] + replacement
            if not _single_paragraph(window + ctx.text[end:stop]):
                yield self._ambiguous(ctx, hyphen, prefix, REASON_STRUCTURE, compound=False)
                continue
            if not rest_of_line_empty:
                if masked(rest_starts, rest_starts + 1):
                    yield self._ambiguous(ctx, hyphen, prefix, REASON_INLINE, compound=False)
                    continue
                line_start = rest_starts
            expected = ctx.text[hyphen:end]
            consumed_until = end
            yield TextEdit(
                hyphen, end, expected, replacement, f"joined hyphenated word '{prefix}{token}'"
            )

    def _ambiguous(
        self, ctx: RuleContext, hyphen: int, prefix: str, why: str, *, compound: bool = True
    ) -> Finding:
        note = "; it may be a real compound" if compound else ""
        return Finding(
            rule_id=self.id,
            code=AMBIGUOUS,
            severity=Severity.INFO,
            message=f"kept line-end hyphen after '{prefix}': {why}{note}",
            location=ctx.locate(hyphen, hyphen + 1),
            excerpt=ctx.text[max(0, hyphen - 20) : hyphen + 21],
        )
