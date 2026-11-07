import pytest

from md_ocr_cleaner import rules as production
from md_ocr_cleaner.rules.base import Rule, RuleResult, Safety, validate_registry, validate_rule_id
from md_ocr_cleaner.rules.selection import SelectionError, parse_rule_ids, select_rules
from md_ocr_cleaner.segmenter import MODIFIABLE_KINDS, SegmentKind

from fakes import DetectRule, ReplaceRule, UpperRule, registry

# --- Metadata ------------------------------------------------------------------------


def test_valid_rule_metadata():
    rule = ReplaceRule()
    assert rule.id == "fake-replace"
    assert rule.safety is Safety.SAFE
    assert rule.default_enabled is True
    assert rule.description


@pytest.mark.parametrize("rule_id", ["a", "trailing-whitespace", "rule2", "x-1-y"])
def test_valid_rule_ids(rule_id):
    assert validate_rule_id(rule_id) == rule_id


@pytest.mark.parametrize(
    "rule_id",
    ["", "Upper", "snake_case", "-lead", "trail-", "double--dash", "1abc", "has space", None],
)
def test_invalid_rule_ids(rule_id):
    with pytest.raises(ValueError):
        validate_rule_id(rule_id)


@pytest.mark.parametrize("rule_id", ["document-io", "markdown-segmenter"])
def test_system_rule_ids_are_reserved(rule_id):
    with pytest.raises(ValueError, match="reserved"):
        validate_rule_id(rule_id)


def _define(**attrs):
    namespace = {
        "id": "ok-rule",
        "description": "Fine.",
        "safety": Safety.SAFE,
        "default_enabled": False,
        "edit_kinds": frozenset({SegmentKind.PARAGRAPH}),
        "apply": lambda self, ctx: RuleResult(),
    }
    namespace.update(attrs)
    return type("Defined", (Rule,), {k: v for k, v in namespace.items() if v is not ...})


def test_rule_definition_validation_accepts_valid():
    assert _define()().id == "ok-rule"


@pytest.mark.parametrize(
    ("attrs", "error"),
    [
        ({"id": "Bad_ID"}, ValueError),
        ({"id": ...}, TypeError),
        ({"description": "  "}, ValueError),
        ({"safety": "safe"}, TypeError),
        ({"default_enabled": 1}, TypeError),
        ({"safety": Safety.DESTRUCTIVE, "default_enabled": True}, ValueError),
        ({"edit_kinds": ...}, TypeError),
        ({"edit_kinds": {SegmentKind.PARAGRAPH}}, TypeError),
        ({"edit_kinds": frozenset({"paragraph"})}, TypeError),
        ({"edit_kinds": frozenset({SegmentKind.FENCED_CODE})}, ValueError),
        ({"edit_kinds": frozenset({SegmentKind.TABLE, SegmentKind.BLANK})}, ValueError),
        ({"safety": Safety.DETECT_ONLY}, ValueError),
    ],
)
def test_rule_definition_validation_rejects(attrs, error):
    with pytest.raises(error):
        _define(**attrs)


# --- Registry ------------------------------------------------------------------------


def test_registry_preserves_order():
    rules = registry()
    assert [r.id for r in validate_registry(rules)] == ["fake-replace", "fake-upper", "fake-detect"]


def test_duplicate_rule_ids_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        validate_registry([ReplaceRule(), ReplaceRule()])


def test_non_rule_entry_rejected():
    with pytest.raises(TypeError):
        validate_registry(["fake-replace"])


def test_production_registry_order_and_metadata():
    rules = production.REGISTRY
    assert [(r.id, r.safety) for r in rules] == [
        ("suspicious-characters", Safety.DETECT_ONLY),
        ("detect-page-numbers", Safety.DETECT_ONLY),
        ("remove-page-numbers", Safety.DESTRUCTIVE),
        ("join-hyphenated-words", Safety.DESTRUCTIVE),
        ("reconstruct-paragraphs", Safety.DESTRUCTIVE),
        ("trailing-whitespace", Safety.SAFE),
        ("repeated-spaces", Safety.SAFE),
        ("excessive-blank-lines", Safety.SAFE),
    ]
    assert validate_registry(rules) == rules
    for rule in rules:
        assert rule.default_enabled is (rule.safety is not Safety.DESTRUCTIVE)
        assert rule.description.strip()
        if rule.safety is Safety.DETECT_ONLY:
            assert rule.edit_kinds == frozenset()


