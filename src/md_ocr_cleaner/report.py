"""Structured change/finding records, the report model, and report rendering.

Findings come from two sources:

- **Rule findings** carry the ID of the rule that produced them.
- **System findings** describe the document itself and use a reserved
  identifier that is not a selectable rule: ``document-io`` for input/output
  properties (for example mixed line endings) and ``markdown-segmenter`` for
  Markdown structure diagnostics (for example an unclosed code fence).

``report_to_dict`` produces a deterministic, JSON-compatible dictionary::

    {
      "schema_version": 1,
      "tool_version": "0.1.0",
      "input": "docs/scan.md",            # display path, see display_path()
      "rules": ["rule-a", "rule-b"],      # enabled rules, execution order
      "summary": {
        "changes": 2,
        "findings": 1,
        "findings_by_severity": {"error": 0, "warning": 1, "info": 0},
        "changes_by_rule": {"rule-a": 2, "rule-b": 0}
      },
      "changes": [
        {"rule_id": ..., "location": LOCATION, "message": ..., "before": ..., "after": ...}
      ],
      "findings": [
        {"rule_id": ..., "code": ..., "severity": "warning", "message": ...,
         "location": LOCATION or null, "excerpt": str or null}
      ]
    }

    LOCATION = {"line": int, "column": int|null, "end_line": int|null, "end_column": int|null}

All positions are one-based. Enum values are lowercase strings. Structured
values are never truncated; only the human renderer shortens them.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SYSTEM_RULE_ID = "document-io"
SEGMENTER_RULE_ID = "markdown-segmenter"
RESERVED_RULE_IDS = frozenset({SYSTEM_RULE_ID, SEGMENTER_RULE_ID})

DISPLAY_LIMIT = 80
"""Maximum characters shown for an excerpt or before/after value."""


class Severity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


def _check_position(name: str, value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, not {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1 (one-based), got {value}")


@dataclass(frozen=True)
class SourceLocation:
    """A one-based position in the original input."""

    line: int
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        if self.line is None:
            raise TypeError("line is required")
        for name in ("line", "column", "end_line", "end_column"):
            _check_position(name, getattr(self, name))
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError("end_line must not precede line")
        if self.end_column is not None:
            if self.end_line is None:
                raise ValueError("end_column requires end_line")
            if self.column is None:
                raise ValueError("end_column requires column")
            if self.end_line == self.line and self.end_column < self.column:
                raise ValueError("end_column must not precede column on the same line")

    def display(self) -> str:
        """``12`` or ``12:4`` (the start position)."""
        return f"{self.line}" if self.column is None else f"{self.line}:{self.column}"


@dataclass(frozen=True)
class Change:
    rule_id: str
    location: SourceLocation
    message: str
    before: str
    after: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    code: str
    severity: Severity
    message: str
    location: SourceLocation | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class Report:
    tool_version: str
    input_path: str
    rules: tuple[str, ...] = ()
    changes: tuple[Change, ...] = ()
    findings: tuple[Finding, ...] = ()
    schema_version: int = field(default=SCHEMA_VERSION)

    def summary(self) -> dict[str, Any]:
        by_severity = {s.value: 0 for s in (Severity.ERROR, Severity.WARNING, Severity.INFO)}
        for finding in self.findings:
            by_severity[finding.severity.value] += 1
        by_rule = dict.fromkeys(self.rules, 0)
        for change in self.changes:
            by_rule[change.rule_id] = by_rule.get(change.rule_id, 0) + 1
        return {
            "changes": len(self.changes),
            "findings": len(self.findings),
            "findings_by_severity": by_severity,
            "changes_by_rule": by_rule,
        }


def display_path(path: str | os.PathLike[str], cwd: str | os.PathLike[str] | None = None) -> str:
    """Path shown in reports, avoiding absolute (possibly personal) paths.

    - A relative path is shown as supplied (with ``/`` separators).
    - An absolute path inside ``cwd`` is shown relative to ``cwd``.
    - Any other absolute path is shown as its file name only.
    """
    path = Path(path)
    if not path.is_absolute():
        return path.as_posix()
    base = Path(cwd) if cwd is not None else Path.cwd()
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def _location_to_dict(location: SourceLocation | None) -> dict[str, int | None] | None:
    if location is None:
        return None
    return {
        "line": location.line,
        "column": location.column,
        "end_line": location.end_line,
        "end_column": location.end_column,
    }


def report_to_dict(report: Report) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "tool_version": report.tool_version,
        "input": report.input_path,
        "rules": list(report.rules),
        "summary": report.summary(),
        "changes": [
            {
                "rule_id": c.rule_id,
                "location": _location_to_dict(c.location),
                "message": c.message,
                "before": c.before,
                "after": c.after,
            }
            for c in report.changes
        ],
        "findings": [
            {
                "rule_id": f.rule_id,
                "code": f.code,
                "severity": f.severity.value,
                "message": f.message,
                "location": _location_to_dict(f.location),
                "excerpt": f.excerpt,
            }
            for f in report.findings
        ],
    }


# --- Human-readable rendering ------------------------------------------------------

_NAMED_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t", "\\": "\\\\"}


def _escape_char(ch: str) -> str:
    if ch in _NAMED_ESCAPES:
        return _NAMED_ESCAPES[ch]
    if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
        code = ord(ch)
        return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"
    return ch


def escape_display(text: str, limit: int | None = DISPLAY_LIMIT) -> str:
    """Single-line, console-safe rendering of ``text``.

    Newlines, tabs, backslashes, and invisible or control characters are shown
    as escapes. If the escaped text exceeds ``limit`` characters it is cut at a
    character boundary and ends with ``... (N chars)``, where N is the length of
    the original text.
    """
    pieces: list[str] = []
    length = 0
    for ch in text:
        piece = _escape_char(ch)
        if limit is not None and length + len(piece) > limit:
            return "".join(pieces) + f"... ({len(text)} chars)"
        pieces.append(piece)
        length += len(piece)
    return "".join(pieces)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _prefix(path: str, location: SourceLocation | None) -> str:
    return f"{path}:{location.display()}" if location is not None else path


def render_findings(report: Report) -> list[str]:
    lines = []
    for f in report.findings:
        lines.append(
            f"{_prefix(report.input_path, f.location)}: {f.severity.value}: "
            f"{escape_display(f.message, None)} [{f.rule_id}/{f.code}]"
        )
        if f.excerpt is not None:
            lines.append(f"    excerpt: {escape_display(f.excerpt)}")
    return lines


def render_changes(report: Report) -> list[str]:
    lines = []
    for c in report.changes:
        lines.append(
            f"{_prefix(report.input_path, c.location)}: change: "
            f"{escape_display(c.message, None)} [{c.rule_id}]"
        )
        lines.append(f"    - {escape_display(c.before)}")
        lines.append(f"    + {escape_display(c.after)}")
    return lines


def render_summary(report: Report) -> str:
    summary = report.summary()
    severity = summary["findings_by_severity"]
    rules = ", ".join(report.rules) if report.rules else "none"
    return (
        f"{report.input_path}: {_plural(summary['changes'], 'change')}, "
        f"{_plural(summary['findings'], 'finding')} "
        f"({severity['error']} error, {severity['warning']} warning, {severity['info']} info); "
        f"rules: {rules}"
    )


def render_text(report: Report, *, verbose: bool = False) -> str:
    """Findings, then (if ``verbose``) every change, then a one-line summary."""
    lines = render_findings(report)
    if verbose:
        lines.extend(render_changes(report))
    lines.append(render_summary(report))
    return "\n".join(lines)
