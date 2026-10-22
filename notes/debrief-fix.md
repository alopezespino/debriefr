# debrief-fix: six debriefr fixes (session notes)

Session: debrief-fix. Branch: `debrief-fix`. Opened 2026-10-21.
This repository is public: names here are generic (Alice/Bob, octocat,
example-org/example-repo, myproject). Machine paths and real project names are
kept out of this file.

## For the manager

The manager session that opened this one closed on 2026-10-21 and
asked for these lines here instead of messages.

```
LESSON: debrief-fix In a git worktree whose package is also installed editable from the main checkout, a scratch script run by path imports the main checkout (its own directory, not the cwd, leads sys.path); print the module's __file__ before trusting any check.
LESSON: debrief-fix Code that can re-execute or exit the process needs a conftest-wide guard from its first commit; a test run whose output ends without a pass/fail summary line was killed, not passed.
CLOSE OK: debrief-fix branch debrief-fix at HEAD (the commit that adds this line); tests 69/69; notes notes/debrief-fix.md; lessons notes/debrief-fix.md 1-4
```

Also for the manager:
- Outside-text list (fixes 1 to 3, plus fix 4): the manager's tasks folder,
  `debrief-fix_outside-text_2026-10-21.md` (new untracked file; see the section
  "Text outside this repository" below).
- Deploy steps: section "Deploy steps" below (one shell profile line, a plain
  copy of the template, an import check).
- Board config lines for the P0/P1/P2 project: decision D6 below.
- Nothing waits on a ruling. Not pushed and not merged, per the session rules.

## Objective

Six fixes, in this order, each with a test that fails before the fix and passes
after it:

1. Archive prune: never prune by default; prune only with an explicit
   `--archive-ttl-days N`, and then move to the system Trash; refuse with a clear
   message when no Trash command exists. README and tests updated.
2. `--no-summary` with `archive-all`: the `.txt` and `.json` stay in
   `transcript_dir`; only the audio (and intermediates) is archived.
3. ffmpeg 9 vs torchcodec 0.11.1: (a) check the diarization audio decoder before
   transcription and fail at once with a message naming the fix; (b) write the
   resume cache right after transcription. Decide whether debriefr sets the
   library path itself. Live check on a 60 s clip.
4. `enroll` appends by default; `--replace` overwrites (GitHub issue #1).
5. `templates/debrief.md` becomes the single, generic source of `/debrief`.
6. `sync-issues`: optional per-project `priority_map` / `status_map` under
   `github:`.

Done means: all fixes committed on the branch, full test suite green, live check
recorded, deploy steps written, lessons committed.

## Inputs

- Code: `debriefr/meeting.py`, `debriefr/cli.py`, `debriefr/enroll.py`,
  `debriefr/sync_issues.py`, `debriefr/pipeline.py`.
- Live `/debrief` command (newer than the template): `~/.claude/commands/debrief.md`.
- Test environment: the main checkout's venv (`<main checkout>/.venv`, Python
  3.13.15, pytest 9.1.1). Run from the worktree root; `debriefr.__file__`
  resolves to the worktree (checked 2026-10-21).

## Outputs

- Code and tests on branch `debrief-fix`.
- `templates/debrief.md`, `README.md`.
- This notes file.

## How to run the tests

From the worktree root:

```bash
<main checkout>/.venv/bin/python -m pytest -q
```

## Decisions

D1 (fix 1). `--archive-ttl-days` and `run_meeting(archive_ttl_days=...)` default to
`None`, meaning keep forever. `prune_archive` returns at once for `None`. With an
explicit N it moves each old top-level file with the first Trash command found:
`trash` (macOS 15+ built in, trash-cli on Linux), `trash-put`, `gio trash`, then
Finder through `osascript` on older macOS. No command found: a `[warn]` line on
stderr says it refuses and how many files it left; the run itself still
completes, because pruning happens after the outputs are written and refusing
loses nothing. A non-zero exit from the Trash command is reported per file and
the file stays. No code path calls `unlink` on archived files any more.

D2 (fix 2). `_apply_cleanup(..., keep_transcripts=...)`; `run_meeting` passes
`keep_transcripts=summary_path is None`, which is true exactly when
`--no-summary` was given. Under `archive-all` only the source audio then moves;
the `.txt`/`.json` stay in `transcript_dir` and a line says so. The `gitignore`
fallback to `archive-all` passes the flag through. "Intermediates" in the task:
the only other intermediates are the derived wav (a temp dir, removed in the
`finally`) and the resume cache (removed on success, as before); neither is in
`transcript_dir` after a successful run, so there is nothing else to archive.

