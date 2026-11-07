import pytest

from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.report import Severity, SourceLocation
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.hyphenation import COMPOUND_PREFIXES, JoinHyphenatedWords
from md_ocr_cleaner.rules.selection import select_rules

RULE = JoinHyphenatedWords()
SHY = chr(0x00AD)
EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
OFF = "<!-- ocr-clean: off -->"
ON = "<!-- ocr-clean: on -->"


def run(text, rules=(RULE,)):
    return run_pipeline(parse_document(text.encode("utf-8")), rules, input_path="t.md")


def cleaned(text, rules=(RULE,)):
    result = run(text, rules)
    again = run(result.document.text, rules)
    assert again.report.changes == ()  # idempotent
    assert again.document.text == result.document.text
    return result.document.text


def ambiguous(text):
    return [
        (f.location, f.severity)
        for f in run(text).report.findings
        if f.code == "ambiguous-hyphenation"
    ]


def test_disabled_by_default():
    assert RULE.default_enabled is False
    assert RULE not in select_rules(REGISTRY)


# --- Joining ---------------------------------------------------------------------------------


def test_fragment_moves_up_and_rest_stays():
    assert cleaned("infor-\nmation is here\n") == "information\nis here\n"


def test_whole_line_fragment_merges_lines():
    assert cleaned("This is infor-\nmation\nmore\n") == "This is information\nmore\n"


def test_punctuation_moves_with_fragment():
    assert cleaned("infor-\nmation, which\n") == "information,\nwhich\n"


def test_accented_latin_split_is_joined():
    assert cleaned("Le résu-\nmé final.\n") == "Le résumé\nfinal.\n"


def test_trailing_spaces_after_hyphen_and_indented_continuation():
    assert cleaned("infor- \t\n   mation is\n") == "information\nis\n"


def test_soft_hyphen_is_joined():
    assert cleaned(f"hyphen{SHY}\nation rules\n") == "hyphenation\nrules\n"


def test_soft_hyphen_joins_even_after_o():
    assert cleaned(f"photo{SHY}\ngraph\n") == "photograph\n"


def test_soft_hyphen_in_middle_of_line_is_not_removed():
    text = f"hyphen{SHY}ation rules\n"
    assert cleaned(text) == text


def test_change_location_and_message():
    result = run("Text infor-\nmation.\n")
    (change,) = result.report.changes
    assert change.location == SourceLocation(1, 11, 2)
    assert change.message == "joined hyphenated word 'information.'"


def test_chained_splits():
    assert cleaned("infor-\nmation tech-\nnika\n") == "information\ntechnika\n"


# --- Ambiguous: kept with a finding ------------------------------------------------------------


def test_prefix_ending_in_o_is_kept_as_ambiguous():
    text = "the photo-\ngraph is\n"
    assert cleaned(text) == text
    assert ambiguous(text) == [(SourceLocation(1, 10), Severity.INFO)]


def test_compound_ending_in_o_is_kept():
    text = "socio-\neconomic study\n"
    assert cleaned(text) == text
    assert len(ambiguous(text)) == 1


@pytest.mark.parametrize("prefix", sorted(COMPOUND_PREFIXES))
def test_compound_prefixes_are_kept(prefix):
    text = f"a {prefix}-\nmade thing\n"
    assert cleaned(text) == text
    assert len(ambiguous(text)) == 1


def test_multi_hyphen_compound_is_kept():
    text = "state-of-the-\nart method\n"
    assert cleaned(text) == text
    assert len(ambiguous(text)) == 1


# --- Never joined, no finding ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "infor-\nMation\n",  # uppercase continuation
        "infor-\n2nd\n",  # digit
        "infor-\n(mation)\n",  # punctuation
        "infor-\nm\n",  # one letter
        "Infor-\nmation\n",  # uppercase prefix
        "a-\nmation\n",  # one-letter prefix
        "abc1de-\nfgh\n",  # digit in word
        "word -\nnext\n",  # dash after a space
        f"infor{EN_DASH}\nmation\n",
        f"infor{EM_DASH}\nmation\n",
        "infor-\n\nmation\n",  # blank line between
        "infor-\n- mation\n",  # list item follows
        "infor-\n# mation\n",  # heading follows
        "infor-\nmation-word\n",  # continuation contains a hyphen
    ],
)
def test_not_joined(text):
    result = run(text)
    assert result.document.text == text
    assert result.report.findings == ()


