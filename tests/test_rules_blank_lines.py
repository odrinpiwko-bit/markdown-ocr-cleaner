import pytest

from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.report import SourceLocation
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.blank_lines import COLLAPSE_THRESHOLD, ExcessiveBlankLines
from md_ocr_cleaner.rules.selection import select_rules

RULE = ExcessiveBlankLines()

DEFAULT_RULES = select_rules(REGISTRY)  # the rules a plain run uses


def clean(text, rules=(RULE,)):
    result = run_pipeline(parse_document(text.encode("utf-8")), rules, input_path="t.md")
    again = run_pipeline(result.document, rules, input_path="t.md")
    assert again.report.changes == ()
    assert again.document.text == result.document.text
    return result.document.text, result.report.changes


def cleaned(text):
    return clean(text)[0]


def test_threshold_is_three():
    assert COLLAPSE_THRESHOLD == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a\nb\n", "a\nb\n"),  # zero blank lines
        ("a\n\nb\n", "a\n\nb\n"),  # one
        ("a\n\n\nb\n", "a\n\n\nb\n"),  # two: kept
        ("a\n\n\n\nb\n", "a\n\nb\n"),  # three: collapsed to one
        ("a" + "\n" * 20 + "b\n", "a\n\nb\n"),  # many
        ("\n\n\n\na\n", "\na\n"),  # leading
        ("\n\na\n", "\n\na\n"),  # leading, two kept
        ("a\n\n\n\n\n", "a\n\n"),  # trailing, final newline kept
        ("a\n\n\n\nb", "a\n\nb"),  # no final newline
        ("", ""),
        ("\n\n\n", "\n"),
        ("\n\n", "\n\n"),
    ],
)
def test_blank_line_policy(text, expected):
    assert cleaned(text) == expected


def test_whitespace_only_lines_interrupt_runs():
    text = "a\n\n\n \n\n\nb\n"
    assert cleaned(text) == text


def test_whitespace_lines_count_after_trailing_whitespace_rule():
    text = "a\n\n\t\n\n\nb\n"
    assert clean(text, DEFAULT_RULES)[0] == "a\n\nb\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# H\n\n\n\n\nText\n", "# H\n\nText\n"),
        ("- a\n\n\n\n\n- b\n", "- a\n\n- b\n"),
        ("> q\n\n\n\n\n> r\n", "> q\n\n> r\n"),
        ("```\nx\n```\n\n\n\n\nafter\n", "```\nx\n```\n\nafter\n"),
        ("para\n\n\n\n    code\n", "para\n\n    code\n"),
    ],
)
def test_around_structural_and_protected_blocks(text, expected):
    assert cleaned(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "```\na\n\n\n\nb\n```\n",
        "    a\n\n\n\n    b\n",
        "---\na: 1\n\n\n\nb: 2\n---\n",
        "<div>\n<pre>\n\n\n\n</pre>\n",
        "<!--\n\n\n\n-->\n",
        "$$\n\n\n\n$$\n",
        "[^1]: a\n\n\n\n    b\n",
        "<!-- ocr-clean: off -->\n\n\n\n\n<!-- ocr-clean: on -->\n",
        "- item\n\n\n\n  continued\n",
    ],
)
def test_blank_lines_inside_protected_or_container_blocks_untouched(text):
    assert clean(text) == (text, ())


def test_change_line_ranges_and_later_mapping():
    text = "a\n\n\n\nb\n\n\n\n\nc  d\n"
    out, changes = clean(text, DEFAULT_RULES)
    assert out == "a\n\nb\n\nc d\n"
    by_rule = [(c.rule_id, c.location) for c in changes]
    assert by_rule == [
        ("repeated-spaces", SourceLocation(10, 2)),
        ("excessive-blank-lines", SourceLocation(2, 1, 4)),
        ("excessive-blank-lines", SourceLocation(6, 1, 9)),
    ]
    assert changes[1].before == "\n\n\n" and changes[1].after == "\n"


def test_location_after_earlier_collapse_uses_original_lines():
    text = "a\n\n\n\n\nb  c\n"
    rules = (
        RULE,
        next(r for r in DEFAULT_RULES if r.id == "repeated-spaces"),
    )  # collapse first, then repeated spaces
    _, changes = clean(text, rules)
    assert changes[1].location == SourceLocation(6, 2)
