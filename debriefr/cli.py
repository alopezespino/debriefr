"""Command-line interface for debriefr.

Subcommands:
  ``transcribe``   Run the full pipeline on a meeting audio file.
  ``enroll``       Enroll a speaker from one or more reference clips.
  ``sync-issues``  Sync action items from a meeting summary to GitHub.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _add_transcribe(sub):
    p = sub.add_parser("transcribe", help="Transcribe, diarize, and summarize a meeting.")
    p.add_argument("audio", nargs="?", default=None,
                   help="Path to audio file (required unless --resume is used)")
    p.add_argument("--project", default=None,
                   help="Project slug from projects.yaml; resolves output-dir, "
                        "participants, bios, handles, and cleanup automatically")
    p.add_argument("--projects-yaml", default=None,
                   help="Path to projects.yaml (default: auto-discovers upward "
                        "from audio file location)")
    p.add_argument("--output-dir", default=None,
                   help="Directory for transcript + summary outputs "
                        "(resolved from registry when --project is used)")
    p.add_argument("--participants", nargs="+", default=None,
                   help="Space-separated list of enrolled speaker names")
    p.add_argument("--enrollments-dir", default="enrollments",
                   help="Directory containing enrolled {name}.npy files (default: ./enrollments)")
    p.add_argument("--samples-dir", default="samples",
                   help="Directory for auto-sampled speaker clips (default: ./samples)")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "groq", "openai", "mlx", "faster-whisper"],
                   help="Whisper backend (default: auto)")
    p.add_argument("--whisper-model", default=None,
                   help="Backend-specific model name (default: backend's preferred)")
    p.add_argument("--language", default=None, help="ISO code (en, es, ...); omit for auto-detect")
    p.add_argument("--num-speakers", type=int, default=None,
                   help="If known, fixes the speaker count (improves diarization)")
    p.add_argument("--threshold", type=float, default=0.5,
                   help="Speaker-id cosine threshold (default: 0.5)")
    p.add_argument("--date", default=None,
                   help="Override meeting date (YYYY-MM-DD); default: audio file mtime")
    p.add_argument("--slug", default=None,
                   help="Output filename slug (default: audio filename stem)")
    p.add_argument("--summary-prompt", default=None,
                   help="Path to a custom summary prompt file (default: bundled default)")
    p.add_argument("--summary-model", default=None, help="Anthropic model for summary")
    p.add_argument("--bio", action="append", default=[], metavar="NAME=BIO",
                   help="Participant bio passed to the summarizer (repeatable)")
    p.add_argument("--no-summary", action="store_true")
    p.add_argument("--skip-sampling", action="store_true",
                   help="Skip interactive speaker sampling; use existing enrollments only")
    p.add_argument("--resume", default=None, metavar="CACHE",
                   help="Resume from a cache file (skip transcription + diarization)")
    p.add_argument("--cleanup", default=None,
                   choices=["gitignore", "archive-all", "archive-audio"],
                   help="Post-processing cleanup mode (default: archive-all with "
                        "--project, none without)")
    p.add_argument("--archive-dir", default=None,
                   help="[DEPRECATED: use --cleanup] Move source audio to this directory")
    p.add_argument("--archive-ttl-days", type=int, default=7,
                   help="Delete archived audio older than this (default: 7)")


def _add_enroll(sub):
    p = sub.add_parser("enroll", help="Enroll a speaker from one or more reference clips.")
    p.add_argument("--name", required=True)
    p.add_argument("--audio", required=True, nargs="+",
                   help="One or more audio files (wav/flac)")
    p.add_argument("--out-dir", default="enrollments",
                   help="Directory to save embeddings (default: ./enrollments)")


def _add_sync_issues(sub):
    p = sub.add_parser("sync-issues", help="Sync a meeting summary's action items to GitHub.")
    p.add_argument("summary", nargs="?", help="Path to meeting summary .md (for sync mode)")
    p.add_argument("--project", default=None,
                   help="Project slug from projects.yaml; resolves repo, "
                        "gh-project, and handles automatically")
    p.add_argument("--projects-yaml", default=None,
                   help="Path to projects.yaml (default: auto-discovers upward "
                        "from summary file location)")
    p.add_argument("--repo", help="GitHub repo in owner/name form (required for sync)")
    p.add_argument("--gh-project", type=int, default=None,
                   help="GitHub Project v2 number (required for sync)")
    p.add_argument("--setup", action="store_true",
                   help="Create a new GitHub Project with Priority + Due fields")
    p.add_argument("--link", type=int, metavar="NUMBER",
                   help="Validate an existing GitHub Project number "
                        "(creates Priority/Due fields if missing)")
    p.add_argument("--title", help="Title for the new project (required with --setup)")
    p.add_argument("--handle", action="append", default=[], metavar="NAME=GH_HANDLE",
                   help="Map a participant name to a GitHub handle (repeatable)")
    p.add_argument("--dry-run", action="store_true")


def _parse_bio_args(bio_args: list[str]) -> dict[str, str]:
    out = {}
    for pair in bio_args:
        if "=" not in pair:
            sys.exit(f"Invalid --bio '{pair}'. Expected Name=Bio text.")
        name, bio = pair.split("=", 1)
        out[name.strip()] = bio.strip()
    return out


def _resolve_registry(project_slug: str, projects_yaml: str | None,
                      discover_from: Path | None) -> tuple[dict, dict]:
    """Load registry and resolve project config.

    Returns (project_config, people) where people is the merged
    participants + guests dict.
    """
    from .registry import (discover_registry, extract_bios, load_registry,
                           merge_people, parse_registry, resolve_project)

    if projects_yaml:
        yaml_path = Path(projects_yaml)
    else:
        yaml_path = discover_registry(discover_from)
        if yaml_path is None:
            sys.exit(
                f"Cannot find projects.yaml (searched upward from "
                f"{discover_from or Path.cwd()}). "
                f"Pass --projects-yaml explicitly."
            )

    data = load_registry(yaml_path)
    projects, participants, guests = parse_registry(data)
    people = merge_people(participants, guests)

    try:
        project_config = resolve_project(projects, project_slug)
    except KeyError as e:
        sys.exit(str(e))

    project_config["_yaml_path"] = yaml_path
    project_config["_people"] = people
    return project_config, people


def cmd_transcribe(args):
    from .meeting import run_meeting
    from .registry import extract_bios

    if not args.resume and not args.audio:
        sys.exit("audio is required unless --resume is used")

    project_config = None
    registry_bios = {}

    if args.project:
        if args.audio:
            discover_from = Path(args.audio).parent.resolve()
        elif args.resume:
            discover_from = Path(args.resume).parent.resolve()
        else:
            discover_from = None
        project_config, people = _resolve_registry(
            args.project, args.projects_yaml, discover_from
        )
        registry_bios = extract_bios(people)

    output_dir = args.output_dir
    if not output_dir:
        if project_config:
            output_dir = str(project_config["output_dir"])
        else:
            sys.exit("--output-dir is required unless --project is used")

    participants = args.participants
    if not participants and not args.resume:
        if project_config:
            participants = project_config["default_participants"]
        else:
            sys.exit("--participants is required unless --project or --resume is used")

    bios = dict(registry_bios)
    if args.bio:
        bios.update(_parse_bio_args(args.bio))

    # Resolve cleanup mode
    cleanup = args.cleanup
    archive_base = None
    if args.archive_dir:
        print("[DEPRECATED] --archive-dir is deprecated; use --cleanup instead.",
              file=sys.stderr)
        cleanup = cleanup or "archive-audio"
        archive_base = args.archive_dir
    elif cleanup is None and project_config:
        cleanup = project_config.get("cleanup", "archive-all")

    if cleanup and not archive_base and project_config:
        archive_base = str(project_config["path"])

    kwargs = dict(
        audio=args.audio,
        output_dir=output_dir,
        participants=participants,
        enrollments_dir=args.enrollments_dir,
        samples_dir=args.samples_dir,
        backend=args.backend,
        whisper_model=args.whisper_model,
        language=args.language,
        num_speakers=args.num_speakers,
        threshold=args.threshold,
        date=args.date,
        participant_bios=bios or None,
        summary_prompt_path=args.summary_prompt,
        skip_summary=args.no_summary,
        skip_sampling=args.skip_sampling,
        slug=args.slug,
        cleanup=cleanup,
        archive_base=archive_base,
        archive_ttl_days=args.archive_ttl_days,
        resume=args.resume,
    )
    if args.summary_model:
        kwargs["summary_model"] = args.summary_model

    # Pass registry path for gitignore tracking
    if project_config:
        kwargs["_registry_yaml"] = str(project_config["_yaml_path"])
        kwargs["_project_slug"] = args.project

    result = run_meeting(**kwargs)

    if result["status"] == "needs_sampling":
        clips = result["clips"]
        print(f"\n{'='*60}")
        print(f"  {len(clips)} unknown speaker(s) — clips extracted:")
        print(f"{'='*60}\n")
        for spk, info in clips.items():
            print(f"  {spk}: {info['path']} ({info['duration']}s, "
                  f"{info['n_segments']} segments)")
        if result.get("no_clip"):
            print(f"\n  {len(result['no_clip'])} cluster(s) with no clean segments "
                  f"(likely noise): {result['no_clip']}")
        print(f"\nLabel each unknown speaker, then enroll and resume:")
        print(f"  debriefr enroll --name <Name> --audio samples/<SPEAKER>.wav")
        resume_cmd = f"  debriefr transcribe --resume {result['cache_path']}"
        if args.project:
            resume_cmd += f" --project {args.project}"
        else:
            resume_cmd += f" --output-dir {output_dir}"
        if args.no_summary:
            resume_cmd += " --no-summary"
        print(resume_cmd)


def cmd_enroll(args):
    from .enroll import enroll_speaker
    enroll_speaker(args.name, args.audio, out_dir=args.out_dir)


def cmd_sync_issues(args):
    from .sync_issues import link_project, parse_handle_args, setup_project, sync_summary

    if args.setup and args.link is not None:
        sys.exit("--setup and --link are mutually exclusive.")

    if args.setup:
        if not args.title:
            sys.exit("--setup requires --title")
        number = setup_project(args.title)
        hint_repo = args.repo or "OWNER/REPO"
        print(f"\nRun sync with: debriefr sync-issues SUMMARY.md "
              f"--repo {hint_repo} --gh-project {number}")
        return

    if args.link is not None:
        link_project(args.link)
        return

    if not args.summary:
        sys.exit("Provide a summary path (or use --setup / --link).")

    project_config = None
    registry_handles = {}

    if args.project:
        from .registry import extract_handles
        summary_dir = Path(args.summary).parent.resolve() if args.summary else None
        project_config, people = _resolve_registry(
            args.project, args.projects_yaml, summary_dir
        )
        registry_handles = extract_handles(people)

    repo = args.repo
    gh_project = args.gh_project

    if not repo:
        if project_config and project_config.get("github"):
            repo = project_config["github"].get("repo")
        if not repo:
            sys.exit("--repo is required for sync (or set github.repo in projects.yaml).")

    if not gh_project:
        if project_config and project_config.get("github"):
            gh_project = project_config["github"].get("project")
        if not gh_project:
            sys.exit("--gh-project is required for sync "
                     "(or set github.project in projects.yaml).")

    summary_path = Path(args.summary).resolve()
    if not summary_path.is_file():
        sys.exit(f"Not a file: {summary_path}")

    handles = dict(registry_handles)
    cli_handles = parse_handle_args(args.handle)
    handles.update(cli_handles)

    sync_summary(summary_path, repo, gh_project, handles, dry_run=args.dry_run)


def main():
    parser = argparse.ArgumentParser(
        prog="debriefr",
        description="Transcribe, diarize, and summarize meetings; sync action items to GitHub.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    _add_transcribe(sub)
    _add_enroll(sub)
    _add_sync_issues(sub)
    args = parser.parse_args()

    dispatch = {
        "transcribe": cmd_transcribe,
        "enroll": cmd_enroll,
        "sync-issues": cmd_sync_issues,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
