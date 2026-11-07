"""Command-line interface for md-ocr-clean.

Output streams: findings, the summary, and status lines go to stderr. Stdout
carries only machine-readable content: the unified diff (``--diff``) or the
JSON report (``--report-json -``), never both, and never the cleaned document.

Order of a normal run: read, run the pipeline, print findings, serialize the
report in memory, validate the cleaned-output and report paths, print the
diff, write the cleaned document, then write the report. A dry run (also
implied by ``--check``) never writes the cleaned document or a backup; it only
writes a report file if one was explicitly requested.

If stdout cannot be written (for example ``--diff | head`` closing the pipe),
the run stops with exit code 3. A diff is printed before anything is written,
so a failed diff means nothing was written.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from md_ocr_cleaner import __version__
from md_ocr_cleaner.document import (
    BACKUP_SUFFIX,
    DEFAULT_ENCODING,
    ENCODINGS,
    DocumentError,
    OutputError,
    backup_path,
    check_output_path,
    default_output_path,
    encode_document,
    read_document,
    same_file,
    write_output,
)
from md_ocr_cleaner.pipeline import PipelineError, run_pipeline
from md_ocr_cleaner.report import Report, Severity, display_path, render_text, report_to_dict
from md_ocr_cleaner.rules import REGISTRY, Rule
from md_ocr_cleaner.rules.selection import SelectionError, parse_rule_ids, select_rules

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_IO = 3
EXIT_INTERNAL = 4

STDOUT = "-"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="md-ocr-clean",
        description=(
            "Conservatively clean Markdown and plain-text documents produced by OCR. "
            "Protected Markdown (code, tables, front matter, HTML, ...) is never "
            "changed. Use --list-rules to see the available rules."
        ),
        epilog=(
            "exit codes: 0 success; 1 --check found changes or --fail-on-warning "
            "triggered; 2 usage error; 3 input/output error (including a closed stdout); "
            "4 rule, pipeline, or internal failure"
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        metavar="INPUT",
        help="input file (Markdown or plain text)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help="output file (default: INPUT with '.cleaned' before the extension)",
    )
    parser.add_argument(
        "--encoding",
        choices=ENCODINGS,
        default=DEFAULT_ENCODING,
        help=f"input encoding (default: {DEFAULT_ENCODING}); output is always UTF-8",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow replacing an existing output or report file (never the input file)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="write the result back to INPUT; requires --backup or --no-backup",
    )
    backup = parser.add_mutually_exclusive_group()
    backup.add_argument(
        "--backup",
        dest="backup",
        action="store_const",
        const=True,
        default=None,
        help=f"with --in-place, first save the original as INPUT{BACKUP_SUFFIX}",
    )
    backup.add_argument(
        "--no-backup",
        dest="backup",
        action="store_const",
        const=False,
        help="with --in-place, do not create a backup",
    )
    modes = parser.add_argument_group("preview and checking")
    modes.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="run the rules but do not write the cleaned output or a backup",
    )
    modes.add_argument(
        "--diff",
        action="store_true",
        help="print a unified diff of the text changes to stdout",
    )
    modes.add_argument(
        "--check",
        action="store_true",
        help="like --dry-run, but exit with 1 if any change would be made",
    )
    modes.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="exit with 1 if any warning or error finding is reported",
    )
    modes.add_argument(
        "--report-json",
        metavar="PATH",
        help="write the report as JSON to PATH, or to stdout with '-'",
    )
    rules = parser.add_argument_group("rule selection")
    rules.add_argument(
        "--enable",
        action="append",
        default=[],
        metavar="RULE[,RULE]",
        help="enable rules in addition to the defaults (repeatable)",
    )
    rules.add_argument(
        "--disable",
        action="append",
        default=[],
        metavar="RULE[,RULE]",
        help="disable rules; applied after --enable (repeatable)",
    )
    rules.add_argument(
        "--only",
        action="append",
        metavar="RULE[,RULE]",
        help="run exactly these rules; cannot be combined with --enable/--disable",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also list every individual change",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="list available rules and exit",
    )
    return parser


def format_rule_table(registry: Sequence[Rule]) -> str:
    if not registry:
        return "No rules are implemented yet."
    headers = ("RULE", "SAFETY", "DEFAULT", "DESCRIPTION")
    rows = [
        (rule.id, rule.safety.value, "on" if rule.default_enabled else "off", rule.description)
        for rule in registry
    ]
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(3)]
    return "\n".join(
        f"{a:<{widths[0]}}  {b:<{widths[1]}}  {c:<{widths[2]}}  {d}".rstrip()
        for a, b, c, d in [headers, *rows]
    )


def unified_diff(before: str, after: str, from_name: str, to_name: str) -> str:
    """Deterministic unified diff of two normalized (LF) texts; empty if equal.

    Lines are split on ``\\n`` only. A last line without a newline is marked
    with ``\\ No newline at end of file``, so the diff always ends with a newline.
    """
    if before == after:
        return ""

    def lines(text: str) -> list[str]:
        parts = text.split("\n")
        result = [part + "\n" for part in parts[:-1]]
        if parts[-1]:
            result.append(parts[-1])  # last line without a newline
        return result

    out = []
    for line in difflib.unified_diff(
        lines(before), lines(after), fromfile=from_name, tofile=to_name, n=3
    ):
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n\\ No newline at end of file\n")
    return "".join(out)


def report_json(report: Report) -> str:
    return json.dumps(report_to_dict(report), ensure_ascii=False, indent=2) + "\n"


def _configure_streams() -> None:
    # Never crash on a console that cannot display file names or text.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="backslashreplace")


class StdoutError(Exception):
    """Writing to stdout failed (for example, the reading end of a pipe was closed)."""


def _silence_stdout() -> None:
    """Send further stdout output to the null device.

    After a broken pipe, the interpreter would otherwise fail again while
    flushing stdout at exit and print a traceback.
    """
    with contextlib.suppress(OSError, ValueError, AttributeError):
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)


def _write_stdout(text: str) -> None:
    """Write text to stdout as UTF-8, whatever the console encoding."""
    try:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is None:
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            sys.stdout.flush()
            buffer.write(text.encode("utf-8"))
            buffer.flush()
    except OSError as exc:
        _silence_stdout()
        raise StdoutError(exc.strerror or str(exc) or type(exc).__name__) from exc


def _status(message: str) -> None:
    print(message, file=sys.stderr)


def _error(message: str) -> None:
    print(f"md-ocr-clean: error: {message}", file=sys.stderr)


def _validate(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[Rule, ...]:
    if args.input is None:
        parser.error("an INPUT file is required")
    if args.in_place:
        if args.output is not None:
            parser.error("--in-place cannot be combined with -o/--output")
        if args.force:
            parser.error("--force cannot be combined with --in-place")
        if args.backup is None:
            parser.error("--in-place requires an explicit --backup or --no-backup")
    elif args.backup is not None:
        parser.error("--backup and --no-backup are only valid with --in-place")
    if args.diff and args.report_json == STDOUT:
        parser.error("--diff and --report-json - cannot both write to stdout")
    if args.report_json == "":
        parser.error("--report-json needs a path or '-'")
    try:
        return select_rules(
            REGISTRY,
            enable=parse_rule_ids(args.enable),
            disable=parse_rule_ids(args.disable),
            only=parse_rule_ids(args.only) if args.only is not None else None,
        )
    except SelectionError as exc:
        parser.error(str(exc))


def _check_report_path(args: argparse.Namespace, report_path: Path, destination: Path) -> None:
    source: Path = args.input
    conflicts = [(source, "the input file"), (destination, "the cleaned output")]
    if args.in_place and args.backup:
        conflicts.append((backup_path(source), "the backup file"))
    for other, name in conflicts:
        if same_file(report_path, other):
            raise OutputError(f"{report_path}: report path is {name}")
    check_output_path(report_path, source=source, force=args.force)


def _process(args: argparse.Namespace, rules: tuple[Rule, ...]) -> int:
    source: Path = args.input
    destination = source if args.in_place else args.output or default_output_path(source)
    dry_run = args.dry_run or args.check
    report_path = Path(args.report_json) if args.report_json not in (None, STDOUT) else None

    try:
        doc = read_document(source, args.encoding)
        result = run_pipeline(doc, rules, input_path=display_path(source))
    except DocumentError as exc:
        print(f"md-ocr-clean: error: {exc}", file=sys.stderr)
        return EXIT_IO
    except PipelineError as exc:
        print(f"md-ocr-clean: internal error: {exc}; no output was written", file=sys.stderr)
        return EXIT_INTERNAL

    report = result.report
    print(render_text(report, verbose=args.verbose), file=sys.stderr)
    serialized = report_json(report) if args.report_json is not None else None

    try:
        check_output_path(
            destination,
            source=source,
            force=args.force,
            in_place=args.in_place,
            backup=args.backup if args.in_place else None,
            refuse_existing=not dry_run,
        )
        if report_path is not None:
            _check_report_path(args, report_path, destination)
    except DocumentError as exc:
        print(f"md-ocr-clean: error: {exc}; nothing was written", file=sys.stderr)
        return EXIT_IO

    changed = result.document.text != doc.text
    if args.diff:
        diff = unified_diff(
            doc.text, result.document.text, display_path(source), display_path(destination)
        )
        if diff:
            try:
                _write_stdout(diff)
            except StdoutError as exc:
                _error(f"cannot write the diff to stdout: {exc}; nothing was written")
                return EXIT_IO
        else:
            _status("diff: no changes")

    if dry_run:
        mode = "check" if args.check else "dry run"
        if changed:
            count = len(report.changes)
            _status(f"{mode}: {count} change(s) would be made; cleaned output not written")
        else:
            _status(f"{mode}: no changes needed; cleaned output not written")
    else:
        try:
            created_backup = write_output(
                encode_document(result.document),
                destination,
                source=source,
                force=args.force,
                in_place=args.in_place,
                backup=args.backup if args.in_place else None,
            )
        except DocumentError as exc:
            print(f"md-ocr-clean: error: {exc}; nothing was written", file=sys.stderr)
            return EXIT_IO
        if created_backup is not None:
            _status(f"Backup: {created_backup}")
        _status(f"Wrote {destination}")

    if serialized is not None:
        if report_path is None:
            try:
                _write_stdout(serialized)
            except StdoutError as exc:
                note = (
                    "" if dry_run else f"; the cleaned output was already written to {destination}"
                )
                _error(f"cannot write the report to stdout: {exc}{note}")
                return EXIT_IO
            _status("Report: written to stdout")
        else:
            try:
                write_output(
                    serialized.encode("utf-8"), report_path, source=source, force=args.force
                )
            except DocumentError as exc:
                note = (
                    "" if dry_run else f"; the cleaned output was already written to {destination}"
                )
                print(f"md-ocr-clean: error: report not written: {exc}{note}", file=sys.stderr)
                return EXIT_IO
            _status(f"Report: {report_path}")

    exit_code = EXIT_OK
    if args.check and changed:
        exit_code = EXIT_FINDINGS
    if args.fail_on_warning:
        serious = [f for f in report.findings if f.severity in (Severity.WARNING, Severity.ERROR)]
        if serious:
            _status(f"fail-on-warning: {len(serious)} warning or error finding(s)")
            exit_code = EXIT_FINDINGS
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command and return its exit code.

    Usage errors exit with 2 through argparse (``SystemExit``). Any other
    unexpected exception becomes exit code 4 with a one-line message instead
    of a traceback, so it can never be mistaken for exit code 1 (findings). A
    closed stdout pipe is an output error (3). ``KeyboardInterrupt`` and other
    ``BaseException`` subclasses are not caught.
    """
    try:
        _configure_streams()
        parser = build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit:
            # --help and --version print to stdout and exit; argparse ignores
            # write errors, so check that the output actually got out.
            _write_stdout("")
            raise

        if args.list_rules:
            _write_stdout(format_rule_table(REGISTRY) + "\n")
            return EXIT_OK

        rules = _validate(parser, args)
        return _process(args, rules)
    except (StdoutError, BrokenPipeError) as exc:  # BrokenPipeError: any other stdout write
        _silence_stdout()
        _error(f"cannot write to stdout: {exc}")
        return EXIT_IO
    except Exception as exc:
        print(f"md-ocr-clean: internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL
