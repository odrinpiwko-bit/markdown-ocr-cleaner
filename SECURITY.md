# Security policy

## Supported versions

Markdown OCR Cleaner is alpha software. Security fixes are made for the
latest release only. The first release is 0.1.0.

## Reporting a vulnerability

**Please do not report security issues in public issues, pull requests, or
discussions.**

Report them privately through GitHub's private vulnerability reporting
("Report a vulnerability" under the repository's **Security** tab). That
private channel will be enabled for the repository before its first public
release. Until it is available, please wait to share details rather than
disclosing them publicly.

Please include:

- the version (`md-ocr-clean --version`), operating system, and Python version;
- the exact command line;
- a minimal input file that reproduces the problem, if you can share one
  (remove anything confidential);
- what happened and what you expected.

You will receive an acknowledgement, and fixes will be coordinated with you
before public disclosure. There is no bug bounty.

## How the tool operates

- It runs **locally** on files you name on the command line.
- It makes **no network connections** at runtime, sends **no telemetry**, and
  uses no external APIs or AI services.
- It has **no runtime dependencies** beyond the Python standard library.
- It does not run subprocesses, read environment variables to change its
  behavior, or execute content from the documents it processes.

## In scope

- **Unintended overwrites or data loss:** writing to the input file without
  `--in-place`, replacing an existing file without `--force`, overwriting an
  existing backup, a dry run or `--check` that writes the cleaned output or a
  backup, or a failure that leaves a partial or truncated file.
- **Path handling:** writing somewhere other than the requested or documented
  destination, output or report paths that resolve to the input or backup, or
  reports that leak unrelated absolute paths.
- **Protected-content corruption:** any change to content the documentation
  says is protected (code, front matter, tables, HTML, definitions, math,
  `ocr-clean: off` regions, inline code, URLs, and other masked spans), or a
  default-enabled rule changing the meaning of text.
- **Denial of service:** inputs of reasonable size that make the tool hang,
  take super-linear time, or use excessive memory.

## Out of scope

- Behavior of opt-in destructive rules that is documented as a limitation
  (for example a missed or kept hyphenation), unless it corrupts protected
  content or creates Markdown structure.
- Problems in third-party tools that render or process the cleaned output.
- Attacks that require control of the user's account, Python installation,
  or file system permissions.
