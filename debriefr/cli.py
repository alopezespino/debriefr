"""Command-line interface for debriefr.

Subcommands:
  ``transcribe``   Run the full pipeline on a meeting audio file.
  ``enroll``       Enroll a speaker from one or more reference clips.
  ``sync-issues``  Sync action items from a meeting summary to GitHub.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _add_transcribe(sub):
    p = sub.add_parser("transcribe", help="Transcribe, diarize, and summarize a meeting.")
    p.add_argument("audio", nargs="?", default=None,
                   help="Path to audio file (required unless --resume is used)")
    p.add_argument("--project", default=None,
                   help="Project slug from projects.yaml; resolves output-dir, "
                        "participants, bios, handles, and cleanup automatically. "
                        "Optional when a project.yaml is discovered (it defines "
                        "exactly one project)")
    p.add_argument("--projects-yaml", default=None,
                   help="Path to a project.yaml or projects.yaml (default: "
                        "auto-discovers either upward from the audio file "
                        "location, then the cwd, then falls back to "
                        "$DEBRIEFR_PROJECTS_YAML)")
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
                        "gh-project, and handles automatically. Optional when "
                        "a project.yaml is discovered")
    p.add_argument("--projects-yaml", default=None,
                   help="Path to a project.yaml or projects.yaml (default: "
                        "auto-discovers either upward from the summary file "
                        "location, then the cwd, then falls back to "
                        "$DEBRIEFR_PROJECTS_YAML)")
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
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip the Proceed? confirmation (required when stdin "
                        "is not a terminal)")


def _parse_bio_args(bio_args: list[str]) -> dict[str, str]:
    out = {}
    for pair in bio_args:
        if "=" not in pair:
            sys.exit(f"Invalid --bio '{pair}'. Expected Name=Bio text.")
        name, bio = pair.split("=", 1)
        out[name.strip()] = bio.strip()
    return out


def _pick_discover_from(candidates) -> Path | None:
    """Choose the directory to start upward registry discovery from.

    ``candidates`` is an ordered list of directories (most specific first,
    e.g. the audio/summary file's parent, then the current working dir).

    A per-project ``project.yaml`` always wins over a shared
    ``projects.yaml`` registry: an audio file dropped in ALE's ``inbox/``
    would discover ALE's projects.yaml from its own parent, but if the
    session was opened inside a project directory that project should own
    the run. So: return the first candidate that discovers a "project"
    kind; otherwise the first candidate that discovers anything at all;
    otherwise None.
    """
    from .registry import discover_registry

    found = []
    for cand in candidates:
        if cand is None:
            continue
        hit = discover_registry(cand)
        if hit is not None and hit[1] == "project":
            return cand
        found.append((cand, hit))
    for cand, hit in found:
        if hit is not None:
            return cand
    return found[0][0] if found else None


def _resolve_registry(project_slug: str | None, projects_yaml: str | None,
                      discover_from: Path | None) -> tuple[dict, dict]:
    """Load a registry (projects.yaml) or a per-project project.yaml.

    Returns (project_config, people) where people is the merged
    participants + guests dict.
    """
    from .registry import (discover_registry, load_any, merge_people,
                           merge_registries, resolve_project)

    kind = None
    yaml_path = None

    if projects_yaml:
        yaml_path = Path(projects_yaml).expanduser()
        kind = "project" if yaml_path.name == "project.yaml" else "registry"
    else:
        # Precedence: upward discovery (per-tree registry) wins; fall back to
        # DEBRIEFR_PROJECTS_YAML so projects whose output dirs live outside the
        # registry's tree (e.g. a transcript dir under a separate repo) resolve
        # without --projects-yaml on every run.
        hit = discover_registry(discover_from)
        if hit is not None:
            yaml_path, kind = hit
        else:
            env_path = os.environ.get("DEBRIEFR_PROJECTS_YAML")
            if env_path:
                yaml_path = Path(env_path).expanduser()
                kind = "registry"
                if not yaml_path.is_file():
                    sys.exit(
                        f"DEBRIEFR_PROJECTS_YAML points to a missing file: "
                        f"{yaml_path}"
                    )
        if yaml_path is None:
            sys.exit(
                f"Cannot find project.yaml or projects.yaml (searched upward "
                f"from {discover_from or Path.cwd()}). "
                f"Set DEBRIEFR_PROJECTS_YAML or pass --projects-yaml explicitly."
            )

    loaded = load_any(yaml_path, kind)

    # Fallback merge: a project.yaml (or any registry other than the env one)
    # still gains the shared people/guests defined in $DEBRIEFR_PROJECTS_YAML.
    env_path = os.environ.get("DEBRIEFR_PROJECTS_YAML")
    if env_path:
        env_yaml = Path(env_path).expanduser()
        if env_yaml.is_file():
            try:
                same = env_yaml.resolve() == Path(yaml_path).resolve()
            except OSError:
                same = False
            if not same:
                loaded = merge_registries(loaded, load_any(env_yaml, "registry"))

    projects, participants, guests = loaded
    people = merge_people(participants, guests)

    if kind == "project":
        only = next(iter(projects))
        if project_slug is None:
            project_slug = only
        elif project_slug.lower() != only.lower():
            sys.exit(
                f"--project {project_slug} does not match the project.yaml at "
                f"{yaml_path} (slug {only})"
            )
        else:
            project_slug = only
    elif project_slug is None:
        sys.exit(
            f"--project is required when using a projects.yaml registry "
            f"(found {yaml_path})"
        )

    try:
        project_config = resolve_project(projects, project_slug)
    except KeyError as e:
        sys.exit(str(e))

    project_config["_yaml_path"] = yaml_path
    project_config["_people"] = people
    project_config["_kind"] = kind
    return project_config, people


def cmd_transcribe(args):
    from .meeting import run_meeting
    from .registry import discover_registry, extract_bios

    if not args.resume and not args.audio:
        sys.exit("audio is required unless --resume is used")

    project_config = None
    registry_bios = {}

    if args.audio:
        file_dir = Path(args.audio).parent.resolve()
    elif args.resume:
        file_dir = Path(args.resume).parent.resolve()
    else:
        file_dir = None
    discover_from = _pick_discover_from([file_dir, Path.cwd()])

    use_registry = bool(args.project or args.projects_yaml)
    if not use_registry:
        hit = discover_registry(discover_from) if discover_from else None
        use_registry = hit is not None and hit[1] == "project"

    if use_registry:
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
        kwargs["_project_slug"] = project_config["slug"]

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
        if project_config:
            resume_cmd += f" --project {project_config['slug']}"
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

    from .registry import discover_registry, extract_handles

    summary_dir = Path(args.summary).parent.resolve() if args.summary else None
    discover_from = _pick_discover_from([summary_dir, Path.cwd()])

    use_registry = bool(args.project or args.projects_yaml)
    if not use_registry:
        hit = discover_registry(discover_from) if discover_from else None
        use_registry = hit is not None and hit[1] == "project"

    if use_registry:
        project_config, people = _resolve_registry(
            args.project, args.projects_yaml, discover_from
        )
        registry_handles = extract_handles(people)

    repo = args.repo
    gh_project = args.gh_project

    if not repo:
        if project_config and project_config.get("github"):
            repo = project_config["github"].get("repo")
        if not repo:
            sys.exit("--repo is required for sync (or set github.repo in project.yaml / projects.yaml).")

    if not gh_project:
        if project_config and project_config.get("github"):
            gh_project = project_config["github"].get("project")
        if not gh_project:
            sys.exit("--gh-project is required for sync "
                     "(or set github.project in project.yaml / projects.yaml).")

    summary_path = Path(args.summary).resolve()
    if not summary_path.is_file():
        sys.exit(f"Not a file: {summary_path}")

    handles = dict(registry_handles)
    cli_handles = parse_handle_args(args.handle)
    handles.update(cli_handles)

    sync_summary(summary_path, repo, gh_project, handles, dry_run=args.dry_run,
                 assume_yes=args.yes)


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
