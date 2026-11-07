"""Exit codes at the outer CLI boundary: unexpected errors and broken stdout."""

import io
import subprocess
import sys

import pytest

from md_ocr_cleaner import cli
from md_ocr_cleaner.cli import EXIT_FINDINGS, EXIT_INTERNAL, EXIT_IO, EXIT_OK, main
from md_ocr_cleaner.rules.base import Rule, Safety
from md_ocr_cleaner.segmenter import SegmentKind

MESSY = b"Word  word \n"


@pytest.fixture
def messy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "scan.md"
    path.write_bytes(MESSY)
    return path


def listing(directory):
    return sorted(p.name for p in directory.iterdir())


class BrokenStdout(io.TextIOBase):
    """A stdout whose reader has gone away."""

    def write(self, s):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")

    def close(self):
        pass  # never flush at garbage collection


def break_stdout(monkeypatch, cls=BrokenStdout):
    # Inside the test body: pytest re-installs its own stdout capture after fixtures.
    monkeypatch.setattr(sys, "stdout", cls())


class KeyErrorRule(Rule):
    id = "fake-key-error"
    description = "Fails with an unexpected exception type."
    safety = Safety.SAFE
    default_enabled = False
    edit_kinds = frozenset({SegmentKind.PARAGRAPH})

    def apply(self, ctx):
        raise KeyError("missing")


# --- Unexpected exceptions ---------------------------------------------------------------


def test_unexpected_rule_exception_exits_4(messy, monkeypatch, capsys):
    monkeypatch.setattr(cli, "REGISTRY", (*cli.REGISTRY, KeyErrorRule()))
    before = listing(messy.parent)
    argv = ["scan.md", "--enable", "fake-key-error", "--report-json", "r.json"]
    assert main(argv) == EXIT_INTERNAL
    err = capsys.readouterr().err
    assert "internal error" in err and "KeyError" in err
    assert listing(messy.parent) == before


@pytest.mark.parametrize("exc", [ValueError("boom"), MemoryError(), RecursionError("deep")])
def test_unexpected_cli_exception_exits_4_without_traceback(messy, monkeypatch, capsys, exc):
    def explode(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cli, "run_pipeline", explode)
    before = listing(messy.parent)
    assert main(["scan.md", "--report-json", "r.json"]) == EXIT_INTERNAL
    captured = capsys.readouterr()
    assert captured.err.startswith(f"md-ocr-clean: internal error: {type(exc).__name__}")
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert listing(messy.parent) == before


def test_unexpected_exception_while_writing_exits_4(messy, monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise RuntimeError("disk gremlin")

    monkeypatch.setattr(cli, "write_output", explode)
    assert main(["scan.md"]) == EXIT_INTERNAL
    assert "internal error: RuntimeError: disk gremlin" in capsys.readouterr().err
    assert not (messy.parent / "scan.cleaned.md").exists()


@pytest.mark.parametrize("exc", [KeyboardInterrupt, GeneratorExit, SystemExit])
def test_base_exceptions_are_not_swallowed(messy, monkeypatch, exc):
    def interrupt(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cli, "run_pipeline", interrupt)
    with pytest.raises(exc):
        main(["scan.md"])


def test_usage_errors_still_exit_2(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["--no-such-option"])
    assert raised.value.code == 2


# --- Broken stdout -----------------------------------------------------------------------


def test_broken_pipe_during_diff_exits_3_and_writes_nothing(messy, monkeypatch, capsys):
    break_stdout(monkeypatch)
    before = listing(messy.parent)
    assert main(["scan.md", "--diff", "--report-json", "r.json"]) == EXIT_IO
    err = capsys.readouterr().err
    assert "cannot write the diff to stdout" in err
    assert "nothing was written" in err
    assert "Traceback" not in err
    assert listing(messy.parent) == before


def test_broken_pipe_during_diff_in_check_mode_is_not_exit_1(messy, monkeypatch):
    break_stdout(monkeypatch)
    assert main(["scan.md", "--check", "--diff"]) == EXIT_IO


def test_broken_pipe_during_json_stdout_exits_3(messy, monkeypatch, capsys):
    break_stdout(monkeypatch)
    assert main(["scan.md", "--check", "--report-json", "-"]) == EXIT_IO
    err = capsys.readouterr().err
    assert "cannot write the report to stdout" in err
    assert "already written" not in err
    assert listing(messy.parent) == ["scan.md"]


def test_broken_pipe_during_json_stdout_after_writing_says_so(messy, monkeypatch, capsys):
    break_stdout(monkeypatch)
    assert main(["scan.md", "--report-json", "-"]) == EXIT_IO
    err = capsys.readouterr().err
    assert "the cleaned output was already written to scan.cleaned.md" in err
    assert (messy.parent / "scan.cleaned.md").read_bytes() == b"Word word\n"


@pytest.mark.parametrize("argv", [["--list-rules"], ["--version"], ["--help"]])
def test_broken_pipe_for_informational_output_exits_3(argv, monkeypatch, capsys):
    break_stdout(monkeypatch)
    assert main(argv) == EXIT_IO
    assert "cannot write to stdout" in capsys.readouterr().err


def test_non_broken_pipe_os_error_on_stdout_also_exits_3(messy, monkeypatch, capsys):
    class InvalidStdout(BrokenStdout):  # Windows reports a closed pipe as EINVAL
        def write(self, s):
            raise OSError(22, "Invalid argument")

    break_stdout(monkeypatch, InvalidStdout)
    assert main(["scan.md", "--dry-run", "--diff"]) == EXIT_IO
    assert "Invalid argument" in capsys.readouterr().err


# --- Exit codes stay distinct --------------------------------------------------------------


def test_exit_codes_1_3_and_4_are_distinct(messy, monkeypatch, capsys):
    assert main(["scan.md", "--check"]) == EXIT_FINDINGS  # changes would be made
    assert main(["scan.md", "--check", "--report-json", "/"]) == EXIT_IO  # unwritable report
    monkeypatch.setattr(cli, "REGISTRY", (*cli.REGISTRY, KeyErrorRule()))
    assert main(["scan.md", "--check", "--enable", "fake-key-error"]) == EXIT_INTERNAL
    messy.write_bytes(b"clean\n")
    assert main(["scan.md", "--check"]) == EXIT_OK


def test_real_closed_pipe_exits_3_without_traceback(messy):
    proc = subprocess.Popen(
        [sys.executable, "-m", "md_ocr_cleaner", "scan.md", "--dry-run", "--diff"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=messy.parent,
    )
    proc.stdout.close()  # the reader goes away before the child writes the diff
    err = proc.stderr.read().decode("utf-8", "replace")
    proc.stderr.close()
    assert proc.wait(timeout=60) == EXIT_IO, err
    assert "cannot write the diff to stdout" in err
    assert "Traceback" not in err
    assert "Exception ignored" not in err
