"""Tests for the --yes / non-TTY guard on sync_summary()."""
from __future__ import annotations

import builtins

import pytest

from debriefr import sync_issues


SUMMARY_MD = """\
## Action Items

| Owner | Task | Due | Priority | Notes |
|-------|------|-----|----------|-------|
| Alice | Do the thing | 2026-09-15 | high | none |
"""


class SentinelError(Exception):
    """Raised by the patched fetch_project_metadata to prove we reached it."""


@pytest.fixture
def summary_path(tmp_path):
    p = tmp_path / "summary.md"
    p.write_text(SUMMARY_MD)
    return p


def _patch_pre_prompt(monkeypatch):
    """Patch everything sync_summary calls before the prompt."""
    monkeypatch.setattr(sync_issues, "current_github_user", lambda: "octocat")
    monkeypatch.setattr(sync_issues, "fetch_open_issues", lambda repo: [])


def _patch_fetch_project_metadata_sentinel(monkeypatch):
    def raise_sentinel(owner, number):
        raise SentinelError("reached post-prompt code")
    monkeypatch.setattr(sync_issues, "fetch_project_metadata", raise_sentinel)


def test_assume_yes_skips_prompt_and_proceeds(monkeypatch, summary_path):
    _patch_pre_prompt(monkeypatch)
    _patch_fetch_project_metadata_sentinel(monkeypatch)

    def fail_input(prompt=""):
        raise AssertionError("input() should not be called when assume_yes=True")
    monkeypatch.setattr(builtins, "input", fail_input)

    with pytest.raises(SentinelError):
        sync_issues.sync_summary(
            summary_path, "owner/repo", 1, {}, assume_yes=True,
        )


def test_non_tty_without_yes_exits(monkeypatch, summary_path):
    _patch_pre_prompt(monkeypatch)
    called = {"hit": False}

    def raise_if_called(owner, number):
        called["hit"] = True
        raise SentinelError("should not be reached")
    monkeypatch.setattr(sync_issues, "fetch_project_metadata", raise_if_called)
    monkeypatch.setattr(sync_issues.sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit) as exc_info:
        sync_issues.sync_summary(
            summary_path, "owner/repo", 1, {}, assume_yes=False,
        )
    assert "stdin is not a terminal; pass --yes to skip the confirmation" in str(
        exc_info.value
    )
    assert called["hit"] is False


def test_tty_prompt_no_answer_aborts(monkeypatch, summary_path, capsys):
    _patch_pre_prompt(monkeypatch)
    called = {"hit": False}

    def raise_if_called(owner, number):
        called["hit"] = True
        raise SentinelError("should not be reached")
    monkeypatch.setattr(sync_issues, "fetch_project_metadata", raise_if_called)
    monkeypatch.setattr(sync_issues.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "n")

    result = sync_issues.sync_summary(
        summary_path, "owner/repo", 1, {}, assume_yes=False,
    )
    assert result is None
    assert called["hit"] is False
    assert "Aborted." in capsys.readouterr().out
