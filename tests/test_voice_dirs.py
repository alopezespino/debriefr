"""Default voice folders: admin/enrollments and admin/audio_samples.

Each case runs through the CLI (``debriefr transcribe`` and ``debriefr
enroll``) with the heavy steps replaced, so it checks the paths the commands
actually pass on, not only the resolver.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from debriefr import cli, decoder, enroll, meeting
from debriefr.pipeline import deserialize_diarization


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A project folder used as the working directory, with no registry."""
    root = tmp_path / "repo"
    root.mkdir()
    audio = root / "standup.wav"
    audio.write_bytes(b"RIFF")
    monkeypatch.chdir(root)
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.setattr(decoder, "ensure_audio_decoder", lambda: None)
    return root


def _transcribe(monkeypatch, *flags, result=None):
    seen = {}

    def fake_run_meeting(**kwargs):
        seen.update(kwargs)
        return result or {"status": "complete"}

    monkeypatch.setattr(meeting, "run_meeting", fake_run_meeting)
    monkeypatch.setattr(sys, "argv", [
        "debriefr", "transcribe", "standup.wav", "--output-dir", "meetings",
        "--participants", "Alice", "Bob", "--no-summary", *flags])
    cli.main()
    return seen


def _enroll(monkeypatch, *flags):
    seen = {}

    def fake_enroll_speaker(name, audio, out_dir=None, replace=False):
        seen["out_dir"] = out_dir

    monkeypatch.setattr(enroll, "enroll_speaker", fake_enroll_speaker)
    monkeypatch.setattr(sys, "argv", [
        "debriefr", "enroll", "--name", "Alice", "--audio", "clip.wav", *flags])
    cli.main()
    return seen


# Case (a): the new folders exist.

def test_transcribe_uses_admin_folders(repo, monkeypatch, capsys):
    (repo / "admin" / "enrollments").mkdir(parents=True)
    (repo / "admin" / "audio_samples").mkdir()
    (repo / "enrollments").mkdir()       # an old copy left behind is ignored
    seen = _transcribe(monkeypatch)
    assert Path(seen["enrollments_dir"]) == Path("admin/enrollments")
    assert Path(seen["samples_dir"]) == Path("admin/audio_samples")
    assert "old voice folder layout" not in capsys.readouterr().err


def test_enroll_uses_admin_folder(repo, monkeypatch, capsys):
    (repo / "admin" / "enrollments").mkdir(parents=True)
    seen = _enroll(monkeypatch)
    assert Path(seen["out_dir"]) == Path("admin/enrollments")
    assert "old voice folder layout" not in capsys.readouterr().err


# Case (b): only the old folders exist; they are used, with one notice.

def test_transcribe_falls_back_to_old_folders_with_notice(repo, monkeypatch, capsys):
    (repo / "enrollments").mkdir()
    (repo / "samples").mkdir()
    seen = _transcribe(monkeypatch)
    assert Path(seen["enrollments_dir"]) == Path("enrollments")
    assert Path(seen["samples_dir"]) == Path("samples")
    err = capsys.readouterr().err
    assert err.count("old voice folder layout") == 1
    assert "enrollments/ -> admin/enrollments/" in err
    assert "samples/ -> admin/audio_samples/" in err
    assert not (repo / "admin").exists()


def test_enroll_falls_back_to_old_folder_with_notice(repo, monkeypatch, capsys):
    (repo / "enrollments").mkdir()
    seen = _enroll(monkeypatch)
    assert Path(seen["out_dir"]) == Path("enrollments")
    err = capsys.readouterr().err
    assert err.count("old voice folder layout") == 1
    assert "admin/enrollments/" in err


def test_half_moved_repo_resolves_each_folder_alone(repo, monkeypatch, capsys):
    (repo / "admin" / "enrollments").mkdir(parents=True)
    (repo / "samples").mkdir()
    seen = _transcribe(monkeypatch)
    assert Path(seen["enrollments_dir"]) == Path("admin/enrollments")
    assert Path(seen["samples_dir"]) == Path("samples")
    err = capsys.readouterr().err
    assert "samples/ -> admin/audio_samples/" in err
    assert "enrollments/ -> " not in err


