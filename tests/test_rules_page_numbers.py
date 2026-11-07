import pytest

from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.pagenumbers import find_page_number_candidates
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.report import Severity, SourceLocation
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.page_numbers import DetectPageNumbers
from md_ocr_cleaner.rules.selection import select_rules
from md_ocr_cleaner.segmenter import segment_document

RULE = DetectPageNumbers()
FF = chr(0x0C)
EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
OFF = "<!-- ocr-clean: off -->"
ON = "<!-- ocr-clean: on -->"

DEFAULT_RULES = select_rules(REGISTRY)  # the rules a plain run uses


def run(text, rules=(RULE,)):
    return run_pipeline(parse_document(text.encode("utf-8")), rules, input_path="t.md")


def found(text):
    findings = run(text).report.findings
    return [(f.code, f.location, f.excerpt) for f in findings if f.rule_id == RULE.id]


def kinds(text):
    return [code.split("-")[0] for code, _, _ in found(text)]


def pages(*lines):
    """Prose paragraphs separated by isolated page-number lines."""
    parts = []
    for line in lines:
        parts.append("Page text with a sentence.")
        parts.append(line)
    return "\n\n".join(parts) + "\n"


# --- Candidate forms ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "12",
        "-12-",
        f"{EN_DASH} 12 {EN_DASH}",
        f"{EM_DASH}12{EM_DASH}",
        "[12]",
        "[ 12 ]",
        "12/340",
        "12 / 340",
        "Page 12",
        "page 12",
        "PAGE 12",
        "Page\t12",
        "  12  ",
    ],
)
def test_candidate_forms(line):
    result = run(f"Text.\n\n{line}\n\nText.\n")
    (finding,) = result.report.findings
    assert (finding.code, finding.severity) == ("possible-page-number", Severity.INFO)
    assert finding.message == "possible page number 12"
    assert finding.location.line == 3


def test_hyphen_dashed_form_with_spaces_is_a_list_item_and_ignored():
    # "- 12 -" is Markdown list-item syntax, and list items are never page numbers.
    assert segment_document("- 12 -\n").segments[0].kind.value == "list-item"
    assert found("Text.\n\n- 12 -\n\nText.\n") == []


def test_location_column_skips_leading_whitespace():
    assert found("x\n\n   12\n")[0][1] == SourceLocation(3, 4)


@pytest.mark.parametrize(
    "line",
    [
        f"{EN_DASH} 12 {EM_DASH}",
        "[12",
        "12)",
        "0",
        "12345",
        "12/5",
        "Page",
        "Page12",
        "p. 12",
        "pg. 12",
        "Page. 12",
    ],
)
def test_non_candidate_forms(line):
    assert found(f"Text.\n\n{line}\n\nText.\n") == []


@pytest.mark.parametrize(
    "text",
    [
        "12. item\n",
        "1) item\n",
        "# 12\n",
        "12\n---\n",
        "12\n===\n",
        "| 12 |\n|---|\n",
        "```\n12\n```\n",
        "    12\n",
        "Text\n12\n",
        "12\nText\n",
        "Page 12 is important.\n",
        "On page 12 we see.\n",
        "> 12\n",
        "- 12\n",
        "[^1]: 12\n",
    ],
)
def test_structural_protected_and_embedded_numbers_are_ignored(text):
    assert found(text) == []


# --- Sequences -----------------------------------------------------------------------------


def test_exactly_two_candidates_remain_possible():
    assert kinds(pages("10", "11")) == ["possible", "possible"]


def test_three_sequential_candidates_are_probable():
    result = run(pages("10", "11", "12"))
    assert [f.code for f in result.report.findings] == ["probable-page-number"] * 3
    assert result.report.findings[0].message == "probable page number 10"


def test_small_gaps_allowed():
    assert kinds(pages("10", "12", "13")) == ["probable"] * 3


def test_sequence_without_step_of_one_is_possible():
    assert kinds(pages("10", "13", "16")) == ["possible"] * 3


def test_decreasing_or_large_gap_breaks_sequence():
    assert kinds(pages("10", "11", "9", "20", "21", "22")) == [
        "possible",
        "possible",
        "possible",
        "probable",
        "probable",
        "probable",
    ]


def test_different_forms_do_not_join_one_sequence():
    assert kinds(pages("10", "[11]", "12")) == ["possible"] * 3


