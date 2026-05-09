"""faster-whisper (CTranslate2) backend.

Optional: install with ``pip install debriefr[local]``. Runs on CPU by default;
detects CUDA automatically if available. Good cross-platform choice when cloud
backends aren't an option.
"""
from __future__ import annotations

from pathlib import Path

from .base import WhisperBackend


class FasterWhisperBackend(WhisperBackend):
    """Whisper via faster-whisper (CTranslate2 runtime)."""

    DEFAULT_MODEL = "large-v3"

    def __init__(
        self,
        model: str | None = None,
        device: str = "auto",
        compute_type: str = "auto",
    ):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper is not installed. Install with:  "
                "pip install debriefr[local]"
            ) from e
        import torch
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        self.model = WhisperModel(
            model or self.DEFAULT_MODEL,
            device=device,
            compute_type=compute_type,
        )

    def transcribe(self, audio_path: Path, language: str | None = None) -> dict:
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
        )
        segments = [
            {"start": s.start, "end": s.end, "text": s.text}
            for s in segments_iter
        ]
        return {"language": info.language, "segments": segments}
