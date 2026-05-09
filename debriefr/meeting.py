"""Meeting orchestrator: audio -> transcript + LLM summary.

:func:`run_meeting` is the single high-level entry point. It handles:

- Ensuring the input is wav/flac (converts with bundled ffmpeg if not).
- Filtering enrollments to the named participants (gracefully handling missing ones).
- Running the transcription + diarization + identification pipeline.
- Auto-sampling clips for unknown speakers (interactive enrollment workflow).
- Summarizing the diarized transcript with an Anthropic Claude model.
- Writing three output files sharing a stem derived from the meeting date
  and (if the summarizer returns one) a topic reference::

      <YYYY-MM-DD>_<reference>.json   structured transcript
      <YYYY-MM-DD>_<reference>.txt    readable transcript
      <YYYY-MM-DD>_<reference>.md     LLM-generated summary

The reference is parsed from the first line of the summary (``reference: <slug>``)
and used to rename the files to a durable, topic-based stem. If extraction
fails or summarization is skipped, the audio filename stem is used as a fallback.

All options come in as function arguments. Use ``--project`` with a projects.yaml
registry (via the CLI or :mod:`debriefr.registry`) for project-specific defaults,
or pass everything explicitly for standalone / library use.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .backends import WhisperBackend
from .pipeline import (
    align_transcript,
    deserialize_diarization,
    diarize,
    extract_cluster_clips,
    identify_speakers,
    init_embedder,
    load_enrollments,
    merge_consecutive_turns,
    serialize_diarization,
    transcribe,
    write_transcript,
)

DEFAULT_SUMMARY_MODEL = "claude-opus-4-6"


def _keychain_get(service: str) -> str | None:
    """Try macOS Keychain; return None silently on any failure."""
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""),
             "-s", service, "-w"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip().lower())
    return re.sub(r"_+", "_", s).strip("_") or "meeting"


def extract_reference(summary_raw: str) -> tuple[str | None, str]:
    """Parse the leading ``reference: <slug>`` line from a summary if present.

    Returns ``(reference_slug, body_without_reference_line)``. If missing,
    returns ``(None, summary_raw)`` unchanged.
    """
    lines = summary_raw.splitlines()
    if not lines:
        return None, summary_raw
    m = re.match(r"^\s*reference:\s*(\S.*?)\s*$", lines[0], re.IGNORECASE)
    if not m:
        return None, summary_raw
    ref = slugify(m.group(1))
    remaining = lines[1:]
    if remaining and not remaining[0].strip():
        remaining = remaining[1:]
    return ref, "\n".join(remaining)


def resolve_unique_stem(root: Path, stem: str) -> str:
    """If ``{root}/{stem}.{json,txt,md}`` exists, append ``_2``, ``_3``, ..."""
    def taken(s: str) -> bool:
        return any((root / f"{s}.{ext}").exists() for ext in ("json", "txt", "md"))
    if not taken(stem):
        return stem
    i = 2
    while taken(f"{stem}_{i}"):
        i += 1
    return f"{stem}_{i}"


def ensure_wav(audio: Path) -> tuple[Path, bool]:
    """Return ``(wav_path, is_derived)``. Derived wavs live in a temp dir.

    pyannote's soundfile backend reads wav/flac reliably but not m4a/aac, so we
    convert anything else with bundled ffmpeg first.
    """
    if audio.suffix.lower() in (".wav", ".flac"):
        return audio, False
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    tmp_dir = Path(tempfile.mkdtemp(prefix="debriefr_wav_"))
    wav = tmp_dir / (audio.stem + ".wav")
    subprocess.check_call([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(wav),
    ])
    return wav, True


def collect_enrollments(enrollments_dir: Path, participants: list[str]) -> tuple[Path, list[str]]:
    """Copy available enrollments for the named participants into a temp dir.

    Returns ``(tmp_dir, names_found)``. Does not fail on missing enrollments --
    auto-sampling handles unenrolled speakers interactively.
    """
    tmp = Path(tempfile.mkdtemp(prefix="debriefr_enroll_"))
    enrollments_dir.mkdir(parents=True, exist_ok=True)
    found = []
    for n in participants:
        src = enrollments_dir / f"{n}.npy"
        if src.exists():
            shutil.copy(src, tmp / f"{n}.npy")
            found.append(n)
    return tmp, found


def rename_matched_samples(name_map: dict[str, str], samples_dir: Path) -> None:
    """Rename ``samples/SPEAKER_XX.wav`` to ``samples/<Name>_autoN.wav`` for matched clusters."""
    for cluster, name in name_map.items():
        if name.startswith("unknown_"):
            continue
        src = samples_dir / f"{cluster}.wav"
        if not src.exists():
            continue
        n = 1
        while (samples_dir / f"{name}_auto{n}.wav").exists():
            n += 1
        dest = samples_dir / f"{name}_auto{n}.wav"
        src.rename(dest)
        print(f"      renamed {src.name} -> {dest.name}")


def save_cache(cache_path: Path, **kwargs) -> None:
    """Save pipeline state so --resume can skip transcription + diarization."""
    cache_path.write_text(json.dumps(kwargs, indent=2, ensure_ascii=False))
    print(f"      cache saved: {cache_path}")


def load_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        raise FileNotFoundError(f"Cache file not found: {cache_path}")
    return json.loads(cache_path.read_text())


def summarize(
    transcript_text: str,
    model: str,
    api_key: str,
    summary_prompt: str,
    participant_bios: dict[str, str] | None = None,
) -> str:
    """Summarize a diarized transcript with Anthropic Claude. Streams for timeout safety."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    if participant_bios:
        bios_block = "Known participants (use these bios verbatim; do not infer or override):\n"
        bios_block += "\n".join(f"- {name}: {bio}" for name, bio in participant_bios.items())
        user_content = f"{bios_block}\n\nTranscript:\n\n{transcript_text}"
    else:
        user_content = f"Transcript:\n\n{transcript_text}"

    with client.messages.stream(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{
            "type": "text",
            "text": summary_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        for _ in stream.text_stream:
            pass
        final = stream.get_final_message()

    text = next((b.text for b in final.content if b.type == "text"), "")
    usage = final.usage
    print(f"      summary tokens in:{usage.input_tokens} "
          f"out:{usage.output_tokens} "
          f"cache_read:{getattr(usage, 'cache_read_input_tokens', 0)}")
    return text


def archive_source(audio: Path, archive_dir: Path) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / audio.name
    if dest.exists():
        dest = archive_dir / f"{audio.stem}_{int(audio.stat().st_mtime)}{audio.suffix}"
    shutil.move(str(audio), str(dest))
    return dest


def prune_archive(archive_dir: Path, ttl_days: int) -> None:
    if not archive_dir.is_dir():
        return
    cutoff = datetime.now() - timedelta(days=ttl_days)
    for f in archive_dir.iterdir():
        if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            print(f"      pruning {f.name} (age > {ttl_days}d)")
            f.unlink()


def default_summary_prompt_path() -> Path:
    """Path to the bundled default summary prompt."""
    return Path(__file__).parent / "prompts" / "summary_default.md"


def _apply_cleanup(
    cleanup: str,
    original_audio: Path,
    transcript_json: Path,
    transcript_txt: Path,
    output_dir: Path,
    archive_base: Path | None,
    archive_ttl_days: int,
    registry_yaml: str | None = None,
    project_slug: str | None = None,
) -> None:
    archive_dir = (archive_base or output_dir.parent) / ".audio-archive"

    if cleanup == "archive-all":
        for f in (original_audio, transcript_json, transcript_txt):
            if f.exists():
                dest = archive_source(f, archive_dir)
                print(f"      archived {f.name} -> {dest}")
        prune_archive(archive_dir, archive_ttl_days)

    elif cleanup == "archive-audio":
        if original_audio.exists():
            dest = archive_source(original_audio, archive_dir)
            print(f"      archived {original_audio.name} -> {dest}")
        prune_archive(archive_dir, archive_ttl_days)

    elif cleanup == "gitignore":
        r = subprocess.run(
            ["git", "-C", str(output_dir), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print("      [warn] not inside a git repo; falling back to archive-all")
            _apply_cleanup(
                "archive-all", original_audio, transcript_json, transcript_txt,
                output_dir, archive_base, archive_ttl_days,
            )
            return

        repo_root = Path(r.stdout.strip())
        gitignore = repo_root / ".gitignore"
        marker = "# debriefr intermediates"
        if gitignore.exists() and marker in gitignore.read_text():
            return
        patterns = [
            "",
            marker,
            "*.m4a",
            "*.wav",
            "*.flac",
            "*.cache.json",
        ]
        rel = output_dir.resolve().relative_to(repo_root.resolve())
        patterns.extend([
            f"{rel}/**/*.txt",
            f"{rel}/**/*.json",
        ])
        with open(gitignore, "a") as f:
            f.write("\n".join(patterns) + "\n")
        print(f"      added debriefr patterns to {gitignore}")

        if registry_yaml and project_slug:
            from .registry import mark_gitignore_configured
            mark_gitignore_configured(registry_yaml, project_slug)


def run_meeting(
    audio: str | Path | None = None,
    output_dir: str | Path = ".",
    participants: list[str] | None = None,
    enrollments_dir: str | Path = "enrollments",
    samples_dir: str | Path = "samples",
    backend: str | WhisperBackend = "auto",
    whisper_model: str | None = None,
    language: str | None = None,
    num_speakers: int | None = None,
    threshold: float = 0.5,
    date: str | None = None,
    participant_bios: dict[str, str] | None = None,
    summary_prompt_path: str | Path | None = None,
    summary_model: str = DEFAULT_SUMMARY_MODEL,
    anthropic_api_key: str | None = None,
    hf_token: str | None = None,
    skip_summary: bool = False,
    skip_sampling: bool = False,
    slug: str | None = None,
    cleanup: str | None = None,
    archive_base: str | Path | None = None,
    archive_ttl_days: int = 7,
    archive_dir: str | Path | None = None,
    resume: str | Path | None = None,
    _registry_yaml: str | None = None,
    _project_slug: str | None = None,
) -> dict:
    """Run the full meeting pipeline end to end.

    Parameters mirror the CLI flags of ``debriefr transcribe``. Returns a dict
    with the result status and paths of produced files::

        # On success:
        {"status": "complete", "transcript_json": Path, "transcript_txt": Path,
         "summary_md": Path | None, "reference": str | None}

        # When auto-sampling pauses for user to label/enroll speakers:
        {"status": "needs_sampling", "cache_path": Path, "clips": dict,
         "unknowns": list[str]}
    """
    # Backward compat: --archive-dir maps to cleanup="archive-audio"
    if archive_dir and not cleanup:
        cleanup = "archive-audio"
        archive_base = archive_dir
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    enrollments_dir = Path(enrollments_dir)
    samples_dir = Path(samples_dir)

    # ── Resume path: load cached transcription + diarization ──────────
    if resume:
        cache = load_cache(Path(resume))
        segments = cache["whisper_segments"]
        detected_lang = cache["language"]
        diarization = deserialize_diarization(cache["diarization_turns"])
        original_audio = Path(cache["original_audio"])
        if not original_audio.exists():
            raise FileNotFoundError(f"Original audio not found: {original_audio}")
        wav, wav_is_derived = ensure_wav(original_audio)
        people = participants or cache["people"]
        date_str = cache["date_str"]
        fallback_slug = cache["fallback_slug"]
        print(f"resumed from cache: {resume}")
        print(f"output dir: {output_dir}")
        print(f"participants: {people}")
    else:
        # ── Fresh run: transcribe + diarize ───────────────────────────
        if audio is None:
            raise ValueError("audio is required unless resume is provided")
        audio = Path(audio).resolve()
        if not audio.exists():
            raise FileNotFoundError(f"Audio file not found: {audio}")

        people = participants or []
        if not people:
            raise ValueError("participants must be a non-empty list of enrolled speaker names.")

        if date:
            datetime.strptime(date, "%Y-%m-%d")
            date_str = date
        else:
            date_str = datetime.fromtimestamp(audio.stat().st_mtime).strftime("%Y-%m-%d")
        fallback_slug = slugify(slug or audio.stem)

        print(f"output dir: {output_dir}")
        print(f"participants: {people}")

        original_audio = audio
        wav, wav_is_derived = ensure_wav(audio)
        segments, detected_lang = transcribe(
            wav, backend, whisper_model, language,
        )
        diarization, _ = diarize(wav, num_speakers, hf_token)

    # ── Identification against available enrollments ──────────────────
    enroll_subset, enrolled = collect_enrollments(enrollments_dir, people)
    not_enrolled = [n for n in people if n not in enrolled]
    if not_enrolled:
        print(f"      not enrolled (will attempt auto-sampling): {not_enrolled}")

    try:
        print("[3/3] speaker identification ...")
        enrollments = load_enrollments(str(enroll_subset))
        if enrollments:
            print(f"      enrolled: {sorted(enrollments)}")
            embed_inf = init_embedder(hf_token=hf_token)
            name_map = identify_speakers(
                diarization, str(wav), enrollments, embed_inf, threshold,
            )
        else:
            speakers = {s for _, _, s in diarization.itertracks(yield_label=True)}
            print("      no enrollments available; all speakers unknown")
            name_map = {s: f"unknown_{s}" for s in speakers}

        rename_matched_samples(name_map, samples_dir)

        # ── Auto-sampling for unknown speakers ────────────────────────
        unknowns = {spk: label for spk, label in name_map.items()
                    if label.startswith("unknown_")}

        if unknowns and not skip_sampling:
            clips = extract_cluster_clips(
                diarization, str(wav), str(samples_dir),
            )
            unknown_clips = {spk: clips[spk] for spk in unknowns if spk in clips}
            no_clip = [spk for spk in unknowns if spk not in clips]

            if unknown_clips:
                tentative_stem = f"{date_str}_{fallback_slug}"
                cache_path = output_dir / f"{tentative_stem}.cache.json"
                save_cache(
                    cache_path,
                    whisper_segments=segments,
                    diarization_turns=serialize_diarization(diarization),
                    original_audio=str(original_audio),
                    language=detected_lang,
                    people=people,
                    date_str=date_str,
                    fallback_slug=fallback_slug,
                )

                return {
                    "status": "needs_sampling",
                    "cache_path": cache_path,
                    "clips": unknown_clips,
                    "unknowns": list(unknowns.values()),
                    "no_clip": no_clip,
                }
            else:
                print(f"      [info] {len(unknowns)} unknown cluster(s) had no clean "
                      f"segments to extract (likely noise); proceeding")

        if unknowns:
            print(f"      [warn] {len(unknowns)} speaker(s) remain unknown "
                  f"(--skip-sampling): {list(unknowns.values())}")

        # ── Align and write transcript ────────────────────────────────
        tentative_stem = f"{date_str}_{fallback_slug}"
        transcript_json = output_dir / f"{tentative_stem}.json"

        aligned = align_transcript(segments, diarization, name_map)
        turns = merge_consecutive_turns(aligned)
        print(f"      merged {len(aligned)} segments -> {len(turns)} speaker turns")
        write_transcript(turns, str(transcript_json), str(wav), detected_lang)
        transcript_txt = transcript_json.with_suffix(".txt")

        # ── Summarization (API path) ─────────────────────────────────
        summary_path: Path | None = None
        reference: str | None = None

        if not skip_summary:
            print("[4/4] summarizing ...")
            api_key = anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or _keychain_get("anthropic_api_key")
            if not api_key:
                raise RuntimeError(
                    "No Anthropic API key found. Pass anthropic_api_key=..., "
                    "export ANTHROPIC_API_KEY=..., or store in macOS Keychain "
                    "(security add-generic-password -a $USER -s anthropic_api_key -w)"
                )
            prompt_path = Path(summary_prompt_path) if summary_prompt_path else default_summary_prompt_path()
            summary_prompt = prompt_path.read_text()
            segs = json.loads(transcript_json.read_text())["segments"]
            transcript_for_llm = "\n".join(
                f"{s['speaker']}: {s['text']}" for s in segs
            )
            bios_for_meeting = (
                {n: participant_bios[n] for n in people if n in participant_bios}
                if participant_bios else None
            )
            summary_raw = summarize(
                transcript_for_llm,
                summary_model,
                api_key,
                summary_prompt,
                participant_bios=bios_for_meeting,
            )
            reference, summary_body = extract_reference(summary_raw)
            if reference:
                final_stem = resolve_unique_stem(output_dir, f"{date_str}_{reference}")
            else:
                print("      [warn] no reference extracted; using audio-slug fallback")
                final_stem = tentative_stem

            if final_stem != tentative_stem:
                transcript_json = transcript_json.rename(output_dir / f"{final_stem}.json")
                transcript_txt = transcript_txt.rename(output_dir / f"{final_stem}.txt")
                print(f"      renamed transcripts -> {final_stem}.{{json,txt}}")

            summary_path = output_dir / f"{final_stem}.md"
            summary_path.write_text(summary_body)
            print(f"      wrote {summary_path}")

        # Clean up cache file if we're resuming and succeeded
        if resume:
            cache_file = Path(resume)
            if cache_file.exists():
                cache_file.unlink()
                print(f"      cleaned up cache: {cache_file}")

    finally:
        shutil.rmtree(enroll_subset, ignore_errors=True)
        if wav_is_derived:
            shutil.rmtree(wav.parent, ignore_errors=True)

    if cleanup:
        _apply_cleanup(
            cleanup, original_audio, transcript_json, transcript_txt,
            output_dir,
            Path(archive_base) if archive_base else None,
            archive_ttl_days,
            _registry_yaml, _project_slug,
        )

    return {
        "status": "complete",
        "transcript_json": transcript_json,
        "transcript_txt": transcript_txt,
        "summary_md": summary_path,
        "reference": reference,
    }
