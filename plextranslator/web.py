"""Browser overlay server: live English subtitles in a web browser.

This makes plextranslator usable when you watch in a browser (Plex Web, or any
browser-based player). It runs entirely on the Python standard library — no web
framework — and works like this:

* A **poller** thread tracks the active Korean/Japanese Plex session and samples
  the playback position (interpolating between samples so timing is smooth).
* A **generator** thread reuses the live pipeline to transcribe/translate the
  media in chunks that stay ahead of the playhead, storing the resulting cues.
* An **HTTP server** serves an overlay web page plus a Server-Sent Events stream
  that pushes the current subtitle line (chosen from the live playhead) to the
  browser. It also serves the cues as a live-growing WebVTT file.

Open ``http://<host>:<port>/`` in a browser and place it over your video, or load
``/subtitles.vtt`` as a subtitle track. Timing follows the real Plex playhead, so
the overlay stays in sync even though generation happens in chunks.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urlparse

from .config import Config
from .pipeline import Pipeline
from .plex_client import PlexClient
from .subtitles import Cue, cue_at, to_vtt
from .live import next_chunk_to_process

logger = logging.getLogger(__name__)

Clock = Callable[[], float]


class SubtitleStore:
    """Thread-safe shared state between the engine threads and HTTP handlers."""

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        # Current item.
        self.rating_key: Optional[str] = None
        self.title: str = ""
        self.file_path: str = ""
        self.duration: float = 0.0
        self.audio_track: Optional[int] = None
        # Generated cues + how far we've translated.
        self.cues: List[Cue] = []
        self.processed_until: float = 0.0
        # Playhead sampling (for smooth interpolation between polls).
        self._offset_sample: float = 0.0
        self._sample_at: Optional[float] = None
        self.playing: bool = False
        self.status: str = "waiting"
        # Live-caption mode (system-audio capture: no playhead, rolling lines).
        self.live_mode: bool = False
        self._live_caption: str = ""
        self._live_caption_until: float = 0.0

    # -- session lifecycle (poller) ---------------------------------------

    def set_session(
        self,
        *,
        rating_key: str,
        title: str,
        file_path: str,
        duration: float,
        audio_track: Optional[int],
    ) -> None:
        with self._lock:
            if rating_key != self.rating_key:
                # New item -> reset generated state.
                self.rating_key = rating_key
                self.title = title
                self.file_path = file_path
                self.duration = duration
                self.audio_track = audio_track
                self.cues = []
                self.processed_until = 0.0
            else:
                # Same item: refresh metadata in case it filled in late.
                self.title = title
                self.file_path = file_path
                self.duration = duration
                self.audio_track = audio_track

    def clear_session(self) -> None:
        with self._lock:
            self.rating_key = None
            self.title = ""
            self.playing = False
            self.status = "no active Korean/Japanese session"

    def update_playhead(self, offset: float, *, playing: bool) -> None:
        with self._lock:
            self._offset_sample = max(0.0, offset)
            self._sample_at = self._clock()
            self.playing = playing
            if self.rating_key is not None and self.status in {
                "waiting",
                "no active Korean/Japanese session",
            }:
                self.status = "playing"

    # -- generation (generator) -------------------------------------------

    def generation_target(self):
        """Snapshot the info the generator needs, or None if nothing is playing."""
        with self._lock:
            if self.rating_key is None or not self.file_path:
                return None
            return {
                "rating_key": self.rating_key,
                "file_path": self.file_path,
                "duration": self.duration,
                "audio_track": self.audio_track,
                "processed_until": self.processed_until,
                "playhead": self._estimated_playhead_locked(),
            }

    def add_cues(self, rating_key: str, new_cues: List[Cue], processed_until: float) -> None:
        """Append cues for ``rating_key``; ignored if the item changed meanwhile."""
        from .subtitles import merge_cues

        with self._lock:
            if rating_key != self.rating_key:
                return  # item changed while we were transcribing; drop stale cues
            self.cues = merge_cues(self.cues, new_cues)
            self.processed_until = max(self.processed_until, processed_until)

    def set_status(self, status: str) -> None:
        with self._lock:
            self.status = status

    # -- live caption mode (capture) --------------------------------------

    def start_live(self, title: str) -> None:
        with self._lock:
            self.live_mode = True
            self.title = title
            self.status = "listening"

    def set_live_caption(self, text: str, *, hold_seconds: float) -> None:
        with self._lock:
            self.live_mode = True
            self._live_caption = text
            self._live_caption_until = self._clock() + hold_seconds
            self.status = "captioning"

    # -- reads (HTTP handlers) --------------------------------------------

    def _estimated_playhead_locked(self) -> float:
        if self._sample_at is None:
            return self._offset_sample
        elapsed = self._clock() - self._sample_at if self.playing else 0.0
        value = self._offset_sample + max(0.0, elapsed)
        if self.duration:
            value = min(value, self.duration)
        return value

    def estimated_playhead(self) -> float:
        with self._lock:
            return self._estimated_playhead_locked()

    def snapshot(self, offset: float = 0.0) -> dict:
        with self._lock:
            if self.live_mode:
                now = self._clock()
                line = self._live_caption if now < self._live_caption_until else ""
                return {
                    "title": self.title or "Live captions",
                    "status": self.status,
                    "playing": True,
                    "playhead": 0.0,
                    "line": line,
                    "ready": bool(self._live_caption),
                    "generated_until": 0.0,
                    "duration": 0.0,
                    "cue_count": 0,
                    "live": True,
                }
            playhead = self._estimated_playhead_locked()
            active = cue_at(self.cues, playhead + offset)
            return {
                "title": self.title,
                "status": self.status,
                "playing": self.playing,
                "playhead": round(playhead, 2),
                "line": active.text if active else "",
                "ready": bool(self.cues),
                "generated_until": round(self.processed_until, 1),
                "duration": round(self.duration, 1),
                "cue_count": len(self.cues),
            }

    def vtt(self) -> str:
        with self._lock:
            return to_vtt(self.cues)


# --------------------------------------------------------------------------- #
# Engine threads
# --------------------------------------------------------------------------- #


class _StoppableThread(threading.Thread):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.daemon = True
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()


class PlexPoller(_StoppableThread):
    """Polls Plex for the active KO/JA session and updates the store's playhead."""

    def __init__(self, config: Config, store: SubtitleStore, plex: Optional[PlexClient] = None):
        super().__init__(name="plextranslator-poller")
        self.config = config
        self.store = store
        self.plex = plex or PlexClient(config.plex_baseurl, config.plex_token)

    def run(self) -> None:  # pragma: no cover - exercised against a live server
        while not self.stopped:
            try:
                session = self.plex.active_target_session()
                if session is None:
                    self.store.clear_session()
                else:
                    self.store.set_session(
                        rating_key=session.rating_key,
                        title=session.title,
                        file_path=session.file_path,
                        duration=session.duration_seconds,
                        audio_track=session.audio_track_index,
                    )
                    self.store.update_playhead(
                        session.view_offset_seconds,
                        playing=session.state == "playing",
                    )
            except Exception as exc:  # noqa: BLE001 - keep polling
                logger.warning("Plex poll failed: %s", exc)
            self._stop.wait(self.config.poll_interval)