@pytest.mark.parametrize(
    "text",
    [
        "`infor-\nmation` code\n",
        "see https://x.io/infor-\nmation here\n",
        "[a](infor-\nmation)\n",
    ],
)
def test_masked_spans_are_not_joined(text):
    assert cleaned(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "- infor-\n  mation\n",
        "> infor-\n> mation\n",
        "# infor-\nmation\n",
        "```\ninfor-\nmation\n```\n",
        "| infor- |\n|---|\n",
        f"{OFF}\ninfor-\nmation\n{ON}\n",
        "[^1]: infor-\n    mation\n",
    ],
)
def test_structural_protected_and_escaped_are_untouched(text):
    assert cleaned(text) == text


@pytest.mark.parametrize(
    "rest",
    [
        "# Heading",
        "- item",
        "* item",
        "+ item",
        "1. item",
        "2) item",
        "- [ ] task",
        "> quote",
        "```code",
        "~~~",
        "---",
        "***",
        "___",
        "===",
        "<div>",
        "<!-- comment -->",
        "<!-- ocr-clean: off -->",
        "<!-- ocr-clean: on -->",
        "[ref]: /url",
        "[^1]: note",
        "$$",
        "$$ x $$",
    ],
)
def test_join_that_would_create_structure_is_refused(rest):
    text = f"Some prose with infor-\nmation {rest}\nmore prose\n"
    result = run(text)
    assert result.document.text == text
    assert result.report.changes == ()
    (finding,) = result.report.findings
    assert finding.code == "ambiguous-hyphenation"
    assert finding.severity is Severity.INFO
    assert finding.location == SourceLocation(1, 22)
    assert finding.message == (
        "kept line-end hyphen after 'infor': joining would create Markdown structure"
    )


@pytest.mark.parametrize(
    "rest",
    ["https://example.com", "<span>x</span> y", "`code` here", "<https://x.io> z", "\\* x"],
)
def test_join_that_would_start_a_line_with_masked_inline_content_is_refused(rest):
    text = f"infor-\nmation {rest}\n"
    result = run(text)
    assert result.document.text == text
    (finding,) = result.report.findings
    assert finding.code == "ambiguous-hyphenation"
    assert finding.location == SourceLocation(1, 6)
    assert "protected inline content" in finding.message


def test_join_that_would_create_a_table_is_refused():
    text = "a | infor-\nmation\n|---|\n"
    assert cleaned(text) == text
    assert ambiguous(text) == [(SourceLocation(1, 10), Severity.INFO)]


def test_join_that_would_create_a_setext_heading_on_merge_is_refused():
    text = "infor-\nmation\n---\n"
    # "---" after a paragraph already makes a setext heading: nothing to join.
    assert cleaned(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("infor-\nmation a # b\n", "information\na # b\n"),
        ("infor-\nmation a - b\n", "information\na - b\n"),
        ("infor-\nmation word > other\n", "information\nword > other\n"),
        ("infor-\nmation 2024 was\n", "information\n2024 was\n"),
        ("infor-\nmation see https://x.io\n", "information\nsee https://x.io\n"),
    ],
)
def test_ordinary_remaining_prose_still_joins(text, expected):
    assert cleaned(text) == expected


def test_refused_join_does_not_block_later_joins():
    text = "infor-\nmation # x\ntech-\nnika\n"
    result = run(text)
    assert result.document.text == "infor-\nmation # x\ntechnika\n"
    assert len(ambiguous(text)) == 1


def test_chained_joins_check_the_line_as_it_reads_after_earlier_joins():
    # After the first join, "tech-" starts its own line; the second join is
    # checked against that line, not the original one.
    assert cleaned("infor-\nmation tech-\nnology is\n") == "information\ntechnology\nis\n"


def test_one_finding_per_hyphen():
    findings = run("socio-\neconomic\n").report.findings
    assert len(findings) == 1


# --- Combined with other rules ---------------------------------------------------------------


def test_later_rules_report_original_lines_after_merge():
    rules = select_rules(REGISTRY, enable=["join-hyphenated-words"])
    text = "infor-\nmation\nwords  words \n"
    result = run(text, rules)
    assert result.document.text == "information\nwords words\n"
    by_rule = {c.rule_id: c.location for c in result.report.changes}
    assert by_rule["trailing-whitespace"] == SourceLocation(3, 13)
    assert by_rule["repeated-spaces"] == SourceLocation(3, 6)


def test_soft_hyphen_detection_finding_remains():
    rules = select_rules(REGISTRY, enable=["join-hyphenated-words"])
    result = run(f"hyphen{SHY}\nation\n", rules)
    assert result.document.text == "hyphenation\n"
    codes = [f.code for f in result.report.findings]
    assert codes == ["invisible-character"]


def test_very_long_word_before_hyphen_is_handled():
    # Regression: an unanchored regex search was quadratic on long letter runs.
    text = "x " + "a" * 200000 + "-\nbc\n"
    out = cleaned(text)
    assert out == "x " + "a" * 200000 + "bc\n"
