"""Central pipeline enforcement against misbehaving rules.

The rules here are deliberately malicious: they propose edits that each
production rule's own checks would never produce. The pipeline must reject
them regardless of what the rule claims.
"""

import pytest

from md_ocr_cleaner import cli
from md_ocr_cleaner.cli import EXIT_INTERNAL, main
from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.inline import SpanKind, mask_inline
from md_ocr_cleaner.pipeline import PipelineError, run_pipeline
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.base import Rule, RuleResult, Safety, Structure, TextEdit
from md_ocr_cleaner.segmenter import MODIFIABLE_KINDS, SegmentKind

PROSE = "Prose line one\nprose two\n"


def malicious(propose, structure=Structure.PRESERVED, kinds=MODIFIABLE_KINDS):
    cls = type(
        "MaliciousRule",
        (Rule,),
        {
            "id": "fake-malicious",
            "description": "Proposes adversarial edits.",
            "safety": Safety.DESTRUCTIVE,
            "default_enabled": False,
            "edit_kinds": kinds,
            "structure": structure,
            "apply": lambda self, ctx: RuleResult(edits=tuple(propose(ctx))),
        },
    )
    return cls()


def run(text, *rules):
    return run_pipeline(parse_document(text.encode("utf-8")), rules, input_path="t.md")


def replace(text, old, new):
    start = text.index(old)
    return lambda ctx: [TextEdit(start, start + len(old), old, new, "malicious")]


# --- Structure ----------------------------------------------------------------------------

STRUCTURE_ATTACKS = {
    "heading": ("Prose", "# Prose"),
    "heading-mid-paragraph": ("prose two", "# prose two"),
    "list": ("Prose", "- Prose"),
    "list-mid-paragraph": ("prose two", "1. prose two"),
    "quote": ("prose two", "> prose two"),
    "fence": ("Prose line one", "```"),
    "fence-mid-paragraph": ("prose two", "~~~"),
    "reference": ("Prose line one", "[ref]: /url"),
    "footnote": ("prose two", "[^1]: note"),
    "escape-directive": ("Prose line one", "<!-- ocr-clean: off -->"),
    "escape-directive-mid-paragraph": ("prose two", "<!-- ocr-clean: off -->"),
    "setext": ("prose two", "==="),
    "thematic-break": ("prose two", "***"),
    "blank-split": ("prose two", "\nprose two"),
}


@pytest.mark.parametrize("structure", list(Structure))
@pytest.mark.parametrize("attack", sorted(STRUCTURE_ATTACKS))
def test_structure_creation_is_rejected_centrally(attack, structure):
    old, new = STRUCTURE_ATTACKS[attack]
    rule = malicious(replace(PROSE, old, new), structure)
    with pytest.raises(PipelineError, match="changed the Markdown structure") as exc:
        run(PROSE, rule)
    assert exc.value.rule_id == "fake-malicious"


def test_preserved_allows_text_changes_within_segments():
    rule = malicious(replace(PROSE, "line", "LINE"))
    assert run(PROSE, rule).document.text == "Prose LINE one\nprose two\n"


def test_preserved_rejects_removing_a_blank_line():
    text = "a\n\n\n\nb\n"
    rule = malicious(lambda ctx: [TextEdit(2, 5, "\n\n\n", "", "drop")])  # merges a and b
    with pytest.raises(PipelineError, match="disappeared"):
        run(text, rule)


def test_blank_lines_policy_allows_collapsing_blanks():
    text = "a\n\n\n\nb\n"
    start = text.index("\n\n\n\n") + 1
    rule = malicious(
        lambda ctx: [TextEdit(start, start + 3, "\n\n\n", "\n", "collapse")],
        Structure.BLANK_LINES,
    )
    assert run(text, rule).document.text == "a\n\nb\n"


