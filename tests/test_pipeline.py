import builtins
import os

import pytest

from md_ocr_cleaner.document import encode_document, parse_document
from md_ocr_cleaner.pipeline import (
    MIXED_LINE_ENDINGS,
    PipelineError,
    document_findings,
    run_pipeline,
)
from md_ocr_cleaner.report import SEGMENTER_RULE_ID, SYSTEM_RULE_ID, Severity, SourceLocation
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.base import TextEdit
from md_ocr_cleaner.rules.selection import select_rules
from md_ocr_cleaner.segmenter import SegmentKind, segment_document

from fakes import (
    BoomRule,
    DetectRule,
    ReplaceRule,
    ScriptedRule,
    SneakyDetectRule,
    UpperRule,
    WrongResultRule,
)

BY_ID = {rule.id: rule for rule in REGISTRY}

DEFAULT_RULES = select_rules(REGISTRY)  # the rules a plain run uses


def run(data, rules=(), encoding="utf-8"):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return run_pipeline(parse_document(data, encoding), rules, input_path="in.md")


# --- Basic behavior ---------------------------------------------------------------------


def test_no_rules_leaves_document_unchanged():
    doc = parse_document(b"a  b \r\n\r\n\r\n\r\nc\r\n")
    result = run_pipeline(doc, (), input_path="in.md")
    assert result.document is doc
    assert result.report.changes == ()
    assert result.report.findings == ()
    assert result.report.rules == ()


def test_default_selection_runs_five_rules_in_order():
    result = run("a  b \n", DEFAULT_RULES)
    assert result.report.rules == (
        "suspicious-characters",
        "detect-page-numbers",
        "trailing-whitespace",
        "repeated-spaces",
        "excessive-blank-lines",
    )
    assert [c.rule_id for c in result.report.changes] == ["trailing-whitespace", "repeated-spaces"]


def test_later_rule_receives_earlier_output():
    result = run("xa\n", (ReplaceRule(), UpperRule()))
    assert result.document.text == "XB\n"
    assert result.report.changes[1].before == "xb\n"


def test_resegmentation_after_text_change(monkeypatch):
    from md_ocr_cleaner import pipeline

    calls = []
    real = pipeline.segment_document
    monkeypatch.setattr(pipeline, "segment_document", lambda t: calls.append(t) or real(t))
    run("aa\n", (ReplaceRule(), DetectRule()))
    assert calls == ["aa\n", "bb\n"]  # initial, then once after the changing rule only


def test_findings_accumulate_with_original_locations():
    result = run("x\n\n\n\n\nparagraph two\n", (*DEFAULT_RULES, DetectRule()))
    detect = [f for f in result.report.findings if f.rule_id == "fake-detect"]
    # The second paragraph moved from line 6 to line 3, but is reported at line 6.
    assert [f.location for f in detect] == [SourceLocation(1, 1), SourceLocation(6, 1)]


def test_document_metadata_is_preserved():
    doc = parse_document(b"\xef\xbb\xbfa  b \r\n", "utf-8-sig")
    out = run_pipeline(doc, DEFAULT_RULES, input_path="in.md").document
    assert out.text == "a b\n"
    assert (out.has_bom, out.newline, out.line_ending, out.encoding, out.had_final_newline) == (
        doc.has_bom,
        doc.newline,
        doc.line_ending,
        doc.encoding,
        doc.had_final_newline,
    )
    assert encode_document(out) == b"\xef\xbb\xbfa b\r\n"


def test_duplicate_rules_rejected():
    with pytest.raises(ValueError):
        run("x", (ReplaceRule(), ReplaceRule()))


def test_report_carries_version_and_path():
    report = run_pipeline(parse_document(b""), (), input_path="d/x.md", tool_version="1.2").report
    assert (report.tool_version, report.input_path) == ("1.2", "d/x.md")


def test_pipeline_performs_no_file_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pipeline touched the filesystem")

    doc = parse_document(b"---\na: 1\n---\nText  with \n\n\n\n```\nx  \n```\n")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    result = run_pipeline(doc, DEFAULT_RULES, input_path="in.md")
    assert result.document.text == "---\na: 1\n---\nText with\n\n```\nx  \n```\n"


