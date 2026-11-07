import builtins
import os

import pytest

from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.segmenter import (
    AMBIGUOUS_HTML,
    AMBIGUOUS_TABLE,
    UNCLOSED_COMMENT,
    UNCLOSED_FENCE,
    UNCLOSED_FRONT_MATTER,
    UNCLOSED_HTML,
    UNCLOSED_MATH,
    UNMATCHED_OFF,
    UNMATCHED_ON,
    Protection,
    Segment,
    SegmentationError,
    SegmentKind,
    reassemble,
    segment_document,
    validate_segments,
)

K = SegmentKind
P = Protection
OFF = "<!-- ocr-clean: off -->"
ON = "<!-- ocr-clean: on -->"


def seg(text):
    """Segment text and check every structural invariant."""
    result = segment_document(text)
    validate_segments(text, result.segments)
    assert reassemble(result.segments) == text
    for s in result.segments:
        assert text[s.start : s.end] == s.text
        assert s.text.startswith(s.prefix)
    assert segment_document(text) == result  # deterministic
    return result


def shape(text):
    return [(s.kind, s.protection, s.text) for s in seg(text).segments]


def kinds(text):
    return [s.kind for s in seg(text).segments]


def codes(text):
    return [(d.code, d.line) for d in seg(text).diagnostics]


# --- General invariants ------------------------------------------------------------------


def test_empty_input():
    result = seg("")
    assert result.segments == ()
    assert result.diagnostics == ()


def test_one_line_without_newline():
    assert shape("Hello") == [(K.PARAGRAPH, P.EDITABLE, "Hello")]


def test_only_newline():
    assert shape("\n") == [(K.BLANK, P.STRUCTURAL, "\n")]


def test_unicode_prose():
    text = "Café au lait in Łódź.\náäčďéľňóôšťúýž ąęłőű ŁŐŘ\n\nNext paragraph.\n"
    assert shape(text) == [
        (K.PARAGRAPH, P.EDITABLE, "Café au lait in Łódź.\náäčďéľňóôšťúýž ąęłőű ŁŐŘ\n"),
        (K.BLANK, P.STRUCTURAL, "\n"),
        (K.PARAGRAPH, P.EDITABLE, "Next paragraph.\n"),
    ]


def test_positions_match_source():
    text = "# A\n\nprose line\nmore\n```\ncode\n```\n"
    segments = seg(text).segments
    assert [(s.start_line, s.end_line) for s in segments] == [(1, 1), (2, 2), (3, 4), (5, 7)]
    assert [(s.start, s.end) for s in segments] == [(0, 4), (4, 5), (5, 21), (21, 34)]


def test_only_newline_splits_lines():
    text = "a\rb\fc\u2028d\n"
    result = seg(text)
    assert len(result.segments) == 1
    assert result.segments[0].end_line == 1


def test_blank_lines_with_whitespace_grouped():
    assert shape("a\n \n\t\n\nb") == [
        (K.PARAGRAPH, P.EDITABLE, "a\n"),
        (K.BLANK, P.STRUCTURAL, " \n\t\n\n"),
        (K.PARAGRAPH, P.EDITABLE, "b"),
    ]


def test_segmenter_performs_no_file_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem access")

    text = FULL_EXAMPLE
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    result = segment_document(text)
    assert reassemble(result.segments) == text


# --- Segment model -------------------------------------------------------------------


def _segment(**overrides):
    values = {
        "kind": K.PARAGRAPH,
        "protection": P.EDITABLE,
        "text": "ab",
        "start_line": 1,
        "end_line": 1,
        "start": 0,
        "end": 2,
    }
    values.update(overrides)
    return Segment(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_line": 0},
        {"end_line": 0},
        {"start": -1, "end": 1},
        {"start": 2, "end": 2, "text": ""},
        {"end": 3},
        {"prefix": "x"},
    ],
)
def test_segment_invariants(overrides):
    with pytest.raises(SegmentationError):
        _segment(**overrides)


def test_validate_detects_gaps_and_wrong_slices():
    with pytest.raises(SegmentationError, match="gap"):
        validate_segments("abcd", [_segment(start=1, end=3, text="bc")])
    with pytest.raises(SegmentationError, match="exact slice"):
        validate_segments("xy", [_segment()])
    with pytest.raises(SegmentationError, match="segments end"):
        validate_segments("ab\nc", [_segment(text="ab\n", end=3)])
    with pytest.raises(SegmentationError, match="mid-line"):
        validate_segments("abc", [_segment()])
    with pytest.raises(SegmentationError, match="wrong line"):
        validate_segments("ab", [_segment(start_line=2, end_line=2)])


# --- YAML front matter ----------------------------------------------------------------


def test_front_matter_closed_with_dashes():
    text = "---\ntitle: Test\n---\nText\n"
    assert shape(text)[0] == (K.FRONT_MATTER, P.PROTECTED, "---\ntitle: Test\n---\n")