def test_blank_lines_policy_rejects_merging_paragraphs():
    text = "a\n\nb\n"
    rule = malicious(lambda ctx: [TextEdit(2, 3, "\n", "", "drop")], Structure.BLANK_LINES)
    with pytest.raises(PipelineError, match="disappeared"):
        run(text, rule)


def test_no_new_blocks_allows_joining_paragraph_lines():
    rule = malicious(replace(PROSE, "\nprose", " prose"), Structure.NO_NEW_BLOCKS)
    assert run(PROSE, rule).document.text == "Prose line one prose two\n"


def test_no_new_blocks_rejects_turning_a_heading_into_prose():
    text = "# Title\n"
    rule = malicious(replace(text, "# ", ""), Structure.NO_NEW_BLOCKS)
    with pytest.raises(PipelineError, match="disappeared"):
        run(text, rule)


def test_remove_paragraphs_allows_removing_a_paragraph_and_blank():
    text = "a\n\n12\n\nb\n"
    start = text.index("12")

    def propose(ctx):
        return [
            TextEdit(start, start + 3, "12\n", "", "page"),
            TextEdit(start + 3, start + 4, "\n", "", "blank"),
        ]

    rule = malicious(propose, Structure.REMOVE_PARAGRAPHS)
    assert run(text, rule).document.text == "a\n\nb\n"


def test_remove_paragraphs_rejects_merging_paragraphs():
    text = "a\n\nb\n"
    rule = malicious(lambda ctx: [TextEdit(2, 3, "\n", "", "drop")], Structure.REMOVE_PARAGRAPHS)
    with pytest.raises(PipelineError, match="paragraphs were merged or split"):
        run(text, rule)


def test_remove_paragraphs_rejects_changing_other_segments():
    text = "a\n\n- item\n"
    rule = malicious(replace(text, "- item", "item"), Structure.REMOVE_PARAGRAPHS)
    with pytest.raises(PipelineError, match="list-item segment was changed or removed"):
        run(text, rule)


def test_production_rules_declare_their_structure_policy():
    policies = {rule.id: rule.structure for rule in REGISTRY}
    assert policies == {
        "suspicious-characters": Structure.PRESERVED,
        "detect-page-numbers": Structure.PRESERVED,
        "remove-page-numbers": Structure.REMOVE_PARAGRAPHS,
        "join-hyphenated-words": Structure.NO_NEW_BLOCKS,
        "reconstruct-paragraphs": Structure.NO_NEW_BLOCKS,
        "trailing-whitespace": Structure.PRESERVED,
        "repeated-spaces": Structure.PRESERVED,
        "excessive-blank-lines": Structure.BLANK_LINES,
    }


def test_detect_only_rule_cannot_declare_a_structure_change():
    with pytest.raises(ValueError, match="detect-only rules must preserve structure"):
        type(
            "BadDetect",
            (Rule,),
            {
                "id": "fake-bad-detect",
                "description": "x",
                "safety": Safety.DETECT_ONLY,
                "default_enabled": False,
                "edit_kinds": frozenset(),
                "structure": Structure.NO_NEW_BLOCKS,
                "apply": lambda self, ctx: RuleResult(),
            },
        )


def test_structure_must_be_a_structure_value():
    with pytest.raises(TypeError, match="structure must be a Structure value"):
        malicious(lambda ctx: [], structure="preserved")


def test_structure_violation_writes_no_output_or_report(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "in.md").write_bytes(PROSE.encode())
    rule = malicious(replace(PROSE, "Prose", "# Prose"))
    monkeypatch.setattr(cli, "REGISTRY", (*cli.REGISTRY, rule))
    before = sorted(p.name for p in tmp_path.iterdir())
    argv = ["in.md", "--enable", "fake-malicious", "--report-json", "r.json"]
    assert main(argv) == EXIT_INTERNAL
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert "changed the Markdown structure" in capsys.readouterr().err


# --- Inline masks -------------------------------------------------------------------------

