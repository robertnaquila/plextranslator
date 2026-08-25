"""Tests for the screen-OCR subtitle mode (pure logic; no screen/OCR engine)."""

from types import SimpleNamespace

import pytest

from plextranslator.config import Config
from plextranslator.ocr import (
    LlmTranslator,
    OcrCaptionEngine,
    Region,
    SubtitleTracker,
    TranslationCache,
    matches_script,
    parse_region,
)
from plextranslator.web import SubtitleStore


SCREEN = (1920, 1080)


# -- parse_region ----------------------------------------------------------


def test_parse_region_explicit_pixels():
    assert parse_region("100,800,1720,250", SCREEN) == Region(100, 800, 1720, 250)


def test_parse_region_bottom_default():
    region = parse_region("bottom", SCREEN)
    assert region.left == 0 and region.width == 1920
    assert region.top == 1080 - int(1080 * 0.30)
    assert region.top + region.height == 1080


def test_parse_region_bottom_percent():
    region = parse_region("bottom:40", SCREEN)
    assert region.top == 1080 - int(1080 * 0.40)


@pytest.mark.parametrize(
    "bad", ["", "1,2,3", "a,b,c,d", "0,0,-5,10", "0,0,10,0", "-1,0,10,10", "bottom:0", "bottom:99", "bottom:x"]
)
def test_parse_region_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_region(bad, SCREEN)


# -- script filter ---------------------------------------------------------


def test_matches_script_korean():
    assert matches_script("여, 여기...", "ko")
    assert not matches_script("Hello world", "ko")
    assert matches_script("mixed 여기 text", "ko")


def test_matches_script_japanese_and_unknown():
    assert matches_script("これは", "ja")
    assert not matches_script("subtitles", "ja")
    assert matches_script("anything", "fr")  # no registered ranges -> accept
    assert matches_script("anything", None)


# -- SubtitleTracker -------------------------------------------------------


def test_tracker_debounces_then_emits():
    tr = SubtitleTracker(source_language="ko", stable_frames=2)
    assert tr.feed("여, 여기...") is None  # first sighting: not yet stable
    assert tr.feed("여, 여기...") == "여, 여기..."  # second: emit
    assert tr.feed("여, 여기...") is None  # already current: no re-emit


def test_tracker_stable_frames_one_emits_immediately():
    tr = SubtitleTracker(source_language="ko", stable_frames=1)
    assert tr.feed("안녕하세요") == "안녕하세요"


def test_tracker_new_line_replaces_old():
    tr = SubtitleTracker(source_language="ko", stable_frames=1)
    assert tr.feed("첫번째 줄") == "첫번째 줄"
    assert tr.feed("두번째 줄") == "두번째 줄"


def test_tracker_single_frame_misread_is_absorbed():
    tr = SubtitleTracker(source_language="ko", stable_frames=2)
    tr.feed("진짜 대사")
    tr.feed("진짜 대사")  # emitted
    assert tr.feed("진짜 매사") is None  # one bad frame: not stable
    assert tr.feed("진짜 대사") is None  # back to current: nothing new


def test_tracker_clears_after_empty_frames():
    tr = SubtitleTracker(source_language="ko", stable_frames=1, clear_frames=3)
    assert tr.feed("대사") == "대사"
    assert tr.feed("") is None
    assert tr.feed("") is None
    assert tr.feed("") == ""  # third empty frame -> clear event
    assert tr.feed("") is None  # only one clear event


def test_tracker_filters_non_source_script():
    tr = SubtitleTracker(source_language="ko", stable_frames=1, clear_frames=2)
    assert tr.feed("대사") == "대사"
    # English overlay text straying into the region is noise, and counts toward
    # the empty streak (the Korean subtitle is gone from the region).
    assert tr.feed("The English caption") is None
    assert tr.feed("The English caption") == ""


def test_tracker_line_can_reappear_after_another():
    tr = SubtitleTracker(source_language="ko", stable_frames=1)
    assert tr.feed("가나") == "가나"
    assert tr.feed("나다") == "나다"
    assert tr.feed("가나") == "가나"  # not suppressed: current was "나다"


