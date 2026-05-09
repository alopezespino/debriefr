---
description: Summarize a diarized meeting transcript into structured notes (skill-based alternative to debriefr's API summarization).
allowed-tools: Bash, Read, Write
---

Summarize a meeting transcript using your own context window instead of an API call. The user will provide the path to a `.txt` transcript file as $ARGUMENTS. If $ARGUMENTS is empty, ask which transcript to summarize.

## Steps

1. **Read the transcript** at the given path. It is in diarized format: `[start - end] Speaker: text`.

2. **Read the summary prompt.** Find the installed debriefr package and read its bundled prompt:
   ```bash
   python3 -c "from debriefr.meeting import default_summary_prompt_path; print(default_summary_prompt_path())"
   ```
   Read the file at the printed path. Follow its instructions exactly (sections, formatting, `reference:` first line, language matching, action-item table schema).

3. **Auto-discover participant bios.** Find `projects.yaml` by searching upward from the transcript's directory:
   ```bash
   python3 -c "from debriefr.registry import discover_registry; r = discover_registry('<TRANSCRIPT_DIR>'); print(r or 'NOT_FOUND')"
   ```
   If found, read the YAML file. The top-level `participants:` and `guests:` keys map names to `{bio, github, email}`. For each speaker that appears in the transcript AND has a bio entry, prepend a "Known participants" block to your context:
   ```
   Known participants (use these bios verbatim; do not infer or override):
   - Name: bio text
   ```
   If no registry is found, skip this step (summaries work fine without bios).

4. **Generate the summary** following the prompt instructions. Remember:
   - First line: `reference: <slug>` (1-3 word lowercase-hyphen topic identifier).
   - Match the transcript's language for the body (Spanish transcript -> Spanish summary).
   - `reference:` line stays in English regardless.

5. **Write the `.md` file.** Strip the `reference:` line (and any blank line after it) from the body before writing. Determine the output path:
   - If the transcript is `<stem>.txt`, write to `<stem>.md` in the same directory.
   - If a `.md` already exists at that path, ask before overwriting.

6. **Rename transcripts** if the reference slug differs from the current filename stem:
   - Parse the date prefix from the current stem (e.g., `2026-04-15_collab` -> date is `2026-04-15`).
   - New stem: `<date>_<reference>`. If files with that stem already exist, append `_2`, `_3`, etc.
   - Rename `.json`, `.txt`, and `.md` to the new stem. Print the renames.

7. **Report** the path to the written summary file.
