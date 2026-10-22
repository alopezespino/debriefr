---
description: Process a meeting recording end to end: transcribe, identify speakers, summarize, and sync action items.
allowed-tools: Bash, Read, Write, Edit
---

Process a meeting recording through the full debriefr pipeline. $ARGUMENTS is the path to
an audio file or a `--resume` cache file. If empty, look for audio as described in Step 1.

## Prerequisites

Each shell call starts fresh, so activating the venv once does not carry over. Resolve the
debriefr executable and its Python once, then write those absolute paths into every command
below (shown here as `<debriefr>` and `<python>`):
```bash
BIN="${DEBRIEFR_VENV:+$DEBRIEFR_VENV/bin/debriefr}"
BIN="${BIN:-$(command -v debriefr)}"
echo "debriefr: ${BIN:-NOT FOUND}"
[ -n "$BIN" ] && echo "python:   $(head -1 "$BIN" | sed 's/^#!//')"
```
`$DEBRIEFR_VENV` (the venv directory that holds `bin/debriefr`) wins; otherwise `debriefr`
on `PATH` is used, and its first line names the venv's Python. If neither is found, ask the
user where the debriefr venv is, and suggest adding `export DEBRIEFR_VENV=<that path>` to
their shell profile.

If the user keeps a shared `projects.yaml` outside the project trees, their shell profile
should export `DEBRIEFR_PROJECTS_YAML=<path to it>`. debriefr uses it when upward discovery
finds no registry, and merges its `guests:` (and any participant missing locally) into a
per-project `project.yaml`. Check with `echo "$DEBRIEFR_PROJECTS_YAML"`.

## Step 1: Identify project

Run a small check from the current directory:
```bash
<python> -c "from debriefr.registry import discover_registry; print(discover_registry())"
```
- Kind `"project"` (a per-project `project.yaml` found upward from the cwd): the session is
  already inside that project. State the inferred project (slug, transcript_dir) and do not
  ask which one; no `--project` flag is needed.
