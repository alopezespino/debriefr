"""Tests for registry resolution in debriefr.cli."""
from __future__ import annotations

from pathlib import Path

import pytest

from debriefr.cli import _pick_discover_from, _resolve_registry


@pytest.fixture
def env_registry(tmp_path, write_yaml_file, registry_yaml_data):
    """A standalone shared registry (ALE-style) under <tmp>/ale/projects.yaml."""
    return write_yaml_file(tmp_path / "ale" / "projects.yaml", registry_yaml_data)


def test_project_yaml_discovered_from_cwd(project_tree, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(project_tree)

    config, people = _resolve_registry(None, None, None)

    assert config["slug"] == project_tree.name
    assert config["output_dir"] == project_tree / "meetings"
    assert config["_kind"] == "project"
    assert set(people) >= {"Alice", "Bob"}
    assert config["github"]["repo"] == "example-org/example-repo"


def test_project_yaml_discovered_from_subdir(project_tree, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(project_tree / "sub" / "deeper")

    config, _ = _resolve_registry(None, None, None)
    assert config["slug"] == project_tree.name


def test_env_registry_merges_guests(project_tree, env_registry, monkeypatch):
    monkeypatch.setenv("DEBRIEFR_PROJECTS_YAML", str(env_registry))
    monkeypatch.chdir(project_tree)

    config, people = _resolve_registry(None, None, None)

    # guest from the shared registry is now visible
    assert "Matt" in people
    assert "Maribel" in people
    # a participant defined in both keeps the project.yaml version
    assert people["Alice"]["bio"] == "Trade economist."
    # fallback projects are not merged in
    assert config["slug"] == project_tree.name


def test_mismatched_slug_exits(project_tree, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(project_tree)

    with pytest.raises(SystemExit) as exc:
        _resolve_registry("not-this-one", None, None)
    msg = str(exc.value)
    assert "does not match the project.yaml at" in msg
    assert f"(slug {project_tree.name})" in msg


def test_matching_slug_is_accepted_case_insensitively(project_tree, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(project_tree)

    config, _ = _resolve_registry(project_tree.name.upper(), None, None)
    assert config["slug"] == project_tree.name


def test_registry_without_slug_exits(env_registry, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(env_registry.parent)

    with pytest.raises(SystemExit) as exc:
        _resolve_registry(None, None, None)
    assert "--project is required when using a projects.yaml registry" in str(exc.value)


def test_registry_with_slug_resolves(env_registry, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(env_registry.parent)

    config, people = _resolve_registry("myproject", None, None)
    assert config["slug"] == "myproject"
    assert config["output_dir"] == Path("/tmp/myproject") / "meetings"
    assert config["_kind"] == "registry"
    assert "Matt" in people  # guests merged by merge_people


def test_explicit_projects_yaml_sets_kind(project_tree, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(Path(project_tree).parent.parent)

    config, _ = _resolve_registry(None, str(project_tree / "project.yaml"), None)
    assert config["_kind"] == "project"
    assert config["slug"] == project_tree.name


def test_nothing_found_exits(tmp_path, monkeypatch):
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    empty = tmp_path / "nowhere"
    empty.mkdir()
    monkeypatch.chdir(empty)

    with pytest.raises(SystemExit) as exc:
        _resolve_registry(None, None, empty)
    assert "Cannot find project.yaml or projects.yaml" in str(exc.value)


def test_pick_discover_from_prefers_project(project_tree, env_registry):
    """Audio sits in the shared-registry tree; cwd is the project -> project wins."""
    inbox = env_registry.parent / "inbox"
    inbox.mkdir()

    assert _pick_discover_from([inbox, project_tree]) == project_tree
    # with no project anywhere, the first candidate that finds anything wins
    assert _pick_discover_from([inbox, env_registry.parent]) == inbox


def test_pick_discover_from_empty():
    assert _pick_discover_from([None]) is None


def test_main_transcribe_without_registry_errors(tmp_path, monkeypatch):
    """No registry, no --output-dir -> the existing error still fires."""
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    empty = tmp_path / "bare"
    empty.mkdir()
    monkeypatch.chdir(empty)
    audio = empty / "meeting.wav"
    audio.write_bytes(b"")

    from debriefr.cli import main
    monkeypatch.setattr("sys.argv", ["debriefr", "transcribe", str(audio)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert "--output-dir is required" in str(exc.value)
