# Markdown OCR Cleaner

> **Status: alpha, version 0.1.0.** Three conservative whitespace
> rules, two detect-only rules, and three opt-in destructive rules
> (page-number removal, hyphen joining, paragraph reconstruction) are
> implemented. Header/footer handling is **not implemented yet**. Nothing
> destructive runs unless you enable it.

Markdown OCR Cleaner (`md-ocr-clean`) is a command-line tool for universal,
conservative, and deterministic cleanup of OCR-produced Markdown and
plain-text documents. It runs locally, uses deterministic text-processing
rules, and needs no network access, external API, or AI service.

Markdown OCR Cleaner preserves and processes Unicode text in any language.
Its dictionary-free cleanup heuristics are conservative and are not
guaranteed to be linguistically accurate for every language or writing
system. Suspicious-character checks use Unicode character properties. The few
built-in words and abbreviations, including `Page`, `Art.` / `art.`, compound
prefixes and unit names, are English-first heuristics. Hyphen joining
requires lowercase letters and therefore does not join words in scripts
without letter case, and paragraph reconstruction is intended for writing
systems that separate words with spaces (see the rule descriptions below).

## Goals

- Preserve the meaning and content of the source text.
- Preserve valid Markdown structure (front matter, headings, lists, tables,
  code, links, footnotes, HTML, and more).
- Conservative defaults: potentially destructive transformations are opt-in.
- Never apply uncertain OCR substitutions (such as `O`/`0`, `l`/`1`/`I`,
  `rn`/`m`) silently. They are reported as findings instead.
- Report every applied change and every warning.
- Unicode text is never normalized or transliterated; only documented
  cleanup edits and input/output handling may change it.
- Dry-run mode, and no overwriting of the input file by default.

## Usage

