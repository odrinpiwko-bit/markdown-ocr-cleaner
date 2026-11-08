# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No changes yet.

## [0.1.0] - 2026-11-08

First release (alpha).

### Added

- `md-ocr-clean` command (also `python -m md_ocr_cleaner`) for one input
  file, with `--version` and `--list-rules`.
- Unicode text in any language is preserved and processed. The cleanup
  heuristics are dictionary-free, conservative, and English-first where they
  use built-in words; they are not guaranteed to be linguistically accurate
  for every language or writing system.
- Conservative, line-oriented Markdown segmenter that classifies every part
  of a document as protected, structural, or editable, keeps exact source
  lines and offsets, protects uncertain constructs, and reports unclosed
  blocks, unmatched directives, and ambiguous tables or HTML as
  `markdown-segmenter` findings. Protected blocks: front matter, fenced and
  indented code, HTML blocks and comments, tables, link-reference and
  footnote definitions, thematic breaks, `$$` math blocks, and list items or
  blockquotes containing such content.
- `<!-- ocr-clean: off -->` / `<!-- ocr-clean: on -->` escape-hatch
  directives with nesting; an unmatched `off` protects the rest of the
  document. No rule changes escaped content; decoding, BOM handling, and
  mixed-line-ending normalization of the whole document still apply to it.
- Inline masking of code spans, link and image destinations, autolinks,
  email autolinks, bare URLs, inline HTML, footnote references, and
  backslash escapes.
- Safe rules, enabled by default: `trailing-whitespace` (keeps Markdown hard
  breaks), `repeated-spaces` (prose paragraphs only), and
  `excessive-blank-lines` (three or more empty lines become one).
- Detect-only rules, enabled by default, that never change text:
  `suspicious-characters` (replacement, control, bidi, invisible characters,
  form feeds, mixed-script words, digit/letter confusion) and
  `detect-page-numbers` (probable and possible page numbers).
- Opt-in destructive rules, disabled by default:
  - `remove-page-numbers`: removes probable page numbers isolated by blank
    lines, keeping one separator, or reports `page-number-kept` with a
    reason;
  - `join-hyphenated-words`: dictionary-free joining of line-end hyphenation
    inside a paragraph, reporting `ambiguous-hyphenation` for likely
    compounds;
  - `reconstruct-paragraphs`: all-or-nothing joining of OCR-wrapped prose
    paragraphs, reporting `paragraph-kept` with the first blocking reason.
- Rule selection with `--enable`, `--disable`, and `--only`; rules always run
  in registry order.
- `-n/--dry-run`, `--check` (implies dry run; exit 1 if changes would be
  made), `--diff` (unified diff on stdout), `--report-json PATH|-` (JSON
  report, `schema_version` 1), `--fail-on-warning`, and `-v/--verbose`.
- Change and finding locations that refer to the original input, with the
  column omitted when it cannot be mapped exactly.
- Document input/output: strict decoding with `--encoding` (`utf-8`,
  `utf-8-sig`, `cp1250`, `iso-8859-2`), UTF-8 output, BOM preservation,
  LF/CRLF preservation, normalization of mixed line endings to the dominant
  style with a warning, and final-newline preservation.
- Safe output: by default `.cleaned` is inserted before the input file
  extension (`scan.md` → `scan.cleaned.md`, `scan.txt` → `scan.cleaned.txt`),
  the input is never overwritten without
  `--in-place` (which requires `--backup` or `--no-backup`), existing files
  replaced only with `--force`, atomic writes, and path-conflict checks for
  the output, report, and backup.
- Central enforcement in the pipeline: every proposed edit is validated
  (range, expected text, no overlap, segment kind, protection, no overlap
  with masked inline spans); after each rule the document is re-segmented
  and protected content must be unchanged and still protected, and the block
  structure may change only as the rule's declared `structure` policy
  allows. Any violation stops the run with exit code 4 and nothing is
  written.
- Documented exit codes: 0 success, 1 check or fail-on-warning, 2 usage
  error, 3 input/output error (including a closed stdout), 4 rule, pipeline,
  or internal error.
- GitHub Actions CI on Ubuntu and Windows with Python 3.11, 3.12, and 3.13
  (Ruff, pytest, CLI smoke tests) and a job that builds, installs, and
  smoke-tests the wheel and source distribution.

### Fixed

These were found during the pre-release audit:

- `join-hyphenated-words` could create Markdown structure by moving a
  fragment up and leaving text such as `# Heading`, `- item`, a fence, or a
  directive at the start of a line. Such joins are now refused and reported
  as `ambiguous-hyphenation` (`joining would create Markdown structure`).
- Unexpected internal errors exited with code 1, which `--check` uses for
  changes; they now exit with 4 and a one-line message. A closed stdout
  pipe exits with 3, and a failed `--diff` writes nothing.
- List or task items whose first line is code-like or starts with HTML, a
  fence, a definition, `$$`, or a directive are protected.
- `trailing-whitespace` keeps whitespace after a lone carriage return and on
  a whitespace-only last line without a final newline.
- Linear-time scanning for trailing whitespace, table delimiter rows,
  line-end hyphens, and page-number runs, which were quadratic on very long
  lines or space runs.

### Security

- `SECURITY.md` describes private vulnerability reporting through GitHub
  and the scope of security issues.
- The tool runs locally only: no network access, telemetry, subprocesses, or
  runtime dependencies.

[Unreleased]: https://github.com/odrinpiwko-bit/markdown-ocr-cleaner/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/odrinpiwko-bit/markdown-ocr-cleaner/releases/tag/v0.1.0
