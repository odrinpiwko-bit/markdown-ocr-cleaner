# Contributing to Markdown OCR Cleaner

Thank you for your interest in contributing.

## Development setup

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows:     .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

## Checks to run before opening a pull request

```bash
ruff check .
ruff format --check .
pytest
```

Optional coverage report (do not commit `.coverage`, `coverage.xml`, or
`htmlcov/`):

```bash
pytest --cov --cov-report=term-missing
```

The CI workflow runs the same checks and CLI smoke tests on Ubuntu and Windows
with Python 3.11, 3.12, and 3.13, and a separate job builds the wheel and
sdist and smoke-tests the installed wheel.

To build the distributions locally:

```bash
python -m build
```

## Project principles

- **Preserve content.** When a transformation might not be safe, leave the
  text unchanged and, where useful, emit a finding.
- **Conservative defaults.** Potentially destructive rules must be disabled by
  default.
- **No silent guesses.** Ambiguous OCR substitutions are reported, never
  auto-corrected.
- **Minimal dependencies.** The runtime uses only the Python standard library.
  Do not add runtime dependencies without discussion.
- **No network access, telemetry, or AI services** at runtime.
- **Never guess the encoding.** Only the documented encodings are accepted,
  and undecodable input fails with a clear error and writes nothing.
- **Escaped regions are off limits.** No rule may change content between
  `<!-- ocr-clean: off -->` and `<!-- ocr-clean: on -->`.
- **English-first, Unicode-safe.** Documentation and examples are in English.
  Heuristics must handle arbitrary Unicode text conservatively; any built-in
  word list must be documented.
- **Cross-platform.** Tests must pass on Linux and Windows; watch CRLF line
  endings and path handling.
- **Accurate documentation.** Never document behavior that is not
  implemented, and add every user-visible change to the CHANGELOG under
  `[Unreleased]`.

## Adding a rule

Rules subclass `md_ocr_cleaner.rules.base.Rule`. A rule never changes text
itself: it receives a read-only `RuleContext` and returns `TextEdit`
proposals, which the pipeline validates and applies.

```python
class RepeatedSpaces(Rule):
    id = "repeated-spaces"
    description = "Collapse repeated spaces inside prose to one space."
    safety = Safety.SAFE
    default_enabled = True
    edit_kinds = frozenset({SegmentKind.PARAGRAPH})
    structure = Structure.PRESERVED

    def apply(self, ctx: RuleContext) -> RuleResult:
        edits = []
        for segment in ctx.segments_for(self.edit_kinds):
            ...  # offsets are in ctx.text
            edits.append(TextEdit(start, end, ctx.text[start:end], " ", "collapsed spaces"))
        return RuleResult(edits=tuple(edits))
```

Metadata is validated when the class is defined. Checklist:

1. A unique lowercase kebab-case `id`. `document-io` and `markdown-segmenter`
   are reserved for system findings.
2. A one-line `description`.
3. A `safety` class. `detect-only` rules must declare no `edit_kinds` and
   propose no edits.
4. `default_enabled`. Destructive rules must be `False`.
5. `edit_kinds`: the smallest set of segment kinds the rule needs to edit.
   Kinds that are always protected cannot be declared, and segments that the
   segmenter protected (for example a list item containing code) are refused
   even if their kind is declared.
6. `apply` is pure: no I/O and no global state. Edits use half-open offsets in
   `ctx.text`, must not overlap, must stay inside one segment, and must carry
   the exact `expected` text. Skip anything covered by `mask_inline` spans
   (the pipeline also rejects such edits, but a rule should not propose
   them). Locations for findings come from `ctx.locate(start, end)`, which
   maps to the original input.
7. `structure`: the `Structure` policy for how the rule's edits may change
   the segmentation. The default, `PRESERVED`, allows no change; choose the
   narrowest policy that fits. If the rule could create structure in some
   case (as moving a hyphenated fragment can), detect that case in the rule
   and keep the text with a finding: a policy violation aborts the whole run.
8. An instance in `REGISTRY` in `src/md_ocr_cleaner/rules/__init__.py`, at the
   position where it must run, with a note on why that order is safe.
