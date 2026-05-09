"""debriefr — meeting audio to structured transcript, summary, and GitHub issues."""
__version__ = "0.2.0"

from .enroll import enroll_speaker
from .meeting import run_meeting
from .registry import (
    discover_registry,
    extract_bios,
    extract_handles,
    is_github_enabled,
    load_registry,
    merge_people,
    parse_registry,
    resolve_project,
)

__all__ = [
    "run_meeting",
    "enroll_speaker",
    "discover_registry",
    "extract_bios",
    "extract_handles",
    "is_github_enabled",
    "load_registry",
    "merge_people",
    "parse_registry",
    "resolve_project",
    "__version__",
]
