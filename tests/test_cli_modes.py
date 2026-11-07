"""Milestone 6: --dry-run, --check, --diff, --report-json, --fail-on-warning."""

import codecs
import json

import pytest

from md_ocr_cleaner import cli, document
from md_ocr_cleaner.cli import EXIT_FINDINGS, EXIT_INTERNAL, EXIT_IO, EXIT_OK, main, unified_diff
from md_ocr_cleaner.report import Finding, Severity
from md_ocr_cleaner.rules import REGISTRY
from md_ocr_cleaner.rules.base import Rule, RuleResult, Safety

MESSY = "# Chapter\n\nCafé  au lait in Łódź. \n\n\n\n\n```\ncode   \n```\n"
CLEAN = "# Chapter\n\nCafé au lait in Łódź.\n\n```\ncode   \n```\n"


@pytest.fixture
def messy(tmp_path):
    path = tmp_path / "scan.md"
    path.write_bytes(MESSY.encode())
    return path


@pytest.fixture
def clean(tmp_path):
    path = tmp_path / "clean.md"
    path.write_bytes(CLEAN.encode())
    return path


def snapshot(directory):
    """Every file under directory with its bytes and modification time."""
    return {
        p.relative_to(directory).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def usage_error(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    return capsys.readouterr().err


class SeverityRule(Rule):
    id = "fake-severity"
    description = "Reports one finding of a configurable severity."
    safety = Safety.DETECT_ONLY
    default_enabled = True
    edit_kinds = frozenset()
    severity = Severity.ERROR

    def apply(self, ctx):
        return RuleResult(findings=(Finding(self.id, "x", self.severity, "found"),))


def with_severity(monkeypatch, severity):
    rule = SeverityRule()
    rule.severity = severity
    monkeypatch.setattr(cli, "REGISTRY", (*REGISTRY, rule))


# --- Dry run ----------------------------------------------------------------------------


def test_dry_run_changes_nothing_on_disk(messy, tmp_path, capsys):
    before = snapshot(tmp_path)
    assert main([str(messy), "--dry-run"]) == EXIT_OK
    assert snapshot(tmp_path) == before
    err = capsys.readouterr().err
    assert "dry run: 3 change(s) would be made; cleaned output not written" in err
    assert err.count("3 changes") == 1  # summary printed once


def test_dry_run_short_option_and_no_changes(clean, tmp_path, capsys):
    before = snapshot(tmp_path)
    assert main([str(clean), "-n"]) == EXIT_OK
    assert snapshot(tmp_path) == before
    assert "dry run: no changes needed; cleaned output not written" in capsys.readouterr().err


def test_dry_run_in_place_creates_no_backup(messy, tmp_path):
    before = snapshot(tmp_path)
    assert main([str(messy), "--in-place", "--backup", "--dry-run"]) == EXIT_OK
    assert snapshot(tmp_path) == before


def test_dry_run_ignores_existing_cleaned_output(messy, tmp_path):
    existing = tmp_path / "scan.cleaned.md"
    existing.write_bytes(b"keep")
    before = snapshot(tmp_path)
    assert main([str(messy), "--dry-run"]) == EXIT_OK
    assert snapshot(tmp_path) == before


def test_dry_run_still_rejects_logical_conflicts(messy, tmp_path, capsys):
    before = snapshot(tmp_path)
    assert main([str(messy), "--dry-run", "-o", str(messy)]) == EXIT_IO
    assert "refusing to overwrite the input file" in capsys.readouterr().err
    assert main([str(messy), "--dry-run", "-o", str(tmp_path / "missing" / "x.md")]) == EXIT_IO
    assert snapshot(tmp_path) == before


def test_dry_run_still_requires_backup_decision(messy, capsys):
    assert "--backup or --no-backup" in usage_error([str(messy), "--in-place", "-n"], capsys)


def test_dry_run_pipeline_failure_writes_nothing(messy, tmp_path, monkeypatch, capsys):
    from fakes import BoomRule

    monkeypatch.setattr(cli, "REGISTRY", (BoomRule(),))
    before = snapshot(tmp_path)
    report = tmp_path / "r.json"
    argv = [str(messy), "--enable", "fake-boom", "--report-json", str(report)]
    assert main(argv) == EXIT_INTERNAL
    assert main([*argv, "--dry-run"]) == EXIT_INTERNAL
    assert snapshot(tmp_path) == before


def test_dry_run_may_write_requested_report_only(messy, tmp_path):
    before = snapshot(tmp_path)
    report = tmp_path / "r.json"
    assert main([str(messy), "--dry-run", "--report-json", str(report)]) == EXIT_OK
    after = snapshot(tmp_path)
    assert set(after) - set(before) == {"r.json"}
    assert {k: v for k, v in after.items() if k != "r.json"} == before


# --- Check --------------------------------------------------------------------------------


def test_check_without_changes_returns_0(clean, tmp_path, capsys):
    before = snapshot(tmp_path)
    assert main([str(clean), "--check"]) == EXIT_OK
    assert snapshot(tmp_path) == before
    assert "check: no changes needed; cleaned output not written" in capsys.readouterr().err


def test_check_with_changes_returns_1_and_writes_nothing(messy, tmp_path, capsys):
    (tmp_path / "scan.cleaned.md").write_bytes(b"existing")
    before = snapshot(tmp_path)
    assert main([str(messy), "--check"]) == EXIT_FINDINGS
    assert snapshot(tmp_path) == before
    assert "check: 3 change(s) would be made" in capsys.readouterr().err


def test_check_usage_io_and_pipeline_errors_keep_their_codes(messy, tmp_path, monkeypatch, capsys):
    usage_error([str(messy), "--check", "--enable", "nope"], capsys)
    assert main([str(tmp_path / "missing.md"), "--check"]) == EXIT_IO
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\xff")
    assert main([str(bad), "--check"]) == EXIT_IO
    from fakes import BoomRule

    monkeypatch.setattr(cli, "REGISTRY", (BoomRule(),))
    assert main([str(messy), "--check", "--enable", "fake-boom"]) == EXIT_INTERNAL


# --- Diff --------------------------------------------------------------------------------

EXPECTED_DIFF = (
    "--- scan.md\n"
    "+++ scan.cleaned.md\n"
    "@@ -1,9 +1,6 @@\n"
    " # Chapter\n"
    " \n"
    "-Café  au lait in Łódź. \n"
    "-\n"
    "-\n"
    "-\n"
    "+Café au lait in Łódź.\n"
    " \n"
    " ```\n"
    " code   \n"
)


def test_diff_exact_on_stdout(messy, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["scan.md", "--diff", "--dry-run"]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == EXPECTED_DIFF
    assert "scan.md: 3 changes" in captured.err
    assert "---" not in captured.err


def test_diff_with_normal_write(messy, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["scan.md", "--diff"]) == EXIT_OK
    assert capsys.readouterr().out == EXPECTED_DIFF
    assert (tmp_path / "scan.cleaned.md").read_bytes() == CLEAN.encode()


def test_no_diff_when_unchanged(clean, capsys):
    assert main([str(clean), "--diff", "-n"]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "diff: no changes" in captured.err


@pytest.mark.parametrize(
    "data",
    [
        CLEAN.replace("\n", "\r\n").encode(),
        codecs.BOM_UTF8 + CLEAN.encode(),
        codecs.BOM_UTF8 + CLEAN.replace("\n", "\r\n").encode(),
        CLEAN.rstrip("\n").encode(),
    ],
)
def test_no_false_diff_from_representation(tmp_path, capsys, data):
    src = tmp_path / "x.md"
    src.write_bytes(data)
    assert main([str(src), "--diff", "-n"]) == EXIT_OK
    assert capsys.readouterr().out == ""


def test_no_false_diff_from_encoding_conversion(tmp_path, capsys):
    src = tmp_path / "x.md"
    src.write_bytes("Größe Łódź.\n".encode("cp1250"))
    assert main([str(src), "--diff", "-n", "--encoding", "cp1250"]) == EXIT_OK
    assert capsys.readouterr().out == ""


def test_diff_of_crlf_bom_source_uses_lf_and_no_bom(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "w.md").write_bytes(codecs.BOM_UTF8 + "Größe  ok\r\n".encode())
    assert main(["w.md", "--diff", "-n"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out == "--- w.md\n+++ w.cleaned.md\n@@ -1 +1 @@\n-Größe  ok\n+Größe ok\n"
    assert "\r" not in out and "\ufeff" not in out


def test_diff_without_final_newline():
    assert unified_diff("a  b", "a b", "x", "y") == (
        "--- x\n+++ y\n@@ -1 +1 @@\n"
        "-a  b\n\\ No newline at end of file\n"
        "+a b\n\\ No newline at end of file\n"
    )


def test_diff_final_newline_only_on_one_side():
    assert unified_diff("a\n", "a", "x", "y") == (
        "--- x\n+++ y\n@@ -1 +1 @@\n-a\n+a\n\\ No newline at end of file\n"
    )


def test_unified_diff_empty_when_equal():
    assert unified_diff("same\n", "same\n", "x", "y") == ""


def test_diff_headers_hide_absolute_paths(messy, tmp_path, monkeypatch, capsys):
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main([str(messy), "--diff", "-n"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("--- scan.md\n+++ scan.cleaned.md\n")
    assert str(tmp_path) not in out


# --- JSON report ----------------------------------------------------------------------------


def test_report_file(messy, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    messy.rename(tmp_path / "Łódź.md")
    assert main(["Łódź.md", "-o", "scan.cleaned.md", "--report-json", "r.json"]) == EXIT_OK
    raw = (tmp_path / "r.json").read_bytes()
    text = raw.decode("utf-8")
    assert text.endswith("}\n")
    data = json.loads(text)
    assert data["schema_version"] == 1
    assert data["input"] == "Łódź.md"
    assert list(data) == [
        "schema_version",
        "tool_version",
        "input",
        "rules",
        "summary",
        "changes",
        "findings",
    ]
    assert data["summary"]["changes"] == 3
    assert "Łódź" in text  # readable UTF-8, not \u escapes
    assert text.startswith('{\n  "schema_version": 1,')
    assert "Report: r.json" in capsys.readouterr().err
    assert (tmp_path / "scan.cleaned.md").read_bytes() == CLEAN.encode()


def test_report_is_deterministic(messy, tmp_path):
    main([str(messy), "-n", "--report-json", str(tmp_path / "a.json")])
    main([str(messy), "-n", "--report-json", str(tmp_path / "b.json")])
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_report_to_stdout(messy, tmp_path, capsys):
    assert main([str(messy), "-n", "--report-json", "-"]) == EXIT_OK
    captured = capsys.readouterr()
    assert json.loads(captured.out)["summary"]["changes"] == 3
    assert captured.out.endswith("}\n")
    assert "Report: written to stdout" in captured.err
    assert "3 changes" not in captured.out  # human summary stays on stderr


def test_report_hides_absolute_paths(messy, tmp_path, monkeypatch, capsys):
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert main([str(messy), "-n", "--report-json", "-"]) == EXIT_OK
    out = capsys.readouterr().out
    assert json.loads(out)["input"] == "scan.md"
    assert str(tmp_path).replace("\\", "\\\\") not in out and str(tmp_path) not in out


def test_existing_report_requires_force(messy, tmp_path, capsys):
    report = tmp_path / "r.json"
    report.write_bytes(b"old")
    before = snapshot(tmp_path)
    assert main([str(messy), "--report-json", str(report)]) == EXIT_IO
    assert "use --force" in capsys.readouterr().err
    assert snapshot(tmp_path) == before  # preflight: cleaned output not written either
    assert main([str(messy), "--report-json", str(report), "--force"]) == EXIT_OK
    assert json.loads(report.read_text(encoding="utf-8"))["schema_version"] == 1


@pytest.mark.parametrize("target", ["input", "output", "backup", "in-place"])
def test_report_path_conflicts(messy, tmp_path, capsys, target):
    argv = [str(messy)]
    if target == "input":
        report = messy
    elif target == "output":
        report = tmp_path / "out.md"
        argv += ["-o", str(report)]
    elif target == "backup":
        report = tmp_path / "scan.md.bak"
        argv += ["--in-place", "--backup"]
    else:
        report = messy
        argv += ["--in-place", "--no-backup"]
    if argv[1:2] != ["--in-place"]:
        argv.append("--force")  # conflicts are refused even with --force
    before = snapshot(tmp_path)
    assert main([*argv, "--report-json", str(report)]) == EXIT_IO
    assert "report path is the" in capsys.readouterr().err
    assert snapshot(tmp_path) == before


def test_report_conflict_with_default_output(messy, tmp_path, capsys):
    report = tmp_path / "scan.cleaned.md"
    assert main([str(messy), "-n", "--report-json", str(report)]) == EXIT_IO
    assert "report path is the cleaned output" in capsys.readouterr().err


def _fail_replace_for(suffix, monkeypatch):
    real = document.os.replace

    def replace(src, dst):
        if str(dst).endswith(suffix):
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    monkeypatch.setattr(document.os, "replace", replace)


def test_report_write_failure_after_output(messy, tmp_path, monkeypatch, capsys):
    _fail_replace_for(".json", monkeypatch)
    assert main([str(messy), "--report-json", str(tmp_path / "r.json")]) == EXIT_IO
    err = capsys.readouterr().err
    assert "report not written" in err
    assert "the cleaned output was already written to" in err
    assert (tmp_path / "scan.cleaned.md").read_bytes() == CLEAN.encode()
    assert not (tmp_path / "r.json").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_report_write_failure_in_dry_run_leaves_nothing(messy, tmp_path, monkeypatch, capsys):
    _fail_replace_for(".json", monkeypatch)
    before = snapshot(tmp_path)
    assert main([str(messy), "-n", "--report-json", str(tmp_path / "r.json")]) == EXIT_IO
    assert "already written" not in capsys.readouterr().err
    assert snapshot(tmp_path) == before


# --- Invalid combinations ---------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        ["--diff", "--report-json", "-"],
        ["--report-json", ""],
        ["--in-place", "--backup", "-o", "x.md"],
        ["--in-place", "--backup", "--force"],
        ["--backup"],
        ["--only", "a", "--enable", "b"],
    ],
)
def test_invalid_combinations_exit_2(messy, tmp_path, capsys, extra):
    before = snapshot(tmp_path)
    usage_error([str(messy), *extra], capsys)
    assert snapshot(tmp_path) == before


def test_check_with_report_stdout_and_no_diff_is_valid(messy, capsys):
    assert main([str(messy), "--check", "--report-json", "-"]) == EXIT_FINDINGS
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


# --- fail-on-warning ----------------------------------------------------------------------


def test_fail_on_warning_without_findings(clean):
    assert main([str(clean), "-n", "--fail-on-warning"]) == EXIT_OK


def test_fail_on_warning_ignores_info(tmp_path):
    src = tmp_path / "t.md"
    src.write_bytes(b"| `a | b |\n|---|---|\n")  # ambiguous table: info
    assert main([str(src), "-n", "--fail-on-warning"]) == EXIT_OK


@pytest.mark.parametrize("severity", [Severity.WARNING, Severity.ERROR])
def test_fail_on_warning_triggers(clean, monkeypatch, capsys, severity):
    with_severity(monkeypatch, severity)
    assert main([str(clean), "-n", "--fail-on-warning"]) == EXIT_FINDINGS
    assert "fail-on-warning: 1 warning or error finding(s)" in capsys.readouterr().err


def test_fail_on_warning_info_rule_returns_0(clean, monkeypatch):
    with_severity(monkeypatch, Severity.INFO)
    assert main([str(clean), "-n", "--fail-on-warning"]) == EXIT_OK


def test_fail_on_warning_still_writes_output_and_report(tmp_path, capsys):
    src = tmp_path / "w.md"
    data = "Text  with \r\nspaces\n\n```\ncode  \n```\n"  # mixed endings: warning
    src.write_bytes(data.encode())
    report = tmp_path / "r.json"
    assert main([str(src), "--fail-on-warning", "--report-json", str(report)]) == EXIT_FINDINGS
    out = (tmp_path / "w.cleaned.md").read_bytes().decode()
    assert out == "Text with\nspaces\n\n```\ncode  \n```\n"
    findings = json.loads(report.read_text(encoding="utf-8"))["findings"]
    assert [f["code"] for f in findings] == ["mixed-line-endings"]
    err = capsys.readouterr().err
    assert err.index("Wrote") < err.index("fail-on-warning")


def test_fail_on_warning_with_check(tmp_path):
    src = tmp_path / "w.md"
    src.write_bytes(b"ok\r\nok\n")  # warning, no text change
    assert main([str(src), "--check"]) == EXIT_OK
    assert main([str(src), "--check", "--fail-on-warning"]) == EXIT_FINDINGS


def test_fail_on_warning_with_diff_and_report(tmp_path, capsys):
    src = tmp_path / "w.md"
    src.write_bytes(b"a  b\r\nc\n")
    assert main([str(src), "-n", "--diff", "--fail-on-warning"]) == EXIT_FINDINGS
    assert capsys.readouterr().out.endswith("-a  b\n+a b\n c\n")
