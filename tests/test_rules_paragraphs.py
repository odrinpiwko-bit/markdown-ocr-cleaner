import builtins
import json
import os

import pytest

from md_ocr_cleaner import cli
from md_ocr_cleaner.cli import EXIT_FINDINGS, EXIT_INTERNAL, EXIT_OK, main
from md_ocr_cleaner.document import encode_document, parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.report import Severity, SourceLocation
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.paragraphs import (
    CHANGE_MESSAGE,
    MIN_RATIO,
    MIN_WIDTH,
    REASON_ALIGNED,
    REASON_COLON,
    REASON_DIALOGUE,
    REASON_FORM_FEED,
    REASON_HARD_BREAK,
    REASON_HYPHEN,
    REASON_INLINE,
    REASON_MARKER,
    REASON_UPPERCASE,
    ReconstructParagraphs,
)
from md_ocr_cleaner.rules.selection import select_rules
from md_ocr_cleaner.segmenter import segment_document

RULE = ReconstructParagraphs()
FF = chr(0x0C)
SHY = chr(0x00AD)
HYPHEN_2010 = chr(0x2010)
HYPHEN_2011 = chr(0x2011)
EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
LOW_QUOTE = chr(0x201E)
LEFT_QUOTE = chr(0x201C)
SECTION = chr(0x00A7)
BULLET = chr(0x2022)
OFF = "<!-- ocr-clean: off -->"
ON = "<!-- ocr-clean: on -->"

# Lines of 45-50 characters: comfortably wrapped prose.
L1 = "Lucy walked to the old café early that day, and"
L2 = "met her sister there. Together they visited the"
L3 = "town library, where they found interesting books"
LAST = "about the city."
PROSE = f"{L1}\n{L2}\n{L3}\n{LAST}\n"
JOINED = f"{L1} {L2} {L3} {LAST}\n"


def run(text, rules=(RULE,)):
    return run_pipeline(parse_document(text.encode("utf-8")), rules, input_path="t.md")


def cleaned(text, rules=(RULE,)):
    result = run(text, rules)
    again = run(result.document.text, rules)
    assert again.report.changes == ()
    assert again.document.text == result.document.text
    return result.document.text


def kept_reasons(text, rules=(RULE,)):
    return [
        f.message.removeprefix("kept paragraph unchanged: ")
        for f in run(text, rules).report.findings
        if f.code == "paragraph-kept"
    ]


def assert_kept(text, reason):
    result = run(text)
    assert result.document.text == text
    assert result.report.changes == ()
    assert kept_reasons(text) == [reason]
    (finding,) = [f for f in result.report.findings if f.code == "paragraph-kept"]
    assert finding.severity is Severity.INFO


def assert_silent(text):
    result = run(text)
    assert result.document.text == text
    assert result.report.findings == ()


def wrap(first, second):
    """A strong two-line candidate: first line long, then the continuation."""
    return f"{first}\n{second}\n"


LONG = "This is a longer line of text which keeps going on"  # 50 code points


def test_metadata_and_thresholds():
    assert RULE.default_enabled is False
    assert RULE not in select_rules(REGISTRY)
    assert (MIN_WIDTH, MIN_RATIO) == (40, 0.75)


# --- Positive reconstruction ------------------------------------------------------------------


def test_wrapped_prose_with_accented_letters():
    assert cleaned(PROSE) == JOINED


def test_two_line_candidate():
    assert cleaned(wrap(LONG, "and ends here.")) == f"{LONG} and ends here.\n"


def test_sentence_boundary_with_uppercase_continuation():
    text = wrap(
        "This is a longer line of text that ends with a period.", "Then another one follows."
    )
    assert (
        cleaned(text)
        == "This is a longer line of text that ends with a period. Then another one follows.\n"
    )


@pytest.mark.parametrize("end", ["!", "?", chr(0x2026)])
def test_other_terminal_punctuation_permits_capitals(end):
    text = wrap(f"This is a longer line of text that ends with a mark{end}", "Then more.")
    assert "\n" not in cleaned(text).rstrip("\n")


def test_leading_spaces_and_single_trailing_space():
    text = f"{L1} \n   {L2}\n{L3}\n{LAST}\n"
    assert cleaned(text) == JOINED


def test_final_newline_absent():
    assert cleaned(PROSE.rstrip("\n")) == JOINED.rstrip("\n")


def test_crlf_and_bom_round_trip():
    data = b"\xef\xbb\xbf" + PROSE.replace("\n", "\r\n").encode()
    doc = parse_document(data, "utf-8-sig")
    out = run_pipeline(doc, (RULE,), input_path="t.md").document
    assert encode_document(out) == b"\xef\xbb\xbf" + JOINED.replace("\n", "\r\n").encode()


