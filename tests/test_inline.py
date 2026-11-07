import itertools

import pytest

from md_ocr_cleaner.inline import InlineSpan, SpanKind, mask_inline, unmasked_ranges


def masked(text, base_offset=0):
    """(kind, masked text) pairs, checking the span invariants on the way."""
    spans = mask_inline(text, base_offset)
    previous_end = base_offset
    for span in spans:
        assert span.start >= previous_end  # sorted and non-overlapping
        assert span.end > span.start
        previous_end = span.end
    return [(s.kind, text[s.start - base_offset : s.end - base_offset]) for s in spans]


K = SpanKind


def test_plain_text_has_no_spans():
    assert masked("Café au lait in Łódź, a < b > c.") == []


def test_single_code_span():
    assert masked("use `x  y` here") == [(K.CODE, "`x  y`")]


def test_multiple_code_spans():
    assert masked("`a` and `b`") == [(K.CODE, "`a`"), (K.CODE, "`b`")]


def test_matching_multi_backtick_span():
    assert masked("``a ` b`` end") == [(K.CODE, "``a ` b``")]


def test_backtick_run_must_match_exactly():
    assert masked("``a` b``") == [(K.CODE, "``a` b``")]


def test_unmatched_backtick_masks_rest_of_line():
    assert masked("a `b c\nnext") == [(K.CODE, "`b c")]


def test_code_span_across_lines_in_segment():
    assert masked("start `multi\nline` end") == [(K.CODE, "`multi\nline`")]


def test_inline_link_destination():
    assert masked("see [text](https://x.io/a  b) ok") == [
        (K.LINK_DESTINATION, "(https://x.io/a  b)")
    ]


def test_image_destination():
    assert masked('![alt](img.png "Title")') == [(K.IMAGE_DESTINATION, '(img.png "Title")')]


def test_destination_with_balanced_parentheses():
    assert masked("[w](https://en.wikipedia.org/wiki/A_(b)) x") == [
        (K.LINK_DESTINATION, "(https://en.wikipedia.org/wiki/A_(b))")
    ]


def test_destination_title_with_parenthesis():
    assert masked('[a](u "ti)tle") x') == [(K.LINK_DESTINATION, '(u "ti)tle")')]


def test_angle_bracket_destination():
    assert masked("[a](<my url>) x") == [(K.LINK_DESTINATION, "(<my url>)")]


def test_unbalanced_destination_masks_rest_of_line():
    assert masked("[a](broken ( x\nnext") == [(K.LINK_DESTINATION, "(broken ( x")]


def test_brackets_without_destination_are_not_masked():
    assert masked("[not a link] and [ref][id]") == []


def test_autolink():
    assert masked("go <https://example.com/a?b=1> now") == [
        (K.AUTOLINK, "<https://example.com/a?b=1>")
    ]


def test_email_autolink():
    assert masked("mail <alex.smith@example.io>.") == [
        (K.EMAIL_AUTOLINK, "<alex.smith@example.io>")
    ]


def test_bare_url():
    assert masked("see https://x.io/a,b and more") == [(K.BARE_URL, "https://x.io/a,b")]


def test_bare_url_requires_word_boundary():
    assert masked("xhttps://nope") == []


@pytest.mark.parametrize(
    "tag",
    ["<span>", "</span>", "<br/>", "<a href=\"x y\" title='t'>", "<img src=a.png alt>"],
)
def test_inline_html_tags(tag):
    assert masked(f"a {tag} b") == [(K.HTML, tag)]


def test_inline_html_comment():
    assert masked("a <!-- note --> b") == [(K.HTML, "<!-- note -->")]


def test_unclosed_inline_comment_masks_to_end():
    assert masked("a <!-- open\nmore") == [(K.HTML, "<!-- open\nmore")]


@pytest.mark.parametrize("text", ["a < b and c > d", "x<3", "1 <= 2", "<>", "< span>"])
def test_comparisons_are_not_html(text):
    assert masked(text) == []


def test_footnote_reference():
    assert masked("text[^1] and [^remarqué].") == [
        (K.FOOTNOTE_REFERENCE, "[^1]"),
        (K.FOOTNOTE_REFERENCE, "[^remarqué]"),
    ]


def test_escaped_punctuation():
    assert masked(r"\*not\* \` \\ \a") == [
        (K.ESCAPE, r"\*"),
        (K.ESCAPE, r"\*"),
        (K.ESCAPE, r"\`"),
        (K.ESCAPE, r"\\"),
    ]


def test_escaped_backtick_does_not_open_code():
    assert masked(r"\`not code`") == [(K.ESCAPE, r"\`"), (K.CODE, "`")]


def test_adjacent_spans():
    assert masked("`a``b`<br>[^1]") == [
        (K.CODE, "`a``b`"),
        (K.HTML, "<br>"),
        (K.FOOTNOTE_REFERENCE, "[^1]"),
    ]


def test_html_inside_code_is_not_separate_span():
    assert masked("`<b>` <b>") == [(K.CODE, "`<b>`"), (K.HTML, "<b>")]


def test_directive_text_inside_code_is_code():
    assert masked("`<!-- ocr-clean: off -->`") == [(K.CODE, "`<!-- ocr-clean: off -->`")]


def test_base_offset_is_applied():
    text = "ab `c` d"
    spans = mask_inline(text, base_offset=100)
    assert spans == (InlineSpan(K.CODE, 103, 106),)
    assert masked(text, 100) == [(K.CODE, "`c`")]


def test_unicode_text_around_spans():
    text = "Read `naïve` and [link](https://exämple.org) in Łódź, Größe."
    assert masked(text) == [(K.CODE, "`naïve`"), (K.LINK_DESTINATION, "(https://exämple.org)")]


def test_masking_never_modifies_text():
    text = "a `b` [c](d) <e> \\* f"
    copy = str(text)
    mask_inline(text)
    assert text == copy


def test_unmasked_ranges_cover_the_rest():
    text = "ab `c` d [^1]"
    spans = mask_inline(text, 10)
    ranges = unmasked_ranges(text, spans, 10)
    assert ranges == [(10, 13), (16, 19)]
    pieces = sorted([(s.start, s.end) for s in spans] + ranges)
    assert pieces[0][0] == 10 and pieces[-1][1] == 10 + len(text)
    assert all(a[1] == b[0] for a, b in itertools.pairwise(pieces))


def test_unmasked_ranges_without_spans():
    assert unmasked_ranges("abc", ()) == [(0, 3)]
    assert unmasked_ranges("", ()) == []


def test_invalid_span_rejected():
    with pytest.raises(ValueError):
        InlineSpan(K.CODE, 5, 5)


@pytest.mark.parametrize(
    "text",
    [
        "<a " + "b " * 5000,
        "`" * 3000 + "x" + "``" * 2000,
        "](" * 5000,
        "[" * 5000 + "]" * 5000,
        "<" + "a" * 10000,
    ],
)
def test_pathological_input_is_fast(text):
    import time

    started = time.perf_counter()
    mask_inline(text)
    assert time.perf_counter() - started < 2.0
