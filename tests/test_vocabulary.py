"""Per-project vocabulary: parsing, people merge, Whisper prompt, corrections."""
from __future__ import annotations

import json

import pytest

from debriefr import meeting
from debriefr.cli import _resolve_registry, _vocabulary_terms, main
from debriefr.pipeline import deserialize_diarization
from debriefr.vocabulary import (build_vocabulary, correct_segments, load_vocabulary_file,
                                 parse_vocabulary, summary_block, whisper_prompt)

VOCAB = """\
# header comment
Jordan Avery <- Jordon | Jorden
Northwind Fund <- Northwind
Delphi <- DLF | Delfy
CostBase <- Cost Base | cost bass
Timber Index <- timber indecks   # trailing comment
Riverton
"""


def test_parse_format():
    terms = parse_vocabulary(VOCAB)
    assert [t.name for t in terms] == [
        "Jordan Avery", "Northwind Fund", "Delphi", "CostBase", "Timber Index", "Riverton"]
    assert terms[0].fixes == {"Jordon": "Jordan Avery", "Jorden": "Jordan Avery"}
    assert terms[4].fixes == {"timber indecks": "Timber Index"}
    assert terms[5].fixes == {}


def test_corrections_on_fixture_transcript(capsys):
    terms = build_vocabulary(parse_vocabulary(VOCAB))
    segs = [
        {"text": "Jordon said cost bass and Cost Base differ."},
        {"text": "Timber indecks prices; timber indecks again."},
        {"text": "Northwind Fund funds it; ask Northwind."},
        {"text": "DLF data, not dewy grass. Fredrick and Jordonn stay."},
    ]
    counts = correct_segments(segs, terms)

    assert segs[0]["text"] == "Jordan Avery said CostBase and CostBase differ."
    # a lowercase mishearing matches any case
    assert segs[1]["text"] == "Timber Index prices; Timber Index again."
    # the correct spelling is never re-corrected
    assert segs[2]["text"] == "Northwind Fund funds it; ask Northwind Fund."
    # a capitalized mishearing matches only as written; whole words only
    assert segs[3]["text"] == "Delphi data, not dewy grass. Fredrick and Jordonn stay."
    assert counts == {"Jordan Avery": 1, "CostBase": 2, "Timber Index": 2,
                      "Northwind Fund": 1, "Delphi": 1}
    assert "vocabulary corrections:" in capsys.readouterr().out


def test_missing_file_prints_notice_and_continues(tmp_path, capsys):
    assert load_vocabulary_file(tmp_path / "nope.txt") == []
    assert "vocabulary file not found" in capsys.readouterr().out
    assert load_vocabulary_file(None) == []


def test_people_merge_without_duplicates():
    people = {"Sam": {"full_name": "Samuel Okafor"}, "Ale": {}}
    guests = {"Jordan": {"full_name": "Jordan Q. Avery"}, "Matt": {}}
    file_terms = parse_vocabulary("Sam Okafor <- Sam Okafore\nJordan Avery <- Jordon\nAle\n")
    terms = build_vocabulary(file_terms, people, guests)

    names = [t.name for t in terms]
    assert names == ["Sam", "Ale", "Jordan", "Matt"]
    jordan = terms[2]
    assert jordan.spellings == ["Jordan", "Jordan Q. Avery", "Jordan Avery"]
    # the file's spelling stays the correction target
    assert jordan.fixes == {"Jordon": "Jordan Avery"}
    segs = [{"text": "Sam Okafore met Jordon."}]
    correct_segments(segs, terms)
    assert segs[0]["text"] == "Sam Okafor met Jordan Avery."


def test_prompt_order_and_limit(capsys):
    file_terms = parse_vocabulary("Zeta\nZephyr <- Zefir\n")
    guests = {f"Guest{i} Person{i}": {} for i in range(80)}
    terms = build_vocabulary(file_terms, {"Ale": {}}, guests)
    prompt = whisper_prompt(terms)

    import tiktoken
    assert len(tiktoken.get_encoding("gpt2").encode(prompt)) <= 224
    # terms with mishearings first, then people, then file terms, then guests
    assert prompt.startswith("Glossary: Zephyr, Ale, Zeta, Guest0 Person0")
    assert "left out of the prompt" in capsys.readouterr().out
    # the summary still lists every term
    assert "Guest79 Person79" in summary_block(terms)


