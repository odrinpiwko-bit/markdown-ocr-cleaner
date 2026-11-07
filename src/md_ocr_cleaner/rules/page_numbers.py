"""Page-number rules: detection (default on) and removal (opt-in, destructive)."""

from __future__ import annotations

from collections.abc import Sequence

from md_ocr_cleaner.pagenumbers import (
    PageNumberCandidate,
    content_between,
    find_page_number_candidates,
)
from md_ocr_cleaner.report import Finding, Severity
from md_ocr_cleaner.rules.base import Rule, RuleContext, RuleResult, Safety, Structure, TextEdit
from md_ocr_cleaner.segmenter import Segment, SegmentKind

PROBABLE = "probable-page-number"
POSSIBLE = "possible-page-number"
KEPT = "page-number-kept"

# Stable reasons for keeping a probable page number.
REASON_FORM_FEED = "removal skipped because the candidate depends on a form-feed boundary"
REASON_NO_TEXT = "removal skipped because no text separates it from a neighboring page number"
REASON_STRUCTURE = "removal skipped because it could change the structure of nearby content"
REASON_FINAL_NEWLINE = (
    "removal skipped because the missing final newline could not be preserved safely"
)

_STABLE_NEIGHBORS = frozenset(
    {SegmentKind.PARAGRAPH, SegmentKind.ATX_HEADING, SegmentKind.SETEXT_HEADING}
)


class DetectPageNumbers(Rule):
    """Report isolated page-number lines; see ``md_ocr_cleaner.pagenumbers``."""

    id = "detect-page-numbers"
    description = "Report isolated lines that look like page numbers; never changes text."
    safety = Safety.DETECT_ONLY
    default_enabled = True
    edit_kinds = frozenset()

    def apply(self, ctx: RuleContext) -> RuleResult:
        findings = []
        for candidate in find_page_number_candidates(ctx.segments):
            code = PROBABLE if candidate.probable else POSSIBLE
            kind = "probable" if candidate.probable else "possible"
            findings.append(
                Finding(
                    rule_id=self.id,
                    code=code,
                    severity=Severity.INFO,
                    message=f"{kind} page number {candidate.value}",
                    location=ctx.locate(candidate.start, candidate.end),
                    excerpt=ctx.text[candidate.start : candidate.end],
                )
            )
        return RuleResult(findings=tuple(findings))


class RemovePageNumbers(Rule):
    """Remove probable page numbers, keeping exactly one block separator.

    A candidate is removed only if it is *probable*, is a whole one-line
    paragraph isolated by blank lines or document edges (not by a form feed),
    is separated by real content from its neighbors in the run, and sits
    between paragraphs or headings whose structure cannot change (the next
    content must not be indented). Otherwise it is kept and reported once as
    ``page-number-kept`` with a stable reason. Possible candidates are never
    removed and get no extra finding.
    """

    id = "remove-page-numbers"
    description = "Remove probable page numbers (destructive; review with --diff)."
    safety = Safety.DESTRUCTIVE
    default_enabled = False
    edit_kinds = frozenset({SegmentKind.PARAGRAPH, SegmentKind.BLANK})
    structure = Structure.REMOVE_PARAGRAPHS

    def apply(self, ctx: RuleContext) -> RuleResult:
        candidates = [c for c in find_page_number_candidates(ctx.segments) if c.probable]
        runs: dict[int, list[PageNumberCandidate]] = {}
        for c in candidates:
            runs.setdefault(c.run_index, []).append(c)
        positions = {id(c): k for run in runs.values() for k, c in enumerate(run)}
        edits: list[TextEdit] = []
        findings: list[Finding] = []
        for candidate in candidates:
            run = runs[candidate.run_index]
            reason = self._reason_to_keep(ctx.segments, candidate, run, positions[id(candidate)])
            planned = None if reason else self._edits(ctx.segments, candidate)
            if reason is None and planned is None:
                reason = REASON_FINAL_NEWLINE
            if reason is not None:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        code=KEPT,
                        severity=Severity.INFO,
                        message=f"kept probable page number {candidate.value}: {reason}",
                        location=ctx.locate(candidate.start, candidate.end),
                        excerpt=ctx.text[candidate.start : candidate.end],
                    )
                )
            else:
                edits.extend(planned)
        return RuleResult(edits=tuple(edits), findings=tuple(findings))

    @staticmethod
    def _reason_to_keep(
        segments: Sequence[Segment],
        candidate: PageNumberCandidate,
        run: list[PageNumberCandidate],
        position: int,
    ) -> str | None:
        if candidate.form_feed_boundary:
            return REASON_FORM_FEED
        neighbors = run[max(0, position - 1) : position] + run[position + 1 : position + 2]
        for other in neighbors:
            if not content_between(segments, candidate, other):
                return REASON_NO_TEXT
        k = candidate.segment_index
        before = segments[k - 2] if k >= 2 else None
        after = segments[k + 2] if k + 2 < len(segments) else None
        for neighbor in (before, after):
            if neighbor is not None and neighbor.kind not in _STABLE_NEIGHBORS:
                return REASON_STRUCTURE
        if after is not None and after.text[:1] in (" ", "\t"):
            return REASON_STRUCTURE
        return None

    def _edits(
        self, segments: Sequence[Segment], candidate: PageNumberCandidate
    ) -> list[TextEdit] | None:
        k = candidate.segment_index
        own = segments[k]
        message = f"Removed probable page number {candidate.value}."
        remove_own = TextEdit(own.start, own.end, own.text, "", message)
        separator = f"Removed the blank line left by page number {candidate.value}."
        following = segments[k + 1] if k + 1 < len(segments) else None
        previous = segments[k - 1] if k > 0 else None

        if following is not None:  # start or middle: drop the first line of the blank below
            first_line = following.text[: following.text.find("\n") + 1]
            if not first_line:
                return None
            end = following.start + len(first_line)
            return [remove_own, TextEdit(following.start, end, first_line, "", separator)]
        if previous is None:
            return None
        if own.text.endswith("\n"):  # end of document with a final newline
            last_line_start = previous.text.rfind("\n", 0, len(previous.text) - 1) + 1
            start = previous.start + last_line_start
            last_line = previous.text[last_line_start:]
            return [TextEdit(start, previous.end, last_line, "", separator), remove_own]
        # End of document without a final newline: also drop the newline that
        # ends the preceding paragraph so the output still has no final newline.
        before = segments[k - 2] if k >= 2 else None
        if before is None or before.kind is not SegmentKind.PARAGRAPH or before.is_protected:
            return None
        if not before.text.endswith("\n"):
            return None
        return [
            TextEdit(before.end - 1, before.end, "\n", "", separator),
            TextEdit(previous.start, previous.end, previous.text, "", separator),
            remove_own,
        ]
