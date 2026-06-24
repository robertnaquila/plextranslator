"""Configuration for plextranslator, loaded from env vars and/or CLI overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Optional


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """Runtime configuration.

    Defaults come from environment variables (see .env.example). The CLI builds a
    Config via :meth:`from_env` and then applies any explicit flags with
    :meth:`merge`.
    """

    # Plex
    plex_baseurl: str = "http://127.0.0.1:32400"
    plex_token: str = ""

    # Whisper
    whisper_model: str = "large-v3"
    device: str = "auto"
    compute_type: str = "auto"

    # Output
    output_dir: str = "./subtitles_out"
    # When True, write the .srt next to the media file so Plex auto-detects it.
    write_sidecar: bool = True
    subtitle_language: str = "en"

    # Optional LLM refinement
    use_llm: bool = False
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-8"

    # Live mode
    chunk_seconds: int = 60
    lead_seconds: int = 120
    poll_interval: int = 5

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            plex_baseurl=os.environ.get("PLEX_BASEURL", cls.plex_baseurl),
            plex_token=os.environ.get("PLEX_TOKEN", cls.plex_token),
            whisper_model=os.environ.get("PLEXTRANSLATOR_WHISPER_MODEL", cls.whisper_model),
            device=os.environ.get("PLEXTRANSLATOR_DEVICE", cls.device),
            compute_type=os.environ.get("PLEXTRANSLATOR_COMPUTE_TYPE", cls.compute_type),
            output_dir=os.environ.get("PLEXTRANSLATOR_OUTPUT_DIR", cls.output_dir),
            write_sidecar=_env_bool("PLEXTRANSLATOR_WRITE_SIDECAR", cls.write_sidecar),
            subtitle_language=os.environ.get(
                "PLEXTRANSLATOR_SUBTITLE_LANGUAGE", cls.subtitle_language
            ),
            use_llm=_env_bool("PLEXTRANSLATOR_USE_LLM", cls.use_llm),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", cls.anthropic_api_key),
            anthropic_model=os.environ.get(
                "PLEXTRANSLATOR_ANTHROPIC_MODEL", cls.anthropic_model
            ),
            chunk_seconds=_env_int("PLEXTRANSLATOR_CHUNK_SECONDS", cls.chunk_seconds),
            lead_seconds=_env_int("PLEXTRANSLATOR_LEAD_SECONDS", cls.lead_seconds),
            poll_interval=_env_int("PLEXTRANSLATOR_POLL_INTERVAL", cls.poll_interval),
        )

    def merge(self, **overrides: Optional[object]) -> "Config":
        """Return a copy with any non-None overrides applied."""
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self

    def validate(self) -> list[str]:
        """Return a list of human-readable problems; empty means OK."""
        problems: list[str] = []
        if not self.plex_baseurl:
            problems.append("Plex base URL is not set (PLEX_BASEURL).")
        if not self.plex_token:
            problems.append("Plex token is not set (PLEX_TOKEN).")
        if self.use_llm and not self.anthropic_api_key:
            problems.append("LLM refinement enabled but ANTHROPIC_API_KEY is not set.")
        if self.chunk_seconds <= 0:
            problems.append("chunk_seconds must be positive.")
        if self.lead_seconds < 0:
            problems.append("lead_seconds must be >= 0.")
        return problems
