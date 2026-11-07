"""Explicit, ordered production rule registry.

Registry order is execution order. Add new rule instances here, in the position
where they must run.

Current order and why it is safe: the detect-only rules run first so they see
the original text (exact columns) and cannot be affected by any edit; they
never change text. The opt-in destructive rules come next: page numbers are
removed before hyphen joining, so a removed page-number paragraph never sits
inside a joined pair. ``reconstruct-paragraphs`` follows hyphen joining, so
repaired word splits can take part in reconstruction while any remaining
line-end hyphen blocks it. All three run before the whitespace rules, which
then tidy whatever they leave. ``trailing-whitespace`` only touches line ends,
``repeated-spaces`` only touches space runs between two non-whitespace
characters (so never a line end), and ``excessive-blank-lines`` only touches
empty lines between blocks. None can create or remove a block marker.
"""

from md_ocr_cleaner.rules.base import (
    Rule,
    RuleContext,
    RuleResult,
    Safety,
    TextEdit,
    validate_registry,
)
from md_ocr_cleaner.rules.blank_lines import ExcessiveBlankLines
from md_ocr_cleaner.rules.hyphenation import JoinHyphenatedWords
from md_ocr_cleaner.rules.page_numbers import DetectPageNumbers, RemovePageNumbers
from md_ocr_cleaner.rules.paragraphs import ReconstructParagraphs
from md_ocr_cleaner.rules.suspicious import SuspiciousCharacters
from md_ocr_cleaner.rules.whitespace import RepeatedSpaces, TrailingWhitespace

REGISTRY: tuple[Rule, ...] = validate_registry(
    (
        SuspiciousCharacters(),
        DetectPageNumbers(),
        RemovePageNumbers(),
        JoinHyphenatedWords(),
        ReconstructParagraphs(),
        TrailingWhitespace(),
        RepeatedSpaces(),
        ExcessiveBlankLines(),
    )
)

__all__ = ["REGISTRY", "Rule", "RuleContext", "RuleResult", "Safety", "TextEdit"]
