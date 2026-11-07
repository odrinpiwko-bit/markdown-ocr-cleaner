import pytest

from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.report import Severity, SourceLocation
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.selection import select_rules
from md_ocr_cleaner.rules.suspicious import _UNIT_WORDS, BIDI_CONTROLS, SuspiciousCharacters

RULE = SuspiciousCharacters()

# Special characters are built with chr() so the source stays plain and visible.
FFFD = chr(0xFFFD)
FF = chr(0x0C)
NUL = chr(0x00)
ESC = chr(0x1B)
C1 = chr(0x85)
LONE_CR = chr(0x0D)
RLO = chr(0x202E)
LRI = chr(0x2066)
ZWSP = chr(0x200B)
WJ = chr(0x2060)
ZWNBSP = chr(0xFEFF)
SHY = chr(0x00AD)
NBSP = chr(0x00A0)
CYR_A = chr(0x0430)  # Cyrillic small a
GREEK_O = chr(0x03BF)  # Greek small omicron
COMBINING_ACUTE = chr(0x0301)
OFF = "<!-- ocr-clean: off -->"
ON = "<!-- ocr-clean: on -->"

ACCENTED_LATIN = (
    "a á à â ä ã å æ ç č ď é è ê ë ě í ì î ï ĺ ľ ł "
    "ñ ň ó ò ô ö õ ø ő œ ŕ ř š ß ť ú ù û ü ů ű ý ÿ ž ź ż"
)

DEFAULT_RULES = select_rules(REGISTRY)  # the rules a plain run uses


def run(text, rules=(RULE,)):
    return run_pipeline(parse_document(text.encode("utf-8")), rules, input_path="t.md")


def found(text, rules=(RULE,)):
    return [(f.code, f.location) for f in run(text, rules).report.findings if f.rule_id == RULE.id]


def codes(text):
    return [code for code, _ in found(text)]


# --- Definite checks ------------------------------------------------------------------------


def test_replacement_character():
    result = run(f"Text{FFFD}ok\n")
    (finding,) = result.report.findings
    assert (finding.code, finding.severity, finding.location) == (
        "replacement-character",
        Severity.WARNING,
        SourceLocation(1, 5),
    )
    assert finding.excerpt == f"Text{FFFD}ok"


@pytest.mark.parametrize("ch", [NUL, ESC, C1, LONE_CR])
def test_control_characters(ch):
    assert found(f"ab{ch}c\n") == [("control-character", SourceLocation(1, 3))]


def test_tab_and_newline_are_not_controls():
    assert found("a\tb\n\tc\n") == []


def test_form_feed_is_info_and_not_control():
    result = run(f"a{FF}b\n")
    assert [(f.code, f.severity) for f in result.report.findings] == [("form-feed", Severity.INFO)]


def test_bidi_coverage_is_explicit():
    expected = {chr(c) for c in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E)}
    expected |= {chr(c) for c in (0x2066, 0x2067, 0x2068, 0x2069)}
    assert expected == BIDI_CONTROLS
    for ch in sorted(BIDI_CONTROLS):
        assert codes(f"x{ch}y\n") == ["bidi-control"]
    for ch in (chr(0x200E), chr(0x200F), chr(0x061C)):  # LRM, RLM, ALM: not reported
        assert codes(f"x{ch}y\n") == []


def test_one_finding_per_line_per_code_at_first_occurrence():
    result = run(f"a{FFFD}b{FFFD}c{NUL}\nsecond {FFFD}\n")
    assert [(f.code, f.location) for f in result.report.findings] == [
        ("replacement-character", SourceLocation(1, 2)),
        ("control-character", SourceLocation(1, 6)),
        ("replacement-character", SourceLocation(2, 8)),
    ]


@pytest.mark.parametrize(
    "text",
    [
        f"```\nprint('{RLO}')\n```\n",
        f"| a | b{FFFD} |\n|---|---|\n",
        f"---\ntitle: x{NUL}\n---\n",
        f"    code{ESC}\n",
        f"<div>{FFFD}</div>\n",
        f"[id]: https://x.io/{RLO}\n",
    ],
)
def test_definite_checks_inspect_protected_content(text):
    assert len(found(text)) == 1


def test_definite_checks_inspect_masked_inline_spans():
    assert codes(f"see `code{RLO}` and [a](https://x.io/{FFFD})\n") == [
        "bidi-control",
        "replacement-character",
    ]


def test_excerpt_is_bounded():
    text = "x" * 100 + FFFD + "y" * 100 + "\n"
    (finding,) = run(text).report.findings
    assert finding.excerpt == "x" * 20 + FFFD + "y" * 20


# --- Heuristic checks ------------------------------------------------------------------------


@pytest.mark.parametrize("ch", [ZWSP, WJ, ZWNBSP, SHY])
def test_invisible_characters(ch):
    result = run(f"slo{ch}vo\n")
    assert [(f.code, f.severity, f.location) for f in result.report.findings] == [
        ("invisible-character", Severity.WARNING, SourceLocation(1, 4))
    ]


def test_decoded_bom_is_not_a_finding():
    doc = parse_document(b"\xef\xbb\xbfText\n", "utf-8")
    assert run_pipeline(doc, (RULE,), input_path="t.md").report.findings == ()