def test_tracker_short_line_needs_extra_stability():
    # A single-character line ("네") is a REAL Korean subtitle, but transient
    # single-glyph OCR noise looks identical for a frame or two — so short
    # lines need stable_frames + short_line_extra consecutive frames.
    tr = SubtitleTracker(source_language="ko", stable_frames=1, short_line_extra=2)
    assert tr.feed("네") is None
    assert tr.feed("네") is None
    assert tr.feed("네") == "네"  # 1 + 2 frames -> emitted


def test_tracker_transient_single_glyph_never_emits():
    # The phantom-caption bug: one noise glyph appears for a frame or two,
    # then the region is empty again. It must not become a caption.
    tr = SubtitleTracker(source_language="ko", stable_frames=1, short_line_extra=2)
    assert tr.feed("이") is None
    assert tr.feed("이") is None
    assert tr.feed("") is None
    assert tr.feed("어") is None
    assert tr.feed("") is None


def test_tracker_low_script_ratio_is_noise():
    # The user's real probe output on a too-wide region: mostly digits and
    # punctuation with a couple of Hangul glyphs. Must be treated as noise —
    # and count toward the clear streak.
    tr = SubtitleTracker(source_language="ko", stable_frames=1, clear_frames=2)
    assert tr.feed("주고받는 대화입니다") == "주고받는 대화입니다"
    junk = "※ 1] 고 | 00016066 이 여기고 | 기 그 0 0"
    assert tr.feed(junk) is None
    assert tr.feed(junk) == ""  # second noise frame clears the display


def test_tracker_punctuated_real_line_survives_ratio():
    # Real subtitles carry punctuation; the ratio gate must not eat them.
    tr = SubtitleTracker(source_language="ko", stable_frames=1)
    assert tr.feed("- 왜, 뭐가? - 언니 여신이에요, 오늘") == "- 왜, 뭐가? - 언니 여신이에요, 오늘"


def test_tracker_normalizes_whitespace():
    tr = SubtitleTracker(source_language="ko", stable_frames=1)
    assert tr.feed("  여기   있어요  ") == "여기 있어요"
    assert tr.feed("여기 있어요") is None  # same after normalization


# -- TranslationCache ------------------------------------------------------


def test_cache_roundtrip_and_eviction():
    cache = TranslationCache(maxsize=2)
    cache.put("a", "1")
    cache.put("b", "2")
    assert cache.get("a") == "1"  # refreshes a
    cache.put("c", "3")  # evicts b (least recent)
    assert cache.get("b") is None
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"


# -- LlmTranslator ---------------------------------------------------------


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(text="  EN OUT  ")])


def _fake_client():
    return SimpleNamespace(messages=_FakeMessages())


def test_translator_returns_stripped_text_and_uses_model():
    client = _fake_client()
    translator = LlmTranslator("key", "some-model", source_language="ko", client=client)
    assert translator.translate("안녕") == "EN OUT"
    call = client.messages.calls[0]
    assert call["model"] == "some-model"
    assert "Korean" in call["system"]
    assert call["messages"] == [{"role": "user", "content": "안녕"}]


def test_translator_keeps_rolling_context():
    client = _fake_client()
    translator = LlmTranslator(
        "key", "m", source_language="ko", context_pairs=2, client=client
    )
    translator.translate("하나")
    translator.translate("둘")
    translator.translate("셋")
    last = client.messages.calls[-1]["messages"]
    # 2 remembered pairs (4 turns) + the new line
    assert len(last) == 5
    assert last[0] == {"role": "user", "content": "하나"}
    assert last[1] == {"role": "assistant", "content": "EN OUT"}
    assert last[-1] == {"role": "user", "content": "셋"}


# -- OcrCaptionEngine ------------------------------------------------------


class _ScriptedOcr:
    """Returns pre-scripted frame texts, then '' forever."""

    def __init__(self, frames):
        self._frames = list(frames)

    def recognize(self, bgra, width, height):
        return self._frames.pop(0) if self._frames else ""


class _FakeGrabber:
    def grab(self, region):
        return b"", 1, 1


class _RecordingTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        return "T:" + text


