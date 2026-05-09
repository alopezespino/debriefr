"""Whisper backends for debriefr.

Four backends are supported:
  - ``groq``: Cloud API via Groq (free tier). Requires ``GROQ_API_KEY``.
  - ``openai``: Cloud API via OpenAI Whisper. Requires ``OPENAI_API_KEY``.
  - ``mlx``: Local Apple Silicon inference. Requires ``pip install debriefr[mlx]``.
  - ``faster-whisper``: Local cross-platform inference. Requires ``pip install debriefr[local]``.

Use :func:`get_backend` to construct one by name, or pass ``"auto"`` to let
debriefr pick the first available option.
"""
from __future__ import annotations

import os
import platform

from .base import WhisperBackend

__all__ = ["WhisperBackend", "get_backend"]


def get_backend(name: str = "auto", **kwargs) -> WhisperBackend:
    """Construct a Whisper backend by name.

    With ``name="auto"``, picks the first backend that is available:

    1. ``groq`` (if ``GROQ_API_KEY`` is set)
    2. ``mlx`` (if on Apple Silicon and ``mlx-whisper`` is installed)
    3. ``faster-whisper`` (if installed)
    4. ``openai`` (if ``OPENAI_API_KEY`` is set)

    Otherwise raises a ``RuntimeError`` listing the options.
    """
    if name == "auto":
        name = _pick_auto()

    if name == "groq":
        from .groq import GroqBackend
        return GroqBackend(**kwargs)
    if name == "openai":
        from .openai import OpenAIBackend
        return OpenAIBackend(**kwargs)
    if name == "mlx":
        from .mlx import MLXBackend
        return MLXBackend(**kwargs)
    if name in ("faster-whisper", "faster_whisper"):
        from .faster_whisper import FasterWhisperBackend
        return FasterWhisperBackend(**kwargs)
    raise ValueError(
        f"Unknown backend: {name!r}. "
        f"Choose from: groq, openai, mlx, faster-whisper, auto."
    )


def _pick_auto() -> str:
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import mlx_whisper  # noqa: F401
            return "mlx"
        except ImportError:
            pass
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except ImportError:
        pass
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    raise RuntimeError(
        "No Whisper backend available. Options:\n"
        "  1. export GROQ_API_KEY=... (sign up: https://console.groq.com)\n"
        "  2. pip install debriefr[mlx]  (Apple Silicon only)\n"
        "  3. pip install debriefr[local]  (cross-platform local inference)\n"
        "  4. export OPENAI_API_KEY=...  (paid, $0.006/min)"
    )