def test_front_matter_closed_with_dots():
    assert kinds("---\na: 1\n...\nText\n") == [K.FRONT_MATTER, K.PARAGRAPH]


def test_front_matter_with_unicode_text():
    text = "---\ntitle: Größe Łódź\n---\n"
    assert shape(text) == [(K.FRONT_MATTER, P.PROTECTED, text)]


def test_empty_front_matter():
    assert kinds("---\n---\nx") == [K.FRONT_MATTER, K.PARAGRAPH]


def test_unclosed_front_matter_protects_to_eof():
    text = "---\ntitle: x\n\n# Not heading\n"
    assert shape(text) == [(K.FRONT_MATTER, P.PROTECTED, text)]
    assert codes(text) == [(UNCLOSED_FRONT_MATTER, 1)]


def test_dashes_not_on_first_line_are_not_front_matter():
    assert kinds("\n---\na: 1\n---\n") == [K.BLANK, K.THEMATIC_BREAK, K.SETEXT_HEADING]


def test_front_matter_opener_must_be_exact():
    assert K.FRONT_MATTER not in kinds("--- \na\n---\n")


def test_horizontal_rule_after_prose_is_not_front_matter():
    assert kinds("Prose\n\n---\n\nMore\n") == [
        K.PARAGRAPH,
        K.BLANK,
        K.THEMATIC_BREAK,
        K.BLANK,
        K.PARAGRAPH,
    ]


# --- Fenced code ---------------------------------------------------------------------


def test_backtick_fence():
    text = "```\ncode  \n```\n"
    assert shape(text) == [(K.FENCED_CODE, P.PROTECTED, text)]


def test_tilde_fence_with_language():
    assert shape("~~~python\nx = 1\n~~~\nafter\n")[0][:2] == (K.FENCED_CODE, P.PROTECTED)


def test_longer_opening_fence_and_inner_backticks():
    text = "`````\n```\nnot closed\n````\n`````\nafter\n"
    assert shape(text)[0] == (K.FENCED_CODE, P.PROTECTED, "`````\n```\nnot closed\n````\n`````\n")


def test_too_short_closing_fence_does_not_close():
    text = "````\ncode\n```\nstill code\n"
    assert shape(text) == [(K.FENCED_CODE, P.PROTECTED, text)]
    assert codes(text) == [(UNCLOSED_FENCE, 1)]


def test_wrong_character_does_not_close():
    text = "```\ncode\n~~~\n"
    assert shape(text) == [(K.FENCED_CODE, P.PROTECTED, text)]


def test_closing_fence_may_be_longer():
    assert kinds("```\nx\n``````\ny\n") == [K.FENCED_CODE, K.PARAGRAPH]


def test_backtick_info_with_backtick_is_not_a_fence():
    assert kinds("``` a ` b\nx\n") == [K.PARAGRAPH]


def test_unclosed_fence_protects_to_eof():
    text = "Intro\n\n```\ncode\n\n# heading-like\n"
    assert shape(text)[-1] == (K.FENCED_CODE, P.PROTECTED, "```\ncode\n\n# heading-like\n")
    assert codes(text) == [(UNCLOSED_FENCE, 3)]


def test_directive_inside_fence_is_ignored():
    text = f"```\n{OFF}\n```\nprose\n"
    assert kinds(text) == [K.FENCED_CODE, K.PARAGRAPH]
    assert codes(text) == []


def test_unicode_inside_code_unchanged():
    text = "```\ngröße = 'łódź'  \n```\n"
    assert seg(text).segments[0].text == text


# --- Indented code -------------------------------------------------------------------


def test_four_space_code():
    assert shape("    code\n") == [(K.INDENTED_CODE, P.PROTECTED, "    code\n")]


def test_tab_code():
    assert shape("\tcode\n") == [(K.INDENTED_CODE, P.PROTECTED, "\tcode\n")]


def test_blank_lines_inside_indented_code():
    text = "para\n\n    a\n\n    b\n\nafter\n"
    assert shape(text) == [
        (K.PARAGRAPH, P.EDITABLE, "para\n"),
        (K.BLANK, P.STRUCTURAL, "\n"),
        (K.INDENTED_CODE, P.PROTECTED, "    a\n\n    b\n"),
        (K.BLANK, P.STRUCTURAL, "\n"),
        (K.PARAGRAPH, P.EDITABLE, "after\n"),
    ]


def test_indented_line_after_paragraph_is_continuation():
    assert kinds("para\n    still para\n") == [K.PARAGRAPH]


def test_list_continuation_is_not_code():
    text = "- item\n\n  continued\n"
    assert shape(text) == [(K.LIST_ITEM, P.STRUCTURAL, text)]