```bash
md-ocr-clean --version
md-ocr-clean --list-rules
md-ocr-clean scan.md                         # writes scan.cleaned.md
md-ocr-clean scan.md -v                      # also list every change
md-ocr-clean scan.md --disable repeated-spaces
md-ocr-clean scan.md --only trailing-whitespace
md-ocr-clean scan.md -o out.md --force       # replace an existing out.md
md-ocr-clean scan.txt --encoding cp1250      # convert to UTF-8
md-ocr-clean scan.md --in-place --backup     # keeps scan.md.bak
md-ocr-clean scan.md --dry-run --diff        # preview changes, write nothing
md-ocr-clean scan.md --check                 # CI: exit 1 if changes are needed
md-ocr-clean scan.md --report-json report.json
md-ocr-clean scan.md -n --report-json - > report.json
md-ocr-clean scan.md --check --fail-on-warning
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | success |
| `1` | `--check` found that changes would be made, or `--fail-on-warning` found a warning or error finding |
| `2` | usage error: unknown rule ID, contradictory options, or two outputs competing for stdout |
| `3` | input/output error: unreadable or undecodable input, a path conflict (output or report path equals the input, the cleaned output, or the backup), an existing file without `--force`, a failed write, or stdout that cannot be written (for example a closed pipe) |
| `4` | rule or pipeline failure (nothing is written), or an unexpected internal error (reported in one line, without a traceback) |

A crash never exits with `1`, so `--check` in CI cannot mistake an internal
error for "changes needed". If `--diff` cannot be written to stdout (for
example `md-ocr-clean scan.md --diff | head -n 1`), the command stops with `3`
before writing anything. If `--report-json -` cannot be written, the command
exits with `3` and says whether the cleaned output was already written.

## Preview, check, diff, and reports

**Streams.** Findings, the summary, and status lines always go to stderr.
Stdout carries only machine-readable content: the diff (`--diff`) or the JSON
report (`--report-json -`), never both. The cleaned document is never printed.

**`-n` / `--dry-run`** runs every rule and prints findings and the summary,
but never writes the cleaned output, a backup, or any temporary file for
them. If you also request `--report-json PATH`, that report file is written;
it is the only file a dry run can create. Without a report path, a dry run
changes nothing on disk. A dry run still validates the input, encoding, rule
selection, option combinations, and path conflicts (for example `-o` pointing
at the input), but an already existing cleaned output is not an error because
it will not be written.

**`--check`** implies `--dry-run` and exits with `1` if any text change would
be made, `0` otherwise.

**`--diff`** prints a unified diff (3 lines of context) of the text changes to
stdout. It compares the normalized text: line endings are shown as LF, a BOM is
not shown, and encoding conversion or line-ending normalization alone never
produces a diff. Headers use the display paths (`--- scan.md`,
`+++ scan.cleaned.md`), with no timestamps or absolute paths. A last line
without a newline is marked `\ No newline at end of file`. If nothing changed,
nothing is printed on stdout and `diff: no changes` appears on stderr. It works
with or without `--dry-run`.

**`--report-json PATH`** writes the report (`schema_version` 1) as indented,
readable UTF-8 JSON with a final newline; `-` writes it to stdout. The report
path may not be the input, the cleaned output, the in-place target, or the
backup, and an existing report is only replaced with `--force`. It is written
atomically.

Order of writes in a normal run: all rules run and the report is built in
memory; both destination paths are validated; then the cleaned document is
written, then the report. If writing the report fails after the cleaned
document was written, the command exits with `3` and says that the cleaned
output was already written; the two writes are not one transaction.

**`--fail-on-warning`** exits with `1` if any finding has severity `warning`
or `error` (not `info`). In a normal run the cleaned output and report are
still written first; the exit code is decided afterwards.

## Rules

Rules run in this order. The three destructive rules are **disabled by
default** and only run when you name them with `--enable` or `--only`.

| Order | Rule | Safety | Default | What it does |
|---|---|---|---|---|
| 1 | `suspicious-characters` | detect-only | on | Reports suspicious characters. Never changes text. |
| 2 | `detect-page-numbers` | detect-only | on | Reports isolated lines that look like page numbers. Never changes text. |
| 3 | `remove-page-numbers` | destructive | **off** | Removes probable page numbers. |
| 4 | `join-hyphenated-words` | destructive | **off** | Joins words split by a hyphen at the end of a line. |
| 5 | `reconstruct-paragraphs` | destructive | **off** | Joins OCR-wrapped paragraph lines into one line. |
| 6 | `trailing-whitespace` | safe | on | Removes trailing spaces and tabs from prose, headings, list items, blockquotes, and blank lines. |
| 7 | `repeated-spaces` | safe | on | Collapses runs of two or more spaces between words in prose paragraphs to one space. |
| 8 | `excessive-blank-lines` | safe | on | Collapses three or more consecutive empty lines to one empty line. |

The detect-only rules run first, so they see the original text.

```bash
md-ocr-clean book.md -n --diff --enable remove-page-numbers,join-hyphenated-words,reconstruct-paragraphs
md-ocr-clean book.md --enable remove-page-numbers,join-hyphenated-words,reconstruct-paragraphs
```

Reviewing destructive changes first with `--dry-run --diff` or `--check` is
strongly recommended.

**suspicious-characters**

At most one finding per line and code, pointing at the first occurrence.

| Code | Severity | Reports | Checked in |
|---|---|---|---|
| `replacement-character` | warning | U+FFFD | everywhere, including protected content and inline code or URLs |
| `control-character` | warning | control characters other than tab, newline, and form feed (including a lone CR) | everywhere |
| `bidi-control` | warning | U+202A to U+202E and U+2066 to U+2069 (LRM, RLM, and ALM are not reported) | everywhere |
| `form-feed` | info | U+000C (often an OCR page break) | everywhere |
| `invisible-character` | warning | zero-width space U+200B, word joiner U+2060, U+FEFF inside the text, soft hyphen U+00AD | unprotected prose and structural lines, outside inline code, URLs, and HTML |
| `mixed-script-word` | warning | a word mixing Latin letters with Cyrillic or Greek letters (possible look-alike) | same as above |
| `digit-letter-confusion` | info | `0` or `1` between lowercase letters (`l0ve`, `fi1e`) or before three or more lowercase letters (`0rder`, `1ike`) | same as above |

The first four are *definite* checks: those characters are almost always a
problem, so they are reported even inside code, tables, and front matter. The
last three are *heuristic* checks and only look at ordinary text. They are
based on Unicode character properties, not on any language: accented Latin
letters, letters of any single script, non-breaking spaces, and combining
accents are never reported. Uppercase codes (`A4`, `H2O`, `COVID-19`), ordinals and
units (`1st`, `10km`), versions, decimals, hexadecimal values, paths, URLs,
and identifiers are not treated as digit/letter confusion. A UTF-8 BOM at the
start of the file is not part of the text and is not reported. Guesses that
need a dictionary (`rn`/`m`, `l`/`I` inside words) are not attempted.

**detect-page-numbers**

A candidate is a single line of an ordinary prose paragraph that is isolated
above and below: by the start or end of the document, by a blank line, or by a
form feed (the line before ends with a form feed, or the line itself starts
with one; likewise below). After removing that form feed and surrounding
spaces, the line must be exactly one of:

- `12` (1 to 4 digits)
- `–12–`, `— 12 —`, or `-12-` (the same dash on both sides). Note that
  `- 12 -` with spaces is Markdown list syntax, and list items are never
  treated as page numbers.
- `[12]`
- `12/340` (current page / total pages)
- `Page 12` (the English label `Page`, in any letter case, then the number);
  no other labels are recognized

List items, headings, tables, code, other protected blocks, multi-line
paragraphs, and numbers inside text are never candidates.

| Code | Severity | Meaning |
|---|---|---|
| `probable-page-number` | info | part of a consistent sequence |
| `possible-page-number` | info | any other candidate |

Sequence algorithm: candidates are taken in document order. A candidate
continues the current run if it has the same form (and, for `12/340`, the same
total) and its value is 1 to 3 higher than the previous candidate's value; the
current page is used for `12/340`. A run of at least three candidates with at
least one step of exactly 1 is *probable* (`10, 11, 12` and `10, 12, 13` are;
`10, 13, 16` and any two candidates alone are not). Bare, dashed, or bracketed
values from 1500 through 2100 are treated as years and never reported; with a
label (`Page 1998`) or as `1998/2100` they are. Page numbers are only
reported, never removed.

Both detection rules produce findings only. Info findings do not trigger
`--fail-on-warning`; warning findings do.

**trailing-whitespace**

- A Markdown hard break (trailing whitespace that ends with two or more
  spaces) is kept: only that final run of spaces stays, anything before it in
  the trailing whitespace is removed. For example `text\t  ` becomes `text  `.
- Otherwise all trailing spaces and tabs are removed: `text \t`, `text\t`, and
  `text  \t` (which is not a hard break) all become `text`.
- Leading and internal whitespace, non-breaking and other Unicode spaces, and
  whitespace inside inline code or HTML are never changed. No lines are added
  or removed.
- Whitespace after a lone carriage return is kept (removing it would turn the
  CR into a CRLF line ending), and so is a whitespace-only last line without a
  final newline (removing it would change the final-newline state).

**repeated-spaces**

- Only runs of ordinary ASCII spaces with a non-whitespace character on both
  sides are collapsed, so indentation, trailing spaces, and hard breaks are
  never touched. Tabs and non-breaking spaces are left alone.
- Only prose paragraphs are edited. Headings, list items, and blockquotes are
  left unchanged in this version.
- Nothing inside inline code, link or image destinations, autolinks, bare
  URLs, inline HTML, footnote references, or backslash escapes is changed.

**excessive-blank-lines**

- Only completely empty lines count. A line containing spaces or tabs
  interrupts a run (after `trailing-whitespace` has run, lines with only tabs
  or a single space have become empty).
- Runs of one or two empty lines are kept. Runs of three or more become one
  empty line. The same applies at the start and end of the document, so a
  final newline is always kept.
- Blank lines inside code, front matter, HTML, tables, math, definitions,
  list items, and `ocr-clean: off` regions are never touched.

**remove-page-numbers** (destructive, off by default)

Removes a page number only when all of these hold:

1. `detect-page-numbers` classifies it as **probable** (a run of at least
   three; see above). *Possible* candidates are never removed and get no extra
   finding.
2. It is a whole one-line paragraph isolated by blank lines or the document
   edges. Candidates that depend on a form feed are never removed, so the page
   break is kept.
3. Real text lies between it and its neighbors in the run.
4. The nearest content above and below is a paragraph or heading (or the
   document edge), and the next content is not indented, so removal cannot
   turn following text into a list continuation, indented code, a footnote or
   reference continuation, or a blockquote continuation.

Exactly one blank-line separator is kept: `Text.\n\n12\n\nText.` becomes
`Text.\n\nText.`, at the start, middle, or end of the document, and the
final-newline state is preserved. Each removal is reported as two changes,
`Removed probable page number 12.` and the removed separator line. A probable
candidate that fails a condition is kept and reported once as an info finding
`page-number-kept` with one of these reasons:

- `removal skipped because the candidate depends on a form-feed boundary`
- `removal skipped because no text separates it from a neighboring page number`
- `removal skipped because it could change the structure of nearby content`
- `removal skipped because the missing final newline could not be preserved safely`

The `detect-page-numbers` finding stays in the report even when the page
number is removed: the finding explains why the line was considered a page
number, and the change records its removal. This is intentional, not
duplicate reporting.

**join-hyphenated-words** (destructive, off by default)

Joins a word split across two lines of the same prose paragraph by moving the
fragment up; the rest of the second line stays where it is:

```text
infor-                 information
mation is here   ->   is here
```

If the fragment is the whole second line, the two lines become one. This is
not paragraph reconstruction: only the split word is repaired.

- A **soft hyphen** (U+00AD) at the end of a line is explicit typesetter
  hyphenation and is joined when the next line starts with two lowercase
  letters. Soft hyphens in the middle of a line are never removed. The
  `suspicious-characters` finding for the original soft hyphen remains in the
  report (detection runs first); this is intentional.
- An **ordinary hyphen** (`-`) is joined only if the prefix is two or more
  lowercase letters without digits or other hyphens, the hyphen follows it
  directly, only spaces or tabs follow the hyphen, the next line starts with
  two lowercase letters, and nothing touches inline code, URLs, or HTML.
  Because lowercase letters are required, words in scripts without letter
  case are never joined.
- En and em dashes, `word -`, uppercase, digit, or punctuation continuations,
  sentence-initial capitalized prefixes, and anything across a blank line, in
  list items, headings, blockquotes, or protected blocks are never joined.
- Joining must not create Markdown structure. Moving `mation` up out of
  `mation # Heading` would leave `# Heading` at the start of a line, turning
  prose into a heading. Before each join, the affected lines are segmented
  again as they would read afterwards; if they are no longer one ordinary
  paragraph (a heading, list or task item, blockquote, fence, thematic break,
  setext underline, table, HTML, link-reference or footnote definition, math
  block, or `ocr-clean` directive would appear), the hyphen is kept and
  reported as `ambiguous-hyphenation` with the reason `joining would create
  Markdown structure`. If the rest of the line would start with inline code, a
  URL, an autolink, inline HTML, a footnote reference, or an escape, the hyphen
  is kept with the reason `joining would move protected inline content to the
  start of a line`.

