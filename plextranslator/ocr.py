"""Screen-OCR subtitles: translate burned-in subtitles by watching the screen.

Many Korean/Japanese shows come with *burned-in* subtitles in the original
language (variety-show captions, hardsubbed rips, shows on someone else's Plex
server). Audio transcription ignores that gift and re-derives the dialogue the
slow way. This mode instead watches the region of the screen where those
subtitles appear, OCRs each new line the moment it shows up, and translates it
to English with Claude — typically 1–2 seconds behind the on-screen text,
far snappier than transcribing rolling audio windows, and with no Whisper
model or audio routing involved.

Pipeline (all off the hot path where it can be slow):

    screen region ──grab (mss)──▶ OCR backend ──▶ SubtitleTracker (debounce,
        dedupe, script filter) ──new line──▶ Claude translate (cached,
        latest-only worker) ──▶ web overlay caption

OCR backends (selected with ``--ocr-backend``, ``auto`` picks for you):

- ``windows`` — the OCR engine built into Windows 10/11 (``winsdk``). Fast and
  accurate; needs the language pack for the source language installed
  (Settings → Time & Language → Language & region → Add a language → 한국어).
- ``tesseract`` — cross-platform via ``pytesseract`` + the Tesseract binary
  with the language's traineddata (e.g. ``kor``).

The overlay browser window must live OUTSIDE the watched region (e.g. put the
region over the video's subtitle band and the overlay window above it, or on
another monitor) — otherwise the OCR would read its own captions. The tracker's
script filter (Hangul/kana required for ko/ja) also guards against that.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import Config
from .web import SubtitleStore, _StoppableThread
from .workers import OrderedWorker

logger = logging.getLogger(__name__)

DEFAULT_BOTTOM_PERCENT = 30


@dataclass(frozen=True)
class Region:
    """A screen rectangle, in pixels relative to the chosen monitor."""

    left: int
    top: int
    width: int
    height: int


def parse_region(spec: str, screen_size: Tuple[int, int]) -> Region:
    """Parse a region spec into a :class:`Region`.

    Accepted forms:
    - ``"left,top,width,height"`` — explicit pixels (e.g. ``"0,780,1920,300"``)
    - ``"bottom"`` — the bottom 30% of the screen, full width (where burned-in
      subtitles usually live)
    - ``"bottom:40"`` — the bottom 40% (1–95)
    """
    spec = (spec or "").strip().lower()
    sw, sh = screen_size
    if spec.startswith("bottom"):
        pct = DEFAULT_BOTTOM_PERCENT
        if ":" in spec:
            try:
                pct = int(spec.split(":", 1)[1])
            except ValueError:
                raise ValueError(f"Bad region {spec!r}: expected e.g. 'bottom:40'.")
        if not 1 <= pct <= 95:
            raise ValueError(f"Bad region {spec!r}: percent must be 1-95.")
        top = sh - int(sh * pct / 100)
        return Region(left=0, top=top, width=sw, height=sh - top)
    parts = spec.split(",")
    if len(parts) != 4:
        raise ValueError(
            f"Bad region {spec!r}: use 'left,top,width,height' pixels, "
            "'bottom', or 'bottom:<percent>'."
        )
    try:
        left, top, width, height = (int(p.strip()) for p in parts)
    except ValueError:
        raise ValueError(f"Bad region {spec!r}: all four values must be integers.")
    if width <= 0 or height <= 0:
        raise ValueError(f"Bad region {spec!r}: width and height must be positive.")
    if left < 0 or top < 0:
        raise ValueError(f"Bad region {spec!r}: left and top must be >= 0.")
    return Region(left=left, top=top, width=width, height=height)


# -- text stabilization ----------------------------------------------------

_SCRIPT_RANGES = {
    # Hangul syllables, jamo, compatibility jamo
    "ko": ((0xAC00, 0xD7A3), (0x1100, 0x11FF), (0x3130, 0x318F)),
    # Hiragana, katakana, CJK ideographs
    "ja": ((0x3040, 0x309F), (0x30A0, 0x30FF), (0x4E00, 0x9FFF)),
}


def count_script_chars(text: str, language: Optional[str]) -> int:
    """Number of characters in ``text`` belonging to ``language``'s script.

    Returns -1 for languages without a registered script range (no filtering).
    """
    ranges = _SCRIPT_RANGES.get((language or "").lower())
    if not ranges:
        return -1
    return sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in ranges))


def matches_script(text: str, language: Optional[str]) -> bool:
    """True if ``text`` contains at least one character of ``language``'s script.

    Filters out OCR noise from UI chrome, logos, or (crucially) the English
    caption overlay itself if it strays into the watched region. Languages
    without a registered script range accept anything.
    """
    return count_script_chars(text, language) != 0


class SubtitleTracker:
    """Turns a noisy stream of per-frame OCR text into clean line events.

    ``feed`` is called once per captured frame and returns:
    - ``None`` — nothing new (same line still showing, or not yet stable)
    - a string — a NEW subtitle line, seen stable for ``stable_frames`` frames
    - ``""`` — the subtitle vanished (``clear_frames`` empty frames in a row),
      so the display should clear

    Debouncing by requiring the same text for ``stable_frames`` consecutive
    frames absorbs single-frame OCR misreads. Two noise gates keep video
    imagery from becoming phantom captions:

    - **script ratio**: text where fewer than ``script_ratio`` of the
      non-space characters are in the source language's script (e.g. random
      punctuation/digits with one Hangul-looking glyph) is treated as noise;
    - **short-line caution**: lines with fewer than ``min_solid_chars`` script
      characters need ``short_line_extra`` additional stable frames — real
      one-character subtitles ("네", "어?") stay up long enough to pass, while
      transient OCR flickers don't.
    - **recurring-line suppression**: a fixed on-screen graphic inside the
      region (a logo, watermark, or decorative caption element) OCRs as the
      SAME text every time the real subtitle clears, re-emitting it endlessly.
      After a text has been emitted ``recurring_limit`` times it is treated as
      furniture: suppressed, and counted as an empty frame so the display
      clears properly. ``ignore_texts`` pre-seeds that suppression for known
      graphics; ``recurring_limit=0`` disables the learning.
    """

    def __init__(
        self,
        *,
        source_language: Optional[str] = None,
        stable_frames: int = 2,
        clear_frames: int = 4,
        script_ratio: float = 0.4,
        min_solid_chars: int = 2,
        short_line_extra: int = 2,
        recurring_limit: int = 3,
        ignore_texts=None,
    ) -> None:
        if stable_frames < 1:
            raise ValueError("stable_frames must be >= 1")
        self.source_language = source_language
        self.stable_frames = stable_frames
        self.clear_frames = clear_frames
        self.script_ratio = script_ratio
        self.min_solid_chars = min_solid_chars
        self.short_line_extra = short_line_extra
        self.recurring_limit = recurring_limit
        self._ignored = {" ".join(t.split()) for t in (ignore_texts or []) if t.strip()}
        self._emit_counts: dict = {}
        self._current: Optional[str] = None
        self._pending: Optional[str] = None
        self._pending_count = 0
        self._empty_count = 0

    def _is_suppressed(self, text: str) -> bool:
        if text in self._ignored:
            return True
        return (
            self.recurring_limit > 0
            and self._emit_counts.get(text, 0) >= self.recurring_limit
        )

    def _record_emission(self, text: str) -> None:
        if self.recurring_limit <= 0:
            return
        if len(self._emit_counts) > 500:  # unbounded-growth backstop
            self._emit_counts.clear()
        count = self._emit_counts.get(text, 0) + 1
        self._emit_counts[text] = count
        if count == self.recurring_limit:
            logger.warning(
                "%r has now appeared %d times - treating it as a fixed on-screen "
                "graphic (logo/watermark inside the region) and suppressing it "
                "from now on. If it's real dialogue, raise --recurring-limit or "
                "redraw the region to exclude the graphic.",
                text,
                count,
            )

    def _is_noise(self, text: str, script_chars: int) -> bool:
        if script_chars < 0:  # no registered script for this language
            return False
        if script_chars == 0:
            return True
        non_space = len(text.replace(" ", ""))
        return bool(non_space) and (script_chars / non_space) < self.script_ratio

    def feed(self, raw: Optional[str]) -> Optional[str]:
        text = " ".join((raw or "").split())
        script_chars = count_script_chars(text, self.source_language)
        if text and (self._is_noise(text, script_chars) or self._is_suppressed(text)):
            text = ""
        if not text:
            self._pending = None
            self._pending_count = 0
            self._empty_count += 1
            if self._current is not None and self._empty_count >= self.clear_frames:
                self._current = None
                return ""
            return None
        self._empty_count = 0
        if text == self._current:
            self._pending = None
            self._pending_count = 0
            return None
        if text == self._pending:
            self._pending_count += 1
        else:
            self._pending = text
            self._pending_count = 1
        required = self.stable_frames
        if 0 <= script_chars < self.min_solid_chars:
            required += self.short_line_extra
        if self._pending_count >= required:
            emitted = self._pending
            self._current = emitted
            self._pending = None
            self._pending_count = 0
            self._record_emission(emitted)
            return emitted
        return None


class TranslationCache:
    """Tiny LRU so repeated lines (recaps, repeated phrases) translate once.

    Locked: the engine thread reads while the translation worker writes.
    """

    def __init__(self, maxsize: int = 500) -> None:
        self.maxsize = maxsize
        self._data: "OrderedDict[str, str]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)


# -- translation -----------------------------------------------------------

_LANGUAGE_NAMES = {"ko": "Korean", "ja": "Japanese", "zh": "Chinese", "en": "English"}


class LlmTranslator:
    """Translates subtitle lines with Claude, keeping short rolling context.

    Recent (source, translation) pairs are replayed as prior turns so pronouns,
    names, and tone stay consistent across lines. The client is injectable for
    tests; otherwise the ``anthropic`` package is imported lazily on first use.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        source_language: str = "ko",
        context_pairs: int = 4,
        client=None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.source_language = source_language
        self._context: "deque[tuple]" = deque(maxlen=context_pairs)
        self._client = client

    def _ensure_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "anthropic not installed. Run: pip install anthropic"
                ) from exc
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _system_prompt(self) -> str:
        lang = _LANGUAGE_NAMES.get(self.source_language, self.source_language)
        return (
            f"You translate {lang} subtitle lines from a TV show into natural, "
            "concise English subtitles. The lines come from OCR of on-screen "
            f"text and may contain recognition errors - infer the most likely "
            f"intended {lang} and translate that. The lines arrive one at a "
            "time, in order. Some captions hold TWO speakers' short lines, "
            'each prefixed with "-" (e.g. "- 누나, 왜! - 안녕하세요"); translate '
            'both and keep the same "- ... - ..." format. Reply with ONLY the '
            "English subtitle text - never an apology, explanation, or comment "
            "about the text or its quality. If a line is partly "
            "unintelligible, translate the intelligible part; if fully "
            "unintelligible, give your best one-line guess."
        )

    def translate(self, text: str) -> str:
        """Translate one line. Returns ``""`` when the model produced
        commentary instead of a translation (callers should show nothing)."""
        client = self._ensure_client()
        messages = []
        for src, tgt in self._context:
            messages.append({"role": "user", "content": src})
            messages.append({"role": "assistant", "content": tgt})
        messages.append({"role": "user", "content": text})
        response = client.messages.create(
            model=self.model,
            max_tokens=300,
            system=self._system_prompt(),
            messages=messages,
        )
        # Take the first text block (thinking-enabled models may lead with
        # non-text blocks that have no .text attribute).
        translated = next(
            (b.text for b in response.content if getattr(b, "text", None)), ""
        ).strip()
        if translated and looks_like_meta_response(translated):
            logger.info(
                "Model returned commentary instead of a translation for %r; "
                "suppressing it.", text
            )
            return ""
        if translated:
            self._context.append((text, translated))
        return translated or text