# --- Failures ---------------------------------------------------------------------------


def test_failing_rule_raises_pipeline_error_with_cause():
    with pytest.raises(PipelineError, match=r"fake-boom.*kaboom") as exc:
        run("x", (ReplaceRule(), BoomRule()))
    assert exc.value.rule_id == "fake-boom"
    assert isinstance(exc.value.__cause__, RuntimeError)


def test_wrong_result_type_is_error():
    with pytest.raises(PipelineError, match="not RuleResult"):
        run("x", (WrongResultRule(),))


def test_detect_only_rule_may_not_edit():
    with pytest.raises(PipelineError, match="detect-only"):
        run("x", (SneakyDetectRule(),))


def scripted(*edits):
    return ScriptedRule(lambda ctx: edits)


@pytest.mark.parametrize(
    ("text", "edits", "message"),
    [
        ("abc\n", [TextEdit(1, 1, "", "x", "m")], "empty or out of range"),
        ("abc\n", [TextEdit(3, 9, "\n", "", "m")], "empty or out of range"),
        ("abc\n", [TextEdit(-1, 1, "a", "", "m")], "empty or out of range"),
        ("abc\n", [TextEdit(0, 1, "z", "y", "m")], "does not match"),
        ("abc\n", [TextEdit(0, 1, "a", "a", "m")], "does not change"),
        ("abc\n", [TextEdit(0, 2, "ab", "", "m"), TextEdit(1, 3, "bc", "", "m")], "overlaps"),
        ("a\n\nb\n", [TextEdit(0, 3, "a\n\n", "", "m")], "crosses a segment"),
        ("abc\n", [TextEdit(0, 1, "a", 5, "m")], "non-string"),
    ],
)
def test_invalid_edits_fail(text, edits, message):
    with pytest.raises(PipelineError, match=message):
        run(text, (scripted(*edits),))


def test_edits_must_be_text_edits():
    with pytest.raises(PipelineError, match="tuple of TextEdit"):
        run("abc\n", (ScriptedRule(lambda ctx: [(0, 1, "a", "")]),))


class ParagraphOnly(ScriptedRule):
    id = "fake-paragraph-only"
    description = "Scripted edits, but only paragraphs are permitted."
    safety = ScriptedRule.safety
    default_enabled = False
    edit_kinds = frozenset({SegmentKind.PARAGRAPH})


def test_edit_outside_declared_kinds_fails():
    text = "# Heading\n"
    edit = TextEdit(2, 9, "Heading", "X", "m")
    assert run(text, (scripted(edit),)).document.text == "# X\n"  # heading edits allowed
    with pytest.raises(PipelineError, match="atx-heading, which the rule may not edit"):
        run(text, (ParagraphOnly(lambda ctx: [edit]),))


def test_protected_container_cannot_be_edited_even_if_kind_allowed():
    text = "- item\n\n  ```\n  code  x\n  ```\n"
    edit = TextEdit(0, 6, "- item", "- ITEM", "m")
    with pytest.raises(PipelineError, match="touches protected list-item"):
        run(text, (scripted(edit),))


def test_findings_must_be_findings():
    from md_ocr_cleaner.rules.base import RuleResult

    rule = ScriptedRule(lambda ctx: ())
    rule.apply = lambda ctx: RuleResult(findings=("not a finding",))
    with pytest.raises(PipelineError, match="tuple of Finding"):
        run("x\n", (rule,))


# --- Adversarial: protected content can never be edited ------------------------------------

PROTECTED_DOCUMENTS = {
    "front-matter": "---\ntitle:  x \n---\n",
    "fenced-code": "```\na  b \n```\n",
    "indented-code": "    a  b \n",
    "html-block": "<div>\na  b \n</div>\n",
    "html-comment": "<!--\na  b \n-->\n",
    "table": "| a  b |\n|---|\n",
    "link-reference": "[id]:  https://x.io \n",
    "footnote-definition": "[^1]:  a  b \n",
    "math-block": "$$\na  b \n$$\n",
    "thematic-break": "***  \n",
    "escaped-region": "<!-- ocr-clean: off -->\na  b \n<!-- ocr-clean: on -->\n",
    "ambiguous-table": "| `a  b |\n|---|---|\n",
    "ambiguous-html": "<custom-el a=1\n  b  c \n",
}


