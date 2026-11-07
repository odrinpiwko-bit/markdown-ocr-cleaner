"""Milestone 7 detection rules through the CLI and the full default pipeline."""

import json

from md_ocr_cleaner.cli import EXIT_FINDINGS, EXIT_OK, main
from md_ocr_cleaner.document import parse_document
from md_ocr_cleaner.pipeline import run_pipeline
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.selection import select_rules

FFFD = chr(0xFFFD)
PAGES = "Text.\n\n10\n\nText.\n\n11\n\nText.\n\n12\n"

DEFAULT_RULES = select_rules(REGISTRY)  # the rules a plain run uses


def test_fail_on_warning_triggers_for_suspicious_warning(tmp_path, capsys):
    src = tmp_path / "s.md"
    src.write_bytes(f"Zl{FFFD} text\n".encode())
    assert main([str(src), "-n", "--fail-on-warning"]) == EXIT_FINDINGS
    assert "replacement-character" in capsys.readouterr().err


def test_fail_on_warning_ignores_info_only_page_numbers(tmp_path, capsys):
    src = tmp_path / "p.md"
    src.write_bytes(PAGES.encode())
    assert main([str(src), "-n", "--fail-on-warning"]) == EXIT_OK
    err = capsys.readouterr().err
    assert err.count("probable-page-number") == 3
    assert "3 findings (0 error, 0 warning, 3 info)" in err


def test_json_report_codes_severities_and_locations(tmp_path, capsys):
    src = tmp_path / "j.md"
    src.write_bytes(f"Text.\n\n10\n\nTe{FFFD}xt.\n\n11\n\nText.\n\n12\n".encode())
    assert main([str(src), "-n", "--report-json", "-"]) == EXIT_OK
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert [(f["rule_id"], f["code"], f["severity"], f["location"]["line"]) for f in findings] == [
        ("suspicious-characters", "replacement-character", "warning", 5),
        ("detect-page-numbers", "probable-page-number", "info", 3),
        ("detect-page-numbers", "probable-page-number", "info", 7),
        ("detect-page-numbers", "probable-page-number", "info", 11),
    ]
    assert findings[0]["location"]["column"] == 3


def test_detection_only_leaves_text_unchanged(tmp_path):
    src = tmp_path / "d.md"
    data = f"a  b {FFFD}\n\n\n\n\n12\n".encode()
    src.write_bytes(data)
    argv = [str(src), "--only", "suspicious-characters,detect-page-numbers"]
    assert main(argv) == EXIT_OK
    assert (tmp_path / "d.cleaned.md").read_bytes() == data


def test_default_pipeline_is_idempotent_with_deterministic_findings():
    text = f"# Chapter\n\nCafé  au {FFFD}. \n\n\n\n\n10\n\nText\n\n11\n\nText\n\n12\n"
    first = run_pipeline(parse_document(text.encode()), DEFAULT_RULES, input_path="x.md")
    second = run_pipeline(first.document, DEFAULT_RULES, input_path="x.md")
    third = run_pipeline(second.document, DEFAULT_RULES, input_path="x.md")
    assert second.report.changes == ()
    assert second.document.text == first.document.text
    assert [(f.code, f.location) for f in second.report.findings] == [
        (f.code, f.location) for f in third.report.findings
    ]
    assert [f.code for f in second.report.findings] == [
        "replacement-character",
        "probable-page-number",
        "probable-page-number",
        "probable-page-number",
    ]
