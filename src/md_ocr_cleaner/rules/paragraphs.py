"""Opt-in destructive rule: reconstruct OCR-wrapped paragraphs into one line.

All or nothing: inside one editable paragraph, every internal line break is
replaced by a single space, or the paragraph is left exactly as it is. The
rule never crosses blank lines or segment boundaries, never re-wraps text, and
never removes, repairs, or reinterprets a hyphen.

In valid CommonMark a line break inside a paragraph already renders as a space,
so this rule mainly normalizes the source text for plain-text use, diffs, and
downstream tools.

Shape test (failures are silent: the paragraph does not look like wrapped
prose, for example verse, addresses, bibliographies, or short heading-like
lines):

- at least two lines and no unusual control characters;
- the median length of the non-final lines, measured without leading and
  trailing spaces and tabs, in Unicode code points, is at least
  ``MIN_WIDTH`` (40);
- every non-final line is at least ``MIN_RATIO`` (0.75) times that median.
  The last line may be shorter.

A paragraph that passes the shape test but is blocked by one of the checks
below is kept and reported once as ``paragraph-kept`` (INFO) with the first
blocking reason, in this order: form feed; aligned columns (three or more
spaces or a tab inside a line, outside inline code, URLs, and other masked
spans); then, for each line break in order: line ends with a hyphen or dash;
hard line break (two or more trailing spaces, or a trailing backslash); line
ends with a colon; next line starts with an enumeration or citation marker (including an
author start such as ``Surname, X.``);
next line starts with a dialogue marker; uppercase start after non-terminal
punctuation (only ``.``, ``!``, ``?``, and the ellipsis permit a capital);
inline Markdown touches the line break.
"""

from __future__ import annotations

import bisect
import re
import statistics
import unicodedata

from md_ocr_cleaner.inline import mask_inline
from md_ocr_cleaner.report import Finding, Severity
from md_ocr_cleaner.rules.base import Rule, RuleContext, RuleResult, Safety, Structure, TextEdit
from md_ocr_cleaner.segmenter import Segment, SegmentKind

KEPT = "paragraph-kept"
MIN_WIDTH = 40
MIN_RATIO = 0.75
CHANGE_MESSAGE = "Joined wrapped paragraph line."

REASON_FORM_FEED = "form feed"
REASON_ALIGNED = "aligned columns"
REASON_HYPHEN = "line ends with a hyphen or dash"
REASON_HARD_BREAK = "hard line break"
REASON_COLON = "line ends with a colon"
REASON_MARKER = "next line starts with an enumeration or citation marker"
REASON_DIALOGUE = "next line starts with a dialogue marker"
REASON_UPPERCASE = "uppercase start after non-terminal punctuation"
REASON_INLINE = "inline Markdown touches the line boundary"

LINE_END_HYPHENS = frozenset("-\u00ad\u2010\u2011\u2013\u2014")
TERMINAL_PUNCTUATION = frozenset(".!?\u2026")
DIALOGUE_MARKERS = frozenset('\u2013\u2014\u201e\u201c"\u00bb\u00ab')
_MARKER = re.compile(
    r"(?:\(\d{1,3}\)|\d{1,3}\)|\(?[A-Za-z]\)|[ivxlcdmIVXLCDM]{1,6}[.)](?=[ \t]|$)"
    r"|\u00a7|Art\.|art\.|[\u2022\u00b7\u25aa]"
    # Bibliography-style author start such as "Surname, X." (surname, comma, initial).
    r"|[^\W\d_][^\W\d_\-]*, [^\W\d_]\.)"
)
_ALIGNED = re.compile(r" {3,}|\t")
_BLANKS = " \t"


def _has_unusual_controls(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" and ch not in "\t\n\f" for ch in text)


class ReconstructParagraphs(Rule):
    id = "reconstruct-paragraphs"
    description = (
        "Join OCR-wrapped paragraph lines into one line (destructive; review with --diff)."
    )
    safety = Safety.DESTRUCTIVE
    default_enabled = False
    edit_kinds = frozenset({SegmentKind.PARAGRAPH})
    structure = Structure.NO_NEW_BLOCKS

    def apply(self, ctx: RuleContext) -> RuleResult:
        edits: list[TextEdit] = []
        findings: list[Finding] = []
        for segment in ctx.segments_for(self.edit_kinds):
            result = self._paragraph(ctx, segment)
            if isinstance(result, Finding):
                findings.append(result)
            elif result:
                edits.extend(result)
        return RuleResult(edits=tuple(edits), findings=tuple(findings))

    def _paragraph(self, ctx: RuleContext, segment: Segment) -> list[TextEdit] | Finding | None:
        lines = segment.text.split("\n")
        if segment.text.endswith("\n"):
            lines.pop()
        if len(lines) < 2 or _has_unusual_controls(segment.text):
            return None
        widths = [len(line.strip(_BLANKS)) for line in lines]
        reference = statistics.median(widths[:-1])
        if reference < MIN_WIDTH or any(w < MIN_RATIO * reference for w in widths[:-1]):
            return None  # not shaped like wrapped prose: no finding

        offsets = [segment.start]
        for line in lines[:-1]:
            offsets.append(offsets[-1] + len(line) + 1)
        spans = mask_inline(segment.text, segment.start)
        span_starts = [s.start for s in spans]

        def masked(start: int, end: int) -> bool:
            k = bisect.bisect_right(span_starts, end - 1) - 1
            return k >= 0 and spans[k].end > start

        def kept(reason: str, start: int) -> Finding:
            return Finding(
                rule_id=self.id,
                code=KEPT,
                severity=Severity.INFO,
                message=f"kept paragraph unchanged: {reason}",
                location=ctx.locate(start, start + 1),
                excerpt=ctx.text[start : start + 41].split("\n")[0],
            )

        form_feed = segment.text.find("\f")
        if form_feed != -1:
            return kept(REASON_FORM_FEED, segment.start + form_feed)
        for k, line in enumerate(lines):
            lead = len(line) - len(line.lstrip(_BLANKS))
            body_end = len(line.rstrip(_BLANKS))
            for m in _ALIGNED.finditer(line, lead, body_end):
                start = offsets[k] + m.start()
                if not masked(start, start + len(m.group())):
                    return kept(REASON_ALIGNED, start)

        edits = []
        for k in range(len(lines) - 1):
            line, following = lines[k], lines[k + 1]
            content = line.rstrip(_BLANKS)
            nxt = following.lstrip(_BLANKS)
            line_start = offsets[k]
            boundary_start = line_start + len(content)
            boundary_end = offsets[k + 1] + (len(following) - len(nxt))
            reason = None
            if content[-1:] in LINE_END_HYPHENS:
                reason = REASON_HYPHEN
            elif line.endswith("  ") or content.endswith("\\"):
                reason = REASON_HARD_BREAK
            elif content.endswith(":"):
                reason = REASON_COLON
            elif _MARKER.match(nxt):
                reason = REASON_MARKER
            elif nxt[:1] in DIALOGUE_MARKERS:
                reason = REASON_DIALOGUE
            elif nxt[:1].isupper() and content[-1:] not in TERMINAL_PUNCTUATION:
                reason = REASON_UPPERCASE
            elif masked(boundary_start - 1, boundary_end + 1):
                reason = REASON_INLINE
            if reason is not None:
                return kept(reason, line_start)
            expected = ctx.text[boundary_start:boundary_end]
            edits.append(TextEdit(boundary_start, boundary_end, expected, " ", CHANGE_MESSAGE))
        return edits