MASK_SAMPLES = {
    SpanKind.CODE: "see `x  y` here",
    SpanKind.LINK_DESTINATION: "see [a](url) here",
    SpanKind.IMAGE_DESTINATION: "see ![a](img.png) here",
    SpanKind.AUTOLINK: "see <https://x.io> here",
    SpanKind.EMAIL_AUTOLINK: "see <a@b.io> here",
    SpanKind.BARE_URL: "see https://x.io/a here",
    SpanKind.HTML: "see <span>x</span> here",
    SpanKind.FOOTNOTE_REFERENCE: "see [^1] here",
    SpanKind.ESCAPE: "see \\* here",
}


def span_of(text, kind):
    (span,) = [s for s in mask_inline(text) if s.kind is kind][:1]
    return span


@pytest.mark.parametrize("kind", list(MASK_SAMPLES))
def test_edit_inside_masked_span_is_rejected(kind):
    text = MASK_SAMPLES[kind] + "\n"
    span = span_of(text, kind)
    old = text[span.start + 1]
    rule = malicious(
        lambda ctx: [TextEdit(span.start + 1, span.start + 2, old, "Z", "inside")],
        kinds=frozenset({SegmentKind.PARAGRAPH}),
    )
    with pytest.raises(PipelineError, match=f"overlaps a masked {kind.value} span"):
        run(text, rule)


@pytest.mark.parametrize("kind", list(MASK_SAMPLES))
def test_edit_covering_masked_span_is_rejected(kind):
    text = MASK_SAMPLES[kind] + "\n"
    span = span_of(text, kind)
    start, end = span.start - 1, span.end + 1
    rule = malicious(lambda ctx: [TextEdit(start, end, text[start:end], "", "covering")])
    with pytest.raises(PipelineError, match="overlaps a masked"):
        run(text, rule)


@pytest.mark.parametrize("kind", list(MASK_SAMPLES))
def test_edits_adjacent_to_masked_span_are_allowed(kind):
    text = MASK_SAMPLES[kind] + "\n"
    span = span_of(text, kind)
    before, after = text[span.start - 1], text[span.end]

    def propose(ctx):
        return [
            TextEdit(span.start - 1, span.start, before, "_", "before"),
            TextEdit(span.end, span.end + 1, after, "_", "after"),
        ]

    result = run(text, malicious(propose))
    expected = text[: span.start - 1] + "_" + text[span.start : span.end] + "_"
    assert result.document.text == expected + text[span.end + 1 :]


def test_masks_are_enforced_in_structural_segments():
    text = "- item `a  b`\n"
    span = span_of(text, SpanKind.CODE)
    rule = malicious(lambda ctx: [TextEdit(span.start + 2, span.start + 4, "  ", " ", "x")])
    with pytest.raises(PipelineError, match="overlaps a masked code span"):
        run(text, rule)


def test_mask_spanning_lines_is_enforced():
    text = "a `b\nc` d\n"
    newline = text.index("\n")
    rule = malicious(lambda ctx: [TextEdit(newline, newline + 1, "\n", " ", "join")])
    with pytest.raises(PipelineError, match="overlaps a masked code span"):
        run(text, rule)


def test_all_production_rules_pass_central_checks_on_rich_input():
    text = (
        "# Title  \n\n"
        "Prose with `code  span`, [link](a  b), ![img](i.png), <https://x.io>,\n"
        "<a@b.io>, https://x.io/a  b, <span>x</span>, [^1] and \\*  text.   \n"
        "infor-\nmation # not a heading\n\n\n\n"
        "12\n\n"
        "- item  with  spaces \n"
        "> quote  \n\n"
        "A long enough wrapped paragraph line that keeps going and going on\n"
        "and the next line continues the same sentence without any break here.\n"
    )
    result = run(text, *REGISTRY)  # every rule, including the opt-in ones
    assert result.document.text != text
    for kind in MASK_SAMPLES:
        if kind is not SpanKind.ESCAPE:
            span = span_of(text, kind)
            assert text[span.start : span.end] in result.document.text
