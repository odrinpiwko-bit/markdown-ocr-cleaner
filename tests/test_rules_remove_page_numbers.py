import pytest

from md_ocr_cleaner.document import encode_document, parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.report import Severity, SourceLocation
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.page_numbers import (
    REASON_FINAL_NEWLINE,
    REASON_FORM_FEED,
    REASON_NO_TEXT,
    REASON_STRUCTURE,
    RemovePageNumbers,
)
from md_ocr_cleaner.rules.selection import select_rules
from md_ocr_cleaner.segmenter import segment_document

RULE = RemovePageNumbers()
FF = chr(0x0C)
OFF = "<!-- ocr-clean: off -->"
ON = "<!-- ocr-clean: on -->"
WITH_DETECTION = select_rules(REGISTRY, enable=["remove-page-numbers"])


def run(text, rules=(RULE,)):
    return run_pipeline(parse_document(text.encode("utf-8")), rules, input_path="t.md")


def cleaned(text):
    result = run(text)
    again = run(result.document.text)
    assert again.report.changes == ()  # idempotent
    assert again.document.text == result.document.text
    return result.document.text


def kept(text):
    return [
        (f.location.line, f.message.split(": ", 1)[1])
        for f in run(text).report.findings
        if f.code == "page-number-kept"
    ]


def test_disabled_by_default():
    assert RULE.default_enabled is False
    assert RULE not in select_rules(REGISTRY)


def test_middle_of_document():
    text = "A.\n\n10\n\nB.\n\n11\n\nC.\n\n12\n\nD.\n"
    assert cleaned(text) == "A.\n\nB.\n\nC.\n\nD.\n"


def test_exact_single_separator():
    text = "Text.\n\n10\n\nText.\n\n11\n\nText.\n\n12\n\nText.\n"
    assert "\n\n\n" not in cleaned(text)


def test_start_of_document():
    text = "1\n\nA.\n\n2\n\nB.\n\n3\n\nC.\n"
    assert cleaned(text) == "A.\n\nB.\n\nC.\n"


def test_end_of_document_with_final_newline():
    text = "A.\n\n1\n\nB.\n\n2\n\nC.\n\n3\n"
    assert cleaned(text) == "A.\n\nB.\n\nC.\n"


def test_end_of_document_without_final_newline():
    text = "A.\n\n1\n\nB.\n\n2\n\nC.\n\n3"
    out = cleaned(text)
    assert out == "A.\n\nB.\n\nC."
    assert not out.endswith("\n")


def test_end_without_final_newline_after_heading_is_kept():
    text = "A.\n\n1\n\nB.\n\n2\n\n# Chapter\n\n3"
    result = run(text)
    assert result.document.text.endswith("# Chapter\n\n3")
    assert kept(text) == [(11, REASON_FINAL_NEWLINE)]


def test_crlf_and_bom_preserved():
    data = b"\xef\xbb\xbfA.\r\n\r\n1\r\n\r\nB.\r\n\r\n2\r\n\r\nC.\r\n\r\n3\r\n\r\nD.\r\n"
    doc = parse_document(data, "utf-8-sig")
    out = run_pipeline(doc, (RULE,), input_path="t.md").document
    assert encode_document(out) == b"\xef\xbb\xbfA.\r\n\r\nB.\r\n\r\nC.\r\n\r\nD.\r\n"


def test_possible_candidates_kept_without_extra_finding():
    text = "A.\n\n10\n\nB.\n\n11\n\nC.\n"  # only two: possible
    result = run(text)
    assert result.document.text == text
    assert result.report.findings == ()


def test_sequence_without_step_of_one_is_kept():
    text = "A.\n\n10\n\nB.\n\n13\n\nC.\n\n16\n\nD.\n"
    assert cleaned(text) == text


def test_years_are_never_removed():
    text = "A.\n\n1998\n\nB.\n\n1999\n\nC.\n\n2000\n\nD.\n"
    assert cleaned(text) == text


def test_labelled_year_like_pages_are_removed():
    text = "A.\n\nPage 1998\n\nB.\n\nPage 1999\n\nC.\n\nPage 2000\n\nD.\n"
    assert cleaned(text) == "A.\n\nB.\n\nC.\n\nD.\n"


