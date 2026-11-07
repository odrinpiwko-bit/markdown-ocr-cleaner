"""Detect-only rule: report suspicious characters. Never changes text.

Two tiers:

- **Definite** checks report specific characters that are almost always a
  problem. They inspect every segment, including protected ones and masked
  inline spans: ``replacement-character`` (U+FFFD), ``control-character``
  (C0/C1 controls except tab, newline, and form feed), ``bidi-control``
  (U+202A-U+202E, U+2066-U+2069), and ``form-feed`` (U+000C).
- **Heuristic** checks only inspect unprotected prose and structural segments
  outside masked inline spans: ``invisible-character`` (U+200B, U+2060,
  U+FEFF, U+00AD), ``mixed-script-word`` (Latin mixed with Cyrillic or Greek
  in one word), and ``digit-letter-confusion`` (``l0ve``, ``0rder``).

``ocr-clean: off`` regions (and stray directive lines) are skipped entirely.
At most one finding is reported per original line and code, located at the
first occurrence on that line.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Iterator

from md_ocr_cleaner.inline import mask_inline
from md_ocr_cleaner.report import Finding, Severity
from md_ocr_cleaner.rules.base import Rule, RuleContext, RuleResult, Safety
from md_ocr_cleaner.segmenter import Segment, SegmentKind

REPLACEMENT = "replacement-character"
CONTROL = "control-character"
BIDI = "bidi-control"
FORM_FEED = "form-feed"
INVISIBLE = "invisible-character"
MIXED_SCRIPT = "mixed-script-word"
DIGIT_LETTER = "digit-letter-confusion"

SEVERITY = {
    REPLACEMENT: Severity.WARNING,
    CONTROL: Severity.WARNING,
    BIDI: Severity.WARNING,
    INVISIBLE: Severity.WARNING,
    MIXED_SCRIPT: Severity.WARNING,
    FORM_FEED: Severity.INFO,
    DIGIT_LETTER: Severity.INFO,
}

_MESSAGES = {
    REPLACEMENT: "replacement character U+FFFD (lost or undecodable character)",
    CONTROL: "control character",
    BIDI: "bidirectional control character",
    FORM_FEED: "form feed (possible page break)",
    INVISIBLE: "invisible character",
    MIXED_SCRIPT: "word mixes Latin with Cyrillic or Greek letters (possible look-alike)",
    DIGIT_LETTER: "digit inside a word (possible OCR confusion of 0/o or 1/l)",
}

BIDI_CONTROLS = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
INVISIBLE_CHARACTERS = frozenset({"\u200b", "\u2060", "\ufeff", "\u00ad"})

_HEURISTIC_KINDS = frozenset(
    {
        SegmentKind.PARAGRAPH,
        SegmentKind.ATX_HEADING,
        SegmentKind.SETEXT_HEADING,
        SegmentKind.LIST_ITEM,
        SegmentKind.TASK_ITEM,
        SegmentKind.BLOCKQUOTE,
    }
)
_SKIPPED_KINDS = frozenset({SegmentKind.ESCAPED_REGION, SegmentKind.DIRECTIVE})
_EXCERPT_RADIUS = 20

# digit-letter-confusion
_CHUNK = re.compile(r"\S+")
_EDGE_PUNCTUATION = "\"'()[]{}<>.,;:!?\u00ab\u00bb\u201e\u201c\u201d\u2018\u2019\u201a*"
_SKIP_CHUNK = re.compile(r"[_/\\:@]|\.\w")
_INNER_CONFUSION = re.compile(r"[^\W\d_][01][^\W\d_]")
_LEADING_CONFUSION = re.compile(r"[01]([^\W\d_]{3,})")
_HEX = re.compile(r"(?:0x)?[0-9a-f]+")
_UNIT_WORDS = frozenset(
    {"min", "sec", "mph", "rpm", "ppm", "mol", "kcal", "bit", "bits", "byte", "bytes"}
)


def _definite_code(ch: str) -> str | None:
    if ch == "\ufffd":
        return REPLACEMENT
    if ch == "\f":
        return FORM_FEED
    if ch in BIDI_CONTROLS:
        return BIDI
    if ch not in "\t\n" and unicodedata.category(ch) == "Cc":
        return CONTROL
    return None


def _script(ch: str) -> str | None:
    """LATIN, CYRILLIC, GREEK, or None if the letter cannot be classified."""
    name = unicodedata.name(ch, "")
    for script in ("LATIN", "CYRILLIC", "GREEK"):
        if name.startswith(script + " "):
            return script
    return None


def _letter_tokens(line: str) -> Iterator[tuple[int, int]]:
    """Runs of letters and combining marks: (start, end) within line."""
    start = None
    for k, ch in enumerate(line):
        if unicodedata.category(ch)[0] in "LM":
            if start is None:
                start = k
        elif start is not None:
            yield start, k
            start = None
    if start is not None:
        yield start, len(line)


def _mixed_script(token: str) -> bool:
    scripts = set()
    for ch in token:
        if unicodedata.category(ch)[0] != "L":
            continue  # combining marks belong to their base letter
        script = _script(ch)
        if script is None:
            return False  # unclassifiable letter: prefer no finding
        scripts.add(script)
    return "LATIN" in scripts and bool(scripts & {"CYRILLIC", "GREEK"})


def _digit_confusion(chunk: str) -> int | None:
    """Offset of the suspicious digit within chunk, or None."""
    if _SKIP_CHUNK.search(chunk):
        return None  # paths, URLs, identifiers, versions, decimals
    lead = len(chunk) - len(chunk.lstrip(_EDGE_PUNCTUATION))
    core = chunk.strip(_EDGE_PUNCTUATION)
    if len(core) < 3 or not core.isalnum() or core != core.lower():
        return None  # punctuation inside (COVID-19), uppercase codes (A4, H2O)
    if _HEX.fullmatch(core):
        return None
    m = _INNER_CONFUSION.search(core)
    if m:
        return lead + m.start() + 1
    m = _LEADING_CONFUSION.fullmatch(core)
    if m and m.group(1) not in _UNIT_WORDS:
        return lead
    return None


class SuspiciousCharacters(Rule):
    id = "suspicious-characters"
    description = (
        "Report suspicious characters (U+FFFD, controls, look-alikes); never changes text."
    )
    safety = Safety.DETECT_ONLY
    default_enabled = True
    edit_kinds = frozenset()

    def apply(self, ctx: RuleContext) -> RuleResult:
        findings: list[Finding] = []
        for segment in ctx.segments:
            if segment.kind not in _SKIPPED_KINDS:
                findings.extend(self._segment_findings(ctx, segment))
        return RuleResult(findings=tuple(findings))

    def _segment_findings(self, ctx: RuleContext, segment: Segment) -> Iterator[Finding]:
        heuristic = segment.kind in _HEURISTIC_KINDS and not segment.is_protected
        masked_starts: list[int] = []
        masked_ends: list[int] = []
        if heuristic:
            for span in mask_inline(segment.text, segment.start):
                masked_starts.append(span.start)
                masked_ends.append(span.end)

        def masked(start: int, end: int) -> bool:
            k = bisect.bisect_right(masked_starts, end - 1) - 1
            return k >= 0 and masked_ends[k] > start

        offset = segment.start
        for line in segment.text.split("\n"):
            first: dict[str, int] = {}
            for k, ch in enumerate(line):
                code = _definite_code(ch)
                if code is not None:
                    first.setdefault(code, k)
                elif (
                    heuristic
                    and ch in INVISIBLE_CHARACTERS
                    and not masked(offset + k, offset + k + 1)
                ):
                    first.setdefault(INVISIBLE, k)
            if heuristic:
                for start, end in _letter_tokens(line):
                    if _mixed_script(line[start:end]) and not masked(offset + start, offset + end):
                        first.setdefault(MIXED_SCRIPT, start)
                        break
                for m in _CHUNK.finditer(line):
                    pos = _digit_confusion(m.group())
                    if pos is not None and not masked(offset + m.start(), offset + m.end()):
                        first.setdefault(DIGIT_LETTER, m.start() + pos)
                        break
            for code, k in sorted(first.items(), key=lambda item: (item[1], item[0])):
                start = offset + k
                excerpt = line[max(0, k - _EXCERPT_RADIUS) : k + _EXCERPT_RADIUS + 1]
                yield Finding(
                    rule_id=self.id,
                    code=code,
                    severity=SEVERITY[code],
                    message=_MESSAGES[code],
                    location=ctx.locate(start, start + 1),
                    excerpt=excerpt,
                )
            offset += len(line) + 1
