"""Meeting audio to transcript with speaker identities and timestamps.

Steps:

1. Whisper transcription (segments with timestamps + text) via the selected backend.
2. pyannote speaker diarization (who spoke when, anonymous labels).
3. pyannote speaker embedding per cluster, cosine-matched to enrolled voices.
4. Align whisper segments to diarization turns; write JSON + readable TXT.

Each step is exposed as a public function so callers (e.g. run_meeting) can
run them individually -- for instance, to pause between diarization and
identification for interactive speaker labeling.

Prereqs:

- HuggingFace token with access to pyannote models:
    - ``pyannote/speaker-diarization-3.1``
    - ``pyannote/segmentation-3.0``
    - ``pyannote/wespeaker-voxceleb-resnet34-LM``
- Enrollments built with ``debriefr enroll`` (optional; without them, speakers
  stay as ``SPEAKER_00``, ``SPEAKER_01``, ...).
- A Whisper backend selected (see :mod:`debriefr.backends`).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from pyannote.audio import Inference, Model, Pipeline
from pyannote.core import Annotation, Segment

from .backends import WhisperBackend, get_backend


def _pick_device() -> torch.device:
    """Select the best available compute device: cuda, then mps, then cpu."""
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def load_enrollments(path: str | Path) -> dict[str, np.ndarray]:
    """Load each ``{name}.npy`` under ``path`` as an ``(N_clips, D)`` array.

    Legacy single-vector ``(D,)`` files are treated as ``N=1``. Rows are
    L2-normalized for cosine scoring at identification time.
    """
    d = {}
    for f in Path(path).glob("*.npy"):
        arr = np.load(f)
        if arr.ndim == 1:
            arr = arr[None, :]
        norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
        d[f.stem] = arr / norms
    return d


def transcribe(
    audio: str | Path,
    backend: str | WhisperBackend = "auto",
    whisper_model: str | None = None,
    language: str | None = None,
):
    """Step 1: Whisper transcription. Returns (segments, detected_language)."""
    if isinstance(backend, str):
        kwargs = {"model": whisper_model} if whisper_model else {}
        backend = get_backend(backend, **kwargs)

    print(f"[1/3] transcribing with {type(backend).__name__} ...")
    result = backend.transcribe(Path(audio), language=language)
    segments = result["segments"]
    detected_lang = result["language"]
    print(f"      {len(segments)} segments, lang={detected_lang}")
    return segments, detected_lang


def diarize(
    audio: str | Path,
    num_speakers: int | None = None,
    hf_token: str | None = None,
    device: torch.device | None = None,
):
    """Step 2: Speaker diarization. Returns (diarization_annotation, device)."""
    hf_auth = hf_token if hf_token else True
    if device is None:
        device = _pick_device()
    print(f"[2/3] diarization ... (device: {device})")
    diar = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", use_auth_token=hf_auth,
    )
    diar.to(device)
    diar_kwargs = {"num_speakers": num_speakers} if num_speakers else {}
    diarization = diar(str(audio), **diar_kwargs)
    speakers = {s for _, _, s in diarization.itertracks(yield_label=True)}
    print(f"      {len(speakers)} speaker cluster(s)")
    return diarization, device


def init_embedder(device: torch.device | None = None, hf_token: str | None = None):
    """Load the speaker embedding model. Returns an Inference object."""
    hf_auth = hf_token if hf_token else True
    if device is None:
        device = _pick_device()
    embed_model = Model.from_pretrained(
        "pyannote/wespeaker-voxceleb-resnet34-LM", use_auth_token=hf_auth,
    )
    return Inference(embed_model, window="whole", device=device)


def _overlap_ratio(seg: Segment, other_turns: list[Segment]) -> float:
    """Fraction of ``seg`` that overlaps with any segment in ``other_turns``."""
    if seg.duration <= 0:
        return 1.0
    overlap = 0.0
    for other in other_turns:
        ov = max(0.0, min(seg.end, other.end) - max(seg.start, other.start))
        overlap += ov
    return min(overlap / seg.duration, 1.0)


def extract_cluster_clips(
    diarization,
    audio_path: str | Path,
    output_dir: str | Path,
    min_segment_secs: float = 2.0,
    target_secs: float = 45.0,
    max_overlap_ratio: float = 0.15,
) -> dict[str, dict]:
    """Extract clean speech clips per diarization cluster.

    For each cluster, collects segments that are:
      - >= min_segment_secs long (filters brief interjections)
      - <= max_overlap_ratio cross-talk with other speakers (filters noisy segments)
    Segments are sorted cleanest-first (lowest overlap, then longest), and
    concatenated up to target_secs. Saves as 16 kHz mono wav.

    Returns ``{cluster_label: {"path": str, "duration": float, "n_segments": int}}``.
    """
    turns_by_speaker: dict[str, list[Segment]] = {}
    for turn, _, spk in diarization.itertracks(yield_label=True):
        turns_by_speaker.setdefault(spk, []).append(turn)

    all_other: dict[str, list[Segment]] = {}
    for spk in turns_by_speaker:
        all_other[spk] = [
            t for other_spk, ts in turns_by_speaker.items()
            if other_spk != spk for t in ts
        ]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clips: dict[str, dict] = {}
    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        for spk, turns in turns_by_speaker.items():
            others = all_other[spk]
            scored = []
            for t in turns:
                if t.duration < min_segment_secs:
                    continue
                ratio = _overlap_ratio(t, others)
                if ratio <= max_overlap_ratio:
                    scored.append((ratio, t))
            scored.sort(key=lambda pair: (pair[0], -pair[1].duration))

            audio_chunks = []
            total_secs = 0.0
            for _, turn in scored:
                if total_secs >= target_secs:
                    break
                start_frame = int(turn.start * sr)
                n_frames = int(turn.duration * sr)
                f.seek(start_frame)
                chunk = f.read(n_frames)
                audio_chunks.append(chunk)
                total_secs += turn.duration

            if not audio_chunks:
                continue

            combined = np.concatenate(audio_chunks)
            clip_path = out_dir / f"{spk}.wav"
            sf.write(str(clip_path), combined, sr, subtype="PCM_16")
            clips[spk] = {
                "path": str(clip_path),
                "duration": round(total_secs, 1),
                "n_segments": len(audio_chunks),
            }

    return clips


def serialize_diarization(diarization) -> list[dict]:
    """Serialize a pyannote Annotation to a JSON-safe list of turns."""
    return [
        {"start": turn.start, "end": turn.end, "speaker": spk}
        for turn, _, spk in diarization.itertracks(yield_label=True)
    ]


def deserialize_diarization(turns: list[dict]):
    """Reconstruct a pyannote Annotation from serialized turns."""
    ann = Annotation()
    for i, t in enumerate(turns):
        ann[Segment(t["start"], t["end"]), i] = t["speaker"]
    return ann


def write_transcript(turns: list[dict], out: str | Path, audio: str | Path, language: str) -> dict:
    """Write the final JSON + TXT transcript files. Returns the result dict."""
    result = {
        "audio": str(audio),
        "language": language,
        "speakers": sorted({t["speaker"] for t in turns}),
        "segments": turns,
    }
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"wrote {out_path}")

    txt_path = out_path.with_suffix(".txt")
    with txt_path.open("w") as f:
        for t in turns:
            f.write(f"[{t['start']:>8.2f} - {t['end']:>8.2f}] {t['speaker']}: {t['text']}\n")
    print(f"wrote {txt_path}")
    return result


def identify_speakers(diarization, audio, enrollments, embed_inf, threshold):
    """Map each pyannote speaker cluster to an enrolled name, or ``unknown_*``."""
    turns_by_speaker: dict[str, list[Segment]] = {}
    for turn, _, spk in diarization.itertracks(yield_label=True):
        turns_by_speaker.setdefault(spk, []).append(turn)

    name_map = {}
    for spk, turns in turns_by_speaker.items():
        turns.sort(key=lambda s: s.duration, reverse=True)
        longest = turns[0]
        clip = Segment(longest.start, min(longest.end, longest.start + 30.0))
        emb = np.asarray(embed_inf.crop(audio, clip)).squeeze()
        emb = emb / (np.linalg.norm(emb) + 1e-9)

        best_name, best_score, best_clip_idx = None, -1.0, -1
        for name, refs in enrollments.items():
            scores = refs @ emb
            i = int(np.argmax(scores))
            s = float(scores[i])
            if s > best_score:
                best_name, best_score, best_clip_idx = name, s, i

        resolved = best_name if best_score >= threshold else f"unknown_{spk}"
        name_map[spk] = resolved
        n_clips = enrollments[best_name].shape[0] if best_name else 0
        print(f"  {spk} -> {resolved}  (best cosine={best_score:.3f}"
              f", clip {best_clip_idx + 1}/{n_clips})")
    return name_map


def align_transcript(whisper_segments, diarization, name_map):
    out = []
    turns = list(diarization.itertracks(yield_label=True))
    for seg in whisper_segments:
        s, e = seg["start"], seg["end"]
        best_spk, best_overlap = None, 0.0
        for turn, _, spk in turns:
            ov = max(0.0, min(e, turn.end) - max(s, turn.start))
            if ov > best_overlap:
                best_spk, best_overlap = spk, ov
        name = name_map.get(best_spk, "unknown") if best_spk else "unknown"
        out.append({
            "start": round(s, 2),
            "end": round(e, 2),
            "speaker": name,
            "text": seg["text"].strip(),
        })
    return out


def merge_consecutive_turns(aligned: list[dict]) -> list[dict]:
    """Collapse consecutive same-speaker segments into single turns."""
    merged: list[dict] = []
    for seg in aligned:
        if merged and seg["speaker"] == merged[-1]["speaker"]:
            merged[-1]["end"] = seg["end"]
            joined = merged[-1]["text"] + " " + seg["text"]
            merged[-1]["text"] = " ".join(joined.split())
        else:
            merged.append(dict(seg))
    return merged


def run_pipeline(
    audio: str | Path,
    out: str | Path,
    enrollments_dir: str | Path = "enrollments",
    backend: str | WhisperBackend = "auto",
    whisper_model: str | None = None,
    language: str | None = None,
    threshold: float = 0.5,
    hf_token: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    """Run the full pipeline and write ``<out>.json`` + ``<out>.txt``.

    This is a convenience wrapper that calls the individual step functions
    in sequence. For finer control (e.g. caching or auto-sampling), call
    :func:`transcribe`, :func:`diarize`, :func:`init_embedder`,
    :func:`identify_speakers`, :func:`align_transcript`,
    :func:`merge_consecutive_turns`, and :func:`write_transcript` directly.

    Returns the output dict. The sibling ``.txt`` is written next to ``out``
    with the same stem.
    """
    segments, detected_lang = transcribe(audio, backend, whisper_model, language)
    diarization, device = diarize(audio, num_speakers, hf_token)

    print("[3/3] speaker identification ...")
    enrollments = load_enrollments(enrollments_dir)
    if enrollments:
        print(f"      enrolled: {sorted(enrollments)}")
        embed_inf = init_embedder(device, hf_token)
        name_map = identify_speakers(
            diarization, str(audio), enrollments, embed_inf, threshold,
        )
    else:
        speakers = {s for _, _, s in diarization.itertracks(yield_label=True)}
        print(f"      no enrollments at {enrollments_dir}; keeping anonymous labels")
        name_map = {s: s for s in speakers}

    aligned = align_transcript(segments, diarization, name_map)
    turns = merge_consecutive_turns(aligned)
    print(f"      merged {len(aligned)} segments -> {len(turns)} speaker turns")

    return write_transcript(turns, out, audio, detected_lang)