# Phrases that mark a model response as commentary ABOUT the text rather than a
# translation OF it. Deliberately narrow: real subtitle translations ("I'm
# sorry.", "I can't do this!") must never match, so we key on translation-task
# vocabulary that essentially never appears in dialogue.
_META_MARKERS = (
    "cannot translate",
    "can't translate",
    "unable to translate",
    "confidently translate",
    "difficult to translate",
    "translate this",
    "translation of this",
    "not translatable",
    "this passage",
    "unintelligib",
    "garbled",
    "gibberish",
    "ocr",
    "korean word",
    "korean text",
    "japanese word",
    "japanese text",
    "not valid korean",
    "not valid japanese",
    "recognition error",
    "something wrong with this",
    "as an ai",
)


def looks_like_meta_response(response: str) -> bool:
    """True if ``response`` reads as commentary about the input, not a subtitle."""
    lowered = response.lower()
    return any(marker in lowered for marker in _META_MARKERS)


# -- screen capture --------------------------------------------------------


def screen_size(monitor_index: int = 1) -> Tuple[int, int]:
    """Return (width, height) of the chosen monitor (1 = primary for mss)."""
    try:
        import mss
    except ImportError as exc:
        raise RuntimeError(
            "mss not installed - screen capture needs it. Run: pip install mss"
        ) from exc
    try:
        with mss.mss() as sct:
            monitors = sct.monitors
            if not 0 <= monitor_index < len(monitors):
                raise RuntimeError(
                    f"Monitor {monitor_index} not found ({len(monitors) - 1} available)."
                )
            mon = monitors[monitor_index]
            return mon["width"], mon["height"]
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - e.g. no display on headless boxes
        raise RuntimeError(f"Cannot access the screen: {exc}") from exc


