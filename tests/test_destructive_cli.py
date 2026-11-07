"""Milestone 8 opt-in destructive rules through the CLI."""

import json

import pytest

from md_ocr_cleaner import cli
from md_ocr_cleaner.cli import EXIT_FINDINGS, EXIT_INTERNAL, EXIT_OK, main

DOC = (
    "The first page has infor-\nmation here.\n\n"
    "10\n\n"
    "The second page is socio-\neconomic.\n\n"
    "11\n\n"
    "The third page.\n\n"
    "12\n\n"
    "The end.\n"
)
BOTH = "remove-page-numbers,join-hyphenated-words"
CLEANED = (
    "The first page has information\nhere.\n\n"
    "The second page is socio-\neconomic.\n\n"
    "The third page.\n\n"
    "The end.\n"
)


@pytest.fixture
def doc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.md").write_bytes(DOC.encode())
    return tmp_path / "book.md"


def snapshot(directory):
    return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(directory.iterdir())}


def test_default_run_does_not_use_destructive_rules(doc, capsys):
    assert main(["book.md"]) == EXIT_OK
    assert (doc.parent / "book.cleaned.md").read_bytes() == DOC.encode()
    err = capsys.readouterr().err
    assert "remove-page-numbers" not in err
    assert "join-hyphenated-words" not in err
    assert err.count("probable-page-number") == 3  # Milestone 7 behavior unchanged


def test_enable_activates_both(doc, capsys):
    assert main(["book.md", "--enable", BOTH]) == EXIT_OK
    assert (doc.parent / "book.cleaned.md").read_bytes() == CLEANED.encode()
    err = capsys.readouterr().err
    assert "rules: suspicious-characters, detect-page-numbers, remove-page-numbers, " in err
    assert "join-hyphenated-words/ambiguous-hyphenation" in err


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (
            "remove-page-numbers",
            DOC.replace("10\n\n", "").replace("11\n\n", "").replace("12\n\n", ""),
        ),
        ("join-hyphenated-words", DOC.replace("infor-\nmation here", "information\nhere")),
    ],
)
def test_only_each_destructive_rule(doc, rule, expected):
    assert main(["book.md", "--only", rule]) == EXIT_OK
    assert (doc.parent / "book.cleaned.md").read_bytes() == expected.encode()


def test_check_detects_destructive_changes_without_writing(doc):
    before = snapshot(doc.parent)
    assert main(["book.md", "--check", "--enable", BOTH]) == EXIT_FINDINGS
    assert snapshot(doc.parent) == before
    assert main(["book.md", "--check"]) == EXIT_OK  # defaults change nothing here


def test_diff_shows_exact_changes(doc, capsys):
    assert main(["book.md", "-n", "--diff", "--enable", BOTH]) == EXIT_OK
    assert capsys.readouterr().out == (
        "--- book.md\n"
        "+++ book.cleaned.md\n"
        "@@ -1,15 +1,9 @@\n"
        "-The first page has infor-\n"
        "-mation here.\n"
        "-\n"
        "-10\n"
        "+The first page has information\n"
        "+here.\n"
        " \n"
        " The second page is socio-\n"
        " economic.\n"
        " \n"
        "-11\n"
        "-\n"
        " The third page.\n"
        " \n"
        "-12\n"
        "-\n"
        " The end.\n"
    )


def test_json_report_contains_changes_and_findings(doc, capsys):
    assert main(["book.md", "-n", "--enable", BOTH, "--report-json", "-"]) == EXIT_OK
    data = json.loads(capsys.readouterr().out)
    changes = [(c["rule_id"], c["message"], c["location"]["line"]) for c in data["changes"]]
    assert ("remove-page-numbers", "Removed probable page number 10.", 4) in changes
    assert ("remove-page-numbers", "Removed probable page number 11.", 9) in changes
    assert ("remove-page-numbers", "Removed probable page number 12.", 13) in changes
    # Exactly one join: the real split infor-/mation. The socio-/economic
    # compound must stay split (reported as ambiguous below).
    assert [c for c in changes if c[0] == "join-hyphenated-words"] == [
        ("join-hyphenated-words", "joined hyphenated word 'information'", 1)
    ]
    codes = [(f["rule_id"], f["code"], f["severity"]) for f in data["findings"]]
    assert ("join-hyphenated-words", "ambiguous-hyphenation", "info") in codes
    assert codes.count(("detect-page-numbers", "probable-page-number", "info")) == 3


def test_second_run_is_idempotent(doc, capsys):
    assert main(["book.md", "--enable", BOTH]) == EXIT_OK
    capsys.readouterr()
    assert main(["book.cleaned.md", "--check", "--enable", BOTH]) == EXIT_OK
    assert "0 changes" in capsys.readouterr().err


def test_info_findings_do_not_fail_on_warning(doc):
    assert main(["book.md", "-n", "--enable", BOTH, "--fail-on-warning"]) == EXIT_OK


def test_pipeline_failure_writes_no_output_or_report(doc, monkeypatch):
    from fakes import BoomRule

    monkeypatch.setattr(cli, "REGISTRY", (*cli.REGISTRY, BoomRule()))
    before = snapshot(doc.parent)
    argv = ["book.md", "--enable", f"{BOTH},fake-boom", "--report-json", "r.json"]
    assert main(argv) == EXIT_INTERNAL
    assert snapshot(doc.parent) == before


def test_protected_and_escaped_content_exact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    text = (
        "A.\n\n1\n\n"
        "```\ninfor-\nmation\n```\n\n"
        "B.\n\n2\n\n"
        "<!-- ocr-clean: off -->\ninfor-\nmation\n\n7\n\n<!-- ocr-clean: on -->\n\n"
        "C.\n\n3\n\nD.\n"
    )
    (tmp_path / "p.md").write_bytes(text.encode())
    assert main(["p.md", "--enable", BOTH]) == EXIT_OK
    out = (tmp_path / "p.cleaned.md").read_bytes().decode()
    assert "```\ninfor-\nmation\n```\n" in out
    assert "<!-- ocr-clean: off -->\ninfor-\nmation\n\n7\n\n<!-- ocr-clean: on -->\n" in out
