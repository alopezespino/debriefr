"""hf_auth: given token, then HF_TOKEN, then the secret store, then hf's own login."""
from debriefr import pipeline


def test_given_token_wins(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "from-env")
    monkeypatch.setattr(pipeline, "_secret_store_get", lambda s: "from-store")
    assert pipeline.hf_auth("given") == "given"


def test_env_before_store(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "from-env")
    monkeypatch.setattr(pipeline, "_secret_store_get", lambda s: "from-store")
    assert pipeline.hf_auth() == "from-env"


def test_store_item_name(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    asked = []
    monkeypatch.setattr(pipeline, "_secret_store_get",
                        lambda s: asked.append(s) or "from-store")
    assert pipeline.hf_auth() == "from-store"
    assert asked == ["hf_token"]


def test_falls_back_to_hf_login(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(pipeline, "_secret_store_get", lambda s: None)
    assert pipeline.hf_auth() is True