def test_four_space_list_continuation_is_not_code():
    text = "10. item\n\n    continued\n"
    assert shape(text) == [(K.LIST_ITEM, P.STRUCTURAL, text)]


def test_ambiguous_deep_indentation_in_list_is_protected():
    text = "- item\n\n      code or text?\n"
    assert shape(text) == [(K.LIST_ITEM, P.PROTECTED, text)]


# --- HTML -----------------------------------------------------------------------------


def test_one_line_comment():
    assert shape("<!-- note -->\ntext\n")[0] == (K.HTML_COMMENT, P.PROTECTED, "<!-- note -->\n")


def test_multiline_comment():
    text = "<!--\nline\n-->\nafter\n"
    assert kinds(text) == [K.HTML_COMMENT, K.PARAGRAPH]


def test_unclosed_comment_protects_to_eof():
    text = "<!-- open\n\ntext\n"
    assert shape(text) == [(K.HTML_COMMENT, P.PROTECTED, text)]
    assert codes(text) == [(UNCLOSED_COMMENT, 1)]


def test_directive_like_text_in_comment_is_ignored():
    text = f"<!--\n{OFF}\n-->\nprose\n"
    assert kinds(text) == [K.HTML_COMMENT, K.PARAGRAPH]


def test_block_html_until_blank_line():
    text = '<div class="x">\n  *text*\n</div>\n\nafter\n'
    assert shape(text)[0] == (K.HTML_BLOCK, P.PROTECTED, '<div class="x">\n  *text*\n</div>\n')


def test_raw_html_block_until_closing_tag():
    text = "<pre>\na\n\nb\n</pre>\nafter\n"
    assert shape(text)[0] == (K.HTML_BLOCK, P.PROTECTED, "<pre>\na\n\nb\n</pre>\n")


def test_unclosed_raw_html_protects_to_eof():
    text = "<script>\nx\n\ny\n"
    assert shape(text) == [(K.HTML_BLOCK, P.PROTECTED, text)]
    assert codes(text) == [(UNCLOSED_HTML, 1)]


def test_lone_complete_tag_is_html_block():
    assert kinds("<custom-el>\n") == [K.HTML_BLOCK]


def test_inline_html_stays_in_paragraph():
    assert kinds("Text with <b>bold</b> inside.\n") == [K.PARAGRAPH]


def test_html_block_stops_at_directive():
    text = f"<div>\n{OFF}\nsecret\n"
    assert kinds(text) == [K.HTML_BLOCK, K.ESCAPED_REGION]


# Milestone 5 regression: blank lines inside a raw element opened within a
# block-level HTML block are content and must stay protected.


def test_pre_inside_html_block_keeps_block_open_across_blank_lines():
    text = "<div>\n<pre>\n\n\n\n</pre>\n</div>\n\nafter\n"
    assert shape(text)[0] == (K.HTML_BLOCK, P.PROTECTED, "<div>\n<pre>\n\n\n\n</pre>\n</div>\n")
    assert codes(text) == []


def test_unclosed_pre_inside_html_block_protects_to_eof():
    text = "<div><pre>\n\nx\n\ny\n"
    assert shape(text) == [(K.HTML_BLOCK, P.PROTECTED, text)]
    assert codes(text) == [(UNCLOSED_HTML, 1)]


def test_closed_pre_on_one_line_does_not_extend_block():
    assert kinds("<div><pre>x</pre>\n\nafter\n") == [K.HTML_BLOCK, K.BLANK, K.PARAGRAPH]


# --- Tables ------------------------------------------------------------------------------


def test_pipe_table():
    text = "| a | b |\n|---|---|\n| 1 | 2 |\n\nafter\n"
    assert shape(text)[0] == (K.TABLE, P.PROTECTED, "| a | b |\n|---|---|\n| 1 | 2 |\n")


def test_table_alignment_markers():
    assert kinds("a | b | c\n:--- | ---: | :---:\n1 | 2 | 3\n") == [K.TABLE]


def test_table_without_leading_pipes():
    assert kinds("a | b\n--- | ---\nx | y\n") == [K.TABLE]


def test_table_rows_continue_until_blank():
    text = "| a |\n| - |\n| x |\nrow without pipe\n\np\n"
    assert shape(text)[0] == (K.TABLE, P.PROTECTED, "| a |\n| - |\n| x |\nrow without pipe\n")


def ambiguous_table(text):
    """Assert the whole text is one protected table flagged as ambiguous."""
    assert shape(text) == [(K.TABLE, P.PROTECTED, text)]
    assert codes(text) == [(AMBIGUOUS_TABLE, 1)]


def test_valid_table_has_no_diagnostic():
    assert codes("| a | b |\n|---|---|\n| 1 | 2 |\n") == []


# Milestone 4.1: strong table evidence (a delimiter row under a line with a
# pipe) is protected even when inline syntax makes the cell count uncertain.


