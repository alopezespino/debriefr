"""Archived files are kept unless --archive-ttl-days is given; pruning uses the Trash."""
from __future__ import annotations

import argparse
import inspect
import os
import shutil
import subprocess
import time

import pytest

from debriefr import meeting
from debriefr.cli import _add_transcribe


def _old(path, days=30):
    path.write_text("x")
    t = time.time() - days * 86400
    os.utime(path, (t, t))
    return path


@pytest.fixture
def archive(tmp_path):
    """<tmp>/proj/.audio-archive with one 30-day-old and one fresh file."""
    d = tmp_path / "proj" / ".audio-archive"
    d.mkdir(parents=True)
    old = _old(d / "2026-01-01_old.txt")
    new = d / "2026-10-01_new.txt"
    new.write_text("y")
    return d, old, new


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


def test_cli_ttl_default_is_none():
    parser = argparse.ArgumentParser()
    _add_transcribe(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["transcribe", "rec.m4a"])
    assert args.archive_ttl_days is None


def test_run_meeting_ttl_default_is_none():
    sig = inspect.signature(meeting.run_meeting)
    assert sig.parameters["archive_ttl_days"].default is None


@pytest.mark.parametrize("mode", ["archive-all", "archive-audio"])
def test_cleanup_without_ttl_keeps_old_archive_files(tmp_path, archive, mode):
    d, old, new = archive
    audio, js, txt, out = _meeting_files(tmp_path)

    meeting._apply_cleanup(mode, audio, js, txt, out, tmp_path / "proj", None)

    assert old.exists() and new.exists()
    assert (d / "rec.m4a").exists()


def test_prune_with_ttl_moves_old_files_to_trash(archive, monkeypatch):
    d, old, new = archive
    calls = []
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/usr/bin/trash" if name == "trash" else None)

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(subprocess, "run", fake_run)

    meeting.prune_archive(d, 7)

    assert calls == [["trash", str(old)]]
    # the fake trash command moves nothing: proves no unlink happened
    assert old.exists() and new.exists()


def test_prune_without_trash_command_refuses(archive, monkeypatch, capsys):
    d, old, new = archive
    monkeypatch.setattr(shutil, "which", lambda name: None)

    def fail_run(argv, **kw):
        raise AssertionError(f"no command should run: {argv}")
    monkeypatch.setattr(subprocess, "run", fail_run)

    meeting.prune_archive(d, 7)

    assert old.exists() and new.exists()
    err = capsys.readouterr().err
    assert "no Trash command found" in err
    assert "refusing to delete 1 archived file" in err


def test_prune_trash_failure_keeps_file(archive, monkeypatch, capsys):
    d, old, _ = archive
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/usr/bin/trash" if name == "trash" else None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "denied"),
    )

    meeting.prune_archive(d, 7)

    assert old.exists()
    assert "could not move" in capsys.readouterr().err