def test_bare_year_sequences_are_ignored():
    assert found(pages("1998", "1999", "2000")) == []
    assert found(pages("- 1999 -")) == []
    assert found(pages("[2001]")) == []


def test_four_digit_values_outside_year_range_are_candidates():
    assert kinds(pages("1200", "1201", "1202")) == ["probable"] * 3


def test_labelled_four_digit_pages_are_candidates():
    assert kinds(pages("Page 1998", "Page 1999", "Page 2000")) == ["probable"] * 3


def test_current_total_uses_current_page():
    result = run(pages("12/340", "13/340", "14/340"))
    assert [f.code for f in result.report.findings] == ["probable-page-number"] * 3
    assert [f.message for f in result.report.findings][1] == "probable page number 13"
    candidates = find_page_number_candidates(segment_document(pages("12/340")).segments)
    assert (candidates[0].value, candidates[0].total, candidates[0].form) == (
        12,
        340,
        "current-total",
    )


def test_current_total_with_different_totals_does_not_join():
    assert kinds(pages("12/340", "13/341", "14/340")) == ["possible"] * 3


def test_current_total_year_like_values_are_explicit():
    assert kinds(pages("1998/2100", "1999/2100", "2000/2100")) == ["probable"] * 3


def test_sequence_is_deterministic():
    text = pages("1", "2", "3", "7")
    assert found(text) == found(text)
    assert kinds(text) == ["probable", "probable", "probable", "possible"]


def test_candidates_carry_source_offsets_for_later_removal():
    text = "Text.\n\n  12  \n\nMore.\n"
    (candidate,) = find_page_number_candidates(segment_document(text).segments)
    assert text[candidate.start : candidate.end] == "12"
    assert text[candidate.line_start : candidate.line_end] == "  12  \n"


# --- Form feeds ------------------------------------------------------------------------------


def test_form_feed_immediately_before_candidate_on_same_line():
    text = f"End of a page.\n{FF}12\n\nNext page.\n"
    assert found(text) == [("possible-page-number", SourceLocation(2, 2), "12")]


def test_form_feed_at_end_of_preceding_line():
    text = f"End of a page.{FF}\n12\n\nNext page.\n"
    assert found(text) == [("possible-page-number", SourceLocation(2, 1), "12")]


def test_form_feed_after_candidate_and_before_next_line():
    assert kinds(f"Text.\n\n12{FF}\nNext page.\n") == ["possible"]
    assert kinds(f"Text.\n\n12\n{FF}Next page.\n") == ["possible"]


def test_form_feed_in_prose_does_not_create_candidates():
    assert found(f"Page{FF} 12 is here.\n") == []
    assert found(f"Text {FF}12 more text\nnext\n") == []
    assert found(f"Text\n12 {FF} words\n") == []


def test_form_feed_needs_isolation_on_the_other_side_too():
    assert found(f"The end.{FF}\n12\nmore text\n") == []


def test_form_feed_inside_escaped_region_has_no_effect():
    text = f"{OFF}\nKoniec.{FF}\n{ON}\n12\nText\n"
    assert found(text) == []
    assert found(f"{OFF}\n{FF}12\n\n7\n{ON}\n") == []


# --- Escaped regions and pipeline -------------------------------------------------------


def test_no_page_numbers_inside_escaped_region():
    assert found(f"{OFF}\n\n12\n\n13\n\n14\n\n{ON}\n") == []


def test_directive_lines_produce_nothing():
    assert found(f"{OFF}\n{ON}\n\n{ON}\n") == []


def test_no_edits_and_text_unchanged():
    text = pages("10", "11", "12")
    result = run(text)
    assert result.document.text == text
    assert result.report.changes == ()


def test_realistic_ocr_excerpt():
    text = (
        "# Chapter 1\n\n"
        "Once upon a time.\nPeople lived there.\n\n"
        "1\n\n"
        "Another page of text.\n\n"
        f"{EN_DASH} 2 {EN_DASH}\n\n"
        "The text continues in\n1998 and later.\n\n"
        "3\n\n"
        "The year 1999 mattered.\n\n"
        "4\n\n"
        "5\n"
    )
    assert kinds(text) == ["possible", "possible", "probable", "probable", "probable"]


def test_full_pipeline_locations_are_original():
    text = "Text.\n\n\n\n\n12\n\nMore.\n"
    result = run(text, DEFAULT_RULES)
    page = [f for f in result.report.findings if f.rule_id == RULE.id]
    assert [f.location for f in page] == [SourceLocation(6, 1)]
