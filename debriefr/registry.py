"""Project registry for debriefr.

Reads a projects.yaml file that maps project slugs to config (output paths,
participants, GitHub settings) and centralizes participant metadata (bios,
GitHub handles, emails) so a single ``--project`` flag resolves everything.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_registry(yaml_path: str | Path) -> dict:
    path = Path(yaml_path)
    if not path.is_file():
        raise FileNotFoundError(f"Registry not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def parse_registry(data: dict) -> tuple[dict, dict, dict]:
    data = dict(data)
    participants = data.pop("participants", {}) or {}
    guests = data.pop("guests", {}) or {}
    return data, participants, guests


def merge_people(participants: dict, guests: dict) -> dict:
    combined = dict(guests)
    combined.update(participants)
    return combined


def extract_bios(people: dict) -> dict[str, str]:
    return {
        name: meta.get("bio", "")
        for name, meta in people.items()
        if isinstance(meta, dict) and meta.get("bio")
    }


def extract_handles(people: dict) -> dict[str, str]:
    return {
        name: meta["github"]
        for name, meta in people.items()
        if isinstance(meta, dict) and meta.get("github")
    }


def resolve_project(projects: dict, slug: str) -> dict:
    if slug not in projects:
        known = sorted(projects.keys())
        raise KeyError(
            f"Unknown project '{slug}'. Known projects: {', '.join(known)}"
        )
    entry = projects[slug]
    path = Path(entry["path"])
    transcript_dir = entry.get("transcript_dir", "meetings")
    return {
        "slug": slug,
        "path": path,
        "transcript_dir": transcript_dir,
        "output_dir": path / transcript_dir,
        "default_participants": entry.get("default_participants", []),
        "cleanup": entry.get("cleanup", "archive-all"),
        "github": entry.get("github"),
    }


def is_github_enabled(project_config: dict) -> bool:
    gh = project_config.get("github")
    return bool(gh and gh.get("repo"))


def discover_registry(
    start_dir: str | Path | None = None,
) -> tuple[Path, str] | None:
    """Search upward for a registry file.

    Returns ``(path, kind)`` where kind is "project" for a per-project
    ``project.yaml`` and "registry" for ``projects.yaml`` /
    ``projects/projects.yaml``. Returns None if nothing is found.
    """
    d = Path(start_dir) if start_dir else Path.cwd()
    d = d.resolve()
    candidates = [
        ("project.yaml", "project"),
        ("projects.yaml", "registry"),
        ("projects/projects.yaml", "registry"),
    ]
    while True:
        for name, kind in candidates:
            p = d / name
            if p.is_file():
                return p, kind
        parent = d.parent
        if parent == d:
            return None
        d = parent


def load_single_project(yaml_path: str | Path) -> tuple[dict, dict, dict]:
    """Build a registry triple from a per-project ``project.yaml``."""
    path = Path(yaml_path).resolve()
    data = load_registry(path)
    directory = path.parent
    people = data.get("collaborators") or data.get("participants") or {}
    slug = data.get("name") or directory.name
    projects = {
        slug: {
            "path": str(directory),
            "transcript_dir": data.get("transcript_dir", "meetings"),
            "default_participants": (
                data.get("default_participants") or list(people.keys())
            ),
            "cleanup": data.get("cleanup", "archive-all"),
            "github": data.get("github"),
        }
    }
    return projects, people, {}


def load_any(yaml_path: str | Path, kind: str) -> tuple[dict, dict, dict]:
    """Load either registry flavour into the same (projects, participants, guests)."""
    if kind == "project":
        return load_single_project(yaml_path)
    return parse_registry(load_registry(yaml_path))


def merge_registries(
    primary: tuple[dict, dict, dict],
    fallback: tuple[dict, dict, dict],
) -> tuple[dict, dict, dict]:
    """Merge people from a fallback registry into a primary one.

    Projects are NOT merged: a project.yaml owns its own project.
    """
    p_projects, p_participants, p_guests = primary
    _, f_participants, f_guests = fallback

    participants = dict(f_participants)
    participants.update(p_participants)

    guests = dict(f_guests)
    guests.update(p_guests)
    for name in p_participants:
        guests.pop(name, None)

    return dict(p_projects), participants, guests


def mark_gitignore_configured(yaml_path: str | Path, slug: str) -> None:
    path = Path(yaml_path)
    text = path.read_text()
    data = yaml.safe_load(text) or {}
    if slug in data and isinstance(data[slug], dict):
        data[slug]["gitignore_configured"] = True
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
