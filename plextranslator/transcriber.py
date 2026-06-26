"""Speech-to-English transcription/translation via faster-whisper.

Whisper's ``translate`` task converts speech in any supported language directly
into English text, which is exactly what we want for Korean/Japanese audio: one
model pass yields English subtitles. The result can optionally be polished by the
LLM refinement step (see :mod:`plextranslator.translator`).

faster-whisper is imported lazily so this module can be imported without it.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .subtitles import Cue

logger = logging.getLogger(__name__)


def _resolve_compute_type(device: str, compute_type: str) -> str:
    if compute_type != "auto":
        return compute_type
    # Sensible defaults: float16 on GPU, int8 on CPU.
    return "float16" if device == "cuda" else "int8"


class Transcriber:
    """Thin wrapper around faster-whisper's WhisperModel.

    The model is loaded lazily on first use so constructing a Transcriber is cheap
    (the CLI can build one and validate config before paying the load cost).
    """

    def __init__(
        self,
        model_size: str = "large-v3",
        device: str = "auto",
        compute_type: str = "auto",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None  # loaded on demand

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - exercised only without dep
            raise RuntimeError(
                "faster-whisper is not installed. Install with "
                "`pip install 'plextranslator[run]'`."
            ) from exc

        device = self.device
        if device == "auto":
            device = self._autodetect_device()
        compute_type = _resolve_compute_type(device, self.compute_type)
        logger.info(
            "Loading Whisper model %s (device=%s, compute_type=%s)",
            self.model_size,
            device,
            compute_type,
        )
        self._model = WhisperModel(
            self.model_size, device=device, compute_type=compute_type
        )
        return self._model

    @staticmethod
    def _autodetect_device() -> str:
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:  # pragma: no cover - depends on runtime hardware
            pass
        return "cpu"

    def translate_audio(
        self,
        audio_path: str,
        *,
        source_language: Optional[str] = None,
        time_offset: float = 0.0,
        beam_size: int = 5,
    ) -> List[Cue]:
        """Transcribe-and-translate ``audio_path`` to English cues.

        ``time_offset`` (seconds) is added to every cue's timing — used when the
        audio file is a window extracted starting partway into the media, so the
        returned cues line up with absolute playback time.
        """
        model = self._ensure_model()
        # task="translate" => output is English regardless of source language.
        segments, info = model.transcribe(
            audio_path,
            task="translate",
            language=source_language,
            beam_size=beam_size,
            vad_filter=True,
        )
        logger.debug(
            "Detected language=%s p=%.2f", getattr(info, "language", "?"),
            getattr(info, "language_probability", 0.0),
        )
        cues: List[Cue] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            cues.append(
                Cue(
                    start=seg.start + time_offset,
                    end=seg.end + time_offset,
                    text=text,
                )
            )
        return cues

    def translate_samples(
        self,
        samples,
        *,
        source_language: Optional[str] = None,
        beam_size: int = 5,
    ) -> List[Cue]:
        """Transcribe-and-translate raw audio ``samples`` (a float32 numpy array
        of 16 kHz mono PCM) to English cues with window-relative timings.

        Used by live system-audio capture, which already has decoded samples in
        memory and doesn't go through a file.
        """
        model = self._ensure_model()
        segments, _info = model.transcribe(
            samples,
            task="translate",
            language=source_language,
            beam_size=beam_size,
            vad_filter=True,
        )
        cues: List[Cue] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if text:
                cues.append(Cue(start=seg.start, end=seg.end, text=text))
        return cues