class _SyncWorker:
    """Runs submitted work immediately (no thread) for deterministic tests."""

    def __init__(self, func):
        self._func = func

    def submit(self, item):
        self._func(item)

    def start(self):
        pass

    def stop(self):
        pass


def _ocr_engine(frames, translator=None):
    store = SubtitleStore()
    store.start_live("t")
    engine = OcrCaptionEngine(
        store,
        region=Region(0, 0, 10, 10),
        ocr=_ScriptedOcr(frames),
        translator=translator,
        source_language="ko",
        stable_frames=1,
        grabber=_FakeGrabber(),
    )
    engine._worker = _SyncWorker(engine._translate_apply)
    return engine, store


def test_engine_translates_and_displays_new_line():
    translator = _RecordingTranslator()
    engine, store = _ocr_engine(["", "여, 여기..."], translator)
    engine._step()
    assert store.snapshot()["line"] == ""
    engine._step()
    assert store.snapshot()["line"] == "T:여, 여기..."
    assert translator.calls == ["여, 여기..."]


def test_engine_caches_repeated_lines():
    translator = _RecordingTranslator()
    engine, store = _ocr_engine(["가나다", "마바사", "가나다"], translator)
    engine._step()
    engine._step()
    engine._step()
    # "가나다" reappeared after "마바사" but was translated only once
    assert translator.calls == ["가나다", "마바사"]
    assert store.snapshot()["line"] == "T:가나다"


def test_engine_without_translator_shows_original():
    engine, store = _ocr_engine(["대사입니다"], translator=None)
    engine._step()
    assert store.snapshot()["line"] == "대사입니다"


def test_engine_translation_failure_falls_back_to_original():
    class _Broken:
        def translate(self, text):
            raise RuntimeError("api down")

    engine, store = _ocr_engine(["대사"], _Broken())
    engine._step()
    assert store.snapshot()["line"] == "대사"


def test_engine_clear_event_blanks_caption():
    translator = _RecordingTranslator()
    engine, store = _ocr_engine(["대사", "", "", "", ""], translator)
    engine._step()
    assert store.snapshot()["line"] == "T:대사"
    for _ in range(4):
        engine._step()
    assert store.snapshot()["line"] == ""


def test_engine_survives_ocr_errors():
    class _BrokenOcr:
        def recognize(self, bgra, width, height):
            raise RuntimeError("ocr died")

    store = SubtitleStore()
    store.start_live("t")
    engine = OcrCaptionEngine(
        store,
        region=Region(0, 0, 10, 10),
        ocr=_BrokenOcr(),
        translator=None,
        grabber=_FakeGrabber(),
    )
    engine._step()  # must not raise


def test_stale_translation_not_displayed():
    engine, store = _ocr_engine([], _RecordingTranslator())
    engine._displayed_seq = 7  # something newer is already on screen
    engine._translate_apply((3, "옛날 대사"))
    assert store.snapshot()["line"] != "T:옛날 대사"
    engine._translate_apply((8, "새로운 대사"))  # newer than displayed -> shows
    assert store.snapshot()["line"] == "T:새로운 대사"


def test_engine_fast_burst_shows_every_line_in_order():
    # The fast-dialogue bug: lines logged in the terminal but never captioned,
    # because a latest-only worker dropped intermediates and discarded the
    # in-flight line. With the FIFO worker every emitted line is translated
    # and displayed, in order.
    translator = _RecordingTranslator()
    engine, store = _ocr_engine(["하나입니다", "둘입니다", "셋입니다"], translator)
    engine._step()
    engine._step()
    engine._step()
    assert translator.calls == ["하나입니다", "둘입니다", "셋입니다"]
    assert store.snapshot()["line"] == "T:셋입니다"


def test_clear_supersedes_in_flight_translation():
    # Subtitle vanished (clear shown) while its translation was still at
    # Claude; the late completion must not resurrect it over the blank.
    engine, store = _ocr_engine([], _RecordingTranslator())
    engine._seq = 4
    engine._displayed_seq = 4  # a clear event at seq 4 blanked the display
    engine.store.set_live_caption("", hold_seconds=1.0)
    engine._translate_apply((3, "사라진 대사"))
    assert store.snapshot()["line"] == ""