def test_production_rule_permissions_are_minimal():
    by_id = {r.id: r for r in production.REGISTRY}
    assert by_id["repeated-spaces"].edit_kinds == {SegmentKind.PARAGRAPH}
    assert by_id["excessive-blank-lines"].edit_kinds == {SegmentKind.BLANK}
    trailing = by_id["trailing-whitespace"].edit_kinds
    assert SegmentKind.PARAGRAPH in trailing and SegmentKind.BLANK in trailing
    for rule in production.REGISTRY:
        assert rule.edit_kinds <= MODIFIABLE_KINDS


def test_production_rules_are_independently_selectable():
    for rule in production.REGISTRY:
        assert select_rules(production.REGISTRY, only=[rule.id]) == (rule,)
        remaining = select_rules(production.REGISTRY, disable=[rule.id])
        defaults = select_rules(production.REGISTRY)
        assert rule not in remaining
        assert len(remaining) == len(defaults) - (1 if rule.default_enabled else 0)


def test_test_rules_do_not_leak_into_production_registry():
    ids = {r.id for r in production.REGISTRY}
    assert not any(i.startswith("fake-") for i in ids)
    assert not any(type(r).__module__ == "fakes" for r in production.REGISTRY)


# --- Selection -----------------------------------------------------------------------


def ids(rules):
    return [r.id for r in rules]


def test_default_selection_uses_default_enabled():
    assert ids(select_rules(registry())) == ["fake-replace", "fake-detect"]


def test_default_disabled_rule_not_selected():
    assert "fake-upper" not in ids(select_rules(registry()))


def test_enable_adds_rule():
    selected = select_rules(registry(), enable=["fake-upper"])
    assert ids(selected) == ["fake-replace", "fake-upper", "fake-detect"]


def test_disable_removes_rule():
    assert ids(select_rules(registry(), disable=["fake-replace"])) == ["fake-detect"]


def test_enable_then_disable():
    selected = select_rules(
        registry(), enable=["fake-upper"], disable=["fake-upper", "fake-detect"]
    )
    assert ids(selected) == ["fake-replace"]


def test_only_selects_exactly():
    assert ids(select_rules(registry(), only=["fake-upper"])) == ["fake-upper"]


def test_only_empty_selection():
    assert select_rules(registry(), only=[]) == ()


def test_registry_order_regardless_of_option_order():
    selected = select_rules(registry(), only=["fake-detect", "fake-upper", "fake-replace"])
    assert ids(selected) == ["fake-replace", "fake-upper", "fake-detect"]


def test_duplicate_ids_select_once():
    selected = select_rules(registry(), enable=parse_rule_ids(["fake-upper,fake-upper"]))
    assert ids(selected).count("fake-upper") == 1


@pytest.mark.parametrize("kwargs", [{"enable": ["fake-upper"]}, {"disable": ["fake-upper"]}])
def test_only_conflicts(kwargs):
    with pytest.raises(SelectionError, match="--only"):
        select_rules(registry(), only=["fake-replace"], **kwargs)


@pytest.mark.parametrize("kind", ["enable", "disable", "only"])
def test_unknown_ids_rejected(kind):
    with pytest.raises(
        SelectionError, match=r"unknown rule ID.*nope.*available rules: fake-replace"
    ):
        select_rules(registry(), **{kind: ["nope"]})


def test_unknown_id_with_empty_registry():
    with pytest.raises(SelectionError, match="no rules are implemented"):
        select_rules((), enable=["anything"])


def test_parse_repeated_and_comma_separated():
    assert parse_rule_ids(["a,b", "c", " d , e "]) == ["a", "b", "c", "d", "e"]


@pytest.mark.parametrize("value", ["", ",", "a,", ",a", "a,,b", " , "])
def test_parse_malformed_lists(value):
    with pytest.raises(SelectionError, match="empty rule ID"):
        parse_rule_ids([value])


def test_detect_rule_metadata():
    assert DetectRule.safety is Safety.DETECT_ONLY
    assert UpperRule.default_enabled is False