This rule uses **no dictionary** and is not linguistically accurate. A real
compound split at a line end cannot be told apart from a hyphenated word, so
these cases are left unchanged and reported as an info finding
`ambiguous-hyphenation`:

- the prefix ends in `o`, a common shape of combining forms in compounds such
  as `socio-` / `economic` or `electro-` / `magnetic`. This deliberately also
  keeps ordinary splits like `photo-` / `graph` unchanged: a known false
  negative of the dictionary-free policy;
- the prefix is one of a small, **incomplete** list of English compound
  prefixes (`well`, `self`, `non`, `co`, `ex`, `anti`, `pro`, `pre`, `post`,
  `multi`);
- the prefix itself follows another hyphen (`state-of-the-` / `art`).

Info findings from both rules never trigger `--fail-on-warning`.

**reconstruct-paragraphs** (destructive, off by default)

Joins the lines of an OCR-wrapped paragraph into one line, replacing each line
break (with the spaces or tabs around it) by a single space. In valid
CommonMark a line break inside a paragraph already renders as a space, so this
rule mainly normalizes the **source text** for plain-text use, diffs, and
downstream processing. It does not re-wrap text to a width and does not
recognize paragraphs linguistically.

The rule joins wrapped lines using a space. It is intended primarily for
prose that normally separates words with spaces. It may be unsuitable for
Chinese, Japanese, Thai and other writing systems or layouts where spaces are
not reliable word boundaries. It is opt-in and disabled by default, it is a
heuristic that decides all or nothing per paragraph, and its changes should
always be reviewed with `--dry-run --diff` before writing.