def test_candidates_without_text_between_are_kept():
    text = "A.\n\n1\n\n2\n\n3\n\nB.\n"
    result = run(text)
    assert result.document.text == text
    assert kept(text) == [(3, REASON_NO_TEXT), (5, REASON_NO_TEXT), (7, REASON_NO_TEXT)]


def test_form_feed_isolated_candidate_is_kept_once():
    text = f"A.\n\n1\n\nB.{FF}\n2\n\nC.\n\n3\n\nD.\n"
    result = run(text)
    assert f"B.{FF}\n2\n" in result.document.text
    assert kept(text) == [(6, REASON_FORM_FEED)]
    assert result.document.text == f"A.\n\nB.{FF}\n2\n\nC.\n\nD.\n"


def test_text_in_form_feed_candidates_paragraph_counts_as_separating_text():
    # "B." shares a paragraph with the form-feed candidate 2 but still lies
    # between candidates 1 and 2, so candidate 1 is removable.
    text = f"A.\n\n1\n\nB.{FF}\n2\n\nC.\n\n3\n\nD.\n"
    removed = [c.message for c in run(text).report.changes if "probable" in c.message]
    assert removed == ["Removed probable page number 1.", "Removed probable page number 3."]


def test_form_feed_on_candidate_line_is_kept():
    text = f"A.\n\n1\n\nB.\n\n{FF}2\n\nC.\n\n3\n\nD.\n"
    result = run(text)
    assert f"{FF}2" in result.document.text
    assert kept(text) == [(7, REASON_FORM_FEED)]


@pytest.mark.parametrize(
    "after",
    ["- item", "  indented text", "    code", "[^1]: note", "[id]: https://x.io", "> quote"],
)
def test_unsafe_following_structure_keeps_candidate(after):
    text = f"A.\n\n1\n\nB.\n\n2\n\n{after}\n\nC.\n\n3\n\nD.\n"
    result = run(text)
    assert "\n2\n" in result.document.text
    assert (7, REASON_STRUCTURE) in kept(text)


def test_unsafe_preceding_structure_keeps_candidate():
    text = "A.\n\n1\n\n- item\n\n2\n\nB.\n\n3\n\nC.\n"
    assert (7, REASON_STRUCTURE) in kept(text)


def test_headings_are_stable_neighbors():
    text = "# A\n\n1\n\n## B\n\n2\n\nC.\n\n3\n\nD\n===\n"
    assert cleaned(text) == "# A\n\n## B\n\nC.\n\nD\n===\n"


def test_one_kept_finding_per_candidate_with_stable_reason():
    text = f"A.\n\n1\n\n2{FF}\n\n3\n\nB.\n"
    findings = [f for f in run(text).report.findings if f.code == "page-number-kept"]
    assert len({f.location.line for f in findings}) == len(findings)
    for f in findings:
        assert f.severity is Severity.INFO
        assert f.message.startswith("kept probable page number ")


def test_protected_and_escaped_content_exact():
    text = (
        f"A.\n\n1\n\n```\n\n2\n\n```\n\nB.\n\n3\n\n{OFF}\n\n4\n\n{ON}\n\nC.\n\n5\n\nD.\n\n6\n\nE.\n"
    )
    out = run(text).document.text
    assert "```\n\n2\n\n```\n" in out
    assert f"{OFF}\n\n4\n\n{ON}\n" in out
    for segment in segment_document(text).segments:
        if segment.is_protected:
            assert segment.text in out


def test_changes_and_detection_findings_use_original_locations():
    text = "A.\n\n\n\n\n10\n\nB.\n\n11\n\nC.\n\n12\n\nD.\n"
    result = run(text, WITH_DETECTION)
    removed = [c for c in result.report.changes if c.rule_id == "remove-page-numbers"]
    assert [
        (c.location, c.message) for c in removed if c.message.startswith("Removed probable")
    ] == [
        (SourceLocation(6, 1), "Removed probable page number 10."),
        (SourceLocation(10, 1), "Removed probable page number 11."),
        (SourceLocation(14, 1), "Removed probable page number 12."),
    ]
    detected = [f for f in result.report.findings if f.rule_id == "detect-page-numbers"]
    assert [f.location.line for f in detected] == [6, 10, 14]  # still reported
    assert result.document.text == "A.\n\nB.\n\nC.\n\nD.\n"