D3 (fix 3). (a) `debriefr/decoder.py:audio_decoder_error()` checks the exact
condition behind the NameError: whether `pyannote.audio.core.io` holds
`AudioDecoder` (pyannote imports it from torchcodec at import time and only
warns on failure). `run_meeting` raises `RuntimeError` with the fix before
`ensure_wav`/Whisper; the CLI runs `ensure_audio_decoder()` after argument and
registry checks and just before `run_meeting` (and in `enroll`, which decodes
through the same path). (b) The cache is saved right after Whisper with
`diarization_turns: null` plus `num_speakers`, and saved again after
diarization. `--resume` on a cache without turns runs diarization first.
The cache is removed after any successful run (before: only after a resumed
one), kept on failure and on `needs_sampling`. A failure after the cache
exists prints `resume with: debriefr transcribe --resume <cache>`. Caches
written by older versions (turns present, no `num_speakers`) still resume.

Library path: debriefr sets it itself, by restarting once. Reasons:
- dyld reads `DYLD_LIBRARY_PATH` only at process launch, so setting it inside a
  running process does nothing; the only in-process route is `os.execve`.
- Without it, every shell that runs debriefr needs the export, including each
  fresh Claude Code shell; notes on this Mac already disagree on which variable
  to set (`DYLD_LIBRARY_PATH` vs `DYLD_FALLBACK_LIBRARY_PATH`).
- Cost measured live: the check-only run took 2.6 s; the restart plus
  diarization of 60 s took 9.9 s in total.
Limits: macOS only (`sys.platform == "darwin"`); only when the check fails and a
keg-only `ffmpeg@8` (then 7 to 4) lib dir with `libavutil.*.dylib` exists under
`$HOMEBREW_PREFIX`, `/opt/homebrew` or `/usr/local`; at most once (env marker
`DEBRIEFR_DECODER_REEXEC=1`); the existing `DYLD_LIBRARY_PATH` is kept after the
keg. Library callers of `run_meeting` never get a restart, only the message.
The re-executed command is `sys.executable` plus `sys.orig_argv[1:]`, which
reproduces both the `debriefr` console script and `python -m debriefr.cli`.
Side effects checked: the variable reaches child processes; imageio-ffmpeg's
binary is static, and `security`/`git` are SIP-protected and drop DYLD
variables; the keg's lib dir holds only FFmpeg's own libraries.

D4 (fix 4). `enroll_speaker(..., replace=False)`; CLI `--replace`. Append is the
default: the stored stack (a legacy 1-D vector is read as one row) gets the new
rows below it. A new clip whose embedding has cosine >= 0.9999 with a stored
row is skipped (re-enrolling the same wav does not grow the file; the issue
suggested deduping). A stored stack of another dimension raises `ValueError`
naming `--replace` and leaves the file as it was. `--replace` prints how many
stored rows it overwrites.

D6 (fix 6). `sync_summary(..., priority_map=None, status_map=None)`; the CLI
reads both from the resolved project's `github:` block (project.yaml or a
projects.yaml entry; both loaders pass the whole block through). `map_option`
compares keys case-insensitively and sends unmapped values unchanged, so the
default (no maps) is byte-for-byte the old behaviour. Status keys are
debriefr's own status names (`Todo`, `In Progress`), not priorities. Option
names on the board were already matched case-insensitively, so `In Progress`
finds a board's `In progress` without a map entry.

Config lines for the project whose board uses P0/P1/P2 and
Backlog/Ready/In progress/In review/Done (add under its existing `github:`
block, next to `repo:` and `project:`):

```yaml
  priority_map:
    urgent: P0
    high: P1
    medium: P2
  status_map:
    Todo: Ready
```

With these, urgent rows get P0 and status In progress, high rows P1 and Ready,
medium rows P2 and Ready. `low` has no entry, so a low row still prints
"option 'low' not found" and its Priority stays empty; add `low: P2` (or a P3
option on the board) if that matters.

D5 (fix 5). `templates/debrief.md` now carries the live command's behaviour
(project.yaml single-project mode vs projects.yaml registry mode, the
`DEBRIEFR_PROJECTS_YAML` fallback, inbox staging in registry mode, tuning stop,
dry-run then `--yes`, filing after sync, full-path report) in generic words.
Venv resolution: `$DEBRIEFR_VENV/bin/debriefr` if set, else `command -v
debriefr`; the console script's first line (`#!<venv python>`) gives the Python
for the registry snippets, so no `python3` without debriefr is used. Checked in
bash: unset gives NOT FOUND (then the command asks the user), set and on-PATH
both give the venv's debriefr and python3.13. No machine path is in the file,
so the live copy is a plain copy; the machine-specific part moves into one
shell profile line (see Deploy steps). Fixes 1 to 4 and 6 are reflected:
no `--archive-ttl-days` unless asked; `--no-summary` transcripts stay in
`<transcript_dir>` (the old "move them back" note is gone); the decoder check
and its restart line; the `--resume` hint; `enroll` appends, `--replace`;
`priority_map`/`status_map` when the sync reports a missing option. The
em-dash rule is phrased as "apply the user's own writing rules", with the grep
written as `$'\xe2\x80\x94'` so the template contains no em dash; on a
machine whose instructions forbid em dashes this keeps the live behaviour.

