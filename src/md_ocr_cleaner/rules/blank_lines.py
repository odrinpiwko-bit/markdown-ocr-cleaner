"""Safe blank-line rule: collapse long runs of empty lines."""

from __future__ import annotations

import re
from collections.abc import Iterator

from md_ocr_cleaner.rules.base import Rule, RuleContext, RuleResult, Safety, Structure, TextEdit
from md_ocr_cleaner.segmenter import SegmentKind

COLLAPSE_THRESHOLD = 3
"""Runs of at least this many consecutive empty lines are collapsed to one."""

_EMPTY_LINES = re.compile(rf"(?:(?<=\n)|^)\n{{{COLLAPSE_THRESHOLD},}}")


class ExcessiveBlankLines(Rule):
    """Collapse runs of three or more completely empty lines to one empty line.

    Only empty lines between blocks (blank segments) are considered; lines
    containing spaces or tabs are not empty and interrupt a run. Blank lines
    inside code, front matter, HTML, tables, math, definitions, list items, or
    ``ocr-clean: off`` regions are never touched. The same policy applies at
    the start and end of the document, so a final newline is always kept.
    """

    id = "excessive-blank-lines"
    description = "Collapse three or more consecutive empty lines to one."
    safety = Safety.SAFE
    default_enabled = True
    edit_kinds = frozenset({SegmentKind.BLANK})
    structure = Structure.BLANK_LINES

    def apply(self, ctx: RuleContext) -> RuleResult:
        return RuleResult(edits=tuple(self._edits(ctx)))

    def _edits(self, ctx: RuleContext) -> Iterator[TextEdit]:
        for segment in ctx.segments_for(self.edit_kinds):
            for m in _EMPTY_LINES.finditer(segment.text):
                start = segment.start + m.start()
                end = segment.start + m.end()
                count = m.end() - m.start()
                yield TextEdit(start, end, m.group(), "\n", f"collapsed {count} empty lines to one")
