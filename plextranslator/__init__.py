"""plextranslator — real-time English subtitles for Korean & Japanese Plex media.

The package is split so that the pure logic (subtitle formatting, chunk planning,
language detection, ffmpeg command building) has no heavy third-party dependencies
and can be imported and unit-tested anywhere. The integrations that need a GPU or
network (faster-whisper, plexapi, anthropic) are imported lazily inside the
modules that use them.
"""

__version__ = "0.1.0"

# Target source languages. ISO 639-1 and 639-2 codes Plex / Whisper may report.
TARGET_LANGUAGES = frozenset({"ko", "kor", "ja", "jpn", "jp"})

__all__ = ["__version__", "TARGET_LANGUAGES"]