def test_escaped_pipe_with_delimiter_row_is_protected():
    ambiguous_table("a \\| b\n---|---\n")


def test_pipe_inside_code_with_delimiter_row_is_protected():
    ambiguous_table("`a | b`\n---|---\n")


def test_cell_count_mismatch_is_protected():
    ambiguous_table("| a | b |\n|---|\n")


def test_unmatched_backtick_in_header_is_protected():
    ambiguous_table("| `code | b |\n|---|---|\n| 1 | 2 |\n")


def test_unmatched_backtick_in_body_row_is_protected():
    text = "| a | b |\n|---|---|\n| `x | y |\n"
    assert shape(text) == [(K.TABLE, P.PROTECTED, text)]
    assert codes(text) == []  # header and delimiter agree; body is protected anyway


def test_malformed_link_in_table_is_protected():
    # Cell counting ignores links, so this passes the strict check and needs no
    # diagnostic; what matters is that it is a protected table.
    text = "| [link](https://x.io | b |\n|---|---|\n| 1 | 2 |\n"
    assert shape(text) == [(K.TABLE, P.PROTECTED, text)]


def test_malformed_link_hiding_pipe_count_is_protected():
    ambiguous_table("| `[a](x | b |\n|---|---|\n")


def test_ambiguous_table_with_unicode_text():
    text = "| City `code | Region |\n|:--|--:|\n| Łódź | Łódzkie ąęł |\n"
    result = seg(text)
    (table,) = result.segments
    assert (table.kind, table.protection) == (K.TABLE, P.PROTECTED)
    assert text[table.start : table.end] == table.text == text
    assert [d.code for d in result.diagnostics] == [AMBIGUOUS_TABLE]


def test_ambiguous_table_splits_preceding_paragraph():
    text = "intro\n| `a | b |\n|---|---|\n"
    assert shape(text) == [
        (K.PARAGRAPH, P.EDITABLE, "intro\n"),
        (K.TABLE, P.PROTECTED, "| `a | b |\n|---|---|\n"),
    ]


def test_escaped_pipe_inside_table_cell():
    assert kinds("| a \\| b | c |\n|---|---|\n") == [K.TABLE]
    assert codes("| a \\| b | c |\n|---|---|\n") == []


def test_single_pipe_prose_is_not_table():
    assert shape("This | that.\nnext line\n") == [
        (K.PARAGRAPH, P.EDITABLE, "This | that.\nnext line\n")
    ]


def test_unmatched_backtick_and_pipe_without_delimiter_is_prose():
    text = "This `is | that.\nnext line\n"
    assert shape(text) == [(K.PARAGRAPH, P.EDITABLE, text)]
    assert codes(text) == []


def test_delimiter_row_without_pipe_in_header_is_not_table():
    assert kinds("a `b\n---|---\n") == [K.PARAGRAPH]


def test_malformed_delimiter_row_is_not_table():
    assert kinds("| a | b |\n|--x|---|\n") == [K.PARAGRAPH]


def test_table_inside_paragraph_splits_it():
    assert kinds("intro\n| a | b |\n|---|---|\n") == [K.PARAGRAPH, K.TABLE]


def test_table_with_unicode_text():
    text = "| City | Region |\n|:--|:--|\n| Łódź | Łódzkie |\n"
    assert shape(text) == [(K.TABLE, P.PROTECTED, text)]


# --- References and footnotes ----------------------------------------------------------


def test_link_reference_definition():
    assert shape("[id]: https://example.com\n") == [
        (K.LINK_REFERENCE, P.PROTECTED, "[id]: https://example.com\n")
    ]


def test_link_reference_title_continuation():
    text = '[id]: https://example.com\n  "Title"\nprose\n'
    assert shape(text)[0] == (
        K.LINK_REFERENCE,
        P.PROTECTED,
        '[id]: https://example.com\n  "Title"\n',
    )


def test_link_reference_destination_on_next_line():
    text = "[id]:\nhttps://example.com\n'Title'\n"
    assert shape(text) == [(K.LINK_REFERENCE, P.PROTECTED, text)]


def test_ordinary_link_is_not_reference_definition():
    assert kinds("[text](https://x.io) and [ref][id]\n") == [K.PARAGRAPH]


def test_footnote_definition():
    assert shape("[^1]: Note.\n") == [(K.FOOTNOTE_DEFINITION, P.PROTECTED, "[^1]: Note.\n")]


def test_footnote_indented_and_blank_continuation():
    text = "[^n]: First.\n    second\n\n    third para\n\nafter\n"
    assert shape(text)[0] == (
        K.FOOTNOTE_DEFINITION,
        P.PROTECTED,
        "[^n]: First.\n    second\n\n    third para\n",
    )
    assert kinds(text)[-1] == K.PARAGRAPH