class ScreenGrabber:
    """Grabs a screen region as raw BGRA bytes via mss.

    Create the instance in the thread that will use it (mss keeps per-thread
    native handles on some platforms).
    """

    def __init__(self, monitor_index: int = 1) -> None:
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError(
                "mss not installed - screen capture needs it. Run: pip install mss"
            ) from exc
        try:
            self._sct = mss.mss()
            monitors = self._sct.monitors
        except Exception as exc:  # noqa: BLE001 - e.g. no display available
            raise RuntimeError(f"Cannot access the screen: {exc}") from exc
        if not 0 <= monitor_index < len(monitors):
            raise RuntimeError(
                f"Monitor {monitor_index} not found ({len(monitors) - 1} available)."
            )
        self._mon = monitors[monitor_index]

    def grab(self, region: Region) -> Tuple[bytes, int, int]:
        shot = self._sct.grab(
            {
                "left": self._mon["left"] + region.left,
                "top": self._mon["top"] + region.top,
                "width": region.width,
                "height": region.height,
            }
        )
        return bytes(shot.bgra), shot.width, shot.height


# -- OCR backends ----------------------------------------------------------

# tesseract uses ISO 639-2 codes; Windows OCR uses BCP-47 tags.
_TESSERACT_LANGS = {"ko": "kor", "ja": "jpn", "zh": "chi_sim", "en": "eng"}