It is **all or nothing**: a paragraph is either joined completely or left
exactly as it is. It works inside one existing prose paragraph only and never
joins across blank lines, page breaks, removed page numbers, headings, lists,
blockquotes, tables, code, definitions, HTML, math, front matter, or
`ocr-clean: off` regions.

A paragraph is a candidate only if it looks like wrapped prose (fixed
thresholds in this version):

- at least two lines and no unusual control characters;
- the median length of the non-final lines (Unicode code points, without
  leading and trailing spaces and tabs) is at least **40**;
- every non-final line is at least **0.75** times that median; the last line
  may be shorter.

Paragraphs that fail this test (verse, addresses, short bibliography lines,
short heading-like lines) are left unchanged **without a finding**. A
candidate is still left unchanged, with one info finding `paragraph-kept`
naming the first reason, when any of these applies (checked in this order):

1. `form feed` anywhere in the paragraph
2. `aligned columns`: three or more spaces or a tab inside a line, outside
   inline code, URLs, and other masked spans
3. for each line break, in order:
   - `line ends with a hyphen or dash` (`-`, soft hyphen, U+2010, U+2011, en
     dash, em dash). This rule never removes or repairs a hyphen; that is
     `join-hyphenated-words`' job, which runs first. A remaining hyphen always
     blocks the paragraph, whether or not that rule is enabled.
   - `hard line break` (two or more trailing spaces, or a trailing backslash)
   - `line ends with a colon`
   - `next line starts with an enumeration or citation marker`: `(1)`, `a)`,
     `(a)`, `i.`, `iv)`, `§`, `Art.`, `art.`, `•`, `·`, `▪`, or an author start
     such as `Smith, J.` (`1)` at a line start is a Markdown list item and ends
     the paragraph anyway)
   - `next line starts with a dialogue marker`: `–`, `—`, `„`, `“`, `"`, `»`, `«`
   - `uppercase start after non-terminal punctuation`: a capital at the start of
     the next line is allowed only after `.`, `!`, `?`, or `…`
   - `inline Markdown touches the line boundary` (for example a code span or
     link that crosses the line break, or `<br>` at its end)

