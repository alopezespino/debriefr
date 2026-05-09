"""Groq-hosted Whisper backend.

Uses the OpenAI-compatible Groq endpoint. Free tier is generous for personal
use; sign up at https://console.groq.com.
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import WhisperBackend


class GroqBackend(WhisperBackend):
    """Whisper via Groq's hosted inference API."""

    DEFAULT_MODEL = "whisper-large-v3"
    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        from openai import OpenAI  # shared client for both Groq and OpenAI
        key = api_key or os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY not set. Sign up at https://console.groq.com "
                "and export GROQ_API_KEY=..."
            )
        self.client = OpenAI(api_key=key, base_url=self.BASE_URL)
        self.model = model or self.DEFAULT_MODEL

    def transcribe(self, audio_path: Path, language: str | None = None) -> dict:
        with open(audio_path, "rb") as f:
            kwargs = {
                "model": self.model,
                "file": f,
                "response_format": "verbose_json",
            }
            if language:
                kwargs["language"] = language
            resp = self.client.audio.transcriptions.create(**kwargs)
        return {
            "language": resp.language,
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in resp.segments
            ],
        }