def test_inline_code_and_link_away_from_boundary():
    text = wrap("Use `code  x` and [link](https://x.io/a  b) in the text", "and keep going.")
    assert (
        cleaned(text) == "Use `code  x` and [link](https://x.io/a  b) in the text and keep going.\n"
    )


def test_change_locations_for_each_join():
    result = run(PROSE)
    assert [(c.location, c.message) for c in result.report.changes] == [
        (SourceLocation(1, len(L1) + 1), CHANGE_MESSAGE),
        (SourceLocation(2, len(L2) + 1), CHANGE_MESSAGE),
        (SourceLocation(3, len(L3) + 1), CHANGE_MESSAGE),
    ]


# --- Shape failures: unchanged, no finding ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "A short line of text,\nwhich keeps going.\n",  # width below 40
        f"{L1}\nshort\n{L3}\n{LAST}\n",  # short internal line
        "Roses are red, violets are blue,\nthe sun shines over hills and valleys,\n"
        "the wind blows,\nnight is falling.\n",  # verse
        "Jane Smith\n12 High Street\nSpringfield 12345\nCountry\n",  # address
        "Smith, J.: History of the Town, 1998.\nBrown, P.: Libraries, 2001.\n",  # bibliography
        "Chapter One\nA longer text that continues after a short heading.\n",  # heading-like
    ],
)
def test_shape_failures_are_silent(text):
    assert_silent(text)


def test_anomalously_long_line_does_not_distort_median():
    text = f"{L1}\n{L2}\n{'x' * 200}\n{L3}\n{LAST}\n"
    assert cleaned(text).count("\n") == 1  # median stays ~45, all lines pass


def test_ratio_failure():
    short = "a" * 30  # 30 < 0.75 * 45
    assert_silent(f"{L1}\n{short}\n{L2}\n{LAST}\n")


def test_unusual_control_characters_are_silent():
    assert_silent(f"{L1}{chr(0x01)}\n{L2}\n{LAST}\n")


# --- Strong candidates kept with paragraph-kept ------------------------------------------------


@pytest.mark.parametrize("mark", ["-", SHY, HYPHEN_2010, HYPHEN_2011, EN_DASH, EM_DASH])
def test_line_end_hyphens_and_dashes(mark):
    assert_kept(
        wrap(f"This is a longer line of text that keeps going fur{mark}", "ther and ends."),
        REASON_HYPHEN,
    )


def test_unresolved_split_is_not_turned_into_spaced_hyphen():
    text = wrap("This is a longer line of text with a socio-", "economic term.")
    assert "socio- economic" not in run(text).document.text


def test_two_space_hard_break():
    assert_kept(wrap(f"{LONG}  ", "and ends here."), REASON_HARD_BREAK)


def test_trailing_backslash_hard_break():
    assert_kept(wrap(f"{LONG}\\", "and ends here."), REASON_HARD_BREAK)


def test_colon():
    assert_kept(
        wrap("This is a longer line of text that has a list:", "first thing and second."),
        REASON_COLON,
    )


@pytest.mark.parametrize(
    "start", ["(1) first", "a) first", "(a) first", "i. first", "iv) first", f"{BULLET} first"]
)
def test_enumeration_markers(start):
    assert_kept(wrap(LONG, start), REASON_MARKER)


@pytest.mark.parametrize(
    "start", [f"{SECTION} 5 para. 2", "Art. 12 of the treaty", "art. 12", "Art. 3", "art. 3(1)"]
)
def test_legal_citation_markers(start):
    assert_kept(wrap(f"{LONG},", start), REASON_MARKER)


@pytest.mark.parametrize(
    "start",
    [
        f"{EN_DASH} she said",
        f"{EM_DASH} she said",
        f"{LOW_QUOTE}Hello",
        f"{LEFT_QUOTE}Hello",
        '"Hello',
    ],
)
def test_dialogue_markers(start):
    assert_kept(wrap(LONG, start), REASON_DIALOGUE)


def test_uppercase_after_comma():
    assert_kept(wrap(f"{LONG},", "London and beyond."), REASON_UPPERCASE)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("This is a longer line of text with code `start", "end` and more."),
        ("This is a longer line of text with a link [text](https://x.io/", "path) and more."),
        ("This is a longer line of text with a break <br>", "and more."),
        ("This is a longer line of text that ends in `code`", "and more."),
    ],
)
def test_inline_markdown_touching_boundary(first, second):
    assert_kept(wrap(first, second), REASON_INLINE)


