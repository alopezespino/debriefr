"""Shared fixtures: build small yaml trees under tmp_path."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, sort_keys=False))
    return path


@pytest.fixture
def write_yaml_file():
    return write_yaml


@pytest.fixture
def project_yaml_data() -> dict:
    return {
        "collaborators": {
            "Alice": {"bio": "Trade economist.", "github": "octocat"},
            "Bob": {"bio": "IO economist.", "github": "bob-example"},
        },
        "github": {"repo": "example-org/example-repo", "project": 12},
        "transcript_dir": "meetings",
        "default_participants": ["Alice", "Bob"],
        "environments": {"mac": {"python": "/usr/bin/python3"}},
        "status": "active",
        "open_questions": ["ignored"],
    }


@pytest.fixture
def registry_yaml_data() -> dict:
    return {
        "participants": {
            "Alice": {"bio": "Registry bio.", "github": "octocat"},
            "Maribel": {"bio": "RA.", "github": "maribel"},
        },
        "guests": {"Matt": {"bio": "Guest economist."}},
        "myproject": {
            "path": "/tmp/myproject",
            "transcript_dir": "meetings",
            "default_participants": ["Alice"],
            "cleanup": "archive-all",
            "github": {"repo": "example-org/example-repo", "project": 12},
        },
    }


@pytest.fixture
def project_tree(tmp_path, project_yaml_data) -> Path:
    """<tmp>/workspace/projects.yaml (registry) + <tmp>/workspace/myproject/project.yaml."""
    ws = tmp_path / "workspace"
    write_yaml(ws / "projects.yaml", {"participants": {}, "guests": {}})
    proj = ws / "myproject"
    write_yaml(proj / "project.yaml", project_yaml_data)
    (proj / "sub" / "deeper").mkdir(parents=True)
    return proj
