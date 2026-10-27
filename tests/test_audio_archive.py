"""Per-project `audio_archive:` chooses the exact folder cleanup archives into."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time

import pytest
import yaml

from debriefr import meeting
from debriefr.registry import load_single_project, resolve_project


def _meeting_files(tmp_path):
    out = tmp_path / "proj" / "meetings"
    out.mkdir(parents=True, exist_ok=True)
    audio = tmp_path / "rec.m4a"
    audio.write_bytes(b"a")
    js = out / "2026-10-21_m.json"
    js.write_text("{}")
    txt = out / "2026-10-21_m.txt"
    txt.write_text("t")
    return audio, js, txt, out


def _project(tmp_path, **extra):
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    data = {"collaborators": {"Alice": {"bio": "x"}}, **extra}
    (proj / "project.yaml").write_text(yaml.safe_dump(data))
    projects, _, _ = load_single_project(proj / "project.yaml")
    return resolve_project(projects, "proj")


@pytest.mark.parametrize("mode", ["archive-all", "archive-audio"])
def test_key_set_archives_into_exact_folder(tmp_path, mode):
    target = tmp_path / "elsewhere" / "audio"
    config = _project(tmp_path, audio_archive=str(target))
    assert config["audio_archive"] == target
    audio, js, txt, out = _meeting_files(tmp_path)

    meeting._apply_cleanup(mode, audio, js, txt, out, tmp_path / "proj", None,
                           audio_archive=config["audio_archive"])

    assert (target / "rec.m4a").exists()
    assert not (target / ".audio-archive").exists()
    assert not (tmp_path / "proj" / ".audio-archive").exists()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    if mode == "archive-all":
        assert (target / js.name).exists() and (target / txt.name).exists()


def test_key_set_prunes_that_folder(tmp_path, monkeypatch):
    target = tmp_path / "elsewhere"
    target.mkdir()
    old = target / "2026-01-01_old.txt"
    old.write_text("x")
    t = time.time() - 30 * 86400
    os.utime(old, (t, t))
    calls = []
    monkeypatch.setattr(shutil, "which",
                        lambda n: "/usr/bin/trash" if n == "trash" else None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: calls.append(argv)
        or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    audio, js, txt, out = _meeting_files(tmp_path)

    meeting._apply_cleanup("archive-audio", audio, js, txt, out,
                           tmp_path / "proj", 7, audio_archive=target)

    assert calls == [["trash", str(old)]]


def test_key_absent_keeps_project_audio_archive(tmp_path):
    config = _project(tmp_path)
    assert config["audio_archive"] is None
    audio, js, txt, out = _meeting_files(tmp_path)

    meeting._apply_cleanup("archive-all", audio, js, txt, out, config["path"],
                           None, audio_archive=config["audio_archive"])

    assert (config["path"] / ".audio-archive" / "rec.m4a").exists()


def test_tilde_is_expanded(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = _project(tmp_path, audio_archive="~/Archive/proj")
    assert config["audio_archive"] == home / "Archive" / "proj"

    projects = {"proj": {"path": str(tmp_path / "proj"),
                         "audio_archive": "~/Archive/proj"}}
    assert resolve_project(projects, "proj")["audio_archive"] == (
        home / "Archive" / "proj")
