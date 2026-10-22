"""Decoder pre-flight: restart once with the FFmpeg 8 library path, else exit."""
from __future__ import annotations

import os
import sys

import pytest

from debriefr import decoder


class Exec(Exception):
    """Raised by the patched os.execve so the test can inspect its arguments."""


@pytest.fixture
def broken(monkeypatch):
    monkeypatch.setattr(decoder, "audio_decoder_error",
                        lambda: "pyannote cannot load its audio decoder")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv(decoder.REEXEC_MARKER, raising=False)
    monkeypatch.delenv("DYLD_LIBRARY_PATH", raising=False)
    seen = {}

    def fake_execve(path, argv, env):
        seen.update(path=path, argv=argv, env=env)
        raise Exec()
    monkeypatch.setattr(os, "execve", fake_execve)
    return seen


@pytest.fixture
def keg(tmp_path, monkeypatch):
    lib = tmp_path / "brew" / "opt" / "ffmpeg@8" / "lib"
    lib.mkdir(parents=True)
    (lib / "libavutil.60.dylib").write_bytes(b"")
    monkeypatch.setenv("HOMEBREW_PREFIX", str(tmp_path / "brew"))
    return lib


def test_find_ffmpeg_lib_prefers_homebrew_prefix(keg):
    assert decoder.find_ffmpeg_lib() == keg


def test_restarts_once_with_library_path(broken, keg):
    with pytest.raises(Exec):
        decoder.ensure_audio_decoder()

    assert broken["path"] == sys.executable
    assert broken["argv"][0] == sys.executable
    assert broken["argv"][1:] == sys.orig_argv[1:]
    assert broken["env"]["DYLD_LIBRARY_PATH"] == str(keg)
    assert broken["env"][decoder.REEXEC_MARKER] == "1"


def test_does_not_restart_twice(broken, keg, monkeypatch):
    monkeypatch.setenv(decoder.REEXEC_MARKER, "1")

    with pytest.raises(SystemExit) as exc:
        decoder.ensure_audio_decoder()
    assert f"Restarted with DYLD_LIBRARY_PATH={keg}" in str(exc.value)
    assert broken == {}


def test_without_keg_exits_with_brew_hint(broken, monkeypatch):
    monkeypatch.setattr(decoder, "find_ffmpeg_lib", lambda: None)

    with pytest.raises(SystemExit) as exc:
        decoder.ensure_audio_decoder()
    msg = str(exc.value)
    assert "brew install ffmpeg@8" in msg
    assert "stopping before any transcription" in msg
    assert broken == {}


def test_linux_never_restarts(broken, keg, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(SystemExit) as exc:
        decoder.ensure_audio_decoder()
    assert "LD_LIBRARY_PATH" in str(exc.value)
    assert broken == {}


def test_working_decoder_is_a_no_op(broken, monkeypatch):
    monkeypatch.setattr(decoder, "audio_decoder_error", lambda: None)
    decoder.ensure_audio_decoder()
    assert broken == {}


def test_audio_decoder_error_reads_pyannote_io(monkeypatch):
    from pyannote.audio.core import io as pa_io

    monkeypatch.setattr(pa_io, "AudioDecoder", object, raising=False)
    assert decoder.audio_decoder_error() is None
    monkeypatch.delattr(pa_io, "AudioDecoder")
    assert "audio decoder" in decoder.audio_decoder_error()


@pytest.mark.parametrize("argv", [
    ["debriefr", "transcribe", "standup.wav", "--output-dir", "out",
     "--participants", "Alice"],
    ["debriefr", "enroll", "--name", "Alice", "--audio", "alice.wav"],
])
def test_cli_stops_before_any_work(broken, monkeypatch, tmp_path, argv):
    monkeypatch.setattr(decoder, "find_ffmpeg_lib", lambda: None)
    monkeypatch.chdir(tmp_path)

    def must_not_run(*a, **k):
        raise AssertionError("pipeline should not start")
    import debriefr.enroll
    import debriefr.meeting
    monkeypatch.setattr(debriefr.meeting, "run_meeting", must_not_run)
    monkeypatch.setattr(debriefr.enroll, "enroll_speaker", must_not_run)

    from debriefr.cli import main
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        main()
    assert "audio decoder" in str(exc.value)
