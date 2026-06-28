from plextranslator.config import Config
from plextranslator.doctor import (
    FAIL,
    OK,
    SKIP,
    WARN,
    check_backend,
    check_config,
    check_ffmpeg,
    check_llm,
    check_output_dir,
    check_plex,
    check_whisper_cpp_model,
    exit_code,
    render,
    run_doctor,
)


# -- ffmpeg ---------------------------------------------------------------


def test_check_ffmpeg_found():
    c = check_ffmpeg(which=lambda name: "/usr/bin/ffmpeg")
    assert c.status == OK and "ffmpeg" in c.detail


def test_check_ffmpeg_missing():
    c = check_ffmpeg(which=lambda name: None)
    assert c.status == FAIL


# -- backend --------------------------------------------------------------


def test_check_backend_faster_whisper_available():
    c = check_backend(Config(), faster_whisper_available=True)
    assert c.status == OK


def test_check_backend_faster_whisper_missing():
    c = check_backend(Config(), faster_whisper_available=False)
    assert c.status == FAIL


def test_check_backend_whisper_cpp_binary_on_path():
    cfg = Config(backend="whisper.cpp", whisper_cpp_model="/m.bin")
    c = check_backend(cfg, which=lambda n: "/usr/local/bin/whisper-cli")
    assert c.status == OK


def test_check_backend_whisper_cpp_binary_absolute_path():
    cfg = Config(backend="whisper.cpp", whisper_cpp_bin="/opt/whisper-cli", whisper_cpp_model="/m.bin")
    c = check_backend(cfg, which=lambda n: None, exists=lambda p: p == "/opt/whisper-cli")
    assert c.status == OK and c.detail == "/opt/whisper-cli"


def test_check_backend_whisper_cpp_binary_missing():
    cfg = Config(backend="whisper.cpp", whisper_cpp_model="/m.bin")
    c = check_backend(cfg, which=lambda n: None, exists=lambda p: False)
    assert c.status == FAIL


# -- whisper.cpp model ----------------------------------------------------


def test_check_model_skipped_for_faster_whisper():
    assert check_whisper_cpp_model(Config()).status == SKIP


def test_check_model_missing_path():
    cfg = Config(backend="whisper.cpp")
    assert check_whisper_cpp_model(cfg).status == FAIL


def test_check_model_present():
    cfg = Config(backend="whisper.cpp", whisper_cpp_model="/m/ggml-small.bin")
    c = check_whisper_cpp_model(cfg, exists=lambda p: True)
    assert c.status == OK


def test_check_model_file_not_found():
    cfg = Config(backend="whisper.cpp", whisper_cpp_model="/m/missing.bin")
    c = check_whisper_cpp_model(cfg, exists=lambda p: False)
    assert c.status == FAIL


# -- plex -----------------------------------------------------------------


def test_check_plex_skipped_without_creds():
    c = check_plex(Config())
    assert c.status == WARN


class _FakePlex:
    def __init__(self, info=None, exc=None):
        self._info = info
        self._exc = exc

    def server_info(self):
        if self._exc:
            raise self._exc
        return self._info


def test_check_plex_ok():
    cfg = Config(plex_baseurl="http://x", plex_token="t")
    info = {"name": "Tower", "version": "1.40", "sections": ["Korean", "Japanese"]}
    c = check_plex(cfg, factory=lambda _c: _FakePlex(info=info))
    assert c.status == OK
    assert "Tower" in c.detail and "2 libraries" in c.detail


def test_check_plex_failure():
    cfg = Config(plex_baseurl="http://x", plex_token="t")
    c = check_plex(cfg, factory=lambda _c: _FakePlex(exc=ConnectionError("refused")))
    assert c.status == FAIL and "refused" in c.detail


# -- llm ------------------------------------------------------------------


def test_check_llm_disabled():
    assert check_llm(Config()).status == SKIP


def test_check_llm_missing_package():
    cfg = Config(use_llm=True, anthropic_api_key="k")
    assert check_llm(cfg, anthropic_available=False).status == FAIL


def test_check_llm_missing_key():
    cfg = Config(use_llm=True)
    assert check_llm(cfg, anthropic_available=True).status == FAIL


def test_check_llm_ok():
    cfg = Config(use_llm=True, anthropic_api_key="k")
    assert check_llm(cfg, anthropic_available=True).status == OK


# -- config / output / orchestration --------------------------------------


def test_check_config_ok_and_fail():
    assert check_config(Config(plex_baseurl="http://x", plex_token="t")).status == OK
    assert check_config(Config()).status == FAIL  # missing token


def test_check_output_dir_writable(tmp_path):
    c = check_output_dir(Config(output_dir=str(tmp_path / "out")))
    assert c.status == OK


def test_exit_code_and_render():
    cfg = Config(plex_baseurl="http://x", plex_token="t", output_dir="/tmp")
    checks = run_doctor(cfg)
    assert isinstance(checks, list) and checks
    code = exit_code(checks)
    assert code in (0, 1)
    text = render(checks)
    assert "plextranslator doctor" in text
    # ffmpeg check name appears in the rendered report
    assert "ffmpeg" in text


def test_exit_code_fails_on_any_fail():
    from plextranslator.doctor import Check

    assert exit_code([Check("a", OK), Check("b", WARN)]) == 0
    assert exit_code([Check("a", OK), Check("b", FAIL)]) == 1