class WindowsOcrBackend:
    """OCR via the engine built into Windows 10/11 (needs ``pip install winsdk``
    and the source language's Windows language pack)."""

    def __init__(self, language: str = "ko") -> None:
        if sys.platform != "win32":
            raise RuntimeError("The 'windows' OCR backend only works on Windows.")
        try:
            from winsdk.windows.globalization import Language
            from winsdk.windows.media.ocr import OcrEngine
        except ImportError as exc:
            raise RuntimeError(
                "winsdk not installed - the Windows OCR backend needs it. "
                "Run: pip install winsdk"
            ) from exc
        self._engine = OcrEngine.try_create_from_language(Language(language))
        if self._engine is None:
            # available_recognizer_languages is a STATIC property on the class.
            try:
                languages = OcrEngine.available_recognizer_languages
                available = ", ".join(lang.language_tag for lang in languages)
            except Exception:  # noqa: BLE001 - the error message matters more
                available = "unknown"
            raise RuntimeError(
                f"Windows has no OCR support installed for {language!r} "
                f"(available: {available or 'none'}). Install the language pack: "
                "Settings > Time & Language > Language & region > Add a language."
            )
        # max_image_dimension is also a static property on the OcrEngine class.
        try:
            self._max_dim = int(OcrEngine.max_image_dimension)
        except Exception:  # noqa: BLE001 - degrade to "no limit check"
            self._max_dim = 0
        self.language = language

    def recognize(self, bgra: bytes, width: int, height: int) -> str:
        import asyncio

        from winsdk.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winsdk.windows.storage.streams import DataWriter

        if self._max_dim and max(width, height) > self._max_dim:
            raise RuntimeError(
                f"Region {width}x{height} exceeds Windows OCR's max dimension "
                f"({self._max_dim}px) - use a smaller --region."
            )
        writer = DataWriter()
        writer.write_bytes(bgra)
        buffer = writer.detach_buffer()
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            buffer, BitmapPixelFormat.BGRA8, width, height
        )

        async def _recognize():
            return await self._engine.recognize_async(bitmap)

        result = asyncio.run(_recognize())
        return (result.text or "").strip()


