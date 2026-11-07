"""Run selected rules over a segmented document and collect a report.

Flow:

1. Segment the original text and turn segmentation diagnostics into system
   findings (``markdown-segmenter``), next to document findings
   (``document-io``).
2. For each rule, in the given (registry) order: give it a ``RuleContext``,
   validate every proposed ``TextEdit`` centrally (including that it does not
   overlap a masked inline span of its segment), apply the edits, re-segment,
   verify that every previously protected segment is unchanged and still
   protected, and verify that the segmentation changed only as the rule's
   ``structure`` policy allows.

Escaped (``ocr-clean: off``) regions are protected segments, so rules never
change them. Decoding, BOM handling, and line-ending normalization happen in
the document layer before segmentation and apply to the whole document,
escaped regions included.

Any violation or rule exception aborts the run with ``PipelineError`` naming
the rule, with the original exception as its cause. No partial output is
produced. The pipeline is pure: it never reads or writes files.

Locations: Changes and rule Findings refer to the original input. Each applied
rule records how its edits moved offsets; current offsets are mapped back
through those steps. A position inside text replaced by an earlier rule keeps
its original line but has no column.
"""

from __future__ import annotations

import bisect
import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass

from md_ocr_cleaner import __version__
from md_ocr_cleaner.document import Document
from md_ocr_cleaner.inline import InlineSpan, mask_inline
from md_ocr_cleaner.report import (
    SEGMENTER_RULE_ID,
    SYSTEM_RULE_ID,
    Change,
    Finding,
    Report,
    Severity,
    SourceLocation,
)
from md_ocr_cleaner.rules.base import Rule, RuleContext, RuleResult, Safety, Structure, TextEdit
from md_ocr_cleaner.segmenter import (
    AMBIGUOUS_HTML,
    AMBIGUOUS_TABLE,
    Protection,
    Segment,
    SegmentationResult,
    SegmentKind,
    segment_document,
)

MIXED_LINE_ENDINGS = "mixed-line-endings"

_INFO_DIAGNOSTICS = frozenset({AMBIGUOUS_TABLE, AMBIGUOUS_HTML})


class PipelineError(Exception):
    def __init__(self, rule_id: str, message: str) -> None:
        super().__init__(f"rule {rule_id!r} failed: {message}")
        self.rule_id = rule_id


@dataclass(frozen=True)
class PipelineResult:
    document: Document
    report: Report


# --- System findings -----------------------------------------------------------------


def document_findings(doc: Document) -> list[Finding]:
    """System findings describing the input document itself."""
    findings = []
    if doc.mixed_line_endings:
        style = "CRLF" if doc.newline == "\r\n" else "LF"
        findings.append(
            Finding(
                rule_id=SYSTEM_RULE_ID,
                code=MIXED_LINE_ENDINGS,
                severity=Severity.WARNING,
                message=(
                    f"mixed line endings ({doc.lf_count} LF, {doc.crlf_count} CRLF); "
                    f"output normalized to {style}"
                ),
            )
        )
    return findings


def segmentation_findings(segmentation: SegmentationResult) -> list[Finding]:
    """System findings for segmentation diagnostics of the original text."""
    return [
        Finding(
            rule_id=SEGMENTER_RULE_ID,
            code=d.code,
            severity=Severity.INFO if d.code in _INFO_DIAGNOSTICS else Severity.WARNING,
            message=d.message,
            location=SourceLocation(d.line),
        )
        for d in segmentation.diagnostics
    ]


# --- Offset mapping ----------------------------------------------------------------------


class _Step:
    """How one rule's edits moved offsets: (new_start, new_end, old_start, old_end)."""

    def __init__(self, pieces: list[tuple[int, int, int, int]]) -> None:
        self.pieces = pieces
        self.new_starts = [p[0] for p in pieces]
        self.old_starts = [p[2] for p in pieces]

    def back(self, pos: int, is_end: bool) -> tuple[int, bool]:
        """Map a new offset to an old one; False if it falls inside replaced text."""
        k = bisect.bisect_right(self.new_starts, pos) - 1
        if k < 0:
            return pos, True
        new_start, new_end, old_start, old_end = self.pieces[k]
        if is_end and pos == new_start:
            return old_start, True
        if pos >= new_end:
            return old_end + (pos - new_end), True
        if pos == new_start:
            return old_start, True
        return (old_end if is_end else old_start), False

    def forward(self, pos: int) -> int | None:
        """Map an old offset to a new one; None if it falls inside replaced text."""
        k = bisect.bisect_right(self.old_starts, pos) - 1
        if k < 0:
            return pos
        new_start, new_end, old_start, old_end = self.pieces[k]
        if pos >= old_end:
            return new_end + (pos - old_end)
        if pos == old_start:
            return new_start
        return None


