import os
from pathlib import Path

import pytest

from md_ocr_cleaner import document
from md_ocr_cleaner.document import (
    BackupError,
    OutputError,
    OutputExistsError,
    SourceOverwriteError,
    WriteError,
    backup_path,
    default_output_path,
    same_file,
    write_output,
)

ORIGINAL = b"Original text.\n"
NEW = b"New text.\n"


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "in.md"
    path.write_bytes(ORIGINAL)
    return path


def leftover_temp_files(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.name.endswith(".tmp")]


# --- Path helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [("scan.md", "scan.cleaned.md"), ("scan.txt", "scan.cleaned.txt"), ("scan", "scan.cleaned")],
)
def test_default_output_path(tmp_path, name, expected):
    assert default_output_path(tmp_path / name) == tmp_path / expected


def test_backup_path(tmp_path):
    assert backup_path(tmp_path / "in.md") == tmp_path / "in.md.bak"


def test_same_file_resolves_dot_dot(source, tmp_path):
    (tmp_path / "sub").mkdir()
    assert same_file(tmp_path / "sub" / ".." / "in.md", source)


def test_same_file_for_missing_paths(tmp_path):
    assert same_file(tmp_path / "x" / ".." / "new.md", tmp_path / "new.md")
    assert not same_file(tmp_path / "a.md", tmp_path / "b.md")


# --- Normal output -----------------------------------------------------------------


def test_writes_new_output(source, tmp_path):
    out = tmp_path / "out.md"
    assert write_output(NEW, out, source=source) is None
    assert out.read_bytes() == NEW
    assert source.read_bytes() == ORIGINAL
    assert leftover_temp_files(tmp_path) == []


@pytest.mark.parametrize("force", [False, True])
def test_source_is_never_overwritten_by_default(source, force):
    with pytest.raises(SourceOverwriteError):
        write_output(NEW, source, source=source, force=force)
    assert source.read_bytes() == ORIGINAL


def test_source_reached_through_alternate_spelling_is_rejected(source, tmp_path):
    (tmp_path / "sub").mkdir()
    with pytest.raises(SourceOverwriteError):
        write_output(NEW, tmp_path / "sub" / ".." / "in.md", source=source)
    assert source.read_bytes() == ORIGINAL


def test_source_with_different_case_is_rejected_on_case_insensitive_fs(source, tmp_path):
    variant = tmp_path / "IN.MD"
    if not variant.exists():
        pytest.skip("filesystem is case-sensitive")
    with pytest.raises(SourceOverwriteError):
        write_output(NEW, variant, source=source)
    assert source.read_bytes() == ORIGINAL


def test_existing_output_is_rejected_without_force(source, tmp_path):
    out = tmp_path / "out.md"
    out.write_bytes(b"keep")
    with pytest.raises(OutputExistsError, match="--force"):
        write_output(NEW, out, source=source)
    assert out.read_bytes() == b"keep"


def test_force_replaces_existing_output(source, tmp_path):
    out = tmp_path / "out.md"
    out.write_bytes(b"old")
    write_output(NEW, out, source=source, force=True)
    assert out.read_bytes() == NEW
    assert source.read_bytes() == ORIGINAL
    assert leftover_temp_files(tmp_path) == []


def test_output_directory_is_rejected(source, tmp_path):
    out = tmp_path / "dir"
    out.mkdir()
    with pytest.raises(OutputError, match="directory"):
        write_output(NEW, out, source=source, force=True)


def test_missing_output_directory_is_rejected(source, tmp_path):
    with pytest.raises(OutputError, match="does not exist"):
        write_output(NEW, tmp_path / "missing" / "out.md", source=source)


def test_backup_argument_requires_in_place(source, tmp_path):
    with pytest.raises(ValueError):
        write_output(NEW, tmp_path / "out.md", source=source, backup=True)


# --- In-place mode -----------------------------------------------------------------


def test_in_place_without_backup_decision_is_rejected(source):
    with pytest.raises(BackupError, match="explicit backup decision"):
        write_output(NEW, source, source=source, in_place=True)
    assert source.read_bytes() == ORIGINAL


def test_in_place_requires_destination_to_be_source(source, tmp_path):
    with pytest.raises(OutputError, match="in-place"):
        write_output(NEW, tmp_path / "out.md", source=source, in_place=True, backup=False)


def test_in_place_with_backup_preserves_original(source, tmp_path):
    created = write_output(NEW, source, source=source, in_place=True, backup=True)
    assert created == tmp_path / "in.md.bak"
    assert created.read_bytes() == ORIGINAL
    assert source.read_bytes() == NEW
    assert leftover_temp_files(tmp_path) == []


def test_in_place_with_explicit_no_backup(source, tmp_path):
    assert write_output(NEW, source, source=source, in_place=True, backup=False) is None
    assert source.read_bytes() == NEW
    assert not backup_path(source).exists()


def test_existing_backup_is_not_overwritten(source):
    bak = backup_path(source)
    bak.write_bytes(b"older backup")
    with pytest.raises(BackupError, match="already exists"):
        write_output(NEW, source, source=source, in_place=True, backup=True)
    assert bak.read_bytes() == b"older backup"
    assert source.read_bytes() == ORIGINAL


# --- Failures ------------------------------------------------------------------------


def _fail(exc):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


def test_replace_failure_keeps_existing_output_and_cleans_up(source, tmp_path, monkeypatch):
    out = tmp_path / "out.md"
    out.write_bytes(b"existing")
    monkeypatch.setattr(document.os, "replace", _fail(PermissionError(13, "Permission denied")))
    with pytest.raises(WriteError, match="open in another program"):
        write_output(NEW, out, source=source, force=True)
    assert out.read_bytes() == b"existing"
    assert source.read_bytes() == ORIGINAL
    assert leftover_temp_files(tmp_path) == []


def test_write_failure_leaves_no_partial_output(source, tmp_path, monkeypatch):
    out = tmp_path / "out.md"
    monkeypatch.setattr(document.os, "fsync", _fail(OSError(28, "No space left on device")))
    with pytest.raises(WriteError, match="No space left"):
        write_output(NEW, out, source=source)
    assert not out.exists()
    assert leftover_temp_files(tmp_path) == []


def test_in_place_replace_failure_preserves_source(source, tmp_path, monkeypatch):
    monkeypatch.setattr(document.os, "replace", _fail(PermissionError(13, "Permission denied")))
    with pytest.raises(WriteError):
        write_output(NEW, source, source=source, in_place=True, backup=True)
    assert source.read_bytes() == ORIGINAL
    # The redundant backup is removed so the command can simply be retried.
    assert not backup_path(source).exists()
    assert leftover_temp_files(tmp_path) == []


def test_interrupt_during_write_cleans_up(source, tmp_path, monkeypatch):
    monkeypatch.setattr(document.os, "fsync", _fail(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        write_output(NEW, tmp_path / "out.md", source=source)
    assert not (tmp_path / "out.md").exists()
    assert leftover_temp_files(tmp_path) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_replacing_existing_output_keeps_its_permissions(source, tmp_path):
    out = tmp_path / "out.md"
    out.write_bytes(b"old")
    out.chmod(0o640)
    write_output(NEW, out, source=source, force=True)
    assert out.stat().st_mode & 0o777 == 0o640
