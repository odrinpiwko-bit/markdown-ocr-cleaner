"""Test-only rules. These must never be added to the production registry."""

from md_ocr_cleaner.report import Finding, Severity
from md_ocr_cleaner.rules.base import Rule, RuleResult, Safety, TextEdit
from md_ocr_cleaner.segmenter import MODIFIABLE_KINDS, SegmentKind

PARAGRAPH = frozenset({SegmentKind.PARAGRAPH})


class ReplaceRule(Rule):
    """Replace every "a" in paragraphs with "b"."""

    id = "fake-replace"
    description = "Replace a with b."
    safety = Safety.SAFE
    default_enabled = True
    edit_kinds = PARAGRAPH

    def apply(self, ctx):
        edits = []
        for segment in ctx.segments_for(self.edit_kinds):
            for k, ch in enumerate(segment.text):
                if ch == "a":
                    start = segment.start + k
                    edits.append(TextEdit(start, start + 1, "a", "b", "a to b"))
        return RuleResult(edits=tuple(edits))


class UpperRule(Rule):
    """Upper-case each paragraph."""

    id = "fake-upper"
    description = "Upper-case paragraphs."
    safety = Safety.DESTRUCTIVE
    default_enabled = False
    edit_kinds = PARAGRAPH

    def apply(self, ctx):
        edits = tuple(
            TextEdit(s.start, s.end, s.text, s.text.upper(), "upper")
            for s in ctx.segments_for(self.edit_kinds)
            if s.text.upper() != s.text
        )
        return RuleResult(edits=edits)


class DetectRule(Rule):
    """Report one finding per paragraph, located in the original input."""

    id = "fake-detect"
    description = "Report paragraphs without changing text."
    safety = Safety.DETECT_ONLY
    default_enabled = True
    edit_kinds = frozenset()

    def apply(self, ctx):
        findings = tuple(
            Finding(self.id, "fake-code", Severity.INFO, "paragraph", ctx.locate(s.start, s.end))
            for s in ctx.segments_for(PARAGRAPH)
        )
        return RuleResult(findings=findings)


class BoomRule(Rule):
    id = "fake-boom"
    description = "Always fails."
    safety = Safety.SAFE
    default_enabled = False
    edit_kinds = PARAGRAPH

    def apply(self, ctx):
        raise RuntimeError("kaboom")


class SneakyDetectRule(Rule):
    id = "fake-sneaky"
    description = "Detect-only rule that illegally proposes an edit."
    safety = Safety.DETECT_ONLY
    default_enabled = False
    edit_kinds = frozenset()

    def apply(self, ctx):
        s = ctx.segments[0]
        return RuleResult(edits=(TextEdit(s.start, s.start + 1, s.text[0], "!", "sneaky"),))


class WrongResultRule(Rule):
    id = "fake-wrong"
    description = "Returns the wrong type."
    safety = Safety.SAFE
    default_enabled = False
    edit_kinds = PARAGRAPH

    def apply(self, ctx):
        return ctx.text


class ScriptedRule(Rule):
    """Proposes whatever edits the test supplies; may edit any modifiable kind."""

    id = "fake-scripted"
    description = "Scripted edits for adversarial tests."
    safety = Safety.SAFE
    default_enabled = False
    edit_kinds = MODIFIABLE_KINDS

    def __init__(self, propose):
        self.propose = propose

    def apply(self, ctx):
        return RuleResult(edits=tuple(self.propose(ctx)))


def registry():
    return (ReplaceRule(), UpperRule(), DetectRule())