class _Locator:
    def __init__(self, original: str, steps: list[_Step]) -> None:
        self.line_starts = [0] + [k + 1 for k, ch in enumerate(original) if ch == "\n"]
        self.steps = steps

    def _original(self, pos: int, is_end: bool) -> tuple[int, bool]:
        exact = True
        for step in reversed(self.steps):
            pos, step_exact = step.back(pos, is_end)
            exact = exact and step_exact
        return pos, exact

    def __call__(self, start: int, end: int) -> SourceLocation:
        orig_start, exact = self._original(start, is_end=False)
        orig_end, _ = self._original(end, is_end=True)
        line = bisect.bisect_right(self.line_starts, orig_start)
        column = orig_start - self.line_starts[line - 1] + 1 if exact else None
        end_line = bisect.bisect_right(self.line_starts, max(orig_start, orig_end - 1))
        return SourceLocation(line, column, end_line if end_line != line else None)


# --- Central edit validation and application ---------------------------------------------


def _masked_span(
    cache: dict[int, tuple[InlineSpan, ...]], segment: Segment, edit: TextEdit
) -> InlineSpan | None:
    """The inline span of ``segment`` that ``edit`` overlaps, if any (half-open ranges)."""
    if segment.start not in cache:
        cache[segment.start] = mask_inline(segment.text, segment.start)
    spans = cache[segment.start]
    k = bisect.bisect_right(spans, edit.end - 1, key=lambda span: span.start) - 1
    if k >= 0 and spans[k].end > edit.start:
        return spans[k]
    return None


def _validate_edits(
    rule: Rule, text: str, segments: Sequence[Segment], edits: object
) -> list[TextEdit]:
    def fail(message: str) -> PipelineError:
        return PipelineError(rule.id, message)

    if not isinstance(edits, tuple) or not all(isinstance(e, TextEdit) for e in edits):
        raise fail("edits must be a tuple of TextEdit")
    if edits and rule.safety is Safety.DETECT_ONLY:
        raise fail("detect-only rule proposed edits")

    starts = [s.start for s in segments]
    ordered = sorted(edits, key=lambda e: (e.start, e.end))
    masks: dict[int, tuple[InlineSpan, ...]] = {}
    previous_end = 0
    for edit in ordered:
        where = f"edit [{edit.start}, {edit.end})"
        if not (
            isinstance(edit.start, int)
            and isinstance(edit.end, int)
            and 0 <= edit.start < edit.end <= len(text)
        ):
            raise fail(f"{where} is empty or out of range")
        if not isinstance(edit.replacement, str) or not isinstance(edit.message, str):
            raise fail(f"{where} has a non-string replacement or message")
        if text[edit.start : edit.end] != edit.expected:
            raise fail(f"{where} does not match the current text")
        if edit.replacement == edit.expected:
            raise fail(f"{where} does not change anything")
        if edit.start < previous_end:
            raise fail(f"{where} overlaps another edit")
        segment = segments[bisect.bisect_right(starts, edit.start) - 1]
        if edit.end > segment.end:
            raise fail(f"{where} crosses a segment boundary")
        if segment.protection is Protection.PROTECTED:
            raise fail(
                f"{where} touches protected {segment.kind.value} (line {segment.start_line})"
            )
        if segment.kind not in rule.edit_kinds:
            raise fail(f"{where} edits {segment.kind.value}, which the rule may not edit")
        span = _masked_span(masks, segment, edit)
        if span is not None:
            raise fail(f"{where} overlaps a masked {span.kind.value} span")
        previous_end = edit.end
    return ordered


def _apply_edits(text: str, edits: list[TextEdit]) -> tuple[str, _Step]:
    parts = []
    pieces = []
    pos = 0
    delta = 0
    for edit in edits:
        parts.append(text[pos : edit.start])
        parts.append(edit.replacement)
        new_start = edit.start + delta
        pieces.append((new_start, new_start + len(edit.replacement), edit.start, edit.end))
        delta += len(edit.replacement) - (edit.end - edit.start)
        pos = edit.end
    parts.append(text[pos:])
    return "".join(parts), _Step(pieces)


def _removed_paragraphs(segments: Sequence[Segment], edits: list[TextEdit]) -> int:
    """Number of edits that delete one whole paragraph segment."""
    bounds = {(s.start, s.end) for s in segments if s.kind is SegmentKind.PARAGRAPH}
    return sum(1 for e in edits if e.replacement == "" and (e.start, e.end) in bounds)