class SubtitleGenerator(_StoppableThread):
    """Generates English cues ahead of the playhead and stores them."""

    def __init__(
        self,
        config: Config,
        store: SubtitleStore,
        pipeline: Optional[Pipeline] = None,
    ):
        super().__init__(name="plextranslator-generator")
        self.config = config
        self.store = store
        self.pipeline = pipeline or Pipeline(config)

    def run(self) -> None:  # pragma: no cover - exercised against a live server
        while not self.stopped:
            target = self.store.generation_target()
            if target is None:
                self._stop.wait(self.config.poll_interval)
                continue
            chunk = next_chunk_to_process(
                duration=target["duration"],
                playhead=target["playhead"],
                processed_until=target["processed_until"],
                lead_seconds=self.config.lead_seconds,
                chunk_seconds=self.config.chunk_seconds,
            )
            if chunk is None:
                self.store.set_status("playing")
                self._stop.wait(self.config.poll_interval)
                continue
            try:
                self.store.set_status("translating")
                cues = self.pipeline.process_window(
                    target["file_path"],
                    start=chunk.start,
                    duration=chunk.duration,
                    audio_track=target["audio_track"],
                )
                self.store.add_cues(target["rating_key"], cues, chunk.end)
                self.store.set_status("playing")
            except Exception as exc:  # noqa: BLE001 - keep generating
                logger.warning("Chunk translation failed: %s", exc)
                self._stop.wait(self.config.poll_interval)


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #


def build_overlay_html() -> str:
    """The single-page overlay served at ``/``.

    Connects to ``/events`` via EventSource and renders the current subtitle line.
    Includes font-size and sync-offset controls (the offset reconnects the stream
    with an ``?offset=`` query so timing can be nudged if it drifts).
    """
    return _OVERLAY_HTML


