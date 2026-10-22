"""templates/debrief.md is the single, machine-independent source of /debrief."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "debrief.md"


@pytest.fixture(scope="module")
def text() -> str:
    return TEMPLATE.read_text()


def test_no_machine_specific_paths(text):
    assert not re.search(r"/Users/|/home/|C:\\\\", text)
    assert "/path/to/debriefr" not in text


def test_venv_resolution_is_portable(text):
    assert "DEBRIEFR_VENV" in text
    assert "command -v debriefr" in text
    # bare python3 does not see the venv's debriefr package
    assert 'python3 -c "' not in text and "python3 -c '" not in text


def test_both_registry_modes_and_env_fallback(text):
    assert 'Kind `"project"`' in text and 'Kind `"registry"`' in text
    assert "DEBRIEFR_PROJECTS_YAML" in text
    assert "load_any" in text


def test_reflects_archive_and_no_summary_fixes(text):
    assert "kept\n  indefinitely" in text or "kept indefinitely" in text
    assert "back from `.audio-archive/`" not in text
    assert "stay in `<transcript_dir>`" in text


def test_names_decoder_check_and_resume(text):
    assert "audio decoder" in text
    assert "brew install ffmpeg@8" in text
    assert "--resume <cache-file>" in text


def test_mentions_board_maps_and_enroll_replace(text):
    assert "priority_map" in text and "status_map" in text
    assert "--replace" in text


def test_no_em_dash_or_section_sign(text):
    assert "\u2014" not in text
    assert "\u00a7" not in text
