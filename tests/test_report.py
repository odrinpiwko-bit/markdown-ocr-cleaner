import json
from pathlib import Path

import pytest

from md_ocr_cleaner.report import (
    SCHEMA_VERSION,
    Change,
    Finding,
    Report,
    Severity,
    SourceLocation,
    display_path,
    escape_display,
    render_text,
    report_to_dict,
)

# --- SourceLocation ----------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"line": 1},
        {"line": 3, "column": 1},
        {"line": 3, "column": 5, "end_line": 4, "end_column": 1},
        {"line": 3, "column": 5, "end_line": 3, "end_column": 5},
        {"line": 2, "end_line": 7},
    ],
)
def test_valid_locations(kwargs):
    assert SourceLocation(**kwargs).line == kwargs["line"]


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"line": 0}, ValueError),
        ({"line": -1}, ValueError),
        ({"line": 1, "column": 0}, ValueError),
        ({"line": 1, "end_line": 0}, ValueError),
        ({"line": 5, "end_line": 4}, ValueError),
        ({"line": 1, "column": 5, "end_line": 1, "end_column": 4}, ValueError),
        ({"line": 1, "column": 1, "end_column": 3}, ValueError),
        ({"line": 1, "end_line": 1, "end_column": 3}, ValueError),
        ({"line": True}, TypeError),
        ({"line": 1.0}, TypeError),
        ({"line": None}, TypeError),
    ],
)
def test_invalid_locations(kwargs, error):
    with pytest.raises(error):
        SourceLocation(**kwargs)


def test_location_display():
    assert SourceLocation(12).display() == "12"
    assert SourceLocation(12, 4).display() == "12:4"


# --- Models and serialization --------------------------------------------------------


def sample_report(**overrides):
    values = {
        "tool_version": "9.9.9",
        "input_path": "docs/scan.md",
        "rules": ("rule-a", "rule-b"),
        "changes": (
            Change("rule-a", SourceLocation(2), "fixed", "x  ", "x"),
            Change("rule-a", SourceLocation(5, 3), "fixed", "y ", "y"),
        ),
        "findings": (
            Finding("document-io", "mixed-line-endings", Severity.WARNING, "mixed"),
            Finding("rule-b", "odd-char", Severity.INFO, "odd", SourceLocation(7, 2), "0rder"),
        ),
    }
    values.update(overrides)
    return Report(**values)


def test_change_and_finding_construction():
    change = Change("r", SourceLocation(1), "msg", "a", "b")
    assert (change.before, change.after) == ("a", "b")
    finding = Finding("r", "code", Severity.ERROR, "msg")
    assert finding.location is None and finding.excerpt is None
    assert not isinstance(finding, BaseException)


def test_severity_values():
    assert [s.value for s in Severity] == ["info", "warning", "error"]


def test_schema_version_is_one():
    assert SCHEMA_VERSION == 1
    assert report_to_dict(sample_report())["schema_version"] == 1


def test_summary_counts():
    assert sample_report().summary() == {
        "changes": 2,
        "findings": 2,
        "findings_by_severity": {"error": 0, "warning": 1, "info": 1},
        "changes_by_rule": {"rule-a": 2, "rule-b": 0},
    }


def test_report_to_dict_structure():
    data = report_to_dict(sample_report())
    assert list(data) == [
        "schema_version",
        "tool_version",
        "input",
        "rules",
        "summary",
        "changes",
        "findings",
    ]
    assert data["input"] == "docs/scan.md"
    assert data["rules"] == ["rule-a", "rule-b"]
    assert data["changes"][1] == {
        "rule_id": "rule-a",
        "location": {"line": 5, "column": 3, "end_line": None, "end_column": None},
        "message": "fixed",
        "before": "y ",
        "after": "y",
    }
    assert data["findings"][0]["location"] is None
    assert data["findings"][0]["severity"] == "warning"
    assert data["findings"][1]["excerpt"] == "0rder"


def test_serialization_is_deterministic_and_json_compatible():
    first = json.dumps(report_to_dict(sample_report()), ensure_ascii=False)
    second = json.dumps(report_to_dict(sample_report()), ensure_ascii=False)
    assert first == second
    assert json.loads(first) == report_to_dict(sample_report())