def test_footnote_lazy_continuation_is_protected():
    assert kinds("[^n]: First\nlazy line\n[^m]: Next\n") == [
        K.FOOTNOTE_DEFINITION,
        K.FOOTNOTE_DEFINITION,
    ]


# --- Headings and structure --------------------------------------------------------------


def test_atx_headings():
    result = seg("# H1\n### H3 ###\n#hashtag\n")
    assert [(s.kind, s.prefix) for s in result.segments] == [
        (K.ATX_HEADING, "# "),
        (K.ATX_HEADING, "### "),
        (K.PARAGRAPH, ""),
    ]
    assert result.segments[0].protection is P.STRUCTURAL


def test_setext_headings():
    assert shape("Title\n=====\nSub\n---\n") == [
        (K.SETEXT_HEADING, P.STRUCTURAL, "Title\n=====\n"),
        (K.SETEXT_HEADING, P.STRUCTURAL, "Sub\n---\n"),
    ]


def test_multiline_setext_heading():
    assert shape("a\nb\n===\n") == [(K.SETEXT_HEADING, P.STRUCTURAL, "a\nb\n===\n")]


def test_thematic_break_vs_setext():
    assert kinds("para\n\n---\n") == [K.PARAGRAPH, K.BLANK, K.THEMATIC_BREAK]
    assert kinds("para\n---\n") == [K.SETEXT_HEADING]
    assert kinds("para\n- - -\n") == [K.PARAGRAPH, K.THEMATIC_BREAK]
    assert shape("***\n") == [(K.THEMATIC_BREAK, P.PROTECTED, "***\n")]


def test_unordered_list_items_are_separate():
    result = seg("- one\n- two\n* three\n+ four\n")
    assert [(s.kind, s.prefix, s.text) for s in result.segments] == [
        (K.LIST_ITEM, "- ", "- one\n"),
        (K.LIST_ITEM, "- ", "- two\n"),
        (K.LIST_ITEM, "* ", "* three\n"),
        (K.LIST_ITEM, "+ ", "+ four\n"),
    ]
    assert {s.protection for s in result.segments} == {P.STRUCTURAL}


def test_ordered_list():
    result = seg("1. one\n2) two\n")
    assert [(s.kind, s.prefix) for s in result.segments] == [
        (K.LIST_ITEM, "1. "),
        (K.LIST_ITEM, "2) "),
    ]


def test_task_list():
    result = seg("- [ ] todo\n- [x] done\n- [link] not task\n")
    assert [(s.kind, s.prefix) for s in result.segments] == [
        (K.TASK_ITEM, "- [ ] "),
        (K.TASK_ITEM, "- [x] "),
        (K.LIST_ITEM, "- "),
    ]


def test_list_item_lazy_continuation_and_nesting():
    text = "- parent\nlazy\n  - child\n    child text\n- sibling\n"
    assert shape(text) == [
        (K.LIST_ITEM, P.STRUCTURAL, "- parent\nlazy\n  - child\n    child text\n"),
        (K.LIST_ITEM, P.STRUCTURAL, "- sibling\n"),
    ]


def test_list_item_interrupts_paragraph():
    assert kinds("prose\n- item\nprose again\n") == [K.PARAGRAPH, K.LIST_ITEM]


def test_list_item_with_fence_is_protected():
    text = "- item\n\n  ```\n  code\n\n  x\n  ```\n- next\n"
    assert shape(text) == [
        (K.LIST_ITEM, P.PROTECTED, "- item\n\n  ```\n  code\n\n  x\n  ```\n"),
        (K.LIST_ITEM, P.STRUCTURAL, "- next\n"),
    ]


def test_blockquote():
    result = seg("> quoted\n> more\nlazy\n\nafter\n")
    first = result.segments[0]
    assert (first.kind, first.protection, first.text, first.prefix) == (
        K.BLOCKQUOTE,
        P.STRUCTURAL,
        "> quoted\n> more\nlazy\n",
        "> ",
    )


def test_nested_blockquote_prefix():
    assert seg("> > deep\n").segments[0].prefix == "> > "


def test_blockquote_with_code_is_protected():
    text = "> ```\n> code\n> ```\n"
    assert shape(text) == [(K.BLOCKQUOTE, P.PROTECTED, text)]


def test_blockquote_with_list_stays_structural():
    assert shape("> - a\n")[0][:2] == (K.BLOCKQUOTE, P.STRUCTURAL)


def test_list_marker_without_space_is_prose():
    assert kinds("-not a list\n1.5 million\n") == [K.PARAGRAPH]


# --- Math -------------------------------------------------------------------------------


def test_closed_math_block():
    text = "$$\nx^2  +  y\n$$\nafter\n"
    assert shape(text)[0] == (K.MATH_BLOCK, P.PROTECTED, "$$\nx^2  +  y\n$$\n")


