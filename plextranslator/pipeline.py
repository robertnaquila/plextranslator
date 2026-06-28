"""Orchestration: media file -> extract audio -> translate -> (refine) -> cues.

The chunk planner is a pure function used by both library and live modes.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import List, Optional

from .audio import extract_audio
from .config import Config
from .subtitles import Cue
from .transcriber import make_transcriber
from .translator import Refiner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Chunk:
    """A [start, start+duration) window of a media file, in seconds."""

    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def plan_chunks(
    total_duration: float,
    chunk_seconds: float,
    *,
    start: float = 0.0,
) -> List[Chunk]:
    """Split ``[start, total_duration)`` into chunks of ``chunk_seconds``.

    The last chunk is clamped to the remaining length. Returns an empty list when
    there is nothing to process.
    """
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")
    if total_duration <= start:
        return []
    chunks: List[Chunk] = []
    cursor = max(0.0, start)
    while cursor < total_duration:
        duration = min(chunk_seconds, total_duration - cursor)
        chunks.append(Chunk(start=cursor, duration=duration))
        cursor += chunk_seconds
    return chunks


class Pipeline:
    """Reusable transcribe(+refine) pipeline backed by one loaded Whisper model."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.transcriber = make_transcriber(config)
        self.refiner: Optional[Refiner] = None
        if config.use_llm and config.anthropic_api_key:
            self.refiner = Refiner(
                api_key=config.anthropic_api_key, model=config.anthropic_model
            )

    def process_window(
        self,
        media_path: str,
        *,
        start: Optional[float] = None,
        duration: Optional[float] = None,
        audio_track: Optional[int] = None,
        source_language: Optional[str] = None,
        refine: bool = True,
    ) -> List[Cue]:
        """Extract a window of audio, translate it, and (optionally) refine it.

        Returns cues with absolute (whole-file) timings.
        """
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="plextranslator_")
        os.close(tmp_fd)
        try:
            extract_audio(
                media_path,
                tmp_path,
                start=start,
                duration=duration,
                audio_track=audio_track,
            )
            cues = self.transcriber.translate_audio(
                tmp_path,
                source_language=source_language,
                time_offset=start or 0.0,
            )
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        if refine and self.refiner is not None and cues:
            cues = self.refiner.refine(cues)
        return cues

    def process_file(
        self,
        media_path: str,
        *,
        audio_track: Optional[int] = None,
        source_language: Optional[str] = None,
    ) -> List[Cue]:
        """Translate an entire media file in one pass."""
        return self.process_window(
            media_path,
            audio_track=audio_track,
            source_language=source_language,
        )