Each joined line break is reported as a change `Joined wrapped paragraph
line.` at its original position. Limitations: many legitimate wrapped
paragraphs are left unchanged by these conservative checks (false negatives),
and a line ending in an abbreviation such as `Dr.` or `etc.` before a capitalized line
is treated as a sentence end and joined. Review with `--dry-run --diff` or
`--check` first.

**Selecting rules:** `--enable` adds rules, then `--disable` removes rules (a
rule named in both is disabled). `--only` runs exactly the named rules and
cannot be combined with `--enable` or `--disable`. All three accept repeated
options and comma-separated lists. Rules always run in the order above, each
at most once. Unknown IDs are rejected before any file is read or written.

## Safety model

### Protected content

A conservative, line-oriented segmenter splits each document into segments.
It is **not a full CommonMark parser**. Its rule is: when classification is
uncertain, protect the content.

| Classification | Meaning | Segment kinds |
|---|---|---|
| protected | no rule may change it | YAML front matter, fenced and indented code, HTML blocks and comments, pipe tables, link-reference and footnote definitions, horizontal rules, `$$` math blocks, `ocr-clean: off` regions |
| structural | only whitespace-safe rules; never joined with neighbors | headings, list and task items, blockquotes, blank lines |
| editable | ordinary prose | paragraphs |

