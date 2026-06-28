import pytest

from plextranslator.capture import (
    build_capture_cmd,
    default_capture_input,
    pcm16_to_float32,
)
from plextranslator.web import SubtitleStore


def test_build_capture_cmd_streams_pcm_to_stdout():
    cmd = build_capture_cmd("default", "pulse")
    assert cmd[0] == "ffmpeg"
    # input format + device
    assert cmd[cmd.index("-f") + 1] == "pulse"
    assert cmd[cmd.index("-i") + 1] == "default"
    # mono 16 kHz s16le to pipe:1
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[-1] == "pipe:1"
    assert "s16le" in cmd


def test_default_capture_input_per_platform():
    assert default_capture_input("Linux")["format"] == "pulse"
    assert default_capture_input("Darwin")["format"] == "avfoundation"
    assert default_capture_input("Windows")["format"] == "dshow"
    # unknown falls back to pulse
    assert default_capture_input("Plan9")["format"] == "pulse"


def test_pcm16_to_float32():
    np = pytest.importorskip("numpy")
    # int16 max -> ~1.0, min -> -1.0, zero -> 0
    raw = np.array([0, 32767, -32768], dtype="<i2").tobytes()
    out = pcm16_to_float32(raw)
    assert out[0] == pytest.approx(0.0)
    assert out[1] == pytest.approx(1.0, abs=1e-3)
    assert out[2] == pytest.approx(-1.0, abs=1e-3)


def test_pcm16_handles_odd_byte():
    np = pytest.importorskip("numpy")
    raw = np.array([1, 2], dtype="<i2").tobytes() + b"\x00"  # trailing odd byte
    out = pcm16_to_float32(raw)
    assert len(out) == 2


class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_store_live_caption_shows_then_expires():
    clk = _FakeClock()
    store = SubtitleStore(clock=clk)
    store.start_live("Live captions (system audio)")
    snap = store.snapshot()
    assert snap["live"] is True
    assert snap["title"] == "Live captions (system audio)"
    assert snap["line"] == ""  # nothing yet
    assert snap["status"] == "listening"

    store.set_live_caption("Hello", hold_seconds=5)
    assert store.snapshot()["line"] == "Hello"
    assert store.snapshot()["status"] == "captioning"

    clk.t += 4  # still within hold window
    assert store.snapshot()["line"] == "Hello"

    clk.t += 2  # past hold window (6s elapsed > 5s hold)
    assert store.snapshot()["line"] == ""


def test_store_live_caption_replaces():
    clk = _FakeClock()
    store = SubtitleStore(clock=clk)
    store.set_live_caption("first", hold_seconds=10)
    store.set_live_caption("second", hold_seconds=10)
    assert store.snapshot()["line"] == "second"