def test_single_line_math_block():
    assert kinds("$$ a = b $$\ntext\n") == [K.MATH_BLOCK, K.PARAGRAPH]


def test_unclosed_math_protects_to_eof():
    text = "$$\nx\n\ny\n"
    assert shape(text) == [(K.MATH_BLOCK, P.PROTECTED, text)]
    assert codes(text) == [(UNCLOSED_MATH, 1)]


def test_directive_inside_math_is_ignored():
    text = f"$$\n{OFF}\n$$\nprose\n"
    assert kinds(text) == [K.MATH_BLOCK, K.PARAGRAPH]


# --- Escape hatch -------------------------------------------------------------------------


def test_off_on_pair():
    text = f"before\n{OFF}\nraw   text\n# not heading\n{ON}\nafter\n"
    assert shape(text) == [
        (K.PARAGRAPH, P.EDITABLE, "before\n"),
        (K.ESCAPED_REGION, P.PROTECTED, f"{OFF}\nraw   text\n# not heading\n{ON}\n"),
        (K.PARAGRAPH, P.EDITABLE, "after\n"),
    ]
    assert codes(text) == []


def test_unmatched_off_protects_to_eof():
    text = f"a\n\n{OFF}\nrest\n\nmore\n"
    assert shape(text)[-1] == (K.ESCAPED_REGION, P.PROTECTED, f"{OFF}\nrest\n\nmore\n")
    assert codes(text) == [(UNMATCHED_OFF, 3)]


def test_unmatched_on_is_protected_with_diagnostic():
    text = f"a\n{ON}\nb\n"
    assert shape(text) == [
        (K.PARAGRAPH, P.EDITABLE, "a\n"),
        (K.DIRECTIVE, P.PROTECTED, f"{ON}\n"),
        (K.PARAGRAPH, P.EDITABLE, "b\n"),
    ]
    assert codes(text) == [(UNMATCHED_ON, 2)]


def test_nested_off_uses_depth_counter():
    text = f"{OFF}\na\n{OFF}\nb\n{ON}\nstill off\n{ON}\nafter\n"
    assert shape(text) == [
        (K.ESCAPED_REGION, P.PROTECTED, f"{OFF}\na\n{OFF}\nb\n{ON}\nstill off\n{ON}\n"),
        (K.PARAGRAPH, P.EDITABLE, "after\n"),
    ]


def test_nested_off_without_enough_on_protects_to_eof():
    text = f"{OFF}\n{OFF}\nx\n{ON}\ny\n"
    assert shape(text) == [(K.ESCAPED_REGION, P.PROTECTED, text)]
    assert codes(text) == [(UNMATCHED_OFF, 1)]


def test_multiple_regions():
    text = f"{OFF}\na\n{ON}\nmid\n{OFF}\nb\n{ON}\n"
    assert kinds(text) == [K.ESCAPED_REGION, K.PARAGRAPH, K.ESCAPED_REGION]


@pytest.mark.parametrize(
    "line",
    [
        "<!--ocr-clean:off-->",
        "  <!--   ocr-clean:   off   -->  ",
        "\t<!-- ocr-clean: off -->",
        "    <!-- ocr-clean: off -->",
    ],
)
def test_directive_whitespace_policy(line):
    assert kinds(f"{line}\nx\n") == [K.ESCAPED_REGION]


@pytest.mark.parametrize(
    "line",
    [
        "<!-- ocr-clean: Off -->",
        "<!-- ocr-clean: offx -->",
        "<!-- ocr-clean off -->",
        "<!-- ocr_clean: off -->",
        "text <!-- ocr-clean: off -->",
        "`<!-- ocr-clean: off -->`",
    ],
)
def test_near_matches_are_not_directives(line):
    result = seg(f"{line}\nx\n")
    assert K.ESCAPED_REGION not in [s.kind for s in result.segments]
    assert result.diagnostics == ()


def test_directive_inside_front_matter_is_ignored():
    text = f"---\n{OFF}\n---\nprose\n"
    assert kinds(text) == [K.FRONT_MATTER, K.PARAGRAPH]


def test_on_inside_fence_within_region_does_not_end_region():
    text = f"{OFF}\n```\n{ON}\n```\nstill off\n{ON}\nafter\n"
    assert kinds(text) == [K.ESCAPED_REGION, K.PARAGRAPH]
    assert seg(text).segments[1].text == "after\n"


def test_directive_interrupts_paragraph_and_list():
    assert kinds(f"prose\n{OFF}\nx\n{ON}\n") == [K.PARAGRAPH, K.ESCAPED_REGION]
    assert kinds(f"- item\n  {OFF}\nx\n{ON}\n") == [K.LIST_ITEM, K.ESCAPED_REGION]