## Progress

| Fix | Commit | Test before fix | Test after fix |
|-----|--------|-----------------|----------------|
| 1 prune | f42fcfc | `tests/test_archive_prune.py`: 7 failed of 7 | 7 passed; suite 35/35 |
| 2 no-summary | 21fc908 | `tests/test_run_meeting.py::test_no_summary_archive_all_keeps_transcripts` failed (txt moved to archive); the with-summary contrast passed | both passed; suite 37/37 |
| 3 decoder + cache | eb84480 | export of 21fc908: `tests/test_decoder.py` collection error (no module); `tests/test_run_meeting.py` 3 failed, 4 passed (the 4 are fix-2 tests and 2 guards) | suite 51/51 |
| 4 enroll append | 82d53cd | `tests/test_enroll.py`: 7 failed of 7 | 7 passed; suite 58/58 |
| 5 template | dcfba5d | `tests/test_template.py` against the old template (export of 4baf632): 7 failed of 7 | 7 passed; suite 69/69 |
| 6 board maps | 4baf632 | `tests/test_board_maps.py`: 3 failed, 1 passed (the default-board guard) | 4 passed; suite 62/62 |

Fix 1 live check (2026-10-21, macOS, `/usr/bin/trash`): a scratch
`.audio-archive/` with one file aged 30 days and one fresh file.
`prune_archive(d, None)` left both; `prune_archive(d, 7)` printed
"moved ... to Trash (age > 7d)", the old file was in `~/.Trash`, the fresh one
stayed.

Fix 3 live check (2026-10-21; gate: 72% free, no heavy process; worktree code
via `python -m debriefr.cli` from the worktree root; all outputs in the job
scratchpad). Clip: the first 60.0 s of an 80 s wav in the main checkout's
`samples/`, resampled to 16 kHz mono. A transcription-only cache (six
placeholder segments, `diarization_turns: null`) stood in for Whisper, so no
Whisper model was loaded.
- Run A, restart disabled (`DEBRIEFR_DECODER_REEXEC=1`), no
  `DYLD_LIBRARY_PATH`, fresh `transcribe --backend mlx`: exit 1 after 2.6 s,
  0 "transcribing" lines, the decoder message printed.
- Run B, restart allowed, no `DYLD_LIBRARY_PATH`, `transcribe --resume
  <cache> --no-summary --skip-sampling`: printed "restarting with
  DYLD_LIBRARY_PATH=<brew prefix>/opt/ffmpeg@8/lib", diarization on mps found
  1 speaker cluster, 6 segments merged to 1 turn, `.json`/`.txt` written, cache
  removed; exit 0 after 9.9 s.
- Swap before and after both runs: 2466.50 MB used of 4096 MB (unchanged).

## Bugs and surprises

S1. A helper script run as `python /scratch/x.py` imports the MAIN checkout's
debriefr (the venv holds an editable install of it), not the worktree: Python
puts the script's own directory on `sys.path`, not the cwd. The first live Trash
probe therefore ran the old `prune_archive` and raised on `ttl_days=None` before
touching anything. Fix: `sys.path.insert(0, <worktree>)` at the top of every
scratch script and print `debriefr.__file__`. `python -m pytest` from the
worktree root is not affected (it puts the cwd first).

S2. The first full test run after adding the pre-flight stopped after 18 tests
with no summary line: the existing CLI test calls `main()` for `transcribe`,
the pre-flight ran before the argument checks, the real decoder on this Mac is
broken, and `os.execve` really replaced the pytest process. Fixes: the CLI
pre-flight now runs after argument and registry checks, just before
`run_meeting`; `tests/conftest.py` sets `DEBRIEFR_DECODER_REEXEC=1` for every
test, and the restart tests remove it and patch `os.execve`.

S3. `samples/` holds no file of 60 s or more except one of 80 s (the next
largest is 50.3 s; three of them are 48 kHz). Byte size is not duration:
`ls -S` ranked a 48 kHz 50 s file above the 16 kHz 80 s one.

S4. `debriefr/enroll.py` passes `use_auth_token=` to `Model.from_pretrained`,
while pyannote 4.0.4 takes `token=` (as `pipeline.py` does); the unknown
keyword goes into `**kwargs`. Left unchanged (out of scope, not exercised by
the mocked tests); see follow-ups.

## Text outside this repository that fixes 1 to 3 make wrong

