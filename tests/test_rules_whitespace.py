import pytest

from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.report import SourceLocation
from md_ocr_cleaner.rules.whitespace import RepeatedSpaces, TrailingWhitespace

TRAILING = TrailingWhitespace()
REPEATED = RepeatedSpaces()


def clean(text, rule):
    result = run_pipeline(parse_document(text.encode("utf-8")), (rule,), input_path="t.md")
    again = run_pipeline(result.document, (rule,), input_path="t.md")
    assert again.report.changes == ()  # idempotent
    assert again.document.text == result.document.text
    return result.document.text, result.report.changes


def cleaned(text, rule):
    return clean(text, rule)[0]


# --- trailing-whitespace -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("no trailing\n", "no trailing\n"),
        ("one space \n", "one space\n"),
        ("one tab\t\n", "one tab\n"),
        ("tabs\t\t\t\n", "tabs\n"),
        ("hard break  \nnext\n", "hard break  \nnext\n"),
        ("three spaces   \nnext\n", "three spaces   \nnext\n"),
        ("space then tab \t\n", "space then tab\n"),
        ("two spaces then tab  \t\n", "two spaces then tab\n"),  # not a hard break
        ("tab then spaces\t  \n", "tab then spaces  \n"),  # hard break kept
        ("tab then space\t \n", "tab then space\n"),
        ("mixed \t  \n", "mixed  \n"),
    ],
)
def test_trailing_policy(text, expected):
    assert cleaned(text, TRAILING) == expected


def test_final_line_without_newline():
    assert cleaned("a\nlast \t", TRAILING) == "a\nlast"


def test_final_newline_is_kept():
    assert cleaned("last \n", TRAILING) == "last\n"


def test_blank_lines_with_whitespace():
    assert cleaned("a\n \n\t\n  \n\nb\n", TRAILING) == "a\n\n\n  \n\nb\n"


def test_leading_and_internal_whitespace_untouched():
    assert cleaned("  lead  in\tside \n", TRAILING) == "  lead  in\tside\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Heading \t\n", "# Heading\n"),
        ("Title \n===\t\n", "Title\n===\n"),
        ("- item \n- [ ] task\t\n", "- item\n- [ ] task\n"),
        ("> quote \n>\t\n", "> quote\n>\n"),
    ],
)
def test_structural_segments(text, expected):
    assert cleaned(text, TRAILING) == expected


def test_accented_latin_prose():
    assert cleaned("Café au lait in Łódź. \t\nThen Größe \n", TRAILING) == (
        "Café au lait in Łódź.\nThen Größe\n"
    )


def test_unicode_spaces_are_not_removed():
    text = "nbsp\u00a0\nideographic\u3000\nthin\u2009 \n"
    assert cleaned(text, TRAILING) == "nbsp\u00a0\nideographic\u3000\nthin\u2009\n"


def test_trailing_space_inside_multiline_code_span_is_kept():
    text = "a `code \nmore` b \n"
    assert cleaned(text, TRAILING) == "a `code \nmore` b\n"


@pytest.mark.parametrize(
    "text",
    [
        "---\ntitle: x \t\n---\n",
        "```\ncode \t\n```\n",
        "    code \t\n",
        "<div>\nx \t\n</div>\n",
        "<!--\nx \t\n-->\n",
        "| a | b | \n|---|---|\t\n",
        "[id]: https://x.io \t\n",
        "[^1]: note \t\n",
        "$$\nx \t\n$$\n",
        "***\t\n",
        "<!-- ocr-clean: off -->\nx \t\n<!-- ocr-clean: on -->\n",
        "- item\n\n  ```\n  x \t\n  ```\n",
    ],
)
def test_protected_segments_untouched(text):
    assert clean(text, TRAILING) == (text, ())


def test_change_locations_and_granularity():
    _, changes = clean("a \nb\nc\t\t\n", TRAILING)
    assert [(c.location, c.before, c.after) for c in changes] == [
        (SourceLocation(1, 2), " ", ""),
        (SourceLocation(3, 2), "\t\t", ""),
    ]


# --- repeated-spaces -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("two  spaces\n", "two spaces\n"),
        ("several      spaces\n", "several spaces\n"),
        ("a  b   c    d\n", "a b c d\n"),
        ("Café  au   lait  Łódź.\n", "Café au lait Łódź.\n"),
        ("   leading  kept\n", "   leading kept\n"),
        ("trailing kept  \nnext\n", "trailing kept  \nnext\n"),
        ("tab\t\tkept and \t mixed\n", "tab\t\tkept and \t mixed\n"),
        ("nbsp\u00a0\u00a0kept a\u00a0 b\n", "nbsp\u00a0\u00a0kept a\u00a0 b\n"),
        ("line one  x\nline  two\n", "line one x\nline two\n"),
    ],
)
def test_repeated_policy(text, expected):
    assert cleaned(text, REPEATED) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("use `a  b` here\n", "use `a  b` here\n"),
        ("`a  b`  and  `c  d`\n", "`a  b` and `c  d`\n"),
        ("[text  x](https://x.io/a  b)\n", "[text x](https://x.io/a  b)\n"),
        ("![alt](img.png  'title')\n", "![alt](img.png  'title')\n"),
        ("<https://x.io/a>  next\n", "<https://x.io/a> next\n"),
        ("<alex@x.io>  mail\n", "<alex@x.io> mail\n"),
        ("see https://x.io/a  b\n", "see https://x.io/a b\n"),
        ('<span  class="x">  t\n', '<span  class="x"> t\n'),
        ("note[^1]  more\n", "note[^1] more\n"),
        ("\\*  star\n", "\\* star\n"),
        ("`a`<b>`c`  d\n", "`a`<b>`c` d\n"),
        ("x  `y`  z\n", "x `y` z\n"),
        ("open `code  rest\n", "open `code  rest\n"),
    ],
)
def test_inline_masks(text, expected):
    assert cleaned(text, REPEATED) == expected


@pytest.mark.parametrize(
    "text",
    [
        "#  Heading  with  spaces\n",
        "-  item  text\n",
        "1.  item  text\n",
        ">  quote  text\n",
        "Title  here\n===\n",
        "```\na  b\n```\n",
        "| a  b |\n|---|\n",
        "<!-- ocr-clean: off -->\na  b\n<!-- ocr-clean: on -->\n",
        "    code  x\n",
    ],
)
def test_structural_and_protected_untouched(text):
    assert clean(text, REPEATED) == (text, ())


def test_repeated_change_locations_non_overlapping():
    _, changes = clean("a  b   c\n", REPEATED)
    assert [(c.location, c.before) for c in changes] == [
        (SourceLocation(1, 2), "  "),
        (SourceLocation(1, 5), "   "),
    ]


# Milestone 10 audit regressions --------------------------------------------------------


def test_whitespace_after_lone_cr_is_kept():
    # Removing it would turn the lone CR into a CRLF line ending.
    text = "a\r \nb\n"
    assert clean(text, TRAILING) == (text, ())


def test_whitespace_only_last_line_without_newline_is_kept():
    # Emptying it would give the document a final newline it did not have.
    for text in ("text\n\n\t", "text\n\n ", "text\n \t"):
        assert clean(text, TRAILING) == (text, ())


def test_trailing_whitespace_on_last_line_with_content_still_removed():
    assert cleaned("a\nlast \t", TRAILING) == "a\nlast"
    assert cleaned("\t", TRAILING) == "\t"  # whitespace-only document: blank line kept


def test_long_space_run_followed_by_text_is_linear_and_unchanged():
    text = "a" + " " * 200000 + "b\n"
    assert clean(text, TRAILING) == (text, ())
