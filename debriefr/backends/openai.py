"""OpenAI Whisper API backend.

Uses the standard OpenAI endpoint (``whisper-1``). Files over ~24 MB are split
with ffmpeg into 10-minute chunks, transcribed sequentially, and stitched back
together with time offsets so the downstream alignment still sees one
continuous segment stream.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .base import WhisperBackend


class OpenAIBackend(WhisperBackend):
    """Whisper via the OpenAI hosted API."""

    DEFAULT_MODEL = "whisper-1"
    MAX_SIZE = 24 * 1024 * 1024
    CHUNK_SECONDS = 600

    def __init__(self, model: str | None = None, api_key: str | None = None):
        from openai import OpenAI
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Get one at https://platform.openai.com "
                "and export OPENAI_API_KEY=..."
            )
        self.client = OpenAI(api_key=key)
        self.model = model or self.DEFAULT_MODEL

    def transcribe(self, audio_path: Path, language: str | None = None) -> dict:
        if audio_path.stat().st_size <= self.MAX_SIZE:
            return self._transcribe_single(audio_path, language, offset=0.0)
        return self._transcribe_chunked(audio_path, language)

    def _transcribe_single(self, audio_path: Path, language: str | None, offset: float) -> dict:
        with open(audio_path, "rb") as f:
            kwargs = {"model": self.model, "file": f, "response_format": "verbose_json"}
            if language:
                kwargs["language"] = language
            resp = self.client.audio.transcriptions.create(**kwargs)
        return {
            "language": resp.language,
            "segments": [
                {"start": s.start + offset, "end": s.end + offset, "text": s.text}
                for s in resp.segments
            ],
        }

    def _transcribe_chunked(self, audio_path: Path, language: str | None) -> dict:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        duration = _get_duration(audio_path, ffmpeg)
        segments: list[dict] = []
        lang: str | None = None
        with tempfile.TemporaryDirectory(prefix="debriefr_chunk_") as tmp:
            tmp_dir = Path(tmp)
            num_chunks = int(duration // self.CHUNK_SECONDS) + 1
            print(f"      splitting {audio_path.name} into {num_chunks} x {self.CHUNK_SECONDS}s chunks")
            for i in range(num_chunks):
                start = i * self.CHUNK_SECONDS
                chunk_path = tmp_dir / f"chunk_{i}.wav"
                subprocess.check_call([
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(start), "-t", str(self.CHUNK_SECONDS),
                    "-i", str(audio_path),
                    "-vn", "-ac", "1", "-ar", "16000",
                    str(chunk_path),
                ])
                if not chunk_path.exists() or chunk_path.stat().st_size == 0:
                    continue
                result = self._transcribe_single(chunk_path, language, offset=float(start))
                segments.extend(result["segments"])
                lang = lang or result["language"]
        return {"language": lang or language or "en", "segments": segments}


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _get_duration(audio_path: Path, ffmpeg: str) -> float:
    """Return audio duration in seconds by parsing ffmpeg's stderr metadata line."""
    r = subprocess.run(
        [ffmpeg, "-i", str(audio_path)],
        capture_output=True, text=True,
    )
    m = _DURATION_RE.search(r.stderr)
    if not m:
        raise RuntimeError(f"Could not determine duration of {audio_path}")
    h, mm, ss = m.groups()
    return int(h) * 3600 + int(mm) * 60 + float(ss)
