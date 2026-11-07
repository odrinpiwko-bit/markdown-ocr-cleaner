import codecs
import subprocess
import sys

import pytest

from md_ocr_cleaner import __version__, cli
from md_ocr_cleaner.cli import EXIT_INTERNAL, EXIT_IO, EXIT_OK, main

UNICODE_TEXT = "Café au lait, Größe, Łódź, Tőkés.\náäčďéěíľňóôřšťúůýžąęłńśźżőűăâîşţ ŁŐŘ\n"


def test_version_option(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"md-ocr-clean {__version__}"


def test_list_rules_production_registry(capsys):
    assert main(["--list-rules"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[:3] for line in lines[1:]] == [
        ["suspicious-characters", "detect-only", "on"],
        ["detect-page-numbers", "detect-only", "on"],
        ["remove-page-numbers", "destructive", "off"],
        ["join-hyphenated-words", "destructive", "off"],
        ["reconstruct-paragraphs", "destructive", "off"],
        ["trailing-whitespace", "safe", "on"],
        ["repeated-spaces", "safe", "on"],
        ["excessive-blank-lines", "safe", "on"],
    ]


def test_list_rules_with_empty_registry(monkeypatch, capsys):
    monkeypatch.setattr(cli, "REGISTRY", ())
    assert main(["--list-rules"]) == EXIT_OK
    assert "No rules are implemented yet." in capsys.readouterr().out


def usage_error(argv, capsys) -> str:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


def test_no_arguments_is_usage_error(capsys):
    assert "INPUT file is required" in usage_error([], capsys)


def test_unknown_option_is_usage_error(capsys):
    usage_error(["--no-such-option"], capsys)


def test_python_dash_m_version():
    result = subprocess.run(
        [sys.executable, "-m", "md_ocr_cleaner", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f"md-ocr-clean {__version__}"


# --- Round trip ---------------------------------------------------------------------


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "scan.md"
    path.write_bytes(UNICODE_TEXT.encode("utf-8"))
    return path


def test_default_output_is_cleaned_file_next_to_source(source, tmp_path, capsys):
    assert main([str(source)]) == EXIT_OK
    out = tmp_path / "scan.cleaned.md"
    assert out.read_bytes() == UNICODE_TEXT.encode("utf-8")
    assert source.read_bytes() == UNICODE_TEXT.encode("utf-8")
    captured = capsys.readouterr()
    assert captured.out == ""  # document content is never printed
    assert "Wrote" in captured.err


@pytest.mark.parametrize(
    ("name", "expected"),
    [("scan.md", "scan.cleaned.md"), ("scan.txt", "scan.cleaned.txt"), ("scan", "scan.cleaned")],
)
def test_default_output_inserts_cleaned_before_the_extension(tmp_path, name, expected):
    src = tmp_path / name
    src.write_bytes(b"a  b\n")
    assert main([str(src)]) == EXIT_OK
    assert (tmp_path / expected).read_bytes() == b"a b\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([name, expected])


def test_explicit_output(source, tmp_path):
    out = tmp_path / "result.md"
    assert main([str(source), "-o", str(out)]) == EXIT_OK
    assert out.read_bytes() == source.read_bytes()


def test_bom_is_preserved(tmp_path):
    src = tmp_path / "bom.md"
    src.write_bytes(codecs.BOM_UTF8 + b"x\r\n")
    assert main([str(src), "--encoding", "utf-8-sig"]) == EXIT_OK
    assert (tmp_path / "bom.cleaned.md").read_bytes() == codecs.BOM_UTF8 + b"x\r\n"


@pytest.mark.parametrize("encoding", ["cp1250", "iso-8859-2"])
def test_legacy_encoding_is_converted_to_utf8(tmp_path, encoding):
    src = tmp_path / "legacy.md"
    data = UNICODE_TEXT.encode(encoding)
    src.write_bytes(data)
    assert main([str(src), "--encoding", encoding]) == EXIT_OK
    assert (tmp_path / "legacy.cleaned.md").read_bytes() == UNICODE_TEXT.encode("utf-8")
    assert src.read_bytes() == data


def test_mixed_line_endings_warn_exactly_once(tmp_path, capsys):
    src = tmp_path / "mixed.md"
    src.write_bytes(b"a\r\nb\nc\n")
    assert main([str(src)]) == EXIT_OK
    err = capsys.readouterr().err
    assert err.count("mixed line endings") == 1
    assert (
        "warning: mixed line endings (2 LF, 1 CRLF); output normalized to LF "
        "[document-io/mixed-line-endings]"
    ) in err
    assert (tmp_path / "mixed.cleaned.md").read_bytes() == b"a\nb\nc\n"


def test_consistent_input_has_no_warning(source, capsys):
    assert main([str(source)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "warning:" not in err
    assert "0 changes, 0 findings" in err


def test_report_does_not_expose_absolute_paths_outside_cwd(source, tmp_path, monkeypatch, capsys):
    other = tmp_path / "cwd"
    other.mkdir()
    monkeypatch.chdir(other)
    assert main([str(source)]) == EXIT_OK
    summary = capsys.readouterr().err.splitlines()[0]
    assert summary.startswith("scan.md: 0 changes")


# --- Rules ----------------------------------------------------------------------------


def test_list_rules_with_isolated_registry(monkeypatch, capsys):
    from fakes import registry

    monkeypatch.setattr(cli, "REGISTRY", registry())
    assert main(["--list-rules"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["RULE", "SAFETY", "DEFAULT", "DESCRIPTION"]
    assert lines[1].split()[:3] == ["fake-replace", "safe", "on"]
    assert lines[2].split()[:3] == ["fake-upper", "destructive", "off"]
    assert lines[3].split()[:3] == ["fake-detect", "detect-only", "on"]
    assert lines[1].endswith("Replace a with b.")


@pytest.mark.parametrize(
    "extra",
    [
        ["--enable", "nope"],
        ["--disable", "nope"],
        ["--only", "nope"],
        ["--enable", "a,,b"],
        ["--only", "x", "--enable", "y"],
    ],
)
def test_rule_selection_errors_exit_2_before_writing(source, tmp_path, capsys, extra):
    err = usage_error([str(source), *extra], capsys)
    assert "md-ocr-clean: error:" in err
    assert not (tmp_path / "scan.cleaned.md").exists()


def test_unknown_rule_message_lists_available_rules(source, capsys):
    err = usage_error([str(source), "--enable", "x"], capsys)
    assert (
        "available rules: suspicious-characters, detect-page-numbers, remove-page-numbers, "
        "join-hyphenated-words, reconstruct-paragraphs, trailing-whitespace, "
        "repeated-spaces, excessive-blank-lines"
    ) in err


def test_unknown_rule_message_with_empty_registry(source, monkeypatch, capsys):
    monkeypatch.setattr(cli, "REGISTRY", ())
    assert "no rules are implemented" in usage_error([str(source), "--enable", "x"], capsys)


@pytest.mark.parametrize("flag", ["--enable", "--disable"])
def test_only_conflict_exit_2(source, capsys, monkeypatch, flag):
    from fakes import registry

    monkeypatch.setattr(cli, "REGISTRY", registry())
    err = usage_error([str(source), "--only", "fake-replace", flag, "fake-upper"], capsys)
    assert "--only cannot be combined" in err


def test_selected_rules_run_in_registry_order(tmp_path, monkeypatch, capsys):
    from fakes import registry

    src = tmp_path / "scan.md"
    src.write_bytes(b"banana\n")
    monkeypatch.setattr(cli, "REGISTRY", registry())
    argv = [str(src), "--only", "fake-upper", "--only", "fake-replace,fake-upper", "-v"]
    assert main(argv) == EXIT_OK
    # Replace ran first (a -> b), then upper, each once.
    assert (tmp_path / "scan.cleaned.md").read_bytes() == b"BBNBNB\n"
    err = capsys.readouterr().err
    assert "rules: fake-replace, fake-upper" in err
    assert "change: a to b [fake-replace]" in err


# --- Production rules through the CLI ---------------------------------------------------

MESSY = "---\ntitle:  x \n---\n\nText  with   spaces. \n\n\n\n\n```\ncode   \n```\nEnd\t\n"
CLEAN = "---\ntitle:  x \n---\n\nText with spaces.\n\n```\ncode   \n```\nEnd\n"


@pytest.fixture
def messy(tmp_path):
    path = tmp_path / "messy.md"
    path.write_bytes(MESSY.encode())
    return path


def output_of(path):
    return path.with_name(path.stem + ".cleaned.md").read_bytes().decode()


def test_default_invocation_cleans_copy(messy, capsys):
    assert main([str(messy)]) == EXIT_OK
    assert output_of(messy) == CLEAN
    assert messy.read_bytes() == MESSY.encode()  # source untouched
    err = capsys.readouterr().err
    assert "change:" not in err  # details only with -v
    assert err.count("5 changes, 0 findings") == 1  # 2 trailing, 2 repeated, 1 blank run


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (
            "trailing-whitespace",
            "---\ntitle:  x \n---\n\nText  with   spaces.\n\n\n\n\n```\ncode   \n```\nEnd\n",
        ),
        (
            "repeated-spaces",
            "---\ntitle:  x \n---\n\nText with spaces. \n\n\n\n\n```\ncode   \n```\nEnd\t\n",
        ),
        (
            "excessive-blank-lines",
            "---\ntitle:  x \n---\n\nText  with   spaces. \n\n```\ncode   \n```\nEnd\t\n",
        ),
    ],
)
def test_only_each_rule(messy, rule, expected):
    assert main([str(messy), "--only", rule]) == EXIT_OK
    assert output_of(messy) == expected


@pytest.mark.parametrize(
    "rule", ["trailing-whitespace", "repeated-spaces", "excessive-blank-lines"]
)
def test_disable_each_rule(messy, capsys, rule):
    assert main([str(messy), "--disable", rule]) == EXIT_OK
    others = [r.id for r in cli.REGISTRY if r.default_enabled]
    others.remove(rule)
    assert f"rules: {', '.join(others)}" in capsys.readouterr().err


def test_enable_default_rule_is_harmless(messy):
    assert main([str(messy), "--enable", "repeated-spaces"]) == EXIT_OK
    assert output_of(messy) == CLEAN


def test_verbose_shows_changes(messy, capsys):
    assert main([str(messy), "-v"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "messy.md:5:5: change: collapsed repeated spaces [repeated-spaces]" in err
    assert "messy.md:6:1: change: collapsed 4 empty lines to one [excessive-blank-lines]" in err


def test_crlf_and_bom_preserved_while_cleaning(tmp_path):
    src = tmp_path / "w.md"
    src.write_bytes(codecs.BOM_UTF8 + b"a  b \r\n\r\n\r\n\r\n\r\nc\r\n")
    assert main([str(src)]) == EXIT_OK
    assert (tmp_path / "w.cleaned.md").read_bytes() == codecs.BOM_UTF8 + b"a b\r\n\r\nc\r\n"


def test_legacy_encoding_cleaned_and_converted(tmp_path):
    src = tmp_path / "l.md"
    src.write_bytes("Größe  Łódź \n".encode("cp1250"))
    assert main([str(src), "--encoding", "cp1250"]) == EXIT_OK
    assert (tmp_path / "l.cleaned.md").read_bytes() == "Größe Łódź\n".encode()


def test_protected_blocks_preserved_exactly_in_output(tmp_path):
    from test_pipeline import PROTECTED_DOCUMENTS

    text = "\n\n".join(PROTECTED_DOCUMENTS[k] for k in PROTECTED_DOCUMENTS if k != "front-matter")
    src = tmp_path / "p.md"
    src.write_bytes(text.encode())
    assert main([str(src)]) == EXIT_OK
    out = (tmp_path / "p.cleaned.md").read_bytes().decode()
    for name, block in PROTECTED_DOCUMENTS.items():
        if name != "front-matter":
            assert block in out


def test_failing_rule_writes_nothing(source, tmp_path, monkeypatch, capsys):
    from fakes import BoomRule

    monkeypatch.setattr(cli, "REGISTRY", (BoomRule(),))
    assert main([str(source), "--enable", "fake-boom"]) == EXIT_INTERNAL
    assert "no output was written" in capsys.readouterr().err
    assert not (tmp_path / "scan.cleaned.md").exists()


# --- Errors -------------------------------------------------------------------------


def test_invalid_utf8_fails_without_output(tmp_path, capsys):
    src = tmp_path / "bad.md"
    src.write_bytes(b"ok\n\xff\n")
    assert main([str(src)]) == EXIT_IO
    assert "cannot decode input as utf-8" in capsys.readouterr().err
    assert not (tmp_path / "bad.cleaned.md").exists()


def test_missing_input(tmp_path, capsys):
    assert main([str(tmp_path / "missing.md")]) == EXIT_IO
    assert "not found" in capsys.readouterr().err


def test_directory_input(tmp_path, capsys):
    assert main([str(tmp_path)]) == EXIT_IO
    assert "directory" in capsys.readouterr().err


def test_unsupported_encoding(source, capsys):
    assert "invalid choice" in usage_error([str(source), "--encoding", "latin-1"], capsys)


def test_output_equal_to_input_is_rejected(source, capsys):
    assert main([str(source), "-o", str(source)]) == EXIT_IO
    assert "refusing to overwrite the input file" in capsys.readouterr().err
    assert source.read_bytes() == UNICODE_TEXT.encode("utf-8")


def test_existing_output_requires_force(source, tmp_path):
    out = tmp_path / "scan.cleaned.md"
    out.write_bytes(b"keep")
    assert main([str(source)]) == EXIT_IO
    assert out.read_bytes() == b"keep"
    assert main([str(source), "--force"]) == EXIT_OK
    assert out.read_bytes() == source.read_bytes()


# --- In-place -------------------------------------------------------------------------


def test_in_place_requires_backup_decision(source, capsys):
    assert "--backup or --no-backup" in usage_error([str(source), "--in-place"], capsys)


def test_in_place_with_backup(source, tmp_path):
    assert main([str(source), "--in-place", "--backup"]) == EXIT_OK
    assert (tmp_path / "scan.md.bak").read_bytes() == UNICODE_TEXT.encode("utf-8")
    assert source.read_bytes() == UNICODE_TEXT.encode("utf-8")


def test_in_place_with_no_backup(source, tmp_path):
    assert main([str(source), "--in-place", "--no-backup"]) == EXIT_OK
    assert not (tmp_path / "scan.md.bak").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--backup"],
        ["--no-backup"],
        ["--in-place", "--backup", "-o", "x.md"],
        ["--in-place", "--backup", "--force"],
        ["--in-place", "--backup", "--no-backup"],
    ],
)
def test_invalid_option_combinations(source, capsys, extra):
    usage_error([str(source), *extra], capsys)