@pytest.mark.parametrize("name", PROTECTED_DOCUMENTS)
def test_rule_cannot_edit_protected_segment(name):
    text = PROTECTED_DOCUMENTS[name]
    assert segment_document(text).segments[0].is_protected

    def propose(ctx):
        k = ctx.text.index("  ")
        return [TextEdit(k, k + 2, "  ", " ", "attack")]

    with pytest.raises(PipelineError, match="touches protected"):
        run(text, (ScriptedRule(propose),))


@pytest.mark.parametrize("name", PROTECTED_DOCUMENTS)
def test_production_rules_leave_protected_documents_unchanged(name):
    text = PROTECTED_DOCUMENTS[name]
    result = run(text, DEFAULT_RULES)
    assert result.document.text == text
    assert result.report.changes == ()


def test_edit_that_would_remove_protection_fails():
    # Deleting the blank line before indented code would turn it into a
    # paragraph continuation: the protection check must refuse.
    text = "para\n\n    code\n"

    def propose(ctx):
        return [TextEdit(5, 6, "\n", "", "remove separator")]

    with pytest.raises(PipelineError, match="lost its protection"):
        run(text, (ScriptedRule(propose),))


def test_edit_merging_paragraph_into_table_is_allowed_only_if_table_unchanged():
    text = "intro\n\n| a | b |\n|---|---|\n"
    result = run(text, (scripted(TextEdit(0, 5, "intro", "INTRO", "m")),))
    assert result.document.text == "INTRO\n\n| a | b |\n|---|---|\n"


# --- System findings -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"a\r\nb\nc\n", "mixed line endings (2 LF, 1 CRLF); output normalized to LF"),
        (b"a\r\nb\r\nc\n", "mixed line endings (1 LF, 2 CRLF); output normalized to CRLF"),
        (b"a\r\nb\n", "mixed line endings (1 LF, 1 CRLF); output normalized to LF"),
    ],
)
def test_mixed_line_endings_produce_one_system_finding(data, message):
    findings = run(data, DEFAULT_RULES).report.findings
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.rule_id, finding.code, finding.severity) == (
        SYSTEM_RULE_ID,
        MIXED_LINE_ENDINGS,
        Severity.WARNING,
    )
    assert finding.message == message


@pytest.mark.parametrize("data", [b"a\nb\n", b"a\r\nb\r\n", b"", b"no newline"])
def test_consistent_line_endings_produce_no_finding(data):
    assert document_findings(parse_document(data)) == []


@pytest.mark.parametrize(
    ("text", "code", "line", "severity"),
    [
        ("a  b\n\n```\nx\n", "unclosed-fenced-code", 3, Severity.WARNING),
        ("a\n$$\nx\n", "unclosed-math-block", 2, Severity.WARNING),
        ("<!-- x\n", "unclosed-html-comment", 1, Severity.WARNING),
        ("<script>\n", "unclosed-html-block", 1, Severity.WARNING),
        ("---\nx\n", "unclosed-front-matter", 1, Severity.WARNING),
        ("<!-- ocr-clean: off -->\nx\n", "unmatched-ocr-clean-off", 1, Severity.WARNING),
        ("x\n\n<!-- ocr-clean: on -->\n", "unmatched-ocr-clean-on", 3, Severity.WARNING),
        ("| `a | b |\n|---|---|\n", "ambiguous-markdown-table", 1, Severity.INFO),
        ("<custom-el a=1\n", "ambiguous-html-block", 1, Severity.INFO),
    ],
)
def test_segmentation_diagnostics_become_findings_once(text, code, line, severity):
    findings = run(text, (*DEFAULT_RULES, ReplaceRule())).report.findings
    assert len(findings) == 1
    finding = findings[0]
    assert (finding.rule_id, finding.code, finding.severity) == (SEGMENTER_RULE_ID, code, severity)
    assert finding.location == SourceLocation(line)