Protection is enforced centrally, not left to each rule. Rules only propose
edits; the pipeline rejects any edit that touches a protected segment, crosses
a segment boundary, overlaps another edit, does not match the current text,
targets a segment kind the rule is not permitted to edit, or overlaps inline
code, a link or image destination, an autolink, a bare URL, inline HTML, a
footnote reference, or a backslash escape. After each rule the document is
segmented again, and:

- every previously protected region must be unchanged and still protected;
- the block structure may change only as that rule is permitted: the
  whitespace rules may not change it at all, `excessive-blank-lines` may only
  change blank lines, `join-hyphenated-words` and `reconstruct-paragraphs` may
  not create, remove, or reclassify any heading, list item, blockquote, blank
  line, or protected block, and `remove-page-numbers` may only remove whole
  paragraphs (one per removed page number) and blank lines.

Any violation stops the run with exit code 4 and nothing is written.

- Unclosed fences, math blocks, HTML comments, raw HTML blocks, and front
  matter protect everything to the end of the document.
- List items and blockquotes containing code, HTML, tables, definitions, or
  unusually deep indentation are protected as a whole. This includes a list or
  task item whose first line is code-like, such as `-     code` or `-<tab><tab>code`
  (five or more columns after the marker), or starts with HTML, a fence, a
  definition, `$$`, or a directive.
- A line containing `|` followed by a valid-looking delimiter row (such as
  `|---|:--:|`) is protected as a table even when malformed inline syntax (an
  unmatched backtick, an escaped pipe, a pipe inside code) makes its columns
  uncertain. A single `|` in ordinary prose without a delimiter row stays prose.
- A line starting with an HTML tag that is not closed on the same line is
  protected up to the next blank line. An HTML block containing an unclosed
  `<pre>`, `<script>`, `<style>`, or `<textarea>` stays protected until that
  element is closed.
- `$$` math blocks are a pragmatic extension, not part of CommonMark.

### Escape hatch

No rule changes content between these lines, and no rule reports findings
about it (not even definite suspicious characters):

```markdown
<!-- ocr-clean: off -->
anything here is left as it is
<!-- ocr-clean: on -->
```

The region comes out character for character as it was decoded. Whole-document
operations of the input/output layer still apply to it, so it is
byte-for-byte identical only when the input is UTF-8 with consistent line
endings:

- mixed LF/CRLF input is normalized to the dominant line ending everywhere,
  including inside escaped regions (a `mixed-line-endings` warning is
  reported);
- `cp1250` and `iso-8859-2` input is converted to UTF-8 everywhere;
- a BOM is handled at the document level.

Each directive must be alone on its line (surrounding spaces or tabs are
fine). They nest: each `off` needs its own `on`. An `off` that is never
turned back on protects the rest of the document. Directives inside fenced
code, math blocks, HTML comments, or YAML front matter have no effect.

