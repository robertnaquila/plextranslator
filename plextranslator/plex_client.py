"""Plex integration: discover KO/JA media, follow playback, upload subtitles.

plexapi is imported lazily so the pure helpers (language detection, picking the
target audio track) can be tested without it or a live server.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from . import TARGET_LANGUAGES

logger = logging.getLogger(__name__)


def normalize_lang(code: Optional[str]) -> str:
    """Normalize a language code to a lowercase short form for comparison."""
    if not code:
        return ""
    return code.strip().lower()


def is_target_language(code: Optional[str]) -> bool:
    """True if ``code`` denotes Korean or Japanese."""
    return normalize_lang(code) in TARGET_LANGUAGES


def pick_target_audio_index(stream_languages: List[Optional[str]]) -> Optional[int]:
    """Given the ordered language codes of a media's audio streams, return the
    index of the first Korean/Japanese track, or None if none match.

    The returned index is the position among *audio* streams, suitable for
    ffmpeg's ``-map 0:a:<index>``.
    """
    for idx, lang in enumerate(stream_languages):
        if is_target_language(lang):
            return idx
    return None


@dataclass
class MediaItem:
    """A flattened view of a Plex movie/episode we may subtitle."""

    rating_key: str
    title: str
    file_path: str
    audio_languages: List[str]
    has_english_subs: bool
    audio_track_index: Optional[int]

    @property
    def is_target(self) -> bool:
        return self.audio_track_index is not None


@dataclass
class PlaybackSession:
    """The currently-playing item and where the playhead is."""

    rating_key: str
    title: str
    file_path: str
    view_offset_seconds: float
    duration_seconds: float
    audio_languages: List[str]
    audio_track_index: Optional[int]
    state: str  # playing | paused | buffering


class PlexClient:
    """Wraps a plexapi.server.PlexServer connection."""

    def __init__(self, baseurl: str, token: str) -> None:
        self.baseurl = baseurl
        self.token = token
        self._server = None

    def _ensure_server(self):
        if self._server is not None:
            return self._server
        try:
            from plexapi.server import PlexServer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "plexapi not installed. Install with `pip install 'plextranslator[run]'`."
            ) from exc
        self._server = PlexServer(self.baseurl, self.token)
        return self._server

    # -- discovery ---------------------------------------------------------

    def iter_target_media(self, section_titles: Optional[List[str]] = None):
        """Yield MediaItem for every movie/episode whose audio is KO/JA.

        ``section_titles`` optionally restricts to named library sections.
        """
        server = self._ensure_server()
        sections = server.library.sections()
        for section in sections:
            if section_titles and section.title not in section_titles:
                continue
            if section.type not in {"movie", "show"}:
                continue
            for video in section.all():
                if section.type == "show":
                    for episode in video.episodes():
                        item = self._to_media_item(episode)
                        if item and item.is_target:
                            yield item
                else:
                    item = self._to_media_item(video)
                    if item and item.is_target:
                        yield item

    def _to_media_item(self, video) -> Optional[MediaItem]:
        try:
            part = video.media[0].parts[0]
        except (IndexError, AttributeError):
            return None
        audio_streams = [s for s in part.streams() if s.streamType == 2]
        sub_streams = [s for s in part.streams() if s.streamType == 3]
        audio_langs = [normalize_lang(getattr(s, "languageCode", "")) for s in audio_streams]
        has_en_subs = any(
            normalize_lang(getattr(s, "languageCode", "")) in {"en", "eng"}
            for s in sub_streams
        )
        return MediaItem(
            rating_key=str(video.ratingKey),
            title=video.title,
            file_path=part.file,
            audio_languages=audio_langs,
            has_english_subs=has_en_subs,
            audio_track_index=pick_target_audio_index(audio_langs),
        )

    # -- live sessions -----------------------------------------------------

    def active_target_session(self) -> Optional[PlaybackSession]:
        """Return the first active playback session whose audio is KO/JA."""
        server = self._ensure_server()
        for session in server.sessions():
            try:
                part = session.media[0].parts[0]
            except (IndexError, AttributeError):
                continue
            audio_streams = [s for s in part.streams() if s.streamType == 2]
            audio_langs = [
                normalize_lang(getattr(s, "languageCode", "")) for s in audio_streams
            ]
            track_index = pick_target_audio_index(audio_langs)
            if track_index is None:
                continue
            player = session.players[0] if session.players else None
            return PlaybackSession(
                rating_key=str(session.ratingKey),
                title=session.title,
                file_path=part.file,
                view_offset_seconds=(session.viewOffset or 0) / 1000.0,
                duration_seconds=(session.duration or 0) / 1000.0,
                audio_languages=audio_langs,
                audio_track_index=track_index,
                state=getattr(player, "state", "unknown") if player else "unknown",
            )
        return None

    def upload_subtitle(self, rating_key: str, srt_path: str, language: str = "en") -> None:
        """Upload an .srt to the Plex item so it shows up as a subtitle track."""
        server = self._ensure_server()
        item = server.fetchItem(int(rating_key))
        item.uploadSubtitles(srt_path)
        logger.info("Uploaded subtitles for %s (%s)", item.title, language)
