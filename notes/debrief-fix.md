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

## Progress

| Fix | Commit | Test before fix | Test after fix |
|-----|--------|-----------------|----------------|
| 1 prune | (see git log) | `tests/test_archive_prune.py`: 7 failed of 7 | 7 passed; suite 35/35 |
| 2 no-summary | (see git log) | `tests/test_run_meeting.py::test_no_summary_archive_all_keeps_transcripts` failed (txt moved to archive); the with-summary contrast passed | both passed; suite 37/37 |

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

## Lessons
