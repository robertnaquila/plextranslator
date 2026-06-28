import os
from unittest import mock

from plextranslator.config import Config


def test_defaults_validate_requires_plex():
    cfg = Config()
    problems = cfg.validate()
    assert any("token" in p for p in problems)


def test_merge_only_applies_non_none():
    cfg = Config(plex_token="abc")
    merged = cfg.merge(plex_token=None, whisper_model="small")
    assert merged.plex_token == "abc"  # None ignored
    assert merged.whisper_model == "small"


def test_validate_llm_requires_key():
    cfg = Config(plex_baseurl="http://x", plex_token="t", use_llm=True)
    assert any("ANTHROPIC_API_KEY" in p for p in cfg.validate())
    cfg2 = cfg.merge(anthropic_api_key="k")
    assert not any("ANTHROPIC_API_KEY" in p for p in cfg2.validate())


def test_from_env_reads_values():
    env = {
        "PLEX_BASEURL": "http://host:32400",
        "PLEX_TOKEN": "tok",
        "PLEXTRANSLATOR_WHISPER_MODEL": "medium",
        "PLEXTRANSLATOR_USE_LLM": "true",
        "PLEXTRANSLATOR_CHUNK_SECONDS": "45",
    }
    with mock.patch.dict(os.environ, env, clear=False):
        cfg = Config.from_env()
    assert cfg.plex_baseurl == "http://host:32400"
    assert cfg.plex_token == "tok"
    assert cfg.whisper_model == "medium"
    assert cfg.use_llm is True
    assert cfg.chunk_seconds == 45


def test_from_env_bad_int_falls_back():
    with mock.patch.dict(os.environ, {"PLEXTRANSLATOR_CHUNK_SECONDS": "notanint"}):
        cfg = Config.from_env()
    assert cfg.chunk_seconds == Config().chunk_seconds