def test_engine_uses_ordered_worker_by_default():
    from plextranslator.workers import OrderedWorker

    store = SubtitleStore()
    engine = OcrCaptionEngine(
        store,
        region=Region(0, 0, 10, 10),
        ocr=_ScriptedOcr([]),
        translator=_RecordingTranslator(),
        grabber=_FakeGrabber(),
    )
    assert isinstance(engine._worker, OrderedWorker)


def test_run_ocr_probe_smoke(monkeypatch, capsys):
    """run_ocr --probe path with everything faked (no screen, no network)."""
    import plextranslator.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "screen_size", lambda idx=1: SCREEN)
    monkeypatch.setattr(
        ocr_mod,
        "make_ocr_backend",
        lambda name, lang, **kw: _ScriptedOcr(["안녕하세요"]),
    )
    monkeypatch.setattr(ocr_mod, "ScreenGrabber", lambda idx=1: _FakeGrabber())

    config = Config(anthropic_api_key="")  # no key -> no translation attempted
    ocr_mod.run_ocr(config, region_spec="bottom", probe=True)
    out = capsys.readouterr().out
    assert "안녕하세요" in out
    assert "1920x1080" in out


# -- Tesseract binary discovery -------------------------------------------


def test_find_tesseract_prefers_path(monkeypatch):
    import plextranslator.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod.shutil, "which", lambda name: "/usr/bin/tesseract")
    assert ocr_mod.find_tesseract_binary() == "/usr/bin/tesseract"


def test_find_tesseract_checks_windows_install_dir(monkeypatch):
    import plextranslator.ocr as ocr_mod

    win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    monkeypatch.setattr(ocr_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr_mod.os.path, "isfile", lambda p: p == win_path)
    assert ocr_mod.find_tesseract_binary() == win_path


def test_find_tesseract_returns_none_when_absent(monkeypatch):
    import plextranslator.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr_mod.os.path, "isfile", lambda p: False)
    assert ocr_mod.find_tesseract_binary() is None


def test_tesseract_backend_errors_without_binary(monkeypatch):
    import plextranslator.ocr as ocr_mod

    pytest.importorskip("pytesseract")
    monkeypatch.setattr(ocr_mod, "find_tesseract_binary", lambda: None)
    with pytest.raises(RuntimeError, match="--tesseract-cmd"):
        ocr_mod.TesseractOcrBackend("kor")


# -- preprocessing ---------------------------------------------------------


def _pixels(img):
    """Pillow renamed getdata() -> get_flattened_data(); support both."""
    getter = getattr(img, "get_flattened_data", None) or img.getdata
    return list(getter())


def _solid_bgra(width, height, b, g, r):
    return bytes([b, g, r, 255]) * (width * height)


def test_preprocess_upscales_and_binarizes():
    pytest.importorskip("PIL")
    from plextranslator.ocr import preprocess_for_ocr

    # bright pixels (subtitle text) -> black after invert; image is upscaled
    img = preprocess_for_ocr(_solid_bgra(4, 3, 255, 255, 255), 4, 3, scale=2)
    assert img.size == (8, 6)
    assert set(_pixels(img)) == {0}


def test_preprocess_dark_background_becomes_white():
    pytest.importorskip("PIL")
    from plextranslator.ocr import preprocess_for_ocr

    img = preprocess_for_ocr(_solid_bgra(4, 3, 0, 0, 0), 4, 3, scale=1)
    assert img.size == (4, 3)
    assert set(_pixels(img)) == {255}


def test_preprocess_output_is_only_black_and_white():
    pytest.importorskip("PIL")
    from plextranslator.ocr import preprocess_for_ocr

    mixed = bytes()
    for i in range(12):
        v = (i * 20) % 256
        mixed += bytes([v, v, v, 255])
    img = preprocess_for_ocr(mixed, 4, 3, scale=2)
    assert set(_pixels(img)) <= {0, 255}


