"""Tests for the capture stream plumbing (windowing, monitor tee, reader)."""

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


class _FakeTranscriber:
    """Returns a fixed cue per window so we can assert captions appear."""

    def __init__(self):
        self.calls = 0

    def translate_samples(self, samples, *, source_language=None, beam_size=5):
        self.calls += 1
        return [Cue(0, 1, f"line{self.calls}")]


def _engine(store, transcriber, **kw):
    cfg = Config()
    kw.setdefault("window_seconds", 0.02)  # 0.02s -> 640 bytes per window
    kw.setdefault("overlap_seconds", 0.0)
    return AudioCaptureEngine(
        cfg,
        store,
        device="x",
        input_format="pulse",
        transcriber=transcriber,
        **kw,
    )


def _window_bytes(window_seconds=0.02):
    return int(window_seconds * SAMPLE_RATE * BYTES_PER_SAMPLE)


def test_process_stream_emits_caption_per_window():
    pytest.importorskip("numpy")
    store = SubtitleStore()
    store.start_live("t")
    t = _FakeTranscriber()
    engine = _engine(store, t)

    q = queue.Queue()
    q.put(b"\x01\x00" * (_window_bytes() // 2))  # one full window
    q.put(None)
    engine._process_stream(q)

    assert t.calls == 1
    assert store.snapshot()["line"] == "line1"


def test_process_stream_assembles_window_from_partial_chunks():
    pytest.importorskip("numpy")
    store = SubtitleStore()
    store.start_live("t")
    t = _FakeTranscriber()
    engine = _engine(store, t)

    half = b"\x01\x00" * (_window_bytes() // 4)
    q = queue.Queue()
    q.put(b"")  # empty chunk is harmless
    q.put(half)
    q.put(half)
    q.put(None)
    engine._process_stream(q)

    assert t.calls == 1


def test_reader_tees_to_monitor_and_enqueues():
    store = SubtitleStore()
    engine = _engine(store, _FakeTranscriber())

    written = []

    class _FakeMonitor:
        def write(self, data):
            written.append(data)

        def close(self):
            pass

    class _FakeStream:
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        def read(self, n=None):
            return next(self._chunks, b"")

    class _FakeProc:
        def __init__(self, chunks):
            self.stdout = _FakeStream(chunks)
            self.stderr = _FakeStream([])

        def poll(self):
            return 0

    q = queue.Queue()
    engine._reader(_FakeProc([b"aa", b"bb"]), q, _FakeMonitor())

    # monitor saw both chunks; queue holds both + the None sentinel
    assert written == [b"aa", b"bb"]
    assert [q.get(), q.get(), q.get()] == [b"aa", b"bb", None]


def test_reader_without_monitor_still_enqueues():
    store = SubtitleStore()
    engine = _engine(store, _FakeTranscriber())

    class _FakeStream:
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        def read(self, n=None):
            return next(self._chunks, b"")

    class _FakeProc:
        def __init__(self, chunks):
            self.stdout = _FakeStream(chunks)
            self.stderr = _FakeStream([])

        def poll(self):
            return 0

    q = queue.Queue()
    engine._reader(_FakeProc([b"cc"]), q, None)
    assert [q.get(), q.get()] == [b"cc", None]
