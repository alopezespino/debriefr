"""Apple Silicon MLX Whisper backend.

Optional: install with ``pip install debriefr[mlx]``. Uses ``mlx-whisper`` and
Apple's MLX framework; not available on non-Apple-Silicon platforms.
"""
from __future__ import annotations

from pathlib import Path

from .base import WhisperBackend


class MLXBackend(WhisperBackend):
    """Whisper via Apple MLX on Apple Silicon."""

    DEFAULT_MODEL = "large-v3"

    def __init__(self, model: str | None = None):
        try:
            import mlx_whisper
        except ImportError as e:
            raise RuntimeError(
                "mlx-whisper is not installed. Install with:  "
                "pip install debriefr[mlx]  (Apple Silicon only)."
            ) from e
        self._mlx_whisper = mlx_whisper
        self.model = model or self.DEFAULT_MODEL

    def transcribe(self, audio_path: Path, language: str | None = None) -> dict:
        result = self._mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=f"mlx-community/whisper-{self.model}-mlx",
            language=language,
            word_timestamps=False,
        )
        return {
            "language": result["language"],
            "segments": [
                {"start": s["start"], "end": s["end"], "text": s["text"]}
                for s in result["segments"]
            ],
        }
