"""Milestone 10 audit: error-handling and structural paths found uncovered."""

import os
from pathlib import Path

import pytest

from md_ocr_cleaner import document
from md_ocr_cleaner.cli import EXIT_IO, main
from md_ocr_cleaner.document import (
    BackupError,
    InputFileError,
    WriteError,
    backup_path,
    read_document,
    write_output,
)
from md_ocr_cleaner.inline import SpanKind, mask_inline
from md_ocr_cleaner.rules.page_numbers import REASON_FINAL_NEWLINE, RemovePageNumbers
from md_ocr_cleaner.segmenter import (
    Protection,
    Segment,
    SegmentationError,
    SegmentKind,
    segment_document,
    validate_segments,
)

ORIGINAL = b"original\n"


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "in.md"
    path.write_bytes(ORIGINAL)
    return path


def leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith((".tmp", ".bak")))


# --- document.py failure paths -----------------------------------------------------------


def test_unreadable_input_is_input_error(source, monkeypatch):
    def fail(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", fail)
    with pytest.raises(InputFileError, match="cannot read input file"):
        read_document(source)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX named pipes")
def test_non_regular_input_is_rejected(tmp_path):
    fifo = tmp_path / "pipe.md"
    os.mkfifo(fifo)
    with pytest.raises(InputFileError, match="not a regular file"):
        read_document(fifo)


def test_failed_backup_write_leaves_no_partial_backup(source, tmp_path, monkeypatch):
    monkeypatch.setattr(
        document.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError(28, "No space"))
    )
    with pytest.raises(BackupError, match="cannot create backup"):
        write_output(b"new", source, source=source, in_place=True, backup=True)
    assert source.read_bytes() == ORIGINAL
    assert not backup_path(source).exists()
    assert leftovers(tmp_path) == []


def test_unreadable_source_cannot_be_backed_up(source, monkeypatch):
    real = Path.read_bytes

    def fail(self):
        if self == source:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", fail)
    with pytest.raises(BackupError, match="cannot read file to back up"):
        write_output(b"new", source, source=source, in_place=True, backup=True)
    assert not backup_path(source).exists()


def test_temp_name_collisions_never_overwrite_existing_files(source, tmp_path, monkeypatch):
    monkeypatch.setattr(document.os, "urandom", lambda n: b"\0" * n)
    squatter = tmp_path / ".out.md.00000000.tmp"
    squatter.write_bytes(b"someone else's file")
    with pytest.raises(WriteError, match="unique temporary file"):
        write_output(b"new", tmp_path / "out.md", source=source)
    assert squatter.read_bytes() == b"someone else's file"
    assert not (tmp_path / "out.md").exists()


