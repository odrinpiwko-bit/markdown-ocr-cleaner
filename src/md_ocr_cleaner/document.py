"""Document input and output: strict decoding, line endings, and safe writes.

This module knows nothing about Markdown, cleaning rules, or reporting. It turns
bytes into normalized text plus the metadata needed to write that text back
safely, and it writes output without ever clobbering data by accident.

Behavior summary:

- Input encodings are chosen explicitly (never guessed) and decoded strictly.
- Output is always UTF-8. A UTF-8 BOM present in UTF-8 input is preserved;
  legacy encodings (cp1250, iso-8859-2) are converted to UTF-8 without a BOM.
- Line endings are normalized to ``\\n`` internally. Consistent input keeps its
  style; mixed LF/CRLF input is written with the dominant style (LF on a tie).
  A lone ``\\r`` is not a line ending and is kept as content.
- The input file is never overwritten unless in-place mode is requested, and
  in-place mode requires an explicit backup decision.
- Writes go to a temporary file in the destination directory and are moved
  into place with ``os.replace``.
"""

from __future__ import annotations

import codecs
import contextlib
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Public CLI encoding names mapped to Python codec names. Only these are accepted.
ENCODINGS: dict[str, str] = {
    "utf-8": "utf_8",
    "utf-8-sig": "utf_8",
    "cp1250": "cp1250",
    "iso-8859-2": "iso8859_2",
}
DEFAULT_ENCODING = "utf-8"
_UTF8_ENCODINGS = frozenset({"utf-8", "utf-8-sig"})

BACKUP_SUFFIX = ".bak"
CLEANED_SUFFIX = ".cleaned"


class DocumentError(Exception):
    """Base class for all document input/output errors."""


class UnsupportedEncodingError(DocumentError):
    """The requested encoding is not one of the supported public names."""


class InputFileError(DocumentError):
    """The input file is missing, not a regular file, or unreadable."""


class DecodingError(DocumentError):
    """The input bytes are not valid in the selected encoding."""


class OutputError(DocumentError):
    """The output destination is invalid."""


class SourceOverwriteError(OutputError):
    """The output would overwrite the input file without in-place mode."""


class OutputExistsError(OutputError):
    """The output file exists and overwriting was not requested."""


class BackupError(DocumentError):
    """A backup is required but missing, or could not be created."""


class WriteError(DocumentError):
    """Writing the temporary file or replacing the destination failed."""


class LineEnding(Enum):
    NONE = "none"
    LF = "lf"
    CRLF = "crlf"
    MIXED = "mixed"


@dataclass(frozen=True)
class Document:
    text: str
    """Decoded text with every CRLF normalized to LF and without a BOM."""
    encoding: str
    """Public name of the encoding the input was decoded with."""
    has_bom: bool
    """Whether the input started with a UTF-8 BOM (written back on output)."""
    line_ending: LineEnding
    """Line-ending style detected in the input."""
    newline: str
    """Line ending used when rendering output: ``"\\n"`` or ``"\\r\\n"``."""
    lf_count: int
    crlf_count: int
    had_final_newline: bool

    @property
    def mixed_line_endings(self) -> bool:
        return self.line_ending is LineEnding.MIXED


def _codec_for(encoding: str) -> str:
    try:
        return ENCODINGS[encoding]
    except KeyError:
        choices = ", ".join(ENCODINGS)
        raise UnsupportedEncodingError(
            f"unsupported encoding {encoding!r}; choose one of: {choices}"
        ) from None


def parse_document(data: bytes, encoding: str = DEFAULT_ENCODING) -> Document:
    """Decode ``data`` strictly and detect its BOM and line endings."""
    codec = _codec_for(encoding)

    has_bom = encoding in _UTF8_ENCODINGS and data.startswith(codecs.BOM_UTF8)
    body = data[len(codecs.BOM_UTF8) :] if has_bom else data

    try:
        raw = body.decode(codec)
    except UnicodeDecodeError as exc:
        offset = exc.start + (len(codecs.BOM_UTF8) if has_bom else 0)
        line = body.count(b"\n", 0, exc.start) + 1
        raise DecodingError(
            f"cannot decode input as {encoding}: invalid byte 0x{body[exc.start]:02x} "
            f"at byte offset {offset} (line {line})"
        ) from None

    crlf_count = raw.count("\r\n")
    lf_count = raw.count("\n") - crlf_count
    if crlf_count and lf_count:
        line_ending = LineEnding.MIXED
        newline = "\r\n" if crlf_count > lf_count else "\n"
    elif crlf_count:
        line_ending, newline = LineEnding.CRLF, "\r\n"
    elif lf_count:
        line_ending, newline = LineEnding.LF, "\n"
    else:
        line_ending, newline = LineEnding.NONE, "\n"

    text = raw.replace("\r\n", "\n") if crlf_count else raw
    return Document(
        text=text,
        encoding=encoding,
        has_bom=has_bom,
        line_ending=line_ending,
        newline=newline,
        lf_count=lf_count,
        crlf_count=crlf_count,
        had_final_newline=raw.endswith("\n"),
    )


def encode_document(doc: Document, text: str | None = None) -> bytes:
    """Render ``text`` (default: ``doc.text``) as UTF-8 bytes using the document's
    line ending and BOM."""
    if text is None:
        text = doc.text
    if doc.newline != "\n":
        text = text.replace("\n", doc.newline)
    data = text.encode("utf-8")
    return codecs.BOM_UTF8 + data if doc.has_bom else data


