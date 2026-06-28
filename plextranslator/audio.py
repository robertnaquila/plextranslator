"""Audio extraction via ffmpeg.

The command builder is a pure function so it can be unit-tested without ffmpeg
installed; :func:`extract_audio` runs it.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional


class FfmpegNotFoundError(RuntimeError):
    """Raised when the ffmpeg binary cannot be located."""


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_ffmpeg_cmd(
    media_path: str,
    out_path: str,
    *,
    start: Optional[float] = None,
    duration: Optional[float] = None,
    sample_rate: int = 16000,
    audio_track: Optional[int] = None,
    ffmpeg_bin: str = "ffmpeg",
) -> List[str]:
    """Build the ffmpeg command to extract mono PCM WAV audio.

    16 kHz mono PCM is what Whisper expects. ``start``/``duration`` (seconds)
    extract just a window — used by live mode to process the part of the file
    near the playhead. ``-ss`` is placed before ``-i`` for a fast input seek.
    """
    cmd: List[str] = [ffmpeg_bin, "-nostdin", "-y"]
    if start is not None and start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", media_path]
    if duration is not None and duration > 0:
        cmd += ["-t", f"{duration:.3f}"]
    if audio_track is not None:
        # Select the Nth audio stream of the input.
        cmd += ["-map", f"0:a:{audio_track}"]
    cmd += [
        "-vn",  # drop video
        "-ac",
        "1",  # mono
        "-ar",
        str(sample_rate),
        "-acodec",
        "pcm_s16le",
        "-f",
        "wav",
        out_path,
    ]
    return cmd


def extract_audio(
    media_path: str,
    out_path: str,
    *,
    start: Optional[float] = None,
    duration: Optional[float] = None,
    sample_rate: int = 16000,
    audio_track: Optional[int] = None,
    timeout: Optional[float] = None,
) -> str:
    """Extract audio to ``out_path``. Returns ``out_path`` on success."""
    if not ffmpeg_available():
        raise FfmpegNotFoundError(
            "ffmpeg not found on PATH. Install it (e.g. `apt install ffmpeg` or "
            "`brew install ffmpeg`)."
        )
    cmd = build_ffmpeg_cmd(
        media_path,
        out_path,
        start=start,
        duration=duration,
        sample_rate=sample_rate,
        audio_track=audio_track,
    )
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}"
        )
    return out_path