def test_directive_inside_blockquote_is_not_honored_but_protected():
    result = seg(f"> {OFF}\n> x\n")
    assert (result.segments[0].kind, result.segments[0].protection) == (K.BLOCKQUOTE, P.PROTECTED)


def test_prefixed_directive_does_not_change_global_state():
    text = f"> {OFF}\n\nprose after\n\n{ON}\n"
    assert shape(text) == [
        (K.BLOCKQUOTE, P.PROTECTED, f"> {OFF}\n"),
        (K.BLANK, P.STRUCTURAL, "\n"),
        (K.PARAGRAPH, P.EDITABLE, "prose after\n"),
        (K.BLANK, P.STRUCTURAL, "\n"),
        (K.DIRECTIVE, P.PROTECTED, f"{ON}\n"),
    ]
    # The prefixed "off" did not open a region, so the bare "on" is unmatched.
    assert codes(text) == [(UNMATCHED_ON, 5)]


def test_prefixed_on_does_not_close_region():
    text = f"{OFF}\n> {ON}\nstill off\n{ON}\nafter\n"
    assert kinds(text) == [K.ESCAPED_REGION, K.PARAGRAPH]


def test_unprefixed_directives_still_work():
    assert kinds(f"a\n\n{OFF}\nb\n{ON}\n\nc\n") == [
        K.PARAGRAPH,
        K.BLANK,
        K.ESCAPED_REGION,
        K.BLANK,
        K.PARAGRAPH,
    ]


# --- Milestone 4.1 audit: strong block signals with ambiguous inline content -----------


def test_incomplete_html_tag_is_protected():
    text = '<div class="x\n  y">\ncontent\n\nafter\n'
    result = seg(text)
    assert (result.segments[0].kind, result.segments[0].protection) == (K.HTML_BLOCK, P.PROTECTED)
    assert result.segments[0].text == '<div class="x\n  y">\ncontent\n'
    assert codes(text) == []  # <div is a known block tag: not ambiguous


def test_incomplete_unknown_tag_is_protected_with_diagnostic():
    text = '<custom-el data-x="1"\n  data-y="2">\n\nafter\n'
    assert shape(text)[0] == (K.HTML_BLOCK, P.PROTECTED, '<custom-el data-x="1"\n  data-y="2">\n')
    assert codes(text) == [(AMBIGUOUS_HTML, 1)]


@pytest.mark.parametrize("text", ["<3 love it\n", "< not a tag\n", "<https://x.io> link\n"])
def test_angle_bracket_prose_is_not_html(text):
    assert kinds(text) == [K.PARAGRAPH]


def test_setext_with_ambiguous_inline_is_structural():
    assert shape("Heading `code\n---\n") == [
        (K.SETEXT_HEADING, P.STRUCTURAL, "Heading `code\n---\n")
    ]


def test_link_reference_with_malformed_destination_is_protected():
    assert shape("[id]: <https://x.io\n") == [
        (K.LINK_REFERENCE, P.PROTECTED, "[id]: <https://x.io\n")
    ]


def test_footnote_with_malformed_continuation_is_protected():
    text = "[^1]: Text `code\n    continued [link](x\n"
    assert shape(text) == [(K.FOOTNOTE_DEFINITION, P.PROTECTED, text)]


def test_math_with_inline_noise_is_protected():
    text = "$$ a | b `c\n$$\n"
    assert shape(text) == [(K.MATH_BLOCK, P.PROTECTED, text)]


@pytest.mark.parametrize(
    "text",
    [
        "- item\n  | `a | b |\n  |---|---|\n",
        "> | `a | b |\n> |---|---|\n",
        "- item\n\n  ```\n  unclosed\n",
        "> $$\n> x\n",
        "- item\n  <div class='x\n",
    ],
)
def test_containers_with_malformed_protected_constructs_are_protected(text):
    assert seg(text).segments[0].protection is P.PROTECTED


def test_escape_region_reassembles_exactly():
    text = f"  {OFF}  \nwhat   is  here \t\n{ON}\n"
    assert seg(text).segments[0].text == text


# --- Protection invariants over mixed fixtures ------------------------------------------

FULL_EXAMPLE = f"""---
title: Sample
---

# Chapter 1

Café au lait in Łódź. Use `code  with spaces` and [link](https://exämple.io/a  b).

| City | Region |
|:------|-----:|
| Łódź | Łódzkie |

```python
print("Größe Łódź")
```

{OFF}
This   must   not   change.
{ON}

- [ ] task
- item

> quote

[^1]: Note.
[id]: https://example.com
"""

FIXTURES = [
    FULL_EXAMPLE,
    "plain\r\ntext with lone\rcarriage return\n",
    "```\nunclosed",
    "---\nunclosed front matter",
    "<!-- unclosed",
    "$$\nunclosed math",
    f"{OFF}\nunclosed off",
    f"{ON}\n",
    "- a\n  - b\n    - c\n\n      deep\n",
    "> a\n>\n> ```\n> b\n",
    "|a|\n|-|\n",
    "    \n\t\n",
    "\n\n\n",
    "x\n===\n---\n***\n",
    "[^a]:\n\n    x\n",
    "<div>\n\n</div>\n",
    "1.\n2.\n-\n",
]


