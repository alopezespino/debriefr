# debriefr

Drop a meeting recording, get a diarized transcript, a structured summary, and GitHub issues for each action item. Designed to work with [Claude Code](https://claude.ai/code) as the orchestrator.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/downloads/)

## How it works

You drop an audio file in your workspace. Claude Code picks it up, asks which project it belongs to, and runs the pipeline end to end:

1. **Transcribes** speech with Whisper (multiple backends: Groq free tier, OpenAI API, MLX for Apple Silicon, or faster-whisper for CPU/CUDA).
2. **Diarizes** (who spoke when) with pyannote.
3. **Identifies** enrolled speakers by matching voice embeddings to reference voiceprints you build once per person. Unknown speakers trigger interactive auto-sampling: the pipeline extracts clean clips, pauses for you to label them, and resumes.
4. **Summarizes** the transcript with Claude into structured markdown: participants, discussion points, decisions, open questions, and an action-items table.
5. **Syncs** action items to GitHub Issues on a Project v2 board with Priority + Due fields.

The output lands in the right project folder, intermediates are cleaned up, and you're done.

You can also run every step manually from the command line -- debriefr is a standard Python CLI tool. But the intended workflow is Claude Code orchestration via the `/debrief` skill.

## Quick start

### 1. Install debriefr

```bash
git clone https://github.com/alopezespino/debriefr.git
cd debriefr
pip install -e .
```

For local Whisper (no cloud API needed), add an extra:

```bash
pip install -e .[mlx]    # Apple Silicon (recommended for 16 GB+ Macs)
pip install -e .[local]  # cross-platform (faster-whisper, CPU or CUDA)
pip install -e .[all]    # both
```

ffmpeg is bundled via `imageio-ffmpeg`; no system install needed.

### 2. Set up credentials

debriefr needs three services. Each step below shows a one-shot `export` and how to persist it securely.

#### HuggingFace (required -- diarization models are license-gated)

1. Sign up at <https://huggingface.co/>, create a **Read** token at [Settings > Access Tokens](https://huggingface.co/settings/tokens).
2. Log in: `hf auth login` (paste your token).
3. Accept licenses for [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1), [segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0), and [wespeaker-voxceleb-resnet34-LM](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM).

#### Whisper backend (cloud backends only)

Skip if using MLX or faster-whisper.

**Groq (recommended, free):** sign up at <https://console.groq.com/>, get a key at [API Keys](https://console.groq.com/keys).

```bash
export GROQ_API_KEY=gsk_...
```

Persist on **macOS**: `security add-generic-password -a "$USER" -s groq_api_key -w`, then add to `~/.zshrc`: `export GROQ_API_KEY=$(security find-generic-password -a "$USER" -s groq_api_key -w)`.

Persist on **Linux**: `secret-tool store --label="Groq API key" service groq_api_key account "$USER"`, then add to `~/.bashrc`: `export GROQ_API_KEY=$(secret-tool lookup service groq_api_key account "$USER")`.

The `auto` backend picks Groq > MLX > faster-whisper > OpenAI, whichever is available first.

#### Anthropic (required for summarization)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Persist the same way as above (replace service name with `anthropic_api_key`).

#### GitHub CLI (optional, for issue sync)

```bash
gh auth login
```

### 3. Set up your project registry

Copy the example to your workspace:

```bash
cp debriefr/templates/projects_example.yaml projects/projects.yaml
```

Edit it to define your participants and projects:

```yaml
participants:
  Alice:
    bio: Staff engineer, owns the API gateway.
    github: alice-gh
  Bob:
    bio: Product manager, drives roadmap.
    github: bob-gh

guests:
  Speaker:
    bio: External consultant.
    github: null

ProjectAlpha:
  path: /path/to/project-alpha
  transcript_dir: meetings
  default_participants: [Alice, Bob]
  cleanup: archive-all
  github:
    repo: alice-gh/project-alpha
    project: 42

ProjectBeta:
  path: /path/to/project-beta
  transcript_dir: notes/meetings
  default_participants: [Alice]
  # no github: section -- pipeline ends at summary
```

**Participants** are defined once and shared across projects. **Guests** are one-off speakers (same schema, separate section to avoid clutter). **GitHub** is optional per project -- omit it entirely for projects that don't use issue tracking.

debriefr auto-discovers `projects.yaml` by searching upward from the audio file's location for `projects.yaml` or `projects/projects.yaml`.

### 4. Install the `/debrief` skill

Copy the bundled skill template into your Claude Code workspace:

```bash
mkdir -p .claude/commands
cp debriefr/templates/debrief.md .claude/commands/debrief.md
```

This gives you `/debrief path/to/transcript.txt` in Claude Code -- it reads the transcript, auto-discovers participant bios from `projects.yaml`, and generates the summary using Claude's own context window.

### 5. Use it

Drop an audio file in your workspace and tell Claude Code to process it. Or run directly:

```bash
debriefr transcribe recording.m4a --project ProjectAlpha
```

That's it. The `--project` flag resolves output directory, participants, bios, and cleanup mode from the registry.

## Speaker enrollment

### Auto-sampling (recommended)

When you run `debriefr transcribe` and some participants aren't enrolled, the pipeline automatically:

1. Runs transcription and diarization to detect speaker clusters.
2. Extracts ~45 s of clean speech per unknown cluster (filtering out short interjections and cross-talk).
3. Saves clips to `samples/SPEAKER_XX.wav`.
4. Saves a cache file so the expensive transcription + diarization don't re-run.
5. Exits with instructions for labeling.

After reviewing the clips (listen with `afplay` on macOS or `aplay` on Linux), rename, enroll, and resume:

```bash
afplay samples/SPEAKER_02.wav

mv samples/SPEAKER_02.wav samples/Alice.wav
debriefr enroll --name Alice --audio samples/Alice.wav

debriefr transcribe --resume ./meetings/2026-04-17_meeting.cache.json \
  --project ProjectAlpha
```

As your enrollment library grows, the pipeline matches more speakers automatically. Use `--skip-sampling` to skip the interactive pause when you want to proceed with whatever enrollments are available.

### Manual enrollment

Make a clean reference clip per person: >= 20-30 s of single-voice audio with no overlap. Save as 16 kHz mono wav.

```bash
debriefr enroll --name Alice --audio samples/alice.wav
debriefr enroll --name Bob   --audio samples/bob_1.wav samples/bob_2.wav
```

Multiple clips per person are stored individually. At identification time, each cluster is scored against every clip and the best cosine match wins -- so contamination in one clip doesn't block a match on another.

## Cleanup modes

After processing, debriefr cleans up intermediates. Set the mode with `--cleanup` or per-project in `projects.yaml`:

| Mode | What it does | Best for |
|------|-------------|----------|
| `archive-all` | Moves audio + `.txt` + `.json` to `.audio-archive/`. Only `.md` stays. Prunes files older than `--archive-ttl-days` (default: 7). | Default with `--project`. |
| `archive-audio` | Moves only source audio. Transcripts stay alongside the summary. | Transcripts in the repo, raw audio out. |
| `gitignore` | Keeps everything in place. Adds patterns to `.gitignore` (once per project). | All files locally, nothing in git. |

Without `--project` and without `--cleanup`, no cleanup is performed.

## Syncing action items to GitHub

For projects with `github:` config, debriefr pushes each action item to a **GitHub Project v2** board.

**One-time setup:**

```bash
debriefr sync-issues --setup --title "My Project - Action Items"
```

Creates a Project v2 with Priority and Due fields. Note the project number and add it to `projects.yaml` under `github.project`. Iteration fields must be added manually in the browser.

**Per meeting:**

```bash
debriefr sync-issues meetings/summary.md --project ProjectAlpha
debriefr sync-issues meetings/summary.md --project ProjectAlpha --dry-run
```

Or without a registry:

```bash
debriefr sync-issues meetings/summary.md \
  --repo owner/repo --gh-project 42 \
  --handle Alice=alice-gh --handle Bob=bob-gh
```

Features: duplicate detection (Jaccard >= 0.45), idempotent sync markers, iteration assignment by due date, issue-to-task linking in the summary.

## CLI reference

### `debriefr transcribe`

| Flag | Description |
|------|-------------|
| `--project SLUG` | Resolve config from projects.yaml |
| `--projects-yaml PATH` | Explicit registry path (skips auto-discovery) |
| `--output-dir DIR` | Output directory (resolved from registry with `--project`) |
| `--participants NAME ...` | Speaker names (resolved from registry with `--project`) |
| `--bio NAME=BIO` | Participant bio for summarizer (repeatable; merges with registry bios) |
| `--backend {auto,groq,openai,mlx,faster-whisper}` | Whisper backend |
| `--language CODE` | ISO language code (omit for auto-detect) |
| `--num-speakers N` | Pin speaker count (improves diarization) |
| `--threshold FLOAT` | Speaker ID cosine threshold (default: 0.5) |
| `--date YYYY-MM-DD` | Override meeting date (default: audio file mtime) |
| `--summary-prompt PATH` | Custom summary prompt file |
| `--summary-model MODEL` | Anthropic model for summary |
| `--no-summary` | Transcribe only; skip Claude |
| `--skip-sampling` | Don't pause for unknown speakers |
| `--resume CACHE` | Resume from cache after enrollment |
| `--cleanup MODE` | `gitignore`, `archive-all`, or `archive-audio` |
| `--archive-ttl-days N` | Prune archived files older than N days (default: 7) |

### `debriefr enroll`

| Flag | Description |
|------|-------------|
| `--name NAME` | Speaker name |
| `--audio FILE ...` | One or more reference audio files |
| `--out-dir DIR` | Enrollment output directory (default: `./enrollments`) |

### `debriefr sync-issues`

| Flag | Description |
|------|-------------|
| `--project SLUG` | Resolve repo, gh-project, handles from registry |
| `--repo OWNER/NAME` | GitHub repo |
| `--gh-project NUMBER` | GitHub Project v2 number |
| `--handle NAME=HANDLE` | Name-to-GitHub-handle mapping (repeatable) |
| `--setup` | Create a new Project v2 with Priority + Due fields |
| `--link NUMBER` | Validate existing Project, create missing fields |
| `--title TEXT` | Title for new project (required with `--setup`) |
| `--dry-run` | Preview without creating |

## Pick a Whisper backend

| Backend | Where it runs | Cost | Install extra | Best for |
|---------|--------------|------|---------------|----------|
| Groq | Cloud | Free tier | None | Default. Fast, zero local RAM. |
| OpenAI | Cloud | ~$0.006/min | None | Alternative cloud option. |
| MLX | Local (Apple Silicon) | Free | `[mlx]` | 16 GB+ Macs with active cooling. |
| faster-whisper | Local (CPU/CUDA) | Free | `[local]` | Privacy-sensitive or GPU setups. |

The `auto` backend picks Groq > MLX > faster-whisper > OpenAI, whichever is available first.

## Cost estimate (Groq + Claude, 1-hour meeting)

| Step | Service | Cost |
|------|---------|------|
| Transcription | Groq (whisper-large-v3) | $0.00 (free tier) |
| Summarization | Claude Opus 4.6 (default) | ~$0.50 |
| **Total** | | **~$0.50** |

Using `--summary-model claude-sonnet-4-6` drops summarization to ~$0.08. The summary prompt is cached (`cache_control: ephemeral`).

## Scope

This is a personal tool I use daily; I'm sharing it in case it helps others. Contributions welcome but not solicited.

- **Tested on:** macOS 14+ and Ubuntu 22.04+.
- **Windows users:** use WSL2.
- **Stability:** this evolves with my workflow. Pin a version if you need stability.

## License

AGPL-3.0. See [LICENSE](LICENSE) for the full text.

Built by [alopezespino](https://github.com/alopezespino).