def preprocess_for_ocr(bgra: bytes, width: int, height: int, scale: int = 2):
    """Turn a raw BGRA screen grab into a high-contrast image Tesseract can read.

    Subtitles are light text over arbitrary video, at screen resolution — which
    Tesseract handles poorly (it wants ~300 DPI, dark-on-light text). Upscaling
    and binarizing makes the difference between usable text and noise:

    1. BGRA -> grayscale
    2. upscale (Tesseract is much more accurate on larger glyphs)
    3. autocontrast, then threshold to pure black/white
    4. invert so the text is dark on white
    """
    from PIL import Image, ImageOps

    # The canonical mss->PIL recipe: BGRA raw bytes, ignore alpha.
    image = Image.frombytes("RGB", (width, height), bgra, "raw", "BGRX")
    gray = image.convert("L")
    if scale > 1:
        gray = gray.resize((width * scale, height * scale), Image.LANCZOS)
    gray = ImageOps.autocontrast(gray)
    # Subtitle glyphs are the brightest thing in the band; keep only those.
    binary = gray.point(lambda p: 255 if p > 180 else 0, mode="L")
    return ImageOps.invert(binary)


def save_frame(bgra: bytes, width: int, height: int, path: str) -> None:
    """Write the raw grab and the preprocessed image next to it, for debugging
    what the OCR engine is actually being shown."""
    from PIL import Image

    Image.frombytes("RGB", (width, height), bgra, "raw", "BGRX").save(path)
    root, _, ext = path.rpartition(".")
    processed_path = f"{root}.ocr.{ext}" if root else path + ".ocr.png"
    preprocess_for_ocr(bgra, width, height).save(processed_path)
    print(f"Saved raw frame to {path}")
    print(f"Saved preprocessed (what OCR sees) to {processed_path}")


def find_tesseract_binary() -> Optional[str]:
    """Locate ``tesseract`` on PATH, or at the usual Windows install locations.

    The Windows installer does not add itself to PATH, so pytesseract fails to
    find it even when Tesseract is correctly installed. Checking the standard
    paths saves everyone a ``--tesseract-cmd`` flag.
    """
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Tesseract-OCR\tesseract.exe"),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


class TesseractOcrBackend:
    """OCR via pytesseract + the Tesseract binary (cross-platform fallback)."""

    def __init__(
        self,
        language: str = "kor",
        tesseract_cmd: Optional[str] = None,
        psm: int = 6,
    ) -> None:
        try:
            import pytesseract
            from PIL import Image  # noqa: F401 - verify Pillow is present too
        except ImportError as exc:
            raise RuntimeError(
                "pytesseract/Pillow not installed - the tesseract OCR backend "
                "needs them. Run: pip install pytesseract Pillow (and install "
                "the Tesseract binary + language data, e.g. 'kor')."
            ) from exc
        binary = tesseract_cmd or find_tesseract_binary()
        if not binary:
            raise RuntimeError(
                "The Tesseract binary was not found. Install it (Windows: "
                "`winget install UB-Mannheim.TesseractOCR`, including the Korean "
                "language data) and, if it is not on PATH, pass its full path "
                r"with --tesseract-cmd 'C:\Program Files\Tesseract-OCR\tesseract.exe'."
            )
        if tesseract_cmd and not os.path.isfile(tesseract_cmd):
            raise RuntimeError(f"--tesseract-cmd path does not exist: {tesseract_cmd!r}")
        pytesseract.pytesseract.tesseract_cmd = binary
        self._verify_language(pytesseract, binary, language)
        self._pytesseract = pytesseract
        self.binary = binary
        self.language = language
        # psm 6 = "one uniform block"; 7 = "one line" (better for a tight,
        # single-line subtitle region); 11/12 = sparse text.
        self.config = f"--psm {psm}"

    @staticmethod
    def _verify_language(pytesseract, binary: str, language: str) -> None:
        """Fail early with a clear message if the language data is missing."""
        try:
            available = set(pytesseract.get_languages(config=""))
        except Exception as exc:  # noqa: BLE001 - can't list; let OCR try anyway
            logger.debug("Could not list Tesseract languages: %s", exc)
            return
        if language not in available:
            raise RuntimeError(
                f"Tesseract has no {language!r} language data installed "
                f"(has: {', '.join(sorted(available)) or 'none'}). Re-run the "
                f"installer and tick the language, or drop {language}.traineddata "
                "into the tessdata folder next to " + binary + "."
            )

    def recognize(self, bgra: bytes, width: int, height: int) -> str:
        image = preprocess_for_ocr(bgra, width, height)
        text = self._pytesseract.image_to_string(
            image, lang=self.language, config=self.config
        )
        return (text or "").strip()


