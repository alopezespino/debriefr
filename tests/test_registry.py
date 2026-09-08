"""Tests for debriefr.registry discovery, single-project loading, and merging."""
from __future__ import annotations

from pathlib import Path

from debriefr.registry import (
    discover_registry,
    load_any,
    load_single_project,
    merge_people,
    merge_registries,
    parse_registry,
    resolve_project,
)


# --- discovery ------------------------------------------------------------

def test_discovery_prefers_project_yaml_from_below(project_tree):
    path, kind = discover_registry(project_tree / "sub" / "deeper")
    assert kind == "project"
    assert path == project_tree / "project.yaml"


def test_discovery_finds_nested_registry(tmp_path, write_yaml_file):
    write_yaml_file(tmp_path / "ws" / "projects" / "projects.yaml", {"participants": {}})
    sub = tmp_path / "ws" / "a" / "b"
    sub.mkdir(parents=True)
    path, kind = discover_registry(sub)
    assert kind == "registry"
    assert path == tmp_path / "ws" / "projects" / "projects.yaml"


def test_discovery_prefers_project_yaml_at_same_level(tmp_path, write_yaml_file,
                                                     project_yaml_data):
    write_yaml_file(tmp_path / "d" / "projects.yaml", {"participants": {}})
    write_yaml_file(tmp_path / "d" / "project.yaml", project_yaml_data)
    path, kind = discover_registry(tmp_path / "d")
    assert (path.name, kind) == ("project.yaml", "project")


def test_discovery_returns_none(tmp_path):
    empty = tmp_path / "nothing" / "here"
    empty.mkdir(parents=True)
    assert discover_registry(empty) is None


# --- load_single_project --------------------------------------------------

def test_slug_from_directory_basename(project_tree):
    projects, participants, guests = load_single_project(project_tree / "project.yaml")
    assert list(projects) == ["myproject"]
    assert set(participants) == {"Alice", "Bob"}
    assert guests == {}


def test_slug_from_name_key(tmp_path, write_yaml_file):
    p = write_yaml_file(tmp_path / "somedir" / "project.yaml",
                        {"name": "us-roos", "participants": {"Alice": {}}})
    projects, _, _ = load_single_project(p)
    assert list(projects) == ["us-roos"]


def test_participants_alias_and_collaborators_wins(tmp_path, write_yaml_file):
    p = write_yaml_file(tmp_path / "a" / "project.yaml",
                        {"participants": {"Matt": {"bio": "x"}}})
    _, people, _ = load_single_project(p)
    assert set(people) == {"Matt"}

    q = write_yaml_file(tmp_path / "b" / "project.yaml",
                        {"collaborators": {"Alice": {}},
                         "participants": {"Matt": {}}})
    _, people2, _ = load_single_project(q)
    assert set(people2) == {"Alice"}


def test_defaults_and_extra_keys_ignored(tmp_path, write_yaml_file):
    p = write_yaml_file(tmp_path / "proj" / "project.yaml",
                        {"collaborators": {"Alice": {}, "Bob": {}},
                         "litrev_dir": "LitRev", "links": [{"url": "x"}]})
    projects, _, _ = load_single_project(p)
    entry = projects["proj"]
    assert entry["transcript_dir"] == "meetings"
    assert entry["cleanup"] == "archive-all"
    assert sorted(entry["default_participants"]) == ["Alice", "Bob"]
    assert entry["github"] is None
    assert set(entry) == {"path", "transcript_dir", "default_participants",
                          "cleanup", "github"}


def test_resolve_project_round_trip(project_tree):
    projects, _, _ = load_single_project(project_tree / "project.yaml")
    cfg = resolve_project(projects, "myproject")
    assert cfg["path"] == project_tree
    assert cfg["output_dir"] == project_tree / "meetings"
    assert cfg["github"] == {"repo": "example-org/example-repo", "project": 12}
    assert cfg["cleanup"] == "archive-all"


def test_load_any_dispatch(project_tree, tmp_path, write_yaml_file,
                           registry_yaml_data):
    projects, participants, guests = load_any(
        project_tree / "project.yaml", "project")
    assert list(projects) == ["myproject"] and guests == {}

    reg = write_yaml_file(tmp_path / "r" / "projects.yaml", registry_yaml_data)
    rprojects, rparts, rguests = load_any(reg, "registry")
    assert list(rprojects) == ["myproject"]
    assert set(rparts) == {"Alice", "Maribel"} and set(rguests) == {"Matt"}


# --- merge_registries -----------------------------------------------------

def _triples(project_tree, registry_yaml_data):
    primary = load_single_project(project_tree / "project.yaml")
    fallback = parse_registry(registry_yaml_data)
    return primary, fallback


def test_merge_registries(project_tree, registry_yaml_data):
    primary, fallback = _triples(project_tree, registry_yaml_data)
    projects, participants, guests = merge_registries(primary, fallback)

    # fallback projects are not merged
    assert projects == primary[0]
    # fallback guests survive
    assert "Matt" in guests
    # fallback-only participant survives
    assert "Maribel" in participants
    # primary wins on the same name
    assert participants["Alice"]["bio"] == "Trade economist."


def test_merge_registries_drops_duplicate_guest(project_tree,
                                                registry_yaml_data):
    registry_yaml_data["guests"]["Alice"] = {"bio": "guest copy"}
    primary, fallback = _triples(project_tree, registry_yaml_data)
    _, participants, guests = merge_registries(primary, fallback)
    assert "Alice" not in guests
    assert participants["Alice"]["bio"] == "Trade economist."
    assert merge_people(participants, guests)["Alice"]["bio"] == "Trade economist."


# --- registry path unchanged ---------------------------------------------

def test_parse_registry_unchanged(registry_yaml_data):
    projects, participants, guests = parse_registry(registry_yaml_data)
    assert list(projects) == ["myproject"]
    assert "participants" not in projects and "guests" not in projects
    cfg = resolve_project(projects, "myproject")
    assert cfg["path"] == Path("/tmp/myproject")
    assert cfg["output_dir"] == Path("/tmp/myproject/meetings")
    assert cfg["default_participants"] == ["Alice"]
    assert cfg["github"]["project"] == 12
    people = merge_people(participants, guests)
    assert set(people) == {"Alice", "Maribel", "Matt"}
