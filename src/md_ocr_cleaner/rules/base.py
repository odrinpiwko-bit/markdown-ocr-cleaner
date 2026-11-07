"""Rule interface: metadata, edit proposals, and the context rules receive.

A rule never changes text itself. ``apply`` receives a read-only
``RuleContext`` (current text, current segments, and a helper that maps
offsets to original source locations) and returns ``TextEdit`` proposals.
The pipeline validates every proposal centrally and applies it; a rule cannot
touch PROTECTED segments or segment kinds outside its ``edit_kinds``.

Metadata is validated when the subclass is defined, so a malformed rule fails
at import time rather than during a run.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from md_ocr_cleaner.report import RESERVED_RULE_IDS, Finding, SourceLocation
from md_ocr_cleaner.segmenter import MODIFIABLE_KINDS, Segment, SegmentKind

RULE_ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")


class Safety(Enum):
    SAFE = "safe"
    DESTRUCTIVE = "destructive"
    DETECT_ONLY = "detect-only"


class Structure(Enum):
    """How a rule's edits may change the segmentation; enforced by the pipeline.

    Segments are compared by (kind, protection) in document order after
    re-segmenting the edited text.
    """

    PRESERVED = "preserved"
    """No change at all to the sequence of segments."""
    BLANK_LINES = "blank-lines"
    """Only blank segments may change; all other segments stay the same."""
    NO_NEW_BLOCKS = "no-new-blocks"
    """Paragraphs may change, but no structural or protected segment may appear,
    disappear, or change."""
    REMOVE_PARAGRAPHS = "remove-paragraphs"
    """Whole paragraphs (one per paragraph-removing edit) and blank segments may
    disappear; nothing else may change."""


@dataclass(frozen=True)
class TextEdit:
    """Replace ``expected`` at ``[start, end)`` of the current text with ``replacement``."""

    start: int
    end: int
    expected: str
    replacement: str
    message: str


@dataclass(frozen=True)
class RuleResult:
    edits: tuple[TextEdit, ...] = ()
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class RuleContext:
    text: str
    """Current normalized text (after earlier rules)."""
    segments: tuple[Segment, ...]
    """Segmentation of ``text``."""
    locate: Callable[[int, int], SourceLocation]
    """Map a ``[start, end)`` range of ``text`` to a location in the original input."""

    def segments_for(self, kinds: Iterable[SegmentKind]) -> Iterator[Segment]:
        """Non-protected segments of the given kinds, in document order."""
        wanted = frozenset(kinds)
        return (s for s in self.segments if s.kind in wanted and not s.is_protected)


def validate_rule_id(rule_id: object) -> str:
    if not isinstance(rule_id, str) or not RULE_ID_PATTERN.fullmatch(rule_id):
        raise ValueError(f"invalid rule ID {rule_id!r}: must be lowercase kebab-case")
    if rule_id in RESERVED_RULE_IDS:
        raise ValueError(f"rule ID {rule_id!r} is reserved for system findings")
    return rule_id


class Rule(ABC):
    id: ClassVar[str]
    description: ClassVar[str]
    safety: ClassVar[Safety]
    default_enabled: ClassVar[bool]
    edit_kinds: ClassVar[frozenset[SegmentKind]]
    """Segment kinds this rule may edit. Empty for detect-only rules."""
    structure: ClassVar[Structure] = Structure.PRESERVED
    """Segmentation changes this rule's edits may cause."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        for attr in ("id", "description", "safety", "default_enabled", "edit_kinds"):
            if attr not in cls.__dict__:
                raise TypeError(f"rule {cls.__name__} must define {attr!r}")
        validate_rule_id(cls.id)
        if not isinstance(cls.description, str) or not cls.description.strip():
            raise ValueError(f"rule {cls.id!r} needs a non-empty description")
        if not isinstance(cls.safety, Safety):
            raise TypeError(f"rule {cls.id!r}: safety must be a Safety value")
        if not isinstance(cls.default_enabled, bool):
            raise TypeError(f"rule {cls.id!r}: default_enabled must be a bool")
        if cls.default_enabled and cls.safety is Safety.DESTRUCTIVE:
            raise ValueError(f"rule {cls.id!r}: destructive rules must be disabled by default")
        if not isinstance(cls.structure, Structure):
            raise TypeError(f"rule {cls.id!r}: structure must be a Structure value")
        if cls.safety is Safety.DETECT_ONLY and cls.structure is not Structure.PRESERVED:
            raise ValueError(f"rule {cls.id!r}: detect-only rules must preserve structure")
        kinds = cls.edit_kinds
        if not isinstance(kinds, frozenset) or not all(isinstance(k, SegmentKind) for k in kinds):
            raise TypeError(f"rule {cls.id!r}: edit_kinds must be a frozenset of SegmentKind")
        if cls.safety is Safety.DETECT_ONLY and kinds:
            raise ValueError(f"rule {cls.id!r}: detect-only rules cannot declare edit_kinds")
        if not kinds <= MODIFIABLE_KINDS:
            forbidden = ", ".join(sorted(k.value for k in kinds - MODIFIABLE_KINDS))
            raise ValueError(f"rule {cls.id!r}: kinds are always protected: {forbidden}")

    @abstractmethod
    def apply(self, ctx: RuleContext) -> RuleResult:
        """Propose edits (and findings) for ``ctx.text``. Must not do I/O."""


def validate_registry(rules: Iterable[Rule]) -> tuple[Rule, ...]:
    """Check a registry for non-rule entries and duplicate IDs; keep its order."""
    result = tuple(rules)
    seen: set[str] = set()
    for rule in result:
        if not isinstance(rule, Rule):
            raise TypeError(f"registry entry {rule!r} is not a Rule instance")
        if rule.id in seen:
            raise ValueError(f"duplicate rule ID {rule.id!r} in registry")
        seen.add(rule.id)
    return result
