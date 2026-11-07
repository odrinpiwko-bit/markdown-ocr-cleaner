import codecs

import pytest

from md_ocr_cleaner.document import (
    DecodingError,
    InputFileError,
    LineEnding,
    UnsupportedEncodingError,
    encode_document,
    parse_document,
    read_document,
)

BOM = codecs.BOM_UTF8
UNICODE_TEXT = "Café au lait, Größe, Łódź, Tőkés.\náäčďéěíľňóôřšťúůýžąęłńśźżőűăâîşţ ŁŐŘ\n"


# --- Encodings ---------------------------------------------------------------


def test_empty_utf8_file():
    doc = parse_document(b"", "utf-8")
    assert doc.text == ""
    assert not doc.has_bom
    assert doc.line_ending is LineEnding.NONE
    assert not doc.had_final_newline
    assert encode_document(doc) == b""


def test_utf8_ascii_round_trip():
    data = b"# Title\n\nPlain ASCII text.\n"
    doc = parse_document(data, "utf-8")
    assert doc.text == data.decode("ascii")
    assert encode_document(doc) == data


def test_utf8_unicode_round_trip():
    data = UNICODE_TEXT.encode("utf-8")
    doc = parse_document(data, "utf-8")
    assert doc.text == UNICODE_TEXT
    assert not doc.has_bom
    assert encode_document(doc) == data


def test_default_encoding_is_utf8():
    assert parse_document(UNICODE_TEXT.encode("utf-8")).encoding == "utf-8"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_utf8_bom_is_hidden_from_text_and_preserved(encoding):
    data = BOM + UNICODE_TEXT.encode("utf-8")
    doc = parse_document(data, encoding)
    assert doc.has_bom
    assert "\ufeff" not in doc.text
    assert doc.text == UNICODE_TEXT
    assert encode_document(doc) == data


def test_utf8_sig_without_bom_writes_no_bom():
    data = UNICODE_TEXT.encode("utf-8")
    doc = parse_document(data, "utf-8-sig")
    assert not doc.has_bom
    assert encode_document(doc) == data


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_bom_only_file_stays_bom_only(encoding):
    doc = parse_document(BOM, encoding)
    assert doc.has_bom
    assert doc.text == ""
    assert doc.line_ending is LineEnding.NONE
    assert encode_document(doc) == BOM


def test_only_first_bom_is_stripped():
    data = BOM + "a\ufeffb".encode()
    doc = parse_document(data, "utf-8")
    assert doc.text == "a\ufeffb"
    assert encode_document(doc) == data


@pytest.mark.parametrize("encoding", ["cp1250", "iso-8859-2"])
def test_legacy_encoding_is_converted_to_utf8_without_bom(encoding):
    data = UNICODE_TEXT.encode(encoding)
    assert data != UNICODE_TEXT.encode("utf-8")
    doc = parse_document(data, encoding)
    assert doc.text == UNICODE_TEXT
    assert not doc.has_bom
    out = encode_document(doc)
    assert out == UNICODE_TEXT.encode("utf-8")
    assert not out.startswith(BOM)


@pytest.mark.parametrize("encoding", ["cp1250", "iso-8859-2"])
def test_legacy_encodings_do_not_detect_bom(encoding):
    data = BOM + b"x\n"
    doc = parse_document(data, encoding)
    assert not doc.has_bom
    assert doc.text == data.decode(encoding)


def test_invalid_utf8_reports_offset_and_line():
    with pytest.raises(DecodingError, match=r"0xff at byte offset 6 \(line 2\)"):
        parse_document(b"abc\nde\xff\n", "utf-8")


def test_invalid_utf8_after_bom_reports_file_offset():
    with pytest.raises(DecodingError, match=r"byte offset 4"):
        parse_document(BOM + b"a\xff", "utf-8-sig")


def test_invalid_cp1250_byte_is_rejected():
    # 0x81 is undefined in cp1250.
    with pytest.raises(DecodingError, match="cp1250"):
        parse_document(b"ok \x81", "cp1250")


def test_cp1250_text_is_not_valid_utf8():
    with pytest.raises(DecodingError):
        parse_document(UNICODE_TEXT.encode("cp1250"), "utf-8")


@pytest.mark.parametrize("encoding", ["latin-1", "utf8", "UTF-8", "ascii", "cp1252", ""])
def test_unsupported_encoding_is_rejected(encoding):
    with pytest.raises(UnsupportedEncodingError, match="choose one of"):
        parse_document(b"x", encoding)


