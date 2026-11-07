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


def test_board_owner_is_repo_owner_not_logged_in_user(monkeypatch, summary_path):
    """An organization's board is looked up under the organization."""
    _patch_pre_prompt(monkeypatch)  # logged-in user is "octocat"
    seen = {}

    def capture(owner, number):
        seen["owner"] = owner
        raise SentinelError("reached post-prompt code")
    monkeypatch.setattr(sync_issues, "fetch_project_metadata", capture)

    with pytest.raises(SentinelError):
        sync_issues.sync_summary(
            summary_path, "some-org/repo", 3, {}, assume_yes=True,
        )
    assert seen["owner"] == "some-org"


def test_project_owner_overrides_repo_owner(monkeypatch, summary_path):
    """A board kept under another account is looked up under that account."""
    _patch_pre_prompt(monkeypatch)
    seen = {}

    def capture(owner, number):
        seen["owner"] = owner
        raise SentinelError("reached post-prompt code")
    monkeypatch.setattr(sync_issues, "fetch_project_metadata", capture)

    with pytest.raises(SentinelError):
        sync_issues.sync_summary(
            summary_path, "repo-owner/repo", 3, {}, assume_yes=True,
            project_owner="board-owner",
        )
    assert seen["owner"] == "board-owner"


def test_fetch_project_items_reads_all_and_filters_repo(monkeypatch):
    """Items past gh's default 30 are read, and other repos' issues skipped."""
    seen = {}

    def fake_sh(*args, **kw):
        seen["args"] = args
        return {"items": [
            {"id": "A", "content": {"number": 25, "repository": "org/one"}},
            {"id": "B", "content": {"number": 25, "repository": "org/two"}},
            {"id": "C", "content": {}},
        ]}
    monkeypatch.setattr(sync_issues, "sh", fake_sh)

    got = sync_issues.fetch_project_items("org", 3, "org/one")
    assert got == {25: "A"}
    assert "--limit" in seen["args"]
