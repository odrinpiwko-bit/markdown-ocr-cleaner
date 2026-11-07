#!/usr/bin/env bash
# CLI smoke test against whatever md-ocr-clean is on PATH.
# Runs in a fresh temporary directory so the source tree is not involved.
set -euo pipefail

work="$(mktemp -d)"
cd "$work"

expect_exit() {
  local expected="$1"
  shift
  set +e
  "$@"
  local code=$?
  set -e
  if [ "$code" -ne "$expected" ]; then
    echo "expected exit $expected, got $code: $*" >&2
    exit 1
  fi
}

printf 'Word  word \n\n\n\n\ninfor-\nmation is here\n\n1\n\nText.\n\n2\n\nText.\n\n3\n\nThe end.\n' > smoke.md

md-ocr-clean --version
python -m md_ocr_cleaner --version
md-ocr-clean --list-rules

# Default cleaning writes smoke.cleaned.md and never touches the input.
cp smoke.md original.md
md-ocr-clean smoke.md
test -f smoke.cleaned.md
cmp smoke.md original.md

# Preview and check modes write nothing.
md-ocr-clean smoke.md --dry-run --diff
expect_exit 1 md-ocr-clean smoke.md --check
md-ocr-clean smoke.cleaned.md --check

# JSON report on stdout.
md-ocr-clean smoke.md -n --report-json - > report.json
python -c "import json; r = json.load(open('report.json', encoding='utf-8')); assert r['schema_version'] == 1"

# Destructive rules are opt-in.
md-ocr-clean smoke.md -n --diff --enable remove-page-numbers,join-hyphenated-words,reconstruct-paragraphs

# Usage and I/O errors.
expect_exit 2 md-ocr-clean smoke.md --enable no-such-rule
expect_exit 3 md-ocr-clean missing.md
expect_exit 3 md-ocr-clean smoke.md  # smoke.cleaned.md exists, no --force

cmp smoke.md original.md
echo "smoke test passed"
