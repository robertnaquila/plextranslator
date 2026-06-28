"""De-duplication and smoothing for rolling live captions.

Capture mode transcribes overlapping audio windows independently, so consecutive
window translations repeat words around the boundary — e.g. window A ends
"...running away" and window B starts "away from us". Showing each window
verbatim makes the caption stutter and repeat.

:class:`CaptionAccumulator` merges windows into one continuous transcript by
removing the overlapping prefix of each new window (the longest run of leading
tokens that matches the tail of what's already committed), then exposes a
trailing slice — the last sentence or two — to display as a smooth, flowing
caption.

Pure module: no third-party dependencies, fully unit-tested.
"""

from __future__ import annotations

import re
from typing import List

_WORD_RE = re.compile(r"\S+")
# Split on sentence-final punctuation incl. CJK full stops, keeping it on the left.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？…])\s+")


def tokenize(text: str) -> List[str]:
    """Split text into whitespace-delimited tokens (words keep their punctuation)."""
    return _WORD_RE.findall(text)


def _norm(token: str) -> str:
    """Normalize a token for overlap comparison: drop punctuation, lowercase."""
    return re.sub(r"[^\w]", "", token, flags=re.UNICODE).lower()


def overlap_count(prev: List[str], new: List[str], max_overlap: int) -> int:
    """Return how many leading tokens of ``new`` duplicate the tail of ``prev``.

    Finds the largest ``k`` (``<= max_overlap``) such that the last ``k`` tokens
    of ``prev`` equal the first ``k`` tokens of ``new`` after normalization.
    Returns 0 if there's no overlap. Tokens that normalize to empty (pure
    punctuation) never count toward a match.
    """
    limit = min(len(prev), len(new), max_overlap)
    if limit <= 0:
        return 0
    prev_tail = [_norm(t) for t in prev[-limit:]]
    new_head = [_norm(t) for t in new[:limit]]
    for k in range(limit, 0, -1):
        a = prev_tail[-k:]
        b = new_head[:k]
        if all(a) and a == b:
            return k
    return 0


def trailing_caption(words: List[str], *, max_sentences: int = 2, max_words: int = 24) -> str:
    """Render the tail of ``words`` as a caption: the last ``max_sentences``
    sentences, hard-capped at ``max_words`` words so a long run-on can't overflow.
    """
    if not words:
        return ""
    text = " ".join(words)
    sentences = [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    tail = " ".join(sentences[-max_sentences:]).strip() if sentences else text
    tail_words = tail.split()
    if len(tail_words) > max_words:
        tail = " ".join(tail_words[-max_words:])
    return tail


class CaptionAccumulator:
    """Merges overlapping window translations into a smooth rolling caption."""

    def __init__(
        self,
        *,
        max_overlap_words: int = 12,
        max_sentences: int = 2,
        max_words: int = 24,
        history_cap: int = 4000,
    ) -> None:
        self.max_overlap_words = max_overlap_words
        self.max_sentences = max_sentences
        self.max_words = max_words
        self.history_cap = history_cap
        self._committed: List[str] = []

    def add(self, text: str) -> str:
        """Merge a new window's text in (de-duplicating the overlap) and return
        the current caption to display."""
        new = tokenize(text)
        if new:
            k = overlap_count(self._committed, new, self.max_overlap_words)
            self._committed.extend(new[k:])
            if len(self._committed) > self.history_cap:
                self._committed = self._committed[-self.history_cap :]
        return self.display()

    def display(self) -> str:
        return trailing_caption(
            self._committed,
            max_sentences=self.max_sentences,
            max_words=self.max_words,
        )

    def reset(self) -> None:
        self._committed = []