def make_ocr_backend(
    name: str,
    source_language: str = "ko",
    tesseract_cmd: Optional[str] = None,
    psm: int = 6,
):
    """Build the requested OCR backend; ``auto`` prefers Windows OCR on Windows
    and falls back to Tesseract, raising a combined message if neither works."""
    tesseract_lang = _TESSERACT_LANGS.get(source_language, source_language)
    errors: List[str] = []
    if name in ("auto", "windows"):
        try:
            return WindowsOcrBackend(source_language)
        except RuntimeError as exc:
            if name == "windows":
                raise
            errors.append(f"windows: {exc}")
    if name in ("auto", "tesseract"):
        try:
            return TesseractOcrBackend(
                tesseract_lang, tesseract_cmd=tesseract_cmd, psm=psm
            )
        except RuntimeError as exc:
            if name == "tesseract":
                raise
            errors.append(f"tesseract: {exc}")
    if name not in ("auto", "windows", "tesseract"):
        raise RuntimeError(f"Unknown OCR backend {name!r}.")
    raise RuntimeError("No OCR backend available:\n  " + "\n  ".join(errors))


# -- the engine ------------------------------------------------------------


class OcrCaptionEngine(_StoppableThread):
    """Watches a screen region for subtitle text and publishes translations."""

    def __init__(
        self,
        store: SubtitleStore,
        *,
        region: Region,
        ocr,
        translator: Optional[LlmTranslator],
        interval: float = 0.2,
        source_language: str = "ko",
        stable_frames: int = 2,
        hold_seconds: float = 45.0,
        monitor_index: int = 1,
        recurring_limit: int = 3,
        ignore_texts=None,
        grabber=None,
        title: str = "Live subtitles (screen OCR)",
    ) -> None:
        super().__init__(name="plextranslator-ocr")
        self.store = store
        self.region = region
        self.ocr = ocr
        self.translator = translator
        self.interval = interval
        self.hold_seconds = hold_seconds
        self.monitor_index = monitor_index
        self.title = title
        self.tracker = SubtitleTracker(
            source_language=source_language,
            stable_frames=stable_frames,
            recurring_limit=recurring_limit,
            ignore_texts=ignore_texts,
        )
        self.cache = TranslationCache()
        self._grabber = grabber
        self._seq = 0
        # Highest emission seq currently reflected on screen. A finished
        # translation only displays if it's newer — so a slow translation can't
        # overwrite a caption (or a clear) that superseded it, while every line
        # in a fast burst still shows in order (the worker is FIFO).
        self._displayed_seq = 0
        self._worker = OrderedWorker(self._translate_apply, name="plextranslator-translate")

    def _translate_apply(self, item) -> None:
        seq, text = item
        try:
            translated = self.translator.translate(text)
            if not translated:
                # Commentary/meta response — show nothing (and cache nothing,
                # so a later cleaner OCR of the same line gets a fresh chance).
                return
            self.cache.put(text, translated)
        except Exception as exc:  # noqa: BLE001 - show the original over nothing
            logger.warning("Translation failed (%s); showing original text.", exc)
            translated = text
        if seq > self._displayed_seq:
            self._displayed_seq = seq
            self.store.set_live_caption(translated, hold_seconds=self.hold_seconds)

    def _step(self) -> None:
        """One frame: grab -> OCR -> track -> (translate ->) display."""
        raw = None
        try:
            bgra, width, height = self._grabber.grab(self.region)
            raw = self.ocr.recognize(bgra, width, height)
        except Exception as exc:  # noqa: BLE001 - keep watching
            logger.warning("OCR frame failed: %s", exc)
            return
        event = self.tracker.feed(raw)
        if event is None:
            return
        self._seq += 1
        if event == "":
            self._displayed_seq = self._seq
            self.store.set_live_caption("", hold_seconds=1.0)
            return
        logger.info("Subtitle: %s", event)
        cached = self.cache.get(event)
        if cached is not None:
            self._displayed_seq = self._seq
            self.store.set_live_caption(cached, hold_seconds=self.hold_seconds)
        elif self.translator is None:
            self._displayed_seq = self._seq
            self.store.set_live_caption(event, hold_seconds=self.hold_seconds)
        else:
            self._worker.submit((self._seq, event))

    def run(self) -> None:  # pragma: no cover - needs a screen
        self.store.start_live(self.title)
        self.store.set_status(
            f"watching {self.region.width}x{self.region.height} at "
            f"({self.region.left},{self.region.top})"
        )
        try:
            if self._grabber is None:
                self._grabber = ScreenGrabber(self.monitor_index)
        except RuntimeError as exc:
            logger.error("%s", exc)
            self.store.set_status(f"error: {exc}")
            return
        self._worker.start()
        try:
            while not self.stopped:
                started = time.monotonic()
                try:
                    self._step()
                except Exception:  # noqa: BLE001 - never let one frame kill the loop
                    logger.exception("OCR step failed")
                delay = self.interval - (time.monotonic() - started)
                if delay > 0:
                    time.sleep(min(delay, 0.5))
        finally:
            self._worker.stop()