# Case (c): neither exists; the new folders are created under admin/.

def test_transcribe_creates_admin_folders(repo, monkeypatch, capsys):
    seen = _transcribe(monkeypatch)
    assert Path(seen["enrollments_dir"]) == Path("admin/enrollments")
    assert Path(seen["samples_dir"]) == Path("admin/audio_samples")
    assert (repo / "admin" / "enrollments").is_dir()
    assert (repo / "admin" / "audio_samples").is_dir()
    assert not (repo / "enrollments").exists()
    assert not (repo / "samples").exists()
    assert "old voice folder layout" not in capsys.readouterr().err


def test_enroll_creates_admin_folder(repo, monkeypatch):
    seen = _enroll(monkeypatch)
    assert Path(seen["out_dir"]) == Path("admin/enrollments")
    assert (repo / "admin" / "enrollments").is_dir()


# An explicit flag always wins.

def test_explicit_flags_win_over_admin_folders(repo, monkeypatch, capsys):
    (repo / "admin" / "enrollments").mkdir(parents=True)
    (repo / "admin" / "audio_samples").mkdir()
    shared = repo.parent / "shared"
    seen = _transcribe(monkeypatch,
                       "--enrollments-dir", str(shared / "enr"),
                       "--samples-dir", str(shared / "clips"))
    assert Path(seen["enrollments_dir"]) == shared / "enr"
    assert Path(seen["samples_dir"]) == shared / "clips"
    assert "old voice folder layout" not in capsys.readouterr().err


def test_explicit_out_dir_wins_over_admin_folder(repo, monkeypatch, capsys):
    (repo / "admin" / "enrollments").mkdir(parents=True)
    seen = _enroll(monkeypatch, "--out-dir", "elsewhere")
    assert Path(seen["out_dir"]) == Path("elsewhere")
    assert "old voice folder layout" not in capsys.readouterr().err


def test_explicit_flags_do_not_create_admin(repo, monkeypatch):
    _transcribe(monkeypatch, "--enrollments-dir", "e", "--samples-dir", "s")
    assert not (repo / "admin").exists()


# The printed hint names the folders in use.

def test_needs_sampling_hint_names_admin_samples(repo, monkeypatch, capsys):
    result = {"status": "needs_sampling", "cache_path": "c.json",
              "clips": {}, "unknowns": []}
    _transcribe(monkeypatch, result=result)
    out = capsys.readouterr().out
    assert "--audio admin/audio_samples/<SPEAKER>.wav" in out
    assert " samples/<SPEAKER>.wav" not in out


def test_needs_sampling_hint_carries_explicit_enrollments(repo, monkeypatch, capsys):
    result = {"status": "needs_sampling", "cache_path": "c.json",
              "clips": {}, "unknowns": []}
    _transcribe(monkeypatch, "--enrollments-dir", "/x/enr",
                "--samples-dir", "/x/clips", result=result)
    out = capsys.readouterr().out
    assert "--audio /x/clips/<SPEAKER>.wav --out-dir /x/enr" in out


# Library defaults (no CLI) resolve the same way.

def test_run_meeting_default_reads_admin_enrollments(repo, monkeypatch):
    (repo / "admin" / "enrollments").mkdir(parents=True)
    seen = {}

    def stop(enrollments_dir, participants):
        seen["dir"] = enrollments_dir
        raise RuntimeError("stop")

    monkeypatch.setattr(meeting, "audio_decoder_error", lambda: None, raising=False)
    monkeypatch.setattr(meeting, "transcribe", lambda *a, **k: ([], "en"))
    monkeypatch.setattr(meeting, "diarize", lambda *a, **k: (
        deserialize_diarization([{"start": 0.0, "end": 1.0,
                                  "speaker": "SPEAKER_00"}]), None))
    monkeypatch.setattr(meeting, "collect_enrollments", stop)
    with pytest.raises(RuntimeError, match="stop"):
        meeting.run_meeting(audio=str(repo / "standup.wav"),
                            output_dir=str(repo / "meetings"),
                            participants=["Alice"], date="2026-01-01",
                            skip_sampling=True, skip_summary=True)
    assert Path(seen["dir"]) == Path("admin/enrollments")
