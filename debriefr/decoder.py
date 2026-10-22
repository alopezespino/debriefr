"""Pre-flight check for pyannote's audio decoder (torchcodec + FFmpeg).

pyannote.audio imports torchcodec's ``AudioDecoder`` at import time and only
warns if that fails. The failure then surfaces as ``NameError: name
'AudioDecoder' is not defined`` at the first audio file pyannote reads, which
is diarization, after Whisper has already run. The usual cause on macOS is a
Homebrew FFmpeg newer than torchcodec supports (torchcodec 0.11 loads FFmpeg 4
to 8; Homebrew's ``ffmpeg`` moved to 9).

:func:`audio_decoder_error` detects that before any work starts.
:func:`ensure_audio_decoder` is the CLI's pre-flight: on macOS, if a keg-only
``ffmpeg@4`` to ``ffmpeg@8`` is installed, it re-executes the process once with
``DYLD_LIBRARY_PATH`` pointing at it (dyld reads that variable only at process
start, so it cannot be set from inside a running process); otherwise it exits
with a message naming the fix.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SUPPORTED_FFMPEG = (8, 7, 6, 5, 4)
REEXEC_MARKER = "DEBRIEFR_DECODER_REEXEC"


def audio_decoder_error() -> str | None:
    """Return why pyannote cannot decode audio files, or None if it can."""
    from pyannote.audio.core import io as pa_io

    if getattr(pa_io, "AudioDecoder", None) is not None:
        return None
    return ("pyannote cannot load its audio decoder: torchcodec could not load "
            "a supported FFmpeg (major versions 4 to 8).")


def find_ffmpeg_lib() -> Path | None:
    """Library dir of a Homebrew keg-only ``ffmpeg@N`` (N = 8 down to 4), or None."""
    prefixes = [os.environ.get("HOMEBREW_PREFIX"), "/opt/homebrew", "/usr/local"]
    for prefix in dict.fromkeys(p for p in prefixes if p):
        for major in SUPPORTED_FFMPEG:
            lib = Path(prefix) / "opt" / f"ffmpeg@{major}" / "lib"
            if any(lib.glob("libavutil.*.dylib")):
                return lib
    return None


def decoder_failure_message(err: str) -> str:
    """``err`` plus the fix that applies on this machine."""
    lib = find_ffmpeg_lib() if sys.platform == "darwin" else None
    if lib is not None and os.environ.get(REEXEC_MARKER) == "1":
        fix = (f"Restarted with DYLD_LIBRARY_PATH={lib} and the decoder still "
               f"does not load; check that FFmpeg install (brew reinstall "
               f"{lib.parent.name}).")
    elif lib is not None:
        fix = (f"Fix: start the process with DYLD_LIBRARY_PATH={lib} set "
               f"(the debriefr CLI does this itself).")
    elif sys.platform == "darwin":
        fix = ("Fix: brew install ffmpeg@8, then rerun; the debriefr CLI then "
               "sets DYLD_LIBRARY_PATH=<brew prefix>/opt/ffmpeg@8/lib itself.")
    else:
        fix = ("Fix: install FFmpeg 4 to 8 shared libraries and make them "
               "loadable (LD_LIBRARY_PATH).")
    return (f"{err}\npyannote reads audio through it for diarization and "
            f"speaker embeddings; stopping before any transcription.\n{fix}")


def ensure_audio_decoder() -> None:
    """CLI pre-flight: return if the decoder loads; else restart once or exit."""
    err = audio_decoder_error()
    if err is None:
        return
    lib = find_ffmpeg_lib() if sys.platform == "darwin" else None
    if lib is not None and os.environ.get(REEXEC_MARKER) != "1":
        env = dict(os.environ)
        env["DYLD_LIBRARY_PATH"] = os.pathsep.join(
            p for p in (str(lib), env.get("DYLD_LIBRARY_PATH")) if p
        )
        env[REEXEC_MARKER] = "1"
        print(f"[debriefr] audio decoder unavailable; restarting with "
              f"DYLD_LIBRARY_PATH={lib}", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        os.execve(sys.executable, [sys.executable, *sys.orig_argv[1:]], env)
    sys.exit(decoder_failure_message(err))
