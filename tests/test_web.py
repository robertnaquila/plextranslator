import json
import threading
import urllib.request
from contextlib import closing

import pytest

from plextranslator.subtitles import Cue, cue_at
from plextranslator.web import (
    SubtitleStore,
    _parse_offset,
    build_overlay_html,
    make_server,
)


# -- cue_at ---------------------------------------------------------------


def test_cue_at_finds_active():
    cues = [Cue(0, 2, "a"), Cue(2, 4, "b"), Cue(5, 6, "c")]
    assert cue_at(cues, 1).text == "a"
    assert cue_at(cues, 3).text == "b"
    assert cue_at(cues, 4.5) is None  # gap
    assert cue_at(cues, 5.5).text == "c"


def test_cue_at_boundaries():
    cues = [Cue(0, 2, "a"), Cue(2, 4, "b")]
    # end is exclusive, start inclusive
    assert cue_at(cues, 2).text == "b"
    assert cue_at(cues, 0).text == "a"


def test_cue_at_empty():
    assert cue_at([], 1) is None


# -- SubtitleStore --------------------------------------------------------


class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_store_interpolates_playhead_when_playing():
    clk = _FakeClock()
    store = SubtitleStore(clock=clk)
    store.set_session(
        rating_key="1", title="X", file_path="/x.mkv", duration=600, audio_track=0
    )
    store.update_playhead(100.0, playing=True)
    clk.t += 5  # 5 seconds pass
    assert store.estimated_playhead() == pytest.approx(105.0)


def test_store_no_interpolation_when_paused():
    clk = _FakeClock()
    store = SubtitleStore(clock=clk)
    store.set_session(
        rating_key="1", title="X", file_path="/x.mkv", duration=600, audio_track=0
    )
    store.update_playhead(100.0, playing=False)
    clk.t += 5
    assert store.estimated_playhead() == pytest.approx(100.0)


def test_store_clamps_playhead_to_duration():
    clk = _FakeClock()
    store = SubtitleStore(clock=clk)
    store.set_session(
        rating_key="1", title="X", file_path="/x.mkv", duration=102, audio_track=0
    )
    store.update_playhead(100.0, playing=True)
    clk.t += 60
    assert store.estimated_playhead() == pytest.approx(102.0)


def test_store_new_item_resets_cues():
    store = SubtitleStore()
    store.set_session(
        rating_key="1", title="A", file_path="/a.mkv", duration=10, audio_track=0
    )
    store.add_cues("1", [Cue(0, 1, "hi")], 1.0)
    assert store.snapshot()["cue_count"] == 1
    store.set_session(
        rating_key="2", title="B", file_path="/b.mkv", duration=10, audio_track=0
    )
    assert store.snapshot()["cue_count"] == 0


def test_store_add_cues_ignored_for_stale_item():
    store = SubtitleStore()
    store.set_session(
        rating_key="2", title="B", file_path="/b.mkv", duration=10, audio_track=0
    )
    # cues for a different (old) item are dropped
    store.add_cues("1", [Cue(0, 1, "stale")], 1.0)
    assert store.snapshot()["cue_count"] == 0


def test_store_snapshot_returns_current_line():
    clk = _FakeClock()
    store = SubtitleStore(clock=clk)
    store.set_session(
        rating_key="1", title="Busan", file_path="/x.mkv", duration=600, audio_track=0
    )
    store.update_playhead(10.0, playing=False)
    store.add_cues("1", [Cue(9, 12, "Run!")], 12.0)
    snap = store.snapshot()
    assert snap["line"] == "Run!"
    assert snap["title"] == "Busan"
    assert snap["ready"] is True


def test_store_snapshot_applies_offset():
    clk = _FakeClock()
    store = SubtitleStore(clock=clk)
    store.set_session(
        rating_key="1", title="X", file_path="/x.mkv", duration=600, audio_track=0
    )
    store.update_playhead(10.0, playing=False)
    store.add_cues("1", [Cue(13, 15, "later line")], 15.0)
    assert store.snapshot(offset=0.0)["line"] == ""
    # nudging +4s should surface the line at t=14
    assert store.snapshot(offset=4.0)["line"] == "later line"


# -- helpers / HTML -------------------------------------------------------


def test_parse_offset():
    assert _parse_offset("offset=2.5") == 2.5
    assert _parse_offset("offset=bad") == 0.0
    assert _parse_offset("") == 0.0


def test_overlay_html_has_eventsource_and_endpoint():
    html = build_overlay_html()
    assert "EventSource" in html
    assert "/events" in html


# -- HTTP integration (no engine threads) ---------------------------------


def _serve(store):
    server = make_server(store, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, port


def _get(port, path):
    with closing(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)) as r:
        return r.status, r.read().decode("utf-8")


def test_http_endpoints():
    store = SubtitleStore()
    store.set_session(
        rating_key="1", title="Parasite", file_path="/p.mkv", duration=30, audio_track=0
    )
    store.update_playhead(5.0, playing=False)
    store.add_cues("1", [Cue(4, 8, "Hello there")], 8.0)
    server, port = _serve(store)
    try:
        status, body = _get(port, "/")
        assert status == 200 and "EventSource" in body

        status, body = _get(port, "/state")
        assert status == 200
        data = json.loads(body)
        assert data["title"] == "Parasite"
        assert data["line"] == "Hello there"

        status, body = _get(port, "/subtitles.vtt")
        assert status == 200 and body.startswith("WEBVTT")
        assert "Hello there" in body

        status, body = _get(port, "/healthz")
        assert status == 200 and body == "ok"
    finally:
        server.shutdown()
        server.server_close()