9. Unit tests for positive cases, negative cases (text that must not change),
   edge cases, protected content, exact change locations, and idempotence. Use
   isolated test registries for pipeline behavior.
10. A CHANGELOG entry.

### Detect-only rules

Detection rules use `Safety.DETECT_ONLY`, declare `edit_kinds = frozenset()`,
and return findings only (the pipeline rejects any edit). They must:

- skip `ESCAPED_REGION` and `DIRECTIVE` segments entirely;
- only inspect PROTECTED segments or inline-masked spans for *definite*
  problems (specific characters), never for heuristic guesses;
- report at most one finding per line and code, located with
  `ctx.locate(start, end)`;
- use INFO for guesses and WARNING for findings that should fail
  `--fail-on-warning`;
- avoid false positives on accented Latin letters, single-script non-Latin
  text, NBSP typography, and combining marks; base heuristics on Unicode
  properties, not on one language.

Shared analysis that a later removal rule will need (such as
`pagenumbers.py`) must not decide on removal itself.

### Destructive rules

Destructive rules use `Safety.DESTRUCTIVE` and `default_enabled = False`
(enforced). They must:

- declare the smallest `edit_kinds` they need and keep every `TextEdit` inside
  one segment (several edits in neighboring segments are fine);
- act only on strong evidence. When a candidate is strong but withheld for a
  documented safety reason, report one INFO finding with a stable code and
  reason; never report every weak candidate;
- leave the text unchanged rather than guess, and never rely on a later rule
  to repair what they leave behind;
- be tested for idempotence, original locations, protected and escaped
  content, and every documented withheld case.

Prefer all-or-nothing decisions per unit (as `reconstruct-paragraphs` does per
paragraph) when partial edits could make a later run decide differently;
that keeps destructive rules idempotent by construction.

## Markdown segmentation

`segment_document(text)` in `src/md_ocr_cleaner/segmenter.py` is the safety
boundary between rules and Markdown structure. It returns contiguous segments
that are exact slices of the input, each `PROTECTED`, `STRUCTURAL`, or
`EDITABLE`. `mask_inline(text)` in `inline.py` returns spans inside a segment
that must also stay unchanged.

When changing the segmenter:

- Prefer protecting more. A construct the segmenter is unsure about must not
  become `EDITABLE`.
- Never change text. Every change must keep `validate_segments` passing and
  `reassemble(segments) == text`.
- Add a test for the construct and its malformed or unclosed variant.

## How the pipeline enforces protection

For each rule, `pipeline.py` rejects edits that are out of range, empty,
no-ops, overlapping, mismatched with the current text, crossing a segment
boundary, inside a protected segment, in an undeclared segment kind, or
overlapping a masked inline span. After applying a rule's edits it re-segments
the text and checks that every previously protected segment is unchanged and
still covered only by protected segments, and that the sequence of segment
kinds and protections changed only as the rule's `structure` policy allows.
Any failure raises `PipelineError` and nothing is written.

`tests/test_central_enforcement.py` holds deliberately malicious test rules
for these checks. Never relax a check to make a rule pass; fix the rule.

## Exit codes and errors

The CLI maps every outcome to a documented exit code (0 success, 1 check or
fail-on-warning, 2 usage, 3 input/output including a closed stdout, 4 rule,
pipeline, or internal failure). `main` catches unexpected `Exception`s and
reports them in one line with exit code 4, so a crash can never look like
exit code 1. Do not catch `BaseException`.

## Command-line output rules

- Human-readable findings, summaries, and status lines go to stderr.
- Stdout is reserved for one machine-readable output at a time (`--diff` or
  `--report-json -`). Never print the cleaned document or diagnostics there.
- Every write goes through `document.write_output` (atomic, overwrite-safe),
  and every destination is validated with `check_output_path` before anything
  is written.
- A dry run must never write the cleaned output or a backup.

## Bug fixes

Every confirmed bug fix must include a regression test that fails before the
fix and passes after it.

## Commit and pull request guidelines

- Keep pull requests small and focused on one change.
- Write clear commit messages in the imperative mood.
- Update documentation and the CHANGELOG when behavior changes.
