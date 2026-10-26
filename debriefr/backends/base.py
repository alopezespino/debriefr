"""Whisper backend interface.

All backends return the same output shape so the downstream alignment code
(`align_transcript` in `pipeline.py`) can consume any of them uniformly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class WhisperBackend(ABC):
    """Abstract Whisper backend."""

    @abstractmethod
    def transcribe(self, audio_path: Path, language: str | None = None,
                   prompt: str | None = None) -> dict:
        """Transcribe an audio file.

        ``prompt`` is Whisper's initial prompt (a glossary of proper nouns
        that biases spelling); backends pass it as their API names it.

        Returns a dict of the form::

            {
                "language": "<ISO code>",
                "segments": [
                    {"start": float, "end": float, "text": str},
                    ...
                ],
            }
        """
        ...