def test_form_feed():
    assert_kept(f"{L1}\n{L2}{FF}\n{L3}\n{LAST}\n", REASON_FORM_FEED)


def test_aligned_spaces_outside_masks():
    assert_kept(
        wrap("This is a longer line of text,   which keeps going", "and ends."), REASON_ALIGNED
    )


def test_spaces_inside_masks_are_not_aligned_columns():
    text = wrap("This is a longer line of text with code `a   b` inside", "and ends.")
    assert cleaned(text).count("\n") == 1


def test_internal_tab():
    assert_kept(
        wrap("This is a longer line of text,\twhich keeps going", "and ends."), REASON_ALIGNED
    )


def test_reason_order_is_deterministic():
    # Form feed is checked before aligned columns, which come before boundary
    # reasons; boundaries are checked in order, first failing reason wins.
    text = f"{L1}   x:\n{L2}{FF}\n{L3}\n{LAST}\n"
    assert kept_reasons(text) == [REASON_FORM_FEED]
    text = f"{L1}   x:\n{L2}\n{L3}\n{LAST}\n"
    assert kept_reasons(text) == [REASON_ALIGNED]
    text = f"{L1}-\n{L2}:\n{L3}\n{LAST}\n"
    assert kept_reasons(text) == [REASON_HYPHEN]


def test_one_finding_per_withheld_paragraph():
    text = f"{L1}:\n{L2}:\n{L3}:\n{LAST}\n"
    assert kept_reasons(text) == [REASON_COLON]


def test_finding_location_is_original_line():
    text = f"Intro.\n\n{L1}\n{L2}:\n{L3}\n{LAST}\n"
    (finding,) = run(text).report.findings
    assert finding.location == SourceLocation(4, 1)


def test_all_or_nothing():
    text = f"{L1}\n{L2}\n{L3}:\n{LAST}\n"  # only the last boundary is blocked
    result = run(text)
    assert result.document.text == text
    assert result.report.changes == ()


# --- Structural safety ---------------------------------------------------------------------------

STRUCTURAL = [
    f"# {L1}\n",
    f"{L1}\n{L2}\n===\n",
    f"- {L1}\n  {L2}\n",
    f"1. {L1}\n   {L2}\n",
    f"- [ ] {L1}\n  {L2}\n",
    f"> {L1}\n> {L2}\n",
    f"| {L1} |\n|---|\n| {L2} |\n",
    f"```\n{L1}\n{L2}\n```\n",
    f"    {L1}\n    {L2}\n",
    f"---\ntitle: {L1}\nx: {L2}\n---\n",
    f"<div>\n{L1}\n{L2}\n</div>\n",
    f"<!--\n{L1}\n{L2}\n-->\n",
    f"$$\n{L1}\n{L2}\n$$\n",
    f"[id]: https://x.io\n  '{L1}'\n",
    f"[^1]: {L1}\n    {L2}\n",
    "***\n",
    f"{OFF}\n{L1}\n{L2}\n{ON}\n",
    f"| `{L1} |\n|---|---|\n",
    f"<custom-el a=1\n{L1}\n{L2}\n",
]


@pytest.mark.parametrize("text", STRUCTURAL)
def test_structural_and_protected_untouched(text):
    result = run(text)
    assert result.document.text == text
    assert result.report.changes == ()


def test_protected_content_exact_in_mixed_document():
    text = "\n".join(STRUCTURAL) + "\n" + PROSE
    out = run(text).document.text
    for segment in segment_document(text).segments:
        if segment.is_protected:
            assert segment.text in out
    assert out.endswith(JOINED)


# --- Interaction ---------------------------------------------------------------------------------

BOTH = select_rules(REGISTRY, enable=["join-hyphenated-words", "reconstruct-paragraphs"])


def test_hyphen_joining_then_reconstruction():
    text = (
        "Together they visited the old town library, where infor-\n"
        "mation about the history of the city was kept in old\n"
        "books.\n"
    )
    out = cleaned(text, BOTH)
    assert out == (
        "Together they visited the old town library, where information "
        "about the history of the city was kept in old books.\n"
    )


def test_remaining_hyphen_blocks_with_hyphen_rule_enabled():
    text = wrap("This is a longer line of text with a socio-", "economic term.")
    result = run(text, BOTH)
    assert result.document.text == text
    assert "paragraph-kept" in [f.code for f in result.report.findings]


def test_remaining_hyphen_blocks_with_hyphen_rule_disabled():
    text = wrap("This is a longer line of text, with an infor-", "mation about it.")
    assert_kept(text, REASON_HYPHEN)


