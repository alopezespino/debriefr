"""Default locations of the voice folders (enrollments and audio samples).

The folders live under ``admin/`` in a project repository:

- ``admin/enrollments/`` holds one speaker embedding per person (``<Name>.npy``).
- ``admin/audio_samples/`` holds the voice clips (``.wav``) used to enroll.

Older repositories keep them at the top level as ``enrollments/`` and
``samples/``. When no folder is given explicitly, each one is resolved
against the working directory, in this order:

1. the new folder under ``admin/``, when it exists;
2. the old top-level folder, when it exists, with one printed notice that
   names the new place;
3. otherwise the new folder, created under ``admin/``.

An explicit path (a CLI flag or a function argument) is always used as given.
Each folder is resolved on its own, so a repository where only one of the two
has been moved still finds both.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

NEW_ENROLLMENTS = Path("admin") / "enrollments"
OLD_ENROLLMENTS = Path("enrollments")
NEW_SAMPLES = Path("admin") / "audio_samples"
OLD_SAMPLES = Path("samples")


def _resolve_one(explicit: str | Path | None, new: Path, old: Path,
                 base: Path, legacy: list[tuple[Path, Path]]) -> Path:
    if explicit is not None:
        return Path(explicit)
    if (base / new).is_dir():
        return new
    if (base / old).is_dir():
        legacy.append((old, new))
        return old
    (base / new).mkdir(parents=True, exist_ok=True)
    return new


def resolve_voice_dirs(
    enrollments_dir: str | Path | None = None,
    samples_dir: str | Path | None = None,
    *,
    samples: bool = True,
    base: str | Path | None = None,
    stream: TextIO | None = None,
) -> tuple[Path, Path | None]:
    """Return ``(enrollments_dir, samples_dir)`` for this run.

    ``samples=False`` resolves only the enrollments folder (``debriefr
    enroll``) and returns ``None`` for the samples folder. Paths are relative
    to ``base`` (default: the working directory) unless given explicitly.
    When an old top-level folder is used, one notice naming the new place is
    printed to ``stream`` (default: stderr).
    """
    base = Path(base) if base is not None else Path.cwd()
    legacy: list[tuple[Path, Path]] = []
    enroll = _resolve_one(enrollments_dir, NEW_ENROLLMENTS, OLD_ENROLLMENTS,
                          base, legacy)
    samp = (_resolve_one(samples_dir, NEW_SAMPLES, OLD_SAMPLES, base, legacy)
            if samples else None)
    if legacy:
        moves = ", ".join(f"{old}/ -> {new}/" for old, new in legacy)
        print(f"[debriefr] using the old voice folder layout; the new place "
              f"is under admin/: {moves}", file=stream or sys.stderr)
    return enroll, samp
