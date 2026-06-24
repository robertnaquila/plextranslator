from plextranslator.config import Config
from plextranslator.live import LiveSubtitler, next_chunk_to_process
from plextranslator.pipeline import Chunk
from plextranslator.plex_client import PlaybackSession
from plextranslator.subtitles import Cue


def test_next_chunk_starts_near_playhead_on_fresh_start():
    chunk = next_chunk_to_process(
        duration=600,
        playhead=0,
        processed_until=0,
        lead_seconds=120,
        chunk_seconds=60,
    )
    assert chunk == Chunk(0, 60)


def test_next_chunk_none_when_far_enough_ahead():
    # Already processed to 200s, playhead at 0, lead 120 -> 200 >= 0+120 -> idle.
    chunk = next_chunk_to_process(
        duration=600,
        playhead=0,
        processed_until=200,
        lead_seconds=120,
        chunk_seconds=60,
    )
    assert chunk is None


def test_next_chunk_catches_up_after_seek():
    # Viewer seeked to 300 but we only processed to 100 -> restart near playhead.
    chunk = next_chunk_to_process(
        duration=600,
        playhead=300,
        processed_until=100,
        lead_seconds=120,
        chunk_seconds=60,
    )
    assert chunk is not None
    assert chunk.start == 298  # playhead - 2


def test_next_chunk_none_at_end():
    chunk = next_chunk_to_process(
        duration=600,
        playhead=590,
        processed_until=600,
        lead_seconds=120,
        chunk_seconds=60,
    )
    assert chunk is None


class _FakePlex:
    def __init__(self, session):
        self._session = session
        self.uploads = []

    def active_target_session(self):
        return self._session

    def upload_subtitle(self, rating_key, path, language="en"):
        self.uploads.append((rating_key, path, language))


class _FakePipeline:
    def __init__(self):
        self.calls = []

    def process_window(self, media_path, *, start, duration, audio_track):
        self.calls.append((start, duration))
        return [Cue(start, start + 1, f"line@{int(start)}")]


def test_live_loop_processes_then_idles(tmp_path):
    session = PlaybackSession(
        rating_key="42",
        title="Train to Busan",
        file_path="/media/busan.mkv",
        view_offset_seconds=0,
        duration_seconds=600,
        audio_languages=["kor"],
        audio_track_index=0,
        state="playing",
    )
    plex = _FakePlex(session)
    pipeline = _FakePipeline()
    config = Config(
        plex_baseurl="http://x",
        plex_token="t",
        output_dir=str(tmp_path),
        chunk_seconds=60,
        lead_seconds=120,
        poll_interval=0,
    )
    sleeps = []
    sub = LiveSubtitler(
        config, pipeline=pipeline, plex=plex, sleeper=lambda s: sleeps.append(s)
    )
    # 3 iterations: with playhead at 0 and lead 120, it processes chunks at
    # 0 and 60, then 120 >= 0+120 -> idle (sleep).
    sub.run_forever(max_iterations=3)

    assert pipeline.calls == [(0.0, 60), (60.0, 60)]
    assert len(plex.uploads) == 2
    # third iteration idled
    assert sleeps == [0]
    # output srt written and accumulates both lines
    out = tmp_path / "42.en.srt"
    assert out.exists()
    content = out.read_text()
    assert "line@0" in content and "line@60" in content
