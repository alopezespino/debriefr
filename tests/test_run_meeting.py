"""run_meeting with the heavy steps (Whisper, pyannote, Claude) mocked out."""
from __future__ import annotations

import pytest

from debriefr import meeting
from debriefr.pipeline import deserialize_diarization

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "Hello."},
    {"start": 2.0, "end": 4.0, "text": "Hi there."},
]
TURNS = [
    {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
    {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A project dir, a wav, and call counters for the mocked heavy steps."""
    proj = tmp_path / "myproject"
    out = proj / "meetings"
    audio = tmp_path / "inbox" / "standup.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"RIFF")
    calls = {"transcribe": 0, "diarize": 0}

    def fake_transcribe(wav, backend, model, language):
        calls["transcribe"] += 1
        return [dict(s) for s in SEGMENTS], "en"

    def fake_diarize(wav, num_speakers, hf_token):
        calls["diarize"] += 1
        return deserialize_diarization(TURNS), None

    monkeypatch.setattr(meeting, "transcribe", fake_transcribe)
    monkeypatch.setattr(meeting, "diarize", fake_diarize)
    # The decoder pre-flight is tested on its own; here it always passes.
    monkeypatch.setattr(meeting, "audio_decoder_error", lambda: None, raising=False)
    monkeypatch.setattr(meeting, "summarize",
                        lambda *a, **k: "reference: weekly_sync\n\nSummary body.")
    monkeypatch.setattr(meeting, "_secret_store_get", lambda service: "test-key")

    return {
        "tmp": tmp_path, "proj": proj, "out": out, "audio": audio,
        "calls": calls,
        "kwargs": dict(
            audio=str(audio), output_dir=str(out), participants=["Alice", "Bob"],
            enrollments_dir=str(tmp_path / "enrollments"),
            samples_dir=str(tmp_path / "samples"),
            date="2026-10-21", skip_sampling=True,
        ),
    }


def test_no_summary_archive_all_keeps_transcripts(env):
    res = meeting.run_meeting(
        **env["kwargs"], skip_summary=True,
        cleanup="archive-all", archive_base=str(env["proj"]),
    )

    archive = env["proj"] / ".audio-archive"
    assert res["summary_md"] is None
    assert res["transcript_txt"].parent == env["out"]
    assert res["transcript_txt"].exists() and res["transcript_json"].exists()
    assert not (archive / res["transcript_txt"].name).exists()
    assert not (archive / res["transcript_json"].name).exists()
    # the audio is still archived
    assert (archive / "standup.wav").exists()
    assert not env["audio"].exists()


def test_summary_archive_all_still_archives_transcripts(env):
    res = meeting.run_meeting(
        **env["kwargs"], cleanup="archive-all", archive_base=str(env["proj"]),
    )

    archive = env["proj"] / ".audio-archive"
    assert res["summary_md"] == env["out"] / "2026-10-21_weekly_sync.md"
    assert res["summary_md"].exists()
    assert (archive / "2026-10-21_weekly_sync.txt").exists()
    assert (archive / "2026-10-21_weekly_sync.json").exists()
    assert (archive / "standup.wav").exists()
    assert sorted(p.name for p in env["out"].iterdir()) == ["2026-10-21_weekly_sync.md"]
