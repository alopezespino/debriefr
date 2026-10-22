"""sync-issues maps Priority/Status onto a board whose options use other names."""
from __future__ import annotations

import sys

import pytest

from debriefr import sync_issues

SUMMARY_MD = """\
## Action Items

| # | Owner | Task | Due | Priority | Notes |
|---|-------|------|-----|----------|-------|
| 1 | Alice | Fix the outage | 2026-11-02 | urgent | none |
| 2 | Bob | Write the draft | 2026-11-09 | high | none |
| 3 | Alice | Tidy the slides | 2026-11-16 | medium | none |
"""


def _field(fid, name, options):
    return {"id": fid, "name": name,
            "options": [{"id": f"opt-{o}", "name": o} for o in options]}


P_BOARD = {
    "priority": _field("F_P", "Priority", ["P0", "P1", "P2"]),
    "status": _field("F_S", "Status",
                     ["Backlog", "Ready", "In progress", "In review", "Done"]),
}
DEFAULT_BOARD = {
    "priority": _field("F_P", "Priority", ["low", "medium", "high", "urgent"]),
    "status": _field("F_S", "Status", ["Todo", "In Progress", "Done"]),
}


@pytest.fixture
def board(monkeypatch, tmp_path):
    """Mock gh: returns the board's fields and records every field edit."""
    state = {"fields": None, "edits": [], "next_issue": 7}
    monkeypatch.setattr(sync_issues, "current_github_user", lambda: "octocat")
    monkeypatch.setattr(sync_issues, "fetch_open_issues", lambda repo: [])
    monkeypatch.setattr(sync_issues, "fetch_project_metadata",
                        lambda owner, number: ("PID", state["fields"]))
    monkeypatch.setattr(sync_issues, "fetch_iterations", lambda owner, number: [])
    monkeypatch.setattr(sync_issues, "fetch_project_items", lambda owner, number: {})

    def fake_sh(*args, check=True, json_out=False):
        if args[:3] == ("gh", "issue", "create"):
            n = state["next_issue"]
            state["next_issue"] += 1
            return f"https://github.com/example-org/example-repo/issues/{n}\n"
        if args[:3] == ("gh", "project", "item-add"):
            return {"id": f"ITEM-{state['next_issue'] - 1}"}
        if args[:3] == ("gh", "project", "item-edit"):
            a = list(args)
            field = a[a.index("--field-id") + 1]
            if "--single-select-option-id" in a:
                value = a[a.index("--single-select-option-id") + 1]
            else:
                value = a[a.index("--date") + 1]
            state["edits"].append((a[a.index("--id") + 1], field, value))
            return ""
        raise AssertionError(f"unexpected gh call: {args}")
    monkeypatch.setattr(sync_issues, "sh", fake_sh)

    summary = tmp_path / "2026-10-21_weekly_sync.md"
    summary.write_text(SUMMARY_MD)
    state["summary"] = summary
    return state


def _selects(state):
    return {(item, field): value for item, field, value in state["edits"]
            if field in ("F_P", "F_S")}


def test_maps_translate_priority_and_status(board, capsys):
    board["fields"] = P_BOARD
    sync_issues.sync_summary(
        board["summary"], "example-org/example-repo", 12, {}, assume_yes=True,
        priority_map={"urgent": "P0", "high": "P1", "medium": "P2"},
        status_map={"Todo": "Ready"},
    )

    assert _selects(board) == {
        ("ITEM-7", "F_P"): "opt-P0", ("ITEM-7", "F_S"): "opt-In progress",
        ("ITEM-8", "F_P"): "opt-P1", ("ITEM-8", "F_S"): "opt-Ready",
        ("ITEM-9", "F_P"): "opt-P2", ("ITEM-9", "F_S"): "opt-Ready",
    }
    assert "not found on field" not in capsys.readouterr().out


def test_map_keys_match_case_insensitively(board, capsys):
    board["fields"] = P_BOARD
    sync_issues.sync_summary(
        board["summary"], "example-org/example-repo", 12, {}, assume_yes=True,
        priority_map={"URGENT": "P0", "High": "P1", "medium": "P2"},
        status_map={"todo": "Ready", "in progress": "In review"},
    )
    sel = _selects(board)
    assert sel[("ITEM-7", "F_P")] == "opt-P0"
    assert sel[("ITEM-7", "F_S")] == "opt-In review"
    assert sel[("ITEM-8", "F_S")] == "opt-Ready"


def test_default_without_maps_is_unchanged(board, capsys):
    board["fields"] = DEFAULT_BOARD
    sync_issues.sync_summary(
        board["summary"], "example-org/example-repo", 12, {}, assume_yes=True,
    )
    assert _selects(board) == {
        ("ITEM-7", "F_P"): "opt-urgent", ("ITEM-7", "F_S"): "opt-In Progress",
        ("ITEM-8", "F_P"): "opt-high", ("ITEM-8", "F_S"): "opt-Todo",
        ("ITEM-9", "F_P"): "opt-medium", ("ITEM-9", "F_S"): "opt-Todo",
    }
    assert "not found on field" not in capsys.readouterr().out


def test_cli_passes_maps_from_project_yaml(tmp_path, monkeypatch, write_yaml_file,
                                           project_yaml_data):
    data = dict(project_yaml_data)
    data["github"] = {
        "repo": "example-org/example-repo", "project": 12,
        "priority_map": {"urgent": "P0", "high": "P1", "medium": "P2"},
        "status_map": {"Todo": "Ready"},
    }
    proj = tmp_path / "myproject"
    write_yaml_file(proj / "project.yaml", data)
    summary = proj / "meetings" / "2026-10-21_weekly_sync.md"
    summary.parent.mkdir(parents=True)
    summary.write_text(SUMMARY_MD)
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(proj)

    seen = {}
    monkeypatch.setattr(sync_issues, "sync_summary",
                        lambda *a, **kw: seen.update(args=a, **kw))
    from debriefr.cli import main
    monkeypatch.setattr(sys, "argv", ["debriefr", "sync-issues", str(summary), "--yes"])
    main()

    assert seen["args"][1:3] == ("example-org/example-repo", 12)
    assert seen["priority_map"] == {"urgent": "P0", "high": "P1", "medium": "P2"}
    assert seen["status_map"] == {"Todo": "Ready"}
