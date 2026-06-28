"""Tests for the capture audio-monitor passthrough plumbing.

The actual sounddevice playback can't run in CI (no audio hardware), so these
exercise the stream-windowing / tee logic with fakes.
"""

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
    return AudioCaptureEngine(
        cfg,
        store,
        device="x",
        input_format="pulse",
        window_seconds=kw.get("window_seconds", 0.02),  # 0.02s -> 640 bytes
        overlap_seconds=kw.get("overlap_seconds", 0.0),
        transcriber=transcriber,
        **{k: v for k, v in kw.items() if k not in {"window_seconds", "overlap_seconds"}},
    )


def test_process_stream_emits_caption_per_window():
    pytest.importorskip("numpy")
    store = SubtitleStore()
    store.start_live("t")
    t = _FakeTranscriber()
    engine = _engine(store, t)

    window_bytes = int(0.02 * SAMPLE_RATE * BYTES_PER_SAMPLE)  # 640
    # One full window of bytes, then end-of-stream.
    chunks = [b"\x01\x00" * (window_bytes // 2), None]
    it = iter(chunks)
    engine._process_stream(lambda: next(it))

    assert t.calls == 1
    assert store.snapshot()["line"] == "line1"


def test_process_stream_skips_empty_chunks_and_stops_on_none():
    pytest.importorskip("numpy")
    store = SubtitleStore()
    store.start_live("t")
    t = _FakeTranscriber()
    engine = _engine(store, t)

    window_bytes = int(0.02 * SAMPLE_RATE * BYTES_PER_SAMPLE)
    half = b"\x01\x00" * (window_bytes // 4)
    # empty chunk (skipped), two halves (=> one window), then EOF
    chunks = [b"", half, half, None]
    it = iter(chunks)
    engine._process_stream(lambda: next(it))

    assert t.calls == 1


def test_reader_tees_to_monitor_and_enqueues():
    import queue

    store = SubtitleStore()
    engine = _engine(store, _FakeTranscriber())

    written = []

    class _FakeMonitor:
        def write(self, data):
            written.append(data)

        def close(self):
            pass

    class _FakeProc:
        def __init__(self, chunks):
            self._chunks = iter(chunks)

        class _Out:
            def __init__(self, outer):
                self._outer = outer

            def read(self, n):
                return next(self._outer._chunks, b"")

        @property
        def stdout(self):
            return _FakeProc._Out(self)

    q = queue.Queue()
    proc = _FakeProc([b"aa", b"bb"])
    engine._reader(proc, q, _FakeMonitor())

    # monitor saw both chunks; queue holds both + the None sentinel
    assert written == [b"aa", b"bb"]
    drained = [q.get(), q.get(), q.get()]
    assert drained == [b"aa", b"bb", None]
