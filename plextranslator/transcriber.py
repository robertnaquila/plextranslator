"""Speech-to-English transcription/translation via faster-whisper.

Whisper's ``translate`` task converts speech in any supported language directly
into English text, which is exactly what we want for Korean/Japanese audio: one
model pass yields English subtitles. The result can optionally be polished by the
LLM refinement step (see :mod:`plextranslator.translator`).

faster-whisper is imported lazily so this module can be imported without it.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

from .subtitles import Cue, parse_srt

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


def build_whisper_cpp_cmd(
    binary: str,
    model_path: str,
    audio_path: str,
    out_base: str,
    *,
    source_language: Optional[str] = None,
    threads: int = 0,
    beam_size: int = 5,
    extra_args: Optional[List[str]] = None,
) -> List[str]:
    """Build a whisper.cpp ``whisper-cli`` command that translates ``audio_path``
    to English and writes ``<out_base>.srt``.

    ``--translate`` is whisper.cpp's equivalent of Whisper's translate task
    (output is English regardless of source language). Pure builder for testing.
    """
    cmd = [
        binary,
        "-m",
        model_path,
        "-f",
        audio_path,
        "--translate",
        "--output-srt",
        "-of",
        out_base,
        "-l",
        source_language or "auto",
    ]
    if threads and threads > 0:
        cmd += ["-t", str(threads)]
    if beam_size and beam_size > 0:
        cmd += ["-bs", str(beam_size)]
    if extra_args:
        cmd += list(extra_args)
    return cmd


class WhisperCppTranscriber:
    """Transcriber backend that shells out to the whisper.cpp CLI.

    whisper.cpp runs on CPUs without AVX (build ggml with AVX disabled), which is
    what makes low-power NAS boxes like the Synology DS1517+ (Atom C2538) viable.
    It mirrors :class:`Transcriber`'s interface so the pipeline doesn't care which
    backend is in use.
    """

    def __init__(
        self,
        binary: str = "whisper-cli",
        model_path: str = "",
        *,
        threads: int = 0,
        extra_args: Optional[List[str]] = None,
    ) -> None:
        if not model_path:
            raise ValueError(
                "whisper.cpp backend needs a model path "
                "(PLEXTRANSLATOR_WHISPER_CPP_MODEL or --whisper-cpp-model), e.g. "
                "/models/ggml-small.bin"
            )
        self.binary = binary
        self.model_path = model_path
        self.threads = threads
        self.extra_args = extra_args or []

    def _ensure(self) -> None:
        if shutil.which(self.binary) is None and not os.path.exists(self.binary):
            raise RuntimeError(
                f"whisper.cpp binary not found: {self.binary!r}. Build whisper.cpp "
                "and point --whisper-cpp-bin at its 'whisper-cli'."
            )
        if not os.path.exists(self.model_path):
            raise RuntimeError(f"whisper.cpp model not found: {self.model_path!r}")

    def translate_audio(
        self,
        audio_path: str,
        *,
        source_language: Optional[str] = None,
        time_offset: float = 0.0,
        beam_size: int = 5,
    ) -> List[Cue]:
        self._ensure()
        tmp_dir = tempfile.mkdtemp(prefix="plextranslator_wcpp_")
        out_base = os.path.join(tmp_dir, "out")
        cmd = build_whisper_cpp_cmd(
            self.binary,
            self.model_path,
            audio_path,
            out_base,
            source_language=source_language,
            threads=self.threads,
            beam_size=beam_size,
            extra_args=self.extra_args,
        )
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    "whisper.cpp failed (exit "
                    f"{proc.returncode}): "
                    f"{proc.stderr.decode('utf-8', 'replace')[-1000:]}"
                )
            srt_path = out_base + ".srt"
            if not os.path.exists(srt_path):
                return []
            with open(srt_path, "r", encoding="utf-8", errors="replace") as fh:
                cues = parse_srt(fh.read())
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if time_offset:
            cues = [c.shifted(time_offset) for c in cues]
        return cues

    def translate_samples(
        self,
        samples,
        *,
        source_language: Optional[str] = None,
        beam_size: int = 5,
    ) -> List[Cue]:
        """Write the in-memory samples to a temp WAV, then translate it."""
        import wave

        import numpy as np

        int16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="plextranslator_wcpp_")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(int16)
            return self.translate_audio(
                wav_path, source_language=source_language, beam_size=beam_size
            )
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass


def make_transcriber(config):
    """Build the configured transcriber backend (faster-whisper or whisper.cpp)."""
    if config.backend == "whisper.cpp":
        return WhisperCppTranscriber(
            binary=config.whisper_cpp_bin,
            model_path=config.whisper_cpp_model,
            threads=config.whisper_cpp_threads,
        )
    return Transcriber(
        model_size=config.whisper_model,
        device=config.device,
        compute_type=config.compute_type,
    )