A directive behind a blockquote prefix, such as `> <!-- ocr-clean: off -->`,
is **not** a directive: it does not switch cleaning off for the rest of the
document. The blockquote containing it is protected as a whole and the text
is kept exactly. The same applies to directive text that shares its line with
other content.

## Reports

Every run prints its findings and a one-line summary to stderr; `-v` also
lists each change. Document content is never printed to stdout.

- Locations are shown as `file:line` or `file:line:column` and refer to the
  **original input**, even after earlier rules have removed lines. If a
  position falls inside text that an earlier rule replaced, only the line is
  reported, never a guessed column.
- Report paths are shown as given, relative to the current directory, or as a
  bare file name, never as an unrelated absolute path.
- **Rule findings** carry the ID of the rule that found them. **System
  findings** use reserved IDs: `document-io` for input properties (for example
  `document-io/mixed-line-endings`) and `markdown-segmenter` for structure
  diagnostics: `unclosed-fenced-code`, `unclosed-math-block`,
  `unclosed-html-comment`, `unclosed-html-block`, `unclosed-front-matter`,
  `unmatched-ocr-clean-off`, `unmatched-ocr-clean-on` (warnings), and
  `ambiguous-markdown-table`, `ambiguous-html-block` (info).
- `--report-json` writes the same information as JSON (see above).

## Input and output

- **Output location:** by default, `.cleaned` is inserted before the input
  file extension, next to the source file: `scan.md` becomes
  `scan.cleaned.md`, and `scan.txt` becomes `scan.cleaned.txt`. A file
  without an extension, such as `scan`, becomes `scan.cleaned`.
- **No accidental overwrites:** the input file is never overwritten unless
  `--in-place` is given, and `--in-place` requires an explicit `--backup`
  (saves `name.md.bak`, never overwriting an existing backup) or `--no-backup`.
  An existing output file is only replaced with `--force`.
- **Atomic writes:** output is written to a temporary file in the destination
  directory and moved into place, so a failure never leaves a partial file.
- **Encodings:** input is decoded as UTF-8 by default. `--encoding` accepts
  exactly `utf-8`, `utf-8-sig`, `cp1250`, and `iso-8859-2`. The encoding is
  never guessed; invalid bytes stop the command with the byte offset and line,
  and no output is written.
- **Output is always UTF-8.** cp1250 and iso-8859-2 input is converted to UTF-8
  without a BOM. For `utf-8` and `utf-8-sig` a leading BOM is preserved when
  present and not added when absent (the two names behave the same). A file
  containing only a BOM stays a BOM-only file.
- **Line endings:** consistent LF or CRLF input keeps its style. Mixed input is
  written with the more frequent style (LF on a tie) and a warning finding is
  reported. This applies to the whole document, including `ocr-clean: off`
  regions. A lone CR is treated as content. Whether the file ends with a
  newline is preserved; an empty file stays empty.

## Not implemented yet

- Header/footer detection or removal, character correction, Unicode
  normalization, dictionaries or word lists, Roman-numeral page numbers,
  form-feed-preserving page-number removal, hyphen joining or paragraph
  reconstruction across paragraphs, pages, list items, blockquotes, or
  footnotes, partial paragraph reconstruction, re-wrapping to a width, and
  configurable thresholds.
- Multiple input files, globs, and `--output-dir` (planned after 0.1.0).

## Installation

Requires Python 3.11 or newer; tested with Python 3.11, 3.12, and 3.13 on
Ubuntu and Windows. There are no runtime dependencies. The package is not
published on PyPI; install it from a source checkout:

```bash
git clone https://github.com/odrinpiwko-bit/markdown-ocr-cleaner.git
cd markdown-ocr-cleaner
python -m venv .venv
.venv/bin/python -m pip install .           # Windows: .venv\Scripts\python
.venv/bin/python -m pip install -e ".[dev]" # for development (tests, Ruff)
```

The tool runs entirely locally: no network access, no telemetry, and no
subprocesses.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
