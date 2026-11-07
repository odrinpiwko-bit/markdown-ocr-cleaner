"""Rule selection from ``--enable``, ``--disable``, and ``--only``.

Semantics:

- Default: every rule with ``default_enabled=True``.
- ``--enable`` adds rules to the default selection, then ``--disable`` removes
  rules from the result (so a rule named in both ends up disabled).
- ``--only`` selects exactly the named rules and cannot be combined with
  ``--enable`` or ``--disable``.
- Selected rules always run in registry order, each at most once.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from md_ocr_cleaner.rules.base import Rule


class SelectionError(ValueError):
    """Invalid rule selection (unknown IDs, malformed lists, conflicts)."""


def parse_rule_ids(values: Iterable[str]) -> list[str]:
    """Flatten repeated and comma-separated option values, rejecting empty items."""
    ids: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if not item:
                raise SelectionError(f"empty rule ID in {value!r}")
            ids.append(item)
    return ids


def select_rules(
    registry: Sequence[Rule],
    *,
    enable: Iterable[str] = (),
    disable: Iterable[str] = (),
    only: Iterable[str] | None = None,
) -> tuple[Rule, ...]:
    enable, disable = set(enable), set(disable)
    only = set(only) if only is not None else None
    if only is not None and (enable or disable):
        raise SelectionError("--only cannot be combined with --enable or --disable")

    known = {rule.id for rule in registry}
    requested = enable | disable | (only or set())
    unknown = sorted(requested - known)
    if unknown:
        available = ", ".join(rule.id for rule in registry) or "none (no rules are implemented)"
        raise SelectionError(
            f"unknown rule ID(s): {', '.join(unknown)}; available rules: {available}"
        )

    if only is not None:
        chosen = only
    else:
        chosen = ({rule.id for rule in registry if rule.default_enabled} | enable) - disable
    return tuple(rule for rule in registry if rule.id in chosen)