def test_temp_file_creation_failure(source, tmp_path, monkeypatch):
    real_open = document.os.open

    def fail(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            raise PermissionError(13, "Permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(document.os, "open", fail)
    with pytest.raises(WriteError, match="cannot create temporary file"):
        write_output(b"new", tmp_path / "out.md", source=source)
    assert not (tmp_path / "out.md").exists()


def test_cli_write_failure_after_preflight_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.md").write_bytes(b"a  b\n")
    real = document.os.replace

    def replace(src, dst):
        if str(dst).endswith(".cleaned.md"):
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    monkeypatch.setattr(document.os, "replace", replace)
    assert main(["a.md", "--report-json", "r.json"]) == EXIT_IO
    assert "nothing was written" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.md"]  # no output, no report


# --- segmenter structural paths -------------------------------------------------------------


def test_validate_detects_wrong_end_line():
    segment = Segment(SegmentKind.PARAGRAPH, Protection.EDITABLE, "a\nb\n", 1, 1, 0, 4)
    with pytest.raises(SegmentationError, match="ends on the wrong line"):
        validate_segments("a\nb\n", [segment])


@pytest.mark.parametrize(
    "line", ["<pre>x</pre>", "<?php echo 1; ?>", "<![CDATA[x]]>", "<!DOCTYPE html>"]
)
def test_single_line_raw_html_blocks_close_on_their_line(line):
    kinds = [s.kind for s in segment_document(f"{line}\nafter\n").segments]
    assert kinds == [SegmentKind.HTML_BLOCK, SegmentKind.PARAGRAPH]


def test_directive_ends_footnote_definition():
    text = "[^1]: note\n<!-- ocr-clean: off -->\nx\n"
    kinds = [s.kind for s in segment_document(text).segments]
    assert kinds == [SegmentKind.FOOTNOTE_DEFINITION, SegmentKind.ESCAPED_REGION]


def test_interrupting_block_ends_blockquote():
    kinds = [s.kind for s in segment_document("> quote\n# Heading\n").segments]
    assert kinds == [SegmentKind.BLOCKQUOTE, SegmentKind.ATX_HEADING]


# --- inline destination paths -------------------------------------------------------------


def test_escaped_parenthesis_inside_destination():
    text = r"[a](x\)y) z"
    (span,) = mask_inline(text)
    assert (span.kind, text[span.start : span.end]) == (SpanKind.LINK_DESTINATION, r"(x\)y)")


def test_blank_line_ends_unbalanced_destination():
    text = "[a](x\n\nnext  word"
    (span,) = mask_inline(text)
    assert text[span.start : span.end] == "(x"


def test_unclosed_angle_destination_masks_rest_of_line():
    text = "[a](<open  dest\nnext"
    (span,) = mask_inline(text)
    assert text[span.start : span.end] == "(<open  dest"


# --- remove-page-numbers rare edge ------------------------------------------------------------


def test_page_number_before_whitespace_only_last_line_is_kept():
    from md_ocr_cleaner.document import parse_document
    from md_ocr_cleaner.pipeline import run_pipeline

    text = "A.\n\n1\n\nB.\n\n2\n\nC.\n\n3\n  "
    result = run_pipeline(parse_document(text.encode()), (RemovePageNumbers(),), input_path="t.md")
    assert result.document.text.endswith("3\n  ")
    kept = [f.message for f in result.report.findings if f.code == "page-number-kept"]
    assert kept == [f"kept probable page number 3: {REASON_FINAL_NEWLINE}"]


# --- Escaped regions and document-layer normalization -------------------------------------

ESCAPED_BODY = "kept  as\tis \n"


def test_escaped_region_follows_whole_document_newline_normalization(tmp_path, monkeypatch, capsys):
    # Two LF lines outside, CRLF inside the region: LF is dominant, so the
    # region's line endings are normalized too; its characters are untouched.
    monkeypatch.chdir(tmp_path)
    source = (
        "a  b \n\n<!-- ocr-clean: off -->\r\n"
        + ESCAPED_BODY.replace("\n", "\r\n")
        + "<!-- ocr-clean: on -->\r\n\nc  d\n\ne\n"
    ).encode()
    (tmp_path / "m.md").write_bytes(source)
    assert main(["m.md"]) == 0
    out = (tmp_path / "m.cleaned.md").read_bytes()
    region = b"<!-- ocr-clean: off -->\n" + ESCAPED_BODY.encode() + b"<!-- ocr-clean: on -->\n"
    assert region in out
    assert b"\r" not in out
    assert out.startswith(b"a b\n")
    assert "mixed-line-endings" in capsys.readouterr().err


def test_escaped_region_is_byte_identical_with_consistent_crlf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    region = ("<!-- ocr-clean: off -->\n" + ESCAPED_BODY + "<!-- ocr-clean: on -->\n").replace(
        "\n", "\r\n"
    )
    source = ("a  b\r\n\r\n" + region + "\r\nc\r\n").encode()
    (tmp_path / "c.md").write_bytes(source)
    assert main(["c.md"]) == 0
    assert region.encode() in (tmp_path / "c.cleaned.md").read_bytes()


def test_escaped_region_is_converted_with_the_document_encoding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    region = (
        "<!-- ocr-clean: off -->\n"
        + chr(0x10D)
        + "o  "
        + chr(0x161)
        + " \n<!-- ocr-clean: on -->\n"
    )
    (tmp_path / "w.md").write_bytes(("x  y\n\n" + region).encode("cp1250"))
    assert main(["w.md", "--encoding", "cp1250"]) == 0
    out = (tmp_path / "w.cleaned.md").read_bytes()
    assert out == ("x y\n\n" + region).encode("utf-8")
