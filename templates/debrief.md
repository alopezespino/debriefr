---
description: Process a meeting recording end to end — transcribe, identify speakers, summarize, and sync action items.
allowed-tools: Bash, Read, Write, Edit
---

Process a meeting recording through the full debriefr pipeline. $ARGUMENTS is the path to an audio file or a `--resume` cache file. If empty, look in `inbox/` for audio files and ask which one to process.

## Prerequisites

The debriefr venv must be activated before running any `debriefr` command. Find it:
```bash
python3 -c "import debriefr; print(debriefr.__path__[0])" 2>/dev/null
```
If that fails, activate it first. Check common locations: the path printed by the command above will be inside the venv's `lib/` — the venv root is two levels up. Alternatively search for it:
```bash
find /Users -maxdepth 3 -name debriefr -path "*/.venv/*" 2>/dev/null | head -1
```

## Step 1: Identify project

Ask the user which project this recording belongs to. The project must match a slug in `projects.yaml`.

To list available projects:
```bash
python3 -c "
from debriefr.registry import discover_registry, load_registry, parse_registry
r = discover_registry('$AUDIO_DIR')
if r:
    projects, _, _ = parse_registry(load_registry(r))
    for slug in sorted(projects): print(f'  {slug}')
else:
    print('No projects.yaml found')
"
```

If the user specifies participants beyond the defaults, note them for the `--participants` override.

## Step 2: Run the pipeline

```bash
source /path/to/debriefr/.venv/bin/activate
debriefr transcribe <audio-file> --project <slug> [--language es] [--participants Name1 Name2 ...]
```

This runs transcription, diarization, and speaker identification. Monitor the output.

## Step 3: Handle auto-sampling (if needed)

If the pipeline exits with `status: needs_sampling`, it means some speakers aren't enrolled. The output will list extracted clips like `samples/SPEAKER_XX.wav`.

Help the user identify each clip:
1. Play each clip: `afplay samples/SPEAKER_XX.wav`
2. Ask the user who each speaker is.
3. For each identified speaker, rename and enroll:
   ```bash
   mv samples/SPEAKER_XX.wav samples/<Name>.wav
   debriefr enroll --name <Name> --audio samples/<Name>.wav
   ```
4. Resume the pipeline (skips transcription + diarization, re-runs identification only):
   ```bash
   debriefr transcribe --resume <cache-file> --project <slug>
   ```

If the user can't identify a speaker, use `--skip-sampling` on resume to proceed with unknown labels.

## Step 4: Review transcript (optional)

If the user wants to review or edit the transcript before summarizing, the pipeline should have been run with `--no-summary`. Read the `.txt` file and let the user make corrections.

## Step 5: Summarize

The pipeline produces the summary automatically via the Anthropic API unless `--no-summary` was passed. If the user wants to use Claude Code's own context instead (no extra API cost), do it manually:

1. Read the `.txt` transcript.
2. Read the summary prompt:
   ```bash
   python3 -c "from debriefr.meeting import default_summary_prompt_path; print(default_summary_prompt_path())"
   ```
3. Auto-discover participant bios from `projects.yaml`. For each speaker in the transcript that has a bio, prepend:
   ```
   Known participants (use these bios verbatim; do not infer or override):
   - Name: bio text
   ```
4. Generate the summary following the prompt exactly. First line: `reference: <slug>`. Match transcript language.
5. Strip the `reference:` line and write to `<stem>.md` in the same directory.
6. Rename `.json`, `.txt`, and `.md` if the reference slug differs from the current stem.

## Step 6: Sync action items to GitHub (if applicable)

Check if the project has GitHub config:
```bash
python3 -c "
from debriefr.registry import discover_registry, load_registry, parse_registry, resolve_project, is_github_enabled
r = discover_registry('$AUDIO_DIR')
projects, _, _ = parse_registry(load_registry(r))
pc = resolve_project(projects, '<slug>')
print('GitHub enabled' if is_github_enabled(pc) else 'No GitHub config')
"
```

If enabled, ask the user if they want to sync action items, then run:
```bash
debriefr sync-issues <summary.md> --project <slug> --dry-run
```

Show the dry-run plan. If the user approves, run without `--dry-run`.

## Step 7: Report

Summarize what was done:
- Path to the `.md` summary
- Any speakers that were enrolled
- Whether action items were synced (and how many issues created/updated)
- Remind about `closes #N` in commits for any assigned action items
