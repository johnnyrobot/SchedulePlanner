# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
go to the **Security** tab of this repository and click **Report a vulnerability**.

Please include:

- a description of the issue and its impact,
- steps to reproduce (or a proof of concept), and
- any suggested fix, if you have one.

We aim to acknowledge reports within a few days and will keep you updated as we
investigate. Once a fix is available, we will credit you in the release notes
unless you prefer to remain anonymous.

## Scope

SchedulePlanner is a local, offline-first desktop/CLI tool. It performs no
network calls inside its analysis engine and persists no student-level data or
PII (see the privacy invariants in `tests/test_privacy_invariants.py`). The most
relevant areas for security reports are:

- the optional **live LACCD data** path (`build_live_workbook.py`, `sources/`),
  which fetches from public, unauthenticated APIs, and
- the optional local AI assistant (`chat_assist.py`), which talks only to a
  local Ollama instance.

## Supported versions

Security fixes are applied to the **latest released version**. Please upgrade to
the most recent release before reporting.
