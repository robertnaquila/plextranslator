"""Live system-audio capture: subtitles for Netflix and any browser streaming.

Streaming services like Netflix don't expose the media file (the audio is
DRM-protected) and there's no playback position to query — so the file-based
approach used for Plex can't work. What *does* work is capturing the audio your
computer is actually playing and translating it on the fly.

This module captures a system/loopback audio device with ffmpeg, runs Whisper's
``translate`` task on rolling windows of that audio, and publishes the resulting
English text as live captions to the web overlay (see :mod:`plextranslator.web`).

> **You must capture a loopback / "monitor" device, not a microphone**, or you'll
> transcribe the room instead of the show. See the README for per-OS setup
> (PulseAudio monitor on Linux, BlackHole on macOS, VB-CABLE / Stereo Mix on
> Windows).
"""

from __future__ import annotations

import logging
import platform
import queue
import subprocess
import threading
from typing import Callable, List, Optional

from .config import Config
from .dedupe import CaptionAccumulator
from .subtitles import Cue
from .transcriber import Transcriber, make_transcriber
from .translator import Refiner
from .web import SubtitleStore, _StoppableThread

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # s16le
READ_CHUNK = 32768  # bytes per ffmpeg stdout read


def default_capture_input(platform_name: Optional[str] = None) -> dict:
    """Return ffmpeg ``{format, device}`` defaults for the current platform.

    These are *starting points* — to capture playback audio (not a mic) you
    usually need to point ``device`` at a loopback/monitor source.
    """
    name = (platform_name or platform.system()).lower()
    if name.startswith("linux"):
        return {"format": "pulse", "device": "default"}
    if name == "darwin":
        return {"format": "avfoundation", "device": ":0"}
    if name.startswith("win"):
        return {"format": "dshow", "device": "audio=Stereo Mix"}
    return {"format": "pulse", "device": "default"}


def build_capture_cmd(
    device: str,
    input_format: str,
    *,
    sample_rate: int = SAMPLE_RATE,
    channels: int = 1,
    ffmpeg_bin: str = "ffmpeg",
) -> List[str]:
    """Build the ffmpeg command that reads a live audio device and streams raw
    16-bit mono PCM to stdout (``pipe:1``)."""
    return [
        ffmpeg_bin,
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        input_format,
        "-i",
        device,
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]


def pcm16_to_float32(data: bytes):
    """Convert little-endian 16-bit PCM bytes to a float32 numpy array in
    [-1, 1] (the input format faster-whisper expects)."""
    import numpy as np

    if len(data) % 2:
        data = data[:-1]  # drop a trailing odd byte
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def list_output_devices() -> List[tuple]:
    """Return ``[(index, name), ...]`` of audio OUTPUT devices via sounddevice.

    Used by ``capture --list-monitor-devices`` so you can find your Samsung
    output to pass to ``--monitor-device``.
    """
    import sounddevice as sd  # lazy: only needed for monitor playback

    devices = sd.query_devices()
    return [
        (i, d["name"])
        for i, d in enumerate(devices)
        if d.get("max_output_channels", 0) > 0
    ]


class _Monitor:
    """Plays captured PCM out to a chosen device so you can still hear it.

    The stream is 16 kHz mono (what we capture for Whisper) — fine for following
    dialogue, lower fidelity than the original (no stereo / high frequencies).
    Returns None from :func:`open_monitor` if sounddevice is missing or the
    device can't be opened, so capture/captions continue regardless.
    """

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, data: bytes) -> None:
        try:
            self._stream.write(data)
        except Exception as exc:  # noqa: BLE001 - never let playback kill capture
            logger.debug("monitor write dropped: %s", exc)

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass


def open_monitor(device) -> Optional["_Monitor"]:
    try:
        import sounddevice as sd
    except ImportError:
        logger.error(
            "sounddevice not installed — run `pip install sounddevice` to use "
            "--monitor-device. Continuing without audio passthrough."
        )
        return None
    try:
        dev = int(device) if str(device).isdigit() else device
        stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=dev
        )
        stream.start()
        logger.info("Monitoring audio to output device: %r", device)
        return _Monitor(stream)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not open monitor output %r: %s", device, exc)
        return None