# --- Reading files -------------------------------------------------------------


def test_read_document_from_file(tmp_path):
    path = tmp_path / "in.md"
    path.write_bytes(UNICODE_TEXT.encode("utf-8"))
    assert read_document(path).text == UNICODE_TEXT


def test_read_document_does_not_modify_input(tmp_path):
    path = tmp_path / "in.md"
    data = UNICODE_TEXT.encode("cp1250")
    path.write_bytes(data)
    read_document(path, "cp1250")
    assert path.read_bytes() == data


def test_read_missing_file(tmp_path):
    with pytest.raises(InputFileError, match="not found"):
        read_document(tmp_path / "missing.md")


def test_read_directory(tmp_path):
    with pytest.raises(InputFileError, match="directory"):
        read_document(tmp_path)


def test_read_invalid_utf8_names_file(tmp_path):
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff")
    with pytest.raises(DecodingError, match=r"bad\.md"):
        read_document(path)


def test_read_rejects_unsupported_encoding_before_reading(tmp_path):
    with pytest.raises(UnsupportedEncodingError):
        read_document(tmp_path / "missing.md", "latin-1")


# --- Line endings ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "line_ending", "newline"),
    [
        (b"a\nb\n", LineEnding.LF, "\n"),
        (b"a\r\nb\r\n", LineEnding.CRLF, "\r\n"),
        (b"a\r\nb\nc\n", LineEnding.MIXED, "\n"),
        (b"a\r\nb\r\nc\n", LineEnding.MIXED, "\r\n"),
        (b"a\r\nb\n", LineEnding.MIXED, "\n"),  # tie -> LF
        (b"no newline", LineEnding.NONE, "\n"),
        (b"", LineEnding.NONE, "\n"),
    ],
)
def test_line_ending_detection(data, line_ending, newline):
    doc = parse_document(data, "utf-8")
    assert doc.line_ending is line_ending
    assert doc.newline == newline
    assert doc.mixed_line_endings == (line_ending is LineEnding.MIXED)
    assert "\r\n" not in doc.text


def test_crlf_is_not_counted_as_cr_plus_lf():
    doc = parse_document(b"a\r\nb\r\nc\n", "utf-8")
    assert doc.crlf_count == 2
    assert doc.lf_count == 1


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"a\r\nb\nc\n", b"a\nb\nc\n"),
        (b"a\r\nb\r\nc\n", b"a\r\nb\r\nc\r\n"),
        (b"a\r\nb\n", b"a\nb\n"),
    ],
)
def test_mixed_line_endings_normalized_to_dominant(data, expected):
    assert encode_document(parse_document(data, "utf-8")) == expected


@pytest.mark.parametrize(
    ("data", "final"),
    [
        (b"a\nb\n", True),
        (b"a\nb", False),
        (b"a\r\nb\r\n", True),
        (b"a\r\nb", False),
    ],
)
def test_final_newline_state_is_preserved(data, final):
    doc = parse_document(data, "utf-8")
    assert doc.had_final_newline is final
    assert encode_document(doc) == data


@pytest.mark.parametrize(
    ("data", "line_ending"),
    [(b"\n", LineEnding.LF), (b"\r\n", LineEnding.CRLF)],
)
def test_file_with_only_a_newline(data, line_ending):
    doc = parse_document(data, "utf-8")
    assert doc.text == "\n"
    assert doc.line_ending is line_ending
    assert doc.had_final_newline
    assert encode_document(doc) == data


def test_lone_cr_is_content_not_a_line_ending():
    data = b"a\rb\n"
    doc = parse_document(data, "utf-8")
    assert doc.line_ending is LineEnding.LF
    assert encode_document(doc) == data


@pytest.mark.parametrize(
    "data",
    [
        b"a\nb\n",
        b"a\r\nb\r\n",
        b"a\r\n\r\n\r\nb",
        UNICODE_TEXT.encode("utf-8"),
        UNICODE_TEXT.replace("\n", "\r\n").encode("utf-8"),
        BOM + UNICODE_TEXT.replace("\n", "\r\n").encode("utf-8"),
        b"\n\n\n",
        b"  trailing spaces  \n\t\n",
    ],
)
def test_consistent_input_round_trips_exactly(data):
    assert encode_document(parse_document(data, "utf-8")) == data


def test_encode_document_with_replacement_text_uses_document_style():
    doc = parse_document(BOM + b"a\r\n", "utf-8")
    assert encode_document(doc, "x\ny\n") == BOM + b"x\r\ny\r\n"