def test_structured_values_are_not_truncated():
    long = "é" * 5000 + "\n" + "x" * 5000
    report = sample_report(changes=(Change("rule-a", SourceLocation(1), "m", long, long + "!"),))
    data = report_to_dict(report)
    assert data["changes"][0]["before"] == long
    assert data["changes"][0]["after"] == long + "!"


# --- Display path ----------------------------------------------------------------------


def test_relative_path_kept_as_given():
    assert display_path(Path("docs") / "scan.md") == "docs/scan.md"


def test_absolute_path_inside_cwd_becomes_relative(tmp_path):
    assert display_path(tmp_path / "sub" / "scan.md", cwd=tmp_path) == "sub/scan.md"


def test_absolute_path_outside_cwd_shows_only_name(tmp_path):
    other = tmp_path / "elsewhere"
    result = display_path(tmp_path / "private" / "scan.md", cwd=other)
    assert result == "scan.md"
    assert str(tmp_path) not in result


# --- Rendering -------------------------------------------------------------------------


def test_render_findings_with_and_without_location():
    text = render_text(sample_report())
    lines = text.splitlines()
    assert lines[0] == "docs/scan.md: warning: mixed [document-io/mixed-line-endings]"
    assert lines[1] == "docs/scan.md:7:2: info: odd [rule-b/odd-char]"
    assert lines[2] == "    excerpt: 0rder"


def test_render_line_only_location():
    report = sample_report(
        findings=(Finding("rule-a", "c", Severity.ERROR, "bad", SourceLocation(12)),)
    )
    assert render_text(report).splitlines()[0] == "docs/scan.md:12: error: bad [rule-a/c]"


def test_concise_summary_has_no_change_details():
    text = render_text(sample_report())
    assert "change:" not in text
    assert text.splitlines()[-1] == (
        "docs/scan.md: 2 changes, 2 findings (0 error, 1 warning, 1 info); rules: rule-a, rule-b"
    )


def test_summary_singular_and_no_rules():
    report = Report(tool_version="1", input_path="a.md")
    assert (
        render_text(report)
        == "a.md: 0 changes, 0 findings (0 error, 0 warning, 0 info); rules: none"
    )
    one = sample_report(changes=sample_report().changes[:1], findings=sample_report().findings[:1])
    assert "1 change, 1 finding " in render_text(one)


def test_verbose_lists_changes():
    lines = render_text(sample_report(), verbose=True).splitlines()
    index = lines.index("docs/scan.md:2: change: fixed [rule-a]")
    assert lines[index + 1] == "    - x  "
    assert lines[index + 2] == "    + x"
    assert "docs/scan.md:5:3: change: fixed [rule-a]" in lines


def test_escape_multiline_and_control_characters():
    assert escape_display("a\nb\r\tc\\") == "a\\nb\\r\\tc\\\\"
    raw = "x\x00\x1b\u200b\u00ad\ufeff\u2028"
    assert escape_display(raw) == "x\\u0000\\u001b\\u200b\\u00ad\\ufeff\\u2028"


def test_rendered_output_has_no_uncontrolled_newlines():
    finding = Finding("r", "c", Severity.INFO, "line1\nline2", SourceLocation(1), "e1\ne2")
    report = Report(tool_version="1", input_path="a.md", findings=(finding,))
    lines = render_text(report).splitlines()
    assert lines[0] == "a.md:1: info: line1\\nline2 [r/c]"
    assert lines[1] == "    excerpt: e1\\ne2"
    assert len(lines) == 3


def test_long_values_are_bounded_in_rendering():
    long = "a" * 500
    shown = escape_display(long)
    assert shown == "a" * 80 + "... (500 chars)"
    report = sample_report(changes=(Change("rule-a", SourceLocation(1), "m", long, long),))
    assert all(len(line) < 120 for line in render_text(report, verbose=True).splitlines())


def test_truncation_does_not_split_escapes():
    shown = escape_display("a" * 79 + "\n" + "b", limit=80)
    assert shown == "a" * 79 + "... (81 chars)"


def test_unicode_text_renders_unchanged():
    text = "áäčďéľňóôšťúýž ąęłőű ŁŐŘ"
    assert escape_display(text) == text
    finding = Finding("r", "c", Severity.INFO, text, SourceLocation(1), text)
    report = Report(tool_version="1", input_path="document-éł.md", findings=(finding,))
    assert render_text(report).splitlines()[0] == f"document-éł.md:1: info: {text} [r/c]"
