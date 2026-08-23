"""Tests for the capture latency features: catch-up and async refinement."""

import queue

import pytest

from plextranslator.capture import (
    SAMPLE_RATE,
    BYTES_PER_SAMPLE,
    AudioCaptureEngine,
)
from plextranslator.config import Config
from plextranslator.subtitles import Cue
from plextranslator.web import SubtitleStore


WINDOW_SECONDS = 0.02
WINDOW_BYTES = int(WINDOW_SECONDS * SAMPLE_RATE * BYTES_PER_SAMPLE)


class _CountingTranscriber:
    def __init__(self):
        self.calls = 0

    def translate_samples(self, samples, *, source_language=None, beam_size=5):
        self.calls += 1
        return [Cue(0, 1, f"line{self.calls}")]


class _BeamRecorder:
    def __init__(self):
        self.beam_sizes = []

    def translate_samples(self, samples, *, source_language=None, beam_size=5):
        self.beam_sizes.append(beam_size)
        return [Cue(0, 1, "x")]


def _engine(transcriber, **kw):
    store = SubtitleStore()
    store.start_live("t")
    kw.setdefault("window_seconds", WINDOW_SECONDS)
    kw.setdefault("overlap_seconds", 0.0)
    engine = AudioCaptureEngine(
        Config(),
        store,
        device="x",
        input_format="pulse",
        transcriber=transcriber,
        **kw,
    )
    return engine, store


def _queue_with_windows(n):
    q = queue.Queue()
    for _ in range(n):
        q.put(b"\x01\x00" * (WINDOW_BYTES // 2))
    q.put(None)
    return q


def test_catch_up_drops_stale_backlog():
    pytest.importorskip("numpy")
    t = _CountingTranscriber()
    engine, _ = _engine(t, catch_up=True)
    # 5 windows are already queued (transcription "fell behind"); with catch-up
    # only the first and the freshest remaining window are transcribed.
    engine._process_stream(_queue_with_windows(5))
    assert t.calls == 2


def test_no_catch_up_processes_everything():
    pytest.importorskip("numpy")
    t = _CountingTranscriber()
    engine, _ = _engine(t, catch_up=False)
    engine._process_stream(_queue_with_windows(5))
    assert t.calls == 5


def test_catch_up_small_backlog_is_kept():
    pytest.importorskip("numpy")
    t = _CountingTranscriber()
    engine, _ = _engine(t, catch_up=True)
    # 2 windows: backlog after the first is exactly one window -> nothing skipped.
    engine._process_stream(_queue_with_windows(2))
    assert t.calls == 2


def test_beam_size_is_passed_through():
    pytest.importorskip("numpy")
    t = _BeamRecorder()
    engine, _ = _engine(t, beam_size=1)
    engine._process_stream(_queue_with_windows(1))
    assert t.beam_sizes == [1]


def test_process_window_shows_raw_and_submits_refine():
    pytest.importorskip("numpy")
    t = _CountingTranscriber()
    engine, store = _engine(t)

    submitted = []

    class _FakeWorker:
        def submit(self, item):
            submitted.append(item)

    engine.refiner = object()  # refinement enabled
    engine._refine_worker = _FakeWorker()
    engine._process_window(b"\x01\x00" * (WINDOW_BYTES // 2))

    # raw caption is shown immediately, refinement queued with the current seq
    assert store.snapshot()["line"] == "line1"
    assert submitted == [(1, "line1")]


def test_refine_apply_swaps_caption_for_current_seq():
    pytest.importorskip("numpy")
    engine, store = _engine(_CountingTranscriber())

    class _FakeRefiner:
        def refine(self, cues):
            return [Cue(0, 1, "REFINED " + cues[0].text)]

    engine.refiner = _FakeRefiner()
    engine._last_hold = 10.0
    engine._refine_seq = 2

    engine._refine_apply((2, "hello"))
    assert store.snapshot()["line"] == "REFINED hello"


def test_refine_apply_discards_stale_result():
    pytest.importorskip("numpy")
    engine, store = _engine(_CountingTranscriber())

    class _FakeRefiner:
        def refine(self, cues):
            return [Cue(0, 1, "STALE")]

    engine.refiner = _FakeRefiner()
    engine._last_hold = 10.0
    engine._refine_seq = 5  # a newer caption has been shown since

    engine._refine_apply((3, "old"))
    assert store.snapshot()["line"] != "STALE"


def test_refine_apply_survives_refiner_errors():
    pytest.importorskip("numpy")
    engine, store = _engine(_CountingTranscriber())

    class _BrokenRefiner:
        def refine(self, cues):
            raise RuntimeError("api down")

    engine.refiner = _BrokenRefiner()
    engine._refine_seq = 1
    engine._refine_apply((1, "text"))  # must not raise


def test_adaptive_hold_is_bounded():
    pytest.importorskip("numpy")
    engine, _ = _engine(_CountingTranscriber())
    engine._process_window(b"\x01\x00" * (WINDOW_BYTES // 2))
    assert WINDOW_SECONDS < engine._last_hold <= 45.0