def test_no_reconstruction_across_removed_page_number():
    rules = select_rules(REGISTRY, enable=["remove-page-numbers", "reconstruct-paragraphs"])
    page = (
        "This is a longer line of text at the end of a page, and\n"
        "it continues on the next line.\n\n{n}\n\n"
        "and here the text continues on the next page with one\n"
        "more line of text.\n\n"
    )
    text = "".join(page.format(n=n) for n in (10, 11, 12))
    out = cleaned(text, rules)
    paragraphs = [p for p in out.split("\n\n") if p.strip()]
    assert len(paragraphs) == 6  # each paragraph joined on its own, never merged


def test_later_whitespace_changes_keep_original_lines():
    rules = select_rules(REGISTRY, enable=["reconstruct-paragraphs"])
    text = PROSE + "\nwords  words \n"
    result = run(text, rules)
    by_rule = {c.rule_id: c.location for c in result.report.changes if c.rule_id != RULE.id}
    assert by_rule["trailing-whitespace"] == SourceLocation(6, 13)
    assert by_rule["repeated-spaces"] == SourceLocation(6, 6)


def test_pipeline_performs_no_file_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("file I/O")

    doc = parse_document(PROSE.encode())
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(os, "replace", forbidden)
    all_rules = select_rules(REGISTRY, enable=[r.id for r in REGISTRY])
    assert run_pipeline(doc, all_rules, input_path="t.md").document.text == JOINED


# --- CLI -----------------------------------------------------------------------------------------


@pytest.fixture
def prose_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "p.md").write_bytes(PROSE.encode())
    return tmp_path / "p.md"


def test_default_run_does_not_reconstruct(prose_file, capsys):
    assert main(["p.md"]) == EXIT_OK
    assert (prose_file.parent / "p.cleaned.md").read_bytes() == PROSE.encode()
    assert "reconstruct-paragraphs" not in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--enable", "--only"])
def test_enable_and_only(prose_file, option):
    assert main(["p.md", option, "reconstruct-paragraphs"]) == EXIT_OK
    assert (prose_file.parent / "p.cleaned.md").read_bytes() == JOINED.encode()


def test_check_diff_and_json(prose_file, capsys):
    assert main(["p.md", "--check", "--enable", "reconstruct-paragraphs"]) == EXIT_FINDINGS
    capsys.readouterr()
    assert main(["p.md", "-n", "--diff", "--enable", "reconstruct-paragraphs"]) == EXIT_OK
    diff = capsys.readouterr().out
    assert diff.startswith("--- p.md\n+++ p.cleaned.md\n@@ -1,4 +1 @@\n")
    assert diff.endswith(f"+{JOINED}")
    argv = ["p.md", "-n", "--enable", "reconstruct-paragraphs", "--report-json", "-"]
    assert main(argv) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert [(c["rule_id"], c["message"], c["location"]["line"]) for c in data["changes"]] == [
        ("reconstruct-paragraphs", CHANGE_MESSAGE, 1),
        ("reconstruct-paragraphs", CHANGE_MESSAGE, 2),
        ("reconstruct-paragraphs", CHANGE_MESSAGE, 3),
    ]


def test_fail_on_warning_ignores_paragraph_kept(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "k.md").write_bytes(wrap(f"{LONG}:", "and more.").encode())
    argv = ["k.md", "-n", "--enable", "reconstruct-paragraphs", "--fail-on-warning"]
    assert main(argv) == EXIT_OK


def test_pipeline_failure_writes_nothing(prose_file, monkeypatch):
    from fakes import BoomRule

    monkeypatch.setattr(cli, "REGISTRY", (*cli.REGISTRY, BoomRule()))
    before = sorted(p.name for p in prose_file.parent.iterdir())
    argv = ["p.md", "--enable", "reconstruct-paragraphs,fake-boom", "--report-json", "r.json"]
    assert main(argv) == EXIT_INTERNAL
    assert sorted(p.name for p in prose_file.parent.iterdir()) == before


def test_full_destructive_run_is_idempotent(prose_file, capsys):
    rules = "remove-page-numbers,join-hyphenated-words,reconstruct-paragraphs"
    assert main(["p.md", "--enable", rules]) == EXIT_OK
    capsys.readouterr()
    assert main(["p.cleaned.md", "--check", "--enable", rules]) == EXIT_OK


def test_one_paren_marker_is_a_list_item_not_a_paragraph_line():
    # "1) ..." is CommonMark list syntax, so the paragraph ends before it.
    assert_silent(wrap(LONG, "1) first item"))


def test_long_bibliography_entries_are_kept():
    first = "Smith, J.: A History of the Town and Its Region. London 1998."
    assert_kept(wrap(first, "Brown, P.: Libraries."), REASON_MARKER)