def run_ocr(
    config: Config,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    region_spec: str = "bottom",
    interval: float = 0.2,
    backend: str = "auto",
    source_language: str = "ko",
    monitor_index: int = 1,
    stable_frames: int = 2,
    hold_seconds: float = 45.0,
    probe: bool = False,
    tesseract_cmd: Optional[str] = None,
    psm: int = 6,
    save_frame_path: Optional[str] = None,
    recurring_limit: int = 3,
    ignore_texts=None,
) -> None:
    """Start screen-OCR subtitles + the web overlay server (or a one-shot probe)."""
    from .web import make_server

    size = screen_size(monitor_index)
    region = parse_region(region_spec, size)
    ocr = make_ocr_backend(
        backend, source_language, tesseract_cmd=tesseract_cmd, psm=psm
    )

    translator: Optional[LlmTranslator] = None
    if config.anthropic_api_key:
        translator = LlmTranslator(
            api_key=config.anthropic_api_key,
            model=config.anthropic_model,
            source_language=source_language,
        )
    else:
        logger.warning(
            "ANTHROPIC_API_KEY is not set - captions will show the ORIGINAL "
            "text untranslated. Set the key (e.g. in .env) to translate."
        )

    if probe:
        grabber = ScreenGrabber(monitor_index)
        bgra, width, height = grabber.grab(region)
        text = " ".join((ocr.recognize(bgra, width, height) or "").split())
        print(f"Monitor {monitor_index}: {size[0]}x{size[1]}")
        print(f"Region: {region.left},{region.top},{region.width},{region.height}")
        print(f"OCR text: {text!r}")
        if save_frame_path:
            save_frame(bgra, width, height, save_frame_path)
        if text and not matches_script(text, source_language):
            print(
                "\nNOTE: none of that text is in the source language's script, so "
                "it would be filtered out as noise. Narrow the region to just the "
                "subtitle band (try --select-region)."
            )
        if text and translator is not None:
            print(f"Translation: {translator.translate(text)}")
        return

    store = SubtitleStore()
    engine = OcrCaptionEngine(
        store,
        region=region,
        ocr=ocr,
        translator=translator,
        interval=interval,
        source_language=source_language,
        stable_frames=stable_frames,
        hold_seconds=hold_seconds,
        monitor_index=monitor_index,
        recurring_limit=recurring_limit,
        ignore_texts=ignore_texts,
    )
    engine.start()
    server = make_server(store, host, port)
    url = f"http://{host}:{port}/"
    print(f"plextranslator screen-OCR subtitles running at {url}")
    print(
        f"Watching region {region.left},{region.top},{region.width},{region.height} "
        f"on monitor {monitor_index} every {interval}s"
    )
    print("Keep the overlay window OUTSIDE that region. Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        engine.stop()
        server.shutdown()
        server.server_close()