The exact list (file, line, quoted text, which fix) names people, projects and
machine paths, so it is not in this public repository. It is in the manager's
tasks folder as `debrief-fix_outside-text_2026-10-21.md` (an untracked new file
this session created there). Summary of what it covers: the workspace
`CLAUDE.md` (archive "auto-pruned after 7 days"; archive-all "keeps only
.md"), the workspace `.claudeignore` comment, the live `/debrief` command's
Step 4 and Step 5 notes, six memory files across three project memories
(prune reminder, `--no-summary` move-back advice, the FFmpeg 8 library-path
export, enroll overwriting), three open task files, two notes of a file-cleanup project
(one is a generated report: regenerate), and two lessons in one project's
lessons log (keep them, add that debriefr now handles the case). Sources: a
search agent over the workspace, `~/.claude` and all memories, plus a depth-3
grep of every project root in the shared registry; the main hits were re-read.

## Open follow-ups

F1. `sync-issues --link` still warns about missing low/medium/high/urgent
options even when the project has a `priority_map` (it has no project config).
About 15 lines; needs `--link` to resolve the registry.
F2. The sync's dry-run plan prints the unmapped priority (`urgent`), not the
board option (`P0`). Cosmetic.
F3. Version is still 0.2.0 although defaults changed (TTL, enroll append).
A bump is the owner's call.
F4. Alternative to FFmpeg entirely: hand pyannote in-memory waveforms read with
soundfile (`{"waveform", "sample_rate"}`) instead of file paths, at the three
call sites (diarize, identify crop, enroll). Removes the torchcodec/FFmpeg
dependency for wav input; needs a live comparison of diarization output.
F5. `import debriefr` imports `enroll` and so `pyannote.audio`, which prints a
roughly 100-line torchcodec warning before the pre-flight runs (seen in both
live runs). Lazy imports in `debriefr/__init__.py` would hide it.
F6. `enroll.py` `use_auth_token=` vs pyannote 4 `token=` (S4).
F7. The board in fix 6 has no option for `low`; a low row leaves Priority
empty unless the project adds `low: P2` or the board adds a P3.

## Deploy steps

After the merge of `debrief-fix` into `main` is approved (the main checkout's venv
holds an editable install of the main checkout, so the merge is what makes the code
live):

1. Code: no reinstall is needed for an editable install; the new module
   `debriefr/decoder.py` lives inside the package directory the editable finder
   maps. Confirm from a neutral directory (not the repo root):
   `$HOME/debriefr/.venv/bin/python -c "import debriefr.decoder, debriefr; print(debriefr.__file__)"`
   must print a path inside the main checkout. If it fails, run
   `$HOME/debriefr/.venv/bin/pip install -e $HOME/debriefr`.
2. Check the new defaults on the live install:
   `$HOME/debriefr/.venv/bin/debriefr transcribe --help | grep -A2 archive-ttl`
   must say "keep archived files forever".
3. Shell profile (the file that already exports `DEBRIEFR_PROJECTS_YAML`, i.e.
   `~/.bashrc` on this Mac): add, next to that line,
   `export DEBRIEFR_VENV="$HOME/debriefr/.venv"`.
   The `DYLD_LIBRARY_PATH` export some memory notes recommend is no longer
   needed: the CLI restarts itself with it.
4. `/debrief` command: back up, then plain copy (no substitutions):
   `cp ~/.claude/commands/debrief.md ~/.claude/commands/debrief.md.bak-2026-10-21`
   then `cp $HOME/debriefr/templates/debrief.md ~/.claude/commands/debrief.md`.
5. Add the board maps to the one project named in the fix-6 prompt (lines under
   D6 above).
6. Edit the outside text listed under "Text outside this repository".

## Lessons

1. Symptom: a scratch script run as `python <scratch>/x.py` tested the main
   checkout's code, not the worktree's. Cause: the venv has an editable install
   of the main checkout, and a script's own directory, not the cwd, goes first
   on `sys.path`. Fix: insert the worktree path at the top of each scratch
   script and print `debriefr.__file__`. Rule: in a worktree, prove which copy
   of the package a check imports before reading its result.
2. Symptom: the test suite silently stopped after 18 of 51 tests. Cause: a
   code path that calls `os.execve` ran for real inside pytest, because an
   existing test drove `main()` through it on a machine where the condition
   holds. Fix: run the restart after argument checks, and an autouse fixture
   that disables it in every test. Rule: any code that can replace or exit the
   process needs a test-wide guard in conftest from the first commit, and a
   suite whose output ends without a summary line has been killed, not passed.
3. Symptom: a fix that must happen "before transcription" was first placed
   before argument validation. Cause: "as early as possible" was read as "first
   line". Rule: place a pre-flight after the cheap user-input checks and before
   the first expensive step; users should see their own typo before an
   environment problem.
4. Symptom: the 60 s clip came out 50.3 s. Cause: the source was chosen by
   byte size, and a 48 kHz file outranked a longer 16 kHz one. Rule: pick audio
   by measured duration (`soundfile.info`), and check the cut's duration.