def _check_structure(
    rule: Rule,
    old_segments: Sequence[Segment],
    new_segments: Sequence[Segment],
    edits: list[TextEdit],
) -> None:
    """The re-segmented text may change only as ``rule.structure`` allows."""
    policy = rule.structure

    def signature(segments: Sequence[Segment]) -> list[tuple[str, str]]:
        if policy is Structure.BLANK_LINES:
            segments = [s for s in segments if s.kind is not SegmentKind.BLANK]
        elif policy is Structure.NO_NEW_BLOCKS:
            segments = [s for s in segments if s.protection is not Protection.EDITABLE]
        return [(s.kind.value, s.protection.value) for s in segments]

    def fail(detail: str) -> PipelineError:
        return PipelineError(rule.id, f"edits changed the Markdown structure: {detail}")

    def compare(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> None:
        for before, after in zip(old, new, strict=False):
            if before != after:
                raise fail(f"{before[1]} {before[0]} became {after[1]} {after[0]}")
        if len(new) > len(old):
            raise fail(f"new {new[len(old)][1]} {new[len(old)][0]} segment")
        if len(new) < len(old):
            raise fail(f"{old[len(new)][1]} {old[len(new)][0]} segment disappeared")

    old, new = signature(old_segments), signature(new_segments)
    if policy is not Structure.REMOVE_PARAGRAPHS:
        compare(old, new)
    else:  # the old segments minus whole paragraphs and blank segments
        removable = {
            (SegmentKind.PARAGRAPH.value, Protection.EDITABLE.value),
            (SegmentKind.BLANK.value, Protection.STRUCTURAL.value),
        }
        j = 0
        for item in old:
            if j < len(new) and new[j] == item:
                j += 1
            elif item not in removable:
                raise fail(f"{item[1]} {item[0]} segment was changed or removed")
        if j < len(new):
            raise fail(f"new {new[j][1]} {new[j][0]} segment")
        paragraph = (SegmentKind.PARAGRAPH.value, Protection.EDITABLE.value)
        expected = old.count(paragraph) - _removed_paragraphs(old_segments, edits)
        if new.count(paragraph) != expected:
            raise fail("paragraphs were merged or split")


def _check_protection(
    rule_id: str,
    old_segments: Sequence[Segment],
    new_text: str,
    new_segments: Sequence[Segment],
    step: _Step,
) -> None:
    """Every old protected segment must be unchanged and still fully protected."""
    starts = [s.start for s in new_segments]
    for old in old_segments:
        if not old.is_protected:
            continue
        start, end = step.forward(old.start), step.forward(old.end)
        if start is None or end is None or new_text[start:end] != old.text:
            raise PipelineError(rule_id, f"protected {old.kind.value} was modified")
        k = bisect.bisect_right(starts, start) - 1
        while k < len(new_segments) and new_segments[k].start < end:
            if not new_segments[k].is_protected:
                raise PipelineError(
                    rule_id, f"protected {old.kind.value} lost its protection after the edit"
                )
            k += 1


# --- Pipeline ----------------------------------------------------------------------------


def run_pipeline(
    doc: Document,
    rules: Sequence[Rule],
    *,
    input_path: str,
    tool_version: str = __version__,
) -> PipelineResult:
    ids = [rule.id for rule in rules]
    if len(set(ids)) != len(ids):
        raise ValueError(f"rules must be unique, got {ids}")

    segmentation = segment_document(doc.text)
    findings = document_findings(doc) + segmentation_findings(segmentation)
    changes: list[Change] = []

    text = doc.text
    segments = segmentation.segments
    steps: list[_Step] = []
    locate = _Locator(doc.text, steps)

    for rule in rules:
        ctx = RuleContext(text=text, segments=segments, locate=locate)
        try:
            result = rule.apply(ctx)
        except Exception as exc:
            raise PipelineError(rule.id, f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(result, RuleResult):
            raise PipelineError(rule.id, f"returned {type(result).__name__}, not RuleResult")
        if not isinstance(result.findings, tuple) or not all(
            isinstance(f, Finding) for f in result.findings
        ):
            raise PipelineError(rule.id, "findings must be a tuple of Finding")

        edits = _validate_edits(rule, text, segments, result.edits)
        for edit in edits:
            changes.append(
                Change(
                    rule_id=rule.id,
                    location=locate(edit.start, edit.end),
                    message=edit.message,
                    before=edit.expected,
                    after=edit.replacement,
                )
            )
        findings.extend(result.findings)

        if edits:
            new_text, step = _apply_edits(text, edits)
            new_segments = segment_document(new_text).segments
            _check_protection(rule.id, segments, new_text, new_segments, step)
            _check_structure(rule, segments, new_segments, edits)
            text, segments = new_text, new_segments
            steps.append(step)

    report = Report(
        tool_version=tool_version,
        input_path=input_path,
        rules=tuple(ids),
        changes=tuple(changes),
        findings=tuple(findings),
    )
    document = doc if text == doc.text else dataclasses.replace(doc, text=text)
    return PipelineResult(document=document, report=report)