def test_diagnostic_line_refers_to_original_input():
    text = "a\n\n\n\n\n```\nx\n"
    result = run(text, DEFAULT_RULES)
    assert result.document.text == "a\n\n```\nx\n"
    assert result.report.findings[0].location == SourceLocation(6)


def test_system_findings_precede_rule_findings():
    findings = run(b"a\r\nb\n", (DetectRule(),)).report.findings
    assert [f.rule_id for f in findings] == [SYSTEM_RULE_ID, "fake-detect"]


@pytest.mark.parametrize(
    ("data", "expected"),
    [(b"a\r\nb\nc\n", b"a\nb\nc\n"), (b"a\r\nb\r\nc\n", b"a\r\nb\r\nc\r\n")],
)
def test_output_newline_behavior_unchanged(data, expected):
    assert encode_document(run(data).document) == expected


# --- Original locations -----------------------------------------------------------------


def test_change_locations_refer_to_original_lines_after_blank_collapse():
    text = "a\n\n\n\n\nb  c \n"
    result = run(
        text,
        (BY_ID["excessive-blank-lines"], BY_ID["trailing-whitespace"], BY_ID["repeated-spaces"]),
    )  # blank lines first
    changes = {c.rule_id: c for c in result.report.changes}
    assert changes["excessive-blank-lines"].location == SourceLocation(2, 1, 5)
    assert changes["trailing-whitespace"].location == SourceLocation(6, 5)
    assert changes["repeated-spaces"].location == SourceLocation(6, 2)


def test_column_omitted_inside_previously_replaced_text():
    # UpperRule replaces the whole paragraph; a later edit inside that
    # replacement has no reliable original column, so only the line is kept.
    rule = ScriptedRule(lambda ctx: [TextEdit(2, 3, "A", "z", "inner")])
    result = run("aaaa\n", (UpperRule(), rule))
    assert result.report.changes[1].location == SourceLocation(1)


def test_columns_exact_outside_replaced_text():
    text = "x  y\nlast  word\n"
    result = run(text, (BY_ID["repeated-spaces"],))
    assert [c.location for c in result.report.changes] == [
        SourceLocation(1, 2),
        SourceLocation(2, 5),
    ]


# --- Combined realistic document ----------------------------------------------------------

COMBINED = (
    "---\ntitle:  Sample \n---\n"
    "\n"
    "Café  au   lait in Łódź. \t\n"
    "See `code  x` and [link](https://exämple.io/a  b)[^1].  \n"
    "\n\n\n\n"
    "| City  | Region |\n|:--|--:|\n| Łódź  | Łódzkie |\n"
    "\n"
    "```\ncode   \n\n\n\n```\n"
    "\n"
    "<!-- ocr-clean: off -->\nKeep   as is. \n\n\n\n<!-- ocr-clean: on -->\n"
    "\n"
    "[^1]:  Note. \n"
)

COMBINED_EXPECTED = (
    "---\ntitle:  Sample \n---\n"
    "\n"
    "Café au lait in Łódź.\n"
    "See `code  x` and [link](https://exämple.io/a  b)[^1].  \n"
    "\n"
    "| City  | Region |\n|:--|--:|\n| Łódź  | Łódzkie |\n"
    "\n"
    "```\ncode   \n\n\n\n```\n"
    "\n"
    "<!-- ocr-clean: off -->\nKeep   as is. \n\n\n\n<!-- ocr-clean: on -->\n"
    "\n"
    "[^1]:  Note. \n"
)


def test_combined_document():
    result = run(COMBINED, DEFAULT_RULES)
    assert result.document.text == COMBINED_EXPECTED
    original = segment_document(COMBINED).segments
    for segment in original:
        if segment.is_protected:
            assert segment.text in result.document.text
    assert [(c.rule_id, c.location) for c in result.report.changes] == [
        ("trailing-whitespace", SourceLocation(5, 25)),
        ("repeated-spaces", SourceLocation(5, 5)),
        ("repeated-spaces", SourceLocation(5, 9)),
        ("excessive-blank-lines", SourceLocation(7, 1, 10)),
    ]
    again = run(result.document.text, DEFAULT_RULES)
    assert again.document.text == result.document.text
    assert again.report.changes == ()
