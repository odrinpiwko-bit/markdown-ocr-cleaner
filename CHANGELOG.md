# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project skeleton: src layout, hatchling build, MIT license.
- `md-ocr-clean` command with `--version` and a `--list-rules` placeholder.
- Initial pytest suite, Ruff configuration, and GitHub Actions CI workflow.
- Safe document input/output: strict decoding with `--encoding`
  (`utf-8`, `utf-8-sig`, `cp1250`, `iso-8859-2`), UTF-8 output, BOM
  preservation, LF/CRLF preservation with a warning for mixed line endings,
  final-newline preservation, and atomic writes.
- Output options: positional `INPUT`, `-o/--output` (by default `.cleaned`
  is inserted before the input file extension: `scan.md` → `scan.cleaned.md`,
  `scan.txt` → `scan.cleaned.txt`), `--force`, and `--in-place` with a required
  `--backup` or `--no-backup`. The input file is never overwritten by default.
- Rule framework: `Rule` base class with validated metadata, an explicit
  ordered registry, and rule selection with `--enable`,
  `--disable`, and `--only`. `--list-rules` now reads the real registry.
- Report model (`SourceLocation`, `Change`, `Finding`, `Report`) with
  deterministic JSON-compatible serialization (`schema_version` 1) and
  console-safe human rendering; `-v/--verbose` lists individual changes.
- Transformation pipeline that runs selected rules in registry order and fails
  with exit code 4 (writing nothing) if a rule raises.

- Conservative Markdown block segmenter (`segmenter.py`) that classifies
  every part of a document as protected, structural, or editable, keeps exact
  source lines and offsets, and reports unclosed blocks and unmatched
  directives as diagnostics.
- `<!-- ocr-clean: off -->` / `<!-- ocr-clean: on -->` escape-hatch
  directives with nesting.
- Inline masker (`inline.py`) for code spans, link and image destinations,
  autolinks, bare URLs, inline HTML, footnote references, and escapes.
- First cleaning rules, all safe and enabled by default, in this order:
  `trailing-whitespace` (keeps Markdown hard breaks), `repeated-spaces`
  (prose paragraphs only, never inside code, URLs, or HTML), and
  `excessive-blank-lines` (three or more empty lines become one).
- Segmentation diagnostics are reported as `markdown-segmenter` findings.
- `-n/--dry-run` (never writes the cleaned output or a backup; only an
  explicitly requested report file), `--check` (implies dry run, exit 1 if
  changes would be made), `--diff` (unified diff of the normalized text on
  stdout), `--report-json PATH|-` (UTF-8 JSON report, written atomically, path
  conflicts refused), and `--fail-on-warning` (exit 1 on warning or error
  findings, after writing).
- Exit code `1` for `--check` and `--fail-on-warning`.
- Detect-only rules, enabled by default and run before all other rules:
  `suspicious-characters` (`replacement-character`, `control-character`,
  `bidi-control`, `invisible-character`, `mixed-script-word` as warnings;
  `form-feed`, `digit-letter-confusion` as info) and `detect-page-numbers`
  (`probable-page-number`, `possible-page-number` as info). They never change
  text and report nothing inside `ocr-clean: off` regions.
- Opt-in destructive rules, disabled by default: `remove-page-numbers`
  (removes probable page numbers isolated by blank lines, keeping one
  separator; otherwise reports `page-number-kept` with a reason) and
  `join-hyphenated-words` (joins line-end hyphenation inside a paragraph by
  moving the fragment up; dictionary-free, reports `ambiguous-hyphenation` for
  prefixes ending in `o`, common compound prefixes, and multi-hyphen words).
- Opt-in destructive rule `reconstruct-paragraphs`, disabled by default:
  joins the lines of an OCR-wrapped prose paragraph into one line, all or
  nothing, using fixed shape thresholds (median non-final line width at least
  40, every non-final line at least 0.75 of it). Blocked candidates are kept
  and reported once as `paragraph-kept` with the first reason.
- Central structure enforcement: every rule declares how its edits may change
  the block structure (`structure` policy), and the pipeline verifies it after
  re-segmenting. Whitespace rules may not change it, `excessive-blank-lines`
  may only change blank lines, `join-hyphenated-words` and
  `reconstruct-paragraphs` may not create, remove, or reclassify structural or
  protected blocks, and `remove-page-numbers` may only remove whole paragraphs
  and blank lines. Violations stop the run with exit code 4.
- Central inline-mask enforcement: the pipeline rejects any edit that overlaps
  inline code, a link or image destination, an autolink, a bare URL, inline
  HTML, a footnote reference, or a backslash escape, whatever the rule checks
  itself.
- `SECURITY.md` with the security policy and scope.
- `build` and `pytest-cov` in the `dev` extra, with coverage configuration.

### Fixed

- `join-hyphenated-words` could create Markdown structure: moving the
  fragment up out of `mation # Heading` left `# Heading` at the start of a
  line (likewise list items, blockquotes, fences, directives, definitions,
  tables). Such joins are now refused and reported as `ambiguous-hyphenation`
  with the reason `joining would create Markdown structure`; joins that would
  move inline code, a URL, or inline HTML to the start of a line are refused
  with their own reason.
- Unexpected internal errors exited with Python's default code `1`, the same
  code as `--check` findings. They now exit with `4` and a one-line message
  without a traceback. A closed stdout (for example `--diff | head`) exits
  with `3` instead of crashing; a failed `--diff` writes nothing.
- A list or task item whose first line is code-like (`-     code`, tab
  indentation, five or more columns after the marker) or starts with HTML, a
  fence, a definition, `$$`, or a directive is now protected instead of
  structural.
- `trailing-whitespace` no longer removes whitespace after a lone carriage
  return (which turned it into a CRLF line ending) or empties a
  whitespace-only last line without a final newline (which changed the
  final-newline state).
- Linear-time scanning for trailing whitespace, table delimiter rows, and
  line-end hyphens, and for grouping page-number runs; very long lines or
  space runs previously took quadratic time.

### Changed

- The pipeline is now segment-aware. Rules receive a `RuleContext` and return
  `TextEdit` proposals limited to their declared `edit_kinds`; the pipeline
  validates every edit, applies it, re-segments, and verifies that protected
  content is unchanged and still protected. Change and finding locations refer
  to the original input.
- HTML blocks containing an unclosed `<pre>`, `<script>`, `<style>`, or
  `<textarea>` now stay protected until that element is closed, so blank lines
  inside it are never collapsed.

- Segmenter safety correction: table-like regions whose inline syntax is
  ambiguous (unmatched backticks, escaped pipes, pipes in code, mismatched
  cell counts) are now protected instead of left editable, with an
  `ambiguous-markdown-table` diagnostic. HTML-like blocks whose opening tag is
  not closed on its line are protected (`ambiguous-html-block` for unknown
  tags).
- The mixed-line-endings warning is now a `document-io/mixed-line-endings`
  finding shown through the report renderer.
- Documented that line-ending normalization of mixed LF/CRLF input, encoding
  conversion, and BOM handling apply to the whole document, including
  `ocr-clean: off` regions: rules never change those regions, but they are
  byte-for-byte identical only for UTF-8 input with consistent line endings.
- Development status classifier is now Alpha.
