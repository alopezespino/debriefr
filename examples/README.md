# debriefr examples

End-to-end walkthroughs. Replace `alice`, `bob`, and `username/repo` with your real values.

## 1. First-time setup

```bash
pip install -e ".[mlx]"     # or [local], or leave off extras if using Groq
hf auth login                # and accept the 3 pyannote licenses in browser
export GROQ_API_KEY=sk-...   # free tier: https://console.groq.com
export ANTHROPIC_API_KEY=sk-...
gh auth login                # only if you want issue sync
```

## 2. Enroll two speakers

```bash
mkdir -p samples
# Trim each clip to >= 20-30s of single-voice audio (Audacity is handy).
ffmpeg -ss 00:00:30 -to 00:01:00 -i meeting.m4a -ac 1 -ar 16000 samples/alice.wav
ffmpeg -ss 00:02:00 -to 00:02:45 -i meeting.m4a -ac 1 -ar 16000 samples/bob.wav

debriefr enroll --name Alice --audio samples/alice.wav
debriefr enroll --name Bob   --audio samples/bob.wav
```

## 3. Transcribe a meeting

```bash
mkdir -p meetings
debriefr transcribe recording.m4a \
  --output-dir meetings \
  --participants Alice Bob \
  --language en \
  --bio Alice="lead engineer, owns API gateway" \
  --bio Bob="PM, owns roadmap"
```

Three files land in `meetings/` with a topic-derived stem, e.g.:

```
meetings/2026-04-17_onboarding-plan.json
meetings/2026-04-17_onboarding-plan.txt
meetings/2026-04-17_onboarding-plan.md
```

## 4. Set up a GitHub Project v2 once

```bash
debriefr sync-issues --setup --title "My Project - Action Items"
# note the project number printed at the end; use it below
```

## 5. Sync this meeting's action items

```bash
# Preview what will happen
debriefr sync-issues meetings/2026-04-17_onboarding-plan.md \
  --repo username/repo \
  --project 42 \
  --handle Alice=alice-gh \
  --handle Bob=bob-gh \
  --dry-run

# If the plan looks right, re-run without --dry-run
```

## 6. Iterate: re-sync after editing the summary

The sync command is idempotent - rows already synced are skipped. If you edit the summary to add new action items, re-run and only the new rows are pushed.

```bash
debriefr sync-issues meetings/2026-04-17_onboarding-plan.md \
  --repo username/repo --project 42 \
  --handle Alice=alice-gh --handle Bob=bob-gh
```

## 7. Use just the issue sync without transcribing

debriefr's `sync-issues` only needs a markdown file with an `## Action Items` table in the expected schema. You can write notes by hand and sync them the same way. The repo ships a ready-to-use skeleton at [`templates/summary_template.md`](../templates/summary_template.md):

```bash
cp templates/summary_template.md meetings/2026-04-24_notes.md
# fill in the sections and the Action Items table in your editor

debriefr sync-issues meetings/2026-04-24_notes.md \
  --repo username/repo --project 42 \
  --handle Alice=alice-gh --handle Bob=bob-gh
```

Only the `## Action Items` table is parsed; the other sections are for your own reference.