@pytest.mark.parametrize(
    "text",
    [
        f"```\nslo{ZWSP}vo\n```\n",
        f"| slo{SHY}vo |\n|---|\n",
        f"use `slo{ZWSP}vo` here\n",
        f"[a](https://x.io/{WJ})\n",
    ],
)
def test_invisible_not_reported_in_protected_or_masked(text):
    assert codes(text) == []


def test_mixed_script_word():
    result = run(f"This w{CYR_A}rd and w{GREEK_O}rd.\n")
    assert [(f.code, f.severity, f.location) for f in result.report.findings] == [
        ("mixed-script-word", Severity.WARNING, SourceLocation(1, 6))
    ]


def test_pure_cyrillic_or_greek_words_are_fine():
    assert codes("Москва and Αθήνα are cities.\n") == []


def test_accented_latin_letters_are_clean():
    text = f"{ACCENTED_LATIN}\n{ACCENTED_LATIN.upper()}\nCafé au lait in Łódź, Größe.\n"
    assert codes(text) == []


def test_nbsp_typography_is_not_reported():
    assert codes(f"See page{NBSP}5 in vol.{NBSP}2, by Mr.{NBSP}Smith.\n") == []


def test_combining_marks_are_part_of_the_word():
    assert codes(f"re{COMBINING_ACUTE}sume{COMBINING_ACUTE} and cafe{COMBINING_ACUTE}\n") == []


def test_mixed_script_with_combining_mark():
    assert codes(f"sl{CYR_A}{COMBINING_ACUTE}vo\n") == ["mixed-script-word"]


def test_unclassifiable_letters_give_no_finding():
    assert codes(f"x{chr(0x4E2D)}{CYR_A}\n") == []  # CJK letter: ambiguous, no finding


def test_mixed_script_skipped_in_code_and_protected():
    assert codes(f"`w{CYR_A}rd`\n\n```\nw{CYR_A}rd\n```\n") == []


@pytest.mark.parametrize(
    ("text", "column"),
    [("I l0ve it\n", 4), ("the fi1e is\n", 7), ("in 0rder to\n", 4), ("1ike this\n", 1)],
)
def test_digit_letter_confusion(text, column):
    result = run(text)
    assert [(f.code, f.severity, f.location) for f in result.report.findings] == [
        ("digit-letter-confusion", Severity.INFO, SourceLocation(1, column))
    ]


@pytest.mark.parametrize(
    "word",
    [
        "COVID-19",
        "A4",
        "H2O",
        "1st",
        "2nd",
        "10km",
        "1km",
        "1min",
        "v1.2.3",
        "3.14",
        "0x1f",
        "deadbeef01",
        "src/l0g",
        "my_v0ar",
        "user@l0ve.io",
        "mp3",
        "B2B",
        "12",
        "1990",
    ],
)
def test_digit_letter_no_false_positives(word):
    assert codes(f"Word {word} here.\n") == []


def test_unit_exclusions_are_english_units_only():
    assert (
        frozenset(
            {"min", "sec", "mph", "rpm", "ppm", "mol", "kcal", "bit", "bits", "byte", "bytes"}
        )
        == _UNIT_WORDS
    )


@pytest.mark.parametrize("unit", sorted(_UNIT_WORDS))
def test_digit_before_unit_is_not_confusion(unit):
    assert codes(f"It took 1{unit} here.\n") == []


@pytest.mark.parametrize("word", ["1ike", "0rder", "1ate", "0pen"])
def test_digit_before_other_words_is_still_confusion(word):
    assert codes(f"Word {word} here.\n") == ["digit-letter-confusion"]


def test_digit_letter_skipped_in_code_and_urls():
    assert codes("`l0ve` and https://x.io/l0ve and <a href=l0ve>\n") == []


def test_structural_segments_are_inspected_heuristically():
    assert codes(f"# Heading w{CYR_A}rd\n") == ["mixed-script-word"]


# --- Escaped regions -----------------------------------------------------------------------


def test_nothing_reported_inside_escaped_region():
    text = f"{OFF}\n{FFFD}{RLO}{NUL}{FF}w{CYR_A}rd l0ve {ZWSP}\n{ON}\n"
    assert found(text) == []


def test_unmatched_off_suppresses_to_eof():
    assert found(f"{OFF}\n{FFFD}\n") == []


def test_directive_lines_themselves_produce_nothing():
    assert found(f"{OFF}\n{ON}\n{ON}\n") == []


# --- Pipeline behavior -------------------------------------------------------------------------


def test_no_edits_and_text_unchanged():
    text = f"a{FFFD}  b \n\n\n\n\nw{CYR_A}rd\n"
    result = run(text)
    assert result.document.text == text
    assert result.report.changes == ()
    assert RULE.apply  # rule proposes no edits (pipeline would reject them otherwise)


def test_locations_are_original_with_full_pipeline():
    text = "a\n\n\n\n\nb  c " + FFFD + "\n"
    result = run(text, DEFAULT_RULES)
    suspicious = [f for f in result.report.findings if f.rule_id == RULE.id]
    assert [f.location for f in suspicious] == [SourceLocation(6, 6)]
