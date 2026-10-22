"""enroll appends to an existing enrollment by default; --replace overwrites it."""
from __future__ import annotations

import sys

import numpy as np
import pytest

from debriefr import enroll

VECS = {
    "a.wav": [1.0, 0.0, 0.0],
    "b.wav": [0.0, 2.0, 0.0],   # not unit length: enroll normalizes
    "c.wav": [0.0, 0.0, 1.0],
}


@pytest.fixture(autouse=True)
def fake_models(monkeypatch):
    class FakeModel:
        @staticmethod
        def from_pretrained(*a, **k):
            return object()

    def fake_inference(model, window, device):
        return lambda path: np.array(VECS[path.split("/")[-1]])

    monkeypatch.setattr(enroll, "Model", FakeModel)
    monkeypatch.setattr(enroll, "Inference", fake_inference)


def test_enroll_appends_by_default(tmp_path):
    enroll.enroll_speaker("Alice", ["a.wav"], out_dir=tmp_path)
    out = enroll.enroll_speaker("Alice", ["b.wav", "c.wav"], out_dir=tmp_path)

    arr = np.load(out)
    assert arr.shape == (3, 3)
    np.testing.assert_allclose(arr, np.eye(3))


def test_enroll_replace_overwrites(tmp_path):
    enroll.enroll_speaker("Alice", ["a.wav", "b.wav"], out_dir=tmp_path)
    out = enroll.enroll_speaker("Alice", ["c.wav"], out_dir=tmp_path, replace=True)

    np.testing.assert_allclose(np.load(out), [[0.0, 0.0, 1.0]])


def test_enroll_appends_to_legacy_single_vector(tmp_path):
    np.save(tmp_path / "Alice.npy", np.array([1.0, 0.0, 0.0]))
    out = enroll.enroll_speaker("Alice", ["b.wav"], out_dir=tmp_path)

    np.testing.assert_allclose(np.load(out), [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def test_enroll_skips_clip_already_stored(tmp_path, capsys):
    enroll.enroll_speaker("Alice", ["a.wav"], out_dir=tmp_path)
    out = enroll.enroll_speaker("Alice", ["a.wav", "b.wav"], out_dir=tmp_path)

    assert np.load(out).shape == (2, 3)
    assert "skipped 1 clip(s) already stored" in capsys.readouterr().out


def test_enroll_dimension_mismatch_refuses(tmp_path):
    np.save(tmp_path / "Alice.npy", np.ones((2, 4)))
    with pytest.raises(ValueError, match="--replace"):
        enroll.enroll_speaker("Alice", ["a.wav"], out_dir=tmp_path)
    assert np.load(tmp_path / "Alice.npy").shape == (2, 4)


@pytest.mark.parametrize("extra, expected", [([], False), (["--replace"], True)])
def test_cli_replace_flag(monkeypatch, extra, expected):
    from debriefr import decoder
    monkeypatch.setattr(decoder, "audio_decoder_error", lambda: None)
    seen = {}
    monkeypatch.setattr(enroll, "enroll_speaker",
                        lambda name, audio, **kw: seen.update(name=name, audio=audio, **kw))

    from debriefr.cli import main
    monkeypatch.setattr(sys, "argv",
                        ["debriefr", "enroll", "--name", "Alice", "--audio", "a.wav", *extra])
    main()
    assert seen["name"] == "Alice" and seen["audio"] == ["a.wav"]
    assert seen["replace"] is expected