def read_document(path: str | os.PathLike[str], encoding: str = DEFAULT_ENCODING) -> Document:
    """Read and decode a document. The file is opened read-only."""
    _codec_for(encoding)
    path = Path(path)
    if not path.exists():
        raise InputFileError(f"{path}: input file not found")
    if path.is_dir():
        raise InputFileError(f"{path}: input is a directory, not a file")
    if not path.is_file():
        raise InputFileError(f"{path}: input is not a regular file")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InputFileError(f"{path}: cannot read input file ({exc.strerror})") from None
    try:
        return parse_document(data, encoding)
    except DecodingError as exc:
        raise DecodingError(f"{path}: {exc}") from None


def default_output_path(source: str | os.PathLike[str]) -> Path:
    """Insert ``.cleaned`` before the extension, next to the source file.

    ``scan.md`` -> ``scan.cleaned.md``, ``scan.txt`` -> ``scan.cleaned.txt``,
    and ``scan`` (no extension) -> ``scan.cleaned``.
    """
    source = Path(source)
    return source.with_name(f"{source.stem}{CLEANED_SUFFIX}{source.suffix}")


def backup_path(source: str | os.PathLike[str]) -> Path:
    """``dir/name.md`` -> ``dir/name.md.bak``."""
    source = Path(source)
    return source.with_name(source.name + BACKUP_SUFFIX)


def same_file(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """Whether two paths refer to the same file, accounting for ``..``, symlinks,
    and case-insensitive filesystems where the paths exist."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(Path(a).resolve()) == os.path.normcase(Path(b).resolve())


def check_output_path(
    destination: str | os.PathLike[str],
    *,
    source: str | os.PathLike[str],
    force: bool = False,
    in_place: bool = False,
    backup: bool | None = None,
    refuse_existing: bool = True,
) -> None:
    """Validate a write to ``destination`` without touching the filesystem.

    Raises the same errors ``write_output`` would raise before writing. With
    ``refuse_existing=False`` (used by dry runs, which write nothing) an
    existing destination is not an error; logical conflicts still are.
    """
    destination = Path(destination)
    source = Path(source)
    targets_source = same_file(destination, source)

    if in_place:
        if not targets_source:
            raise OutputError(
                f"{destination}: in-place mode requires the destination to be the input file"
            )
        if backup is None:
            raise BackupError(
                f"{source}: in-place mode requires an explicit backup decision "
                "(--backup or --no-backup)"
            )
    else:
        if backup is not None:
            raise ValueError("backup is only meaningful in in-place mode")
        if targets_source:
            raise SourceOverwriteError(
                f"{destination}: refusing to overwrite the input file; use in-place mode explicitly"
            )

    if destination.is_dir():
        raise OutputError(f"{destination}: output path is a directory")
    if refuse_existing and not in_place and destination.exists() and not force:
        raise OutputExistsError(
            f"{destination}: output file already exists; use --force to replace it"
        )
    if not destination.parent.is_dir():
        raise OutputError(f"{destination.parent}: output directory does not exist")


def write_output(
    data: bytes,
    destination: str | os.PathLike[str],
    *,
    source: str | os.PathLike[str],
    force: bool = False,
    in_place: bool = False,
    backup: bool | None = None,
) -> Path | None:
    """Safely write ``data`` to ``destination``.

    - The source file is only ever replaced when ``in_place`` is true, and then
      ``backup`` must be explicitly ``True`` or ``False``.
    - An existing non-source destination is only replaced when ``force`` is true.
    - With ``backup=True`` the original is copied to ``backup_path(source)``
      first; an existing backup is never overwritten.

    Returns the backup path if one was created, otherwise ``None``.
    """
    destination = Path(destination)
    source = Path(source)
    check_output_path(destination, source=source, force=force, in_place=in_place, backup=backup)

    created_backup = _create_backup(source) if in_place and backup else None
    try:
        _atomic_write(data, destination)
    except BaseException:
        # The source is unchanged, so the backup is redundant; removing it lets
        # the user simply retry.
        if created_backup is not None:
            _remove_quietly(created_backup)
        raise
    return created_backup


def _create_backup(source: Path) -> Path:
    target = backup_path(source)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise BackupError(f"{source}: cannot read file to back up ({exc.strerror})") from None
    try:
        with open(target, "xb") as fh:
            try:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            except BaseException:
                fh.close()
                _remove_quietly(target)
                raise
    except FileExistsError:
        raise BackupError(
            f"{target}: backup file already exists; refusing to overwrite it"
        ) from None
    except OSError as exc:
        raise BackupError(f"{target}: cannot create backup ({exc.strerror})") from None
    return target


def _create_temp_file(directory: Path, name: str) -> tuple[int, Path]:
    # O_EXCL guarantees a fresh file; mode 0o666 lets the umask apply as for any
    # newly created file (tempfile.mkstemp would force 0o600).
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        candidate = directory / f".{name}.{os.urandom(4).hex()}.tmp"
        try:
            return os.open(candidate, flags, 0o666), candidate
        except FileExistsError:
            continue
    raise WriteError(f"{directory}: cannot create a unique temporary file")


def _atomic_write(data: bytes, destination: Path) -> None:
    try:
        fd, tmp = _create_temp_file(destination.parent, destination.name)
    except OSError as exc:
        raise WriteError(
            f"{destination.parent}: cannot create temporary file ({exc.strerror})"
        ) from None

    stage = "write temporary file"
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if destination.exists():
            # Keep the permission bits of the file being replaced.
            shutil.copymode(destination, tmp)
        stage = "replace"
        os.replace(tmp, destination)
    except BaseException as exc:
        _remove_quietly(tmp)
        if not isinstance(exc, OSError):
            raise
        if stage == "replace":
            raise WriteError(
                f"{destination}: cannot replace file ({exc.strerror}); "
                "it may be open in another program"
            ) from None
        raise WriteError(f"{destination}: cannot write output ({exc.strerror})") from None


def _remove_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