@pytest.mark.parametrize("text", FIXTURES)
def test_protected_segments_are_exact_source_slices(text):
    result = seg(text)
    for s in result.segments:
        if s.is_protected:
            assert s.text == text[s.start : s.end]
            line_starts = [0] + [k + 1 for k, ch in enumerate(text) if ch == "\n"]
            assert line_starts[s.start_line - 1] == s.start


def test_full_example_classification():
    result = seg(FULL_EXAMPLE)
    summary = [(s.kind, s.protection) for s in result.segments if s.kind is not K.BLANK]
    assert summary == [
        (K.FRONT_MATTER, P.PROTECTED),
        (K.ATX_HEADING, P.STRUCTURAL),
        (K.PARAGRAPH, P.EDITABLE),
        (K.TABLE, P.PROTECTED),
        (K.FENCED_CODE, P.PROTECTED),
        (K.ESCAPED_REGION, P.PROTECTED),
        (K.TASK_ITEM, P.STRUCTURAL),
        (K.LIST_ITEM, P.STRUCTURAL),
        (K.BLOCKQUOTE, P.STRUCTURAL),
        (K.FOOTNOTE_DEFINITION, P.PROTECTED),
        (K.LINK_REFERENCE, P.PROTECTED),
    ]
    assert result.diagnostics == ()


def test_segmenting_does_not_affect_pipeline_or_document():
    data = FULL_EXAMPLE.replace("\n", "\r\n").encode("utf-8")
    doc = parse_document(data)
    seg(doc.text)
    result = run_pipeline(doc, (), input_path="x.md")
    assert result.document is doc
    assert result.report.findings == ()


def test_delimiter_row_with_long_space_run_is_handled():
    # Regression: the previous single regex backtracked quadratically here.
    text = "x | y\n|-" + " " * 200000 + "x\n"
    assert reassemble(seg(text).segments) == text


@pytest.mark.parametrize("row", ["\t|---|", "||---|", "|---||", "    ---|---"])
def test_delimiter_row_grammar_unchanged(row):
    # Same answers as the original regex grammar.
    expected = {"\t|---|": False, "||---|": False, "|---||": False, "    ---|---": True}
    from md_ocr_cleaner.segmenter import _is_delimiter_row

    assert _is_delimiter_row(row) is expected[row]


# --- List items whose first line is code-like ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("-     code\n", K.LIST_ITEM),
        ("*      code\n", K.LIST_ITEM),
        ("1.     code\n", K.LIST_ITEM),
        ("10)     code\n", K.LIST_ITEM),
        ("-\t\tcode\n", K.LIST_ITEM),
        ("1.\t\tcode\n", K.LIST_ITEM),
        ("- [ ]     code\n", K.TASK_ITEM),
        ("- [x]\t\tcode\n", K.TASK_ITEM),
        ("-     [ ] code\n", K.TASK_ITEM),
        ("-     code\n  more\n", K.LIST_ITEM),
    ],
)
def test_list_item_with_code_like_first_line_is_protected(text, kind):
    assert shape(text) == [(kind, P.PROTECTED, text)]


@pytest.mark.parametrize(
    "text",
    [
        "- <!-- comment\n  -->\n",
        "- <div>\n",
        "- [a]: /url\n",
        "- [^1]: note\n",
        "- $$\n",
        "- ```\n",
        f"- {OFF}\n",
        "- ---|---\n",
    ],
)
def test_list_item_with_risky_first_line_content_is_protected(text):
    assert [(s.kind, s.protection) for s in seg(text).segments] == [(K.LIST_ITEM, P.PROTECTED)]


@pytest.mark.parametrize(
    "text",
    [
        "- item\n",
        "-    item\n",  # four spaces: still list content
        "- \titem\n",
        "-\titem\n",
        "1. item\n",
        "- [ ] task\n",
        "- [ ]    task\n",
        "-     \n",  # empty item with trailing spaces
        "- item `code` and [link](u)\n",
    ],
)
def test_ordinary_list_items_stay_structural(text):
    assert [s.protection for s in seg(text).segments] == [P.STRUCTURAL]


def test_code_like_list_item_is_unchanged_by_all_rules():
    text = "-     code  with   spaces \t\n1.\t\tx  y \n- [ ]     a   b  \n\n\n\n"
    result = run_pipeline(parse_document(text.encode()), REGISTRY, input_path="t.md")
    body = "-     code  with   spaces \t\n1.\t\tx  y \n- [ ]     a   b  \n"
    assert result.document.text.startswith(body)