class AudioCaptureEngine(_StoppableThread):
    """Captures system audio and publishes rolling English captions."""

    def __init__(
        self,
        config: Config,
        store: SubtitleStore,
        *,
        device: str,
        input_format: str,
        window_seconds: float = 6.0,
        overlap_seconds: float = 0.5,
        source_language: Optional[str] = None,
        title: str = "Live captions (system audio)",
        dedupe: bool = True,
        monitor_device=None,
        transcriber: Optional[Transcriber] = None,
    ) -> None:
        super().__init__(name="plextranslator-capture")
        self.config = config
        self.store = store
        self.device = device
        self.input_format = input_format
        self.window_seconds = window_seconds
        self.overlap_seconds = overlap_seconds
        self.source_language = source_language
        self.title = title
        self.monitor_device = monitor_device
        self.accumulator = CaptionAccumulator() if dedupe else None
        self.transcriber = transcriber or make_transcriber(config)
        self.refiner: Optional[Refiner] = None
        if config.use_llm and config.anthropic_api_key:
            self.refiner = Refiner(
                api_key=config.anthropic_api_key, model=config.anthropic_model
            )

    def run(self) -> None:  # pragma: no cover - needs ffmpeg + an audio device
        self.store.start_live(self.title)
        cmd = build_capture_cmd(self.device, self.input_format)
        logger.info("Capturing audio: %s", " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )
        except FileNotFoundError:
            logger.error("ffmpeg not found on PATH; cannot capture audio.")
            self.store.set_status("error: ffmpeg not found")
            return

        monitor = open_monitor(self.monitor_device) if self.monitor_device is not None else None
        reader: Optional[threading.Thread] = None
        try:
            if monitor is not None:
                # A reader thread tees ffmpeg's audio to the output device (so you
                # still hear it) and feeds transcription via a queue — decoupling
                # playback from the (bursty) transcription so audio stays smooth.
                q: "queue.Queue" = queue.Queue()
                reader = threading.Thread(
                    target=self._reader, args=(proc, q, monitor), daemon=True
                )
                reader.start()
                self._process_stream(q.get)
            else:
                self._process_stream(self._make_stdout_getter(proc))
        finally:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            if reader is not None:
                reader.join(timeout=2)
            if monitor is not None:
                monitor.close()

    def _make_stdout_getter(self, proc) -> Callable[[], Optional[bytes]]:
        """Chunk source that reads ffmpeg stdout directly (no monitor)."""

        def get() -> Optional[bytes]:
            chunk = proc.stdout.read(READ_CHUNK)
            if not chunk:
                if proc.poll() is not None:
                    err = proc.stderr.read().decode("utf-8", "replace")[-500:]
                    logger.error("Audio capture ended: %s", err.strip())
                    self.store.set_status("error: capture stopped (check device)")
                return None  # EOF
            return chunk

        return get

    def _reader(self, proc, q: "queue.Queue", monitor: "_Monitor") -> None:
        """Read ffmpeg, play to the monitor device, and enqueue for transcription."""
        try:
            while not self.stopped:
                chunk = proc.stdout.read(READ_CHUNK)
                if not chunk:
                    break
                monitor.write(chunk)
                q.put(chunk)
        finally:
            q.put(None)  # sentinel -> _process_stream stops

    def _process_stream(self, get_chunk: Callable[[], Optional[bytes]]) -> None:
        """Accumulate fixed windows from a chunk source and transcribe each.

        ``get_chunk`` returns bytes, ``b""`` to skip, or ``None`` at end-of-stream.
        """
        window_bytes = int(self.window_seconds * SAMPLE_RATE * BYTES_PER_SAMPLE)
        overlap_bytes = int(self.overlap_seconds * SAMPLE_RATE * BYTES_PER_SAMPLE)
        buf = bytearray()
        while not self.stopped:
            chunk = get_chunk()
            if chunk is None:
                break
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) >= window_bytes:
                self._process_window(bytes(buf))
                # keep a short overlap so words spanning the boundary aren't lost
                buf = bytearray(buf[-overlap_bytes:]) if overlap_bytes else bytearray()

    def _process_window(self, raw: bytes) -> None:
        try:
            samples = pcm16_to_float32(raw)
            cues = self.transcriber.translate_samples(
                samples, source_language=self.source_language
            )
        except Exception as exc:  # noqa: BLE001 - keep listening
            logger.warning("Window transcription failed: %s", exc)
            return
        text = " ".join(c.text for c in cues).strip()
        if not text:
            return
        if self.refiner is not None:
            try:
                text = self.refiner.refine([Cue(0, 1, text)])[0].text
            except Exception as exc:  # noqa: BLE001
                logger.debug("Refinement skipped: %s", exc)
        # Merge with prior windows so overlapping boundary words don't repeat.
        caption = self.accumulator.add(text) if self.accumulator is not None else text
        # Hold the caption a bit past the next window so it doesn't flicker to blank.
        self.store.set_live_caption(caption, hold_seconds=self.window_seconds + 2.0)


def run_capture(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    device: Optional[str] = None,
    input_format: Optional[str] = None,
    window_seconds: float = 6.0,
    overlap_seconds: float = 0.5,
    source_language: Optional[str] = None,
    dedupe: bool = True,
    monitor_device=None,
) -> None:
    """Start audio capture + the web overlay server. Serves until interrupted."""
    from .web import make_server

    defaults = default_capture_input()
    device = device or defaults["device"]
    input_format = input_format or defaults["format"]

    store = SubtitleStore()
    engine = AudioCaptureEngine(
        config,
        store,
        device=device,
        input_format=input_format,
        window_seconds=window_seconds,
        overlap_seconds=overlap_seconds,
        source_language=source_language,
        dedupe=dedupe,
        monitor_device=monitor_device,
    )
    engine.start()
    server = make_server(store, host, port)
    url = f"http://{host}:{port}/"
    print(f"plextranslator live captions running at {url}")
    print(f"Capturing audio from: [{input_format}] {device}")
    if monitor_device is not None:
        print(f"Playing audio through monitor device: {monitor_device}")
    print("Open the overlay in a browser, then play Netflix (or anything) with")
    print("Korean/Japanese audio. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        engine.stop()
        server.shutdown()
        server.server_close()