def test_save_frame_writes_both_images(tmp_path, capsys):
    pytest.importorskip("PIL")
    from plextranslator.ocr import save_frame

    out = tmp_path / "frame.png"
    save_frame(_solid_bgra(4, 3, 10, 20, 30), 4, 3, str(out))
    assert out.exists()
    assert (tmp_path / "frame.ocr.png").exists()
    assert "Saved raw frame" in capsys.readouterr().out


# -- recurring-line (watermark) suppression --------------------------------


def test_recurring_line_suppressed_after_limit():
    # The real phantom: a fixed graphic inside the region OCRs as "다" every
    # time the actual subtitle clears, re-emitting it between every real line.
    tr = SubtitleTracker(
        source_language="ko", stable_frames=1, short_line_extra=0,
        recurring_limit=3, clear_frames=2,
    )
    emissions = []
    for line in ("다", "실제 대사입니다", "다", "다른 대사예요", "다"):
        result = tr.feed(line)
        if result:
            emissions.append(result)
    assert emissions.count("다") == 3  # allowed up to the limit...
    # ...but from now on it's furniture: suppressed AND counts toward clear.
    assert tr.feed("마지막 대사") == "마지막 대사"
    assert tr.feed("다") is None
    assert tr.feed("다") == ""  # clears the display instead of re-captioning


def test_ignore_texts_suppressed_immediately():
    tr = SubtitleTracker(
        source_language="ko", stable_frames=1, short_line_extra=0,
        ignore_texts=["다"], clear_frames=2,
    )
    assert tr.feed("다") is None
    assert tr.feed("진짜 대사") == "진짜 대사"
    assert tr.feed("다") is None
    assert tr.feed("다") == ""  # counts as empty -> clears


def test_recurring_limit_zero_disables_learning():
    tr = SubtitleTracker(
        source_language="ko", stable_frames=1, short_line_extra=0,
        recurring_limit=0,
    )
    for expected in range(10):
        assert tr.feed("네네") == "네네"
        assert tr.feed("다른 말") == "다른 말"


# -- meta-response (commentary) suppression --------------------------------


def test_meta_detector_catches_real_examples():
    from plextranslator.ocr import looks_like_meta_response

    # The two responses actually observed in the field:
    assert looks_like_meta_response(
        "I am unable to confidently translate this passage"
    )
    assert looks_like_meta_response(
        "There seems to be something wrong with this Korean word"
    )


def test_meta_detector_spares_real_subtitles():
    from plextranslator.ocr import looks_like_meta_response

    for line in (
        "I'm sorry.",
        "I can't do this anymore!",
        "Yes.",
        "What are you talking about?",
        "I was unable to sleep last night.",  # 'unable to' alone isn't meta
    ):
        assert not looks_like_meta_response(line), line


def test_translator_suppresses_meta_and_keeps_context_clean():
    class _MetaThenReal:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            reply = (
                "I am unable to confidently translate this passage"
                if len(self.calls) == 1
                else "Real translation."
            )
            return SimpleNamespace(content=[SimpleNamespace(text=reply)])

    fake = _MetaThenReal()
    translator = LlmTranslator(
        "key", "m", source_language="ko", client=SimpleNamespace(messages=fake)
    )
    assert translator.translate("갈비된 텍스트") == ""  # suppressed
    assert translator.translate("진짜 대사") == "Real translation."
    # The meta exchange must NOT have been replayed as context.
    assert fake.calls[1]["messages"] == [{"role": "user", "content": "진짜 대사"}]


def test_engine_skips_display_and_cache_on_meta_response():
    class _MetaTranslator:
        def translate(self, text):
            return ""  # what LlmTranslator returns for meta responses

    engine, store = _ocr_engine(["엉망인 텍스트라인"], _MetaTranslator())
    engine.store.set_live_caption("previous caption", hold_seconds=30)
    engine._step()
    assert store.snapshot()["line"] == "previous caption"  # unchanged
    assert engine.cache.get("엉망인 텍스트라인") is None  # not cached


def test_hardened_prompt_mentions_ocr_errors():
    client = _fake_client()
    translator = LlmTranslator("key", "m", source_language="ko", client=client)
    translator.translate("안녕하세요")
    system = client.messages.calls[0]["system"]
    assert "OCR" in system
    assert "never" in system.lower()
