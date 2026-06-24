"""Live mode: follow the active Plex playback session and generate English
subtitles in near-real-time.

How it works:

1. Poll Plex for an active Korean/Japanese playback session.
2. Process the media in chunks, starting just before the current playhead, and
   keep generating *ahead* of the playhead until we have a configurable lead
   (``lead_seconds``). Each chunk is transcribed/translated and merged into a
   growing SRT.
3. Re-upload the SRT to the Plex item so the new lines become visible. Plex lets
   you switch the subtitle track to the uploaded one and it updates live as the
   file grows / is re-uploaded.

This isn't literally word-by-word live (Whisper is not a streaming model), but in
practice subtitles appear within a chunk of starting playback and then stay ahead
of you for the rest of the show — close enough to "watch it now and understand".
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, List, Optional

from .config import Config
from .pipeline import Chunk, Pipeline, plan_chunks
from .plex_client import PlaybackSession, PlexClient
from .subtitles import Cue, merge_cues, to_srt

logger = logging.getLogger(__name__)

# A sleeper that tests can stub out.
Sleeper = Callable[[float], None]


def next_chunk_to_process(
    *,
    duration: float,
    playhead: float,
    processed_until: float,
    lead_seconds: float,
    chunk_seconds: float,
) -> Optional[Chunk]:
    """Decide the next window to transcribe, or None if we're far enough ahead.

    We process forward from ``processed_until`` but never let the work fall behind
    the playhead: if the viewer has jumped ahead of what we've generated, we jump
    the cursor to (just before) the playhead so subtitles catch up quickly.
    """
    # If the viewer seeked past what we've generated, restart near the playhead.
    cursor = processed_until
    if playhead > processed_until + 1.0:
        cursor = max(0.0, playhead - 2.0)

    if cursor >= duration:
        return None
    # Stay ahead by lead_seconds; if we already are, nothing to do.
    if cursor >= playhead + lead_seconds:
        return None

    chunks = plan_chunks(duration, chunk_seconds, start=cursor)
    return chunks[0] if chunks else None


class LiveSubtitler:
    """Drives the per-session subtitle generation loop."""

    def __init__(
        self,
        config: Config,
        *,
        pipeline: Optional[Pipeline] = None,
        plex: Optional[PlexClient] = None,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self.config = config
        self.pipeline = pipeline or Pipeline(config)
        self.plex = plex or PlexClient(config.plex_baseurl, config.plex_token)
        self.sleep = sleeper

    def run_forever(self, *, max_iterations: Optional[int] = None) -> None:
        """Poll for sessions and subtitle whichever KO/JA item is playing.

        ``max_iterations`` bounds the outer poll loop (used by tests).
        """
        current_key: Optional[str] = None
        cues: List[Cue] = []
        processed_until = 0.0
        iterations = 0

        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            session = self.plex.active_target_session()
            if session is None:
                logger.debug("No active KO/JA session; waiting.")
                self.sleep(self.config.poll_interval)
                continue

            # New item started -> reset state.
            if session.rating_key != current_key:
                logger.info("Now subtitling: %s", session.title)
                current_key = session.rating_key
                cues = []
                processed_until = max(0.0, session.view_offset_seconds - 2.0)

            chunk = next_chunk_to_process(
                duration=session.duration_seconds,
                playhead=session.view_offset_seconds,
                processed_until=processed_until,
                lead_seconds=self.config.lead_seconds,
                chunk_seconds=self.config.chunk_seconds,
            )
            if chunk is None:
                # Far enough ahead (or finished) — idle until the playhead moves.
                self.sleep(self.config.poll_interval)
                continue

            cues, processed_until = self._process_and_publish(
                session, chunk, cues
            )

        logger.debug("Live loop exited after %d iterations.", iterations)

    def _process_and_publish(
        self, session: PlaybackSession, chunk: Chunk, cues: List[Cue]
    ) -> tuple[List[Cue], float]:
        logger.info(
            "Translating %s [%.0f-%.0f s] (playhead %.0f)",
            session.title,
            chunk.start,
            chunk.end,
            session.view_offset_seconds,
        )
        new_cues = self.pipeline.process_window(
            session.file_path,
            start=chunk.start,
            duration=chunk.duration,
            audio_track=session.audio_track_index,
        )
        merged = merge_cues(cues, new_cues)
        self._publish(session, merged)
        return merged, chunk.end

    def _publish(self, session: PlaybackSession, cues: List[Cue]) -> None:
        os.makedirs(self.config.output_dir, exist_ok=True)
        out_path = os.path.join(
            self.config.output_dir,
            f"{session.rating_key}.{self.config.subtitle_language}.srt",
        )
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(to_srt(cues))
        try:
            self.plex.upload_subtitle(
                session.rating_key, out_path, self.config.subtitle_language
            )
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            logger.warning("Subtitle upload failed (will retry next chunk): %s", exc)
