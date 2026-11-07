"""Conservative, line-oriented Markdown block segmentation.

This is a safety boundary, not a CommonMark parser. It splits normalized text
(``\\n`` line endings) into contiguous segments, each classified as:

- ``PROTECTED``: no rule may change it (code, front matter, tables, HTML,
  definitions, math, thematic breaks, ``ocr-clean: off`` regions, ...).
- ``STRUCTURAL``: headings, list items, blockquotes, and blank lines. Only
  whitespace-safe rules may touch these, and they are never joined with
  neighboring content.
- ``EDITABLE``: ordinary prose paragraphs.

When classification is uncertain, content is protected. Unclosed fences, math
blocks, HTML comments, raw HTML blocks, front matter, and unmatched
``<!-- ocr-clean: off -->`` directives protect everything to the end of the
document and are reported as diagnostics.

Segmentation never changes text: every segment is an exact slice of the input,
and concatenating the segments reproduces the input exactly. It performs no
I/O.

Escape-hatch directives must be alone on their line (surrounding spaces and
tabs allowed)::

    <!-- ocr-clean: off -->
    <!-- ocr-clean: on -->

They nest with a depth counter. They are ignored inside fenced code, math
blocks, HTML comments, front matter, and container prefixes (``>``).
``$$`` math blocks are a pragmatic extension, not CommonMark.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from md_ocr_cleaner.inline import SpanKind, mask_inline


class Protection(Enum):
    PROTECTED = "protected"
    STRUCTURAL = "structural"
    EDITABLE = "editable"


class SegmentKind(Enum):
    FRONT_MATTER = "front-matter"
    FENCED_CODE = "fenced-code"
    INDENTED_CODE = "indented-code"
    HTML_COMMENT = "html-comment"
    HTML_BLOCK = "html-block"
    TABLE = "table"
    LINK_REFERENCE = "link-reference"
    FOOTNOTE_DEFINITION = "footnote-definition"
    THEMATIC_BREAK = "thematic-break"
    MATH_BLOCK = "math-block"
    ESCAPED_REGION = "escaped-region"
    DIRECTIVE = "directive"
    ATX_HEADING = "atx-heading"
    SETEXT_HEADING = "setext-heading"
    LIST_ITEM = "list-item"
    TASK_ITEM = "task-item"
    BLOCKQUOTE = "blockquote"
    BLANK = "blank"
    PARAGRAPH = "paragraph"


_DEFAULT_PROTECTION = {
    SegmentKind.ATX_HEADING: Protection.STRUCTURAL,
    SegmentKind.SETEXT_HEADING: Protection.STRUCTURAL,
    SegmentKind.LIST_ITEM: Protection.STRUCTURAL,
    SegmentKind.TASK_ITEM: Protection.STRUCTURAL,
    SegmentKind.BLOCKQUOTE: Protection.STRUCTURAL,
    SegmentKind.BLANK: Protection.STRUCTURAL,
    SegmentKind.PARAGRAPH: Protection.EDITABLE,
}

MODIFIABLE_KINDS = frozenset(_DEFAULT_PROTECTION)
"""Segment kinds that are not always protected; the only kinds a rule may edit."""

# Diagnostic codes.
UNMATCHED_OFF = "unmatched-ocr-clean-off"
UNMATCHED_ON = "unmatched-ocr-clean-on"
UNCLOSED_FRONT_MATTER = "unclosed-front-matter"
UNCLOSED_FENCE = "unclosed-fenced-code"
UNCLOSED_MATH = "unclosed-math-block"
UNCLOSED_COMMENT = "unclosed-html-comment"
UNCLOSED_HTML = "unclosed-html-block"
AMBIGUOUS_TABLE = "ambiguous-markdown-table"
AMBIGUOUS_HTML = "ambiguous-html-block"


class SegmentationError(Exception):
    """Segments violate an invariant (gap, overlap, wrong slice or position)."""


@dataclass(frozen=True)
class Segment:
    kind: SegmentKind
    protection: Protection
    text: str
    """Exact slice of the normalized input."""
    start_line: int
    """One-based line of the first character."""
    end_line: int
    """One-based line of the last character (inclusive)."""
    start: int
    """Character offset of the first character (inclusive)."""
    end: int
    """Character offset after the last character (exclusive)."""
    prefix: str = ""
    """Structural marker at the start of the segment (e.g. ``"## "``, ``"- [ ] "``),
    including any leading indentation."""

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise SegmentationError("start_line must be >= 1")
        if self.end_line < self.start_line:
            raise SegmentationError("end_line must not precede start_line")
        if self.start < 0 or self.end <= self.start:
            raise SegmentationError(f"invalid offsets {self.start}..{self.end}")
        if len(self.text) != self.end - self.start:
            raise SegmentationError("text length does not match offsets")
        if not self.text.startswith(self.prefix):
            raise SegmentationError("prefix must be the start of the segment text")

    @property
    def is_protected(self) -> bool:
        return self.protection is Protection.PROTECTED


@dataclass(frozen=True)
class SegmentDiagnostic:
    code: str
    line: int
    """One-based line where the problem starts."""
    message: str


@dataclass(frozen=True)
class SegmentationResult:
    segments: tuple[Segment, ...]
    diagnostics: tuple[SegmentDiagnostic, ...] = ()


def reassemble(segments: Sequence[Segment]) -> str:
    return "".join(segment.text for segment in segments)


def validate_segments(text: str, segments: Sequence[Segment]) -> None:
    """Raise SegmentationError unless ``segments`` exactly and contiguously cover ``text``."""
    pos = 0
    line = 1
    for segment in segments:
        if segment.start != pos:
            raise SegmentationError(f"gap or overlap at offset {pos}")
        if text[segment.start : segment.end] != segment.text:
            raise SegmentationError(f"segment at offset {pos} is not an exact slice")
        if segment.start_line != line:
            raise SegmentationError(f"segment at offset {pos} starts on the wrong line")
        newlines = segment.text.count("\n")
        covered = newlines if segment.text.endswith("\n") else newlines + 1
        if segment.end_line != line + covered - 1:
            raise SegmentationError(f"segment at offset {pos} ends on the wrong line")
        if not segment.text.endswith("\n") and segment.end != len(text):
            raise SegmentationError(f"segment at offset {pos} ends mid-line")
        pos = segment.end
        line = segment.end_line + 1
    if pos != len(text):
        raise SegmentationError(f"segments end at {pos}, text has {len(text)} characters")


# --- Line classification -----------------------------------------------------------

_DIRECTIVE = re.compile(r"[ \t]*<!--[ \t]*ocr-clean:[ \t]*(off|on)[ \t]*-->[ \t]*")
_FENCE_OPEN = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_FENCE_CLOSE = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*")
_MATH_OPEN = re.compile(r" {0,3}\$\$")
_HTML_COMMENT_OPEN = re.compile(r" {0,3}<!--")
_HTML_RAW_OPEN = re.compile(r" {0,3}<(?:script|pre|style|textarea)(?=[\s>]|$)", re.IGNORECASE)
_HTML_RAW_CLOSE = re.compile(r"</(?:script|pre|style|textarea)\s*>", re.IGNORECASE)
_HTML_RAW_TAG = re.compile(r"<(/?)(?:script|pre|style|textarea)(?=[\s/>]|$)", re.IGNORECASE)
_HTML_PI_OPEN = re.compile(r" {0,3}<\?")
_HTML_CDATA_OPEN = re.compile(r" {0,3}<!\[CDATA\[")
_HTML_DECL_OPEN = re.compile(r" {0,3}<![A-Za-z]")
_HTML_BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|"
    "details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|"
    "h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|"
    "optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|"
    "tr|track|ul"
)
_HTML_BLOCK_OPEN = re.compile(rf" {{0,3}}</?(?:{_HTML_BLOCK_TAGS})(?=[\s/>]|$)", re.IGNORECASE)
_HTML_COMPLETE_TAG = re.compile(
    r" {0,3}(?:<[A-Za-z][A-Za-z0-9\-]*"
    r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:\-]*(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*"
    r"\s*/?>|</[A-Za-z][A-Za-z0-9\-]*\s*>)[ \t]*"
)
# A tag name followed by whitespace or end of line, with no ">" on the line:
# probably a tag whose attributes continue on the next line.
_HTML_INCOMPLETE_TAG = re.compile(r" {0,3}</?[A-Za-z][A-Za-z0-9\-]*(?:\s|$)[^>]*")
_ATX_HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]+|$)")
_THEMATIC_BREAK = re.compile(r" {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}")
_SETEXT_UNDERLINE = re.compile(r" {0,3}(?:=+|-+)[ \t]*")
_BLOCKQUOTE = re.compile(r" {0,3}>")
_QUOTE_PREFIX = re.compile(r"(?: {0,3}>[ \t]?)+")
_LIST_ITEM = re.compile(r"( {0,3})([-+*]|\d{1,9}[.)])([ \t]+|$)")
_TASK_MARKER = re.compile(r"\[[ xX]\](?:[ \t]+|$)")
_LINK_REFERENCE = re.compile(r" {0,3}\[((?!\^)(?:[^\[\]\\]|\\.){1,999})\]:")
_FOOTNOTE_DEFINITION = re.compile(r" {0,3}\[\^[^\]\s]+\]:")
_DELIMITER_CELL = re.compile(r":?-+:?")


def _split_lines(text: str) -> list[str]:
    """Split on ``\\n`` only; other separators (``\\r``, ``\\f``, U+2028) are content."""
    lines = []
    start = 0
    while start < len(text):
        newline = text.find("\n", start)
        end = len(text) if newline == -1 else newline + 1
        lines.append(text[start:end])
        start = end
    return lines


def _blank(body: str) -> bool:
    return not body.strip(" \t")


def _indent(body: str) -> int:
    """Leading indentation in columns (tab stops of 4)."""
    col = 0
    for ch in body:
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += 4 - col % 4
        else:
            break
    return col


def _column(text: str) -> int:
    """Column after ``text`` (tab stops of 4)."""
    col = 0
    for ch in text:
        col += 4 - col % 4 if ch == "\t" else 1
    return col


def _dedent(body: str, cols: int) -> str:
    """Remove up to ``cols`` columns of leading whitespace (for detection only)."""
    col = 0
    k = 0
    while k < len(body) and col < cols and body[k] in " \t":
        col += 1 if body[k] == " " else 4 - col % 4
        k += 1
    return body[k:]


def _directive(body: str) -> str | None:
    m = _DIRECTIVE.fullmatch(body)
    return m.group(1) if m else None


def _is_fence_open(body: str) -> bool:
    m = _FENCE_OPEN.fullmatch(body)
    return bool(m) and not (m.group(1)[0] == "`" and "`" in m.group(2))


def _is_link_reference(body: str) -> bool:
    m = _LINK_REFERENCE.match(body)
    return bool(m) and bool(m.group(1).strip())


def _html_start(body: str, *, allow_complete_tag: bool) -> bool:
    return bool(
        _HTML_RAW_OPEN.match(body)
        or _HTML_PI_OPEN.match(body)
        or _HTML_CDATA_OPEN.match(body)
        or _HTML_DECL_OPEN.match(body)
        or _HTML_BLOCK_OPEN.match(body)
        or (allow_complete_tag and _HTML_COMPLETE_TAG.fullmatch(body))
    )


def _is_delimiter_row(body: str) -> bool:
    """A table delimiter row such as ``|---|:--:|`` or ``--- | ---``.

    Checked cell by cell; the equivalent single regex backtracks
    quadratically on long runs of spaces.
    """
    if "|" not in body:
        return False
    # Same grammar as `` {0,3}\|?[ \t]*CELL[ \t]*(\|[ \t]*CELL[ \t]*)*\|?[ \t]*``.
    row = body[min(3, len(body) - len(body.lstrip(" "))) :]
    row = row.removeprefix("|").rstrip(" \t")
    if row.endswith("|"):
        row = row[:-1]
    return all(_DELIMITER_CELL.fullmatch(cell.strip(" \t")) for cell in row.split("|"))


def _cell_count(body: str) -> int | None:
    """Number of table cells, counting only pipes outside code spans and escapes."""
    masked = [
        (s.start, s.end) for s in mask_inline(body) if s.kind in (SpanKind.CODE, SpanKind.ESCAPE)
    ]
    pipes = [
        k
        for k, ch in enumerate(body)
        if ch == "|" and not any(start <= k < end for start, end in masked)
    ]
    if not pipes:
        return None
    first = len(body) - len(body.lstrip())
    last = len(body.rstrip()) - 1
    cells = len(pipes) + 1
    if pipes[0] == first:
        cells -= 1
    if pipes[-1] == last and not (len(pipes) == 1 and pipes[0] == first):
        cells -= 1
    return cells


def _risky_content(body: str) -> bool:
    """Container content that a structural classification could put at risk."""
    return bool(
        _is_fence_open(body)
        or _MATH_OPEN.match(body)
        or _HTML_COMMENT_OPEN.match(body)
        or _html_start(body, allow_complete_tag=True)
        or _is_delimiter_row(body)
        or _indent(body) >= 4
        or _is_link_reference(body)
        or _FOOTNOTE_DEFINITION.match(body)
        or _directive(body) is not None
    )


# --- Segmenter -------------------------------------------------------------------------


class _Segmenter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = _split_lines(text)
        self.bodies = [line[:-1] if line.endswith("\n") else line for line in self.lines]
        self.offsets = [0]
        for line in self.lines:
            self.offsets.append(self.offsets[-1] + len(line))
        self.n = len(self.lines)
        self.blocks: list[tuple[SegmentKind, Protection, int, int, str]] = []
        self.diagnostics: list[SegmentDiagnostic] = []

    # -- output --

    def emit(
        self,
        kind: SegmentKind,
        start: int,
        end: int,
        protection: Protection | None = None,
        prefix: str = "",
    ) -> int:
        if protection is None:
            protection = _DEFAULT_PROTECTION.get(kind, Protection.PROTECTED)
        self.blocks.append((kind, protection, start, end, prefix))
        return end

    def diagnose(self, code: str, index: int, message: str) -> None:
        self.diagnostics.append(SegmentDiagnostic(code, index + 1, message))

    def run(self) -> SegmentationResult:
        i = 0
        if self.n and self.bodies[0] == "---":
            i = self.front_matter()
        while i < self.n:
            i = self.block(i)
        segments = tuple(
            Segment(
                kind=kind,
                protection=protection,
                text=self.text[self.offsets[start] : self.offsets[end]],
                start_line=start + 1,
                end_line=end,
                start=self.offsets[start],
                end=self.offsets[end],
                prefix=prefix,
            )
            for kind, protection, start, end, prefix in self.blocks
        )
        validate_segments(self.text, segments)
        return SegmentationResult(segments, tuple(self.diagnostics))

    # -- end finders: return (end index, closed) or None if line i does not open --

    def fence_end(self, i: int, indent: int = 0) -> tuple[int, bool] | None:
        body = _dedent(self.bodies[i], indent)
        if not _is_fence_open(body):
            return None
        fence = _FENCE_OPEN.fullmatch(body).group(1)
        for j in range(i + 1, self.n):
            m = _FENCE_CLOSE.fullmatch(_dedent(self.bodies[j], indent))
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                return j + 1, True
        return self.n, False

    def math_end(self, i: int, indent: int = 0) -> tuple[int, bool] | None:
        body = _dedent(self.bodies[i], indent)
        m = _MATH_OPEN.match(body)
        if not m:
            return None
        if body[m.end() :].rstrip().endswith("$$"):
            return i + 1, True
        for j in range(i + 1, self.n):
            if self.bodies[j].rstrip().endswith("$$"):
                return j + 1, True
        return self.n, False

    def comment_end(self, i: int, indent: int = 0) -> tuple[int, bool] | None:
        body = _dedent(self.bodies[i], indent)
        m = _HTML_COMMENT_OPEN.match(body)
        if not m:
            return None
        if "-->" in body[m.end() :]:
            return i + 1, True
        for j in range(i + 1, self.n):
            if "-->" in self.bodies[j]:
                return j + 1, True
        return self.n, False

    def construct_end(self, i: int, indent: int = 0) -> int | None:
        """End of a fence, math block, or HTML comment opening on line i."""
        for finder in (self.fence_end, self.math_end, self.comment_end):
            found = finder(i, indent)
            if found is not None:
                return found[0]
        return None

    def html_end(self, i: int) -> tuple[int, bool] | None:
        body = self.bodies[i]
        closers: tuple[tuple[re.Pattern[str], Callable[[str], bool]], ...] = (
            (_HTML_RAW_OPEN, lambda s: bool(_HTML_RAW_CLOSE.search(s))),
            (_HTML_PI_OPEN, lambda s: "?>" in s),
            (_HTML_CDATA_OPEN, lambda s: "]]>" in s),
            (_HTML_DECL_OPEN, lambda s: ">" in s),
        )
        for opener, closes in closers:
            m = opener.match(body)
            if m:
                if closes(body[m.end() :]):
                    return i + 1, True
                for j in range(i + 1, self.n):
                    if closes(self.bodies[j]):
                        return j + 1, True
                return self.n, False
        if _HTML_BLOCK_OPEN.match(body) or _HTML_COMPLETE_TAG.fullmatch(body):
            # Normally ends at a blank line, but a <pre>, <script>, <style> or
            # <textarea> opened inside the block keeps it open until closed,
            # because blank lines there are content.
            j = i
            raw_open = False
            while j < self.n:
                line = self.bodies[j]
                if j > i and not raw_open and (_blank(line) or _directive(line) is not None):
                    break
                for tag in _HTML_RAW_TAG.finditer(line):
                    raw_open = not tag.group(1)
                j += 1
            return j, not raw_open
        return None

    def table_start(self, i: int) -> bool:
        """A non-blank line containing ``|`` followed by a delimiter row.

        This is deliberately broader than GFM: malformed inline syntax (an
        unmatched backtick, an escaped pipe, a pipe inside code) must not turn
        a plausible table into editable prose.
        """
        if i + 1 >= self.n or not _is_delimiter_row(self.bodies[i + 1]):
            return False
        header = self.bodies[i]
        return not _blank(header) and "|" in header

    def table_is_confident(self, i: int) -> bool:
        """Whether the table at line i also passes the strict GFM-style cell check."""
        cells = _cell_count(self.bodies[i])
        return cells is not None and cells == _cell_count(self.bodies[i + 1])

    def interrupts(self, j: int) -> bool:
        """Whether line j could start a block, ending a paragraph or lazy continuation."""
        body = self.bodies[j]
        return bool(
            _directive(body) is not None
            or _is_fence_open(body)
            or _MATH_OPEN.match(body)
            or _HTML_COMMENT_OPEN.match(body)
            or _html_start(body, allow_complete_tag=False)
            or _ATX_HEADING.match(body)
            or _THEMATIC_BREAK.fullmatch(body)
            or _BLOCKQUOTE.match(body)
            or _LIST_ITEM.match(body)
            or self.table_start(j)
            or _is_link_reference(body)
            or _FOOTNOTE_DEFINITION.match(body)
        )

    # -- blocks --

    def front_matter(self) -> int:
        for j in range(1, self.n):
            if self.bodies[j] in ("---", "..."):
                return self.emit(SegmentKind.FRONT_MATTER, 0, j + 1)
        self.diagnose(UNCLOSED_FRONT_MATTER, 0, "front matter is not closed; protected to EOF")
        return self.emit(SegmentKind.FRONT_MATTER, 0, self.n)

    def block(self, i: int) -> int:
        body = self.bodies[i]

        if _blank(body):
            j = i + 1
            while j < self.n and _blank(self.bodies[j]):
                j += 1
            return self.emit(SegmentKind.BLANK, i, j)

        directive = _directive(body)
        if directive == "off":
            return self.escaped_region(i)
        if directive == "on":
            self.diagnose(UNMATCHED_ON, i, "'ocr-clean: on' without a preceding 'off'")
            return self.emit(SegmentKind.DIRECTIVE, i, i + 1)

        for finder, kind, code in (
            (self.fence_end, SegmentKind.FENCED_CODE, UNCLOSED_FENCE),
            (self.math_end, SegmentKind.MATH_BLOCK, UNCLOSED_MATH),
            (self.comment_end, SegmentKind.HTML_COMMENT, UNCLOSED_COMMENT),
        ):
            found = finder(i, 0)
            if found is not None:
                end, closed = found
                if not closed:
                    self.diagnose(code, i, f"{kind.value} is not closed; protected to EOF")
                return self.emit(kind, i, end)

        if _indent(body) >= 4:
            return self.indented_code(i)

        found = self.html_end(i)
        if found is not None:
            end, closed = found
            if not closed:
                self.diagnose(UNCLOSED_HTML, i, "HTML block is not closed; protected to EOF")
            return self.emit(SegmentKind.HTML_BLOCK, i, end)
        if _HTML_INCOMPLETE_TAG.fullmatch(body):
            j = i + 1
            while j < self.n and not _blank(self.bodies[j]) and _directive(self.bodies[j]) is None:
                j += 1
            self.diagnose(
                AMBIGUOUS_HTML, i, "HTML-like block protected because its tag is incomplete"
            )
            return self.emit(SegmentKind.HTML_BLOCK, i, j)

        m = _ATX_HEADING.match(body)
        if m:
            return self.emit(SegmentKind.ATX_HEADING, i, i + 1, prefix=m.group(0))
        if _THEMATIC_BREAK.fullmatch(body):
            return self.emit(SegmentKind.THEMATIC_BREAK, i, i + 1)
        if self.table_start(i):
            return self.table(i)
        if _is_link_reference(body):
            return self.link_reference(i)
        if _FOOTNOTE_DEFINITION.match(body):
            return self.footnote_definition(i)
        if _BLOCKQUOTE.match(body):
            return self.blockquote(i)
        m = _LIST_ITEM.match(body)
        if m:
            return self.list_item(i, m)
        return self.paragraph(i)

    def escaped_region(self, i: int) -> int:
        depth = 1
        j = i + 1
        while j < self.n:
            directive = _directive(self.bodies[j])
            if directive == "off":
                depth += 1
            elif directive == "on":
                depth -= 1
                if depth == 0:
                    return self.emit(SegmentKind.ESCAPED_REGION, i, j + 1)
            else:
                end = self.construct_end(j)
                if end is not None:
                    j = end
                    continue
            j += 1
        self.diagnose(
            UNMATCHED_OFF, i, "'ocr-clean: off' is never turned back on; protected to EOF"
        )
        return self.emit(SegmentKind.ESCAPED_REGION, i, self.n)

    def indented_code(self, i: int) -> int:
        j = end = i + 1
        while j < self.n:
            body = self.bodies[j]
            if _blank(body):
                j += 1
            elif _indent(body) >= 4 and _directive(body) is None:
                j += 1
                end = j
            else:
                break
        return self.emit(SegmentKind.INDENTED_CODE, i, end)

    def table(self, i: int) -> int:
        j = i + 2
        while j < self.n and not _blank(self.bodies[j]) and not self.interrupts(j):
            j += 1
        if not self.table_is_confident(i):
            self.diagnose(
                AMBIGUOUS_TABLE,
                i,
                "table-like region protected because its inline syntax could not be "
                "classified safely",
            )
        return self.emit(SegmentKind.TABLE, i, j)

    def link_reference(self, i: int) -> int:
        body = self.bodies[i]
        j = i + 1
        rest = body[_LINK_REFERENCE.match(body).end() :]
        if not rest.strip() and j < self.n and not _blank(self.bodies[j]):
            j += 1  # destination on the next line
        while j < self.n:
            line = self.bodies[j]
            if _blank(line) or _directive(line) is not None:
                break
            first = line.lstrip(" \t")[:1]
            if line[0] in " \t" or (first and first in "\"'(<"):
                j += 1
            else:
                break
        return self.emit(SegmentKind.LINK_REFERENCE, i, j)

    def footnote_definition(self, i: int) -> int:
        j = i + 1
        while j < self.n:
            body = self.bodies[j]
            if _blank(body):
                k = j
                while k < self.n and _blank(self.bodies[k]):
                    k += 1
                if k < self.n and _indent(self.bodies[k]) >= 4:
                    j = k
                    continue
                break
            if _directive(body) is not None:
                break
            if _indent(body) >= 4 or not self.interrupts(j):
                j += 1
                continue
            break
        return self.emit(SegmentKind.FOOTNOTE_DEFINITION, i, j)

    def blockquote(self, i: int) -> int:
        j = i + 1
        while j < self.n:
            body = self.bodies[j]
            if _blank(body):
                break
            if _BLOCKQUOTE.match(body) or not self.interrupts(j):
                j += 1
            else:
                break
        risky = any(
            _risky_content(_QUOTE_PREFIX.sub("", self.bodies[k], count=1))
            for k in range(i, j)
            if _BLOCKQUOTE.match(self.bodies[k])
        )
        prefix = _QUOTE_PREFIX.match(self.bodies[i]).group(0)
        protection = Protection.PROTECTED if risky else Protection.STRUCTURAL
        return self.emit(SegmentKind.BLOCKQUOTE, i, j, protection, prefix)

    def list_item(self, i: int, m: re.Match[str]) -> int:
        body = self.bodies[i]
        marker_end = len(m.group(1)) + len(m.group(2))
        spaces = _indent(" " * marker_end + m.group(3)) - marker_end
        width = marker_end + (spaces if 1 <= spaces <= 4 else 1)
        prefix = m.group(0)
        kind = SegmentKind.LIST_ITEM
        gap = spaces  # widest whitespace gap before the first-line content, in columns
        task = _TASK_MARKER.match(body, m.end())
        if task:
            kind = SegmentKind.TASK_ITEM
            prefix = body[: task.end()]
            gap = max(gap, _column(prefix) - _column(body[: m.end() + 3]))

        # Five or more columns after the marker (or task box) make the first
        # line indented code in CommonMark; protect that and any other risky
        # first-line content, as for continuation lines.
        content = body[len(prefix) :]
        risky = bool(content) and (gap >= 5 or _risky_content(content))
        j = i + 1
        while j < self.n:
            line = self.bodies[j]
            if _blank(line):
                k = j
                while k < self.n and _blank(self.bodies[k]):
                    k += 1
                if k < self.n and _indent(self.bodies[k]) >= width:
                    j = k
                    continue
                break
            if _directive(line) is not None:
                break
            if _indent(line) >= width:
                end = self.construct_end(j, width)
                if end is not None:
                    risky = True
                    j = end
                    continue
                if _risky_content(_dedent(line, width)):
                    risky = True
                j += 1
                continue
            if self.interrupts(j):
                break
            j += 1  # lazy continuation
        protection = Protection.PROTECTED if risky else Protection.STRUCTURAL
        return self.emit(kind, i, j, protection, prefix)

    def paragraph(self, i: int) -> int:
        j = i + 1
        while j < self.n and not _blank(self.bodies[j]):
            if _SETEXT_UNDERLINE.fullmatch(self.bodies[j]):
                return self.emit(SegmentKind.SETEXT_HEADING, i, j + 1)
            if self.interrupts(j):
                break
            j += 1
        return self.emit(SegmentKind.PARAGRAPH, i, j)


def segment_document(text: str) -> SegmentationResult:
    """Split normalized text into classified segments. Never changes the text."""
    return _Segmenter(text).run()
