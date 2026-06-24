"""Optional LLM refinement of Whisper's English output.

Whisper's ``translate`` task is fast and decent, but it can be literal or clip
idioms. This step sends the rough English cues to Claude in small batches and asks
for natural, fluent English while preserving cue count and order, so timings stay
aligned. It is entirely optional — if disabled or the SDK/key is missing, the
original cues are returned unchanged.

Uses the official Anthropic SDK (imported lazily).
"""

from __future__ import annotations

import json
import logging
from typing import List

from .subtitles import Cue

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a professional subtitle translator. You receive a JSON array of "
    "subtitle lines that were machine-translated into English from Korean or "
    "Japanese audio. Rewrite each line as natural, idiomatic, concise English "
    "suitable for on-screen subtitles. Preserve meaning and speaker intent. "
    "Do NOT merge, split, reorder, add, or drop lines: return exactly the same "
    "number of lines, in the same order. Respond with ONLY a JSON array of "
    "strings, one per input line."
)


class Refiner:
    """Refines batches of cue text with Claude. No-op if not configured."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-8",
        *,
        batch_size: int = 40,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "anthropic SDK not installed. Install with "
                "`pip install 'plextranslator[llm]'`."
            ) from exc
        self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def refine(self, cues: List[Cue]) -> List[Cue]:
        """Return cues with refined text. On any error, returns input unchanged."""
        if not cues:
            return cues
        refined: List[Cue] = []
        for start in range(0, len(cues), self.batch_size):
            batch = cues[start : start + self.batch_size]
            try:
                texts = self._refine_batch([c.text for c in batch])
            except Exception as exc:  # noqa: BLE001 - refinement is best-effort
                logger.warning("LLM refinement failed for a batch, keeping raw: %s", exc)
                refined.extend(batch)
                continue
            if len(texts) != len(batch):
                logger.warning(
                    "LLM returned %d lines for a batch of %d; keeping raw.",
                    len(texts),
                    len(batch),
                )
                refined.extend(batch)
                continue
            for cue, new_text in zip(batch, texts):
                refined.append(Cue(start=cue.start, end=cue.end, text=new_text.strip()))
        return refined

    def _refine_batch(self, lines: List[str]) -> List[str]:
        client = self._ensure_client()
        payload = json.dumps(lines, ensure_ascii=False)
        message = client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": payload}],
        )
        text = "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()
        return _parse_json_array(text)


def _parse_json_array(text: str) -> List[str]:
    """Parse a JSON array of strings, tolerating ```json fences."""
    text = text.strip()
    if text.startswith("```"):
        # Strip a leading ```json / ``` fence and trailing ```.
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array")
    return [str(item) for item in data]