class _Handler(BaseHTTPRequestHandler):
    store: SubtitleStore  # set on the handler class by make_server

    server_version = "plextranslator-web"

    def log_message(self, fmt: str, *args) -> None:  # quiet by default
        logger.debug("http: " + fmt, *args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required name
        parsed = urlparse(self.path)
        path = parsed.path
        offset = _parse_offset(parsed.query)

        if path == "/" or path == "/index.html":
            self._send(200, build_overlay_html().encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/state":
            body = json.dumps(self.store.snapshot(offset)).encode("utf-8")
            self._send(200, body, "application/json")
        elif path == "/subtitles.vtt":
            self._send(200, self.store.vtt().encode("utf-8"), "text/vtt; charset=utf-8")
        elif path == "/healthz":
            self._send(200, b"ok", "text/plain")
        elif path == "/events":
            self._serve_events(offset)
        else:
            self._send(404, b"not found", "text/plain")

    def _serve_events(self, offset: float) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(self.store.snapshot(offset))
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError):
            return  # client navigated away


def _parse_offset(query: str) -> float:
    try:
        values = parse_qs(query).get("offset", ["0"])
        return float(values[0])
    except (ValueError, IndexError):
        return 0.0


def make_server(store: SubtitleStore, host: str, port: int) -> ThreadingHTTPServer:
    """Build (but do not start) the HTTP server bound to ``store``."""
    handler = type("_BoundHandler", (_Handler,), {"store": store})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def run_web(config: Config, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the poller, generator, and HTTP server and serve until interrupted."""
    store = SubtitleStore()
    poller = PlexPoller(config, store)
    generator = SubtitleGenerator(config, store)
    poller.start()
    generator.start()
    server = make_server(store, host, port)
    url = f"http://{host}:{port}/"
    print(f"plextranslator web overlay running at {url}")
    print("Open it in a browser and place it over your video (Ctrl-C to stop).")
    print(f"Or load {url}subtitles.vtt as a subtitle track.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        poller.stop()
        generator.stop()
        server.shutdown()
        server.server_close()


_OVERLAY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>plextranslator subtitles</title>
<style>
  :root { --size: 34px; }
  html, body {
    margin: 0; height: 100%;
    background: #0b0b0d; color: #fff;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    overflow: hidden;
  }
  #bar {
    position: fixed; top: 0; left: 0; right: 0;
    display: flex; gap: 12px; align-items: center;
    padding: 8px 12px; font-size: 13px;
    background: rgba(0,0,0,0.55); color: #cfd2d6;
  }
  #bar .title { font-weight: 600; color: #fff; }
  #bar .spacer { flex: 1; }
  #bar button {
    background: #23262b; color: #fff; border: 1px solid #3a3e44;
    border-radius: 6px; padding: 4px 9px; cursor: pointer; font-size: 13px;
  }
  #bar button:hover { background: #2e3238; }
  #status .dot { color: #f5a623; }
  #status .live { color: #4caf50; }
  #wrap {
    position: fixed; left: 0; right: 0; bottom: 8%;
    display: flex; justify-content: center; padding: 0 6%;
    text-align: center; pointer-events: none;
  }
  #line {
    font-size: var(--size); line-height: 1.3; font-weight: 600;
    background: rgba(0,0,0,0.62); padding: 6px 16px; border-radius: 8px;
    text-shadow: 0 2px 6px rgba(0,0,0,0.9);
    max-width: 90%;
  }
  #line:empty { background: transparent; }
  .hint { color: #8b9098; }
</style>
</head>
<body>
  <div id="bar">
    <span class="title" id="title">Waiting…</span>
    <span id="status"><span class="dot">●</span> connecting</span>
    <span class="spacer"></span>
    <span class="hint" id="sync">sync 0.0s</span>
    <button onclick="nudge(-0.5)">−0.5s</button>
    <button onclick="nudge(0.5)">+0.5s</button>
    <button onclick="resize(-2)">A−</button>
    <button onclick="resize(2)">A+</button>
  </div>
  <div id="wrap"><div id="line"></div></div>
<script>
  let offset = 0.0;
  let es = null;
  const lineEl = document.getElementById('line');
  const titleEl = document.getElementById('title');
  const statusEl = document.getElementById('status');
  const syncEl = document.getElementById('sync');

  function connect() {
    if (es) es.close();
    es = new EventSource('/events?offset=' + offset);
    es.onmessage = (e) => {
      const s = JSON.parse(e.data);
      lineEl.textContent = s.line || '';
      titleEl.textContent = s.title || 'Waiting…';
      const live = s.status === 'playing' && s.ready;
      statusEl.innerHTML = (live ? '<span class="live">●</span> '
                                 : '<span class="dot">●</span> ') + s.status;
    };
    es.onerror = () => {
      statusEl.innerHTML = '<span class="dot">●</span> reconnecting…';
    };
  }
  function nudge(d) {
    offset = Math.round((offset + d) * 10) / 10;
    syncEl.textContent = 'sync ' + offset.toFixed(1) + 's';
    connect();
  }
  function resize(d) {
    const root = document.documentElement;
    const cur = parseInt(getComputedStyle(root).getPropertyValue('--size'));
    root.style.setProperty('--size', Math.max(14, cur + d) + 'px');
  }
  connect();
</script>
</body>
</html>
"""