- Kind `"registry"` (a shared `projects.yaml` or `projects/projects.yaml`: the session runs in
  the registry's workspace): list the slugs and ask which one:
  ```bash
  <python> -c "
  from debriefr.registry import discover_registry, load_registry, parse_registry
  r = discover_registry()
  projects, _, _ = parse_registry(load_registry(r[0]))
  for slug in sorted(projects): print(f'  {slug}')
  "
  ```
- `None`: if `$DEBRIEFR_PROJECTS_YAML` is set, use registry mode with that file (pass
  `--projects-yaml "$DEBRIEFR_PROJECTS_YAML"` or rely on the fallback); otherwise ask the
  user for `--output-dir` and `--participants`.

Audio path: if `$ARGUMENTS` is empty, look for audio files: in registry mode in the
workspace's `inbox/`; in project mode in the project's own `inbox/` or its root directory.
Ask the user which file to process.

If the user names participants beyond the defaults, note them for a `--participants`
override.

## Step 2: Run the pipeline

Registry mode:
```bash
<debriefr> transcribe <audio-file> --project <slug> [--language es] [--no-summary]
```
Project mode (no `--project` needed):
```bash
<debriefr> transcribe <audio-file> [--language es] [--no-summary]
```

This runs transcription, diarization, and speaker identification. Monitor the output.

- Before transcribing, debriefr checks that pyannote can load its audio decoder (FFmpeg 4
  to 8 through torchcodec). A line `restarting with DYLD_LIBRARY_PATH=...` means it found a
  supported FFmpeg keg and restarted itself; that is expected. If it stops with
  `pyannote cannot load its audio decoder`, nothing was transcribed: show the user the
  `Fix:` line it printed (on macOS usually `brew install ffmpeg@8`) and stop. Do not retry
  until the fix is in place.
- The transcription is cached as soon as Whisper finishes. If a later step fails, the output
  names a `--resume <cache-file>` command: fix the cause and resume with it (Whisper does
  not run again), using the same `--project` rule as above.
- Leave `--archive-ttl-days` off. Archived files in `.audio-archive/` are kept
  indefinitely; with that flag debriefr moves old archived files to the Trash, which is only
  for when the user asks for it.

## Step 3: Handle auto-sampling (if needed)

If the pipeline exits with `status: needs_sampling`, some speakers aren't enrolled. The
output lists extracted clips like `samples/SPEAKER_XX.wav`.

Help the user identify each clip:
1. Play each clip: `afplay samples/SPEAKER_XX.wav` (macOS) or `aplay` (Linux).
2. Ask the user who each speaker is.
3. For each identified speaker, rename and enroll:
   ```bash
   mv samples/SPEAKER_XX.wav samples/<Name>.wav
   <debriefr> enroll --name <Name> --audio samples/<Name>.wav
   ```
   Enrolling an existing name appends the clip to the stored enrollment. Use `--replace`
   only if the user wants to discard the stored clips.
4. Resume the pipeline (skips transcription and diarization, re-runs identification only),
   same `--project` rule as Step 2:
   ```bash
   <debriefr> transcribe --resume <cache-file> [--project <slug>]
   ```

If the user can't identify a speaker, use `--skip-sampling` on resume to proceed with
unknown labels.

## Step 4: Review transcript (optional)

If the user wants to review or edit the transcript before summarizing, run the pipeline with
`--no-summary`. The `.txt` and `.json` then stay in `<transcript_dir>` (cleanup archives
only the audio until a summary exists). Read the `.txt` file and let the user make
corrections.

## Step 5: Summarize

The pipeline produces the summary automatically via the Anthropic API unless `--no-summary`
was passed. If the user wants to use Claude Code's own context instead (no extra API cost),
do it manually:

1. Read the `.txt` transcript in `<transcript_dir>`.
2. Read the summary prompt:
   ```bash
   <python> -c "from debriefr.meeting import default_summary_prompt_path; print(default_summary_prompt_path())"
   ```
3. Take participant bios from the resolved registry (project.yaml or projects.yaml). For
   each speaker in the transcript that has a bio, prepend:
   ```
   Known participants (use these bios verbatim; do not infer or override):
   - Name: bio text
   ```
4. Generate the summary following the prompt exactly. First line: `reference: <slug>`.
   Match transcript language.
5. Strip the `reference:` line and write `<stem>.md` next to the transcript.
6. If the reference slug differs from the current stem, rename the `.json`, `.txt` and `.md`
   to `<date>_<reference>`.

## Step 6: Stage the summary for tuning

**Registry mode:** if the workspace has an `inbox/`, move the summary there so the user can
fine-tune it before anything is synced or filed:
```bash
mv <project.path>/<transcript_dir>/<stem>.md inbox/<stem>.md
```
Without an `inbox/`, tune it in place as in project mode.

**Project mode:** the summary already lives in `<transcript_dir>`; tune it there, in place.

Either way:
- Apply the user's own writing rules for documents to the API-generated summary. For
  example, if their instructions forbid em dashes, replace each with a colon, comma,
  parentheses or two sentences, and check with `grep -c $'\xe2\x80\x94' <summary>`.
- Point out rows in the Action Items table that will not sync cleanly (compound owners such
  as "A & B", owners not in `participants:`/`collaborators:`, sensitive personal notes that
  should not go on a public board) and suggest consolidating rows into one issue per owner
  when they form a single deliverable.
- Stop here. The user edits the summary; do not proceed to Step 7 until they say tuning is
  done. Print the full path of the staged summary.

## Step 7: Sync action items to GitHub (if applicable, only after tuning is done)

Check whether the project has GitHub config:
```bash
<python> -c "
from debriefr.registry import (discover_registry, is_github_enabled,
                               load_any, resolve_project)
path, kind = discover_registry()
projects, _, _ = load_any(path, kind)
slug = next(iter(projects)) if kind == 'project' else '<slug>'
pc = resolve_project(projects, slug)
print('GitHub enabled' if is_github_enabled(pc) else 'No GitHub config')
"
```

If enabled, ask the user whether to sync action items. Always dry-run first, against the
tuned copy:
```bash
<debriefr> sync-issues <path-to-summary> [--project <slug>] --dry-run
```

Show the dry-run plan. Only after the user approves, run it for real with `--yes` (a Claude
Code shell is non-interactive, so the plain confirmation prompt would hang):
```bash
<debriefr> sync-issues <path-to-summary> [--project <slug>] --yes
```

If the sync prints `option '...' not found on field 'Priority'` (or `'Status'`), the board
uses other option names. Tell the user they can add `priority_map` / `status_map` under the
project's `github:` config (for example `priority_map: {urgent: P0, high: P1, medium: P2}`,
`status_map: {Todo: Ready}`) and re-sync the affected rows.

The sync appends `<!-- synced:#N -->` markers to the summary file; those markers must travel
with it.

## Step 8: File the summary in the project

**Registry mode with an `inbox/`:** only after the sync (or after the user declines
syncing), move the tuned summary to its final location:
```bash
mv inbox/<stem>.md <project.path>/<transcript_dir>/<stem>.md
```
Never move it before the user confirms tuning and syncing are complete.

**Otherwise:** the summary was tuned in place in `<transcript_dir>`; there is nothing to
move.

## Step 9: Report

Summarize what was done:
- Full path of every file created or written (summary `.md`, transcripts, any enrollment
  `.npy` files, any sample `.wav` clips)
- Any speakers that were enrolled
- Whether action items were synced (and how many issues created/updated)
- Remind about `closes #N` in commits for any assigned action items
