# debrief-fix: six debriefr fixes (session notes)

Session: debrief-fix. Branch: `debrief-fix`. Opened 2026-10-21.
This repository is public: names here are generic (Alice/Bob, octocat,
example-org/example-repo, myproject). Machine paths and real project names are
kept out of this file.

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
| 1 prune | (see git log) | `tests/test_archive_prune.py`: 7 failed of 7 | 7 passed; suite 35/35 |
| 2 no-summary | (see git log) | `tests/test_run_meeting.py::test_no_summary_archive_all_keeps_transcripts` failed (txt moved to archive); the with-summary contrast passed | both passed; suite 37/37 |
| 3 decoder + cache | (see git log) | export of 21fc908: `tests/test_decoder.py` collection error (no module); `tests/test_run_meeting.py` 3 failed, 4 passed (the 4 are fix-2 tests and 2 guards) | suite 51/51 |
| 4 enroll append | (see git log) | `tests/test_enroll.py`: 7 failed of 7 | 7 passed; suite 58/58 |
| 5 template | (see git log) | `tests/test_template.py` against the old template (export of 4baf632): 7 failed of 7 | 7 passed; suite 69/69 |
| 6 board maps | (see git log) | `tests/test_board_maps.py`: 3 failed, 1 passed (the default-board guard) | 4 passed; suite 62/62 |

Fix 1 live check (2026-10-21, macOS, `/usr/bin/trash`): a scratch
`.audio-archive/` with one file aged 30 days and one fresh file.
`prune_archive(d, None)` left both; `prune_archive(d, 7)` printed
"moved ... to Trash (age > 7d)", the old file was in `~/.Trash`, the fresh one
stayed.

## Bugs and surprises

S1. A helper script run as `python /scratch/x.py` imports the MAIN checkout's
debriefr (the venv holds an editable install of it), not the worktree: Python
puts the script's own directory on `sys.path`, not the cwd. The first live Trash
probe therefore ran the old `prune_archive` and raised on `ttl_days=None` before
touching anything. Fix: `sys.path.insert(0, <worktree>)` at the top of every
scratch script and print `debriefr.__file__`. `python -m pytest` from the
worktree root is not affected (it puts the cwd first).

## Text outside this repository that fixes 1 to 3 make wrong

## Open follow-ups

## Deploy steps

After Ale approves the merge of `debrief-fix` into `main` (the main checkout's venv
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
