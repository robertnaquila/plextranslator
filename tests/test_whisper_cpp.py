import pytest

from plextranslator.config import Config
from plextranslator.transcriber import (
    Transcriber,
    WhisperCppTranscriber,
    build_whisper_cpp_cmd,
    make_transcriber,
)


def test_build_cmd_core_flags():
    cmd = build_whisper_cpp_cmd(
        "whisper-cli", "/m/ggml-small.bin", "/tmp/a.wav", "/tmp/out"
    )
    assert cmd[0] == "whisper-cli"
    assert cmd[cmd.index("-m") + 1] == "/m/ggml-small.bin"
    assert cmd[cmd.index("-f") + 1] == "/tmp/a.wav"
    assert cmd[cmd.index("-of") + 1] == "/tmp/out"
    assert "--translate" in cmd
    assert "--output-srt" in cmd
    # no explicit language -> auto
    assert cmd[cmd.index("-l") + 1] == "auto"


def test_build_cmd_language_and_threads():
    cmd = build_whisper_cpp_cmd(
        "whisper-cli", "/m.bin", "/a.wav", "/out",
        source_language="ko", threads=4, beam_size=0,
    )
    assert cmd[cmd.index("-l") + 1] == "ko"
    assert cmd[cmd.index("-t") + 1] == "4"
    assert "-bs" not in cmd  # beam_size=0 omitted


def test_build_cmd_beam_size_and_extra_args():
    cmd = build_whisper_cpp_cmd(
        "whisper-cli", "/m.bin", "/a.wav", "/out", beam_size=5, extra_args=["-nt"]
    )
    assert cmd[cmd.index("-bs") + 1] == "5"
    assert cmd[-1] == "-nt"


def test_whisper_cpp_requires_model():
    with pytest.raises(ValueError):
        WhisperCppTranscriber(model_path="")


def test_make_transcriber_selects_whisper_cpp():
    cfg = Config(backend="whisper.cpp", whisper_cpp_model="/m/ggml-small.bin")
    t = make_transcriber(cfg)
    assert isinstance(t, WhisperCppTranscriber)
    assert t.model_path == "/m/ggml-small.bin"


def test_make_transcriber_defaults_to_faster_whisper():
    cfg = Config()  # default backend
    assert isinstance(make_transcriber(cfg), Transcriber)


def test_config_validate_whisper_cpp_needs_model():
    cfg = Config(plex_baseurl="http://x", plex_token="t", backend="whisper.cpp")
    assert any("whisper.cpp" in p for p in cfg.validate())
    ok = cfg.merge(whisper_cpp_model="/m/ggml-small.bin")
    assert not any("whisper.cpp backend selected" in p for p in ok.validate())


def test_config_validate_unknown_backend():
    cfg = Config(plex_baseurl="http://x", plex_token="t", backend="nonsense")
    assert any("Unknown backend" in p for p in cfg.validate())
