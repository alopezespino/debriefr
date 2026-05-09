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


def discover_registry(start_dir: str | Path | None = None) -> Path | None:
    d = Path(start_dir) if start_dir else Path.cwd()
    d = d.resolve()
    candidates = ["projects.yaml", "projects/projects.yaml"]
    while True:
        for c in candidates:
            p = d / c
            if p.is_file():
                return p
        parent = d.parent
        if parent == d:
            return None
        d = parent


def mark_gitignore_configured(yaml_path: str | Path, slug: str) -> None:
    path = Path(yaml_path)
    text = path.read_text()
    data = yaml.safe_load(text) or {}
    if slug in data and isinstance(data[slug], dict):
        data[slug]["gitignore_configured"] = True
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