@pytest.fixture
def vocab_project(tmp_path, write_yaml_file, project_yaml_data, monkeypatch):
    proj = tmp_path / "proj"
    data = dict(project_yaml_data)
    data["guests"] = {"Jordan": {"full_name": "Jordan Q. Avery", "bio": "Business manager."}}
    data["vocabulary"] = "meetings/vocabulary.txt"
    write_yaml_file(proj / "project.yaml", data)
    (proj / "meetings").mkdir()
    (proj / "meetings" / "vocabulary.txt").write_text("CostBase <- cost bass\n")
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(proj)
    return proj


def test_project_yaml_guest_reaches_prompt_and_summary(vocab_project):
    config, people = _resolve_registry(None, None, None)
    assert config["vocabulary"] == vocab_project / "meetings" / "vocabulary.txt"
    assert "Jordan" in people                       # project.yaml guests are read

    terms = _vocabulary_terms(config, None, ["Alice"])
    prompt = whisper_prompt(terms)
    block = summary_block(terms)
    for name in ("Jordan", "Jordan Q. Avery", "Alice", "Bob", "CostBase"):
        assert name in prompt and name in block


def test_vocab_init_adds_key_and_template(tmp_path, write_yaml_file, project_yaml_data,
                                          monkeypatch, capsys):
    proj = tmp_path / "proj"
    write_yaml_file(proj / "project.yaml", project_yaml_data)
    monkeypatch.delenv("DEBRIEFR_PROJECTS_YAML", raising=False)
    monkeypatch.chdir(proj)
    monkeypatch.setattr("sys.argv", ["debriefr", "vocab", "init"])
    main()

    assert "vocabulary: meetings/vocabulary.txt" in (proj / "project.yaml").read_text()
    assert (proj / "meetings" / "vocabulary.txt").read_text().startswith("# Proper nouns")
    (proj / "meetings" / "vocabulary.txt").write_text("Kept\n")
    main()                                          # second run changes nothing
    assert (proj / "meetings" / "vocabulary.txt").read_text() == "Kept\n"
    assert (proj / "project.yaml").read_text().count("vocabulary:") == 1


def test_vocab_apply_rewrites_json_and_txt(vocab_project, monkeypatch):
    tj = vocab_project / "meetings" / "2026-10-26_x.json"
    tj.write_text(json.dumps({"segments": [
        {"start": 0.0, "end": 1.0, "speaker": "Alice", "text": "cost bass data."}]}))
    monkeypatch.setattr("sys.argv", ["debriefr", "vocab", "apply", str(tj)])
    main()

    assert json.loads(tj.read_text())["segments"][0]["text"] == "CostBase data."
    assert "Alice: CostBase data." in tj.with_suffix(".txt").read_text()


def test_run_meeting_uses_vocabulary(tmp_path, monkeypatch):
    seen = {}

    def fake_transcribe(wav, backend, model, language, prompt=None):
        seen["prompt"] = prompt
        return [{"start": 0.0, "end": 2.0, "text": "Jordon bought cost bass."}], "en"

    def fake_summarize(*a, **k):
        seen["vocab_block"] = k.get("vocabulary_block")
        return "reference: data_buy\n\nBody."

    monkeypatch.setattr(meeting, "transcribe", fake_transcribe)
    monkeypatch.setattr(meeting, "diarize", lambda *a: (deserialize_diarization(
        [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]), None))
    monkeypatch.setattr(meeting, "audio_decoder_error", lambda: None, raising=False)
    monkeypatch.setattr(meeting, "summarize", fake_summarize)
    monkeypatch.setattr(meeting, "_secret_store_get", lambda service: "test-key")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    out = tmp_path / "out"
    terms = build_vocabulary(parse_vocabulary("Jordan Avery <- Jordon\nCostBase <- cost bass\n"))

    res = meeting.run_meeting(
        audio=str(audio), output_dir=str(out), participants=["Alice"],
        enrollments_dir=str(tmp_path / "e"), samples_dir=str(tmp_path / "s"),
        date="2026-10-26", skip_sampling=True, vocabulary=terms,
    )

    assert seen["prompt"].startswith("Glossary: Jordan Avery, CostBase")
    assert "CostBase" in seen["vocab_block"]
    data = json.loads(res["transcript_json"].read_text())
    assert data["segments"][0]["text"] == "Jordan Avery bought CostBase."
    assert "Jordan Avery bought CostBase." in res["transcript_txt"].read_text()
