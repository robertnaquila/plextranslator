"""Batch mode: scan the Plex library and generate English sidecar subtitles.

For every Korean/Japanese movie/episode that lacks English subtitles, translate
the whole file and write a `<media>.en.srt` next to it (so Plex auto-detects it)
and/or upload it to the Plex item.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from .config import Config
from .pipeline import Pipeline
from .plex_client import MediaItem, PlexClient
from .subtitles import to_srt

logger = logging.getLogger(__name__)


def sidecar_path(media_path: str, language: str = "en") -> str:
    """Return the sidecar subtitle path Plex looks for: `<base>.<lang>.srt`."""
    base, _ext = os.path.splitext(media_path)
    return f"{base}.{language}.srt"


def run_library(
    config: Config,
    *,
    section_titles: Optional[List[str]] = None,
    limit: Optional[int] = None,
    skip_existing: bool = True,
    dry_run: bool = False,
) -> int:
    """Process the library. Returns the number of items subtitled."""
    plex = PlexClient(config.plex_baseurl, config.plex_token)
    pipeline: Optional[Pipeline] = None  # built lazily so dry runs need no model
    processed = 0

    for item in plex.iter_target_media(section_titles=section_titles):
        if limit is not None and processed >= limit:
            break
        if skip_existing and _already_has_english(item, config):
            logger.info("Skip (already has English subs): %s", item.title)
            continue

        out_path = sidecar_path(item.file_path, config.subtitle_language)
        logger.info(
            "Translating: %s  [audio=%s -> %s]",
            item.title,
            ",".join(item.audio_languages) or "?",
            out_path,
        )
        if dry_run:
            processed += 1
            continue

        if pipeline is None:
            pipeline = Pipeline(config)
        cues = pipeline.process_file(
            item.file_path, audio_track=item.audio_track_index
        )
        if not cues:
            logger.warning("No speech translated for %s; skipping.", item.title)
            continue

        srt = to_srt(cues)
        _write_output(item, srt, out_path, config, plex)
        processed += 1

    logger.info("Done. Subtitled %d item(s).", processed)
    return processed


def _already_has_english(item: MediaItem, config: Config) -> bool:
    if item.has_english_subs:
        return True
    return os.path.exists(sidecar_path(item.file_path, config.subtitle_language))


def _write_output(
    item: MediaItem, srt: str, out_path: str, config: Config, plex: PlexClient
) -> None:
    wrote_somewhere = False
    if config.write_sidecar:
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(srt)
            logger.info("Wrote sidecar %s", out_path)
            wrote_somewhere = True
        except OSError as exc:
            logger.warning("Could not write sidecar %s: %s", out_path, exc)

    if not wrote_somewhere or not config.write_sidecar:
        # Fall back to the output dir + Plex upload when we can't sit next to media.
        os.makedirs(config.output_dir, exist_ok=True)
        alt = os.path.join(
            config.output_dir,
            f"{item.rating_key}.{config.subtitle_language}.srt",
        )
        with open(alt, "w", encoding="utf-8") as fh:
            fh.write(srt)
        logger.info("Wrote %s", alt)
        try:
            plex.upload_subtitle(item.rating_key, alt, config.subtitle_language)
        except Exception as exc:  # noqa: BLE001 - upload is best-effort
            logger.warning("Could not upload subtitles to Plex: %s", exc)
